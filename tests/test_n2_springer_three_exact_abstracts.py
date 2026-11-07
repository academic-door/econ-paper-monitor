"""Three exact Springer publisher abstracts: bounded, identity-verified recovery."""
from __future__ import annotations

import io
import sys
import unittest
import urllib.error
from copy import deepcopy
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import enrich_metadata  # noqa: E402


TARGETS = {
    "10.1007/s10640-026-01129-7": (
        "Environmental and Resource Economics",
        "Environmental Policy and Automation",
    ),
    "10.1007/s00148-026-01220-9": (
        "Journal of Population Economics",
        "The externalities of private tutoring on students’ academic and noncognitive outcomes: evidence and mechanisms",
    ),
    "10.1007/s00182-026-01017-w": (
        "International Journal of Game Theory",
        "Competition for market share, oversupply, and price rigidity under sequential supply decisions",
    ),
}
ABSTRACT = (
    "A complete verified abstract in this hypothetical unit-test fixture "
    "examines the consequences of policy choices under a specific theoretical "
    "model and quantifies how decisions affect household welfare, incentives, "
    "production, and labor supply across carefully described scenarios. "
    "It provides an original illustrative interpretation without publisher text."
)


class FakeResponse:
    def __init__(self, body: bytes, *, url: str, status: int = 200):
        self.body = io.BytesIO(body)
        self.url = url
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.body.close()

    def geturl(self):
        return self.url

    def read(self, n):
        return self.body.read(n)


def fake_html(title: str, doi: str, abstract: str = ABSTRACT) -> bytes:
    return (
        "<html><head>"
        f'<meta name="citation_title" content="{title}">'
        f'<meta name="dc.description" content="{abstract}">'
        "</head><body>"
        f"<h1>{title}</h1><span>{doi}</span>"
        "</body></html>"
    ).encode("utf-8")


def rec(doi: str, journal: str, title: str) -> dict:
    return {
        "doi": doi,
        "title": title,
        "journal": journal,
        "first_seen": "2026-11-01T07:10:00+00:00",
        "available_online": "2026-10-28",
        "published_online": "2026-10-28",
        "date_source": "crossref_published_online",
        "authors": ["Official Author"],
        "source_type": "journal",
        "url": f"https://doi.org/{doi}",
    }


class ThreeExactSpringerMetadataTests(unittest.TestCase):
    @patch.object(enrich_metadata.urllib.request, "urlopen")
    def test_three_verified_identities_are_in_scope(self, urlopen):
        for doi, (journal, title) in TARGETS.items():
            with self.subTest(doi=doi):
                urlopen.return_value = FakeResponse(
                    fake_html(title, doi), url=f"https://link.springer.com/article/{doi}"
                )
                result = enrich_metadata.verified_springer_tax_abstract(rec(doi, journal, title), timeout=12)
                self.assertEqual(result["abstract"], ABSTRACT)
                self.assertEqual(result["abstract_source"], "springer_publisher_meta:dc.description")
                self.assertEqual(urlopen.call_args.kwargs["timeout"], 8)
                self.assertEqual(urlopen.call_args.args[0].full_url, f"https://link.springer.com/article/{doi}")
        self.assertEqual(urlopen.call_count, len(TARGETS))

    @patch.object(enrich_metadata.urllib.request, "urlopen")
    def test_scope_does_not_leak_to_other_doi_or_journal(self, urlopen):
        doi, (journal, title) = next(iter(TARGETS.items()))
        self.assertEqual(enrich_metadata.verified_springer_tax_abstract(rec(doi, "Other Journal", title), 8), {})
        self.assertEqual(enrich_metadata.verified_springer_tax_abstract(rec("10.1007/s00148-026-99999-9", journal, title), 8), {})
        urlopen.assert_not_called()

    @patch.object(enrich_metadata.urllib.request, "urlopen")
    def test_identity_mismatch_truncation_and_redirect_fail_closed(self, urlopen):
        doi, (journal, title) = next(iter(TARGETS.items()))
        entry = rec(doi, journal, title)
        cases = [
            (fake_html("Completely different journal paper title", doi), f"https://link.springer.com/article/{doi}"),
            (fake_html(title, "10.1007/s00182-026-88888-8"), f"https://link.springer.com/article/{doi}"),
            (fake_html(title, doi, ABSTRACT + "..."), f"https://link.springer.com/article/{doi}"),
            (fake_html(title, doi, "Short teaser."), f"https://link.springer.com/article/{doi}"),
            (fake_html(title, doi), "https://login.example.com/"),
        ]
        for data, url in cases:
            with self.subTest(url=url, body_length=len(data)):
                urlopen.return_value = FakeResponse(data, url=url)
                self.assertEqual(enrich_metadata.verified_springer_tax_abstract(entry, 8), {})

    @patch.object(enrich_metadata.urllib.request, "urlopen")
    @patch.object(enrich_metadata, "semantic_scholar_doi_metadata", return_value={})
    @patch.object(enrich_metadata, "openalex_doi_metadata", return_value={})
    @patch.object(enrich_metadata, "crossref_doi_metadata", return_value={})
    def test_abstract_only_preserves_all_provenance(self, _cross, _open, _semantic, urlopen):
        doi, (journal, title) = next(iter(TARGETS.items()))
        entry = rec(doi, journal, title)
        before = deepcopy(entry)
        urlopen.return_value = FakeResponse(
            fake_html(title, doi), url=f"https://link.springer.com/article/{doi}"
        )
        changed, status = enrich_metadata.enrich_abstract_record(entry, 12)
        self.assertTrue(changed)
        self.assertEqual(status, "metadata-updated:springer-publisher-meta")
        self.assertEqual(entry["abstract"], ABSTRACT)
        self.assertEqual(entry["abstract_source"], "springer_publisher_meta:dc.description")
        for field in before:
            self.assertEqual(entry[field], before[field])


if __name__ == "__main__":
    unittest.main()
