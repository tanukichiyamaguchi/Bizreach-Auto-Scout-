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


def body_shape(html: str, limit: int = 6000) -> str:
    """<head>・script・style を除いた本文のみのPIIなし構造表現（純関数）。

    受信箱はSPA殻の <head> が巨大で、先頭切り出しだと本文（メッセージ一覧）が
    ダイジェストに入らないため、本文だけを対象にする。
    """
    body = re.sub(r"(?is)<head\b.*?</head>", "", html or "")
    body = re.sub(r"(?is)<script\b.*?</script>", "", body)
    body = re.sub(r"(?is)<style\b.*?</style>", "", body)
    body = re.sub(r"\s*\n\s*", "\n", body)
    return ascii_shape(body, limit)


# 詳細リンクから除外する「一覧・操作系」URLのパターン。
_NON_DETAIL_LINK = re.compile(
    r"folderCd=|pageSize=|logout|\.css|\.js|\.ico|javascript:|^#|^mailto:"
    r"|/message/?$|/message/template", re.I)
# 「候補者の詳細/スレッド」に遷移しそうなリンクのキーワード（受信箱の行→候補者）。
_DETAIL_KW = re.compile(r"message|candidate|resume|detail|thread|scout|mrcc|talk", re.I)


def extract_message_links(html: str, base: str, limit: int = 30) -> list[str]:
    """受信箱HTMLから候補者スレッド/詳細らしいリンクを抽出する（純関数）。

    一覧行の遷移先（候補者スレッド）には mrccid など識別子が含まれる可能性が高い。
    メニュー（/message/・/message/template）やフォルダ切替・アセットは除外する。
    """
    base = base.rstrip("/")
    seen: set[str] = set()
    out: list[str] = []
    for href in re.findall(r'href="([^"]+)"', html or ""):
        if not _DETAIL_KW.search(href) or _NON_DETAIL_LINK.search(href):
            continue
        url = href if href.startswith("http") else base + "/" + href.lstrip("/")
        if not url.startswith(base):
            continue  # 外部サイトは対象外
        if url not in seen:
            seen.add(url)
            out.append(url)
        if len(out) >= limit:
            break
    return out


# DOM から拾う「リンク/クリック信号」属性（Angularの ng-click / data-* も対象）。
_SIGNAL_ATTR = re.compile(
    r'(?:href|ng-href|ng-click|onclick|data-[\w-]+|data-url)="([^"]{3,200})"', re.I)
# 候補者ID/mrccid らしい長い英数字トークン。
_ID_TOKEN = re.compile(r"[A-Za-z0-9_-]{18,28}")


def extract_dom_signals(html: str, limit: int = 50) -> list[str]:
    """DOMから候補者スレッド遷移らしい属性値（href/ng-click/data-*）を抽出（純関数）。"""
    seen: set[str] = set()
    out: list[str] = []
    for m in _SIGNAL_ATTR.finditer(html or ""):
        v = m.group(1).strip()
        if _DETAIL_KW.search(v) and v not in seen:
            seen.add(v)
            out.append(v)
        if len(out) >= limit:
            break
    return out


def extract_id_tokens(html: str, limit: int = 60) -> list[str]:
    """DOM中のmrccid様トークン（長い英数字）を重複なしで抽出（純関数・診断用）。"""
    seen: set[str] = set()
    out: list[str] = []
    for t in _ID_TOKEN.findall(html or ""):
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= limit:
            break
    return out


# 静的アセット（ライブラリ/画像）。データ応答を埋もれさせるためインデックスから除外。
_STATIC_ASSET = re.compile(r"\.(js|css|ico|png|jpe?g|gif|svg|woff2?|map)(\?|$)", re.I)


def api_index(responses: list[tuple[str, str]]) -> str:
    """捕捉したAPI応答のPIIなしインデックス（url・サイズ・構造）を文字列化（純関数）。

    データ応答（DWR plaincall / JSON）を見えるように、静的アセット(.js/.css等)は除外する。
    """
    import json as _json

    from .reply_probe import redact_shape  # 遅延importで循環回避

    best: dict[str, str] = {}
    for url, body in responses:
        if _STATIC_ASSET.search(url):
            continue  # ライブラリ本体は識別子照合にもインデックスにも不要
        if url not in best or len(body) > len(best[url]):
            best[url] = body
    ranked = sorted(best.items(), key=lambda t: -len(t[1]))
    lines = [f"[captured API] データ応答 {len(best)}種（静的アセット除外）"]
    for i, (url, body) in enumerate(ranked[:20]):
        keys = ""
        with contextlib.suppress(Exception):
            data = _json.loads(body)
            if isinstance(data, dict):
                keys = f" keys={sorted(data.keys())[:12]}"
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                keys = f" list[0].keys={sorted(data[0].keys())[:12]}"
        lines.append(f"  [{i:02d}] {url} ({len(body)}B){keys}")
    # データ応答上位3件の構造サンプル（値は伏せ字。DWRはJSONでないためテキスト断片）。
    for url, body in ranked[:3]:
        with contextlib.suppress(Exception):
            data = _json.loads(body)
            lines.append(f"  構造[{url[-60:]}]: "
                         + _json.dumps(redact_shape(data, max_depth=4),
                                       ensure_ascii=False)[:1500])
            continue
        # 非JSON（DWR等）は英数字トークンだけ抜き出してPIIなしで様子を見る。
        tokens = re.findall(r"[A-Za-z0-9_]{4,40}", body)
        uniq: list[str] = []
        for t in tokens:
            if t not in uniq:
                uniq.append(t)
        lines.append(f"  非JSON応答トークン[{url[-60:]}]: {uniq[:60]}")
    return "\n".join(lines)


