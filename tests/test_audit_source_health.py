"""Unit tests for the formal source-health decision logic."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_source_health  # noqa: E402


NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
TODAY = date(2026, 8, 5)
JOURNAL = {"id": "test-j", "title": "Test Journal", "publisher": "Test Press"}
# A journal that belongs to the priority-toc target set.
PRIORITY_JOURNAL = {"id": "quarterly-journal-of-economics", "title": "Quarterly Journal of Economics", "publisher": "Oxford University Press"}


def registry_entry(rss_status="none", rss_count=0, rss_error=None, crossref_status="ok"):
    return {
        "last_rss_status": rss_status,
        "last_rss_count": rss_count,
        "last_rss_error": rss_error,
        "last_crossref_status": crossref_status,
        "last_checked_at": "2026-08-05",
        "updated_at": "2026-08-05T10:00:00+00:00",
    }


def make_status(priority_state=None, journal_id="test-j"):
    sources = {}
    if priority_state is not None:
        sources["priority-toc"] = {
            "ok": True,
            "updated_at": "2026-08-05T10:00:00+00:00",
            "journals": {journal_id: priority_state},
        }
    return {"sources": sources}


class InspectJournalTests(unittest.TestCase):
    def _row(self, entry=None, status=None, journal=JOURNAL, journal_id="test-j"):
        registry = {"journals": {journal_id: entry or registry_entry()}}
        return audit_source_health.inspect_journal(journal, registry, TODAY, NOW, 36, status or make_status())

    def test_rss_and_crossref_healthy(self):
        row = self._row(registry_entry(rss_status="configured", rss_count=20))
        self.assertEqual(row["level"], "healthy")

    def test_empty_configured_rss_is_not_reliable(self):
        row = self._row(registry_entry(rss_status="configured", rss_count=0))
        self.assertEqual(row["level"], "degraded")
        self.assertNotIn("rss", row["usable_paths"])
        self.assertEqual(row["degradation_reason"], "single_path")

    def test_failed_tertiary_path_keeps_healthy_with_two_reliable(self):
        entry = registry_entry(rss_status="configured", rss_count=20)
        status = make_status({"count": 3, "ok": True, "publisher_ok": False}, journal_id="quarterly-journal-of-economics")
        row = self._row(entry, status, journal=PRIORITY_JOURNAL, journal_id="quarterly-journal-of-economics")
        self.assertEqual(row["level"], "healthy")
        self.assertTrue(row["failed_paths"])

    def test_failed_tertiary_path_with_single_reliable_is_degraded(self):
        # review-of-economic-studies is in the priority-toc set but not in the
        # supplemental-closure list, so a blocked publisher page still degrades it.
        journal = {"id": "review-of-economic-studies", "title": "Review of Economic Studies", "publisher": "Oxford University Press"}
        status = make_status({"count": 3, "ok": True, "publisher_ok": False}, journal_id="review-of-economic-studies")
        row = self._row(registry_entry(), status, journal=journal, journal_id="review-of-economic-studies")
        self.assertEqual(row["level"], "degraded")
        self.assertEqual(row["degradation_reason"], "failed_path")

    def test_crossref_only_is_degraded(self):
        row = self._row(registry_entry())
        self.assertEqual(row["level"], "degraded")
        self.assertEqual(row["degradation_reason"], "single_path")

    def test_crossref_only_with_supplemental_closure_is_closed_not_degraded(self):
        entry = registry_entry()
        journal = {"id": "review-of-economics-and-statistics", "title": "Review of Economics and Statistics", "publisher": "MIT Press"}
        row = self._row(entry, journal=journal, journal_id="review-of-economics-and-statistics")
        self.assertEqual(row["level"], "supplemental-closed")
        self.assertIsNotNone(row["supplemental_closed_note"])

    def test_crossref_only_without_closure_is_degraded(self):
        row = self._row(registry_entry())
        self.assertEqual(row["level"], "degraded")
        self.assertIsNone(row["supplemental_closed_note"])

    def test_springer_ci_blocked_rss_is_closed_not_degraded(self):
        # public-choice is in SUPPLEMENTAL_CLOSED_NOTES; when its RSS fails in
        # CI and only Crossref works, it must be supplemental-closed, not degraded.
        entry = registry_entry(rss_status="configured", rss_count=0, rss_error="ParseError: not well-formed")
        journal = {"id": "public-choice", "title": "Public Choice", "publisher": "Springer-Verlag"}
        row = self._row(entry, journal=journal, journal_id="public-choice")
        self.assertEqual(row["level"], "supplemental-closed")
        self.assertIsNotNone(row["supplemental_closed_note"])

    def test_batch1_ci_blocked_rss_is_closed_not_degraded(self) -> None:
        cases = {
            "management-science": ("HTTPError: HTTP Error 403: Forbidden", "Management Science", "INFORMS"),
            "journal-of-business-and-economic-statistics": ("HTTPError: HTTP Error 403: Forbidden", "Journal of Business & Economic Statistics", "Taylor & Francis"),
            "review-of-accounting-studies": ("ParseError: not well-formed", "Review of Accounting Studies", "Springer"),
            "journal-of-risk-and-uncertainty": ("ParseError: not well-formed", "Journal of Risk and Uncertainty", "Springer"),
        }
        for journal_id, (rss_error, title, publisher) in cases.items():
            with self.subTest(journal_id=journal_id):
                entry = registry_entry(rss_status="configured", rss_count=0, rss_error=rss_error)
                journal = {"id": journal_id, "title": title, "publisher": publisher}
                row = self._row(entry, journal=journal, journal_id=journal_id)
                self.assertEqual(row["level"], "supplemental-closed")
                self.assertIsNotNone(row["supplemental_closed_note"])


if __name__ == "__main__":
    unittest.main()

    def test_local_uchicago_status_marks_uchicago_healthy(self):
        journal = {"id": "journal-of-political-economy", "title": "Journal of Political Economy", "publisher": "The University of Chicago Press"}
        registry = {"journals": {"journal-of-political-economy": registry_entry()}}
        local = {"ok": True, "updated_at": "2026-08-05T10:00:00+00:00"}
        row = audit_source_health.inspect_journal(journal, registry, TODAY, NOW, 36, make_status(), local)
        self.assertEqual(row["level"], "healthy")
        self.assertIn("uchicago-local", row["usable_paths"])
        self.assertEqual(row["coverage"], "official_or_specialized")

    def test_stale_local_uchicago_status_stays_supplemental_closed(self):
        journal = {"id": "journal-of-political-economy", "title": "Journal of Political Economy", "publisher": "The University of Chicago Press"}
        registry = {"journals": {"journal-of-political-economy": registry_entry()}}
        local = {"ok": True, "updated_at": "2026-08-01T10:00:00+00:00"}
        row = audit_source_health.inspect_journal(journal, registry, TODAY, NOW, 36, make_status(), local)
        self.assertEqual(row["level"], "supplemental-closed")
        self.assertNotIn("uchicago-local", row["usable_paths"])


def test_jhr_is_priority_toc_and_supplemental_closed() -> None:
    assert "journal-of-human-resources" in audit_source_health.PRIORITY_TOC_JOURNALS
    assert "journal-of-human-resources" in audit_source_health.SUPPLEMENTAL_CLOSED_NOTES


def test_batch1_ci_blocked_journals_have_supplemental_closure() -> None:
    expected = {
        "management-science",
        "journal-of-business-and-economic-statistics",
        "review-of-accounting-studies",
        "journal-of-risk-and-uncertainty",
    }
    assert expected.issubset(audit_source_health.SUPPLEMENTAL_CLOSED_NOTES)
