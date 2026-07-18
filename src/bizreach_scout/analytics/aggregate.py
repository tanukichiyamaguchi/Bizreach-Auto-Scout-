"""送信ログの集計（純関数のみ・I/Oなし）。

週次・月次の返信率とセグメント別（年齢帯・学歴・転職回数・トーン・送信枠）の集計。
返信は「その候補者の初回送信が属する週/月」に帰属させる（コホート方式）。
直近の期間は返信がまだ届き得るため、返信率が低めに出る（シート上に注記する）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta


def age_band(age: int | None) -> str:
    """年齢帯ラベル（集計の次元）。"""
    if age is None:
        return "不明"
    if age <= 24:
        return "〜24"
    if age <= 29:
        return "25〜29"
    if age <= 34:
        return "30〜34"
    if age <= 39:
        return "35〜39"
    if age <= 44:
        return "40〜44"
    if age <= 49:
        return "45〜49"
    return "50〜"


def job_change_band(n: int | None) -> str:
    """転職回数帯ラベル。"""
    if n is None:
        return "不明"
    if n <= 0:
        return "0回"
    if n == 1:
        return "1回"
    if n == 2:
        return "2回"
    return "3回以上"


_EDU_LABELS = {
    "doctor": "博士", "master": "大学院卒", "bachelor": "大学卒",
    "associate": "短大・高専卒", "vocational": "専門卒", "high_school": "高校卒",
    "unknown": "不明", "": "不明",
}


def education_label(v: str) -> str:
    return _EDU_LABELS.get(v, v or "不明")


_CHANNEL_LABELS = {
    "platinum": "プラチナ", "normal": "通常", "pickup": "ピックアップ", "": "不明",
}


def channel_label(v: str) -> str:
    return _CHANNEL_LABELS.get(v, v or "不明")


def parse_db_datetime(s: str | None) -> datetime | None:
    """DBの naive ISO 文字列を datetime に（JST とみなす）。不正値は None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


@dataclass
class SentRecord:
    """analytics_rows() の1行（会員単位）。"""

    member_no: str
    first_sent_at: datetime
    resent_at: datetime | None
    replied: bool
    replied_at: datetime | None
    detected_by: str
    candidate_name: str
    note: str
    channel: str
    tone_key: str
    model: str
    source: str
    age: int | None
    age_band: str
    gender: str
    education: str
    university: str
    current_company: str
    current_title: str
    job_change_count: int | None
    tenure_years: float | None
    salary_current: str
    candidate_class: str
    status_flags: str

    @classmethod
    def from_row(cls, r) -> SentRecord | None:
        first = parse_db_datetime(r["first_sent_at"])
        if first is None:
            return None
        return cls(
            member_no=r["member_no"],
            first_sent_at=first,
            resent_at=parse_db_datetime(r["resent_at"]),
            replied=bool(r["replied"]),
            replied_at=parse_db_datetime(r["replied_at"]),
            detected_by=r["detected_by"] or "",
            candidate_name=r["candidate_name"] or "",
            note=r["note"] or "",
            channel=r["channel"] or "",
            tone_key=r["tone_key"] or "",
            model=r["model"] or "",
            source=r["source"] or "",
            age=r["age"],
            age_band=r["age_band"] or age_band(r["age"]),
            gender=r["gender"] or "",
            education=r["education"] or "",
            university=r["university"] or "",
            current_company=r["current_company"] or "",
            current_title=r["current_title"] or "",
            job_change_count=r["job_change_count"],
            tenure_years=r["tenure_years"],
            salary_current=r["salary_current"] or "",
            candidate_class=r["candidate_class"] or "",
            status_flags=r["status_flags"] or "",
        )


@dataclass
class PeriodStat:
    """1期間（週または月）の送信・返信集計。"""

    label: str          # 例 "2026-W29（7/13〜7/19）" / "2026-07"
    start: date
    sent: int
    replied: int

    @property
    def rate(self) -> float:
        return self.replied / self.sent if self.sent else 0.0


def _week_start(d: date) -> date:
    """ISO週（月曜始まり）の開始日。"""
    return d - timedelta(days=d.weekday())


