"""JFQA official full-abstract RSS path: source identity, provenance and dedupe."""
from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dedupe  # noqa: E402
import fetch_rss  # noqa: E402
from sources.jfqa_official import verified_forthcoming_abstract  # noqa: E402


JFQA = {
    "id": "journal-of-financial-and-quantitative-analysis",
    "title": "Journal of Financial and Quantitative Analysis",
    "short_name": "JFQA",
    "publisher": "Cambridge University Press",
}

PARAGRAPH = (
    "This article examines individual equity investment across family birth order. "
    "We combine a large household dataset with direct measures of participation "
    "and portfolio decisions, analyze differences by sibling position, and "
    "evaluate competing explanations for the observed behavior."
)


def xml_fixture(encoded: str | None = None) -> str:
    suffix = (
        "<content:encoded><![CDATA[" + encoded + "]]></content:encoded>"
        if encoded is not None else ""
    )
    return (
        '<?xml version="1.0"?><rss version="2.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        "<channel><title>Forthcoming Articles – JFQA</title>"
        "<item><title>Individuals’ Equity Investment and Birth Order</title>"
        "<link>https://jfqa.org/2026/10/31/individuals-equity-investment-and-birth-order/</link>"
        "<dc:creator>JFQA Admin</dc:creator>"
        "<pubDate>Sat, 31 Oct 2026 18:50:21 +0000</pubDate>"
        "<description><![CDATA[Ranadeb Chaudhuri, Zoran Ivkovich, and Andrei Simonov "
        "♦ This article examines individual equity investment but the teaser ends […]"
        "]]></description>"
        "<guid>https://jfqa.org/?p=10849</guid>" + suffix
        + "</item></channel></rss>"
    )


class JfqaFullAbstractTests(unittest.TestCase):
    def test_untruncated_encoded_content_not_rss_teaser(self):
        encoded = (
            '<h4 class="wp-block-heading">Ranadeb Chaudhuri et al.</h4>'
            '<p class="wp-block-paragraph">♦ ' + PARAGRAPH + "</p>"
            '<p class="wp-block-paragraph">Read it '
            '<a href="https://jfqa.org/paper.pdf">here</a>.</p>'
        )
        record, = fetch_rss.parse_feed(xml_fixture(encoded), JFQA, fetch_rss.JFQA_FORTHCOMING_FEED)
        self.assertEqual(record["abstract"], PARAGRAPH)
        self.assertEqual(record["abstract_source"], "jfqa_forthcoming_official:content_encoded")
        self.assertEqual(record["abstract_status_code"], "available")
        self.assertEqual(record["abstract_completeness"], "full")
        self.assertNotIn("Read it", record["abstract"])
        self.assertEqual(record["available_online"], "2026-10-31")
        self.assertIsNone(record["published_online"])
        self.assertEqual(record["date_source"], "rss_published")
        self.assertEqual(record["raw_data"]["publication_stage"], "jfqa_forthcoming")

        existing = {
            "id": dedupe.stable_id(record),
            "title": record["title"],
            "journal": record["journal"],
            "journal_id": record["journal_id"],
            "url": record["url"],
            "first_seen": "2026-11-03T21:45:34+00:00",
            "available_online": "2026-10-31",
            "published_online": None,
            "abstract": None,
            "abstract_status": "摘要暂未公开，系统将自动重试",
            "abstract_status_code": "missing_retry",
        }
        first_seen = existing["first_seen"]
        self.assertTrue(dedupe.enrich_record(existing, record))
        self.assertEqual(existing["abstract"], PARAGRAPH)
        self.assertEqual(existing["abstract_source"], "jfqa_forthcoming_official:content_encoded")
        self.assertEqual(existing["abstract_status_code"], "available")
        self.assertNotIn("abstract_status", existing)
        self.assertEqual(existing["first_seen"], first_seen)
        self.assertEqual(existing["available_online"], "2026-10-31")
        self.assertIsNone(existing["published_online"])
        self.assertFalse(dedupe.enrich_record(existing, record))

    def test_missing_or_truncated_encoded_content_is_never_full(self):
        variants = [
            None,
            "<p>♦ Very short abstract.</p>",
            "<p>♦ " + PARAGRAPH + " […]</p>",
            "<p>♦ " + PARAGRAPH + "...</p>",
            "<p>Abstract: " + PARAGRAPH + "</p>",
            "<p>♦ " + PARAGRAPH + "</p><p>♦ " + PARAGRAPH + "</p>",
        ]
        for encoded in variants:
            with self.subTest(encoded=encoded):
                record, = fetch_rss.parse_feed(
                    xml_fixture(encoded), JFQA, fetch_rss.JFQA_FORTHCOMING_FEED
                )
                self.assertFalse(record.get("abstract"))

    def test_wrong_journal_or_feed_never_uses_encoded_abstract(self):
        encoded = "<p>♦ " + PARAGRAPH + "</p>"
        wrong_journal = {**JFQA, "id": "public-choice", "title": "Public Choice"}
        other, = fetch_rss.parse_feed(
            xml_fixture(encoded), wrong_journal, fetch_rss.JFQA_FORTHCOMING_FEED
        )
        self.assertFalse(other.get("abstract"))
        self.assertIsNone(verified_forthcoming_abstract("<p>♦ Too short.</p>"))


if __name__ == "__main__":
    unittest.main()
