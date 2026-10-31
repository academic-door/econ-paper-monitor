"""Unit tests for RSS record construction and Springer feed parsing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_rss  # noqa: E402


JOURNAL = {
    "id": "public-choice",
    "title": "Public Choice",
    "short_name": "PC",
    "publisher": "Springer-Verlag",
}
FEED_URL = "https://link.springer.com/search.rss?facet-journal-id=11127"


class RssRecordTests(unittest.TestCase):
    def test_bare_doi_guid_is_extracted(self):
        record = fetch_rss.make_record(
            "Fairness trade-offs in expert evaluation",
            "https://link.springer.com/article/10.1007/s11127-026-01444-z",
            "2026-08-04",
            JOURNAL,
            FEED_URL,
            guid="10.1007/s11127-026-01444-z",
        )
        self.assertEqual(record["doi"], "10.1007/s11127-026-01444-z")
        self.assertEqual(record["date_source"], "rss_published")
        self.assertEqual(record["date_confidence"], "A")

    def test_rss_published_date_is_grade_a(self):
        record = fetch_rss.make_record(
            "A title",
            "https://example.com/a",
            "2026-08-01",
            JOURNAL,
            "https://example.com/feed",
        )
        self.assertEqual(record["date_confidence"], "A")

    def test_no_date_falls_back_to_f(self):
        record = fetch_rss.make_record("A title", "https://example.com/a", None, JOURNAL, "https://example.com/feed")
        self.assertEqual(record["date_confidence"], "F")

    def test_parse_springer_rss_item(self):
        xml = (
            '<?xml version="1.0"?><rss version="2.0"><channel><title>Latest Results</title>'

            "<item><title>T</title><description><p>Abstract text with substance.</p></description>"
            "<link>https://link.springer.com/article/10.1007/s11127-026-01444-z</link>"
            "<pubDate>2026-08-04</pubDate><guid>10.1007/s11127-026-01444-z</guid></item>"
            "</channel></rss>"
        )
        records = fetch_rss.parse_feed(xml, JOURNAL, FEED_URL)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["doi"], "10.1007/s11127-026-01444-z")
        self.assertEqual(records[0]["published_online"], "2026-08-04")

    def test_child_text_any_reads_nested_elements(self):
        from xml.etree import ElementTree

        item = ElementTree.fromstring("<item><description><p>Nested text with substance.</p></description></item>")
        self.assertEqual(fetch_rss.child_text_any(item, ["description"]), "Nested text with substance.")


if __name__ == "__main__":
    unittest.main()

    def test_fetch_journal_feed_retries_transient_failure(self):
        xml = (
            '<?xml version="1.0"?><rss version="2.0"><channel><item>'
            "<title>T</title><link>https://example.com/a</link><pubDate>2026-08-01</pubDate></item>"
            "</channel></rss>"
        )
        real = fetch_rss.fetch_feed_with_retry
        calls = {"n": 0}

        def flaky_once(url, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("transient")
            return xml

        with unittest.mock.patch.object(fetch_rss, "fetch_feed_with_retry", side_effect=flaky_once):
            records, error = fetch_rss.fetch_journal_feed(JOURNAL, {"url": "https://example.com/feed"})
        self.assertIsNone(error)
        self.assertEqual(len(records), 1)
        self.assertEqual(calls["n"], 2)

    def test_fetch_journal_feed_returns_error_after_retries(self):
        with unittest.mock.patch.object(fetch_rss, "fetch_feed_with_retry", side_effect=ConnectionError("blocked")):
            records, error = fetch_rss.fetch_journal_feed(JOURNAL, {"url": "https://example.com/feed"})
        self.assertEqual(records, [])
        self.assertIn("ConnectionError", error)


OUP_JOURNAL = {
    "id": "review-of-economic-studies",
    "title": "Review of Economic Studies",
    "short_name": "REStud",
    "publisher": "Oxford University Press",
}
OUP_FEED_URL = "https://academic.oup.com/rss/site_5508/advanceAccess_3369.xml"

OUP_ADVANCE_RSS = """<?xml version="1.0"?>
<rss version="2.0" xmlns:prism="http://purl.org/rss/1.0/modules/prism/">
  <channel>
    <title>The Review of Economic Studies Advance Access</title>
    <link>http://academic.oup.com/restud</link>
    <pubDate>Thu, 03 Sep 2026 00:00:00 GMT</pubDate>
    <generator>Silverchair</generator>
    <item>
      <title>Equity Frictions and Firm Ownership</title>
      <link>https://academic.oup.com/restud/advance-article/doi/10.1093/restud/rdag098/8780917?rss=1</link>
      <pubDate>Thu, 03 Sep 2026 00:00:00 GMT</pubDate>
      <prism:startingPage xmlns:prism="prism">rdag098</prism:startingPage>
      <prism:doi xmlns:prism="prism">10.1093/restud/rdag098</prism:doi>
      <guid>http://doi.org/10.1093/restud/rdag098</guid>
    </item>
    <item>
      <title>Public Employee Pensions and Municipal Insolvency</title>
      <link>https://academic.oup.com/restud/advance-article/doi/10.1093/restud/rdag097/8772202?rss=1</link>
      <pubDate>Fri, 28 Aug 2026 00:00:00 GMT</pubDate>
      <prism:startingPage xmlns:prism="prism">rdag097</prism:startingPage>
      <prism:doi xmlns:prism="prism">10.1093/restud/rdag097</prism:doi>
      <guid>http://doi.org/10.1093/restud/rdag097</guid>
    </item>
  </channel>
