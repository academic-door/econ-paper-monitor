from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import clean_historical_working_papers as cleaner  # noqa: E402
import fetch_preprints  # noqa: E402


def _record(*, source: str = "working_papers", source_id: str = "cepr-dp", url: str, official: str) -> dict:
    return {
        "id": f"url:{url}",
        "title": "Example paper",
        "source": source,
        "source_id": source_id,
        "source_type": "working_paper" if source == "working_papers" else "journal",
        "url": url,
        "available_online": official,
        "published_online": official,
        "date_confidence": "A",
        "first_seen": "2026-09-21T00:20:01+00:00",
    }


def _run(tmp_path: Path, monkeypatch, records: list[dict]) -> tuple[list[dict], list[dict]]:
    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / "2026-09-21.json").write_text(json.dumps(records), encoding="utf-8")
    pending = tmp_path / "pending.json"
    pending.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "clean_historical_working_papers.py",
            "--daily-dir",
            str(daily),
            "--pending",
            str(pending),
            "--max-age-days",
            "14",
        ],
    )
    cleaner.main()
    return (
        json.loads((daily / "2026-09-21.json").read_text(encoding="utf-8")),
        json.loads(pending.read_text(encoding="utf-8")),
    )


def test_old_cepr_rediscovery_is_quarantined_even_when_first_seen_is_today(tmp_path: Path, monkeypatch) -> None:
    record = _record(
        url="https://cepr.org/publications/dp13798",
        official="2019-06-13",
    )
    kept, pending = _run(tmp_path, monkeypatch, [record])

    assert kept == []
    assert len(pending) == 1
    assert pending[0]["url"].endswith("/dp13798")
    assert pending[0]["first_seen"] == "2026-09-21T00:20:01+00:00"
    assert "legacy-catalogue" in pending[0]["pending_reason"]


def test_fetcher_rejects_only_legacy_cepr_catalogue_numbers() -> None:
    assert fetch_preprints.is_historical_cepr_record(
        {"source_id": "cepr-dp", "url": "https://cepr.org/publications/dp13798"}
    )
    assert not fetch_preprints.is_historical_cepr_record(
        {"source_id": "cepr-dp", "url": "https://cepr.org/publications/dp21172"}
    )
    assert not fetch_preprints.is_historical_cepr_record(
        {"source_id": "cepr-dp", "url": "https://cepr.org/publications/dp21958"}
    )


def test_cepr_prefers_official_rss_before_html(monkeypatch) -> None:
    source = {
        "id": "cepr-dp",
        "title": "CEPR Discussion Papers",
        "type": "working_paper",
        "homepage": "https://cepr.org/publications/discussion-papers/search-discussion-papers",
        "feed": "https://cepr.org/rss/discussion-paper",
        "url_pattern": r"/publications/dp\d+",
        "url_contains": ["/publications/dp"],
        "fields": ["macro"],
    }
    rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>DP21958 Estimating treatment-effect heterogeneity across sites</title>
          <link>https://cepr.org/publications/dp21958</link>
          <pubDate>Sun, 20 Sep 2026 00:00:00 GMT</pubDate>
          <description>Current CEPR discussion paper.</description>
        </item>
      </channel>
    </rss>
    """
    calls: list[str] = []

    def fake_fetch_text(url: str, timeout: int = 30, headers=None) -> str:
        calls.append(url)
        if url == source["feed"]:
            return rss
        raise AssertionError("HTML fallback should not run when official RSS succeeds")

    monkeypatch.setattr(fetch_preprints, "fetch_text", fake_fetch_text)
    records, method = fetch_preprints.fetch_source(source, timeout=5, limit=12)

    assert method == "official-rss"
    assert calls == [source["feed"]]
    assert len(records) == 1
    assert records[0]["paper_number"] == "DP21958"
    assert records[0]["published_online"] == "2026-09-20"
    assert records[0]["date_source"] == "rss_published"


def test_cepr_rss_failure_falls_back_to_current_search_html(monkeypatch) -> None:
    source = {
        "id": "cepr-dp",
        "title": "CEPR Discussion Papers",
        "type": "working_paper",
        "homepage": "https://cepr.org/publications/discussion-papers/search-discussion-papers",
        "feed": "https://cepr.org/rss/discussion-paper",
        "url_pattern": r"/publications/dp\d+",
        "url_contains": ["/publications/dp"],
        "fields": ["macro"],
    }
    html = (
        '<a href="/publications/dp21958">'
        "DP21958 Estimating treatment-effect heterogeneity across sites"
        "</a>"
    )

    def fake_fetch_text(url: str, timeout: int = 30, headers=None) -> str:
        if url == source["feed"]:
            raise OSError("RSS transport unavailable")
        if url == source["homepage"]:
            return html
        raise AssertionError(url)

    monkeypatch.setattr(fetch_preprints, "fetch_text", fake_fetch_text)
    records, method = fetch_preprints.fetch_source(source, timeout=5, limit=12)

    assert method == "specialized-html"
    assert len(records) == 1
    assert records[0]["paper_number"] == "DP21958"


def test_current_cepr_paper_remains_eligible_for_today(tmp_path: Path, monkeypatch) -> None:
    record = _record(
        url="https://cepr.org/publications/dp21958",
        official="2026-09-20",
    )
    kept, pending = _run(tmp_path, monkeypatch, [record])

    assert [item["url"] for item in kept] == ["https://cepr.org/publications/dp21958"]
    assert pending == []


def test_old_journal_first_discovery_semantics_are_unchanged(tmp_path: Path, monkeypatch) -> None:
    record = _record(
        source="crossref",
        source_id="journal-example",
        url="https://doi.org/10.1234/example",
        official="2019-06-13",
    )
    kept, pending = _run(tmp_path, monkeypatch, [record])

    assert len(kept) == 1
    assert pending == []
