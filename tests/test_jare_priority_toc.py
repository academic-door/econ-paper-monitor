from __future__ import annotations

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


class JareAdvanceSourceTests(unittest.TestCase):
    def test_jare_official_advance_target_is_configured(self) -> None:
        target = fetch_priority_toc.TARGETS[JARE_ID][0]
        self.assertEqual(target["kind"], "jare_advance")
        self.assertEqual(target["url"], "https://jareonline.org/preprint-online/")
        self.assertEqual(target["fallback_issn"], "1068-5502")

    def test_numeric_publisher_date_is_normalized(self) -> None:
        self.assertEqual(fetch_priority_toc.parse_date("8/24/2026"), "2026-08-24")

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

    def test_fetch_target_uses_official_jare_card_without_detail_fetch(self) -> None:
        journal = {
            "id": JARE_ID,
            "title": "Journal of Agricultural and Resource Economics",
        }
        target = {
            "kind": "jare_advance",
            "url": "https://jareonline.org/preprint-online/",
            "date_source": "jare_published_online",
            "date_confidence": "B",
            "fallback_issn": "1068-5502",
        }
        with mock.patch.object(fetch_priority_toc, "fetch_toc_text", return_value=JARE_HTML), \
                mock.patch.object(fetch_priority_toc, "enrich_detail") as enrich_detail:
            records = fetch_priority_toc.fetch_target(
                journal,
                target,
                timeout=5,
                detail_limit=12,
                max_items=10,
            )

        self.assertEqual(len(records), 1)
        enrich_detail.assert_not_called()
        record = records[0]
        self.assertEqual(record["source"], "priority_toc")
        self.assertEqual(record["source_url"], "https://jareonline.org/preprint-online/")
        self.assertEqual(record["published_online"], "2026-08-24")
        self.assertEqual(record["date_source"], "jare_published_online")
        self.assertEqual(record["raw_data"]["priority_toc_kind"], "jare_advance")
        self.assertIsNone(record.get("doi"))

    def test_source_health_treats_jare_priority_toc_as_reliable_not_closed(self) -> None:
        self.assertIn(JARE_ID, audit_source_health.PRIORITY_TOC_JOURNALS)
        self.assertNotIn(JARE_ID, audit_source_health.SUPPLEMENTAL_CLOSED_NOTES)


if __name__ == "__main__":
    unittest.main()
