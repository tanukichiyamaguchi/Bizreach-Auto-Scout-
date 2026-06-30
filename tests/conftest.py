"""テスト共通設定。送信間隔の実スリープを無効化する。"""

from __future__ import annotations

import pytest

from bizreach_scout.config import get_settings


@pytest.fixture(autouse=True)
def _settings_isolation():
    """送信間隔のスリープ無効化と、上限値のテスト間リーク防止。"""
    s = get_settings()
    orig = (s.send_delay_min, s.send_delay_max, s.max_sends_per_run)
    s.send_delay_min = 0.0
    s.send_delay_max = 0.0
    yield
    s.send_delay_min, s.send_delay_max, s.max_sends_per_run = orig
