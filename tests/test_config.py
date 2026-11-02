"""設定解決（送信求人IDなど）のテスト。"""

from __future__ import annotations

from bizreach_scout.config import scout_job_id


def test_scout_job_id_from_company_yaml():
    # company.yaml の job.scout_job_id（両会員種別に送れる求人）を返す。
    assert scout_job_id() == "7437375"


def test_scout_job_id_env_override(monkeypatch):
    monkeypatch.setenv("BIZSCOUT_SCOUT_JOB_ID", "12345")
    assert scout_job_id() == "12345"


# --- 生成モデル: Sonnet 5.5 に揃える ------------------------------------------

def test_default_model_is_sonnet_5_5():
    from bizreach_scout.config import GENERATION_MODEL, Settings
    assert GENERATION_MODEL == "claude-sonnet-5-5"
    assert Settings().model == GENERATION_MODEL


def test_opus_and_older_sonnet_are_read_as_generation_model():
    """文面生成は Sonnet 5.5 を使う方針（2026-11）。

    GitHub の Variables に以前の claude-opus-5-5 や旧 Sonnet が残っていても Sonnet 5.5 で動くこと。
    """
    from bizreach_scout.config import GENERATION_MODEL, Settings, normalize_model
    for old in ("claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-1-20250805",
                "claude-sonnet-5", "claude-sonnet-4-6"):
        assert normalize_model(old) == GENERATION_MODEL
        assert Settings(model=old).model == GENERATION_MODEL


def test_other_models_are_left_alone():
    """haiku 等の指定はそのまま尊重する。"""
    from bizreach_scout.config import GENERATION_MODEL, Settings, normalize_model
    for m in ("claude-haiku-4-5", "claude-haiku-5-5", GENERATION_MODEL):
        assert normalize_model(m) == m
        assert Settings(model=m).model == m
