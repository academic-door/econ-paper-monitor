"""Regression tests for bounded cleanup of historical OECD mirror contamination."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import backfill_iza_authors as backfill  # noqa: E402


class OecdHistoricalContaminationTests(unittest.TestCase):
    def setUp(self):
        self.url = (
            "https://www.oecd.org/en/publications/"
            "mapping-drought-severity-in-mexico-using-high-resolution-satellite-data_f2a165e7-en.html"
        )
        self.body = (
            "This paper analyses drought severity across Mexican regions between 2000 and 2025 "
            "using satellite-based indicators of vegetation health and surface moisture. The study "
            "provides a consistent high-resolution measure of drought intensity for climate policy."
        )
        self.polluted = (
            self.body
            + " In the same series See all publications Working paper The climate and adaptation "
            "spatial general equilibrium model 4 September 2026 89 Pages"
        )

    def test_polluted_proxy_abstract_is_bounded_repair_target(self):
        record = {
            "id": "url:205e5e9de6ae0278",
            "source": "working_papers",
            "source_id": "oecd-working-papers",
            "source_type": "policy_paper",
            "title": "Mapping drought severity in Mexico using high-resolution satellite data",
            "url": self.url,
            "authors": ["Ilyes Boumahdi", "Alberto González Pandiella"],
            "abstract": self.polluted,
            "abstract_source": "oecd_official_page_proxy",
            "first_seen": "2026-06-18T20:31:17+00:00",
            "date_confidence": "F",
            "doi": "10.1787/f2a165e7-en",
        }

        self.assertTrue(backfill.needs_scheduled_repair(record, "oecd-working-papers"))
        self.assertEqual(
            backfill.durable_oecd_seen_targets({"target": record}),
            {"2026-06-19": {f"url:{self.url.casefold()}"}},
        )

if __name__ == "__main__":
    unittest.main()
