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