</rss>
"""


class OupRestudFeedTests(unittest.TestCase):
    """The OUP /rss/ route is the official Advance Access feed for REStud.

    The do.org-form guid and the bare prism:doi must yield a proper DOI so the
    record can be deduped/enriched by identity instead of a tracking URL.
    """

    def test_feed_identity_matches(self) -> None:
        self.assertTrue(fetch_rss.feed_identity_matches(OUP_ADVANCE_RSS, OUP_JOURNAL))

    def test_parse_extracts_doi_and_online_date(self) -> None:
        records = fetch_rss.parse_feed(OUP_ADVANCE_RSS, OUP_JOURNAL, OUP_FEED_URL)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["title"], "Equity Frictions and Firm Ownership")
        self.assertEqual(records[0]["doi"], "10.1093/restud/rdag098")
        self.assertEqual(records[0]["published_online"], "2026-09-03")
        self.assertEqual(records[1]["doi"], "10.1093/restud/rdag097")
        self.assertEqual(records[1]["published_online"], "2026-08-28")
        self.assertEqual(records[0]["source"], "rss")


JFQA_JOURNAL = {
    "id": "journal-of-financial-and-quantitative-analysis",
    "title": "Journal of Financial and Quantitative Analysis",
    "short_name": "JFQA",
    "publisher": "Cambridge University Press",
}

JFQA_FORTHCOMING_XML = """<?xml version="1.0"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/modules/" version="2.0">
  <channel><title>Forthcoming Articles – JFQA</title>
    <item>
      <title>Production-Based Option Pricing</title>
      <link>https://jfqa.org/2026/10/20/production-based-option-pricing/</link>
      <dc:creator>JFQA Admin</dc:creator>
      <pubDate>Tue, 20 Oct 2026 17:08:08 +0000</pubDate>
      <description>Andrea Gamba and Alessio Saretto ♦ We show that a structural model generates implied volatility surfaces.</description>
      <guid>https://jfqa.org/?p=10814</guid>
    </item>
  </channel>
</rss>"""


class JfqaFirstPartyForthcomingTests(unittest.TestCase):
    def test_identity_only_accepts_exact_official_forthcoming_feed(self):
        self.assertTrue(fetch_rss.feed_identity_matches(
            JFQA_FORTHCOMING_XML, JFQA_JOURNAL, fetch_rss.JFQA_FORTHCOMING_FEED))
        self.assertFalse(fetch_rss.feed_identity_matches(
            JFQA_FORTHCOMING_XML, JFQA_JOURNAL, "https://untrusted.example/feed"))
        self.assertFalse(fetch_rss.feed_identity_matches(
            JFQA_FORTHCOMING_XML, JOURNAL, fetch_rss.JFQA_FORTHCOMING_FEED))

    def test_real_authors_and_forthcoming_date_semantics(self):
        record, = fetch_rss.parse_feed(
            JFQA_FORTHCOMING_XML, JFQA_JOURNAL, fetch_rss.JFQA_FORTHCOMING_FEED)
        self.assertEqual(record["title"], "Production-Based Option Pricing")
        self.assertEqual(record["journal_id"], JFQA_JOURNAL["id"])
        self.assertEqual(record["authors"], ["Andrea Gamba", "Alessio Saretto"])
        self.assertEqual(record["available_online"], "2026-10-20")
        self.assertIsNone(record["published_online"])
        self.assertIsNone(record["accepted_date"])
        self.assertEqual(record["date_source"], "rss_published")
        self.assertEqual(record["raw_data"]["publication_stage"], "jfqa_forthcoming")

    def test_wordpress_admin_is_never_used_as_article_author(self):
        xml = JFQA_FORTHCOMING_XML.replace(
            "Andrea Gamba and Alessio Saretto ♦ ", "")
        record, = fetch_rss.parse_feed(xml, JFQA_JOURNAL, fetch_rss.JFQA_FORTHCOMING_FEED)
        self.assertEqual(record["authors"], [])
