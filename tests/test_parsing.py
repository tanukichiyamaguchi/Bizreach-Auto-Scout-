from bizreach_scout.ingest.parsing import (
    parse_age,
    parse_education,
    parse_gender,
    parse_member_no,
    parse_years,
    split_companies,
)
from bizreach_scout.models import Education, Gender


def test_parse_member_no():
    assert parse_member_no("会員番号：BU3765516 様") == "BU3765516"
    assert parse_member_no("番号なし") == ""


def test_parse_age():
    assert parse_age("35歳") == 35
    assert parse_age(31) == 31
    assert parse_age("") is None


def test_parse_years():
    assert parse_years("4年") == 4.0
    assert parse_years("3.5") == 3.5
    assert parse_years(None) is None


def test_parse_gender():
    assert parse_gender("男性") == Gender.male
    assert parse_gender("女") == Gender.female
    assert parse_gender("") == Gender.unknown


def test_parse_education_levels():
    assert parse_education("大学院卒") == Education.master
    assert parse_education("早稲田大学 卒業") == Education.bachelor
    assert parse_education("高校卒業") == Education.high_school
    assert parse_education("") == Education.unknown


def test_split_companies():
    assert split_companies("リクルート、ABC社／XYZ") == ["リクルート", "ABC社", "XYZ"]
    assert split_companies([" A ", "B"]) == ["A", "B"]
