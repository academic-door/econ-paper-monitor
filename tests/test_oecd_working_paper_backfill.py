"""Tests for OECD scheduled recent working-paper metadata repair."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

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


class OecdScheduledBackfillTests(unittest.TestCase):
    def test_oecd_is_in_scheduled_recent_working_paper_backfill(self):
        self.assertIn("oecd-working-papers", SCHEDULED_SOURCE_IDS)


if __name__ == "__main__":
    unittest.main()
