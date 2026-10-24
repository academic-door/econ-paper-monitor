"""#389: MIT Press verified first-online corrections are DOI-scoped and immutable for first_seen."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import apply_overrides  # noqa: E402
from common import DATA_DIR  # noqa: E402
from date_provenance import OFFICIAL, date_kind  # noqa: E402


VERIFIED = {
    "10.1162/rest_a_01533": "2024-11-25",
    "10.1162/rest_a_01540": "2024-11-25",
    "10.1162/rest_a_01545": "2024-11-25",
    "10.1162/rest_a_01538": "2024-11-25",
    "10.1162/rest_a_01525": "2024-10-29",
    "10.1162/rest_a_01502": "2024-09-16",
    "10.1162/rest_a_01501": "2024-09-16",
    "10.1162/rest_a_01508": "2024-09-16",
    "10.1162/rest_a_01510": "2024-09-16",
    "10.1162/rest_a_01503": "2024-09-16",
    "10.1162/rest_a_01506": "2024-09-16",
    "10.1162/rest_a_01507": "2024-09-16",
    "10.1162/rest_a_01509": "2024-09-16",
}


def test_restat_verified_dates_are_scoped_to_exact_legacy_dois() -> None:
    overrides = apply_overrides.load_overrides(DATA_DIR / "manual_overrides.yml")
    assert len(VERIFIED) == 13
    for doi, expected in VERIFIED.items():
        correction = overrides[doi]
        assert correction["available_online"] == expected
        assert correction["published_online"] == expected
        assert correction["date_confidence"] == "A"
        assert correction["date_source"] == "publisher_mitpress_online_early_verified"
        assert "first_seen" not in correction
        assert "issue_date" not in correction

    # Distinct new 2026 RESTAT DOI family: do not backdate without publisher evidence.
    for suffix in range(1873, 1883):
        assert f"10.1162/rest.a.{suffix}" not in overrides

    # Unverified members of the 21-paper legacy batch remain evidence-blocked.
    assert "10.1162/rest_a_01472" not in overrides
    assert "10.1162/rest.a.1616" not in overrides


def test_restat_correction_preserves_first_observation_and_identity() -> None:
    overrides = apply_overrides.load_overrides(DATA_DIR / "manual_overrides.yml")
    record = {
        "id": "doi:10.1162/rest_a_01533",
        "doi": "10.1162/rest_a_01533",
        "source": "crossref",
        "available_online": "2026-10-13",
        "published_online": "2026-10-13",
        "date_source": "crossref_published_online",
        "date_confidence": "C",
        "issue_date": "2026-10-13",
        "first_seen": "2026-10-22T22:50:14+00:00",
    }
    assert apply_overrides.apply_to_record(record, overrides[record["doi"]])
    assert record["published_online"] == "2024-11-25"
    assert record["available_online"] == "2024-11-25"
    assert record["date_confidence"] == "A"
    assert date_kind(record) == OFFICIAL
    assert record["first_seen"] == "2026-10-22T22:50:14+00:00"
    assert record["issue_date"] == "2026-10-13"
    assert record["id"] == "doi:10.1162/rest_a_01533"
    assert not apply_overrides.apply_to_record(record, overrides[record["doi"]])


def test_unverified_2026_restat_article_is_not_rewritten() -> None:
    overrides = apply_overrides.load_overrides(DATA_DIR / "manual_overrides.yml")
    doi = "10.1162/rest.a.1882"
    record = {
        "doi": doi,
        "published_online": "2026-10-16",
        "first_seen": "2026-10-22T23:57:54+00:00",
    }
    assert doi not in overrides
    assert record["published_online"] == "2026-10-16"
    assert record["first_seen"] == "2026-10-22T23:57:54+00:00"
