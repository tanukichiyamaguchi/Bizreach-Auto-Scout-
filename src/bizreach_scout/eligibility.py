"""候補者の対象条件（必須要件）判定。

条件: 27歳〜42歳 / 2.5年以上の同じ会社での勤務歴 / 男性 / 大学・大学院卒業以上 /
日本人（日本語ネイティブの可能性が高い）。
いずれかを満たさない（または判定不能）の場合は eligible=False とし、
完全自動送信からは除外して「要確認」リストへ回す。

職歴の種類（詳細は career_types.py。通常スカウト・ピックアップの両方）:
- 現職が個人事業主 / キャリアの半分以上が契約社員 / 半分以上が工場・生産部門

勤務条件の判定（詳細は work_conditions.py）:
- 既定で 出張不可 / 残業なし希望 / 在宅勤務希望（転勤NGは自社求人が「転勤なし」のため既定で無効）
  （本人が希望として書いた欄のみを走査。「リモート可」等の“許容”は除外しない）

外国人の判定（詳細は foreign.py）:
- 海外の学校卒・高校を含む（overseas_education）
- 日本語学校（留学生向け日本語課程）の学歴（japanese_language_school）
- 日本語ネイティブでない: 日本語検定の保有／語学欄で日本語が非ネイティブ／外国語がネイティブ
  （語学の判定は**語学欄のみ**を対象にする）
- 職務要約・職歴がほとんど英語
- 永住権・在留資格・ビザに関する本人の申告
- 居住地が海外（overseas_residence）
"""

from __future__ import annotations

from .career_types import contract_ratio, factory_ratio, is_current_self_employed
from .config import scout_rules
from .foreign import (
    has_foreign_native_language,
    has_residency_status_mention,
    is_english_dominant,
    japanese_listed_as_non_native,
)
from .models import Candidate, Education, EligibilityResult, Gender
from .work_conditions import DEFAULT_SIGNALS as DEFAULT_WORK_CONDITION_SIGNALS
from .work_conditions import unwanted_work_conditions

_EDU_MAP = {
    "high_school": Education.high_school,
    "vocational": Education.vocational,
    "associate": Education.associate,
    "bachelor": Education.bachelor,
    "master": Education.master,
    "doctor": Education.doctor,
}

# 日本語検定（JLPT等）の保有を「日本語ネイティブでない」ことの代替シグナルとして扱う
# キーワード（レジュメには母語・ネイティブ言語のフィールドが無いための代替判定）。
_JAPANESE_CERT_KEYWORDS_JA = ("日本語能力試験", "日本語検定")
_JAPANESE_CERT_KEYWORD_EN = "JLPT"


def _has_japanese_proficiency_cert(candidate: Candidate) -> bool:
    """日本語検定（JLPT等）の保有をテキストから検知する。

    日本語ネイティブは通常この種の検定を受けない前提の代替シグナル。
    raw_profile（Bizreach APIでは資格欄を含む）・summary・languages を横断して検索する。
    """
    haystack = "\n".join([candidate.raw_profile, candidate.summary, candidate.languages])
    if any(k in haystack for k in _JAPANESE_CERT_KEYWORDS_JA):
        return True
    return _JAPANESE_CERT_KEYWORD_EN in haystack.upper()


