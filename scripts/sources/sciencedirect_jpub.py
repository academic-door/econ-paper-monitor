"""Bounded first-party ScienceDirect abstract recovery for one verified JPub article.

This adapter only uses the public official article HTML, not full text,
paywall access, third-party proxies, or publisher preview metadata.
Identity and abstract-container guards fail closed when the layout changes.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from common import clean_abstract_text, normalize_doi


EXACT_DOI = "10.1016/j.jpubeco.2026.105812"
EXACT_TITLE = "Tax evasion at the top of the income distribution"
EXACT_JOURNAL = "Journal of Public Economics"


class _PublisherAbstract(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}
        self.abstract_section = False
        self.section_depth = 0
        self.abstract_depth = 0
        self.abstract_parts: list[str] = []
        self.abstract_done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        props = {str(k).casefold(): v for k, v in attrs if v is not None}
        if tag == "meta":
            key = str(props.get("name") or "").casefold()
            if key in {"citation_doi", "citation_title", "citation_journal_title"}:
                self.metadata[key] = str(props.get("content") or "")
        if tag != "div":
            return
        if self.abstract_depth:
            self.abstract_depth += 1
        elif self.abstract_section and props.get("id") == "sp0050" and not self.abstract_done:
            self.abstract_depth = 1
        if self.abstract_section:
            self.section_depth += 1
        elif props.get("id") == "abs0005":
            self.abstract_section = True
            self.section_depth = 1

    def handle_data(self, text: str) -> None:
        if self.abstract_depth:
            self.abstract_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self.abstract_depth:
            self.abstract_depth -= 1
            if self.abstract_depth == 0:
                self.abstract_done = True
        if self.abstract_section:
            self.section_depth -= 1
            if self.section_depth == 0:
                self.abstract_section = False


def verified_jpub_abstract(record: dict[str, Any], timeout: int) -> dict[str, str]:
    """Return only exact-title/DOI first-party complete abstract fields."""
    if str(record.get("journal") or "") != EXACT_JOURNAL:
        return {}
    doi = normalize_doi(record.get("doi")) or ""
    if doi != EXACT_DOI or str(record.get("title") or "").strip().casefold() != EXACT_TITLE.casefold():
        return {}

    request = urllib.request.Request(
        f"https://doi.org/{doi}",
        headers={
            "User-Agent": "Academic-Door-Daily-Door-Source-Quality-Audit/1.0",
            "Accept": "text/html",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=min(max(int(timeout), 1), 8)) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if (
                response.status != 200
                or response.headers.get_content_type() != "text/html"
                or final.scheme != "https"
                or final.hostname not in {"www.sciencedirect.com", "sciencedirect.com"}
            ):
                return {}
            body = response.read(2_097_153)
    except (OSError, TimeoutError, urllib.error.URLError):
        return {}
    if len(body) > 2_097_152:
        return {}

    parsed = _PublisherAbstract()
    parsed.feed(body.decode("utf-8", errors="replace"))
    fields = parsed.metadata
    if (
        (normalize_doi(fields.get("citation_doi")) or "") != doi
        or fields.get("citation_title", "").strip().casefold() != EXACT_TITLE.casefold()
        or fields.get("citation_journal_title", "").strip() != EXACT_JOURNAL
        or not parsed.abstract_done
    ):
        return {}
    abstract = clean_abstract_text(" ".join(parsed.abstract_parts))
    if (
        not (250 <= len(abstract) <= 12_000)
        or abstract.endswith(("...", "…", "[…]", "[...]"))
        or "get full access" in abstract.casefold()
    ):
        return {}
    return {
        "abstract": abstract,
        "abstract_source": "sciencedirect_jpub_official:abs0005",
    }
