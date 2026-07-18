"""返信データの偵察（実送信なし・読み取りのみ）。

ビズリーチの「メッセージ／スカウト管理／候補者管理」系の画面を開き、その際に流れる
/api/ 応答を全て記録して data/exports/ にダンプする。返信一覧・スレッド系エンドポイントの
特定が目的（PickupProbe と同じ「画面を開いてAPIトラフィックを丸ごと録る」パターン）。

あわせて、送信済み候補者1名のレジュメを再取得し、返信関連キー
（candidateName / contactHistory / hasContact / lastLoginDate）のみを抜き出して保存する
（reply_detect の述語を実データで確定させるため）。
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

from ..config import project_root
from ..logging_config import logger

# クリックして回る画面リンクのテキスト候補（存在しないものはスキップ）。
_NAV_TEXTS = ("メッセージ", "スカウト管理", "送信済み", "返信", "候補者管理", "やりとり")
# JSバンドルから拾う返信関連キーワード。
_REPLY_KEYWORDS = re.compile(
    r"reply|thread|inbox|unread|contactHistory|hasContact|messageList|conversation", re.I)
_RESUME_SIGNAL_KEYS = ("candidateName", "contactHistory", "hasContact", "lastLoginDate")


class ReplyProbe:
    def __init__(self, client):
        self.client = client
        self.base = client.sel.base_url.rstrip("/")
        self.out = Path(project_root()) / "data" / "exports"
        self.out.mkdir(parents=True, exist_ok=True)
        self.responses: list[tuple[str, str]] = []  # (url, body)
        self.requests: list[str] = []

    def _log(self, msg: str) -> None:
        logger.info("[reply-probe] %s", msg)

    def _install_capture(self, page) -> None:
        def on_response(resp):
            with contextlib.suppress(Exception):
                url = resp.url
                if "/api/" in url or "/ajax/" in url:
                    self.responses.append((url, resp.text()))

        def on_request(req):
            with contextlib.suppress(Exception):
                if "/api/" in req.url or "/ajax/" in req.url:
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

        # 2. 収集した応答をサイズ順にダンプ（大きいものほど一覧APIの可能性が高い）。
        self._dump_responses()
        # 3. JSバンドルから返信関連エンドポイント文字列を抽出。
        self._dump_js_hints(page)
        # 4. 送信済み候補者のレジュメから返信シグナルキーのみ抜粋。
        if sent_mrccid:
            self._dump_resume_signals(sent_mrccid)
        self._log("偵察完了。data/exports の reply_* を確認してください。")

    def _dump_responses(self) -> None:
        ranked = sorted(self.responses, key=lambda t: -len(t[1]))[:30]
        index_lines = []
        for i, (url, body) in enumerate(ranked):
            fname = f"reply_api_{i:02d}.json"
            (self.out / fname).write_text(body, encoding="utf-8")
            index_lines.append(f"{fname}\t{len(body)}B\t{url}")
        (self.out / "reply_api_index.txt").write_text(
            "\n".join(index_lines), encoding="utf-8")
        (self.out / "reply_requests.txt").write_text(
            "\n\n".join(self.requests), encoding="utf-8")
        self._log(f"API応答 {len(ranked)} 件を reply_api_*.json に保存。")

    def _dump_js_hints(self, page) -> None:
        try:
            content = ""
            with contextlib.suppress(Exception):
                content = page.content()
            chunk_urls = {self.base + p for p in
                          re.findall(r'/_next/static/chunks/[^"\']+?\.js', content)}
            endpoints: set[str] = set()
            hints: list[str] = []
            for url in sorted(chunk_urls):
                resp = self.client.page.request.get(url)
                if resp.status != 200:
                    continue
                js = resp.text()
                for m in re.findall(r'[`"\']/(?:api/)?v2/[^`"\'\s]+', js):
                    endpoints.add(m.strip('`"\''))
                for m in re.finditer(r".{0,60}(" + _REPLY_KEYWORDS.pattern + r").{0,60}",
                                     js, re.I):
                    seg = m.group(0)
                    if "/api/" in seg or "candidates" in seg or "message" in seg.lower():
                        hints.append(seg)
            reply_like = sorted(e for e in endpoints if _REPLY_KEYWORDS.search(e))
            (self.out / "reply_js_endpoints.txt").write_text(
                "== 返信関連らしいエンドポイント ==\n" + "\n".join(reply_like)
                + "\n\n== 全 /v2 エンドポイント ==\n" + "\n".join(sorted(endpoints))
                + "\n\n== キーワード文脈 ==\n" + "\n".join(sorted(set(hints))[:200]),
                encoding="utf-8",
            )
            self._log(f"JSから /v2 {len(endpoints)} 種（返信関連 {len(reply_like)} 種）を抽出。")
        except Exception as e:
            self._log(f"JS解析に失敗: {e}")

    def _dump_resume_signals(self, mrccid: str) -> None:
        try:
            from .api import BizreachApi

            resume = BizreachApi(self.client).get_resume(mrccid)
            if not isinstance(resume, dict):
                self._log("レジュメ取得に失敗（シグナル抜粋をスキップ）。")
                return
            signals = {k: resume.get(k) for k in _RESUME_SIGNAL_KEYS}
            (self.out / "reply_resume_signals.json").write_text(
                json.dumps(signals, ensure_ascii=False, indent=2), encoding="utf-8")
            self._log("送信済み候補者の返信シグナルを reply_resume_signals.json に保存。")
        except Exception as e:
            self._log(f"レジュメシグナル抜粋に失敗: {e}")
