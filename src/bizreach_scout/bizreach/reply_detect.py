"""レジュメJSONから「候補者が返信したか」を推定する述語（純関数）。

ビズリーチに返信の公開APIは無いため、レジュメの再取得で観測できるシグナルから
保守的に判定する（誤検知で「返信あり」を誤記録しない側に倒す）:

- candidateName: 通常は匿名（null）。返信・コンタクト成立後に開示される。
- contactHistory: 接触履歴。候補者側のエントリ（返信・応募等）が含まれ得る。
- hasContact: 意味が「自社が接触した」の可能性があるため単独では返信と断定しない。

v0 の判定: candidateName が開示されている場合は返信ありとみなす。
contactHistory に候補者側と読めるエントリがあれば返信あり（日時も取得）。
実データでの偵察（probe-replies）結果に応じてこの述語を更新する。
"""

from __future__ import annotations

from dataclasses import dataclass

# contactHistory のエントリで「候補者側のアクション」を示すと思われるキーワード
# （偵察で実データを確認したら精緻化する）。
_CANDIDATE_ACTION_KEYWORDS = (
    "reply", "replied", "message", "apply", "applied", "response",
    "返信", "応募", "メッセージ",
)


@dataclass
class ReplySignal:
    replied: bool
    replied_at: str | None
    candidate_name: str
    evidence: str  # 判定根拠（replies.note に記録される）


def _entry_text(entry: object) -> str:
    """contactHistory エントリを検索用の小文字テキストへ。"""
    if isinstance(entry, dict):
        return " ".join(f"{k}:{v}" for k, v in entry.items()).lower()
    return str(entry).lower()


def _entry_datetime(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    for key in ("date", "datetime", "createdAt", "created_at", "at", "timestamp"):
        v = entry.get(key)
        if isinstance(v, str) and v:
            return v
    return None


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
    candidate_entries = [e for e in entries
                         if any(k in _entry_text(e) for k in _CANDIDATE_ACTION_KEYWORDS)]

    if candidate_entries:
        replied_at = _entry_datetime(candidate_entries[-1])
        return ReplySignal(True, replied_at, name,
                           "contactHistoryに候補者側エントリ")
    if name:
        # 氏名開示は接触成立の強いシグナル（通常は返信まで匿名のため）。
        return ReplySignal(True, None, name, "候補者名が開示")
    return ReplySignal(False, None, "", "")
