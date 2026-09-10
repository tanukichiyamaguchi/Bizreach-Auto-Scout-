"""外国人（日本語ネイティブでない候補者）検出のヒューリスティック。

Bizreach のレジュメには「国籍」「母語」の直接フィールドが無いため、本人の
自己申告テキスト・学歴名・語学欄・居住地から次の代替シグナルで外国人を判定する。

1. 海外の学校卒（高校を含む）… ``is_overseas_school_name``（学校名が日本語表記でない）
                              ``has_country_tag``（「北京市第一六一中学（中国）」「イギリス コベントリー大学」）
2. 日本語学校の学歴          … ``is_japanese_language_school``（留学生向け日本語課程。ISI・日本語学校 等）
3. 語学欄で日本語が非ネイティブ … ``japanese_listed_as_non_native``（「日本語：ビジネス会話レベル」等。
                              日本語ネイティブは語学欄に日本語を書かないか「ネイティブ」と書く）
4. 外国語がネイティブ        … ``has_foreign_native_language``（英語/北京語/ヒンディー語 等 × ネイティブ表記）
5. 職務要約・職歴が英語      … ``is_english_dominant``（テキストがほぼラテン文字）
6. 永住権・在留資格・ビザの自己申告 … ``has_residency_status_mention``
7. 居住地が海外              … （API の居住地コードで判定。``bizreach/api.py``）

方針: 「誤って外国人へ送る」より「疑わしきは要確認に回す」方が安全なため、
取りこぼしを減らす側（やや厳しめ）に閾値を置く。誤検知は送信されず要確認リストへ回る。
実例（2026-09 運用者提示の4名）は tests/test_foreign_profiles.py に固定してある。
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
    "ノースアジア",  # ノースアジア大学（秋田）
)
# 核から取り除く設置形態・分野の語（「カリフォルニア州立大学」「マサチューセッツ工科大学」の
# 州立・工科は校名の一部ではなく、海外校でも漢字になるため）。
_CORE_DESCRIPTORS = ("州立", "市立", "国立", "公立", "私立", "工科", "理工", "科技")
# 校名に大学系の接尾辞が無いときに核の終端とみなす区切り（学部・専攻が続く場合）。
_CORE_DELIMITER = re.compile(r"[\s\u3000/／]")

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
    core = _school_core(name)
    for word in _CORE_DESCRIPTORS:
        core = core.replace(word, "")
    if _KANJI_OR_HIRAGANA.search(core):
        return False  # 漢字・ひらがなを含む＝国内校
    return bool(_KATAKANA.search(core))


def _school_core(name: str) -> str:
    """校名の核＝最初に現れる「大学／大学院／大学校」の手前まで。

    「カラチ大学 数学/統計/コンピュータサイエンス」のように学部・専攻が後ろに続いても
    核は「カラチ」になる（従来は後続の漢字で国内校と誤判定していた）。接尾辞が無ければ
    最初の空白・スラッシュまでを核とする。
    """
    best: tuple[int, str] | None = None
    for suf in _UNIVERSITY_SUFFIXES:
        i = name.find(suf)
        if i >= 0 and (best is None or i < best[0] or (i == best[0] and len(suf) > len(best[1]))):
            best = (i, suf)
    if best is not None:
        return name[: best[0]]
    return _CORE_DELIMITER.split(name, 1)[0]


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


# --- 語学欄で「日本語」が非ネイティブとして申告されているか --------------------
# 語学欄の1エントリを区切る文字（API 経路は「、」区切りで生成する）。「/」「／」は
# UI の「英語 / ネイティブレベル」のように言語名とレベルの区切りにも使われるため含めない。
_ENTRY_SEPARATORS = "、,;；\n。"
# 「レベルを申告している」と見なす語。これが無い（例:「日本語のほか英語も可」）場合は
# レベル表記ではないので判定しない（誤検知防止）。
_LEVEL_MARKERS = (
    "レベル", "会話", "ビジネス", "日常", "初級", "中級", "上級", "基礎", "読み書き", "級",
    "business", "daily", "conversation", "basic", "elementary", "intermediate",
    "advanced", "fluent", "beginner", "level", "jlpt", "n1", "n2", "n3", "n4", "n5",
)


def japanese_listed_as_non_native(text: str | None) -> bool:
    """語学欄で「日本語」をネイティブ以外のレベルで申告しているか。

    日本語ネイティブは語学欄に日本語を書かないか「日本語：ネイティブ」と書く。
    「日本語：ビジネス会話レベル」「日本語：日常会話レベル」「Japanese: Business」は
    外国人がほぼ確実（運用者提示の実例 3/4 名がこの形）。

    「日本語」トークンの直後から、次のエントリ区切りまたは次の言語名までをその
    エントリのレベル表記とみなし、ネイティブ表記が無く、かつレベル語を含むときのみ True。
    レベル語が無い（「日本語 / 英語」のような列挙のみ）場合は判定しない。
    """
    low = (text or "").lower()
    if not low:
        return False
    stop_tokens = _FOREIGN_LANGS + _JP_LANGS
    for _start, end in _spans(_JP_LANGS, low):
        seg = low[end:]
        cut = len(seg)
        for sep in _ENTRY_SEPARATORS:
            i = seg.find(sep)
            if 0 <= i < cut:
                cut = i
        for tok in stop_tokens:
            i = seg.find(tok)
            if 0 <= i < cut:
                cut = i
        seg = seg[:cut]
        if any(m in seg for m in _NATIVE_MARKERS):
            continue
        if any(m in seg for m in _LEVEL_MARKERS):
            return True
    return False


# --- 学校名・企業名の国名タグ（例:「北京市第一六一中学（中国）」）------------------
# 本人が入力する学校名には所在国を括弧書きで添える例が多い。「（中国）」のように
# 括弧内が国名そのもの、または「イギリス コベントリー大学」のように先頭の国名の直後に
# 区切りがある場合のみ国名タグとみなす。「中国学園大学」「（中国語）」は一致しない。
_COUNTRY_NAMES = (
    # アジア
    "中国", "中華人民共和国", "台湾", "香港", "マカオ", "韓国", "大韓民国", "北朝鮮", "モンゴル",
    "インド", "パキスタン", "バングラデシュ", "スリランカ", "ネパール", "ブータン",
    "ベトナム", "タイ", "ラオス", "カンボジア", "ミャンマー", "マレーシア", "シンガポール",
    "インドネシア", "フィリピン", "ブルネイ", "ウズベキスタン", "カザフスタン",
    # 中東・アフリカ
    "イラン", "イラク", "イスラエル", "トルコ", "サウジアラビア", "アラブ首長国連邦", "UAE",
    "エジプト", "南アフリカ", "ナイジェリア", "ケニア", "ガーナ", "モロッコ",
    # 欧州
    "イギリス", "英国", "アイルランド", "フランス", "ドイツ", "イタリア", "スペイン", "ポルトガル",
    "オランダ", "ベルギー", "スイス", "オーストリア", "スウェーデン", "ノルウェー", "デンマーク",
    "フィンランド", "ポーランド", "チェコ", "ハンガリー", "ルーマニア", "ギリシャ", "ロシア",
    "ウクライナ",
    # 米州・オセアニア
    "アメリカ", "米国", "アメリカ合衆国", "カナダ", "メキシコ", "ブラジル", "アルゼンチン", "チリ",
    "コロンビア", "ペルー", "オーストラリア", "ニュージーランド",
    # 総称
    "海外", "国外",
    # 英語表記
    "China", "Taiwan", "Hong Kong", "Korea", "South Korea", "Mongolia", "India", "Pakistan",
    "Bangladesh", "Sri Lanka", "Nepal", "Vietnam", "Thailand", "Cambodia", "Myanmar",
    "Malaysia", "Singapore", "Indonesia", "Philippines", "Iran", "Israel", "Turkey",
    "Saudi Arabia", "Egypt", "South Africa", "Nigeria", "Kenya", "Morocco",
    "UK", "U.K.", "United Kingdom", "England", "Scotland", "Ireland", "France", "Germany",
    "Italy", "Spain", "Portugal", "Netherlands", "Belgium", "Switzerland", "Austria", "Sweden",
    "Norway", "Denmark", "Finland", "Poland", "Russia", "Ukraine",
    "USA", "U.S.A.", "U.S.", "United States", "America", "Canada", "Mexico", "Brazil",
    "Argentina", "Australia", "New Zealand", "Overseas",
)
_COUNTRY_ALT = "|".join(re.escape(c) for c in sorted(_COUNTRY_NAMES, key=len, reverse=True))
# 括弧内が国名そのもの: 「（中国）」「(China)」。
_COUNTRY_IN_PARENS = re.compile(rf"[（(]\s*(?:{_COUNTRY_ALT})\s*[)）]", re.IGNORECASE)
# 先頭の国名の直後に空白・中黒・スラッシュ: 「イギリス コベントリー大学」「中国・北京大学」。
_COUNTRY_LEADING = re.compile(rf"^\s*(?:{_COUNTRY_ALT})(?=[\s\u3000・/／])", re.IGNORECASE)


def has_country_tag(text: str | None) -> bool:
    """学校名などに日本以外の国名タグが付いているか（「北京市第一六一中学（中国）」等）。"""
    text = (text or "").strip()
    if not text:
        return False
    return bool(_COUNTRY_IN_PARENS.search(text) or _COUNTRY_LEADING.match(text))


# --- 日本語学校（留学生向け日本語課程）---------------------------------------------
# 校名にこれらを含む学校は日本語を母語としない留学生向け。日本語ネイティブは在籍しない。
# 「日本語教育」「日本語学科」は日本人（日本語教師志望・日本語学専攻）も該当するため含めない。
_JP_LANGUAGE_SCHOOL_KEYWORDS = (
    "日本語学校", "日本語学院", "日本語センター", "日本語別科", "留学生別科", "留学生センター",
    "日本語教育センター", "日本語課程", "日本語コース", "国際交流学園", "国際交流学院",
    "japanese language school", "japanese language institute", "japanese language center",
    "japanese language centre", "nihongo",
)
# 校名の先頭がこれで始まる学校グループ（ISI: ISIランゲージスクール／ISI外語カレッジ／
# ISIキャリア外語アカデミー 等はいずれも留学生向け日本語学校）。直後に英字が続く別名
# （例: "ISIS..."）は一致させない。
_JP_LANGUAGE_SCHOOL_PREFIXES = re.compile(r"^(?:ISI)(?![A-Za-z])")


def is_japanese_language_school(name: str | None) -> bool:
    """校名が留学生向けの日本語学校（日本語課程）を指すか。"""
    name = (name or "").strip()
    if not name:
        return False
    low = name.lower()
    if any(k in low for k in _JP_LANGUAGE_SCHOOL_KEYWORDS):
        return True
    return bool(_JP_LANGUAGE_SCHOOL_PREFIXES.match(name))


# --- 永住権・在留資格・ビザの自己申告 ---------------------------------------------
# 本人が自分の在留状況として書く言い回しに限定する。「在留資格」「特定技能」等の単語
# だけでは、人材業界の日本人（外国人採用支援の経験）を誤検知するため含めない。
_RESIDENCY_PHRASES = (
    "永住権を取得", "永住権取得", "永住権保有", "永住権あり", "永住権有", "永住者です", "永住ビザ",
    "在留資格：", "在留資格:", "在留資格は", "在留カード", "配偶者ビザ",
    "就労制限なし", "就労制限はなし", "就労制限は無", "就労制限無し",
    "ビザサポート不要", "ビザサポートは不要", "ビザ支援不要", "ビザ更新", "ビザの更新",
    "就労ビザを保有", "就労ビザ保有", "就労ビザ取得", "高度専門職ビザ", "高度専門職1号", "高度専門職2号",
    "高度人材ポイント",
    "permanent resident", "permanent residency", "no visa sponsorship",
    "visa sponsorship is not required", "visa sponsorship not required", "work visa holder",
    "spouse visa", "highly skilled professional visa",
)


def has_residency_status_mention(text: str | None) -> bool:
    """自己PR等に、本人の永住権・在留資格・ビザに関する申告があるか。"""
    low = (text or "").lower()
    if not low:
        return False
    return any(p.lower() in low for p in _RESIDENCY_PHRASES)
