"""テスト用の候補者ファクトリ。"""

from __future__ import annotations

from bizreach_scout.models import Candidate, Education, Gender


def make_candidate(**overrides) -> Candidate:
    base = dict(
        member_no="BU3765516",
        age=31,
        gender=Gender.male,
        education=Education.bachelor,
        university="早稲田大学",
        current_company="株式会社サンプル商事",
        current_title="営業課長",
        current_tenure_years=4.0,
        total_experience_years=8.0,
        industry="人材",
        job_function="法人営業",
        prior_companies=["リクルート"],
        salary_current="850万円",
        salary_desired="1000万円以上",
        summary="新規開拓で全社表彰2回。チーム6名のマネジメント経験あり。",
    )
    base.update(overrides)
    return Candidate(**base)
