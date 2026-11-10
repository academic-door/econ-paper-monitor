"""Exactly scoped official Applied Economics full abstracts: bounded fixtures."""
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
from sources import tandf_applied_economics as tandf  # noqa: E402


ABSTRACT = (
    "This synthetic verified article abstract examines how institutional "
    "rules and economic incentives shape observed outcomes across multiple "
    "regions. The empirical framework compares policy exposures, estimates "
    "heterogeneous treatment effects, and tests potential confounding "
    "mechanisms. Robustness exercises show how the main findings respond "
    "to alternative assumptions, and the resulting evidence informs "
    "interpretation of economic policy in the modelled setting."
)


class FakeResponse:
    def __init__(self, body: bytes, url: str, status: int = 200):
        self.data = io.BytesIO(body)
        self.url = url
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.data.close()

    def geturl(self):
        return self.url

    def read(self, amount: int):
        return self.data.read(amount)


def html_fixture(doi: str, title: str, abstract: str = ABSTRACT, *,
                 abstract_id: str = "abstractId1", heading: str = "ABSTRACT") -> bytes:
    return (
        "<html><head>"
        f'<meta name="dc.Title" content="{title}">'
        f'<meta name="dc.Description" content="{ABSTRACT[:200]}">'
        "</head><body>"
        f"<h1>{title}</h1><span>{doi}</span>"
        f'<div id="{abstract_id}" class="hlFld-Abstract">'
        f'<h2 class="section-heading-2" id="abstract">{heading}</h2>'
        f'<p class="last">{abstract}</p>'
        "</div><div class=\"abstractKeywords\"><p>KEYWORDS</p></div>"
        "</body></html>"
    ).encode("utf-8")


def record(doi: str, title: str) -> dict:
    return {
        "journal": "Applied Economics",
        "doi": doi,
        "title": title,
        "source_type": "journal",
        "url": f"https://doi.org/{doi}",
        "first_seen": "2026-11-06T06:22:17+00:00",
        "available_online": "2026-11-06",
        "published_online": "2026-11-06",
        "date_source": "crossref_doi_published_online",
        "authors": ["Original Verified Author"],
    }


