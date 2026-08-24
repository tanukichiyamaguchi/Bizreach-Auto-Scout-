"""restore-state CLI（送信記録の自己修復）のテスト。

キャッシュ消失で状態DBが空になっても、復元表から scouts / sent_log を
再構築して doctor の送信履歴チェックを通せることが要点。
"""

from __future__ import annotations

from click.testing import CliRunner

from bizreach_scout.cli import cli
from bizreach_scout.storage.repository import Repository


def test_restore_state_rebuilds_empty_db(monkeypatch, tmp_path):
    from bizreach_scout.analytics import sync as sync_mod
    from bizreach_scout.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "db_path", str(tmp_path / "state.db"))
    monkeypatch.setattr(sync_mod, "load_sent_backfill", lambda: [
        ("BU7777777", "first", "pickup", "2026-07-06T18:00:00"),
        ("BU8888888", "first", "platinum", "2026-07-07T18:00:00"),
    ])
    monkeypatch.setattr(sync_mod, "load_channel_backfill", lambda: [])

    result = CliRunner().invoke(cli, ["restore-state"])
    assert result.exit_code == 0, result.output
    assert "消失復元+2" in result.output
    assert "計2件" in result.output

    repo = Repository(db_path=s.db_file)
    try:
        assert repo.has_any_sent()  # doctor の送信履歴チェックを通せる状態
    finally:
        repo.close()

    # 冪等: 2回目は何も追加されない。
    result2 = CliRunner().invoke(cli, ["restore-state"])
    assert result2.exit_code == 0
    assert "消失復元+0" in result2.output
    assert "計2件" in result2.output


def test_merge_db_cli_reports_counts(monkeypatch, tmp_path):
    """merge-db CLI がスナップショットを取り込み、件数を報告する。"""
    from bizreach_scout.config import get_settings
    from bizreach_scout.eligibility import check_eligibility
    from bizreach_scout.models import GeneratedScout, ScoutContent

    from .factories import make_candidate

    snap_path = tmp_path / "snapshot.db"
    snap = Repository(db_path=snap_path)
    cand = make_candidate(member_no="BU1111111")
    snap.upsert_candidate(cand, check_eligibility(cand))
    snap.record_generated(GeneratedScout(
        member_no="BU1111111",
        first=ScoutContent(subject="件名", body="本文"),
        resend=ScoutContent(subject="再送", body="再送本文"),
        model="fake", tone_key="early30s"))
    snap.mark_sent("BU1111111", "first", 5, channel="platinum")
    snap.upsert_reply("BU1111111", replied=True, replied_at="2026-08-10T10:00:00",
                      detected_by="auto")
    snap.close()

    s = get_settings()
    monkeypatch.setattr(s, "db_path", str(tmp_path / "current.db"))
    result = CliRunner().invoke(cli, ["merge-db", "--from", str(snap_path)])
    assert result.exit_code == 0, result.output
    assert "送信ログ+1" in result.output
    assert "返信+1" in result.output
    assert "初回送信 計1件・返信あり 計1件" in result.output
