"""ビズリーチ候補者検索結果の走査。

検索条件はビズリーチの検索画面で保存検索を作成し、その結果URLを search_url として
渡す運用を想定（条件フィルタUIはアカウントにより異なるため）。
"""

from __future__ import annotations

from collections.abc import Iterator

from ..config import project_root
from ..logging_config import logger
from .client import BizreachClient


class BizreachSearch:
    def __init__(self, client: BizreachClient):
        self.client = client
        self.sel = client.sel

    def _dump_debug(self, page, name: str) -> None:
        """現在ページのHTMLとスクショを data/exports に保存（セレクタ調整・ログイン確認用）。"""
        try:
            out = project_root() / "data" / "exports"
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{name}.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(out / f"{name}.png"), full_page=True)
            logger.info("デバッグ情報を保存しました（現在URL: %s）: %s.html / %s.png",
                        page.url, name, name)
        except Exception as e:  # noqa: BLE001
            logger.warning("デバッグ情報の保存に失敗: %s", e)

    def _dump_api(self, responses: list) -> None:
        """捕捉したJSONレスポンスを大きい順に保存（候補者一覧APIの構造確認用）。"""
        try:
            out = project_root() / "data" / "exports"
            out.mkdir(parents=True, exist_ok=True)
            items = []
            for r in responses:
                try:
                    body = r.text()
                except Exception:  # noqa: BLE001
                    continue
                if body:
                    items.append((len(body), r.url, body))
            items.sort(key=lambda x: -x[0])
            index = []
            for i, (size, u, body) in enumerate(items[:8]):
                (out / f"api_{i:02d}.json").write_text(body[:2_000_000], encoding="utf-8")
                index.append(f"api_{i:02d}.json\t{size}B\t{u}")
            if index:
                (out / "api_index.txt").write_text("\n".join(index), encoding="utf-8")
                logger.info("APIレスポンスを %d 件保存しました。", len(index))
        except Exception as e:  # noqa: BLE001
            logger.warning("APIレスポンスの保存に失敗: %s", e)

    def iter_candidate_urls(
        self, search_url: str | None = None, max_candidates: int = 50
    ) -> Iterator[str]:
        """検索結果から候補者プロフィールURLを順に返す。"""
        page = self.client.page
        url = search_url or self.sel.search_url

        # React SPA は候補者一覧を非同期取得する。裏のJSONレスポンスを捕捉する。
        captured: list = []

        def _on_response(resp) -> None:
            try:
                ct = (resp.headers or {}).get("content-type", "")
                if "json" in ct.lower():
                    captured.append(resp)
            except Exception:  # noqa: BLE001
                pass

        page.on("response", _on_response)
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        self.client.human_delay(2.0, 4.0)  # 一覧のレンダリング/取得を待つ

        seen: set[str] = set()
        yielded = 0
        while yielded < max_candidates:
            links = page.locator(self.sel.result_link)
            count = links.count()
            if count == 0:
                logger.info("検索結果に候補者リンクが見つかりません（セレクタ要確認）。")
                # 実DOM・API・ログイン状態を確認できるよう保存する。
                self._dump_debug(page, "search_debug")
                self._dump_api(captured)
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
