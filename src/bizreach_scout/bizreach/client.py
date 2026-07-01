"""ビズリーチへのログインとブラウザセッション管理（Playwright）。

セッションは storage_state に保存し、次回以降のログインを省略する。
人間的な待機を入れ、レート制限・bot検知のリスクを下げる。
"""

from __future__ import annotations

import random
import time
from pathlib import Path

from ..config import BizreachCredentials, get_settings, project_root
from ..logging_config import logger
from .selectors import Selectors, load_selectors


class BizreachClient:
    def __init__(
        self,
        credentials: BizreachCredentials | None = None,
        selectors: Selectors | None = None,
        headless: bool = True,
    ):
        self.creds = credentials or BizreachCredentials.from_env()
        self.sel = selectors or load_selectors()
        self.headless = headless
        self.settings = get_settings()
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None

    # --- ライフサイクル -------------------------------------------------------
    def start(self) -> BizreachClient:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        # 自動化フラグを隠し、実ブラウザに寄せる。
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )

        state_path = self._storage_state_path()
        ctx_kwargs: dict = {
            "locale": "ja-JP",
            "timezone_id": "Asia/Tokyo",
            "user_agent": self.settings.user_agent,
            "viewport": {"width": 1366, "height": 900},
            "extra_http_headers": {"Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8"},
        }
        if state_path.exists():
            ctx_kwargs["storage_state"] = str(state_path)
        self._context = self._browser.new_context(**ctx_kwargs)
        self.page = self._context.new_page()
        return self

    def close(self) -> None:
        try:
            if self._context:
                self._save_state()
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception as e:  # noqa: BLE001
            logger.warning("ブラウザ終了時に例外: %s", e)

    def __enter__(self) -> BizreachClient:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    # --- 待機 -----------------------------------------------------------------
    def human_delay(self, lo: float = 0.8, hi: float = 2.5) -> None:
        time.sleep(random.uniform(lo, hi))

    # --- セッション状態 -------------------------------------------------------
    def _storage_state_path(self) -> Path:
        p = Path(self.creds.storage_state)
        return p if p.is_absolute() else project_root() / p

    def _save_state(self) -> None:
        path = self._storage_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._context.storage_state(path=str(path))
        except Exception as e:  # noqa: BLE001
            logger.warning("セッション保存に失敗: %s", e)

    # --- ログイン -------------------------------------------------------------
    def ensure_logged_in(self) -> None:
        """保存済みセッションで未ログインならログインし、担当グループを選択する。"""
        self.page.goto(self.sel.base_url, wait_until="domcontentloaded")
        self.human_delay()
        if self.page.locator(self.sel.logged_in_marker).count() == 0:
            self.login()
        self.select_group()

    def select_group(self) -> None:
        """ログイン後、担当グループを選択する（未選択だと検索/スカウトに進めない）。

        ビズリーチは /login/selectGroup/ でグループを選ばせる。グループが1つでも
        明示的に選択する必要があるため、選択リンクがあればクリックする。
        """
        sel = getattr(self.sel, "group_select_link", "")
        if not sel:
            return
        try:
            group_url = self.sel.base_url.rstrip("/") + "/login/selectGroup/"
            self.page.goto(group_url, wait_until="domcontentloaded")
            self.human_delay()
            link = self.page.locator(sel)
            n = link.count()
            if n > 0:
                logger.info("担当グループを選択します（候補 %d 件の先頭）。", n)
                link.first.click()
                self.page.wait_for_load_state("networkidle")
                self.human_delay()
            else:
                logger.info("グループ選択リンクが見つかりません（選択済み/不要の可能性）。")
        except Exception as e:  # noqa: BLE001
            logger.warning("グループ選択でエラー: %s", e)

    def login(self) -> None:
        if not self.creds.email or not self.creds.password:
            raise RuntimeError(
                "BIZREACH_EMAIL / BIZREACH_PASSWORD が未設定です。ログインできません。"
            )
        logger.info("ビズリーチへログインします。")
        self.page.goto(self.sel.login_url, wait_until="domcontentloaded")
        self.human_delay()
        self.page.fill(self.sel.login_email, self.creds.email)
        self.human_delay(0.3, 0.9)
        self.page.fill(self.sel.login_password, self.creds.password)
        self.human_delay(0.3, 0.9)
        # ログインボタンをクリック。見つからなければ Enter 送信でフォールバック。
        try:
            self.page.locator(self.sel.login_submit).first.click(timeout=5000)
        except Exception as e:  # noqa: BLE001
            logger.info("ログインボタンをクリックできず(%s)。Enterで送信します。", e)
            self.page.press(self.sel.login_password, "Enter")
        self.page.wait_for_load_state("networkidle")
        self.human_delay()
        if self.page.locator(self.sel.logged_in_marker).count() == 0:
            logger.warning(
                "ログイン成功の確認要素が見つかりません。"
                "2段階認証やセレクタ変更の可能性があります（selectors を確認してください）。"
            )
        self._save_state()
