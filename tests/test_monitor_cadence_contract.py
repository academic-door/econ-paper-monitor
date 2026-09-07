from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MonitorCadenceContractTests(unittest.TestCase):
    def test_fast_lane_is_true_quarter_hour_with_full_slot_replacement(self) -> None:
        text = (ROOT / ".github" / "workflows" / "fast-discovery.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "0,15,45 * * * *"', text)
        self.assertIn('cron: "30 1-5,7-11,13-17,19-23 * * *"', text)
        self.assertIn("--tier hourly_crossref_priority", text)
        self.assertNotIn("DEEPSEEK_API_KEY", text)
        self.assertNotIn("should_run_monitor.py", text)

    def test_core_and_full_discovery_have_independent_schedules(self) -> None:
        text = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "7 * * * *"', text)
        for cron in (
            'cron: "30 18 * * *"',
            'cron: "30 0 * * *"',
            'cron: "30 6 * * *"',
            'cron: "30 12 * * *"',
        ):
            self.assertIn(cron, text)
        self.assertNotIn('cron: "*/15 * * * *"', text)
        self.assertNotIn("should_run_monitor.py", text)
        self.assertNotIn("DEEPSEEK_API_KEY", text)
        self.assertNotIn("scripts/translate.py", text)
        self.assertNotIn("scripts/ai_china_relevance.py", text)

    def test_ai_enrichment_is_independent_and_explicitly_uses_v4_flash(self) -> None:
        text = (ROOT / ".github" / "workflows" / "ai-enrichment.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "15 0,4,10,13,19 * * *"', text)
        self.assertIn("DEEPSEEK_MODEL: deepseek-v4-flash", text)
        self.assertIn("scripts/translate.py", text)
        self.assertIn("scripts/ai_china_relevance.py", text)
        self.assertIn("group: paper-monitor-main-writer", text)

    def test_all_writer_lanes_share_one_serial_writer_group(self) -> None:
        for workflow in ("fast-discovery.yml", "update.yml", "ai-enrichment.yml"):
            text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
            self.assertIn("group: paper-monitor-main-writer", text)
            self.assertIn("cancel-in-progress: false", text)


if __name__ == "__main__":
    unittest.main()
