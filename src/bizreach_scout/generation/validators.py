"""生成文面の制約バリデーション（禁止表現・件名形式・感嘆符数・絵文字）。"""

from __future__ import annotations

import re

from ..config import scout_rules

# 絵文字のおおまかな検出（主要な絵文字ブロック）。
# 注: 矢印(U+2190-21FF)・囲み記号(◎ U+25CE)・罫線(─ U+2500)は固定フッターで
# 正当に使用されるため除外する。誤検知で毎回の修正リトライが走るのを防ぐ。
_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"  # 絵文字本体（顔・記号・乗り物など）
    "\U00002600-\U000026ff"  # その他記号（☀☂等）
    "\U00002702-\U000027b0"  # 装飾記号（✂✈✨❤等）
    "\U0001f000-\U0001f0ff"  # 麻雀・トランプ
    "\U0001f1e6-\U0001f1ff"  # 国旗
    "\U0000fe0f"             # 異体字セレクタ16（絵文字表示）
    "]"
)


def validate_subject(subject: str, rules: dict | None = None) -> list[str]:
    cfg = (rules or scout_rules()).get("constraints", {})
    issues: list[str] = []
    prefix = cfg.get("subject_prefix", "【Premium Offer】")
    if not subject.startswith(prefix):
        issues.append(f"件名が「{prefix}」で始まっていません")
    # 【】は Premium Offer のみ。先頭の prefix を除いた残りに【】が無いこと。
    rest = subject[len(prefix):] if subject.startswith(prefix) else subject
    if "【" in rest or "】" in rest:
        issues.append("件名の【】は Premium Offer 以外に使用されています")
    return issues


def validate_body(body: str, rules: dict | None = None) -> list[str]:
    cfg = (rules or scout_rules()).get("constraints", {})
    issues: list[str] = []

    for bad in cfg.get("forbid_strings", []):
        if bad in body:
            issues.append(f"禁止表現が含まれています: {bad!r}")

    for phrase in cfg.get("forbid_phrase", []):
        if phrase in body:
            issues.append(f"禁止フレーズが含まれています: {phrase!r}")

    max_excl = cfg.get("max_exclamations", 2)
    excl = body.count("!") + body.count("！")
    if excl > max_excl:
        issues.append(f"感嘆符が{max_excl}回を超えています（{excl}回）")

    if cfg.get("forbid_emoji", True) and _EMOJI_RE.search(body):
        issues.append("絵文字が含まれています")

    return issues


def validate_scout(subject: str, body: str, rules: dict | None = None) -> list[str]:
    return validate_subject(subject, rules) + validate_body(body, rules)
