"""ビズリーチの内部APIから候補者を取り込む CandidateSource。

保存検索(rrsc)→候補者一覧→各レジュメ の順にAPIを呼び、Candidate を生成する。
DOMスクレイピングより堅牢。Playwright とログイン済みセッションが必要。
"""

from __future__ import annotations

from collections.abc import Iterator

from ..logging_config import logger
from ..models import Candidate
from .base import CandidateSource


class BizreachApiSource(CandidateSource):
    def __init__(
        self,
        search_url: str | None,
        max_candidates: int = 50,
        headless: bool = True,
        client=None,
    ):
        self.search_url = search_url
        self.max_candidates = max_candidates
        self.headless = headless
        self._client = client  # 既存クライアントを共有可能

    def iter_candidates(self) -> Iterator[Candidate]:
        from ..bizreach.api import BizreachApi
        from ..bizreach.client import BizreachClient

        owns = self._client is None
        client = self._client or BizreachClient(headless=self.headless)
        if owns:
            client.start()
            client.ensure_logged_in()
        try:
            if not self.search_url:
                logger.info("search_url 未指定のため候補者取り込みをスキップします。")
                return
            api = BizreachApi(client)
            ids = list(api.iter_candidate_ids(self.search_url, self.max_candidates))
            logger.info("APIから %d 件の候補者IDを取得。", len(ids))
            for mrccid in ids:
                cand = api.get_candidate(mrccid)
                if cand:
                    cand.profile_url = f"{api.base}/scout/highclass/resume/{mrccid}"
                    yield cand
                client.human_delay(0.5, 1.5)
        finally:
            if owns:
                client.close()
