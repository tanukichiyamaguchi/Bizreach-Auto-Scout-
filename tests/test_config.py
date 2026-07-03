"""設定解決（送信求人IDなど）のテスト。"""

from __future__ import annotations

from bizreach_scout.config import scout_job_id


def test_scout_job_id_from_company_yaml():
    # company.yaml の job.scout_job_id（両会員種別に送れる求人）を返す。
    assert scout_job_id() == "7437375"


def test_scout_job_id_env_override(monkeypatch):
    monkeypatch.setenv("BIZSCOUT_SCOUT_JOB_ID", "12345")
    assert scout_job_id() == "12345"
