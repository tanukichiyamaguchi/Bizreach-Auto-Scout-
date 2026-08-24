"""起動前チェック(ops)の検証。

ネットワーク・実ブラウザ・実認証情報を必要としないよう monkeypatch を活用する。
"""

from __future__ import annotations

from bizreach_scout import ops
from bizreach_scout.ops import Check


def test_run_checks_returns_list_of_check_without_raising():
    """run_checks() は list[Check] を返し、例外を投げないこと。"""
    checks = ops.run_checks()
    assert isinstance(checks, list)
    assert checks  # 空でない
    assert all(isinstance(c, Check) for c in checks)
    # status は許可された値のみ。
    assert all(c.status in {"ok", "warn", "fail"} for c in checks)


def test_format_report_returns_str():
    """format_report() は str を返し、各 status の記号と総合判定を含むこと。"""
    checks = ops.run_checks()
    report = ops.format_report(checks)
    assert isinstance(report, str)
    assert "総合判定" in report


def test_overall_ok_logic():
    """overall_ok() は fail が無ければ True、あれば False。"""
    assert ops.overall_ok([Check("a", "ok", ""), Check("b", "warn", "")]) is True
    assert ops.overall_ok([Check("a", "ok", ""), Check("b", "fail", "")]) is False
    assert ops.overall_ok([]) is True


def test_format_report_marks_each_status():
    """整形結果に ok/warn/fail それぞれの記号が現れること。"""
    checks = [
        Check("ok項目", "ok", "詳細1"),
        Check("warn項目", "warn", "詳細2"),
        Check("fail項目", "fail", "詳細3"),
    ]
    report = ops.format_report(checks)
    assert "✓" in report
    assert "⚠" in report
    assert "✗" in report
    assert "ok項目" in report
    assert "詳細3" in report
    assert "NG" in report  # fail があるので NG


def test_anthropic_api_key_present(monkeypatch):
    """ANTHROPIC_API_KEY があれば該当チェックは ok。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-key")
    check = ops._check_anthropic_api_key()
    assert check.status == "ok"


def test_anthropic_api_key_absent(monkeypatch):
    """ANTHROPIC_API_KEY が無ければ該当チェックは fail。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    check = ops._check_anthropic_api_key()
    assert check.status == "fail"


def test_bizreach_credentials_present(monkeypatch):
    """email/password が揃っていれば ok。"""
    monkeypatch.setenv("BIZREACH_EMAIL", "user@example.com")
    monkeypatch.setenv("BIZREACH_PASSWORD", "secret")
    check = ops._check_bizreach_credentials()
    assert check.status == "ok"


def test_bizreach_credentials_missing(monkeypatch):
    """email/password が欠けていれば fail。"""
    monkeypatch.delenv("BIZREACH_EMAIL", raising=False)
    monkeypatch.delenv("BIZREACH_PASSWORD", raising=False)
    check = ops._check_bizreach_credentials()
    assert check.status == "fail"


def test_kill_switch_present_warns(monkeypatch, tmp_path):
    """kill switch が存在すれば warn。"""
    switch = tmp_path / "STOP"
    switch.write_text("stop", encoding="utf-8")
    settings = ops.get_settings()
    monkeypatch.setattr(type(settings), "kill_switch_path", switch)
    check = ops._check_kill_switch()
    assert check.status == "warn"


def test_dry_run_true_warns(monkeypatch):
    """dry_run=True なら warn（実送信されない旨）。"""
    settings = ops.get_settings()
    monkeypatch.setattr(settings, "dry_run", True)
    check = ops._check_dry_run()
    assert check.status == "warn"


def test_dry_run_false_ok(monkeypatch):
    """dry_run=False なら ok（本番送信有効を明記）。"""
    settings = ops.get_settings()
    monkeypatch.setattr(settings, "dry_run", False)
    check = ops._check_dry_run()
    assert check.status == "ok"
    assert "本番送信" in check.detail


# --- 安全弁の実効値の可視化 ---------------------------------------------------

def test_safety_limits_check_shows_effective_values(monkeypatch):
    """送信上限・送信間隔・状態DBガードの実効値がレポートに出る。"""
    from bizreach_scout.config import get_settings
    from bizreach_scout.ops import _check_safety_limits

    monkeypatch.setenv("BIZSCOUT_MAX_SENDS_PER_RUN", "7")
    monkeypatch.setenv("BIZSCOUT_EXPECT_STATE", "true")
    get_settings.cache_clear()
    c = _check_safety_limits()
    assert c.status == "ok"
    assert "送信上限=7" in c.detail
    assert "状態DB消失ガード=有効" in c.detail
    get_settings.cache_clear()


def test_safety_limits_check_warns_when_state_guard_is_off(monkeypatch):
    """ガードが無効なら warn にして、配線漏れに気づけるようにする。"""
    from bizreach_scout.config import get_settings
    from bizreach_scout.ops import _check_safety_limits

    monkeypatch.setenv("BIZSCOUT_EXPECT_STATE", "false")
    get_settings.cache_clear()
    c = _check_safety_limits()
    assert c.status == "warn"
    assert "状態DB消失ガード=無効" in c.detail
    assert "BIZSCOUT_EXPECT_STATE" in c.detail
    get_settings.cache_clear()


def test_safety_limits_is_part_of_doctor():
    from bizreach_scout.ops import _CHECK_FUNCS, _check_safety_limits

    assert _check_safety_limits in _CHECK_FUNCS


# --- 送信履歴(状態DB)チェック ------------------------------------------------

def _settings_for_state_check(monkeypatch, tmp_path, *, expect_state, dry_run):
    from bizreach_scout.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "db_path", str(tmp_path / "state.db"))
    monkeypatch.setattr(s, "expect_state", expect_state)
    monkeypatch.setattr(s, "dry_run", dry_run)
    return s


def test_sent_history_fail_when_expected_but_empty(monkeypatch, tmp_path):
    """expect_state=true・本番送信なのに送信履歴が空なら fail（保存も送信も止める）。"""
    _settings_for_state_check(monkeypatch, tmp_path, expect_state=True, dry_run=False)
    check = ops._check_sent_history()
    assert check.status == "fail"
    assert "空" in check.detail


def test_sent_history_ok_when_records_exist(monkeypatch, tmp_path):
    """送信履歴が1件でもあれば ok。"""
    from bizreach_scout.storage.repository import Repository

    s = _settings_for_state_check(monkeypatch, tmp_path, expect_state=True, dry_run=False)
    repo = Repository(db_path=s.db_file)
    repo.restore_lost_sends([("BU1234567", "first", "platinum", "2026-07-01T10:00:00")])
    repo.close()
    check = ops._check_sent_history()
    assert check.status == "ok"


def test_sent_history_ok_when_not_expected(monkeypatch, tmp_path):
    """expect_state=false（本当の初回運用）は空でも ok。"""
    _settings_for_state_check(monkeypatch, tmp_path, expect_state=False, dry_run=False)
    check = ops._check_sent_history()
    assert check.status == "ok"


def test_sent_history_warn_on_dry_run(monkeypatch, tmp_path):
    """dry_run なら実害が無いため warn に留める（実行は続く）。"""
    _settings_for_state_check(monkeypatch, tmp_path, expect_state=True, dry_run=True)
    check = ops._check_sent_history()
    assert check.status == "warn"
