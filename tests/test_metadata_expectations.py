"""Tests for content-aware metadata trust and retry classification."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_metadata_retry_queue import queue_item  # noqa: E402
from metadata_expectations import expected_missing_reason, normalized_title  # noqa: E402
from product_audit import audit  # noqa: E402


def record(title: str, **overrides) -> dict:
    base = {
        "id": f"id:{title}",
        "title": title,
        "doi": "10.1/test",
        "url": "https://example.com/test",
        "journal": "Test Journal",
        "journal_id": "test-j",
        "source": "crossref",
        "source_type": "journal",
        "authors": [],
        "abstract": "",
        "date_confidence": "A",
        "date_source": "publisher_published_online",
        "_daily_date": "2026-09-01",
        "available_online": "2026-09-01",
        "published_online": "2026-09-01",
        "issue_date": None,
        "first_seen": "2026-09-01T12:00:00+00:00",
        "fields": [],
    }
    base.update(overrides)
    return base


class MetadataExpectationTests(unittest.TestCase):
    def test_normalizes_html_and_whitespace(self):
        item = record("<i>Journal of Economic Literature</i>\n , September 2026, Volume LXIV, Number 3")
        self.assertEqual(
            normalized_title(item),
            "Journal of Economic Literature , September 2026, Volume LXIV, Number 3",
        )

    def test_reviewer_acknowledgement_expects_abstract_and_authors_missing(self):
        item = record("Food Policy Outstanding Reviewer Appreciation", journal="Food Policy")
        self.assertEqual(expected_missing_reason(item, "abstract"), "reviewer_acknowledgement")
        self.assertEqual(expected_missing_reason(item, "authors"), "reviewer_acknowledgement")

    def test_editor_announcement_expects_abstract_and_authors_missing(self):
        item = record(
            "Announcing new JCE editors and new journal subtitle, journal of comparative economics: Political economy",
            journal="Journal of Comparative Economics",
        )
        self.assertEqual(expected_missing_reason(item, "abstract"), "editorial_announcement")
        self.assertEqual(expected_missing_reason(item, "authors"), "editorial_announcement")

    def test_issue_front_matter_expects_abstract_and_authors_missing(self):
        item = record(
            "<i>Journal of Economic Literature</i>, September 2026, Volume LXIV, Number 3",
            journal="Journal of Economic Literature",
        )
        self.assertEqual(expected_missing_reason(item, "abstract"), "issue_front_matter")
        self.assertEqual(expected_missing_reason(item, "authors"), "issue_front_matter")

    def test_book_review_only_makes_abstract_optional(self):
        item = record(
            "The Laissez-Faire Experiment: Why Britain Embraced and Then Abandoned Small Government, 1800–1914 by W. Walker Hanlon",
            journal="Journal of Economic Literature",
        )
        self.assertEqual(expected_missing_reason(item, "abstract"), "book_review")
        self.assertIsNone(expected_missing_reason(item, "authors"))

    def test_corrigendum_only_makes_abstract_optional(self):
        item = record("Corrigendum to ‘A research article’", journal="World Development")
        self.assertEqual(expected_missing_reason(item, "abstract"), "correction_notice")
        self.assertIsNone(expected_missing_reason(item, "authors"))

    def test_special_issue_introduction_only_makes_abstract_optional(self):
        item = record(
            "Introduction to the Special Issue Honoring Michel Juillard’s Contribution to Macroeconomics",
            journal="Journal of Economic Dynamics and Control",
        )
        self.assertEqual(expected_missing_reason(item, "abstract"), "special_issue_introduction")
        self.assertIsNone(expected_missing_reason(item, "authors"))

    def test_editorial_introduction_only_makes_abstract_optional(self):
        item = record("Editor's Introduction", journal="Journal of Money, Credit and Banking")
        self.assertEqual(expected_missing_reason(item, "abstract"), "editorial_introduction")
        self.assertIsNone(expected_missing_reason(item, "authors"))

    def test_editor_report_only_makes_abstract_optional(self):
        item = record("Economic Inquiry 2025 Editor's Report", journal="Economic Inquiry")
        self.assertEqual(expected_missing_reason(item, "abstract"), "editor_report")
        self.assertIsNone(expected_missing_reason(item, "authors"))

    def test_quoted_discussion_only_makes_abstract_optional(self):
        item = record(
            "Discussion of “Connectivity and Selective Rural Migration”",
            journal="International Economic Review",
        )
        self.assertEqual(expected_missing_reason(item, "abstract"), "discussion_article")
        self.assertIsNone(expected_missing_reason(item, "authors"))

    def test_public_choice_explicit_book_review_only_makes_abstract_optional(self):
        item = record(
            "Public choice book review for FDR: a new political life, by David Beito (2025, Carus Books)",
            journal="Public Choice",
        )
        self.assertEqual(expected_missing_reason(item, "abstract"), "book_review")
        self.assertIsNone(expected_missing_reason(item, "authors"))

    def test_similar_research_titles_remain_actionable(self):
        titles = [
            "Introduction of a New Monetary Policy Instrument",
            "Discussion of Market Power in Digital Platforms",
            "Editor Networks and Reporting Quality",
            "Book Reviews and Household Reading Demand",
            "Special Issue Effects on Citation Behavior",
        ]
        for title in titles:
            with self.subTest(title=title):
                item = record(title, journal="Public Choice")
                self.assertIsNone(expected_missing_reason(item, "abstract"))
                self.assertIsNone(expected_missing_reason(item, "authors"))

    def test_research_record_remains_actionable(self):
        item = record("Market Inefficiencies in Renewable Support Policies")
        self.assertIsNone(expected_missing_reason(item, "abstract"))
        self.assertIsNone(expected_missing_reason(item, "authors"))


class ProductAuditTrustTests(unittest.TestCase):
    def _totals(self, item: dict) -> dict:
        return audit([item], {"test-j"})["totals"]

    def test_research_gap_is_actionable(self):
        totals = self._totals(record("A research article"))
        self.assertEqual(totals["missing_abstract_recent"], 1)
        self.assertEqual(totals["missing_abstract_recent_actionable"], 1)
        self.assertEqual(totals["missing_abstract_recent_expected"], 0)
        self.assertEqual(totals["missing_authors_recent_actionable"], 1)
        self.assertEqual(totals["missing_authors_recent_expected"], 0)

    def test_acknowledgement_gap_is_expected_but_raw_count_is_preserved(self):
        totals = self._totals(record("Food Policy Outstanding Reviewer Appreciation", journal="Food Policy"))
        self.assertEqual(totals["missing_abstract_recent"], 1)
        self.assertEqual(totals["missing_abstract_recent_actionable"], 0)
        self.assertEqual(totals["missing_abstract_recent_expected"], 1)
        self.assertEqual(totals["missing_authors_recent"], 1)
        self.assertEqual(totals["missing_authors_recent_actionable"], 0)
        self.assertEqual(totals["missing_authors_recent_expected"], 1)

    def test_book_review_keeps_missing_author_actionable(self):
        totals = self._totals(
            record(
                "Mission and Margin: A Practical Guide to University Finances by Daniel Diermeier and Brett C. Sweet",
                journal="Journal of Economic Literature",
            )
        )
        self.assertEqual(totals["missing_abstract_recent_expected"], 1)
        self.assertEqual(totals["missing_authors_recent_actionable"], 1)


class RetryQueueTrustTests(unittest.TestCase):
    anchor = date(2026, 9, 7)
    recent_start = date(2026, 8, 9)

    def test_expected_only_gaps_do_not_enter_queue(self):
        item = record("Food Policy Outstanding Reviewer Appreciation", journal="Food Policy")
        self.assertIsNone(queue_item(item, self.anchor, self.recent_start))

    def test_expected_gaps_remain_transparent_when_weak_date_is_actionable(self):
        item = record(
            "Food Policy Outstanding Reviewer Appreciation",
            journal="Food Policy",
            date_confidence="C",
        )
        queued = queue_item(item, self.anchor, self.recent_start)
        self.assertIsNotNone(queued)
        self.assertEqual(queued["reasons"], ["weak_date_evidence"])
        self.assertEqual(
            queued["expected_metadata_gaps"],
            {
                "abstract": "reviewer_acknowledgement",
                "authors": "reviewer_acknowledgement",
            },
        )

    def test_book_review_still_retries_missing_reviewer_author(self):
        item = record(
            "Mission and Margin: A Practical Guide to University Finances by Daniel Diermeier and Brett C. Sweet",
            journal="Journal of Economic Literature",
        )
        queued = queue_item(item, self.anchor, self.recent_start)
        self.assertIsNotNone(queued)
        self.assertEqual(queued["reasons"], ["missing_authors"])
        self.assertEqual(queued["expected_metadata_gaps"], {"abstract": "book_review"})

    def test_research_gap_keeps_both_retry_reasons(self):
        queued = queue_item(record("A research article"), self.anchor, self.recent_start)
        self.assertIsNotNone(queued)
        self.assertEqual(queued["reasons"], ["missing_abstract", "missing_authors"])
        self.assertNotIn("expected_metadata_gaps", queued)


if __name__ == "__main__":
    unittest.main()
