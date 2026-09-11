"""Tests for OECD scheduled recent working-paper metadata repair."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import backfill_iza_authors as backfill  # noqa: E402
from backfill_iza_authors import SCHEDULED_SOURCE_IDS, canonical_detail_url  # noqa: E402


class OecdCanonicalDetailUrlTests(unittest.TestCase):
    def test_oecd_ilibrary_publication_url_uses_current_official_host(self):
        self.assertEqual(
            canonical_detail_url(
                "oecd-working-papers",
                "https://www.oecd-ilibrary.org/en/publications/mapping-drought-severity-in-mexico-using-high-resolution-satellite-data_f2a165e7-en.html",
            ),
            "https://www.oecd.org/en/publications/mapping-drought-severity-in-mexico-using-high-resolution-satellite-data_f2a165e7-en.html",
        )

    def test_oecd_doi_is_derived_only_from_current_publication_slug(self):
        self.assertEqual(
            backfill.oecd_doi_from_url(
                "https://www.oecd.org/en/publications/mapping-drought-severity-in-mexico-using-high-resolution-satellite-data_f2a165e7-en.html"
            ),
            "10.1787/f2a165e7-en",
        )
        self.assertIsNone(backfill.oecd_doi_from_url("https://www.oecd.org/en/publications/not-a-publication.html"))


class OecdReadonlyFallbackTests(unittest.TestCase):
    def test_official_proxy_and_doi_metadata_fill_bounded_gap(self):
        record = {
            "id": "url:205e5e9de6ae0278",
            "source": "working_papers",
            "source_id": "oecd-working-papers",
            "source_type": "policy_paper",
            "title": "Mapping drought severity in Mexico using high-resolution satellite data",
            "url": "https://www.oecd.org/en/publications/mapping-drought-severity-in-mexico-using-high-resolution-satellite-data_f2a165e7-en.html",
            "authors": [],
            "abstract": "",
            "first_seen": "2026-06-18T20:31:17+00:00",
            "date_confidence": "F",
        }
        markdown = """
OECD Publications
Mapping drought severity in Mexico using high-resolution satellite data
OECD Economics Department Working Papers

1 April 2026
Download PDF
Cite this publication
Abstract
Related publications
Related topics
Share
Abstract

