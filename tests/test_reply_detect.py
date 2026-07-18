"""返信検知述語（reply_detect.py）のテスト。"""

from __future__ import annotations

import json
from pathlib import Path

from bizreach_scout.bizreach.reply_detect import detect_reply

FIX = Path(__file__).parent / "fixtures" / "resume_sample.json"


def test_pre_reply_resume_not_detected():
    # 実レジュメ形式のfixture（candidateName=null・contactHistoryなし）→ 返信なし。
    resume = json.loads(FIX.read_text(encoding="utf-8"))
    signal = detect_reply(resume)
    assert signal.replied is False
    assert signal.candidate_name == ""


def test_disclosed_name_detected_as_reply():
    resume = json.loads(FIX.read_text(encoding="utf-8"))
    resume["candidateName"] = "山田 太郎"
    signal = detect_reply(resume)
    assert signal.replied is True
    assert signal.candidate_name == "山田 太郎"
    assert "開示" in signal.evidence


def test_contact_history_candidate_entry_detected_with_datetime():
    resume = json.loads(FIX.read_text(encoding="utf-8"))
    resume["contactHistory"] = [
        {"type": "SCOUT_SENT", "date": "2026-07-10T10:00:00"},
        {"type": "REPLY_RECEIVED", "date": "2026-07-12T09:30:00"},
    ]
    signal = detect_reply(resume)
    assert signal.replied is True
    assert signal.replied_at == "2026-07-12T09:30:00"
    assert "contactHistory" in signal.evidence


def test_our_side_contact_history_only_not_detected():
    # 自社送信のみの履歴では返信ありにしない（保守的）。
    resume = json.loads(FIX.read_text(encoding="utf-8"))
    resume["contactHistory"] = [{"type": "SCOUT_SENT", "date": "2026-07-10T10:00:00"}]
    signal = detect_reply(resume)
    assert signal.replied is False


def test_has_contact_alone_not_detected():
    # hasContact 単独では意味が確定していないため返信ありにしない。
    resume = json.loads(FIX.read_text(encoding="utf-8"))
    resume["hasContact"] = True
    assert detect_reply(resume).replied is False


def test_dict_style_candidate_name():
    resume = json.loads(FIX.read_text(encoding="utf-8"))
    resume["candidateName"] = {"ja": "佐藤 花子", "en": None}
    signal = detect_reply(resume)
    assert signal.replied is True
    assert signal.candidate_name == "佐藤 花子"
