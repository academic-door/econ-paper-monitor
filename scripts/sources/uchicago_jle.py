"""DOI-scoped, first-party Journal of Law & Economics abstract recovery.

Only the seven official articles whose raw HTML has a complete
og:description distinct from truncated dc.Description are eligible.
No retries, scraping bypass, credentials, or publication-date inference.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
import urllib.error
from html.parser import HTMLParser
from typing import Any

from common import clean_abstract_text, normalize_doi

JLE_EXACT_ARTICLES = {
    "10.1086/744288": "Contractual Remedies in Mergers",
    "10.1086/744287": "Revolutionary Constitutional Compliance",
    "10.1086/738356": "The Power of Business Legislation: Evidence from China’s 1904 Company Law",
    "10.1086/735891": "Demand for Artificial Intelligence in Settlement Negotiations",
    "10.1086/744286": "Big Three (Dis)Engagements",
    "10.1086/744285": "The Effects of Increased Sentencing Severity on Fertility and Family Formation",
    "10.1086/744289": "The Emergence of Enforcement",
}


class _PublisherMeta(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "meta":
            return
        values = {name.casefold(): value for name, value in attrs if value is not None}
        key = values.get("name") or values.get("property")
        if key and values.get("content"):
            self.fields[key.casefold()] = values["content"]


def verified_jle_abstract(record: dict[str, Any], timeout: int) -> dict[str, str]:
    """One bounded official article fetch; return only verified full abstract."""
    if str(record.get("journal") or "") != "Journal of Law & Economics":
        return {}
    doi = normalize_doi(record.get("doi")) or ""
    expected = JLE_EXACT_ARTICLES.get(doi)
    if not expected or str(record.get("title") or "").casefold().strip() != expected.casefold():
        return {}

    url = f"https://www.journals.uchicago.edu/doi/{doi}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Academic-Door-Daily-Door-Source-Quality-Audit/1.0",
            "Accept": "text/html",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=min(max(int(timeout), 1), 8)) as response:
            if response.status != 200 or response.headers.get_content_type() != "text/html":
                return {}
            target = urllib.parse.urlsplit(response.geturl())
            if target.scheme != "https" or target.hostname not in {
                "www.journals.uchicago.edu", "journals.uchicago.edu"
            }:
                return {}
            body = response.read(1_048_577)
    except (OSError, TimeoutError, urllib.error.URLError):
        return {}
    if len(body) > 1_048_576:
        return {}

    raw = body.decode("utf-8", errors="replace")
    if doi not in raw.casefold():
        return {}
    parsed = _PublisherMeta()
    parsed.feed(raw)
    metadata = parsed.fields
    if str(metadata.get("dc.title") or "").strip().casefold() != expected.casefold():
        return {}

    complete = str(metadata.get("og:description") or "").strip()
    teaser = str(metadata.get("dc.description") or "").strip()
    if (
        not teaser or len(teaser) > 300
        or len(complete) < len(teaser) + 150
        or not complete.startswith("Abstract")
    ):
        return {}
    abstract = clean_abstract_text(complete[len("Abstract"):])
    if (
        not (350 <= len(abstract) <= 12_000)
        or abstract.endswith(("...", "…", "[…]", "[...]"))
        or "get full access" in abstract.casefold()
    ):
        return {}
    return {
        "abstract": abstract,
        "abstract_source": "uchicago_jle_official:og_description",
    }
