"""返信自動同期（reply_sync.py）のテスト。FakeApi でネットワーク不要。"""

from __future__ import annotations

from datetime import datetime, timedelta

from bizreach_scout.analytics.reply_sync import sync_replies
from bizreach_scout.eligibility import check_eligibility
from bizreach_scout.models import GeneratedScout, ScoutContent
from bizreach_scout.storage.repository import Repository

from .factories import make_candidate


class FakeApi:
    """get_resume だけを持つ BizreachApi のスタブ。"""

    def __init__(self, resumes: dict[str, dict]):
        self.resumes = resumes
        self.calls: list[str] = []

    def get_resume(self, mrccid):
        self.calls.append(mrccid)
        return self.resumes.get(mrccid)


def _repo_with_sent(tmp_path, members: list[tuple[str, int]]) -> Repository:
    """members: (member_no, 送信何日前) のリスト。mrccid は M-{member_no}。"""
    repo = Repository(db_path=tmp_path / "t.db")
    now = datetime.now()
    for mno, days_ago in members:
        cand = make_candidate(member_no=mno, mrccid=f"M-{mno}")
        repo.upsert_candidate(cand, check_eligibility(cand))
        repo.record_generated(GeneratedScout(
            member_no=mno, first=ScoutContent(subject="s", body="b"),
            resend=ScoutContent(subject="s2", body="b2"), model="m"))
        sent = (now - timedelta(days=days_ago)).isoformat(timespec="seconds")
        repo.conn.execute(
            "UPDATE scouts SET status='sent', sent_at=? WHERE member_no=? AND kind='first'",
            (sent, mno))
        repo.conn.commit()
        repo._log_sent_event(mno, "first", "platinum", sent)
    return repo


def _resume(name=None, history=None) -> dict:
    r: dict = {"bizreachUserId": "BUX", "age": 30}
    if name is not None:
        r["candidateName"] = name
    if history is not None:
        r["contactHistory"] = history
    return r


def test_sync_replies_detects_and_records(tmp_path):
    repo = _repo_with_sent(tmp_path, [("BU1", 3), ("BU2", 5)])
    api = FakeApi({
        "M-BU1": _resume(name="山田 太郎"),   # 氏名開示 → 返信あり
        "M-BU2": _resume(),                     # 匿名のまま → 未返信
    })
    report = sync_replies(api, repo, max_checks=10, recent_days=45)
    assert report.checked == 2
    assert report.detected == 1
    row = repo.conn.execute("SELECT * FROM replies WHERE member_no='BU1'").fetchone()
    assert row["replied"] == 1
    assert row["detected_by"] == "auto"
    assert row["candidate_name"] == "山田 太郎"
    assert repo.conn.execute(
        "SELECT COUNT(*) AS n FROM replies WHERE member_no='BU2'").fetchone()["n"] == 0
    repo.close()


def test_sync_replies_respects_max_checks_oldest_first(tmp_path):
    repo = _repo_with_sent(tmp_path, [("BU_NEW", 1), ("BU_OLD", 30), ("BU_MID", 10)])
    api = FakeApi({f"M-{m}": _resume() for m in ("BU_NEW", "BU_OLD", "BU_MID")})
    report = sync_replies(api, repo, max_checks=2, recent_days=45)
    assert report.checked == 2
    assert api.calls == ["M-BU_OLD", "M-BU_MID"]  # 古い順に上限まで
    repo.close()


class FakeScanner:
    """InboxScanner のスタブ（DOM htmls と 捕捉API応答 responses を返す）。"""

    def __init__(self, htmls: list[str], detail_htmls: list[str] | None = None,
                 responses: list[tuple[str, str]] | None = None):
        self.htmls = htmls
        self.detail_htmls = detail_htmls or []
        self.responses = responses or []
        self.detail_called = 0
        self.base = "https://cr-support.jp"

    def fetch_pages(self, max_pages: int = 5, page_size: int = 50) -> list[str]:
        return self.htmls

    def fetch_detail_pages(self, list_htmls, max_details: int = 30) -> list[str]:
        self.detail_called += 1
        return self.detail_htmls

    def captured_text(self) -> str:
        return "\n".join(b for _u, b in self.responses)


