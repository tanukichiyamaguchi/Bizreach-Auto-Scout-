"""ビズリーチをブラウザ自動操作して候補者を取り込む CandidateSource。

注意: Playwright とログイン情報が必要。検索結果URL(保存検索)を search_url に渡す。
"""

from __future__ import annotations

from collections.abc import Iterator

from ..logging_config import logger
from ..models import Candidate
from .base import CandidateSource


class BizreachSource(CandidateSource):
    def __init__(
        self,
        search_url: str | None = None,
        max_candidates: int = 50,
        headless: bool = True,
        client=None,
    ):
        self.search_url = search_url
        self.max_candidates = max_candidates
        self.headless = headless
        self._client = client  # 既存クライアントを共有可能（送信と同一セッション）

    def iter_candidates(self) -> Iterator[Candidate]:
        from ..bizreach.client import BizreachClient
        from ..bizreach.profile import extract_candidate
        from ..bizreach.search import BizreachSearch

        owns_client = self._client is None
        client = self._client or BizreachClient(headless=self.headless)
        if owns_client:
            client.start()
            client.ensure_logged_in()
        try:
            search = BizreachSearch(client)
            urls = list(search.iter_candidate_urls(self.search_url, self.max_candidates))
            logger.info("検索結果から %d 件の候補者URLを取得。", len(urls))
            for url in urls:
                cand = extract_candidate(client, url)
                if cand:
                    yield cand
        finally:
            if owns_client:
                client.close()
