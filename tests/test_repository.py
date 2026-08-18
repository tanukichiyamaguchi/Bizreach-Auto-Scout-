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
    repo.record_generated(_scout())

    # 生成直後は未送信（生成済みでも first_sent は False）
    assert repo.get_scout("BU3765516", "first") is not None
    assert repo.first_sent("BU3765516") is False

    repo.mark_sent("BU3765516", "first", 5)
    assert repo.first_sent("BU3765516") is True
    repo.close()


def test_mark_sent_first_schedules_resend(tmp_path):
    repo = _repo(tmp_path)
    repo.record_generated(_scout())
    # 送信前は再送予定なし
    assert repo.get_scout("BU3765516", "resend")["scheduled_at"] is None
    repo.mark_sent("BU3765516", "first", 5)
    # 送信後に再送予定が確定
    assert repo.get_scout("BU3765516", "resend")["scheduled_at"] is not None
    repo.close()


def test_due_resends_returns_only_past_due(tmp_path):
    repo = _repo(tmp_path)
    repo.record_generated(_scout())
    # 5日後に予定 → 今は対象外
    repo.mark_sent("BU3765516", "first", 5)
    assert repo.due_resends() == []
    # 過去に予定 → 対象
    repo.mark_sent("BU3765516", "first", -1)
    due = repo.due_resends()
    assert len(due) == 1 and due[0]["member_no"] == "BU3765516"
    repo.close()


def test_begin_send_persists_key_and_sets_sending(tmp_path):
    """P1: begin_send は status='sending' にし冪等キーを永続化する。"""
    repo = _repo(tmp_path)
    repo.record_generated(_scout())
    key = repo.begin_send("BU3765516", "first")
    assert key
    row = repo.get_scout("BU3765516", "first")
    assert row["status"] == "sending"
    assert row["idempotency_key"] == key
    # sending は送信済みではない（＝再試行対象）。
    assert repo.first_sent("BU3765516") is False
    repo.close()


def test_begin_send_reuses_key_while_sending(tmp_path):
    """P1: 'sending' のまま再度呼ぶと同一キーを返す（クラッシュ再試行での二重送信防止）。"""
    repo = _repo(tmp_path)
    repo.record_generated(_scout())
    key1 = repo.begin_send("BU3765516", "first")
    key2 = repo.begin_send("BU3765516", "first")
    assert key1 == key2
    repo.close()


def test_begin_send_new_key_after_generated(tmp_path):
    """generated 状態からの送信は新しいキーを発行する（確定失敗後の再試行は別リクエスト）。"""
    repo = _repo(tmp_path)
    repo.record_generated(_scout())
    key1 = repo.begin_send("BU3765516", "first")
    repo.mark_failed("BU3765516", "first", "boom")  # 確定失敗
    # record_generated で generated に戻してから再送
    repo.record_generated(_scout())
    key2 = repo.begin_send("BU3765516", "first")
    assert key1 != key2
    repo.close()


def test_migration_is_idempotent(tmp_path):
    """P1: 同じDBファイルで Repository を再オープンしてもマイグレーションが失敗しない。"""
    db = tmp_path / "t.db"
    Repository(db_path=db).close()
    repo2 = Repository(db_path=db)  # idempotency_key 列は既にある
    repo2.record_generated(_scout())
    assert repo2.get_scout("BU3765516", "first")["idempotency_key"] == ""
    repo2.close()


def test_has_any_sent(tmp_path):
    repo = _repo(tmp_path)
    repo.record_generated(_scout())
    assert repo.has_any_sent() is False
    repo.mark_sent("BU3765516", "first", 5)
    assert repo.has_any_sent() is True
    repo.close()


def test_ineligible_recorded(tmp_path):
    repo = _repo(tmp_path)
    cand = make_candidate(age=24)
    repo.upsert_candidate(cand, check_eligibility(cand))
    rows = repo.ineligible_candidates()
    assert len(rows) == 1
    assert "年齢" in rows[0]["eligibility_failed"]
    repo.close()


def test_recently_evaluated_mrccids_returns_evaluated_candidates(tmp_path):
    """評価済み（対象外含む）候補者の mrccid を返す。取り込みカーソルの土台。"""
    repo = Repository(db_path=tmp_path / "t.db")
    ok = make_candidate(member_no="BU0000001", mrccid="MR001")
    ng = make_candidate(member_no="BU0000002", mrccid="MR002", age=24)  # 対象外
    repo.upsert_candidate(ok, check_eligibility(ok))
    repo.upsert_candidate(ng, check_eligibility(ng))

    # 対象外でも「評価済み」なので次回は取り込まない（同じ人を毎回取り直さない）。
    assert repo.recently_evaluated_mrccids(30) == {"MR001", "MR002"}
    repo.close()


def test_recently_evaluated_mrccids_disabled_with_zero_days(tmp_path):
    repo = Repository(db_path=tmp_path / "t.db")
    cand = make_candidate(member_no="BU0000003", mrccid="MR003")
    repo.upsert_candidate(cand, check_eligibility(cand))
    assert repo.recently_evaluated_mrccids(0) == set()
    repo.close()


def test_recently_evaluated_mrccids_ignores_old_records(tmp_path):
    """期間外（古い評価）は対象外。時間が経てば条件が変わるので再評価する。"""
    repo = Repository(db_path=tmp_path / "t.db")
    cand = make_candidate(member_no="BU0000004", mrccid="MR004")
    repo.upsert_candidate(cand, check_eligibility(cand))
    repo.conn.execute(
        "UPDATE candidates SET updated_at='2020-01-01T00:00:00' WHERE member_no=?",
        ("BU0000004",),
    )
    repo.conn.commit()
    assert repo.recently_evaluated_mrccids(30) == set()
    repo.close()
