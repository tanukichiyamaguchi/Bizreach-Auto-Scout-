"""外国人判定ヒューリスティック（foreign.py）の単体テスト。"""

from __future__ import annotations

from bizreach_scout.foreign import (
    has_country_tag,
    has_foreign_native_language,
    has_japanese_script,
    has_residency_status_mention,
    is_english_dominant,
    is_japanese_language_school,
    is_overseas_school_name,
    japanese_char_ratio,
    japanese_listed_as_non_native,
)

# --- has_japanese_script ----------------------------------------------------

def test_has_japanese_script_detects_kana_and_kanji():
    assert has_japanese_script("早稲田大学")       # 漢字
    assert has_japanese_script("スタンフォード")     # カタカナ
    assert has_japanese_script("えいご")            # ひらがな
    assert not has_japanese_script("Stanford University")
    assert not has_japanese_script("")
    assert not has_japanese_script(None)


# --- japanese_char_ratio ----------------------------------------------------

def test_japanese_char_ratio_ignores_digits_and_symbols():
    # 数字・記号・空白は分母に含めない。
    assert japanese_char_ratio("営業 123 !!!") == 1.0        # 日本語のみ
    assert japanese_char_ratio("sales 123 !!!") == 0.0       # ラテンのみ
    assert japanese_char_ratio("") == 1.0                     # 文字なし＝日本語扱い（安全側）
    r = japanese_char_ratio("営業sales")                      # 2文字ずつ→漢字2/(2+5)
    assert 0.0 < r < 1.0


# --- is_overseas_school_name（① 海外の大学）---------------------------------

def test_overseas_when_ja_empty_and_en_present():
    assert is_overseas_school_name(None, "Stanford University")
    assert is_overseas_school_name("", "Harvard Business School")


def test_overseas_when_ja_is_latin_only():
    # ja 欄にラテン文字だけが入っているケース（例: 英語で記入された学校名）。
    assert is_overseas_school_name("Stanford University", None)


def test_domestic_when_ja_has_kanji():
    assert not is_overseas_school_name("早稲田大学", "Waseda University")
    assert not is_overseas_school_name("慶應義塾大学", None)


def test_overseas_when_ja_is_katakana_university():
    # カタカナ表記の海外大学も海外と判定する（核がカタカナのみ）。
    assert is_overseas_school_name("スタンフォード大学", "Stanford University")
    assert is_overseas_school_name("ハーバード大学", None)
    assert is_overseas_school_name("オックスフォード大学", None)
    assert is_overseas_school_name("ソウル大学校", None)


def test_overseas_katakana_university_with_department_suffix():
    # 学部・専攻が後ろに続いても核（大学の手前）で判定する（運用者提示の実例: カラチ大学）。
    assert is_overseas_school_name("カラチ大学 数学/統計/コンピュータサイエンス", None)
    assert is_overseas_school_name("コベントリー大学 ビジネススクール／ビジネス・マーケティング", None)
    assert is_overseas_school_name("カリフォルニア州立大学 ロングビーチ校", None)  # 州立は核から除く
    assert is_overseas_school_name("マサチューセッツ工科大学", None)
    assert not is_overseas_school_name("早稲田大学 商学部", None)
    assert not is_overseas_school_name("東京工科大学 メディア学部", None)
    assert not is_overseas_school_name("ノースアジア大学 経済学部", None)  # 秋田の国内大学


def test_domestic_katakana_university_not_flagged():
    # 核に漢字を持つ国内校（ルーテル学院・立命館アジア太平洋 等）は海外にしない。
    assert not is_overseas_school_name("立命館アジア太平洋大学", None)
    assert not is_overseas_school_name("ルーテル学院大学", None)
    # カタカナ名の国内大学は許可リストで除外する。
    assert not is_overseas_school_name("サイバー大学", None)
    assert not is_overseas_school_name("デジタルハリウッド大学", None)


def test_overseas_false_when_both_empty():
    assert not is_overseas_school_name("", "")
    assert not is_overseas_school_name(None, None)


# --- has_foreign_native_language（② 外国語ネイティブ）------------------------

def test_native_english_japanese_notation():
    assert has_foreign_native_language("語学: 英語（ネイティブ）、日本語（日常会話）")


def test_native_english_english_notation():
    assert has_foreign_native_language("Native English speaker; business level Japanese")


