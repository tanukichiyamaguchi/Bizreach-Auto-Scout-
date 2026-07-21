"""受信箱（メッセージ一覧）からの返信検知。

ビズリーチの受信箱 /message/?folderCd=inbox はサーバレンダリングのHTML画面で、
返信一覧を返すJSON APIは存在しない（2026-07-18 の probe-replies 偵察で確認）。
そのため受信箱ページのHTMLを取得し、**自分たちが送信した候補者の識別子
（会員番号 BUxxxxxxx / mrccid）がHTML内に現れるか**で返信を判定する。

「受信箱にその候補者からのメッセージがある」= 候補者が返信した、という
権威的なシグナルであり、レジュメの氏名開示より確実に返信を捉えられる。
辞退の返信も「返信あり」として数える（statusDecline=true で辞退も含めて取得）。

HTML の行構造には依存しない（部分文字列の照合のみ）ため、画面の
マークアップ変更に強い。識別子が1つも見つからない場合は、PIIを伏せた
構造ダイジェストをログに出して調査材料にする。
"""

from __future__ import annotations

import contextlib
import re

from ..logging_config import logger

# 会員番号のパターン（実データ: BU3765516 / BU03803587 など 7〜8桁を確認済み）。
_MEMBER_NO = re.compile(r"BU\d{6,10}")
# mrccid の誤検知を防ぐ最小長（短い値の偶然一致を除外）。
_MIN_MRCCID_LEN = 8


def extract_member_nos(html: str) -> list[str]:
    """HTMLから会員番号（BU…）を出現順・重複なしで抽出する（純関数）。"""
    seen: set[str] = set()
    out: list[str] = []
    for m in _MEMBER_NO.findall(html or ""):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def find_sent_in_html(html: str, pairs: list[tuple[str, str]]) -> set[str]:
    """送信済み候補者のうち、HTML内に識別子が現れる member_no の集合を返す（純関数）。

    pairs: (member_no, mrccid) のリスト。member_no か mrccid のどちらかが
    HTMLに部分文字列として現れれば「受信箱にその候補者のメッセージがある」とみなす。
    """
    if not html:
        return set()
    found: set[str] = set()
    for member_no, mrccid in pairs:
        if (member_no and member_no in html) or (
                mrccid and len(mrccid) >= _MIN_MRCCID_LEN and mrccid in html):
            found.add(member_no)
    return found


def ascii_shape(text: str, limit: int = 2000) -> str:
    """非ASCII文字の連なりを〈文字数〉に置換したPIIなしの構造表現（純関数）。

    候補者名・企業名など日本語のPIIは長さだけになり、BU番号・日付・URL・
    class名などASCIIの構造情報は残る（受信箱の構造調査用）。
    """
    shaped = re.sub(r"[^\x00-\x7f]+", lambda m: f"({len(m.group(0))})", text or "")
    shaped = re.sub(r"[ \t]+", " ", shaped)
    return shaped[:limit]


class InboxScanner:
    """受信箱のHTMLをページ送りしながら取得する（読み取りのみ・実送信なし）。"""

    def __init__(self, client):
        self.client = client
        self.base = client.sel.base_url.rstrip("/")

    def fetch_pages(self, max_pages: int = 5, page_size: int = 50) -> list[str]:
        page = self.client.page
        htmls: list[str] = []
        for n in range(1, max_pages + 1):
            url = (f"{self.base}/message/?pageSize={page_size}&folderCd=inbox"
                   f"&currentPageNo={n}&statusDecline=true&kw=")
            try:
                page.goto(url, wait_until="domcontentloaded")
                with contextlib.suppress(Exception):
                    page.wait_for_load_state("networkidle", timeout=10000)
                self.client.human_delay(1.0, 2.0)
                html = page.content()
            except Exception as e:
                logger.warning("受信箱ページ %d の取得に失敗: %s", n, e)
                break
            htmls.append(html)
            # 次ページへのリンクが無ければ終了。
            if f"currentPageNo={n + 1}" not in html:
                break
        logger.info("受信箱を %d ページ取得しました。", len(htmls))
        return htmls
