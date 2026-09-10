from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import enrich_metadata  # noqa: E402


class TaylorFrancisAbstractRecoveryTests(unittest.TestCase):
    def test_candidate_urls_prefer_tandf_abstract_surface_before_full(self) -> None:
        doi = "10.1080/07350015.2026.2729095"
        urls = enrich_metadata.candidate_urls(
            {
                "doi": doi,
                "url": f"https://doi.org/{doi}",
                "publisher": "Taylor & Francis",
            }
        )

        abstract_url = f"https://www.tandfonline.com/doi/abs/{doi}"
        full_url = f"https://www.tandfonline.com/doi/full/{doi}"
        self.assertIn(abstract_url, urls)
        self.assertIn(full_url, urls)
        self.assertLess(urls.index(abstract_url), urls.index(full_url))

    def test_truncated_generic_meta_is_not_promoted_to_full_abstract(self) -> None:
        truncated = (
            "This Taylor and Francis description is deliberately longer than eighty characters, "
            "but it is only a publisher search snippet and visibly ends before the full abstract..."
        )
        html = f'<html><head><meta name="dc.description" content="{truncated}"></head><body></body></html>'

        metadata = enrich_metadata.extract_page_metadata(html)

        self.assertNotIn("abstract", metadata)

    def test_unicode_ellipsis_generic_meta_is_not_promoted_to_full_abstract(self) -> None:
        truncated = (
            "This publisher social description is deliberately long enough to pass the old length gate, "
            "while remaining visibly truncated at the end of the snippet…"
        )
        html = f'<html><head><meta property="og:description" content="{truncated}"></head><body></body></html>'

        metadata = enrich_metadata.extract_page_metadata(html)

        self.assertNotIn("abstract", metadata)

    def test_citation_abstract_remains_authoritative_even_if_it_ends_with_ellipsis(self) -> None:
        abstract = (
            "This citation abstract is an authoritative publisher abstract field and is intentionally long "
            "enough to remain accepted even when the publisher-supplied abstract itself ends with an ellipsis..."
        )
        html = f'<html><head><meta name="citation_abstract" content="{abstract}"></head><body></body></html>'

        metadata = enrich_metadata.extract_page_metadata(html)

        self.assertEqual(metadata.get("abstract"), abstract)
        self.assertEqual(metadata.get("abstract_source"), "publisher_meta:citation_abstract")

    def test_complete_generic_meta_without_truncation_remains_accepted(self) -> None:
        abstract = (
            "This complete publisher description is intentionally longer than eighty characters and ends "
            "with a normal sentence terminator so the generic metadata fallback remains useful."
        )
        html = f'<html><head><meta name="description" content="{abstract}"></head><body></body></html>'

        metadata = enrich_metadata.extract_page_metadata(html)

        self.assertEqual(metadata.get("abstract"), abstract)
        self.assertEqual(metadata.get("abstract_source"), "publisher_meta:description")

    @patch.object(enrich_metadata, "publisher_proxy_metadata")
    @patch.object(enrich_metadata, "semantic_scholar_doi_metadata")
    @patch.object(enrich_metadata, "openalex_doi_metadata")
    @patch.object(enrich_metadata, "crossref_doi_metadata")
    def test_tandf_abstract_only_route_uses_abs_surface_and_preserves_first_seen(
        self,
        crossref_mock,
        openalex_mock,
        semantic_mock,
        proxy_mock,
    ) -> None:
        crossref_mock.return_value = {}
        openalex_mock.return_value = {}
        semantic_mock.return_value = {}
        recovered = (
            "This authoritative Taylor and Francis abstract is intentionally long enough to verify that the "
            "abstract-only retry route uses the canonical abstract surface instead of the full article shell."
        )
        proxy_mock.return_value = {
            "abstract": recovered,
            "abstract_source": "publisher_page_via_readonly_proxy",
        }
        first_seen = "2026-09-08T12:34:56Z"
        record = {
            "doi": "10.1080/00036846.2026.2730528",
            "url": "https://doi.org/10.1080/00036846.2026.2730528",
            "publisher": "Taylor & Francis",
            "source_type": "journal",
            "first_seen": first_seen,
        }

        changed, status = enrich_metadata.enrich_abstract_record(record, timeout=1)

        self.assertTrue(changed)
        self.assertEqual(status, "abstract-updated")
        self.assertEqual(record.get("abstract"), recovered)
        self.assertEqual(record.get("first_seen"), first_seen)
        proxy_mock.assert_called_once_with(
            "https://www.tandfonline.com/doi/abs/10.1080/00036846.2026.2730528",
            1,
        )

    def test_non_tandf_candidate_route_is_unchanged(self) -> None:
        doi = "10.1111/iere.70111"
        urls = enrich_metadata.candidate_urls({"doi": doi, "url": f"https://doi.org/{doi}"})

        self.assertIn(f"https://onlinelibrary.wiley.com/doi/full/{doi}", urls)
        self.assertFalse(any("tandfonline.com" in url for url in urls))


if __name__ == "__main__":
    unittest.main()
