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
from contextlib import contextmanager

import click

from .config import get_settings
from .generation.generator import ScoutGenerator, render_for_human
from .ingest.csv_source import CSVSource
from .ingest.text_source import TextSource
from .logging_config import logger, setup_logging
from .storage.repository import Repository


def _exit_on_total_failure(report) -> None:
    """処理対象があるのに1件も送れず全て失敗した場合は異常終了する。

    認証切れ・API仕様変更などで「実行成功(緑)なのに送信0件」になる事故を
    CI/監視で検知できるようにするための安全弁（部分失敗では落とさない）。
    """
    if report.processed > 0 and report.sent == 0 and report.failed > 0:
        raise SystemExit(1)


@contextmanager
def _bizreach_client(headless: bool):
    """ログイン済みの BizreachClient を開き、終了時に必ずクローズする。

    各コマンドに散在していた start→ensure_logged_in→finally close の
    定型処理を1箇所に集約する。
    """
    from .bizreach.client import BizreachClient

    client = BizreachClient(headless=headless).start()
    try:
        client.ensure_logged_in()
        yield client
    finally:
        client.close()


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
            from .export import export_scout

            repo.upsert_candidate(candidate, check_eligibility(candidate))
            repo.record_generated(scout)
            export_scout(scout)
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
        _exit_on_total_failure(report)
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
    from .ingest.bizreach_pickup_source import BizreachPickupSource
    from .pipeline import ScoutPipeline

    if send and get_settings().dry_run:
        click.echo("※ DRY_RUN 有効: 文面は用意しますが実送信は行いません。")

    repo = Repository()
    try:
        with _bizreach_client(headless) as client:
            sender = ApiScoutSender(BizreachApi(client), pickup=True) if send else None
            source = BizreachPickupSource(max_candidates=max_candidates, kind=kind, client=client)
            # ピックアップは会員ステータス条件を適用しない（ユーザー指定・本命リストのため）。
            # 送信は無料枠（プラチナ残数を消費しない）ため、検索スカウトの送信上限
            # （BIZSCOUT_MAX_SENDS_PER_RUN）とは切り分け、処理件数(--max)ぶんまで送信できる。
            pipeline = ScoutPipeline(repo=repo, sender=sender, apply_status_filter=False,
                                     max_sends=max_candidates)
            report = pipeline.run(source, send=send)
            click.echo(report.summary())
            _exit_on_total_failure(report)
    finally:
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
        # 対象があるのに1件も送れず全て失敗した場合は異常終了（緑にしない）。
        if report.due > 0 and report.sent == 0 and report.failed > 0:
            raise SystemExit(1)
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


@cli.group()
def analytics() -> None:
    """スカウト分析（Google Sheets への同期・集計レポート）。"""


def _sheets_client():
    """設定から GspreadSheets を構築する（未設定なら分かりやすく失敗）。"""
    from .analytics.sheets import GspreadSheets

    settings = get_settings()
    if not settings.gsheet_spreadsheet_id or not settings.gsheet_credentials:
        raise click.ClickException(
            "Google Sheets が未設定です。BIZSCOUT_GSHEET_SPREADSHEET_ID と "
            "BIZSCOUT_GSHEET_CREDENTIALS（サービスアカウント鍵JSONのパス）を設定してください。"
            "手順は docs/スカウト分析.md を参照。"
        )
    return GspreadSheets(settings.gsheet_spreadsheet_id, settings.gsheet_credentials)


@analytics.command(name="sync")
@click.option("--charts/--no-charts", default=True, help="チャートの作成/更新を行うか")
@click.option("--trend/--no-trend", default=True,
              help="傾向分析（週1回・Claude生成）の更新判定を行うか")
def analytics_sync(charts: bool, trend: bool) -> None:
    """DBの送信・返信データを Google スプレッドシートへ同期する（ブラウザ不要）。"""
    from .analytics.sync import sync_analytics
    from .analytics.trend import generate_trend_commentary

    sheets = _sheets_client()
    repo = Repository()
    try:
        trend_fn = (lambda w, m, s: generate_trend_commentary(w, m, s)) if trend else None
        report = sync_analytics(repo, sheets, with_charts=charts, trend_fn=trend_fn)
        click.echo(report.summary())
        if report.errors:
            click.echo("警告: " + " / ".join(report.errors))
    finally:
        repo.close()


