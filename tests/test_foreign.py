"""外国人判定ヒューリスティック（foreign.py）の単体テスト。"""

from __future__ import annotations

from bizreach_scout.foreign import (
    has_foreign_native_language,
    has_japanese_script,
    is_english_dominant,
    is_overseas_school_name,
    japanese_char_ratio,
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
