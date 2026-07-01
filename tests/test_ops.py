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
