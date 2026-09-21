from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import render_site


def duplicate_records() -> list[dict]:
    base = {
        "title": "Trade with China and Firm Outcomes",
        "doi": "10.1234/example",
        "journal": "Example Journal",
        "journal_id": "example-journal",
        "source_type": "journal_article",
        "detected_at": "2026-09-21T00:00:00+00:00",
    }
    return [
        dict(base, abstract="General trade evidence.", china_relevance_status="none"),
        dict(base, abstract="Evidence from China.", china_related=True, china_relevance_status="confirmed"),
    ]


def test_search_catalog_preserves_positive_china_signal_across_duplicates() -> None:
    catalog = render_site.search_catalog_records(duplicate_records())

    assert len(catalog) == 1
    assert render_site.is_public_china_related(catalog[0])


def test_search_body_count_matches_unique_china_identity() -> None:
    html = render_site.search_body(duplicate_records())

    assert "<strong>1</strong><span>与中国相关</span>" in html


def test_search_lazy_dataset_keeps_china_filter_signal() -> None:
    render_site.LAZY_DATASETS.clear()
    dataset_id = render_site.register_lazy_dataset("search", duplicate_records())
    records, _ = render_site.LAZY_DATASETS[dataset_id]

    assert len(records) == 1
    assert render_site.is_public_china_related(records[0])
