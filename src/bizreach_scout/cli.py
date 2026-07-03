"""bizscout コマンドライン。

サブコマンド:
  generate          候補者から文面を生成して表示/保存（送信しない・最も安全）
  run               取り込み→生成→(初回)送信までの一括処理
  run-resends       期限の到来した再送を送信
  import-consultants  consultant_profiles_v2.docx を consultants.json に変換
  preview           保存済みスカウトを表示
  report            送信状況サマリ
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from .config import get_settings
from .generation.generator import ScoutGenerator, render_for_human
from .ingest.csv_source import CSVSource
from .ingest.text_source import TextSource
from .logging_config import logger, setup_logging
from .storage.repository import Repository


def _build_source(source: str, input_path: str | None, search_url: str | None,
                  max_candidates: int, client=None):
    if source == "csv":
        if not input_path:
            raise click.UsageError("--input にCSVファイルを指定してください。")
        return CSVSource(input_path)
    if source == "text":
        if not input_path:
            raise click.UsageError("--input にテキストファイルを指定してください。")
        return TextSource.from_file(input_path)
    if source == "bizreach":
        from .ingest.bizreach_api_source import BizreachApiSource

        return BizreachApiSource(
            search_url=search_url, max_candidates=max_candidates, client=client
        )
    raise click.UsageError(f"未知のソース: {source}")


@click.group()
@click.option("--verbose", is_flag=True, help="詳細ログ")
def cli(verbose: bool) -> None:
    """ビズリーチ自動スカウト文面生成・送信システム。"""
    setup_logging("DEBUG" if verbose else "INFO", logfile="logs/bizscout.log")


@cli.command()
@click.option("--source", type=click.Choice(["csv", "text"]), default="text",
              help="入力ソース（csv / text）")
@click.option("--input", "input_path", type=click.Path(exists=True),
              help="入力ファイル。未指定なら標準入力(text)。")
@click.option("--save/--no-save", default=False, help="DBに保存し data/exports に書き出す")
def generate(source: str, input_path: str | None, save: bool) -> None:
    """候補者プロフィールから初回・再送のスカウト文面を生成して表示する（送信しない）。"""
    if source == "text" and not input_path:
        text = sys.stdin.read()
        src = TextSource(text)
    else:
        src = _build_source(source, input_path, None, 0)

    generator = ScoutGenerator()
    repo = Repository() if save else None
    count = 0
    for candidate in src:
        logger.info("生成中: %s", candidate.member_no)
        scout = generator.generate(candidate)
        click.echo("\n" + "=" * 60)
        click.echo(f"会員番号: {candidate.member_no}（トーン: {scout.tone_key}）")
        click.echo("=" * 60)
        click.echo(render_for_human(scout))
        if repo is not None:
            from .eligibility import check_eligibility

            repo.upsert_candidate(candidate, check_eligibility(candidate))
            repo.record_generated(scout, get_settings().resend_after_days)
            out = Path("data/exports") / f"{candidate.member_no}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_for_human(scout), encoding="utf-8")
        count += 1
    click.echo(f"\n生成完了: {count} 件")


@cli.command()
@click.option("--source", type=click.Choice(["csv", "text", "bizreach"]), default="bizreach")
@click.option("--input", "input_path", type=click.Path(exists=True), help="csv/text の入力ファイル")
@click.option("--search-url", help="bizreach の検索結果URL（保存検索）")
@click.option("--max", "max_candidates", default=50, help="最大処理件数")
@click.option("--send/--no-send", default=True, help="初回送信を行うか")
@click.option("--headless/--no-headless", default=True, help="ブラウザをヘッドレスで起動")
def run(source: str, input_path: str | None, search_url: str | None,
        max_candidates: int, send: bool, headless: bool) -> None:
    """取り込み→生成→(初回)送信を一括実行する。"""
    from .pipeline import ScoutPipeline

    settings = get_settings()
    if send and settings.dry_run:
        click.echo("※ DRY_RUN 有効: 文面は入力しますが実送信は行いません（.env の BIZSCOUT_DRY_RUN）。")

    # 自動送信はビズリーチ画面を操作できる bizreach ソースのみ対応。
    # csv/text は信頼できる遷移先が無いため生成のみ（ブラウザを起動しない）。
    can_send = source == "bizreach"
    if send and not can_send:
        click.echo("※ 自動送信は --source bizreach のみ対応です。csv/text は生成・保存のみ行います。")

    repo = Repository()
    generator = ScoutGenerator()
    client = None
    sender = None

    try:
        if source == "bizreach":  # 検索取得・送信ともにブラウザが必要
            from .bizreach.api import BizreachApi
            from .bizreach.api_sender import ApiScoutSender
            from .bizreach.client import BizreachClient

            client = BizreachClient(headless=headless).start()
            client.ensure_logged_in()
            if send:
                sender = ApiScoutSender(BizreachApi(client))

        src = _build_source(source, input_path, search_url, max_candidates, client=client)
        pipeline = ScoutPipeline(repo=repo, generator=generator, sender=sender)
        report = pipeline.run(src, send=send and can_send)
        click.echo(report.summary())
    finally:
        if client:
            client.close()
        repo.close()


@cli.command(name="run-pickup")
@click.option("--kind", type=click.Choice(["job", "candidate", "both"]), default="job",
              help="job=本日のピックアップ求人(本命) / candidate=ピックアップ候補者 / both")
@click.option("--max", "max_candidates", default=20, help="最大処理件数")
@click.option("--send/--no-send", default=True, help="送信を行うか")
@click.option("--headless/--no-headless", default=True)
def run_pickup(kind: str, max_candidates: int, send: bool, headless: bool) -> None:
    """本日のピックアップ（無料枠・プラチナ残数を消費しない）へスカウト送信する。

    mypageの freescout からレジュメを開いて mrccid を取得し、対象条件を満たす候補者だけに
    /v2/scouts/pickup で送信する。条件外は従来どおりスキップ。
    """
    from .bizreach.api import BizreachApi
    from .bizreach.api_sender import ApiScoutSender
    from .bizreach.client import BizreachClient
    from .ingest.bizreach_pickup_source import BizreachPickupSource
    from .pipeline import ScoutPipeline

    if send and get_settings().dry_run:
        click.echo("※ DRY_RUN 有効: 文面は用意しますが実送信は行いません。")

    repo = Repository()
    client = None
    try:
        client = BizreachClient(headless=headless).start()
        client.ensure_logged_in()
        sender = ApiScoutSender(BizreachApi(client), pickup=True) if send else None
        source = BizreachPickupSource(max_candidates=max_candidates, kind=kind, client=client)
        # ピックアップは会員ステータス条件を適用しない（ユーザー指定・本命リストのため）。
        pipeline = ScoutPipeline(repo=repo, sender=sender, apply_status_filter=False)
        report = pipeline.run(source, send=send)
        click.echo(report.summary())
    finally:
        if client:
            client.close()
        repo.close()


@cli.command(name="run-resends")
@click.option("--send/--no-send", default=True, help="再送を行うか")
@click.option("--headless/--no-headless", default=True)
def run_resends(send: bool, headless: bool) -> None:
    """初回送信からN日後の再送を送信する。"""
    from .scheduler import run_due_resends

    repo = Repository()
    client = None
    sender = None
    try:
        if send:
            from .bizreach.api import BizreachApi
            from .bizreach.api_sender import ApiScoutSender
            from .bizreach.client import BizreachClient

            client = BizreachClient(headless=headless).start()
            client.ensure_logged_in()
            sender = ApiScoutSender(BizreachApi(client))
        report = run_due_resends(repo, sender)
        click.echo(report.summary())
    finally:
        if client:
            client.close()
        repo.close()


@cli.command(name="import-consultants")
@click.argument("docx_path", type=click.Path(exists=True))
@click.option("--out", default="config/consultants.json", help="出力先JSON")
def import_consultants(docx_path: str, out: str) -> None:
    """consultant_profiles_v2.docx を consultants.json に変換する。"""
    from .consultant_import import import_to_json

    n = import_to_json(docx_path, out)
    click.echo(f"{n} 名のコンサルタントを {out} に書き出しました。内容を必ず確認してください。")


@cli.command()
@click.argument("member_no")
@click.option("--kind", type=click.Choice(["first", "resend", "both"]), default="both")
def preview(member_no: str, kind: str) -> None:
    """保存済みスカウトを表示する。"""
    repo = Repository()
    kinds = ["first", "resend"] if kind == "both" else [kind]
    for k in kinds:
        row = repo.get_scout(member_no, k)
        if not row:
            click.echo(f"[{k}] 未生成: {member_no}")
            continue
        label = "初回送信用" if k == "first" else "再送用"
        click.echo(f"\n【{label}】 status={row['status']} scheduled_at={row['scheduled_at']}")
        click.echo("件名:\n```\n" + row["subject"] + "\n```")
        click.echo("本文:\n```\n" + row["body"] + "\n```")
    repo.close()


@cli.command()
def report() -> None:
    """送信状況のサマリと要確認候補者を表示する。"""
    repo = Repository()
    counts = repo.counts_by_status()
    click.echo("==== スカウト状況 ====")
    for status, n in counts.items():
        click.echo(f"  {status}: {n}")
    ineligible = repo.ineligible_candidates()
    if ineligible:
        click.echo("\n==== 要確認（対象条件を満たさない候補者）====")
        for row in ineligible:
            click.echo(f"  {row['member_no']}: {row['eligibility_failed']}")
    repo.close()


@cli.command()
@click.option("--headless/--no-headless", default=False,
              help="既定はブラウザ表示（2FAを手動入力するため）")
def login(headless: bool) -> None:
    """ビズリーチに一度ログインしてセッション(storage_state)を保存する。

    2段階認証はブラウザ上で手動入力してください。保存されたセッションファイルは、
    GitHub Actions で運用する場合に secret(BIZREACH_STORAGE_STATE_B64)へ
    base64 で登録すると、CI 上の自動ログイン/2FA を回避できます。
    """
    from .bizreach.client import BizreachClient

    client = BizreachClient(headless=headless).start()
    try:
        client.ensure_logged_in()
        path = client._storage_state_path()
        click.echo(f"\nセッションを保存しました: {path}")
        click.echo("GitHub Actions 用に base64 化するには:")
        click.echo(f"  base64 -w0 {path}    # この出力を secret BIZREACH_STORAGE_STATE_B64 に登録")
    finally:
        client.close()


@cli.command(name="probe-send")
@click.option("--mrccid", help="偵察対象の候補者mrccid。未指定なら検索先頭の候補者を使う")
@click.option("--search-url", help="bizreach の検索結果URL（mrccid未指定時に使用）")
@click.option("--headless/--no-headless", default=True, help="ブラウザをヘッドレスで起動")
def probe_send(mrccid: str | None, search_url: str | None, headless: bool) -> None:
    """スカウト送信フローを安全に偵察する（送信APIとセレクタ特定用・実送信なし）。

    送信ボタン押下の直前に /api/ POST のブロックを武装するため、実際の送信は
    絶対に行われません。data/exports に DOM・スクショ・捕捉した送信POSTを保存します。
    """
    from .bizreach.api import BizreachApi
    from .bizreach.client import BizreachClient
    from .bizreach.send_probe import SendProbe

    client = BizreachClient(headless=headless).start()
    try:
        client.ensure_logged_in()
        target = mrccid
        if not target:
            if not search_url:
                raise click.UsageError("--mrccid か --search-url のどちらかを指定してください。")
            api = BizreachApi(client)
            target = next(iter(api.iter_candidate_ids(search_url, 1)), None)
            if not target:
                click.echo("検索から候補者を取得できませんでした。")
                return
            click.echo(f"検索先頭の候補者を使用: mrccid={target}")
        SendProbe(client).run(target, search_url=search_url)
        click.echo("偵察完了。data/exports の probe_* を確認してください（実送信はしていません）。")
    finally:
        client.close()


@cli.command(name="test-send")
@click.option("--search-url", required=True, help="bizreach の検索結果URL（保存検索）")
@click.option("--mrccid", help="対象候補者mrccid。未指定なら検索先頭の候補者")
@click.option("--headless/--no-headless", default=True)
def test_send(search_url: str, mrccid: str | None, headless: bool) -> None:
    """送信APIを dryRun=True で検証する（実送信なし・payload/権限の確認用）。

    ジョブID取得 → 候補者取得 → 文面生成 → 送信前チェック → dryRun送信 まで実行し、
    各APIレスポンスを表示する。実際のスカウトは送信されない。
    """
    from .bizreach.api import BizreachApi
    from .bizreach.client import BizreachClient

    client = BizreachClient(headless=headless).start()
    try:
        client.ensure_logged_in()
        api = BizreachApi(client)

        from .config import scout_job_id

        # スカウト送信は「会員種別を問わず送れる求人」で行う（設定値を優先）。
        job_id = scout_job_id() or api.get_job_id(search_url)
        click.echo(f"送信求人ID(jobId): {job_id}"
                   f"（検索の求人: {api.get_job_id(search_url)}）")
        if not job_id:
            click.echo("jobId を取得できませんでした。company.yaml の scout_job_id を確認してください。")
            return

        holders = api.get_platinum_scout_holders()
        click.echo(f"[プラチナ残数] {holders.get('count')}（{holders}）")

        target = mrccid or next(iter(api.iter_candidate_ids(search_url, 1)), None)
        if not target:
            click.echo("候補者を取得できませんでした。")
            return
        click.echo(f"対象 mrccid: {target}")

        candidate = api.get_candidate(target)
        if not candidate:
            click.echo("レジュメを取得できませんでした。")
            return

        scout = ScoutGenerator().generate(candidate)
        click.echo(f"生成完了: {candidate.member_no} 件名={scout.first.subject[:40]}...")

        check = api.check_candidates(job_id, [target])
        click.echo(f"\n[送信前チェック] {check}")

        # 会員種別に応じて通常/プラチナへ自動振り分けして dryRun 送信。
        result = api.route_scout(
            job_id=job_id, mrccid=target,
            subject=scout.first.subject, body=scout.first.body,
            dry_run=True,
        )
        click.echo(f"\n[dryRun送信結果] endpoint={result.get('endpoint')} {result}")
        if result.get("status") in (200, 201):
            click.echo(f"\n✅ 送信APIの検証に成功しました（{result.get('endpoint')}"
                       f"・status={result.get('status')}・実送信なし）。")
        elif result.get("endpoint") == "skip":
            click.echo(f"\nℹ️ この候補者は送信対象外でした（{result.get('skipped')}）。"
                       "別の候補者で再確認してください。")
        else:
            click.echo("\n⚠️ 検証に失敗しました。上記レスポンスを確認してください。")
    finally:
        client.close()


@cli.command(name="test-pickup-send")
@click.option("--kind", type=click.Choice(["job", "candidate", "both"]), default="job",
              help="job=本日のピックアップ求人(本命) / candidate / both")
@click.option("--mrccid", help="対象mrccid。未指定ならピックアップ先頭の候補者")
@click.option("--headless/--no-headless", default=True)
def test_pickup_send(kind: str, mrccid: str | None, headless: bool) -> None:
    """ピックアップ送信API(/v2/scouts/pickup)を dryRun=True で検証する（実送信なし）。

    対象条件は無視し、既知の候補者へ dryRun 送信をぶつけてエンドポイントが 200/201 を
    返すか（payload/権限）を確認する。プラチナ残数は消費せず、実送信も行わない。
    本番(dry_run=false)前に、まだ一度も呼ばれていないピックアップ送信経路を潰すためのもの。
    """
    from .bizreach.api import BizreachApi
    from .bizreach.client import BizreachClient
    from .config import scout_job_id
    from .ingest.bizreach_pickup_source import BizreachPickupSource

    client = BizreachClient(headless=headless).start()
    try:
        client.ensure_logged_in()
        api = BizreachApi(client)

        job_id = scout_job_id()
        if not job_id:
            click.echo("scout_job_id が未設定です。company.yaml を確認してください。")
            return
        click.echo(f"送信求人ID(jobId): {job_id}")

        target = mrccid
        if not target:
            src = BizreachPickupSource(max_candidates=1, kind=kind, client=client)
            cand = next(iter(src.iter_candidates()), None)
            target = cand.mrccid if cand else None
        if not target:
            click.echo("ピックアップ候補者を取得できませんでした（--mrccid で明示指定も可）。")
            return
        click.echo(f"対象 mrccid: {target}（対象条件は無視して送信APIのみ検証）")

        subject = "（ピックアップ送信APIテスト・実送信なし）"
        body = ("本メッセージは送信API(/v2/scouts/pickup)の dryRun 検証用です。"
                "実送信は行われません。")
        result = api.send_pickup_scout(job_id, target, subject, body, dry_run=True)
        click.echo(f"\n[dryRun送信結果] endpoint={result.get('endpoint')} {result}")
        if result.get("status") in (200, 201):
            click.echo("\n✅ ピックアップ送信APIの検証に成功しました"
                       f"（status={result.get('status')}・実送信なし・残数消費なし）。"
                       "本番(dry_run=false)へ進めます。")
        else:
            click.echo("\n⚠️ ピックアップ送信APIが 200/201 以外を返しました。"
                       "上記レスポンスを確認してください（本番前に要修正）。")
    finally:
        client.close()


@cli.command(name="probe-pickup")
@click.option("--headless/--no-headless", default=True)
def probe_pickup(headless: bool) -> None:
    """「本日のピックアップ」候補者リストAPIを偵察する（実送信なし）。

    mypage を開いて /api/ の通信を data/exports に保存する。ピックアップ候補者リストの
    エンドポイントを特定するために使う。
    """
    from .bizreach.client import BizreachClient
    from .bizreach.pickup_probe import PickupProbe

    client = BizreachClient(headless=headless).start()
    try:
        client.ensure_logged_in()
        PickupProbe(client).run()
        click.echo("偵察完了。data/exports の pickup_* を確認してください（実送信なし）。")
    finally:
        client.close()


@cli.command()
def doctor() -> None:
    """完全自動運用の起動前チェック（環境・設定・依存を点検）。"""
    from .ops import format_report, overall_ok, run_checks

    checks = run_checks()
    click.echo(format_report(checks))
    raise SystemExit(0 if overall_ok(checks) else 1)


@cli.command()
@click.option("--search-url", help="bizreach の検索結果URL（保存検索）。未指定なら再送のみ")
@click.option("--interval", default=86400, help="サイクル間隔（秒）。既定は1日(86400)")
@click.option("--max", "max_candidates", default=50, help="1サイクルの最大処理件数")
@click.option("--headless/--no-headless", default=True, help="ブラウザをヘッドレスで起動")
@click.option("--once", is_flag=True, help="1サイクルだけ実行して終了")
def serve(search_url: str | None, interval: int, max_candidates: int,
          headless: bool, once: bool) -> None:
    """取り込み→生成→送信→再送を一定間隔で自動実行する常駐サービス。"""
    from .service import serve as _serve

    if get_settings().dry_run:
        click.echo("※ DRY_RUN 有効: 文面は入力しますが実送信は行いません（.env の BIZSCOUT_DRY_RUN）。")
    _serve(
        search_url=search_url,
        interval=interval,
        max_candidates=max_candidates,
        headless=headless,
        once=once,
    )


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