def test_native_various_foreign_languages():
    assert has_foreign_native_language("母国語：中国語")
    assert has_foreign_native_language("フランス語 ネイティブ")
    assert has_foreign_native_language("韓国語はネイティブレベルです")


def test_bilingual_native_is_flagged_aggressively():
    # 「日本語・英語ともにネイティブ」は外国語ネイティブとして対象外にする（要件どおり）。
    assert has_foreign_native_language("日本語・英語ともにネイティブ")


def test_japanese_native_with_foreign_non_native_not_flagged():
    # 日本語ネイティブ＋外国語は非ネイティブ → 対象（誤検出しない）。
    assert not has_foreign_native_language("日本語：ネイティブ / 英語：ビジネスレベル")
    assert not has_foreign_native_language("日本語ネイティブ、英語はTOEIC900")
    assert not has_foreign_native_language("英語：日常会話レベル")
    # 「日本語：ネイティブ、英語：ビジネス」はネイティブが日本語に紐づく（英語は非ネイティブ）。
    # 日本語履歴に非常に多い記載パターンなので、ここを誤検出すると大量の日本人を除外してしまう。
    assert not has_foreign_native_language("日本語：ネイティブ、英語：ビジネス")
    assert not has_foreign_native_language("母語は日本語です")


def test_business_and_conversational_level_not_flagged():
    # 外国語のビジネス／日常会話レベルは除外しない（ネイティブのみ除外）。
    assert not has_foreign_native_language("英語：ビジネスレベル")
    assert not has_foreign_native_language("英語：日常会話レベル、中国語：ビジネス")
    assert not has_foreign_native_language("英語（ビジネス）、フランス語（初級）")
    # ネイティブレベル／ネイティブスピーカーは除外する（あくまでネイティブ）。
    assert has_foreign_native_language("英語（ネイティブレベル）")
    assert has_foreign_native_language("中国語ネイティブスピーカー")


def test_no_language_mention_not_flagged():
    assert not has_foreign_native_language("法人営業として新規開拓を担当。")
    assert not has_foreign_native_language("")


# --- is_english_dominant（③ 職務要約・職歴が英語）---------------------------

def test_english_resume_flagged():
    text = (
        "Experienced sales manager with over ten years in enterprise software, "
        "leading teams and closing large deals across the APAC region."
    )
    assert is_english_dominant(text)


def test_japanese_resume_not_flagged():
    text = "法人営業として新規開拓から大型提案まで一貫して担当。チーム6名のマネジメント経験あり。"
    assert not is_english_dominant(text)


def test_bilingual_japanese_not_flagged():
    # 日本語主体＋一部英語（バイリンガル日本人）は日本語割合が高いので対象外にしない。
    text = ("グローバル法人営業を担当。English business communication と海外顧客対応の経験あり。"
            "新規開拓で全社表彰。")
    assert not is_english_dominant(text)


def test_short_english_not_flagged():
    # 英語が少量（役職名程度）では英語優勢と見なさない（誤検出防止）。
    assert not is_english_dominant("Sales Manager")
    assert not is_english_dominant("")


# --- japanese_listed_as_non_native（語学欄で日本語が非ネイティブ）---------------

def test_japanese_business_level_in_language_field_is_non_native():
    # 運用者提示の実例3名はいずれも「日本語 / ビジネス会話レベル」を申告していた。
    assert japanese_listed_as_non_native("英語：ネイティブレベル、ヒンディー語：ネイティブレベル、日本語：ビジネス会話レベル")
    assert japanese_listed_as_non_native("英語 / 日常会話レベル\n日本語 / ビジネス会話レベル\n北京語 / ネイティブレベル")
    assert japanese_listed_as_non_native("日本語：日常会話レベル")
    assert japanese_listed_as_non_native("Japanese: Business level, English: Native")
    assert japanese_listed_as_non_native("日本語能力試験N1")  # 級・N1 もレベル表記


def test_japanese_native_or_unlisted_is_not_flagged():
    # 日本人は日本語を書かないか「ネイティブ」と書く。どちらも判定しない。
    assert not japanese_listed_as_non_native("英語：ビジネス会話レベル")
    assert not japanese_listed_as_non_native("日本語：ネイティブ / 英語：ビジネスレベル")
    assert not japanese_listed_as_non_native("日本語（母語）・英語（ビジネス会話レベル）")
    assert not japanese_listed_as_non_native("英語：ビジネス、日本語：ネイティブレベル")
    assert not japanese_listed_as_non_native("Japanese (Native), English (Business)")
    assert not japanese_listed_as_non_native("")
    assert not japanese_listed_as_non_native(None)


