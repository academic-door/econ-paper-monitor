"""Fetch priority publisher TOC pages that are faster than Crossref.

This adapter is intentionally narrow.  It covers sources that matter for the
project's economics-journal scope and that external sentinels often see before
Crossref catches up: REStud advance articles, REStat current/advance pages,
Econometrica, Theoretical Economics, and Quantitative Economics forthcoming
papers.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import os
import re
import ssl
import json
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

from common import DATA_DIR, load_journals, today_str, write_json
from sources.record import article_record
from status import record_source


TARGETS = {
    "review-of-economic-studies": [
        {
            "kind": "restud_advance",
            "url": "https://academic.oup.com/restud/advance-articles",
            "fallback_urls": [
                "https://r.jina.ai/http://academic.oup.com/restud/advance-articles",
            ],
            "date_source": "oup_advance_articles",
            "date_confidence": "B",
            "fallback_issn": "0034-6527",
        },
        {
            "kind": "restud_official_accepted",
            "url": "https://www.restud.com/",
            "fallback_urls": [
                "https://r.jina.ai/http://www.restud.com/",
            ],
            "date_source": "restud_published_time",
            "date_confidence": "A",
            "fallback_issn": "0034-6527",
        },
    ],
    "review-of-economics-and-statistics": [
        {
            "kind": "restat_current",
            "url": "https://direct.mit.edu/rest/issue/current",
            "fallback_urls": [
                "https://r.jina.ai/http://direct.mit.edu/rest/issue/current",
            ],
            "date_source": "mitpress_current_issue",
            "date_confidence": "C",
            "fallback_issn": "0034-6535",
        },
        {
            "kind": "restat_advance",
            "url": "https://direct.mit.edu/rest/advance-articles",
            "fallback_urls": [
                "https://r.jina.ai/http://direct.mit.edu/rest/advance-articles",
            ],
            "date_source": "mitpress_advance_articles",
            "date_confidence": "B",
            "fallback_issn": "0034-6535",
        },
    ],
    "econometrica": [
        {
            "kind": "econometrica_forthcoming",
            "url": "https://www.econometricsociety.org/publications/econometrica/forthcoming-papers",
            "fallback_urls": [
                "https://r.jina.ai/http://www.econometricsociety.org/publications/econometrica/forthcoming-papers",
            ],
            "date_source": "econometric_society_forthcoming",
            "date_confidence": "B",
            "fallback_issn": "0012-9682",
        }
    ],
    "theoretical-economics": [
        {
            "kind": "theoretical_economics_forthcoming",
            "url": "https://www.econometricsociety.org/publications/theoretical-economics/forthcoming-papers",
            "fallback_urls": [
                "https://r.jina.ai/http://www.econometricsociety.org/publications/theoretical-economics/forthcoming-papers",
            ],
            "date_source": "econometric_society_forthcoming",
            "date_confidence": "B",
            "fallback_issn": "1933-6837",
        }
    ],
    "quantitative-economics": [
        {
            "kind": "quantitative_economics_forthcoming",
            "url": "https://www.econometricsociety.org/publications/quantitative-economics/forthcoming-papers",
            "fallback_urls": [
                "https://r.jina.ai/http://www.econometricsociety.org/publications/quantitative-economics/forthcoming-papers",
            ],
            "date_source": "econometric_society_forthcoming",
            "date_confidence": "B",
            "fallback_issn": "1759-7323",
        }
    ],
    "quarterly-journal-of-economics": [
        {
            "kind": "oup_advance_articles",
            "url": "https://academic.oup.com/qje/advance-articles",
            "fallback_urls": ["https://r.jina.ai/http://academic.oup.com/qje/advance-articles"],
            "date_source": "oup_advance_articles",
            "date_confidence": "B",
            "fallback_issn": "0033-5533",
        }
    ],
    "economic-journal": [
        {
            "kind": "oup_advance_articles",
            "url": "https://academic.oup.com/econj/advance-articles",
            "fallback_urls": ["https://r.jina.ai/http://academic.oup.com/econj/advance-articles"],
            "date_source": "oup_advance_articles",
            "date_confidence": "B",
            "fallback_issn": "1468-0297",
        }
    ],
    "journal-of-the-european-economic-association": [
        {
            "kind": "oup_advance_articles",
            "url": "https://academic.oup.com/jeea/advance-articles",
            "fallback_urls": ["https://r.jina.ai/http://academic.oup.com/jeea/advance-articles"],
            "date_source": "oup_advance_articles",
            "date_confidence": "B",
            "fallback_issn": "1542-4766",
        }
    ],
    "journal-of-law-economics-and-organization": [
        {
            "kind": "oup_advance_articles",
            "url": "https://academic.oup.com/jleo/advance-articles",
            "fallback_urls": ["https://r.jina.ai/http://academic.oup.com/jleo/advance-articles"],
            "date_source": "oup_advance_articles",
            "date_confidence": "B",
            "fallback_issn": "8756-6222",
        }
    ],
    "review-of-financial-studies": [
        {
            "kind": "oup_advance_articles",
            "url": "https://academic.oup.com/rfs/advance-articles",
            "fallback_urls": ["https://r.jina.ai/http://academic.oup.com/rfs/advance-articles"],
            "date_source": "oup_advance_articles",
            "date_confidence": "B",
            "fallback_issn": "0893-9454",
        }
    ],
    "european-review-of-agricultural-economics": [
        {
            "kind": "oup_advance_articles",
            "url": "https://academic.oup.com/erae/advance-articles",
            "fallback_urls": ["https://r.jina.ai/http://academic.oup.com/erae/advance-articles"],
            "date_source": "oup_advance_articles",
            "date_confidence": "B",
            "fallback_issn": "0165-1587",
        }
    ],
    # Springer RSS frequently returns malformed XML from CI networks. The
    # publisher's Online First pages provide an independent official HTML
    # path while preserving article DOI links.
    "international-journal-of-game-theory": [{
        "kind": "springer_online_first",
        "url": "https://link.springer.com/journal/182/online-first",
        "fallback_issn": "0020-7276",
        "date_source": "springer_online_first",
        "date_confidence": "B",
    }],
    "economic-theory": [{
        "kind": "springer_online_first",
        "url": "https://link.springer.com/journal/199/online-first",
        "fallback_issn": "0938-2259",
        "date_source": "springer_online_first",
        "date_confidence": "B",
    }],
    "review-of-economic-design": [{
        "kind": "springer_online_first",
        "url": "https://link.springer.com/journal/10058/online-first",
        "fallback_issn": "1434-4742",
        "date_source": "springer_online_first",
        "date_confidence": "B",
    }],
    "social-choice-and-welfare": [{
        "kind": "springer_online_first",
        "url": "https://link.springer.com/journal/355/online-first",
        "fallback_issn": "0176-1714",
        "date_source": "springer_online_first",
        "date_confidence": "B",
    }],
    "public-choice": [{
        "kind": "springer_online_first",
        "url": "https://link.springer.com/journal/11127/online-first",
        "fallback_issn": "0048-5829",
        "date_source": "springer_online_first",
        "date_confidence": "B",
    }],
    "international-tax-and-public-finance": [{
        "kind": "springer_online_first",
        "url": "https://link.springer.com/journal/10797/online-first",
        "fallback_issn": "0927-5940",
        "date_source": "springer_online_first",
        "date_confidence": "B",
    }],
    "journal-of-economic-growth": [{
        "kind": "springer_online_first",
        "url": "https://link.springer.com/journal/10887/online-first",
        "fallback_issn": "1381-4338",
        "date_source": "springer_online_first",
        "date_confidence": "B",
    }],
    "journal-of-population-economics": [{
        "kind": "springer_online_first",
        "url": "https://link.springer.com/journal/148/online-first",
        "fallback_issn": "0933-1433",
        "date_source": "springer_online_first",
        "date_confidence": "B",
    }],
    "environmental-and-resource-economics": [{
        "kind": "springer_online_first",
        "url": "https://link.springer.com/journal/10640/online-first",
        "fallback_issn": "0924-6460",
        "date_source": "springer_online_first",
        "date_confidence": "B",
    }],
    "journal-of-agricultural-and-resource-economics": [{
        "kind": "jare_advance",
        "url": "https://jareonline.org/preprint-online/",
        "publisher_attempts": 3,
        "date_source": "jare_published_online",
        "date_confidence": "B",
    }],
    "journal-of-human-resources": [
        {
            "kind": "jhr_early_recent",
            "url": "https://jhr.uwpress.org/content/early/recent",
            "fallback_urls": ["https://r.jina.ai/http://jhr.uwpress.org/content/early/recent"],
            "date_source": "jhr_early_recent",
            "date_confidence": "B",
            "fallback_issn": "0022-166X",
        },
        {
            "kind": "jhr_current",
            "url": "https://jhr.uwpress.org/content/current",
            "fallback_urls": ["https://r.jina.ai/http://jhr.uwpress.org/content/current"],
            "date_source": "jhr_current",
            "date_confidence": "B",
            "fallback_issn": "0022-166X",
        },
    ],
}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


def jina_headers(url: str) -> dict[str, str]:
    """Prepare r.jina.ai mirror headers (markdown Accept + bearer key)."""
    headers = dict(BROWSER_HEADERS)
    if url.startswith("https://r.jina.ai/"):
        headers["Accept"] = "text/plain,text/markdown;q=0.9,*/*;q=0.8"
        if os.environ.get("JINA_API_KEY"):
            headers["Authorization"] = f"Bearer {os.environ['JINA_API_KEY']}"
    return headers


def fetch_one(url: str, timeout: int) -> str:
    """Fetch one candidate URL and return decoded page text."""
    request = urllib.request.Request(url, headers=jina_headers(url))
    try:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
        except Exception:
            context = ssl._create_unverified_context()
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                payload = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
        for encoding in dict.fromkeys([charset, "utf-8", "latin-1"]):
            try:
                text = payload.decode(encoding)
                if text.strip():
                    if is_challenge_page(text):
                        raise ValueError("publisher returned an anti-bot challenge page")
                    return text
            except Exception:
                continue
        raise ValueError("empty or undecodable publisher response")
    except Exception:
        raise


def is_challenge_page(text: str) -> bool:
    """Reject HTTP-200 anti-bot interstitials as source success."""
    lowered = re.sub(r"\s+", " ", text or "").casefold()
    strong_markers = (
        "just a moment...",
        "verify you are human",
        "enable javascript and cookies to continue",
        "cf-chl-",
        "sgcaptcha",
    )
    return len(lowered) < 5000 and any(marker in lowered for marker in strong_markers)


def fetch_toc_text(url: str, timeout: int, fallback_urls: list[str] | None = None) -> str:
    """Fetch a publisher page, then a text-rendering mirror when blocked.

    The mirror is only an acquisition fallback.  Dates and article metadata
    still come from the page content and are labelled with the publisher
    source configured for the target.
    """
    urls = [url, *(fallback_urls or [])]
    last_error: Exception | None = None
    for candidate in dict.fromkeys(urls):
        # JINA mirrors occasionally rate-limit the first request; retry once.
        attempts = 2 if candidate.startswith("https://r.jina.ai/") else 1
        for attempt in range(attempts):
            try:
                return fetch_one(candidate, timeout)
            except Exception as exc:  # noqa: BLE001 - try the next acquisition path.
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(2.0)
    raise last_error or RuntimeError("no TOC acquisition URL")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def doi_from_text(value: str | None) -> str | None:
    match = re.search(r"(10\.\d{4,9}/[^\s\"'<>]+)", value or "", flags=re.I)
    if not match:
        return None
    return match.group(1).rstrip(").,;").lower()


def parse_date(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)
    if match:
        month, day, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass
    months = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    match = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(20\d{2})", text)
    if match and match.group(1).casefold() in months:
        return f"{int(match.group(3)):04d}-{months[match.group(1).casefold()]:02d}-{int(match.group(2)):02d}"
    match = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})", text)
    if match and match.group(2).casefold() in months:
        return f"{int(match.group(3)):04d}-{months[match.group(2).casefold()]:02d}-{int(match.group(1)):02d}"
    return None


def meta_values(html_text: str, name: str) -> list[str]:
    values: list[str] = []
    patterns = [
        rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']{re.escape(name)}["\']',
    ]
    for pattern in patterns:
        values.extend(clean_text(match) for match in re.findall(pattern, html_text, flags=re.I | re.S))
    return [value for value in values if value]


def article_links(html_text: str, base_url: str) -> list[tuple[str, str]]:
    """Return article-detail links and reject publisher navigation links.

    OUP, MIT Press, and the Econometric Society reuse journal paths for
    navigation, policy, and submission pages. A link is an article candidate
    only when it carries the journal DOI pattern or an article-detail path.
    """
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    if "econometricsociety.org/publications/" in base_url.lower():
        # Forthcoming pages expose article cards with a title, author line,
        # and PDF link, but no DOI or HTML article URL. Only the primary
        # ``file`` link is a paper; supplemental files are excluded.
        card_pattern = re.compile(
            r'<div[^>]+class=["\']article["\'][^>]*>.*?'
            r'<h3[^>]+class=["\']article_title["\'][^>]*>(?P<title>.*?)</h3>\s*'
            r'<p>(?P<authors>.*?)</p>.*?'
            r'<a[^>]+href=["\'](?P<href>[^"\']+/file/[^"\']+\.pdf)["\']',
            flags=re.I | re.S,
        )
        for match in card_pattern.finditer(html_text):
            title = clean_text(match.group("title"))
            href = urljoin(base_url, html.unescape(match.group("href")).strip())
            if title and len(title) >= 8 and href not in seen:
                seen.add(href)
                links.append((href, title))
        if links:
            return links
    for match in re.finditer(r'<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>.*?)</a>', html_text, flags=re.I | re.S):
        href = html.unescape(match.group("href")).strip()
        raw_title = match.group("title")
        title = clean_text(match.group("title"))
        if "restud.com" in base_url.lower() and (
            "<h3" not in raw_title.lower()
            or "<time" not in raw_title.lower()
            or "author-short" not in raw_title.lower()
        ):
            continue
        if "restud.com" in base_url.lower():
            h3_title = re.search(r"<h3[^>]*>(.*?)</h3>", raw_title, flags=re.I | re.S)
            if h3_title:
                title = clean_text(re.sub(r"<[^>]+>", " ", h3_title.group(1)))
        if "restud.com" in base_url.lower() and "###" in title:
            restud_title = re.search(r"###\s*(.+?)\s+\d{1,2}\s+[A-Za-z]+\s+20\d{2}", title)
            if restud_title:
                title = clean_text(restud_title.group(1))
        if not title or len(title) < 8:
            continue
        href_lower = href.lower()
        base_lower = base_url.lower()
        is_doi_article = bool(re.search(r"10\.\d{4,9}/", href_lower))
        is_restud_article = "10.1093/restud/" in href_lower or "/restud/article/" in href_lower
        is_restat_article = "10.1162/rest" in href_lower or "/rest/article/" in href_lower
        is_econometrica_article = "10.3982/ecta" in href_lower
        is_theoretical_economics_article = "10.3982/te" in href_lower
        is_quantitative_economics_article = "10.3982/qe" in href_lower
        if "econometricsociety.org/publications/econometrica" in base_lower:
            valid_article = is_econometrica_article
        elif "econometricsociety.org/publications/theoretical-economics" in base_lower:
            valid_article = is_theoretical_economics_article
        elif "econometricsociety.org/publications/quantitative-economics" in base_lower:
            valid_article = is_quantitative_economics_article
        elif "academic.oup.com/restud" in base_lower:
            valid_article = is_restud_article or (is_doi_article and "restud" in href_lower)
        elif "direct.mit.edu/rest" in base_lower:
            valid_article = is_restat_article
        elif "restud.com" in base_lower:
            valid_article = ("restud.com/" in href_lower or href_lower.startswith("/")) and href_lower.rstrip("/") not in {
                "https://www.restud.com",
                "http://www.restud.com",
            }
        else:
            valid_article = is_doi_article
        if not valid_article:
            continue
        if any(skip in title.casefold() for skip in ("pdf", "permissions", "supplementary", "view metrics")):
            continue
        url = urljoin(base_url, href)
        key = url.split("?", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        links.append((url, title))
    # r.jina.ai normally returns Markdown rather than HTML.
    for match in re.finditer(r'\[(?P<title>[^\]]{8,240})\]\((?P<href>https?://[^)]+)\)', html_text):
        href = html.unescape(match.group("href")).strip()
        raw_title = match.group("title")
        title = clean_text(raw_title)
        if "restud.com" in base_url.lower() and "###" not in raw_title:
            continue
        if "restud.com" in base_url.lower() and "###" in title:
            restud_title = re.search(r"###\s*(.+?)\s+\d{1,2}\s+[A-Za-z]+\s+20\d{2}", title)
            if restud_title:
                title = clean_text(restud_title.group(1))
        href_lower = href.lower()
        valid = bool(re.search(r"10\.\d{4,9}/", href_lower))
        if "academic.oup.com/restud" in base_url.lower():
            valid = valid and ("restud" in href_lower)
        elif "direct.mit.edu/rest" in base_url.lower():
            valid = valid and ("10.1162/rest" in href_lower or "/rest/" in href_lower)
        elif "restud.com" in base_url.lower():
            valid = "restud.com/" in href_lower
        elif "econometricsociety.org/publications/econometrica" in base_url.lower():
            valid = valid and "10.3982/ecta" in href_lower
        elif "econometricsociety.org/publications/theoretical-economics" in base_url.lower():
            valid = valid and "10.3982/te" in href_lower
        elif "econometricsociety.org/publications/quantitative-economics" in base_url.lower():
            valid = valid and "10.3982/qe" in href_lower
        if not valid or any(skip in title.casefold() for skip in ("pdf", "permissions", "supplementary")):
            continue
        key = href.split("?", 1)[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            links.append((href, title))
    return links


def jhr_article_blocks(html_text: str, base_url: str) -> list[dict[str, Any]]:
    """Parse Journal of Human Resources Atypon early/current TOC cards."""
    blocks: list[dict[str, Any]] = []
    seen: set[str] = set()
    anchor_pattern = re.compile(
        r'<a[^>]+href=["\'](?P<href>[^"\']*/(?:content/early|content/current)/[^"\']+)["\'][^>]*>'
        r'\s*<span[^>]+class=["\'][^"\']*highwire-cite-title["\'][^>]*>(?P<title>.*?)</span>\s*</a>',
        flags=re.I | re.S,
    )
    anchors = list(anchor_pattern.finditer(html_text))
    for index, link_match in enumerate(anchors):
        href = html.unescape(link_match.group("href")).strip()
        url = urljoin(base_url, href)
        key = url.split("?", 1)[0].rstrip("/")
        if key in seen:
            continue
        title = clean_text(link_match.group("title"))
        if not title or len(title) < 8:
            continue
        seen.add(key)
        context_end = anchors[index + 1].start() if index + 1 < len(anchors) else min(len(html_text), link_match.end() + 2500)
        context = html_text[link_match.end():context_end]
        authors = [
            clean_text(part)
            for part in re.findall(
                r'<span[^>]+class=["\'][^"\']*highwire-citation-author[^"\']*["\'][^>]*>(.*?)</span>',
                context,
                flags=re.I | re.S,
            )
        ]
        authors = [author for author in authors if author]
        published: str | None = None
        date_match = re.search(
            r"highwire-cite-metadata-date[^>]*>\s*([A-Za-z]{3,9}\s+\d{1,2},\s+20\d{2})",
            context,
            flags=re.I,
        )
        if date_match:
            published = parse_date(date_match.group(1))
        doi: str | None = None
        doi_match = re.search(
            r"DOI:\s*(?:https?://doi\.org/)?(?P<doi>10\.\d{4,9}/[^\s<]+)",
            context,
            flags=re.I,
        )
        if doi_match:
            doi = doi_match.group("doi").rstrip(".,;").casefold()
        blocks.append(
            {
                "url": url,
                "title": title,
                "authors": authors[:12],
                "published_online": published,
                "doi": doi,
            }
        )
    if blocks:
        return blocks
    # r.jina.ai mirror returns Markdown rather than HTML.
    for match in re.finditer(r"\[(?P<title>[^\]]{8,240})\]\((?P<href>https?://[^)]+)\)", html_text):
        href = html.unescape(match.group("href")).strip()
        url = urljoin(base_url, href)
        if "jhr.uwpress.org" not in url.lower() and "10.3368/" not in url.lower():
            continue
        title = clean_text(match.group("title"))
        if not title or len(title) < 8:
            continue
        key = url.split("?", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        blocks.append(
            {
                "url": url,
                "title": title,
                "authors": [],
                "published_online": None,
                "doi": doi_from_text(url),
            }
        )
    return blocks


def jare_advance_blocks(html_text: str, base_url: str) -> list[dict[str, Any]]:
    """Parse official JARE Published Online cards without fetching the PDF host."""
    pattern = re.compile(
        r'<h1[^>]*class=["\'][^"\']*elementor-heading-title[^"\']*["\'][^>]*>\s*'
        r'<a[^>]+href=["\'](?P<href>https?://ageconsearch\.umn\.edu/[^"\']+)["\'][^>]*>(?P<title>.*?)</a>\s*</h1>',
        flags=re.I | re.S,
    )
    matches = list(pattern.finditer(html_text))
    blocks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(html_text)
        context = html_text[match.start():end]
        title = clean_text(match.group("title"))
        url = urljoin(base_url, html.unescape(match.group("href")).strip())
        if not title or len(title) < 8 or url in seen:
            continue
        seen.add(url)
        plain = clean_text(context)
        date_match = re.search(r"\b(\d{1,2}/\d{1,2}/20\d{2})\b", plain)
        published = parse_date(date_match.group(1)) if date_match else None
        authors: list[str] = []
        if date_match:
            author_match = re.search(r"\bBy:\s*(.+)$", plain[:date_match.start()].strip(), flags=re.I)
            if author_match:
                authors = [clean_text(author) for author in author_match.group(1).split(";")]
                authors = [author for author in authors if author][:12]
        abstract_match = re.search(
            r"\bAbstract\b\s*(.+?)(?:\s+Download Full Article\b|$)",
            plain,
            flags=re.I | re.S,
        )
        abstract = clean_text(abstract_match.group(1)) if abstract_match else None
        blocks.append({
            "url": url,
            "title": title,
            "authors": authors,
            "published_online": published,
            "abstract": abstract,
            "doi": None,
        })
    return blocks


def restud_author_map(html_text: str, base_url: str) -> dict[str, list[str]]:
    """Read the clean author-short field from the official REStud cards."""
    if "restud.com" not in base_url.lower():
        return {}
    authors_by_url: dict[str, list[str]] = {}
    for match in re.finditer(
        r'<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<body>.*?)</a>',
        html_text,
        flags=re.I | re.S,
    ):
        author_match = re.search(r'class=["\']author-short["\'][^>]*>(.*?)</', match.group("body"), flags=re.I | re.S)
        if not author_match:
            continue
        href = urljoin(base_url, html.unescape(match.group("href")).strip())
        author_text = clean_text(re.sub(r"<[^>]+>", " ", author_match.group(1)))
        if author_text:
            authors_by_url[href.split("?", 1)[0].rstrip("/")] = [author_text]
    return authors_by_url


def econometric_society_author_map(html_text: str, base_url: str) -> dict[str, list[str]]:
    """Map forthcoming PDF links to the author line in each article card."""
    if "econometricsociety.org/publications/" not in base_url.lower():
        return {}
    authors_by_url: dict[str, list[str]] = {}
    card_pattern = re.compile(
        r'<div[^>]+class=["\']article["\'][^>]*>.*?'
        r'<h3[^>]+class=["\']article_title["\'][^>]*>.*?</h3>\s*'
        r'<p>(?P<authors>.*?)</p>.*?'
        r'<a[^>]+href=["\'](?P<href>[^"\']+/file/[^"\']+\.pdf)["\']',
        flags=re.I | re.S,
    )
    for match in card_pattern.finditer(html_text):
        href = urljoin(base_url, html.unescape(match.group("href")).strip())
        author_text = clean_text(match.group("authors"))
        if author_text:
            authors_by_url[href.rstrip("/")] = [author_text]
    return authors_by_url


def restud_abstract_from_jina(text: str) -> str | None:
    """Extract the first substantive abstract paragraph from a REStud page."""
    content = text.split("Markdown Content:", 1)[-1] if "Markdown Content:" in text else text
    lines = [clean_text(line) for line in content.splitlines()]
    lines = [line for line in lines if line]
    date_index = next(
        (index for index, line in enumerate(lines) if re.fullmatch(r"\d{1,2}\s+[A-Za-z]+\s+20\d{2}", line)),
        None,
    )
    if date_index is None:
        return None
    for paragraph in lines[date_index + 2 :]:
        if len(paragraph) >= 80:
            return paragraph
    return None


def enrich_detail(url: str, fallback_title: str, timeout: int) -> dict[str, object]:
    try:
        html_text = fetch_toc_text(url, timeout=timeout, fallback_urls=[f"https://r.jina.ai/http://{url.removeprefix('https://').removeprefix('http://')}"])
    except Exception:
        return {"title": fallback_title, "authors": [], "doi": doi_from_text(url)}
    title = (meta_values(html_text, "citation_title") or [fallback_title])[0]
    authors = meta_values(html_text, "citation_author")[:12]
    doi = (meta_values(html_text, "citation_doi") or [doi_from_text(url)])[0]
    published = (
        parse_date((meta_values(html_text, "citation_online_date") or [None])[0])
        or parse_date((meta_values(html_text, "citation_publication_date") or [None])[0])
        or parse_date((meta_values(html_text, "dc.Date") or [None])[0])
    )
    jina_text = ""
    if "restud.com" in url.lower() and (not published or not authors or not meta_values(html_text, "citation_abstract")):
        jina_url = f"https://r.jina.ai/http://{url.removeprefix('https://').removeprefix('http://')}"
        try:
            jina_text = fetch_toc_text(jina_url, timeout=timeout)
            if not published:
                published_match = re.search(r"Published Time:\s*(20\d{2}-\d{2}-\d{2})", jina_text, flags=re.I)
                published = published_match.group(1) if published_match else published
            if not authors:
                author_match = re.search(
                    r"Markdown Content:\s*\n\s*\d{1,2}\s+[A-Za-z]+\s+20\d{2}\s*\n\s*([^\n]+)",
                    jina_text,
                    flags=re.I,
                )
                if author_match:
                    authors = [clean_text(author_match.group(1))]
        except Exception:
            pass
    if not published:
        published_match = re.search(r"Published Time:\s*(20\d{2}-\d{2}-\d{2})", html_text, flags=re.I)
        published = published_match.group(1) if published_match else None
    abstract = (meta_values(html_text, "citation_abstract") or [None])[0]
    if "restud.com" in url.lower() and not abstract and jina_text:
        abstract = restud_abstract_from_jina(jina_text)
    if "restud.com" in url.lower() and not authors:
        author_match = re.search(
            r"Markdown Content:\s*\n\s*\d{1,2}\s+[A-Za-z]+\s+20\d{2}\s*\n\s*([^\n]+)",
            html_text,
            flags=re.I,
        )
        if author_match:
            authors = [clean_text(author_match.group(1))]
    return {
        "title": title,
        "authors": authors,
        "doi": doi,
        "published_online": published,
        "abstract": abstract,
    }


def fetch_target(journal: dict, target: dict[str, str], *, timeout: int, detail_limit: int, max_items: int) -> list[dict]:
    page_url = target["url"]
    html_text = fetch_toc_text(page_url, timeout=timeout, fallback_urls=target.get("fallback_urls"))
    if target["kind"].startswith("jhr_"):
        records: list[dict] = []
        for block in jhr_article_blocks(html_text, page_url):
            records.append(
                article_record(
                    journal,
                    title=block["title"],
                    url=block["url"],
                    source="priority_toc",
                    source_url=page_url,
                    doi=block["doi"],
                    authors=block["authors"] or [],
                    published_online=block["published_online"],
                    available_online=block["published_online"],
                    date_source=target["date_source"],
                    date_confidence=target["date_confidence"],
                    raw_data={"priority_toc_kind": target["kind"]},
                )
            )
            if len(records) >= max_items:
                break
        return records
    if target["kind"] == "jare_advance":
        records: list[dict] = []
        for block in jare_advance_blocks(html_text, page_url):
            records.append(
                article_record(
                    journal,
                    title=block["title"],
                    url=block["url"],
                    source="priority_toc",
                    source_url=page_url,
                    doi=None,
                    authors=block["authors"],
                    abstract=block["abstract"],
                    published_online=block["published_online"],
                    available_online=block["published_online"],
                    date_source=target["date_source"],
                    date_confidence=target["date_confidence"],
                    raw_data={"priority_toc_kind": target["kind"]},
                )
            )
            if len(records) >= max_items:
                break
        return records
    author_map = restud_author_map(html_text, page_url)
    author_map.update(econometric_society_author_map(html_text, page_url))
    records: list[dict] = []
    for url, title in article_links(html_text, page_url):
        is_econometric_society_pdf = "econometricsociety.org/publications/" in url.lower() and "/file/" in url.lower()
        detail = (
            {"title": title, "doi": doi_from_text(url)}
            if is_econometric_society_pdf
            else enrich_detail(url, title, timeout) if len(records) < detail_limit
            else {"title": title, "doi": doi_from_text(url)}
        )
        records.append(
            article_record(
                journal,
                title=str(detail.get("title") or title),
                url=url,
                source="priority_toc",
                source_url=page_url,
                doi=detail.get("doi") if isinstance(detail.get("doi"), str) else None,
                authors=author_map.get(url.rstrip("/")) if author_map else None
                or (detail.get("authors") if isinstance(detail.get("authors"), list) else []),
                abstract=detail.get("abstract") if isinstance(detail.get("abstract"), str) else None,
                published_online=detail.get("published_online") if isinstance(detail.get("published_online"), str) else None,
                available_online=detail.get("published_online") if isinstance(detail.get("published_online"), str) else None,
                date_source=target["date_source"],
                date_confidence=target["date_confidence"],
                raw_data={"priority_toc_kind": target["kind"]},
            )
        )
        if len(records) >= max_items:
            break
    return records


def fetch_crossref_fallback(journal: dict, target: dict[str, str], *, timeout: int, max_items: int) -> list[dict]:
    """Use Crossref online-publication metadata when a publisher endpoint blocks CI."""
    issn = str(target.get("fallback_issn") or "").strip()
    if not issn:
        return []
    start = (date.today() - timedelta(days=45)).isoformat()
    params = urlencode({
        "filter": f"from-online-pub-date:{start},until-online-pub-date:{date.today().isoformat()}",
        "rows": max_items,
        "select": "DOI,title,author,published-online,published,created,URL,abstract",
    })
    request = urllib.request.Request(
        f"https://api.crossref.org/journals/{issn}/works?{params}",
        headers={**BROWSER_HEADERS, "Accept": "application/json", "User-Agent": "AcademicDoorPaperMonitor/1.0 (mailto:academic-door@users.noreply.github.com)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    records: list[dict] = []
    for item in (payload.get("message", {}).get("items") or []):
        title = str((item.get("title") or [""])[0]).strip()
        doi = str(item.get("DOI") or "").strip().lower()
        if not title or not doi:
            continue
        online = item.get("published-online") or item.get("published") or {}
        parts = online.get("date-parts") or []
        published = None
        if parts and parts[0]:
            values = parts[0]
            published = "-".join(str(value).zfill(2) for value in values[:3])
            if len(values) == 1:
                published += "-01-01"
            elif len(values) == 2:
                published += "-01"
        authors = [
            " ".join(part for part in (author.get("given"), author.get("family")) if part).strip()
            for author in (item.get("author") or [])
        ]
        authors = [author for author in authors if author]
        records.append(article_record(
            journal,
            title=title,
            url=str(item.get("URL") or f"https://doi.org/{doi}"),
            source="priority_crossref_fallback",
            source_url=f"https://api.crossref.org/journals/{issn}/works",
            doi=doi,
            authors=authors,
            abstract=item.get("abstract"),
            published_online=published,
            available_online=published,
            date_source="crossref_published_online",
            date_confidence="B" if item.get("published-online") else "C",
            raw_data={"priority_toc_kind": target["kind"], "fallback": "crossref", "issn": issn},
        ))
        if len(records) >= max_items:
            break
    return records


def fetch_target_with_fallback(
    journal: dict,
    target: dict[str, str],
    *,
    timeout: int,
    detail_limit: int,
    max_items: int,
) -> tuple[list[dict], int, bool, list[str], bool]:
    """Fetch one target without allowing it to block its sibling targets."""
    label = f"{journal.get('id')}/{target['kind']}"
    publisher_attempts = max(1, int(target.get("publisher_attempts") or 1))
    last_error: Exception | None = None
    for attempt in range(publisher_attempts):
        try:
            fetched = fetch_target(
                journal,
                target,
                timeout=timeout,
                detail_limit=detail_limit,
                max_items=max_items,
            )
            if fetched:
                return fetched, 0, True, [f"{label}: {len(fetched)}"], False
            fallback = fetch_crossref_fallback(
                journal, target, timeout=timeout, max_items=max_items
            )
            return (
                fallback,
                len(fallback),
                False,
                [f"{label}: 0", f"{label}: crossref fallback {len(fallback)}"],
                not fallback,
            )
        except Exception as exc:  # noqa: BLE001 - bounded source retry before fallback.
            last_error = exc
            if attempt + 1 < publisher_attempts:
                time.sleep(2.0)
                continue
            break
    exc = last_error or RuntimeError("publisher fetch failed without an exception")
    try:
        fallback = fetch_crossref_fallback(
            journal, target, timeout=timeout, max_items=max_items
        )
        fallback_message = f"{label}: {type(exc).__name__}; crossref fallback {len(fallback)}"
        return fallback, len(fallback), False, [fallback_message], not fallback
    except Exception as fallback_exc:  # noqa: BLE001 - preserve both errors.
        return (
            [],
            0,
            False,
            [
                f"{label}: {type(exc).__name__}; crossref fallback "
                f"{type(fallback_exc).__name__}: {fallback_exc}"
            ],
            True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journals", type=Path, default=DATA_DIR / "journals.yml")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--detail-limit", type=int, default=12)
    parser.add_argument("--max-items-per-source", type=int, default=40)
    parser.add_argument(
        "--journal",
        action="append",
        dest="journal_ids",
        help="Only fetch the named journal id; repeat for focused source checks.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Maximum concurrent priority targets; keep bounded for publisher etiquette.",
    )
    args = parser.parse_args()

    journals = {str(journal.get("id")): journal for journal in load_journals(args.journals)}
    selected_journals = set(args.journal_ids or TARGETS)
    output = args.output or DATA_DIR / "raw" / "priority-toc" / f"{today_str()}.json"
    records: list[dict] = []
    messages: list[str] = []
    failures = 0
    journal_status: dict[str, dict[str, object]] = {}
    jobs: dict[Any, tuple[str, dict, dict]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as executor:
        for journal_id, targets in TARGETS.items():
            if journal_id not in selected_journals:
                continue
            journal = journals.get(journal_id)
            if not journal:
                continue
            for target in targets:
                future = executor.submit(
                    fetch_target_with_fallback,
                    journal,
                    target,
                    timeout=args.timeout,
                    detail_limit=args.detail_limit,
                    max_items=args.max_items_per_source,
                )
                jobs[future] = (journal_id, journal, target)

        per_journal: dict[str, dict[str, Any]] = {}
        for future in as_completed(jobs):
            journal_id, _journal, target = jobs[future]
            fetched, fallback_count, publisher_success, result_messages, failed = future.result()
            records.extend(fetched)
            state = per_journal.setdefault(
                journal_id,
                {"count": 0, "fallback_count": 0, "publisher_ok": False, "failed": 0},
            )
            state["count"] += len(fetched)
            state["fallback_count"] += fallback_count
            state["publisher_ok"] = bool(state["publisher_ok"] or publisher_success)
            state["failed"] += int(failed)
            messages.extend(result_messages)

        for journal_id in selected_journals:
            if journal_id not in per_journal or journal_id not in journals:
                continue
            state = per_journal[journal_id]
            failures += int(state["failed"])
            if not state["count"]:
                messages.append(f"{journal_id}: 0")
            journal_status[journal_id] = {
                "ok": bool(state["count"]),
                "count": state["count"],
                "publisher_ok": state["publisher_ok"],
                "fallback_count": state["fallback_count"],
            }

    write_json(output, records)
    record_source(
        "priority-toc",
        # Keep the source usable when at least one priority journal produced
        # records and another optional publisher page was unavailable.
        ok=failures == 0 or bool(records),
        count=len(records),
        message="; ".join(messages[-20:]) or str(output),
        details={"journals": journal_status, "partial_failures": failures},
    )
    print(f"wrote {len(records)} priority TOC records to {output}")
    for message in messages:
        print(message)


if __name__ == "__main__":
    main()
