"""テキストから候補者属性を推定するパーサ群。"""

from __future__ import annotations

import re

from ..models import Education, Gender

MEMBER_NO_RE = re.compile(r"\bBU\d{4,}\b")
_AGE_RE = re.compile(r"(\d{2})\s*歳")
_YEARS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*年")


def parse_member_no(text: str) -> str:
    m = MEMBER_NO_RE.search(text or "")
    return m.group(0) if m else ""


def parse_age(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = _AGE_RE.search(str(value))
    if m:
        return int(m.group(1))
    digits = re.search(r"\d{2}", str(value))
    return int(digits.group(0)) if digits else None


def parse_years(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _YEARS_RE.search(str(value))
    if m:
        return float(m.group(1))
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def parse_gender(value) -> Gender:
    s = str(value or "").strip().lower()
    if not s:
        return Gender.unknown
    if s in {"male", "m", "男", "男性"} or "男" in s:
        return Gender.male
    if s in {"female", "f", "女", "女性"} or "女" in s:
        return Gender.female
    return Gender.unknown


def parse_education(value) -> Education:
    s = str(value or "")
    if not s.strip():
        return Education.unknown
    # 上位から判定（大学院＞大学＞短大…）。
    if any(k in s for k in ("博士", "Ph.D", "PhD", "doctor")):
        return Education.doctor
    if any(k in s for k in ("修士", "大学院", "master", "MBA")):
        return Education.master
    if any(k in s for k in ("大学卒", "学士", "大卒", "bachelor")) or s.strip() in ("大学",):
        return Education.bachelor
    if "大学" in s and "短" not in s:
        return Education.bachelor
    if any(k in s for k in ("短大", "短期大学", "associate")):
        return Education.associate
    if any(k in s for k in ("専門", "vocational")):
        return Education.vocational
    if any(k in s for k in ("高校", "高卒", "high school")):
        return Education.high_school
    return Education.unknown


def split_companies(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in re.split(r"[、,;／/\n]+", str(value)) if p.strip()]
