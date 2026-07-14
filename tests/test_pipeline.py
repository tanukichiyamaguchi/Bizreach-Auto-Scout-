"""パイプラインの送信ロジック（H1: 送信漏れ防止・再利用）を検証。"""

from __future__ import annotations

import contextlib
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
    def __init__(self, status="sent", dry_run=False):
        self.status = status
        self.dry_run = dry_run
        self.sent = []
        self.idempotency_keys = []

    def send_scout(self, candidate, subject, body, reminder=None, idempotency_key=None):
        self.sent.append((candidate.profile_url, subject, body, reminder))
        self.idempotency_keys.append(idempotency_key)
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
    assert any(s[:3] == ("https://ex.com/2", "【Premium Offer】初回", "初回本文")
               for s in sender.sent)

    # 2件目の文面は2回目で再生成されていない（generate は1回目に各1回のみ）
    assert gen.calls.count("BU1000002") == 1


def test_crash_between_send_and_mark_reuses_idempotency_key(tmp_path):
    """P1: 送信成功→mark_sent の間でクラッシュしても、再試行は同一冪等キーで送る。

    サーバ側dedupe（x-idempotency-key）が効き、二重送信にならない。
    """
    db = tmp_path / "t.db"
    gen = FakeGenerator()
    sender = FakeSender("sent", dry_run=False)

    # 1回目: mark_sent の直前で擬似クラッシュさせる。
    repo = Repository(db_path=db)
    pipe = ScoutPipeline(repo=repo, generator=gen, sender=sender)
    pipe.settings.max_sends_per_run = 5
    orig_mark_sent = repo.mark_sent
    repo.mark_sent = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash"))
    cand = [make_candidate(member_no="BU4100001", profile_url="https://ex.com/4")]
    with contextlib.suppress(RuntimeError):
        pipe.run(ListSource(cand), send=True)
    repo.mark_sent = orig_mark_sent
    # 送信はされたが sent 記録前に落ちたので status は 'sending' のまま。
    assert repo.get_scout("BU4100001", "first")["status"] == "sending"
    first_key = sender.idempotency_keys[-1]
    assert first_key  # 冪等キーが送られている
    repo.close()

    # 2回目: 送信済み扱いにはならず再試行される。冪等キーは1回目と同一。
    repo2 = Repository(db_path=db)
    pipe2 = ScoutPipeline(repo=repo2, generator=gen, sender=sender)
    pipe2.settings.max_sends_per_run = 5
    report2 = pipe2.run(ListSource(cand), send=True)
    repo2.close()

    assert report2.sent == 1
    assert sender.idempotency_keys[-1] == first_key  # 同一キーで再送
    assert gen.calls.count("BU4100001") == 1  # 文面は再生成しない（再利用）


def test_state_guard_blocks_send_when_db_empty(tmp_path):
    """P1: expect_state=true で状態DBが空なら、実送信を伴う実行を中断する。"""
    repo = Repository(db_path=tmp_path / "t.db")
    pipe = ScoutPipeline(repo=repo, generator=FakeGenerator(),
                         sender=FakeSender("sent", dry_run=False))
    orig = pipe.settings.expect_state
    pipe.settings.expect_state = True
    try:
        cand = [make_candidate(member_no="BU4200001", profile_url="https://ex.com/x")]
        import pytest
        with pytest.raises(RuntimeError, match="状態DB"):
            pipe.run(ListSource(cand), send=True)
    finally:
        pipe.settings.expect_state = orig
        repo.close()


def test_state_guard_allows_dry_run_even_when_db_empty(tmp_path):
    """dry_run はそもそも送信しないのでガード対象外（空DBでも実行できる）。"""
    repo = Repository(db_path=tmp_path / "t.db")
    pipe = ScoutPipeline(repo=repo, generator=FakeGenerator(),
                         sender=FakeSender("dry_run", dry_run=True))
    orig = pipe.settings.expect_state
    pipe.settings.expect_state = True
    try:
        cand = [make_candidate(member_no="BU4200002", profile_url="https://ex.com/y")]
        report = pipe.run(ListSource(cand), send=True)  # 例外を出さず完了する
        assert report.dry_run == 1
    finally:
        pipe.settings.expect_state = orig
        repo.close()


