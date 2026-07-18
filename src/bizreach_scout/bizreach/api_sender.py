"""内部APIベースのスカウト送信アダプタ。

パイプライン/スケジューラの sender インターフェース `send_scout(candidate, subject, body)`
を満たしつつ、BizreachApi.route_scout（プラチナスカウト主・両会員種別対応）で送信する。

安全機構:
- kill switch ファイルが存在する場合は一切送信しない。
- dry_run の場合はサーバ側の dryRun 検証のみ（実送信しない）。
- プラチナ残数の枯渇時は route_scout 側で送信をスキップする。
"""

from __future__ import annotations

from ..config import get_settings, scout_job_id
from ..logging_config import logger
from ..models import Candidate
from .outcome import SendOutcome


class ApiScoutSender:
    def __init__(self, api, job_id: str | None = None, dry_run: bool | None = None,
                 pickup: bool = False):
        self.api = api
        self.settings = get_settings()
        self.job_id = job_id or scout_job_id()
        self.dry_run = self.settings.dry_run if dry_run is None else dry_run
        # pickup=True の場合は無料枠(/v2/scouts/pickup)で送信（プラチナ残数を消費しない）。
        self.pickup = pickup

    def _kill_switch_active(self) -> bool:
        return self.settings.kill_switch_path.exists()

    def send_scout(self, candidate: Candidate, subject: str, body: str,
                   reminder: dict | None = None,
                   idempotency_key: str | None = None) -> SendOutcome:
        if self._kill_switch_active():
            logger.warning("kill switch が有効です。送信を中止します。")
            return SendOutcome("blocked", "kill switch active")
        if not getattr(candidate, "mrccid", ""):
            return SendOutcome("failed", "mrccid が無いため送信不可")
        if not self.job_id:
            return SendOutcome("failed", "scout_job_id が未設定（company.yaml を確認）")

        if self.pickup:
            # 無料枠のピックアップ送信（プラチナ残数を消費しない）。
            result = self.api.send_pickup_scout(
                self.job_id, candidate.mrccid, subject, body,
                dry_run=self.dry_run, reminder=reminder,
                idempotency_key=idempotency_key,
            )
        else:
            result = self.api.route_scout(
                self.job_id, candidate.mrccid, subject, body,
                dry_run=self.dry_run, reminder=reminder,
                idempotency_key=idempotency_key,
            )
        endpoint = str(result.get("endpoint") or ("pickup" if self.pickup else ""))
        status = result.get("status")
        detail = f"{endpoint} status={status}"

        if result.get("skipped"):
            # 既送信・残数枯渇・対象外など。送信はしていない。
            return SendOutcome("blocked", f"skipped:{result.get('skipped')}", endpoint)
        if status in (200, 201):
            return SendOutcome("dry_run" if self.dry_run else "sent", detail, endpoint)
        return SendOutcome("failed", f"{detail} body={result}", endpoint)
