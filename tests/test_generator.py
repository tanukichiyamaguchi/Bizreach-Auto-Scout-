"""ScoutGenerator のテスト（Anthropic クライアントをモック）。"""

from __future__ import annotations

from types import SimpleNamespace

from bizreach_scout.generation.generator import ScoutGenerator, render_for_human
from bizreach_scout.models import ConsultantMatch, ConsultantProfile

from .factories import make_candidate

VALID_INPUT = {
    "analysis": "30代前半・営業出身。マネジメント経験を裁量に接続。",
    "tone_key": "early30s",
    "subject_first": "【Premium Offer】法人営業での突出した実績に惹かれ限定オファーをさせていただきます",
    "greeting_offer": "数多くのご経歴を拝見する中で、貴殿のご実績に強く惹かれご連絡しました。",
    "scout_reason": "新規開拓で全社表彰を重ねられた点、そしてチームを率いてこられた点に魅力を感じています。",
    "consultant_intro_lead": "",
    "consultant_intros": [],
    "company_intro": "当社は480医院を支援する、医院・病院経営に特化したコンサルティングファームです。",
    "career_title": "入社後のキャリア",
    "career_body": "2年目で年収990万円、7年目で2,000万円というモデルもございます。",
    "position_title": "このポジションの魅力",
    "position_body": "経営全般に深く関与し、クライアントの成果に長期で伴走できます。",
    "subject_resend": "【どうしても諦めきれず２度目のご連絡です。】貴殿の組織づくりの経験をどうしても当社で活かしたく",
    "resend_body": "先日ご連絡いたしました件、どうしても諦めきれず改めてご連絡しました。貴殿の組織づくりの経験は当社で必ず活きると確信しております。",
    "resend_consultant_intro_lead": "",
    "resend_consultant_intros": [],
}

# consultants_intro機能のテスト用フィクスチャ（実データconsultants.jsonに依存しない）。
_INOUE = ConsultantProfile(
    id="inoue", display_name="井ノ上 貴之",
    profile_url="https://www.consuldent.jp/member/inoue",
)
_SOMETANI = ConsultantProfile(
    id="sometani", display_name="染谷 東希",
    profile_url="https://www.consuldent.jp/member/sometani",
)


def _fake_client(input_payload: dict):
    block = SimpleNamespace(type="tool_use", name="emit_scout", input=input_payload, id="t1")
    resp = SimpleNamespace(content=[block])
    messages = SimpleNamespace(create=lambda **kwargs: resp)
    return SimpleNamespace(messages=messages)


def test_missing_required_field_raises_generation_error():
    """P4: emit_scout の必須フィールド欠落は GenerationError（KeyError ではなく）。"""
    import pytest

    from bizreach_scout.generation.generator import GenerationError

    broken = dict(VALID_INPUT)
    del broken["greeting_offer"]
    gen = ScoutGenerator(client=_fake_client(broken), model="test-model")
    with pytest.raises(GenerationError, match="greeting_offer"):
        gen.generate(make_candidate(), matches=[])


def test_generate_produces_two_messages():
    gen = ScoutGenerator(client=_fake_client(VALID_INPUT), model="test-model")
    scout = gen.generate(make_candidate(), matches=[])

    assert scout.first.subject.startswith("【Premium Offer】")
    assert scout.resend.subject.startswith("【どうしても諦めきれず２度目のご連絡です。】")
    # 初回本文に固定要素が含まれる
    assert "BU3765516様" in scout.first.body
    assert "このスカウトのポイント" in scout.first.body
    # 再送本文は署名まで（フッターなし）、フルネーム署名
    assert "岩渕龍正" in scout.resend.body
    assert "弊社について" not in scout.resend.body
    assert scout.model == "test-model"
    assert scout.tone_key == "early30s"


def _capturing_client(captured: dict):
    block = SimpleNamespace(type="tool_use", name="emit_scout", input=VALID_INPUT, id="t1")
    resp = SimpleNamespace(content=[block])

    def _create(**kwargs):
        captured.update(kwargs)
        return resp

    return SimpleNamespace(messages=SimpleNamespace(create=_create))


