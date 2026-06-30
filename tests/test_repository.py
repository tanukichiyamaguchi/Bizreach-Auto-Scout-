from bizreach_scout.eligibility import check_eligibility
from bizreach_scout.models import GeneratedScout, ScoutContent
from bizreach_scout.storage.repository import Repository

from .factories import make_candidate


def _scout(mno="BU3765516") -> GeneratedScout:
    return GeneratedScout(
        member_no=mno,
        first=ScoutContent(subject="【Premium Offer】初回", body="初回本文"),
        resend=ScoutContent(subject="【Premium Offer】再送", body="再送本文"),
        model="fake",
    )


def _repo(tmp_path) -> Repository:
    return Repository(db_path=tmp_path / "t.db")


def test_first_sent_only_true_after_send(tmp_path):
    repo = _repo(tmp_path)
    cand = make_candidate()
    repo.upsert_candidate(cand, check_eligibility(cand))
    repo.record_generated(_scout(), resend_after_days=5)

    # 生成直後は未送信
    assert repo.first_already_handled("BU3765516") is True
    assert repo.first_sent("BU3765516") is False

    repo.mark_sent("BU3765516", "first", 5)
    assert repo.first_sent("BU3765516") is True
    repo.close()


def test_mark_sent_first_schedules_resend(tmp_path):
    repo = _repo(tmp_path)
    repo.record_generated(_scout(), resend_after_days=5)
    # 送信前は再送予定なし
    assert repo.get_scout("BU3765516", "resend")["scheduled_at"] is None
    repo.mark_sent("BU3765516", "first", 5)
    # 送信後に再送予定が確定
    assert repo.get_scout("BU3765516", "resend")["scheduled_at"] is not None
    repo.close()


def test_due_resends_returns_only_past_due(tmp_path):
    repo = _repo(tmp_path)
    repo.record_generated(_scout(), resend_after_days=5)
    # 5日後に予定 → 今は対象外
    repo.mark_sent("BU3765516", "first", 5)
    assert repo.due_resends() == []
    # 過去に予定 → 対象
    repo.mark_sent("BU3765516", "first", -1)
    due = repo.due_resends()
    assert len(due) == 1 and due[0]["member_no"] == "BU3765516"
    repo.close()


def test_ineligible_recorded(tmp_path):
    repo = _repo(tmp_path)
    cand = make_candidate(age=24)
    repo.upsert_candidate(cand, check_eligibility(cand))
    rows = repo.ineligible_candidates()
    assert len(rows) == 1
    assert "年齢" in rows[0]["eligibility_failed"]
    repo.close()
