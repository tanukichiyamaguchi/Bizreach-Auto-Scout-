"""勤務条件の希望による除外判定（転勤NG・出張不可・残業なし希望・在宅勤務希望）。

運用方針（2026-10 運用者指示）: 次のいずれかを表明している候補者にはスカウトを送らない。

1. 転勤NG       … 転勤・転居を伴う異動を避けたい
2. 出張不可     … 出張を避けたい（自社求人は「全国クライアント医院へ月15〜20件の出張あり」）
3. 残業なし希望 … 残業のない／ごく少ない職場を希望
4. 在宅勤務希望 … 在宅・リモートでの勤務を希望

**「可（許容）」と「希望・不可（意思表示）」を必ず区別する**（運用者の明示指示）。
「リモート可」「転勤可能」「出張は厭いません」は“それでも構わない”であって希望ではないため
除外しない。4 の在宅だけは極性が逆で、「望んでいる」ことが除外条件になる。

判定の対象は**本人が希望として書いた欄のみ**（``work_style`` / ``desired_other`` /
``desired_locations``）。職務要約・自己PR・職務経歴は走査しない。これらには
「残業を月40h→10hへ削減」「在宅勤務制度を全社導入」「全国転勤を経験」のように、
同じ語を**経歴・実績**として書く例が極めて多く、人事・労務経験者やコンサル経験者を
体系的に誤除外してしまうため（誤検知例は tests/test_work_conditions.py に固定）。
"""

from __future__ import annotations

import re

# --- 話題語（表記ゆれを含む）-------------------------------------------------
_TOPIC_PATTERNS: dict[str, str] = {
    "転勤": r"norelocation|no relocation|転勤|転居を伴う(?:異動|配置転換)?|勤務地限定|エリア限定",
    "出張": r"nobusinesstrip|no business trip|出張|外泊",
    "残業": r"noovertime|no overtime|ノー残業|ノ-残業|残業|時間外労働|定時退社|休日出勤|定時で",
    "在宅": r"在宅|リモート|テレワーク|remote|wfh|work from home",
}

# 話題語から後ろへ何文字まで見るか（同じ文の中の意思表示だけを拾う）。
_WINDOW = 40
# 文の切れ目。ここを越えたら別の話題として扱う。
_SENTENCE_END = re.compile(r"[。．\n\r]")

# --- 「避けたい」の表明（転勤・出張・残業に使う）-------------------------------
# 部分一致の事故を避けるため正規表現で書く。「こなし」「みなし」の中の『なし』、
# 「問題ない」の『ない』、「不可能」の中の『可能』などを拾わないよう境界を付ける。
_REFUSE_RE = re.compile(
    r"(?<![こみ])なし|無し|ナシ|のない|のなく|が?ないこと"
    r"|不可|不可能|ng|だめ|ダメ"
    r"|厳しい|難しい|困難"
    r"|でき(?:ません|ない)|出来(?:ません|ない)|致しかねます|いたしかねます"
    r"|avoid|避けたい|避けられ|控えたい|控えて|控えております"
    r"|したくない|しません|せずに|お受けできません|受けられません"
    r"|留めたい|少ない|少なめ|限定"
    r"|以内|以下|程度まで"
)
# --- 「許容する」の表明（話題語により近ければ除外しない）-----------------------
_ACCEPT_RE = re.compile(
    r"(?<!不)可能|(?<!不)可(?![否め])|あり|有り|ok"
    r"|歓迎|問題(?:ありません|ございません|なし)"
    r"|厭(?:いません|わない)|いとわない|構(?:いません|わない)"
    r"|こだわ(?:りません|りはありません)|柔軟|対応(?:できます|いたします|可)"
    r"|どちらでも|どこへでも|挑戦|関心|意欲|前向き|楽しめる"
)
# --- 経歴・実績としての言及（本人の希望ではないので除外しない）-------------------
# 希望欄しか走査しない設計だが、自由記述に経歴を書く人がいるため保険として効かせる。
_CAREER_RE = re.compile(
    r"経験|実績|削減|改善|導入|整備|規程|規定|制度|推進|従事|担当|構築|設計"
    r"|運用|企画|マネジメント|育成|研修|策定|見直し|プロジェクト|実現|開拓"
    r"|こなし|培|主導|立ち上げ|支援|コンサル|商談|受注|率"
)
# 「残業は月10時間以内」「出張は月1回まで」のような上限指定。
_CAP_RE = re.compile(
    r"^(?:は|が|も|を|の)?(?:月|週|年|1日)?\s*\d+\s*(?:時間|h|回|日|割)"
    r".{0,8}?(?:以内|以下|まで)"
)
# それ自体が「避けたい」を意味する語。興味のある働き方の選択肢（「転勤なし」等）が
# 英語コードで返る場合（NoRelocation / NoOvertime …）もここで拾う。
# 経歴としての言及（「ノー残業デーの導入を推進」等）は _CAREER_RE 側で落ちる。
_SELF_REFUSING = (
    "定時退社", "ノー残業", "ノ-残業", "定時で",
    "norelocation", "no relocation", "nobusinesstrip", "no business trip",
    "noovertime", "no overtime",
)


