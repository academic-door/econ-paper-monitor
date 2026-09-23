from __future__ import annotations

import json
import sys
import urllib.error
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


def _cepr_html(number: int, title: str = "Estimating treatment effect heterogeneity across sites") -> str:
    return (
        '<html><body>'
        f'<a href="/publications/dp{number}">DP{number} {title}</a>'
        '</body></html>'
    )


def test_cepr_current_listing_falls_back_after_primary_403(monkeypatch) -> None:
    source = {
        "id": "cepr-dp",
        "title": "CEPR Discussion Papers",
        "type": "working_paper",
        "homepage": "https://cepr.org/publications/discussion-papers/search-discussion-papers",
        "url_pattern": r"/publications/dp\d+",
        "url_contains": ["/publications/dp"],
        "fields": ["macro"],
    }
    requested: list[str] = []

    def fake_fetch_text(url: str, *, timeout: int) -> str:
        requested.append(url)
        if "search-discussion-papers" in url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)
        if url.rstrip("/") == "https://cepr.org/publications":
            return _cepr_html(21958)
        return "<html></html>"

    monkeypatch.setattr(fetch_preprints, "fetch_text", fake_fetch_text)
    records, method = fetch_preprints.fetch_source(source, timeout=5, limit=12)

    assert method == "cepr-official-html:publications"
    assert [record["paper_number"] for record in records] == ["DP21958"]
    assert requested[:2] == [
        "https://cepr.org/publications/discussion-papers/search-discussion-papers",
        "https://cepr.org/publications",
    ]


def test_cepr_current_listing_rejects_stale_only_surfaces(monkeypatch) -> None:
    source = {
        "id": "cepr-dp",
        "title": "CEPR Discussion Papers",
        "type": "working_paper",
        "homepage": "https://cepr.org/publications/discussion-papers/search-discussion-papers",
        "url_pattern": r"/publications/dp\d+",
        "url_contains": ["/publications/dp"],
        "fields": ["macro"],
    }

    monkeypatch.setattr(
        fetch_preprints,
        "fetch_text",
        lambda url, *, timeout: _cepr_html(13978, "A historically rediscovered discussion paper title"),
    )

    try:
        fetch_preprints.fetch_source(source, timeout=5, limit=12)
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("stale-only CEPR surfaces must fail closed")

    assert "stale-max-DP13978" in message


def test_cepr_listing_floor_is_transport_only() -> None:
    assert fetch_preprints.CEPR_CURRENT_LISTING_FLOOR == 20000
    old_record = {"source_id": "cepr-dp", "url": "https://cepr.org/publications/dp13978"}
    current_record = {"source_id": "cepr-dp", "url": "https://cepr.org/publications/dp21958"}
    assert not fetch_preprints.is_historical_cepr_record(old_record)
    assert not fetch_preprints.is_current_cepr_listing_record(old_record)
    assert fetch_preprints.is_current_cepr_listing_record(current_record)


def test_cepr_current_listing_drops_stale_rows_from_mixed_surface(monkeypatch) -> None:
    source = {
        "id": "cepr-dp",
        "title": "CEPR Discussion Papers",
        "type": "working_paper",
        "homepage": "https://cepr.org/publications/discussion-papers/search-discussion-papers",
        "url_pattern": r"/publications/dp\d+",
        "url_contains": ["/publications/dp"],
        "fields": ["macro"],
    }
    html = (
        '<html><body>'
        '<a href="/publications/dp21958">DP21958 A current discussion paper with a sufficiently descriptive title</a>'
        '<a href="/publications/dp13127">DP13127 Optimal fund menus and historical catalogue contamination</a>'
        '</body></html>'
    )
    monkeypatch.setattr(fetch_preprints, "fetch_text", lambda url, *, timeout: html)

    records, method = fetch_preprints.fetch_source(source, timeout=5, limit=12)

    assert method == "cepr-official-html:search"
    assert [record["paper_number"] for record in records] == ["DP21958"]


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
