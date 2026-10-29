"""N2 source-isolated publisher abstract recovery regression fixtures.

Tests never request real publisher URLs or copy real publisher text.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from copy import deepcopy
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import enrich_metadata  # noqa: E402
import apply_overrides  # noqa: E402

DOI = "10.1007/s10797-026-10011-7"
TITLE = "Optimal taxation of robotic capital with a binding minimum wage"
ABSTRACT = (
    "This original test fixture analyzes resource allocation and policy choices "
    "under a hypothetical economic model. It reports multiple conditions, "
    "explains the assumptions used in the analysis, and offers carefully bounded "
    "conclusions about policy interpretation."
)


class FakeResponse:
    def __init__(self, body: bytes, *, url: str | None = None, status: int = 200):
        self.data = io.BytesIO(body)
        self.url = url or f"https://link.springer.com/article/{DOI}"
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.data.close()

    def geturl(self):
        return self.url

    def read(self, n: int):
        return self.data.read(n)


def html_fixture(title: str = TITLE, doi: str = DOI, abstract: str = ABSTRACT) -> bytes:
    return (
        "<html><head>"
        f'<meta name="dc.description" content="{abstract}">'
        "</head><body>"
        f"<h1>{title}</h1><span>{doi}</span>"
        "<h2>Abstract</h2><p>Publisher content deliberately omitted in tests.</p>"
        "</body></html>"
    ).encode("utf-8")


def record(**kwargs) -> dict:
    base = {
        "journal": "International Tax and Public Finance",
        "doi": DOI,
        "title": TITLE,
        "first_seen": "2026-10-27T13:13:52+00:00",
        "available_online": "2026-10-27",
        "date_source": "crossref_published_online",
        "authors": ["Fixture Author"],
        "url": f"https://doi.org/{DOI}",
    }
    base.update(kwargs)
    return base


class SpringerTaxRecoveryTests(unittest.TestCase):
    @patch.object(enrich_metadata.urllib.request, "urlopen")
    def test_verified_publisher_metadata_only(self, urlopen):
        urlopen.return_value = FakeResponse(html_fixture())
        result = enrich_metadata.verified_springer_tax_abstract(record(), timeout=25)
        self.assertEqual(result["abstract"], ABSTRACT)
        self.assertEqual(result["abstract_source"], "springer_publisher_meta:dc.description")
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 8)
        self.assertEqual(urlopen.call_args.args[0].full_url, f"https://link.springer.com/article/{DOI}")

    @patch.object(enrich_metadata.urllib.request, "urlopen")
    def test_exact_title_identity_required(self, urlopen):
        urlopen.return_value = FakeResponse(html_fixture(title="A different study about robot taxation"))
        self.assertEqual(enrich_metadata.verified_springer_tax_abstract(record(), 5), {})

    @patch.object(enrich_metadata.urllib.request, "urlopen")
    def test_exact_doi_identity_required(self, urlopen):
        urlopen.return_value = FakeResponse(html_fixture(doi="10.1007/s10797-026-99999-9"))
        self.assertEqual(enrich_metadata.verified_springer_tax_abstract(record(), 5), {})

    @patch.object(enrich_metadata.urllib.request, "urlopen")
    def test_redirect_to_untrusted_host_is_rejected(self, urlopen):
        urlopen.return_value = FakeResponse(html_fixture(), url="https://other.test/article")
        self.assertEqual(enrich_metadata.verified_springer_tax_abstract(record(), 5), {})

    @patch.object(enrich_metadata.urllib.request, "urlopen")
    def test_short_teaser_and_truncated_description_rejected(self, urlopen):
        for text in ("Brief publisher description.", ABSTRACT + "...", "This is a preview of subscription content. " + ABSTRACT):
            urlopen.return_value = FakeResponse(html_fixture(abstract=text))
            self.assertEqual(enrich_metadata.verified_springer_tax_abstract(record(), 5), {})

    @patch.object(enrich_metadata.urllib.request, "urlopen")
    def test_hard_response_limit_does_not_admit_oversized_payload(self, urlopen):
        urlopen.return_value = FakeResponse(html_fixture() + b" " * 1_048_577)
        self.assertEqual(enrich_metadata.verified_springer_tax_abstract(record(), 5), {})

    @patch.object(enrich_metadata.urllib.request, "urlopen")
    def test_http_403_is_a_noop_not_a_fake_abstract(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError(
            "https://link.springer.com", 403, "Forbidden", Message(), None
        )
        self.assertEqual(enrich_metadata.verified_springer_tax_abstract(record(), 5), {})

    @patch.object(enrich_metadata.urllib.request, "urlopen")
    def test_journal_and_doi_scope_are_exact(self, urlopen):
        self.assertEqual(enrich_metadata.verified_springer_tax_abstract(record(journal="Other Journal"), 5), {})
        self.assertEqual(enrich_metadata.verified_springer_tax_abstract(record(doi="10.1007/s10797-026-88888-8"), 5), {})
        urlopen.assert_not_called()

    @patch.object(enrich_metadata, "verified_springer_tax_abstract")
    @patch.object(enrich_metadata, "semantic_scholar_doi_metadata", return_value={})
    @patch.object(enrich_metadata, "openalex_doi_metadata", return_value={})
    @patch.object(enrich_metadata, "crossref_doi_metadata", return_value={})
    def test_abstract_only_integration_preserves_first_seen_and_dates(self, _cross, _open, _sem, springer):
        springer.return_value = {"abstract": ABSTRACT, "abstract_source": "springer_publisher_meta:dc.description"}
        entry = record()
        before = {k: entry[k] for k in ("first_seen", "available_online", "date_source", "authors", "doi", "title")}
        changed, status = enrich_metadata.enrich_abstract_record(entry, timeout=8)
        self.assertTrue(changed)
        self.assertEqual(status, "metadata-updated:springer-publisher-meta")
        self.assertEqual(entry["abstract"], ABSTRACT)
        self.assertEqual(entry["abstract_source"], "springer_publisher_meta:dc.description")
        self.assertEqual({k: entry[k] for k in before}, before)


    def test_index_abstract_retry_cannot_replace_publisher_date_with_preprint_date(self):
        entry = record(
            doi="10.1007/s10797-026-10008-2",
            title="A poorly targeted subsidy? The fiscal and social costs of EU VAT expenditures",
            date_confidence="C",
            published_online="2026-10-24",
            available_online="2026-10-24",
            raw_data={"date_evidence": [{"source": "crossref-doi", "published_online": "2026-10-24"}]},
        )
        original = deepcopy(entry)
        crossref = {
            "available_online": "2026-10-24",
            "published_online": "2026-10-24",
            "date_confidence": "C",
            "date_source": "crossref_doi_published_online",
            "authors": ["Different Author"],
        }
        # April 14 is the same-titled Research Square PREPRINT, not the
        # Springer journal article's October 24 first publication date.
        openalex = {
            "available_online": "2026-04-14",
            "published_online": "2026-04-14",
            "date_confidence": "C",
            "date_source": "openalex_publication_date",
            "abstract": ABSTRACT,
            "abstract_source": "openalex",
        }
        with (
            patch.object(enrich_metadata, "crossref_doi_metadata", return_value=crossref),
            patch.object(enrich_metadata, "openalex_doi_metadata", return_value=openalex),
            patch.object(enrich_metadata, "semantic_scholar_doi_metadata", return_value={}),
            patch.object(enrich_metadata, "verified_springer_tax_abstract") as publisher,
        ):
            changed, status = enrich_metadata.enrich_abstract_record(entry, timeout=8)
        self.assertTrue(changed)
        self.assertEqual(status, "metadata-updated:openalex")
        self.assertEqual(entry["abstract"], ABSTRACT)
        self.assertEqual(entry["abstract_source"], "openalex")
        publisher.assert_not_called()
        for key in ("doi", "title", "first_seen", "available_online", "published_online",
                    "date_source", "date_confidence", "authors", "raw_data"):
            self.assertEqual(entry[key], original[key], key)

    def test_missing_abstract_does_not_write_date_evidence_from_indexes(self):
        entry = record(
            date_confidence="C",
            published_online="2026-10-27",
            raw_data={"date_evidence": [{"source": "crossref", "published_online": "2026-10-27"}]},
        )
        original = deepcopy(entry)
        with (
            patch.object(enrich_metadata, "crossref_doi_metadata", return_value={
                "published_online": "2026-04-14", "date_source": "crossref_doi_published_online",
                "date_confidence": "C", "authors": ["Replacement Author"],
            }),
            patch.object(enrich_metadata, "openalex_doi_metadata", return_value={}),
            patch.object(enrich_metadata, "semantic_scholar_doi_metadata", return_value={}),
            patch.object(enrich_metadata, "verified_springer_tax_abstract", return_value={}),
        ):
            changed, status = enrich_metadata.enrich_abstract_record(entry, timeout=8)
        self.assertFalse(changed)
        self.assertEqual(status, "abstract-route-missing")
        self.assertEqual(entry, original)

    def test_abstract_only_can_fill_missing_authors_without_date_writes(self):
        entry = record(authors=[], date_confidence="C", published_online="2026-10-27")
        original = deepcopy(entry)
        with (
            patch.object(enrich_metadata, "crossref_doi_metadata", return_value={
                "authors": ["Verified Author"],
                "published_online": "2026-04-14",
                "date_source": "crossref_doi_published_online",
                "date_confidence": "C",
            }),
            patch.object(enrich_metadata, "openalex_doi_metadata", return_value={}),
            patch.object(enrich_metadata, "semantic_scholar_doi_metadata", return_value={}),
            patch.object(enrich_metadata, "verified_springer_tax_abstract", return_value={}),
        ):
            changed, status = enrich_metadata.enrich_abstract_record(entry, timeout=8)
        self.assertTrue(changed)
        self.assertEqual(status, "metadata-only")
        self.assertEqual(entry["authors"], ["Verified Author"])
        for key in ("doi", "title", "first_seen", "available_online", "published_online",
                    "date_source", "date_confidence"):
            self.assertEqual(entry[key], original[key], key)

    def test_elsevier_abstract_only_keeps_date_fields(self):
        entry = record(
            journal="Energy Economics", doi="10.1016/j.eneco.2026.100001",
            date_confidence="C", published_online="2026-10-27",
        )
        original = deepcopy(entry)
        with (
            patch.object(enrich_metadata, "crossref_doi_metadata", return_value={}),
            patch.object(enrich_metadata, "openalex_doi_metadata", return_value={}),
            patch.object(enrich_metadata, "semantic_scholar_doi_metadata", return_value={}),
            patch.object(enrich_metadata, "elsevier_api_metadata", return_value={
                "abstract": ABSTRACT,
                "abstract_source": "elsevier_article_api",
                "published_online": "2026-04-14",
                "date_source": "elsevier_article_api",
                "date_confidence": "A",
            }),
        ):
            changed, status = enrich_metadata.enrich_abstract_record(entry, timeout=8)
        self.assertTrue(changed)
        self.assertEqual(status, "metadata-updated:elsevier-article-api")
        self.assertEqual(entry["abstract"], ABSTRACT)
        for key in ("doi", "title", "first_seen", "available_online", "published_online",
                    "date_source", "date_confidence", "authors"):
            self.assertEqual(entry[key], original[key], key)


    def test_explicit_date_only_fallback_still_updates_date_metadata(self):
        entry = record(
            date_confidence="C", published_online="2026-10-27",
            raw_data={"date_evidence": []},
        )
        original_first_seen = entry["first_seen"]
        with (
            patch.object(enrich_metadata, "crossref_doi_metadata", return_value={
                "available_online": "2026-10-26",
                "published_online": "2026-10-26",
                "date_source": "crossref_doi_published_online",
                "date_confidence": "A",
            }),
            patch.object(enrich_metadata, "openalex_doi_metadata", return_value={}),
            patch.object(enrich_metadata, "semantic_scholar_doi_metadata", return_value={}),
            patch.object(enrich_metadata, "verified_springer_tax_abstract", return_value={}),
        ):
            changed, status = enrich_metadata.enrich_abstract_record(
                entry, timeout=8, allow_date_metadata=True
            )
        self.assertTrue(changed)
        self.assertEqual(status, "metadata-only")
        self.assertEqual(entry["available_online"], "2026-10-26")
        self.assertEqual(entry["published_online"], "2026-10-26")
        self.assertEqual(entry["date_source"], "crossref_doi_published_online")
        self.assertEqual(entry["first_seen"], original_first_seen)
        self.assertEqual(entry["raw_data"]["date_evidence"][0]["source"], "crossref-doi")


class VerifiedSpringerOverrideAcceptanceTests(unittest.TestCase):
    EXPECTED = {
        "10.1007/s10797-026-10011-7": ("2026-10-27", 1542),
        "10.1007/s10797-026-10004-6": ("2026-10-27", 888),
        "10.1007/s10797-026-10009-1": ("2026-10-24", 1081),
        "10.1007/s10797-026-10008-2": ("2026-10-24", 1650),
        "10.1007/s10797-026-09999-9": ("2026-10-24", 532),
        "10.1007/s10797-026-10010-8": ("2026-10-22", 1455),
    }

    def test_all_six_exact_source_matched_overrides_are_idempotent_and_preserve_dates(self):
        root = Path(__file__).resolve().parents[1]
        overrides = apply_overrides.load_overrides(root / "data" / "manual_overrides.yml")
        records = [
            record
            for day in ("2026-10-22", "2026-10-24", "2026-10-27")
            for record in json.loads((root / "data" / "daily" / f"{day}.json").read_text(encoding="utf-8"))
            if record.get("doi") in self.EXPECTED
        ]
        self.assertEqual({entry["doi"] for entry in records}, set(self.EXPECTED))
        for entry in records:
            doi = entry["doi"]
            with self.subTest(doi=doi):
                online_date, expected_length = self.EXPECTED[doi]
                override = overrides[doi]
                self.assertEqual(override["abstract_source"], "publisher_page_verified")
                self.assertEqual(len(override["abstract"]), expected_length)
                self.assertEqual(entry["published_online"], online_date)
                original = deepcopy(entry)
                self.assertTrue(apply_overrides.apply_to_record(entry, override))
                self.assertFalse(apply_overrides.apply_to_record(entry, override))
                self.assertEqual(entry["abstract"], override["abstract"])
                self.assertEqual(entry["abstract_source"], "publisher_page_verified")
                self.assertEqual(entry["abstract_status_code"], "available")
                self.assertEqual(entry["abstract_completeness"], "full")
                self.assertEqual(entry["abstract_enrichment_status"], "available")
                self.assertNotIn("abstract_status", entry)
                for key in (
                    "first_seen", "available_online", "published_online", "date_source",
                    "date_confidence", "doi", "title", "authors", "raw_data",
                ):
                    self.assertEqual(entry.get(key), original.get(key), key)

    def test_seen_ledger_six_doi_overrides_are_identity_and_date_preserving(self):
        # CI has repository-local access to the complete 41MB seen ledger;
        # chat-level GitHub contents API cannot independently read it.
        root = Path(__file__).resolve().parents[1]
        overrides = apply_overrides.load_overrides(root / "data" / "manual_overrides.yml")
        seen = json.loads((root / "data" / "seen.json").read_text(encoding="utf-8"))
        papers = seen["papers"]
        for doi in self.EXPECTED:
            with self.subTest(doi=doi):
                entry = papers[f"doi:{doi}"]
                original = deepcopy(entry)
                self.assertTrue(apply_overrides.apply_to_record(entry, overrides[doi]))
                self.assertEqual(entry["abstract"], overrides[doi]["abstract"])
                self.assertEqual(entry["abstract_source"], "publisher_page_verified")
                self.assertEqual(entry["abstract_status_code"], "available")
                self.assertEqual(entry["abstract_completeness"], "full")
                for key in (
                    "first_seen", "available_online", "published_online", "date_source",
                    "date_confidence", "doi", "title", "authors", "raw_data",
                ):
                    self.assertEqual(entry.get(key), original.get(key), key)
                self.assertFalse(apply_overrides.apply_to_record(entry, overrides[doi]))

    def test_non_abstract_override_keeps_missing_state(self):
        entry = record(
            abstract_status_code="missing_retry",
            abstract_completeness="missing",
            abstract_enrichment_status="metadata-only",
            abstract_status="摘要暂未公开，系统将自动重试",
        )
        self.assertTrue(apply_overrides.apply_to_record(entry, {"title_zh": "标题"}))
        self.assertEqual(entry["abstract_status_code"], "missing_retry")
        self.assertEqual(entry["abstract_completeness"], "missing")
        self.assertEqual(entry["abstract_enrichment_status"], "metadata-only")
        self.assertIn("abstract_status", entry)

    def test_empty_abstract_does_not_set_available_status(self):
        entry = record(
            abstract_status_code="missing_retry",
            abstract_completeness="missing",
            abstract_enrichment_status="metadata-only",
        )
        self.assertFalse(apply_overrides.apply_to_record(entry, {}))
        self.assertEqual(entry["abstract_status_code"], "missing_retry")


if __name__ == "__main__":
    unittest.main()
