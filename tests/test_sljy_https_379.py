"""First-party SLJY endpoint route must not depend on plaintext HTTP."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_cn_journals as cn


def test_sljy_first_party_url_uses_https() -> None:
    url = cn.CN_HOME_URLS["journal-edcb877d78"]
    assert url == "https://www.jqte.net/sljjjsjjyj/ch/index.aspx"
    assert url.startswith("https://")
    # This only selects the publisher transport. No RSS/CNKI mirror is
    # promoted to official roster authority by a successful transport.


def test_sljy_toc_record_keeps_official_url_and_issue_only_date() -> None:
    journal = {"id": "journal-edcb877d78", "title": "数量经济技术经济研究", "fields": []}
    page = (
        '<div>本刊最新目录</div><li><div class="aList">'
        '<a href="reader/view_abstract.aspx?file_no=20260901&flag=1">'
        '首发经济如何提振城市消费活力——来自首店进入的经验证据</a>'
        '<em>2026,(9):5-30</em></div></li>'
    )
    got = cn.parse_jqte(page, journal, limit=10)
    assert len(got) == 1
    assert got[0]["url"].startswith("https://www.jqte.net/")
    assert got[0]["source"] == "cn-official"
    assert got[0]["source_issue"] == "2026,(9):5-30"
    assert got[0]["date_source"] == "issue_only"
    assert not got[0]["published_online"]
