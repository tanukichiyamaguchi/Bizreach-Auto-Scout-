"""Sheets 同期（sync.py）のテスト。FakeSheets（インメモリ）でネットワーク不要。"""

from __future__ import annotations

from datetime import datetime

import pytest

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


@pytest.fixture(autouse=True)
def _no_shipped_backfills(monkeypatch):
    """出荷中の復元表（実データ56件等）がテストDBに混入しないよう既定は空にする。

    復元の挙動を検証するテストは、この後から個別に monkeypatch で上書きする。
    """
    from bizreach_scout.analytics import sync as sync_mod

    monkeypatch.setattr(sync_mod, "load_sent_backfill", lambda: [])
    monkeypatch.setattr(sync_mod, "load_channel_backfill", lambda: [])


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
    # チェックボックス入力規則 + チャート6種（週次/月次/年齢帯/学歴/曜日/時間帯）。
    kinds = [next(iter(r)) for r in sheets.batch_requests]
    assert kinds.count("setDataValidation") == 1
    assert kinds.count("addChart") == 6
    assert report.charts == 6


def test_weekly_sheet_has_channel_breakdown_after_chart_columns(tmp_path):
    # 週次シートに送信内訳（通常スカウト/ピックアップ/内訳不明）が出ること。
    # かつ、コンボチャートが参照する列0/1/3の並びが変わっていないこと（内訳は末尾追加）。
    repo = _repo_with_sent(tmp_path, members=("BU1",))
    # BU1 は platinum。ピックアップ送信の候補者を1名足す。
    cand = make_candidate(member_no="BU9")
    repo.upsert_candidate(cand, check_eligibility(cand))
    repo.record_generated(GeneratedScout(
        member_no="BU9", first=ScoutContent(subject="s", body="b"),
        resend=ScoutContent(subject="s2", body="b2"), model="m", tone_key="early30s"))
    repo.mark_sent("BU9", "first", 5, channel="pickup")
    # 送信日時を集計対象週（2026-07-13〜19）に固定して決定的にする（mark_sent は現在時刻）。
    repo.conn.execute("UPDATE sent_log SET sent_at='2026-07-14T10:00:00'")
    repo.conn.commit()

    sheets = FakeSheets()
    sync_analytics(repo, sheets, now=datetime(2026, 7, 16, 12, 0),
                   with_charts=False, trend_fn=None)
    repo.close()

    header = sheets.data[WEEKLY_SHEET][0]
    # チャートが参照する先頭列の並びは不変。
    assert header[:6] == ["週", "送信数", "返信数", "返信率(%)", "累計送信", "累計返信率(%)"]
    # 内訳列は末尾に追加されている。
    assert "通常スカウト送信" in header and "ピックアップ送信" in header
    assert "内訳不明送信" in header
    assert header.index("通常スカウト送信") >= 6
    i_normal = header.index("通常スカウト送信")
    i_pickup = header.index("ピックアップ送信")
    # 当週の行を取り、内訳が送信数と一致することを確認。
    data_rows = [r for r in sheets.data[WEEKLY_SHEET][1:] if r and str(r[0]).startswith("2026-W")]
    total_sent = sum(int(r[1]) for r in data_rows)
    assert total_sent == 2
    assert sum(int(r[i_normal]) for r in data_rows) == 1   # platinum → 通常スカウト
    assert sum(int(r[i_pickup]) for r in data_rows) == 1   # pickup


