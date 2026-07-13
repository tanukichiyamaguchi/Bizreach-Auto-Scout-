"""起動前チェック（preflight/doctor）。

完全自動運用を始める前に、環境が正しく整っているかを点検する純粋ロジック。
各点検は例外を投げず、必ず ``Check`` の status("ok"|"warn"|"fail") に結果を落とす。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import (
    BizreachCredentials,
    consultants_path,
    get_settings,
    project_root,
)
from .logging_config import logger

# status 表示用の記号。
_STATUS_MARK = {"ok": "✓", "warn": "⚠", "fail": "✗"}


@dataclass
class Check:
    """1 項目分の点検結果。"""

    name: str
    status: str  # "ok"|"warn"|"fail"
    detail: str


def _check_anthropic_api_key() -> Check:
    """ANTHROPIC_API_KEY の有無を点検する。"""
    key = get_settings().anthropic_api_key
    if key:
        return Check("ANTHROPIC_API_KEY", "ok", "API キーが設定されています。")
    return Check(
        "ANTHROPIC_API_KEY",
        "fail",
        "ANTHROPIC_API_KEY が未設定です。環境変数に Anthropic の API キーを設定してください。",
    )


def _check_model() -> Check:
    """生成モデル名と拡張思考の設定を表示する（常に ok）。"""
    s = get_settings()
    budget = s.thinking_budget_tokens
    think = f"拡張思考ON({budget}tok)" if budget and budget > 0 else "拡張思考OFF"
    return Check("生成モデル", "ok",
                 f"使用モデル: {s.model} / {think} / max_tokens={s.max_tokens}")


def _check_bizreach_credentials() -> Check:
    """ビズリーチ認証(email/password)の有無を点検する。"""
    try:
        creds = BizreachCredentials.from_env()
    except Exception as exc:  # 念のため: from_env 内の予期せぬ失敗も status に落とす。
        return Check(
            "ビズリーチ認証",
            "fail",
            f"認証情報の読み込みに失敗しました: {exc}",
        )
    missing: list[str] = []
    if not creds.email:
        missing.append("BIZREACH_EMAIL")
    if not creds.password:
        missing.append("BIZREACH_PASSWORD")
    if missing:
        return Check(
            "ビズリーチ認証",
            "fail",
            "未設定の認証情報があります: " + ", ".join(missing),
        )
    return Check("ビズリーチ認証", "ok", "email/password が設定されています。")


def _check_selectors_override() -> Check:
    """セレクタ上書きファイルの有無を点検する。"""
    path = project_root() / "config" / "bizreach_selectors.yaml"
    if path.exists():
        return Check("セレクタ上書き", "ok", f"上書きファイルを検出: {path}")
    return Check(
        "セレクタ上書き",
        "warn",
        (
            "config/bizreach_selectors.yaml が存在しません。"
            "既定の想定値のままでは送信が正しく動かない可能性があります。"
            "ブラウザ開発者ツールで実際のセレクタを確認し、上書きファイルを作成してください。"
        ),
    )


def _check_consultants() -> Check:
    """コンサルタントデータが実データかサンプルかを点検する。"""
    try:
        path = consultants_path()
    except Exception as exc:
        return Check("コンサルタントデータ", "fail", f"データ判定に失敗しました: {exc}")
    if path.name == "consultants.json" and path.exists():
        return Check("コンサルタントデータ", "ok", f"実データを使用: {path}")
    return Check(
        "コンサルタントデータ",
        "warn",
        (
            f"サンプルデータを使用中です: {path}。"
            "config/consultants.json に実データを配置してください。"
        ),
    )


def _check_db_writable() -> Check:
    """DB ファイルの親ディレクトリが作成・書き込み可能かを点検する。"""
    db_file = get_settings().db_file
    parent = db_file.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / ".bizscout_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        return Check(
            "DB 書き込み",
            "fail",
            f"DB ディレクトリへ書き込めません ({parent}): {exc}",
        )
    return Check("DB 書き込み", "ok", f"DB ディレクトリへ書き込み可能です: {db_file}")


def _check_kill_switch() -> Check:
    """kill switch の存在を点検する（存在すれば送信停止中）。"""
    path = get_settings().kill_switch_path
    try:
        exists = path.exists()
    except Exception as exc:
        return Check("kill switch", "warn", f"存在確認に失敗しました ({path}): {exc}")
    if exists:
        return Check(
            "kill switch",
            "warn",
            f"kill switch が存在します ({path})。送信は停止されます。",
        )
    return Check("kill switch", "ok", "kill switch はありません（送信可能）。")


def _check_dry_run() -> Check:
    """dry_run 設定を点検する。"""
    if get_settings().dry_run:
        return Check(
            "dry_run",
            "warn",
            "dry_run=True です。実際のスカウト送信は行われません（テストモード）。",
        )
    return Check(
        "dry_run",
        "ok",
        "dry_run=False です。【注意】本番送信が有効です。実際にスカウトが送信されます。",
    )


def _check_playwright() -> Check:
    """playwright が import 可能かを点検する。"""
    try:
        import playwright  # noqa: F401
    except Exception as exc:
        return Check(
            "playwright",
            "fail",
            (
                f"playwright を import できません: {exc}。"
                "`pip install playwright` の後 `playwright install` を実行してください。"
            ),
        )
    return Check("playwright", "ok", "playwright を import できます。")


def _check_chromium() -> Check:
    """chromium ブラウザの利用可否を best-effort で点検する。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return Check(
            "chromium",
            "warn",
            "playwright が未導入のため chromium を確認できません。",
        )
    try:
        with sync_playwright() as p:
            executable = p.chromium.executable_path
    except Exception as exc:
        return Check(
            "chromium",
            "warn",
            (
                f"chromium のパスを取得できません: {exc}。"
                "`playwright install chromium` を実行してください。"
            ),
        )
    try:
        if executable and Path(executable).exists():
            return Check("chromium", "ok", f"chromium を検出: {executable}")
    except Exception:
        # パス判定で失敗しても致命的ではないため warn に落とす。
        pass
    return Check(
        "chromium",
        "warn",
        (
            "chromium の実行ファイルが見つかりません。"
            "`playwright install chromium` を実行してください。"
        ),
    )


