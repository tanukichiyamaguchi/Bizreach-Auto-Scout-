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
        # 値は payload(dict) か (status, payload) のタプル。
        self._responses = responses or {}

    def _resp_for(self, url, default_payload):
        for key, val in self._responses.items():
            if key in url:
                status, payload = val if isinstance(val, tuple) else (200, val)
                return _FakeResponse(status, payload)
        return _FakeResponse(200, default_payload)

    def post(self, url, headers=None, data=None):
        self.calls.append({"url": url, "headers": headers or {}, "data": data})
        return self._resp_for(url, {"candidates": [{"mrccid": "X"}]})

    def get(self, url):
        return self._resp_for(url, {})


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


def test_route_no_error_goes_platinum():
    # 実運用はプラチナスカウト。error無し（Talent等）でもプラチナで送る。
    resp = {"checkCandidates": {"candidates": [{"mrccid": "TL", "error": None}]}}
    api, req = _api(resp)
    out = api.route_scout("J", "TL", "s", "b", dry_run=True)
    assert out["endpoint"] == "platinum"
    assert req.calls[-1]["url"].endswith("/scouts/platinum")
    # 通常(/candidates)は叩かない。
    assert all("scouts/candidates" not in c["url"] for c in req.calls)


def test_platinum_201_is_success_and_decrements():
    # プラチナは 201 Created で成功。本送信で残数を減算する。
    resp = {
        "checkCandidates": {"candidates": [{"mrccid": "HC", "error": "ClassMismatch"}]},
        "platinum/holders": (200, {"count": 4}),  # より具体的なキーを先に
        "scouts/platinum": (201, {}),
    }
    api, _ = _api(resp)
    out = api.route_scout("J", "HC", "s", "b", dry_run=False)
    assert out["status"] == 201
    assert out["endpoint"] == "platinum"
    assert out["platinum_remaining"] == 3  # 4 -> 3


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


# --- プラチナ残数（quota）ガード ---------------------------------------------

def _get_fake(url_count):
    """holders GET が指定の count を返す FakeRequest。"""
    class _Req(_FakeRequest):
        def get(self, url):
            if "platinum/holders" in url:
                return _FakeResponse(200, {"count": url_count, "holderType": "Company"})
            return _FakeResponse(200, {})
    return _Req


def test_platinum_remaining_cached():
    api, _ = _api()
    api.client.page.request = _get_fake(5)()
    assert api.platinum_remaining() == 5
    # キャッシュ: 2回目はGETを増やさない（値は同じ）。
    assert api.platinum_remaining() == 5


def test_real_platinum_send_skipped_when_quota_zero():
    resp = {"checkCandidates": {"candidates": [{"mrccid": "HC", "error": "ClassMismatch"}]}}
    req = _get_fake(0)(resp)
    api = BizreachApi(_FakeClient(req))
    out = api.route_scout("J", "HC", "s", "b", dry_run=False)  # 本送信
    assert out["skipped"] == "PlatinumQuotaExhausted"
    # 送信APIは叩かれない。
    assert all("scouts/platinum" not in c["url"] for c in req.calls)


def test_real_platinum_send_decrements_quota():
    resp = {
        "checkCandidates": {"candidates": [{"mrccid": "HC", "error": "ClassMismatch"}]},
        "scouts/platinum": {"result": "ok"},
    }
    req = _get_fake(2)(resp)
    api = BizreachApi(_FakeClient(req))
    out = api.route_scout("J", "HC", "s", "b", dry_run=False)
    assert out["endpoint"] == "platinum"
    assert out["platinum_remaining"] == 1  # 2 -> 1 に減算


def test_dryrun_platinum_does_not_consume_quota():
    resp = {"checkCandidates": {"candidates": [{"mrccid": "HC", "error": "ClassMismatch"}]}}
    req = _get_fake(3)(resp)
    api = BizreachApi(_FakeClient(req))
    out = api.route_scout("J", "HC", "s", "b", dry_run=True)
    assert out["endpoint"] == "platinum"
    assert api._platinum_remaining == 3  # 消費されない
