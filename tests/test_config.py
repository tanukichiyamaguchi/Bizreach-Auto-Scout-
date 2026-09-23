"""設定解決（送信求人IDなど）のテスト。"""

from __future__ import annotations

from bizreach_scout.config import scout_job_id


def test_scout_job_id_from_company_yaml():
    # company.yaml の job.scout_job_id（両会員種別に送れる求人）を返す。
    assert scout_job_id() == "7437375"


def test_scout_job_id_env_override(monkeypatch):
    monkeypatch.setenv("BIZSCOUT_SCOUT_JOB_ID", "12345")
    assert scout_job_id() == "12345"


# --- 生成モデル: Opus は最新の Opus に揃える --------------------------------

def test_default_model_is_latest_opus():
    from bizreach_scout.config import OPUS_MODEL, Settings
    assert OPUS_MODEL == "claude-opus-5-5"
    assert Settings().model == OPUS_MODEL


def test_legacy_opus_is_upgraded_to_latest():
    """GitHub Variables に旧 Opus が残っていても最新の Opus で動く。

    前世代の claude-opus-5 も読み替え対象（Opus 5.5 への切り替え後、Variables に
    claude-opus-5 が残っていても最新で動くこと）。
    """
    from bizreach_scout.config import OPUS_MODEL, Settings, normalize_model
    for old in ("claude-opus-5", "claude-opus-4-8", "claude-opus-4-1",
                "claude-opus-4-1-20250805"):
        assert normalize_model(old) == OPUS_MODEL
        assert Settings(model=old).model == OPUS_MODEL


def test_non_opus_models_are_left_alone():
    """コスト重視の sonnet/haiku 指定は尊重する。"""
    from bizreach_scout.config import OPUS_MODEL, Settings, normalize_model
    for m in ("claude-sonnet-4-6", "claude-haiku-4-5", "claude-sonnet-5", OPUS_MODEL):
        assert normalize_model(m) == m
        assert Settings(model=m).model == m
