"""貼り付けテキスト（ビズリーチのプロフィールをコピペ）から候補者を取り込む。

ラベル付き行（例:「現年収：800万円」）を最大限拾いつつ、解析できない情報も
raw_profile としてそのまま保持し、文面生成時にLLMへ渡す。
複数候補者を含む場合は会員番号(BU...)の出現位置で分割する。
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from ..models import Candidate
from .base import CandidateSource
from .parsing import (
    MEMBER_NO_RE,
    parse_age,
    parse_education,
    parse_gender,
    parse_years,
    split_companies,
)

# ラベル -> Candidate フィールド
_LABELS: list[tuple[str, str]] = [
    (r"(?:最終学歴|学歴)", "education"),
    (r"(?:出身大学|大学)", "university"),
    (r"(?:現在の勤務先|現職企業|現職|勤務先)", "current_company"),
    (r"(?:役職|現職役職)", "current_title"),
    (r"(?:現年収|現在の年収|年収)", "salary_current"),
    (r"(?:希望年収)", "salary_desired"),
    (r"(?:業種|業界)", "industry"),
    (r"(?:職種)", "job_function"),
    (r"(?:前職|前職企業|職歴)", "prior_companies"),
    (r"(?:語学|語学力)", "languages"),
    (r"(?:希望職種)", "desired_jobs"),
    (r"(?:希望業界|希望業種)", "desired_industries"),
    (r"(?:興味のある働き方|働き方)", "work_style"),
]


def _extract_labeled(text: str, label_re: str) -> str:
    m = re.search(rf"{label_re}\s*[:：]\s*(.+)", text)
    return m.group(1).strip() if m else ""


def parse_profile_text(chunk: str) -> Candidate | None:
    m = MEMBER_NO_RE.search(chunk)
    if not m:
        return None
    member_no = m.group(0)

    values: dict[str, str] = {}
    for label_re, field in _LABELS:
        if field not in values:  # 先に出たラベルを優先
            v = _extract_labeled(chunk, label_re)
            if v:
                values[field] = v

    age_m = re.search(r"(\d{2})\s*歳", chunk)
    tenure = ""
    tm = re.search(r"(?:現職|在籍)[^\d]{0,6}(\d+(?:\.\d+)?)\s*年", chunk)
    if tm:
        tenure = tm.group(1)

    return Candidate(
        member_no=member_no,
        age=parse_age(age_m.group(0)) if age_m else None,
        gender=parse_gender(chunk if ("男性" in chunk or "女性" in chunk) else ""),
        education=parse_education(values.get("education", "")),
        university=values.get("university", ""),
        current_company=values.get("current_company", ""),
        current_title=values.get("current_title", ""),
        current_tenure_years=parse_years(tenure) if tenure else None,
        industry=values.get("industry", ""),
        job_function=values.get("job_function", ""),
        prior_companies=split_companies(values.get("prior_companies", "")),
        salary_current=values.get("salary_current", ""),
        salary_desired=values.get("salary_desired", ""),
        languages=values.get("languages", ""),
        desired_jobs=values.get("desired_jobs", ""),
        desired_industries=values.get("desired_industries", ""),
        work_style=values.get("work_style", ""),
        raw_profile=chunk.strip(),
        source="text",
    )


def _split_profiles(text: str) -> list[str]:
    """会員番号の出現位置でチャンク分割。1件のみなら全体を返す。"""
    positions = [m.start() for m in MEMBER_NO_RE.finditer(text)]
    if len(positions) <= 1:
        return [text] if text.strip() else []
    chunks = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        chunks.append(text[start:end])
    return chunks


class TextSource(CandidateSource):
    def __init__(self, text: str):
        self.text = text

    @classmethod
    def from_file(cls, path: str) -> TextSource:
        from pathlib import Path

        return cls(Path(path).read_text(encoding="utf-8"))

    def iter_candidates(self) -> Iterator[Candidate]:
        for chunk in _split_profiles(self.text):
            cand = parse_profile_text(chunk)
            if cand:
                yield cand
