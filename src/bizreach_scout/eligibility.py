"""候補者の対象条件（必須要件）判定。

条件: 27歳〜42歳 / 2.5年以上の同じ会社での勤務歴 / 男性 / 大学・大学院卒業以上 /
国内の教育機関出身 / 日本語ネイティブ（の可能性が高い）。
いずれかを満たさない（または判定不能）の場合は eligible=False とし、
完全自動送信からは除外して「要確認」リストへ回す。
"""

from __future__ import annotations

from .config import scout_rules
from .models import Candidate, Education, EligibilityResult, Gender

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

    # --- 学歴（海外の教育機関）は対象外 -----------------------------------------
    # 最終学歴の学校名に日本語表記が無く英語表記のみの場合を海外教育機関とみなす
    # （Bizreach APIのレジュメには学校の所在国フィールドが無いための代替シグナル。
    #  CSV/テキスト取り込みでは判定材料が無いため常に False＝この条件では対象外にしない）。
    if cfg.get("exclude_overseas_education", True) and candidate.overseas_education:
        failed.append("海外の教育機関出身のため対象外")

    # --- 言語（日本語以外がネイティブレベル）は対象外 ----------------------------
    # 日本語検定（JLPT等）の保有を非ネイティブの代替シグナルとして扱う。
    if cfg.get("exclude_non_japanese_native", True) and _has_japanese_proficiency_cert(candidate):
        failed.append("日本語検定の保有により日本語ネイティブでない可能性があるため対象外")

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
