"""再送スケジューラ（H2: 送信上限の適用）を検証。"""

from __future__ import annotations

from types import SimpleNamespace

from bizreach_scout.config import get_settings
from bizreach_scout.eligibility import check_eligibility
from bizreach_scout.models import GeneratedScout, ScoutContent
from bizreach_scout.scheduler import run_due_resends
from bizreach_scout.storage.repository import Repository

from .factories import make_candidate


class FakeSender:
    def __init__(self, status="sent", dry_run=False):
        self.status = status
        self.dry_run = dry_run
        self.sent = []
        self.idempotency_keys = []

    def send_scout(self, url, subject, body, idempotency_key=None):
        self.sent.append(url)
        self.idempotency_keys.append(idempotency_key)
        return SimpleNamespace(status=self.status, detail="")


def _seed_due_resends(repo: Repository, n: int) -> None:
    for i in range(n):
        mno = f"BU90000{i:02d}"
        cand = make_candidate(member_no=mno, profile_url=f"https://ex.com/{i}")
        repo.upsert_candidate(cand, check_eligibility(cand))
        repo.record_generated(
            GeneratedScout(
                member_no=mno,
                first=ScoutContent(subject="【Premium Offer】初回", body="初回"),
                resend=ScoutContent(subject="【Premium Offer】再送", body="再送"),
                model="fake",
            )
        )
        # 過去に予定 → 期限到来扱い
        repo.mark_sent(mno, "first", -1)


def test_resend_respects_send_cap(tmp_path):
    repo = Repository(db_path=tmp_path / "t.db")
    get_settings().max_sends_per_run = 2
    _seed_due_resends(repo, 5)

    sender = FakeSender("sent")
    report = run_due_resends(repo, sender)
    repo.close()

    assert report.due == 5
    assert report.sent == 2          # 上限で打ち切り
    assert len(sender.sent) == 2     # 3件は次回へ持ち越し


def test_resend_no_sender_skips_all(tmp_path):
    repo = Repository(db_path=tmp_path / "t.db")
    _seed_due_resends(repo, 3)
    report = run_due_resends(repo, sender=None)
    repo.close()
    assert report.due == 3
    assert report.sent == 0
    assert report.skipped == 3
