"""送信枠（通常スカウト/ピックアップ）の復元対応表のテスト。

対応表は GitHub Actions の実行ログ（どのステップで送ったか）から機械的に抽出した
事実のみを持つ。読み込みが壊れても分析同期を止めないことが要点。
"""

from __future__ import annotations

import json

from bizreach_scout.analytics.channel_backfill import (
    channel_backfill_path,
    load_channel_backfill,
)


def _write(tmp_path, payload):
    path = tmp_path / "channel_backfill.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_loads_valid_entries(tmp_path):
    path = _write(tmp_path, {"entries": [
        ["BU1111111", "first", "pickup"],
        ["BU2222222", "resend", "platinum"],
    ]})
    assert load_channel_backfill(path) == [
        ("BU1111111", "first", "pickup"),
        ("BU2222222", "resend", "platinum"),
    ]


def test_drops_rows_with_unknown_kind_or_channel(tmp_path):
    path = _write(tmp_path, {"entries": [
        ["BU1111111", "first", "pickup"],
        ["BU2222222", "unknown", "platinum"],   # kind が不正
        ["BU3333333", "first", "mystery"],      # channel が不正
        ["BU4444444", "first"],                 # 要素数不足
        "BU5555555",                            # 形式違い
    ]})
    assert load_channel_backfill(path) == [("BU1111111", "first", "pickup")]


def test_missing_file_returns_empty(tmp_path):
    assert load_channel_backfill(tmp_path / "no_such_file.json") == []


def test_broken_json_returns_empty_without_raising(tmp_path):
    path = tmp_path / "channel_backfill.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_channel_backfill(path) == []


def test_shipped_table_is_readable_and_consistent():
    """出荷中の対応表が読め、(会員番号, kind) が一意であること。"""
    path = channel_backfill_path()
    if not path.exists():
        return
    entries = load_channel_backfill()
    keys = [(m, k) for m, k, _ in entries]
    assert len(keys) == len(set(keys)), "同じ (会員番号, kind) が重複している"
    assert all(c in ("platinum", "pickup") for _, _, c in entries)
