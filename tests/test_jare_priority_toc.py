from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_source_health  # noqa: E402
import fetch_priority_toc  # noqa: E402


JARE_ID = "journal-of-agricultural-and-resource-economics"
JARE_HTML = """
<div class="elementor post-3216 preprints2022 type-preprints2022 status-publish hentry ast-article-single">
  <h1 class="elementor-heading-title elementor-size-default">
    <a href="https://ageconsearch.umn.edu/record/401358/files/Ohler_preprint.pdf">
      Willingness to Pay for Water Quality Improvements to a Recreational Reservoir
    </a>
  </h1>
  <div class="elementor-widget-container">
    By: Ohler, Adrienne; Alkilany, Yousef; McCann, Laura
  </div>
  <div class="elementor-widget-container">8/24/2026</div>
  <div class="elementor-widget-container">
    <strong>Abstract</strong>
    We estimate households' willingness to pay for water quality improvements using stated preference evidence from a recreational reservoir.
  </div>
  <div class="elementor-widget-container">Download Full Article</div>
</div>
"""
JARE_REST = json.dumps([
    {
        "id": 3232,
        "link": "https://jareonline.org/preprints2022/buffering-supply-shocks/",
        "title": {
            "rendered": "Buffering Supply Shocks in Successive Oligopoly: Market Power and Welfare Implications"
        },
        "content": {
            "rendered": "<p>Fallback abstract text.</p>",
        },
        "acf": {
            "preprint_title": "Buffering Supply Shocks in Successive Oligopoly: Market Power and Welfare Implications",
            "ppabstract": "We investigate the welfare consequences of supply shocks in concentrated agricultural supply chains.",
            "ppauthor": "Kim, Youngjune; , Tian Xia; Pendell, Dustin L.",
            "ppissue_date": "9/15/2026",
            "pprecordnum": 393803,
            "ppdirect_pdf_link": "https://ageconsearch.umn.edu/record/393803/files/Kim_preprint.pdf",
        },
    }
])


