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

from ..bizreach.inbox import (
    api_index,
    body_shape,
    extract_dom_signals,
    extract_id_tokens,
    extract_message_links,
    find_sent_in_html,
    match_subjects,
)
from ..bizreach.reply_detect import detect_reply
from ..logging_config import logger
from ..storage.repository import Repository


@dataclass
class ReplySyncReport:
    inbox_pages: int = 0
    inbox_details: int = 0
    inbox_detected: int = 0
    checked: int = 0
    detected: int = 0
    errors: int = 0

    def summary(self) -> str:
        return (f"返信チェック: 受信箱{self.inbox_pages}ページ+詳細{self.inbox_details}件から"
                f"{self.inbox_detected}件検知 / "
                f"レジュメ{self.checked}件確認から{self.detected}件検知 / エラー{self.errors}件")


def _captured_text(scanner) -> str:
    """scanner が捕捉したAPI応答本文（無ければ空文字）。"""
    fn = getattr(scanner, "captured_text", None)
    return fn() if callable(fn) else ""


def _captured_responses(scanner) -> list:
    return list(getattr(scanner, "responses", []) or [])


def _log_structure_digest(scanner, list_htmls: list[str], detail_htmls: list[str],
                          base: str) -> None:
    """照合が0件だったときの調査材料（PIIなし）をログへ出す。"""
    # 主因はSPAのAPI応答。まず捕捉したAPIの構造を出す（識別子フィールド特定用）。
    responses = _captured_responses(scanner)
    if responses:
        for line in api_index(responses).split("\n"):
            logger.info("%s", line)
    first = list_htmls[0] if list_htmls else ""
    for i, chunk in enumerate([body_shape(first)[j:j + 800] for j in range(0, 2400, 800)]):
        if chunk.strip():
            logger.info("受信箱本文の構造(%d): %s", i, chunk)
    logger.info("受信箱の詳細リンク候補（最大10件）: %s",
                extract_message_links(first, base, limit=10))
    # DOMのリンク/クリック信号と mrccid様トークン（候補者スレッドの遷移先特定用）。
    logger.info("受信箱DOMのリンク/クリック信号: %s", extract_dom_signals(first, limit=40))
    logger.info("受信箱DOMのID様トークン（先頭）: %s", extract_id_tokens(first, limit=40))
    # 詳細ページを開けていれば、その中の信号も出す（スレッド→履歴書リンク特定用）。
    if detail_htmls:
        logger.info("詳細ページDOMのリンク/クリック信号: %s",
                    extract_dom_signals(detail_htmls[0], limit=40))
        logger.info("詳細ページDOMのID様トークン（先頭）: %s",
                    extract_id_tokens(detail_htmls[0], limit=40))


def sync_inbox_replies(scanner, repo: Repository, report: ReplySyncReport) -> None:
    """受信箱から送信済み候補者の返信を検知して記録する。

    照合は2本立て:
    1. 件名照合（主）: 受信箱の返信は「Re: <送信した件名>」で並ぶ。件名は候補者ごとに
       個別生成の一点物で全件DBに保存済みのため、これが会員番号の突合キーになる
       （2026-07-21 スクリーンショットで実確認。返信者は実名表示で識別子が出ないため）。
    2. 識別子照合（副）: 会員番号/mrccid が DOM・API応答に現れた場合。
    それでも0件なら各メッセージの詳細ページまで開いて再照合する。
    """
    list_htmls = scanner.fetch_pages()
    report.inbox_pages = len(list_htmls)
    if not list_htmls:
        logger.info("受信箱を取得できませんでした（今回は返信の照合をスキップ）。")
        return
    pairs = repo.sent_members_with_mrccid()
    subjects = repo.sent_subjects()
    # DOM と API応答の両方を対象に照合（一覧の描画がDOMでもAJAXでも取りこぼさない）。
    blob = "\n".join(list_htmls) + "\n" + _captured_text(scanner)
    by_subject = match_subjects(blob, subjects)
    by_id = find_sent_in_html(blob, pairs)

    detail_htmls: list[str] = []
    if not (by_subject or by_id):
        # 各メッセージの詳細ページを開いて照合する（DOMとAPIの両方）。
        detail_htmls = scanner.fetch_detail_pages(list_htmls)
        report.inbox_details = len(detail_htmls)
        blob = "\n".join(detail_htmls) + "\n" + _captured_text(scanner)
        by_subject |= match_subjects(blob, subjects)
        by_id |= find_sent_in_html(blob, pairs)

    if not (by_subject or by_id):
        logger.info("受信箱に送信済み候補者の識別子・件名が見つかりませんでした"
                    "（送信済み%d名・件名%d件と照合）。", len(pairs), len(subjects))
        _log_structure_digest(scanner, list_htmls, detail_htmls, getattr(scanner, "base", ""))

    # 受信箱スキャンの結果を自動返信の唯一の真実として反映（誤検知を自己修復）。
    detected = {m: "受信箱に返信（件名一致）" for m in by_subject}
    for m in by_id - by_subject:
        detected[m] = "受信箱にメッセージあり"
    added, removed = repo.reconcile_auto_replies(detected)
    report.inbox_detected = added
    logger.info("受信箱スキャン: 件名一致%d名 / 識別子一致%d名 / 新規%d名 / 取消%d名",
                len(by_subject), len(by_id), added, removed)


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
            sync_inbox_replies(scanner, repo, report)
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