def test_resend_after_days_comes_from_rules(tmp_path):
    """P2: 再送日数は scout_rules.yaml resend.after_days を単一情報源とする。

    reminder の daysAfter 丸め（3/5/10）に after_days が反映されることを確認する。
    """
    db = tmp_path / "t.db"
    gen = FakeGenerator()
    sender = FakeSender("sent", dry_run=False)
    repo = Repository(db_path=db)
    pipe = ScoutPipeline(repo=repo, generator=gen, sender=sender)
    pipe.settings.max_sends_per_run = 5
    pipe.resend_after_days = 10  # rules 由来の値を差し替え（=YAMLで10にした場合）
    cand = [make_candidate(member_no="BU4300001", profile_url="https://ex.com/a")]
    pipe.run(ListSource(cand), send=True)
    reminder = sender.sent[0][3]
    assert reminder["daysAfter"] == "TenDays"  # after_days=10 → TenDays に丸め
    repo.close()


def test_max_sends_override_decouples_pickup_from_settings(tmp_path):
    """ピックアップ用: max_sends 指定は settings.max_sends_per_run より優先される。

    ピックアップ送信は無料枠のため、検索スカウトの送信上限(BIZSCOUT_MAX_SENDS_PER_RUN)
    に縛られず、処理対象ぶんまで送信できる（run-pickup は max_sends=--max を渡す）。
    """
    gen = FakeGenerator()
    sender = FakeSender("sent")
    repo = Repository(db_path=tmp_path / "t.db")
    pipe = ScoutPipeline(repo=repo, generator=gen, sender=sender, max_sends=5)
    pipe.settings.max_sends_per_run = 1  # 検索スカウト用の上限が 1 でも…
    try:
        report = pipe.run(ListSource(_candidates()), send=True)
    finally:
        pipe.settings.max_sends_per_run = 20  # シングルトンを元へ
    repo.close()
    assert report.sent == 2  # …ピックアップは max_sends=5 に従い全員へ送信


def test_max_sends_defaults_to_settings(tmp_path):
    """max_sends 未指定なら従来どおり settings.max_sends_per_run に従う。"""
    repo = Repository(db_path=tmp_path / "t.db")
    pipe = ScoutPipeline(repo=repo, generator=FakeGenerator(), sender=FakeSender("sent"))
    pipe.settings.max_sends_per_run = 1
    try:
        report = pipe.run(ListSource(_candidates()), send=True)
    finally:
        pipe.settings.max_sends_per_run = 20
    repo.close()
    assert report.sent == 1  # 上限1で打ち切り


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


def test_native_reminder_attached_and_resend_marked_skipped(tmp_path):
    """初回送信に追客(reminder)が添付され、独自再送は skipped（二重送信防止）。"""
    db = tmp_path / "t.db"
    gen = FakeGenerator()
    sender = FakeSender("sent")
    repo = Repository(db_path=db)
    pipe = ScoutPipeline(repo=repo, generator=gen, sender=sender)
    pipe.settings.max_sends_per_run = 5
    cand = [make_candidate(member_no="BU9000001", profile_url="https://ex.com/9")]
    pipe.run(ListSource(cand), send=True)

    reminder = sender.sent[0][3]
    assert reminder is not None
    assert reminder["daysAfter"] == "FiveDays"
    assert reminder["subject"] == "【Premium Offer】再送"
    assert reminder["body"] == "再送本文"
    # 独自再送は行わない（native reminder 扱いで skipped）。
    assert repo.get_scout("BU9000001", "resend")["status"] == "skipped"
    repo.close()


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
