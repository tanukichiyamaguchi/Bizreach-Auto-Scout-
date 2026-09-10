"""ビズリーチの内部APIから候補者を取り込む CandidateSource。

保存検索(rrsc)→候補者一覧→各レジュメ の順にAPIを呼び、Candidate を生成する。
DOMスクレイピングより堅牢。Playwright とログイン済みセッションが必要。
"""

from __future__ import annotations

from collections.abc import Iterator

from ..logging_config import logger
from ..models import Candidate
from .base import CandidateSource


class BizreachApiSource(CandidateSource):
    def __init__(
        self,
        search_url: str | None,
        max_candidates: int = 50,
        headless: bool = True,
        client=None,
        skip_mrccids: set[str] | None = None,
        max_pages: int | None = None,
        settled_member_nos: set[str] | None = None,
        on_resolved=None,
        settled_fetch_multiplier: int = 5,
    ):
        self.search_url = search_url
        self.max_candidates = max_candidates
        self.headless = headless
        self._client = client  # 既存クライアントを共有可能
        # 取り込み対象から外す候補者（直近で評価済み）。呼び出し側と共有する集合を
        # 受け取り、取り込んだ候補者をここへ追記していく。複数の保存検索を続けて
        # 処理するとき、同じ候補者を2度取り込まないためにこの共有が必要。
        self.skip_mrccids = skip_mrccids
        self.max_pages = max_pages
        # 会員番号で見た「判定済み」集合。mrccid が未知の送信済み候補者
        # （復元した送信記録など）は skip_mrccids では弾けないため、レジュメを
        # 取得してから会員番号で弾く。弾いた分は取り込み枠を消費しない。
        self.settled_member_nos = settled_member_nos or set()
        # (member_no, mrccid) を覚えるコールバック。次回以降は mrccid で弾ける。
        self.on_resolved = on_resolved
        # 判定済みを読み飛ばすためにレジュメを取得してよい倍率（暴走防止）。
        self.settled_fetch_multiplier = max(1, settled_fetch_multiplier)

    def iter_candidates(self) -> Iterator[Candidate]:
        from ..bizreach.api import BizreachApi
        from ..bizreach.client import BizreachClient

        owns = self._client is None
        client = self._client or BizreachClient(headless=self.headless)
        if owns:
            client.start()
            client.ensure_logged_in()
        try:
            if not self.search_url:
                logger.info("search_url 未指定のため候補者取り込みをスキップします。")
                return
            api = BizreachApi(client)
            kwargs: dict = {}
            if self.skip_mrccids is not None:
                kwargs["skip_mrccids"] = self.skip_mrccids
            if self.max_pages is not None:
                kwargs["max_pages"] = self.max_pages
            # 判定済みを弾いた分だけ奥へ進めるよう、必要数より多めに引く。
            # 生成器なので、必要数が揃えば残りのページは取得しない。
            fetch_cap = self.max_candidates * self.settled_fetch_multiplier
            yielded = 0
            fetched = 0
            settled_skipped = 0
            for mrccid in api.iter_candidate_ids(self.search_url, fetch_cap, **kwargs):
                if yielded >= self.max_candidates:
                    break
                if self.skip_mrccids is not None:
                    # 次の保存検索で同じ候補者を取り込まないよう、共有集合へ追記する。
                    self.skip_mrccids.add(mrccid)
                cand = api.get_candidate(mrccid)
                fetched += 1
                client.human_delay(0.5, 1.5)
                if not cand:
                    continue
                # 会員番号が分かった時点で対応表へ覚える（対象外でも覚える）。
                if self.on_resolved is not None and cand.member_no:
                    self.on_resolved(cand.member_no, mrccid)
                if cand.member_no in self.settled_member_nos:
                    # 送信済み・直近で対象外と判定済み。取り込み枠は消費しない。
                    settled_skipped += 1
                    continue
                cand.profile_url = f"{api.base}/scout/highclass/resume/{mrccid}"
                yielded += 1
                yield cand
            logger.info(
                "APIから %d 件のレジュメを取得し、判定済み %d 件を読み飛ばして"
                "%d 件を取り込みました（要求 %d 件）。",
                fetched, settled_skipped, yielded, self.max_candidates,
            )
            if yielded < self.max_candidates:
                logger.warning(
                    "取り込みが要求数に届きませんでした（%d/%d）。検索結果を"
                    "使い切ったか、レジュメ取得上限(%d件)に達しています。",
                    yielded, self.max_candidates, fetch_cap,
                )
        finally:
            if owns:
                client.close()
