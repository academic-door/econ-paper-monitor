"""Management World official publisher can return HTTP 200 *validation* instead of a TOC.

This is a source block, never an empty official roster or a successful transport.
The parser must not burn its 55s budget scanning dozens of identical challenges.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_cn_journals as cn


GLSJ = "journal-379b4022ce"
JOURNAL = {"id": GLSJ, "title": "管理世界", "fields": []}
CHALLENGE = '<html><link href="WebPublication/css/login.css"><a href="showValidateCode.aspx">verify</a></html>'
EMPTY = "暂无内容"


class FakeResponse:
    def __init__(self, body: str):
        self.body = body

    def read(self):
        return self.body.encode("utf-8")


class FakeOpener:
    def __init__(self, first_page: str, issue_page: str):
        self.first_page = first_page
        self.issue_page = issue_page
        self.issued: list[str] = []

    def open(self, request, timeout):
        url = request.full_url
        self.issued.append(url)
        if "index.aspx" in url:
            return FakeResponse("<html>index</html>")
        if "getFirstPublish" in url:
            return FakeResponse(self.first_page)
        if "getThisIssuePaperInfo" in url:
            return FakeResponse(self.issue_page)
        raise AssertionError(f"unexpected publisher request: {url}")


def test_publisher_validation_is_not_a_toc():
    assert cn.glsj_validation_page(CHALLENGE)
    assert cn.glsj_validation_page('<html><a href="showValidateCode.aspx">verify</a></html>')
    assert not cn.glsj_validation_page('<li><a href="paperDigest.aspx?paperID=123">study</a></li>')


def test_first_publish_validation_stops_without_scanning_issue(monkeypatch):
    opener = FakeOpener(CHALLENGE, EMPTY)
    monkeypatch.setattr(cn.urllib.request, "build_opener", lambda *args: opener)
    rows, mode = cn.fetch_journal(JOURNAL, cn.CN_HOME_URLS[GLSJ], 20)
    assert rows == []
    assert "first-publish-validation" in mode
    assert len(opener.issued) == 2  # index + the first-publish page
    assert not any("getThisIssuePaperInfo" in url for url in opener.issued)


def test_current_issue_challenge_aborts_before_many_issue_requests(monkeypatch):
    opener = FakeOpener(EMPTY, CHALLENGE)
    monkeypatch.setattr(cn.urllib.request, "build_opener", lambda *args: opener)
    rows, mode = cn.fetch_journal(JOURNAL, cn.CN_HOME_URLS[GLSJ], 20)
    assert rows == []
    assert "current-issue-validation" in mode
    attempted = [x for x in opener.issued if "getThisIssuePaperInfo" in x]
    assert len(attempted) == 1
    current_month = datetime.now(ZoneInfo("Asia/Shanghai")).month
    assert attempted[0].endswith(f"i={current_month}")


def test_archive_challenge_stops_fallback_search(monkeypatch):
    attempts = []
    def blocked_page(url, timeout, max_bytes):
        attempts.append(url)
        return CHALLENGE
    monkeypatch.setattr(cn, "fetch_text_partial", blocked_page)
    cn.GLSJ_LAST_NOTE = ""
    rows = cn.fetch_glsj_archive_issue(JOURNAL, 20, deadline=cn.time.monotonic() + 45)
    assert rows == []
    assert cn.GLSJ_LAST_NOTE == "archive-validation"
    assert len(attempts) == 1


def test_empty_management_world_not_reported_as_healthy(tmp_path, monkeypatch):
    monkeypatch.setattr(cn, "load_journals", lambda _: [JOURNAL])
    monkeypatch.setattr(cn, "filter_journals_by_tier", lambda journals, tier: journals)
    monkeypatch.setattr(cn, "fetch_journal_with_retry",
                        lambda *args, **kwargs: ([], "glsj-current-year-empty, current-year-empty", 1))
    monkeypatch.setattr(cn, "record_source", lambda *args, **kwargs: None)
    monkeypatch.setattr(cn, "load_status", lambda: {})
    captured = {}
    monkeypatch.setattr(cn, "save_status", lambda status: captured.update(status))
    monkeypatch.setattr(sys, "argv", ["fetch_cn_journals.py", "--only", GLSJ,
                                     "--output", str(tmp_path / "glsj.json"), "--retries", "0"])
    cn.main()
    group = captured["source_groups"]["cn-journals"]
    assert not group["ok"]
    row = group["journals"][0]
    assert row["ok"] is False
    assert row["fallback_status"] == "queued"
    assert row["fallback"] == "local-cnki-rss"
    assert row["count"] == 0


def test_issue_candidates_prioritize_current_month():
    months = cn.current_issue_candidates()
    current_month = datetime.now(ZoneInfo("Asia/Shanghai")).month
    assert months[0] == current_month
    assert len(months) == len(set(months))
    assert all(1 <= m <= 12 for m in months)
