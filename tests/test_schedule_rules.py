"""送信曜日の運用ルール（土日祝は通常スカウトを送らない）のテスト。

ピックアップ（無料枠）はこの制限を受けず毎日送信する運用のため、この判定の対象外。
"""

from __future__ import annotations

from datetime import date, datetime

from bizreach_scout.schedule_rules import (
    holiday_name,
    search_scout_pause_reason,
    should_send_search_scout,
)

RULES = {"schedule": {
    "search_skip_weekdays": [6, 7],
    "search_skip_holidays": True,
    "search_skip_dates": ["2026-12-30"],
}}


def test_weekday_is_sendable():
    # 2026-07-28 は火曜・祝日でない。
    assert should_send_search_scout(date(2026, 7, 28), RULES) is True
    assert search_scout_pause_reason(date(2026, 7, 28), RULES) == ""


def test_saturday_and_sunday_are_paused():
    assert should_send_search_scout(date(2026, 7, 25), RULES) is False  # 土
    assert should_send_search_scout(date(2026, 7, 26), RULES) is False  # 日
    assert "土曜日" in search_scout_pause_reason(date(2026, 7, 25), RULES)
    assert "日曜日" in search_scout_pause_reason(date(2026, 7, 26), RULES)


def test_national_holiday_on_weekday_is_paused():
    # 2026-07-20（月）海の日、2026-08-11（火）山の日。
    for d, name in ((date(2026, 7, 20), "海の日"), (date(2026, 8, 11), "山の日")):
        reason = search_scout_pause_reason(d, RULES)
        assert should_send_search_scout(d, RULES) is False
        assert "祝日" in reason and name in reason


def test_substitute_holiday_and_kokumin_no_kyujitsu_are_paused():
    # 振替休日・国民の休日も休止対象（jpholiday が判定）。
    assert should_send_search_scout(date(2026, 5, 6), RULES) is False    # 振替休日
    assert should_send_search_scout(date(2026, 9, 22), RULES) is False   # 国民の休日


def test_explicit_skip_date_is_paused():
    assert should_send_search_scout(date(2026, 12, 30), RULES) is False
    assert "休止日" in search_scout_pause_reason(date(2026, 12, 30), RULES)


def test_accepts_datetime_and_uses_its_date():
    assert should_send_search_scout(datetime(2026, 7, 25, 16, 9), RULES) is False  # 土
    assert should_send_search_scout(datetime(2026, 7, 28, 16, 9), RULES) is True   # 火


def test_disabled_rules_send_every_day():
    # 設定が空（既定）なら休止しない＝従来どおり毎日送信。
    empty: dict = {"schedule": {}}
    for d in (date(2026, 7, 25), date(2026, 7, 26), date(2026, 7, 20)):
        assert should_send_search_scout(d, empty) is True


def test_holiday_name_returns_empty_for_normal_day():
    assert holiday_name(date(2026, 7, 28)) == ""
    assert holiday_name(date(2026, 7, 20)) == "海の日"


def test_production_rules_pause_weekend_and_holiday():
    # 実際の config/scout_rules.yaml で土日祝が休止対象になっていること。
    assert should_send_search_scout(date(2026, 7, 25)) is False   # 土
    assert should_send_search_scout(date(2026, 7, 26)) is False   # 日
    assert should_send_search_scout(date(2026, 7, 20)) is False   # 海の日
    assert should_send_search_scout(date(2026, 7, 28)) is True    # 平日
