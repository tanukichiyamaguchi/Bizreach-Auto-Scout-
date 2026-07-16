"""在籍コンサルタントとの共通点マッチング。

候補者の出身企業・業界・大学・職種から、共通点のある在籍コンサルタントを抽出し、
本文に織り込むための情報（共通点の説明・紹介URL）を返す。

特別ルール:
- リクルート出身の候補者 → リクルート出身コンサルタント(タグ recruit)を全員マッチ。
- 保険業界出身の候補者 → 保険出身コンサルタント(タグ insurance)を全員マッチ。
"""

from __future__ import annotations

import re

from .config import load_consultants, scout_rules
from .models import Candidate, ConsultantMatch, ConsultantProfile


def normalize_consultant_id(s: str) -> str:
    """consultant_id 照合用の正規化（前後空白除去＋casefold）。

    emit_scout の consultant_id はモデルの自由記述で、大文字小文字が
    ずれても（例: 'inoue' → 'Inoue'）取りこぼさないようにする。
    generator.py の _blurb_map / _consultant_coverage_issues でも同じ規則を
    使い、キー生成側と参照側で正規化がずれないようにする。
    """
    return (s or "").strip().casefold()


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

    if candidate.industry and _contains_any([candidate.industry], c.industries):
        points.append(f"業界の共通点（{candidate.industry}）")

    if candidate.university and _contains_any([candidate.university], c.universities):
        points.append(f"出身大学の共通点（{candidate.university}）")

    if candidate.job_function and _contains_any([candidate.job_function], c.roles):
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


def _label_for(m: ConsultantMatch) -> str:
    """プロンプト用のマッチ根拠ラベル（共通点／近い経歴／紹介）。"""
    if m.category in ("recruit", "insurance", "general"):
        return "共通点"
    if m.category == "soft":
        return "近い経歴"
    return "紹介理由"  # fallback


def render_matches_block(matches: list[ConsultantMatch]) -> str:
    """プロンプトに差し込むコンサルタント一覧ブロックを生成。

    consultant_id は emit_scout の consultant_intros[].consultant_id で
    紐付けるための内部識別子（本文には出さない）。共通点マッチ・近い経歴（soft）・
    フォールバックを区別してラベル付けし、モデルが根拠に応じた自然な紹介文を
    書けるようにする（fallback は共通点を断定せず「当社のコンサルタント」として紹介）。
    """
    if not matches:
        return "（在籍コンサルタントの情報が取得できませんでした。）"
    lines = []
    for m in matches:
        c = m.consultant
        lines.append(
            f"- consultant_id: {c.id}｜氏名: {c.display_name}｜"
            f"{_label_for(m)}: {'、'.join(m.common_points)}｜紹介URL: {c.profile_url}"
        )
    return "\n".join(lines)


def _soft_points(candidate: Candidate, c: ConsultantProfile) -> list[str]:
    """共通点マッチが無い候補者向けの「近い経歴」ソフト一致を返す（誇張しない範囲で）。

    職種（現職役職含む）・業界の近さを、コンサルタントの roles/specialties/industries と
    照合する。断定的な「共通点」ではなく、自然に紹介へつなげるための素材。
    """
    pts: list[str] = []
    if _contains_any([candidate.job_function, candidate.current_title],
                     c.roles + c.specialties):
        pts.append(f"職種の近さ（{candidate.job_function or candidate.current_title}）")
    if candidate.industry and _contains_any([candidate.industry], c.industries + c.specialties):
        pts.append(f"業界での近い経歴（{candidate.industry}）")
    return pts


def _fallback_point(c: ConsultantProfile) -> str:
    """共通点も近い経歴も無い場合の、コンサルタント本人の専門に基づく紹介理由（事実ベース）。"""
    spec = "・".join((c.specialties or c.roles)[:2])
    return f"当社で活躍するコンサルタント（{spec}）" if spec else "当社で活躍するコンサルタント"


def default_blurb(m: ConsultantMatch) -> str:
    """モデルが blurb を出さなかった場合の、プロフィール由来の事実ベース紹介文。

    最終メールに必ずコンサルタント紹介を載せるための保険。候補者との共通点を
    断定せず、コンサルタント本人の専門・経歴のみを述べる（consultants.json 由来）。
    """
    c = m.consultant
    spec = "・".join((c.specialties or c.roles)[:3])
    if spec:
        return f"{c.display_name}は{spec}などの経験を持つ、当社で活躍するコンサルタントです。"
    return f"{c.display_name}は当社で活躍するコンサルタントです。"


