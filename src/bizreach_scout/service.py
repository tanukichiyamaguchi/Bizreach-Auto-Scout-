"""完全自動運用のための常駐サービス（daemon/serve）。

`run_cycle` が1サイクル（取り込み→生成→初回送信→期限到来分の再送）を実行し、
`serve` がそれを一定間隔で繰り返す。暴走防止は各下位モジュール
（パイプライン・送信・kill switch）に委ねつつ、ここではプロセスを落とさず
継続運用するための隔離（各サイクルの例外捕捉）とシグナルによる graceful 終了を担う。
"""

from __future__ import annotations

import re
import signal
import threading

from .config import get_settings
from .logging_config import logger
from .pipeline import ScoutPipeline
from .scheduler import run_due_resends
from .storage.repository import Repository


def _kill_switch_active() -> bool:
    """kill switch ファイルが存在すれば True。"""
    return get_settings().kill_switch_path.exists()


def parse_search_urls(value: str | list[str] | None) -> list[str]:
    """検索URL指定を複数URLのリストに正規化する。

    1つでも複数でも受け付ける。複数指定は「半角スペース・改行・パイプ(|)」で区切る。
    （URLにカンマが含まれ得るため、カンマは区切り文字にしない）
    """
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r"[\s|]+", str(value).strip())
    return [u.strip() for u in items if u and u.strip()]


def run_cycle(
    search_url: str | list[str] | None,
    max_candidates: int = 50,
    headless: bool = True,
    send: bool = True,
) -> dict:
    """1サイクル分の自動運用を実行する。

    - kill switch が有効なら何も送信せず ``{"skipped": "kill_switch"}`` を返す。
    - search_url が指定されていれば取り込み→生成→初回送信のパイプラインを回す。
    - 続けて期限到来分の再送を実行する。
    - クライアント・リポジトリは finally で必ずクローズする。
    - 例外はここで捕捉してログし ``{"error": ...}`` を返す（プロセスは落とさない）。

    戻り値はパイプライン・再送の件数を含む dict。
    """
    if _kill_switch_active():
        logger.warning("kill switch が有効です。このサイクルの送信を全てスキップします。")
        return {"skipped": "kill_switch"}

    # 取り込み（API）はブラウザ（認証済みコンテキスト）が必要。
    from .bizreach.client import BizreachClient
    from .ingest.bizreach_api_source import BizreachApiSource

    repo = Repository()
    client: BizreachClient | None = None
    result: dict = {}
    try:
        client = BizreachClient(headless=headless).start()
        client.ensure_logged_in()
        # スカウト送信APIは未特定のため、現状は文面生成・保存まで（送信は保留）。
        sender = None

        # --- 初回送信パイプライン（search_url がある場合のみ。複数URL対応）---
        urls = parse_search_urls(search_url)
        if urls:
            keys = ("processed", "generated", "reused", "sent", "dry_run",
                    "skipped_duplicate", "skipped_ineligible", "failed")
            agg = dict.fromkeys(keys, 0)
            pipeline = ScoutPipeline(repo=repo, generator=None, sender=sender)
            for i, url in enumerate(urls, start=1):
                logger.info("検索URL %d/%d を処理します。", i, len(urls))
                source = BizreachApiSource(url, max_candidates, client=client)
                # 既送信件数を渡し、複数URLでも1実行あたりの送信上限を守る。
                sent_so_far = agg["sent"] + agg["dry_run"]
                report = pipeline.run(source, send=send, sent_offset=sent_so_far)
                for k in keys:
                    agg[k] += getattr(report, k, 0)
            agg["search_urls"] = len(urls)
            result["pipeline"] = agg
        else:
            logger.info("search_url 未指定のため取り込み・初回送信はスキップします。")

        # --- 期限到来分の再送 ---
        resend_report = run_due_resends(repo, sender)
        result["resend"] = {
            "due": resend_report.due,
            "sent": resend_report.sent,
            "dry_run": resend_report.dry_run,
            "skipped": resend_report.skipped,
            "failed": resend_report.failed,
        }
        return result
    except Exception as e:  # noqa: BLE001
        # サイクル単位で握りつぶし、常駐プロセスを継続させる。
        logger.exception("サイクル実行中に例外が発生しました: %s", e)
        return {"error": str(e)}
    finally:
        if client is not None:
            client.close()
        repo.close()


def serve(
    search_url: str | None = None,
    interval: int = 86400,
    max_candidates: int = 50,
    headless: bool = True,
    once: bool = False,
    max_cycles: int | None = None,
) -> None:
    """run_cycle を interval 秒ごとに繰り返す常駐ループ。

    - once=True なら1サイクルだけ実行して終了する。
    - max_cycles を指定すると、その回数で停止する（テスト・運用上の上限）。
    - KeyboardInterrupt / SIGTERM を受けると、進行中サイクル完了後に graceful 終了する。
    - 各サイクルは run_cycle 内で例外隔離されるため、失敗しても次サイクルへ進む。
    - サイクル間の待機はシグナルで即座に中断できる（threading.Event）。
    """
    stop_event = threading.Event()

    def _handle_signal(signum, _frame) -> None:
        logger.info("シグナル %s を受信しました。graceful 終了します。", signum)
        stop_event.set()

    # SIGTERM ハンドラを設定（メインスレッドでない場合は設定できないので無視）。
    previous_handler = None
    try:
        previous_handler = signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, OSError):
        # 非メインスレッド等でハンドラ設定できない場合は KeyboardInterrupt のみで対応。
        logger.debug("SIGTERM ハンドラを設定できませんでした（非メインスレッド等）。")

    cycle = 0
    logger.info(
        "常駐サービスを開始します（interval=%ds, once=%s, max_cycles=%s）。",
        interval,
        once,
        max_cycles,
    )
    try:
        while not stop_event.is_set():
            cycle += 1
            logger.info("==== サイクル %d 開始 ====", cycle)
            try:
                result = run_cycle(
                    search_url=search_url,
                    max_candidates=max_candidates,
                    headless=headless,
                )
                logger.info("サイクル %d 完了: %s", cycle, result)
            except Exception as e:  # noqa: BLE001
                # run_cycle は通常例外を返さないが、二重に保険をかけてループを継続する。
                logger.exception("サイクル %d で予期しない例外: %s", cycle, e)

            if once or (max_cycles is not None and cycle >= max_cycles):
                logger.info("停止条件を満たしたため常駐ループを終了します。")
                break

            # 次サイクルまで待機（シグナルで即中断可能）。
            if stop_event.wait(timeout=interval):
                logger.info("待機中に停止要求を受信しました。常駐ループを終了します。")
                break
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt を受信しました。graceful 終了します。")
    finally:
        # 元の SIGTERM ハンドラを復元する。
        if previous_handler is not None:
            try:
                signal.signal(signal.SIGTERM, previous_handler)
            except (ValueError, OSError):
                pass
        logger.info("常駐サービスを終了しました（実行サイクル数: %d）。", cycle)
