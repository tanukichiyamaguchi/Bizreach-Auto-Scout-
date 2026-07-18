"""SQLite による候補者・スカウト送信状況の永続化。

主目的:
- 重複送信の防止（同一会員番号に同一種別を二度送らない）
- 再送スケジュール（初回送信からN日後）の管理
- 監査ログ（生成・送信・失敗の履歴）
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from ..config import get_settings
from ..models import Candidate, EligibilityResult, GeneratedScout

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    member_no        TEXT PRIMARY KEY,
    profile_json     TEXT NOT NULL,
    eligible         INTEGER NOT NULL DEFAULT 0,
    eligibility_failed TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scouts (
    member_no    TEXT NOT NULL,
    kind         TEXT NOT NULL,          -- first / resend
    subject      TEXT NOT NULL,
    body         TEXT NOT NULL,
    status       TEXT NOT NULL,          -- generated / sending / sent / failed / skipped
    scheduled_at TEXT,                   -- 再送予定時刻
    sent_at      TEXT,
    error        TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    tone_key     TEXT NOT NULL DEFAULT '',
    analysis     TEXT NOT NULL DEFAULT '',
    -- 送信リクエストの冪等キー（x-idempotency-key）。送信前に永続化し、
    -- 「送信成功→sent記録」の間にクラッシュしても再試行が同一キーで送られる
    -- ことでサーバ側dedupeが効き、二重送信を防ぐ。
    idempotency_key TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    PRIMARY KEY (member_no, kind)
);

CREATE INDEX IF NOT EXISTS idx_scouts_status ON scouts(status);
CREATE INDEX IF NOT EXISTS idx_scouts_scheduled ON scouts(scheduled_at);

-- 送信イベントの分析ログ（append-only）。送信時点の候補者プロフィールを非正規化して
-- 保持し、週次・月次の返信率集計が candidates の後日変化に影響されないようにする。
CREATE TABLE IF NOT EXISTS sent_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_no TEXT NOT NULL,
    kind TEXT NOT NULL,                  -- first / resend
    channel TEXT NOT NULL DEFAULT '',    -- platinum / normal / pickup / ''(不明=backfill)
    sent_at TEXT NOT NULL,               -- naive JST ISO（workflow で TZ=Asia/Tokyo）
    tone_key TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',     -- candidate.source (bizreach / bizreach_pickup 等)
    age INTEGER,
    age_band TEXT NOT NULL DEFAULT '',
    gender TEXT NOT NULL DEFAULT '',
    education TEXT NOT NULL DEFAULT '',
    university TEXT NOT NULL DEFAULT '',
    current_company TEXT NOT NULL DEFAULT '',
    current_title TEXT NOT NULL DEFAULT '',
    job_change_count INTEGER,
    tenure_years REAL,
    salary_current TEXT NOT NULL DEFAULT '',
    candidate_class TEXT NOT NULL DEFAULT '',
    status_flags TEXT NOT NULL DEFAULT '',
    backfilled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(member_no, kind)
);
CREATE INDEX IF NOT EXISTS idx_sent_log_sent_at ON sent_log(sent_at);

-- 候補者ごとの返信状態。自動検知(auto)とシートの手動チェック(manual)の両方から更新される。
CREATE TABLE IF NOT EXISTS replies (
    member_no TEXT PRIMARY KEY,
    replied INTEGER NOT NULL DEFAULT 0,
    replied_at TEXT,
    detected_by TEXT NOT NULL DEFAULT '',   -- auto / manual
    candidate_name TEXT NOT NULL DEFAULT '',-- 返信等で開示された場合のみ記録
    note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

-- 分析まわりのメタ情報（傾向分析の最終生成時刻など）。
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Repository:
    def __init__(self, db_path: str | Path | None = None):
        settings = get_settings()
        self.path = Path(db_path) if db_path else settings.db_file
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """既存DBへの無停止マイグレーション（列が既にあれば何もしない）。"""
        # duplicate column name（追加済み）は OperationalError で無視する。
        with contextlib.suppress(sqlite3.OperationalError):
            self.conn.execute(
                "ALTER TABLE scouts ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''"
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Repository:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- 候補者 ---------------------------------------------------------------
    def upsert_candidate(self, candidate: Candidate, elig: EligibilityResult) -> None:
        now = _now_iso()
        self.conn.execute(
            """
            INSERT INTO candidates (member_no, profile_json, eligible, eligibility_failed,
                                    created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(member_no) DO UPDATE SET
                profile_json=excluded.profile_json,
                eligible=excluded.eligible,
                eligibility_failed=excluded.eligibility_failed,
                updated_at=excluded.updated_at
            """,
            (
                candidate.member_no,
                candidate.model_dump_json(),
                int(elig.eligible),
                " / ".join(elig.failed),
                now,
                now,
            ),
        )
        self.conn.commit()

    # --- 重複判定 -------------------------------------------------------------
    def first_sent(self, member_no: str) -> bool:
        """初回が実際に送信済みか。重複送信防止の唯一の基準。

        generated/skipped/failed は未送信のため再試行可能（送信漏れを防ぐ）。
        """
        row = self.conn.execute(
            "SELECT 1 FROM scouts WHERE member_no=? AND kind='first' AND status='sent'",
            (member_no,),
        ).fetchone()
        return row is not None

    def get_scout(self, member_no: str, kind: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM scouts WHERE member_no=? AND kind=?", (member_no, kind)
        ).fetchone()

    # --- スカウトの記録 -------------------------------------------------------
    def record_generated(self, scout: GeneratedScout) -> None:
        """初回・再送を generated 状態で保存する。

        再送の scheduled_at は初回送信時（mark_sent）に確定するため、ここでは設定しない。
        """
        now = _now_iso()
        # 初回（送信前なので scheduled_at は未設定）
        self._insert_scout(scout.member_no, "first", scout.first.subject, scout.first.body,
                            status="generated", scheduled_at=None, model=scout.model,
                            tone_key=scout.tone_key, analysis=scout.analysis, created_at=now)
        # 再送（初回送信時に scheduled_at を確定し直す）
        self._insert_scout(scout.member_no, "resend", scout.resend.subject, scout.resend.body,
                            status="generated", scheduled_at=None, model=scout.model,
                            tone_key=scout.tone_key, analysis="", created_at=now)

    def _insert_scout(self, member_no, kind, subject, body, status, scheduled_at,
                      model, tone_key, analysis, created_at) -> None:
        self.conn.execute(
            """
            INSERT INTO scouts (member_no, kind, subject, body, status, scheduled_at,
                                model, tone_key, analysis, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(member_no, kind) DO UPDATE SET
                subject=excluded.subject, body=excluded.body, status=excluded.status,
                scheduled_at=excluded.scheduled_at, model=excluded.model,
                tone_key=excluded.tone_key, analysis=excluded.analysis
            """,
            (member_no, kind, subject, body, status, scheduled_at, model, tone_key,
             analysis, created_at),
        )
        self.conn.commit()

    def begin_send(self, member_no: str, kind: str) -> str:
        """送信意図を記録し、この送信で使う冪等キーを返す（write-ahead）。

        - 直前の状態が 'sending'（＝送信したか不明のままクラッシュした）場合のみ
          **同じキー**を再利用する。再試行が同一キーで送信されるため、前回の
          リクエストが実は成功していてもサーバ側dedupeで二重送信にならない。
        - 確定失敗(failed)や未送信(generated)からの送信は新しいキーを発行する
          （確定失敗後の再試行は別リクエストとして扱うのが正しい）。
        - status を 'sending' にする（'sent' ではないので first_sent() は False の
          まま＝再試行対象。成功時に mark_sent で確定する）。
        """
        row = self.conn.execute(
            "SELECT status, idempotency_key FROM scouts WHERE member_no=? AND kind=?",
            (member_no, kind),
        ).fetchone()
        if row and row["status"] == "sending" and row["idempotency_key"]:
            key = row["idempotency_key"]
        else:
            key = str(uuid.uuid4())
        self.conn.execute(
            "UPDATE scouts SET status='sending', idempotency_key=? "
            "WHERE member_no=? AND kind=?",
            (key, member_no, kind),
        )
        self.conn.commit()
        return key

    def has_any_sent(self) -> bool:
        """送信済みレコードが1件でも存在するか（状態DB消失ガード用）。"""
        row = self.conn.execute(
            "SELECT 1 FROM scouts WHERE status='sent' LIMIT 1"
        ).fetchone()
        return row is not None

    def mark_sent(self, member_no: str, kind: str, resend_after_days: int = 5,
                  channel: str = "") -> None:
        now = datetime.now()
        self.conn.execute(
            "UPDATE scouts SET status='sent', sent_at=?, error='' WHERE member_no=? AND kind=?",
            (now.isoformat(timespec="seconds"), member_no, kind),
        )
        # 初回送信時に再送予定日を確定。
        if kind == "first":
            due = (now + timedelta(days=resend_after_days)).isoformat(timespec="seconds")
            self.conn.execute(
                "UPDATE scouts SET scheduled_at=? WHERE member_no=? AND kind='resend' "
                "AND status='generated'",
                (due, member_no),
            )
        self.conn.commit()
        # 分析ログ（失敗しても送信記録は壊さない）。
        with contextlib.suppress(Exception):
            self._log_sent_event(member_no, kind, channel,
                                 now.isoformat(timespec="seconds"))

    # --- 分析（sent_log / replies）--------------------------------------------
    def _log_sent_event(self, member_no: str, kind: str, channel: str,
                        sent_at: str, backfilled: int = 0) -> None:
        """送信イベントを sent_log へ追記する（INSERT OR IGNORE = 冪等）。

        送信時点のプロフィールを candidates.profile_json から非正規化して固定する。
        """
        from ..analytics.aggregate import age_band  # 循環回避のため遅延import

        scout = self.get_scout(member_no, kind)
        cand = self.load_candidate(member_no)
        profile: dict = {}
        if cand is not None:
            profile = {
                "source": cand.source,
                "age": cand.age,
                "age_band": age_band(cand.age),
                "gender": cand.gender.value,
                "education": cand.education.value,
                "university": cand.university,
                "current_company": cand.current_company,
                "current_title": cand.current_title,
                "job_change_count": cand.job_change_count(),
                "tenure_years": cand.current_tenure_years,
                "salary_current": cand.salary_current,
                "candidate_class": cand.candidate_class,
                "status_flags": "/".join(sorted(cand.status_flags())),
            }
        self.conn.execute(
            """
            INSERT OR IGNORE INTO sent_log
                (member_no, kind, channel, sent_at, tone_key, model, source,
                 age, age_band, gender, education, university,
                 current_company, current_title, job_change_count, tenure_years,
                 salary_current, candidate_class, status_flags, backfilled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                member_no, kind, channel, sent_at,
                scout["tone_key"] if scout else "",
                scout["model"] if scout else "",
                profile.get("source", ""),
                profile.get("age"),
                profile.get("age_band", ""),
                profile.get("gender", ""),
                profile.get("education", ""),
                profile.get("university", ""),
                profile.get("current_company", ""),
                profile.get("current_title", ""),
                profile.get("job_change_count"),
                profile.get("tenure_years"),
                profile.get("salary_current", ""),
                profile.get("candidate_class", ""),
                profile.get("status_flags", ""),
                backfilled,
                _now_iso(),
            ),
        )
        self.conn.commit()

    def backfill_sent_log(self) -> int:
        """既存の送信済み scouts を sent_log へ補完する（冪等・自己修復）。

        過去の sent_at はCI（UTC）で記録された naive 時刻のため +9h して JST に揃える。
        既に sent_log にある (member_no, kind) は INSERT OR IGNORE でスキップされる。
        """
        rows = self.conn.execute(
            "SELECT member_no, kind, sent_at FROM scouts "
            "WHERE status='sent' AND sent_at IS NOT NULL"
        ).fetchall()
        before = self.conn.execute("SELECT COUNT(*) AS n FROM sent_log").fetchone()["n"]
        for r in rows:
            with contextlib.suppress(Exception):
                dt = datetime.fromisoformat(r["sent_at"]) + timedelta(hours=9)
                self._log_sent_event(r["member_no"], r["kind"], "",
                                     dt.isoformat(timespec="seconds"), backfilled=1)
        after = self.conn.execute("SELECT COUNT(*) AS n FROM sent_log").fetchone()["n"]
        return after - before

    def analytics_rows(self) -> list[sqlite3.Row]:
        """分析用: 会員単位に first/resend をピボットし replies を LEFT JOIN した行。

        scouts との JOIN で文面の特徴（件名・本文の文字数）も取得する
        （「どのような文面で送ると返信率が高いか」の分析用）。
        """
        return self.conn.execute(
            """
            SELECT
                f.member_no,
                f.sent_at            AS first_sent_at,
                rs.sent_at           AS resent_at,
                f.channel, f.tone_key, f.model, f.source,
                f.age, f.age_band, f.gender, f.education, f.university,
                f.current_company, f.current_title,
                f.job_change_count, f.tenure_years, f.salary_current,
                f.candidate_class, f.status_flags,
                LENGTH(sc.subject)           AS subject_len,
                LENGTH(sc.body)              AS body_len,
                COALESCE(rp.replied, 0)      AS replied,
                rp.replied_at,
                COALESCE(rp.detected_by, '') AS detected_by,
                COALESCE(rp.candidate_name, '') AS candidate_name,
                COALESCE(rp.note, '')        AS note
            FROM sent_log f
            LEFT JOIN sent_log rs ON rs.member_no = f.member_no AND rs.kind = 'resend'
            LEFT JOIN scouts sc ON sc.member_no = f.member_no AND sc.kind = 'first'
            LEFT JOIN replies rp ON rp.member_no = f.member_no
            WHERE f.kind = 'first'
            ORDER BY f.sent_at ASC, f.member_no ASC
            """
        ).fetchall()

    def upsert_reply(self, member_no: str, *, replied: bool, replied_at: str | None,
                     detected_by: str, candidate_name: str = "", note: str = "") -> None:
        """返信状態を昇格方向のみで更新する（replied=1 を 0 に戻さない）。"""
        self.conn.execute(
            """
            INSERT INTO replies (member_no, replied, replied_at, detected_by,
                                 candidate_name, note, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(member_no) DO UPDATE SET
                replied     = MAX(replies.replied, excluded.replied),
                replied_at  = COALESCE(replies.replied_at, excluded.replied_at),
                detected_by = CASE WHEN replies.replied = 1
                                   THEN replies.detected_by ELSE excluded.detected_by END,
                candidate_name = CASE WHEN excluded.candidate_name != ''
                                      THEN excluded.candidate_name
                                      ELSE replies.candidate_name END,
                note = CASE WHEN excluded.note != '' THEN excluded.note ELSE replies.note END,
                updated_at = excluded.updated_at
            """,
            (member_no, int(replied), replied_at, detected_by,
             candidate_name, note, _now_iso()),
        )
        self.conn.commit()

    def merge_manual_replies(self, entries: list[tuple[str, bool, str, str]]) -> int:
        """シートから読み戻した手動チェックをDBへマージする。

        entries: (member_no, checked, replied_at_str, note)。checked=True かつ DB 未返信の
        もののみ manual として昇格する（False によるDBの取り消しはしない＝自動検知優先）。
        戻り値は新たに返信扱いになった件数。
        """
        merged = 0
        for member_no, checked, replied_at, note in entries:
            if not checked or not member_no:
                continue
            row = self.conn.execute(
                "SELECT replied FROM replies WHERE member_no=?", (member_no,)
            ).fetchone()
            if row and row["replied"]:
                continue
            self.upsert_reply(member_no, replied=True,
                              replied_at=replied_at or None,
                              detected_by="manual", note=note)
            merged += 1
        return merged

    def unreplied_sent(self, *, recent_days: int, now: datetime | None = None,
                       limit: int = 60) -> list[sqlite3.Row]:
        """自動返信チェックの対象: 未返信かつ初回送信が recent_days 以内（古い順）。"""
        now = now or datetime.now()
        cutoff = (now - timedelta(days=recent_days)).isoformat(timespec="seconds")
        return self.conn.execute(
            """
            SELECT f.member_no, f.sent_at
            FROM sent_log f
            LEFT JOIN replies rp ON rp.member_no = f.member_no
            WHERE f.kind='first' AND f.sent_at >= ?
              AND COALESCE(rp.replied, 0) = 0
            ORDER BY f.sent_at ASC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def mark_failed(self, member_no: str, kind: str, error: str) -> None:
        self.conn.execute(
            "UPDATE scouts SET status='failed', error=? WHERE member_no=? AND kind=?",
            (error[:1000], member_no, kind),
        )
        self.conn.commit()

    def mark_skipped(self, member_no: str, kind: str, reason: str) -> None:
        self.conn.execute(
            "UPDATE scouts SET status='skipped', error=? WHERE member_no=? AND kind=?",
            (reason[:1000], member_no, kind),
        )
        self.conn.commit()

    # --- 再送スケジュール -----------------------------------------------------
    def due_resends(self, now: datetime | None = None) -> list[sqlite3.Row]:
        now = now or datetime.now()
        # 'sending' も対象に含める: 送信途中でクラッシュした再送を取り残さない
        # （begin_send が同一冪等キーを再利用するため二重送信にはならない）。
        return self.conn.execute(
            """
            SELECT * FROM scouts
            WHERE kind='resend' AND status IN ('generated', 'sending')
              AND scheduled_at IS NOT NULL AND scheduled_at <= ?
            ORDER BY scheduled_at ASC
            """,
            (now.isoformat(timespec="seconds"),),
        ).fetchall()

    # --- レポート -------------------------------------------------------------
    def counts_by_status(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM scouts GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def ineligible_candidates(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT member_no, eligibility_failed FROM candidates WHERE eligible=0"
        ).fetchall()

    def load_candidate(self, member_no: str) -> Candidate | None:
        row = self.conn.execute(
            "SELECT profile_json FROM candidates WHERE member_no=?", (member_no,)
        ).fetchone()
        if not row:
            return None
        return Candidate(**json.loads(row["profile_json"]))
