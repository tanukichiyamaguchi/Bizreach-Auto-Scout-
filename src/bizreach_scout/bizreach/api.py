"""ビズリーチの内部JSON APIクライアント（候補者検索・レジュメ取得）。

ビズリーチのスカウト画面はReact + JSON APIで動作する。DOMを追うより、
認証済みブラウザコンテキストからAPIを直接呼ぶ方が確実。

判明しているエンドポイント（cr-support.jp）:
- GET  /api/v2/candidates/searchConditions/{rrsc}      保存検索の条件
- POST /api/v2/candidates:search                        候補者一覧（ページング）
- GET  /api/v2/candidates/{mrccid}/resume               候補者レジュメ（会員番号・職歴）
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from ..logging_config import logger
from ..models import Candidate, Education, Employment, Gender

_GRADE_MAP = {
    "Doctor": Education.doctor,
    "Master": Education.master,
    "Masters": Education.master,
    "Bachelor": Education.bachelor,
    "Bachelors": Education.bachelor,
    "Associate": Education.associate,
    "Vocational": Education.vocational,
    "HighSchool": Education.high_school,
}


def _income_label(code: str | None) -> str:
    if not code:
        return ""
    m = re.match(r"Between(\d+)And(\d+)", code)
    if m:
        return f"{m.group(1)}〜{m.group(2)}万円"
    m = re.match(r"Upper(\d+)", code)
    if m:
        return f"{m.group(1)}万円以上"
    m = re.match(r"Under(\d+)", code)
    if m:
        return f"{m.group(1)}万円未満"
    return code


def _ja(node) -> str:
    """{"ja": "...", "en": ...} 形式から日本語を取り出す。"""
    if isinstance(node, dict):
        return (node.get("ja") or "").strip()
    return (node or "").strip() if isinstance(node, str) else ""


def _years_since(period_from: dict | None, now: datetime | None = None) -> float | None:
    if not period_from or "year" not in period_from:
        return None
    now = now or datetime.now()
    y = period_from.get("year")
    mth = period_from.get("month") or 1
    if not y:
        return None
    return round((now.year - y) + (now.month - mth) / 12.0, 1)


def _career_text(ce: dict) -> str:
    """1社分の職務経歴を読みやすいテキストに整形。"""
    parts = []
    for cc in ce.get("companyCareers", []) or []:
        name = _ja(cc.get("name"))
        contents = cc.get("contents", {}) or {}
        lines = contents.get("ja") or []
        body = "\n".join(x for x in lines if x)
        if name or body:
            parts.append((name + "\n" + body).strip())
    return "\n".join(parts)


def resume_to_candidate(resume: dict, mrccid: str | None = None,
                        now: datetime | None = None) -> Candidate | None:
    """レジュメAPIのJSONを Candidate に変換する。"""
    if not isinstance(resume, dict):
        return None
    member_no = resume.get("bizreachUserId") or ""
    mrccid = mrccid or resume.get("mrccid") or ""
    if not member_no and not mrccid:
        return None

    gender = {"Male": Gender.male, "Female": Gender.female}.get(
        resume.get("gender", ""), Gender.unknown
    )

    # --- 学歴・大学 ---
    education = Education.unknown
    university = ""
    edus = resume.get("educations") or []
    if edus:
        education = _GRADE_MAP.get(edus[0].get("schoolGrade", ""), Education.unknown)
        university = _ja(edus[0].get("name"))

    # --- 職歴 ---
    companies = resume.get("companyExperiences") or []
    current_company = current_title = ""
    current_tenure = None
    employments: list[Employment] = []
    prior: list[str] = []
    career_blocks: list[str] = []
    for i, ce in enumerate(companies):
        name = _ja(ce.get("companyName"))
        title = _ja(ce.get("positionName"))
        yrs = _years_since((ce.get("period") or {}).get("from"), now)
        if i == 0:
            current_company, current_title, current_tenure = name, title, yrs
        else:
            if name:
                prior.append(name)
        if name:
            employments.append(Employment(company=name, title=title, years=yrs, industry=""))
        block = _career_text(ce)
        if name or block:
            career_blocks.append(f"■{name}（{title}）\n{block}".strip())

    # --- 要約・自己PR・実績（文面生成の材料）---
    summary_parts = [_ja(resume.get("jobSummary"))]
    for cc in resume.get("coreCompetencies") or []:
        summary_parts.append("・" + _ja(cc))
    summary = "\n".join(p for p in summary_parts if p)

    raw_parts = [
        f"会員番号: {member_no}",
        f"職務要約:\n{_ja(resume.get('jobSummary'))}",
        "職務経歴:\n" + "\n\n".join(career_blocks),
        "自己PR:\n" + _ja(resume.get("specialInstruction")),
    ]
    awards = resume.get("awards") or []
    if awards:
        raw_parts.append("表彰: " + "、".join(
            f"{a.get('year','')}{_ja(a.get('title'))}" for a in awards))
    quals = resume.get("qualifications") or []
    if quals:
        raw_parts.append("資格: " + "、".join(_ja(q.get("name")) for q in quals))
    raw_profile = "\n\n".join(p for p in raw_parts if p.strip().rstrip(":"))

    return Candidate(
        member_no=member_no or mrccid,
        mrccid=mrccid,
        age=resume.get("age"),
        gender=gender,
        education=education,
        university=university,
        current_company=current_company,
        current_title=current_title,
        current_tenure_years=current_tenure,
        prior_companies=prior,
        employments=employments,
        job_function=current_title,
        salary_current=_income_label(resume.get("income")),
        salary_desired=_income_label((resume.get("desiredConditions") or {}).get("income")),
        summary=summary,
        raw_profile=raw_profile,
        source="bizreach",
    )


class BizreachApi:
    """認証済みブラウザコンテキストからビズリーチAPIを呼ぶ。"""

    def __init__(self, client):
        self.client = client
        self.base = client.sel.base_url.rstrip("/")

    @property
    def _req(self):
        return self.client.page.request

    @staticmethod
    def parse_rrsc(search_url: str) -> str | None:
        """検索URLの rrsc（保存検索ID）を取り出す。"""
        try:
            q = parse_qs(urlparse(search_url).query)
            return q.get("rrsc", [None])[0]
        except Exception:  # noqa: BLE001
            return None

    def get_search_condition(self, rrsc: str) -> dict | None:
        try:
            resp = self._req.get(f"{self.base}/api/v2/candidates/searchConditions/{rrsc}")
            if resp.status != 200:
                logger.warning("検索条件の取得に失敗 status=%s", resp.status)
                return None
            data = resp.json()
            return data.get("condition", data)
        except Exception as e:  # noqa: BLE001
            logger.warning("検索条件の取得で例外: %s", e)
            return None

    def search_page(self, condition: dict, page: int, page_size: int = 100) -> dict:
        body = {
            "searchId": None,
            "proposalSearchId": None,
            "paging": {"page": page, "maxPageSize": page_size},
            "condition": condition,
        }
        resp = self._req.post(f"{self.base}/api/v2/candidates:search", data=body)
        if resp.status != 200:
            logger.warning("候補者検索に失敗 status=%s page=%s", resp.status, page)
            return {}
        return resp.json()

    def iter_candidate_ids(self, search_url: str, max_candidates: int = 50) -> Iterator[str]:
        """保存検索から mrccid を順に返す（ページング）。"""
        rrsc = self.parse_rrsc(search_url)
        if not rrsc:
            logger.warning("検索URLから rrsc を取得できません: %s", search_url)
            return
        condition = self.get_search_condition(rrsc)
        if not condition:
            return
        page = 1
        yielded = 0
        while yielded < max_candidates:
            data = self.search_page(condition, page)
            items = data.get("items") or []
            if not items:
                break
            total = data.get("totalCount")
            logger.info("候補者検索 page=%d 取得=%d 総数=%s", page, len(items), total)
            for it in items:
                if yielded >= max_candidates:
                    break
                mid = it.get("mrccid")
                if mid:
                    yielded += 1
                    yield mid
            if not data.get("hasNextPage"):
                break
            page += 1
            self.client.human_delay(1.0, 2.5)

    def get_resume(self, mrccid: str) -> dict | None:
        try:
            resp = self._req.get(f"{self.base}/api/v2/candidates/{mrccid}/resume")
            if resp.status != 200:
                logger.warning("レジュメ取得に失敗 mrccid=%s status=%s", mrccid, resp.status)
                return None
            return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("レジュメ取得で例外 mrccid=%s: %s", mrccid, e)
            return None

    def get_candidate(self, mrccid: str) -> Candidate | None:
        resume = self.get_resume(mrccid)
        if resume is None:
            return None
        return resume_to_candidate(resume, mrccid)
