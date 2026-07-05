from bizreach_scout.consultants import (
    candidate_flags,
    match_consultants,
    render_consultant_intro_section,
    render_matches_block,
    select_intro_matches,
)
from bizreach_scout.models import ConsultantMatch, ConsultantProfile

from .factories import make_candidate

SAMPLE = [
    ConsultantProfile(id="c001", display_name="A.S（リクルート出身）",
                      former_companies=["リクルート"], tags=["recruit"],
                      profile_url="https://example.com/c001"),
    ConsultantProfile(id="c002", display_name="B.K（リクルート出身）",
                      former_companies=["リクルートライフスタイル"], tags=["recruit"],
                      profile_url="https://example.com/c002"),
    ConsultantProfile(id="c008", display_name="H.M（プルデンシャル出身）",
                      former_companies=["プルデンシャル生命"], industries=["保険"],
                      tags=["insurance"], profile_url="https://example.com/c008"),
    ConsultantProfile(id="c009", display_name="N.F（コンサル出身）",
                      former_companies=["アクセンチュア"], tags=["consultant"],
                      profile_url="https://example.com/c009"),
]


def test_recruit_flag_detected():
    flags = candidate_flags(make_candidate(prior_companies=["リクルート"]))
    assert flags["is_recruit"] is True
    assert flags["is_insurance"] is False


def test_insurance_flag_detected():
    cand = make_candidate(prior_companies=["プルデンシャル生命"], industry="保険")
    flags = candidate_flags(cand)
    assert flags["is_insurance"] is True


def test_recruit_candidate_matches_all_recruit_consultants():
    cand = make_candidate(prior_companies=["リクルート"], industry="人材")
    matches = match_consultants(cand, consultants=SAMPLE)
    ids = {m.consultant.id for m in matches}
    assert {"c001", "c002"}.issubset(ids)
    assert all(m.category == "recruit" for m in matches if m.consultant.id in {"c001", "c002"})


def test_insurance_candidate_matches_insurance_consultant():
    cand = make_candidate(prior_companies=["第一生命"], industry="生命保険",
                          current_company="第一生命")
    matches = match_consultants(cand, consultants=SAMPLE)
    ids = {m.consultant.id for m in matches}
    assert "c008" in ids


def test_no_common_ground_yields_no_match():
    cand = make_candidate(prior_companies=["無関係株式会社"], industry="製造",
                          current_company="無関係株式会社", university="無名大学",
                          job_function="製造")
    matches = match_consultants(cand, consultants=SAMPLE)
    assert matches == []


def test_render_block_includes_url():
    cand = make_candidate(prior_companies=["リクルート"])
    matches = match_consultants(cand, consultants=SAMPLE)
    block = render_matches_block(matches)
    assert "https://example.com/c001" in block


def test_render_block_includes_consultant_id():
    # emit_scout の consultant_intros[].consultant_id で参照するための識別子。
    cand = make_candidate(prior_companies=["リクルート"])
    matches = match_consultants(cand, consultants=SAMPLE)
    block = render_matches_block(matches)
    assert "consultant_id: c001" in block


# --- select_intro_matches（本文で紹介する人数の上限）--------------------------

def test_select_intro_matches_caps_to_configured_max():
    cand = make_candidate(prior_companies=["リクルート"], industry="人材")
    matches = match_consultants(cand, consultants=SAMPLE)
    assert len(matches) > 1  # SAMPLEにはrecruitマッチが複数ある
    capped = select_intro_matches(matches, rules={"matching": {"max_intro_consultants": 1}})
    assert len(capped) == 1
    assert capped[0].consultant.id == matches[0].consultant.id  # 優先順位の先頭を維持


def test_select_intro_matches_unlimited_when_zero():
    cand = make_candidate(prior_companies=["リクルート"], industry="人材")
    matches = match_consultants(cand, consultants=SAMPLE)
    capped = select_intro_matches(matches, rules={"matching": {"max_intro_consultants": 0}})
    assert capped == matches


def test_select_intro_matches_default_caps_at_three():
    cand = make_candidate(prior_companies=["リクルート"], industry="人材")
    matches = match_consultants(cand, consultants=SAMPLE)
    capped = select_intro_matches(matches)  # rules未指定→scout_rules.yamlの既定(3)
    assert len(capped) <= 3


# --- render_consultant_intro_section（1人ずつ独立したブロックの組み立て）--------

def test_render_consultant_intro_section_format():
    m1 = ConsultantMatch(
        consultant=ConsultantProfile(id="a", display_name="A太郎",
                                     profile_url="https://example.com/a"),
        common_points=["共通点"],
    )
    m2 = ConsultantMatch(
        consultant=ConsultantProfile(id="b", display_name="B次郎",
                                     profile_url="https://example.com/b"),
        common_points=["共通点"],
    )
    section = render_consultant_intro_section(
        "余談ですが紹介します。",
        {"a": "Aさんの紹介文。", "b": "Bさんの紹介文。"},
        [m1, m2],
    )
    # 導入文は先頭コンサルタントの紹介文と地続き（改行のみ・空行なし）。
    assert section.startswith(
        "余談ですが紹介します。\nAさんの紹介文。\n▼A太郎 プロフィール\nhttps://example.com/a"
    )
    # 2人目以降は空行で区切られた独立ブロック。
    assert "\n\nBさんの紹介文。\n▼B次郎 プロフィール\nhttps://example.com/b" in section


def test_render_consultant_intro_section_skips_missing_blurb():
    m1 = ConsultantMatch(
        consultant=ConsultantProfile(id="a", display_name="A太郎",
                                     profile_url="https://example.com/a"),
        common_points=["共通点"],
    )
    # blurb が提供されていないコンサルタントはブロックごと省略する。
    assert render_consultant_intro_section("導入文", {}, [m1]) == ""


def test_render_consultant_intro_section_empty_matches_yields_empty_string():
    assert render_consultant_intro_section("導入文", {"a": "紹介文"}, []) == ""
