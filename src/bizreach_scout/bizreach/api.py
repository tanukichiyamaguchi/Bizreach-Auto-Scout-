"""ビズリーチの内部JSON APIクライアント（候補者検索・レジュメ取得）。

ビズリーチのスカウト画面はReact + JSON APIで動作する。DOMを追うより、
認証済みブラウザコンテキストからAPIを直接呼ぶ方が確実。

判明しているエンドポイント（cr-support.jp）:
- GET  /api/v2/candidates/searchConditions/{rrsc}      保存検索の条件
- POST /api/v2/candidates:search                        候補者一覧（ページング）
- GET  /api/v2/candidates/{mrccid}/resume               候補者レジュメ（会員番号・職歴）
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from ..foreign import has_country_tag, is_japanese_language_school, is_overseas_school_name
from ..logging_config import logger
from ..models import Candidate, Education, Employment, Gender
from .errors import BizreachAuthError

_GRADE_MAP = {
    "Doctor": Education.doctor,
    "Master": Education.master,
    "Masters": Education.master,
    "Bachelor": Education.bachelor,
    "Bachelors": Education.bachelor,
    "Associate": Education.associate,
    "Vocational": Education.vocational,
    "HighSchool": Education.high_school,
}


def _map_grade(raw: str) -> Education:
    """schoolGrade(英語enum)を Education 水準へ写像する。

    ビズリーチの enum を _GRADE_MAP で網羅しきれない（例: 高専・専門・短大卒は
    Associate/Vocational とは別の値で返る）ため、未知値はキーワードで水準を推定する。
    「大学卒以上」を取りこぼさない（=誤って不明/下位に落とさない）ことを最優先に、
    上位学歴から順に判定する。高専・専門・短大卒は大学卒未満として扱う。
    真に判別不能な値のみ unknown を返す。
    """
    if not raw:
        return Education.unknown
    exact = _GRADE_MAP.get(raw)
    if exact is not None:
        return exact
    low = raw.lower()
    if any(k in low for k in ("doctor", "phd", "doctorate")):
        return Education.doctor
    # MBA/EMBA/LLM 等の専門職学位は大学院卒（master 相当）。実データで "MBA" を確認済み。
    if ("master" in low or "mba" in low or "llm" in low or "postgrad" in low
            or ("graduate" in low and "undergrad" not in low)):
        return Education.master
    if any(k in low for k in ("bachelor", "university", "undergrad")):
        return Education.bachelor
    if any(k in low for k in ("associate", "junior", "technical")):
        return Education.associate  # 短大・高専相当（大学卒未満）
    if any(k in low for k in ("vocational", "special", "college", "professional")):
        return Education.vocational  # 専門相当（大学卒未満）
    if "high" in low and "school" in low:
        return Education.high_school
    return Education.unknown


def _income_label(code: str | None) -> str:
    if not code:
        return ""
    m = re.match(r"Between(\d+)And(\d+)", code)
    if m:
        return f"{m.group(1)}〜{m.group(2)}万円"
    m = re.match(r"Upper(\d+)", code)
    if m:
        return f"{m.group(1)}万円以上"
    m = re.match(r"Under(\d+)", code)
    if m:
        return f"{m.group(1)}万円未満"
    return code


def _ja(node) -> str:
    """{"ja": "...", "en": ...} 形式から日本語を取り出す。"""
    if isinstance(node, dict):
        return (node.get("ja") or "").strip()
    return (node or "").strip() if isinstance(node, str) else ""


def _en(node) -> str:
    """{"ja": ..., "en": "..."} 形式から英語を取り出す（外国人判定用）。"""
    if isinstance(node, dict):
        return (node.get("en") or "").strip()
    return ""


# --- 語学欄の抽出 -----------------------------------------------------------
# ビズリーチのレジュメJSONで語学欄が入っている可能性のあるフィールド名（実データで
# 確定するまでの候補）。1件でも該当すれば採用する。
_LANGUAGE_KEYS = (
    "languages", "languageSkills", "languageExperiences",
    "foreignLanguages", "languageAbilities", "languageList",
)
# 語学エントリ内で「言語名」を持つ可能性のあるキー。
_LANG_NAME_KEYS = ("name", "language", "languageName", "languageType", "type")
# 語学エントリ内で「言語コード」を持つキー。実データ（2026-09-09 実行ログ）で語学欄は
#   languageSkills: [{"languageCode": "EN", "level": "Basic"}]
# の構造と確定した（言語名ではなくコード）。コードは日本語名へ写像して「英語：基礎レベル」
# の形にする（外国人判定 foreign.py は日本語の言語名・レベル語で照合するため）。
_LANG_CODE_KEYS = ("languageCode", "langCode", "code")
# 語学エントリ内で「レベル」を持つ可能性のあるキー。
_LANG_LEVEL_KEYS = ("level", "proficiency", "languageLevel", "skillLevel", "grade", "ability")
# 言語コード（ISO 639-1 大文字。ビズリーチ独自の北京語・広東語等のコードも推定で含める）→ 日本語名。
_LANGUAGE_CODE_NAMES = {
    "JA": "日本語", "JP": "日本語", "EN": "英語", "ZH": "中国語", "ZH_CN": "北京語", "CMN": "北京語",
    "ZH_TW": "台湾語", "YUE": "広東語", "ZH_HK": "広東語", "KO": "韓国語", "FR": "フランス語",
    "DE": "ドイツ語", "ES": "スペイン語", "PT": "ポルトガル語", "IT": "イタリア語", "RU": "ロシア語",
    "TL": "タガログ語", "FIL": "タガログ語", "VI": "ベトナム語", "TH": "タイ語", "ID": "インドネシア語",
    "HI": "ヒンディー語", "AR": "アラビア語", "MS": "マレー語", "NL": "オランダ語", "SV": "スウェーデン語",
    "TR": "トルコ語", "PL": "ポーランド語", "MY": "ミャンマー語", "NE": "ネパール語", "BN": "ベンガル語",
    "UR": "ウルドゥー語", "TA": "タミル語", "MN": "モンゴル語", "KM": "クメール語", "FA": "ペルシャ語",
    "UK": "ウクライナ語", "EL": "ギリシャ語", "HE": "ヘブライ語", "CS": "チェコ語", "HU": "ハンガリー語",
    "DA": "デンマーク語", "FI": "フィンランド語", "NO": "ノルウェー語", "SW": "スワヒリ語",
}
# レベル enum（小文字）→ 画面表記。実データで "Basic" / "None"（レベル未設定）を確認済み。
_LANGUAGE_LEVEL_LABELS = {
    "native": "ネイティブレベル", "business": "ビジネス会話レベル", "daily": "日常会話レベル",
    "conversation": "日常会話レベル", "conversational": "日常会話レベル", "basic": "基礎レベル",
    "beginner": "初級レベル", "elementary": "初級レベル", "intermediate": "中級レベル",
    "advanced": "上級レベル", "fluent": "上級レベル", "none": "",
}
# 未知の言語コード・レベル enum は各1回だけログに出す（マッピング追加のサイン）。
_logged_language_codes: set[str] = set()
_logged_language_levels: set[str] = set()

# --- 居住地 -----------------------------------------------------------------
# レジュメの location は国内なら "J" + 都道府県コード2桁（例: J13=東京都）。それ以外の
# 非空コードは海外在住（例: アメリカ・カナダ）とみなす。未知のコードは1回だけログに出す。
_DOMESTIC_LOCATION = re.compile(r"^J\d{2}$")
_logged_location_codes: set[str] = set()


def is_overseas_location(code: str | None) -> bool:
    """居住地コードが国内の都道府県（J01〜J47）でない＝海外在住か。空は不明として False。"""
    code = (code or "").strip()
    return bool(code) and not _DOMESTIC_LOCATION.match(code)

# 語学欄フィールド名を実データから特定するための調査ログ（各1回だけ出す）。
_logged_resume_keys = False
_logged_language_field = False
_logged_language_raw = False
_logged_desired_keys = False

# --- 希望条件（興味のある働き方・希望職種・希望業界）の抽出 -------------------
# 重要: これらは **desiredConditions（希望条件）配下のみ** から取る。レジュメの
# トップレベルにある industries / jobCategories は「経験してきた業界・職種」であり
# 「希望」ではないため、希望として扱うと文面に事実と異なる内容が入る。
# desiredConditions の実キーは本番ログ（2026-07-28）で確認済みで、以下の8つ:
#   considerableAbroadEmploymentTypes / income / industries / jobCategories /
#   jobChangePeriod / otherDesiredText / workLocations / workStyles
# 確認済みキーを先頭に置き、表記ゆれ対策のエイリアスを1つずつ残す（将来キー名が
# 変わっても _extract_desired のキー一覧ログで気づける）。取れなければ空のままにする
# （空ならプロンプトに出ず、モデルは言及できない＝嘘を防ぐ）。
_WORK_STYLE_KEYS = ("workStyles", "workStyle")
_DESIRED_JOB_KEYS = ("jobCategories", "desiredJobCategories")
_DESIRED_INDUSTRY_KEYS = ("industries", "desiredIndustries")
_DESIRED_LOCATION_KEYS = ("workLocations", "workLocation")
_DESIRED_OTHER_KEYS = ("otherDesiredText", "otherDesired")

# 自由記述（その他の希望）はレジュメによっては非常に長いため、プロンプトに載せる分だけ残す。
_DESIRED_OTHER_MAX = 400


def _flatten_labels(val: object, depth: int = 0) -> str:
    """希望条件の値（文字列/リスト/辞書）を「、」区切りの読みやすい文字列にする。"""
    out: list[str] = []

    def walk(v: object, d: int) -> None:
        if d > 4:
            return
        if isinstance(v, str):
            s = v.strip()
            if s:
                out.append(s)
        elif isinstance(v, dict):
            s = _ja(v) or _en(v)
            if s:
                out.append(s)
                return
            for k in ("name", "label", "text", "title", "value"):
                if k in v:
                    walk(v[k], d + 1)
                    return
        elif isinstance(v, list):
            for x in v:
                walk(x, d + 1)

    walk(val, depth)
    seen: set[str] = set()
    uniq: list[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return "、".join(uniq)


def _pick_desired(desired: dict, keys: tuple[str, ...]) -> str:
    """希望条件 dict から、候補キーのうち最初に値が取れたものを文字列で返す。"""
    if not isinstance(desired, dict):
        return ""
    for k in keys:
        if k in desired:
            text = _flatten_labels(desired[k])
            if text:
                return text
    return ""


_DESIRED_FIELDS = ("work_style", "desired_jobs", "desired_industries",
                   "desired_locations", "desired_other")


def _extract_desired(resume: dict) -> dict[str, str]:
    """希望条件（働き方・職種・業界・勤務地・その他の自由記述）を抽出する。

    キー一覧は初回だけログに出す（値は出さない＝個人情報を残さない）。ビズリーチ側で
    キー名が変わったらこのログで気づける。取得できなければ空文字のままとし、
    プロンプトにも出さない（モデルが推測で「リモート希望とのことですが」等と
    書く事故を防ぐ）。
    """
    global _logged_desired_keys
    desired = resume.get("desiredConditions")
    if isinstance(desired, dict) and not _logged_desired_keys:
        logger.info("希望条件(desiredConditions)のキー一覧（働き方の項目特定用）: %s",
                    sorted(desired.keys()))
        _logged_desired_keys = True
    if not isinstance(desired, dict):
        return dict.fromkeys(_DESIRED_FIELDS, "")
    other = _pick_desired(desired, _DESIRED_OTHER_KEYS)
    if len(other) > _DESIRED_OTHER_MAX:
        other = other[:_DESIRED_OTHER_MAX] + "…"
    return {
        "work_style": _pick_desired(desired, _WORK_STYLE_KEYS),
        "desired_jobs": _pick_desired(desired, _DESIRED_JOB_KEYS),
        "desired_industries": _pick_desired(desired, _DESIRED_INDUSTRY_KEYS),
        "desired_locations": _pick_desired(desired, _DESIRED_LOCATION_KEYS),
        "desired_other": other,
    }


def _lang_field(item: dict, keys: tuple[str, ...]) -> str:
    """語学エントリから、候補キーのうち最初に値が取れたもの（ja→en→文字列）を返す。"""
    for k in keys:
        if k in item and (s := (_ja(item[k]) or _en(item[k]))):
            return s
    return ""


def _lang_name(item: dict) -> str:
    """語学エントリの言語名。言語名が無ければ言語コード（EN 等）を日本語名へ写像する。"""
    name = _lang_field(item, _LANG_NAME_KEYS)
    if name:
        return name
    code = _lang_field(item, _LANG_CODE_KEYS)
    if not code:
        return ""
    key = code.upper().replace("-", "_")
    mapped = _LANGUAGE_CODE_NAMES.get(key)
    if mapped is None:
        if key not in _logged_language_codes:
            logger.info("語学欄の未知の言語コード（マッピング追加のサイン）: %s", code)
            _logged_language_codes.add(key)
        return code
    return mapped


def _lang_level(item: dict) -> str:
    """語学エントリのレベル。英語 enum（Native/Business/Basic…）は画面表記へ写像する。"""
    level = _lang_field(item, _LANG_LEVEL_KEYS)
    if not level:
        return ""
    label = _LANGUAGE_LEVEL_LABELS.get(level.lower())
    if label is None:
        # 日本語表記（CSV等）や未知 enum はそのまま使う。英字の未知 enum は1回だけログに出す。
        if level.isascii() and level not in _logged_language_levels:
            logger.info("語学欄の未知のレベル値（マッピング追加のサイン）: %s", level)
            _logged_language_levels.add(level)
        return level
    return label


def _extract_languages(resume: dict) -> str:
    """レジュメの語学欄を「言語名：レベル」形式の文字列に変換する。

    実データでフィールド名は languageSkills（2026-07-14 実行ログ）、内部構造は
    [{"languageCode": "EN", "level": "Basic"}]（2026-09-09 実行ログ）と確認済み。
    言語コードは日本語名へ、レベル enum は画面表記へ写像し「英語：基礎レベル」の形にする。
    構造が想定と異なり抽出できなかった場合は、生の構造を1回だけログに出して次回の
    修正材料にする（語学情報のみで氏名等は含まない）。
    """
    global _logged_resume_keys, _logged_language_field, _logged_language_raw
    if not _logged_resume_keys:
        logger.info("レジュメのトップレベルキー（語学欄フィールド特定用）: %s", sorted(resume.keys()))
        _logged_resume_keys = True

    for key in _LANGUAGE_KEYS:
        val = resume.get(key)
        entries: list[str] = []
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    name = _lang_name(item)
                    if name:
                        level = _lang_level(item)
                        entries.append(f"{name}：{level}".rstrip("："))
                elif isinstance(item, str) and item.strip():
                    entries.append(item.strip())
        elif isinstance(val, str) and val.strip():
            entries.append(val.strip())
        if entries:
            text = "、".join(entries)
            if not _logged_language_field:
                logger.info("語学欄フィールドを検出: '%s' → %s", key, text[:200])
                _logged_language_field = True
            return text
        if val and not _logged_language_raw:
            # フィールドは存在するのに想定構造で抽出できなかった。生構造を1回だけ出す
            # （語学欄と履歴書言語のみ。次回このログを見て _LANG_*_KEYS を確定させる）。
            try:
                raw = json.dumps(val, ensure_ascii=False)[:600]
            except (TypeError, ValueError):
                raw = repr(val)[:600]
            # 空振りが静かに続くと語学欄の外国人判定が丸ごと効かなくなる（2026-07〜09 に実際に
            # 発生）ため warning にする。
            logger.warning("語学欄 '%s' は存在するが未対応の構造（語学判定が効いていない）: %s / resumeLanguage=%r",
                           key, raw, resume.get("resumeLanguage"))
            _logged_language_raw = True
    return ""


def _years_since(period_from: dict | None, now: datetime | None = None) -> float | None:
    if not period_from or "year" not in period_from:
        return None
    now = now or datetime.now()
    y = period_from.get("year")
    mth = period_from.get("month") or 1
    if not y:
        return None
    return round((now.year - y) + (now.month - mth) / 12.0, 1)


def _career_text(ce: dict) -> str:
    """1社分の職務経歴を読みやすいテキストに整形。"""
    parts = []
    for cc in ce.get("companyCareers", []) or []:
        name = _ja(cc.get("name"))
        contents = cc.get("contents", {}) or {}
        lines = contents.get("ja") or []
        body = "\n".join(x for x in lines if x)
        if name or body:
            parts.append((name + "\n" + body).strip())
    return "\n".join(parts)


def resume_to_candidate(resume: dict, mrccid: str | None = None,
                        now: datetime | None = None) -> Candidate | None:
    """レジュメAPIのJSONを Candidate に変換する。"""
    if not isinstance(resume, dict):
        return None
    member_no = resume.get("bizreachUserId") or ""
    mrccid = mrccid or resume.get("mrccid") or ""
    if not member_no and not mrccid:
        return None

    gender = {"Male": Gender.male, "Female": Gender.female}.get(
        resume.get("gender", ""), Gender.unknown
    )

    # --- 学歴・大学 ---
    # エントリ順に依存せず、全学歴レコードの最上位（最高ランク）を最終学歴とする。
    education = Education.unknown
    university = ""
    overseas_education = False
    japanese_language_school = False
    edus = resume.get("educations") or []
    if edus:
        best_name = ""
        for e in edus:
            g = _map_grade(e.get("schoolGrade", ""))
            if g.rank > education.rank:
                education, best_name = g, _ja(e.get("name"))
        university = best_name or _ja(edus[0].get("name"))
        # どの学歴レコードも判別できない値のみ可視化（要マッピング追加のサイン）。
        if education is Education.unknown:
            raws = [e.get("schoolGrade", "") for e in edus if e.get("schoolGrade")]
            if raws:
                logger.info("未対応の学歴グレード（要マッピング確認）: %s（mrccid=%s 大学=%s）",
                            raws, mrccid or member_no, _ja(edus[0].get("name")))
        # 海外教育機関の判定: いずれかの学歴（高校を含む）の学校名が日本語表記でない（ja が空で
        # en のみ／ラテン文字のみ／カタカナ主体）か、国名タグ付き（「北京市第一六一中学（中国）」
        # 「イギリス コベントリー大学」）の場合を海外の学校とみなす。学校の所在国フィールドが
        # 無いための代替シグナルで、全学歴を対象に取りこぼさない。
        overseas_education = any(
            is_overseas_school_name(_ja(e.get("name")), _en(e.get("name")))
            or has_country_tag(_ja(e.get("name")))
            or has_country_tag(_en(e.get("name")))
            for e in edus
        )
        # 日本語学校（留学生向け日本語課程）の学歴: 「ISI〜」「〜日本語学校」「〜国際交流学園」等。
        japanese_language_school = any(
            is_japanese_language_school(_ja(e.get("name")))
            or is_japanese_language_school(_en(e.get("name")))
            for e in edus
        )

    # --- 職歴 ---
    companies = resume.get("companyExperiences") or []
    current_company = current_title = ""
    current_tenure = None
    employments: list[Employment] = []
    prior: list[str] = []
    career_blocks: list[str] = []
    for i, ce in enumerate(companies):
        name = _ja(ce.get("companyName"))
        title = _ja(ce.get("positionName"))
        yrs = _years_since((ce.get("period") or {}).get("from"), now)
        if i == 0:
            current_company, current_title, current_tenure = name, title, yrs
        else:
            if name:
                prior.append(name)
        if name:
            employments.append(Employment(company=name, title=title, years=yrs, industry=""))
        block = _career_text(ce)
        if name or block:
            career_blocks.append(f"■{name}（{title}）\n{block}".strip())

    # --- 要約・自己PR・実績（文面生成の材料）---
    summary_parts = [_ja(resume.get("jobSummary"))]
    for cc in resume.get("coreCompetencies") or []:
        summary_parts.append("・" + _ja(cc))
    summary = "\n".join(p for p in summary_parts if p)

    raw_parts = [
        f"会員番号: {member_no}",
        f"職務要約:\n{_ja(resume.get('jobSummary'))}",
        "職務経歴:\n" + "\n\n".join(career_blocks),
        "自己PR:\n" + _ja(resume.get("specialInstruction")),
    ]
    awards = resume.get("awards") or []
    if awards:
        raw_parts.append("表彰: " + "、".join(
            f"{a.get('year','')}{_ja(a.get('title'))}" for a in awards))
    quals = resume.get("qualifications") or []
    if quals:
        raw_parts.append("資格: " + "、".join(_ja(q.get("name")) for q in quals))
    raw_profile = "\n\n".join(p for p in raw_parts if p.strip().rstrip(":"))

    # --- 外国人判定用テキスト（生成には使わない）---
    # 英語で記入された職務要約・自己PR・職歴・学歴名・資格を集める。ja が空で en のみの
    # レジュメでも「英語優勢／外国語ネイティブ」の判定が効くようにするための材料。
    foreign_parts: list[str] = [
        _en(resume.get("jobSummary")),
        _en(resume.get("specialInstruction")),
    ]
    foreign_parts += [_en(cc) for cc in resume.get("coreCompetencies") or []]
    for ce in companies:
        foreign_parts.append(_en(ce.get("companyName")))
        for cc in ce.get("companyCareers", []) or []:
            en_lines = (cc.get("contents", {}) or {}).get("en") or []
            foreign_parts.append("\n".join(x for x in en_lines if x))
    foreign_parts += [_en(e.get("name")) for e in edus]
    foreign_parts += [_ja(q.get("name")) + " " + _en(q.get("name")) for q in quals]
    foreign_text = "\n".join(p for p in foreign_parts if p and p.strip())

    # 希望条件（興味のある働き方・希望職種・希望業界）。取得できなければ空のまま。
    desired = _extract_desired(resume)

    # --- 居住地（国内は J+都道府県コード。それ以外は海外在住とみなす）---
    loc = resume.get("location")
    residence = loc.strip() if isinstance(loc, str) else (_ja(loc) or _en(loc))
    overseas_residence = is_overseas_location(residence)
    if overseas_residence and residence not in _logged_location_codes:
        # コードの実値を把握するため（都道府県コード以外は初出のみ）。ID とコードだけでPIIは含まない。
        logger.info("居住地コードが国内都道府県(J01〜J47)以外: %s（mrccid=%s）→ 海外在住として扱う",
                    residence, mrccid or member_no)
        _logged_location_codes.add(residence)

    return Candidate(
        member_no=member_no or mrccid,
        mrccid=mrccid,
        age=resume.get("age"),
        gender=gender,
        education=education,
        university=university,
        overseas_education=overseas_education,
        japanese_language_school=japanese_language_school,
        residence=residence,
        overseas_residence=overseas_residence,
        current_company=current_company,
        current_title=current_title,
        current_tenure_years=current_tenure,
        prior_companies=prior,
        employments=employments,
        job_function=current_title,
        salary_current=_income_label(resume.get("income")),
        salary_desired=_income_label((resume.get("desiredConditions") or {}).get("income")),
        work_style=desired["work_style"],
        desired_jobs=desired["desired_jobs"],
        desired_industries=desired["desired_industries"],
        desired_locations=desired["desired_locations"],
        desired_other=desired["desired_other"],
        summary=summary,
        raw_profile=raw_profile,
        foreign_text=foreign_text,
        languages=_extract_languages(resume),
        source="bizreach",
        intention=resume.get("intention") or [],
        resume_updated_status=resume.get("resumeUpdatedStatus") or "",
        contract_plan=resume.get("contractPlan") or "",
        candidate_class=resume.get("candidateClass") or "",
    )


class BizreachApi:
    """認証済みブラウザコンテキストからビズリーチAPIを呼ぶ。"""

    def __init__(self, client):
        self.client = client
        self.base = client.sel.base_url.rstrip("/")
        self._platinum_remaining: int | None = None  # プラチナ残数キャッシュ

    @property
    def _req(self):
        return self.client.page.request

    @staticmethod
    def _raise_on_auth_error(status: int, body: dict | None) -> None:
        """認証切れを検出したら BizreachAuthError を送出する。

        実例: パスワード期限切れ時に全APIが
        {'status': 403, 'code': 'Unauthorized', 'reason': 'PasswordExpired'} を返し、
        従来は warning ログだけで「実行成功・送信0件」の緑になっていた。
        401、または 403 かつ code=Unauthorized のときのみ発火する
        （単発の権限系403で全体を落とさないよう保守的に判定）。
        """
        if status not in (401, 403):
            return
        body = body or {}
        code = str(body.get("code", ""))
        if status == 401 or code == "Unauthorized":
            reason = str(body.get("reason", ""))
            raise BizreachAuthError(
                f"ビズリーチ認証エラー status={status} code={code} reason={reason}。"
                "パスワード期限切れ/セッション失効の可能性があります。"
            )

    @staticmethod
    def _safe_json(resp) -> dict | None:
        try:
            data = resp.json()
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    @staticmethod
    def parse_rrsc(search_url: str) -> str | None:
        """検索URLの rrsc（保存検索ID）を取り出す。"""
        try:
            q = parse_qs(urlparse(search_url).query)
            return q.get("rrsc", [None])[0]
        except Exception:
            return None

    def get_saved_search(self, rrsc: str) -> dict | None:
        """保存検索の全体（condition＋job など）を返す。"""
        try:
            resp = self._req.get(f"{self.base}/api/v2/candidates/searchConditions/{rrsc}")
            if resp.status != 200:
                self._raise_on_auth_error(resp.status, self._safe_json(resp))
                logger.warning("検索条件の取得に失敗 status=%s", resp.status)
                return None
            return resp.json()
        except BizreachAuthError:
            raise
        except Exception as e:
            logger.warning("検索条件の取得で例外: %s", e)
            return None

    def get_search_condition(self, rrsc: str) -> dict | None:
        data = self.get_saved_search(rrsc)
        if data is None:
            return None
        return data.get("condition", data)

    def get_job_id(self, search_url: str) -> str | None:
        """保存検索に紐づく求人ID(jobId)を返す（スカウト送信に必須）。"""
        rrsc = self.parse_rrsc(search_url)
        if not rrsc:
            return None
        data = self.get_saved_search(rrsc)
        if not data:
            return None
        return (data.get("job") or {}).get("jobId")

    def search_page(self, condition: dict, page: int, page_size: int = 100) -> dict:
        body = {
            "searchId": None,
            "proposalSearchId": None,
            "paging": {"page": page, "maxPageSize": page_size},
            "condition": condition,
        }
        resp = self._req.post(f"{self.base}/api/v2/candidates:search", data=body)
        if resp.status != 200:
            self._raise_on_auth_error(resp.status, self._safe_json(resp))
            logger.warning("候補者検索に失敗 status=%s page=%s", resp.status, page)
            return {}
        return resp.json()

    def iter_candidate_ids(self, search_url: str, max_candidates: int = 50,
                           skip_mrccids: set[str] | None = None,
                           max_pages: int = 20) -> Iterator[str]:
        """保存検索から未評価の mrccid を順に返す（ページング）。

        skip_mrccids に評価済みの候補者を渡すと、それらを飛ばして**検索結果の奥へ
        進みながら** max_candidates 件の新しい候補者を集める。これを渡さないと
        毎回同じ上位N件を取り直し、検索結果の大半へ永久に到達しない
        （2026-08の本番で、1714件の検索から毎回同じ先頭10件だけを取得し、
        すべて対象外スキップされて送信0件が続いていた）。

        max_pages は暴走防止の上限。到達した場合は件数不足でも打ち切ってログに出す。
        """
        skip = skip_mrccids or set()
        rrsc = self.parse_rrsc(search_url)
        if not rrsc:
            logger.warning("検索URLから rrsc を取得できません: %s", search_url)
            return
        condition = self.get_search_condition(rrsc)
        if not condition:
            return
        page = 1
        yielded = 0
        skipped = 0
        while yielded < max_candidates and page <= max_pages:
            data = self.search_page(condition, page)
            items = data.get("items") or []
            if not items:
                break
            total = data.get("totalCount")
            logger.info("候補者検索 page=%d 取得=%d 総数=%s", page, len(items), total)
            for it in items:
                if yielded >= max_candidates:
                    break
                mid = it.get("mrccid")
                if not mid:
                    continue
                if mid in skip:
                    skipped += 1
                    continue
                yielded += 1
                yield mid
            if not data.get("hasNextPage"):
                break
            page += 1
            self.client.human_delay(1.0, 2.5)
        if skipped:
            logger.info("評価済みのため取り込みをスキップ: %d 件（%d ページ走査して新規 %d 件）",
                        skipped, page, yielded)
        if yielded < max_candidates and page > max_pages:
            logger.warning(
                "ページ上限(%d)に達したため新規候補者が %d 件で打ち切りました"
                "（未評価の候補者が枯渇しつつある可能性があります）。", max_pages, yielded)

    def get_resume(self, mrccid: str) -> dict | None:
        try:
            resp = self._req.get(f"{self.base}/api/v2/candidates/{mrccid}/resume")
            if resp.status != 200:
                self._raise_on_auth_error(resp.status, self._safe_json(resp))
                logger.warning("レジュメ取得に失敗 mrccid=%s status=%s", mrccid, resp.status)
                return None
            return resp.json()
        except BizreachAuthError:
            raise
        except Exception as e:
            logger.warning("レジュメ取得で例外 mrccid=%s: %s", mrccid, e)
            return None

    def get_candidate(self, mrccid: str) -> Candidate | None:
        resume = self.get_resume(mrccid)
        if resume is None:
            return None
        return resume_to_candidate(resume, mrccid)

    # --- スカウト送信 --------------------------------------------------------
    # フロントJS(sendScoutCandidates / sendScout(platinum))から判明したAPI契約:
    #   事前確認: POST /api/v2/scouts/checkCandidates  body={jobId, mrccids}
    #             → {candidates:[{mrccid, error, data}], errors}
    #   通常送信: POST /api/v2/scouts/candidates
    #             body={subject, body, dryRun, jobId, mrccids[], isReservation,
    #                   reminder, oneTimeToken}（oneTimeToken必要）
    #   プラチナ: POST /api/v2/scouts/platinum
    #             body={subject, body, dryRun, jobId, mrccid, isReservation,
    #                   reminder}（単数mrccid・oneTimeToken不要）
    #   共通: header Content-Type:application/json, x-idempotency-key(必須),
    #         任意 x-search-id / x-screen-type。認証はセッションcookie。
    # dryRun=True で実送信せずサーバ側検証のみ（安全確認用）。
    # 会員種別の振り分けは checkCandidates の error に基づく（ClassMismatch→プラチナ）。

    # 検索由来の送信であることを示す画面種別（JSのScreenType enumより）。
    SCREEN_TYPE_SAVED = "resume_search_with_saved_condition"

    @staticmethod
    def _reminder_obj(reminder: dict | None) -> dict | None:
        """reminderは null か {daysAfter, subject, body}（daysAfter∈ThreeDays等）。
        誤って文字列が来た場合は None に落として型不一致(400)を防ぐ。"""
        return reminder if isinstance(reminder, dict) else None

    def _post_json(self, path: str, payload: dict, extra_headers: dict) -> dict:
        headers = {"Content-Type": "application/json", **extra_headers}
        # 明示的にJSON文字列化（非ASCIIをそのまま送る）。dictでも可だが曖昧さを排除。
        resp = self._req.post(f"{self.base}{path}", headers=headers,
                              data=json.dumps(payload, ensure_ascii=False))
        out = {"status": resp.status}
        try:
            out.update(resp.json())
        except Exception:
            out["text"] = resp.text()[:2000]
        # 認証切れは恒久エラーとして即座に浮上させる（緑のまま全滅を防ぐ）。
        self._raise_on_auth_error(out.get("status", 0), out)
        return out

    def create_one_time_token(self) -> str | None:
        """送信用ワンタイムトークンを取得する（通常送信で必要）。

        注: 生成エンドポイントのパスはフロントJSのバンドルに現れず未確定。
        判明している候補パスを順に試す。取得できなければ None。
        """
        for path in ("/api/v2/oneTimeTokens", "/api/v2/scouts/oneTimeTokens",
                     "/api/v2/oneTimeToken", "/api/v2/scouts/oneTimeToken"):
            try:
                resp = self._req.post(f"{self.base}{path}", data={})
                if resp.status in (200, 201):
                    data = resp.json()
                    token = (data.get("oneTimeToken") or data.get("token")
                             or data.get("value"))
                    if token:
                        logger.info("ワンタイムトークンを取得 (%s)", path)
                        return token
            except Exception:
                continue
        logger.info("ワンタイムトークンを取得できませんでした（通常送信は不可の可能性）。")
        return None

    def get_platinum_scout_holders(self) -> dict:
        """プラチナスカウトの残数を取得する（GET /api/v2/scouts/platinum/holders）。

        戻り値 {"status", "count", "holderType"} 。count が月内の送信可能残数。
        """
        try:
            resp = self._req.get(f"{self.base}/api/v2/scouts/platinum/holders")
            out = {"status": resp.status}
            try:
                out.update(resp.json())
            except Exception:
                out["text"] = resp.text()[:500]
            return out
        except Exception as e:
            logger.warning("プラチナ残数の取得で例外: %s", e)
            return {"status": 0, "error": str(e)}

    def platinum_remaining(self, refresh: bool = False) -> int | None:
        """プラチナ残数（キャッシュ）。取得できない場合は None（不明）。"""
        if refresh or self._platinum_remaining is None:
            info = self.get_platinum_scout_holders()
            cnt = info.get("count")
            self._platinum_remaining = cnt if isinstance(cnt, int) else None
        return self._platinum_remaining

    def check_candidates(self, job_id: str, mrccids: list[str]) -> dict:
        """送信前チェック。候補者が送信可能か検証する。"""
        try:
            return self._post_json("/api/v2/scouts/checkCandidates",
                                   {"jobId": job_id, "mrccids": mrccids}, {})
        except BizreachAuthError:
            raise
        except Exception as e:
            logger.warning("送信前チェックで例外: %s", e)
            return {"status": 0, "error": str(e)}

    def send_scout(self, job_id: str, mrccid: str, subject: str, body: str,
                   dry_run: bool = True, search_id: str | None = None,
                   reminder: dict | None = None,
                   one_time_token: str | None = None,
                   idempotency_key: str | None = None) -> dict:
        """通常スカウトを送信する（POST /api/v2/scouts/candidates・oneTimeToken必要）。

        idempotency_key を呼び出し側（Repository.begin_send）から渡すと、
        クラッシュ後の再試行が同一キーで送られサーバ側dedupeが効く。
        """
        headers = {"x-idempotency-key": idempotency_key or str(uuid.uuid4()),
                   "x-screen-type": self.SCREEN_TYPE_SAVED}
        if search_id:
            headers["x-search-id"] = search_id
        payload = {
            "subject": subject, "body": body, "dryRun": dry_run,
            "jobId": job_id, "mrccids": [mrccid], "isReservation": False,
            "reminder": self._reminder_obj(reminder),
            "oneTimeToken": one_time_token,
        }
        try:
            out = self._post_json("/api/v2/scouts/candidates", payload, headers)
        except BizreachAuthError:
            raise
        except Exception as e:
            logger.error("スカウト送信で例外 mrccid=%s: %s", mrccid, e)
            return {"status": 0, "error": str(e)}
        self._log_send_result("通常", mrccid, dry_run, out)
        return out

    def send_platinum_scout(self, job_id: str, mrccid: str, subject: str, body: str,
                            dry_run: bool = True, search_id: str | None = None,
                            reminder: dict | None = None,
                            idempotency_key: str | None = None) -> dict:
        """プラチナスカウトを送信する（POST /api/v2/scouts/platinum・単数mrccid・token不要）。"""
        headers = {"x-idempotency-key": idempotency_key or str(uuid.uuid4()),
                   "x-screen-type": self.SCREEN_TYPE_SAVED}
        if search_id:
            headers["x-search-id"] = search_id
        payload = {
            "subject": subject, "body": body, "dryRun": dry_run,
            "jobId": job_id, "mrccid": mrccid, "isReservation": False,
            "reminder": self._reminder_obj(reminder),
        }
        try:
            out = self._post_json("/api/v2/scouts/platinum", payload, headers)
        except BizreachAuthError:
            raise
        except Exception as e:
            logger.error("プラチナ送信で例外 mrccid=%s: %s", mrccid, e)
            return {"status": 0, "error": str(e)}
        self._log_send_result("プラチナ", mrccid, dry_run, out)
        return out

    def send_pickup_scout(self, job_id: str, mrccid: str, subject: str, body: str,
                          dry_run: bool = True, search_id: str | None = None,
                          reminder: dict | None = None,
                          idempotency_key: str | None = None) -> dict:
        """本日のピックアップ枠でスカウト送信（POST /api/v2/scouts/pickup）。

        プラチナ残数を消費しない無料枠。単数mrccid・token不要。プラチナと同じボディ形状。
        """
        headers = {"x-idempotency-key": idempotency_key or str(uuid.uuid4()),
                   "x-screen-type": "daily_pickup_resume_list"}
        if search_id:
            headers["x-search-id"] = search_id
        payload = {
            "subject": subject, "body": body, "dryRun": dry_run,
            "jobId": job_id, "mrccid": mrccid, "isReservation": False,
            "reminder": self._reminder_obj(reminder),
        }
        try:
            out = self._post_json("/api/v2/scouts/pickup", payload, headers)
        except BizreachAuthError:
            raise
        except Exception as e:
            logger.error("ピックアップ送信で例外 mrccid=%s: %s", mrccid, e)
            return {"status": 0, "error": str(e)}
        self._log_send_result("ピックアップ", mrccid, dry_run, out)
        out["endpoint"] = "pickup"
        return out

    @staticmethod
    def _ok(out: dict) -> bool:
        """送信成功の判定。プラチナ/ピックアップは 201 Created、通常は 200 を返す。"""
        return out.get("status") in (200, 201)

    def _log_send_result(self, kind: str, mrccid: str, dry_run: bool, out: dict) -> None:
        if self._ok(out):
            logger.info("%sスカウト %s mrccid=%s (dryRun=%s, status=%s)",
                        kind, "検証OK" if dry_run else "完了", mrccid, dry_run,
                        out.get("status"))
        else:
            logger.warning("%sスカウト送信に失敗 mrccid=%s status=%s body=%s",
                           kind, mrccid, out.get("status"), out)

    def _platinum_send_guarded(self, job_id: str, mrccid: str, subject: str, body: str,
                               dry_run: bool, search_id: str | None,
                               reminder: dict | None, label: str = "platinum",
                               idempotency_key: str | None = None) -> dict:
        """残数ガード付きのプラチナ送信（本送信のみ残数を確認・減算）。"""
        remaining = self.platinum_remaining()
        if not dry_run and remaining is not None and remaining <= 0:
            logger.warning("プラチナ残数が0のため送信をスキップ mrccid=%s", mrccid)
            return {"status": 0, "skipped": "PlatinumQuotaExhausted",
                    "endpoint": label, "platinum_remaining": 0}
        out = self.send_platinum_scout(job_id, mrccid, subject, body,
                                       dry_run, search_id, reminder,
                                       idempotency_key=idempotency_key)
        out["endpoint"] = label
        if not dry_run and self._ok(out) and self._platinum_remaining is not None:
            self._platinum_remaining -= 1
        out["platinum_remaining"] = self._platinum_remaining
        return out

    def route_scout(self, job_id: str, mrccid: str, subject: str, body: str,
                    dry_run: bool = True, search_id: str | None = None,
                    reminder: dict | None = None,
                    idempotency_key: str | None = None) -> dict:
        """スカウトを送信する（プラチナスカウトが主・両会員種別に対応）。

        実運用はプラチナスカウト（/platinum・token不要）で、求人 scout_job_id を使えば
        Talent・HighClass の両方に送れる。checkCandidates は主に除外判定に使う:
          - error が ClassMismatch / なし → プラチナで送信
          - その他の error（既送信など）   → スキップ
        """
        check = self.check_candidates(job_id, [mrccid])
        err = None
        for c in check.get("candidates", []) or []:
            if c.get("mrccid") == mrccid:
                err = c.get("error")
                break

        # ClassMismatch はプラチナで送るので除外しない。それ以外の error はスキップ。
        if err and err != "ClassMismatch":
            logger.info("送信不可のためスキップ mrccid=%s error=%s", mrccid, err)
            return {"status": 0, "skipped": err, "endpoint": "skip"}

        return self._platinum_send_guarded(job_id, mrccid, subject, body,
                                           dry_run, search_id, reminder,
                                           idempotency_key=idempotency_key)
