from pathlib import Path

from bizreach_scout.ingest.csv_source import CSVSource
from bizreach_scout.ingest.text_source import TextSource, parse_profile_text
from bizreach_scout.models import Education, Gender

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_csv_source_reads_candidates():
    candidates = list(CSVSource(EXAMPLES / "sample_candidates.csv"))
    assert len(candidates) == 3
    first = candidates[0]
    assert first.member_no == "BU3765516"
    assert first.age == 31
    assert first.gender == Gender.male
    assert first.education == Education.bachelor
    assert "リクルート" in first.prior_companies


def test_text_source_single_profile():
    text = (EXAMPLES / "sample_profile.txt").read_text(encoding="utf-8")
    candidates = list(TextSource(text))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.member_no == "BU3765516"
    assert c.age == 35
    assert c.gender == Gender.male
    assert c.raw_profile  # 生プロフィールを保持


def test_text_source_splits_multiple_profiles():
    text = "会員番号：BU1111111 男性 30歳\n---\n会員番号：BU2222222 男性 33歳"
    candidates = list(TextSource(text))
    assert {c.member_no for c in candidates} == {"BU1111111", "BU2222222"}


def test_parse_profile_text_without_member_no_returns_none():
    assert parse_profile_text("会員番号なしのテキスト") is None