def test_sync_reads_manual_input_column_not_display_column(tmp_path):
    repo = _repo_with_sent(tmp_path)
    # 前回同期のシートを再現: 表示列「返信」は自動判定の投影（読み戻さない）。
    # 入力列「手動で返信ありにする」に人が BU2 だけチェックした状態。
    header = ["会員番号", "返信", "返信日", "検知", "メモ", "手動で返信ありにする"]
    sheets = FakeSheets({SENT_LOG_SHEET: [
        ["注記"], header,
        # BU1 は表示列が TRUE でも入力列は空 → 読み戻さない（往復ループ防止）。
        ["BU1", "TRUE", "", "自動", "受信箱にメッセージあり", ""],
        ["BU2", "FALSE", "", "", "", "TRUE"],
    ]})
    report = sync_analytics(repo, sheets, now=datetime(2026, 7, 16, 12, 0),
                            with_charts=False, trend_fn=None)
    # 取り込まれるのは入力列にチェックのある BU2 のみ（BU1 の表示 TRUE は無視）。
    assert report.manual_merged == 1
    assert report.replied == 1
    # DB には BU2 が manual として記録され、BU1 は返信なしのまま。
    row = repo.conn.execute("SELECT * FROM replies WHERE member_no='BU2'").fetchone()
    assert row["replied"] == 1 and row["detected_by"] == "manual"
    assert repo.conn.execute(
        "SELECT COUNT(*) AS n FROM replies WHERE member_no='BU1'").fetchone()["n"] == 0
    # 書き換え後: 入力列は常に空、表示列は DB を反映。
    log = sheets.data[SENT_LOG_SHEET]
    header_idx = next(i for i, row in enumerate(log) if "会員番号" in row)
    disp_col = log[header_idx].index("返信")
    input_col = log[header_idx].index("手動で返信ありにする")
    by_member = {row[0]: row for row in log[header_idx + 1:]}
    assert by_member["BU2"][disp_col] is True
    assert by_member["BU2"][input_col] == ""      # 入力列は取り込み後クリア
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


def test_read_manual_entries_reads_only_input_column():
    # 注記行があってもヘッダーを特定でき、入力列だけを読む。
    rows = [["自動更新の注記"],
            ["会員番号", "氏名", "返信", "メモ", "手動で返信ありにする"],
            ["BU9", "山田", "FALSE", "受信箱にメッセージあり", "TRUE"],   # 入力列TRUE → 取り込む
            ["BU8", "田中", "TRUE", "受信箱に返信（件名一致）", ""]]        # 表示TRUEだが入力空 → 無視
    assert read_manual_entries(rows) == [("BU9", True, "", "手動")]
    # 入力列が無い（旧レイアウト）なら空。往復ループを起こさない。
    assert read_manual_entries([["会員番号", "返信あり"], ["BU1", "TRUE"]]) == []
    assert read_manual_entries([["foo", "bar"]]) == []
    assert read_manual_entries([]) == []


def test_sync_fills_missing_channels_from_backfill_table(tmp_path, monkeypatch):
    """送信枠が空の過去行が、対応表から復元されて週次の内訳に載る。"""
    from bizreach_scout.analytics import sync as sync_mod

    repo = Repository(db_path=tmp_path / "t.db")
    # 送信枠を記録する前の過去分（channel が空）を2件用意する。
    for mno, sent_at in (("BU1", "2026-07-06T10:00:00"), ("BU2", "2026-07-07T10:00:00")):
        repo.conn.execute(
            "INSERT INTO sent_log (member_no, kind, channel, sent_at, created_at,"
            " backfilled) VALUES (?, 'first', '', ?, ?, 1)", (mno, sent_at, sent_at))
    repo.conn.commit()

    monkeypatch.setattr(sync_mod, "load_channel_backfill", lambda: [
        ("BU1", "first", "pickup"), ("BU2", "first", "platinum")])
    sheets = FakeSheets()
    report = sync_analytics(repo, sheets, now=datetime(2026, 7, 16, 12, 0),
                            with_charts=False, trend_fn=None)
    repo.close()

    assert report.channels_filled == 2
    header, *rows = sheets.data[WEEKLY_SHEET]
    i_normal = header.index("通常スカウト送信")
    i_pickup = header.index("ピックアップ送信")
    i_unknown = header.index("内訳不明送信")
    week = next(r for r in rows if r and str(r[0]).startswith("2026-W28"))
    assert (week[i_normal], week[i_pickup], week[i_unknown]) == (1, 1, 0)


