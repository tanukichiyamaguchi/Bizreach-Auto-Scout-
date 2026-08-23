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


# --- 消失送信の復元表（sent_backfill.json）----------------------------------

def test_load_sent_backfill_valid_and_invalid(tmp_path):
    from bizreach_scout.analytics.channel_backfill import load_sent_backfill

    path = tmp_path / "sent_backfill.json"
    path.write_text(json.dumps({"entries": [
        ["BU1111111", "first", "pickup", "2026-07-21T18:35:11", "run1"],
        ["BU2222222", "first", "platinum", "2026-07-26T18:21:00"],   # run_id無しも可
        ["BU3333333", "unknown", "platinum", "2026-07-26T18:21:00"],  # kind不正
        ["BU4444444", "first", "mystery", "2026-07-26T18:21:00"],     # channel不正
        ["BU5555555", "first", "pickup", "いつか"],                    # 日時不正
        ["BU6666666", "first"],                                       # 要素不足
    ]}, ensure_ascii=False), encoding="utf-8")
    assert load_sent_backfill(path) == [
        ("BU1111111", "first", "pickup", "2026-07-21T18:35:11"),
        ("BU2222222", "first", "platinum", "2026-07-26T18:21:00"),
    ]


def test_load_sent_backfill_missing_or_broken(tmp_path):
    from bizreach_scout.analytics.channel_backfill import load_sent_backfill

    assert load_sent_backfill(tmp_path / "nope.json") == []
    p = tmp_path / "sent_backfill.json"
    p.write_text("{broken", encoding="utf-8")
    assert load_sent_backfill(p) == []


def test_shipped_sent_backfill_is_consistent():
    """出荷中の復元表: 全初回送信を収録し、(会員番号,種別)が一意で妥当な値のみ。

    この表は「キャッシュが全損してもここから送信履歴を再構築できる」ことを
    担保するもの。実行ログの「初回送信完了」行から抽出した事実だけを持つ。
    """
    from bizreach_scout.analytics.channel_backfill import (
        load_sent_backfill,
        sent_backfill_path,
    )

    assert sent_backfill_path().exists()
    entries = load_sent_backfill()
    # 2026-08-23 時点で553件。運用が続けば増えるため下限で確認する。
    assert len(entries) >= 553
    keys = [(m, k) for m, k, _, _ in entries]
    assert len(keys) == len(set(keys))
    assert all(k == "first" for _, k, _, _ in entries)
    assert all(c in ("platinum", "pickup") for _, _, c, _ in entries)
    assert all(s.startswith("2026-") for _, _, _, s in entries)


def test_shipped_sent_backfill_member_numbers_are_canonical():
    """会員番号は正準形（ゼロ埋めなし）で収録されていること。

    表記ゆれが混じると同じ人を別人として復元し、重複送信防止が効かない。
    """
    from bizreach_scout.analytics.channel_backfill import load_sent_backfill
    from bizreach_scout.models import normalize_member_no

    entries = load_sent_backfill()
    assert [m for m, _, _, _ in entries if normalize_member_no(m) != m] == []
