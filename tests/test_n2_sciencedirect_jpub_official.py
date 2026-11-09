"""Exact first-party JPub public abstract; no date/author/first_seen changes."""

import io
import sys
from copy import deepcopy
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import enrich_metadata
from sources import sciencedirect_jpub


ABSTRACT = (
    "This synthetic publisher abstract examines how tax enforcement and information "
    "quality affect measured noncompliance among high-income individuals. The empirical "
    "design uses administrative evidence and estimates heterogeneous outcomes across "
    "income groups, while accounting for imperfect detection. Independent validation "
    "explores alternative counterfactual assumptions and shows where naïve measurements "
    "may be biased. The analysis reports implications for economic research and tax policy."
)


class FakeResponse:
    def __init__(self, content: bytes, url: str, *, status=200, content_type="text/html"):
        self.data = io.BytesIO(content)
        self.url = url
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.data.close()

    def geturl(self):
        return self.url

    def read(self, n):
        return self.data.read(n)


def article_fixture(*, doi=sciencedirect_jpub.EXACT_DOI,
                    title=sciencedirect_jpub.EXACT_TITLE,
                    journal=sciencedirect_jpub.EXACT_JOURNAL,
                    abstract=ABSTRACT, section="abs0005", paragraph="sp0050"):
    return (
        '<html><head>'
        f'<meta name="citation_doi" content="{doi}">'
        f'<meta name="citation_title" content="{title}">'
        f'<meta name="citation_journal_title" content="{journal}">'
        '<meta property="og:description" content="Short preview…">'
        '</head><body><div id="abstracts">'
        '<div id="abs0010"><h2>Highlights</h2><div id="sp0040">Not an abstract.</div></div>'
        f'<div id="{section}"><h2>Abstract</h2>'
        f'<div id="{paragraph}">{abstract}</div></div>'
        '</div><div id="fulltext">Unrelated full-text passage</div></body></html>'
    ).encode("utf-8")


def record():
    doi = sciencedirect_jpub.EXACT_DOI
    return {
        "doi": doi,
        "title": sciencedirect_jpub.EXACT_TITLE,
        "journal": sciencedirect_jpub.EXACT_JOURNAL,
        "authors": ["Verified Author"],
        "first_seen": "2026-11-09T08:52:00+00:00",
        "published_online": "2026-11-09",
        "available_online": "2026-11-09",
        "date_source": "crossref_elsevier_created_online",
    }


@patch.object(sciencedirect_jpub.urllib.request, "urlopen")
def test_exact_official_jpub_abstract(urlopen):
    urlopen.return_value = FakeResponse(article_fixture(), "https://www.sciencedirect.com/science/article/pii/S0047272726000001")
    found = sciencedirect_jpub.verified_jpub_abstract(record(), 20)
    assert found == {"abstract": ABSTRACT, "abstract_source": "sciencedirect_jpub_official:abs0005"}
    assert urlopen.call_args.kwargs["timeout"] == 8


@patch.object(sciencedirect_jpub.urllib.request, "urlopen")
def test_wrong_scope_never_fetches(urlopen):
    for changes in ({"journal": "Other"}, {"doi": "10.1016/j.jpubeco.2026.000000"}, {"title": "Other"}):
        assert sciencedirect_jpub.verified_jpub_abstract({**record(), **changes}, 8) == {}
    urlopen.assert_not_called()


@patch.object(sciencedirect_jpub.urllib.request, "urlopen")
def test_fail_closed_bad_page_abstract_identity_and_transport(urlopen):
    url = "https://www.sciencedirect.com/science/article/pii/S0047272726000001"
    cases = [
        (article_fixture(abstract="Short teaser."), url, 200),
        (article_fixture(abstract=ABSTRACT + "..."), url, 200),
        (article_fixture(doi="10.1016/j.jpubeco.2026.000000"), url, 200),
        (article_fixture(title="Wrong title"), url, 200),
        (article_fixture(section="different-section"), url, 200),
        (article_fixture(paragraph="different-paragraph"), url, 200),
        (article_fixture(), "https://invalid.example/landing", 200),
        (article_fixture(), url, 403),
    ]
    for body, final_url, status in cases:
        urlopen.return_value = FakeResponse(body, final_url, status=status)
        assert sciencedirect_jpub.verified_jpub_abstract(record(), 8) == {}


@patch.object(sciencedirect_jpub.urllib.request, "urlopen")
@patch.object(enrich_metadata, "semantic_scholar_doi_metadata", return_value={})
@patch.object(enrich_metadata, "openalex_doi_metadata", return_value={})
@patch.object(enrich_metadata, "crossref_doi_metadata", return_value={})
@patch.object(enrich_metadata, "elsevier_api_metadata", return_value={})
def test_abstract_only_does_not_rewrite_canonical_fields(_els, _cr, _oa, _ss, urlopen):
    entry = record()
    original = deepcopy(entry)
    urlopen.return_value = FakeResponse(article_fixture(), "https://www.sciencedirect.com/science/article/pii/S0047272726000001")
    changed, state = enrich_metadata.enrich_abstract_record(entry, timeout=8)
    assert changed
    assert state == "metadata-updated:sciencedirect-jpub-official"
    assert entry["abstract"] == ABSTRACT
    assert entry["abstract_source"] == "sciencedirect_jpub_official:abs0005"
    for k, v in original.items():
        assert entry[k] == v