def test_sync_restores_lost_sends_into_weekly(tmp_path, monkeypatch):
    """消失した送信が復元され、週次の分母と内訳に反映される。"""
    from bizreach_scout.analytics import sync as sync_mod

    repo = Repository(db_path=tmp_path / "t.db")
    monkeypatch.setattr(sync_mod, "load_sent_backfill", lambda: [
        ("BU7777777", "first", "pickup", "2026-07-06T18:00:00"),
        ("BU8888888", "first", "platinum", "2026-07-07T18:00:00"),
    ])
    monkeypatch.setattr(sync_mod, "load_channel_backfill", lambda: [])
    sheets = FakeSheets()
    report = sync_analytics(repo, sheets, now=datetime(2026, 7, 16, 12, 0),
                            with_charts=False, trend_fn=None)
    repo.close()

    assert report.lost_restored == 2
    assert report.members == 2
    header, *rows = sheets.data[WEEKLY_SHEET]
    week = next(r for r in rows if r and str(r[0]).startswith("2026-W28"))
    i_sent = header.index("送信数")
    i_normal = header.index("通常スカウト送信")
    i_pickup = header.index("ピックアップ送信")
    assert (week[i_sent], week[i_normal], week[i_pickup]) == (2, 1, 1)


# --- シートの巻き戻り防止 ----------------------------------------------------

def _sheet_with_members(members: list[str]) -> FakeSheets:
    """会員番号入りの送信ログシートを模擬する（注記行 + ヘッダー + データ）。"""
    from bizreach_scout.analytics.sync import SENT_LOG_HEADER, SENT_LOG_NOTE

    rows = [[SENT_LOG_NOTE], list(SENT_LOG_HEADER)]
    for m in members:
        rows.append([m] + [""] * (len(SENT_LOG_HEADER) - 1))
    return FakeSheets({SENT_LOG_SHEET: rows})


def test_sync_refuses_to_shrink_the_sheet(tmp_path):
    """シートに記録があるのにDBが空なら、上書きせず中止する。"""
    from bizreach_scout.analytics.sync import SheetRegressionError

    repo = Repository(db_path=tmp_path / "t.db")          # 巻き戻った空のDB
    sheets = _sheet_with_members([f"BU{i:07d}" for i in range(1, 21)])
    before = [list(r) for r in sheets.data[SENT_LOG_SHEET]]

    with pytest.raises(SheetRegressionError) as ei:
        sync_analytics(repo, sheets, now=datetime(2026, 8, 23, 12, 0),
                       with_charts=False, trend_fn=None)
    repo.close()

    assert "20 件" in str(ei.value)
    assert "restore-db" in str(ei.value)
    # シートは1行も書き換わっていない。
    assert sheets.data[SENT_LOG_SHEET] == before
    assert WEEKLY_SHEET not in sheets.data


def test_sync_allows_shrink_when_explicitly_forced(tmp_path):
    """--allow-shrink 相当の指定があれば従来どおり書き換える。"""
    repo = Repository(db_path=tmp_path / "t.db")
    sheets = _sheet_with_members([f"BU{i:07d}" for i in range(1, 21)])
    report = sync_analytics(repo, sheets, now=datetime(2026, 8, 23, 12, 0),
                            with_charts=False, trend_fn=None, allow_shrink=True)
    repo.close()
    assert report.members == 0
    assert WEEKLY_SHEET in sheets.data


def test_sync_allows_growth_and_equal_counts(tmp_path, monkeypatch):
    """件数が増える/同数の通常ケースは従来どおり通る。"""
    from bizreach_scout.analytics import sync as sync_mod

    repo = Repository(db_path=tmp_path / "t.db")
    monkeypatch.setattr(sync_mod, "load_sent_backfill", lambda: [
        ("BU0000001", "first", "pickup", "2026-08-18T18:00:00"),
        ("BU0000002", "first", "platinum", "2026-08-18T18:10:00"),
    ])
    sheets = _sheet_with_members(["BU0000001"])           # シートは1件、DBは2件
    report = sync_analytics(repo, sheets, now=datetime(2026, 8, 23, 12, 0),
                            with_charts=False, trend_fn=None)
    repo.close()
    assert report.members == 2
    assert WEEKLY_SHEET in sheets.data


def test_count_sheet_members_ignores_blank_and_missing_header():
    from bizreach_scout.analytics.sync import SENT_LOG_HEADER, count_sheet_members

    rows = [["注記"], list(SENT_LOG_HEADER), ["BU1111111"], [""], ["BU2222222"]]
    assert count_sheet_members(rows) == 2
    assert count_sheet_members([]) == 0                   # 初回（シート無し）
    assert count_sheet_members([["無関係"], ["データ"]]) == 0
