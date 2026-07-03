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
