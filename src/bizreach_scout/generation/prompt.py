"""システムプロンプトの組み立てと emit_scout ツール定義。"""

from __future__ import annotations

from ..config import company_config, prompt_template, scout_rules
from ..consultants import candidate_flags, render_matches_block
from ..models import Candidate, ConsultantMatch

# Claude に構造化出力を強制するためのツール定義。
EMIT_SCOUT_TOOL = {
    "name": "emit_scout",
    "description": (
        "初回・再送スカウトの件名と本文セクションを構造化して出力する。"
        "ヘッダー・定型文・署名・フッターはシステムが付与するため含めないこと。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "analysis": {
                "type": "string",
                "description": "候補者分析（内部ログ用。メール本文には出力されない）",
            },
            "tone_key": {
                "type": "string",
                "description": "適用したトーン区分のキー（late20s/early30s/sales 等）",
            },
            "subject_first": {
                "type": "string",
                "description": "初回件名。【Premium Offer】で始める。",
            },
            "greeting_offer": {"type": "string", "description": "④挨拶＋限定オファー文"},
            "scout_reason": {
                "type": "string",
                "description": "⑤スカウト理由。経歴に具体的に言及し、共通点コンサルタントやリクルート/保険訴求を自然に織り込む。",
            },
            "company_intro": {"type": "string", "description": "⑥会社紹介（候補者に刺さる点）"},
            "career_title": {"type": "string", "description": "⑦入社後キャリアのタイトル"},
            "career_body": {"type": "string", "description": "⑦入社後キャリアの本文"},
            "position_title": {"type": "string", "description": "⑧ポジション魅力のタイトル"},
            "position_body": {"type": "string", "description": "⑧ポジション魅力の本文"},
            "subject_resend": {
                "type": "string",
                "description": "再送件名。初回と異なる切り口。【Premium Offer】で始める。",
            },
            "resend_body": {
                "type": "string",
                "description": (
                    "再送本文。初回の約1/2の分量。冒頭で再送に自然に触れ、初回と異なる訴求。"
                    "共通点コンサルタントには数を絞って言及。署名・フッターは含めない。"
                ),
            },
        },
        "required": [
            "subject_first",
            "greeting_offer",
            "scout_reason",
            "company_intro",
            "career_title",
            "career_body",
            "position_title",
            "position_body",
            "subject_resend",
            "resend_body",
        ],
    },
}


def select_tones(candidate: Candidate, rules: dict | None = None) -> list[dict]:
    """候補者に合致するトーンプロファイルを返す（年齢系＋職種系）。"""
    profiles = (rules or scout_rules()).get("tone_profiles", [])
    matched: list[dict] = []
    for p in profiles:
        m = p.get("match", {})
        if "job_functions" in m:
            text = f"{candidate.job_function} {candidate.current_title} {candidate.summary}"
            if any(_kw in text for _kw in m["job_functions"]):
                matched.append(p)
            continue
        # 年齢・経験ベース
        if candidate.age is None:
            continue
        if "age_min" in m and candidate.age < m["age_min"]:
            continue
        if "age_max" in m and candidate.age > m["age_max"]:
            continue
        if "experience_max" in m and candidate.total_experience_years is not None:
            if candidate.total_experience_years > m["experience_max"]:
                continue
        matched.append(p)
    return matched


def render_tone_guidance(candidate: Candidate, rules: dict | None = None) -> tuple[str, str]:
    """(ガイダンス文, 代表トーンキー) を返す。"""
    tones = select_tones(candidate, rules)
    if not tones:
        return (
            "標準的なプロフェッショナルトーン（500-700字目安）で作成してください。",
            "default",
        )
    lines = []
    for t in tones:
        lines.append(
            f"- {t['label']}：トーン={t['tone']} / 文字数目安={t['length']} / 重点訴求={t['focus']}"
        )
    return "\n".join(lines), tones[0]["key"]


def render_special_instructions(
    candidate: Candidate, rules: dict | None = None, company: dict | None = None
) -> str:
    company = company or company_config()
    appeals = company.get("appeals", {})
    flags = candidate_flags(candidate, rules)
    out: list[str] = []
    if flags["is_recruit"]:
        count = appeals.get("recruit_consultant_count", 7)
        out.append(
            f"この候補者はリクルート出身です。当社にリクルート出身のコンサルタントが{count}名"
            "在籍している旨を本文で必ず伝え、上記の共通点コンサルタント（リクルート出身）全員の"
            "紹介と紹介URLを本文に分かりやすく添付してください。"
        )
    if flags["is_insurance"]:
        url = appeals.get("insurance_reference_url", "https://www.consuldent.jp/recruitment/2020/04/3272/")
        out.append(
            "この候補者は保険業界出身です。当社にプルデンシャル生命出身の人材も在籍していることを"
            f"アピールし、URL {url} を本文に紹介してください。"
        )
    if not out:
        out.append("特別な出身カテゴリ（リクルート/保険）は検出されていません。")
    return "\n".join(out)


def render_candidate_profile(candidate: Candidate) -> str:
    fields = [
        ("会員番号", candidate.member_no),
        ("年齢", candidate.age),
        ("性別", candidate.gender.value),
        ("学歴", candidate.education.value),
        ("出身大学", candidate.university),
        ("現職企業", candidate.current_company),
        ("現職役職", candidate.current_title),
        ("現職在籍年数", candidate.current_tenure_years),
        ("総経験年数", candidate.total_experience_years),
        ("業界", candidate.industry),
        ("職種", candidate.job_function),
        ("前職企業", "、".join(candidate.prior_companies)),
        ("現年収", candidate.salary_current),
        ("希望年収", candidate.salary_desired),
        ("語学", candidate.languages),
        ("希望職種", candidate.desired_jobs),
        ("希望業界", candidate.desired_industries),
        ("興味のある働き方", candidate.work_style),
        ("職務要約・自己PR", candidate.summary),
    ]
    lines = [f"- {label}: {value}" for label, value in fields if value not in (None, "", [])]
    if candidate.employments:
        lines.append("- 職務経歴:")
        for e in candidate.employments:
            seg = f"  - {e.company}（{e.title}、{e.years}年、{e.industry}）"
            lines.append(seg)
    if candidate.raw_profile:
        lines.append("\n【生プロフィール（参考）】\n" + candidate.raw_profile.strip())
    return "\n".join(lines)


def build_system_prompt(
    candidate: Candidate,
    matches: list[ConsultantMatch],
    rules: dict | None = None,
    company: dict | None = None,
) -> tuple[str, str]:
    """(system_prompt, tone_key) を返す。"""
    template = prompt_template()
    tone_guidance, tone_key = render_tone_guidance(candidate, rules)
    prompt = (
        template.replace("<<MEMBER_NO>>", candidate.member_no)
        .replace("<<TONE_GUIDANCE>>", tone_guidance)
        .replace("<<CONSULTANT_MATCHES>>", render_matches_block(matches))
        .replace("<<SPECIAL_INSTRUCTIONS>>", render_special_instructions(candidate, rules, company))
        .replace("<<CANDIDATE_PROFILE>>", render_candidate_profile(candidate))
    )
    return prompt, tone_key
