"""scout_rules の型付き検証（タイポ即死化・振る舞い不変）を検証。"""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from bizreach_scout.config import project_root, scout_rules
from bizreach_scout.rules import ScoutRules, validate_rules


def _raw_yaml() -> dict:
    path = project_root() / "config" / "scout_rules.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_current_yaml_validates():
    """出荷中の scout_rules.yaml が検証を通る。"""
    validate_rules(_raw_yaml())


def test_validation_is_behavior_preserving():
    """検証・正規化後の dict が raw YAML と完全一致する（振る舞い不変の保証）。

    これが崩れると eligibility 等の判定が変わり得るため、最重要の回帰テスト。
    """
    raw = _raw_yaml()
    assert validate_rules(raw) == raw


def test_scout_rules_matches_raw_yaml():
    """config.scout_rules() の戻り値も raw YAML と一致する。"""
    assert scout_rules() == _raw_yaml()


def test_unknown_key_is_rejected():
    """未知キー（タイポ）は ValidationError で即エラー。"""
    bad = _raw_yaml()
    bad["eligibility"]["min_ages"] = 30  # min_age のタイポ
    with pytest.raises(ValidationError):
        validate_rules(bad)


def test_unknown_top_level_key_is_rejected():
    with pytest.raises(ValidationError):
        validate_rules({"eligibilty": {}})  # eligibility のタイポ


def test_type_error_is_rejected():
    bad = _raw_yaml()
    bad["eligibility"]["min_age"] = "二十七"  # 数値でない
    with pytest.raises(ValidationError):
        validate_rules(bad)


def test_model_defaults_match_shipped_yaml():
    """モデルのデフォルトが出荷 YAML の主要値と一致する（コード側フォールバックとの乖離検知）。"""
    raw = _raw_yaml()
    defaults = ScoutRules().eligibility
    assert defaults.min_age == raw["eligibility"]["min_age"]
    assert defaults.max_age == raw["eligibility"]["max_age"]
    assert defaults.min_same_company_years == raw["eligibility"]["min_same_company_years"]
    assert ScoutRules().resend.after_days == raw["resend"]["after_days"]
    assert ScoutRules().matching.max_intro_consultants == raw["matching"]["max_intro_consultants"]


def test_none_default_fields_are_dropped_by_exclude_none():
    """None デフォルトの任意フィールドは dump に現れない（exclude_none）。

    これが tone_profiles.match や job_changes_exclude のブラケットで効くため、
    出荷 YAML の round-trip（test_validation_is_behavior_preserving）が成立する。
    """
    out = validate_rules({"tone_profiles": [{"key": "x", "match": {"age_max": 29}}]})
    match = out["tone_profiles"][0]["match"]
    assert match == {"age_max": 29}  # age_min/experience_max/job_functions は落ちる


def test_empty_input_yields_full_defaults():
    """空 dict でも検証が通り、全セクションがモデルのデフォルトで埋まる（起動を妨げない）。"""
    out = validate_rules({})
    assert set(out) == {"eligibility", "tone_profiles", "resend", "constraints", "matching"}
    assert out["eligibility"]["min_age"] == 27
    assert out["resend"]["after_days"] == 5
