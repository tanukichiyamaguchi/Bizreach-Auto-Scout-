"""スカウト送信フローの安全な偵察（送信APIとセレクタの特定用）。

ビズリーチのスカウト送信は React 画面 + 内部 JSON API で行われる。検索・レジュメ
APIは判明済みだが、送信APIは「実際にスカウト作成画面を開いて送信ボタンを押す」まで
発火しないため、通常の検索閲覧では捕捉できない。

本モジュールは送信画面まで到達し、DOM・スクリーンショット・ネットワークを保存する。
送信ボタンを押す直前に「送信ブロックモード」を有効化し、以降 /api/ への POST を
すべて記録した上で中断(abort)する。したがって実際の送信は絶対に行われない。

出力（data/exports/ 配下）:
- probe_resume_dom.html / .png    レジュメ画面
- probe_compose_dom.html / .png   スカウト作成画面
- probe_send_capture.txt          送信ボタン押下後にブロックした POST（URL/ヘッダ/本文）
- probe_posts.txt                 フロー全体で観測した /api/ POST 一覧
- probe_summary.txt               各ステップの結果サマリ
"""

from __future__ import annotations

import re

from ..config import project_root
from ..logging_config import logger

SENTINEL = "ZZ_PROBE_DO_NOT_SEND_ZZ"

# スカウトボタンとして試すテキスト候補（表記ゆれに対応）。
_SCOUT_TEXTS = ["プラチナスカウト", "スカウト", "スカウトを送る", "スカウトする", "オファー"]
# 送信ボタンとして試すテキスト候補。
_SEND_TEXTS = ["確認画面へ", "送信内容を確認", "内容を確認", "送信する", "送信", "この内容で送信"]
# 確認ダイアログの実行ボタン候補。
_CONFIRM_TEXTS = ["送信する", "送信", "OK", "はい"]


