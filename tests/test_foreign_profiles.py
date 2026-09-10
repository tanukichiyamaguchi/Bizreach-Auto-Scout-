"""外国人判定の実例テスト（2026-09 運用者提示の4名のレジュメ構造を再現）。

運用者が「これらは全員外国人」と提示した4名のプロフィール（画面表示）を、Bizreach API の
レジュメ JSON 構造に落として固定する。いずれも自動送信から除外され、除外理由に
「外国人」の根拠が含まれることを保証する。あわせて、日本人（海外経験のある人を含む）が
誤って除外されないことも固定する。
"""

from __future__ import annotations

from datetime import datetime

from bizreach_scout.bizreach.api import resume_to_candidate
from bizreach_scout.eligibility import check_eligibility

NOW = datetime(2026, 9, 10)


def _lang(code: str, level: str) -> dict:
    return {"languageCode": code, "level": level}


def _edu(grade: str, ja: str, en: str | None = None) -> dict:
    return {"schoolGrade": grade, "name": {"ja": ja, "en": en}}


def _company(name: str, title: str, y_from: int, m_from: int,
             y_to: int | None = None, m_to: int | None = None) -> dict:
    return {
        "companyName": {"ja": name, "en": None},
        "positionName": {"ja": title, "en": None},
        "period": {"from": {"year": y_from, "month": m_from},
                   "to": None if y_to is None else {"year": y_to, "month": m_to}},
        "companyCareers": [],
    }


def _base(member_no: str, age: int, **over) -> dict:
    r = {
        "bizreachUserId": member_no,
        "mrccid": f"mrc{member_no}",
        "age": age,
        "gender": "Male",
        "location": "J13",
        "intention": ["Will"],
        "candidateClass": "Talent",
        "jobSummary": {"ja": "法人営業・事業開発を担当。新規開拓と既存深耕の両輪で成果を上げてきました。", "en": ""},
        "specialInstruction": {"ja": "", "en": ""},
    }
    r.update(over)
    return r


def _reasons(resume: dict) -> list[str]:
    c = resume_to_candidate(resume, now=NOW)
    assert c is not None
    return check_eligibility(c).failed


# --- 実例1: BU07526501 ------------------------------------------------------
# 学歴: 大学卒 / カラチ大学。言語: 英語ネイティブ・ヒンディー語ネイティブ・日本語ビジネス会話。
def test_profile_karachi_university_hindi_native():
    r = _base("BU07526501", 40,
              educations=[_edu("Bachelors", "カラチ大学 数学/統計/コンピュータサイエンス")],
              languageSkills=[_lang("EN", "Native"), _lang("HI", "Native"), _lang("JA", "Business")],
              companyExperiences=[
                  _company("株式会社エルテス", "新規事業マネジャー", 2020, 5),
                  _company("株式会社Psychic VR Lab", "グローバル事業マネジャー", 2018, 2, 2020, 4),
                  _company("アレムジャパン株式会社", "営業・IT事業マネジャー", 2015, 10, 2018, 1),
                  _company("Standard Chartered Bank", "リスク管理部長", 2005, 6, 2013, 10),
              ])
    reasons = _reasons(r)
    assert any("海外の教育機関" in x for x in reasons)           # カラチ大学
    assert any("日本語を非ネイティブ" in x for x in reasons)     # 日本語：ビジネス会話レベル
    assert any("外国語がネイティブ" in x for x in reasons)       # 英語／ヒンディー語ネイティブ


