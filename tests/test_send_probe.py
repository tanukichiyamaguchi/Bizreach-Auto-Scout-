"""送信フロー偵察の安全機構テスト（実送信を絶対にしないことの担保）。

SendProbe はブラウザ依存だが、送信ブロックの中核ロジック（route handler）は
純粋に検証できる。武装(arm)後は /api/ への POST を必ず abort し、記録することを確認する。
"""

from __future__ import annotations

from bizreach_scout.bizreach.send_probe import SendProbe


class _FakeSel:
    base_url = "https://cr-support.jp/"


class _FakePage:
    def __init__(self):
        self.handler = None
        self.listeners = {}

    def route(self, _pattern, handler):
        self.handler = handler

    def on(self, event, cb):
        self.listeners[event] = cb


class _FakeClient:
    def __init__(self):
        self.sel = _FakeSel()
        self.page = _FakePage()


class _FakeRequest:
    def __init__(self, method, url, post_data=""):
        self.method = method
        self.url = url
        self.post_data = post_data
        self.headers = {"content-type": "application/json"}


class _FakeRoute:
    def __init__(self, request):
        self.request = request
        self.aborted = False
        self.continued = False

    def abort(self):
        self.aborted = True

    def continue_(self):
        self.continued = True


def _make_probe(tmp_path):
    probe = SendProbe(_FakeClient())
    probe.out = tmp_path
    return probe


def test_post_continues_before_arming(tmp_path):
    probe = _make_probe(tmp_path)
    probe._install_capture(probe.client.page)
    handler = probe.client.page.handler

    route = _FakeRoute(_FakeRequest("POST", "https://cr-support.jp/api/v2/x", '{"a":1}'))
    handler(route)

    assert route.continued is True
    assert route.aborted is False
    assert len(probe.posts) == 1  # 記録はされる
    assert probe.blocked == []


def test_post_blocked_after_arming(tmp_path):
    probe = _make_probe(tmp_path)
    probe._install_capture(probe.client.page)
    handler = probe.client.page.handler

    probe._arm_block = True
    route = _FakeRoute(
        _FakeRequest("POST", "https://cr-support.jp/api/v2/scout:send", '{"body":"x"}')
    )
    handler(route)

    # 武装後の送信POSTは必ず中断され、実送信されない。
    assert route.aborted is True
    assert route.continued is False
    assert len(probe.blocked) == 1
    assert "scout:send" in probe.blocked[0][0]


def test_get_always_continues(tmp_path):
    probe = _make_probe(tmp_path)
    probe._install_capture(probe.client.page)
    handler = probe.client.page.handler

    probe._arm_block = True  # 武装中でも GET は素通し
    route = _FakeRoute(_FakeRequest("GET", "https://cr-support.jp/api/v2/candidates/x/resume"))
    handler(route)

    assert route.continued is True
    assert route.aborted is False
    assert probe.blocked == []
