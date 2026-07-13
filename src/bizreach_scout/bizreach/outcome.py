"""スカウト送信結果の共通型。

送信アダプタ（ApiScoutSender）が返す結果。パイプライン/スケジューラは
status を見て記録・リトライを判断する。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SendOutcome:
    status: str  # sent / dry_run / failed / blocked
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("sent", "dry_run")
