"""N2 exact DOI publisher-verified Applied Economics abstracts.

Tests never request a publisher or print the copyrighted abstract text.
"""
from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_overrides  # noqa: E402


class AppliedEconomicsOperaOverrides(unittest.TestCase):
    # First-party Taylor & Francis "Published online" dates independently
    # observed in connected Opera on 2026-10-29; DOI and exact article
    # headings matched existing canonical records before overrides.
    EXPECTED = {
        "10.1080/00036846.2026.2750976": ("2026-10-29", "2026-10-29", 1223),
        "10.1080/00036846.2026.2753036": ("2026-10-29", "2026-10-28", 1394),
        "10.1080/00036846.2026.2747147": ("2026-10-28", "2026-10-28", 1202),
        "10.1080/00036846.2026.2750282": ("2026-10-29", "2026-10-29", 1159),
        "10.1080/00036846.2026.2754322": ("2026-10-28", "2026-10-28", 1508),
        "10.1080/00036846.2026.2753041": ("2026-10-28", "2026-10-28", 1572),
    }

    @classmethod
    def setUpClass(cls):
        cls.overrides = apply_overrides.load_overrides(ROOT / "data" / "manual_overrides.yml")
        cls.records = {
            record["doi"]: record
            for date in ("2026-10-28", "2026-10-29")
            for record in json.loads((ROOT / "data" / "daily" / f"{date}.json").read_text(encoding="utf-8"))
            if record.get("doi") in cls.EXPECTED
        }

    def assert_preserves_canonical_evidence(self, transformed, original):
        for key in (
            "first_seen", "available_online", "published_online",
            "date_source", "date_confidence", "doi", "title",
            "authors", "raw_data",
        ):
            self.assertEqual(transformed.get(key), original.get(key), key)

    def test_exact_six_original_daily_records_and_status(self):
        self.assertEqual(set(self.records), set(self.EXPECTED))
        for doi, (bucket, publisher_date, length) in self.EXPECTED.items():
            with self.subTest(doi=doi):
                override = self.overrides[doi]
                self.assertEqual(override["abstract_source"], "publisher_page_verified")
                self.assertEqual(len(override["abstract"]), length)
                original = self.records[doi]
                self.assertEqual(original["published_online"], publisher_date)
                self.assertEqual(original["first_seen"][:10], "2026-10-29")
                bucket_records = json.loads(
                    (ROOT / "data" / "daily" / f"{bucket}.json").read_text(encoding="utf-8")
                )
                self.assertIn(doi, {r.get("doi") for r in bucket_records})
                after = deepcopy(original)
                self.assertFalse(apply_overrides.apply_to_record(after, override))
                self.assertFalse(apply_overrides.apply_to_record(after, override))
                self.assertEqual(after["abstract"], override["abstract"])
                self.assertEqual(after["abstract_source"], "publisher_page_verified")
                self.assertEqual(after["abstract_status_code"], "available")
                self.assertEqual(after["abstract_completeness"], "full")
                self.assertEqual(after["abstract_enrichment_status"], "available")
                self.assertNotIn("abstract_status", after)
                self.assert_preserves_canonical_evidence(after, original)

    def test_exact_seen_ledger_recovery_and_identity_invariants(self):
        # Repository CI has the full seen.json; GitHub contents connector
        # cannot independently return its >40MB file.
        seen = json.loads((ROOT / "data" / "seen.json").read_text(encoding="utf-8"))
        for doi in self.EXPECTED:
            with self.subTest(doi=doi):
                record = seen["papers"][f"doi:{doi}"]
                before = deepcopy(record)
                after = deepcopy(record)
                self.assertFalse(apply_overrides.apply_to_record(after, self.overrides[doi]))
                self.assertEqual(after["abstract"], self.overrides[doi]["abstract"])
                self.assertEqual(after["abstract_source"], "publisher_page_verified")
                self.assertEqual(after["abstract_status_code"], "available")
                self.assertFalse(apply_overrides.apply_to_record(after, self.overrides[doi]))
                self.assert_preserves_canonical_evidence(after, before)


    def test_missing_record_replay_transitions_once_then_is_idempotent(self):
        # Separately exercise the original missing -> verified-full transition.
        # Canonical Daily and seen are now already applied; reapplication must
        # be a no-op. This cloned fixture must never write production files.
        for doi in self.EXPECTED:
            with self.subTest(doi=doi):
                original = self.records[doi]
                entry = deepcopy(original)
                for field in (
                    "abstract", "abstract_source", "abstract_status_code",
                    "abstract_completeness", "abstract_enrichment_status",
                ):
                    entry.pop(field, None)
                entry["abstract_status"] = "missing_retry"
                before = deepcopy(entry)
                self.assertTrue(apply_overrides.apply_to_record(entry, self.overrides[doi]))
                self.assertFalse(apply_overrides.apply_to_record(entry, self.overrides[doi]))
                self.assertEqual(entry["abstract"], self.overrides[doi]["abstract"])
                self.assertEqual(entry["abstract_source"], "publisher_page_verified")
                self.assertEqual(entry["abstract_status_code"], "available")
                self.assertEqual(entry["abstract_completeness"], "full")
                self.assertEqual(entry["abstract_enrichment_status"], "available")
                self.assertNotIn("abstract_status", entry)
                self.assert_preserves_canonical_evidence(entry, before)



if __name__ == "__main__":
    unittest.main()
