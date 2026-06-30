"""パイプラインの送信ロジック（H1: 送信漏れ防止・再利用）を検証。"""

from __future__ import annotations

from types import SimpleNamespace

from bizreach_scout.models import GeneratedScout, ScoutContent
from bizreach_scout.pipeline import ScoutPipeline
from bizreach_scout.storage.repository import Repository

from .factories import make_candidate


class FakeGenerator:
    def __init__(self):
        self.calls = []

    def generate(self, candidate):
        self.calls.append(candidate.member_no)
        return GeneratedScout(
            member_no=candidate.member_no,
            first=ScoutContent(subject="【Premium Offer】初回", body="初回本文"),
            resend=ScoutContent(subject="【Premium Offer】再送", body="再送本文"),
            model="fake",
        )


class FakeSender:
    def __init__(self, status="sent"):
        self.status = status
        self.sent = []

    def send_scout(self, url, subject, body):
        self.sent.append((url, subject, body))
        return SimpleNamespace(status=self.status, detail="")


class ListSource:
    def __init__(self, candidates):
        self.candidates = candidates

    def __iter__(self):
        return iter(self.candidates)


def _candidates():
    return [
        make_candidate(member_no="BU1000001", profile_url="https://ex.com/1"),
        make_candidate(member_no="BU1000002", profile_url="https://ex.com/2"),
    ]


def test_cap_then_retry_next_run_does_not_lose_candidate(tmp_path):
    """H1: 1件目で上限に達した候補者が、次回実行で再送信される。"""
    db = tmp_path / "t.db"
    gen = FakeGenerator()
    sender = FakeSender("sent")

    # 1回目: 上限1件 → 1件目のみ送信、2件目はスキップ
    repo = Repository(db_path=db)
    pipe = ScoutPipeline(repo=repo, generator=gen, sender=sender)
    pipe.settings.max_sends_per_run = 1  # settings はシングルトン
    report1 = pipe.run(ListSource(_candidates()), send=True)
    repo.close()

    assert report1.sent == 1
    sent_first_run = {s[0] for s in sender.sent}
    assert sent_first_run == {"https://ex.com/1"}

    # 2回目: 1件目は送信済みで重複スキップ、2件目は再利用して送信
    repo2 = Repository(db_path=db)
    pipe2 = ScoutPipeline(repo=repo2, generator=gen, sender=sender)
    pipe2.settings.max_sends_per_run = 5
    report2 = pipe2.run(ListSource(_candidates()), send=True)
    repo2.close()

    assert report2.skipped_duplicate == 1  # BU1000001
    assert report2.sent == 1               # BU1000002 が今回送信される
    assert report2.reused == 1             # 再生成せず再利用
    assert ("https://ex.com/2", "【Premium Offer】初回", "初回本文") in sender.sent

    # 2件目の文面は2回目で再生成されていない（generate は1回目に各1回のみ）
    assert gen.calls.count("BU1000002") == 1


def test_ineligible_skipped(tmp_path):
    gen = FakeGenerator()
    sender = FakeSender("sent")
    repo = Repository(db_path=tmp_path / "t.db")
    pipe = ScoutPipeline(repo=repo, generator=gen, sender=sender)
    young = make_candidate(member_no="BU2000001", age=24, profile_url="https://ex.com/y")
    report = pipe.run(ListSource([young]), send=True)
    repo.close()
    assert report.skipped_ineligible == 1
    assert report.sent == 0
    assert gen.calls == []  # 生成すら行わない


def test_dry_run_keeps_generated_and_can_send_later(tmp_path):
    """dry_run は mark_sent しないため、後で本送信できる。"""
    db = tmp_path / "t.db"
    gen = FakeGenerator()
    repo = Repository(db_path=db)
    pipe = ScoutPipeline(repo=repo, generator=gen, sender=FakeSender("dry_run"))
    cand = [make_candidate(member_no="BU3000001", profile_url="https://ex.com/3")]
    r1 = pipe.run(ListSource(cand), send=True)
    assert r1.dry_run == 1 and r1.sent == 0
    assert repo.first_sent("BU3000001") is False  # 未送信のまま
    repo.close()
