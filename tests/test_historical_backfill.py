from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_preprints  # noqa: E402
import clean_historical_working_papers  # noqa: E402
import normalize_records  # noqa: E402
import quarantine_historical_backfill  # noqa: E402
import repair_historical_backfill  # noqa: E402
import remove_seen_backflow  # noqa: E402
import render_site  # noqa: E402
from dedupe import record_match_keys  # noqa: E402


class HistoricalBackfillTests(unittest.TestCase):
    def test_future_online_record_is_quarantined_instead_of_restored_to_an_old_bucket(self) -> None:
        record = {"available_online": "2026-07-30", "published_online": "2026-07-30"}
        self.assertEqual(remove_seen_backflow.future_official_date(record, "2026-07-29"), "2026-07-30")
        self.assertIsNone(remove_seen_backflow.future_official_date(record, "2026-07-30"))

    def test_nep_rediscovery_cannot_move_the_original_issue_date_forward(self) -> None:
        record = {
            "source_id": "repec-nep-mac",
            "date_source": "nep_issue_date",
            "first_seen": "2026-06-21T01:28:34+00:00",
            "available_online": "2026-07-20",
            "published_online": "2026-07-20",
        }

        self.assertTrue(normalize_records.normalize_nep_issue_date(record))
        self.assertEqual(record["available_online"], "2026-06-21")
        self.assertEqual(record["published_online"], "2026-06-21")
        self.assertEqual(record["date_source"], "nep_first_seen_issue_date")

    def test_historical_cleanup_uses_each_canonical_bucket_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily = root / "daily"
            daily.mkdir()
            pending = root / "pending.json"
            record = {
                "title": "Historical catalogue item",
                "source": "working_papers",
                "source_id": "cepr-dp",
                "available_online": "2023-01-19",
                "date_confidence": "B",
                "url": "https://cepr.org/publications/dp17822",
            }
            (daily / "2026-07-27.json").write_text(json.dumps([record]), encoding="utf-8")
            argv = [
                "clean_historical_working_papers.py",
                "--daily-dir",
                str(daily),
                "--pending",
                str(pending),
            ]

            with patch.object(sys, "argv", argv):
                clean_historical_working_papers.main()

            self.assertEqual(json.loads((daily / "2026-07-27.json").read_text(encoding="utf-8")), [])
            self.assertEqual(len(json.loads(pending.read_text(encoding="utf-8"))), 1)

    def test_fresh_first_seen_does_not_make_stale_cepr_catalogue_item_current(self) -> None:
        record = {
            "source": "working_papers",
            "source_id": "cepr-dp",
            "published_online": "2019-06-13",
            "available_online": "2019-06-13",
            "date_source": "cepr_published_time",
            "date_confidence": "F",
            "first_seen": "2026-09-21T00:20:01+00:00",
            "url": "https://cepr.org/publications/dp13798",
        }
        self.assertTrue(
            clean_historical_working_papers.is_historical_cepr(
                record,
                run_date="2026-09-21",
                max_age_days=14,
            )
        )

    def test_recent_cepr_with_current_official_date_remains_eligible(self) -> None:
        record = {
            "source": "working_papers",
            "source_id": "cepr-dp",
            "published_online": "2026-09-20",
            "available_online": "2026-09-20",
            "date_source": "publisher_detail",
            "date_confidence": "A",
            "first_seen": "2026-09-21T00:20:01+00:00",
            "url": "https://cepr.org/publications/dp21958",
        }
        self.assertFalse(
            clean_historical_working_papers.is_historical_cepr(
                record,
                run_date="2026-09-21",
                max_age_days=14,
            )
        )

    def test_cepr_source_uses_current_search_listing(self) -> None:
        sources = fetch_preprints.load_sources(
            Path(__file__).resolve().parents[1] / "data" / "working_paper_sources.yml"
        )
        cepr = next(source for source in sources if source.get("id") == "cepr-dp")
        self.assertEqual(
            cepr["homepage"],
            "https://cepr.org/publications/discussion-papers/search-discussion-papers",
        )

    def test_recent_cepr_number_with_old_official_date_moves_to_pending(self) -> None:
        record = {
            "source": "working_papers",
            "source_id": "cepr-dp",
            "published_online": "2025-06-03",
            "url": "https://cepr.org/publications/dp20322",
        }
        self.assertTrue(clean_historical_working_papers.is_historical_working_paper(
            record, run_date="2026-07-27", max_age_days=14
        ))

    def test_verified_old_working_paper_moves_to_official_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            daily = Path(tmp)
            record = {
                "title": "An old CEPR paper discovered from a catalogue",
                "source": "working_papers",
                "source_id": "cepr-dp",
                "available_online": "2011-01-03",
                "published_online": "2011-01-03",
                "date_confidence": "B",
                "detected_at": "2026-07-21T14:43:15+00:00",
                "url": "https://cepr.org/publications/dp8176",
            }
            (daily / "2026-07-21.json").write_text(json.dumps([record]), encoding="utf-8")

            moved, _ = quarantine_historical_backfill.quarantine_date(daily, "2026-07-21", 30)

            self.assertEqual(moved, 1)
            self.assertEqual(json.loads((daily / "2026-07-21.json").read_text(encoding="utf-8")), [])
            archived = json.loads((daily / "2011-01-03.json").read_text(encoding="utf-8"))
            self.assertTrue(archived[0]["historical_backfill"])
            self.assertTrue(archived[0]["public_flow_excluded"])

    def test_low_confidence_date_is_not_moved(self) -> None:
        record = {"available_online": "2011-01-03", "date_confidence": "F"}
        self.assertFalse(quarantine_historical_backfill.should_quarantine(record, "2026-07-21", 30))

    def test_undated_stale_cepr_catalogue_item_moves_to_pending_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily = root / "daily"
            daily.mkdir()
            payload = [
                {
                    "title": "A current CEPR discussion paper with a research title",
                    "source": "working_papers",
                    "source_id": "cepr-dp",
                    "paper_number": "DP21770",
                    "available_online": "2026-07-21",
                    "date_confidence": "B",
                    "url": "https://cepr.org/publications/dp21770",
                },
                {
                    "title": "An undated old CEPR catalogue paper with a research title",
                    "source": "working_papers",
                    "source_id": "cepr-dp",
                    "paper_number": "DP8177",
                    "date_confidence": "F",
                    "url": "https://cepr.org/publications/dp8177",
                },
            ]
            (daily / "2026-07-21.json").write_text(json.dumps(payload), encoding="utf-8")

            moved, _ = quarantine_historical_backfill.quarantine_date(daily, "2026-07-21", 30)

            self.assertEqual(moved, 1)
            remaining = json.loads((daily / "2026-07-21.json").read_text(encoding="utf-8"))
            self.assertEqual([record["paper_number"] for record in remaining], ["DP21770"])
            pending = json.loads((root / "historical_backfill_pending.json").read_text(encoding="utf-8"))
            self.assertEqual(pending[0]["paper_number"], "DP8177")
            self.assertEqual(pending[0]["historical_backfill_status"], "pending_official_date")

    def test_cepr_listing_prefers_highest_current_dp_numbers(self) -> None:
        html = """
        <a href="/publications/dp8176">Anticipated Alternative Instrument-Rate Paths in Policy Simulations</a>
        <a href="/publications/dp21229">A Current Discussion Paper with a Valid Research Title</a>
        <a href="/publications/dp21228">Another Current Discussion Paper with a Valid Research Title</a>
        """
        source = {"id": "cepr-dp", "title": "CEPR Discussion Papers", "homepage": "https://cepr.org/publications/discussion-papers"}
        records = fetch_preprints.parse_specialized_html(html, source, 2)
        self.assertEqual([record["paper_number"] for record in records], ["DP21229", "DP21228"])

    def test_semicolon_author_bundle_is_flattened_and_deduplicated(self) -> None:
        record = {"authors": ["Lars E.O. Svensson", "Stefan Laseen", "Lars E.O. Svensson; Stefan Laseen"]}
        self.assertTrue(normalize_records.normalize_authors(record))
        self.assertEqual(record["authors"], ["Lars E.O. Svensson", "Stefan Laseen"])

    def test_working_paper_aliases_share_title_identity(self) -> None:
        issue_record = {
            "title": "An Experimental Comparison of Cap- and Intensity-based Pollution Markets",
            "source": "working_papers",
            "source_type": "aggregator",
            "url": "https://nep.repec.org/nep-cna/2026-06-08#p1",
        }
        canonical_record = {
            **issue_record,
            "url": "https://econpapers.repec.org/RePEc:example:paper:1",
        }
        self.assertTrue(record_match_keys(issue_record) & record_match_keys(canonical_record))

    def test_pending_record_is_archived_after_date_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily = root / "daily"
            daily.mkdir()
            pending_path = root / "historical_backfill_pending.json"
            pending_path.write_text(
                json.dumps(
                    [{
                        "title": "A quarantined CEPR paper with a research title",
                        "source": "working_papers",
                        "source_id": "cepr-dp",
                        "paper_number": "DP8177",
                        "url": "https://cepr.org/publications/dp8177",
                    }]
                ),
                encoding="utf-8",
            )

            def fake_enrich(record: dict, _source: dict, *, timeout: int) -> dict:
                record["available_online"] = "2011-01-03"
                record["published_online"] = "2011-01-03"
                record["date_confidence"] = "B"
                record["date_source"] = "publisher_detail"
                return record

            with patch.object(repair_historical_backfill, "enrich_record_from_detail", side_effect=fake_enrich):
                moved, attempted = repair_historical_backfill.repair_pending(
                    pending_path,
                    daily,
                    {"id": "cepr-dp"},
                    limit=1,
                    workers=1,
                    timeout=1,
                )

            self.assertEqual((moved, attempted), (1, 1))
            self.assertEqual(json.loads(pending_path.read_text(encoding="utf-8")), [])
            archived = json.loads((daily / "2011-01-03.json").read_text(encoding="utf-8"))
            self.assertEqual(archived[0]["historical_backfill_status"], "archived_by_official_date")

    def test_unresolved_pending_record_rotates_behind_untouched_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily = root / "daily"
            daily.mkdir()
            pending_path = root / "historical_backfill_pending.json"
            pending_path.write_text(
                json.dumps([
                    {"title": "Blocked old record", "source_id": "cepr-dp", "url": "https://cepr.org/publications/dp1"},
                    {"title": "Untouched old record", "source_id": "cepr-dp", "url": "https://cepr.org/publications/dp2"},
                ]),
                encoding="utf-8",
            )
            with patch.object(repair_historical_backfill, "enrich_record_from_detail", side_effect=lambda record, *_args, **_kwargs: record):
                moved, attempted = repair_historical_backfill.repair_pending(
                    pending_path,
                    daily,
                    {"id": "cepr-dp"},
                    limit=1,
                    workers=1,
                    timeout=1,
                )
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
            self.assertEqual((moved, attempted), (0, 1))
            self.assertEqual([record["title"] for record in pending], ["Untouched old record", "Blocked old record"])
            self.assertEqual(pending[1]["historical_backfill_attempts"], 1)

    def test_seen_only_old_record_is_moved_off_todays_page(self) -> None:
        """A record whose canonical first_seen predates today must leave today.

        The suppression used to live in ``render_site.is_historical_backfill``.
        It now runs one stage earlier, in ``remove_seen_backflow``, which both
        drops the record from today's page and files it under its first-seen
        date. Asserting there keeps the guarantee covered.
        """
        today = "2026-07-21"
        first_seen_date = "2026-02-26"
        record = {
            "id": "cepr-dp-0001",
            "title": "A previously published CEPR paper restored from seen state",
            "url": "https://cepr.org/publications/dp0001",
            "available_online": first_seen_date,
            "date_confidence": "B",
            "detected_at": "2026-07-21T14:43:15+00:00",
        }
        seen = {
            "papers": {
                "cepr-dp-0001": {
                    "title": record["title"],
                    "url": record["url"],
                    "first_seen": f"{first_seen_date}T08:00:00+00:00",
                }
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            daily_dir = Path(tmp) / "daily"
            daily_dir.mkdir()
            (daily_dir / f"{today}.json").write_text(json.dumps([record]), encoding="utf-8")
            seen_path = Path(tmp) / "seen.json"
            seen_path.write_text(json.dumps(seen), encoding="utf-8")

            argv = [
                "remove_seen_backflow.py",
                "--date",
                today,
                "--daily-dir",
                str(daily_dir),
                "--seen",
                str(seen_path),
            ]
            with patch.object(sys, "argv", argv), patch.object(remove_seen_backflow, "record_source"):
                remove_seen_backflow.main()

            today_records = json.loads((daily_dir / f"{today}.json").read_text(encoding="utf-8"))
            restored_records = json.loads((daily_dir / f"{first_seen_date}.json").read_text(encoding="utf-8"))

        self.assertEqual(today_records, [])
        self.assertEqual([item["title"] for item in restored_records], [record["title"]])
        self.assertEqual(restored_records[0]["_restored_from_backflow"], today)


if __name__ == "__main__":
    unittest.main()
