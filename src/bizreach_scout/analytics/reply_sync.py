"""返信の自動検知（受信箱スキャン → レジュメ再取得の2段構え）。

1. 受信箱スキャン（主）: /message/?folderCd=inbox のHTMLに送信済み候補者の
   識別子（会員番号/mrccid）が現れるかで返信を権威的に判定する。
   受信箱にメッセージがある = 返信あり（辞退の返信も含む）。
2. レジュメ再取得（副）: 未返信の候補者のレジュメを再取得し、氏名開示・
   contactHistory の候補者側イベントから判定する（reply_detect）。
   対象は reply_checked_at のローテーション順（未チェック→最も昔にチェック）で、
   毎回同じ候補者だけを見続けない（全員が数日で一巡する）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..bizreach.inbox import ascii_shape, find_sent_in_html
from ..bizreach.reply_detect import detect_reply
from ..logging_config import logger
from ..storage.repository import Repository


@dataclass
class ReplySyncReport:
    inbox_pages: int = 0
    inbox_detected: int = 0
    checked: int = 0
    detected: int = 0
    errors: int = 0

    def summary(self) -> str:
        return (f"返信チェック: 受信箱{self.inbox_pages}ページから{self.inbox_detected}件検知 / "
                f"レジュメ{self.checked}件確認から{self.detected}件検知 / エラー{self.errors}件")


def sync_inbox_replies(htmls: list[str], repo: Repository) -> int:
    """受信箱HTMLから送信済み候補者の返信を検知して記録する。戻り値は新規検知数。"""
    if not htmls:
        return 0
    html = "\n".join(htmls)
    pairs = repo.sent_members_with_mrccid()
    found = find_sent_in_html(html, pairs)
    if not found:
        # 識別子が1つも見つからない場合、構造調査用にPIIなしのダイジェストを出す
        # （会員番号がHTMLに出ない画面仕様の可能性。次の改善の材料にする）。
        logger.info("受信箱に送信済み候補者の識別子が見つかりませんでした（送信済み%d名と照合）。",
                    len(pairs))
        logger.info("受信箱ページの構造（PIIなし・先頭2000字）: %s", ascii_shape(html))
        return 0
    marked = 0
    for member_no in sorted(found):
        if repo.is_replied(member_no):
            continue
        repo.upsert_reply(member_no, replied=True, replied_at=None,
                          detected_by="auto", note="受信箱にメッセージあり")
        marked += 1
        logger.info("返信を検知（受信箱）: %s", member_no)
    logger.info("受信箱スキャン: 一致%d名 / 新規に返信あり%d名", len(found), marked)
    return marked


def sync_replies(api, repo: Repository, *, max_checks: int = 60,
                 recent_days: int = 45, client=None, scanner=None) -> ReplySyncReport:
    """返信の自動検知を実行する。

    api: BizreachApi（get_resume(mrccid) を持つ）。client: human_delay 用（省略可）。
    scanner: InboxScanner（fetch_pages() を持つ・省略時は受信箱スキャンをスキップ）。
    """
    report = ReplySyncReport()

    # 1. 受信箱スキャン（主・権威的シグナル）。
    if scanner is not None:
        try:
            htmls = scanner.fetch_pages()
            report.inbox_pages = len(htmls)
            report.inbox_detected = sync_inbox_replies(htmls, repo)
        except Exception as e:
            logger.warning("受信箱スキャンに失敗（レジュメ確認は継続します）: %s", e)
            report.errors += 1

    # 2. レジュメ再取得（副・ローテーションで全員を一巡させる）。
    targets = repo.unreplied_sent(recent_days=recent_days, limit=max_checks)
    logger.info("返信自動チェック対象: %d件（直近%d日・上限%d件・ローテーション順）",
                len(targets), recent_days, max_checks)

    for row in targets:
        member_no = row["member_no"]
        cand = repo.load_candidate(member_no)
        if cand is None or not cand.mrccid:
            repo.mark_reply_checked(member_no)  # 対象外もローテーションを前進させる
            continue
        try:
            resume = api.get_resume(cand.mrccid)
        except Exception as e:
            logger.warning("返信チェック用のレジュメ取得に失敗 %s: %s", member_no, e)
            report.errors += 1
            repo.mark_reply_checked(member_no)
            continue
        report.checked += 1
        repo.mark_reply_checked(member_no)
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
            logger.info("返信を検知（レジュメ）: %s（%s）", member_no, signal.evidence)
        if client is not None:
            client.human_delay(1.0, 2.5)

    logger.info(report.summary())
    return report
