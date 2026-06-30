from bizreach_scout.consultants import (
    candidate_flags,
    match_consultants,
    render_matches_block,
)
from bizreach_scout.models import ConsultantProfile

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
