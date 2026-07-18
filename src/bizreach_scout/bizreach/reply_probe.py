"""返信データの偵察（実送信なし・読み取りのみ）。

ビズリーチの「メッセージ／スカウト管理／候補者管理」系の画面を開き、その際に流れる
XHR/fetch 応答を全て記録する。返信一覧・スレッド系エンドポイントの特定が目的
（PickupProbe と同じ「画面を開いてAPIトラフィックを丸ごと録る」パターン）。

成果物は2系統で残す:
1. data/exports/reply_*.json … 生の応答（artifact 用）。
2. ジョブログ／$GITHUB_STEP_SUMMARY … PIIを型・長さに置換した「構造ダイジェスト」。
   artifact をダウンロードできない環境でも、ログを読むだけで返信APIの形と
   返信を示すフィールドを特定できるようにするため（reply_detect の述語確定用）。

あわせて送信済み候補者1名のレジュメを再取得し、その全トップレベルキーと（値を伏せた）
構造を出力する。返信・接触状態を示すフィールド名を実データから見つけるのが狙い。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path

from ..config import project_root
from ..logging_config import logger

# クリックして回る画面リンクのテキスト候補（存在しないものはスキップ）。
# 返信（受信）側の一覧を先に開くため、受信・返信系を前方に置く。
_NAV_TEXTS = (
    "メッセージ", "受信", "返信", "やりとり", "スカウト管理", "候補者管理", "送信済み",
)
# 「返信・メッセージ関連らしい」応答を選ぶための URL / キー名キーワード。
_REPLY_KEYWORDS = re.compile(
    r"reply|thread|inbox|unread|message|conversation|contact|scout|candidate|talk",
    re.I,
)
# レジュメで特に注目する返信・接触シグナルのキー（存在すれば値の様子も見る）。
_RESUME_SIGNAL_KEYS = (
    "candidateName", "contactHistory", "hasContact", "lastLoginDate",
    "scoutStatus", "contactStatus", "replyStatus", "hasReply", "repliedAt",
)

# --- PII 伏せ字（構造ダイジェスト用の純関数） -------------------------------
# 値を「型・長さ」に置換して個人情報をログに出さない。ただし列挙値・ID・日付など
# 判定に効く短い安全文字列はそのまま見せる（名前・メール・企業名は伏せる）。
_PII_KEY = re.compile(
    r"name|mail|company|corp|tel|phone|address|kana|furigana|birth|nick|url",
    re.I,
)
_SAFE_STR = re.compile(r"^[A-Za-z0-9_\-.:/]+$")


def _redact_str(value: str, key: str) -> str:
    """文字列値を安全に表す。列挙値等はそのまま、名前等の恐れがあれば長さに置換。"""
    if key and _PII_KEY.search(key):
        return f"str:{len(value)}"
    if len(value) <= 40 and _SAFE_STR.match(value):
        return value  # 列挙値・ID・ISO日付など（判定に有用・PIIの恐れ低）
    return f"str:{len(value)}"


def redact_shape(value: object, key: str = "", depth: int = 0, max_depth: int = 5):
    """JSONの構造を保ったまま値をPIIなしの型情報へ置換する（純関数）。

    - dict: キーは残し、値を再帰的に置換（キー数が多い場合は先頭50件）。
    - list: 長さと先頭要素の構造のみ（`["len=N", <先頭要素の構造>]`）。
    - str : 列挙値等はそのまま／名前等の恐れは "str:長さ"。
    - bool/None: そのまま（hasContact:true 等は判定に有用）。
    - int/float: 型名のみ（ID・年齢等の実値は出さない）。
    """
    if isinstance(value, dict):
        if depth >= max_depth:
            return f"{{…{len(value)}キー}}"
        return {k: redact_shape(v, k, depth + 1, max_depth)
                for k, v in list(value.items())[:50]}
    if isinstance(value, list):
        if not value:
            return []
        if depth >= max_depth:
            return f"[…{len(value)}件]"
        return [f"len={len(value)}", redact_shape(value[0], key, depth + 1, max_depth)]
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return type(value).__name__
    if isinstance(value, str):
        return _redact_str(value, key)
    return type(value).__name__


def _top_keys(body: str) -> list[str]:
    with contextlib.suppress(Exception):
        data = json.loads(body)
        if isinstance(data, dict):
            return sorted(data.keys())
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return sorted(data[0].keys())
    return []


def _looks_reply_related(url: str, keys: list[str]) -> bool:
    if _REPLY_KEYWORDS.search(url):
        return True
    return any(_REPLY_KEYWORDS.search(k) for k in keys)


def build_digest(responses: list[tuple[str, str, str]],
                 resume: dict | None) -> str:
    """捕捉した応答とレジュメから、PIIなしの構造ダイジェスト文字列を作る（純関数）。

    responses: (method, url, body) のリスト。body はJSON文字列を想定（非JSONは無視）。
    """
    lines: list[str] = []
    lines.append("========== 返信偵察ダイジェスト（値は型/長さに置換・PIIなし）==========")
    lines.append(f"[captured] XHR/fetch 応答 {len(responses)} 件")

    # URL 重複は本文が大きい方を残す。
    best: dict[str, tuple[str, str]] = {}
    for method, url, body in responses:
        if url not in best or len(body) > len(best[url][1]):
            best[url] = (method, body)
    uniq = [(m, u, b) for u, (m, b) in best.items()]
    uniq.sort(key=lambda t: -len(t[2]))

    indexed = [(m, u, b, _top_keys(b)) for m, u, b in uniq]
    reply_like = [t for t in indexed if _looks_reply_related(t[1], t[3])]

    lines.append("")
    lines.append(f"--- 返信・メッセージ関連らしい応答 {len(reply_like)} 件 ---")
    for m, u, _b, keys in reply_like:
        lines.append(f"  {m} {u}")
        if keys:
            lines.append(f"      keys={keys}")

    lines.append("")
    lines.append("--- 全応答インデックス（本文サイズ順）---")
    for i, (m, u, b, keys) in enumerate(indexed):
        lines.append(f"  [{i:02d}] {m} {u} ({len(b)}B) keys={keys}")

    # レジュメの全トップレベルキーと注目キーの様子（返信/接触状態のフィールド探し）。
    lines.append("")
    lines.append("--- 送信済み候補者レジュメの構造（値は伏せ字）---")
    if isinstance(resume, dict):
        lines.append(f"top-level keys: {sorted(resume.keys())}")
        for k in _RESUME_SIGNAL_KEYS:
            if k in resume:
                shape = redact_shape(resume.get(k), k, max_depth=4)
                lines.append(f"  {k}: {json.dumps(shape, ensure_ascii=False)}")
    else:
        lines.append("（レジュメ取得なし）")

    # 返信一覧らしき応答の詳細構造（上位のみ・値は伏せ字）。
    lines.append("")
    lines.append("--- 応答の構造サンプル（返信関連 上位5件・値は伏せ字）---")
    for m, u, b, _keys in (reply_like[:5] or indexed[:5]):
        with contextlib.suppress(Exception):
            shape = redact_shape(json.loads(b), max_depth=5)
            lines.append(f"### {m} {u}")
            lines.append(json.dumps(shape, ensure_ascii=False, indent=1)[:4000])
    lines.append("========== ダイジェスト終わり ==========")
    return "\n".join(lines)


class ReplyProbe:
    def __init__(self, client):
        self.client = client
        self.base = client.sel.base_url.rstrip("/")
        self.out = Path(project_root()) / "data" / "exports"
        self.out.mkdir(parents=True, exist_ok=True)
        self.responses: list[tuple[str, str, str]] = []  # (method, url, body)
        self.requests: list[str] = []

    def _log(self, msg: str) -> None:
        logger.info("[reply-probe] %s", msg)

    def _install_capture(self, page) -> None:
        def on_response(resp):
            with contextlib.suppress(Exception):
                req = resp.request
                rtype = getattr(req, "resource_type", "")
                url = resp.url
                # XHR/fetch を全て録る（メッセージAPIがどのパスでも取りこぼさない）。
                if rtype not in ("xhr", "fetch") and "/api/" not in url \
                        and "/ajax/" not in url:
                    return
                body = resp.text()
                if len(body) > 400_000:
                    body = body[:400_000]
                self.responses.append((req.method, url, body))

        def on_request(req):
            with contextlib.suppress(Exception):
                rtype = getattr(req, "resource_type", "")
                if rtype in ("xhr", "fetch") or "/api/" in req.url or "/ajax/" in req.url:
                    body = req.post_data or ""
                    self.requests.append(f"{req.method} {req.url}\n{body[:500]}")

        page.on("response", on_response)
        page.on("request", on_request)

    def run(self, sent_mrccid: str | None = None) -> None:
        page = self.client.page
        self._install_capture(page)

        # 1. mypage とナビゲーションを巡回してAPIトラフィックを収集。
        page.goto(f"{self.base}/mypage/", wait_until="domcontentloaded")
        with contextlib.suppress(Exception):
            page.wait_for_load_state("networkidle", timeout=15000)
        self.client.human_delay(2.0, 3.0)
        for text in _NAV_TEXTS:
            with contextlib.suppress(Exception):
                loc = page.locator(f"text={text}").first
                if loc.count() > 0:
                    self._log(f"リンク '{text}' をクリックします。")
                    loc.click(timeout=5000)
                    with contextlib.suppress(Exception):
                        page.wait_for_load_state("networkidle", timeout=10000)
                    self.client.human_delay(1.5, 2.5)

        # 2. 収集した応答をサイズ順にダンプ（artifact 用の生データ）。
        self._dump_responses()
        # 3. 送信済み候補者のレジュメを再取得（構造ダイジェストに使う）。
        resume = self._fetch_resume(sent_mrccid)
        self._dump_resume_signals(resume)
        # 4. PIIなしの構造ダイジェストをジョブログ／ステップサマリへ出力。
        self._emit_digest(resume)
        self._log("偵察完了。data/exports の reply_* とジョブログのダイジェストを確認してください。")

    def _dump_responses(self) -> None:
        ranked = sorted(self.responses, key=lambda t: -len(t[2]))[:40]
        index_lines = []
        for i, (method, url, body) in enumerate(ranked):
            fname = f"reply_api_{i:02d}.json"
            (self.out / fname).write_text(body, encoding="utf-8")
            index_lines.append(f"{fname}\t{method}\t{len(body)}B\t{url}")
        (self.out / "reply_api_index.txt").write_text(
            "\n".join(index_lines), encoding="utf-8")
        (self.out / "reply_requests.txt").write_text(
            "\n\n".join(self.requests), encoding="utf-8")
        self._log(f"XHR/fetch 応答 {len(self.responses)} 件（上位{len(ranked)}件を保存）。")

    def _fetch_resume(self, mrccid: str | None) -> dict | None:
        if not mrccid:
            return None
        with contextlib.suppress(Exception):
            from .api import BizreachApi

            resume = BizreachApi(self.client).get_resume(mrccid)
            if isinstance(resume, dict):
                return resume
        self._log("レジュメ取得に失敗（構造ダイジェストのレジュメ部はスキップ）。")
        return None

    def _dump_resume_signals(self, resume: dict | None) -> None:
        if not isinstance(resume, dict):
            return
        with contextlib.suppress(Exception):
            signals = {k: resume.get(k) for k in _RESUME_SIGNAL_KEYS if k in resume}
            (self.out / "reply_resume_signals.json").write_text(
                json.dumps(signals, ensure_ascii=False, indent=2), encoding="utf-8")
            self._log("送信済み候補者の返信シグナルを reply_resume_signals.json に保存。")

    def _emit_digest(self, resume: dict | None) -> None:
        digest = build_digest(self.responses, resume)
        # artifact にも残す。
        with contextlib.suppress(Exception):
            (self.out / "reply_digest.txt").write_text(digest, encoding="utf-8")
        # ジョブログへ（artifact をダウンロードできなくてもログで読める）。
        for chunk in digest.split("\n"):
            logger.info("%s", chunk)
        # GitHub Actions のステップサマリ（実行結果ページで読める）。
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with contextlib.suppress(Exception), \
                    open(summary_path, "a", encoding="utf-8") as fh:
                fh.write("\n```\n" + digest + "\n```\n")
