"""分析用の永続化（sent_log / replies / meta）のテスト。"""

from __future__ import annotations

from datetime import datetime, timedelta

from bizreach_scout.eligibility import check_eligibility
from bizreach_scout.models import GeneratedScout, ScoutContent
from bizreach_scout.storage.repository import Repository

from .factories import make_candidate


def _scout(mno="BU3765516") -> GeneratedScout:
    return GeneratedScout(
        member_no=mno,
        first=ScoutContent(subject="【Premium Offer】初回", body="初回本文"),
        resend=ScoutContent(subject="【Premium Offer】再送", body="再送本文"),
        model="fake", tone_key="early30s",
    )


def _repo_with_sent(tmp_path, mno="BU3765516", channel="platinum") -> Repository:
    repo = Repository(db_path=tmp_path / "t.db")
    cand = make_candidate(member_no=mno)
    repo.upsert_candidate(cand, check_eligibility(cand))
    repo.record_generated(_scout(mno))
    repo.mark_sent(mno, "first", 5, channel=channel)
    return repo


def test_mark_sent_logs_sent_event_with_profile(tmp_path):
    repo = _repo_with_sent(tmp_path)
    row = repo.conn.execute("SELECT * FROM sent_log WHERE kind='first'").fetchone()
    assert row is not None
    assert row["member_no"] == "BU3765516"
    assert row["channel"] == "platinum"
    assert row["age"] == 31                       # factories の既定
    assert row["age_band"] == "30〜34"
    assert row["education"] == "bachelor"
    assert row["university"] == "早稲田大学"
    assert row["current_company"] == "株式会社サンプル商事"
    assert row["job_change_count"] == 1           # 現職+リクルート=2社
    assert row["tone_key"] == "early30s"
    assert row["backfilled"] == 0
    repo.close()


def test_mark_sent_twice_does_not_duplicate_sent_log(tmp_path):
    repo = _repo_with_sent(tmp_path)
    repo.mark_sent("BU3765516", "first", 5, channel="platinum")  # クラッシュ再試行を模擬
    n = repo.conn.execute(
        "SELECT COUNT(*) AS n FROM sent_log WHERE kind='first'").fetchone()["n"]
    assert n == 1
    repo.close()


def test_backfill_shifts_utc_to_jst_and_is_idempotent(tmp_path):
    repo = Repository(db_path=tmp_path / "t.db")
    cand = make_candidate()
    repo.upsert_candidate(cand, check_eligibility(cand))
    repo.record_generated(_scout())
    # 過去のCI（UTC）で送信済みだった状態を再現: sent_log には入っていない。
    repo.conn.execute(
        "UPDATE scouts SET status='sent', sent_at='2026-07-10T08:00:00' "
        "WHERE member_no='BU3765516' AND kind='first'")
    repo.conn.commit()

    added = repo.backfill_sent_log()
    assert added == 1
    row = repo.conn.execute("SELECT * FROM sent_log").fetchone()
    assert row["sent_at"] == "2026-07-10T17:00:00"  # +9h（JST）
    assert row["backfilled"] == 1
    # 2回目は何も追加しない（冪等）。
    assert repo.backfill_sent_log() == 0
    repo.close()


def test_upsert_reply_never_demotes(tmp_path):
    repo = Repository(db_path=tmp_path / "t.db")
    repo.upsert_reply("BU1", replied=True, replied_at="2026-07-15T10:00:00",
                      detected_by="auto", candidate_name="山田太郎")
    # replied=False の後追い更新でも 1 のまま・検知元も維持。
    repo.upsert_reply("BU1", replied=False, replied_at=None, detected_by="manual")
    row = repo.conn.execute("SELECT * FROM replies WHERE member_no='BU1'").fetchone()
    assert row["replied"] == 1
    assert row["detected_by"] == "auto"
    assert row["candidate_name"] == "山田太郎"
    repo.close()


def test_merge_manual_replies_promotes_only_unreplied(tmp_path):
    repo = Repository(db_path=tmp_path / "t.db")
    repo.upsert_reply("BU_A", replied=True, replied_at=None, detected_by="auto")
    merged = repo.merge_manual_replies([
        ("BU_A", True, "", ""),               # 既に auto で返信済み → 変化なし
        ("BU_B", True, "2026-07-15", "電話あり"),  # 新規の手動チェック → 昇格
        ("BU_C", False, "", ""),              # 未チェック → 何もしない
        ("", True, "", ""),                   # 会員番号なし → 無視
    ])
    assert merged == 1
    row_b = repo.conn.execute("SELECT * FROM replies WHERE member_no='BU_B'").fetchone()
    assert row_b["replied"] == 1 and row_b["detected_by"] == "manual"
    assert row_b["note"] == "電話あり"
    assert repo.conn.execute(
        "SELECT COUNT(*) AS n FROM replies WHERE member_no='BU_C'").fetchone()["n"] == 0
    # BU_A は auto のまま。
    assert repo.conn.execute(
        "SELECT detected_by FROM replies WHERE member_no='BU_A'").fetchone()[0] == "auto"
    repo.close()


