"""A1 first-party scope / independent publisher issue-list / canonical diff gates."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_sljy_official as audit


def _home(titles=("Alpha — Study", "Beta")):
    chunks = []
    for n, title in enumerate(titles, 1):
        chunks.append(
            '<li><div class="aList"><a href="reader/view_abstract.aspx?file_no=2026090%d&flag=1">%s</a>'
            '<span>[甲，乙]</span></div><div class="time"><em>2026,(9):%d-%d</em></div></li>'
            % (n, title, n * 10, n * 10 + 9)
        )
    return "<html>本刊最新目录<ul>" + "".join(chunks) + "</ul></html>"


def _issue(titles=("Alpha — Study", "Beta")):
    return "".join(
        '<table><a href="view_abstract.aspx?file_no=2026090%d&flag=1">%s</a>'
        '<a href="view_abstract.aspx?file_no=2026090%d&flag=1">下载全文及数据</a></table>'
        % (n, title, n) for n, title in enumerate(titles, 1)
    )


def _archive(tmp_path, titles=("Alpha-Study", "Beta")):
    folder = tmp_path / "daily"
    folder.mkdir()
    path = folder / "2026-09-09.json"
    items = [
        {"journal_id": audit.JOURNAL_ID, "title": title,
         "first_seen": "2026-09-10T10:30:00Z", "published_online": "2026-09-09"}
        for title in titles
    ]
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return folder, path


def test_realistic_two_first_party_pages_and_first_seen_invariant(tmp_path):
    daily, path = _archive(tmp_path)
    before = path.read_bytes()
    result = audit.evaluate(_home(), _issue(), daily_dir=daily,
                            observation_time="2026-10-18T18:00:00+00:00",
                            hashes={"homepage": "h1", "issue_list": "h2"})
    assert result["issue"] == "2026(9)"
    assert result["scope"] == "PUBLISHER_LISTED_CURRENT_PRINT_ISSUE_ONLY"
    assert result["print_issue_roster_status"] == "RECONCILED_AS_OF"
    assert result["online_first_coverage_status"] == "NOT_VERIFIED"
    assert [r["file_no"] for r in result["publisher_member_order"]] == ["20260901", "20260902"]
    assert result["archive"]["publisher_listed"] == 2
    assert result["archive"]["unique_archive_matches"] == 2
    assert result["archive"]["missing"] == []
    assert path.read_bytes() == before  # never rewrite canonical observation timestamps


@pytest.mark.parametrize("issue_html", [
    _issue(("Beta", "Alpha — Study")),
    _issue(("Alpha — Study",)),
    _issue(("Alpha — Study", "Different")),
])
def test_mismatched_publisher_page_is_blocked(issue_html):
    with pytest.raises(audit.ObservationBlocked, match="differs"):
        audit.compare_pages(_home(), issue_html)


def test_missing_canonical_member_is_a_diff_not_complete(tmp_path):
    daily, _ = _archive(tmp_path, ("Alpha-Study",))
    result = audit.evaluate(_home(), _issue(), daily_dir=daily,
                            observation_time="2026-10-18T18:00:00Z", hashes={})
    assert result["print_issue_roster_status"] == "DIFF_OBSERVED_AS_OF"
    assert result["archive"]["unique_archive_matches"] == 1
    assert [item["file_no"] for item in result["archive"]["missing"]] == ["20260902"]


def test_canonical_duplicate_is_not_reconciled(tmp_path):
    daily, _ = _archive(tmp_path)
    extra = [{"journal_id": audit.JOURNAL_ID, "title": "Beta", "first_seen": "later"}]
    (daily / "2026-09-10.json").write_text(json.dumps(extra), encoding="utf-8")
    result = audit.evaluate(_home(), _issue(), daily_dir=daily,
                            observation_time="2026-10-18T18:00:00Z", hashes={})
    assert result["print_issue_roster_status"] == "DIFF_OBSERVED_AS_OF"
    assert len(result["archive"]["duplicate_matches"]) == 1


def test_missing_or_mixed_publisher_issue_is_blocked():
    with pytest.raises(audit.ObservationBlocked, match="heading"):
        audit.compare_pages("<html>no issue</html>", _issue())
    mixed = _home().replace("2026,(9):20-29", "2026,(8):20-29")
    with pytest.raises(audit.ObservationBlocked, match="mixed issues"):
        audit.compare_pages(mixed, _issue())


def test_missing_canonical_daily_archive_is_blocked(tmp_path):
    with pytest.raises(audit.ObservationBlocked, match="unavailable"):
        audit.audit_archive(audit.parse_homepage(_home()), tmp_path / "missing")


def test_non_allowlisted_publisher_url_is_rejected_without_network():
    with pytest.raises(audit.ObservationBlocked, match="allowlist"):
        audit.observe_url("https://other.example/reader/issue_list.aspx")


def test_publisher_page_rejects_invalid_redirect(monkeypatch):
    class Response:
        status = 200
        headers = type("H", (), {"get_content_charset": lambda self: "utf-8"})()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def geturl(self):
            return "https://redirect.example/"
        def read(self, n):
            return b""
    monkeypatch.setattr(audit.urllib.request, "urlopen", lambda *a, **k: Response())
    with pytest.raises(audit.ObservationBlocked, match="redirect/status"):
        audit.observe_url(audit.HOME)
