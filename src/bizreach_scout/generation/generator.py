"""Claude API を用いたスカウト文面生成。"""

from __future__ import annotations

import re
from datetime import datetime

from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import company_config, get_settings, scout_rules
from ..consultants import (
    match_consultants,
    normalize_consultant_id,
    render_consultant_intro_section,
    select_intro_matches,
)
from ..logging_config import logger
from ..models import Candidate, ConsultantMatch, GeneratedScout, ScoutContent
from .prompt import EMIT_SCOUT_TOOL, build_system_prompt
from .templates import FirstSections, assemble_first_body, assemble_resend_body
from .validators import validate_scout

_USER_INSTRUCTION = (
    "上記の候補者について、初回送信用と再送用のスカウト2通を emit_scout ツールで出力してください。"
)

# emit_scout が必ず返すべきフィールド（欠落は生成失敗として扱う）。
_REQUIRED_FIELDS = (
    "subject_first", "greeting_offer", "scout_reason", "company_intro",
    "career_title", "career_body", "position_title", "position_body",
    "subject_resend", "resend_body",
)


class GenerationError(RuntimeError):
    """emit_scout の出力が不正（必須フィールド欠落など）で組み立てできない。

    pipeline 側の生成失敗ハンドリング（report.failed）に載せるための明示的な型。
    """


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
    def _call(self, system: str, messages: list[dict], force_tool: bool = False):
        kwargs: dict = {
            "model": self._model,
            "max_tokens": self._settings.max_tokens,
            "system": system,
            "messages": messages,
            "tools": [EMIT_SCOUT_TOOL],
        }
        budget = self._settings.thinking_budget_tokens
        if budget and budget > 0 and not force_tool:
            # 拡張思考(adaptive thinking)を有効化。
            # 注: Opus 4.8/4.7 では {"type":"enabled","budget_tokens":N} は廃止され400に
            #     なるため adaptive を使い、深さは output_config.effort で制御する。
            #     また拡張思考時は強制 tool_choice(tool/any) が使えないため auto にする。
            kwargs["thinking"] = {"type": "adaptive"}
            if self._settings.thinking_effort:
                kwargs["output_config"] = {"effort": self._settings.thinking_effort}
            kwargs["tool_choice"] = {"type": "auto"}
        else:
            # 思考オフ（またはフォールバック時）は強制tool_choiceで確実に構造化出力を得る。
            kwargs["tool_choice"] = {"type": "tool", "name": "emit_scout"}
        return self.client.messages.create(**kwargs)

    @staticmethod
    def _extract_tool_input(resp) -> tuple[dict, object]:
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "emit_scout":
                return block.input, block
        raise RuntimeError("emit_scout ツールの出力が得られませんでした。")

    def _call_ensuring_tool(self, system: str, messages: list[dict]):
        """emit_scoutツールの出力を確実に得る。

        adaptive thinking + tool_choice=auto では稀にツールを呼ばず終わることがある。
        その場合は思考なし＋強制tool_choiceで1回だけ取り直す。バリデーション再試行時の
        2回目呼び出しでも同じ揺れが起こり得るため、呼び出し箇所を問わずこのヘルパーを
        通すことで常に安全側フォールバックを効かせる。
        """
        resp = self._call(system, messages)
        try:
            data, tool_block = self._extract_tool_input(resp)
        except RuntimeError:
            logger.warning("拡張思考でツール未出力。強制tool_choiceで再試行します。")
            resp = self._call(system, messages, force_tool=True)
            data, tool_block = self._extract_tool_input(resp)
        return resp, data, tool_block

    def generate(
        self,
        candidate: Candidate,
        matches: list[ConsultantMatch] | None = None,
    ) -> GeneratedScout:
        rules = scout_rules()
        company = company_config()
        if matches is None:
            matches = match_consultants(candidate, rules=rules)
        # 本文で紹介するのは上位N名まで（全員紹介は非現実的で省略の一因だった）。
        intro_matches = select_intro_matches(matches, rules)
        resend_max = max(0, rules.get("resend", {}).get("max_consultant_mentions", 1))
        resend_intro_matches = intro_matches[:resend_max]

        system, tone_key = build_system_prompt(candidate, intro_matches, rules, company)
        messages: list[dict] = [{"role": "user", "content": _USER_INSTRUCTION}]

        resp, data, tool_block = self._call_ensuring_tool(system, messages)

        scout = self._assemble(
            candidate, data, matches, intro_matches, resend_intro_matches, tone_key, company, rules
        )

        # バリデーション失敗時は1回だけ修正リクエスト。
        # 共通点コンサルタント紹介の省略は最重要指示のため、ここでも網羅性を検証する。
        issues = self._all_issues(scout, data, intro_matches, resend_intro_matches, rules)
        if issues:
            scout = self._retry_with_corrections(
                system, messages, resp, tool_block, issues,
                candidate, matches, intro_matches, resend_intro_matches,
                tone_key, company, rules,
            )
        return scout

    def _all_issues(
        self,
        scout: GeneratedScout,
        data: dict,
        intro_matches: list[ConsultantMatch],
        resend_intro_matches: list[ConsultantMatch],
        rules: dict,
    ) -> list[str]:
        """文面制約違反とコンサルタント紹介の不足をまとめて返す。"""
        return (
            self._collect_issues(scout, rules)
            + self._consultant_coverage_issues(data, intro_matches, resend_intro_matches)
        )

    def _retry_with_corrections(
        self,
        system: str,
        messages: list[dict],
        resp,
        tool_block,
        issues: list[str],
        candidate: Candidate,
        matches: list[ConsultantMatch],
        intro_matches: list[ConsultantMatch],
        resend_intro_matches: list[ConsultantMatch],
        tone_key: str,
        company: dict,
        rules: dict,
    ) -> GeneratedScout:
        """指摘を tool_result で返して1回だけ再生成し、再組み立てした結果を返す。

        再生成後も指摘が残る場合でも、最善のものとして再生成結果を採用する
        （元よりは指摘を減らせているため）。
        """
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
        _, data2, _ = self._call_ensuring_tool(system, messages)
        scout2 = self._assemble(
            candidate, data2, matches, intro_matches, resend_intro_matches,
            tone_key, company, rules,
        )
        if self._all_issues(scout2, data2, intro_matches, resend_intro_matches, rules):
            logger.warning("修正後も指摘が残りました。生成結果を採用します。")
        return scout2

    def _assemble(
        self,
        candidate: Candidate,
        data: dict,
        matches: list[ConsultantMatch],
        intro_matches: list[ConsultantMatch],
        resend_intro_matches: list[ConsultantMatch],
        tone_key: str,
        company: dict,
        rules: dict,
    ) -> GeneratedScout:
        missing = [k for k in _REQUIRED_FIELDS if not str(data.get(k, "")).strip()]
        if missing:
            raise GenerationError(
                "emit_scout の必須フィールドが欠落/空です: " + "、".join(missing)
            )
        consultant_intro = render_consultant_intro_section(
            data.get("consultant_intro_lead", ""),
            self._blurb_map(data.get("consultant_intros")),
            intro_matches,
        )
        resend_consultant_intro = render_consultant_intro_section(
            data.get("resend_consultant_intro_lead", ""),
            self._blurb_map(data.get("resend_consultant_intros")),
            resend_intro_matches,
        )

        sections = FirstSections(
            greeting_offer=data["greeting_offer"],
            scout_reason=data["scout_reason"],
            consultant_intro=consultant_intro,
            company_intro=data["company_intro"],
            career_title=data["career_title"],
            career_body=data["career_body"],
            position_title=data["position_title"],
            position_body=data["position_body"],
        )
        first_body = assemble_first_body(candidate.member_no, sections, company)
        resend_body = assemble_resend_body(
            candidate.member_no, data["resend_body"], company, resend_consultant_intro
        )

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
    def _blurb_map(intros: object) -> dict[str, str]:
        """emit_scout の consultant_intros/resend_consultant_intros を
        {consultant_id: blurb} の辞書へ正規化する。想定外の形は無視する。

        キーは normalize_consultant_id() で正規化する（モデルが 'Inoue' のように
        大文字始まりで返しても consultants.json 側の 'inoue' と一致させるため）。
        """
        if not isinstance(intros, list):
            return {}
        return {
            normalize_consultant_id(str(item.get("consultant_id", ""))): str(item.get("blurb", ""))
            for item in intros
            if isinstance(item, dict) and item.get("consultant_id")
        }

    @staticmethod
    def _collect_issues(scout: GeneratedScout, rules: dict) -> list[str]:
        issues = validate_scout(scout.first.subject, scout.first.body, rules, "first")
        issues += validate_scout(scout.resend.subject, scout.resend.body, rules, "resend")
        return issues

    @staticmethod
    def _consultant_coverage_issues(
        data: dict,
        intro_matches: list[ConsultantMatch],
        resend_intro_matches: list[ConsultantMatch],
    ) -> list[str]:
        """共通点コンサルタント紹介の省略を検知する（最重要指示のため）。

        期待される consultant_id 全員分の blurb が emit_scout の出力に含まれているかを
        確認し、欠けていれば1回だけの修正リトライに回すための指摘文を返す。
        """
        issues: list[str] = []

        def _missing(intros_key: str, expected: list[ConsultantMatch], label: str) -> None:
            if not expected:
                return
            have = {
                normalize_consultant_id(str(x.get("consultant_id", "")))
                for x in (data.get(intros_key) or [])
                if isinstance(x, dict) and str(x.get("blurb", "")).strip()
            }
            missing = [
                m.consultant.display_name
                for m in expected
                if normalize_consultant_id(m.consultant.id) not in have
            ]
            if missing:
                issues.append(
                    f"{intros_key}（{label}）に以下のコンサルタントの紹介が不足しています。"
                    "全員分のblurbを追加してください: " + "、".join(missing)
                )

        _missing("consultant_intros", intro_matches, "初回")
        _missing("resend_consultant_intros", resend_intro_matches, "再送")

        if intro_matches and not str(data.get("consultant_intro_lead", "")).strip():
            issues.append(
                "consultant_intro_lead（コンサルタント紹介の導入文）が空です。追加してください。"
            )
        if resend_intro_matches and not str(data.get("resend_consultant_intro_lead", "")).strip():
            issues.append(
                "resend_consultant_intro_lead（再送のコンサルタント紹介導入文）が空です。"
                "追加してください。"
            )

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
