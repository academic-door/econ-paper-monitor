import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import render_site  # noqa: E402


class PublicDateViewTests(unittest.TestCase):
    def test_seen_only_record_does_not_return_to_first_seen_day(self) -> None:
        record = {
            "_daily_date": "2026-07-27",
            "_from_seen_only": True,
            "first_seen": "2026-07-27T10:00:00+00:00",
            "available_online": "2025-06-04",
            "published_online": "2025-06-04",
        }

        self.assertFalse(render_site.record_is_on_date(record, "2026-07-27"))

    def test_seen_only_record_can_appear_when_officially_online_today(self) -> None:
        record = {
            "_daily_date": "2026-07-27",
            "_from_seen_only": True,
            "first_seen": "2026-07-27T10:00:00+00:00",
            "available_online": "2026-07-27",
            "published_online": "2026-07-27",
            "date_source": "publisher_detail",
            "date_confidence": "B",
        }

        self.assertTrue(render_site.record_is_on_date(record, "2026-07-27"))

    def test_recent72_excludes_old_catalogue_backfill(self) -> None:
        record = {
            "detected_at": "2026-07-27T10:00:00+00:00",
            "available_online": "2025-06-04",
            "published_online": "2025-06-04",
            "title": "Banks vs. Firms: Who Benefits from Credit Guarantees?",
        }

        self.assertEqual(render_site.recent_detected_records([record], 3), [])

    def test_crossref_metadata_date_is_not_presented_as_official_online(self) -> None:
        record = {
            "available_online": "2026-07-28",
            "published_online": "2026-07-28",
            "date_source": "crossref_doi_published_online",
            "date_confidence": "C",
        }
        self.assertIn("Crossref 元数据日期", render_site.public_date_line(record))
        self.assertEqual(render_site.detection_lag_days(record), None)

    def test_crossref_date_cannot_resurrect_seen_record_into_today(self) -> None:
        record = {
            "_from_seen_only": True,
            "first_seen": "2026-07-27T10:00:00+00:00",
            "available_online": "2026-07-28",
            "published_online": "2026-07-28",
            "date_source": "crossref_doi_elsevier_created_online",
            "date_confidence": "C",
        }
        self.assertFalse(render_site.record_is_on_date(record, "2026-07-28"))

    def test_acceptance_date_is_displayed_but_not_treated_as_online_date(self) -> None:
        record = {
            "accepted_date": "2026-07-17",
            "detected_at": "2026-07-20T08:00:00+00:00",
            "date_source": "publisher_detail",
            "date_confidence": "A",
        }
        self.assertEqual(render_site.official_date(record), "")
        self.assertIn("接受日期 2026-07-17", render_site.public_date_line(record))


if __name__ == "__main__":
    unittest.main()


    def test_confidence_labels_follow_policy_a(self) -> None:
        expected = {
            "A": "A：官方渠道明确日期",
            "B": "B：官方渠道备选/推断日期",
            "C": "C：Crossref/聚合登记日期",
            "D": "D：卷期/印刷日期",
            "F": "F：仅本站首次发现",
        }
        for value, label in expected.items():
            self.assertEqual(render_site.confidence_label(value), label)


    def test_confidence_labels_follow_policy_a(self) -> None:
        expected = {
            "A": "A：官方渠道明确日期",
            "B": "B：官方渠道备选/推断日期",
            "C": "C：Crossref/聚合登记日期",
            "D": "D：卷期/印刷日期",
            "F": "F：仅本站首次发现",
        }
        for value, label in expected.items():
            self.assertEqual(render_site.confidence_label(value), label)