@analytics.command(name="report")
def analytics_report() -> None:
    """週次・月次の返信率を端末に表示する（Sheets設定なしでも確認できる）。"""
    from datetime import datetime as _dt

    from .analytics.aggregate import (
        SentRecord,
        monthly_summary,
        standard_segments,
        weekly_summary,
    )

    repo = Repository()
    try:
        repo.backfill_sent_log()
        records = [rec for r in repo.analytics_rows()
                   if (rec := SentRecord.from_row(r)) is not None]
        now = _dt.now()
        click.echo(f"==== 送信済み {len(records)}名 / "
                   f"返信 {sum(1 for r in records if r.replied)}名 ====")
        click.echo("\n-- 週次（直近8週）--")
        for s in weekly_summary(records, now=now, weeks=8):
            click.echo(f"  {s.label}: 送信{s.sent} 返信{s.replied} ({s.rate * 100:.1f}%)")
        click.echo("\n-- 月次（直近6ヶ月）--")
        for s in monthly_summary(records, now=now, months=6):
            click.echo(f"  {s.label}: 送信{s.sent} 返信{s.replied} ({s.rate * 100:.1f}%)")
        for table in standard_segments(records)[:3]:
            click.echo(f"\n-- {table.title} --")
            for row in table.rows:
                click.echo(f"  {row.segment}: 送信{row.sent} 返信{row.replied} "
                           f"({row.rate * 100:.1f}%)")
    finally:
        repo.close()


@cli.command(name="sync-replies")
@click.option("--max", "max_checks", default=None, type=int,
              help="1回で確認する最大人数（既定は BIZSCOUT_REPLY_CHECK_MAX）")
@click.option("--headless/--no-headless", default=True)
def sync_replies_cmd(max_checks: int | None, headless: bool) -> None:
    """受信箱スキャンとレジュメ再取得で返信を自動検知し、DBへ記録する。"""
    from .analytics.reply_sync import sync_replies
    from .bizreach.api import BizreachApi
    from .bizreach.inbox import InboxScanner

    settings = get_settings()
    with _bizreach_client(headless) as client:
        api = BizreachApi(client)
        repo = Repository()
        try:
            report = sync_replies(
                api, repo,
                max_checks=max_checks or settings.reply_check_max,
                recent_days=settings.reply_recent_days,
                client=client,
                scanner=InboxScanner(client),
            )
            click.echo(report.summary())
        finally:
            repo.close()


@cli.command(name="probe-replies")
@click.option("--headless/--no-headless", default=True)
def probe_replies(headless: bool) -> None:
    """返信データの偵察（実送信なし）。メッセージ系画面のAPI応答を data/exports にダンプする。"""
    from .bizreach.reply_probe import ReplyProbe

    repo = Repository()
    try:
        # 送信済み候補者を1名選び、レジュメの返信シグナルキーも抜粋する。
        row = repo.conn.execute(
            "SELECT member_no FROM scouts WHERE kind='first' AND status='sent' "
            "ORDER BY sent_at DESC LIMIT 1").fetchone()
        mrccid = None
        if row:
            cand = repo.load_candidate(row["member_no"])
            mrccid = cand.mrccid if cand else None
    finally:
        repo.close()

    with _bizreach_client(headless) as client:
        ReplyProbe(client).run(sent_mrccid=mrccid)
        click.echo("偵察完了。data/exports の reply_* を確認してください（実送信はしていません）。")


@cli.command()
@click.option("--headless/--no-headless", default=False,
              help="既定はブラウザ表示（2FAを手動入力するため）")
def login(headless: bool) -> None:
    """ビズリーチに一度ログインしてセッション(storage_state)を保存する。

    2段階認証はブラウザ上で手動入力してください。保存されたセッションファイルは、
    GitHub Actions で運用する場合に secret(BIZREACH_STORAGE_STATE_B64)へ
    base64 で登録すると、CI 上の自動ログイン/2FA を回避できます。
    """
    with _bizreach_client(headless) as client:
        path = client._storage_state_path()
        click.echo(f"\nセッションを保存しました: {path}")
        click.echo("GitHub Actions 用に base64 化するには:")
        click.echo(f"  base64 -w0 {path}    # この出力を secret BIZREACH_STORAGE_STATE_B64 に登録")


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
    from .bizreach.send_probe import SendProbe

    with _bizreach_client(headless) as client:
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

    with _bizreach_client(headless) as client:
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
    from .config import scout_job_id
    from .ingest.bizreach_pickup_source import BizreachPickupSource

    with _bizreach_client(headless) as client:
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


@cli.command(name="probe-pickup")
@click.option("--headless/--no-headless", default=True)
def probe_pickup(headless: bool) -> None:
    """「本日のピックアップ」候補者リストAPIを偵察する（実送信なし）。

    mypage を開いて /api/ の通信を data/exports に保存する。ピックアップ候補者リストの
    エンドポイントを特定するために使う。
    """
    from .bizreach.pickup_probe import PickupProbe

    with _bizreach_client(headless) as client:
        PickupProbe(client).run()
        click.echo("偵察完了。data/exports の pickup_* を確認してください（実送信なし）。")


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
    result = _serve(
        search_url=search_url,
        interval=interval,
        max_candidates=max_candidates,
        headless=headless,
        once=once,
    )
    # 認証切れ等でサイクルが失敗したまま「成功(exit 0)」になるのを防ぐ。
    # 常駐（--once なし）では途中失敗はログのみで継続するが、最後の結果が
    # エラーで終わった場合は非0で終了する。
    if isinstance(result, dict) and result.get("error"):
        raise SystemExit(1)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
