"""返信偵察の構造ダイジェスト（reply_probe の純関数）のテスト。

ブラウザ不要。redact_shape が PII を伏せること、build_digest が返信関連の応答と
レジュメの返信シグナルを構造として（値を伏せて）並べることを検証する。
"""

from __future__ import annotations

import json

from bizreach_scout.bizreach.reply_probe import build_digest, redact_shape


def test_redact_shape_hides_names_and_emails_keeps_enums_and_flags():
    value = {
        "candidateName": "山田 太郎",          # 名前 → 伏せる
        "email": "taro@example.com",           # メール → 伏せる
        "status": "REPLIED",                    # 列挙値 → 残す
        "hasContact": True,                     # フラグ → 残す
        "repliedAt": "2026-07-14T10:00:00",     # ISO日付 → 残す
        "age": 31,                              # 数値 → 型のみ
        "note": "電話で面談の約束をしました",     # 日本語長文 → 伏せる
    }
    shape = redact_shape(value)
    assert shape["candidateName"] == "str:5"
    assert shape["email"] == "str:16"
    assert shape["status"] == "REPLIED"
    assert shape["hasContact"] is True
    assert shape["repliedAt"] == "2026-07-14T10:00:00"
    assert shape["age"] == "int"
    assert shape["note"].startswith("str:")


def test_redact_shape_lists_show_length_and_first_element_shape():
    value = {"threads": [
        {"from": "candidate", "unread": True, "candidateName": "佐藤 花子"},
        {"from": "recruiter", "unread": False, "candidateName": "鈴木 一郎"},
    ]}
    shape = redact_shape(value)
    assert shape["threads"][0] == "len=2"
    first = shape["threads"][1]
    assert first["from"] == "candidate"     # 列挙値は残る
    assert first["unread"] is True
    assert first["candidateName"] == "str:5"  # 名前は伏せる（"佐藤 花子"=5文字）


def test_build_digest_flags_reply_related_and_indexes_all():
    responses = [
        ("GET", "https://cr-support.jp/api/v2/messages/threads",
         json.dumps({"threads": [{"lastMessageFrom": "candidate", "unread": True}]})),
        ("GET", "https://cr-support.jp/api/v2/notice/count",
         json.dumps({"count": 3})),
        ("POST", "https://cr-support.jp/api/v2/candidates:search",
         json.dumps({"items": [{"mrccid": "abc"}], "totalCount": 10})),
    ]
    resume = {"candidateName": None, "hasContact": False,
              "contactHistory": [], "age": 33}
    digest = build_digest(responses, resume)
    # 返信関連（threads/messages/candidate）が抽出される。
    assert "messages/threads" in digest
    assert "candidates:search" in digest
    # 全応答インデックスに3件とも現れる。
    assert digest.count("[0") >= 1 and "notice/count" in digest
    # レジュメの注目キーが伏せ字で並ぶ。
    assert "top-level keys" in digest
    assert "hasContact" in digest
    # 生の名前・値が漏れていない（None は許容）。
    assert "山田" not in digest


def test_build_digest_without_resume_is_safe():
    digest = build_digest([], None)
    assert "XHR/fetch 応答 0 件" in digest
    assert "レジュメ取得なし" in digest
