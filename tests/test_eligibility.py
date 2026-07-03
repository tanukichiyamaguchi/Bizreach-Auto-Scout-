from bizreach_scout.eligibility import check_eligibility
from bizreach_scout.models import Education, Gender

from .factories import make_candidate


def test_eligible_candidate_passes():
    result = check_eligibility(make_candidate())
    assert result.eligible
    assert result.failed == []


def test_too_young_fails():
    result = check_eligibility(make_candidate(age=25))
    assert not result.eligible
    assert any("年齢" in r for r in result.failed)


def test_female_fails():
    result = check_eligibility(make_candidate(gender=Gender.female))
    assert not result.eligible
    assert any("性別" in r for r in result.failed)


def test_short_tenure_fails():
    result = check_eligibility(
        make_candidate(current_tenure_years=1.0, employments=[])
    )
    assert not result.eligible
    assert any("勤続" in r for r in result.failed)


def test_low_education_fails():
    result = check_eligibility(make_candidate(education=Education.high_school))
    assert not result.eligible
    assert any("学歴" in r for r in result.failed)


def test_unknown_fields_need_confirmation():
    result = check_eligibility(
        make_candidate(age=None, gender=Gender.unknown, education=Education.unknown,
                       current_tenure_years=None, total_experience_years=None)
    )
    assert not result.eligible
    assert result.needs_confirmation
    assert len(result.failed) == 4


def test_master_education_meets_bachelor_requirement():
    result = check_eligibility(make_candidate(education=Education.master))
    assert result.eligible


# --- 会員ステータス条件（新着/更新/HOT/WILL/プレミアムのいずれか）---------------

def test_no_target_status_fails():
    # どのステータスにも該当しない候補者は対象外。
    result = check_eligibility(
        make_candidate(intention=[], resume_updated_status="None",
                       contract_plan="Free")
    )
    assert not result.eligible
    assert any("ステータス" in r for r in result.failed)


def test_hot_status_passes():
    result = check_eligibility(
        make_candidate(intention=["Hot"], resume_updated_status="None",
                       contract_plan="Free")
    )
    assert result.eligible


def test_premium_only_passes():
    # HOT/WILLでなくてもプレミアム会員なら対象。
    result = check_eligibility(
        make_candidate(intention=[], resume_updated_status="None",
                       contract_plan="Premium")
    )
    assert result.eligible


def test_new_and_updated_status_pass():
    for status in ("New", "Updated"):
        result = check_eligibility(
            make_candidate(intention=[], resume_updated_status=status,
                           contract_plan="Free")
        )
        assert result.eligible, status


def test_highclass_is_in_scope():
    # HighClass も対象（Talentと区別しない）。ステータス条件を満たせば eligible。
    result = check_eligibility(
        make_candidate(candidate_class="HighClass", intention=["Will"])
    )
    assert result.eligible


def test_status_flags_helper():
    c = make_candidate(intention=["Hot", "Will"], resume_updated_status="New",
                       contract_plan="Premium")
    assert c.status_flags() == {"hot", "will", "new", "premium"}


def test_pickup_ignores_status_filter():
    # ピックアップ(apply_status_filter=False)はステータス不問。他条件は満たす前提。
    cand = make_candidate(intention=[], resume_updated_status="None", contract_plan="Free")
    assert not check_eligibility(cand).eligible                      # 通常はステータスで落ちる
    assert check_eligibility(cand, apply_status_filter=False).eligible  # ピックアップは通る


def test_pickup_still_enforces_core_conditions():
    # ステータス不問でも、学歴・性別などコア条件は常に適用する。
    cand = make_candidate(education=Education.high_school, intention=[],
                          resume_updated_status="None", contract_plan="Free")
    result = check_eligibility(cand, apply_status_filter=False)
    assert not result.eligible
    assert any("学歴" in r for r in result.failed)
