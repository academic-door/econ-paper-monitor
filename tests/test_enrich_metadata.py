from __future__ import annotations

import sys
import json
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import enrich_metadata  # noqa: E402


class EnrichMetadataTests(unittest.TestCase):
    def test_retry_queue_loads_durable_identity_keys(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.json"
            path.write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "identity": "doi:10.1234/example",
                                "identity_keys": ["url:https://example.test/paper"],
                                "title": "Queued paper",
                                "journal": "Example Journal",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            keys = enrich_metadata.load_retry_identity_keys(path)

        self.assertIn("doi:10.1234/example", keys)
        self.assertIn("url:https://example.test/paper", keys)
        self.assertIn("source-title:example journal:queued paper", keys)

    def test_date_recovery_targets_missing_and_low_confidence_evidence(self) -> None:
        self.assertTrue(enrich_metadata.needs_date_recovery({"date_confidence": "F"}))
        self.assertTrue(
            enrich_metadata.needs_date_recovery(
                {"issue_date": "2026-07-01", "date_confidence": "C"}
            )
        )
        self.assertFalse(
            enrich_metadata.needs_date_recovery(
                {"available_online": "2026-07-01", "date_confidence": "B"}
            )
        )
        self.assertTrue(
            enrich_metadata.needs_date_recovery(
                {"accepted_date": "2026-07-01", "date_confidence": "A"}
            )
        )
        self.assertFalse(
            enrich_metadata.has_ab_date(
                {"accepted_date": "2026-07-01", "date_confidence": "A"}
            )
        )

    @patch.object(enrich_metadata, "fetch_elsevier_json")
    def test_elsevier_full_api_extracts_nested_abstract(self, fetch_mock) -> None:
        fetch_mock.return_value = {
            "full-text-retrieval-response": {
                "coredata": {"pii": "S030438782600163X"},
                "item": {
                    "bibrecord": {
                        "head": {
                            "abstracts": {
                                "abstract": {
                                    "ce:para": "This is a publisher supplied abstract that is intentionally longer than eighty characters so the Elsevier full response parser can retain it."
                                }
                            }
                        }
                    }
                },
            }
        }

        with patch.dict(
            enrich_metadata.os.environ,
            {"ELSEVIER_API_KEY": "test-key", "ELSEVIER_INSTTOKEN": "test-insttoken"},
            clear=False,
        ):
            result = enrich_metadata.elsevier_api_metadata("10.1016/j.jdeveco.2026.103880", timeout=1)

        self.assertEqual(result["abstract_source"], "elsevier_article_api_full")
        self.assertIn("publisher supplied abstract", result["abstract"])
        called_url = fetch_mock.call_args.args[0]
        self.assertIn("view=FULL", called_url)
        self.assertEqual(fetch_mock.call_args.kwargs["api_key"], "test-key")
        self.assertEqual(fetch_mock.call_args.kwargs["insttoken"], "test-insttoken")

    @patch.object(enrich_metadata, "fetch_elsevier_json")
    def test_elsevier_anonymous_api_keeps_core_metadata_fallback(self, fetch_mock) -> None:
        fetch_mock.return_value = {
            "full-text-retrieval-response": {
                "coredata": {
                    "pii": "S030438782600163X",
                    "prism:coverDisplayDate": "Available online 16 July 2026",
                }
            }
        }

        with patch.dict(
            enrich_metadata.os.environ,
            {"ELSEVIER_API_KEY": "", "ELS_API_KEY": "", "ELSEVIER_INSTTOKEN": ""},
            clear=False,
        ):
            result = enrich_metadata.elsevier_api_metadata("10.1016/j.jdeveco.2026.103880", timeout=1)

        self.assertNotIn("view=FULL", fetch_mock.call_args.args[0])
        self.assertEqual(fetch_mock.call_args.kwargs["api_key"], "")
        self.assertEqual(fetch_mock.call_args.kwargs["insttoken"], "")
        self.assertEqual(result["available_online"], "2026-07-16")

    @patch.object(enrich_metadata, "fetch_json")
    def test_crossref_doi_elsevier_created_fallback_uses_utc_date(self, fetch_mock) -> None:
        fetch_mock.return_value = {
            "message": {
                "DOI": "10.1016/j.foodpol.2026.103163",
                "title": ["Ozone pollution, cash crop production, and farmer adaptation: evidence from China"],
                "created": {"date-time": "2026-08-06T21:59:13Z", "date-parts": [[2026, 8, 6]]},
                "issued": {"date-parts": [[2026, 9]]},
            }
        }

        result = enrich_metadata.crossref_doi_metadata("10.1016/j.foodpol.2026.103163", timeout=1)

        self.assertEqual(result["available_online"], "2026-08-06")
        self.assertEqual(result["published_online"], "2026-08-06")
        self.assertEqual(result["date_source"], "crossref_doi_elsevier_created_online")
        self.assertEqual(result["date_confidence"], "C")

    @patch.object(enrich_metadata, "fetch_elsevier_json")
    def test_elsevier_display_date_without_phrase_is_provisional(self, fetch_mock) -> None:
        fetch_mock.return_value = {
            "full-text-retrieval-response": {
                "coredata": {
                    "pii": "S0306919226001326",
                    "prism:coverDisplayDate": "6 August 2026",
                }
            }
        }
        with patch.dict(
            enrich_metadata.os.environ,
            {"ELSEVIER_API_KEY": "test-key"},
            clear=False,
        ):
            result = enrich_metadata.elsevier_api_metadata("10.1016/j.foodpol.2026.103163", timeout=1)

        self.assertEqual(result["available_online"], "2026-08-06")
        self.assertEqual(result["date_source"], "elsevier_article_api_display_date")
        self.assertEqual(result["date_confidence"], "C")

    def test_elsevier_no_date_fields_returns_nothing(self) -> None:
        result = enrich_metadata.elsevier_api_online_date({"dc:title": "Example paper"})
        self.assertEqual(result, {})


class MetadataProviderRetryTests(unittest.TestCase):
    def test_fetch_json_retry_backs_off_on_429(self) -> None:
        import common

        headers = Message()
        headers.add_header("Retry-After", "0")
        exc = urllib.error.HTTPError(
            "https://api.semanticscholar.org/graph/v1/paper/DOI:10.1234/x",
            429,
            "Too Many Requests",
            headers,
            None,
        )
        with patch("common.fetch_json", side_effect=[exc, exc, {"abstract": "ok"}]) as fetch_mock, patch.object(
            common.time, "sleep"
        ):
            result = common.fetch_json_retry(
                "https://api.semanticscholar.org/graph/v1/paper/DOI:10.1234/x",
                timeout=1,
                retries=2,
            )
        self.assertEqual(fetch_mock.call_count, 3)
        self.assertEqual(result, {"abstract": "ok"})

    @patch.object(enrich_metadata, "fetch_json_retry")
    def test_semantic_scholar_404_maps_to_not_found(self, fetch_mock) -> None:
        fetch_mock.side_effect = urllib.error.HTTPError(
            "https://api.semanticscholar.org/paper/DOI:10.1234/x",
            404,
            "Not Found",
            Message(),
            None,
        )
        result = enrich_metadata.semantic_scholar_doi_metadata("10.1234/x", timeout=1)
        self.assertEqual(result.get("_status"), "not_found")

    @patch.object(enrich_metadata, "fetch_json_retry")
    def test_semantic_scholar_429_maps_to_rate_limited(self, fetch_mock) -> None:
        fetch_mock.side_effect = urllib.error.HTTPError(
            "https://api.semanticscholar.org/paper/DOI:10.1234/x",
            429,
            "Too Many Requests",
            Message(),
            None,
        )
        result = enrich_metadata.semantic_scholar_doi_metadata("10.1234/x", timeout=1)
        self.assertEqual(result.get("_status"), "rate_limited")

    @patch.object(enrich_metadata, "fetch_json_retry")
    def test_semantic_scholar_success_extracts_fields(self, fetch_mock) -> None:
        abstract = (
            "This paper estimates the labor market effects of an education "
            "reform using administrative data. Identification comes from a "
            "policy change and the results are robust across specifications."
        )
        fetch_mock.return_value = {
            "abstract": abstract,
            "authors": [{"name": "Alice Author"}],
            "publicationDate": "2026-07-28",
        }
        result = enrich_metadata.semantic_scholar_doi_metadata("10.1234/x", timeout=1)
        self.assertEqual(result["abstract"], abstract)
        self.assertEqual(result["abstract_source"], "semantic_scholar")
        self.assertEqual(result["authors"], ["Alice Author"])
        self.assertEqual(result["published_online"], "2026-07-28")

    def test_semantic_scholar_rate_limited_then_skipped_is_honest(self) -> None:
        enrich_metadata.reset_semantic_scholar_throttle()
        exc = urllib.error.HTTPError(
            "https://api.semanticscholar.org/paper/DOI:10.1234/x",
            429,
            "Too Many Requests",
            Message(),
            None,
        )
        with patch.object(enrich_metadata, "SS_RATE_LIMIT_SKIP_AFTER", 2), patch.object(
            enrich_metadata, "fetch_json_retry", side_effect=exc
        ):
            first = enrich_metadata.semantic_scholar_doi_metadata("10.1234/x", timeout=1)
            second = enrich_metadata.semantic_scholar_doi_metadata("10.1234/y", timeout=1)
            third = enrich_metadata.semantic_scholar_doi_metadata("10.1234/z", timeout=1)
        self.assertEqual(first["_status"], "rate_limited")
        self.assertEqual(second["_status"], "rate_limited")
        self.assertEqual(third["_status"], "skipped_rate_limited")
        self.assertNotEqual(third, {})
        enrich_metadata.reset_semantic_scholar_throttle()

    @patch.object(enrich_metadata, "fetch_json_retry")
    def test_semantic_scholar_api_key_header(self, fetch_mock) -> None:
        enrich_metadata.reset_semantic_scholar_throttle()
        fetch_mock.return_value = {
            "abstract": (
                "A sufficiently detailed abstract for an API-key authenticated "
                "Semantic Scholar response used by the monitor integration tests."
            ),
            "authors": [{"name": "Alice Author"}],
        }
        with patch.dict(
            enrich_metadata.os.environ,
            {"SEMANTIC_SCHOLAR_API_KEY": "test-key"},
            clear=False,
        ):
            enrich_metadata.semantic_scholar_doi_metadata("10.1234/x", timeout=1)
        self.assertEqual(fetch_mock.call_args.kwargs["headers"], {"x-api-key": "test-key"})
        enrich_metadata.reset_semantic_scholar_throttle()

    def test_semantic_scholar_throttle_interval_tracks_api_key(self) -> None:
        enrich_metadata.reset_semantic_scholar_throttle()
        with patch.dict(enrich_metadata.os.environ, {}, clear=True):
            without_key = enrich_metadata.semantic_scholar_throttle_state()
        self.assertFalse(without_key["key_configured"])
        self.assertEqual(without_key["min_interval_seconds"], enrich_metadata.SS_MIN_INTERVAL_SECONDS)

        with patch.dict(
            enrich_metadata.os.environ,
            {"SEMANTIC_SCHOLAR_API_KEY": "test-key"},
            clear=True,
        ):
            with_key = enrich_metadata.semantic_scholar_throttle_state()
        self.assertTrue(with_key["key_configured"])
        self.assertEqual(with_key["min_interval_seconds"], enrich_metadata.SS_KEY_MIN_INTERVAL_SECONDS)
        enrich_metadata.reset_semantic_scholar_throttle()

    @patch.object(enrich_metadata, "fetch_text")
    def test_publisher_proxy_retries_with_jina_key(self, fetch_mock) -> None:
        exc = urllib.error.HTTPError(
            "https://r.jina.ai/http://www.sciencedirect.com/science/article/pii/S0000000000000000",
            429,
            "Too Many Requests",
            Message(),
            None,
        )
        markdown = (
            "# Paper title\n\n"
            "## Abstract\n\n"
            "This publisher abstract is intentionally long enough to pass "
            "validation and prove that the JINA API key retry path preserves "
            "the Authorization header across retries for the monitor."
        )
        fetch_mock.side_effect = [exc, markdown]
        with patch.object(enrich_metadata.time, "sleep"), patch.dict(
            enrich_metadata.os.environ,
            {"JINA_API_KEY": "test-key"},
            clear=False,
        ):
            result = enrich_metadata.publisher_proxy_metadata(
                "https://www.sciencedirect.com/science/article/pii/S0000000000000000",
                timeout=1,
            )
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertIn("JINA API key retry path", result.get("abstract") or "")
        self.assertEqual(fetch_mock.call_args.kwargs["headers"], {"Authorization": "Bearer test-key"})

    def test_extract_markdown_abstract(self) -> None:
        markdown = """# Paper

## Abstract

This abstract is intentionally longer than eighty characters so the parser can verify that only the public abstract section is retained.

## Introduction

This text must not be included.
"""
        abstract = enrich_metadata.extract_markdown_abstract(markdown)
        self.assertIsNotNone(abstract)
        self.assertIn("public abstract section", abstract or "")
        self.assertNotIn("must not be included", abstract or "")

    def test_springer_records_use_article_page_proxy_route(self) -> None:
        record = {
            "doi": "10.1007/s11127-026-01439-w",
            "url": "https://doi.org/10.1007/s11127-026-01439-w",
            "publisher": "Springer",
        }

        self.assertEqual(enrich_metadata.publisher_bucket(record), "Springer")
        self.assertIn(
            "https://link.springer.com/article/10.1007/s11127-026-01439-w",
            enrich_metadata.candidate_urls(record),
        )

    def test_missing_abstract_gets_compact_retry_status(self) -> None:
        record = {"abstract": None}

        changed = enrich_metadata.update_abstract_attempt_status(record, "abstract-not-exposed")

        self.assertTrue(changed)
        self.assertEqual(record["abstract_status"], "摘要暂未公开，系统将自动重试")
        self.assertEqual(record["abstract_status_code"], "missing_retry")
        self.assertEqual(record["abstract_completeness"], "missing")
        self.assertEqual(record["abstract_enrichment_status"], "abstract-not-exposed")

    @patch.object(enrich_metadata, "now", return_value="2026-07-31T00:00:00+00:00")
    def test_publisher_failure_enters_auditable_retry_state(self, _now_mock) -> None:
        record = {}

        changed = enrich_metadata.queue_metadata_retry(record, "blocked-captcha")

        self.assertTrue(changed)
        self.assertEqual(record["metadata_retry_state"]["status"], "queued")
        self.assertEqual(record["metadata_retry_state"]["reason"], "blocked-captcha")
        self.assertEqual(
            record["metadata_retry_state"]["fallbacks"],
            ["crossref-doi", "openalex", "readonly-proxy"],
        )

    @patch.object(enrich_metadata, "fetch_text")
    def test_proxy_reports_captcha_instead_of_missing_abstract(self, fetch_mock) -> None:
        fetch_mock.return_value = "## Are you a robot?\nPlease complete the CAPTCHA challenge."

        result = enrich_metadata.publisher_proxy_metadata(
            "https://www.sciencedirect.com/science/article/pii/S0014498326000343",
            timeout=1,
        )

        self.assertEqual(result, {"_status": "blocked-captcha"})

    def test_weaker_date_metadata_does_not_replace_official_api_date(self) -> None:
        record = {
            "available_online": "2026-07-14",
            "published_online": "2026-07-14",
            "date_source": "elsevier_article_api",
            "date_confidence": "B",
        }
        incoming = {
            "available_online": "2026-07-15",
            "published_online": "2026-07-15",
            "date_source": "crossref_doi_elsevier_created_online",
            "date_confidence": "C",
            "abstract": "This is a sufficiently long abstract that should still be merged even when weaker date metadata is rejected by confidence precedence.",
        }

        changed = enrich_metadata.merge_metadata(record, incoming)

        self.assertTrue(changed)
        self.assertEqual(record["available_online"], "2026-07-14")
        self.assertEqual(record["date_source"], "elsevier_article_api")
        self.assertTrue(record["abstract"].startswith("This is a sufficiently long abstract"))

    @patch.object(enrich_metadata, "openalex_doi_metadata")
    @patch.object(enrich_metadata, "crossref_doi_metadata")
    @patch.object(enrich_metadata, "publisher_proxy_metadata")
    @patch.object(enrich_metadata, "elsevier_api_metadata")
    def test_abstract_only_route_skips_blocked_publisher_html(
        self,
        elsevier_mock,
        proxy_mock,
        crossref_mock,
        openalex_mock,
    ) -> None:
        crossref_mock.return_value = {}
        openalex_mock.return_value = {}
        elsevier_mock.return_value = {"pii": "S0095069626001166"}
        proxy_mock.return_value = {
            "abstract": "This abstract-only fallback is intentionally long enough to verify the fast path without requesting the blocked publisher HTML page.",
            "abstract_source": "publisher_page_via_readonly_proxy",
        }
        record = {
            "doi": "10.1016/j.jeem.2026.103396",
            "url": "https://doi.org/10.1016/j.jeem.2026.103396",
            "source_type": "journal",
        }

        changed, status = enrich_metadata.enrich_abstract_record(record, timeout=1)

        self.assertTrue(changed)
        self.assertEqual(status, "abstract-updated")
        self.assertTrue(record["abstract"].startswith("This abstract-only fallback"))

    @patch("fetch_preprints.enrich_record_from_proxy")
    def test_abstract_only_route_includes_cepr_working_papers(self, proxy_mock) -> None:
        proxy_mock.side_effect = lambda record, _source_id, *, timeout: record.update(
            {
                "abstract": "This CEPR abstract is long enough to prove working-paper records are included in abstract-only retries.",
                "authors": ["First Author"],
            }
        )
        record = {
            "source_id": "cepr-dp",
            "source": "working_papers",
            "source_type": "working_paper",
            "url": "https://cepr.org/publications/dp20328",
        }

        changed, status = enrich_metadata.enrich_abstract_record(record, timeout=1)

        self.assertTrue(changed)
        self.assertEqual(status, "abstract-updated:readonly-proxy")
        proxy_mock.assert_called_once_with(record, "cepr-dp", timeout=1)

    @patch.object(enrich_metadata, "publisher_proxy_metadata")
    @patch.object(enrich_metadata, "elsevier_api_metadata")
    @patch.object(enrich_metadata, "api_fallback_metadata")
    @patch.object(enrich_metadata, "fetch_text_and_url")
    @patch.object(enrich_metadata, "crossref_doi_metadata")
    def test_elsevier_date_does_not_stop_abstract_backfill(
        self,
        crossref_mock,
        fetch_mock,
        api_fallback_mock,
        elsevier_mock,
        proxy_mock,
    ) -> None:
        crossref_mock.return_value = {
            "available_online": "2026-07-15",
            "published_online": "2026-07-15",
            "date_source": "crossref_doi_elsevier_created_online",
            "date_confidence": "C",
        }
        fetch_mock.side_effect = OSError("publisher blocked")
        api_fallback_mock.return_value = ({}, "api-fallback-empty")
        elsevier_mock.return_value = {"pii": "S0095069626001166"}
        proxy_mock.return_value = {
            "abstract": "This publisher abstract is intentionally long enough to pass validation and prove that date metadata no longer stops abstract enrichment.",
            "abstract_source": "publisher_page_via_readonly_proxy",
        }
        record = {
            "doi": "10.1016/j.jeem.2026.103396",
            "url": "https://doi.org/10.1016/j.jeem.2026.103396",
            "source_type": "journal",
        }

        changed, status = enrich_metadata.enrich_record(record, timeout=1, allow_proxy_abstract=True)

        self.assertTrue(changed)
        self.assertEqual(status, "publisher-proxy-abstract")
        self.assertEqual(record["pii"], "S0095069626001166")
        self.assertTrue(record["abstract"].startswith("This publisher abstract"))


if __name__ == "__main__":
    unittest.main()
