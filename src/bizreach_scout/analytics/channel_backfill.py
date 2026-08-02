"""送信枠を記録する前に送った過去スカウトの「送信枠」を復元するための対応表。

sent_log.channel は 2026-07-18 の分析基盤導入（#21）以降に記録している。それ以前の
送信は backfill_sent_log が scouts テーブルから復元しているため channel が空で、
週次サマリでは「内訳不明」に積み上がっていた。

送信枠そのものはDBに残っていないが、**どのスカウトがどのステップで送られたかは
GitHub Actions の実行ログに残っている**（ピックアップ送信ステップ／検索スカウト＋再送
ステップ）。そこから会員番号ごとの送信枠を機械的に抽出し、
config/channel_backfill.json に固定した対応表として持つ。

推測は一切していない。ログに「初回送信完了: BU...」「再送完了: BU...」が出た
ステップだけを根拠にしており、ログに無い送信は対応表に入っていない（＝内訳不明のまま
残る）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..config import project_root
from ..logging_config import logger

# sent_log.channel が取り得る値（api_sender の endpoint 由来）。
# platinum = プラチナスカウト（検索スカウト＋再送で使う経路）、pickup = 本日のピックアップ。
VALID_CHANNELS = ("platinum", "normal", "pickup")


def channel_backfill_path() -> Path:
    return project_root() / "config" / "channel_backfill.json"


def sent_backfill_path() -> Path:
    return project_root() / "config" / "sent_backfill.json"


def load_sent_backfill(path: Path | None = None) -> list[tuple[str, str, str, str]]:
    """「送信されたのにDBから消えた送信記録」の復元表を読む。

    各エントリは (member_no, kind, channel, sent_at)。2026-07 に送信完了後の
    タイムアウト/失敗で actions/cache の保存が走らず、送信記録が巻き戻った56件
    （実行ログから復元）。ファイルが無い・壊れている場合は空リストを返す。
    """
    path = path or sent_backfill_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("消失送信の復元表を読めませんでした（続行）: %s", e)
        return []
    out: list[tuple[str, str, str, str]] = []
    for row in data.get("entries", []):
        if not isinstance(row, list | tuple) or len(row) < 4:
            continue
        member_no, kind, channel, sent_at = (str(x) for x in row[:4])
        if kind not in ("first", "resend") or channel not in VALID_CHANNELS:
            continue
        try:
            datetime.fromisoformat(sent_at)
        except ValueError:
            continue
        out.append((member_no, kind, channel, sent_at))
    return out


def load_channel_backfill(path: Path | None = None) -> list[tuple[str, str, str]]:
    """(member_no, kind, channel) の対応表を読む。

    ファイルが無い・壊れている場合は空リストを返す（分析同期を止めない）。
    """
    path = path or channel_backfill_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("送信枠の対応表を読めませんでした（内訳不明のまま続行）: %s", e)
        return []
    out: list[tuple[str, str, str]] = []
    for row in data.get("entries", []):
        if not isinstance(row, list | tuple) or len(row) != 3:
            continue
        member_no, kind, channel = (str(x) for x in row)
        if kind in ("first", "resend") and channel in VALID_CHANNELS:
            out.append((member_no, kind, channel))
    return out