def test_analytics_rows_pivots_first_resend_and_joins_replies(tmp_path):
    repo = _repo_with_sent(tmp_path)
    repo.mark_sent("BU3765516", "resend", 5, channel="platinum")
    repo.upsert_reply("BU3765516", replied=True, replied_at="2026-07-16T09:00:00",
                      detected_by="manual")
    rows = repo.analytics_rows()
    assert len(rows) == 1
    r = rows[0]
    assert r["member_no"] == "BU3765516"
    assert r["first_sent_at"] and r["resent_at"]
    assert r["replied"] == 1 and r["detected_by"] == "manual"
    # 文面特徴（scouts JOIN）: 件名「【Premium Offer】初回」/ 本文「初回本文」の文字数。
    assert r["body_len"] == len("初回本文")
    assert r["subject_len"] == len("【Premium Offer】初回")
    repo.close()


def test_unreplied_sent_orders_oldest_first_and_limits(tmp_path):
    repo = Repository(db_path=tmp_path / "t.db")
    now = datetime(2026, 7, 16, 12, 0, 0)
    for i, days_ago in enumerate([1, 30, 60, 5]):  # 60日前は対象外(recent_days=45)
        mno = f"BU{i}"
        cand = make_candidate(member_no=mno)
        repo.upsert_candidate(cand, check_eligibility(cand))
        repo.record_generated(_scout(mno))
        sent = (now - timedelta(days=days_ago)).isoformat(timespec="seconds")
        repo.conn.execute(
            "UPDATE scouts SET status='sent', sent_at=? WHERE member_no=? AND kind='first'",
            (sent, mno))
        repo.conn.commit()
    repo.backfill_sent_log()
    # BU2(60日前)は期間外。古い順 = BU1(30日) → BU3(5日) → BU0(1日)。
    # 注: backfill は +9h するため実質 59.6日等になるが順序・境界に影響しない。
    rows = repo.unreplied_sent(recent_days=45, now=now + timedelta(hours=9), limit=2)
    assert [r["member_no"] for r in rows] == ["BU1", "BU3"]
    # 返信済みは対象から外れる。
    repo.upsert_reply("BU1", replied=True, replied_at=None, detected_by="auto")
    rows = repo.unreplied_sent(recent_days=45, now=now + timedelta(hours=9), limit=10)
    assert [r["member_no"] for r in rows] == ["BU3", "BU0"]
    repo.close()


def test_reconcile_auto_replies_promotes_and_removes_stale(tmp_path):
    repo = Repository(db_path=tmp_path / "t.db")
    # 受信箱由来（note が「受信箱…」）の既存返信。今回検知されなければ取り消す。
    repo.upsert_reply("BU_STALE", replied=True, replied_at=None, detected_by="auto",
                      note="受信箱にメッセージあり")
    # シート往復で manual 化した誤検知も、note で受信箱由来と判別して取り消す。
    repo.upsert_reply("BU_ROUNDTRIP", replied=True, replied_at=None, detected_by="manual",
                      note="受信箱にメッセージあり")
    # ユーザーの手動指定（note が「受信箱…」でない）は残す。
    repo.upsert_reply("BU_MANUAL", replied=True, replied_at=None, detected_by="manual",
                      note="手動")
    added, removed = repo.reconcile_auto_replies({"BU_NEW": "受信箱に返信（件名一致）"})
    assert added == 1                      # BU_NEW を昇格
    assert removed == 2                    # BU_STALE と BU_ROUNDTRIP を取消
    assert repo.is_replied("BU_NEW") is True
    assert repo.is_replied("BU_STALE") is False
    assert repo.is_replied("BU_ROUNDTRIP") is False    # manual でも受信箱由来なら消える
    assert repo.is_replied("BU_MANUAL") is True         # 真の手動指定は残る
    repo.close()


def test_meta_roundtrip(tmp_path):
    repo = Repository(db_path=tmp_path / "t.db")
    assert repo.get_meta("last_trend_at") is None
    repo.set_meta("last_trend_at", "2026-07-16T00:00:00")
    assert repo.get_meta("last_trend_at") == "2026-07-16T00:00:00"
    repo.set_meta("last_trend_at", "2026-07-17T00:00:00")  # 上書き
    assert repo.get_meta("last_trend_at") == "2026-07-17T00:00:00"
    repo.close()
