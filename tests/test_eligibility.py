from bizreach_scout.eligibility import check_eligibility
from bizreach_scout.models import Education, Employment, Gender

from .factories import make_candidate

# 直近の転職テスト用: 過去に3年以上の在籍歴を持たせて「勤続3年以上」条件を満たす。
_LONG_PAST = [Employment(company="過去在籍", years=5.0)]


def test_eligible_candidate_passes():
    result = check_eligibility(make_candidate())
    assert result.eligible
    assert result.failed == []


def test_too_young_fails():
    result = check_eligibility(make_candidate(age=25))
    assert not result.eligible
    assert any("年齢" in r for r in result.failed)


def test_age_42_passes():
    # 上限の42歳は対象内（他条件は満たす前提。40代枠の転職回数上限=6回未満で通す）。
    result = check_eligibility(make_candidate(age=42, current_tenure_years=4.0))
    assert result.eligible


def test_age_43_fails():
    # 43歳（上限超過）は対象外。
    result = check_eligibility(make_candidate(age=43, current_tenure_years=4.0))
    assert not result.eligible
    assert any("年齢が42歳超過" in r for r in result.failed)


def test_max_age_disabled_when_unset():
    # max_age を設定しない rules dict では年齢上限を適用しない。
    from bizreach_scout.config import scout_rules

    rules = scout_rules()
    custom = {**rules, "eligibility": {**rules["eligibility"], "max_age": None}}
    result = check_eligibility(make_candidate(age=50, current_tenure_years=4.0), rules=custom)
    assert not any("超過" in r for r in result.failed)


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


def test_tenure_2_5_years_passes():
    # 下限の2.5年は対象内（同一企業2.5年以上）。
    result = check_eligibility(
        make_candidate(current_tenure_years=2.5, employments=[])
    )
    assert result.eligible


def test_tenure_2_4_years_fails():
    # 2.5年未満（2.4年）は対象外。
    result = check_eligibility(
        make_candidate(current_tenure_years=2.4, employments=[])
    )
    assert not result.eligible
    assert any("勤続" in r for r in result.failed)


def test_low_education_fails():
    result = check_eligibility(make_candidate(education=Education.high_school))
    assert not result.eligible
    assert any("学歴" in r for r in result.failed)


# --- 海外の教育機関出身は対象外 -----------------------------------------------

def test_overseas_education_fails():
    result = check_eligibility(make_candidate(overseas_education=True))
    assert not result.eligible
    assert any("海外の教育機関" in r for r in result.failed)


def test_domestic_education_passes():
    result = check_eligibility(make_candidate(overseas_education=False))
    assert result.eligible


def test_exclude_overseas_education_can_be_disabled_via_config():
    from bizreach_scout.config import scout_rules

    rules = scout_rules()
    custom = {**rules, "eligibility": {**rules["eligibility"], "exclude_overseas_education": False}}
    result = check_eligibility(make_candidate(overseas_education=True), rules=custom)
    assert not any("海外の教育機関" in r for r in result.failed)


# --- 日本語以外がネイティブレベルの人は対象外（日本語検定の保有を代替シグナルに）---

def test_japanese_proficiency_cert_in_raw_profile_fails():
    result = check_eligibility(make_candidate(raw_profile="資格: 日本語能力試験N1、TOEIC 800"))
    assert not result.eligible
    assert any("日本語検定" in r for r in result.failed)


def test_jlpt_keyword_in_summary_fails():
    result = check_eligibility(make_candidate(summary="JLPT N1保有。営業経験8年。"))
    assert not result.eligible
    assert any("日本語検定" in r for r in result.failed)


def test_no_japanese_cert_mention_passes():
    result = check_eligibility(make_candidate(raw_profile="資格: TOEIC 800、簿記2級"))
    assert result.eligible


def test_exclude_non_japanese_native_can_be_disabled_via_config():
    from bizreach_scout.config import scout_rules

    rules = scout_rules()
    custom = {**rules, "eligibility": {**rules["eligibility"], "exclude_non_japanese_native": False}}
    result = check_eligibility(
        make_candidate(raw_profile="資格: 日本語能力試験N1"), rules=custom
    )
    assert not any("日本語検定" in r for r in result.failed)


# --- 外国語がネイティブレベル（外国人の可能性）は対象外 ------------------------

def test_foreign_native_language_in_languages_fails():
    result = check_eligibility(make_candidate(languages="英語（ネイティブ）、日本語（日常会話）"))
    assert not result.eligible
    assert any("外国語がネイティブ" in r for r in result.failed)


def test_foreign_native_language_in_foreign_text_fails():
    # API経路で en 欄に入っていた語学情報（foreign_text）でも検出できる。
    result = check_eligibility(make_candidate(foreign_text="Native English speaker"))
    assert not result.eligible
    assert any("外国語がネイティブ" in r for r in result.failed)


def test_japanese_native_english_business_passes():
    # 日本語ネイティブ＋英語ビジネスレベルは対象（誤検出しない）。
    result = check_eligibility(make_candidate(languages="日本語：ネイティブ / 英語：ビジネスレベル"))
    assert result.eligible


def test_foreign_native_language_can_be_disabled_via_config():
    from bizreach_scout.config import scout_rules

    rules = scout_rules()
    custom = {**rules, "eligibility": {**rules["eligibility"], "exclude_non_japanese_native": False}}
    result = check_eligibility(make_candidate(languages="英語（ネイティブ）"), rules=custom)
    assert not any("外国語がネイティブ" in r for r in result.failed)


