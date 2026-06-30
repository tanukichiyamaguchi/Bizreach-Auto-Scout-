"""SQLite による候補者・スカウト送信状況の永続化。

主目的:
- 重複送信の防止（同一会員番号に同一種別を二度送らない）
- 再送スケジュール（初回送信からN日後）の管理
- 監査ログ（生成・送信・失敗の履歴）
"""

from __future__ import annotations

import json
import sqlite3
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
    status       TEXT NOT NULL,          -- generated / sent / failed / skipped
    scheduled_at TEXT,                   -- 再送予定時刻
    sent_at      TEXT,
    error        TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    tone_key     TEXT NOT NULL DEFAULT '',
    analysis     TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    PRIMARY KEY (member_no, kind)
);

CREATE INDEX IF NOT EXISTS idx_scouts_status ON scouts(status);
CREATE INDEX IF NOT EXISTS idx_scouts_scheduled ON scouts(scheduled_at);
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
        self.conn.commit()

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
    def first_already_handled(self, member_no: str) -> bool:
        """初回が生成済み（=処理済み）かどうか。重複生成・重複送信を防ぐ。"""
        row = self.conn.execute(
            "SELECT 1 FROM scouts WHERE member_no=? AND kind='first'", (member_no,)
        ).fetchone()
        return row is not None

    def get_scout(self, member_no: str, kind: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM scouts WHERE member_no=? AND kind=?", (member_no, kind)
        ).fetchone()

    # --- スカウトの記録 -------------------------------------------------------
    def record_generated(self, scout: GeneratedScout, resend_after_days: int) -> None:
        """初回・再送を generated 状態で保存。再送には scheduled_at を仮設定。"""
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

    def mark_sent(self, member_no: str, kind: str, resend_after_days: int = 5) -> None:
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
        return self.conn.execute(
            """
            SELECT * FROM scouts
            WHERE kind='resend' AND status='generated'
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
