"""ScoutGenerator のテスト（Anthropic クライアントをモック）。"""

from __future__ import annotations

from types import SimpleNamespace

from bizreach_scout.generation.generator import ScoutGenerator, render_for_human

from .factories import make_candidate

VALID_INPUT = {
    "analysis": "30代前半・営業出身。マネジメント経験を裁量に接続。",
    "tone_key": "early30s",
    "subject_first": "【Premium Offer】法人営業での突出した実績に惹かれ限定オファーをさせていただきます",
    "greeting_offer": "数多くのご経歴を拝見する中で、貴殿のご実績に強く惹かれご連絡しました。",
    "scout_reason": "新規開拓で全社表彰を重ねられた点、そしてチームを率いてこられた点に魅力を感じています。",
    "company_intro": "当社は480医院を支援する、医院・病院経営に特化したコンサルティングファームです。",
    "career_title": "入社後のキャリア",
    "career_body": "2年目で年収990万円、7年目で2,000万円というモデルもございます。",
    "position_title": "このポジションの魅力",
    "position_body": "経営全般に深く関与し、クライアントの成果に長期で伴走できます。",
    "subject_resend": "【Premium Offer】改めて貴殿のマネジメント経験に惹かれご連絡しました",
    "resend_body": "先日ご連絡いたしました件、改めてご案内です。貴殿の組織づくりの経験は当社で必ず活きます。",
}


def _fake_client(input_payload: dict):
    block = SimpleNamespace(type="tool_use", name="emit_scout", input=input_payload, id="t1")
    resp = SimpleNamespace(content=[block])
    messages = SimpleNamespace(create=lambda **kwargs: resp)
    return SimpleNamespace(messages=messages)


def test_generate_produces_two_messages():
    gen = ScoutGenerator(client=_fake_client(VALID_INPUT), model="test-model")
    scout = gen.generate(make_candidate())

    assert scout.first.subject.startswith("【Premium Offer】")
    assert scout.resend.subject.startswith("【Premium Offer】")
    # 初回本文に固定要素が含まれる
    assert "BU3765516様" in scout.first.body
    assert "このスカウトのポイント" in scout.first.body
    # 再送本文にも固定フッターが含まれる
    assert "弊社について" in scout.resend.body
    assert scout.model == "test-model"
    assert scout.tone_key == "early30s"


def test_subject_normalized_when_prefix_missing():
    payload = dict(VALID_INPUT, subject_first="ご経歴に惹かれて限定オファー")
    gen = ScoutGenerator(client=_fake_client(payload), model="test-model")
    scout = gen.generate(make_candidate())
    assert scout.first.subject.startswith("【Premium Offer】")


def test_normalize_subject_strips_wrong_bracket_block():
    from bizreach_scout.generation.generator import _normalize_subject

    rules = {"constraints": {"subject_prefix": "【Premium Offer】"}}
    # 誤った【】が先頭にあっても閉じ括弧が宙に浮かない
    out = _normalize_subject("【急募】優秀なエンジニア", rules)
    assert out == "【Premium Offer】優秀なエンジニア"
    # 既に正しい接頭辞ならそのまま
    assert _normalize_subject("【Premium Offer】X", rules) == "【Premium Offer】X"


def test_render_for_human_has_code_blocks():
    gen = ScoutGenerator(client=_fake_client(VALID_INPUT), model="test-model")
    scout = gen.generate(make_candidate())
    rendered = render_for_human(scout)
    assert "【初回送信用】" in rendered
    assert "【再送用】" in rendered
    assert rendered.count("```") >= 8  # 件名×2 + 本文×2 = 4ブロック → 8フェンス
