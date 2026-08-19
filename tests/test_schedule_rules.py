"""送信曜日の運用ルール（金土日・祝日・祝日前日は通常スカウトを送らない）のテスト。

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
    "search_skip_weekdays": [5, 6, 7],
    "search_skip_holidays": True,
    "search_skip_holiday_eve": True,
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


def test_friday_is_paused():
    """金曜は翌日から休みで読まれずに埋もれるため休止対象。"""
    assert should_send_search_scout(date(2026, 7, 24), RULES) is False  # 金
    assert "金曜日" in search_scout_pause_reason(date(2026, 7, 24), RULES)


def test_day_before_holiday_is_paused():
    """祝日の前日も休止対象（金曜を止めるのと同じ理由）。"""
    # 2026-08-11(火) 山の日 → 前日の 8-10(月) は休止。
    reason = search_scout_pause_reason(date(2026, 8, 10), RULES)
    assert should_send_search_scout(date(2026, 8, 10), RULES) is False
    assert "前日" in reason and "山の日" in reason


def test_day_before_holiday_only_applies_to_holidays():
    """翌日がただの平日なら休止しない（前日判定が広がりすぎないこと）。"""
    # 2026-08-12(水) は平日 → 前日の 8-11 は祝日だが、8-12 自体は送信可。
    assert should_send_search_scout(date(2026, 8, 12), RULES) is True


def test_holiday_eve_can_be_disabled():
    """search_skip_holiday_eve を切れば前日は休止しない。"""
    rules = {"schedule": dict(RULES["schedule"], search_skip_holiday_eve=False)}
    assert should_send_search_scout(date(2026, 8, 10), rules) is True


def test_production_rules_pause_friday_weekend_holiday_and_eve():
    # 実際の config/scout_rules.yaml の設定を検証する。
    assert should_send_search_scout(date(2026, 7, 24)) is False   # 金
    assert should_send_search_scout(date(2026, 7, 25)) is False   # 土
    assert should_send_search_scout(date(2026, 7, 26)) is False   # 日
    assert should_send_search_scout(date(2026, 7, 20)) is False   # 海の日
    assert should_send_search_scout(date(2026, 8, 10)) is False   # 山の日の前日
    assert should_send_search_scout(date(2026, 7, 28)) is True    # 火（送信日）


def test_production_rules_send_on_monday_to_thursday():
    """通常週は月〜木のみ送信する。"""
    # 2026-08-17(月)〜08-21(金) の週。祝日なし。
    sendable = [d for d in range(17, 22)
                if should_send_search_scout(date(2026, 8, d))]
    assert sendable == [17, 18, 19, 20]  # 月火水木のみ（金は休止）
