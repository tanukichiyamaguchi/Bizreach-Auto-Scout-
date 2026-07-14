"""外国人（日本語ネイティブでない候補者）検出のヒューリスティック。

Bizreach のレジュメには「国籍」「母語」の直接フィールドが無いため、本人の
自己申告テキスト・学歴名・語学欄から次の3つの代替シグナルで外国人を判定する。

1. 海外の大学卒         … ``is_overseas_school_name``（学校名が日本語表記でない）
2. 外国語がネイティブ    … ``has_foreign_native_language``（英語/中国語等 × ネイティブ表記）
3. 職務要約・職歴が英語  … ``is_english_dominant``（テキストがほぼラテン文字）

方針: 「誤って外国人へ送る」より「疑わしきは要確認に回す」方が安全なため、
取りこぼしを減らす側（やや厳しめ）に閾値を置く。誤検知は送信されず要確認リストへ回る。
"""

from __future__ import annotations

import re

# --- 文字種の判定 -----------------------------------------------------------
# 日本語スクリプト: ひらがな・カタカナ・CJK拡張A・CJK統合漢字・半角カナ。
_JP_CHAR = re.compile(r"[぀-ゟ゠-ヿ㐀-䶿一-鿿ｦ-ﾝ]")
# ラテン文字（英語＋アクセント付き＝仏・独・西・葡等）。
_LATIN_CHAR = re.compile(r"[A-Za-zÀ-ɏ]")
# 漢字・ひらがな（＝国内校の目印。カタカナは含めない）。
_KANJI_OR_HIRAGANA = re.compile(r"[぀-ゟ㐀-䶿一-鿿]")
# カタカナ（全角・半角。長音符ー・中黒・を含む）。
_KATAKANA = re.compile(r"[゠-ヿｦ-ﾟ]")

# 校名から除いて「核」を取り出す接尾辞（長い順）。核が漢字を含めば国内校とみなす。
_UNIVERSITY_SUFFIXES = ("大学院大学", "大学院", "大学校", "大学")
# カタカナ表記だが国内の大学（カタカナ核でも海外と誤判定しないための許可リスト・随時追加可）。
_DOMESTIC_KATAKANA_SCHOOLS = (
    "サイバー",
    "デジタルハリウッド",
    "ハリウッド",
    "ビジネス・ブレークスルー",
    "ビジネスブレークスルー",
)

# 職務要約・職歴が「ほぼ英語」と見なす閾値。
_MIN_LATIN_LETTERS = 50  # これ未満は「英語しか書いていない」とは見なさない（誤検知防止）
_MAX_JP_RATIO = 0.15  # 日本語文字がこの割合未満＝ほぼ英語

# 「ネイティブ」等の申告語（小文字化後のテキストに対して照合）。
_NATIVE_MARKERS = (
    "ネイティブ",
    "ネーティブ",
    "母語",
    "母国語",
    "第一言語",
    "native",
    "mother tongue",
    "first language",
)
# 日本語以外の言語名（日本語表記＋英語表記、いずれも小文字）。網羅は完全でないが主要言語を広く含む。
_FOREIGN_LANGS = (
    "英語", "english",
    "中国語", "chinese", "mandarin", "cantonese", "北京語", "広東語",
    "韓国語", "朝鮮語", "korean", "ハングル",
    "フランス語", "仏語", "french", "français",
    "ドイツ語", "独語", "german", "deutsch",
    "スペイン語", "西語", "spanish", "español",
    "ポルトガル語", "portuguese", "português",
    "イタリア語", "italian", "italiano",
    "ロシア語", "russian",
    "タガログ語", "tagalog", "filipino",
    "ベトナム語", "vietnamese",
    "タイ語", "thai",
    "インドネシア語", "indonesian",
    "ヒンディー語", "hindi",
    "アラビア語", "arabic",
    "マレー語", "malay",
)
# 日本語を指す語（ネイティブ表記が日本語に紐づくケースを除外するために使う）。
_JP_LANGS = ("日本語", "japanese", "邦語")
# 《外国語名》と《ネイティブ表記》が「英語:ネイティブ」のように近接する場合のみ結び付ける。
# この距離（文字数）以内に両者があれば同一の語学申告とみなす。長文で無関係に共起した
# 「英語…（中略）…日本語はネイティブ」を誤検出しないための上限。
_NATIVE_PROXIMITY = 10


def _count(pattern: re.Pattern[str], text: str) -> int:
    return len(pattern.findall(text or ""))


def has_japanese_script(text: str | None) -> bool:
    """日本語スクリプト（かな・漢字・カナ）を1文字でも含むか。"""
    return bool(_JP_CHAR.search(text or ""))


