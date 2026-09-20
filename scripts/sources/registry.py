"""Source registry and RSS feed discovery."""

from __future__ import annotations

import re
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from common import DATA_DIR, fetch_json, fetch_text, read_json, write_json


REGISTRY_PATH = DATA_DIR / "source_registry.json"


class FeedLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "link":
            rel = attr.get("rel", "").lower()
            typ = attr.get("type", "").lower()
            href = attr.get("href", "")
            if href and "alternate" in rel and ("rss" in typ or "atom" in typ or "xml" in typ):
                self.links.append({"url": self._join(href), "label": typ or "alternate"})
        if tag.lower() == "a":
            self._href = attr.get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        text = re.sub(r"\s+", " ", "".join(self._text)).strip()
        if text and re.search(r"rss|atom|feed|online first|advance|latest|current issue", text, re.I):
            self.links.append({"url": self._join(self._href), "label": text})
        self._href = None
        self._text = []

    def _join(self, href: str) -> str:
        return urllib.parse.urljoin(self.base_url, href).split("#", 1)[0]


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    return read_json(path, {"journals": {}})


def save_registry(registry: dict[str, Any], path: Path = REGISTRY_PATH) -> None:
    write_json(path, registry)


def configured_rss_urls(journal: dict[str, Any]) -> list[dict[str, str]]:
    feeds: list[dict[str, str]] = []
    for source in journal.get("sources", []):
        if source.get("type") == "rss" and source.get("url"):
            feeds.append({"url": source["url"], "label": "configured"})
    return feeds


def compact_issn(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"[^0-9Xx]", "", value).upper()
    return compact if len(compact) == 8 else None


def crossref_issn_candidates(
    journal: dict[str, Any],
    registry: dict[str, Any],
    registry_entry: dict[str, Any],
) -> list[str]:
    """Return ISSNs with electronic ISSN first, caching Crossref lookup."""
    cached = registry_entry.get("crossref_issn_candidates")
    if isinstance(cached, list) and cached:
        return [value for value in (compact_issn(str(item)) for item in cached) if value]

    issn = str(journal.get("issn") or registry_entry.get("issn") or "").strip()
    compact = compact_issn(issn)
    if not compact:
        return []
    candidates = [compact]
    try:
        payload = fetch_json(f"https://api.crossref.org/journals/{issn}", timeout=12)
        item = payload.get("message") or {}
        typed = item.get("issn-type") or []
        electronic = [compact_issn(entry.get("value")) for entry in typed if entry.get("type") == "electronic"]
        other = [compact_issn(value) for value in item.get("ISSN", [])]
        candidates = [value for value in electronic + other + [compact] if value]
        candidates = list(dict.fromkeys(candidates))
        registry_entry["crossref_issn_candidates"] = candidates
        if electronic:
            registry_entry["online_issn"] = f"{electronic[0][:4]}-{electronic[0][4:]}"
        save_registry(registry)
    except Exception:
        pass
    return candidates


TANDF_JOURNAL_CODES = {
    # Taylor & Francis journal codes are not derivable from ISSN.
    # Keep explicit, tested mappings here.
    "applied-economics": "raec20",
}

UCHICAGO_JOURNAL_CODES = {
    # Chicago's official RSS endpoint requires the journal code rather than ISSN.
    "journal-of-political-economy": "jpe",
    "economic-development-and-cultural-change": "edcc",
    "journal-of-labor-economics": "jole",
    "journal-of-law-and-economics": "jle",
}


