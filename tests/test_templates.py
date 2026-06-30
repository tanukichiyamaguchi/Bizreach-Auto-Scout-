from bizreach_scout.config import company_config
from bizreach_scout.generation.templates import (
    CASUAL_MEETING,
    NAME_DISCLAIMER,
    NOT_TEMPLATE,
    TAP_GUIDE,
    FirstSections,
    assemble_first_body,
    assemble_resend_body,
    build_footer,
    build_signature,
)


def _sections() -> FirstSections:
    return FirstSections(
        greeting_offer="このたびは限定オファーです。",
        scout_reason="ご経歴に強く惹かれました。",
        company_intro="当社は医院経営に特化したファームです。",
        career_title="入社後のキャリア",
        career_body="2年目で年収990万円も射程です。",
        position_title="ポジションの魅力",
        position_body="経営全般に深く関与できます。",
    )


def test_first_body_contains_all_fixed_blocks():
    body = assemble_first_body("BU3765516", _sections(), company_config())
    assert NOT_TEMPLATE in body
    assert "BU3765516様" in body
    assert NAME_DISCLAIMER in body
    assert CASUAL_MEETING in body
    assert TAP_GUIDE in body
    assert "このスカウトのポイント" in body
    assert "弊社について" in body


def test_header_has_no_linebreak_between_marker_and_member_no():
    body = assemble_first_body("BU3765516", _sections(), company_config())
    assert f"{NOT_TEMPLATE}BU3765516様" in body


def test_signature_from_company_config():
    sig = build_signature(company_config())
    assert "経営戦略研究所株式会社" in sig
    assert "岩渕" in sig


def test_footer_identical_for_first_and_resend():
    footer = build_footer(company_config())
    first = assemble_first_body("BU1", _sections(), company_config())
    resend = assemble_resend_body("BU1", "先日はご連絡しました。改めてご案内です。", company_config())
    assert footer in first
    assert footer in resend


def test_no_forbidden_kagikakko_in_assembled_body():
    body = assemble_first_body("BU1", _sections(), company_config())
    # 「」（カギ括弧）は固定要素に含まれない。
    assert "「" not in body
    assert "」" not in body


def test_resend_body_includes_header_and_footer():
    resend = assemble_resend_body("BU9", "再度のご連絡です。", company_config())
    assert "BU9様" in resend
    assert "弊社について" in resend
    assert build_signature(company_config()) in resend
