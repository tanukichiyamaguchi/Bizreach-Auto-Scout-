"""在籍コンサルタントとの共通点マッチング。

候補者の出身企業・業界・大学・職種から、共通点のある在籍コンサルタントを抽出し、
本文に織り込むための情報（共通点の説明・紹介URL）を返す。

特別ルール:
- リクルート出身の候補者 → リクルート出身コンサルタント(タグ recruit)を全員マッチ。
- 保険業界出身の候補者 → 保険出身コンサルタント(タグ insurance)を全員マッチ。
"""

from __future__ import annotations

from .config import load_consultants, scout_rules
from .models import Candidate, ConsultantMatch, ConsultantProfile


def _norm(s: str) -> str:
    return s.strip().lower().replace(" ", "").replace("　", "")


def _contains_any(haystacks: list[str], needles: list[str]) -> list[str]:
    """haystacks のいずれかに needles のいずれかが含まれれば、その needle を返す。"""
    hits: list[str] = []
    norm_hay = [_norm(h) for h in haystacks if h]
    for n in needles:
        nn = _norm(n)
        if not nn:
            continue
        if any(nn in h or h in nn for h in norm_hay):
            hits.append(n)
    return hits


def candidate_flags(candidate: Candidate, rules: dict | None = None) -> dict[str, bool]:
    """候補者の出身カテゴリ（リクルート/保険）を判定する。"""
    cfg = (rules or scout_rules()).get("matching", {})
    companies = candidate.all_companies()
    industry_fields = [candidate.industry, candidate.current_company, *candidate.prior_companies]

    is_recruit = bool(_contains_any(companies, cfg.get("recruit_keywords", [])))
    is_insurance = bool(
        _contains_any(companies + industry_fields, cfg.get("insurance_keywords", []))
    )
    return {"is_recruit": is_recruit, "is_insurance": is_insurance}


def _common_points(candidate: Candidate, c: ConsultantProfile) -> list[str]:
    points: list[str] = []

    company_hits = _contains_any(candidate.all_companies(), c.former_companies)
    if company_hits:
        points.append(f"前職企業の共通点（{ '・'.join(c.former_companies) }）")

    if candidate.industry:
        if _contains_any([candidate.industry], c.industries):
            points.append(f"業界の共通点（{candidate.industry}）")

    if candidate.university:
        if _contains_any([candidate.university], c.universities):
            points.append(f"出身大学の共通点（{candidate.university}）")

    if candidate.job_function:
        if _contains_any([candidate.job_function], c.roles):
            points.append(f"職種・役割の共通点（{candidate.job_function}）")

    return points


def match_consultants(
    candidate: Candidate,
    consultants: list[ConsultantProfile] | None = None,
    rules: dict | None = None,
) -> list[ConsultantMatch]:
    consultants = consultants if consultants is not None else load_consultants()
    flags = candidate_flags(candidate, rules)
    matches: dict[str, ConsultantMatch] = {}

    for c in consultants:
        points = _common_points(candidate, c)
        category = "general"

        # 特別ルール: 出身カテゴリ一致は共通点が無くてもマッチさせる。
        if flags["is_recruit"] and "recruit" in c.tags:
            category = "recruit"
            if not points:
                points = ["リクルート出身という共通のバックグラウンド"]
        elif flags["is_insurance"] and "insurance" in c.tags:
            category = "insurance"
            if not points:
                points = ["保険業界出身という共通のバックグラウンド"]

        if not points:
            continue

        matches[c.id] = ConsultantMatch(consultant=c, common_points=points, category=category)

    # recruit / insurance を先頭に、共通点の多い順で並べる。
    order = {"recruit": 0, "insurance": 1, "general": 2}
    return sorted(
        matches.values(),
        key=lambda m: (order.get(m.category, 9), -len(m.common_points)),
    )


def render_matches_block(matches: list[ConsultantMatch]) -> str:
    """プロンプトに差し込むコンサルタント共通点ブロックを生成。

    consultant_id は emit_scout の consultant_intros[].consultant_id で
    紐付けるための内部識別子（本文には出さない）。
    """
    if not matches:
        return "（共通点のある在籍コンサルタントは特定されていません。無理に言及しないこと。）"
    lines = []
    for m in matches:
        c = m.consultant
        lines.append(
            f"- consultant_id: {c.id}｜氏名: {c.display_name}｜"
            f"共通点: {'、'.join(m.common_points)}｜紹介URL: {c.profile_url}"
        )
    return "\n".join(lines)


def select_intro_matches(
    matches: list[ConsultantMatch], rules: dict | None = None
) -> list[ConsultantMatch]:
    """本文で紹介するコンサルタントを、match_consultants の優先順位のまま上位N名に絞る。

    候補者によっては共通点のあるコンサルタントが10名以上になることがあり、全員を
    自然な文章で紹介するのは非現実的（＝紹介の省略や視認性低下の一因だった）。
    matching.max_intro_consultants（既定3）で上限を設け、現実的なタスクにする。
    """
    cfg = (rules or scout_rules()).get("matching", {})
    max_n = cfg.get("max_intro_consultants", 3)
    if not max_n or max_n <= 0:
        return list(matches)
    return list(matches[:max_n])


def render_consultant_intro_section(
    lead: str, blurbs: dict[str, str], matches: list[ConsultantMatch]
) -> str:
    """モデルが生成した導入文＋コンサルタントごとの紹介文を固定書式で組み立てる。

    1人ずつ独立したブロックにする（文章を連結しない＝視認性を優先）:
        {blurb}
        ▼{display_name} プロフィール
        {profile_url}
    先頭のブロックのみ、導入文(lead)に自然につながる形で同じ段落として続ける。
    2人目以降は空行で区切った独立ブロックにする。blurb が空/未提供のコンサルタントは
    紹介ブロックごとスキップする。
    """
    blocks: list[tuple[str, str, str]] = []
    for m in matches:
        blurb = (blurbs.get(m.consultant.id) or "").strip()
        if not blurb:
            continue
        blocks.append((m.consultant.display_name, blurb, m.consultant.profile_url))
    if not blocks:
        return ""

    lead = (lead or "").strip()
    name0, blurb0, url0 = blocks[0]
    first_lines = [p for p in (lead, blurb0) if p] + [f"▼{name0} プロフィール", url0]
    rendered = ["\n".join(first_lines)]
    for name, blurb, url in blocks[1:]:
        rendered.append(f"{blurb}\n▼{name} プロフィール\n{url}")
    return "\n\n".join(rendered)