# --- 職務要約・職歴がほとんど英語（外国人の可能性）は対象外 --------------------

def test_english_dominant_resume_fails():
    english = ("Experienced enterprise sales manager with more than ten years leading "
               "teams and closing large deals across the APAC region.")
    result = check_eligibility(make_candidate(summary=english, raw_profile=""))
    assert not result.eligible
    assert any("英語" in r and "対象外" in r for r in result.failed)


def test_bilingual_japanese_resume_passes():
    # 日本語主体＋一部英語（バイリンガル日本人）は対象（英語優勢と見なさない）。
    mixed = ("グローバル法人営業を担当。English business communication の経験あり。"
             "新規開拓で全社表彰2回。")
    result = check_eligibility(make_candidate(summary=mixed))
    assert result.eligible


def test_exclude_english_resume_can_be_disabled_via_config():
    from bizreach_scout.config import scout_rules

    english = ("Experienced enterprise sales manager with more than ten years leading "
               "teams and closing large deals across the region.")
    rules = scout_rules()
    custom = {**rules, "eligibility": {**rules["eligibility"], "exclude_english_resume": False}}
    result = check_eligibility(make_candidate(summary=english, raw_profile=""), rules=custom)
    assert not any("英語" in r and "対象外" in r for r in result.failed)


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


# --- 直近1年以内の転職（現職在籍が短い）は対象外 -------------------------------

def test_recent_job_change_fails():
    # 過去に3年以上の在籍歴はあるが、現職在籍が1年未満 = 直近に転職 → 対象外。
    result = check_eligibility(make_candidate(current_tenure_years=0.5,
                                              employments=_LONG_PAST))
    assert not result.eligible
    assert any("直近1年以内の転職" in r for r in result.failed)


def test_current_tenure_one_year_passes():
    # ちょうど1年は「未満」ではないので recency では弾かない（他条件は満たす前提）。
    result = check_eligibility(make_candidate(current_tenure_years=1.0,
                                              employments=_LONG_PAST))
    assert result.eligible


def test_recent_job_change_not_flagged_when_tenure_unknown():
    # 現職在籍も過去在籍も無く不明なら「勤続年数が不明」で拾う（recency文言は付けない）。
    result = check_eligibility(make_candidate(current_tenure_years=None))
    assert not any("直近1年以内の転職" in r for r in result.failed)
    assert any("勤続年数が不明" in r for r in result.failed)


def test_unknown_current_tenure_with_past_record_needs_confirmation():
    # 過去に長期在籍があり勤続条件は満たすが、現職在籍が不明で recency 判定不能 →
    # 自動送信せず要確認に回す（要件1の抜け穴を塞ぐ）。
    result = check_eligibility(make_candidate(current_tenure_years=None,
                                              employments=_LONG_PAST))
    assert not result.eligible
    assert any("現職の在籍年数が不明" in r for r in result.failed)


# --- 転職回数が多い（年代別上限「以上」）は対象外 -----------------------------

def test_job_change_count_helper():
    # prior 3社 + 現職 = 4社 → 転職回数 3。
    c = make_candidate(prior_companies=["A", "B", "C"])
    assert c.job_change_count() == 3


def test_20s_three_changes_fails():
    # 20代（29歳）で転職回数3回（=4社）→ 対象外。
    result = check_eligibility(make_candidate(age=29, current_tenure_years=4.0,
                                              prior_companies=["A", "B", "C"]))
    assert not result.eligible
    assert any("転職回数が多い" in r for r in result.failed)


def test_20s_two_changes_passes():
    # 20代で転職回数2回（=3社）→ 対象。
    result = check_eligibility(make_candidate(age=29, current_tenure_years=4.0,
                                              prior_companies=["A", "B"]))
    assert result.eligible


def test_30s_five_changes_fails_four_passes():
    # 30代（35歳）: 5回以上で対象外。4回はOK。
    fail = check_eligibility(make_candidate(age=35, current_tenure_years=4.0,
                                            prior_companies=["A", "B", "C", "D", "E"]))
    assert not fail.eligible
    assert any("転職回数が多い" in r for r in fail.failed)
    ok = check_eligibility(make_candidate(age=35, current_tenure_years=4.0,
                                          prior_companies=["A", "B", "C", "D"]))
    assert ok.eligible


def test_40s_six_changes_fails_five_passes():
    # 40代以上（42歳＝年齢上限内）: 6回以上で対象外。5回はOK。
    fail = check_eligibility(make_candidate(
        age=42, current_tenure_years=4.0,
        prior_companies=["A", "B", "C", "D", "E", "F"]))
    assert not fail.eligible
    assert any("転職回数が多い" in r for r in fail.failed)
    ok = check_eligibility(make_candidate(
        age=42, current_tenure_years=4.0,
        prior_companies=["A", "B", "C", "D", "E"]))
    assert ok.eligible


def test_job_change_rule_skipped_when_age_unknown():
    # 年齢不明なら年代別の転職回数ルールは適用しない（年齢不明は別条件で拾う）。
    result = check_eligibility(make_candidate(age=None,
                                              prior_companies=["A", "B", "C", "D", "E", "F"]))
    assert not any("転職回数が多い" in r for r in result.failed)


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