def test_extended_thinking_enabled_passes_adaptive_and_auto_tool_choice():
    captured: dict = {}
    gen = ScoutGenerator(client=_capturing_client(captured), model="test-model")
    orig = gen._settings.thinking_budget_tokens
    gen._settings.thinking_budget_tokens = 8000
    try:
        gen.generate(make_candidate(), matches=[])
    finally:
        gen._settings.thinking_budget_tokens = orig
    # Opus 4.8 では adaptive thinking を使う（budget_tokens形式は400になる）。
    assert captured["thinking"] == {"type": "adaptive"}
    assert captured["output_config"]["effort"]  # 深さは effort で制御
    assert captured["tool_choice"] == {"type": "auto"}  # 拡張思考時は強制tool_choice不可


def test_thinking_falls_back_to_forced_tool_when_no_tool_output():
    # adaptive(auto)でツール未出力なら、思考なし＋強制toolで確実に取得する。
    calls: list = []
    tool_block = SimpleNamespace(type="tool_use", name="emit_scout", input=VALID_INPUT, id="t1")
    tool_resp = SimpleNamespace(content=[tool_block])
    text_resp = SimpleNamespace(content=[SimpleNamespace(type="text", text="…")])

    def _create(**kwargs):
        tc = kwargs.get("tool_choice")
        calls.append(tc)
        forced = tc == {"type": "tool", "name": "emit_scout"}
        return tool_resp if forced else text_resp

    gen = ScoutGenerator(
        client=SimpleNamespace(messages=SimpleNamespace(create=_create)), model="test-model")
    orig = gen._settings.thinking_budget_tokens
    gen._settings.thinking_budget_tokens = 8000
    try:
        scout = gen.generate(make_candidate(), matches=[])
    finally:
        gen._settings.thinking_budget_tokens = orig
    assert scout is not None
    assert calls[0] == {"type": "auto"}                       # 1回目: 拡張思考(auto)
    assert calls[1] == {"type": "tool", "name": "emit_scout"}  # 2回目: 強制toolフォールバック


def test_thinking_disabled_uses_forced_tool_choice():
    captured: dict = {}
    gen = ScoutGenerator(client=_capturing_client(captured), model="test-model")
    orig = gen._settings.thinking_budget_tokens
    gen._settings.thinking_budget_tokens = 0
    try:
        gen.generate(make_candidate(), matches=[])
    finally:
        gen._settings.thinking_budget_tokens = orig
    assert "thinking" not in captured
    assert captured["tool_choice"] == {"type": "tool", "name": "emit_scout"}


def test_subject_normalized_when_prefix_missing():
    payload = dict(VALID_INPUT, subject_first="ご経歴に惹かれて限定オファー")
    gen = ScoutGenerator(client=_fake_client(payload), model="test-model")
    scout = gen.generate(make_candidate(), matches=[])
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
    scout = gen.generate(make_candidate(), matches=[])
    rendered = render_for_human(scout)
    assert "【初回送信用】" in rendered
    assert "【再送用】" in rendered
    assert rendered.count("```") >= 8  # 件名×2 + 本文×2 = 4ブロック → 8フェンス


# --- 共通点コンサルタント紹介（省略厳禁・1人ずつ独立ブロック）---------------------

def test_consultant_intro_rendered_as_separate_blocks():
    matches = [
        ConsultantMatch(consultant=_INOUE, common_points=["IT分野の共通点"]),
        ConsultantMatch(consultant=_SOMETANI, common_points=["慶應義塾大学の共通点"]),
    ]
    payload = dict(
        VALID_INPUT,
        consultant_intro_lead=(
            "余談ですが、当社にはあなたと同じIT分野での出身者や"
            "慶応義塾大学出身のコンサルタントも在籍しておりますので紹介いたします。"
        ),
        consultant_intros=[
            {"consultant_id": "inoue",
             "blurb": "当社の井ノ上貴之はIT・テクノロジー出身の当社コンサルタントとして在籍しています。"},
            {"consultant_id": "sometani",
             "blurb": "また、染谷東希は慶應義塾大学卒業後、当社で地域一番医院の創出に取り組んでおります。"},
        ],
    )
    gen = ScoutGenerator(client=_fake_client(payload), model="test-model")
    scout = gen.generate(make_candidate(), matches=matches)

    body = scout.first.body
    assert "余談ですが" in body
    # 1人ずつ独立したブロック（▼氏名 プロフィール＋URL）になっている
    assert "▼井ノ上 貴之 プロフィール\nhttps://www.consuldent.jp/member/inoue" in body
    assert "▼染谷 東希 プロフィール\nhttps://www.consuldent.jp/member/sometani" in body
    # 1人目のブロックと2人目の紹介文が空行で区切られている（連結されていない）
    idx_inoue_url = body.index("https://www.consuldent.jp/member/inoue")
    idx_sometani_blurb = body.index("また、染谷東希")
    assert "\n\n" in body[idx_inoue_url:idx_sometani_blurb]


