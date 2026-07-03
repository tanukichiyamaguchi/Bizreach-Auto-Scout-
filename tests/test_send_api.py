"""スカウト送信API（BizreachApi.send_scout / check_candidates）のpayload検証。

実ネットワークは使わず、Playwrightの request コンテキストをフェイクに差し替えて、
送信先URL・ヘッダ・ボディが JS から判明した契約どおりかを検証する。
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
    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, data=None):
        self.calls.append({"url": url, "headers": headers or {}, "data": data})
        return _FakeResponse(200, {"candidates": [{"mrccid": "X"}]})

    def get(self, url):  # 未使用だが互換のため
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


def _api():
    req = _FakeRequest()
    return BizreachApi(_FakeClient(req)), req


def test_send_scout_url_and_payload():
    api, req = _api()
    out = api.send_scout(
        job_id="3213517", mrccid="ABC", subject="件名", body="本文",
        dry_run=True, search_id="sid-1", reminder=None, one_time_token="tok",
    )
    assert out["status"] == 200
    call = req.calls[-1]
    assert call["url"] == "https://cr-support.jp/api/v2/scouts/candidates"
    # 必須ヘッダ
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["x-idempotency-key"]  # UUID が入る
    assert call["headers"]["x-search-id"] == "sid-1"
    # ボディ契約（JSから判明したフィールド）
    body = call["data"]
    assert body["subject"] == "件名"
    assert body["body"] == "本文"
    assert body["dryRun"] is True
    assert body["jobId"] == "3213517"
    assert body["mrccids"] == ["ABC"]           # 配列
    assert body["isReservation"] is False
    assert body["reminder"] is None
    assert body["oneTimeToken"] == "tok"


def test_idempotency_key_is_unique_per_send():
    api, req = _api()
    api.send_scout(job_id="J", mrccid="A", subject="s", body="b", dry_run=True)
    api.send_scout(job_id="J", mrccid="A", subject="s", body="b", dry_run=True)
    k1 = req.calls[0]["headers"]["x-idempotency-key"]
    k2 = req.calls[1]["headers"]["x-idempotency-key"]
    assert k1 != k2


def test_check_candidates_payload():
    api, req = _api()
    out = api.check_candidates("3213517", ["A", "B"])
    assert out["status"] == 200
    call = req.calls[-1]
    assert call["url"] == "https://cr-support.jp/api/v2/scouts/checkCandidates"
    assert call["data"] == {"jobId": "3213517", "mrccids": ["A", "B"]}
