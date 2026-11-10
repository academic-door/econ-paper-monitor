"""Exact DOI first-party full abstracts for Applied Economics.

The public article HTML has a complete ABSTRACT paragraph; dc.Description is
a truncated teaser. All source identities below were independently verified.
Do not fetch paywalled full text, retry blocked pages, or infer publication dates.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from common import clean_abstract_text, normalize_doi


APPLIED_ECONOMICS_EXACT = {
    "10.1080/00036846.2026.2760527": "Quantity or quality? The impacts of carbon emission trading system on green technology innovation: evidence from a quasi-natural experiment conducted in China",
    "10.1080/00036846.2026.2756530": "Can policy-induced fair competition promote firm cooperative green innovation? Evidence from China’s Competition Review System",
    "10.1080/00036846.2026.2750978": "The macroeconomic effects of local government debt structure in China",
    "10.1080/00036846.2026.2758392": "The impact of government subsidies on labor income share: evidence from Chinese listed firms",
    "10.1080/00036846.2026.2760524": "Do bank branches always exert a positive impact? A perspective from commercial banks issuing wealth management products",
    "10.1080/00036846.2026.2753033": "Green finance policy and environmental decoupling: evidence from China",
    "10.1080/00036846.2026.2756536": "Shifting hours: time allocation and gender inequality in India 1999–2019",
    "10.1080/00036846.2026.2760522": "Executives with environmental backgrounds and corporate greenwashing: evidence from Chinese listed firms",
    "10.1080/00036846.2026.2757187": "Digital finance and economic green development",
    "10.1080/00036846.2026.2756531": "Regional divergence in energy transition under the joint constraints of green finance development and climate policy uncertainty",
    "10.1080/00036846.2026.2761031": "Fiscal innovation subsidies, factor allocation, and SME export performance: evidence from China’s innovation fund for technology-based SMEs",
    "10.1080/00036846.2026.2743846": "The determinants of young people’s access to a significant job in Spain",
    "10.1080/00036846.2026.2761046": "Can climate policy uncertainty reduce corporate cost stickiness?",
}


class _OfficialAbstract(HTMLParser):
    """Capture only the publisher's named article Abstract, never page chrome."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.citation_title = ""
        self.abstract_depth = 0
        self.heading_active = False
        self.heading_text: list[str] = []
        self.heading_valid = False
        self.paragraph_active = False
        self.paragraph_done = False
        self.paragraph_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = {str(k).casefold(): v for k, v in attrs if v is not None}
        if tag == "meta" and fields.get("name", "").casefold() == "dc.title":
            self.citation_title = str(fields.get("content") or "")
        if tag == "div":
            if fields.get("id") == "abstractId1" and not self.abstract_depth:
                self.abstract_depth = 1
            elif self.abstract_depth:
                self.abstract_depth += 1
        if self.abstract_depth and tag == "h2" and fields.get("id") == "abstract":
            self.heading_active = True
        if (
            self.abstract_depth and self.heading_valid
            and tag == "p" and not self.paragraph_done and not self.paragraph_active
        ):
            self.paragraph_active = True

    def handle_data(self, data: str) -> None:
        if self.heading_active:
            self.heading_text.append(data)
        if self.paragraph_active:
            self.paragraph_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2" and self.heading_active:
            self.heading_active = False
            self.heading_valid = "".join(self.heading_text).strip().upper() == "ABSTRACT"
        if tag == "p" and self.paragraph_active:
            self.paragraph_active = False
            self.paragraph_done = True
        if tag == "div" and self.abstract_depth:
            self.abstract_depth -= 1


def verified_applied_economics_abstract(record: dict[str, Any], timeout: int) -> dict[str, str]:
    """Use one bounded HTTPS official abstract-page request per exact paper."""
    if str(record.get("journal") or "") != "Applied Economics":
        return {}
    doi = normalize_doi(record.get("doi")) or ""
    expected = APPLIED_ECONOMICS_EXACT.get(doi)
    if not expected or str(record.get("title") or "").strip().casefold() != expected.casefold():
        return {}

    url = f"https://www.tandfonline.com/doi/abs/{doi}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Academic-Door-Daily-Door-Source-Quality-Audit/1.0",
            "Accept": "text/html",
        },
    )
    def outcome(code: str) -> None:
        # Public exact DOI and an operational outcome only: no publisher HTML,
        # abstract text, access tokens, cookies, or exception bodies in logs.
        print(f"TANDF_FIRST_PARTY_ABSTRACT doi={doi} outcome={code}")

    try:
        with urllib.request.urlopen(request, timeout=min(max(int(timeout), 1), 8)) as response:
            if response.status != 200 or response.headers.get_content_type() != "text/html":
                outcome("NON_HTML_OR_NON_200")
                return {}
            parsed_url = urllib.parse.urlsplit(response.geturl())
            if parsed_url.scheme != "https" or parsed_url.hostname not in {
                "www.tandfonline.com", "tandfonline.com"
            }:
                outcome("NON_PUBLISHER_REDIRECT")
                return {}
            data = response.read(1_048_577)
    except urllib.error.HTTPError as exc:
        outcome(f"HTTP_{exc.code}")
        return {}
    except (OSError, TimeoutError, urllib.error.URLError):
        outcome("NETWORK_FAILURE")
        return {}
    if len(data) > 1_048_576:
        outcome("RESPONSE_TOO_LARGE")
        return {}

    html = data.decode("utf-8", errors="replace")
    if doi not in html.casefold():
        outcome("DOI_MISMATCH")
        return {}
    parsed = _OfficialAbstract()
    parsed.feed(html)
    if parsed.citation_title.strip().casefold() != expected.casefold():
        outcome("TITLE_MISMATCH")
        return {}
    if not parsed.paragraph_done:
        outcome("NAMED_ABSTRACT_MISSING")
        return {}
    abstract = clean_abstract_text("".join(parsed.paragraph_parts))
    if not (180 <= len(abstract) <= 12_000) or abstract.endswith(("...", "…", "[…]", "[...]")):
        outcome("ABSTRACT_PREVIEW_OR_INVALID")
        return {}
    outcome("SUCCESS")
    return {
        "abstract": abstract,
        "abstract_source": "tandf_applied_economics_official:abstractId1",
    }
