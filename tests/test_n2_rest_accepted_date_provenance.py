"""Publisher-post dates must not become formal REStud online-publication dates."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import dedupe  # noqa: E402
import enrich_metadata  # noqa: E402


POST_URLS = (
    "https://www.restud.com/from-design-to-disclosure/",
    "https://www.restud.com/fertility-and-family-leave-policies-in-germany-optimal-policy-design-in-a-dynamic-framework/",
)


def accepted_record(url: str, *, contaminated: bool = True) -> dict:
    return {
        "id": "url:" + url.split("/")[-2],
        "journal": "Review of Economic Studies",
        "journal_id": "review-of-economic-studies",
        "title": url.split("/")[-2].replace("-", " ").title(),
        "url": url,
        "source": "rss",
        "source_type": "journal",
        "doi": None,
        "first_seen": "2026-11-04T06:20:41+00:00",
        "detected_at": "2026-11-04T06:20:41+00:00",
        "available_online": "2026-11-02",
        "published_online": "2026-11-02" if contaminated else None,
        "date_source": "publisher_meta:article:published_time" if contaminated else "rss_published",
        "date_confidence": "A",
        "raw_data": {
            "publication_stage": "restud_accepted",
            "rss_feed_url": "https://www.restud.com/accepted-papers/feed/",
        },
    }


class RestudAcceptedDateProvenanceTests(unittest.TestCase):
    def test_precise_repair_is_idempotent_and_preserves_first_discovery(self):
        entry = accepted_record(POST_URLS[0])
        before = deepcopy(entry)
        self.assertTrue(dedupe.repair_rest_accepted_blog_date(entry))
        self.assertFalse(dedupe.repair_rest_accepted_blog_date(entry))
        self.assertIsNone(entry["published_online"])
        self.assertEqual(entry["date_source"], "rss_published")
        for key in ("first_seen", "detected_at", "available_online",
                    "id", "url", "title", "doi", "raw_data"):
            self.assertEqual(entry[key], before[key])

    def test_repair_not_applied_to_later_doi_or_unrelated_journal(self):
        base = accepted_record(POST_URLS[0])
        cases = (
            {**base, "doi": "10.1093/restud/rdag100"},
            {**base, "journal": "Different Journal"},
            {**base, "url": "https://academic.oup.com/restud/article/1"},
            {**base, "date_source": "crossref_doi_published_online"},
            {**base, "raw_data": {"publication_stage": "other_stage"}},
        )
        for entry in cases:
            with self.subTest(entry=entry):
                before = deepcopy(entry)
                self.assertFalse(dedupe.repair_rest_accepted_blog_date(entry))
                self.assertEqual(entry, before)

    def test_site_meta_can_enrich_abstract_but_not_publication_date(self):
        entry = accepted_record(POST_URLS[0], contaminated=False)
        original_first = entry["first_seen"]
        self.assertTrue(enrich_metadata.merge_metadata(entry, {
            "abstract": "Verified first-party paper abstract from page.",
            "abstract_source": "publisher_meta:description",
            "published_online": "2026-11-02",
            "available_online": "2026-11-02",
            "date_source": "publisher_meta:article:published_time",
            "date_confidence": "A",
        }))
        self.assertIsNone(entry["published_online"])
        self.assertEqual(entry["date_source"], "rss_published")
        self.assertEqual(entry["abstract_source"], "publisher_meta:description")
        self.assertEqual(entry["first_seen"], original_first)
        self.assertTrue(enrich_metadata.merge_metadata(entry, {
            "published_online": "2026-11-12",
            "date_source": "crossref_doi_published_online",
            "date_confidence": "A",
        }))
        self.assertEqual(entry["published_online"], "2026-11-12")

    def test_natural_dedupe_repairs_both_daily_and_seen(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "daily").mkdir()
            (root / "raw").mkdir()
            entries = [accepted_record(url) for url in POST_URLS]
            date_path = root / "daily" / "2026-11-02.json"
            date_path.write_text(json.dumps(entries), encoding="utf-8")
            seen_path = root / "seen.json"
            seen_records = {
                entry["id"]: {k: deepcopy(v) for k, v in entry.items() if k != "raw_data"}
                for entry in entries
            }
            seen_path.write_text(json.dumps({"papers": seen_records}), encoding="utf-8")
            args = ["dedupe.py", "--raw-dir", str(root / "raw"),
                    "--daily-dir", str(root / "daily"),
                    "--seen", str(seen_path), "--date", "2026-11-06"]
            with (
                patch.object(sys, "argv", args),
                patch.object(dedupe, "record_source"),
                patch.object(dedupe, "record_run"),
            ):
                dedupe.main()
                once = date_path.read_text(encoding="utf-8")
                dedupe.main()
                self.assertEqual(date_path.read_text(encoding="utf-8"), once)
            actual = json.loads(date_path.read_text(encoding="utf-8"))
            stored = json.loads(seen_path.read_text(encoding="utf-8"))["papers"]
            self.assertEqual(len(actual), 2)
            self.assertEqual(len(stored), 2)
            for entry in actual + list(stored.values()):
                self.assertIsNone(entry["published_online"])
                self.assertEqual(entry["date_source"], "rss_published")
                self.assertEqual(entry["first_seen"], "2026-11-04T06:20:41+00:00")


if __name__ == "__main__":
    unittest.main()
