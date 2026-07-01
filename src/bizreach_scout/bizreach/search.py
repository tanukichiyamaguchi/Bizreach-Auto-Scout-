"""ビズリーチ候補者検索結果の走査。

検索条件はビズリーチの検索画面で保存検索を作成し、その結果URLを search_url として
渡す運用を想定（条件フィルタUIはアカウントにより異なるため）。
"""

from __future__ import annotations

import json
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

    def _dump_requests(self, requests: list) -> None:
        """捕捉した /api/ リクエストの method/URL/本文を保存（検索の再現用）。"""
        try:
            out = project_root() / "data" / "exports"
            out.mkdir(parents=True, exist_ok=True)
            lines = []
            for r in requests:
                try:
                    body = r.post_data or ""
                except Exception:  # noqa: BLE001
                    body = ""
                lines.append(f"{r.method}\t{r.url}\n{body[:4000]}\n{'-'*60}")
            if lines:
                (out / "api_requests.txt").write_text("\n".join(lines), encoding="utf-8")
                logger.info("APIリクエストを %d 件保存しました。", len(requests))
        except Exception as e:  # noqa: BLE001
            logger.warning("APIリクエストの保存に失敗: %s", e)

    def _probe_candidate_detail(self, page, captured: list) -> None:
        """候補者一覧APIから mrccid を取り出し、詳細APIの候補URLを叩いて保存する。"""
        try:
            mrccid = None
            for r in captured:
                if "candidates:search" in r.url:
                    try:
                        items = json.loads(r.text()).get("items", [])
                        if items:
                            mrccid = items[0].get("mrccid")
                            break
                    except Exception:  # noqa: BLE001
                        continue
            if not mrccid:
                logger.info("mrccid を取得できず、詳細APIの探索をスキップします。")
                return

            base = self.sel.base_url.rstrip("/")
            get_urls = [
                f"{base}/api/v2/candidates/{mrccid}",
                f"{base}/api/v2/candidates/{mrccid}/resume",
                f"{base}/api/v2/candidates/{mrccid}:get",
                f"{base}/api/v2/candidates/{mrccid}/detail",
                f"{base}/api/v2/resumes/{mrccid}",
            ]
            post_specs = [
                (f"{base}/api/v2/candidates:batchGet", {"mrccids": [mrccid]}),
                (f"{base}/api/v2/candidates/resumes:batchGet", {"mrccids": [mrccid]}),
            ]
            out = project_root() / "data" / "exports"
            out.mkdir(parents=True, exist_ok=True)
            summary = [f"probe mrccid={mrccid}"]
            idx = 0
            for u in get_urls:
                try:
                    resp = page.request.get(u)
                    body = resp.text()
                    summary.append(f"GET  {resp.status}  {len(body)}B  {u}")
                    if resp.status == 200 and body.strip().startswith(("{", "[")):
                        (out / f"detail_{idx:02d}.json").write_text(body[:2_000_000], encoding="utf-8")
                        idx += 1
                except Exception as e:  # noqa: BLE001
                    summary.append(f"GET  ERR  {u}  {e}")
            for u, payload in post_specs:
                try:
                    resp = page.request.post(u, data=payload)
                    body = resp.text()
                    summary.append(f"POST {resp.status}  {len(body)}B  {u}")
                    if resp.status == 200 and body.strip().startswith(("{", "[")):
                        (out / f"detail_{idx:02d}.json").write_text(body[:2_000_000], encoding="utf-8")
                        idx += 1
                except Exception as e:  # noqa: BLE001
                    summary.append(f"POST ERR  {u}  {e}")
            (out / "detail_probe.txt").write_text("\n".join(summary), encoding="utf-8")
            logger.info("詳細API探索を保存しました（成功JSON %d 件）。", idx)
        except Exception as e:  # noqa: BLE001
            logger.warning("詳細API探索に失敗: %s", e)

    def iter_candidate_urls(
        self, search_url: str | None = None, max_candidates: int = 50
    ) -> Iterator[str]:
        """検索結果から候補者プロフィールURLを順に返す。"""
        page = self.client.page
        url = search_url or self.sel.search_url

        # React SPA は候補者一覧を非同期取得する。裏のJSON応答/リクエストを捕捉する。
        captured: list = []
        requests: list = []

        def _on_response(resp) -> None:
            try:
                ct = (resp.headers or {}).get("content-type", "")
                if "json" in ct.lower():
                    captured.append(resp)
            except Exception:  # noqa: BLE001
                pass

        def _on_request(req) -> None:
            try:
                if "cr-support.jp/api/" in req.url:
                    requests.append(req)
            except Exception:  # noqa: BLE001
                pass

        page.on("response", _on_response)
        page.on("request", _on_request)
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
                self._dump_requests(requests)
                self._probe_candidate_detail(page, captured)
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
