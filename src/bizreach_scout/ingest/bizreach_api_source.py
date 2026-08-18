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
        skip_mrccids: set[str] | None = None,
        max_pages: int | None = None,
    ):
        self.search_url = search_url
        self.max_candidates = max_candidates
        self.headless = headless
        self._client = client  # 既存クライアントを共有可能
        # 取り込み対象から外す候補者（直近で評価済み）。呼び出し側と共有する集合を
        # 受け取り、取り込んだ候補者をここへ追記していく。複数の保存検索を続けて
        # 処理するとき、同じ候補者を2度取り込まないためにこの共有が必要。
        self.skip_mrccids = skip_mrccids
        self.max_pages = max_pages

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
            kwargs: dict = {}
            if self.skip_mrccids is not None:
                kwargs["skip_mrccids"] = self.skip_mrccids
            if self.max_pages is not None:
                kwargs["max_pages"] = self.max_pages
            ids = list(
                api.iter_candidate_ids(self.search_url, self.max_candidates, **kwargs)
            )
            logger.info("APIから %d 件の候補者IDを取得。", len(ids))
            if self.skip_mrccids is not None:
                # 次の保存検索で同じ候補者を取り込まないよう、共有集合へ追記する。
                self.skip_mrccids.update(ids)
            for mrccid in ids:
                cand = api.get_candidate(mrccid)
                if cand:
                    cand.profile_url = f"{api.base}/scout/highclass/resume/{mrccid}"
                    yield cand
                client.human_delay(0.5, 1.5)
        finally:
            if owns:
                client.close()