class JareAdvanceSourceTests(unittest.TestCase):
    def test_jare_official_rest_target_is_configured_with_html_fallback(self) -> None:
        target = fetch_priority_toc.TARGETS[JARE_ID][0]
        self.assertEqual(target["kind"], "jare_rest")
        self.assertEqual(
            target["url"],
            "https://jareonline.org/wp-json/wp/v2/preprints2022?per_page=100&_fields=id,title,acf,link,content",
        )
        self.assertEqual(target["source_url"], "https://jareonline.org/preprint-online/")
        self.assertEqual(target["html_fallback_url"], "https://jareonline.org/preprint-online/")
        self.assertEqual(target.get("publisher_attempts"), 3)
        self.assertIsNone(target.get("fallback_issn"))

    def test_numeric_publisher_date_is_normalized(self) -> None:
        self.assertEqual(fetch_priority_toc.parse_date("8/24/2026"), "2026-08-24")

    def test_siteground_captcha_meta_refresh_is_rejected_as_challenge(self) -> None:
        challenge = (
            '<html><head><link rel="icon" href="data:;">'
            '<meta http-equiv="refresh" content="0;/.well-known/sgcaptcha/?r=%2Fpreprint-online%2F&y=ipr:9.234.149.177:1788946938.997">'
            "</meta></head></html>"
        )
        self.assertTrue(fetch_priority_toc.is_challenge_page(challenge))

    def test_jare_rest_extracts_structured_first_party_fields(self) -> None:
        blocks = fetch_priority_toc.jare_rest_blocks(JARE_REST)
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual(
            block["title"],
            "Buffering Supply Shocks in Successive Oligopoly: Market Power and Welfare Implications",
        )
        self.assertEqual(
            block["url"],
            "https://ageconsearch.umn.edu/record/393803/files/Kim_preprint.pdf",
        )
        self.assertEqual(
            block["authors"],
            ["Kim, Youngjune", "Tian Xia", "Pendell, Dustin L."],
        )
        self.assertEqual(block["published_online"], "2026-09-15")
        self.assertIn("welfare consequences", block["abstract"].casefold())
        self.assertEqual(block["rest_id"], 3232)

    def test_jare_advance_card_extracts_title_authors_date_abstract_and_pdf(self) -> None:
        blocks = fetch_priority_toc.jare_advance_blocks(
            JARE_HTML,
            "https://jareonline.org/preprint-online/",
        )
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual(
            block["title"],
            "Willingness to Pay for Water Quality Improvements to a Recreational Reservoir",
        )
        self.assertEqual(
            block["url"],
            "https://ageconsearch.umn.edu/record/401358/files/Ohler_preprint.pdf",
        )
        self.assertEqual(
            block["authors"],
            ["Ohler, Adrienne", "Alkilany, Yousef", "McCann, Laura"],
        )
        self.assertEqual(block["published_online"], "2026-08-24")
        self.assertIn("willingness to pay", block["abstract"].casefold())

    def test_fetch_target_prefers_rest_and_preserves_public_source_url(self) -> None:
        journal = {
            "id": JARE_ID,
            "title": "Journal of Agricultural and Resource Economics",
        }
        target = fetch_priority_toc.TARGETS[JARE_ID][0]
        with mock.patch.object(fetch_priority_toc, "fetch_toc_text", return_value=JARE_REST) as fetch:
            records = fetch_priority_toc.fetch_target(
                journal,
                target,
                timeout=5,
                detail_limit=12,
                max_items=10,
            )

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["source"], "priority_toc")
        self.assertEqual(record["source_url"], "https://jareonline.org/preprint-online/")
        self.assertEqual(record["published_online"], "2026-09-15")
        self.assertEqual(record["date_source"], "jare_published_online")
        self.assertEqual(record["raw_data"]["priority_toc_kind"], "jare_rest")
        self.assertEqual(record["raw_data"]["jare_acquisition"], "wordpress-rest")
        self.assertEqual(record["raw_data"]["jare_rest_outcome"], "SUCCESS")
        self.assertEqual(record["raw_data"]["jare_rest_id"], 3232)
        self.assertIsNone(record.get("doi"))

    def test_fetch_target_falls_back_to_official_html_when_rest_is_unavailable(self) -> None:
        journal = {
            "id": JARE_ID,
            "title": "Journal of Agricultural and Resource Economics",
        }
        target = fetch_priority_toc.TARGETS[JARE_ID][0]
        with mock.patch.object(
            fetch_priority_toc,
            "fetch_toc_text",
            side_effect=[
                fetch_priority_toc.PublisherTransportError("CHALLENGE", "publisher challenge"),
                JARE_HTML,
            ],
        ) as fetch:
            records = fetch_priority_toc.fetch_target(
                journal,
                target,
                timeout=5,
                detail_limit=12,
                max_items=10,
            )

        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["source_url"], "https://jareonline.org/preprint-online/")
        self.assertEqual(record["raw_data"]["jare_acquisition"], "html-fallback")
        self.assertEqual(record["raw_data"]["jare_rest_outcome"], "CHALLENGE")
        self.assertEqual(record["raw_data"]["priority_toc_kind"], "jare_rest")
        self.assertEqual(record["published_online"], "2026-08-24")

    def test_fetch_one_preserves_challenge_outcome(self) -> None:
        challenge = (
            '<html><head><meta http-equiv="refresh" '
            'content="0;/.well-known/sgcaptcha/?r=%2Fpreprint-online%2F"></head></html>'
        )
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = challenge.encode("utf-8")
        response.headers.get_content_charset.return_value = "utf-8"

        with mock.patch.object(fetch_priority_toc.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(fetch_priority_toc.PublisherTransportError) as caught:
                fetch_priority_toc.fetch_one("https://jareonline.org/preprint-online/", timeout=5)

        self.assertEqual(caught.exception.outcome, "CHALLENGE")

    def test_transport_classifier_maps_http_and_network_failures(self) -> None:
        http_error = fetch_priority_toc.urllib.error.HTTPError
        self.assertEqual(
            fetch_priority_toc.classify_transport_error(
                http_error("https://example.test", 401, "auth", None, None)
            ),
            "AUTH_REQUIRED",
        )
        self.assertEqual(
            fetch_priority_toc.classify_transport_error(
                http_error("https://example.test", 403, "blocked", None, None)
            ),
            "HTTP_BLOCK",
        )
        self.assertEqual(
            fetch_priority_toc.classify_transport_error(
                http_error("https://example.test", 429, "rate", None, None)
            ),
            "RATE_LIMITED",
        )
        self.assertEqual(
            fetch_priority_toc.classify_transport_error(
                fetch_priority_toc.urllib.error.URLError("temporary network failure")
            ),
            "NETWORK_FAILURE",
        )

    def test_transport_classifier_maps_decode_and_parser_failures(self) -> None:
        decode_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        self.assertEqual(
            fetch_priority_toc.classify_transport_error(decode_error),
            "DECODE_FAILURE",
        )
        with self.assertRaises(fetch_priority_toc.PublisherTransportError) as caught:
            fetch_priority_toc.jare_rest_blocks("{not-json")
        self.assertEqual(caught.exception.outcome, "PARSER_FAILURE")

    def test_valid_empty_rest_falls_back_without_fabricating_success(self) -> None:
        journal = {
            "id": JARE_ID,
            "title": "Journal of Agricultural and Resource Economics",
        }
        target = fetch_priority_toc.TARGETS[JARE_ID][0]
        with mock.patch.object(
            fetch_priority_toc,
            "fetch_toc_text",
            side_effect=["[]", JARE_HTML],
        ):
            records = fetch_priority_toc.fetch_target(
                journal,
                target,
                timeout=5,
                detail_limit=12,
                max_items=10,
            )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["raw_data"]["jare_acquisition"], "html-fallback")
        self.assertEqual(records[0]["raw_data"]["jare_rest_outcome"], "VALID_EMPTY")

    def test_failed_publisher_status_uses_normalized_outcome_not_exception_name(self) -> None:
        journal = {"id": JARE_ID, "title": "Journal of Agricultural and Resource Economics"}
        target = fetch_priority_toc.TARGETS[JARE_ID][0]

        with mock.patch.object(
            fetch_priority_toc,
            "fetch_target",
            side_effect=fetch_priority_toc.PublisherTransportError(
                "CHALLENGE",
                "publisher returned an anti-bot challenge page",
            ),
        ) as fetch_target, mock.patch.object(
            fetch_priority_toc,
            "fetch_crossref_fallback",
            return_value=[],
        ), mock.patch.object(fetch_priority_toc.time, "sleep"):
            result = fetch_priority_toc.fetch_target_with_fallback(
                journal,
                target,
                timeout=5,
                detail_limit=0,
                max_items=10,
            )

        self.assertEqual(fetch_target.call_count, 3)
        self.assertTrue(result[4])
        self.assertEqual(result[5], "CHALLENGE")
        self.assertIn("CHALLENGE", result[3][0])
        self.assertNotIn("ValueError", result[3][0])

    def test_jare_target_retries_transient_publisher_failure_before_crossref_fallback(self) -> None:
        journal = {"id": JARE_ID, "title": "Journal of Agricultural and Resource Economics"}
        target = fetch_priority_toc.TARGETS[JARE_ID][0]
        records = [{"title": "Recovered live record"}]

        with mock.patch.object(
            fetch_priority_toc,
            "fetch_target",
            side_effect=[RuntimeError("transient publisher failure"), records],
        ) as fetch_target, mock.patch.object(
            fetch_priority_toc, "fetch_crossref_fallback"
        ) as fallback, mock.patch.object(fetch_priority_toc.time, "sleep"):
            result = fetch_priority_toc.fetch_target_with_fallback(
                journal,
                target,
                timeout=5,
                detail_limit=0,
                max_items=10,
            )

        self.assertEqual(fetch_target.call_count, 2)
        fallback.assert_not_called()
        self.assertEqual(result[0], records)
        self.assertEqual(result[1], 0)
        self.assertTrue(result[2])
        self.assertFalse(result[4])
        self.assertEqual(result[5], "SUCCESS")

    def test_source_health_treats_jare_priority_toc_as_reliable_not_closed(self) -> None:
        self.assertIn(JARE_ID, audit_source_health.PRIORITY_TOC_JOURNALS)
        self.assertNotIn(JARE_ID, audit_source_health.SUPPLEMENTAL_CLOSED_NOTES)


if __name__ == "__main__":
    unittest.main()