def test_no_common_ground_still_includes_fallback_intro():
    # 共通点マッチが無くても、フォールバックで必ずコンサルタント紹介を載せる（最重要要件）。
    # モデルが blurb を出さなくても、既定紹介文が補われ紹介ブロックが本文に必ず出る。
    gen = ScoutGenerator(client=_fake_client(VALID_INPUT), model="test-model")
    scout = gen.generate(make_candidate(), matches=[])
    assert "▼" in scout.first.body  # 紹介ブロックが必ず存在する


def test_intro_disabled_when_max_is_zero():
    # max_intro_consultants=0 のときのみ紹介を完全に無効化できる。
    gen = ScoutGenerator(client=_fake_client(VALID_INPUT), model="test-model")
    orig = gen  # generate は scout_rules() を読むため、rules を直接差し替える
    import bizreach_scout.generation.generator as gmod
    real_rules = gmod.scout_rules

    def _rules_zero():
        r = dict(real_rules())
        r["matching"] = {**r.get("matching", {}), "max_intro_consultants": 0}
        return r

    gmod.scout_rules = _rules_zero
    try:
        scout = orig.generate(make_candidate(), matches=[])
    finally:
        gmod.scout_rules = real_rules
    assert "▼" not in scout.first.body


def test_missing_consultant_intro_triggers_retry_and_fixes_it():
    # 最重要指示（省略厳禁）が守られなかった場合、1回だけ修正を促して再取得する。
    matches = [ConsultantMatch(consultant=_INOUE, common_points=["共通点"])]
    incomplete = dict(VALID_INPUT, consultant_intro_lead="", consultant_intros=[])
    complete = dict(
        VALID_INPUT,
        consultant_intro_lead="ご紹介します。",
        consultant_intros=[{"consultant_id": "inoue", "blurb": "井ノ上貴之を紹介します。"}],
    )
    calls: list[dict] = []

    def _create(**kwargs):
        calls.append(kwargs)
        payload = incomplete if len(calls) == 1 else complete
        block = SimpleNamespace(
            type="tool_use", name="emit_scout", input=payload, id=f"t{len(calls)}"
        )
        return SimpleNamespace(content=[block])

    gen = ScoutGenerator(
        client=SimpleNamespace(messages=SimpleNamespace(create=_create)), model="test-model")
    scout = gen.generate(make_candidate(), matches=matches)

    assert len(calls) == 2  # 1回目で不足を検知→2回目で修正
    assert "▼井ノ上 貴之 プロフィール" in scout.first.body


def test_case_insensitive_consultant_id_is_not_flagged_as_missing():
    # モデルが consultant_id を大文字始まりで返しても「省略」と誤検知して
    # 不要な修正リトライを起こさず、紹介ブロックも正しく表示されること。
    matches = [ConsultantMatch(consultant=_INOUE, common_points=["共通点"])]
    payload = dict(
        VALID_INPUT,
        consultant_intro_lead="ご紹介します。",
        consultant_intros=[{"consultant_id": "Inoue", "blurb": "井ノ上貴之を紹介します。"}],
        # 再送側も同じ大文字始まりを検証対象に含める（大小文字非依存を両方で確認）。
        resend_consultant_intro_lead="再送でもご紹介します。",
        resend_consultant_intros=[{"consultant_id": "Inoue", "blurb": "再送用紹介文。"}],
    )
    calls: list[dict] = []

    def _create(**kwargs):
        calls.append(kwargs)
        block = SimpleNamespace(type="tool_use", name="emit_scout", input=payload, id="t1")
        return SimpleNamespace(content=[block])

    gen = ScoutGenerator(
        client=SimpleNamespace(messages=SimpleNamespace(create=_create)), model="test-model")
    scout = gen.generate(make_candidate(), matches=matches)

    assert len(calls) == 1  # 誤検知による不要な修正リトライが発生しない
    assert "▼井ノ上 貴之 プロフィール" in scout.first.body


