"""分析集計（aggregate.py 純関数）のテスト。"""

from __future__ import annotations

from datetime import datetime

from bizreach_scout.analytics.aggregate import (
    SentRecord,
    age_band,
    channel_label,
    education_label,
    job_change_band,
    monthly_summary,
    segment_summary,
    standard_segments,
    weekly_summary,
)


def _rec(member_no="BU1", first="2026-07-14T10:00:00", replied=False, **over) -> SentRecord:
    base = dict(
        member_no=member_no,
        first_sent_at=datetime.fromisoformat(first),
        resent_at=None, replied=replied, replied_at=None,
        detected_by="", candidate_name="", note="",
        channel="platinum", tone_key="early30s", model="m", source="bizreach",
        age=31, age_band="30〜34", gender="male", education="bachelor",
        university="早稲田大学", current_company="A社", current_title="営業",
        job_change_count=1, tenure_years=4.0, salary_current="850万円",
        candidate_class="Talent", status_flags="will",
    )
    base.update(over)
    return SentRecord(**base)


def test_age_band_edges():
    assert age_band(None) == "不明"
    assert age_band(24) == "〜24"
    assert age_band(25) == "25〜29"
    assert age_band(29) == "25〜29"
    assert age_band(30) == "30〜34"
    assert age_band(44) == "40〜44"
    assert age_band(50) == "50〜"


def test_job_change_band_edges():
    assert job_change_band(None) == "不明"
    assert job_change_band(0) == "0回"
    assert job_change_band(2) == "2回"
    assert job_change_band(3) == "3回以上"
    assert job_change_band(7) == "3回以上"


def test_labels():
    assert education_label("bachelor") == "大学卒"
    assert education_label("") == "不明"
    assert channel_label("platinum") == "プラチナ"
    assert channel_label("") == "不明"


def test_weekly_summary_buckets_and_cohort_attribution():
    now = datetime(2026, 7, 16, 12, 0)  # 木曜（週は 7/13(月)〜7/19(日)）
    records = [
        _rec("BU1", "2026-07-14T10:00:00", replied=True),   # 今週送信・返信あり
        _rec("BU2", "2026-07-13T09:00:00", replied=False),  # 今週送信
        _rec("BU3", "2026-07-08T09:00:00", replied=True),   # 先週送信・返信あり
    ]
    weekly = weekly_summary(records, now=now, weeks=3)
    assert len(weekly) == 3
    assert weekly[-1].sent == 2 and weekly[-1].replied == 1   # 今週
    assert weekly[-2].sent == 1 and weekly[-2].replied == 1   # 先週
    assert weekly[-3].sent == 0
    assert "W" in weekly[-1].label and "7/13" in weekly[-1].label


def test_weekly_summary_year_boundary():
    # 2026-01-01 は木曜。2025-12-29(月)始まりの週に属する。
    now = datetime(2026, 1, 2, 10, 0)
    records = [_rec("BU1", "2025-12-30T10:00:00"), _rec("BU2", "2026-01-02T10:00:00")]
    weekly = weekly_summary(records, now=now, weeks=2)
    assert weekly[-1].sent == 2  # 同じISO週に属する


def test_monthly_summary_buckets():
    now = datetime(2026, 7, 16)
    records = [
        _rec("BU1", "2026-07-01T00:00:00", replied=True),
        _rec("BU2", "2026-06-30T23:59:59"),
        _rec("BU3", "2026-05-15T12:00:00", replied=True),
    ]
    monthly = monthly_summary(records, now=now, months=3)
    assert [m.label for m in monthly] == ["2026-05", "2026-06", "2026-07"]
    assert monthly[2].sent == 1 and monthly[2].replied == 1
    assert monthly[1].sent == 1 and monthly[1].replied == 0
    assert monthly[0].rate == 1.0