This paper analyses drought severity across Mexican regions between 2000 and 2025 using satellite-based indicators of vegetation health and surface moisture. It provides a consistent high-resolution measure of drought intensity and supports climate adaptation policy.
Related publications
"""
        doi_payload = {
            "DOI": "10.1787/f2a165e7-en",
            "publisher": "Organisation for Economic Co-Operation and Development (OECD)",
            "author": [
                {"given": "Ilyes", "family": "Boumahdi"},
                {"given": "Alberto González", "family": "Pandiella"},
            ],
        }
        with (
            patch.object(backfill, "fetch_text", return_value=markdown) as fetch_text,
            patch.object(backfill, "fetch_json", return_value=doi_payload) as fetch_json,
        ):
            updated = backfill.enrich_oecd_from_readonly_transports(record, timeout=10)

        self.assertEqual(updated["authors"], ["Ilyes Boumahdi", "Alberto González Pandiella"])
        self.assertIn("This paper analyses drought severity", updated["abstract"])
        self.assertEqual(updated["available_online"], "2026-04-01")
        self.assertEqual(updated["date_source"], "oecd_official_page_proxy")
        self.assertEqual(updated["doi"], "10.1787/f2a165e7-en")
        self.assertEqual(updated["authors_source"], "oecd_doi_registry")
        self.assertIn("r.jina.ai/http://www.oecd.org", fetch_text.call_args.args[0])
        self.assertEqual(
            fetch_json.call_args.kwargs["headers"]["Accept"],
            "application/vnd.citationstyles.csl+json",
        )

    def test_repair_falls_back_when_direct_oecd_html_is_blocked(self):
        record = {
            "id": "oecd-test-record",
            "source": "working_papers",
            "source_id": "oecd-working-papers",
            "source_type": "policy_paper",
            "title": "Mapping drought severity in Mexico using high-resolution satellite data",
            "url": "https://www.oecd-ilibrary.org/en/publications/mapping-drought-severity-in-mexico-using-high-resolution-satellite-data_f2a165e7-en.html",
            "authors": [],
            "abstract": "",
            "first_seen": "2026-06-18T20:31:17+00:00",
            "date_confidence": "F",
        }
        source = {"id": "oecd-working-papers"}

        def fallback(item, *, timeout):
            item["authors"] = ["Ilyes Boumahdi", "Alberto González Pandiella"]
            item["abstract"] = "Authoritative OECD abstract from the official publication page."
            return item

        with (
            patch.object(backfill, "enrich_record_from_detail", side_effect=lambda item, source, *, timeout: item),
            patch.object(backfill, "enrich_oecd_from_readonly_transports", side_effect=fallback) as proxy_fallback,
        ):
            changed, authors_added, abstract_added = backfill.repair_record(record, source, timeout=10)

        self.assertTrue(changed)
        self.assertTrue(authors_added)
        self.assertTrue(abstract_added)
        self.assertTrue(proxy_fallback.called)
        self.assertEqual(record["first_seen"], "2026-06-18T20:31:17+00:00")
        self.assertIn("www.oecd.org/", record["url"])


class OecdScheduledBackfillTests(unittest.TestCase):
    def setUp(self):
        self.legacy_url = (
            "https://www.oecd-ilibrary.org/en/publications/"
            "mapping-drought-severity-in-mexico-using-high-resolution-satellite-data_f2a165e7-en.html"
        )
        self.first_seen = "2026-06-18T20:31:17+00:00"
        self.record = {
            "id": "oecd-test-record",
            "source": "working_papers",
            "source_id": "oecd-working-papers",
            "source_type": "policy_paper",
            "title": "Mapping drought severity in Mexico using high-resolution satellite data",
            "url": self.legacy_url,
            "authors": [],
            "abstract": "",
            "first_seen": self.first_seen,
            "date_confidence": "F",
        }

    def _run_backfill(self, *, daily_has_record: bool, queue_target: bool = True) -> tuple[dict, dict]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily_dir = root / "daily"
            daily_dir.mkdir()
            # 20:31 UTC on June 18 is already June 19 in the repository's
            # canonical Beijing Daily timezone.
            daily_path = daily_dir / "2026-06-19.json"
            daily_path.write_text(
                json.dumps([self.record] if daily_has_record else []),
                encoding="utf-8",
            )
            seen_path = root / "seen.json"
            seen_path.write_text(
                json.dumps({"papers": {"oecd-test-record": dict(self.record)}}),
                encoding="utf-8",
            )
            retry_records = []
            if queue_target:
                retry_records.append(
                    {
                        "identity": f"url:{self.legacy_url.casefold()}",
                        "source_id": "oecd-working-papers",
                        "reasons": ["missing_abstract", "missing_authors", "weak_date_evidence"],
                        "first_seen": self.first_seen,
                        "historical_backfill": False,
                    }
                )
            (root / "metadata_retry_queue.json").write_text(
                json.dumps({"records": retry_records}),
                encoding="utf-8",
            )

            def enrich_from_official_detail(item, source, *, timeout):
                item["authors"] = ["Official OECD Author"]
                item["abstract"] = "Official OECD abstract supplied by the publication detail page."
                item["available_online"] = "2025-12-01"
                item["date_confidence"] = "A"
                return item

            argv = [
                "backfill_iza_authors.py",
                "--daily-dir",
                str(daily_dir),
                "--seen",
                str(seen_path),
                "--sources",
                str(root / "working_paper_sources.yml"),
                "--days",
                "7",
                "--limit",
                "20",
                "--timeout",
                "10",
            ]
            with (
                patch.object(backfill, "DATA_DIR", root),
                patch.object(backfill, "today_str", return_value="2026-09-09"),
                patch.object(
                    backfill,
                    "load_sources",
                    return_value=[
                        {
                            "id": "oecd-working-papers",
                            "title": "OECD Working Papers",
                            "type": "policy_paper",
                            "fields": ["general"],
                        }
                    ],
                ),
                patch.object(backfill, "enrich_record_from_detail", side_effect=enrich_from_official_detail),
                patch.object(sys, "argv", argv),
            ):
                backfill.main()

            repaired_daily = json.loads(daily_path.read_text(encoding="utf-8"))
            repaired_seen = json.loads(seen_path.read_text(encoding="utf-8"))
            self.assertEqual(len(repaired_daily), 1)
            return repaired_daily[0], repaired_seen["papers"]["oecd-test-record"]

    def test_oecd_is_in_scheduled_recent_working_paper_backfill(self):
        self.assertIn("oecd-working-papers", SCHEDULED_SOURCE_IDS)

    def test_queued_oecd_record_uses_beijing_first_discovery_bucket(self):
        repaired, seen = self._run_backfill(daily_has_record=True)
        self.assertEqual(repaired["authors"], ["Official OECD Author"])
        self.assertEqual(repaired["first_seen"], self.first_seen)
        self.assertEqual(
            repaired["url"],
            self.legacy_url.replace("www.oecd-ilibrary.org", "www.oecd.org"),
        )
        self.assertEqual(seen["first_seen"], self.first_seen)
        self.assertEqual(seen["authors"], ["Official OECD Author"])

    def test_seen_only_oecd_gap_missing_from_daily_is_restored_then_repaired(self):
        repaired, seen = self._run_backfill(daily_has_record=False, queue_target=False)
        self.assertEqual(repaired["id"], "oecd-test-record")
        self.assertEqual(repaired["first_seen"], self.first_seen)
        self.assertEqual(repaired["authors"], ["Official OECD Author"])
        self.assertIn("Official OECD abstract", repaired["abstract"])
        self.assertEqual(seen["first_seen"], self.first_seen)

    def test_seen_abstract_only_gap_is_not_promoted_into_bounded_restore_scope(self):
        abstract_only = dict(self.record)
        abstract_only["authors"] = ["Known OECD Author"]
        self.assertEqual(
            backfill.durable_oecd_seen_targets({"abstract-only": abstract_only}),
            {},
        )

    def test_seen_double_gap_is_selected_without_retry_queue(self):
        self.assertEqual(
            backfill.durable_oecd_seen_targets({"target": dict(self.record)}),
            {"2026-06-19": {f"url:{self.legacy_url.casefold()}"}},
        )


if __name__ == "__main__":
    unittest.main()
