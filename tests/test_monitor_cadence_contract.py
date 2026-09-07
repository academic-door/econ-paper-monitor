from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import should_dispatch_monitor  # noqa: E402


class MonitorCadenceContractTests(unittest.TestCase):
    def test_fast_lane_is_true_quarter_hour_with_full_slot_replacement(self) -> None:
        text = (ROOT / ".github" / "workflows" / "fast-discovery.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "15,30,45 * * * *"', text)
        self.assertIn('cron: "0 0-3,5-9,11-15,17-21,23 * * *"', text)
        self.assertIn("--tier hourly_crossref_priority", text)
        self.assertIn("git add data/seen.json data/daily", text)
        self.assertNotIn("git add data\n", text)
        self.assertNotIn("DEEPSEEK_API_KEY", text)
        self.assertNotIn("should_run_monitor.py", text)

    def test_core_and_full_discovery_have_independent_schedules(self) -> None:
        text = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "7 0-3,5-9,11-15,17-21,23 * * *"', text)
        for cron in (
            'cron: "0 16 * * *"',
            'cron: "0 22 * * *"',
            'cron: "0 4 * * *"',
            'cron: "0 10 * * *"',
        ):
            self.assertIn(cron, text)
        self.assertIn("Beijing 00:00, 06:00, 12:00, and 18:00", text)
        self.assertNotIn('cron: "*/15 * * * *"', text)
        self.assertNotIn("should_run_monitor.py", text)
        self.assertNotIn("DEEPSEEK_API_KEY", text)
        self.assertNotIn("scripts/translate.py", text)
        self.assertNotIn("scripts/ai_china_relevance.py", text)

    def test_watchdog_is_fallback_not_second_hourly_lane(self) -> None:
        text = (ROOT / ".github" / "workflows" / "watchdog.yml").read_text(encoding="utf-8")
        self.assertIn("--light-min-minutes 75", text)
        self.assertIn("--full-times 00:00,06:00,12:00,18:00", text)
        self.assertIn("--full-grace-minutes 15", text)
        self.assertIn("Dispatch core monitor fallback", text)
        self.assertIn("FAST_MAX_AGE_MINUTES=40", text)
        self.assertIn("--workflow fast-discovery.yml", text)
        self.assertIn("Dispatch fast discovery fallback", text)
        self.assertIn("gh workflow run fast-discovery.yml", text)
        self.assertIn('.status == "pending"', text)
        self.assertIn('.status == "requested"', text)

    def test_full_watchdog_grace_preserves_original_slot_for_freshness(self) -> None:
        windows = should_dispatch_monitor.parse_full_times("00:00,06:00,12:00,18:00")
        before_grace = datetime(2026, 9, 7, 0, 14, tzinfo=should_dispatch_monitor.BEIJING)
        at_grace = datetime(2026, 9, 7, 0, 15, tzinfo=should_dispatch_monitor.BEIJING)
        self.assertIsNone(should_dispatch_monitor.latest_due_full_window(before_grace, windows, 15))
        due = should_dispatch_monitor.latest_due_full_window(at_grace, windows, 15)
        self.assertIsNotNone(due)
        self.assertEqual(due.hour, 0)
        self.assertEqual(due.minute, 0)

    def test_full_run_is_valid_core_freshness_evidence(self) -> None:
        light = datetime(2026, 9, 7, 3, 7, tzinfo=UTC)
        full = datetime(2026, 9, 7, 4, 32, tzinfo=UTC)
        self.assertEqual(should_dispatch_monitor.latest_discovery_finish(light, full), full)

    def test_ai_enrichment_is_independent_and_explicitly_uses_v4_flash(self) -> None:
        text = (ROOT / ".github" / "workflows" / "ai-enrichment.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "15 5,11,17,23 * * *"', text)
        self.assertIn("DEEPSEEK_MODEL: deepseek-v4-flash", text)
        self.assertIn("scripts/translate.py", text)
        self.assertIn("scripts/ai_china_relevance.py", text)
        self.assertIn("git add data/seen.json data/daily data/translation_cache.json data/china_relevance_cache.json data/ai_cost_usage.json", text)
        self.assertNotIn("git add data\n", text)
        self.assertIn("group: paper-monitor-main-writer", text)

    def test_all_writer_lanes_share_one_serial_writer_group(self) -> None:
        for workflow in ("fast-discovery.yml", "update.yml", "ai-enrichment.yml"):
            text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
            self.assertIn("group: paper-monitor-main-writer", text)
            self.assertIn("cancel-in-progress: false", text)


if __name__ == "__main__":
    unittest.main()