def select_intro_matches(
    candidate: Candidate,
    matches: list[ConsultantMatch],
    rules: dict | None = None,
    consultants: list[ConsultantProfile] | None = None,
) -> list[ConsultantMatch]:
    """本文で必ず紹介するコンサルタントを選ぶ（全メールに最低 min 名を保証する）。

    最重要方針: **スカウトには必ずコンサルタント紹介を載せる**。共通点マッチが少ない/
    無い候補者でも、①共通点マッチ → ②近い経歴（職種・業界）のソフトマッチ →
    ③フォールバック（当社の実力派コンサルタント）の順で min〜max 名を確保する。

    - matching.max_intro_consultants（既定3）: 紹介の上限。
    - matching.min_intro_consultants（既定1）: 紹介の下限（保証人数）。
    - max_intro_consultants=0 のときのみ紹介を完全に無効化する（min も無視）。
    """
    cfg = (rules or scout_rules()).get("matching", {})
    max_n = max(0, cfg.get("max_intro_consultants", 3))
    if max_n == 0:
        return []  # 明示的に紹介オフ
    min_n = min(max(0, cfg.get("min_intro_consultants", 1)), max_n)

    selected = list(matches[:max_n])  # ① 共通点マッチ（優先度順・上位max_n名）
    if len(selected) >= min_n:
        return selected  # 既に保証人数を満たす（従来どおりの挙動）

    # 保証人数(min_n)に満たない場合のみ、下記で min_n まで補充する。
    ids = {m.consultant.id for m in selected}
    pool = consultants if consultants is not None else load_consultants()

    # ② 近い経歴（ソフトマッチ）を関連度（一致数）の高い順に補充。
    soft: list[tuple[int, ConsultantMatch]] = []
    for c in pool:
        if c.id in ids:
            continue
        pts = _soft_points(candidate, c)
        if pts:
            soft.append((len(pts), ConsultantMatch(
                consultant=c, common_points=pts, category="soft")))
    soft.sort(key=lambda t: -t[0])  # 一致数の多い順（安定＝カタログ順を保持）
    for _, m in soft:
        if len(selected) >= min_n:
            break
        selected.append(m)
        ids.add(m.consultant.id)

    # ③ それでも下限に満たなければフォールバック（当社の実力派コンサルタント）で保証。
    for c in pool:
        if len(selected) >= min_n:
            break
        if c.id in ids:
            continue
        selected.append(ConsultantMatch(
            consultant=c, common_points=[_fallback_point(c)], category="fallback"))
        ids.add(c.id)

    return selected


def _strip_duplicate_heading(blurb: str, name: str, url: str) -> str:
    """モデルがblurb内に▼見出しやプロフィールURLを重複して書いた場合に除去する。

    render_consultant_intro_section は▼見出し・URLを必ず1回だけ自動付与するため、
    モデルの指示不遵守（blurb内への重複記載）があっても最終出力の正しさ
    （二重表示にならないこと）をコード側で保証する。
    """
    if not blurb:
        return blurb
    pattern = re.compile(rf"▼\s*{re.escape(name)}\s*プロフィール")
    blurb = pattern.sub("", blurb)
    if url:
        blurb = blurb.replace(url, "")
    blurb = re.sub(r"\n{2,}", "\n", blurb)
    return blurb.strip()


def render_consultant_intro_section(
    lead: str, blurbs: dict[str, str], matches: list[ConsultantMatch],
    fill_missing: bool = True,
) -> str:
    """モデルが生成した導入文＋コンサルタントごとの紹介文を固定書式で組み立てる。

    1人ずつ独立したブロックにする（文章を連結しない＝視認性を優先）:
        {blurb}
        ▼{display_name} プロフィール
        {profile_url}
    先頭のブロックのみ、導入文(lead)に自然につながる形で同じ段落として続ける。
    2人目以降は空行で区切った独立ブロックにする。blurb 内に▼見出しやURLが紛れ込んで
    いても重複表示にならないよう除去する（_strip_duplicate_heading）。

    fill_missing=True（既定）のとき、モデルが blurb を出さなかった matches には
    プロフィール由来の既定紹介文（default_blurb）を補い、**必ず紹介ブロックを出す**。
    これにより「コンサルタント紹介が本文から丸ごと消える」事故を防ぐ（最重要指示の担保）。

    blurbs のキーは呼び出し側の正規化有無によらず一致するよう、この関数内で
    normalize_consultant_id により再正規化する（モデルが 'Inoue' のように
    大文字始まりで返しても取りこぼさない）。
    """
    normalized_blurbs = {normalize_consultant_id(k): v for k, v in blurbs.items()}
    blocks: list[tuple[str, str, str]] = []
    for m in matches:
        blurb = (normalized_blurbs.get(normalize_consultant_id(m.consultant.id)) or "").strip()
        if blurb:
            blurb = _strip_duplicate_heading(
                blurb, m.consultant.display_name, m.consultant.profile_url)
        if not blurb and fill_missing:
            blurb = default_blurb(m)  # 保険: モデルが書かなくても既定文で必ず紹介する
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
