"""ビズリーチAPIのレジュメ→Candidate 変換とヘルパのテスト。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from bizreach_scout.bizreach.api import (
    BizreachApi,
    _extract_languages,
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


def test_overseas_detected_for_katakana_university():
    # カタカナ表記の海外大学（例: スタンフォード大学）も海外として弾く。
    resume = _resume()
    resume["educations"] = [
        {"schoolGrade": "Bachelors", "name": {"ja": "スタンフォード大学", "en": "Stanford University"}},
    ]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert c.overseas_education is True
    result = check_eligibility(c)
    assert not result.eligible
    assert any("海外の教育機関" in r for r in result.failed)


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
    # 職務要約・職歴が英語のみで書かれた候補者（外国人）は③英語優勢で除外される。
    resume = _resume()
    resume["jobSummary"] = {
        "ja": "",
        "en": ("Experienced enterprise sales manager with over ten years leading teams "
               "and closing large deals across the APAC region."),
    }
    resume["coreCompetencies"] = [{"ja": "", "en": "New business development and key account management."}]
    resume["specialInstruction"] = {"ja": "", "en": "Self-motivated professional with strong leadership."}
    resume["companyExperiences"] = [
        {
            "companyName": {"ja": "", "en": "Google LLC"},
            "positionName": {"ja": "", "en": "Sales Manager"},
            "period": {"from": {"year": 2018, "month": 4}, "to": None},
            "companyCareers": [
                {"name": {"ja": "", "en": "Sales Manager"},
                 "contents": {"ja": [""], "en": ["Led the enterprise sales team and exceeded targets."]},
                 "isPresent": True},
            ],
        },
    ]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    result = check_eligibility(c)
    assert not result.eligible
    # ③ 職務要約・職歴がほとんど英語 → 外国人の可能性として除外。
    assert any("英語" in r and "外国人の可能性" in r for r in result.failed)


def test_japanese_resume_with_english_school_name_still_eligible():
    # 日本語のレジュメで、学歴の en 名（Waseda University）が foreign_text に入っても、
    # 本文が日本語主体なら英語優勢とは見なさず対象のまま（誤検出しない）。
    c = resume_to_candidate(_resume(), now=datetime(2026, 7, 1))
    assert "Waseda University" in c.foreign_text
    assert check_eligibility(c).eligible


# --- 語学欄の抽出（フィールド名は実データで確定するまで複数候補を試す）------------

def test_extract_languages_from_dict_entries():
    # {name:{ja/en}, level} 構造（レベルが日本語表記／英語コードのいずれも対応）。
    r = {"languages": [
        {"name": {"ja": "英語", "en": "English"}, "level": "ネイティブ"},
        {"name": {"ja": "日本語"}, "level": "日常会話"},
    ]}
    assert _extract_languages(r) == "英語：ネイティブ、日本語：日常会話"


def test_extract_languages_alternate_keys():
    # 別候補のフィールド名・キー（languageSkills / language / proficiency）でも拾う。
    # 英語 enum のレベル（Native 等）は画面表記へ写像する。
    r = {"languageSkills": [{"language": {"ja": "中国語"}, "proficiency": "Native"}]}
    assert _extract_languages(r) == "中国語：ネイティブレベル"


def test_extract_languages_from_language_code_structure():
    # 実データ（2026-09-09 実行ログ）の構造: [{"languageCode": "EN", "level": "Basic"}]。
    # 言語名が無くコードのみ。コードは日本語名へ、レベル enum は画面表記へ写像する。
    # "None" はレベル未設定（言語名のみ）。
    r = {"languageSkills": [
        {"languageCode": "EN", "level": "Basic"},
        {"languageCode": "JA", "level": "Business"},
        {"languageCode": "HI", "level": "Native"},
        {"languageCode": "FR", "level": "None"},
    ]}
    assert _extract_languages(r) == "英語：基礎レベル、日本語：ビジネス会話レベル、ヒンディー語：ネイティブレベル、フランス語"


def test_extract_languages_unknown_code_kept_verbatim():
    # 未知の言語コードはそのまま残す（落とさない。ログでマッピング追加のサインにする）。
    r = {"languageSkills": [{"languageCode": "XX", "level": "Native"}]}
    assert _extract_languages(r) == "XX：ネイティブレベル"


def test_language_code_structure_drives_foreign_detection():
    # 本番構造の語学欄から「日本語が非ネイティブ」「外国語がネイティブ」を検知できること
    # （従来はこの構造を抽出できず語学欄の判定が一度も効いていなかった）。
    resume = _resume()
    resume["languageSkills"] = [
        {"languageCode": "EN", "level": "Daily"},
        {"languageCode": "JA", "level": "Business"},
        {"languageCode": "ZH", "level": "Native"},
    ]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert c.languages == "英語：日常会話レベル、日本語：ビジネス会話レベル、中国語：ネイティブレベル"
    failed = check_eligibility(c).failed
    assert any("日本語を非ネイティブ" in r for r in failed)
    assert any("外国語がネイティブ" in r for r in failed)


def test_resume_location_maps_to_residence():
    # location は国内なら J+都道府県コード（J13=東京都）。それ以外は海外在住。
    c = resume_to_candidate(_resume(), now=datetime(2026, 7, 1))
    assert c.residence == "J13" and not c.overseas_residence
    resume = _resume()
    resume["location"] = "O05"
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert c.residence == "O05" and c.overseas_residence
    assert any("居住地が海外" in r for r in check_eligibility(c).failed)
    resume["location"] = None
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert c.residence == "" and not c.overseas_residence  # 不明は海外扱いしない


def test_resume_country_tagged_school_is_overseas():
    # 校名に国名タグ「（中国）」／先頭の国名「イギリス 〜」があれば海外の学校（高校を含む）。
    resume = _resume()
    resume["educations"] = [
        {"schoolGrade": "Bachelors", "name": {"ja": "北京物資学院（中国）", "en": None}},
        {"schoolGrade": "HighSchool", "name": {"ja": "北京市第一六一中学（中国）", "en": None}},
    ]
    assert resume_to_candidate(resume, now=datetime(2026, 7, 1)).overseas_education
    resume["educations"] = [
        {"schoolGrade": "Bachelors", "name": {"ja": "イギリス コベントリー大学 ビジネススクール", "en": None}},
    ]
    assert resume_to_candidate(resume, now=datetime(2026, 7, 1)).overseas_education
    resume["educations"] = [
        {"schoolGrade": "Bachelors", "name": {"ja": "中国学園大学", "en": None}},  # 国内（岡山）
    ]
    assert not resume_to_candidate(resume, now=datetime(2026, 7, 1)).overseas_education


def test_resume_japanese_language_school_flag():
    resume = _resume()
    resume["educations"] = [
        {"schoolGrade": "Bachelors", "name": {"ja": "早稲田大学", "en": None}},
        {"schoolGrade": "Other", "name": {"ja": "ISIキャリア外語アカデミー高田馬場校", "en": None}},
    ]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert c.japanese_language_school
    assert any("日本語学校" in r for r in check_eligibility(c).failed)
    resume["educations"] = [{"schoolGrade": "Bachelors", "name": {"ja": "早稲田大学", "en": None}}]
    assert not resume_to_candidate(resume, now=datetime(2026, 7, 1)).japanese_language_school


def test_extract_languages_string_form_and_absent():
    assert _extract_languages({"languages": "英語（ネイティブ）"}) == "英語（ネイティブ）"
    assert _extract_languages({"foo": 1}) == ""  # 語学欄なし


def test_resume_with_native_foreign_language_is_ineligible():
    # 語学欄で外国語がネイティブ → 外国人として除外（本文は日本語のまま）。
    resume = _resume()
    resume["languages"] = [
        {"name": {"ja": "日本語"}, "level": "日常会話"},
        {"name": {"ja": "英語"}, "level": "ネイティブ"},
    ]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert "英語：ネイティブ" in c.languages
    result = check_eligibility(c)
    assert not result.eligible
    assert any("語学欄で外国語がネイティブ" in r for r in result.failed)


def test_resume_with_business_foreign_language_is_eligible():
    # 語学欄で外国語がビジネスレベル、日本語がネイティブ → 対象のまま（除外しない）。
    resume = _resume()
    resume["languages"] = [
        {"name": {"ja": "日本語"}, "level": "ネイティブ"},
        {"name": {"ja": "英語"}, "level": "ビジネスレベル"},
    ]
    c = resume_to_candidate(resume, now=datetime(2026, 7, 1))
    assert check_eligibility(c).eligible


def test_parse_rrsc():
    url = "https://cr-support.jp/scout/highclass/search/?rrsc=3444981"
    assert BizreachApi.parse_rrsc(url) == "3444981"
    assert BizreachApi.parse_rrsc("https://cr-support.jp/x") is None


# --- 希望条件（興味のある働き方）の抽出 ---------------------------------------

def test_extract_desired_reads_work_style_from_desired_conditions():
    from bizreach_scout.bizreach.api import resume_to_candidate

    resume = {
        "bizreachUserId": "BU1", "mrccid": "m1",
        "desiredConditions": {
            "income": "Upper800",
            "workStyles": [{"ja": "転勤なし"}, {"ja": "裁量労働"}],
            "desiredJobCategories": ["経営コンサルタント"],
            "desiredIndustries": [{"ja": "コンサルティング"}],
        },
    }
    cand = resume_to_candidate(resume, "m1")
    assert cand.work_style == "転勤なし、裁量労働"
    assert cand.desired_jobs == "経営コンサルタント"
    assert cand.desired_industries == "コンサルティング"


def test_extract_desired_ignores_experience_fields_at_top_level():
    from bizreach_scout.bizreach.api import resume_to_candidate

    # トップレベルの industries / jobCategories は「経験してきた業界・職種」であり
    # 希望ではない。これを希望として拾うと文面に事実と異なる内容が入るため使わない。
    resume = {
        "bizreachUserId": "BU2", "mrccid": "m2",
        "industries": [{"code": "IT", "yearsOfExperience": 10}],
        "jobCategories": ["営業"],
        "desiredConditions": {"income": "Upper800"},
    }
    cand = resume_to_candidate(resume, "m2")
    assert cand.work_style == ""
    assert cand.desired_jobs == ""
    assert cand.desired_industries == ""


def test_extract_desired_handles_missing_and_odd_shapes():
    from bizreach_scout.bizreach.api import resume_to_candidate

    for resume in (
        {"bizreachUserId": "BU3", "mrccid": "m3"},                       # 希望条件なし
        {"bizreachUserId": "BU4", "mrccid": "m4", "desiredConditions": []},  # 想定外の型
        {"bizreachUserId": "BU5", "mrccid": "m5",
         "desiredConditions": {"workStyle": "リモート可"}},              # 単一文字列
    ):
        cand = resume_to_candidate(resume, resume["mrccid"])
        assert cand is not None
    last = resume_to_candidate(
        {"bizreachUserId": "BU5", "mrccid": "m5",
         "desiredConditions": {"workStyle": "リモート可"}}, "m5")
    assert last.work_style == "リモート可"


def test_extract_desired_uses_production_key_names():
    """本番で確認済みの desiredConditions 実キー（2026-07-28 ログ）から全項目を取る。

    実キー: considerableAbroadEmploymentTypes / income / industries / jobCategories /
    jobChangePeriod / otherDesiredText / workLocations / workStyles
    """
    from bizreach_scout.bizreach.api import resume_to_candidate

    resume = {
        "bizreachUserId": "BU6", "mrccid": "m6",
        # トップレベル（＝経験）は希望として拾わない。
        "industries": [{"ja": "小売"}],
        "jobCategories": [{"ja": "店舗運営"}],
        "desiredConditions": {
            "income": "Upper800",
            "workStyles": [{"ja": "転勤なし"}, {"ja": "フレックス"}],
            "jobCategories": [{"ja": "経営コンサルタント"}],
            "industries": [{"ja": "コンサルティング"}],
            "workLocations": [{"ja": "東京都"}, {"ja": "神奈川県"}],
            "otherDesiredText": "専門性を高められる環境を希望します。",
            "jobChangePeriod": "3ヶ月以内",
        },
    }
    cand = resume_to_candidate(resume, "m6")
    assert cand.work_style == "転勤なし、フレックス"
    assert cand.desired_jobs == "経営コンサルタント"
    assert cand.desired_industries == "コンサルティング"
    assert cand.desired_locations == "東京都、神奈川県"
    assert cand.desired_other == "専門性を高められる環境を希望します。"


def test_extract_desired_truncates_long_free_text():
    from bizreach_scout.bizreach.api import _DESIRED_OTHER_MAX, resume_to_candidate

    cand = resume_to_candidate(
        {"bizreachUserId": "BU7", "mrccid": "m7",
         "desiredConditions": {"otherDesiredText": "あ" * (_DESIRED_OTHER_MAX + 50)}}, "m7")
    assert cand.desired_other == "あ" * _DESIRED_OTHER_MAX + "…"


def test_render_candidate_profile_omits_empty_desired_fields():
    """希望が取れていない項目はプロンプトに出さない（モデルが推測で書くのを防ぐ）。"""
    from bizreach_scout.bizreach.api import resume_to_candidate
    from bizreach_scout.generation.prompt import render_candidate_profile

    blank = render_candidate_profile(
        resume_to_candidate({"bizreachUserId": "BU8", "mrccid": "m8"}, "m8"))
    assert "希望勤務地" not in blank and "その他の希望" not in blank

    filled = render_candidate_profile(resume_to_candidate(
        {"bizreachUserId": "BU9", "mrccid": "m9",
         "desiredConditions": {"workLocations": [{"ja": "大阪府"}],
                               "otherDesiredText": "土日休み希望"}}, "m9"))
    assert "- 希望勤務地: 大阪府" in filled
    assert "- その他の希望（本人記入）: 土日休み希望" in filled


# --- 取り込みカーソル（iter_candidate_ids のページ送り）------------------------


class _FakeClient:
    """human_delay だけを持つ最小スタブ（実待機しない）。"""

    def human_delay(self, *_args, **_kwargs) -> None:
        return None


def _api_with_pages(pages: list[list[str]]) -> tuple[BizreachApi, list[int]]:
    """pages[i] を i+1 ページ目の mrccid 一覧として返す BizreachApi を作る。

    戻り値は (api, 要求されたページ番号の記録リスト)。
    """
    api = BizreachApi.__new__(BizreachApi)
    api.client = _FakeClient()
    requested: list[int] = []

    def fake_search_page(condition, page, page_size=100):
        requested.append(page)
        if page > len(pages):
            return {"items": [], "totalCount": 0, "hasNextPage": False}
        return {
            "items": [{"mrccid": m} for m in pages[page - 1]],
            "totalCount": sum(len(p) for p in pages),
            "hasNextPage": page < len(pages),
        }

    api.get_search_condition = lambda rrsc: {"dummy": True}  # type: ignore[method-assign]
    api.search_page = fake_search_page  # type: ignore[method-assign]
    return api, requested


URL = "https://cr-support.jp/search?rrsc=3444981"


def test_iter_candidate_ids_returns_top_n_without_skip():
    """skip 未指定なら従来どおり先頭から max_candidates 件（1ページ目で足りる）。"""
    api, requested = _api_with_pages([["A", "B", "C"], ["D", "E"]])
    assert list(api.iter_candidate_ids(URL, max_candidates=2)) == ["A", "B"]
    assert requested == [1]  # 足りているので2ページ目は取りに行かない


def test_iter_candidate_ids_advances_past_evaluated_candidates():
    """評価済みを飛ばして次ページへ進み、未評価だけを max_candidates 件返す。

    これが無いと 1714 件の検索でも毎回同じ先頭N件を取り直し、すべて対象外
    スキップされて送信0件が続く（2026-08の本番障害）。
    """
    api, requested = _api_with_pages([["A", "B", "C"], ["D", "E", "F"], ["G", "H"]])
    got = list(api.iter_candidate_ids(URL, max_candidates=3,
                                      skip_mrccids={"A", "B", "C", "D"}))
    assert got == ["E", "F", "G"]
    assert requested == [1, 2, 3]  # 未評価を求めてページを進んでいる


def test_iter_candidate_ids_stops_at_max_pages():
    """全件評価済みでも max_pages で打ち切り、無限巡回しない。"""
    pages = [[f"P{p}C{i}" for i in range(3)] for p in range(10)]
    skip = {m for page in pages for m in page}
    api, requested = _api_with_pages(pages)
    assert list(api.iter_candidate_ids(URL, max_candidates=5,
                                       skip_mrccids=skip, max_pages=4)) == []
    assert requested == [1, 2, 3, 4]


def test_iter_candidate_ids_stops_when_results_exhausted():
    """検索結果を使い切ったら（hasNextPage=False）そこで止まる。"""
    api, requested = _api_with_pages([["A", "B"], ["C", "D"]])
    got = list(api.iter_candidate_ids(URL, max_candidates=10, skip_mrccids={"A"}))
    assert got == ["B", "C", "D"]
    assert requested == [1, 2]
