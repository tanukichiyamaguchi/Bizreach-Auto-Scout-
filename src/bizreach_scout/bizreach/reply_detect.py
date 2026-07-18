"""レジュメJSONから「候補者が返信したか」を推定する述語（純関数）。

ビズリーチに返信の公開APIは無いため、レジュメの再取得で観測できるシグナルから
保守的に判定する（誤検知で「返信あり」を誤記録しない側に倒す）。

2026-07-18 の偵察（probe-replies・送信済み未返信の実候補者）で確定した事実:
- candidateName: 未返信では null。返信・コンタクト成立後に氏名が開示される。
  → 開示 = 返信あり（主シグナル）。
- hasContact: 未返信でも true だった。意味は「自社が接触（スカウト送信）済み」。
  → 返信シグナルとしては使用禁止（使うと全送信者が返信ありになる）。
- contactHistory: イベントコード文字列のリスト（例: ["groupScouted", ...]）。
  "groupScouted" は自社側のスカウト送信イベント。
  → 候補者側と断定できるコード（reply/apply 系）だけを返信とみなす。
- 返信前でも email / tel / birthDate 等のキー自体は存在する（値は伏せられる想定）。
  メッセージ受信箱（/message/?folderCd=inbox）はHTML画面でJSON APIが無いため、
  レジュメの氏名開示が候補者単位の権威的シグナルとなる。
"""

from __future__ import annotations

from dataclasses import dataclass

# contactHistory のイベントコードで「候補者側のアクション」と断定できるキーワード。
# 自社側イベント（groupScouted 等の scout/send/message 系）と衝突しない語だけを使う。
_CANDIDATE_ACTION_KEYWORDS = (
    "repl",        # reply / replied / replyReceived
    "appl",        # apply / applied / application
    "entry",       # entried / entry
    "返信", "応募",
)
# 自社側のアクションと確認済みのコード（含まれていても返信とみなさない）。
_OUR_ACTION_KEYWORDS = ("scout", "sent", "send", "remind")


@dataclass
class ReplySignal:
    replied: bool
    replied_at: str | None
    candidate_name: str
    evidence: str  # 判定根拠（replies.note に記録される）


def _entry_text(entry: object) -> str:
    """contactHistory エントリ（文字列 or dict）を検索用の小文字テキストへ。"""
    if isinstance(entry, dict):
        return " ".join(f"{k}:{v}" for k, v in entry.items()).lower()
    return str(entry).lower()


def _entry_datetime(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None  # 実データは文字列コードのため通常は日時なし
    for key in ("date", "datetime", "createdAt", "created_at", "at", "timestamp"):
        v = entry.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _is_candidate_action(entry: object) -> bool:
    """エントリが候補者側のアクションと断定できる場合のみ True（保守的）。

    自社側キーワード（scout/sent等）を含むコードは、candidate 系の語を含んでいても
    除外する（例: "scoutReplyRequested" のような自社イベントを誤検知しない）。
    """
    text = _entry_text(entry)
    if any(k in text for k in _OUR_ACTION_KEYWORDS):
        return False
    return any(k in text for k in _CANDIDATE_ACTION_KEYWORDS)


def detect_reply(resume: dict) -> ReplySignal:
    """レジュメJSONから返信シグナルを推定する（保守的）。"""
    name = ""
    raw_name = resume.get("candidateName")
    if isinstance(raw_name, str) and raw_name.strip():
        name = raw_name.strip()
    elif isinstance(raw_name, dict):
        # {"ja": ..., "en": ...} 形式の可能性にも備える。
        name = str(raw_name.get("ja") or raw_name.get("en") or "").strip()

    history = resume.get("contactHistory")
    entries = history if isinstance(history, list) else []
    candidate_entries = [e for e in entries if _is_candidate_action(e)]

    if candidate_entries:
        replied_at = _entry_datetime(candidate_entries[-1])
        return ReplySignal(True, replied_at, name,
                           f"contactHistoryに候補者側イベント({_entry_text(candidate_entries[-1])[:40]})")
    if name:
        # 氏名開示 = 返信・コンタクト成立（未返信では null と実データで確認済み）。
        return ReplySignal(True, None, name, "候補者名が開示")
    return ReplySignal(False, None, "", "")