def japanese_char_ratio(text: str | None) -> float:
    """「日本語＋ラテン文字」に占める日本語文字の割合（0.0〜1.0）。

    数字・記号・空白は分母から除く（会員番号や年収表記の影響を避ける）。
    文字が全く無ければ 1.0（＝日本語扱い＝安全側で外国人判定しない）。
    """
    jp = _count(_JP_CHAR, text or "")
    latin = _count(_LATIN_CHAR, text or "")
    total = jp + latin
    if total == 0:
        return 1.0
    return jp / total


def is_katakana_foreign_school(name: str | None) -> bool:
    """カタカナ主体の校名（例: "スタンフォード大学"）を海外の大学とみなす。

    校名から「大学／大学院／大学校」を取り除いた**核**が、漢字・ひらがなを含まず
    カタカナで構成される場合を海外とみなす。国内の大学は核に漢字を持つ
    （例: "早稲田" "慶應義塾" "立命館アジア太平洋" "ルーテル学院"）ので区別できる。
    カタカナ表記の国内大学（サイバー大学 等）は許可リストで除外する。
    """
    name = (name or "").strip()
    if not name or any(dom in name for dom in _DOMESTIC_KATAKANA_SCHOOLS):
        return False
    core = name
    for suf in _UNIVERSITY_SUFFIXES:
        core = core.replace(suf, "")
    if _KANJI_OR_HIRAGANA.search(core):
        return False  # 漢字・ひらがなを含む＝国内校
    return bool(_KATAKANA.search(core))


def is_overseas_school_name(ja: str | None, en: str | None) -> bool:
    """学校名が海外（＝日本語表記でない）ものかを判定する。

    - ``ja`` が空で ``en`` のみ存在 … 海外（従来シグナル）。
    - ``ja`` がラテン文字のみ（例: "Stanford University"）… 海外。
    - ``ja`` がカタカナ主体（例: "スタンフォード大学"）… 海外（``is_katakana_foreign_school``）。
    漢字・ひらがなを含む日本語表記（例: "早稲田大学"）があれば海外と断定しない。
    """
    ja = (ja or "").strip()
    en = (en or "").strip()
    if not ja and en:
        return True
    if ja and not has_japanese_script(ja):
        return True
    return is_katakana_foreign_school(ja)


def _spans(needles: tuple[str, ...], text: str) -> list[tuple[int, int]]:
    """text 中の各 needle の出現位置 [start, end) を全て返す。"""
    out: list[tuple[int, int]] = []
    for n in needles:
        start = 0
        while (i := text.find(n, start)) >= 0:
            out.append((i, i + len(n)))
            start = i + 1
    return out


def _gap(a: tuple[int, int], b: tuple[int, int]) -> int:
    """2区間 [s,e) の隙間（重なっていれば負）を返す。"""
    return max(a[0] - b[1], b[0] - a[1])


def has_foreign_native_language(text: str | None) -> bool:
    """レジュメの**語学欄テキスト**に、日本語以外の言語を「ネイティブ／母語」と
    申告しているか。

    ⚠ この関数はレジュメの「語学（言語）」欄の文字列に対してのみ使うこと。
    職務要約・自己PR・職歴など本文全体に適用してはならない（「ネイティブ広告」等、
    語学と無関係の「ネイティブ」を誤検出するため）。

    「英語：ネイティブ」「native English」「母国語：中国語」等を検出する。各《ネイティブ表記》
    について**最も近い言語トークン**を求め、それが外国語で、かつ近接（``_NATIVE_PROXIMITY``
    文字以内）である場合のみ True。「日本語：ネイティブ / 英語：ビジネスレベル」は最近接が
    日本語のため誤検出しない（「英語：ビジネス／日常会話」は日本人に多く除外してはならない）。
    """
    low = (text or "").lower()
    markers = _spans(_NATIVE_MARKERS, low)
    if not markers:
        return False
    foreign = _spans(_FOREIGN_LANGS, low)
    if not foreign:
        return False
    japanese = _spans(_JP_LANGS, low)
    for mk in markers:
        # このネイティブ表記に最も近い外国語／日本語トークンの距離を比べる。
        nearest_foreign = min((_gap(mk, f) for f in foreign), default=None)
        if nearest_foreign is None or nearest_foreign > _NATIVE_PROXIMITY:
            continue
        nearest_jp = min((_gap(mk, j) for j in japanese), default=None)
        # 外国語が日本語より「厳密に」近いときのみ外国語ネイティブと判定する。
        # 同距離（例:「日本語：ネイティブ、英語：ビジネス」）は日本語側に紐づくとみなし誤検出しない。
        if nearest_jp is None or nearest_foreign < nearest_jp:
            return True
    return False


def is_english_dominant(text: str | None) -> bool:
    """職務要約・職歴などがほぼ英語で書かれているか。

    ラテン文字が十分な量あり、かつ日本語文字の割合が極めて低い場合に True。
    日本語主体（バイリンガルの日本人を含む）は日本語割合が高いので False。
    """
    if _count(_LATIN_CHAR, text or "") < _MIN_LATIN_LETTERS:
        return False
    return japanese_char_ratio(text) < _MAX_JP_RATIO
