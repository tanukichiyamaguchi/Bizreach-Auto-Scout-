"""スカウト送信API（通常/プラチナ/振り分け）のpayload・ヘッダ・ルーティング検証。

実ネットワークは使わず、Playwrightの request コンテキストをフェイクに差し替えて、
JSから判明した契約どおり（URL・ヘッダ・JSONボディ）かを検証する。
body は json.dumps 済みの文字列で送られるため、検証時に json.loads する。
"""

from __future__ import annotations

import json

from bizreach_scout.bizreach.api import BizreachApi


class _FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload if payload is not None else {"candidates": []}

    def json(self):
        return self._payload

    def text(self):
        return json.dumps(self._payload)


class _FakeRequest:
    def __init__(self, responses=None):
        self.calls = []
        self._responses = responses or {}

    def _resp_for(self, url):
        for key, payload in self._responses.items():
            if key in url:
                return _FakeResponse(200, payload)
        return _FakeResponse(200, {"candidates": [{"mrccid": "X"}]})

    def post(self, url, headers=None, data=None):
        self.calls.append({"url": url, "headers": headers or {}, "data": data})
        return self._resp_for(url)

    def get(self, url):
        return _FakeResponse(200, {})


class _FakePage:
    def __init__(self, req):
        self.request = req


class _FakeClient:
    def __init__(self, req):
        self.page = _FakePage(req)

        class _Sel:
            base_url = "https://cr-support.jp/"

        self.sel = _Sel()


def _api(responses=None):
    req = _FakeRequest(responses)
    return BizreachApi(_FakeClient(req)), req


def _body(call):
    return json.loads(call["data"])


def test_send_scout_url_headers_and_payload():
    api, req = _api()
    out = api.send_scout(
        job_id="3213517", mrccid="ABC", subject="件名", body="本文",
        dry_run=True, search_id="sid-1", reminder=None, one_time_token="tok",
    )
    assert out["status"] == 200
    call = req.calls[-1]
    assert call["url"] == "https://cr-support.jp/api/v2/scouts/candidates"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["x-idempotency-key"]
    assert call["headers"]["x-search-id"] == "sid-1"
    assert call["headers"]["x-screen-type"] == "resume_search_with_saved_condition"
    body = _body(call)
    assert body["subject"] == "件名"
    assert body["dryRun"] is True
    assert body["jobId"] == "3213517"
    assert body["mrccids"] == ["ABC"]            # 複数形・配列
    assert body["isReservation"] is False
    assert body["reminder"] is None
    assert body["oneTimeToken"] == "tok"


def test_reminder_bare_string_is_coerced_to_none():
    # 文字列reminderは型不一致(400)を招くため None に落とす。
    api, req = _api()
    api.send_scout(job_id="J", mrccid="A", subject="s", body="b",
                   dry_run=True, reminder="ThreeDays")  # 誤用
    assert _body(req.calls[-1])["reminder"] is None


def test_reminder_object_passthrough():
    api, req = _api()
    rem = {"daysAfter": "ThreeDays", "subject": "s2", "body": "b2"}
    api.send_scout(job_id="J", mrccid="A", subject="s", body="b",
                   dry_run=True, reminder=rem)
    assert _body(req.calls[-1])["reminder"] == rem


def test_platinum_scout_singular_mrccid_no_token():
    api, req = _api()
    out = api.send_platinum_scout(job_id="3213517", mrccid="ABC",
                                  subject="件名", body="本文", dry_run=True)
    assert out["status"] == 200
    call = req.calls[-1]
    assert call["url"] == "https://cr-support.jp/api/v2/scouts/platinum"
    body = _body(call)
    assert body["mrccid"] == "ABC"               # 単数
    assert "mrccids" not in body
    assert "oneTimeToken" not in body            # プラチナはtoken不要
    assert body["dryRun"] is True


def test_check_candidates_payload():
    api, req = _api()
    out = api.check_candidates("3213517", ["A", "B"])
    assert out["status"] == 200
    call = req.calls[-1]
    assert call["url"] == "https://cr-support.jp/api/v2/scouts/checkCandidates"
    assert _body(call) == {"jobId": "3213517", "mrccids": ["A", "B"]}


def test_route_classmismatch_goes_platinum():
    # checkCandidates が ClassMismatch を返す → プラチナへ。
    resp = {"checkCandidates": {"candidates": [{"mrccid": "HC", "error": "ClassMismatch"}]}}
    api, req = _api(resp)
    out = api.route_scout("J", "HC", "s", "b", dry_run=True)
    assert out["endpoint"] == "platinum"
    assert req.calls[-1]["url"].endswith("/scouts/platinum")


def test_route_no_error_goes_candidates():
    resp = {"checkCandidates": {"candidates": [{"mrccid": "TL", "error": None}]}}
    api, req = _api(resp)
    out = api.route_scout("J", "TL", "s", "b", dry_run=True)
    assert out["endpoint"] == "candidates"
    assert req.calls[-1]["url"].endswith("/scouts/candidates")


def test_route_other_error_skips():
    resp = {"checkCandidates": {"candidates": [{"mrccid": "AL", "error": "AlreadyScouted"}]}}
    api, req = _api(resp)
    out = api.route_scout("J", "AL", "s", "b", dry_run=True)
    assert out["endpoint"] == "skip"
    assert out["skipped"] == "AlreadyScouted"
    # 送信APIは叩かない（checkCandidatesのみ）。
    assert all("scouts/platinum" not in c["url"] and "scouts/candidates" not in c["url"]
               for c in req.calls)


def test_idempotency_key_unique_per_send():
    api, req = _api()
    api.send_platinum_scout("J", "A", "s", "b", dry_run=True)
    api.send_platinum_scout("J", "A", "s", "b", dry_run=True)
    assert (req.calls[0]["headers"]["x-idempotency-key"]
            != req.calls[1]["headers"]["x-idempotency-key"])