class InboxScanner:
    """受信箱を巡回し、DOMと**裏で流れるAPI応答**の両方を取得する（読み取りのみ）。

    メッセージ画面はAngularJSのSPAで、一覧はDOMではなくXHR/fetchのJSONで描画される
    （2026-07-21 実データで確認）。そのため応答本文も捕捉し、候補者の識別子照合に使う。
    """

    def __init__(self, client):
        self.client = client
        self.base = client.sel.base_url.rstrip("/")
        self.responses: list[tuple[str, str]] = []  # (url, body) 自社APIのみ
        self._installed = False

    def _install_capture(self) -> None:
        if self._installed:
            return
        self._installed = True

        def on_response(resp):
            with contextlib.suppress(Exception):
                req = resp.request
                rtype = getattr(req, "resource_type", "")
                url = resp.url
                if "cr-support.jp" not in url:
                    return
                if rtype not in ("xhr", "fetch") and "/api/" not in url \
                        and "/dwr/" not in url and "/ajax/" not in url:
                    return
                body = resp.text()
                if body:
                    self.responses.append((url, body[:400_000]))

        self.client.page.on("response", on_response)

    def captured_text(self) -> str:
        """捕捉したAPI応答本文の連結（識別子の部分文字列照合用）。"""
        return "\n".join(b for _u, b in self.responses)

    def fetch_pages(self, max_pages: int = 5, page_size: int = 50) -> list[str]:
        self._install_capture()
        page = self.client.page
        htmls: list[str] = []
        for n in range(1, max_pages + 1):
            url = (f"{self.base}/message/?pageSize={page_size}&folderCd=inbox"
                   f"&currentPageNo={n}&statusDecline=true&kw=")
            try:
                page.goto(url, wait_until="domcontentloaded")
                with contextlib.suppress(Exception):
                    page.wait_for_load_state("networkidle", timeout=10000)
                # SPA(DWR)がメッセージ一覧をAJAXで取りに行くのを待ってから本文を読む。
                with contextlib.suppress(Exception):
                    page.wait_for_response(
                        lambda r: "/dwr/call/" in r.url or "/message" in r.url.lower(),
                        timeout=8000)
                self.client.human_delay(3.0, 4.0)
                html = page.content()
            except Exception as e:
                logger.warning("受信箱ページ %d の取得に失敗: %s", n, e)
                break
            htmls.append(html)
            # 次ページへのリンクが無ければ終了。
            if f"currentPageNo={n + 1}" not in html:
                break
        logger.info("受信箱を %d ページ取得（API応答 %d 件を捕捉）。",
                    len(htmls), len(self.responses))
        return htmls

    def fetch_detail_pages(self, list_htmls: list[str],
                           max_details: int = 30) -> list[str]:
        """受信箱一覧からメッセージ詳細ページを開いてHTMLを集める。

        一覧行には候補者の氏名しか出ず識別子が無いことがあるため、
        詳細ページ（スレッド画面。会員番号・レジュメへのリンクが出る）で照合する。
        """
        links: list[str] = []
        seen: set[str] = set()
        for html in list_htmls:
            for url in extract_message_links(html, self.base, limit=max_details):
                if url not in seen:
                    seen.add(url)
                    links.append(url)
        links = links[:max_details]
        page = self.client.page
        htmls: list[str] = []
        for url in links:
            try:
                page.goto(url, wait_until="domcontentloaded")
                with contextlib.suppress(Exception):
                    page.wait_for_load_state("networkidle", timeout=8000)
                self.client.human_delay(1.0, 2.0)
                htmls.append(page.content())
            except Exception as e:
                logger.warning("メッセージ詳細の取得に失敗: %s", e)
        logger.info("メッセージ詳細を %d/%d 件取得しました。", len(htmls), len(links))
        return htmls
