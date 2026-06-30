"""Claude API を用いたスカウト文面生成。"""

from __future__ import annotations

import re
from datetime import datetime

from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import company_config, get_settings, scout_rules
from ..consultants import match_consultants
from ..logging_config import logger
from ..models import Candidate, ConsultantMatch, GeneratedScout, ScoutContent
from .prompt import EMIT_SCOUT_TOOL, build_system_prompt
from .templates import FirstSections, assemble_first_body, assemble_resend_body
from .validators import validate_scout

_USER_INSTRUCTION = (
    "上記の候補者について、初回送信用と再送用のスカウト2通を emit_scout ツールで出力してください。"
)


def _normalize_subject(subject: str, rules: dict, kind: str = "first") -> str:
    from .validators import subject_prefix_for

    prefix = subject_prefix_for(kind, rules.get("constraints", {}))
    s = subject.strip()
    if s.startswith(prefix):
        return s
    # 先頭にある【…】ブロック（誤った接頭辞）をまとめて除去してから付与。
    # 単純な lstrip だと "【急募】X" → "急募】X" のように閉じ括弧が残り壊れるため。
    s = re.sub(r"^(?:【[^】]*】\s*)+", "", s).strip()
    return f"{prefix}{s}"


class ScoutGenerator:
    """候補者から初回・再送スカウトを生成する。"""

    def __init__(self, client=None, model: str | None = None):
        self._settings = get_settings()
        self._model = model or self._settings.model
        self._client = client  # 遅延初期化（テスト時はモックを注入）

    @property
    def client(self):
        if self._client is None:
            from anthropic import Anthropic

            if not self._settings.anthropic_api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY が未設定です。.env に設定してください。"
                )
            self._client = Anthropic(api_key=self._settings.anthropic_api_key)
        return self._client

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
    def _call(self, system: str, messages: list[dict]):
        return self.client.messages.create(
            model=self._model,
            max_tokens=self._settings.max_tokens,
            system=system,
            messages=messages,
            tools=[EMIT_SCOUT_TOOL],
            tool_choice={"type": "tool", "name": "emit_scout"},
        )

    @staticmethod
    def _extract_tool_input(resp) -> tuple[dict, object]:
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "emit_scout":
                return block.input, block
        raise RuntimeError("emit_scout ツールの出力が得られませんでした。")

    def generate(
        self,
        candidate: Candidate,
        matches: list[ConsultantMatch] | None = None,
    ) -> GeneratedScout:
        rules = scout_rules()
        company = company_config()
        if matches is None:
            matches = match_consultants(candidate, rules=rules)

        system, tone_key = build_system_prompt(candidate, matches, rules, company)
        messages: list[dict] = [{"role": "user", "content": _USER_INSTRUCTION}]

        resp = self._call(system, messages)
        data, tool_block = self._extract_tool_input(resp)

        scout = self._assemble(candidate, data, matches, tone_key, company, rules)

        # バリデーション失敗時は1回だけ修正リクエスト。
        issues = self._collect_issues(scout, rules)
        if issues:
            logger.warning("文面バリデーション指摘（修正を試行）: %s", issues)
            messages.append({"role": "assistant", "content": resp.content})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": (
                                "次の制約違反を修正し、再度 emit_scout を呼び出してください:\n- "
                                + "\n- ".join(issues)
                            ),
                        }
                    ],
                }
            )
            resp2 = self._call(system, messages)
            data2, _ = self._extract_tool_input(resp2)
            scout2 = self._assemble(candidate, data2, matches, tone_key, company, rules)
            if not self._collect_issues(scout2, rules):
                scout = scout2
            else:
                logger.warning("修正後も指摘が残りました。生成結果を採用します。")
                scout = scout2

        return scout

    def _assemble(
        self,
        candidate: Candidate,
        data: dict,
        matches: list[ConsultantMatch],
        tone_key: str,
        company: dict,
        rules: dict,
    ) -> GeneratedScout:
        sections = FirstSections(
            greeting_offer=data["greeting_offer"],
            scout_reason=data["scout_reason"],
            company_intro=data["company_intro"],
            career_title=data["career_title"],
            career_body=data["career_body"],
            position_title=data["position_title"],
            position_body=data["position_body"],
        )
        first_body = assemble_first_body(candidate.member_no, sections, company)
        resend_body = assemble_resend_body(candidate.member_no, data["resend_body"], company)

        first = ScoutContent(
            subject=_normalize_subject(data["subject_first"], rules, "first"),
            body=first_body,
        )
        resend = ScoutContent(
            subject=_normalize_subject(data["subject_resend"], rules, "resend"),
            body=resend_body,
        )
        return GeneratedScout(
            member_no=candidate.member_no,
            first=first,
            resend=resend,
            tone_key=data.get("tone_key") or tone_key,
            matched_consultant_ids=[m.consultant.id for m in matches],
            analysis=data.get("analysis", ""),
            model=self._model,
            generated_at=datetime.now(),
        )

    @staticmethod
    def _collect_issues(scout: GeneratedScout, rules: dict) -> list[str]:
        issues = validate_scout(scout.first.subject, scout.first.body, rules, "first")
        issues += validate_scout(scout.resend.subject, scout.resend.body, rules, "resend")
        return issues


def render_for_human(scout: GeneratedScout) -> str:
    """仕様のコードブロック形式（件名・本文を別ブロック）で出力。"""

    def block(text: str) -> str:
        return "```\n" + text.strip() + "\n```"

    return "\n\n".join(
        [
            "【初回送信用】",
            "件名:",
            block(scout.first.subject),
            "本文:",
            block(scout.first.body),
            "【再送用】",
            "件名:",
            block(scout.resend.subject),
            "本文:",
            block(scout.resend.body),
        ]
    )
