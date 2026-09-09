"""Tests for the scheduled recent working-paper metadata backfill."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backfill_iza_authors import (  # noqa: E402
    canonical_detail_url,
    needs_scheduled_repair,
    normalize_authors,
    repair_record,
)


class CanonicalDetailUrlTests(unittest.TestCase):
    def test_cepr_encoded_legacy_path_is_canonicalized(self):
        self.assertEqual(
            canonical_detail_url(
                "cepr-dp",
                "https://cepr.org/index%2Ephp/publications/dp21172",
            ),
            "https://cepr.org/publications/dp21172",
        )

    def test_cepr_decoded_legacy_path_is_canonicalized(self):
        self.assertEqual(
            canonical_detail_url(
                "cepr-dp",
                "https://cepr.org/index.php/publications/dp21172?legacy=1#paper",
            ),
            "https://cepr.org/publications/dp21172",
        )

    def test_unrelated_url_is_unchanged(self):
        url = "https://www.iza.org/publications/dp/19321/example"
        self.assertEqual(canonical_detail_url("iza", url), url)


class ScheduledBackfillTests(unittest.TestCase):
    def test_normalize_authors_flattens_cepr_combined_values(self):
        self.assertEqual(
            normalize_authors(["Marc Klemp; Jane Doe", "Marc Klemp"]),
            ["Marc Klemp", "Jane Doe"],
        )

    def test_cepr_abstract_only_gap_is_scheduled(self):
        record = {
            "source_id": "cepr-dp",
            "authors": ["Existing Author"],
            "abstract": None,
            "url": "https://cepr.org/publications/dp17159",
        }
        self.assertTrue(needs_scheduled_repair(record, "cepr-dp"))

    def test_iza_abstract_only_gap_does_not_broaden_contract(self):
        record = {
            "source_id": "iza",
            "authors": ["Existing Author"],
            "abstract": None,
            "url": "https://www.iza.org/publications/dp/19321/example",
        }
        self.assertFalse(needs_scheduled_repair(record, "iza"))

    def test_repair_record_fetches_cepr_canonical_url_and_preserves_title(self):
        record = {
            "id": "url:745e78d1f96ad024",
            "source_id": "cepr-dp",
            "title": "When Sectoral Recovery Fails to Aggregate",
            "url": "https://cepr.org/index%2Ephp/publications/dp21172",
            "authors": [],
            "abstract": None,
            "first_seen": "2026-09-03T21:27:47+00:00",
        }
        source = {"id": "cepr-dp"}

        def fake_enrich(updated, source_config, *, timeout):
            self.assertEqual(updated["url"], "https://cepr.org/publications/dp21172")
            self.assertEqual(source_config["id"], "cepr-dp")
            self.assertEqual(timeout, 12)
            updated["title"] = "DP21172 When Sectoral Recovery Fails to Aggregate"
            updated["authors"] = ["Marc Klemp"]
            updated["abstract"] = "Authoritative CEPR abstract text. " * 8
            updated["abstract_source"] = "cepr_proxy_markdown"
            return updated

        with patch("backfill_iza_authors.enrich_record_from_detail", side_effect=fake_enrich):
            changed, authors_enriched, abstract_enriched = repair_record(record, source, timeout=12)

        self.assertTrue(changed)
        self.assertTrue(authors_enriched)
        self.assertTrue(abstract_enriched)
        self.assertEqual(record["url"], "https://cepr.org/publications/dp21172")
        self.assertEqual(record["title"], "When Sectoral Recovery Fails to Aggregate")
        self.assertEqual(record["authors"], ["Marc Klemp"])
        self.assertEqual(record["authors_status_code"], "available")
        self.assertEqual(record["abstract_status_code"], "available")
        self.assertEqual(record["abstract_enrichment_status"], "available")
        self.assertEqual(record["first_seen"], "2026-09-03T21:27:47+00:00")

    def test_repair_record_enriches_abstract_without_changing_existing_authors(self):
        record = {
            "id": "url:example",
            "source_id": "cepr-dp",
            "title": "Example CEPR Paper",
            "url": "https://cepr.org/publications/dp17159",
            "authors": ["Existing Author"],
            "abstract": None,
            "first_seen": "2026-09-08T10:00:00+00:00",
        }
        source = {"id": "cepr-dp"}

        def fake_enrich(updated, source_config, *, timeout):
            self.assertEqual(source_config["id"], "cepr-dp")
            updated["abstract"] = "Authoritative CEPR abstract text. " * 8
            updated["abstract_source"] = "cepr_proxy_markdown"
            return updated

        with patch("backfill_iza_authors.enrich_record_from_detail", side_effect=fake_enrich):
            changed, authors_enriched, abstract_enriched = repair_record(record, source, timeout=10)

        self.assertTrue(changed)
        self.assertFalse(authors_enriched)
        self.assertTrue(abstract_enriched)
        self.assertEqual(record["authors"], ["Existing Author"])
        self.assertEqual(record["abstract_status_code"], "available")
        self.assertEqual(record["first_seen"], "2026-09-08T10:00:00+00:00")

    def test_repair_record_reports_no_change_when_enrichment_returns_nothing(self):
        record = {
            "source_id": "iza",
            "title": "Example",
            "url": "https://www.iza.org/publications/dp/19321/example",
            "authors": [],
            "abstract": None,
        }
        source = {"id": "iza"}
        with patch("backfill_iza_authors.enrich_record_from_detail", side_effect=lambda item, source_config, timeout: item):
            changed, authors_enriched, abstract_enriched = repair_record(record, source, timeout=10)
        self.assertFalse(changed)
        self.assertFalse(authors_enriched)
        self.assertFalse(abstract_enriched)


if __name__ == "__main__":
    unittest.main()