def check_eligibility(candidate: Candidate, rules: dict | None = None,
                      apply_status_filter: bool = True) -> EligibilityResult:
    """対象条件を判定する。

    apply_status_filter=False の場合は会員ステータス条件（新着/更新/HOT/WILL/premium）を
    適用しない。ピックアップ求人はビズリーチが選抜した本命リストのため、ステータス不問で
    送る運用（ユーザー指定）。年齢・性別・学歴・勤続などのコア条件は常に適用する。
    """
    cfg = (rules or scout_rules()).get("eligibility", {})
    failed: list[str] = []

    # --- 年齢 -----------------------------------------------------------------
    # 対象は min_age 歳〜max_age 歳（両端含む）。max_age は 43歳以上を除外＝上限42歳。
    min_age = cfg.get("min_age", 27)
    max_age = cfg.get("max_age")
    if candidate.age is None:
        failed.append("年齢が不明（要確認）")
    elif candidate.age < min_age:
        failed.append(f"年齢が{min_age}歳未満（{candidate.age}歳）")
    elif max_age is not None and candidate.age > max_age:
        failed.append(f"年齢が{max_age}歳超過（{candidate.age}歳）")

    # --- 性別 -----------------------------------------------------------------
    required_gender = cfg.get("required_gender", "male")
    if required_gender:
        if candidate.gender is Gender.unknown:
            failed.append("性別が不明（要確認）")
        elif candidate.gender.value != required_gender:
            failed.append(f"性別が条件({required_gender})と不一致（{candidate.gender.value}）")

    # --- 学歴 -----------------------------------------------------------------
    min_edu = _EDU_MAP.get(cfg.get("required_education", "bachelor"), Education.bachelor)
    if candidate.education is Education.unknown:
        failed.append("学歴が不明（要確認）")
    elif not candidate.education.meets(min_edu):
        failed.append(f"学歴が{min_edu.value}未満（{candidate.education.value}）")

    # --- 海外の学校卒（高校を含む・外国人の可能性）は対象外 ----------------------
    # いずれかの学歴の学校名が日本語表記でない（ja が空で en のみ／ラテン文字のみ／カタカナ主体）
    # か、国名タグ付き「〜（中国）」「イギリス 〜大学」の場合を海外の学校とみなす（所在国
    # フィールドが無いための代替シグナル。CSV/テキスト取り込みでは判定材料が無いため常に
    # False＝この条件では対象外にしない）。
    if cfg.get("exclude_overseas_education", True) and candidate.overseas_education:
        failed.append("海外の教育機関（高校・大学等）出身のため対象外（外国人の可能性）")

    # --- 日本語学校（留学生向け日本語課程）の学歴は対象外 ------------------------
    # 「ISIキャリア外語アカデミー」「A.C.C.国際交流学園 日本語学校」等。日本語ネイティブは在籍しない。
    if cfg.get("exclude_japanese_language_school", True) and candidate.japanese_language_school:
        failed.append("日本語学校（留学生向け日本語課程）の学歴があるため対象外（外国人の可能性）")

    # --- 日本語ネイティブでない（外国人の可能性）は対象外 --------------------------
    # 語学の判定は**語学（言語）欄のみ**を対象にする（本文全体で「ネイティブ」を拾わない）。
    # 該当する理由は全て列挙する（要確認リストで根拠が分かるように）。
    if cfg.get("exclude_non_japanese_native", True):
        if _has_japanese_proficiency_cert(candidate):
            failed.append("日本語検定の保有により日本語ネイティブでない可能性があるため対象外")
        # 日本語ネイティブは語学欄に日本語を書かないか「ネイティブ」と書く。
        # 「日本語：ビジネス会話レベル／日常会話レベル」は外国人がほぼ確実。
        if japanese_listed_as_non_native(candidate.languages):
            failed.append("語学欄で日本語を非ネイティブ（ビジネス会話レベル等）と申告のため対象外（外国人の可能性）")
        if has_foreign_native_language(candidate.languages):
            failed.append("語学欄で外国語がネイティブと申告のため対象外（外国人の可能性）")

    # --- 職務要約・職歴がほとんど英語（外国人の可能性）は対象外 ------------------
    if cfg.get("exclude_english_resume", True) and is_english_dominant(
        "\n".join([candidate.summary, candidate.raw_profile, candidate.foreign_text])
    ):
        failed.append("職務要約・職歴がほとんど英語で記載のため対象外（外国人の可能性）")

    # --- 永住権・在留資格・ビザに関する本人の申告は対象外 ------------------------
    # 「永住権取得済」「ビザサポート不要」等、本人の在留状況として書く言い回しのみ拾う。
    if cfg.get("exclude_residency_status_mention", True) and has_residency_status_mention(
        "\n".join([candidate.summary, candidate.raw_profile, candidate.foreign_text])
    ):
        failed.append("永住権・在留資格・ビザに関する自己申告があるため対象外（外国人の可能性）")

    # --- 居住地が海外は対象外 ---------------------------------------------------
    # API の居住地コードが国内の都道府県（J01〜J47）でない場合（例: アメリカ・カナダ在住）。
    if cfg.get("exclude_overseas_residence", True) and candidate.overseas_residence:
        where = f"（{candidate.residence}）" if candidate.residence else ""
        failed.append(f"居住地が海外{where}のため対象外（海外在住・外国人の可能性）")

    # --- 勤務条件の希望が自社求人と合わない（転勤NG/出張不可/残業なし/在宅希望）----
    # 走査するのは本人が「希望」として書いた欄だけ（職務要約・自己PR は見ない）。
    # desired_other はプロンプト用に400字で切り詰められるため、判定には全文を使う。
    if cfg.get("exclude_unwanted_work_conditions", True):
        wish = "\n".join([
            candidate.work_style,
            candidate.desired_other_full or candidate.desired_other,
            candidate.desired_locations,
        ])
        failed.extend(unwanted_work_conditions(
            wish, cfg.get("unwanted_work_conditions", DEFAULT_WORK_CONDITION_SIGNALS)))

    # --- 職歴の種類（現職が個人事業主／契約社員中心／工場・生産部門中心）-----------
    # 通常スカウトとピックアップの両方に効かせるため apply_status_filter に依存させない。
    # 割合は在籍期間の合計に占める割合（社数ではない）。期間が分からなければ判定しない。
    if cfg.get("exclude_current_self_employed", True) and is_current_self_employed(candidate):
        failed.append("現職が個人事業主（フリーランス・自営・業務委託）のため対象外")
    for key, ratio_fn, label in (
        ("contract_career_ratio_exclude", contract_ratio, "契約社員"),
        ("factory_career_ratio_exclude", factory_ratio, "工場・生産部門"),
    ):
        limit = cfg.get(key)
        if limit is None:
            continue
        ratio = ratio_fn(candidate)
        if ratio is not None and ratio >= limit:
            failed.append(f"キャリアの{ratio:.0%}が{label}のため対象外（{limit:.0%}以上）")

    # --- 同一企業での勤続年数 -------------------------------------------------
    min_years = cfg.get("min_same_company_years", 2.5)
    tenure = candidate.max_single_tenure_years()
    if tenure is None:
        failed.append("勤続年数が不明（要確認）")
    elif tenure < min_years:
        failed.append(f"同一企業での勤続が{min_years}年未満（{tenure}年）")

    # --- 直近◯年以内の転職（現職の在籍が短い）は対象外 -------------------------
    # 現職在籍が min_current_tenure_years 未満 = 直近に転職した人として除外。
    min_cur = cfg.get("min_current_tenure_years")
    if min_cur:
        cur = candidate.current_tenure_years
        if cur is None:
            # 現職在籍が不明 = 直近に転職したか判定不能。過去在籍から max_single_tenure が
            # 取れて「勤続年数が不明」で拾えない場合のみ、判定不能→要確認として回す
            # （API経路では現職の期間欠損でもここに落ちる）。
            if tenure is not None:
                failed.append("現職の在籍年数が不明（要確認）")
        elif cur < min_cur:
            failed.append(f"直近{min_cur}年以内の転職（現職{cur}年）")

    # --- 転職回数が多い（年代別の上限「以上」）は対象外 -------------------------
    # 例: 20代=3回以上 / 30代=5回以上 / 40代以上=6回以上。転職回数 = 勤務先数 - 1。
    brackets = cfg.get("job_changes_exclude", []) or []
    if brackets and candidate.age is not None:
        changes = candidate.job_change_count()
        for b in brackets:
            lo = b.get("age_min", 0)
            hi = b.get("age_max", 200)
            if lo <= candidate.age <= hi:
                limit = b.get("count")
                if limit is not None and changes >= limit:
                    failed.append(f"転職回数が多い（{changes}回・{limit}回以上は対象外）")
                break

    # --- 会員ステータス（新着/更新/HOT/WILL/プレミアムのいずれか）-------------
    # ピックアップ求人では適用しない（apply_status_filter=False）。
    require_status = cfg.get("require_any_status", []) or []
    if apply_status_filter and require_status:
        matched = candidate.status_flags() & set(require_status)
        if not matched:
            _labels = {"new": "新着", "updated": "更新", "hot": "HOT",
                       "will": "WILL", "premium": "プレミアム"}
            need = "/".join(_labels.get(s, s) for s in require_status)
            failed.append(f"会員ステータスが対象外（{need} のいずれにも該当せず）")

    return EligibilityResult(eligible=not failed, failed=failed)
