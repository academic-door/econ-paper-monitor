"""N2 source-isolated publisher abstract recovery regression fixtures.

Tests never request real publisher URLs or copy real publisher text.
"""
from __future__ import annotations

import io
import sys
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import enrich_metadata  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
