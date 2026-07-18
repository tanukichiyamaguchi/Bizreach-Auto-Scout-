"""週次・月次・セグメント集計から日本語の傾向分析コメントを生成する（Claude API）。

個人情報（会員番号・氏名・企業名）は渡さない。集計値のみをコンパクトな JSON で渡す。
"""

from __future__ import annotations

import json

from ..config import get_settings
from ..logging_config import logger
from .aggregate import PeriodStat, SegmentTable

_SYSTEM = (
    "あなたは採用マーケティングのアナリストです。医院・病院経営コンサルティング会社が"
    "ビズリーチで送っている自動スカウトの集計データ（週次・月次・セグメント別の送信数と返信率）"
    "から、日本語で簡潔な傾向分析を書いてください。\n"
    "構成（見出し付き・箇条書き中心・全体で400〜700字）:\n"
    "1. 今週の実績（直近週の送信数・返信率と前週比）\n"
    "2. 直近4週のトレンド（改善/悪化と考えられる要因）\n"
    "3. 反応が良いセグメント（返信率が高い年齢帯・学歴・トーン等。母数が小さい場合は断定しない）\n"
    "4. 示唆・次のアクション（ターゲティングや文面の具体的な提案を1〜3個）\n"
    "注意: 母数（送信数）が10未満のセグメントから強い結論を出さない。事実に基づき誇張しない。"
)


def _stats_json(weekly: list[PeriodStat], monthly: list[PeriodStat],
                segments: list[SegmentTable]) -> str:
    payload = {
        "weekly": [
            {"week": s.label, "sent": s.sent, "replied": s.replied,
             "rate_pct": round(s.rate * 100, 1)}
            for s in weekly[-8:]  # 直近8週で十分
        ],
        "monthly": [
            {"month": s.label, "sent": s.sent, "replied": s.replied,
             "rate_pct": round(s.rate * 100, 1)}
            for s in monthly[-6:]
        ],
        "segments": {
            t.title: [
                {"segment": row.segment, "sent": row.sent, "replied": row.replied,
                 "rate_pct": round(row.rate * 100, 1)}
                for row in t.rows
            ]
            for t in segments
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def generate_trend_commentary(weekly: list[PeriodStat], monthly: list[PeriodStat],
                              segments: list[SegmentTable], *, client=None) -> str:
    """傾向分析テキストを生成する。client はテスト時にモックを注入。"""
    settings = get_settings()
    if client is None:
        from anthropic import Anthropic

        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY が未設定のため傾向分析を生成できません。")
        client = Anthropic(api_key=settings.anthropic_api_key)

    resp = client.messages.create(
        model=settings.model,
        max_tokens=2000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": _stats_json(weekly, monthly, segments)}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not text:
        raise RuntimeError("傾向分析の生成結果が空でした。")
    logger.info("傾向分析を生成しました（%d文字）。", len(text))
    return text
