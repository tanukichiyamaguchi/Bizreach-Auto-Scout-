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


def test_resend_subject_requires_its_own_prefix():
    ok = "【どうしても諦めきれず２度目のご連絡です。】貴殿に改めてご連絡しました"
    assert validate_subject(ok, kind="resend") == []
    # 初回の接頭辞では再送として不正
    issues = validate_subject("【Premium Offer】X", kind="resend")
    assert any("で始まっていません" in i for i in issues)


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


def test_allowed_url_passes():
    assert validate_body("詳細はこちら https://www.consuldent.jp/members.html") == []


def test_disallowed_url_flagged():
    issues = validate_body("こちらをご覧ください https://attacker.example/phish")
    assert any("許可されていないURL" in i for i in issues)


def test_star_emoji_detected():
    # ⭐ (U+2B50) は補助記号帯。固定フッターの ↓ (U+2193) とは別。
    issues = validate_body("おすすめ⭐です")
    assert any("絵文字" in i for i in issues)
