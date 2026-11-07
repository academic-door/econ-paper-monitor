"""Source-specific Chicago JLE full-abstract recovery, no live network in tests."""
from __future__ import annotations

import io
import sys
import unittest
from copy import deepcopy
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import enrich_metadata  # noqa: E402
from sources import uchicago_jle  # noqa: E402

ABSTRACT = (
    "This entirely synthetic test abstract examines the effect of a legal "
    "institution on contracting behavior in a simplified economic model. "
    "It contrasts negotiated outcomes across several policy environments, "
    "considers informational asymmetries and strategic incentives, and "
    "evaluates the influence of enforceability on expected welfare. "
    "The analysis uses illustrative settings to distinguish causal "
    "hypotheses and offers several conclusions for institutional design "
    "without relying on any real publisher article text."
)


def record(doi: str, title: str) -> dict:
    return {
        "journal": "Journal of Law & Economics",
        "doi": doi,
        "title": title,
        "first_seen": "2026-11-01T10:15:00+00:00",
        "available_online": "2026-11-01",
        "published_online": "2026-11-01",
        "date_source": "crossref_published_online",
        "authors": ["Verified Author"],
        "url": f"https://doi.org/{doi}",
    }


def publisher_html(doi: str, title: str, full_abstract: str = ABSTRACT, *,
                   teaser: str | None = None, prefix: str = "Abstract") -> bytes:
    preview = teaser if teaser is not None else ABSTRACT[:200]
    return (
        "<html><head>"
        f'<meta name="dc.Title" content="{title}">'
        f'<meta name="dc.Description" content="{preview}">'
        f'<meta property="og:description" content="{prefix}{full_abstract}">'
        "</head><body>"
        f"<h1>{title}</h1><span>{doi}</span>"
        "</body></html>"
    ).encode("utf-8")


class FakeResponse:
    def __init__(self, body: bytes, url: str, status: int = 200):
        self.contents = io.BytesIO(body)
        self.url = url
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.contents.close()

    def geturl(self):
        return self.url

    def read(self, n: int):
        return self.contents.read(n)


class JleOfficialAbstractTests(unittest.TestCase):
    @patch.object(uchicago_jle.urllib.request, "urlopen")
    def test_all_seven_exact_official_identities(self, urlopen):
        for doi, title in uchicago_jle.JLE_EXACT_ARTICLES.items():
            with self.subTest(doi=doi):
                url = f"https://www.journals.uchicago.edu/doi/{doi}"
                urlopen.return_value = FakeResponse(publisher_html(doi, title), url)
                returned = uchicago_jle.verified_jle_abstract(record(doi, title), 12)
                self.assertEqual(returned["abstract"], ABSTRACT)
                self.assertEqual(returned["abstract_source"], "uchicago_jle_official:og_description")
                self.assertEqual(urlopen.call_args.kwargs["timeout"], 8)
                self.assertEqual(urlopen.call_args.args[0].full_url, url)
        self.assertEqual(urlopen.call_count, 7)

    @patch.object(uchicago_jle.urllib.request, "urlopen")
    def test_wrong_journal_doi_or_title_is_a_noop(self, urlopen):
        doi, title = next(iter(uchicago_jle.JLE_EXACT_ARTICLES.items()))
        self.assertEqual(uchicago_jle.verified_jle_abstract({**record(doi, title), "journal": "Other"}, 8), {})
        self.assertEqual(uchicago_jle.verified_jle_abstract({**record(doi, title), "doi": "10.1086/999999"}, 8), {})
        self.assertEqual(uchicago_jle.verified_jle_abstract({**record(doi, title), "title": "Wrong title"}, 8), {})
        urlopen.assert_not_called()

    @patch.object(uchicago_jle.urllib.request, "urlopen")
    def test_short_preview_ambiguous_or_redirected_body_fails_closed(self, urlopen):
        doi, title = next(iter(uchicago_jle.JLE_EXACT_ARTICLES.items()))
        correct_url = f"https://www.journals.uchicago.edu/doi/{doi}"
        cases = [
            (publisher_html(doi, "An unrelated article title"), correct_url),
            (publisher_html("10.1086/999999", title), correct_url),
            (publisher_html(doi, title, full_abstract="Short teaser"), correct_url),
            (publisher_html(doi, title, full_abstract=ABSTRACT + "..."), correct_url),
            (publisher_html(doi, title, prefix="Intro:"), correct_url),
            (publisher_html(doi, title), "https://untrusted.example/page"),
        ]
        for page, final_url in cases:
            with self.subTest(size=len(page),final_url=final_url):
                urlopen.return_value = FakeResponse(page, final_url)
                self.assertEqual(uchicago_jle.verified_jle_abstract(record(doi, title), 8), {})

    @patch.object(uchicago_jle.urllib.request, "urlopen")
    @patch.object(enrich_metadata, "semantic_scholar_doi_metadata", return_value={})
    @patch.object(enrich_metadata, "openalex_doi_metadata", return_value={})
    @patch.object(enrich_metadata, "crossref_doi_metadata", return_value={})
    def test_abstract_only_recovery_keeps_dates_and_first_seen(self, _c, _o, _s, urlopen):
        doi, title = next(iter(uchicago_jle.JLE_EXACT_ARTICLES.items()))
        entry = record(doi, title)
        before = deepcopy(entry)
        urlopen.return_value = FakeResponse(publisher_html(doi, title), f"https://www.journals.uchicago.edu/doi/{doi}")
        changed, status = enrich_metadata.enrich_abstract_record(entry, timeout=12)
        self.assertTrue(changed)
        self.assertEqual(status, "metadata-updated:uchicago-jle-official")
        self.assertEqual(entry["abstract"], ABSTRACT)
        self.assertEqual(entry["abstract_source"], "uchicago_jle_official:og_description")
        for key, old_value in before.items():
            self.assertEqual(entry[key], old_value)


if __name__ == "__main__":
    unittest.main()
