"""Google Sheets への読み書き（gspread）。

テストは SheetsPort を満たす FakeSheets を使い、gspread はここでのみ遅延 import する。
API 呼び出しは 429/5xx に備えて指数バックオフでリトライする。
"""

from __future__ import annotations

from typing import Any, Protocol

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ..logging_config import logger


class SheetsPort(Protocol):
    """sync.py が依存する最小インターフェース。"""

    def read_rows(self, title: str) -> list[list[str]]:
        """シートの全行（無ければ空リスト）。"""
        ...

    def write_rows(self, title: str, rows: list[list[Any]]) -> None:
        """シートを確保し、全消去して rows を書き込む。"""
        ...

    def batch_update(self, requests: list[dict]) -> dict:
        """spreadsheets.batchUpdate（チャート作成・データ検証等）。"""
        ...

    def sheet_id(self, title: str) -> int:
        """シート（タブ）の内部ID。"""
        ...

    def fetch_charts(self) -> dict[str, list[dict]]:
        """タブ名 -> チャート一覧（chartId, spec.title）。冪等なチャート管理用。"""
        ...


def _is_retryable(exc: BaseException) -> bool:
    """429（クォータ）・5xx のみリトライ対象。"""
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return code == 429 or (isinstance(code, int) and code >= 500)


_retry = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    reraise=True,
)


class GspreadSheets:
    """gspread による SheetsPort 実装。"""

    def __init__(self, spreadsheet_id: str, credentials_path: str):
        import gspread  # 遅延import（テスト・未設定環境で不要）

        self._gc = gspread.service_account(
            filename=credentials_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        self._ss = self._gc.open_by_key(spreadsheet_id)

    def _worksheet(self, title: str, *, create: bool = False):
        import gspread

        try:
            return self._ss.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            if not create:
                return None
            logger.info("シート '%s' を新規作成します。", title)
            return self._ss.add_worksheet(title=title, rows=200, cols=30)

    @_retry
    def read_rows(self, title: str) -> list[list[str]]:
        ws = self._worksheet(title)
        return ws.get_all_values() if ws else []

    @_retry
    def write_rows(self, title: str, rows: list[list[Any]]) -> None:
        ws = self._worksheet(title, create=True)
        ws.clear()
        if rows:
            ws.update(range_name="A1", values=rows,
                      value_input_option="USER_ENTERED")

    @_retry
    def batch_update(self, requests: list[dict]) -> dict:
        if not requests:
            return {}
        return self._ss.batch_update({"requests": requests})

    @_retry
    def sheet_id(self, title: str) -> int:
        ws = self._worksheet(title, create=True)
        return int(ws.id)

    @_retry
    def fetch_charts(self) -> dict[str, list[dict]]:
        meta = self._ss.fetch_sheet_metadata(
            params={"fields": "sheets(properties(sheetId,title),charts(chartId,spec(title)))"}
        )
        out: dict[str, list[dict]] = {}
        for sheet in meta.get("sheets", []):
            title = sheet.get("properties", {}).get("title", "")
            out[title] = [
                {"chartId": c.get("chartId"),
                 "title": (c.get("spec") or {}).get("title", "")}
                for c in sheet.get("charts", [])
            ]
        return out
