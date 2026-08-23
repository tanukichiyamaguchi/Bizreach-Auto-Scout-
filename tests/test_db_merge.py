"""DBスナップショットからの復旧（merge_from_db）のテスト。

キャッシュ消失で状態が巻き戻った後、過去実行の artifact に残るDBから
欠けている記録を取り戻せること。現行の記録を壊さないことが要点。
"""

from __future__ import annotations

from bizreach_scout.eligibility import check_eligibility
from bizreach_scout.models import GeneratedScout, ScoutContent
from bizreach_scout.storage.repository import Repository

from .factories import make_candidate


def _scout(mno: str, subject: str = "【Premium Offer】初回") -> GeneratedScout:
    return GeneratedScout(
        member_no=mno,
        first=ScoutContent(subject=subject, body="初回本文"),
        resend=ScoutContent(subject="【Premium Offer】再送", body="再送本文"),
        model="fake", tone_key="early30s",
    )


def _sent(repo: Repository, mno: str, *, channel: str = "platinum",
          subject: str = "【Premium Offer】初回") -> None:
    cand = make_candidate(member_no=mno)
    repo.upsert_candidate(cand, check_eligibility(cand))
    repo.record_generated(_scout(mno, subject))
    repo.mark_sent(mno, "first", 5, channel=channel)


def test_merge_restores_missing_sends_and_replies(tmp_path):
    """巻き戻り前のスナップショットから、消えた送信・返信・件名が戻る。"""
    snap = Repository(db_path=tmp_path / "snapshot.db")
    _sent(snap, "BU1111111", subject="【Premium Offer】山田さまへ")
    _sent(snap, "BU2222222", channel="pickup")
    snap.upsert_reply("BU1111111", replied=True, replied_at="2026-08-10T10:00:00",
                      detected_by="auto", note="受信箱に返信（件名一致）")
    snap.close()

    # 巻き戻り後の現行DB（送信1件だけが残っている状態）。
    repo = Repository(db_path=tmp_path / "current.db")
    _sent(repo, "BU3333333")
    stats = repo.merge_from_db(tmp_path / "snapshot.db")

    assert stats["scouts"] == 4          # 2名 × first/resend
    assert stats["sent_log"] == 2
    assert stats["replies"] == 1
    assert stats["candidates"] == 2

    firsts = {r["member_no"]: r for r in repo.conn.execute(
        "SELECT * FROM sent_log WHERE kind='first'")}
    assert set(firsts) == {"BU1111111", "BU2222222", "BU3333333"}
    assert firsts["BU2222222"]["channel"] == "pickup"
    # 件名が戻る＝受信箱スキャンの件名照合で返信を再検知できる。
    subject = repo.conn.execute(
        "SELECT subject FROM scouts WHERE member_no='BU1111111' AND kind='first'"
    ).fetchone()["subject"]
    assert subject == "【Premium Offer】山田さまへ"
    assert repo.conn.execute(
        "SELECT replied FROM replies WHERE member_no='BU1111111'").fetchone()["replied"] == 1
    repo.close()


def test_merge_is_idempotent(tmp_path):
    """2回マージしても件数は増えない。"""
    snap = Repository(db_path=tmp_path / "snapshot.db")
    _sent(snap, "BU1111111")
    snap.close()

    repo = Repository(db_path=tmp_path / "current.db")
    first = repo.merge_from_db(tmp_path / "snapshot.db")
    second = repo.merge_from_db(tmp_path / "snapshot.db")
    assert first["sent_log"] == 1
    assert second == dict.fromkeys(second, 0)
    n = repo.conn.execute("SELECT COUNT(*) AS n FROM sent_log").fetchone()["n"]
    assert n == 1
    repo.close()


def test_merge_keeps_newer_reply_and_does_not_unset(tmp_path):
    """現行が返信ありなら、スナップショットの未返信で取り消さない。"""
    snap = Repository(db_path=tmp_path / "snapshot.db")
    _sent(snap, "BU1111111")
    snap.upsert_reply("BU1111111", replied=False, replied_at=None, detected_by="auto")
    snap.close()

    repo = Repository(db_path=tmp_path / "current.db")
    _sent(repo, "BU1111111")
    repo.upsert_reply("BU1111111", replied=True, replied_at="2026-08-20T09:00:00",
                      detected_by="manual", note="手動")
    repo.merge_from_db(tmp_path / "snapshot.db")

    row = repo.conn.execute(
        "SELECT * FROM replies WHERE member_no='BU1111111'").fetchone()
    assert row["replied"] == 1
    assert row["detected_by"] == "manual"
    repo.close()


