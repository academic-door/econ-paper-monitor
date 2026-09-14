from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts import release_gate


class ReleaseGateFirstSeenAnchorTests(unittest.TestCase):
    def write_supporting_reports(self, root: Path) -> None:
        (root / "quality.json").write_text(json.dumps({"totals": {}}), encoding="utf-8")
        (root / "ingestion.json").write_text(
            json.dumps({"new_today_missing_candidates": 0, "raw_artifact_count": 1}),
            encoding="utf-8",
        )
        (root / "formal.json").write_text(
            json.dumps({"suspected_missed_journals": 0}), encoding="utf-8"
        )
        (root / "source.json").write_text(
            json.dumps(
                {
                    "checked_at": "2026-07-27T12:00:00+00:00",
                    "counts": {"degraded": 0, "unavailable": 0, "stale": 0},
                    "coverage_counts": {"crossref_only": 0},
                }
            ),
            encoding="utf-8",
        )

    def run_gate(self, root: Path) -> dict:
        return release_gate.run(
            Namespace(
                date="2026-07-27",
                daily_dir=root,
                quality_report=root / "quality.json",
                ingestion_audit=root / "ingestion.json",
                formal_audit=root / "formal.json",
                source_health=root / "source.json",
                max_historical_days=14,
            )
        )

    def old_cepr_record(self, *, first_seen: str) -> dict:
        return {
            "id": "url:e3b3e26fe5ebdeb8",
            "title": "Riders on the Storm",
            "url": "https://cepr.org/publications/dp13978",
            "source": "working_papers",
            "source_id": "cepr-dp",
            "source_type": "working_paper",
            "available_online": "2019-09-02",
            "published_online": "2019-09-02",
            "date_confidence": "A",
            "first_seen": first_seen,
        }

    def test_matching_beijing_first_seen_anchor_exempts_old_official_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 2026-07-26 16:30 UTC is 2026-07-27 in the canonical Beijing Daily bucket.
            record = self.old_cepr_record(first_seen="2026-07-26T16:30:00+00:00")
            (root / "2026-07-27.json").write_text(json.dumps([record]), encoding="utf-8")
            self.write_supporting_reports(root)

            report = self.run_gate(root)

            self.assertTrue(report["ok"])
            self.assertNotIn(
                "historical_records_in_today",
                {item["code"] for item in report["failures"]},
            )

    def test_mismatched_first_seen_does_not_exempt_old_official_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = self.old_cepr_record(first_seen="2026-07-25T12:00:00+00:00")
            (root / "2026-07-27.json").write_text(json.dumps([record]), encoding="utf-8")
            self.write_supporting_reports(root)

            report = self.run_gate(root)

            self.assertFalse(report["ok"])
            self.assertIn(
                "historical_records_in_today",
                {item["code"] for item in report["failures"]},
            )

    def test_anchor_does_not_exempt_explicit_historical_cepr_catalogue_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = self.old_cepr_record(first_seen="2026-07-26T16:30:00+00:00")
            record["url"] = "https://cepr.org/publications/dp9999"
            record["id"] = "url:historical-cepr-dp9999"
            (root / "2026-07-27.json").write_text(json.dumps([record]), encoding="utf-8")
            self.write_supporting_reports(root)

            report = self.run_gate(root)

            self.assertFalse(report["ok"])
            self.assertIn(
                "historical_records_in_today",
                {item["code"] for item in report["failures"]},
            )


if __name__ == "__main__":
    unittest.main()
