from bizreach_scout.consultants import (
    candidate_flags,
    match_consultants,
    render_consultant_intro_section,
    render_matches_block,
    select_intro_matches,
)
from bizreach_scout.models import ConsultantMatch, ConsultantProfile

from .factories import make_candidate

SAMPLE = [
    ConsultantProfile(id="c001", display_name="A.S（リクルート出身）",
                      former_companies=["リクルート"], tags=["recruit"],
                      profile_url="https://example.com/c001"),
    ConsultantProfile(id="c002", display_name="B.K（リクルート出身）",
                      former_companies=["リクルートライフスタイル"], tags=["recruit"],
                      profile_url="https://example.com/c002"),
    ConsultantProfile(id="c008", display_name="H.M（プルデンシャル出身）",
                      former_companies=["プルデンシャル生命"], industries=["保険"],
                      tags=["insurance"], profile_url="https://example.com/c008"),
    ConsultantProfile(id="c009", display_name="N.F（コンサル出身）",
                      former_companies=["アクセンチュア"], tags=["consultant"],
                      profile_url="https://example.com/c009"),
]


def test_recruit_flag_detected():
    flags = candidate_flags(make_candidate(prior_companies=["リクルート"]))
    assert flags["is_recruit"] is True
    assert flags["is_insurance"] is False


def test_insurance_flag_detected():
    cand = make_candidate(prior_companies=["プルデンシャル生命"], industry="保険")
    flags = candidate_flags(cand)
    assert flags["is_insurance"] is True


def test_recruit_candidate_matches_all_recruit_consultants():
    cand = make_candidate(prior_companies=["リクルート"], industry="人材")
    matches = match_consultants(cand, consultants=SAMPLE)
    ids = {m.consultant.id for m in matches}
    assert {"c001", "c002"}.issubset(ids)
    assert all(m.category == "recruit" for m in matches if m.consultant.id in {"c001", "c002"})


def test_insurance_candidate_matches_insurance_consultant():
    cand = make_candidate(prior_companies=["第一生命"], industry="生命保険",
                          current_company="第一生命")
    matches = match_consultants(cand, consultants=SAMPLE)
    ids = {m.consultant.id for m in matches}
    assert "c008" in ids


def test_no_common_ground_yields_no_match():
    cand = make_candidate(prior_companies=["無関係株式会社"], industry="製造",
                          current_company="無関係株式会社", university="無名大学",
                          job_function="製造")
    matches = match_consultants(cand, consultants=SAMPLE)
    assert matches == []


def test_render_block_includes_url():
    cand = make_candidate(prior_companies=["リクルート"])
    matches = match_consultants(cand, consultants=SAMPLE)
    block = render_matches_block(matches)
    assert "https://example.com/c001" in block


def test_render_block_includes_consultant_id():
    # emit_scout の consultant_intros[].consultant_id で参照するための識別子。
    cand = make_candidate(prior_companies=["リクルート"])
    matches = match_consultants(cand, consultants=SAMPLE)
    block = render_matches_block(matches)
    assert "consultant_id: c001" in block


# --- select_intro_matches（人数上限＋全メールに必ず紹介を載せる保証）------------

def test_select_intro_matches_caps_to_configured_max():
    cand = make_candidate(prior_companies=["リクルート"], industry="人材")
    matches = match_consultants(cand, consultants=SAMPLE)
    assert len(matches) > 1  # SAMPLEにはrecruitマッチが複数ある
    capped = select_intro_matches(
        cand, matches, rules={"matching": {"max_intro_consultants": 1}}, consultants=SAMPLE)
    assert len(capped) == 1
    assert capped[0].consultant.id == matches[0].consultant.id  # 優先順位の先頭を維持


def test_select_intro_matches_zero_means_none():
    # max_intro_consultants=0 のときのみ紹介を完全に無効化する（min も無視）。
    cand = make_candidate(prior_companies=["リクルート"], industry="人材")
    matches = match_consultants(cand, consultants=SAMPLE)
    capped = select_intro_matches(
        cand, matches, rules={"matching": {"max_intro_consultants": 0}}, consultants=SAMPLE)
    assert capped == []


def test_select_intro_matches_default_caps_at_three():
    cand = make_candidate(prior_companies=["リクルート"], industry="人材")
    matches = match_consultants(cand, consultants=SAMPLE)
    capped = select_intro_matches(cand, matches, consultants=SAMPLE)  # 既定 max=3
    assert len(capped) <= 3


def test_select_intro_matches_guarantees_min_when_no_common_ground():
    # 共通点マッチが0でも、フォールバックで最低1名を必ず確保する（最重要要件）。
    cand = make_candidate(prior_companies=["無関係株式会社"], industry="製造",
                          current_company="無関係株式会社", university="無名大学",
                          job_function="製造")
    matches = match_consultants(cand, consultants=SAMPLE)
    assert matches == []  # 共通点は無い
    intro = select_intro_matches(
        cand, matches,
        rules={"matching": {"max_intro_consultants": 3, "min_intro_consultants": 1}},
        consultants=SAMPLE)
    assert len(intro) >= 1  # それでも必ず1名以上紹介する
    assert intro[0].category in ("soft", "fallback")