def test_missing_resend_consultant_intro_lead_triggers_retry():
    # 初回側(consultant_intro_lead)と同じチェックが再送側にも対称に効くこと
    # （非対称バグの回帰テスト）。
    matches = [ConsultantMatch(consultant=_INOUE, common_points=["共通点"])]
    incomplete = dict(
        VALID_INPUT,
        consultant_intro_lead="ご紹介します。",
        consultant_intros=[{"consultant_id": "inoue", "blurb": "井ノ上貴之を紹介します。"}],
        resend_consultant_intro_lead="",
        resend_consultant_intros=[{"consultant_id": "inoue", "blurb": "再送用紹介文。"}],
    )
    complete = dict(incomplete, resend_consultant_intro_lead="再送でもご紹介します。")
    calls: list[dict] = []

    def _create(**kwargs):
        calls.append(kwargs)
        payload = incomplete if len(calls) == 1 else complete
        block = SimpleNamespace(
            type="tool_use", name="emit_scout", input=payload, id=f"t{len(calls)}"
        )
        return SimpleNamespace(content=[block])

    gen = ScoutGenerator(
        client=SimpleNamespace(messages=SimpleNamespace(create=_create)), model="test-model")
    scout = gen.generate(make_candidate(), matches=matches)

    assert len(calls) == 2  # 再送導入文の欠落を検知→2回目で修正
    assert "再送でもご紹介します。" in scout.resend.body


def test_validation_retry_survives_missing_tool_output_itself():
    # 検証リトライ(2回目)自体がadaptive+autoでツール未出力になっても、
    # 強制tool_choiceフォールバックで復旧しクラッシュしないことを確認する
    # （_call_ensuring_tool を検証リトライ経路にも適用した回帰テスト）。
    matches = [ConsultantMatch(consultant=_INOUE, common_points=["共通点"])]
    incomplete = dict(VALID_INPUT, consultant_intro_lead="", consultant_intros=[])
    complete = dict(
        VALID_INPUT,
        consultant_intro_lead="ご紹介します。",
        consultant_intros=[{"consultant_id": "inoue", "blurb": "井ノ上貴之を紹介します。"}],
    )
    calls: list[dict] = []

    def _create(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            block = SimpleNamespace(type="tool_use", name="emit_scout", input=incomplete, id="t1")
            return SimpleNamespace(content=[block])
        if n == 2:
            # 検証リトライの1回目(auto)がツール未使用で返ってくるケースを再現。
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="…")])
        block = SimpleNamespace(type="tool_use", name="emit_scout", input=complete, id=f"t{n}")
        return SimpleNamespace(content=[block])

    gen = ScoutGenerator(
        client=SimpleNamespace(messages=SimpleNamespace(create=_create)), model="test-model")
    scout = gen.generate(make_candidate(), matches=matches)  # 例外を送出せず完了すること

    assert len(calls) == 3  # 1回目→検証リトライ(auto失敗)→強制toolで復旧
    assert "▼井ノ上 貴之 プロフィール" in scout.first.body


def test_resend_consultant_intro_capped_to_max_mentions():
    # resend.max_consultant_mentions（既定1）を超えるコンサルタントは再送に含めない。
    matches = [
        ConsultantMatch(consultant=_INOUE, common_points=["共通点1"]),
        ConsultantMatch(consultant=_SOMETANI, common_points=["共通点2"]),
    ]
    payload = dict(
        VALID_INPUT,
        consultant_intro_lead="紹介します。",
        consultant_intros=[
            {"consultant_id": "inoue", "blurb": "井ノ上さん紹介文。"},
            {"consultant_id": "sometani", "blurb": "染谷さん紹介文。"},
        ],
        resend_consultant_intro_lead="再送でも紹介します。",
        resend_consultant_intros=[
            {"consultant_id": "inoue", "blurb": "井ノ上さんの再送用紹介文。"},
            {"consultant_id": "sometani", "blurb": "染谷さんの再送用紹介文。"},
        ],
    )
    gen = ScoutGenerator(client=_fake_client(payload), model="test-model")
    scout = gen.generate(make_candidate(), matches=matches)

    assert "井ノ上さんの再送用紹介文" in scout.resend.body
    assert "染谷さんの再送用紹介文" not in scout.resend.body  # 上限(既定1名)により除外
