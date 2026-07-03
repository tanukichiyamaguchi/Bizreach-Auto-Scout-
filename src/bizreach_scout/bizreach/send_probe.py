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

    # --- 本体 -----------------------------------------------------------------
    def run(self, mrccid: str, search_url: str | None = None) -> None:
        page = self.client.page
        self._install_capture(page)

        # 1) レジュメ画面へ。既知の候補ルートを順に試す。
        resume_routes = [
            f"{self.base}/scout/highclass/resume/{mrccid}",
            f"{self.base}/scout/highclass/candidate/{mrccid}",
            f"{self.base}/scout/highclass/{mrccid}",
        ]
        reached = False
        for url in resume_routes:
            try:
                page.goto(url, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:  # noqa: BLE001
                    pass
                self.client.human_delay(1.5, 3.0)
                # スカウト系ボタンが存在すればレジュメ到達とみなす。
                has_scout = any(
                    page.get_by_text(t, exact=False).count() > 0 for t in _SCOUT_TEXTS
                )
                self._log(f"レジュメ試行 {url} -> scoutボタン={has_scout}")
                if has_scout:
                    reached = True
                    break
            except Exception as e:  # noqa: BLE001
                self._log(f"レジュメ遷移失敗 {url}: {e}")

        self._dump(page, "probe_resume_dom")

        # 到達できなければ検索→クリックのフローにフォールバック。
        if not reached and search_url:
            self._log("直接ルートで未到達。検索画面から候補者を開く。")
            try:
                page.goto(search_url, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:  # noqa: BLE001
                    pass
                self.client.human_delay(2.0, 3.5)
                # レジュメを開くリンク/カードを順に試す。
                for s in [self.sel.result_link, self.sel.result_card,
                          "a[href*='resume']", "a[href*='candidate']"]:
                    loc = page.locator(s)
                    if loc.count() > 0:
                        loc.first.click()
                        self.client.human_delay(1.5, 3.0)
                        self._log(f"検索結果をクリック: '{s}'")
                        break
                self._dump(page, "probe_resume_dom")
            except Exception as e:  # noqa: BLE001
                self._log(f"検索フォールバック失敗: {e}")

        # 2) スカウト作成画面を開く。
        clicked = self._click_by_texts(page, _SCOUT_TEXTS)
        if not clicked:
            self._log("スカウトボタンが見つかりません。レジュメDOMを確認してください。")
            self._finish()
            return
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:  # noqa: BLE001
            pass
        self.client.human_delay(1.5, 3.0)
        self._dump(page, "probe_compose_dom")

        # 3) 件名・本文にセンチネルを入力（送信されても内容で検知できるように）。
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

        # 4) 送信ブロックを武装してから送信/確認ボタンを押す（実送信は abort で阻止）。
        self._arm_block = True
        self._log("送信ブロックを武装。以降の /api/ POST は記録して中断します。")
        step1 = self._click_by_texts(page, _SEND_TEXTS)
        self.client.human_delay(1.0, 2.0)
        # 確認ダイアログが出れば実行ボタンも押す（POSTは武装済みで中断される）。
        step2 = self._click_by_texts(page, _CONFIRM_TEXTS)
        self.client.human_delay(1.0, 2.0)
        self._log(f"送信ステップ: 確認へ='{step1}' 実行='{step2}'")
        self._dump(page, "probe_after_send_click")

        self._finish()

    def _finish(self) -> None:
        # 送信ブロックで捕捉した POST を保存（送信APIの本命）。
        if self.blocked:
            lines = []
            for url, headers, pd in self.blocked:
                lines.append(f"URL: {url}")
                lines.append("HEADERS:")
                for k, v in headers.items():
                    lines.append(f"  {k}: {v}")
                lines.append("PAYLOAD:")
                lines.append(pd[:20000])
                lines.append("-" * 60)
            (self.out / "probe_send_capture.txt").write_text("\n".join(lines), encoding="utf-8")
            self._log(f"送信候補POSTを {len(self.blocked)} 件捕捉 -> probe_send_capture.txt")
        else:
            self._log("送信POSTを捕捉できませんでした（送信ボタン未到達の可能性）。")

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
