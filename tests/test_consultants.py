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
    ConsultantProfile(id="c008", display_name="H.M（保険出身）",
                      former_companies=["サンプル生命"], industries=["保険"],
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
    cand = make_candidate(prior_companies=["サンプル生命"], industry="保険")
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


# --- 文面の虚偽混入を防ぐための照合ルール（実運用の苦情に対する回帰テスト）---

def test_common_points_lists_only_actually_matched_companies():
    # 従来は「実際に一致した社名」ではなく former_companies を全件列挙しており、
    # 一致していない会社まで『候補者と同じ出身企業』としてプロンプトに渡っていた。
    c = ConsultantProfile(id="x1", display_name="X", profile_url="u",
                          former_companies=["リクルート", "会計保守コンサルティング会社"])
    cand = make_candidate(current_company="株式会社リクルート", prior_companies=[],
                          industry="", university="", job_function="")
    block = render_matches_block(match_consultants(cand, consultants=[c]))
    assert "前職企業の共通点（リクルート）" in block
    # 「共通点」ラベルの括弧内には一致した社名だけを出す（一致していない会社を含めない）。
    common_label = block.split("共通点: ")[1].split("｜")[0]
    assert common_label == "前職企業の共通点（リクルート）"
    assert "会計保守" not in common_label
    # 一方、blurb の材料として「前職」欄には本人の実際の職歴を全て渡してよい（事実のため）。
    assert "前職: リクルート・会計保守コンサルティング会社" in block


def test_generic_company_terms_do_not_create_fake_commonality():
    # 「サービス業」等の業種の総称は企業名の共通点にしない（候補者の社名と部分一致するため）。
    c = ConsultantProfile(id="x2", display_name="Y", profile_url="u",
                          former_companies=["サービス業"])
    cand = make_candidate(current_company="株式会社サービスプロダクト", prior_companies=[],
                          industry="", university="", job_function="")
    assert match_consultants(cand, consultants=[c]) == []


def test_reverse_substring_does_not_match_different_company():
    # 候補者「トヨタ自動車」（メーカー本体）と「トヨタ自動車直系の自動車販売会社」は別会社。
    c = ConsultantProfile(id="x3", display_name="Z", profile_url="u",
                          former_companies=["トヨタ自動車直系の自動車販売会社"])
    cand = make_candidate(current_company="トヨタ自動車", prior_companies=[],
                          industry="", university="", job_function="")
    assert match_consultants(cand, consultants=[c]) == []


def test_role_stopwords_exclude_job_titles_from_commonality():
    # 役職名（課長・部長）が一致しても「職種の共通点」にはしない。
    c = ConsultantProfile(id="x4", display_name="W", profile_url="u",
                          roles=["課長", "法人営業"])
    cand = make_candidate(current_company="無関係", prior_companies=[], industry="",
                          university="", job_function="課長")
    assert match_consultants(cand, consultants=[c]) == []
    # 職種そのものが一致する場合は共通点として成立する。
    cand2 = make_candidate(current_company="無関係", prior_companies=[], industry="",
                           university="", job_function="法人営業")
    assert [m.consultant.id for m in match_consultants(cand2, consultants=[c])] == ["x4"]


def test_render_matches_block_includes_consultant_facts():
    # blurb の材料（前職・職種・専門・出身大学）をプロンプトへ渡す。渡していなかったため
    # モデルが経歴を知らないまま紹介文を書き、事実を創作していた。
    c = ConsultantProfile(id="x5", display_name="井ノ上", profile_url="u",
                          former_companies=["富士通"], roles=["ソフトウェア開発"],
                          specialties=["DX"], universities=["鹿児島大学"])
    cand = make_candidate(current_company="富士通", prior_companies=[], industry="",
                          university="", job_function="")
    block = render_matches_block(match_consultants(cand, consultants=[c]))
    for fact in ("前職: 富士通", "職種: ソフトウェア開発", "専門: DX", "出身大学: 鹿児島大学"):
        assert fact in block


def test_signer_is_excluded_from_matching_and_fallback():
    # 署名者（代表取締役社長・岩渕）は差出人本人なので紹介対象にしない。
    rules = {"matching": {"exclude_consultant_ids": ["iwabuchi"],
                          "exclude_consultant_names": ["岩渕"],
                          "min_intro_consultants": 1, "max_intro_consultants": 3}}
    pool = [
        ConsultantProfile(id="iwabuchi", display_name="岩渕 龍正（代表取締役社長）",
                          profile_url="u0", specialties=["マーケティング"]),
        ConsultantProfile(id="other", display_name="他 太郎", profile_url="u1",
                          specialties=["医院経営"]),
    ]
    cand = make_candidate(current_company="無関係", prior_companies=[], industry="",
                          university="", job_function="")
    matches = match_consultants(cand, consultants=pool, rules=rules)
    assert all(m.consultant.id != "iwabuchi" for m in matches)
    # 共通点ゼロでも fallback で岩渕を拾わず、他のコンサルタントで保証人数を満たす。
    intro = select_intro_matches(cand, matches, rules=rules, consultants=pool)
    assert [m.consultant.id for m in intro] == ["other"]


def test_signer_excluded_by_name_even_if_id_changed():
    # import-consultants は id を振り直すため、氏名でも除外できること。
    rules = {"matching": {"exclude_consultant_ids": ["iwabuchi"],
                          "exclude_consultant_names": ["岩渕"],
                          "min_intro_consultants": 1, "max_intro_consultants": 3}}
    pool = [
        ConsultantProfile(id="c001", display_name="岩渕 龍正（代表取締役社長）",
                          profile_url="u0", specialties=["マーケティング"]),
        ConsultantProfile(id="c002", display_name="他 太郎", profile_url="u1",
                          specialties=["医院経営"]),
    ]
    cand = make_candidate(current_company="無関係", prior_companies=[], industry="",
                          university="", job_function="")
    intro = select_intro_matches(cand, match_consultants(cand, consultants=pool, rules=rules),
                                 rules=rules, consultants=pool)
    assert [m.consultant.id for m in intro] == ["c002"]


def test_departed_consultants_are_absent_from_data_not_just_excluded():
    """在籍していない人は除外リストではなく consultants.json から消えていること。

    除外リストは「在籍しているが紹介しない人（署名者本人など）」のためのもの。
    退職者をデータに残したまま除外だけで対処すると、氏名は出ないのに前職企業などの
    属性が訴求の根拠として生き残る（2026-08 の事故がこの形だった）。
    除外対象として残ってよいのは署名者のみ、というのがこのテストの不変条件。
    """
    from bizreach_scout.config import scout_rules

    cfg = scout_rules()["matching"]
    assert cfg.get("exclude_consultant_ids") == ["iwabuchi"]
    assert cfg.get("exclude_consultant_names") == ["岩渕"]


def test_signer_is_excluded_with_production_rules():
    """署名者は実データ・本番設定のどの経路でも紹介されない。"""
    from bizreach_scout.config import load_consultants, scout_rules

    pool = load_consultants()
    rules = scout_rules()
    signer = next((c for c in pool if "岩渕" in c.display_name), None)
    if signer is None:
        return  # サンプルデータ環境では対象外

    cand = make_candidate(
        current_company=(signer.former_companies[0] if signer.former_companies else "無関係"),
        prior_companies=list(signer.former_companies),
        university=(signer.universities[0] if signer.universities else ""),
        job_function=(signer.roles[0] if signer.roles else ""),
    )
    matches = match_consultants(cand, consultants=pool, rules=rules)
    intro = select_intro_matches(cand, matches, rules=rules, consultants=pool)
    assert all("岩渕" not in m.consultant.display_name for m in matches)
    assert all("岩渕" not in m.consultant.display_name for m in intro)
    assert len(intro) >= 1  # 除外しても紹介人数は確保される


# --- 在籍主張の裏づけ（visible_consultants_with_tag）--------------------------


def test_visible_consultants_with_tag_excludes_hidden_ones():
    """除外設定のコンサルタントは「在籍している」の根拠にならない。"""
    from bizreach_scout.consultants import visible_consultants_with_tag

    pool = [
        ConsultantProfile(id="hidden", display_name="非公開 太郎",
                          former_companies=["サンプル生命保険"],
                          tags=["insurance"]),
        ConsultantProfile(id="other", display_name="別 太郎", tags=["recruit"]),
    ]
    rules = {"matching": {"exclude_consultant_ids": ["hidden"]}}
    assert visible_consultants_with_tag("insurance", rules, pool) == []
    assert [c.id for c in visible_consultants_with_tag("recruit", rules, pool)] == ["other"]


def test_visible_consultants_with_tag_excludes_by_name_too():
    """ID が振り直されても氏名で除外できること（in_house 主張の保険）。"""
    from bizreach_scout.consultants import visible_consultants_with_tag

    pool = [ConsultantProfile(id="c999", display_name="非公開 太郎", tags=["insurance"])]
    rules = {"matching": {"exclude_consultant_names": ["非公開"]}}
    assert visible_consultants_with_tag("insurance", rules, pool) == []


def test_production_config_has_no_introducible_insurance_consultant():
    """実データで保険出身の紹介可能者がいないこと（在籍主張の前提を固定する）。

    ここが空である限り、保険出身候補者への「当社にも◯◯出身者が在籍」という
    訴求は出力されない。将来該当者が入社して consultants.json に追加されれば、
    このテストが落ちることで訴求が復活したことに気づける。
    """
    from bizreach_scout.consultants import visible_consultants_with_tag

    assert visible_consultants_with_tag("insurance") == []
