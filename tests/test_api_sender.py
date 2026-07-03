"""ApiScoutSender（パイプライン用の送信アダプタ）のテスト。"""

from __future__ import annotations

from types import SimpleNamespace

from bizreach_scout.bizreach.api_sender import ApiScoutSender


class FakeApi:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def route_scout(self, job_id, mrccid, subject, body, dry_run=True, reminder=None):
        self.calls.append({"job_id": job_id, "mrccid": mrccid,
                           "dry_run": dry_run, "reminder": reminder})
        return dict(self.result)


def _cand(mrccid="ABC"):
    return SimpleNamespace(mrccid=mrccid, profile_url="", member_no="BU1")


def test_dry_run_returns_dry_run_outcome():
    api = FakeApi({"status": 201, "endpoint": "platinum"})
    out = ApiScoutSender(api, job_id="J", dry_run=True).send_scout(_cand(), "s", "b")
    assert out.status == "dry_run"
    assert api.calls[0]["dry_run"] is True


def test_real_send_returns_sent():
    api = FakeApi({"status": 201, "endpoint": "platinum"})
    out = ApiScoutSender(api, job_id="J", dry_run=False).send_scout(_cand(), "s", "b")
    assert out.status == "sent"
    assert api.calls[0]["dry_run"] is False


def test_no_mrccid_fails_without_calling_api():
    api = FakeApi({"status": 201})
    out = ApiScoutSender(api, job_id="J", dry_run=True).send_scout(_cand(mrccid=""), "s", "b")
    assert out.status == "failed"
    assert api.calls == []


def test_no_job_id_fails(monkeypatch):
    # 明示 job_id 無し＋設定にも求人IDが無い場合は送信不可。
    monkeypatch.setattr("bizreach_scout.bizreach.api_sender.scout_job_id", lambda: None)
    api = FakeApi({"status": 201})
    out = ApiScoutSender(api, job_id=None, dry_run=True).send_scout(_cand(), "s", "b")
    assert out.status == "failed"
    assert api.calls == []


def test_skipped_is_blocked():
    api = FakeApi({"status": 0, "skipped": "AlreadyScouted", "endpoint": "skip"})
    out = ApiScoutSender(api, job_id="J", dry_run=True).send_scout(_cand(), "s", "b")
    assert out.status == "blocked"


def test_http_error_returns_failed():
    api = FakeApi({"status": 400, "endpoint": "platinum"})
    out = ApiScoutSender(api, job_id="J", dry_run=True).send_scout(_cand(), "s", "b")
    assert out.status == "failed"


class _PickupApi:
    def __init__(self):
        self.calls = []

    def send_pickup_scout(self, job_id, mrccid, subject, body, dry_run=True, reminder=None):
        self.calls.append(("pickup", mrccid, dry_run))
        return {"status": 201, "endpoint": "pickup"}

    def route_scout(self, *a, **k):
        self.calls.append(("route",))
        return {"status": 201, "endpoint": "platinum"}


def test_pickup_mode_uses_pickup_endpoint():
    api = _PickupApi()
    out = ApiScoutSender(api, job_id="J", dry_run=False, pickup=True).send_scout(
        _cand(), "s", "b")
    assert out.status == "sent"
    assert api.calls[0][0] == "pickup"      # /v2/scouts/pickup を使う


def test_non_pickup_mode_uses_route():
    api = _PickupApi()
    ApiScoutSender(api, job_id="J", dry_run=True, pickup=False).send_scout(_cand(), "s", "b")
    assert api.calls[0][0] == "route"


def test_kill_switch_blocks_and_does_not_send(monkeypatch):
    api = FakeApi({"status": 201})
    s = ApiScoutSender(api, job_id="J", dry_run=True)
    monkeypatch.setattr(s, "_kill_switch_active", lambda: True)
    out = s.send_scout(_cand(), "s", "b")
    assert out.status == "blocked"
    assert api.calls == []