def test_merge_prefers_earlier_first_send(tmp_path):
    """同じ人が両方にいる場合、初回送信日は古い方（真の初回）を採る。

    巻き戻り中に重複送信された人の送信日が、実際の初回へ是正される。
    """
    snap = Repository(db_path=tmp_path / "snapshot.db")
    _sent(snap, "BU1111111")
    snap.conn.execute("UPDATE sent_log SET sent_at='2026-07-10T18:00:00' "
                      "WHERE member_no='BU1111111' AND kind='first'")
    snap.conn.execute("UPDATE scouts SET sent_at='2026-07-10T18:00:00' "
                      "WHERE member_no='BU1111111' AND kind='first'")
    snap.conn.commit()
    snap.close()

    repo = Repository(db_path=tmp_path / "current.db")
    _sent(repo, "BU1111111")
    repo.conn.execute("UPDATE sent_log SET sent_at='2026-08-20T18:00:00' "
                      "WHERE member_no='BU1111111' AND kind='first'")
    repo.conn.execute("UPDATE scouts SET sent_at='2026-08-20T18:00:00' "
                      "WHERE member_no='BU1111111' AND kind='first'")
    repo.conn.commit()
    stats = repo.merge_from_db(tmp_path / "snapshot.db")

    assert stats["sent_log_earlier"] == 1
    assert repo.conn.execute(
        "SELECT sent_at FROM sent_log WHERE member_no='BU1111111' AND kind='first'"
    ).fetchone()["sent_at"] == "2026-07-10T18:00:00"
    # 行は増えない（差し替えであって重複追加ではない）。
    assert repo.conn.execute(
        "SELECT COUNT(*) AS n FROM sent_log").fetchone()["n"] == 1
    repo.close()


def test_merge_does_not_overwrite_newer_send_when_snapshot_is_later(tmp_path):
    """スナップショットの方が新しい送信日なら現行を維持する（初回日を後ろにずらさない）。"""
    snap = Repository(db_path=tmp_path / "snapshot.db")
    _sent(snap, "BU1111111")
    snap.conn.execute("UPDATE sent_log SET sent_at='2026-08-20T18:00:00' "
                      "WHERE member_no='BU1111111' AND kind='first'")
    snap.conn.commit()
    snap.close()

    repo = Repository(db_path=tmp_path / "current.db")
    _sent(repo, "BU1111111")
    repo.conn.execute("UPDATE sent_log SET sent_at='2026-07-10T18:00:00' "
                      "WHERE member_no='BU1111111' AND kind='first'")
    repo.conn.commit()
    repo.merge_from_db(tmp_path / "snapshot.db")

    assert repo.conn.execute(
        "SELECT sent_at FROM sent_log WHERE member_no='BU1111111' AND kind='first'"
    ).fetchone()["sent_at"] == "2026-07-10T18:00:00"
    repo.close()


def test_merge_promotes_unsent_scout_to_sent(tmp_path):
    """現行が未送信(generated)でスナップショットが送信済みなら送信済みへ是正する。

    重複送信防止の基準は scouts.status なので、ここが戻らないと再送信してしまう。
    """
    snap = Repository(db_path=tmp_path / "snapshot.db")
    _sent(snap, "BU1111111")
    snap.close()

    repo = Repository(db_path=tmp_path / "current.db")
    cand = make_candidate(member_no="BU1111111")
    repo.upsert_candidate(cand, check_eligibility(cand))
    repo.record_generated(_scout("BU1111111"))   # generated のまま（未送信）
    repo.merge_from_db(tmp_path / "snapshot.db")

    assert repo.conn.execute(
        "SELECT status FROM scouts WHERE member_no='BU1111111' AND kind='first'"
    ).fetchone()["status"] == "sent"
    assert repo.has_any_sent()
    repo.close()


def test_merge_keeps_existing_meta(tmp_path):
    """meta は現行を優先し、現行に無いキーだけ取り込む。"""
    snap = Repository(db_path=tmp_path / "snapshot.db")
    snap.set_meta("last_trend_at", "2026-07-01T00:00:00")
    snap.set_meta("only_in_snapshot", "x")
    snap.close()

    repo = Repository(db_path=tmp_path / "current.db")
    repo.set_meta("last_trend_at", "2026-08-20T00:00:00")
    repo.merge_from_db(tmp_path / "snapshot.db")

    assert repo.get_meta("last_trend_at") == "2026-08-20T00:00:00"
    assert repo.get_meta("only_in_snapshot") == "x"
    repo.close()