def _check_storage_state_dir() -> Check:
    """storage_state（ログインセッション）保存先の書き込み可否を点検する。"""
    try:
        creds = BizreachCredentials.from_env()
        target = get_settings().resolve(creds.storage_state)
    except Exception as exc:
        return Check(
            "セッション保存先",
            "warn",
            f"保存先の判定に失敗しました: {exc}",
        )
    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / ".bizscout_state_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        return Check(
            "セッション保存先",
            "warn",
            f"セッション保存先へ書き込めません ({parent}): {exc}",
        )
    return Check("セッション保存先", "ok", f"セッション保存先へ書き込み可能です: {target}")


def _check_scout_rules() -> Check:
    """scout_rules.yaml が型付きスキーマで検証を通るか点検する。

    タイポ（未知キー）や型不正があれば送信前に fail として検知し、
    「条件が無言で消える」事故を起動時に潰す。
    """
    from .config import scout_rules

    try:
        rules = scout_rules()
    except Exception as exc:  # ValidationError 等
        return Check("scout_rules.yaml", "fail",
                     f"設定の検証に失敗しました（タイポ/型不正の可能性）: {exc}")
    n = len(rules.get("eligibility", {}).get("job_changes_exclude", []))
    return Check("scout_rules.yaml", "ok",
                 f"設定の検証に成功しました（転職回数ブラケット {n} 件）。")


# run_checks() で実行する点検関数の一覧（表示順）。
_CHECK_FUNCS = (
    _check_anthropic_api_key,
    _check_model,
    _check_scout_rules,
    _check_bizreach_credentials,
    _check_selectors_override,
    _check_consultants,
    _check_db_writable,
    _check_kill_switch,
    _check_dry_run,
    _check_playwright,
    _check_chromium,
    _check_storage_state_dir,
)


def run_checks() -> list[Check]:
    """全ての点検を実行し、結果を順に返す。例外は投げない。"""
    checks: list[Check] = []
    for func in _CHECK_FUNCS:
        try:
            checks.append(func())
        except Exception as exc:  # 個別点検の予期せぬ失敗も status に落とす。
            logger.warning("点検に失敗しました: %s: %s", func.__name__, exc)
            checks.append(
                Check(func.__name__, "fail", f"点検中に予期せぬエラー: {exc}")
            )
    return checks


def overall_ok(checks: list[Check]) -> bool:
    """fail が一つも無ければ True を返す（warn は許容）。"""
    return not any(c.status == "fail" for c in checks)


def format_report(checks: list[Check]) -> str:
    """点検結果を人間可読のレポート文字列へ整形する。"""
    lines: list[str] = ["起動前チェック結果", "=" * 32]
    for c in checks:
        mark = _STATUS_MARK.get(c.status, "?")
        lines.append(f"{mark} [{c.status.upper()}] {c.name}: {c.detail}")
    lines.append("-" * 32)
    if overall_ok(checks):
        warn_count = sum(1 for c in checks if c.status == "warn")
        if warn_count:
            lines.append(f"総合判定: OK（警告 {warn_count} 件。内容を確認してください）")
        else:
            lines.append("総合判定: OK（全ての点検をパスしました）")
    else:
        fail_count = sum(1 for c in checks if c.status == "fail")
        lines.append(f"総合判定: NG（失敗 {fail_count} 件。修正が必要です）")
    return "\n".join(lines)
