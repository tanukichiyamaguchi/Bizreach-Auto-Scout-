"""取り込み時の「判定済み読み飛ばし」のテスト。

検索一覧が返すのは mrccid だけで、会員番号はレジュメを取得して初めて分かる。
mrccid が未知の送信済み候補者（復元した送信記録など）を取り込み枠から
除外できないと、毎回同じ人を取り直して新しい候補者へ到達できなくなる。
"""

from __future__ import annotations

import pytest

from bizreach_scout.ingest.bizreach_api_source import BizreachApiSource
from bizreach_scout.models import Candidate


class _FakeClient:
    def human_delay(self, *_args, **_kwargs) -> None:
        return None


class _FakeApi:
    """mrccid → 会員番号 が 1:1 対応する検索結果を模した API。"""

    base = "https://example.invalid"

    def __init__(self, member_nos: list[str]):
        self._members = member_nos
        self.fetched: list[str] = []

    def iter_candidate_ids(self, _search_url, max_candidates=50,
                           skip_mrccids=None, max_pages=20):
        skip = skip_mrccids or set()
        yielded = 0
        for m in self._members:
            if yielded >= max_candidates:
                return
            mrccid = f"MR-{m}"
            if mrccid in skip:
                continue
            yielded += 1
            yield mrccid

    def get_candidate(self, mrccid: str) -> Candidate:
        self.fetched.append(mrccid)
        member_no = mrccid.removeprefix("MR-")
        return Candidate(member_no=member_no, headline="テスト")


@pytest.fixture
def _patch_api(monkeypatch):
    def _apply(api):
        import bizreach_scout.bizreach.api as api_mod

        monkeypatch.setattr(api_mod, "BizreachApi", lambda _client: api)
    return _apply


def test_settled_members_do_not_consume_the_ingest_budget(_patch_api):
    """送信済みが並んでいても、要求数ぶんの新しい候補者を取り込む。

    これが効かないと、復元した送信済みの人で取り込み枠が埋まり送信0件になる。
    """
    settled = [f"BU{i:07d}" for i in range(1, 13)]     # 先頭12名は送信済み
    fresh = [f"BU9{i:06d}" for i in range(1, 6)]       # そのあとに新規5名
    api = _FakeApi(settled + fresh)
    _patch_api(api)

    source = BizreachApiSource(
        "https://example.invalid/search?rrsc=1", max_candidates=3,
        client=_FakeClient(), skip_mrccids=set(),
        settled_member_nos=set(settled),
    )
    got = [c.member_no for c in source.iter_candidates()]

    assert got == fresh[:3]                 # 新規だけを要求数ぶん取り込む
    assert len(api.fetched) == 15           # 送信済み12 + 新規3


def test_resolved_mrccids_are_remembered_even_when_skipped(_patch_api):
    """読み飛ばした候補者の mrccid も覚える（次回は一覧の時点で弾ける）。"""
    api = _FakeApi(["BU1111111", "BU2222222"])
    _patch_api(api)
    remembered: list[tuple[str, str]] = []

    source = BizreachApiSource(
        "https://example.invalid/search?rrsc=1", max_candidates=5,
        client=_FakeClient(), skip_mrccids=set(),
        settled_member_nos={"BU1111111"},
        on_resolved=lambda m, mid: remembered.append((m, mid)),
    )
    got = [c.member_no for c in source.iter_candidates()]

    assert got == ["BU2222222"]
    assert ("BU1111111", "MR-BU1111111") in remembered   # 読み飛ばした分も覚える
    assert ("BU2222222", "MR-BU2222222") in remembered


def test_fetch_is_bounded_when_everything_is_settled(_patch_api):
    """全員が判定済みでもレジュメ取得は上限で打ち切る（暴走防止）。"""
    members = [f"BU{i:07d}" for i in range(1, 101)]
    api = _FakeApi(members)
    _patch_api(api)

    source = BizreachApiSource(
        "https://example.invalid/search?rrsc=1", max_candidates=4,
        client=_FakeClient(), skip_mrccids=set(),
        settled_member_nos=set(members), settled_fetch_multiplier=5,
    )
    got = list(source.iter_candidates())

    assert got == []
    assert len(api.fetched) == 20          # 4 × 5 で打ち切る


def test_known_mrccids_are_filtered_before_fetching(_patch_api):
    """mrccid が分かっている判定済みはレジュメを取得せずに弾く。"""
    api = _FakeApi(["BU1111111", "BU2222222"])
    _patch_api(api)

    source = BizreachApiSource(
        "https://example.invalid/search?rrsc=1", max_candidates=5,
        client=_FakeClient(), skip_mrccids={"MR-BU1111111"},
    )
    got = [c.member_no for c in source.iter_candidates()]

    assert got == ["BU2222222"]
    assert api.fetched == ["MR-BU2222222"]   # 取得は1件だけ（通信を節約）
