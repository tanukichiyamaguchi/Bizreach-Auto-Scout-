from bizreach_scout.consultants import match_consultants
from bizreach_scout.generation.prompt import (
    build_system_prompt,
    render_special_instructions,
    select_tones,
)

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


def test_insurance_special_instruction_has_url():
    cand = make_candidate(prior_companies=["第一生命"], industry="生命保険",
                          current_company="第一生命")
    text = render_special_instructions(cand)
    assert "プルデンシャル" in text
    assert "https://www.consuldent.jp/recruitment/2020/04/3272/" in text
    # URLはscout_reason経由、コンサルタント紹介はconsultant_intros経由と明記
    # されていること（旧「本文に紹介してください」への回帰を検知）。
    assert "scout_reasonで紹介" in text
    assert "consultant_intros" in text


def test_tone_selection_by_age():
    young = make_candidate(age=28, total_experience_years=4, job_function="その他")
    keys = {t["key"] for t in select_tones(young)}
    assert "late20s" in keys


def test_tone_selection_by_function():
    sales = make_candidate(age=40, job_function="法人営業")
    keys = {t["key"] for t in select_tones(sales)}
    assert "sales" in keys
