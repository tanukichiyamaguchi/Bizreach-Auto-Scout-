"""本日のピックアップ（求人/候補者）から候補者を取り込む CandidateSource。

ピックアップはビズリーチ旧画面(mypage)の機能で、プラチナ残数を消費せずに送信できる。
候補者は resume-id（数値）で描画され、mrccidは表に出ていない。そこで freescout リンクを
クリックしてレジュメのライトボックスを開き、コピー用URLから mrccid を取り出す
（resume-id → mrccid の橋渡し）。以降は既存の /v2 レジュメAPIで Candidate を作る。

送信は無料枠の /v2/scouts/pickup を使う（ApiScoutSender(pickup=True)）。
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from ..logging_config import logger
from ..models import Candidate
from .base import CandidateSource

_MRCCID_RE = re.compile(r"mrccid=([A-Za-z0-9_-]+)")


class BizreachPickupSource(CandidateSource):
    def __init__(self, max_candidates: int = 20, kind: str = "job",
                 headless: bool = True, client=None):
        self.max_candidates = max_candidates
        self.kind = kind  # "job"(本命=ピックアップ求人) / "candidate" / "both"
        self.headless = headless
        self._client = client

    def _prefixes(self) -> list[str]:
        if self.kind == "candidate":
            return ["pick-up-candidate"]
        if self.kind == "both":
            return ["pick-up-job", "pick-up-candidate"]
        return ["pick-up-job"]  # 既定は本命のピックアップ求人

    def iter_candidates(self) -> Iterator[Candidate]:
        from ..bizreach.api import BizreachApi
        from ..bizreach.client import BizreachClient

        owns = self._client is None
        client = self._client or BizreachClient(headless=self.headless)
        if owns:
            client.start()
            client.ensure_logged_in()
        try:
            api = BizreachApi(client)
            page = client.page
            page.goto(f"{api.base}/mypage/", wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:  # noqa: BLE001
                pass
            client.human_delay(2.0, 3.5)

            resume_ids = self._collect_resume_ids(page)
            logger.info("ピックアップ(%s) 未送信の対象: %d件", self.kind, len(resume_ids))

            for rid in resume_ids[: self.max_candidates]:
                mrccid = self._resolve_mrccid(page, rid)
                if not mrccid:
                    logger.info("mrccid取得失敗 resume-id=%s（スキップ）", rid)
                    continue
                cand = api.get_candidate(mrccid)
                if cand:
                    cand.profile_url = f"{api.base}/scout/highclass/resume/{mrccid}"
                    cand.source = "bizreach_pickup"
                    yield cand
                client.human_delay(0.8, 1.8)
        finally:
            if owns:
                client.close()

    def _collect_resume_ids(self, page) -> list[str]:
        """対象セクションの freescout から未送信(SCOUTED以外)の resume-id を集める。"""
        ids: list[str] = []
        for prefix in self._prefixes():
            loc = page.locator(f'li[data-itemid^="{prefix}"] a.freescout')
            for i in range(loc.count()):
                a = loc.nth(i)
                rid = a.get_attribute("data-resume-id")
                status = a.get_attribute("data-scount-status") or ""
                if rid and status != "SCOUTED":
                    ids.append(rid)
        return list(dict.fromkeys(ids))  # 重複除去・順序維持

    def _resolve_mrccid(self, page, resume_id: str) -> str | None:
        """freescoutを開き、コピー用URL(#jsi_lap_url_copy)から mrccid を取り出して閉じる。"""
        try:
            link = page.locator(f'a.freescout[data-resume-id="{resume_id}"]').first
            link.click(timeout=6000)
            copy = page.locator("#jsi_lap_url_copy")
            copy.wait_for(state="attached", timeout=8000)
            txt = copy.get_attribute("data-clipboard-text") or ""
            m = _MRCCID_RE.search(txt)
            mrccid = m.group(1) if m else None
            self._close_lightbox(page)
            return mrccid
        except Exception as e:  # noqa: BLE001
            logger.warning("ライトボックスからのmrccid取得で例外 resume-id=%s: %s", resume_id, e)
            self._close_lightbox(page)
            return None

    def _close_lightbox(self, page) -> None:
        for sel in ("#jsi_lightbox_close", ".lightbox-close", "a.dialog-cancel",
                    "[class*='lightbox'] .close"):
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    loc.first.click(timeout=2000)
                    return
            except Exception:  # noqa: BLE001
                continue
        try:
            page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