class SendProbe:
    """認証済みクライアントでスカウト送信画面を偵察する（実送信なし）。"""

    def __init__(self, client):
        self.client = client
        self.sel = client.sel
        self.base = self.sel.base_url.rstrip("/")
        self.out = project_root() / "data" / "exports"
        self.out.mkdir(parents=True, exist_ok=True)
        self.summary: list[str] = []
        self.posts: list[tuple[str, dict, str]] = []
        self.blocked: list[tuple[str, dict, str]] = []
        self.all_reqs: list[tuple[str, str]] = []  # (method, url) 全 /api/ リクエスト
        self.chunk_urls: set[str] = set()  # フロー中に読み込まれた _next JSチャンク
        self._arm_block = False

    # --- 補助 -----------------------------------------------------------------
    def _log(self, msg: str) -> None:
        logger.info("[probe] %s", msg)
        self.summary.append(msg)

    def _dump(self, page, name: str) -> None:
        try:
            (self.out / f"{name}.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(self.out / f"{name}.png"), full_page=True)
            self._log(f"保存: {name}.html / {name}.png (URL={page.url})")
        except Exception as e:  # noqa: BLE001
            self._log(f"保存失敗 {name}: {e}")

    def _install_capture(self, page) -> None:
        """/api/ への POST を記録。ブロック武装中は abort して実送信を防ぐ。"""

        def on_request(req):
            try:
                if "/api/" in req.url:
                    self.all_reqs.append((req.method, req.url))
                if "/_next/static/chunks/" in req.url and req.url.endswith(".js"):
                    self.chunk_urls.add(req.url)
            except Exception:  # noqa: BLE001
                pass

        page.on("request", on_request)

        def handler(route):
            req = route.request
            try:
                if req.method == "POST":
                    try:
                        pd = req.post_data or ""
                    except Exception:  # noqa: BLE001
                        pd = ""
                    self.posts.append((req.url, dict(req.headers), pd))
                    if self._arm_block:
                        self.blocked.append((req.url, dict(req.headers), pd))
                        self._log(f"送信POSTをブロック: {req.method} {req.url}")
                        route.abort()
                        return
            except Exception as e:  # noqa: BLE001
                self._log(f"route handler例外: {e}")
            route.continue_()

        page.route("**/api/**", handler)

    def _click_by_texts(self, page, texts: list[str], timeout: int = 4000) -> str | None:
        """テキスト候補を順に試し、クリックできたテキストを返す。"""
        for t in texts:
            try:
                loc = page.get_by_role("button", name=t)
                if loc.count() == 0:
                    loc = page.get_by_text(t, exact=False)
                if loc.count() > 0:
                    loc.first.click(timeout=timeout)
                    self._log(f"クリック成功: '{t}'")
                    return t
            except Exception:  # noqa: BLE001
                continue
        return None

    def _fill_first(self, page, selectors: list[str], value: str, label: str) -> bool:
        for s in selectors:
            try:
                loc = page.locator(s)
                if loc.count() > 0:
                    loc.first.fill(value, timeout=3000)
                    self._log(f"入力成功 {label}: '{s}'")
                    return True
            except Exception:  # noqa: BLE001
                continue
        self._log(f"入力欄が見つからず {label}: {selectors}")
        return False

    def _extract_js_endpoints(self, page) -> None:
        """フロントJS(_next chunks)から /api/v2/ エンドポイント文字列を抽出する。

        送信APIは通常運用の走査では発火しないが、フロントのJSにはURL文字列が
        リテラルとして埋め込まれている。認証済みコンテキストでJSを取得し grep する。
        DOM由来 + フロー中にネットワークで読み込まれた（遅延ロード含む）チャンクを併用。
        """
        try:
            try:
                content = page.content()
            except Exception:  # noqa: BLE001
                content = ""
            dom_chunks = {self.base + p for p in
                          re.findall(r'/_next/static/chunks/[^"\']+?\.js', content)}
            chunks = sorted(dom_chunks | self.chunk_urls)
            self._log(f"JSチャンク {len(chunks)} 件を解析します。")
            endpoints: set[str] = set()
            scout_ctx: list[str] = []
            for url in chunks:
                js = self._req_get(url)
                if not js:
                    continue
                for m in re.findall(r'[`"\']/api/v2/[^`"\'\s]+', js):
                    endpoints.add(m.strip('`"\''))
                # scout/message/offer を含む前後を文脈保存（payload推定用）。
                for m in re.finditer(r'.{0,80}(scout|Scout|platinum|Platinum|offer|Offer|'
                                     r'message|Message|sendMessage|:send).{0,80}', js):
                    seg = m.group(0)
                    if "/api/" in seg or "candidates" in seg:
                        scout_ctx.append(seg)
            eps = sorted(endpoints)
            (self.out / "probe_js_endpoints.txt").write_text("\n".join(eps), encoding="utf-8")
            # 送信っぽいものを強調抽出。
            send_like = [e for e in eps if re.search(
                r'scout|platinum|offer|message|:send|/send', e, re.I)]
            uniq_ctx = sorted(set(scout_ctx))[:200]
            (self.out / "probe_js_send_hints.txt").write_text(
                "== 送信候補エンドポイント ==\n" + "\n".join(send_like) +
                "\n\n== 文脈(payload推定用) ==\n" + "\n".join(uniq_ctx),
                encoding="utf-8",
            )
            self._log(f"JSから /api/v2/ を {len(eps)} 種、送信候補 {len(send_like)} 種抽出。")
        except Exception as e:  # noqa: BLE001
            self._log(f"JS解析に失敗: {e}")

    def _req_get(self, url: str) -> str | None:
        try:
            resp = self.client.page.request.get(url)
            if resp.status == 200:
                return resp.text()
        except Exception:  # noqa: BLE001
            return None
        return None

    def _open_resume(self, page, mrccid: str) -> bool:
        """検索結果一覧から候補者のレジュメを開く（aria-label='{mrccid}を開く'）。"""
        try:
            loc = page.locator(f"[aria-label='{mrccid}を開く']")
            if loc.count() == 0:
                # mrccid未指定時は先頭候補者の「〜を開く」を使う。
                loc = page.locator("[aria-label$='を開く']")
            if loc.count() == 0:
                self._log("レジュメを開く要素が見つかりません。")
                return False
            loc.first.click(timeout=6000)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:  # noqa: BLE001
                pass
            self.client.human_delay(1.5, 3.0)
            self._log("レジュメを開きました。")
            return True
        except Exception as e:  # noqa: BLE001
            self._log(f"レジュメ展開に失敗: {e}")
            return False

    def _click_scout_button(self, page) -> bool:
        """レジュメ内のスカウトボタンを押す（サイドナビのラベルは除外）。

        サイドナビの『スカウト』はボタンではなくDIVなので role=button で弾く。
        ダイアログ/ドロワーが開いていればそこにスコープする。
        """
        scopes = [page.get_by_role("dialog"), page.locator("[role='dialog']"),
                  page.locator("main"), page]
        names = ["プラチナスカウト", "スカウトを送る", "スカウトする", "スカウト", "オファー"]
        for scope in scopes:
            try:
                if hasattr(scope, "count") and scope.count() == 0:
                    continue
            except Exception:  # noqa: BLE001
                pass
            for name in names:
                try:
                    btn = scope.get_by_role("button", name=name)
                    if btn.count() > 0:
                        btn.first.click(timeout=5000)
                        self._log(f"スカウトボタン押下: '{name}'")
                        return True
                except Exception:  # noqa: BLE001
                    continue
        self._log("スカウトボタン(role=button)が見つかりません。")
        return False

    # --- 本体 -----------------------------------------------------------------
    def run(self, mrccid: str, search_url: str | None = None) -> None:
        page = self.client.page
        self._install_capture(page)
        try:
            self._run_flow(page, mrccid, search_url)
        except Exception as e:  # noqa: BLE001
            self._log(f"偵察フローで例外: {e}")
        finally:
            # JS解析(送信API抽出)は必ず最後に実行（遅延チャンクも読み込み済み）。
            self._extract_js_endpoints(page)
            self._finish()

    def _run_flow(self, page, mrccid: str, search_url: str | None) -> None:
        # 0) 検索画面を開く（SPA本体とAPIコンテキストをロード）。
        url = search_url or f"{self.base}/scout/highclass/search/"
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        self.client.human_delay(2.0, 3.5)

        # 1) 候補者レジュメを開く → スカウトボタンを押す。
        if not self._open_resume(page, mrccid):
            self._dump(page, "probe_resume_dom")
            return
        self._dump(page, "probe_resume_dom")

        if not self._click_scout_button(page):
            self._log("スカウト作成に進めません。レジュメDOMを確認してください。")
            return
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:  # noqa: BLE001
            pass
        self.client.human_delay(1.5, 3.0)
        self._dump(page, "probe_compose_dom")

        # 2) 件名・本文にセンチネルを入力（送信されても内容で検知できるように）。
        self._fill_first(
            page,
            [self.sel.scout_subject, "input[name='subject']", "input[name='title']",
             "input[placeholder*='件名']", "input[type='text']"],
            f"{SENTINEL} 件名",
            "件名",
        )
        self._fill_first(
            page,
            [self.sel.scout_body, "textarea[name='body']", "textarea[name='message']",
             "textarea[placeholder*='本文']", "textarea", "[contenteditable='true']"],
            f"{SENTINEL} 本文サンプル",
            "本文",
        )
        self.client.human_delay(0.6, 1.5)
        self._dump(page, "probe_compose_filled")

        # 3) 送信ブロックを武装してから送信/確認ボタンを押す（実送信は abort で阻止）。
        self._arm_block = True
        self._log("送信ブロックを武装。以降の /api/ POST は記録して中断します。")
        step1 = self._click_by_texts(page, _SEND_TEXTS)
        self.client.human_delay(1.0, 2.0)
        # 確認ダイアログが出れば実行ボタンも押す（POSTは武装済みで中断される）。
        step2 = self._click_by_texts(page, _CONFIRM_TEXTS)
        self.client.human_delay(1.0, 2.0)
        self._log(f"送信ステップ: 確認へ='{step1}' 実行='{step2}'")
        self._dump(page, "probe_after_send_click")

    def _finish(self) -> None:
        # 分析系(Datadog/GA/Sentry)を除外し、cr-support の送信POSTのみを本命として保存。
        _NOISE = ("datadoghq.com", "google-analytics.com", "sentry.io",
                  "karte.io", "googletagmanager.com")
        real = [b for b in self.blocked if not any(n in b[0] for n in _NOISE)]
        # 送信ブロックで捕捉した POST を保存（送信APIの本命）。
        if real:
            lines = []
            for url, headers, pd in real:
                lines.append(f"URL: {url}")
                lines.append("HEADERS:")
                for k, v in headers.items():
                    lines.append(f"  {k}: {v}")
                lines.append("PAYLOAD:")
                lines.append(pd[:20000])
                lines.append("-" * 60)
            (self.out / "probe_send_capture.txt").write_text("\n".join(lines), encoding="utf-8")
            self._log(f"送信候補POST(cr-support)を {len(real)} 件捕捉 -> probe_send_capture.txt")
        else:
            self._log("cr-support の送信POSTは捕捉できませんでした"
                      "（送信ボタン未到達 or 別経路の可能性。JS抽出結果を参照）。")

        # 全 POST の本文（参考）。
        if self.posts:
            lines = []
            for url, _headers, pd in self.posts:
                lines.append(f"POST {url}\n{pd[:4000]}\n{'-'*60}")
            (self.out / "probe_posts.txt").write_text("\n".join(lines), encoding="utf-8")

        # フロー全体で観測した /api/ リクエスト一覧（GET含む。送信画面のロード経路把握用）。
        if self.all_reqs:
            seen = set()
            lines = []
            for method, url in self.all_reqs:
                key = (method, url.split("?")[0])
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"{method}\t{url}")
            (self.out / "probe_api_requests.txt").write_text("\n".join(lines), encoding="utf-8")

        (self.out / "probe_summary.txt").write_text("\n".join(self.summary), encoding="utf-8")
        self._log("偵察を終了しました。")
