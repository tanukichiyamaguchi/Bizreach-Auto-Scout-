"""CSV から候補者を取り込む。

ヘッダー名は日本語・英語のエイリアスを許容する。未知の列は無視。
最低限 member_no（会員番号）があれば取り込み可能。
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from ..models import Candidate
from .base import CandidateSource
from .parsing import (
    parse_age,
    parse_education,
    parse_gender,
    parse_member_no,
    parse_years,
    split_companies,
)

# Candidate フィールド -> 受け付けるヘッダー名（小文字比較）。
COLUMN_ALIASES: dict[str, list[str]] = {
    "member_no": ["member_no", "会員番号", "会員no", "id"],
    "age": ["age", "年齢"],
    "gender": ["gender", "性別"],
    "education": ["education", "学歴", "最終学歴"],
    "university": ["university", "大学", "出身大学"],
    "current_company": ["current_company", "現職", "現職企業", "現在の勤務先", "勤務先"],
    "current_title": ["current_title", "役職", "現職役職"],
    "current_tenure_years": ["current_tenure_years", "現職在籍年数", "在籍年数", "勤続年数"],
    "total_experience_years": ["total_experience_years", "経験年数", "総経験年数"],
    "industry": ["industry", "業界", "業種"],
    "job_function": ["job_function", "職種"],
    "prior_companies": ["prior_companies", "前職", "前職企業", "職歴企業"],
    "salary_current": ["salary_current", "現年収", "年収"],
    "salary_desired": ["salary_desired", "希望年収"],
    "languages": ["languages", "語学", "語学力"],
    "desired_jobs": ["desired_jobs", "希望職種"],
    "desired_industries": ["desired_industries", "希望業界"],
    "work_style": ["work_style", "働き方", "興味のある働き方"],
    "summary": ["summary", "職務要約", "自己pr", "自己PR", "職務経歴"],
    "raw_profile": ["raw_profile", "プロフィール", "全文"],
    "profile_url": ["profile_url", "url", "プロフィールurl"],
}


def _build_index(fieldnames: list[str]) -> dict[str, str]:
    """Candidate フィールド -> 実際のCSVヘッダー名 の対応を作る。"""
    lower = {fn.strip().lower(): fn for fn in fieldnames if fn}
    index: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            if a.lower() in lower:
                index[field] = lower[a.lower()]
                break
    return index


def row_to_candidate(row: dict[str, str], index: dict[str, str]) -> Candidate | None:
    def g(field: str) -> str:
        col = index.get(field)
        return (row.get(col) or "").strip() if col else ""

    member_no = g("member_no") or parse_member_no(" ".join(row.values()))
    if not member_no:
        return None

    return Candidate(
        member_no=member_no,
        age=parse_age(g("age")),
        gender=parse_gender(g("gender")),
        education=parse_education(g("education")),
        university=g("university"),
        current_company=g("current_company"),
        current_title=g("current_title"),
        current_tenure_years=parse_years(g("current_tenure_years")),
        total_experience_years=parse_years(g("total_experience_years")),
        industry=g("industry"),
        job_function=g("job_function"),
        prior_companies=split_companies(g("prior_companies")),
        salary_current=g("salary_current"),
        salary_desired=g("salary_desired"),
        languages=g("languages"),
        desired_jobs=g("desired_jobs"),
        desired_industries=g("desired_industries"),
        work_style=g("work_style"),
        summary=g("summary"),
        raw_profile=g("raw_profile"),
        profile_url=g("profile_url"),
        source="csv",
        employments=[],
    )


class CSVSource(CandidateSource):
    def __init__(self, path: str | Path, encoding: str = "utf-8-sig"):
        self.path = Path(path)
        self.encoding = encoding

    def iter_candidates(self) -> Iterator[Candidate]:
        with self.path.open(encoding=self.encoding, newline="") as f:
            reader = csv.DictReader(f)
            index = _build_index(reader.fieldnames or [])
            for row in reader:
                cand = row_to_candidate(row, index)
                if cand:
                    yield cand
