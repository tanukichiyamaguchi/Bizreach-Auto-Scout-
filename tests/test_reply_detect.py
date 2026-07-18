"""返信検知述語（reply_detect.py）のテスト。

2026-07-18 の偵察（probe-replies）で確定した実データ形式に基づく:
- 未返信の送信済み候補者: candidateName=null / hasContact=true /
  contactHistory=["groupScouted", ...]（イベントコード文字列のリスト）。
"""

from __future__ import annotations

import json
from pathlib import Path

from bizreach_scout.bizreach.reply_detect import detect_reply

FIX = Path(__file__).parent / "fixtures" / "resume_sample.json"


def _resume(**over) -> dict:
    resume = json.loads(FIX.read_text(encoding="utf-8"))
    resume.update(over)
    return resume


def test_pre_reply_resume_not_detected():
    # 実レジュメ形式のfixture（candidateName=null・contactHistoryなし）→ 返信なし。
    signal = detect_reply(_resume())
    assert signal.replied is False
    assert signal.candidate_name == ""


def test_sent_unreplied_real_shape_not_detected():
    # 偵察で確認した「送信済み・未返信」の実形状。hasContact=true でも返信なし。
    resume = _resume(candidateName=None, hasContact=True,
                     contactHistory=["groupScouted", "groupScouted"],
                     lastLoginDate="2026-07-17")
    signal = detect_reply(resume)
    assert signal.replied is False


def test_disclosed_name_detected_as_reply():
    signal = detect_reply(_resume(candidateName="山田 太郎"))
    assert signal.replied is True
    assert signal.candidate_name == "山田 太郎"
    assert "開示" in signal.evidence


def test_string_event_code_reply_detected():
    # イベントコードが reply/apply 系なら返信あり（文字列形式・日時なし）。
    for code in ("candidateReplied", "replied", "applied", "entryReceived"):
        signal = detect_reply(_resume(contactHistory=["groupScouted", code]))
        assert signal.replied is True, code
        assert "候補者側イベント" in signal.evidence


def test_our_side_codes_not_detected():
    # 自社側イベント（scout/sent/remind 系）は reply 系の語を含んでも返信にしない。
    for code in ("groupScouted", "scoutSent", "reminderSent", "scoutReplyRequested"):
        assert detect_reply(_resume(contactHistory=[code])).replied is False, code


def test_dict_entry_with_datetime_detected():
    # dict 形式のエントリにも備える（date があれば返信日時として拾う）。
    resume = _resume(contactHistory=[
        {"type": "groupScouted", "date": "2026-07-10T10:00:00"},
        {"type": "REPLY_RECEIVED", "date": "2026-07-12T09:30:00"},
    ])
    signal = detect_reply(resume)
    assert signal.replied is True
    assert signal.replied_at == "2026-07-12T09:30:00"


def test_has_contact_alone_not_detected():
    # hasContact は「自社が接触済み」の意味（偵察で未返信でも true を確認）。
    assert detect_reply(_resume(hasContact=True)).replied is False


def test_dict_style_candidate_name():
    signal = detect_reply(_resume(candidateName={"ja": "佐藤 花子", "en": None}))
    assert signal.replied is True
    assert signal.candidate_name == "佐藤 花子"