def test_japanese_without_level_word_is_not_flagged():
    # レベル語が無い列挙や自由文（「日本語のほか英語も可」）は判定しない（誤検知防止）。
    assert not japanese_listed_as_non_native("日本語 / 英語")
    assert not japanese_listed_as_non_native("日本語のほか英語も可")
    assert not japanese_listed_as_non_native("日本語教師資格あり、英語ビジネスレベル")


def test_japanese_level_stops_at_next_entry():
    # 「日本語」の直後のエントリ区切り／次の言語名までをレベル表記とみなす。
    # 次エントリの「ビジネス」を日本語のレベルとして拾わない。
    assert not japanese_listed_as_non_native("日本語、英語：ビジネスレベル")
    assert not japanese_listed_as_non_native("日本語 / 英語：ビジネスレベル")


# --- has_country_tag（校名の国名タグ）------------------------------------------

def test_country_tag_in_parentheses():
    assert has_country_tag("北京市第一六一中学（中国）")
    assert has_country_tag("北京物資学院（中国） 会計学科 資産評価専攻")
    assert has_country_tag("Peking University (China)")
    assert has_country_tag("ソウル大学校(韓国)")
    assert has_country_tag("ABC High School（アメリカ）")


def test_country_tag_leading_country_name():
    assert has_country_tag("イギリス コベントリー大学 ビジネススクール")
    assert has_country_tag("イギリス　コベントリー大学")  # 全角空白
    assert has_country_tag("中国・北京大学")
    assert has_country_tag("USA / Lincoln High School")


def test_country_tag_not_confused_with_school_names_containing_country_words():
    assert not has_country_tag("中国学園大学")          # 岡山の国内大学
    assert not has_country_tag("中国短期大学")
    assert not has_country_tag("東京大学（中国語学科）")  # 括弧内が国名そのものではない
    assert not has_country_tag("早稲田大学 国際教養学部")
    assert not has_country_tag("")
    assert not has_country_tag(None)


# --- is_japanese_language_school（留学生向け日本語学校）------------------------

def test_japanese_language_school_names():
    assert is_japanese_language_school("A.C.C.国際交流学園 日本語学校")
    assert is_japanese_language_school("東京日本語学校")
    assert is_japanese_language_school("ISIキャリア外語アカデミー高田馬場校")
    assert is_japanese_language_school("ISIランゲージスクール")
    assert is_japanese_language_school("東京外国語大学 留学生日本語教育センター")
    assert is_japanese_language_school("Tokyo Japanese Language School")


def test_japanese_native_schools_not_flagged_as_language_school():
    # 日本語教育（教師養成）・日本語学の専攻、ISI で始まる別名は日本語学校扱いにしない。
    assert not is_japanese_language_school("早稲田大学大学院 日本語教育研究科")
    assert not is_japanese_language_school("早稲田大学 日本語日本文学科")
    assert not is_japanese_language_school("ISIS大学")
    assert not is_japanese_language_school("神田外語大学")
    assert not is_japanese_language_school("")
    assert not is_japanese_language_school(None)


# --- has_residency_status_mention（永住権・在留資格・ビザの自己申告）---------------

def test_residency_status_self_report_detected():
    assert has_residency_status_mention("永住権取得済みのため就労制限はありません。")
    assert has_residency_status_mention("在留資格：技術・人文知識・国際業務（2028年まで）")
    assert has_residency_status_mention("ビザサポート不要です。")
    assert has_residency_status_mention("I am a permanent resident of Japan; no visa sponsorship needed.")


def test_residency_words_about_others_not_flagged():
    # 人材業界の日本人が外国人採用支援の経験として書く語（在留資格・特定技能・就労ビザ）は
    # 本人の在留状況ではないため拾わない。
    assert not has_residency_status_mention("外国人材の在留資格申請サポートと特定技能の受け入れ支援を担当。")
    assert not has_residency_status_mention("外国籍社員の就労ビザ申請を人事として支援。")
    assert not has_residency_status_mention("")
    assert not has_residency_status_mention(None)
