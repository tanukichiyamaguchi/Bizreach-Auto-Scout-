"""エンドツーエンドのスカウト処理パイプライン。

流れ: 取り込み → 対象条件判定 → 重複チェック → 文面生成 → 保存 → (初回)送信 → 再送予約。
完全自動送信に対応しつつ、暴走防止（上限件数・送信間隔・kill switch・dry_run）を備える。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .config import get_settings, project_root, scout_rules
from .eligibility import check_eligibility
from .generation.generator import ScoutGenerator, render_for_human
from .ingest.base import CandidateSource
from .logging_config import logger
from .models import Candidate
from .storage.repository import Repository


def _export_scout(scout) -> None:
    """生成したスカウトを data/exports/{会員番号}.md に書き出す（レビュー用）。"""
    try:
        out = project_root() / "data" / "exports"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{scout.member_no}.md").write_text(render_for_human(scout), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("文面のエクスポートに失敗: %s", e)


@dataclass
class PipelineReport:
    processed: int = 0
    generated: int = 0
    reused: int = 0
    sent: int = 0
    dry_run: int = 0
    skipped_duplicate: int = 0
    skipped_ineligible: int = 0
    failed: int = 0
    ineligible: list[tuple[str, list[str]]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "==== スカウト処理レポート ====",
            f"処理候補者数      : {self.processed}",
            f"文面生成          : {self.generated}",
            f"文面再利用        : {self.reused}",
            f"初回送信(実送信)  : {self.sent}",
            f"初回送信(dry-run) : {self.dry_run}",
            f"重複スキップ      : {self.skipped_duplicate}",
            f"対象外スキップ    : {self.skipped_ineligible}",
            f"失敗              : {self.failed}",
        ]
        if self.ineligible:
            lines.append("\n--- 要確認（対象条件を満たさない候補者）---")
            for mno, reasons in self.ineligible:
                lines.append(f"  {mno}: {' / '.join(reasons)}")
        if self.errors:
            lines.append("\n--- エラー ---")
            for mno, err in self.errors:
                lines.append(f"  {mno}: {err}")
        return "\n".join(lines)


class ScoutPipeline:
    def __init__(
        self,
        repo: Repository | None = None,
        generator: ScoutGenerator | None = None,
        sender=None,
    ):
        self.settings = get_settings()
        self.rules = scout_rules()
        self.repo = repo or Repository()
        self.generator = generator or ScoutGenerator()
        self.sender = sender  # BizreachSender or None（None なら生成のみ）

    def _send_delay(self) -> None:
        lo = self.settings.send_delay_min
        hi = max(self.settings.send_delay_max, lo)
        time.sleep(random.uniform(lo, hi))

    def run(
        self, source: CandidateSource, send: bool = True, sent_offset: int = 0
    ) -> PipelineReport:
        """1つの検索ソースを処理する。

        sent_offset は同一サイクルで既に送信済みの件数。複数検索URLをまたいでも
        1実行あたりの送信上限(max_sends_per_run)を守るために使う。
        """
        report = PipelineReport()
        on_ineligible = self.rules.get("eligibility", {}).get("on_ineligible", "skip")

        for candidate in source:
            report.processed += 1
            self._process_one(candidate, send, on_ineligible, report, sent_offset)

        logger.info("\n%s", report.summary())
        return report

    def _process_one(
        self,
        candidate: Candidate,
        send: bool,
        on_ineligible: str,
        report: PipelineReport,
        sent_offset: int = 0,
    ) -> None:
        mno = candidate.member_no
        elig = check_eligibility(candidate, self.rules)
        self.repo.upsert_candidate(candidate, elig)

        if not elig.eligible:
            report.ineligible.append((mno, elig.failed))
            if on_ineligible == "skip":
                report.skipped_ineligible += 1
                logger.info("対象外のためスキップ: %s (%s)", mno, " / ".join(elig.failed))
                return
            logger.warning("対象外だが処理続行: %s (%s)", mno, " / ".join(elig.failed))

        # 送信済みのみ重複スキップ。未送信(generated/skipped/failed)は再試行する。
        if self.repo.first_sent(mno):
            report.skipped_duplicate += 1
            logger.info("既に送信済み（重複スキップ）: %s", mno)
            return

        # --- 文面の用意（既存の未送信文面があれば再生成せず再利用）---
        existing = self.repo.get_scout(mno, "first")
        if existing is not None:
            subject, body = existing["subject"], existing["body"]
            report.reused += 1
            logger.info("生成済み文面を再利用: %s", mno)
        else:
            try:
                scout = self.generator.generate(candidate)
            except Exception as e:  # noqa: BLE001
                report.failed += 1
                report.errors.append((mno, f"生成失敗: {e}"))
                logger.error("文面生成に失敗: %s: %s", mno, e)
                return
            self.repo.record_generated(scout, self.settings.resend_after_days)
            _export_scout(scout)
            report.generated += 1
            subject, body = scout.first.subject, scout.first.body
            logger.info("文面生成完了: %s", mno)

        # --- 初回送信 ---
        if not (send and self.sender is not None):
            return
        if (sent_offset + report.sent + report.dry_run) >= self.settings.max_sends_per_run:
            logger.info("1回あたりの送信上限(%d)に達したため送信スキップ: %s",
                        self.settings.max_sends_per_run, mno)
            self.repo.mark_skipped(mno, "first", "max_sends_per_run reached")
            return
        if not candidate.profile_url:
            logger.info("プロフィールURLが無いため自動送信不可（生成のみ）: %s", mno)
            self.repo.mark_skipped(mno, "first", "no profile_url for sending")
            return

        outcome = self.sender.send_scout(candidate.profile_url, subject, body)
        if outcome.status == "sent":
            self.repo.mark_sent(mno, "first", self.settings.resend_after_days)
            report.sent += 1
            logger.info("初回送信完了: %s", mno)
            self._send_delay()
        elif outcome.status == "dry_run":
            # 実送信していないので generated のまま（後で本番送信できる）。
            report.dry_run += 1
            logger.info("[DRY-RUN] 初回送信を模擬: %s", mno)
            self._send_delay()  # dry_run でも実ブラウザ操作のため人間的間隔を空ける
        elif outcome.status == "blocked":
            self.repo.mark_skipped(mno, "first", outcome.detail)
            logger.warning("送信ブロック（kill switch等）: %s", mno)
        else:
            self.repo.mark_failed(mno, "first", outcome.detail)
            report.failed += 1
            report.errors.append((mno, f"送信失敗: {outcome.detail}"))
