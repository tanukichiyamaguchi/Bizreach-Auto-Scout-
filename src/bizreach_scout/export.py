"""生成したスカウト文面のレビュー用エクスポート。"""

from __future__ import annotations

from .config import project_root
from .generation.generator import render_for_human
from .logging_config import logger
from .models import GeneratedScout


def export_scout(scout: GeneratedScout) -> None:
    """生成したスカウトを data/exports/{会員番号}.md に書き出す（レビュー用）。

    書き出しの失敗は運用を止めない（警告のみ）。
    """
    try:
        out = project_root() / "data" / "exports"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{scout.member_no}.md").write_text(
            render_for_human(scout), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("文面のエクスポートに失敗: %s", e)
