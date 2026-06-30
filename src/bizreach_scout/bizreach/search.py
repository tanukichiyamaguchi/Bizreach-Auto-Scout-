"""ビズリーチ候補者検索結果の走査。

検索条件はビズリーチの検索画面で保存検索を作成し、その結果URLを search_url として
渡す運用を想定（条件フィルタUIはアカウントにより異なるため）。
"""

from __future__ import annotations

from collections.abc import Iterator

from ..logging_config import logger
from .client import BizreachClient


class BizreachSearch:
    def __init__(self, client: BizreachClient):
        self.client = client
        self.sel = client.sel

    def iter_candidate_urls(
        self, search_url: str | None = None, max_candidates: int = 50
    ) -> Iterator[str]:
        """検索結果から候補者プロフィールURLを順に返す。"""
        page = self.client.page
        url = search_url or self.sel.search_url
        page.goto(url, wait_until="domcontentloaded")
        self.client.human_delay()

        seen: set[str] = set()
        yielded = 0
        while yielded < max_candidates:
            links = page.locator(self.sel.result_link)
            count = links.count()
            if count == 0:
                logger.info("検索結果に候補者リンクが見つかりません（セレクタ要確認）。")
                break

            for i in range(count):
                if yielded >= max_candidates:
                    break
                href = links.nth(i).get_attribute("href")
                if not href:
                    continue
                full = href if href.startswith("http") else self.sel.base_url + href
                if full in seen:
                    continue
                seen.add(full)
                yielded += 1
                yield full

            # 次ページ
            nxt = page.locator(self.sel.next_page)
            if nxt.count() == 0 or yielded >= max_candidates:
                break
            nxt.first.click()
            page.wait_for_load_state("networkidle")
            self.client.human_delay()
