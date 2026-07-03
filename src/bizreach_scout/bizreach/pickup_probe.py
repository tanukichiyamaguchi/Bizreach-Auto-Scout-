"""「本日のピックアップ」候補者リストAPIの偵察（無料スカウト枠）。

mypage の本日のピックアップは、通常の検索とは別のリストで、プラチナ残数を消費せずに
スカウトできる。その候補者リストを返すAPIを特定するため、mypage を開いて /api/ の
リクエスト・レスポンスを保存する。
"""

from __future__ import annotations

import json
import re

from ..config import project_root
from ..logging_config import logger


class PickupProbe:
    def __init__(self, client):
        self.client = client
        self.base = client.sel.base_url.rstrip("/")
        self.out = project_root() / "data" / "exports"
        self.out.mkdir(parents=True, exist_ok=True)

    def run(self) -> None:
        page = self.client.page
        responses: list = []
        requests: list = []

        def _watched(url: str) -> bool:
            # 旧スカウト系(/ajax/)と新API(/api/)の両方を対象にする。
            return ("cr-support.jp/api/" in url or "cr-support.jp/ajax/" in url)

        def on_response(resp):
            try:
                if _watched(resp.url):
                    responses.append(resp)
            except Exception:  # noqa: BLE001
                pass

        def on_request(req):
            try:
                if _watched(req.url):
                    requests.append(req)
            except Exception:  # noqa: BLE001
                pass

        # 送信ブロック: freescout操作中の実送信を防ぐ（POSTを記録して中断）。
        self._blocked: list = []
        self._arm = False

        # 送信っぽいURLだけ中断する（作成画面のロードPOSTは通す）。
        send_pat = ("offer", "scout", "send", "pickup", "message", "platinum")

        def handle_route(route):
            r = route.request
            try:
                if (self._arm and r.method == "POST" and _watched(r.url)
                        and any(k in r.url.lower() for k in send_pat)):
                    try:
                        pd = r.post_data or ""
                    except Exception:  # noqa: BLE001
                        pd = ""
                    self._blocked.append((r.url, pd))
                    route.abort()
                    return
            except Exception:  # noqa: BLE001
                pass
            route.continue_()

        page.route("**/*", handle_route)
        page.on("response", on_response)
        page.on("request", on_request)

        page.goto(f"{self.base}/mypage/", wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        self.client.human_delay(2.0, 4.0)

        # タブを行き来して各セクションのfetchを強制発火させる（既定タブは再fetchされない）。
        for label in ("再スカウト候補", "本日のピックアップ", "ピックアップ"):
            try:
                loc = page.get_by_text(label, exact=False)
                if loc.count() > 0:
                    loc.first.click(timeout=3000)
                    self.client.human_delay(1.5, 3.0)
            except Exception:  # noqa: BLE001
                continue
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        self.client.human_delay(1.0, 2.0)

        # ピックアップ候補者をDOMから抽出し、candidateId→mrccid の橋渡しを検証する。
        self._resolve_pickup_candidates(page)

        # freescout（無料スカウト）リンクを押して作成/送信フローを捕捉（送信は中断）。
        self._probe_freescout(page)

        self._dump(page, requests, responses)

    def _probe_freescout(self, page) -> None:
        """最初の freescout リンクを押し、無料スカウトの作成/送信フローを捕捉する。

        送信ブロックを武装するため、送信っぽいPOSTは中断され実送信されない。
        作成画面(ライトボックス/別ページ)のDOM・スクショと、捕捉した送信POSTを保存する。
        """
        try:
            loc = page.locator("a.freescout")
            n = loc.count()
            if n == 0:
                (self.out / "pickup_freescout.txt").write_text(
                    "a.freescout リンクが見つかりません。", encoding="utf-8")
                return
            self._arm = True  # 以降の送信POSTは中断
            try:
                loc.first.click(timeout=6000)
            except Exception as e:  # noqa: BLE001
                logger.info("[pickup] freescoutクリックで例外: %s", e)
            self.client.human_delay(2.0, 3.5)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:  # noqa: BLE001
                pass
            self.client.human_delay(1.0, 2.0)
            try:
                (self.out / "pickup_freescout_dom.html").write_text(
                    page.content(), encoding="utf-8")
                page.screenshot(path=str(self.out / "pickup_freescout_dom.png"),
                                full_page=True)
            except Exception:  # noqa: BLE001
                pass
            lines = [f"URL(現在): {page.url}", f"freescoutリンク数: {n}", ""]
            lines.append("== 中断した送信候補POST ==")
            for url, pd in self._blocked:
                lines.append(f"POST {url}\n{pd[:3000]}\n{'-'*50}")
            (self.out / "pickup_freescout.txt").write_text("\n".join(lines), encoding="utf-8")
            logger.info("[pickup] freescout捕捉: 中断POST %d 件。", len(self._blocked))
        except Exception as e:  # noqa: BLE001
            logger.warning("[pickup] freescout探索に失敗: %s", e)
        finally:
            self._arm = False

    def _resolve_pickup_candidates(self, page, rrsc: str = "3444981") -> None:
        """mypageのピックアップ候補者(data-resume-id=数値candidateId)を抽出し、
        /v2/candidates:search の candidateId フィルタで mrccid に解決できるか検証する。"""
        summary: list[str] = []
        try:
            html = page.content()
        except Exception as e:  # noqa: BLE001
            (self.out / "pickup_resolve.txt").write_text(f"DOM取得失敗: {e}", encoding="utf-8")
            return

        # data-resume-id="12345" と data-itemid="pick-up-candidate:12345" を拾う。
        ids = re.findall(r'data-resume-id="(\d+)"', html)
        ids += re.findall(r'data-itemid="pick-up-candidate:(\d+)"', html)
        ids = list(dict.fromkeys(ids))  # 重複除去・順序維持
        (self.out / "pickup_candidate_ids.txt").write_text("\n".join(ids), encoding="utf-8")
        summary.append(f"抽出したピックアップ候補者ID: {len(ids)}件 -> {ids[:20]}")
        logger.info("[pickup] 候補者ID %d 件を抽出。", len(ids))

        if not ids:
            summary.append("候補者IDが取れませんでした。pickup_dom.html を確認してください。")
            (self.out / "pickup_resolve.txt").write_text("\n".join(summary), encoding="utf-8")
            return

        # 保存検索の条件をベースに candidateId で1名に絞って mrccid を得る。
        base_cond = None
        try:
            resp = page.request.get(
                f"{self.base}/api/v2/candidates/searchConditions/{rrsc}")
            if resp.status == 200:
                base_cond = (resp.json() or {}).get("condition")
        except Exception as e:  # noqa: BLE001
            summary.append(f"検索条件の取得失敗: {e}")

        def _try_search(cid: str, cond: dict, label: str) -> None:
            body = {"searchId": None, "proposalSearchId": None,
                    "paging": {"page": 1, "maxPageSize": 10}, "condition": cond}
            try:
                r = page.request.post(f"{self.base}/api/v2/candidates:search",
                                      headers={"Content-Type": "application/json"},
                                      data=json.dumps(body))
                txt = r.text()
                summary.append(f"[{label}] cid={cid} status={r.status} len={len(txt)}")
                if r.status == 200:
                    (self.out / f"pickup_resolve_{label}_{cid}.json").write_text(
                        txt[:1_000_000], encoding="utf-8")
                    try:
                        items = (r.json() or {}).get("items") or []
                        summary.append(f"    items={len(items)} "
                                       f"mrccids={[it.get('mrccid') for it in items[:5]]}")
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as e:  # noqa: BLE001
                summary.append(f"[{label}] cid={cid} ERR {e}")

        cid = ids[0]
        # (A) 保存検索条件 + candidateId 上書き。
        if base_cond:
            cond_a = dict(base_cond)
            cond_a["candidateId"] = cid
            _try_search(cid, cond_a, "withCond")
        # (B) 最小条件（candidateId のみ）。
        _try_search(cid, {"candidateId": cid}, "minimal")

        (self.out / "pickup_resolve.txt").write_text("\n".join(summary), encoding="utf-8")
        logger.info("[pickup] candidateId→mrccid 解決を検証しました。")

    def _dump(self, page, requests, responses) -> None:
        # リクエスト一覧（method + url + body）。
        try:
            lines = []
            seen = set()
            for r in requests:
                key = (r.method, r.url.split("?")[0])
                if key in seen:
                    continue
                seen.add(key)
                body = ""
                try:
                    body = r.post_data or ""
                except Exception:  # noqa: BLE001
                    body = ""
                lines.append(f"{r.method}\t{r.url}\n{body[:2000]}\n{'-'*60}")
            (self.out / "pickup_requests.txt").write_text("\n".join(lines), encoding="utf-8")
            logger.info("[pickup] /api/ リクエスト %d 件を保存。", len(seen))
        except Exception as e:  # noqa: BLE001
            logger.warning("[pickup] リクエスト保存に失敗: %s", e)

        # レスポンス（大きい順に保存）。ピックアップ候補者リストはこの中にあるはず。
        try:
            items = []
            for r in responses:
                try:
                    txt = r.text()
                except Exception:  # noqa: BLE001
                    continue
                if txt:
                    items.append((len(txt), r.url, txt))
            items.sort(key=lambda x: -x[0])
            index = []
            for i, (size, url, txt) in enumerate(items[:12]):
                (self.out / f"pickup_api_{i:02d}.json").write_text(
                    txt[:2_000_000], encoding="utf-8")
                index.append(f"pickup_api_{i:02d}.json\t{size}B\t{url}")
            (self.out / "pickup_api_index.txt").write_text("\n".join(index), encoding="utf-8")
            logger.info("[pickup] APIレスポンス %d 件を保存。", len(index))
        except Exception as e:  # noqa: BLE001
            logger.warning("[pickup] レスポンス保存に失敗: %s", e)

        # mypage DOM（タブ・候補者行の構造確認用）。
        try:
            (self.out / "pickup_dom.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(self.out / "pickup_dom.png"), full_page=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("[pickup] DOM保存に失敗: %s", e)
        logger.info("[pickup] 偵察を終了しました。data/exports の pickup_* を確認してください。")
