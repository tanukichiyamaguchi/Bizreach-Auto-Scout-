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


def test_missing_required_fields_are_retried_once_before_failing():
    """一部の項目だけ書いた emit_scout は、空の項目を伝えて1回だけ書き直させる（Sonnet 5.5 の本番で発生）。"""
    partial = {k: v for k, v in VALID_INPUT.items()
               if k not in ("company_intro", "career_body", "resend_body")}
    calls: list = []

    def _create(**kwargs):
        calls.append(dict(kwargs, messages=list(kwargs["messages"])))  # 送った時点の会話を保存
        payload = partial if len(calls) == 1 else VALID_INPUT
        block = SimpleNamespace(type="tool_use", name="emit_scout", input=payload, id=f"t{len(calls)}")
        return SimpleNamespace(content=[block])

    gen = ScoutGenerator(
        client=SimpleNamespace(messages=SimpleNamespace(create=_create)), model="test-model")
    scout = gen.generate(make_candidate(), matches=[])
    assert scout is not None
    retry_msg = calls[1]["messages"][-1]["content"][0]
    assert retry_msg["type"] == "tool_result" and retry_msg["tool_use_id"] == "t1"
    assert "company_intro" in retry_msg["content"] and "resend_body" in retry_msg["content"]


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
    # adaptive thinking を使う（budget_tokens形式は近年のモデルで400になる）。
    assert captured["thinking"] == {"type": "adaptive"}
    assert captured["output_config"]["effort"]  # 深さは effort で制御
    assert captured["tool_choice"] == {"type": "auto"}  # 強制tool_choiceは Sonnet 5.5 で400


def test_missing_tool_output_is_retried_with_reminder_not_forced_tool():
    # auto でツール未出力なら、強制 tool_choice（Sonnet 5.5 / Opus 5.5 で400）は使わず、
    # system に「emit_scout を呼ぶ」指示を足して取り直す。
    calls: list = []
    tool_block = SimpleNamespace(type="tool_use", name="emit_scout", input=VALID_INPUT, id="t1")
    tool_resp = SimpleNamespace(content=[tool_block])
    text_resp = SimpleNamespace(content=[SimpleNamespace(type="text", text="…")])

    def _create(**kwargs):
        calls.append(kwargs)
        reminded = "必ず emit_scout ツールを1回呼び出して" in kwargs["system"]
        return tool_resp if reminded else text_resp

    gen = ScoutGenerator(
        client=SimpleNamespace(messages=SimpleNamespace(create=_create)), model="test-model")
    orig = gen._settings.thinking_budget_tokens
    gen._settings.thinking_budget_tokens = 8000
    try:
        scout = gen.generate(make_candidate(), matches=[])
    finally:
        gen._settings.thinking_budget_tokens = orig
    assert scout is not None
    assert all(c["tool_choice"] == {"type": "auto"} for c in calls)
    assert "必ず emit_scout" not in calls[0]["system"]
    assert "必ず emit_scout" in calls[1]["system"]


def test_thinking_disabled_still_uses_auto_tool_choice():
    captured: dict = {}
    gen = ScoutGenerator(client=_capturing_client(captured), model="test-model")
    orig = gen._settings.thinking_budget_tokens
    gen._settings.thinking_budget_tokens = 0
    try:
        gen.generate(make_candidate(), matches=[])
    finally:
        gen._settings.thinking_budget_tokens = orig
    assert "thinking" not in captured
    assert captured["tool_choice"] == {"type": "auto"}


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
    # 「emit_scout を呼ぶ」指示つきの取り直しで復旧しクラッシュしないことを確認する
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

    assert len(calls) == 3  # 1回目→検証リトライ(ツール未出力)→指示つきで復旧
    assert all(c["tool_choice"] == {"type": "auto"} for c in calls)
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


def test_false_commonality_issue_detected_for_non_common_match():
    """共通点が無いコンサルタントの紹介文で「同じ」と断定したら修正リトライに回す。"""
    from bizreach_scout.generation.generator import ScoutGenerator
    from bizreach_scout.models import ConsultantMatch, ConsultantProfile

    c = ConsultantProfile(id="x1", display_name="山田 一郎", profile_url="u")
    fallback = ConsultantMatch(consultant=c, common_points=["当社で活躍するコンサルタント"],
                               category="fallback")
    common = ConsultantMatch(consultant=c, common_points=["前職企業の共通点（リクルート）"],
                             category="recruit")

    data = {"consultant_intros": [
        {"consultant_id": "x1", "blurb": "あなたと同じリクルート出身の山田がおります。"}]}
    # fallback（共通点なし）で断定 → 指摘される。
    issues = ScoutGenerator._false_commonality_issues(data, [fallback], [])
    assert issues and "山田 一郎" in issues[0]
    # 実際に共通点マッチしている場合は指摘しない。
    assert ScoutGenerator._false_commonality_issues(data, [common], []) == []
    # 断定表現が無ければ指摘しない。
    ok = {"consultant_intros": [
        {"consultant_id": "x1", "blurb": "当社で活躍する医院経営分野のコンサルタントです。"}]}
    assert ScoutGenerator._false_commonality_issues(ok, [fallback], []) == []


def test_sonnet_5_5_uses_server_side_refusal_fallback():
    """Sonnet 5.5 では誤判定の拒否に備えて、サーバー側フォールバック（beta）を付けて呼ぶ。"""
    captured: dict = {}
    block = SimpleNamespace(type="tool_use", name="emit_scout", input=VALID_INPUT, id="t1")

    def _create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(content=[block], stop_reason="tool_use")

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=_create)))
    gen = ScoutGenerator(client=client, model="claude-sonnet-5-5")
    gen.generate(make_candidate(), matches=[])
    assert captured["model"] == "claude-sonnet-5-5"
    assert captured["betas"] == ["server-side-fallback-2026-07-01"]
    assert captured["fallbacks"] == "default"
    assert captured["tool_choice"] == {"type": "auto"}
    assert "temperature" not in captured and "top_p" not in captured


def test_refusal_is_reported_as_generation_error():
    """拒否（stop_reason=refusal）は、ツール未出力の取り直しをせず理由つきの生成失敗にする。"""
    import pytest

    from bizreach_scout.generation.generator import GenerationError

    calls: list = []

    def _create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content=[], stop_reason="refusal",
                               stop_details=SimpleNamespace(category="general_harms"))

    gen = ScoutGenerator(
        client=SimpleNamespace(messages=SimpleNamespace(create=_create)), model="test-model")
    with pytest.raises(GenerationError, match="general_harms"):
        gen.generate(make_candidate(), matches=[])
    assert len(calls) == 1
