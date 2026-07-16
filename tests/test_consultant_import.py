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
