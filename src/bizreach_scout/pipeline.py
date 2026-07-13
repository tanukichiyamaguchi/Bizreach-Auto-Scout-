"""エンドツーエンドのスカウト処理パイプライン。

流れ: 取り込み → 対象条件判定 → 重複チェック → 文面生成 → 保存 → (初回)送信 → 再送予約。
完全自動送信に対応しつつ、暴走防止（上限件数・送信間隔・kill switch・dry_run）を備える。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .config import get_settings, scout_rules
from .eligibility import check_eligibility
from .export import export_scout as _export_scout
from .generation.generator import ScoutGenerator
from .ingest.base import CandidateSource
from .logging_config import logger
from .models import Candidate
from .storage.repository import Repository

_REMINDER_DAYS = {3: "ThreeDays", 5: "FiveDays", 10: "TenDays"}


def _reminder_days_after(days: int) -> str:
    """reminder の daysAfter（ThreeDays/FiveDays/TenDays）に丸める。"""
    return _REMINDER_DAYS.get(days, "FiveDays")


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
        apply_status_filter: bool = True,
    ):
        self.settings = get_settings()
        self.rules = scout_rules()
        # 再送日数は scout_rules.yaml resend.after_days が単一情報源。
        self.resend_after_days = int(self.rules.get("resend", {}).get("after_days", 5))
        self.repo = repo or Repository()
        self.generator = generator or ScoutGenerator()
        self.sender = sender  # ApiScoutSender or None（None なら生成のみ）
        # ピックアップ求人は会員ステータス条件を適用しない（False で渡す）。
        self.apply_status_filter = apply_status_filter

    def _guard_state_present(self, send: bool) -> None:
        """状態DB消失ガード。

        expect_state=true かつ実送信を行う（sender あり・dry_run でない）のに
        送信済みレコードが1件も無い場合、dedupe用の状態DBが消えた可能性が高い。
        そのまま進めると全候補者を「初回」として再送信してしまうため中断する。
        （本当の初回運用時は expect_state=false のままにしておく。）
        """
        if not (send and self.sender is not None):
            return
        if not self.settings.expect_state:
            return
        if getattr(self.sender, "dry_run", self.settings.dry_run):
            return
        if not self.repo.has_any_sent():
            raise RuntimeError(
                "BIZSCOUT_EXPECT_STATE=true ですが送信履歴(状態DB)が空です。"
                "actions/cache 失効などで重複防止データが消えた可能性があります。"
                "全候補者への再送信を防ぐため中断しました。復旧手順は "
                "docs/GitHub Actionsで運用.md を参照してください。"
            )

    def _send_delay(self) -> None:
        lo = self.settings.send_delay_min
        hi = max(self.settings.send_delay_max, lo)
        time.sleep(random.uniform(lo, hi))

    def _build_reminder(self, resend_subject: str, resend_body: str) -> dict | None:
        """初回送信に添付する追客(reminder)を組み立てる。無効設定/内容欠如なら None。"""
        cfg = self.rules.get("resend", {})
        if not cfg.get("use_native_reminder", True):
            return None
        if not (resend_subject and resend_body):
            return None
        return {
            "daysAfter": _reminder_days_after(self.resend_after_days),
            "subject": resend_subject,
            "body": resend_body,
        }

    def run(
        self, source: CandidateSource, send: bool = True, sent_offset: int = 0
    ) -> PipelineReport:
        """1つの検索ソースを処理する。

        sent_offset は同一サイクルで既に送信済みの件数。複数検索URLをまたいでも
        1実行あたりの送信上限(max_sends_per_run)を守るために使う。
        """
        report = PipelineReport()
        self._guard_state_present(send)
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
        elig = check_eligibility(candidate, self.rules,
                                 apply_status_filter=self.apply_status_filter)
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
            resend_row = self.repo.get_scout(mno, "resend")
            resend_subject = resend_row["subject"] if resend_row else ""
            resend_body = resend_row["body"] if resend_row else ""
            report.reused += 1
            logger.info("生成済み文面を再利用: %s", mno)
        else:
            try:
                scout = self.generator.generate(candidate)
            except Exception as e:
                report.failed += 1
                report.errors.append((mno, f"生成失敗: {e}"))
                logger.error("文面生成に失敗: %s: %s", mno, e)
                return
            self.repo.record_generated(scout)
            _export_scout(scout)
            report.generated += 1
            subject, body = scout.first.subject, scout.first.body
            resend_subject, resend_body = scout.resend.subject, scout.resend.body
            logger.info("文面生成完了: %s", mno)

        # --- 初回送信 ---
        if not (send and self.sender is not None):
            return
        if (sent_offset + report.sent + report.dry_run) >= self.settings.max_sends_per_run:
            logger.info("1回あたりの送信上限(%d)に達したため送信スキップ: %s",
                        self.settings.max_sends_per_run, mno)
            self.repo.mark_skipped(mno, "first", "max_sends_per_run reached")
            return

        # 再送はビズリーチ標準の追客(reminder)で初回送信時に予約（設定で切替可）。
        reminder = self._build_reminder(resend_subject, resend_body)
        # 実送信の直前に送信意図と冪等キーを永続化する（write-ahead）。
        # 「送信成功→mark_sent」の間にクラッシュしても、次回の再試行が同一キーで
        # 送られるためサーバ側dedupeが効き二重送信にならない。
        # dry_run では状態遷移を変えない（generated のまま＝従来どおり）。
        idem_key = None
        if not getattr(self.sender, "dry_run", self.settings.dry_run):
            idem_key = self.repo.begin_send(mno, "first")
        outcome = self.sender.send_scout(candidate, subject, body, reminder=reminder,
                                         idempotency_key=idem_key)
        if outcome.status == "sent":
            self.repo.mark_sent(mno, "first", self.resend_after_days)
            if reminder:
                # ビズリーチ側が5日後に自動追客するため、独自再送は行わない（二重送信防止）。
                self.repo.mark_skipped(mno, "resend", "native_reminder(ビズリーチ追客で自動送信)")
            report.sent += 1
            logger.info("初回送信完了: %s%s", mno,
                        "（5日後の追客も予約済み）" if reminder else "")
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
