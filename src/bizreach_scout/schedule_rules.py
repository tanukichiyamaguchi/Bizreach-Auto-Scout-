"""送信曜日の運用ルール（通常スカウトの休止日判定）。

運用ルール:
- **通常スカウト（検索スカウト＋再送）は土日祝日に送らない**（相手の休日に届かせない）。
- **ピックアップ（本日のピックアップ・無料枠）は毎日送る**（当日限りの枠のため止めない）。

判定は日本時間の日付で行う。祝日は jpholiday（振替休日・国民の休日・春分/秋分に対応）
で判定し、config/scout_rules.yaml の schedule セクションで曜日・追加休止日を調整できる。
"""

from __future__ import annotations

from datetime import date, datetime

from .config import scout_rules

# 1=月, 2=火, ... 6=土, 7=日（date.isoweekday と同じ）。
_WEEKDAY_NAMES = {1: "月", 2: "火", 3: "水", 4: "木", 5: "金", 6: "土", 7: "日"}


def _schedule_cfg(rules: dict | None = None) -> dict:
    return (rules or scout_rules()).get("schedule", {}) or {}


def holiday_name(d: date) -> str:
    """日本の祝日名（祝日でなければ空文字）。jpholiday 未導入でも落とさない。"""
    try:
        import jpholiday
    except ImportError:  # pragma: no cover - 依存が入っていない環境向けの保険
        return ""
    return jpholiday.is_holiday_name(d) or ""


def search_scout_pause_reason(now: datetime | date | None = None,
                              rules: dict | None = None) -> str:
    """通常スカウトを休止すべき理由を返す（空文字なら送信してよい）。

    ピックアップはこの判定の対象外（毎日送る）。
    """
    cfg = _schedule_cfg(rules)
    d = (now or datetime.now())
    d = d.date() if isinstance(d, datetime) else d

    iso = d.isoformat()
    if iso in {str(x) for x in cfg.get("search_skip_dates", [])}:
        return f"{iso} は送信休止日に指定されています"

    skip_weekdays = cfg.get("search_skip_weekdays", [])
    if d.isoweekday() in skip_weekdays:
        return f"{iso} は{_WEEKDAY_NAMES.get(d.isoweekday(), '?')}曜日のため休止対象です"

    if cfg.get("search_skip_holidays", False):
        name = holiday_name(d)
        if name:
            return f"{iso} は祝日（{name}）のため休止対象です"

    return ""


def should_send_search_scout(now: datetime | date | None = None,
                             rules: dict | None = None) -> bool:
    """通常スカウト（検索スカウト＋再送）を送ってよい日か。"""
    return not search_scout_pause_reason(now, rules)
