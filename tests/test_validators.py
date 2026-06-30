from bizreach_scout.generation.validators import (
    validate_body,
    validate_subject,
)


def test_subject_requires_premium_offer_prefix():
    assert validate_subject("【Premium Offer】貴殿の経験に惹かれて") == []
    issues = validate_subject("貴殿の経験に惹かれて")
    assert any("Premium Offer" in i for i in issues)


def test_subject_rejects_extra_brackets():
    issues = validate_subject("【Premium Offer】【特別】オファー")
    assert any("【】" in i for i in issues)


def test_body_rejects_kagikakko():
    issues = validate_body("これは「テスト」です。")
    assert any("「" in i or "禁止" in i for i in issues)


def test_body_rejects_decorative_dash():
    issues = validate_body("素晴らしい――まさに理想的です。")
    assert any("――" in i for i in issues)


def test_body_rejects_too_many_exclamations():
    issues = validate_body("すごい！本当に！最高です！")
    assert any("感嘆符" in i for i in issues)


def test_body_rejects_bizreach_preface():
    issues = validate_body("ビズリーチにてご経歴を拝見しました。")
    assert any("ビズリーチにて" in i for i in issues)


def test_clean_body_passes():
    assert validate_body("ご経歴を拝見し、ぜひお話ししたく存じます。") == []
