"""候補者プロフィールページから Candidate を抽出する。

ページ全文を raw_profile に保持し、ラベル解析で可能な範囲のフィールドを埋める
（解析できなくても LLM には全文が渡るため文面生成は可能）。
"""

from __future__ import annotations

from ..ingest.parsing import parse_member_no
from ..ingest.text_source import parse_profile_text
from ..logging_config import logger
from ..models import Candidate
from .client import BizreachClient


def extract_candidate(client: BizreachClient, url: str) -> Candidate | None:
    page = client.page
    page.goto(url, wait_until="domcontentloaded")
    client.human_delay()

    try:
        root = page.locator(client.sel.profile_root).first
        text = root.inner_text() if root.count() > 0 else page.inner_text("body")
    except Exception as e:  # noqa: BLE001
        logger.warning("プロフィール本文の取得に失敗 (%s): %s", url, e)
        text = page.content()

    candidate = parse_profile_text(text)
    if candidate is None:
        member_no = parse_member_no(text)
        if not member_no:
            logger.warning("会員番号を特定できませんでした: %s", url)
            return None
        candidate = Candidate(member_no=member_no, raw_profile=text)

    candidate.source = "bizreach"
    candidate.profile_url = url
    return candidate
