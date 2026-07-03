"""ビズリーチAPIのレジュメ→Candidate 変換とヘルパのテスト。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from bizreach_scout.bizreach.api import (
    BizreachApi,
    _income_label,
    _map_grade,
    resume_to_candidate,
)
from bizreach_scout.consultants import candidate_flags
from bizreach_scout.eligibility import check_eligibility
from bizreach_scout.models import Education, Gender

FIX = Path(__file__).parent / "fixtures" / "resume_sample.json"


def _resume() -> dict:
    return json.loads(FIX.read_text(encoding="utf-8"))


def test_income_label():
    assert _income_label("Between750And1000") == "750〜1000万円"
    assert _income_label("Upper1000") == "1000万円以上"
    assert _income_label("Under600") == "600万円未満"
    assert _income_label(None) == ""


def test_resume_to_candidate_basic_fields():
    c = resume_to_candidate(_resume(), now=datetime(2026, 7, 1))
    assert c is not None
    assert c.member_no == "BU5838534"      # bizreachUserId → 会員番号
    assert c.mrccid == "TESTmrccid123"
    assert c.age == 34
    assert c.gender == Gender.male
    assert c.education == Education.bachelor
    assert c.university == "早稲田大学"
    assert c.current_company == "株式会社リクルート"
    assert c.current_title == "法人営業マネージャー"
    # 2018/4 から 2026/7 → 約8年
    assert c.current_tenure_years is not None and c.current_tenure_years >= 8.0
    assert "株式会社ABC商事" in c.prior_companies
    assert c.salary_current == "750〜1000万円"
    assert "全社表彰" in c.raw_profile or "自己PR" in c.raw_profile


def test_mapped_candidate_is_eligible_and_recruit():
    c = resume_to_candidate(_resume(), now=datetime(2026, 7, 1))
    # 34歳・男性・大学卒・現職8年 → 対象条件を満たす
    assert check_eligibility(c).eligible
    # 現職がリクルート → リクルート出身フラグが立つ
    assert candidate_flags(c)["is_recruit"] is True


def test_map_grade_known_values():
    assert _map_grade("Bachelors") == Education.bachelor
    assert _map_grade("Masters") == Education.master
    assert _map_grade("Doctor") == Education.doctor
    assert _map_grade("HighSchool") == Education.high_school
    assert _map_grade("") == Education.unknown


def test_map_grade_junior_college_is_below_bachelor():
    # 高専・専門・短大卒は Associate/Vocational とは別 enum で返るため、
    # キーワード推定で「大学卒未満」に落とす（unknown にしない）のが要点。
    for raw in ("JuniorCollege", "TechnicalCollege", "VocationalSchool",
                "SpecializedTraining"):
        edu = _map_grade(raw)
        assert edu is not Education.unknown, raw
        assert edu.rank < Education.bachelor.rank, raw


def test_map_grade_university_variants_never_dropped():
    # 誤って大学卒以上を不明/下位に落とさないことが最重要（対象取りこぼし防止）。
    assert _map_grade("University").rank >= Education.bachelor.rank
    assert _map_grade("GraduateSchool").rank >= Education.master.rank
    assert _map_grade("Undergraduate") == Education.bachelor  # "graduate"を含むが学部卒


def test_junior_college_candidate_reads_and_is_ineligible():
    # BU2490384 相当（高専・専門・短大卒）は不明ではなく大学卒未満で判定される。
    resume = _resume()
    resume["educations"] = [{"schoolGrade": "JuniorCollege",
                             "name": {"ja": "東京観光専門学校", "en": None}}]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert c.education is not Education.unknown
    result = check_eligibility(c)
    assert not result.eligible
    assert any("学歴" in r for r in result.failed)
    assert not any("不明" in r for r in result.failed)  # 「不明」ではない


def test_resume_missing_ids_returns_none():
    assert resume_to_candidate({"age": 30}) is None


def test_parse_rrsc():
    url = "https://cr-support.jp/scout/highclass/search/?rrsc=3444981"
    assert BizreachApi.parse_rrsc(url) == "3444981"
    assert BizreachApi.parse_rrsc("https://cr-support.jp/x") is None
