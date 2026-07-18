"""返信の自動検知（レジュメ再取得 → reply_detect → replies へ記録）。

対象は「送信済みで未返信、かつ初回送信から recent_days 以内」の候補者（古い順に
max_checks 件まで）。レジュメの再取得は人間的な間隔を空けて行う。
検知述語（reply_detect.detect_reply）は偵察結果に応じて更新される前提で分離してある。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..bizreach.reply_detect import detect_reply
from ..logging_config import logger
from ..storage.repository import Repository


@dataclass
class ReplySyncReport:
    checked: int = 0
    detected: int = 0
    errors: int = 0

    def summary(self) -> str:
        return f"返信チェック: {self.checked}件確認 / {self.detected}件を返信ありに更新 / エラー{self.errors}件"


def sync_replies(api, repo: Repository, *, max_checks: int = 60,
                 recent_days: int = 45, client=None) -> ReplySyncReport:
    """未返信の送信済み候補者のレジュメを再取得し、返信シグナルを検知して記録する。

    api: BizreachApi（get_resume(mrccid) を持つ）。client: human_delay 用（省略可）。
    """
    report = ReplySyncReport()
    targets = repo.unreplied_sent(recent_days=recent_days, limit=max_checks)
    logger.info("返信自動チェック対象: %d件（直近%d日・上限%d件）",
                len(targets), recent_days, max_checks)

    for row in targets:
        member_no = row["member_no"]
        cand = repo.load_candidate(member_no)
        if cand is None or not cand.mrccid:
            continue
        try:
            resume = api.get_resume(cand.mrccid)
        except Exception as e:
            logger.warning("返信チェック用のレジュメ取得に失敗 %s: %s", member_no, e)
            report.errors += 1
            continue
        report.checked += 1
        if not isinstance(resume, dict):
            continue
        signal = detect_reply(resume)
        if signal.replied:
            repo.upsert_reply(
                member_no,
                replied=True,
                replied_at=signal.replied_at,
                detected_by="auto",
                candidate_name=signal.candidate_name,
                note=signal.evidence,
            )
            report.detected += 1
            logger.info("返信を検知: %s（%s）", member_no, signal.evidence)
        if client is not None:
            client.human_delay(1.0, 2.5)

    logger.info(report.summary())
    return report
