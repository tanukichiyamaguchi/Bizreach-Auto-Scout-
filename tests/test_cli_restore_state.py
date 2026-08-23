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