def generated_official_rss_urls(journal: dict[str, Any]) -> list[dict[str, str]]:
    """Return official publisher RSS URLs that can be built from known rules."""
    journal_id = str(journal.get("id") or "")

    registry = load_registry()
    registry_entry = registry.get("journals", {}).get(journal_id, {})
    publisher = " ".join(
        str(value or "")
        for value in (
            journal.get("publisher"),
            registry_entry.get("publisher"),
            registry_entry.get("platform"),
        )
    ).casefold()
    issn = compact_issn(str(journal.get("issn") or registry_entry.get("issn") or ""))
    feeds: list[dict[str, str]] = []

    if issn and ("elsevier" in publisher or registry_entry.get("platform") == "elsevier"):
        candidates = [
            compact_issn(str(registry_entry.get("print_issn") or "")),
            compact_issn(str(journal.get("print_issn") or "")),
            issn,
            *crossref_issn_candidates(journal, registry, registry_entry),
        ]
        for candidate in [value for value in dict.fromkeys(candidates) if value]:
            feeds.append(
                {
                    "url": f"https://rss.sciencedirect.com/publication/science/{candidate}",
                    "label": "ScienceDirect RSS",
                    "type": "official",
                }
            )
    if issn and ("wiley" in publisher or registry_entry.get("platform") == "wiley"):
        candidates = [
            compact_issn(str(registry_entry.get("online_issn") or "")),
            compact_issn(str(journal.get("online_issn") or journal.get("eissn") or "")),
            *crossref_issn_candidates(journal, registry, registry_entry),
            issn,
        ]
        for candidate in [value for value in dict.fromkeys(candidates) if value]:
            feeds.append(
                {
                    "url": f"https://onlinelibrary.wiley.com/action/showFeed?jc={candidate}&type=etoc&feed=rss",
                    "label": "Wiley Online Library RSS",
                    "type": "official",
                }
            )
    tandf_code = TANDF_JOURNAL_CODES.get(journal_id) or registry_entry.get("tandf_code")
    if tandf_code:
        feeds.append(
            {
                "url": f"https://www.tandfonline.com/action/showFeed?type=etoc&feed=rss&jc={tandf_code}",
                "label": "Taylor & Francis RSS",
                "type": "official",
            }
        )
    chicago_code = UCHICAGO_JOURNAL_CODES.get(journal_id) or registry_entry.get("uchicago_code")
    if chicago_code:
        feeds.append(
            {
                "url": f"https://www.journals.uchicago.edu/action/showFeed?type=etoc&feed=rss&jc={chicago_code}",
                "label": "Chicago Journals RSS",
                "type": "official",
            }
        )
    return feeds


def candidate_pages(journal: dict[str, Any]) -> list[str]:
    pages = []
    for key in ("homepage_url", "rss_page_url"):
        if journal.get(key):
            pages.append(journal[key])
    for source in journal.get("sources", []):
        if source.get("type") in {"homepage", "rss_page"} and source.get("url"):
            pages.append(source["url"])
    registry = load_registry()
    journal_entry = registry.get("journals", {}).get(journal["id"], {})
    for source in journal_entry.get("sources", []):
        if source.get("type") in {"homepage", "rss_page", "latest"} and source.get("url"):
            pages.append(source["url"])
    return list(dict.fromkeys(pages))


def discover_feeds_from_page(url: str, timeout: int = 20) -> list[dict[str, str]]:
    html = fetch_text(url, timeout=timeout)
    parser = FeedLinkParser(url)
    parser.feed(html)
    seen: dict[str, dict[str, str]] = {}
    for item in parser.links:
        if item["url"].startswith(("http://", "https://")):
            seen[item["url"]] = item
    return list(seen.values())


def feeds_for_journal(journal: dict[str, Any], *, discover: bool = False) -> tuple[list[dict[str, str]], str]:
    registry = load_registry()
    journal_entry = registry.setdefault("journals", {}).setdefault(journal["id"], {})
    feeds = configured_rss_urls(journal)
    generated_feeds = generated_official_rss_urls(journal)
    feeds.extend(generated_feeds)
    feeds.extend(journal_entry.get("rss", []))

    if discover:
        discovered: list[dict[str, str]] = []
        for page in candidate_pages(journal):
            try:
                discovered.extend(discover_feeds_from_page(page))
            except Exception:
                continue
        if discovered:
            existing = journal_entry.get("rss") if isinstance(journal_entry.get("rss"), list) else []
            merged = {str(item.get("url")): item for item in [*existing, *discovered] if item.get("url")}
            journal_entry["rss"] = list(merged.values())
            journal_entry["rss_status"] = "discovered"
            save_registry(registry)
            feeds.extend(discovered)

    deduped = list({feed["url"]: feed for feed in feeds if feed.get("url")}.values())
    if configured_rss_urls(journal):
        status = "configured"
    elif generated_feeds:
        status = "official-generated"
    else:
        status = journal_entry.get("rss_status", "none")
    return deduped, status
