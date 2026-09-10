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


class OecdScheduledBackfillTests(unittest.TestCase):
    def test_oecd_is_in_scheduled_recent_working_paper_backfill(self):
        self.assertIn("oecd-working-papers", SCHEDULED_SOURCE_IDS)

    def test_queued_oecd_record_outside_recent_window_is_repaired_in_original_bucket(self):
        legacy_url = (
            "https://www.oecd-ilibrary.org/en/publications/"
            "mapping-drought-severity-in-mexico-using-high-resolution-satellite-data_f2a165e7-en.html"
        )
        first_seen = "2026-06-18T20:31:17+00:00"
        record = {
            "id": "oecd-test-record",
            "source_id": "oecd-working-papers",
            "source_type": "policy_paper",
            "title": "Mapping drought severity in Mexico using high-resolution satellite data",
            "url": legacy_url,
            "authors": [],
            "abstract": "",
            "first_seen": first_seen,
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily_dir = root / "daily"
            daily_dir.mkdir()
            daily_path = daily_dir / "2026-06-18.json"
            daily_path.write_text(json.dumps([record]), encoding="utf-8")
            seen_path = root / "seen.json"
            seen_path.write_text(
                json.dumps({"papers": {"oecd-test-record": dict(record)}}),
                encoding="utf-8",
            )
            (root / "metadata_retry_queue.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "identity": f"url:{legacy_url.casefold()}",
                                "source_id": "oecd-working-papers",
                                "reasons": ["missing_abstract", "missing_authors", "weak_date_evidence"],
                                "first_seen": first_seen,
                                "historical_backfill": False,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def enrich_from_official_detail(item, source, *, timeout):
                item["authors"] = ["Official OECD Author"]
                item["abstract"] = "Official OECD abstract supplied by the publication detail page."
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

            repaired = json.loads(daily_path.read_text(encoding="utf-8"))[0]
            self.assertEqual(repaired["authors"], ["Official OECD Author"])
            self.assertEqual(repaired["first_seen"], first_seen)
            self.assertEqual(
                repaired["url"],
                legacy_url.replace("www.oecd-ilibrary.org", "www.oecd.org"),
            )


if __name__ == "__main__":
    unittest.main()
