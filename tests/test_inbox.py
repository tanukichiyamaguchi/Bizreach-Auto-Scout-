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


def test_extract_message_links_filters_and_absolutizes():
    from bizreach_scout.bizreach.inbox import extract_message_links

    html = (
        '<a href="/message/detail?messageId=101">A</a>'
        '<a href="/message/detail?messageId=101">A再掲</a>'
        '<a href="/message/?pageSize=20&folderCd=inbox&currentPageNo=2">次へ</a>'
        '<a href="https://cr-support.jp/message/detail?messageId=102">B</a>'
        '<a href="https://evil.example.com/message/detail?messageId=9">外部</a>'
        '<a href="/css/message.css">css</a>'
    )
    links = extract_message_links(html, "https://cr-support.jp")
    assert links == [
        "https://cr-support.jp/message/detail?messageId=101",
        "https://cr-support.jp/message/detail?messageId=102",
    ]
    assert extract_message_links("", "https://cr-support.jp") == []


def test_api_index_lists_endpoints_and_redacts_values():
    from bizreach_scout.bizreach.inbox import api_index

    responses = [
        ("https://cr-support.jp/api/v1/messages/search",
         '{"messages":[{"mrccid":"M-BU1","candidateName":"山田 太郎","unread":true}]}'),
        ("https://cr-support.jp/api/v2/taskList", '{"count":3}'),
    ]
    out = api_index(responses)
    assert "messages/search" in out and "taskList" in out
    # 構造サンプルは列挙値/フラグを残し、氏名は伏せる。
    assert "mrccid" in out and "unread" in out
    assert "山田" not in out


def test_extract_dom_signals_picks_thread_attrs_not_chrome():
    from bizreach_scout.bizreach.inbox import extract_dom_signals

    html = (
        '<tr ng-click="openThread(12345)"><td>氏名A</td></tr>'
        '<a href="/resume/wV9gfdXhxbDLyqVcHQ/detail">履歴書</a>'
        '<a data-url="/candidate/detail?mrccid=abc123def456ghi789">見る</a>'
        '<a ng-click="logout()">ログアウト</a>'   # 無関係 → 拾わない
    )
    sigs = extract_dom_signals(html)
    assert any("openThread" in s for s in sigs)
    assert any("/resume/" in s for s in sigs)
    assert any("mrccid=" in s for s in sigs)
    assert not any("logout" in s for s in sigs)


def test_extract_id_tokens_finds_mrccid_like_tokens():
    from bizreach_scout.bizreach.inbox import extract_id_tokens

    html = '<a href="/resume/wV9gfdXhxbDLyqVcHQJvXQ">x</a> short abc def'
    toks = extract_id_tokens(html)
    assert "wV9gfdXhxbDLyqVcHQJvXQ" in toks
    assert "short" not in toks  # 18文字未満は拾わない


def test_api_index_excludes_static_assets():
    from bizreach_scout.bizreach.inbox import api_index

    responses = [
        ("https://cr-support.jp/dwr/engine.js?v=1", "x" * 50000),   # ライブラリ → 除外
        ("https://cr-support.jp/css/general.css?v=1", "y" * 40000),  # → 除外
        ("https://cr-support.jp/dwr/call/plaincall/crsAjaxMessage.list.dwr",
         'var s0={"mrccid":"M-BU1"};'),                              # データ応答 → 残す
    ]
    out = api_index(responses)
    assert "crsAjaxMessage.list.dwr" in out
    assert "engine.js" not in out and "general.css" not in out
    # 非JSON(DWR)応答は英数字トークンで様子が見える。
    assert "mrccid" in out


def test_body_shape_strips_head_script_style():
    from bizreach_scout.bizreach.inbox import body_shape

    html = ("<html><head><title>メッセージ</title><script>var x=1;</script></head>"
            "<body><style>.a{}</style><div class='row'>山田 太郎 BU3765516</div></body></html>")
    shaped = body_shape(html)
    assert "title" not in shaped and "var x=1" not in shaped and ".a{}" not in shaped
    assert "BU3765516" in shaped and "class='row'" in shaped
    assert "山田" not in shaped


def test_ascii_shape_hides_japanese_keeps_structure():
    shaped = ascii_shape('<td class="name">山田 太郎</td><td>BU3765516 2026/07/19</td>')
    assert "山田" not in shaped
    assert 'class="name"' in shaped
    assert "BU3765516" in shaped and "2026/07/19" in shaped
    # 非ASCIIは長さ表現に置換される（"山田"(2) + 全角スペース等を含む連なり）。
    assert "(" in shaped
