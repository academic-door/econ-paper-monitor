from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from metadata_expectations import expected_missing_reason  # noqa: E402
from product_audit import audit  # noqa: E402


def _record(title: str) -> dict:
    return {
        "id": "doi:10.1007/s11127-026-01466-7",
        "title": title,
        "doi": "10.1007/s11127-026-01466-7",
        "url": "https://doi.org/10.1007/s11127-026-01466-7",
        "journal": "Public Choice",
        "journal_id": "public-choice",
        "source": "crossref",
        "source_type": "journal",
        "authors": ["Reviewer Name"],
        "abstract": "",
        "date_confidence": "A",
        "date_source": "publisher_published_online",
        "_daily_date": "2026-09-15",
        "available_online": "2026-09-15",
        "published_online": "2026-09-15",
        "issue_date": None,
        "first_seen": "2026-09-15T10:00:00+00:00",
        "fields": [],
    }


def test_bibliographic_review_title_makes_only_abstract_optional() -> None:
    item = _record(
        "Review of Douglas W. Allen and Bryan Leonard. 2025. “Why the rush: an institutional economic analysis of homesteading and the settlement of the west”"
    )
    assert expected_missing_reason(item, "abstract") == "book_review"
    assert expected_missing_reason(item, "authors") is None


def test_bibliographic_review_moves_recent_gap_from_actionable_to_expected() -> None:
    item = _record(
        "Review of Douglas W. Allen and Bryan Leonard. 2025. “Why the rush: an institutional economic analysis of homesteading and the settlement of the west”"
    )
    totals = audit([item], {"public-choice"})["totals"]
    assert totals["missing_abstract_recent"] == 1
    assert totals["missing_abstract_recent_actionable"] == 0
    assert totals["missing_abstract_recent_expected"] == 1


def test_generic_review_of_research_title_remains_actionable() -> None:
    item = _record("Review of monetary policy transmission evidence across emerging markets")
    assert expected_missing_reason(item, "abstract") is None
