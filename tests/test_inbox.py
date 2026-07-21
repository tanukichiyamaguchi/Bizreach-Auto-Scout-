"""受信箱スキャン（inbox.py 純関数）のテスト。ブラウザ不要。"""

from __future__ import annotations

from bizreach_scout.bizreach.inbox import (
    ascii_shape,
    extract_member_nos,
    find_sent_in_html,
)

_HTML = """
<table class="messageList">
  <tr><td><a href="/candidate?id=BU3765516">候補者A</a></td><td>2026/07/19</td></tr>
  <tr><td><a href="/candidate?id=BU03803587">候補者B</a></td><td>2026/07/18</td></tr>
  <tr><td>ビズリーチ事務局</td><td>2026/07/17</td></tr>
  <tr><td><a href="/candidate?id=BU3765516">候補者A（同一人物の2通目）</a></td></tr>
</table>
"""


def test_extract_member_nos_dedupes_and_keeps_order():
    assert extract_member_nos(_HTML) == ["BU3765516", "BU03803587"]
    assert extract_member_nos("") == []
    assert extract_member_nos("BU123") == []  # 6桁未満は会員番号ではない


def test_find_sent_in_html_matches_member_no_or_mrccid():
    pairs = [
        ("BU3765516", "mrccid-aaaa-1111"),   # 会員番号でヒット
        ("BU9999999", "candid-xyz-99887766"),  # HTMLに無い → ヒットしない
        ("BU7777777", ""),                     # mrccid 不明・番号もHTMLに無い
    ]
    assert find_sent_in_html(_HTML, pairs) == {"BU3765516"}
    # mrccid 側でのヒット（会員番号が画面に出ない場合の保険）。
    html2 = '<a href="/api/v2/candidates/candid-xyz-99887766/detail">名前</a>'
    assert find_sent_in_html(html2, pairs) == {"BU9999999"}


def test_find_sent_in_html_ignores_short_mrccid_and_empty():
    # 短い mrccid は偶然一致の恐れがあるため照合しない。
    assert find_sent_in_html("abc123", [("BU1", "abc123")]) == set()
    assert find_sent_in_html("", [("BU1", "mrccid-long-enough")]) == set()


def test_ascii_shape_hides_japanese_keeps_structure():
    shaped = ascii_shape('<td class="name">山田 太郎</td><td>BU3765516 2026/07/19</td>')
    assert "山田" not in shaped
    assert 'class="name"' in shaped
    assert "BU3765516" in shaped and "2026/07/19" in shaped
    # 非ASCIIは長さ表現に置換される（"山田"(2) + 全角スペース等を含む連なり）。
    assert "(" in shaped