# --- 実例2: BU08943408 ------------------------------------------------------
# 中国の企業のみ在籍。学歴: ISIキャリア外語アカデミー／北京物資学院（中国）／北京市第一六一中学（中国）。
# 言語: 英語日常会話・日本語ビジネス会話・北京語ネイティブ。
def test_profile_beijing_isi_mandarin_native():
    r = _base("BU08943408", 39,
              educations=[
                  _edu("Other", "ISIキャリア外語アカデミー高田馬場校"),
                  _edu("Bachelors", "北京物資学院（中国） 会計学科 資産評価専攻"),
                  _edu("HighSchool", "北京市第一六一中学（中国）"),
              ],
              languageSkills=[_lang("EN", "Daily"), _lang("JA", "Business"), _lang("ZH_CN", "Native")],
              companyExperiences=[
                  _company("北京金智創新科技有限公司（中国）", "産業リサーチャー", 2023, 7, 2024, 10),
                  _company("北京渓流財富投資管理有限公司（中国）", "投資マネージャー", 2018, 12, 2023, 5),
                  _company("北京華諾信誠財務顧問有限公司（中国）", "M&Aアソシエイトマネージャー", 2017, 9, 2018, 11),
                  _company("中聯資産評価グループ有限公司（中国）", "資産評価プロジェクトマネージャー", 2014, 7, 2017, 8),
              ])
    reasons = _reasons(r)
    assert any("海外の教育機関" in x for x in reasons)           # 国名タグ（中国）の大学・高校
    assert any("日本語学校" in x for x in reasons)               # ISI〜
    assert any("日本語を非ネイティブ" in x for x in reasons)     # 日本語：ビジネス会話レベル
    assert any("外国語がネイティブ" in x for x in reasons)       # 北京語ネイティブ


def test_profile_beijing_each_signal_is_independent():
    # 各シグナルが単独でも除外に足ることを確認する（他の根拠を消しても除外される）。
    base = dict(
        companyExperiences=[_company("北京金智創新科技有限公司（中国）", "リサーチャー", 2018, 7)],
    )
    # 高校だけ海外（国名タグ）
    r = _base("BU08943408", 39, educations=[_edu("Bachelors", "早稲田大学"),
                                            _edu("HighSchool", "北京市第一六一中学（中国）")],
              languageSkills=[_lang("EN", "Daily")], **base)
    assert any("海外の教育機関" in x for x in _reasons(r))
    # 日本語学校だけ
    r = _base("BU08943408", 39, educations=[_edu("Bachelors", "早稲田大学"),
                                            _edu("Other", "ISIキャリア外語アカデミー高田馬場校")],
              languageSkills=[_lang("EN", "Daily")], **base)
    assert any("日本語学校" in x for x in _reasons(r))
    # 日本語が非ネイティブだけ
    r = _base("BU08943408", 39, educations=[_edu("Bachelors", "早稲田大学")],
              languageSkills=[_lang("JA", "Business")], **base)
    assert any("日本語を非ネイティブ" in x for x in _reasons(r))
    # 北京語ネイティブだけ
    r = _base("BU08943408", 39, educations=[_edu("Bachelors", "早稲田大学")],
              languageSkills=[_lang("ZH_CN", "Native")], **base)
    assert any("外国語がネイティブ" in x for x in _reasons(r))


# --- 実例3: BU09074407 ------------------------------------------------------
# 学歴: 桜美林大学 MBA／A.C.C.国際交流学園 日本語学校／イギリス コベントリー大学。
# 言語: 英語ビジネス会話・日本語ビジネス会話・北京語ネイティブ。
def test_profile_coventry_acc_japanese_school():
    r = _base("BU09074407", 38,
              educations=[
                  _edu("MBA", "桜美林大学 国際学術研究科／博士前期課程／修士（経営学）"),
                  _edu("Other", "A.C.C.国際交流学園 日本語学校"),
                  _edu("Bachelors", "イギリス コベントリー大学 ビジネススクール／ビジネス・マーケティング"),
              ],
              languageSkills=[_lang("EN", "Business"), _lang("JA", "Business"), _lang("ZH_CN", "Native")],
              companyExperiences=[_company("金発科技有限公司", "営業エンジニア", 2014, 9, 2019, 4)])
    reasons = _reasons(r)
    assert any("海外の教育機関" in x for x in reasons)           # イギリス コベントリー大学
    assert any("日本語学校" in x for x in reasons)               # A.C.C.国際交流学園 日本語学校
    assert any("日本語を非ネイティブ" in x for x in reasons)
    assert any("外国語がネイティブ" in x for x in reasons)


