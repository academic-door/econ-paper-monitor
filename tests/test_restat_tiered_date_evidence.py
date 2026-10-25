"""#389: source-graded RESTAT publisher/institution/index date reconciliation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import apply_overrides  # noqa: E402
import date_provenance  # noqa: E402
import render_site  # noqa: E402
import enrich_metadata  # noqa: E402
from common import DATA_DIR  # noqa: E402


CASES = {
    "10.1162/rest_a_01486": (
        "2024-08-22",
        "institutional_online_date_verified",
        "B",
        "研究机构发表记录",
        "研究机构记录在线日期",
    ),
    "10.1162/rest_a_01471": (
        "2024-06-03",
        "independent_bibliographic_online_date",
        "C",
        "独立文献索引",
        "独立文献索引日期",
    ),
}


def test_exact_doi_evidence_tiers_and_no_unverified_date_rewrite() -> None:
    overrides = apply_overrides.load_overrides(DATA_DIR / "manual_overrides.yml")
    for doi, (date, source, confidence, _provenance, _public_label) in CASES.items():
        entry = overrides[doi]
        assert entry["available_online"] == date
        assert entry["published_online"] == date
        assert entry["date_source"] == source
        assert entry["date_confidence"] == confidence
        assert "first_seen" not in entry
        assert "issue_date" not in entry
    for doi in (
        "10.1162/rest_a_01463",
        "10.1162/rest_a_01472",
        "10.1162/rest_a_01478",
        "10.1162/rest.a.1616",
        "10.1162/rest.a.1882",
    ):
        assert doi not in overrides


def test_first_seen_id_and_issue_date_are_unchanged_and_idempotent() -> None:
    overrides = apply_overrides.load_overrides(DATA_DIR / "manual_overrides.yml")
    for doi, (date, source, confidence, _, _) in CASES.items():
        record = {
            "id": f"doi:{doi}",
            "doi": doi,
            "source": "crossref",
            "date_source": "crossref_published_online",
            "date_confidence": "C",
            "available_online": "2026-10-13",
            "published_online": "2026-10-13",
            "issue_date": "2026-10-13",
            "first_seen": "2026-10-22T22:50:14+00:00",
        }
        assert apply_overrides.apply_to_record(record, overrides[doi])
        assert record["published_online"] == date
        assert record["date_source"] == source
        assert record["date_confidence"] == confidence
        assert record["first_seen"] == "2026-10-22T22:50:14+00:00"
        assert record["issue_date"] == "2026-10-13"
        assert record["id"] == f"doi:{doi}"
        assert not apply_overrides.apply_to_record(record, overrides[doi])


def test_both_public_surfaces_explicitly_disclose_nonpublisher_provenance() -> None:
    for doi, (date, source, _confidence, provenance_label, public_label) in CASES.items():
        record = {
            "doi": doi,
            "source": "crossref",
            "source_url": "https://api.crossref.org",
            "published_online": date,
            "available_online": date,
            "date_source": source,
        }
        assert date_provenance.date_kind(record) == date_provenance.REFERENCED
        line = date_provenance.provenance_text(record, date)
        assert line == f"参考在线日期：{date} · 来源：{provenance_label}"
        assert render_site.public_date_line(record) == f"{public_label} {date} · 来源：{provenance_label}"
        assert "官方在线" not in render_site.public_date_line(record)
        assert "来源：Crossref" not in render_site.public_date_line(record)


def test_same_grade_crossref_metadata_cannot_undo_curated_online_date() -> None:
    for doi, (date, source, grade, _evidence, _label) in CASES.items():
        row = {
            "doi": doi,
            "date_source": source,
            "date_confidence": grade,
            "available_online": date,
            "published_online": date,
            "first_seen": "2026-10-22T22:50:14+00:00",
        }
        incoming = {
            "date_source": "crossref_doi_published_online",
            "date_confidence": "C",
            "published_online": "2026-10-13",
            "available_online": "2026-10-13",
            "abstract": "An independently retrieved abstract.",
        }
        assert enrich_metadata.merge_metadata(row, incoming)
        assert row["published_online"] == date
        assert row["date_source"] == source
        assert row["date_confidence"] == grade
        assert row["abstract"] == "An independently retrieved abstract."
        assert row["first_seen"] == "2026-10-22T22:50:14+00:00"

        # New first-party evidence at a stronger grade remains authoritative.
        assert enrich_metadata.merge_metadata(row, {
            "date_source": "publisher_published_online",
            "date_confidence": "A",
            "published_online": "2024-06-02",
            "available_online": "2024-06-02",
        })
        assert row["published_online"] == "2024-06-02"
        assert row["date_source"] == "publisher_published_online"
        assert row["date_confidence"] == "A"
        assert row["first_seen"] == "2026-10-22T22:50:14+00:00"