def test_select_intro_matches_warns_when_pool_empty(caplog):
    # コンサルタントデータが空だと保証を満たせない → 黙殺せず警告を出す（事故検知）。
    cand = make_candidate(prior_companies=["無関係"], industry="製造", job_function="製造")
    with caplog.at_level("WARNING"):
        intro = select_intro_matches(
            cand, [], rules={"matching": {"max_intro_consultants": 3,
                                          "min_intro_consultants": 1}},
            consultants=[])  # プールが空
    assert intro == []
    assert any("保証人数" in r.message for r in caplog.records)


def test_select_intro_matches_soft_match_by_specialty():
    # 共通点マッチ（企業/業界/大学/職種）は無いが、専門領域が候補者の職種に近い場合、
    # soft マッチとして紹介に含める（roles は空なので共通点マッチにはならない）。
    consultants = [
        ConsultantProfile(id="s1", display_name="営業のプロ", roles=[],
                          specialties=["法人営業支援"], profile_url="https://example.com/s1"),
    ]
    cand = make_candidate(prior_companies=["無関係"], current_company="無関係",
                          industry="製造", university="無名", job_function="法人営業")
    matches = match_consultants(cand, consultants=consultants)
    assert matches == []  # 共通点マッチは無い
    intro = select_intro_matches(cand, matches, consultants=consultants)
    assert [m.consultant.id for m in intro] == ["s1"]
    assert intro[0].category == "soft"


# --- render_consultant_intro_section（1人ずつ独立したブロックの組み立て）--------

def test_render_consultant_intro_section_format():
    m1 = ConsultantMatch(
        consultant=ConsultantProfile(id="a", display_name="A太郎",
                                     profile_url="https://example.com/a"),
        common_points=["共通点"],
    )
    m2 = ConsultantMatch(
        consultant=ConsultantProfile(id="b", display_name="B次郎",
                                     profile_url="https://example.com/b"),
        common_points=["共通点"],
    )
    section = render_consultant_intro_section(
        "余談ですが紹介します。",
        {"a": "Aさんの紹介文。", "b": "Bさんの紹介文。"},
        [m1, m2],
    )
    # 導入文は先頭コンサルタントの紹介文と地続き（改行のみ・空行なし）。
    assert section.startswith(
        "余談ですが紹介します。\nAさんの紹介文。\n▼A太郎 プロフィール\nhttps://example.com/a"
    )
    # 2人目以降は空行で区切られた独立ブロック。
    assert "\n\nBさんの紹介文。\n▼B次郎 プロフィール\nhttps://example.com/b" in section


def test_render_consultant_intro_section_case_insensitive_consultant_id():
    # モデルが consultant_id を 'Inoue' のように大文字始まりで返しても
    # カタログ側の 'inoue' と一致させ、紹介ブロックを取りこぼさない。
    m1 = ConsultantMatch(
        consultant=ConsultantProfile(id="inoue", display_name="井ノ上 貴之",
                                     profile_url="https://example.com/inoue"),
        common_points=["共通点"],
    )
    section = render_consultant_intro_section("導入文", {"Inoue": "紹介文。"}, [m1])
    assert "▼井ノ上 貴之 プロフィール" in section
    assert "紹介文。" in section


def test_render_consultant_intro_section_strips_duplicate_heading_and_url():
    # モデルが指示に反しblurb内に▼見出しとURLを重複して書いてしまっても、
    # 最終出力では1回だけしか出現しない（二重表示を防ぐ）。
    m1 = ConsultantMatch(
        consultant=ConsultantProfile(id="a", display_name="A太郎",
                                     profile_url="https://example.com/a"),
        common_points=["共通点"],
    )
    bad_blurb = (
        "Aさんは共通点があります。\n"
        "▼A太郎 プロフィール\n"
        "https://example.com/a"
    )
    section = render_consultant_intro_section("導入文", {"a": bad_blurb}, [m1])
    assert section.count("▼A太郎 プロフィール") == 1
    assert section.count("https://example.com/a") == 1
    assert "Aさんは共通点があります。" in section


def test_render_consultant_intro_section_fills_missing_blurb_by_default():
    # モデルが blurb を出さなくても、既定紹介文（default_blurb）を補い必ずブロックを出す。
    m1 = ConsultantMatch(
        consultant=ConsultantProfile(id="a", display_name="A太郎",
                                     specialties=["経営戦略"],
                                     profile_url="https://example.com/a"),
        common_points=["共通点"],
    )
    section = render_consultant_intro_section("導入文", {}, [m1])
    assert section != ""
    assert "▼A太郎 プロフィール" in section
    assert "A太郎" in section  # 既定紹介文にも氏名が入る


def test_render_consultant_intro_section_can_skip_missing_when_disabled():
    # fill_missing=False を明示した場合のみ、blurb 未提供はブロックごと省略する。
    m1 = ConsultantMatch(
        consultant=ConsultantProfile(id="a", display_name="A太郎",
                                     profile_url="https://example.com/a"),
        common_points=["共通点"],
    )
    assert render_consultant_intro_section("導入文", {}, [m1], fill_missing=False) == ""


def test_render_consultant_intro_section_empty_matches_yields_empty_string():
    assert render_consultant_intro_section("導入文", {"a": "紹介文"}, []) == ""
