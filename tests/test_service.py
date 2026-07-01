"""常駐サービス（service.run_cycle / service.serve）を検証する。

実ブラウザ・ネットワーク・実認証情報は使わず、ScoutPipeline・run_due_resends・
BizreachClient・BizreachSender・BizreachSource を monkeypatch で差し替えて検証する。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bizreach_scout import service


class FakeClient:
    """BizreachClient のスタブ。ブラウザを起動しない。"""

    instances: list[FakeClient] = []

    def __init__(self, *args, **kwargs):
        self.started = False
        self.logged_in = False
        self.closed = False
        FakeClient.instances.append(self)

    def start(self):
        self.started = True
        return self

    def ensure_logged_in(self):
        self.logged_in = True

    def close(self):
        self.closed = True


class FakePipeline:
    """ScoutPipeline のスタブ。run で固定レポートを返す。"""

    instances: list[FakePipeline] = []

    def __init__(self, repo=None, generator=None, sender=None):
        self.repo = repo
        self.generator = generator
        self.sender = sender
        self.run_calls = []
        FakePipeline.instances.append(self)

    def run(self, source, send=True, **kwargs):
        self.run_calls.append((source, send, kwargs))
        return SimpleNamespace(
            processed=3,
            generated=2,
            reused=1,
            sent=2,
            dry_run=0,
            skipped_duplicate=0,
            skipped_ineligible=1,
            failed=0,
        )


class FakeRepository:
    """Repository のスタブ。close されたことだけ記録する。"""

    instances: list[FakeRepository] = []

    def __init__(self, *args, **kwargs):
        self.closed = False
        FakeRepository.instances.append(self)

    def close(self):
        self.closed = True


def _fake_resend_report():
    return SimpleNamespace(due=4, sent=4, dry_run=0, skipped=0, failed=0)


@pytest.fixture
def patched(monkeypatch):
    """run_cycle が参照する全依存をスタブ化し、呼び出し記録を返す。"""
    FakePipeline.instances.clear()
    FakeRepository.instances.clear()
    FakeClient.instances.clear()
    calls: dict = {"resend": [], "source": [], "sender": []}

    monkeypatch.setattr(service, "Repository", FakeRepository)
    monkeypatch.setattr(service, "ScoutPipeline", FakePipeline)

    def fake_run_due_resends(repo, sender, now=None):
        calls["resend"].append((repo, sender))
        return _fake_resend_report()

    monkeypatch.setattr(service, "run_due_resends", fake_run_due_resends)

    # ブラウザ系（service 内で遅延 import される）を差し替える。
    import bizreach_scout.bizreach.client as client_mod
    import bizreach_scout.bizreach.sender as sender_mod
    import bizreach_scout.ingest.bizreach_source as source_mod

    monkeypatch.setattr(client_mod, "BizreachClient", FakeClient)

    def fake_sender_factory(client, *args, **kwargs):
        s = SimpleNamespace(client=client)
        calls["sender"].append(s)
        return s

    monkeypatch.setattr(sender_mod, "BizreachSender", fake_sender_factory)

    def fake_source_factory(search_url=None, max_candidates=50, **kwargs):
        s = SimpleNamespace(search_url=search_url, max_candidates=max_candidates)
        calls["source"].append(s)
        return s

    monkeypatch.setattr(source_mod, "BizreachSource", fake_source_factory)

    # kill switch は既定で無効（存在しないパス）にしておく。
    _disable_kill_switch(monkeypatch)
    return calls


def _disable_kill_switch(monkeypatch):
    monkeypatch.setattr(service, "_kill_switch_active", lambda: False)


def _enable_kill_switch(monkeypatch):
    monkeypatch.setattr(service, "_kill_switch_active", lambda: True)


# --- run_cycle ------------------------------------------------------------


def test_run_cycle_kill_switch_blocks_everything(monkeypatch):
    """kill switch 有効時は送信系を一切呼ばず skipped を返す。"""
    FakePipeline.instances.clear()
    FakeRepository.instances.clear()
    monkeypatch.setattr(service, "Repository", FakeRepository)
    monkeypatch.setattr(service, "ScoutPipeline", FakePipeline)

    called = {"resend": False}

    def fake_run_due_resends(repo, sender, now=None):
        called["resend"] = True
        return _fake_resend_report()

    monkeypatch.setattr(service, "run_due_resends", fake_run_due_resends)
    _enable_kill_switch(monkeypatch)

    result = service.run_cycle(search_url="https://ex.com/search")

    assert result == {"skipped": "kill_switch"}
    # 送信系・パイプライン・再送・リポジトリのいずれも起動しない。
    assert FakePipeline.instances == []
    assert FakeRepository.instances == []
    assert called["resend"] is False


def test_run_cycle_with_search_url_runs_pipeline_and_resends(patched):
    """search_url ありで pipeline と再送が実行され、件数が dict で返る。"""
    result = service.run_cycle(search_url="https://ex.com/search", max_candidates=10)

    assert result["pipeline"]["sent"] == 2
    assert result["pipeline"]["processed"] == 3
    assert result["resend"]["sent"] == 4

    # pipeline が1回 run され、BizreachSource に search_url/max が渡る。
    assert len(FakePipeline.instances) == 1
    assert FakePipeline.instances[0].run_calls[0][1] is True  # send=True
    assert patched["source"][0].search_url == "https://ex.com/search"
    assert patched["source"][0].max_candidates == 10
    # 再送も実行される。
    assert len(patched["resend"]) == 1


def test_run_cycle_without_search_url_only_resends(patched):
    """search_url 無しなら pipeline は走らず再送のみ実行する。"""
    result = service.run_cycle(search_url=None)

    assert "pipeline" not in result
    assert result["resend"]["due"] == 4
    assert FakePipeline.instances == []  # パイプライン未起動
    assert patched["source"] == []       # 取り込みソース未生成
    assert len(patched["resend"]) == 1


def test_parse_search_urls_variants():
    """複数URLは 空白・改行・パイプ で区切る。カンマは区切らない。"""
    assert service.parse_search_urls(None) == []
    assert service.parse_search_urls("") == []
    assert service.parse_search_urls("https://a") == ["https://a"]
    assert service.parse_search_urls("https://a https://b") == ["https://a", "https://b"]
    assert service.parse_search_urls("https://a\nhttps://b | https://c") == [
        "https://a", "https://b", "https://c"
    ]
    assert service.parse_search_urls(["https://a", " https://b "]) == ["https://a", "https://b"]
    # カンマは区切り文字にしない（URL内に含まれ得るため）。
    assert service.parse_search_urls("https://a?x=1,2") == ["https://a?x=1,2"]


def test_run_cycle_multiple_search_urls_aggregates(patched):
    """複数の検索URLを1サイクルで処理し、件数を合算する。"""
    result = service.run_cycle(search_url="https://ex.com/s1 https://ex.com/s2")

    # 検索URLは2件、パイプラインは使い回し(1インスタンス)で2回 run。
    assert result["pipeline"]["search_urls"] == 2
    assert len(FakePipeline.instances) == 1
    assert len(FakePipeline.instances[0].run_calls) == 2
    # 件数は各URLの合算（1URLあたり sent=2, processed=3）。
    assert result["pipeline"]["sent"] == 4
    assert result["pipeline"]["processed"] == 6
    assert {s.search_url for s in patched["source"]} == {
        "https://ex.com/s1", "https://ex.com/s2"
    }
    # 2URL目には既送信件数が sent_offset として渡る。
    assert FakePipeline.instances[0].run_calls[1][2].get("sent_offset") == 2


def test_run_cycle_closes_client_and_repo(patched):
    """正常系でクライアントとリポジトリが必ずクローズされる。"""
    service.run_cycle(search_url="https://ex.com/search")
    assert FakeRepository.instances[0].closed is True
    assert FakeClient.instances[0].closed is True  # ブラウザのリーク無し


def test_run_cycle_catches_exception_and_closes(patched, monkeypatch):
    """サイクル内例外は捕捉され error dict を返し、リポジトリはクローズされる。"""

    def boom(repo, sender, now=None):
        raise RuntimeError("再送で爆発")

    monkeypatch.setattr(service, "run_due_resends", boom)

    result = service.run_cycle(search_url=None)

    assert "error" in result
    assert "再送で爆発" in result["error"]
    # 例外時もリポジトリ・クライアントは finally でクローズされる（リーク無し）。
    assert FakeRepository.instances[0].closed is True
    assert FakeClient.instances[0].closed is True


# --- serve ----------------------------------------------------------------


def test_serve_once_runs_single_cycle(monkeypatch):
    """once=True なら run_cycle が1回だけ呼ばれて終了する。"""
    calls = []

    def fake_run_cycle(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(service, "run_cycle", fake_run_cycle)

    service.serve(search_url="https://ex.com/search", once=True, interval=999999)

    assert len(calls) == 1
    assert calls[0]["search_url"] == "https://ex.com/search"


def test_serve_max_cycles_limits_iterations(monkeypatch):
    """max_cycles で指定回数だけ実行して停止する（interval は待機させない）。"""
    calls = []

    def fake_run_cycle(**kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(service, "run_cycle", fake_run_cycle)
    # interval=0 で待機を即時終了させ、max_cycles=3 で打ち切る。
    service.serve(max_cycles=3, interval=0)

    assert len(calls) == 3


def test_serve_isolates_cycle_exception_and_continues(monkeypatch):
    """サイクルで例外が出てもループは継続し、後続サイクルが実行される。"""
    calls = []

    def flaky_run_cycle(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("1回目で例外")
        return {"ok": True}

    monkeypatch.setattr(service, "run_cycle", flaky_run_cycle)
    service.serve(max_cycles=3, interval=0)

    # 1回目が例外でも2・3回目が実行される（合計3回）。
    assert len(calls) == 3
