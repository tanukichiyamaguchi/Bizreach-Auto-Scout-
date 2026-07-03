"""候補者の対象条件（必須要件）判定。

条件: 27歳以上 / 3年以上の同じ会社での勤務歴 / 男性 / 大学・大学院卒業以上。
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


def check_eligibility(candidate: Candidate, rules: dict | None = None) -> EligibilityResult:
    cfg = (rules or scout_rules()).get("eligibility", {})
    failed: list[str] = []

    # --- 年齢 -----------------------------------------------------------------
    min_age = cfg.get("min_age", 27)
    if candidate.age is None:
        failed.append("年齢が不明（要確認）")
    elif candidate.age < min_age:
        failed.append(f"年齢が{min_age}歳未満（{candidate.age}歳）")

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

    # --- 同一企業での勤続年数 -------------------------------------------------
    min_years = cfg.get("min_same_company_years", 3)
    tenure = candidate.max_single_tenure_years()
    if tenure is None:
        failed.append("勤続年数が不明（要確認）")
    elif tenure < min_years:
        failed.append(f"同一企業での勤続が{min_years}年未満（{tenure}年）")

    # --- 会員ステータス（新着/更新/HOT/WILL/プレミアムのいずれか）-------------
    require_status = cfg.get("require_any_status", []) or []
    if require_status:
        matched = candidate.status_flags() & set(require_status)
        if not matched:
            _labels = {"new": "新着", "updated": "更新", "hot": "HOT",
                       "will": "WILL", "premium": "プレミアム"}
            need = "/".join(_labels.get(s, s) for s in require_status)
            failed.append(f"会員ステータスが対象外（{need} のいずれにも該当せず）")

    return EligibilityResult(eligible=not failed, failed=failed)
