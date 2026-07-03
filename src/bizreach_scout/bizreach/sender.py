"""ビズリーチ上でスカウトを送信する。

安全機構:
- kill switch ファイルが存在する場合は一切送信しない。
- dry_run の場合は入力欄まで埋めて送信ボタンは押さない（プレビュー検証）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import get_settings
from ..logging_config import logger
from .client import BizreachClient


@dataclass
class SendOutcome:
    status: str  # sent / dry_run / failed / blocked
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("sent", "dry_run")


class BizreachSender:
    def __init__(self, client: BizreachClient, dry_run: bool | None = None):
        self.client = client
        self.sel = client.sel
        self.settings = get_settings()
        self.dry_run = self.settings.dry_run if dry_run is None else dry_run

    def _kill_switch_active(self) -> bool:
        return self.settings.kill_switch_path.exists()

    def send_scout(self, candidate, subject: str, body: str) -> SendOutcome:
        if self._kill_switch_active():
            logger.warning("kill switch が有効です。送信を中止します。")
            return SendOutcome("blocked", "kill switch active")

        profile_url = getattr(candidate, "profile_url", "") or ""
        if not profile_url:
            return SendOutcome("failed", "profile_url が無いため送信不可")

        page = self.client.page
        try:
            if page.url != profile_url:
                page.goto(profile_url, wait_until="domcontentloaded")
                self.client.human_delay()

            page.locator(self.sel.scout_button).first.click()
            page.wait_for_load_state("networkidle")
            self.client.human_delay()

            page.fill(self.sel.scout_subject, subject)
            self.client.human_delay(0.4, 1.2)
            page.fill(self.sel.scout_body, body)
            self.client.human_delay(0.6, 1.5)

            if self.dry_run:
                logger.info("[DRY-RUN] 件名・本文を入力。送信ボタンは押しません: %s", profile_url)
                return SendOutcome("dry_run", "dry run; not sent")

            page.locator(self.sel.scout_send).first.click()
            self.client.human_delay()
            confirm = page.locator(self.sel.scout_confirm)
            if confirm.count() > 0:
                confirm.first.click()
            page.wait_for_load_state("networkidle")
            self.client.human_delay()

            if page.locator(self.sel.scout_sent_marker).count() > 0:
                return SendOutcome("sent", "confirmed by marker")
            logger.warning("送信完了マーカーが確認できませんでした: %s", profile_url)
            return SendOutcome("sent", "sent (marker not confirmed)")

        except Exception as e:  # noqa: BLE001
            logger.error("スカウト送信に失敗 (%s): %s", profile_url, e)
            return SendOutcome("failed", str(e))