def test_segment_summary_with_order_and_rate():
    records = [
        _rec("BU1", age_band="25〜29", replied=True),
        _rec("BU2", age_band="25〜29", replied=False),
        _rec("BU3", age_band="30〜34", replied=False),
    ]
    table = segment_summary(records, lambda r: r.age_band, "年齢帯別",
                            ["〜24", "25〜29", "30〜34"])
    assert [r.segment for r in table.rows] == ["25〜29", "30〜34"]  # 実在セグメントのみ
    assert table.rows[0].sent == 2 and table.rows[0].replied == 1
    assert table.rows[0].rate == 0.5


def test_standard_segments_returns_eleven_tables_in_stable_order():
    tables = standard_segments([_rec()])
    # 先頭2つ・7/8番目はチャートが固定位置で参照するため順序を変えてはならない。
    assert [t.title for t in tables] == [
        "年齢帯別", "学歴別", "転職回数別", "トーン別", "送信枠別", "会員クラス別",
        "曜日別", "時間帯別", "想定年収帯別", "会員ステータス別（重複あり）", "本文文字数帯別",
    ]


def test_weekday_and_hour_band():
    from bizreach_scout.analytics.aggregate import hour_band, weekday_label

    dt = datetime(2026, 7, 14, 16, 30)  # 火曜 16:30
    assert weekday_label(dt) == "火"
    assert hour_band(dt) == "15〜18時"
    assert hour_band(datetime(2026, 7, 14, 18, 0)) == "18〜21時"
    assert hour_band(datetime(2026, 7, 14, 8, 59)) == "〜9時"
    assert hour_band(datetime(2026, 7, 14, 23, 0)) == "21時〜"
    assert weekday_label(None) == "不明" and hour_band(None) == "不明"


def test_body_length_band():
    from bizreach_scout.analytics.aggregate import body_length_band

    assert body_length_band(None) == "不明"
    assert body_length_band(600) == "〜600字"
    assert body_length_band(601) == "601〜800字"
    assert body_length_band(1000) == "801〜1000字"
    assert body_length_band(1500) == "1201字〜"


def test_salary_segments_sorted_numerically():
    from bizreach_scout.analytics.aggregate import salary_segments

    records = [
        _rec("BU1", salary_current="1000万円以上"),
        _rec("BU2", salary_current="600〜750万円"),
        _rec("BU3", salary_current="750〜1000万円", replied=True),
    ]
    table = salary_segments(records)
    assert [r.segment for r in table.rows] == ["600〜750万円", "750〜1000万円", "1000万円以上"]


def test_status_flag_segments_counts_overlapping_flags():
    from bizreach_scout.analytics.aggregate import status_flag_segments

    records = [
        _rec("BU1", status_flags="hot/will", replied=True),  # HOTとWILL両方に計上
        _rec("BU2", status_flags="will"),
        _rec("BU3", status_flags=""),
    ]
    table = status_flag_segments(records)
    by = {r.segment: r for r in table.rows}
    assert by["HOT"].sent == 1 and by["HOT"].replied == 1
    assert by["WILL"].sent == 2 and by["WILL"].replied == 1
    assert by["なし"].sent == 1


def test_segments_by_weekday_and_hour_rates():
    # 火曜16時台×返信あり、月曜18時台×返信なし → 曜日別・時間帯別に正しく割れる。
    records = [
        _rec("BU1", first="2026-07-14T16:30:00", replied=True),   # 火
        _rec("BU2", first="2026-07-13T18:40:00", replied=False),  # 月
    ]
    tables = {t.title: t for t in standard_segments(records)}
    wd = {r.segment: r for r in tables["曜日別"].rows}
    assert wd["火"].replied == 1 and wd["月"].replied == 0
    hr = {r.segment: r for r in tables["時間帯別"].rows}
    assert hr["15〜18時"].rate == 1.0 and hr["18〜21時"].rate == 0.0


def test_empty_records_safe():
    now = datetime(2026, 7, 16)
    assert all(s.sent == 0 for s in weekly_summary([], now=now, weeks=4))
    assert all(s.sent == 0 for s in monthly_summary([], now=now, months=3))
    assert standard_segments([])[0].rows == []
