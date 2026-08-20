from bizreach_scout.consultants import match_consultants
from bizreach_scout.generation.prompt import (
    build_system_prompt,
    render_special_instructions,
    select_tones,
)
from bizreach_scout.models import ConsultantProfile

from .factories import make_candidate


def test_system_prompt_has_no_unfilled_placeholders():
    cand = make_candidate()
    matches = match_consultants(cand)
    prompt, tone_key = build_system_prompt(cand, matches)
    assert "<<" not in prompt and ">>" not in prompt
    assert cand.member_no in prompt
    assert tone_key


def test_recruit_special_instruction_mentions_count():
    cand = make_candidate(prior_companies=["リクルート"], industry="人材")
    text = render_special_instructions(cand)
    assert "リクルート出身" in text
    assert "7名" in text
    # 共通点コンサルタントの紹介は自由文(本文添付)ではなく専用フィールド
    # (consultant_intros)へ委譲する新指示になっていること（旧文言への回帰を検知）。
    assert "consultant_intros" in text
    assert "省略禁止" in text
    assert "本文に分かりやすく添付" not in text


def _insurance_candidate():
    return make_candidate(prior_companies=["第一生命"], industry="生命保険",
                          current_company="第一生命")


def test_insurance_instruction_forbids_claim_when_no_such_consultant():
    """当社に保険出身の紹介可能者がいなければ「在籍している」と書かせない。

    2026-08 に、退職済みの保険出身コンサルタントを根拠にした
    「当社にもプルデンシャル生命出身の人材が在籍しており」という記述が実送信された。
    候補者側の出身だけで訴求を組み立てていたことが原因。
    """
    rules = {"matching": {
        "insurance_keywords": ["保険", "生命保険", "第一生命"],
        "recruit_keywords": ["リクルート"],
    }}
    text = render_special_instructions(_insurance_candidate(), rules, consultants=[])
    assert "プルデンシャル" not in text
    assert "3272" not in text
    # 黙って落とすのではなく、書いてはいけないと明示する。
    assert "絶対に書かないでください" in text
    assert "保険業界" in text


def test_insurance_instruction_emitted_when_consultant_is_available():
    """紹介可能な保険出身者がいれば訴求する（ただし特定企業名は出さない）。"""
    rules = {"matching": {
        "insurance_keywords": ["保険", "生命保険", "第一生命"],
        "recruit_keywords": ["リクルート"],
    }}
    pool = [ConsultantProfile(id="ins1", display_name="保険 太郎", tags=["insurance"])]
    text = render_special_instructions(_insurance_candidate(), rules, consultants=pool)
    assert "保険業界出身" in text
    assert "consultant_intros" in text
    # 当社側の特定企業名は固定で書かない（在籍状況が変われば嘘になる）。
    assert "プルデンシャル" not in text
    assert "絶対に書かないでください" not in text


def test_excluded_consultant_does_not_back_the_claim():
    """除外設定されたコンサルタントは訴求の根拠にならない。

    紹介対象外なのに訴求の根拠にできると、名前は出ないのに
    「当社に◯◯出身者がいる」だけが残る（今回の事故の形）。
    """
    rules = {"matching": {
        "insurance_keywords": ["保険", "生命保険", "第一生命"],
        "recruit_keywords": ["リクルート"],
        "exclude_consultant_ids": ["yamamoto"],
    }}
    pool = [ConsultantProfile(id="yamamoto", display_name="山本 峻士",
                              former_companies=["プルデンシャル生命保険"],
                              tags=["insurance", "prudential"])]
    text = render_special_instructions(_insurance_candidate(), rules, consultants=pool)
    assert "絶対に書かないでください" in text
    assert "プルデンシャル" not in text


def test_production_config_does_not_claim_insurance_alumni():
    """実際の config で、保険出身候補者に在籍主張をしないこと（回帰検知）。"""
    text = render_special_instructions(_insurance_candidate())
    assert "プルデンシャル" not in text
    assert "3272" not in text


def test_prompt_template_has_no_hardcoded_insurance_claim():
    """テンプレート側にも特定企業の在籍主張を残さない。"""
    from bizreach_scout.config import prompt_template

    tpl = prompt_template()
    assert "プルデンシャル" not in tpl
    assert "3272" not in tpl


def test_tone_selection_by_age():
    young = make_candidate(age=28, total_experience_years=4, job_function="その他")
    keys = {t["key"] for t in select_tones(young)}
    assert "late20s" in keys


def test_tone_selection_by_function():
    sales = make_candidate(age=40, job_function="法人営業")
    keys = {t["key"] for t in select_tones(sales)}
    assert "sales" in keys