def test_sync_replies_detects_by_subject_match(tmp_path):
    # 受信箱の返信行は「Re: <送信した件名>」で並び、返信者は実名表示のため
    # 会員番号は画面に出ない（2026-07-21 実画面で確認）。件名で突合できることを検証。
    repo = _repo_with_sent(tmp_path, [("BU1111111", 3), ("BU2222222", 5)])
    subject = "【Premium Offer】5店舗の立て直しとメンズ事業部全国1位のご実績に惹かれ限定オファーをさせていただきます"
    repo.conn.execute("UPDATE scouts SET subject=? WHERE member_no='BU1111111' AND kind='first'",
                      (subject,))
    repo.conn.commit()
    inbox_html = (f'<tr><td>高澤 拓也 / 株式会社リンク</td>'
                  f'<td>Re: {subject}</td><td>7月20日 19:36</td></tr>')
    scanner = FakeScanner([inbox_html])
    api = FakeApi({"M-BU1111111": _resume(), "M-BU2222222": _resume()})
    report = sync_replies(api, repo, max_checks=10, recent_days=45, scanner=scanner)
    assert report.inbox_detected == 1
    row = repo.conn.execute("SELECT * FROM replies WHERE member_no='BU1111111'").fetchone()
    assert row["replied"] == 1 and row["detected_by"] == "auto"
    assert "件名一致" in row["note"]
    # 実名しか出ない他の返信者（送信していない人）は誤検知しない。
    assert repo.conn.execute("SELECT COUNT(*) AS n FROM replies").fetchone()["n"] == 1
    repo.close()


def test_sync_replies_detects_from_captured_api_json(tmp_path):
    # メッセージ一覧はSPAのAPI(JSON)で届く: DOMは空でもAPI応答に mrccid があれば検知。
    repo = _repo_with_sent(tmp_path, [("BU1111111", 3), ("BU2222222", 5)])
    api_json = ('{"messages":[{"candidateId":"c1","mrccid":"M-BU1111111",'
                '"lastMessageFrom":"candidate"}]}')
    scanner = FakeScanner(
        ['<html ng-app="V4CRS"><body></body></html>'],   # 空のSPA殻
        responses=[("https://cr-support.jp/api/v1/messages/search", api_json)])
    api = FakeApi({"M-BU1111111": _resume(), "M-BU2222222": _resume()})
    report = sync_replies(api, repo, max_checks=10, recent_days=45, scanner=scanner)
    assert report.inbox_detected == 1
    # 1回目の照合（DOM＋API）で確定するため、詳細ページは開かない。
    assert scanner.detail_called == 0
    row = repo.conn.execute("SELECT * FROM replies WHERE member_no='BU1111111'").fetchone()
    assert row["replied"] == 1 and row["detected_by"] == "auto"
    repo.close()


def test_sync_replies_detects_from_inbox_scan(tmp_path):
    repo = _repo_with_sent(tmp_path, [("BU1111111", 3), ("BU2222222", 5)])
    # 受信箱に BU1111111 のメッセージがある（= 返信あり）。BU9999999 は送信していない他者。
    scanner = FakeScanner(['<tr><td>BU1111111</td><td>2026/07/19</td></tr>',
                           '<tr><td>BU9999999</td></tr>'])
    api = FakeApi({"M-BU1111111": _resume(), "M-BU2222222": _resume()})
    report = sync_replies(api, repo, max_checks=10, recent_days=45, scanner=scanner)
    assert report.inbox_pages == 2
    assert report.inbox_detected == 1
    row = repo.conn.execute("SELECT * FROM replies WHERE member_no='BU1111111'").fetchone()
    assert row["replied"] == 1 and row["detected_by"] == "auto"
    assert "受信箱" in row["note"]
    # 受信箱で返信ありになった候補者はレジュメ再確認の対象から外れる。
    assert "M-BU1111111" not in api.calls
    # 送信していない BU9999999 は記録されない。
    assert repo.conn.execute(
        "SELECT COUNT(*) AS n FROM replies WHERE member_no='BU9999999'").fetchone()["n"] == 0
    # 一覧でヒットしたので詳細ページまでは開かない。
    assert scanner.detail_called == 0
    repo.close()