def _week_label(start: date) -> str:
    iso = start.isocalendar()
    end = start + timedelta(days=6)
    return (f"{iso.year}-W{iso.week:02d}"
            f"（{start.month}/{start.day}〜{end.month}/{end.day}）")


def weekly_summary(records: list[SentRecord], *, now: datetime,
                   weeks: int = 26) -> list[PeriodStat]:
    """直近 weeks 週の週次集計（古い順）。返信は初回送信週に帰属。"""
    this_week = _week_start(now.date())
    starts = [this_week - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    buckets: dict[date, list[SentRecord]] = {s: [] for s in starts}
    for rec in records:
        ws = _week_start(rec.first_sent_at.date())
        if ws in buckets:
            buckets[ws].append(rec)
    return [
        PeriodStat(label=_week_label(s), start=s,
                   sent=len(buckets[s]),
                   replied=sum(1 for r in buckets[s] if r.replied))
        for s in starts
    ]


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _prev_month(d: date) -> date:
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


def monthly_summary(records: list[SentRecord], *, now: datetime,
                    months: int = 12) -> list[PeriodStat]:
    """直近 months ヶ月の月次集計（古い順）。返信は初回送信月に帰属。"""
    starts: list[date] = []
    m = _month_start(now.date())
    for _ in range(months):
        starts.append(m)
        m = _prev_month(m)
    starts.reverse()
    buckets: dict[date, list[SentRecord]] = {s: [] for s in starts}
    for rec in records:
        ms = _month_start(rec.first_sent_at.date())
        if ms in buckets:
            buckets[ms].append(rec)
    return [
        PeriodStat(label=f"{s.year}-{s.month:02d}", start=s,
                   sent=len(buckets[s]),
                   replied=sum(1 for r in buckets[s] if r.replied))
        for s in starts
    ]


@dataclass
class SegmentRow:
    segment: str
    sent: int
    replied: int

    @property
    def rate(self) -> float:
        return self.replied / self.sent if self.sent else 0.0


@dataclass
class SegmentTable:
    title: str          # 例 "年齢帯別"
    rows: list[SegmentRow]


def segment_summary(records: list[SentRecord], key_fn: Callable[[SentRecord], str],
                    title: str, order: list[str] | None = None) -> SegmentTable:
    """任意の次元でのセグメント集計。order 指定時はその順、無指定は送信数の多い順。"""
    buckets: dict[str, list[SentRecord]] = {}
    for rec in records:
        buckets.setdefault(key_fn(rec) or "不明", []).append(rec)
    keys = ([k for k in order if k in buckets] if order
            else sorted(buckets, key=lambda k: -len(buckets[k])))
    rows = [
        SegmentRow(segment=k, sent=len(buckets[k]),
                   replied=sum(1 for r in buckets[k] if r.replied))
        for k in keys
    ]
    return SegmentTable(title=title, rows=rows)


AGE_BAND_ORDER = ["〜24", "25〜29", "30〜34", "35〜39", "40〜44", "45〜49", "50〜", "不明"]
EDU_ORDER = ["大学卒", "大学院卒", "博士", "短大・高専卒", "専門卒", "高校卒", "不明"]
JOB_CHANGE_ORDER = ["0回", "1回", "2回", "3回以上", "不明"]


def standard_segments(records: list[SentRecord]) -> list[SegmentTable]:
    """定番のセグメント6表（年齢帯・学歴・転職回数・トーン・送信枠・会員クラス）。"""
    return [
        segment_summary(records, lambda r: r.age_band, "年齢帯別", AGE_BAND_ORDER),
        segment_summary(records, lambda r: education_label(r.education), "学歴別", EDU_ORDER),
        segment_summary(records, lambda r: job_change_band(r.job_change_count),
                        "転職回数別", JOB_CHANGE_ORDER),
        segment_summary(records, lambda r: r.tone_key or "不明", "トーン別"),
        segment_summary(records, lambda r: channel_label(r.channel), "送信枠別"),
        segment_summary(records, lambda r: r.candidate_class or "不明", "会員クラス別"),
    ]
