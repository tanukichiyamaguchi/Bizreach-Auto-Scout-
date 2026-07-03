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
import uuid
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

    def get_saved_search(self, rrsc: str) -> dict | None:
        """保存検索の全体（condition＋job など）を返す。"""
        try:
            resp = self._req.get(f"{self.base}/api/v2/candidates/searchConditions/{rrsc}")
            if resp.status != 200:
                logger.warning("検索条件の取得に失敗 status=%s", resp.status)
                return None
            return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("検索条件の取得で例外: %s", e)
            return None

    def get_search_condition(self, rrsc: str) -> dict | None:
        data = self.get_saved_search(rrsc)
        if data is None:
            return None
        return data.get("condition", data)

    def get_job_id(self, search_url: str) -> str | None:
        """保存検索に紐づく求人ID(jobId)を返す（スカウト送信に必須）。"""
        rrsc = self.parse_rrsc(search_url)
        if not rrsc:
            return None
        data = self.get_saved_search(rrsc)
        if not data:
            return None
        return (data.get("job") or {}).get("jobId")

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

    # --- スカウト送信 --------------------------------------------------------
    # フロントJS(sendScoutCandidates)から判明したAPI契約:
    #   事前確認: POST /api/v2/scouts/checkCandidates  body={jobId, mrccids}
    #   送信:     POST /api/v2/scouts/candidates
    #             header x-idempotency-key(必須), 任意 x-search-id / x-screen-type
    #             body={subject, body, dryRun, jobId, mrccids[], isReservation,
    #                   reminder, oneTimeToken}
    # dryRun=True を指定すると、実送信せずにサーバ側の検証のみ行える（安全確認用）。

    def create_one_time_token(self) -> str | None:
        """送信用のワンタイムトークンを取得する（必要な場合）。パスは複数候補を試行。"""
        for path in ("/api/v2/oneTimeTokens", "/api/v2/scouts/oneTimeTokens",
                     "/api/v2/oneTimeToken"):
            try:
                resp = self._req.post(f"{self.base}{path}", data={})
                if resp.status in (200, 201):
                    data = resp.json()
                    token = (data.get("oneTimeToken") or data.get("token")
                             or data.get("value"))
                    if token:
                        logger.info("ワンタイムトークンを取得 (%s)", path)
                        return token
            except Exception:  # noqa: BLE001
                continue
        logger.info("ワンタイムトークンは取得できませんでした（不要の可能性）。")
        return None

    def check_candidates(self, job_id: str, mrccids: list[str]) -> dict:
        """送信前チェック。候補者が送信可能か検証する。"""
        body = {"jobId": job_id, "mrccids": mrccids}
        try:
            resp = self._req.post(f"{self.base}/api/v2/scouts/checkCandidates",
                                  headers={"Content-Type": "application/json"}, data=body)
            out = {"status": resp.status}
            try:
                out.update(resp.json())
            except Exception:  # noqa: BLE001
                out["text"] = resp.text()[:2000]
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("送信前チェックで例外: %s", e)
            return {"status": 0, "error": str(e)}

    def send_scout(self, job_id: str, mrccid: str, subject: str, body: str,
                   dry_run: bool = True, search_id: str | None = None,
                   reminder: str | None = None,
                   one_time_token: str | None = None) -> dict:
        """スカウトを送信する（dry_run=Trueで実送信せず検証のみ）。

        戻り値は {"status": HTTPコード, ...レスポンス} 。status==200 で成功。
        """
        headers = {
            "Content-Type": "application/json",
            "x-idempotency-key": str(uuid.uuid4()),  # 二重送信防止（必須）
        }
        if search_id:
            headers["x-search-id"] = search_id
        payload = {
            "subject": subject,
            "body": body,
            "dryRun": dry_run,
            "jobId": job_id,
            "mrccids": [mrccid],
            "isReservation": False,
            "reminder": reminder,          # ThreeDays/FiveDays/TenDays or None
            "oneTimeToken": one_time_token,
        }
        try:
            resp = self._req.post(f"{self.base}/api/v2/scouts/candidates",
                                  headers=headers, data=payload)
            out = {"status": resp.status}
            try:
                out.update(resp.json())
            except Exception:  # noqa: BLE001
                out["text"] = resp.text()[:2000]
            if resp.status == 200:
                logger.info("スカウト送信 %s mrccid=%s (dryRun=%s)",
                            "検証OK" if dry_run else "完了", mrccid, dry_run)
            else:
                logger.warning("スカウト送信に失敗 mrccid=%s status=%s body=%s",
                               mrccid, resp.status, out)
            return out
        except Exception as e:  # noqa: BLE001
            logger.error("スカウト送信で例外 mrccid=%s: %s", mrccid, e)
            return {"status": 0, "error": str(e)}
