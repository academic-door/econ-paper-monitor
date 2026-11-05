"""Verify two publisher bylines without ever using WordPress editor as author."""
from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_overrides  # noqa: E402


class OfficialRestudAuthorsTests(unittest.TestCase):
    def test_exact_first_party_author_allowlist(self):
        overrides = apply_overrides.load_overrides(ROOT / "data" / "manual_overrides.yml")
        expected = {
            "url:550f02ebc50f31c0": ["S. Nageeb Ali", "Andreas Kleiner", "Kun Zhang"],
            "url:97a35b87fb1e51e3": ["Hanna Wang"],
        }
        for key, byline in expected.items():
            self.assertEqual(overrides[key]["authors"], byline)
            self.assertNotIn("Ann", byline)

    def test_actual_daily_and_seen_identity_and_first_seen_are_preserved(self):
        overrides = apply_overrides.load_overrides(ROOT / "data" / "manual_overrides.yml")
        import json
        daily = json.loads((ROOT / "data" / "daily" / "2026-11-02.json").read_text(encoding="utf-8"))
        matches = [row for row in daily if row.get("id") in {
            "url:550f02ebc50f31c0", "url:97a35b87fb1e51e3"
        }]
        self.assertEqual(len(matches), 2)
        for original in matches:
            with self.subTest(id=original["id"]):
                entry = deepcopy(original)
                first_seen = entry["first_seen"]
                available_online = entry["available_online"]
                record_id = entry["id"]
                self.assertTrue(apply_overrides.apply_to_record(entry, overrides[record_id]))
                self.assertFalse(apply_overrides.apply_to_record(entry, overrides[record_id]))
                self.assertEqual(entry["authors_status_code"], "available")
                self.assertFalse(entry.get("authors_status"))
                self.assertEqual(entry["first_seen"], first_seen)
                self.assertEqual(entry["available_online"], available_online)
                self.assertEqual(entry["id"], record_id)
                seen = {"id": record_id, "first_seen": first_seen, "authors": []}
                self.assertTrue(apply_overrides.apply_to_record(seen, overrides[record_id]))
                self.assertEqual(seen["first_seen"], first_seen)
                self.assertEqual(seen["authors"], entry["authors"])


if __name__ == "__main__":
    unittest.main()
