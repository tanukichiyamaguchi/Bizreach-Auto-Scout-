"""返信自動同期（reply_sync.py）のテスト。FakeApi でネットワーク不要。"""

from __future__ import annotations

from datetime import datetime, timedelta

from bizreach_scout.analytics.reply_sync import sync_replies
from bizreach_scout.eligibility import check_eligibility
from bizreach_scout.models import GeneratedScout, ScoutContent
from bizreach_scout.storage.repository import Repository

from .factories import make_candidate


class FakeApi:
    """get_resume だけを持つ BizreachApi のスタブ。"""

    def __init__(self, resumes: dict[str, dict]):
        self.resumes = resumes
        self.calls: list[str] = []

    def get_resume(self, mrccid):
        self.calls.append(mrccid)
        return self.resumes.get(mrccid)


def _repo_with_sent(tmp_path, members: list[tuple[str, int]]) -> Repository:
    """members: (member_no, 送信何日前) のリスト。mrccid は M-{member_no}。"""
    repo = Repository(db_path=tmp_path / "t.db")
    now = datetime.now()
    for mno, days_ago in members:
        cand = make_candidate(member_no=mno, mrccid=f"M-{mno}")
        repo.upsert_candidate(cand, check_eligibility(cand))
        repo.record_generated(GeneratedScout(
            member_no=mno, first=ScoutContent(subject="s", body="b"),
            resend=ScoutContent(subject="s2", body="b2"), model="m"))
        sent = (now - timedelta(days=days_ago)).isoformat(timespec="seconds")
        repo.conn.execute(
            "UPDATE scouts SET status='sent', sent_at=? WHERE member_no=? AND kind='first'",
            (sent, mno))
        repo.conn.commit()
        repo._log_sent_event(mno, "first", "platinum", sent)
    return repo


def _resume(name=None, history=None) -> dict:
    r: dict = {"bizreachUserId": "BUX", "age": 30}
    if name is not None:
        r["candidateName"] = name
    if history is not None:
        r["contactHistory"] = history
    return r


def test_sync_replies_detects_and_records(tmp_path):
    repo = _repo_with_sent(tmp_path, [("BU1", 3), ("BU2", 5)])
    api = FakeApi({
        "M-BU1": _resume(name="山田 太郎"),   # 氏名開示 → 返信あり
        "M-BU2": _resume(),                     # 匿名のまま → 未返信
    })
    report = sync_replies(api, repo, max_checks=10, recent_days=45)
    assert report.checked == 2
    assert report.detected == 1
    row = repo.conn.execute("SELECT * FROM replies WHERE member_no='BU1'").fetchone()
    assert row["replied"] == 1
    assert row["detected_by"] == "auto"
    assert row["candidate_name"] == "山田 太郎"
    assert repo.conn.execute(
        "SELECT COUNT(*) AS n FROM replies WHERE member_no='BU2'").fetchone()["n"] == 0
    repo.close()


def test_sync_replies_respects_max_checks_oldest_first(tmp_path):
    repo = _repo_with_sent(tmp_path, [("BU_NEW", 1), ("BU_OLD", 30), ("BU_MID", 10)])
    api = FakeApi({f"M-{m}": _resume() for m in ("BU_NEW", "BU_OLD", "BU_MID")})
    report = sync_replies(api, repo, max_checks=2, recent_days=45)
    assert report.checked == 2
    assert api.calls == ["M-BU_OLD", "M-BU_MID"]  # 古い順に上限まで
    repo.close()


def test_sync_replies_skips_already_replied_and_survives_errors(tmp_path):
    repo = _repo_with_sent(tmp_path, [("BU1", 3), ("BU2", 4)])
    repo.upsert_reply("BU1", replied=True, replied_at=None, detected_by="manual")

    class BoomApi:
        def __init__(self):
            self.calls: list[str] = []

        def get_resume(self, mrccid):
            self.calls.append(mrccid)
            raise RuntimeError("network")

    api = BoomApi()
    report = sync_replies(api, repo, max_checks=10, recent_days=45)
    # BU1 は返信済みなので確認せず、BU2 のみ（エラーでも例外を上げない）。
    assert api.calls == ["M-BU2"]
    assert report.errors == 1 and report.checked == 0
    repo.close()
