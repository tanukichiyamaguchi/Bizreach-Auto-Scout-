"""consultant_import の安全策（0件解析時に既存データを潰さない）のテスト。"""

from __future__ import annotations

import json

import pytest

from bizreach_scout.consultant_import import _existing_consultant_count, import_to_json


def _write_consultants(path, n):
    path.write_text(
        json.dumps({"consultants": [{"id": f"c{i}", "display_name": f"C{i}"} for i in range(n)]},
                   ensure_ascii=False),
        encoding="utf-8",
    )


def test_existing_consultant_count(tmp_path):
    p = tmp_path / "consultants.json"
    assert _existing_consultant_count(p) == 0  # 無ければ0
    _write_consultants(p, 3)
    assert _existing_consultant_count(p) == 3


def test_import_refuses_to_overwrite_nonempty_with_zero(tmp_path, monkeypatch):
    # docx から0名しか取れないとき、既存の非空データは上書きしない（RuntimeError）。
    import bizreach_scout.consultant_import as mod

    monkeypatch.setattr(mod, "parse_docx", lambda p: [])
    out = tmp_path / "consultants.json"
    _write_consultants(out, 13)
    with pytest.raises(RuntimeError, match="上書きを中止"):
        import_to_json("dummy.docx", out)
    # 既存データは保持されている。
    assert _existing_consultant_count(out) == 13


def test_import_allows_zero_when_no_existing_data(tmp_path, monkeypatch):
    # 既存データが無ければ0件でも書き込める（初回など）。
    import bizreach_scout.consultant_import as mod

    monkeypatch.setattr(mod, "parse_docx", lambda p: [])
    out = tmp_path / "consultants.json"
    assert import_to_json("dummy.docx", out) == 0
    assert out.exists()


def test_import_force_overwrites_even_with_zero(tmp_path, monkeypatch):
    # force=True なら明示的に空で上書きできる。
    import bizreach_scout.consultant_import as mod

    monkeypatch.setattr(mod, "parse_docx", lambda p: [])
    out = tmp_path / "consultants.json"
    _write_consultants(out, 5)
    assert import_to_json("dummy.docx", out, force=True) == 0
    assert _existing_consultant_count(out) == 0


# --- 再取り込みでの名簿の増減 ------------------------------------------------


def test_roster_changes_are_logged_on_reimport(tmp_path, caplog):
    """取り込みで名簿に加わる人／外れる人がログに出ること。

    import-consultants は consultants.json を丸ごと作り直すため、名簿から外した人が
    取り込み元 docx に残っていると黙って復活し、そのままスカウト文面で紹介される。
    増減を可視化して目視確認できるようにする。
    """
    import json
    import logging

    from bizreach_scout.consultant_import import _log_roster_changes
    from bizreach_scout.models import ConsultantProfile

    out = tmp_path / "consultants.json"
    out.write_text(json.dumps({"consultants": [
        {"id": "a", "display_name": "残る 太郎"},
        {"id": "b", "display_name": "外れる 次郎"},
    ]}, ensure_ascii=False), encoding="utf-8")

    new = [
        ConsultantProfile(id="a", display_name="残る 太郎"),
        ConsultantProfile(id="c", display_name="加わる 三郎"),
    ]
    with caplog.at_level(logging.INFO, logger="bizscout"):
        _log_roster_changes(out, new)

    text = caplog.text
    assert "加わる 三郎" in text
    assert "外れる 次郎" in text
    # 追加は警告として出す（意図しない復活を見逃さないため）。
    assert any(r.levelno >= logging.WARNING and "加わる 三郎" in r.getMessage()
               for r in caplog.records)


def test_no_roster_change_is_reported_quietly(tmp_path, caplog):
    import json
    import logging

    from bizreach_scout.consultant_import import _log_roster_changes
    from bizreach_scout.models import ConsultantProfile

    out = tmp_path / "consultants.json"
    out.write_text(json.dumps({"consultants": [{"id": "a", "display_name": "同じ 太郎"}]},
                              ensure_ascii=False), encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="bizscout"):
        _log_roster_changes(out, [ConsultantProfile(id="a", display_name="同じ 太郎")])
    assert "変更はありません" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
