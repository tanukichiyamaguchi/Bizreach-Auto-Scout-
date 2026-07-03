"""「本日のピックアップ」候補者リストAPIの偵察（無料スカウト枠）。

mypage の本日のピックアップは、通常の検索とは別のリストで、プラチナ残数を消費せずに
スカウトできる。その候補者リストを返すAPIを特定するため、mypage を開いて /api/ の
リクエスト・レスポンスを保存する。
"""

from __future__ import annotations

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

        def on_response(resp):
            try:
                ct = (resp.headers or {}).get("content-type", "")
                if "json" in ct.lower() and "/api/" in resp.url:
                    responses.append(resp)
            except Exception:  # noqa: BLE001
                pass

        def on_request(req):
            try:
                if "/api/" in req.url:
                    requests.append(req)
            except Exception:  # noqa: BLE001
                pass

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

        # 直接プローブ: /api/v1/frontend/mypage/{名前}/search を推定パスで叩く。
        self._probe_endpoints(page)

        self._dump(page, requests, responses)

    def _probe_endpoints(self, page) -> None:
        """ピックアップ候補者リストの推定エンドポイントを直接叩いて特定する。"""
        names = [
            "pickup", "pickup-scout", "pickup-scout-candidate", "pickup-candidate",
            "pickup-scout-job", "daily-pickup", "daily-pickup-scout-candidate",
            "scout-candidate", "recommend-scout-candidate", "recommended-candidate",
            "hot-scout-candidate", "hot-scout", "matching-scout-candidate",
        ]
        summary = []
        idx = 0
        for name in names:
            url = f"{self.base}/api/v1/frontend/mypage/{name}/search"
            try:
                resp = page.request.post(url, headers={"Content-Type": "application/json"},
                                         data='{"fetchSize":50}')
                body = resp.text()
                summary.append(f"POST {resp.status} {len(body)}B {url}")
                if resp.status == 200 and body.strip().startswith(("{", "[")):
                    (self.out / f"pickup_probe_{idx:02d}.json").write_text(
                        body[:2_000_000], encoding="utf-8")
                    summary[-1] += f"  -> pickup_probe_{idx:02d}.json"
                    idx += 1
            except Exception as e:  # noqa: BLE001
                summary.append(f"POST ERR {url} {e}")
        (self.out / "pickup_probe_result.txt").write_text("\n".join(summary), encoding="utf-8")
        logger.info("[pickup] 推定エンドポイント探索: 成功 %d 件。", idx)

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