def _normalize(text: str | None) -> str:
    """判定用にテキストを正規化する（小文字化・全角英数の半角化・空白の除去）。"""
    s = (text or "").strip().lower()
    # 全角英数字→半角（NG/OK/100% などの表記ゆれを吸収する）。
    s = s.translate(str.maketrans(
        "０１２３４５６７８９ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ％",
        "0123456789abcdefghijklmnopqrstuvwxyz%",
    ))
    return re.sub(r"[ \t\u3000]+", "", s)


def _window_after(text: str, start: int) -> str:
    """話題語の直後から、文の切れ目または ``_WINDOW`` 文字までを返す。"""
    tail = text[start:start + _WINDOW]
    m = _SENTENCE_END.search(tail)
    return tail[:m.start()] if m else tail


def _pos(pattern: re.Pattern[str], text: str) -> int:
    """最初に一致した位置（無ければ非常に大きい値）。どちらが話題語に近いかの比較に使う。"""
    m = pattern.search(text)
    return m.start() if m else 10**6


def _refuses(topic: str, text: str) -> bool:
    """転勤・出張・残業について「避けたい」と表明しているか。"""
    for m in re.finditer(_TOPIC_PATTERNS[topic], text):
        tail = _window_after(text, m.end())
        if _CAREER_RE.search(tail):
            continue  # 経歴・実績としての言及
        if _CAP_RE.match(tail):
            return True
        refuse, accept = _pos(_REFUSE_RE, tail), _pos(_ACCEPT_RE, tail)
        # 「転勤可能です」は許容、「転勤は不可です」は拒否。話題語に近い方を採る。
        if refuse < accept:
            return True
        # 「定時退社」「ノー残業」「NoRelocation」はそれ自体が拒否の表明。
        if m.group(0) in _SELF_REFUSING and _pos(_ACCEPT_RE, tail) > 4:
            return True
    return False


# --- 在宅（極性が逆: 望んでいることが除外条件）--------------------------------
# 「可（選べればよい）」では除外しない。強い希望・必須・限定の表明だけを拾う
# （運用者の明示指示: 「リモート可を除外するわけではない。明確に在宅勤務希望の場合に除外する」）。
_REMOTE_STRONG_RE = re.compile(
    r"必須|前提|限定|のみ|だけ|フル|完全|原則|中心|100%|でないと|しか|専従"
)
_REMOTE_WISH_RE = re.compile(r"希望|探して|したいです|がいい|を条件")
# 在宅を望んでいないことを示す語（あれば除外しない）。
_REMOTE_NEGATIVE_RE = re.compile(
    r"こだわ(?:りません|りはありません)|どちらでも|出社(?:中心|して|型|を希望)"
    r"|可の(?:求人|企業|会社|ポジション)"
)
# 「リモート不可の求人は対象外」＝在宅を必須としている、という裏返しの言い回し。
_REMOTE_INVERTED_RE = re.compile(
    r"(?:在宅|リモート|テレワーク)(?:ワーク|勤務)?(?:が)?(?:ng|不可|なし|無し)"
    r".{0,20}?(?:対象外|応募しません|見送|除外|検討しません)"
)


def _wants_remote(text: str) -> bool:
    """在宅・リモート勤務を**希望**していると読めるか（「可」では除外しない）。"""
    if _REMOTE_INVERTED_RE.search(text):
        return True
    for m in re.finditer(_TOPIC_PATTERNS["在宅"], text):
        tail = _window_after(text, m.end())
        head = text[max(0, m.start() - 8):m.start()]
        if _CAREER_RE.search(tail) or _CAREER_RE.search(head):
            continue  # 「フルリモートのチームをマネジメント」等の経歴
        if _REMOTE_NEGATIVE_RE.search(tail) or _REMOTE_NEGATIVE_RE.search(head):
            continue
        if _REMOTE_STRONG_RE.search(head) or _REMOTE_STRONG_RE.search(tail):
            return True
        # 「テレワーク可の企業を希望します」は“可”であって在宅希望ではない（除外しない）。
        if re.match(r"^(?:は|が|も|に|を|の)?(?<!不)可", tail):
            continue
        if _REMOTE_WISH_RE.search(tail):
            return True
    return False


# 判定結果に対応する「対象外の理由」。eligibility.py が failed に積む文面。
_REASONS = {
    "転勤": "転勤を避けたい希望のため対象外（転勤NG）",
    "出張": "出張を避けたい希望のため対象外（出張不可・自社求人は出張あり）",
    "残業": "残業のない職場を希望のため対象外（残業なし希望）",
    "在宅": "在宅・リモート勤務を希望のため対象外（在宅勤務希望）",
}


def unwanted_work_conditions(text: str | None) -> list[str]:
    """希望欄のテキストから、該当する勤務条件の理由を並べて返す（無ければ空）。"""
    s = _normalize(text)
    if not s:
        return []
    found = [t for t in ("転勤", "出張", "残業") if _refuses(t, s)]
    if _wants_remote(s):
        found.append("在宅")
    return [_REASONS[t] for t in found]
