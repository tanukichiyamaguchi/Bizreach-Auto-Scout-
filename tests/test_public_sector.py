"""キャリアの中に公務員がある人の除外（career_types.is_public_sector）のテスト。

運用者指示（2026-11）: キャリアの中に公務員がある人は、通常スカウト・ピックアップの両方で除外する
（期間・現職か過去かを問わない）。独立行政法人・国立大学法人・民営化した会社は法人で、
職員は公務員ではないため含めない。役職欄の「防衛省向け営業」のような仕事の対象も数えない。
"""

from __future__ import annotations

import pytest

from bizreach_scout.career_types import has_public_sector_career, is_public_sector
from bizreach_scout.eligibility import check_eligibility
from bizreach_scout.models import Employment, EmploymentSegment

from .factories import make_candidate

PUBLIC = [
    ("横浜市", "こども青少年局 主事"), ("横浜市役所", "税務課"), ("神奈川県", ""), ("神奈川県庁", ""),
    ("東京都 産業労働局", ""), ("世田谷区", ""), ("大槌町役場", ""), ("経済産業省", "課長補佐"),
    ("警視庁", "巡査部長"), ("大阪府警察", ""), ("陸上自衛隊", ""), ("防衛省 陸上自衛隊", "2等陸曹"),
    ("横浜市立みなと中学校", "教諭"), ("東京都立駒込病院", "看護師"), ("XX広域消防組合", "消防士"),
    ("東京地方裁判所", "書記官"), ("渋谷税務署", ""), ("官公庁", "事務"),
    ("-", "地方公務員（市役所）"), ("非公開", "市役所職員"),
    ("株式会社X", "元自衛官"), ("株式会社X", "法人営業（前職：横浜市役所）"),
]
NOT_PUBLIC = [
    ("株式会社X", "防衛省向け営業"), ("株式会社X", "官公庁営業"), ("株式会社X", "自治体向けSaaSの営業"),
    ("株式会社X", "公務員試験講師"), ("株式会社X", "経済産業省へ出向"),
    ("独立行政法人国際協力機構", ""), ("国立大学法人東京大学", "事務職員"), ("日本郵便株式会社", ""),
    ("東日本旅客鉄道株式会社", "鉄道職員"), ("株式会社X", "警察官採用試験対策講師"),
    ("株式会社NTTデータ", "公共事業本部 自治体担当"), ("地方独立行政法人XX市民病院", "看護師"),
    ("公立大学法人横浜市立大学", ""), ("在日米国大使館", ""), ("株式会社東京都民銀行", ""),
    ("東京大学", "研究員"), ("株式会社X", "防衛省・自衛隊向け営業"),
]


@pytest.mark.parametrize("company,title", PUBLIC)
def test_public_sector(company, title):
    assert is_public_sector(company, title)


@pytest.mark.parametrize("company,title", NOT_PUBLIC)
def test_not_public_sector(company, title):
    assert not is_public_sector(company, title)


def _job(company, title, years, *, current=False, segments=()):
    return Employment(company=company, title=title, years=years, is_current=current,
                      segments=[EmploymentSegment(name=n, years=y) for n, y in segments])


REASON = "職歴に公務員（官公庁・自治体・警察・消防・自衛隊・公立学校など）があるため対象外"


def test_past_public_sector_job_is_excluded_regardless_of_length():
    """過去に半年だけでも公務員だった人は対象外（期間を問わない）。"""
    c = make_candidate(employments=[
        _job("株式会社A", "法人営業", 8.0, current=True),
        _job("横浜市役所", "税務課", 0.5),
    ])
    assert has_public_sector_career(c)
    assert REASON in check_eligibility(c).failed


def test_applies_to_pickup_too():
    c = make_candidate(employments=[_job("神奈川県庁", "主事", 5.0, current=True)])
    assert REASON in check_eligibility(c, apply_status_filter=False).failed
    assert REASON in check_eligibility(c, apply_status_filter=True).failed


def test_segment_name_is_checked():
    c = make_candidate(employments=[
        _job("-", "", 6.0, current=True, segments=[("横浜市役所 税務課", 3.0), ("総務課", 3.0)]),
    ])
    assert has_public_sector_career(c)


def test_customer_mentions_do_not_exclude():
    c = make_candidate(employments=[_job("株式会社X", "官公庁・自治体向けSaaSの営業", 5.0, current=True)])
    assert not has_public_sector_career(c)
    assert REASON not in check_eligibility(c).failed


def test_falls_back_to_company_names_without_employment_history():
    assert has_public_sector_career(make_candidate(
        current_company="株式会社A", prior_companies=["横浜市役所"], employments=[]))
    assert not has_public_sector_career(make_candidate(employments=[]))


def test_can_be_disabled():
    from bizreach_scout.config import scout_rules

    rules = scout_rules()
    c = make_candidate(employments=[_job("神奈川県庁", "主事", 5.0, current=True)])
    off = {**rules, "eligibility": {**rules["eligibility"], "exclude_public_sector_career": False}}
    assert REASON not in check_eligibility(c, rules=off).failed
