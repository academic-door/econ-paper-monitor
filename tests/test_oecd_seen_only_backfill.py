"""Regression tests for OECD records that exist only in the durable seen ledger."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import backfill_iza_authors as backfill  # noqa: E402


class OecdSeenOnlyBackfillTests(unittest.TestCase):
    def test_legacy_seen_only_oecd_gap_is_repaired_without_creating_daily_record(self):
        legacy_url = (
            "https://www.oecd-ilibrary.org/en/publications/"
            "mapping-drought-severity-in-mexico-using-high-resolution-satellite-data_f2a165e7-en.html"
        )
        first_seen = "2026-06-18T20:31:17+00:00"
        record = {
            "id": "url:205e5e9de6ae0278",
            "source": "working_papers",
            "source_id": "oecd-working-papers",
            "source_type": "policy_paper",
            "title": "Mapping drought severity in Mexico using high-resolution satellite data",
            "url": legacy_url,
            "authors": [],
            "abstract": "",
            "abstract_completeness": "missing",
            "first_seen": first_seen,
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily_dir = root / "daily"
            daily_dir.mkdir()
            seen_path = root / "seen.json"
            seen_path.write_text(
                json.dumps({"papers": {record["id"]: dict(record)}}),
                encoding="utf-8",
            )
            # Deliberately empty: this record has aged out of the rolling retry queue.
            (root / "metadata_retry_queue.json").write_text(
                json.dumps({"records": []}),
                encoding="utf-8",
            )

            def enrich_from_official_detail(item, source, *, timeout):
                item["authors"] = ["Official OECD Author"]
                item["abstract"] = (
                    "Official OECD abstract supplied by the publication detail page and long enough "
                    "to represent the authoritative full abstract for this regression test."
                )
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
                patch.object(backfill, "today_str", return_value="2026-09-10"),
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

            repaired = json.loads(seen_path.read_text(encoding="utf-8"))["papers"][record["id"]]
            self.assertEqual(repaired["authors"], ["Official OECD Author"])
            self.assertGreater(len(repaired["abstract"]), 80)
            self.assertEqual(repaired["abstract_completeness"], "full")
            self.assertEqual(repaired["first_seen"], first_seen)
            self.assertEqual(
                repaired["url"],
                legacy_url.replace("www.oecd-ilibrary.org", "www.oecd.org"),
            )
            self.assertEqual(list(daily_dir.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
