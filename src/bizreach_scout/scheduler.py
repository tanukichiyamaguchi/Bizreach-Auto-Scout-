"""再送スケジュールの実行（初回送信からN日後に再送）。

`bizscout run-resends` から呼ばれる。cron 等で定期実行する想定。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime

from .config import get_settings
from .logging_config import logger
from .storage.repository import Repository


@dataclass
class ResendReport:
    due: int = 0
    sent: int = 0
    dry_run: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            "==== 再送レポート ====\n"
            f"対象(期限到来): {self.due}\n"
            f"再送(実送信)  : {self.sent}\n"
            f"再送(dry-run) : {self.dry_run}\n"
            f"スキップ      : {self.skipped}\n"
            f"失敗          : {self.failed}"
        )


def run_due_resends(repo: Repository, sender, now: datetime | None = None) -> ResendReport:
    """期限の到来した再送を送信する。sender=None の場合はプレビューのみ。"""
    settings = get_settings()
    report = ResendReport()
    rows = repo.due_resends(now)
    report.due = len(rows)
    logger.info("再送対象: %d 件", report.due)

    for row in rows:
        # 暴走防止: 初回送信と同じく1実行あたりの送信上限を適用。
        if (report.sent + report.dry_run) >= settings.max_sends_per_run:
            logger.info("再送の送信上限(%d)に達したため残り%d件を次回に持ち越し。",
                        settings.max_sends_per_run, report.due - report.sent - report.dry_run)
            break

        mno = row["member_no"]
        candidate = repo.load_candidate(mno)

        if sender is None or candidate is None:
            report.skipped += 1
            reason = "no sender" if sender is None else "候補者データ無し"
            logger.info("再送スキップ(%s): %s", reason, mno)
            continue

        # 実送信直前に冪等キーを永続化（初回と同じ二重送信防止）。dry_run では遷移しない。
        idem_key = None
        if not getattr(sender, "dry_run", settings.dry_run):
            idem_key = repo.begin_send(mno, "resend")
        outcome = sender.send_scout(candidate, row["subject"], row["body"],
                                    idempotency_key=idem_key)
        if outcome.status == "sent":
            repo.mark_sent(mno, "resend", settings.resend_after_days)
            report.sent += 1
            logger.info("再送完了: %s", mno)
            time.sleep(random.uniform(settings.send_delay_min,
                                      max(settings.send_delay_max, settings.send_delay_min)))
        elif outcome.status == "dry_run":
            report.dry_run += 1
            logger.info("[DRY-RUN] 再送を模擬: %s", mno)
        elif outcome.status == "blocked":
            report.skipped += 1
            if outcome.detail.startswith("skipped:"):
                # 既送信・対象外など終了状態。毎日のリトライを避けるため skipped にする。
                repo.mark_skipped(mno, "resend", outcome.detail)
                logger.warning("再送スキップ（%s）: %s", outcome.detail, mno)
            else:
                # kill switch 等の一時的ブロックは状態を保持し次回リトライ。
                logger.warning("再送ブロック（%s・次回リトライ）: %s", outcome.detail, mno)
        else:
            repo.mark_failed(mno, "resend", outcome.detail)
            report.failed += 1
            report.errors.append((mno, outcome.detail))

    logger.info("\n%s", report.summary())
    return report
