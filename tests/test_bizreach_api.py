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


def test_map_grade_mba_is_master():
    # 実データで確認: schoolGrade="MBA"（グロービス経営大学院）は大学院卒＝master相当。
    assert _map_grade("MBA") == Education.master
    assert _map_grade("EMBA") == Education.master
    assert _map_grade("LLM") == Education.master


def test_highest_education_wins_across_entries():
    # 学歴が複数ある場合はエントリ順に関係なく最上位を最終学歴とする。
    resume = _resume()
    resume["educations"] = [
        {"schoolGrade": "Bachelors", "name": {"ja": "立教大学", "en": None}},
        {"schoolGrade": "MBA", "name": {"ja": "グロービス経営大学院", "en": None}},
    ]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert c.education == Education.master
    assert check_eligibility(c).eligible  # 大学院卒→大学卒以上を満たす


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


# --- 海外教育機関の判定（ja表記の有無を代替シグナルとする）--------------------

def test_domestic_education_has_japanese_name_and_is_not_overseas():
    c = resume_to_candidate(_resume(), now=datetime(2026, 7, 1))
    assert c.overseas_education is False


def test_overseas_education_detected_when_no_japanese_name():
    resume = _resume()
    resume["educations"] = [
        {"schoolGrade": "Bachelors", "name": {"ja": None, "en": "Stanford University"}},
    ]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert c.overseas_education is True
    result = check_eligibility(c)
    assert not result.eligible
    assert any("海外の教育機関" in r for r in result.failed)


def test_overseas_education_not_flagged_when_both_ja_and_en_missing():
    # ja/en とも判定材料が無い場合は「海外教育機関」とは断定しない（判定不能）。
    resume = _resume()
    resume["educations"] = [{"schoolGrade": "Bachelors", "name": {}}]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert c.overseas_education is False


def test_highest_ranked_entry_used_for_overseas_check():
    # 最上位（最高ランク）の学歴レコードを海外判定にも使う（他エントリのja/enに影響されない）。
    resume = _resume()
    resume["educations"] = [
        {"schoolGrade": "Bachelors", "name": {"ja": "立教大学", "en": None}},
        {"schoolGrade": "MBA", "name": {"ja": None, "en": "Harvard Business School"}},
    ]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert c.education == Education.master
    assert c.overseas_education is True


def test_overseas_detected_when_ja_name_is_latin_only():
    # ja 欄にラテン文字だけ（英語名）が入っているケースも海外の大学とみなす。
    resume = _resume()
    resume["educations"] = [
        {"schoolGrade": "Bachelors", "name": {"ja": "Stanford University", "en": None}},
    ]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert c.overseas_education is True
    assert not check_eligibility(c).eligible


def test_overseas_detected_when_any_education_is_overseas():
    # 最終学歴が国内でも、いずれかの学歴が海外なら「海外の大学卒」として拾う。
    resume = _resume()
    resume["educations"] = [
        {"schoolGrade": "Masters", "name": {"ja": "東京大学大学院", "en": None}},
        {"schoolGrade": "Bachelors", "name": {"ja": None, "en": "University of Oxford"}},
    ]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert c.overseas_education is True


def test_foreign_text_collects_english_fields():
    # en 欄の職務要約・学歴名が foreign_text に集約される（生成用の summary は日本語のまま）。
    resume = _resume()
    resume["jobSummary"] = {"ja": "", "en": "Enterprise sales leader."}
    resume["educations"] = [
        {"schoolGrade": "Bachelors", "name": {"ja": None, "en": "Waseda University"}},
    ]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert "Enterprise sales leader." in c.foreign_text
    assert "Waseda University" in c.foreign_text


def test_full_english_resume_is_ineligible():
    # 職務要約・職歴が英語のみで書かれた候補者（外国人）は自動送信から除外される。
    resume = _resume()
    resume["jobSummary"] = {
        "ja": "",
        "en": ("Experienced enterprise sales manager with over ten years leading teams "
               "and closing large deals across the APAC region."),
    }
    resume["coreCompetencies"] = [{"ja": "", "en": "New business development and key account management."}]
    resume["specialInstruction"] = {"ja": "", "en": "Fluent English and native French speaker."}
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    result = check_eligibility(c)
    assert not result.eligible
    # 英語優勢・外国語ネイティブのいずれか（両方）で外国人として弾かれる。
    assert any("外国人の可能性" in r for r in result.failed)


def test_japanese_resume_with_english_school_name_still_eligible():
    # 日本語のレジュメで、学歴の en 名（Waseda University）が foreign_text に入っても、
    # 本文が日本語主体なら英語優勢とは見なさず対象のまま（誤検出しない）。
    c = resume_to_candidate(_resume(), now=datetime(2026, 7, 1))
    assert "Waseda University" in c.foreign_text
    assert check_eligibility(c).eligible


def test_parse_rrsc():
    url = "https://cr-support.jp/scout/highclass/search/?rrsc=3444981"
    assert BizreachApi.parse_rrsc(url) == "3444981"
    assert BizreachApi.parse_rrsc("https://cr-support.jp/x") is None