class AppliedEconomicsFirstPartyTests(unittest.TestCase):
    @patch.object(tandf.urllib.request, "urlopen")
    def test_allowlisted_exact_articles_have_complete_abstracts(self, urlopen):
        for doi, title in tandf.APPLIED_ECONOMICS_EXACT.items():
            with self.subTest(doi=doi):
                url = f"https://www.tandfonline.com/doi/abs/{doi}"
                urlopen.return_value = FakeResponse(html_fixture(doi, title), url)
                got = tandf.verified_applied_economics_abstract(record(doi, title), 14)
                self.assertEqual(got["abstract"], ABSTRACT)
                self.assertEqual(got["abstract_source"], "tandf_applied_economics_official:abstractId1")
                self.assertEqual(urlopen.call_args.kwargs["timeout"], 8)
                self.assertEqual(urlopen.call_args.args[0].full_url, url)
        self.assertEqual(urlopen.call_count, len(tandf.APPLIED_ECONOMICS_EXACT))

    @patch.object(tandf.urllib.request, "urlopen")
    def test_out_of_scope_source_is_never_fetched(self, urlopen):
        doi, title = next(iter(tandf.APPLIED_ECONOMICS_EXACT.items()))
        self.assertEqual(tandf.verified_applied_economics_abstract({**record(doi, title), "journal": "Other Journal"}, 8), {})
        self.assertEqual(tandf.verified_applied_economics_abstract({**record(doi, title), "doi": "10.1080/00036846.2026.9999999"}, 8), {})
        self.assertEqual(tandf.verified_applied_economics_abstract({**record(doi, title), "title": "Different paper"}, 8), {})
        urlopen.assert_not_called()

    @patch.object(tandf.urllib.request, "urlopen")
    def test_page_chrome_short_preview_truncation_and_bad_identity_rejected(self, urlopen):
        doi, title = next(iter(tandf.APPLIED_ECONOMICS_EXACT.items()))
        page_url = f"https://www.tandfonline.com/doi/abs/{doi}"
        cases = [
            (html_fixture(doi, title, "Too short preview."), page_url),
            (html_fixture(doi, title, ABSTRACT + "..."), page_url),
            (html_fixture(doi, title, abstract_id="otherAbstract"), page_url),
            (html_fixture(doi, title, heading="INTRODUCTION"), page_url),
            (html_fixture(doi, "Wrong article"), page_url),
            (html_fixture("10.1080/00036846.2026.8888888", title), page_url),
            (html_fixture(doi, title), "https://untrusted.example/page"),
        ]
        for page, response_url in cases:
            with self.subTest(response_url=response_url, length=len(page)):
                urlopen.return_value = FakeResponse(page, response_url)
                self.assertEqual(tandf.verified_applied_economics_abstract(record(doi, title), 8), {})

    @patch.object(tandf.urllib.request, "urlopen")
    @patch.object(enrich_metadata, "semantic_scholar_doi_metadata", return_value={})
    @patch.object(enrich_metadata, "openalex_doi_metadata", return_value={})
    @patch.object(enrich_metadata, "crossref_doi_metadata", return_value={})
    def test_abstract_only_pipeline_never_rewrites_dates_or_first_seen(self, _c, _o, _s, urlopen):
        doi, title = next(iter(tandf.APPLIED_ECONOMICS_EXACT.items()))
        entry = record(doi, title)
        baseline = deepcopy(entry)
        urlopen.return_value = FakeResponse(html_fixture(doi, title), f"https://www.tandfonline.com/doi/abs/{doi}")
        changed, status = enrich_metadata.enrich_abstract_record(entry, timeout=8)
        self.assertTrue(changed)
        self.assertEqual(status, "metadata-updated:tandf-applied-economics-official")
        self.assertEqual(entry["abstract"], ABSTRACT)
        self.assertEqual(entry["abstract_source"], "tandf_applied_economics_official:abstractId1")
        for field, old_value in baseline.items():
            self.assertEqual(entry[field], old_value)


    def test_three_new_source_verified_dois_have_exact_titles(self):
        expected = {
            "10.1080/00036846.2026.2761031": "Fiscal innovation subsidies, factor allocation, and SME export performance: evidence from China’s innovation fund for technology-based SMEs",
            "10.1080/00036846.2026.2743846": "The determinants of young people’s access to a significant job in Spain",
            "10.1080/00036846.2026.2761046": "Can climate policy uncertainty reduce corporate cost stickiness?",
        }
        for doi, title in expected.items():
            self.assertEqual(tandf.APPLIED_ECONOMICS_EXACT.get(doi), title)

    @patch("builtins.print")
    @patch.object(tandf.urllib.request, "urlopen")
    def test_hosted_http_block_reports_only_bounded_outcome(self, urlopen, printed):
        doi, title = next(iter(tandf.APPLIED_ECONOMICS_EXACT.items()))
        urlopen.side_effect = tandf.urllib.error.HTTPError(
            f"https://www.tandfonline.com/doi/abs/{doi}", 403, "Forbidden", None, None
        )
        self.assertEqual(tandf.verified_applied_economics_abstract(record(doi, title), 8), {})
        printed.assert_called_once_with(
            f"TANDF_FIRST_PARTY_ABSTRACT doi={doi} outcome=HTTP_403"
        )
        self.assertNotIn("Forbidden", str(printed.call_args))

    @patch("builtins.print")
    @patch.object(tandf.urllib.request, "urlopen")
    def test_hosted_missing_named_abstract_is_explicit_failure(self, urlopen, printed):
        doi, title = next(iter(tandf.APPLIED_ECONOMICS_EXACT.items()))
        url = f"https://www.tandfonline.com/doi/abs/{doi}"
        urlopen.return_value = FakeResponse(
            html_fixture(doi, title, heading="INTRODUCTION"), url
        )
        self.assertEqual(tandf.verified_applied_economics_abstract(record(doi, title), 8), {})
        printed.assert_called_once_with(
            f"TANDF_FIRST_PARTY_ABSTRACT doi={doi} outcome=NAMED_ABSTRACT_MISSING"
        )


if __name__ == "__main__":
    unittest.main()
