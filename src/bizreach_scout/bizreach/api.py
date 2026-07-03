"""ビズリーチの内部JSON APIクライアント（候補者検索・レジュメ取得）。

ビズリーチのスカウト画面はReact + JSON APIで動作する。DOMを追うより、
認証済みブラウザコンテキストからAPIを直接呼ぶ方が確実。

判明しているエンドポイント（cr-support.jp）:
- GET  /api/v2/candidates/searchConditions/{rrsc}      保存検索の条件
- POST /api/v2/candidates:search                        候補者一覧（ページング）
- GET  /api/v2/candidates/{mrccid}/resume               候補者レジュメ（会員番号・職歴）
"""

from __future__ import annotations

import json
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
        intention=resume.get("intention") or [],
        resume_updated_status=resume.get("resumeUpdatedStatus") or "",
        contract_plan=resume.get("contractPlan") or "",
        candidate_class=resume.get("candidateClass") or "",
    )


class BizreachApi:
    """認証済みブラウザコンテキストからビズリーチAPIを呼ぶ。"""

    def __init__(self, client):
        self.client = client
        self.base = client.sel.base_url.rstrip("/")
        self._platinum_remaining: int | None = None  # プラチナ残数キャッシュ

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
    # フロントJS(sendScoutCandidates / sendScout(platinum))から判明したAPI契約:
    #   事前確認: POST /api/v2/scouts/checkCandidates  body={jobId, mrccids}
    #             → {candidates:[{mrccid, error, data}], errors}
    #   通常送信: POST /api/v2/scouts/candidates
    #             body={subject, body, dryRun, jobId, mrccids[], isReservation,
    #                   reminder, oneTimeToken}（oneTimeToken必要）
    #   プラチナ: POST /api/v2/scouts/platinum
    #             body={subject, body, dryRun, jobId, mrccid, isReservation,
    #                   reminder}（単数mrccid・oneTimeToken不要）
    #   共通: header Content-Type:application/json, x-idempotency-key(必須),
    #         任意 x-search-id / x-screen-type。認証はセッションcookie。
    # dryRun=True で実送信せずサーバ側検証のみ（安全確認用）。
    # 会員種別の振り分けは checkCandidates の error に基づく（ClassMismatch→プラチナ）。

    # 検索由来の送信であることを示す画面種別（JSのScreenType enumより）。
    SCREEN_TYPE_SAVED = "resume_search_with_saved_condition"

    @staticmethod
    def _reminder_obj(reminder: dict | None) -> dict | None:
        """reminderは null か {daysAfter, subject, body}（daysAfter∈ThreeDays等）。
        誤って文字列が来た場合は None に落として型不一致(400)を防ぐ。"""
        return reminder if isinstance(reminder, dict) else None

    def _post_json(self, path: str, payload: dict, extra_headers: dict) -> dict:
        headers = {"Content-Type": "application/json", **extra_headers}
        # 明示的にJSON文字列化（非ASCIIをそのまま送る）。dictでも可だが曖昧さを排除。
        resp = self._req.post(f"{self.base}{path}", headers=headers,
                              data=json.dumps(payload, ensure_ascii=False))
        out = {"status": resp.status}
        try:
            out.update(resp.json())
        except Exception:  # noqa: BLE001
            out["text"] = resp.text()[:2000]
        return out

    def create_one_time_token(self) -> str | None:
        """送信用ワンタイムトークンを取得する（通常送信で必要）。

        注: 生成エンドポイントのパスはフロントJSのバンドルに現れず未確定。
        判明している候補パスを順に試す。取得できなければ None。
        """
        for path in ("/api/v2/oneTimeTokens", "/api/v2/scouts/oneTimeTokens",
                     "/api/v2/oneTimeToken", "/api/v2/scouts/oneTimeToken"):
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
        logger.info("ワンタイムトークンを取得できませんでした（通常送信は不可の可能性）。")
        return None

    def get_platinum_scout_holders(self) -> dict:
        """プラチナスカウトの残数を取得する（GET /api/v2/scouts/platinum/holders）。

        戻り値 {"status", "count", "holderType"} 。count が月内の送信可能残数。
        """
        try:
            resp = self._req.get(f"{self.base}/api/v2/scouts/platinum/holders")
            out = {"status": resp.status}
            try:
                out.update(resp.json())
            except Exception:  # noqa: BLE001
                out["text"] = resp.text()[:500]
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("プラチナ残数の取得で例外: %s", e)
            return {"status": 0, "error": str(e)}

    def platinum_remaining(self, refresh: bool = False) -> int | None:
        """プラチナ残数（キャッシュ）。取得できない場合は None（不明）。"""
        if refresh or self._platinum_remaining is None:
            info = self.get_platinum_scout_holders()
            cnt = info.get("count")
            self._platinum_remaining = cnt if isinstance(cnt, int) else None
        return self._platinum_remaining

    def check_candidates(self, job_id: str, mrccids: list[str]) -> dict:
        """送信前チェック。候補者が送信可能か検証する。"""
        try:
            return self._post_json("/api/v2/scouts/checkCandidates",
                                   {"jobId": job_id, "mrccids": mrccids}, {})
        except Exception as e:  # noqa: BLE001
            logger.warning("送信前チェックで例外: %s", e)
            return {"status": 0, "error": str(e)}

    def send_scout(self, job_id: str, mrccid: str, subject: str, body: str,
                   dry_run: bool = True, search_id: str | None = None,
                   reminder: dict | None = None,
                   one_time_token: str | None = None) -> dict:
        """通常スカウトを送信する（POST /api/v2/scouts/candidates・oneTimeToken必要）。"""
        headers = {"x-idempotency-key": str(uuid.uuid4()),
                   "x-screen-type": self.SCREEN_TYPE_SAVED}
        if search_id:
            headers["x-search-id"] = search_id
        payload = {
            "subject": subject, "body": body, "dryRun": dry_run,
            "jobId": job_id, "mrccids": [mrccid], "isReservation": False,
            "reminder": self._reminder_obj(reminder),
            "oneTimeToken": one_time_token,
        }
        try:
            out = self._post_json("/api/v2/scouts/candidates", payload, headers)
        except Exception as e:  # noqa: BLE001
            logger.error("スカウト送信で例外 mrccid=%s: %s", mrccid, e)
            return {"status": 0, "error": str(e)}
        self._log_send_result("通常", mrccid, dry_run, out)
        return out

    def send_platinum_scout(self, job_id: str, mrccid: str, subject: str, body: str,
                            dry_run: bool = True, search_id: str | None = None,
                            reminder: dict | None = None) -> dict:
        """プラチナスカウトを送信する（POST /api/v2/scouts/platinum・単数mrccid・token不要）。"""
        headers = {"x-idempotency-key": str(uuid.uuid4()),
                   "x-screen-type": self.SCREEN_TYPE_SAVED}
        if search_id:
            headers["x-search-id"] = search_id
        payload = {
            "subject": subject, "body": body, "dryRun": dry_run,
            "jobId": job_id, "mrccid": mrccid, "isReservation": False,
            "reminder": self._reminder_obj(reminder),
        }
        try:
            out = self._post_json("/api/v2/scouts/platinum", payload, headers)
        except Exception as e:  # noqa: BLE001
            logger.error("プラチナ送信で例外 mrccid=%s: %s", mrccid, e)
            return {"status": 0, "error": str(e)}
        self._log_send_result("プラチナ", mrccid, dry_run, out)
        return out

    @staticmethod
    def _log_send_result(kind: str, mrccid: str, dry_run: bool, out: dict) -> None:
        if out.get("status") == 200:
            logger.info("%sスカウト %s mrccid=%s (dryRun=%s)",
                        kind, "検証OK" if dry_run else "完了", mrccid, dry_run)
        else:
            logger.warning("%sスカウト送信に失敗 mrccid=%s status=%s body=%s",
                           kind, mrccid, out.get("status"), out)

    def _platinum_send_guarded(self, job_id: str, mrccid: str, subject: str, body: str,
                               dry_run: bool, search_id: str | None,
                               reminder: dict | None, label: str = "platinum") -> dict:
        """残数ガード付きのプラチナ送信（本送信のみ残数を確認・減算）。"""
        remaining = self.platinum_remaining()
        if not dry_run and remaining is not None and remaining <= 0:
            logger.warning("プラチナ残数が0のため送信をスキップ mrccid=%s", mrccid)
            return {"status": 0, "skipped": "PlatinumQuotaExhausted",
                    "endpoint": label, "platinum_remaining": 0}
        out = self.send_platinum_scout(job_id, mrccid, subject, body,
                                       dry_run, search_id, reminder)
        out["endpoint"] = label
        if not dry_run and out.get("status") == 200 \
                and self._platinum_remaining is not None:
            self._platinum_remaining -= 1
        out["platinum_remaining"] = self._platinum_remaining
        return out

    def route_scout(self, job_id: str, mrccid: str, subject: str, body: str,
                    dry_run: bool = True, search_id: str | None = None,
                    reminder: dict | None = None) -> dict:
        """会員種別に応じて通常/プラチナへ振り分けて送信する。

        checkCandidates の error を見て振り分ける:
          - ClassMismatch     → プラチナスカウト（/platinum・token不要）
          - その他の error     → スキップ（既送信・対象外など）
          - error なし        → 通常スカウト（/candidates）。失敗時はプラチナへフォールバック。
        戻り値に "endpoint" を付与する。
        """
        check = self.check_candidates(job_id, [mrccid])
        err = None
        for c in check.get("candidates", []) or []:
            if c.get("mrccid") == mrccid:
                err = c.get("error")
                break

        if err == "ClassMismatch":
            return self._platinum_send_guarded(job_id, mrccid, subject, body,
                                               dry_run, search_id, reminder)
        if err:
            logger.info("送信不可のためスキップ mrccid=%s error=%s", mrccid, err)
            return {"status": 0, "skipped": err, "endpoint": "skip"}

        # error なし → 通常スカウト。tokenが必要でパスが未確定のため、失敗時は
        # プラチナ（token不要・ユーザーの実運用と一致）にフォールバックする。
        token = self.create_one_time_token()
        out = self.send_scout(job_id, mrccid, subject, body,
                              dry_run, search_id, reminder, token)
        out["endpoint"] = "candidates"
        if out.get("status") != 200:
            logger.info("通常送信が失敗(status=%s)。プラチナにフォールバック mrccid=%s",
                        out.get("status"), mrccid)
            fb = self._platinum_send_guarded(job_id, mrccid, subject, body,
                                             dry_run, search_id, reminder,
                                             label="platinum(fallback)")
            if fb.get("status") == 200 or fb.get("skipped"):
                fb["candidates_error"] = out
                return fb
        return out
