"""Sheets 同期（sync.py）のテスト。FakeSheets（インメモリ）でネットワーク不要。"""

from __future__ import annotations

from datetime import datetime

from bizreach_scout.analytics.sync import (
    MONTHLY_SHEET,
    SEGMENT_SHEET,
    SENT_LOG_SHEET,
    TREND_SHEET,
    WEEKLY_SHEET,
    read_manual_entries,
    sync_analytics,
)
from bizreach_scout.eligibility import check_eligibility
from bizreach_scout.models import GeneratedScout, ScoutContent
from bizreach_scout.storage.repository import Repository

from .factories import make_candidate


class FakeSheets:
    """SheetsPort のインメモリ実装。batch_update の内容も記録する。"""

    def __init__(self, initial: dict[str, list[list[str]]] | None = None):
        self.data: dict[str, list[list[str]]] = dict(initial or {})
        self.batch_requests: list[dict] = []
        self.reads: list[str] = []
        self._ids: dict[str, int] = {}

    def read_rows(self, title):
        self.reads.append(title)
        return [list(map(str, row)) for row in self.data.get(title, [])]

    def write_rows(self, title, rows):
        self.data[title] = [list(r) for r in rows]

    def batch_update(self, requests):
        self.batch_requests.extend(requests)
        return {}

    def sheet_id(self, title):
        return self._ids.setdefault(title, 100 + len(self._ids))

    def fetch_charts(self):
        # 既存チャートなし（addChart 経路）。更新経路は ensure_charts の単体で検証。
        return {}


def _repo_with_sent(tmp_path, members=("BU1", "BU2")) -> Repository:
    repo = Repository(db_path=tmp_path / "t.db")
    for mno in members:
        cand = make_candidate(member_no=mno)
        repo.upsert_candidate(cand, check_eligibility(cand))
        repo.record_generated(GeneratedScout(
            member_no=mno,
            first=ScoutContent(subject="s", body="b"),
            resend=ScoutContent(subject="s2", body="b2"),
            model="m", tone_key="early30s"))
        repo.mark_sent(mno, "first", 5, channel="platinum")
    return repo


def test_sync_writes_all_sheets_and_checkbox_rule(tmp_path):
    repo = _repo_with_sent(tmp_path)
    sheets = FakeSheets()
    report = sync_analytics(repo, sheets, now=datetime(2026, 7, 16, 12, 0),
                            with_charts=True, trend_fn=None)
    repo.close()

    assert report.members == 2
    for title in (SENT_LOG_SHEET, WEEKLY_SHEET, MONTHLY_SHEET, SEGMENT_SHEET):
        assert title in sheets.data
    # 送信ログ: 注記 + ヘッダー + 2名。
    log = sheets.data[SENT_LOG_SHEET]
    assert len(log) == 4
    assert "会員番号" in log[1]
    members = {row[0] for row in log[2:]}
    assert members == {"BU1", "BU2"}
    # チェックボックス入力規則 + チャート4種が batch_update に含まれる。
    kinds = [next(iter(r)) for r in sheets.batch_requests]
    assert kinds.count("setDataValidation") == 1
    assert kinds.count("addChart") == 4
    assert report.charts == 4


def test_sync_reads_manual_checks_before_rewrite(tmp_path):
    repo = _repo_with_sent(tmp_path)
    # 前回の同期結果に、人が BU2 へ返信チェックを入れた状態を再現。
    header = ["会員番号", "返信あり", "返信日", "メモ"]
    sheets = FakeSheets({SENT_LOG_SHEET: [
        ["注記"], header,
        ["BU1", "FALSE", "", ""],
        ["BU2", "TRUE", "2026-07-15", "電話面談へ"],
    ]})
    report = sync_analytics(repo, sheets, now=datetime(2026, 7, 16, 12, 0),
                            with_charts=False, trend_fn=None)
    assert report.manual_merged == 1
    assert report.replied == 1
    # 書き換え後のシートにも返信状態が反映されている（会員番号キーで引き継ぎ）。
    log = sheets.data[SENT_LOG_SHEET]
    header_idx = next(i for i, row in enumerate(log) if "会員番号" in row)
    replied_col = log[header_idx].index("返信あり")
    note_col = log[header_idx].index("メモ")
    by_member = {row[0]: row for row in log[header_idx + 1:]}
    assert by_member["BU2"][replied_col] is True
    assert by_member["BU2"][note_col] == "電話面談へ"
    assert by_member["BU1"][replied_col] is False
    # DB 側にも manual として記録されている。
    row = repo.conn.execute("SELECT * FROM replies WHERE member_no='BU2'").fetchone()
    assert row["replied"] == 1 and row["detected_by"] == "manual"
    repo.close()


def test_sync_is_deterministic_on_rerun(tmp_path):
    repo = _repo_with_sent(tmp_path)
    sheets = FakeSheets()
    sync_analytics(repo, sheets, now=datetime(2026, 7, 16), with_charts=False, trend_fn=None)
    first = {k: [list(map(str, r)) for r in v] for k, v in sheets.data.items()}
    sync_analytics(repo, sheets, now=datetime(2026, 7, 16), with_charts=False, trend_fn=None)
    second = {k: [list(map(str, r)) for r in v] for k, v in sheets.data.items()}
    assert first == second
    repo.close()


def test_trend_generated_weekly_and_recorded_in_meta(tmp_path):
    repo = _repo_with_sent(tmp_path, members=tuple(f"BU{i}" for i in range(12)))
    sheets = FakeSheets()
    calls: list[int] = []

    def fake_trend(weekly, monthly, segments):
        calls.append(1)
        return "今週の実績:\n- 送信12件"

    now = datetime(2026, 7, 16, 12, 0)
    r1 = sync_analytics(repo, sheets, now=now, with_charts=False, trend_fn=fake_trend)
    assert r1.trend_refreshed is True
    assert TREND_SHEET in sheets.data
    assert any("送信12件" in "".join(map(str, row)) for row in sheets.data[TREND_SHEET])
    # 同日再実行では再生成しない（週1回制御）。
    r2 = sync_analytics(repo, sheets, now=now, with_charts=False, trend_fn=fake_trend)
    assert r2.trend_refreshed is False
    assert len(calls) == 1
    repo.close()


def test_trend_skipped_when_too_few_sends(tmp_path):
    repo = _repo_with_sent(tmp_path)  # 2名 < trend_min_sends(10)
    sheets = FakeSheets()
    report = sync_analytics(repo, sheets, now=datetime(2026, 7, 16),
                            with_charts=False, trend_fn=lambda *a: "x")
    assert report.trend_refreshed is False
    repo.close()


def test_read_manual_entries_handles_note_row_and_missing_columns():
    # 注記行があってもヘッダーを特定できる。
    rows = [["自動更新の注記"],
            ["会員番号", "氏名", "返信あり", "返信日", "メモ"],
            ["BU9", "山田", "TRUE", "7/15", "ok"]]
    assert read_manual_entries(rows) == [("BU9", True, "7/15", "ok")]
    # ヘッダーが見つからなければ空。
    assert read_manual_entries([["foo", "bar"]]) == []
    assert read_manual_entries([]) == []