# --- 実例4: BU03802305 ------------------------------------------------------
# 居住地: アメリカ・カナダ。在籍企業は海外（カナダ）の企業が中心。学歴は亜細亜大学（国内）。
# 言語: 英語ビジネス会話のみ（日本語の申告なし）。→ 居住地が海外で除外する。
def test_profile_overseas_resident_is_excluded():
    r = _base("BU03802305", 27, location="O02",  # 国内都道府県コード(J01〜J47)以外
              educations=[
                  _edu("Other", "グレイストーンカレッジ デジタルマーケティングプロフェッショナル"),
                  _edu("Bachelors", "亜細亜大学 国際関係学部多文化コミュニケーション学科"),
              ],
              languageSkills=[_lang("EN", "Business")],
              companyExperiences=[
                  _company("Teranishi & Associates Chartered Professional Accountants Ltd.", "総務部", 2024, 9),
                  _company("AK JAPAN IMPORTS Inc.", "営業部/マーケティング部", 2024, 10, 2025, 10),
                  _company("International House Vancouver", "マーケティング/営業", 2024, 4, 2024, 9),
                  _company("Moltaqa morrocan restaurant", "バーテンダー/マネージャー", 2022, 12, 2024, 3),
                  _company("佐藤商事株式会社", "鉄鋼部第一課/営業", 2021, 4, 2022, 6),
              ])
    c = resume_to_candidate(r, now=NOW)
    assert c.overseas_residence and c.residence == "O02"
    reasons = check_eligibility(c).failed
    assert any("居住地が海外" in x for x in reasons)
    # グレイストーンカレッジ（カタカナ主体の校名）も海外の学校として拾う。
    assert any("海外の教育機関" in x for x in reasons)


# --- 対照: 日本人が誤って除外されないこと --------------------------------------
def test_japanese_native_with_english_business_passes():
    r = _base("BU1000001", 33,
              educations=[_edu("Bachelors", "早稲田大学", "Waseda University"),
                          _edu("HighSchool", "東京都立日比谷高等学校")],
              languageSkills=[_lang("EN", "Business")],
              companyExperiences=[_company("株式会社サンプル商事", "営業課長", 2018, 4)])
    c = resume_to_candidate(r, now=NOW)
    assert not c.overseas_education and not c.japanese_language_school and not c.overseas_residence
    assert check_eligibility(c).eligible


def test_japanese_native_listing_japanese_as_native_passes():
    # 語学欄に「日本語：ネイティブ」を書く日本人は除外しない。
    r = _base("BU1000002", 33,
              educations=[_edu("Bachelors", "慶應義塾大学")],
              languageSkills=[_lang("JA", "Native"), _lang("EN", "Business"), _lang("ZH", "Daily")],
              companyExperiences=[_company("株式会社サンプル商事", "営業課長", 2018, 4)])
    assert check_eligibility(resume_to_candidate(r, now=NOW)).eligible


def test_japanese_expat_at_chinese_subsidiary_passes():
    # 日系企業の中国現地法人に駐在中の日本人（学歴は国内・語学は英語/中国語ビジネス・居住地は国内登録）。
    # 企業名の国名タグ「（中国）」だけでは除外しない（所属先の所在国は国籍の根拠にならない）。
    r = _base("BU1000003", 36,
              educations=[_edu("Bachelors", "中国学園大学")],  # 校名に「中国」を含む国内大学
              languageSkills=[_lang("EN", "Business"), _lang("ZH", "Business")],
              companyExperiences=[_company("トヨタ自動車（中国）投資有限公司", "営業部長", 2019, 4)])
    c = resume_to_candidate(r, now=NOW)
    assert not c.overseas_education
    assert check_eligibility(c).eligible


def test_japanese_teacher_major_not_treated_as_language_school():
    # 「日本語教育」を専攻した日本人（日本語教師志望）は日本語学校扱いにしない。
    r = _base("BU1000004", 30,
              educations=[_edu("Masters", "早稲田大学大学院 日本語教育研究科"),
                          _edu("Bachelors", "早稲田大学 日本語日本文学科")],
              languageSkills=[_lang("EN", "Daily")],
              companyExperiences=[_company("株式会社サンプル商事", "営業", 2020, 4)])
    c = resume_to_candidate(r, now=NOW)
    assert not c.japanese_language_school
    assert check_eligibility(c).eligible
