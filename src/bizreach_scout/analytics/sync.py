"""DB → Google Sheets の同期（送信ログ・週次/月次・セグメント・チャート・傾向分析）。

方針:
- DB が唯一の正。シートは投影＋入力チャネル（返信あり/返信日/メモ 列のみ読み戻す）。
- 書き換えは全行の決定的リライト（sent_at, member_no 順）＝何度実行しても同じ結果。
- チャートは spec タイトルで既存を特定し、あれば更新・なければ作成（冪等）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..logging_config import logger
from ..storage.repository import Repository
from .aggregate import (
    PeriodStat,
    SegmentTable,
    SentRecord,
    monthly_summary,
    standard_segments,
    weekly_summary,
)
from .sheets import SheetsPort

SENT_LOG_SHEET = "送信ログ"
WEEKLY_SHEET = "週次サマリ"
MONTHLY_SHEET = "月次サマリ"
SEGMENT_SHEET = "セグメント分析"
TREND_SHEET = "傾向分析"

SENT_LOG_HEADER = [
    "会員番号", "氏名", "初回送信日", "再送日", "送信枠", "トーン",
    "年齢", "年齢帯", "性別", "学歴", "大学", "現職企業", "役職",
    "転職回数", "現職在籍年数", "想定年収", "会員クラス", "ステータス",
    "返信あり", "返信日", "検知", "メモ",
]
SENT_LOG_NOTE = ("このシートは自動更新されます。手動編集が保持されるのは"
                 "「返信あり」「返信日」「メモ」列のみです。")

# セグメント分析シート: 各表を固定アンカー行に書く（チャートの参照範囲を安定させる）。
SEGMENT_BLOCK_ROWS = 13  # 表タイトル1 + ヘッダー1 + データ最大9 + 空行2
SEGMENT_MAX_ROWS = 9

_GENDER_LABELS = {"male": "男性", "female": "女性", "unknown": "不明", "": "不明"}


@dataclass
class SyncReport:
    backfilled: int = 0
    manual_merged: int = 0
    members: int = 0
    replied: int = 0
    charts: int = 0
    trend_refreshed: bool = False
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"分析同期: 対象{self.members}名 / 返信{self.replied}名 / "
                f"backfill+{self.backfilled} / 手動マージ{self.manual_merged}件 / "
                f"チャート{self.charts}件 / 傾向分析更新={self.trend_refreshed}")


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def _find_col(header: list[str], name: str) -> int:
    try:
        return header.index(name)
    except ValueError:
        return -1


def read_manual_entries(rows: list[list[str]]) -> list[tuple[str, bool, str, str]]:
    """既存の送信ログシートから (会員番号, 返信あり, 返信日, メモ) を抽出する。

    ヘッダー行は「会員番号」を含む行を探して特定する（先頭の注記行に依存しない）。
    """
    header_idx = next((i for i, row in enumerate(rows[:5]) if "会員番号" in row), -1)
    if header_idx < 0:
        return []
    header = rows[header_idx]
    c_member = _find_col(header, "会員番号")
    c_replied = _find_col(header, "返信あり")
    c_replied_at = _find_col(header, "返信日")
    c_note = _find_col(header, "メモ")
    if c_member < 0 or c_replied < 0:
        return []
    out: list[tuple[str, bool, str, str]] = []
    for row in rows[header_idx + 1:]:
        member = row[c_member].strip() if c_member < len(row) else ""
        if not member:
            continue
        checked = (row[c_replied].strip().upper() == "TRUE") if c_replied < len(row) else False
        replied_at = row[c_replied_at].strip() if 0 <= c_replied_at < len(row) else ""
        note = row[c_note].strip() if 0 <= c_note < len(row) else ""
        out.append((member, checked, replied_at, note))
    return out


def _sent_log_rows(records: list[SentRecord]) -> list[list[object]]:
    from .aggregate import channel_label, education_label

    rows: list[list[object]] = [[SENT_LOG_NOTE], list(SENT_LOG_HEADER)]
    for r in records:
        rows.append([
            r.member_no,
            r.candidate_name,
            _fmt_dt(r.first_sent_at),
            _fmt_dt(r.resent_at),
            channel_label(r.channel),
            r.tone_key,
            r.age if r.age is not None else "",
            r.age_band,
            _GENDER_LABELS.get(r.gender, r.gender or "不明"),
            education_label(r.education),
            r.university,
            r.current_company,
            r.current_title,
            r.job_change_count if r.job_change_count is not None else "",
            r.tenure_years if r.tenure_years is not None else "",
            r.salary_current,
            r.candidate_class,
            r.status_flags,
            bool(r.replied),
            _fmt_dt(r.replied_at) or (r.replied_at or ""),
            {"auto": "自動", "manual": "手動"}.get(r.detected_by, r.detected_by),
            r.note,
        ])
    return rows


def _period_rows(stats: list[PeriodStat], label_header: str) -> list[list[object]]:
    rows: list[list[object]] = [
        [label_header, "送信数", "返信数", "返信率(%)", "累計送信", "累計返信率(%)"],
    ]
    cum_sent = cum_replied = 0
    for s in stats:
        cum_sent += s.sent
        cum_replied += s.replied
        rows.append([
            s.label, s.sent, s.replied, round(s.rate * 100, 1),
            cum_sent,
            round((cum_replied / cum_sent * 100) if cum_sent else 0.0, 1),
        ])
    rows.append([])
    rows.append(["※返信は初回送信の週/月に帰属します。直近の期間は返信がまだ届き得るため低めに出ます。"])
    return rows


def _segment_rows(tables: list[SegmentTable]) -> list[list[object]]:
    """固定アンカー（SEGMENT_BLOCK_ROWS 間隔）で6表を1シートに配置する。"""
    rows: list[list[object]] = []
    for i, table in enumerate(tables):
        target_start = i * SEGMENT_BLOCK_ROWS
        while len(rows) < target_start:
            rows.append([])
        rows.append([f"■ {table.title}"])
        rows.append(["セグメント", "送信数", "返信数", "返信率(%)"])
        for seg in table.rows[:SEGMENT_MAX_ROWS]:
            rows.append([seg.segment, seg.sent, seg.replied, round(seg.rate * 100, 1)])
    return rows


def _checkbox_validation_request(sheet_id: int, n_members: int) -> dict:
    """送信ログの「返信あり」列にチェックボックスの入力規則を適用する。

    行構成は 注記(0) / ヘッダー(1) / データ(2〜) の 0-based 前提。
    """
    col = SENT_LOG_HEADER.index("返信あり")
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 2,
                "endRowIndex": max(3, n_members + 2),
                "startColumnIndex": col,
                "endColumnIndex": col + 1,
            },
            "rule": {"condition": {"type": "BOOLEAN"}, "strict": True},
        }
    }


def _grid_range(sheet_id: int, start_row: int, end_row: int,
                start_col: int, end_col: int) -> dict:
    return {
        "sheetId": sheet_id,
        "startRowIndex": start_row, "endRowIndex": end_row,
        "startColumnIndex": start_col, "endColumnIndex": end_col,
    }


def _combo_chart_spec(sheet_id: int, n_rows: int, title: str) -> dict:
    """送信数（棒・左軸）×返信率%（線・右軸）のコンボチャート spec。"""
    end = n_rows + 1  # ヘッダー1行 + データ n_rows
    return {
        "title": title,
        "basicChart": {
            "chartType": "COMBO",
            "legendPosition": "BOTTOM_LEGEND",
            "headerCount": 1,
            "axis": [
                {"position": "BOTTOM_AXIS"},
                {"position": "LEFT_AXIS", "title": "送信数"},
                {"position": "RIGHT_AXIS", "title": "返信率(%)"},
            ],
            "domains": [{"domain": {"sourceRange": {"sources": [
                _grid_range(sheet_id, 0, end, 0, 1)]}}}],
            "series": [
                {"series": {"sourceRange": {"sources": [
                    _grid_range(sheet_id, 0, end, 1, 2)]}},
                 "targetAxis": "LEFT_AXIS", "type": "COLUMN"},
                {"series": {"sourceRange": {"sources": [
                    _grid_range(sheet_id, 0, end, 3, 4)]}},
                 "targetAxis": "RIGHT_AXIS", "type": "LINE"},
            ],
        },
    }


def _bar_chart_spec(sheet_id: int, block_index: int, n_rows: int, title: str) -> dict:
    """セグメント分析ブロックの返信率(%)横棒チャート spec。"""
    header_row = block_index * SEGMENT_BLOCK_ROWS + 1  # 0-based（表タイトルの次の行）
    end = header_row + 1 + n_rows
    return {
        "title": title,
        "basicChart": {
            "chartType": "BAR",
            "legendPosition": "NO_LEGEND",
            "headerCount": 1,
            "axis": [{"position": "BOTTOM_AXIS", "title": "返信率(%)"},
                     {"position": "LEFT_AXIS"}],
            "domains": [{"domain": {"sourceRange": {"sources": [
                _grid_range(sheet_id, header_row, end, 0, 1)]}}}],
            "series": [{"series": {"sourceRange": {"sources": [
                _grid_range(sheet_id, header_row, end, 3, 4)]}},
                "targetAxis": "BOTTOM_AXIS"}],
        },
    }


def _chart_position(sheet_id: int, row: int, col: int) -> dict:
    return {"overlayPosition": {
        "anchorCell": {"sheetId": sheet_id, "rowIndex": row, "columnIndex": col},
        "widthPixels": 720, "heightPixels": 400,
    }}


def ensure_charts(sheets: SheetsPort, specs: list[tuple[str, dict, dict]]) -> int:
    """(タブ名, spec, position) のチャート群を冪等に作成/更新する。戻り値=処理件数。

    既存チャートは spec.title で特定する（タイトルは安定キーとして扱う）。
    """
    existing = sheets.fetch_charts()
    requests: list[dict] = []
    for tab, spec, position in specs:
        found = next((c for c in existing.get(tab, [])
                      if c.get("title") == spec.get("title")), None)
        if found and found.get("chartId") is not None:
            requests.append({"updateChartSpec": {
                "chartId": found["chartId"], "spec": spec}})
        else:
            requests.append({"addChart": {"chart": {
                "spec": spec, "position": position}}})
    sheets.batch_update(requests)
    return len(requests)


def sync_analytics(repo: Repository, sheets: SheetsPort, *,
                   now: datetime | None = None,
                   with_charts: bool = True,
                   trend_fn=None,
                   trend_interval_days: int = 7,
                   trend_min_sends: int = 10) -> SyncReport:
    """分析データを Google Sheets へ同期する（冪等）。

    trend_fn: (weekly, monthly, segments) -> str の傾向分析生成関数（None なら更新しない。
    週1回ペースは meta の last_trend_at で制御する）。
    """
    now = now or datetime.now()
    report = SyncReport()

    # 1. sent_log を自己修復（キャッシュ消失・過去分の補完。冪等）。
    report.backfilled = repo.backfill_sent_log()

    # 2. シートの手動チェックを読み戻して DB へマージ（書き換え前に必ず行う）。
    try:
        existing_rows = sheets.read_rows(SENT_LOG_SHEET)
    except Exception as e:  # 初回（シート無し）や一時エラーでも同期は続行する
        logger.warning("送信ログシートの読み取りに失敗（続行）: %s", e)
        existing_rows = []
    report.manual_merged = repo.merge_manual_replies(read_manual_entries(existing_rows))

    # 3. DB から会員単位の分析行を構築。
    records = [rec for r in repo.analytics_rows()
               if (rec := SentRecord.from_row(r)) is not None]
    report.members = len(records)
    report.replied = sum(1 for r in records if r.replied)

    # 4. 各シートを決定的に書き換え（送信ログは 注記行 + ヘッダー + データ）。
    sheets.write_rows(SENT_LOG_SHEET, _sent_log_rows(records))
    weekly = weekly_summary(records, now=now)
    monthly = monthly_summary(records, now=now)
    segments = standard_segments(records)
    sheets.write_rows(WEEKLY_SHEET, _period_rows(weekly, "週"))
    sheets.write_rows(MONTHLY_SHEET, _period_rows(monthly, "月"))
    sheets.write_rows(SEGMENT_SHEET, _segment_rows(segments))

    # 5. チェックボックス入力規則＋チャート（冪等）。
    try:
        sent_log_id = sheets.sheet_id(SENT_LOG_SHEET)
        sheets.batch_update([_checkbox_validation_request(sent_log_id, len(records))])
        if with_charts:
            weekly_id = sheets.sheet_id(WEEKLY_SHEET)
            monthly_id = sheets.sheet_id(MONTHLY_SHEET)
            segment_id = sheets.sheet_id(SEGMENT_SHEET)
            n_age = min(len(segments[0].rows), SEGMENT_MAX_ROWS)
            n_edu = min(len(segments[1].rows), SEGMENT_MAX_ROWS)
            report.charts = ensure_charts(sheets, [
                (WEEKLY_SHEET,
                 _combo_chart_spec(weekly_id, len(weekly), "週次 送信数と返信率"),
                 _chart_position(weekly_id, 1, 7)),
                (MONTHLY_SHEET,
                 _combo_chart_spec(monthly_id, len(monthly), "月次 送信数と返信率"),
                 _chart_position(monthly_id, 1, 7)),
                (SEGMENT_SHEET,
                 _bar_chart_spec(segment_id, 0, n_age, "年齢帯別 返信率"),
                 _chart_position(segment_id, 0, 5)),
                (SEGMENT_SHEET,
                 _bar_chart_spec(segment_id, 1, n_edu, "学歴別 返信率"),
                 _chart_position(segment_id, SEGMENT_BLOCK_ROWS, 5)),
            ])
    except Exception as e:
        logger.warning("チャート/入力規則の更新に失敗（データ同期は完了済み）: %s", e)
        report.errors.append(f"charts: {e}")

    # 6. 傾向分析（週1回・送信数が十分な場合のみ）。
    if trend_fn is not None:
        try:
            last = repo.get_meta("last_trend_at")
            total_sends = len(records)
            due = (last is None
                   or (now - datetime.fromisoformat(last)).days >= trend_interval_days)
            if due and total_sends >= trend_min_sends:
                text = trend_fn(weekly, monthly, segments)
                sheets.write_rows(TREND_SHEET, [
                    [f"傾向分析（自動生成: {now.strftime('%Y-%m-%d %H:%M')}）"],
                    [],
                    *[[line] for line in text.splitlines()],
                ])
                repo.set_meta("last_trend_at", now.isoformat(timespec="seconds"))
                report.trend_refreshed = True
        except Exception as e:
            logger.warning("傾向分析の生成に失敗（他の同期は完了済み）: %s", e)
            report.errors.append(f"trend: {e}")

    logger.info(report.summary())
    return report
