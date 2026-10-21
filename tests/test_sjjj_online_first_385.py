"""Independent official SJJJ online-first publisher *visible roster* checks."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_sjjj_online_first as sjjj
from dedupe import archive_date_for_new_record

JOURNAL = {"id": sjjj.JOURNAL_ID, "title": "世界经济", "fields": []}
PREPUB_DATE = "2026-04-10"


def _article(article_id="1193", title="真实论文标题一", date=PREPUB_DATE, url=None):
    actual_url = url or f"{sjjj.ARTICLE_PREFIX}{article_id}.shtml"
    return (
        f'<li id="art{article_id}"><div class="j-title-1">'
        f'<a href="{actual_url}">{title}</a></div>'
        f'<div class="j-author">张三, 李四</div>'
        f'<div class="j-volumn-doi">预出版日期: {date}</div></li>'
    )


def _page(*articles, current=1, pages=1, total=None, disclaimer=True):
    if total is None:
        total = len(articles)
    scope = "优先出版 说明：最新在线预出版文章，内容和格式将与印刷版一致" if disclaimer else "当期目录"
    return (f'<html>{scope}<ul>{"".join(articles)}</ul>'
            f'<div>第{current}页 共{pages}页 共{total}条记录</div></html>')


def _parse(page):
    return sjjj.parse_roster(page, JOURNAL, page_hash="x" * 64,
                             observed_at="2026-10-21T00:21:00Z")


def test_authoritative_visible_roster_has_independent_provenance_and_prepub_date():
    records = _parse(_page(_article(), _article("1192", "真实论文标题二")))
    assert len(records) == 2
    assert records[0]["source"] == "cn-official"
    assert records[0]["source_url"] == sjjj.OFFICIAL_URL
    assert records[0]["url"] == sjjj.ARTICLE_PREFIX + "1193.shtml"
    assert records[0]["published_online"] == PREPUB_DATE
    assert records[0]["available_online"] == PREPUB_DATE
    assert records[0]["raw_data"]["source_scope"] == "PUBLISHER_VISIBLE_ONLINE_FIRST_ONLY"
    assert records[0]["raw_data"]["publisher_article_id"] == "1193"
    assert records[0]["raw_data"]["observed_at"] == "2026-10-21T00:21:00Z"
    assert records[0]["authors"] == ["张三", "李四"]


def test_new_official_old_date_enters_actual_discovery_daily_not_backdated():
    records = _parse(_page(_article()))
    # Never date a new cn-official *discovery* to its 6-month-old prepub date.
    assert archive_date_for_new_record(records[0], "2026-10-21") == "2026-10-21"
    assert records[0]["detected_at"] != PREPUB_DATE
    assert records[0]["published_online"] == PREPUB_DATE


@pytest.mark.parametrize("page,reason", [
    (_page(_article(), pages=2), "pagination"),
    (_page(_article(), total=2), "article blocks"),
    (_page(_article(), disclaimer=False), "scope statement"),
    (_page(), "pagination"),
    (_page(_article(date="2026-13-40")), "invalid prepublication"),
    (_page(_article(date="no date")), "missing title or date"),
    (_page(_article(url="https://untrusted.example/abstract1193.shtml")), "URL/ID mismatch"),
    (_page(_article("1193"), _article("1193")), "URL/ID mismatch"),
])
def test_unverified_publisher_scope_fails_closed(page, reason):
    with pytest.raises(sjjj.SourceBlocked, match=reason):
        _parse(page)


def test_live_fetch_enforces_exact_https_source_and_bounded_bytes(monkeypatch):
    class Headers:
        def get_content_charset(self):
            return "utf-8"

    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return None
        def geturl(self):
            return "https://wrong.example/redirect"
        def read(self, n):
            return b"x"

    monkeypatch.setattr(sjjj.urllib.request, "urlopen", lambda req, timeout: FakeResponse())
    with pytest.raises(sjjj.SourceBlocked, match="response status or URL"):
        sjjj.fetch_official()


def test_strict_source_hash_is_correct_for_valid_response(monkeypatch):
    raw = _page(_article()).encode("utf-8")
    class FakeResponse:
        status = 200
        def __enter__(self):return self
        def __exit__(self, *a):return None
        def geturl(self):return sjjj.OFFICIAL_URL
        def read(self, n):return raw
    monkeypatch.setattr(sjjj.urllib.request, "urlopen", lambda req, timeout: FakeResponse())
    page, digest, as_of = sjjj.fetch_official()
    assert page == raw.decode("utf-8")
    assert digest == hashlib.sha256(raw).hexdigest()
    assert as_of.endswith("+00:00")
