"""BizreachPickupSource のDOM抽出ロジック（resume-id収集・mrccid解決）のテスト。"""

from __future__ import annotations

from types import SimpleNamespace

from bizreach_scout.ingest.bizreach_pickup_source import _MRCCID_RE, BizreachPickupSource


class FakeEl:
    def __init__(self, attrs):
        self.attrs = attrs

    def get_attribute(self, k):
        return self.attrs.get(k)

    def click(self, timeout=None):
        pass

    def wait_for(self, state=None, timeout=None):
        pass


class FakeLoc:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, i):
        return FakeEl(self.items[i])

    @property
    def first(self):
        return FakeEl(self.items[0] if self.items else {})

    def get_attribute(self, k):
        return self.items[0].get(k) if self.items else None

    def click(self, timeout=None):
        pass

    def wait_for(self, state=None, timeout=None):
        pass


class FakePage:
    def __init__(self, freescout_rows, copy_text=""):
        self.freescout_rows = freescout_rows
        self.copy_text = copy_text
        self.keyboard = SimpleNamespace(press=lambda k: None)

    def locator(self, sel):
        if "jsi_lap_url_copy" in sel:
            return FakeLoc([{"data-clipboard-text": self.copy_text}])
        if "a.freescout" in sel:
            return FakeLoc(self.freescout_rows)
        return FakeLoc([])  # close-lightbox 用は空


def test_mrccid_regex():
    url = "https://cr-support.jp/scout/highclass/search/?mrccid=8Ly-3zUZKgLS2Ye9_NcDmw"
    assert _MRCCID_RE.search(url).group(1) == "8Ly-3zUZKgLS2Ye9_NcDmw"


def test_collect_resume_ids_skips_scouted_and_dedupes():
    rows = [
        {"data-resume-id": "111", "data-scount-status": "ALREADY_READ"},
        {"data-resume-id": "222", "data-scount-status": "SCOUTED"},      # 除外
        {"data-resume-id": "333", "data-scount-status": ""},
        {"data-resume-id": "111", "data-scount-status": "ALREADY_READ"},  # 重複除去
    ]
    src = BizreachPickupSource(kind="job")
    ids = src._collect_resume_ids(FakePage(rows))
    assert ids == ["111", "333"]


def test_resolve_mrccid_from_lightbox():
    src = BizreachPickupSource(kind="job")
    page = FakePage(
        [{"data-resume-id": "2186347", "data-scount-status": "ALREADY_READ"}],
        copy_text="https://cr-support.jp/scout/highclass/search/?mrccid=ABC_def-123",
    )
    assert src._resolve_mrccid(page, "2186347") == "ABC_def-123"


def test_resolve_mrccid_none_when_no_copy_url():
    src = BizreachPickupSource(kind="job")
    page = FakePage([{"data-resume-id": "1", "data-scount-status": ""}], copy_text="")
    assert src._resolve_mrccid(page, "1") is None


def test_default_kind_is_pickup_job():
    assert BizreachPickupSource()._prefixes() == ["pick-up-job"]
    assert BizreachPickupSource(kind="both")._prefixes() == ["pick-up-job", "pick-up-candidate"]


class _ClosePage:
    """閉じるコントロールのクリックと mypage 再遷移を記録するページのフェイク。"""

    def __init__(self, drawer_stays_open: bool):
        self.drawer_stays_open = drawer_stays_open
        self.clicked: list[str] = []
        self.goto_urls: list[str] = []
        self.keyboard = SimpleNamespace(press=lambda k: self.clicked.append(f"key:{k}"))

    def locator(self, sel):
        page = self

        class _L:
            def count(self_inner):
                if sel == "#jsi_lapPageWrapper.showLapPage":
                    return 1 if page.drawer_stays_open else 0
                # 閉じるボタンは常に存在する想定。
                return 1

            @property
            def first(self_inner):
                return self_inner

            def click(self_inner, timeout=None):
                page.clicked.append(sel)

            def wait_for(self_inner, state=None, timeout=None):
                pass

        return _L()

    def goto(self, url, wait_until=None):
        self.goto_urls.append(url)

    def wait_for_load_state(self, state=None, timeout=None):
        pass


def test_close_lightbox_clicks_drawer_close_button():
    src = BizreachPickupSource(kind="job")
    src._mypage_url = "https://cr-support.jp/mypage/"
    page = _ClosePage(drawer_stays_open=False)
    src._close_lightbox(page)
    # 実DOMで確認済みのドロワー右上×を最優先でクリックする。
    assert page.clicked[0] == "#jsi_btnClose"
    assert page.goto_urls == []  # 閉じられたので再遷移不要


def test_close_lightbox_renavigates_when_drawer_persists():
    src = BizreachPickupSource(kind="job")
    src._mypage_url = "https://cr-support.jp/mypage/"
    page = _ClosePage(drawer_stays_open=True)
    src._close_lightbox(page)
    # ドロワーが残る場合は mypage へ再遷移して確実に解消する。
    assert page.goto_urls == ["https://cr-support.jp/mypage/"]