def test_sync_replies_falls_back_to_detail_pages_when_list_has_no_ids(tmp_path):
    # 一覧行は氏名表示のみ（識別子なし・2026-07-21 実データで確認した画面仕様）。
    # 詳細ページに会員番号が出るケース: 詳細照合で検知できる。
    repo = _repo_with_sent(tmp_path, [("BU1111111", 3), ("BU2222222", 5)])
    list_html = ('<div class="msgRow"><a href="/message/detail?messageId=101">氏名A</a></div>'
                 '<div class="msgRow"><a href="/message/detail?messageId=102">氏名B</a></div>')
    detail_htmls = ['<div class="thread">会員番号: BU1111111 <p>返信本文</p></div>']
    scanner = FakeScanner([list_html], detail_htmls)
    api = FakeApi({"M-BU1111111": _resume(), "M-BU2222222": _resume()})
    report = sync_replies(api, repo, max_checks=10, recent_days=45, scanner=scanner)
    assert scanner.detail_called == 1
    assert report.inbox_details == 1
    assert report.inbox_detected == 1
    row = repo.conn.execute("SELECT * FROM replies WHERE member_no='BU1111111'").fetchone()
    assert row["replied"] == 1 and row["detected_by"] == "auto"
    repo.close()


def test_sync_replies_inbox_mrccid_match_and_idempotent(tmp_path):
    repo = _repo_with_sent(tmp_path, [("BU1111111", 3)])
    # 会員番号は画面に出ず、mrccid（M-BU1111111）だけがリンクに現れるケース。
    scanner = FakeScanner(['<a href="/candidates/M-BU1111111/detail">氏名</a>'])
    api = FakeApi({"M-BU1111111": _resume()})
    r1 = sync_replies(api, repo, max_checks=10, recent_days=45, scanner=scanner)
    assert r1.inbox_detected == 1
    # 2回目は既に返信済みのため新規検知0（冪等）。
    r2 = sync_replies(api, repo, max_checks=10, recent_days=45, scanner=scanner)
    assert r2.inbox_detected == 0
    repo.close()


def test_sync_replies_inbox_failure_does_not_block_resume_checks(tmp_path):
    repo = _repo_with_sent(tmp_path, [("BU1", 3)])

    class BoomScanner:
        def fetch_pages(self, max_pages=5, page_size=50):
            raise RuntimeError("navigation failed")

    api = FakeApi({"M-BU1": _resume(name="山田 太郎")})
    report = sync_replies(api, repo, max_checks=10, recent_days=45, scanner=BoomScanner())
    assert report.errors == 1
    assert report.detected == 1  # レジュメ側の検知は動く
    repo.close()


def test_sync_replies_rotates_targets_across_runs(tmp_path):
    # 送信4名・1回の上限2名 → 1回目は古い2名、2回目は残りの2名（毎回同じ2名を
    # 見続けて新しい送信の返信を永遠に見逃す、という以前のバグの回帰テスト）。
    repo = _repo_with_sent(
        tmp_path, [("BU_A", 40), ("BU_B", 30), ("BU_C", 20), ("BU_D", 10)])
    api = FakeApi({f"M-{m}": _resume() for m in ("BU_A", "BU_B", "BU_C", "BU_D")})
    sync_replies(api, repo, max_checks=2, recent_days=45)
    assert api.calls == ["M-BU_A", "M-BU_B"]
    sync_replies(api, repo, max_checks=2, recent_days=45)
    assert api.calls[2:] == ["M-BU_C", "M-BU_D"]
    # 3回目は一巡して最初にチェックした2名へ戻る。
    sync_replies(api, repo, max_checks=2, recent_days=45)
    assert api.calls[4:] == ["M-BU_A", "M-BU_B"]
    repo.close()


def test_sync_replies_skips_already_replied_and_survives_errors(tmp_path):
    repo = _repo_with_sent(tmp_path, [("BU1", 3), ("BU2", 4)])
    repo.upsert_reply("BU1", replied=True, replied_at=None, detected_by="manual")

    class BoomApi:
        def __init__(self):
            self.calls: list[str] = []

        def get_resume(self, mrccid):
            self.calls.append(mrccid)
            raise RuntimeError("network")

    api = BoomApi()
    report = sync_replies(api, repo, max_checks=10, recent_days=45)
    # BU1 は返信済みなので確認せず、BU2 のみ（エラーでも例外を上げない）。
    assert api.calls == ["M-BU2"]
    assert report.errors == 1 and report.checked == 0
    repo.close()
