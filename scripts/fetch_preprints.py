"""Fetch working paper and policy paper metadata sources.

The first implementation is intentionally conservative: collect public
metadata from feeds or list pages, never bulk-download PDFs, and keep failed
sources non-fatal for scheduled runs.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from xml.etree import ElementTree

from common import DATA_DIR, fetch_json, fetch_text, now_iso, today_str, write_json
from status import record_source


ATOM = "{http://www.w3.org/2005/Atom}"
RSS10 = "{http://purl.org/rss/1.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"


def load_sources(path: Path) -> list[dict[str, Any]]:
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return list(loaded.get("sources") or [])
    except Exception:
        # Narrow fallback for the checked-in YAML shape.
        sources: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        list_key: str | None = None
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped == "sources:":
                continue
            if line.startswith("  - id:"):
                if current:
                    sources.append(current)
                current = {"id": stripped.removeprefix("- id:").strip(), "fields": []}
                list_key = None
                continue
            if current is None:
                continue
            if line.startswith("    ") and not line.startswith("      "):
                key, _, value = stripped.partition(":")
                if value == "":
                    list_key = key
                    continue
                value = value.strip().strip('"')
                current[key] = int(value) if key == "stage" and value.isdigit() else value
                list_key = None
                continue
            if list_key and line.startswith("      - "):
                current.setdefault(list_key, []).append(stripped.removeprefix("- ").strip().strip('"'))
        if current:
            sources.append(current)
        return sources


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    value = fix_mojibake(value)
    return value.strip()


def is_boilerplate_text(value: str | None) -> bool:
    normalized = " ".join((value or "").split()).casefold()
    boilerplates = [
        "founded in 1920, the nber is a private",
        "the federal reserve board of governors in washington dc",
    ]
    return any(fragment in normalized for fragment in boilerplates)


def fix_mojibake(value: str) -> str:
    replacements = {
        "鈥檚": "'s",
        "鈥檛": "n't",
        "鈥?": "-",
        "鈥�": "-",
        "鈥�": "-",
        "鈥淪": '"S',
        "鈥漵": '"',
        "鈥": "'",
        "â€™": "'",
        "â€œ": '"',
        "â€\x9d": '"',
        "â€“": "-",
        "â€”": "-",
        "Â ": " ",
        "Â": "",
    }
    for bad, good in replacements.items():
        value = value.replace(bad, good)
    return value


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    value = clean_text(value)
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except Exception:
        pass
    match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", value)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    month_names = {
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
    match = re.search(r"\b([A-Z][a-z]+)\.?\s+(\d{1,2}),?\s+(20\d{2})\b", value)
    if match:
        month_text, day, year = match.groups()
        month = month_names.get(month_text.lower())
        if month:
            return f"{year}-{month:02d}-{int(day):02d}"
    match = re.search(r"\b(\d{1,2})\s+([A-Z][a-z]+)\.?\s+(20\d{2})\b", value)
    if match:
        day, month_text, year = match.groups()
        month = month_names.get(month_text.lower())
        if month:
            return f"{year}-{month:02d}-{int(day):02d}"
    match = re.search(r"\b([A-Z][a-z]+)\.?\s+(20\d{2})\b", value)
    if match:
        month_text, year = match.groups()
        month = month_names.get(month_text.lower())
        if month:
            return f"{year}-{month:02d}-01"
    return None


def month_year_date(value: str | None) -> str | None:
    if not value:
        return None
    month_names = {
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
    match = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(20\d{2})\b",
        clean_text(value),
        flags=re.I,
    )
    if not match:
        return None
    month = month_names.get(match.group(1).casefold().rstrip("."))
    if not month:
        return None
    return f"{match.group(2)}-{month:02d}-01"


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    clean = parsed._replace(query="", fragment="")
    return clean.geturl().rstrip("/")


def first_match(patterns: list[str], text: str, flags: int = re.I | re.S) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=flags)
        if match:
            return clean_text(match.group(1))
    return None


def meta_values(html_text: str, names: list[str]) -> list[str]:
    values: list[str] = []
    for name in names:
        pattern = (
            r'<meta[^>]+(?:name|property)=["\']'
            + re.escape(name)
            + r'["\'][^>]+content=["\']([^"\']+)["\'][^>]*>'
        )
        values.extend(clean_text(match) for match in re.findall(pattern, html_text, flags=re.I | re.S))
        pattern = (
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']'
            + re.escape(name)
            + r'["\'][^>]*>'
        )
        values.extend(clean_text(match) for match in re.findall(pattern, html_text, flags=re.I | re.S))
    return [value for value in values if value]


def json_ld_objects(html_text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_text, flags=re.I | re.S):
        payload = clean_text(match.group(1))
        try:
            parsed = json.loads(payload)
        except Exception:
            continue
        candidates = parsed if isinstance(parsed, list) else [parsed]
        for item in candidates:
            if isinstance(item, dict):
                objects.append(item)
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                objects.extend(node for node in item["@graph"] if isinstance(node, dict))
    return objects


def json_ld_value(html_text: str, keys: list[str]) -> str | None:
    for item in json_ld_objects(html_text):
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return clean_text(value)
            if isinstance(value, list) and value:
                pieces = []
                for part in value:
                    if isinstance(part, str):
                        pieces.append(part)
                    elif isinstance(part, dict) and part.get("name"):
                        pieces.append(str(part["name"]))
                if pieces:
                    return clean_text(", ".join(pieces))
    return None


def iza_detail_authors(html_text: str) -> list[str]:
    """Extract authors from the current IZA detail-page author bar.

    The redesigned IZA site no longer emits citation_author or JSON-LD
    author metadata. Authors are rendered as links in the text block directly
    below the title, so keep this fallback deliberately scoped to IZA markup.
    """
    match = re.search(
        r'<div[^>]+class=["\'][^"\']*text-\[#000\][^"\']*["\'][^>]*>(?P<body>.*?)</div>',
        html_text,
        flags=re.I | re.S,
    )
    if not match:
        return []
    body = match.group("body")
    text = clean_text(body)
    authors = [clean_text(part) for part in re.split(r"\s*,\s*|\s+and\s+", text) if clean_text(part)]
    return list(dict.fromkeys(authors))[:12]


def detect_paper_number(source: dict[str, Any], title: str, url: str | None) -> str | None:
    source_id = str(source.get("id") or "")
    text = f"{title} {url or ''}"
    patterns = {
        "nber": [r"/papers/(w\d+)", r"\b(w\d{4,})\b"],
        "iza": [r"/dp/(\d+)/", r"\bDP\s*No\.?\s*(\d+)\b"],
        "cepr-dp": [r"/publications/(dp\d+)", r"\bDP\s*(\d{4,})\b"],
        "fed-feds": [r"/econres/feds/([^/.]+)", r"\bFEDS\s*(\d{4}-\d+)\b"],
        "bis-working-papers": [r"/publ/(work\d+)", r"\bWorking Paper[s]?\s*No\.?\s*(\d+)\b"],
        "imf-working-papers": [r"\bWP/(\d+/\d+)\b", r"/Issues/.+?/([^/]+)$"],
        "world-bank-prwp": [r"/entities/publication/([^/?#]+)"],
        "cesifo-working-papers": [r"\bWorking Paper\s*No\.?\s*(\d+)\b"],
    }
    for pattern in patterns.get(source_id, []):
        match = re.search(pattern, text, flags=re.I)
        if match:
            return clean_text(match.group(1))
    return None


def child_text(node: ElementTree.Element, names: list[str]) -> str | None:
    for name in names:
        child = node.find(name)
        if child is not None and child.text:
            return child.text.strip()
    wanted = {name.split("}")[-1].split(":")[-1] for name in names}
    for child in list(node):
        local_name = child.tag.split("}")[-1]
        if local_name in wanted and child.text:
            return child.text.strip()
    return None


def feed_authors(node: ElementTree.Element) -> list[str]:
    values: list[str] = []
    for child in node.iter():
        local_name = child.tag.split("}")[-1].split(":")[-1].casefold()
        if local_name not in {"author", "creator"}:
            continue
        text = clean_text(" ".join(child.itertext()))
        for value in re.split(r"\s*;\s*|\s+and\s+", text, flags=re.I):
            value = clean_text(value)
            if value and value not in values:
                values.append(value)
    return values[:12]


def source_record(
    source: dict[str, Any],
    *,
    title: str,
    url: str | None,
    published: str | None = None,
    abstract: str | None = None,
    authors: list[str] | None = None,
) -> dict[str, Any]:
    source_type = str(source.get("type") or "working_paper")
    source_id = str(source.get("id") or "")
    source_title = str(source.get("title") or source.get("id") or "Working Paper")
    clean_url = normalize_url(url)
    clean_title = clean_text(title)
    detected_number = detect_paper_number(source, clean_title, clean_url)
    if source_id == "cepr-dp":
        match = re.match(r"^(DP\s*\d{4,})\s*[:：.-]?\s+(.+)$", clean_title, flags=re.I)
        if match:
            detected_number = detected_number or match.group(1).replace(" ", "").upper()
            clean_title = clean_text(match.group(2))
    return {
        "title": clean_title,
        "authors": authors or [],
        "journal": source_title,
        "journal_id": f"source-{source.get('id')}",
        "publisher": source_title,
        "fields": source.get("fields") or ["general"],
        "source": "working_papers",
        "source_type": source_type,
        "source_id": source.get("id"),
        "source_name": source_title,
        "series": source_title,
        "paper_number": detected_number,
        "source_url": source.get("feed") or source.get("homepage"),
        "url": clean_url,
        "doi": None,
        "abstract": clean_text(abstract) or None,
        "published_online": published,
        "available_online": published,
        "date_source": "rss_published" if published and source.get("feed") else ("source_list_date" if published else None),
        "date_confidence": "B" if published else "F",
        "detected_at": now_iso(),
    }


def clean_markdown_text(value: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    return clean_text(value.replace("*", " "))


def parse_cepr_proxy_markdown(markdown: str) -> tuple[list[str], str | None]:
    """Extract paper metadata from the CEPR page returned by r.jina.ai.

    CEPR pages contain navigation and governance links alongside the paper
    metadata. Keep this parser deliberately section-based so an Advisory
    Board link cannot become the paper's author list.
    """
    abstract: str | None = None
    abstract_match = re.search(
        r"(?is)(?:^|\n)\s*(?:#{1,4}\s*)?(?:\*\*|__)?abstract(?:\*\*|__)?\s*:?[ \t]*\n+"
        r"(?P<body>.*?)(?=\n\s*(?:#{1,4}\s*)?(?:\*\*|__)?"
        r"(?:keywords?|jel|download|citation|doi|related|references?)"
        r"(?:\*\*|__)?\b|\Z)",
        markdown,
    )
    if abstract_match:
        candidate = clean_markdown_text(abstract_match.group("body"))
        if len(candidate) >= 80 and not is_boilerplate_text(candidate):
            abstract = candidate
    if not abstract:
        # Some CEPR pages expose the English summary only inside the URL-encoded
        # language widget, without an Abstract heading in the rendered page.
        decoded = clean_markdown_text(unquote(markdown))
        embedded_match = re.search(
            r"(?is)\b(?:This paper|This study|We examine|We investigate|We develop|We estimate)\b"
            r".{160,5000}?(?=\s+(?:Translation created by Artificial Intelligence|Share via|Keywords)\b)",
            decoded,
        )
        if embedded_match:
            candidate = clean_markdown_text(embedded_match.group(0))
            if len(candidate) >= 80 and not is_boilerplate_text(candidate):
                abstract = candidate

    author_block = re.search(
        r"(?is)(?:^|\n)\s*(?:#{1,4}\s*)?(?:\*\*|__)?authors?"
        r"(?:\*\*|__)?\s*:?[ \t]*\n(?P<body>.*?)(?=\n\s*(?:#{1,4}\s*)?"
        r"(?:\*\*|__)?abstract(?:\*\*|__)?\b|\Z)",
        markdown,
    )
    author_text = author_block.group("body") if author_block else ""
    authors = [
        clean_markdown_text(value)
        for value in re.findall(
            r"\[([^\]]+)\]\(https?://cepr\.org/about/people/[^)]+\)",
            author_text,
            flags=re.I,
        )
    ]
    if not authors:
        authors = [
            clean_markdown_text(value)
            for value in re.findall(
                r"\[([^\]]+)\]\(https?://cepr\.org/about/people/[^)]+\)",
                markdown,
                flags=re.I,
            )
        ]
    blocked = {"advisory board", "cepr people", "research fellows", "staff"}
    authors = list(dict.fromkeys(value for value in authors if value.casefold() not in blocked))[:12]
    return authors, abstract


def enrich_record_from_proxy(record: dict[str, Any], source_id: str, *, timeout: int) -> dict[str, Any]:
    url = str(record.get("url") or "")
    if source_id not in {"fed-feds", "cepr-dp"} or not url:
        return record
    parsed = urlparse(url)
    target = f"http://{parsed.netloc}{parsed.path}"
    try:
        markdown = fetch_text(f"https://r.jina.ai/{target}", timeout=timeout)
    except Exception:
        return record
    authors: list[str] = []
    if source_id == "fed-feds":
        match = re.search(r"(?ms)^###\s+[^\r\n]+\s*\r?\n\s*\r?\n([^\r\n]+)\s*\r?\n\s*\r?\n\*\*Abstract", markdown)
        author_line = clean_markdown_text(match.group(1)) if match else ""
        author_line = re.sub(r",\s+and\s+", ", ", author_line, flags=re.I)
        authors = [clean_text(value) for value in re.split(r"\s*,\s*|\s+and\s+", author_line) if clean_text(value)]
        doi_match = re.search(r"https://doi\.org/(10\.17016/FEDS\.\d{4}\.\d+)", markdown, flags=re.I)
        if doi_match:
            record["doi"] = doi_match.group(1)
        abstract_match = re.search(r"(?ms)\*\*Abstract:\*\*\s*(.*?)(?=\n\*\*Keywords|\n\*\*DOI)", markdown)
        if abstract_match:
            record["abstract"] = clean_markdown_text(abstract_match.group(1))
    else:
        authors, abstract = parse_cepr_proxy_markdown(markdown)
        if abstract:
            record["abstract"] = abstract
            record["abstract_source"] = "cepr_proxy_markdown"
        # Jina preserves the page-level publication timestamp even when the
        # rendered CEPR page has no visible date heading. Use it as publisher
        # evidence, never as a detection timestamp.
        published_match = re.search(
            r"(?im)^Published\s+Time:\s*(\d{4}-\d{2}-\d{2})\s*$",
            markdown,
        )
        published = published_match.group(1) if published_match else None
        if published:
            current = str(record.get("published_online") or "")
            if not current or str(record.get("date_confidence") or "") in {"F", "unknown"}:
                record["published_online"] = published
                record["available_online"] = published
                record["official_date"] = published
                record["date_source"] = "cepr_published_time"
                record["date_confidence"] = "B"
    authors = list(dict.fromkeys(value for value in authors if value))[:12]
    if authors:
        record["authors"] = authors
    return record


def enrich_record_from_detail(record: dict[str, Any], source: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    if source.get("id") == "world-bank-prwp":
        return enrich_world_bank_from_detail(record, source, timeout=timeout) or record

    url = record.get("url")
    if not url:
        return record
    source_id = str(source.get("id") or "")
    try:
        html_text = fetch_text(str(url), timeout=timeout)
    except Exception:
        return enrich_record_from_proxy(record, source_id, timeout=timeout)

    title_meta_names = ["citation_title", "dc.title", "og:title"]
    if source_id == "fed-feds":
        title = (
            (meta_values(html_text, title_meta_names) or [None])[0]
            or json_ld_value(html_text, ["headline", "name"])
            or first_match([r'<h1[^>]*>(.*?)</h1>'], html_text)
        )
    else:
        title = (
            first_match([r'<h1[^>]*>(.*?)</h1>'], html_text)
            or (meta_values(html_text, title_meta_names) or [None])[0]
            or json_ld_value(html_text, ["headline", "name"])
        )
    if title and plausible_title(title):
        record["title"] = title

    authors = meta_values(html_text, ["citation_author", "dc.creator", "author"])
    if authors:
        record["authors"] = list(dict.fromkeys(authors))[:12]
    elif json_authors := json_ld_value(html_text, ["author", "creator"]):
        record["authors"] = [item.strip() for item in json_authors.split(",") if item.strip()][:12]
    elif source_id == "iza":
        detail_authors = iza_detail_authors(html_text)
        if detail_authors:
            record["authors"] = detail_authors

    detail_abstract_patterns = [
        r'<div[^>]+class=["\'][^"\']*page-header__intro[^"\']*["\'][^>]*>\s*<div[^>]*>\s*<p[^>]*>(.*?)</p>',
        r'<h2[^>]*>\s*Abstract\s*</h2>\s*<p[^>]*>(.*?)</p>',
        r'<(?:strong|b)[^>]*>\s*Abstract\s*:?\s*</(?:strong|b)>\s*(?:</?[^>]+>\s*){0,3}<p[^>]*>(.*?)</p>',
        r'(?:Abstract|Summary)\s*:?\s*</?[^>]*>\s*(.{160,2500}?)(?:</p>|<h2|<h3|<div[^>]+class=["\'][^"\']*(?:author|download|citation))',
        r'<div[^>]+class=["\'][^"\']*abstract[^"\']*["\'][^>]*>(.*?)</div>',
        r'<section[^>]+class=["\'][^"\']*abstract[^"\']*["\'][^>]*>(.*?)</section>',
    ]
    generic_description = None if source_id == "nber" else (meta_values(html_text, ["description", "og:description"]) or [None])[0]
    abstract = (
        (meta_values(html_text, ["citation_abstract", "dc.description"]) or [None])[0]
        or first_match(detail_abstract_patterns, html_text)
        or generic_description
        or json_ld_value(html_text, ["description", "abstract"])
    )
    if abstract and not is_boilerplate_text(abstract):
        record["abstract"] = clean_text(abstract)

    date_value = (
        (
            meta_values(
                html_text,
                [
                    "citation_publication_date",
                    "citation_online_date",
                    "citation_date",
                    "article:published_time",
                    "date",
                    "dc.date",
                    "dc.date.issued",
                    "dc.created",
                    "dcterms.issued",
                    "dcterms.created",
                ],
            )
            or [None]
        )[0]
        or json_ld_value(html_text, ["datePublished", "dateCreated", "dateModified"])
        or first_match(
            [
                r'(?:Published|Posted|Date|Release\s+Date|Issued|First\s+posted)\s*:?\s*(?:</?[^>]+>\s*){0,4}([A-Z][a-z]+\.?\s+\d{1,2},?\s+20\d{2})',
                r'(?:Published|Posted|Date|Release\s+Date|Issued|First\s+posted)\s*:?\s*(?:</?[^>]+>\s*){0,4}(\d{1,2}\s+[A-Z][a-z]+\.?\s+20\d{2})',
                r'(?:Published|Posted|Date|Release\s+Date|Issued|First\s+posted)\s*:?\s*(?:</?[^>]+>\s*){0,4}(20\d{2}-\d{1,2}-\d{1,2})',
                r'<time[^>]+datetime=["\']([^"\']+)["\']',
                r'<span[^>]+class=["\'][^"\']*(?:date|published|posted)[^"\']*["\'][^>]*>(.*?)</span>',
            ],
            html_text,
        )
    )
    parsed_date = parse_date(date_value)
    if parsed_date:
        record["published_online"] = parsed_date
        record["available_online"] = parsed_date
        record["date_source"] = "publisher_detail"
        record["date_confidence"] = "B"
    elif source_id == "iza":
        month_date = month_year_date(
            first_match(
                [
                    r">\s*((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+20\d{2})\s*<",
                    r"\b((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+20\d{2})\b",
                ],
                html_text,
            )
        )
        if month_date:
            record["published_online"] = month_date
            record["available_online"] = month_date
            record["date_precision"] = "month"
            record["date_source"] = "iza_detail_month"
            record["date_confidence"] = "C"
    elif "ideas.repec.org/" in str(url or ""):
        year_value = (
            first_match([r'\b(20\d{2})\b'], str(date_value or ""), flags=re.I)
            or first_match([r'"datePublished"\s*:\s*"(20\d{2})"'], html_text, flags=re.I)
            or (meta_values(html_text, ["citation_publication_date"]) or [None])[0]
        )
        year_match = re.search(r"\b(20\d{2})\b", str(year_value or ""))
        if year_match:
            record["published_online"] = f"{year_match.group(1)}-01-01"
            record["available_online"] = f"{year_match.group(1)}-01-01"
            record["date_source"] = "repec_detail_year"
            record["date_confidence"] = "C"

    doi = (meta_values(html_text, ["citation_doi", "dc.identifier"]) or [None])[0]
    if doi and "10." in doi:
        doi_match = re.search(r"(10\.\d{4,9}/\S+)", doi)
        record["doi"] = doi_match.group(1).rstrip(".") if doi_match else doi

    pdf = (meta_values(html_text, ["citation_pdf_url"]) or [None])[0]
    if pdf:
        record["pdf_url"] = urljoin(str(url), pdf)
    record["paper_number"] = record.get("paper_number") or detect_paper_number(source, str(record.get("title") or ""), str(record.get("url") or ""))
    if not record.get("authors") and source_id in {"fed-feds", "cepr-dp"}:
        enrich_record_from_proxy(record, source_id, timeout=timeout)
    return record


def world_bank_uuid(record: dict[str, Any]) -> str | None:
    for value in (record.get("paper_number"), record.get("url")):
        if not isinstance(value, str):
            continue
        match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", value, flags=re.I)
        if match:
            return match.group(1)
    return None


def first_metadata_value(item: dict[str, Any], keys: list[str]) -> str | None:
    values = metadata_values(item, keys)
    return values[0] if values else None


def world_bank_pdf_url(detail: dict[str, Any]) -> str | None:
    for value in nested_values(detail):
        if not isinstance(value, dict):
            continue
        href = value.get("href")
        if isinstance(href, str) and "/bitstreams/" in href:
            return href if href.startswith("http") else urljoin("https://openknowledge.worldbank.org", href)
        uuid = value.get("uuid")
        if isinstance(uuid, str) and value.get("bundleName") != "LICENSE":
            name = str(value.get("name") or value.get("uuid") or "")
            if name.lower().endswith(".pdf") or value.get("format") == "Adobe PDF":
                return f"https://openknowledge.worldbank.org/bitstreams/{uuid}/download"
    return None


def enrich_world_bank_from_detail(record: dict[str, Any], source: dict[str, Any], *, timeout: int) -> dict[str, Any] | None:
    uuid = world_bank_uuid(record)
    if not uuid:
        return None
    detail_url = f"https://openknowledge.worldbank.org/server/api/core/items/{uuid}"
    try:
        detail = fetch_json(detail_url, timeout=timeout)
    except Exception:
        return None
    if not isinstance(detail, dict):
        return None

    title = first_metadata_value(detail, ["dc.title", "title"])
    if title and plausible_title(title):
        record["title"] = title

    authors = list(dict.fromkeys(metadata_values(detail, ["dc.contributor.author", "contributor.author", "author"])))
    if authors:
        record["authors"] = authors[:12]

    abstract = first_metadata_value(detail, ["dc.description.abstract", "description.abstract"])
    if abstract and not is_boilerplate_text(abstract):
        record["abstract"] = abstract
        record["abstract_source"] = "world_bank_detail_api"

    date_value = first_metadata_value(detail, ["dc.date.issued", "date.issued", "issued"])
    parsed_date = parse_date(date_value)
    if parsed_date:
        record["published_online"] = parsed_date
        record["available_online"] = parsed_date
        record["date_source"] = "world_bank_detail_api"
        record["date_confidence"] = "B"

    doi = first_metadata_value(detail, ["dc.identifier.doi", "identifier.doi", "doi"])
    if doi:
        record["doi"] = doi

    handle = first_metadata_value(detail, ["dc.identifier.uri", "identifier.uri"])
    if handle and handle.startswith("http"):
        record["source_url"] = handle

    pdf = world_bank_pdf_url(detail)
    if pdf:
        record["pdf_url"] = pdf

    record["paper_number"] = uuid
    return record


def parse_feed(xml_text: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(xml_text)
    records: list[dict[str, Any]] = []
    if root.tag.endswith("RDF"):
        for item in root.findall(f".//{RSS10}item") or [node for node in root.iter() if node.tag.endswith("item")]:
            title = clean_text(child_text(item, ["title"]))
            link = child_text(item, ["link"])
            if not title or not allowed_url(source, link):
                continue
            published = parse_date(child_text(item, [f"{DC}date", "date", "dc:date"]))
            abstract = child_text(item, ["description", "summary"])
            records.append(source_record(source, title=title, url=link, published=published, abstract=abstract, authors=feed_authors(item)))
        return records
    if root.tag.endswith("rss") or root.find("channel") is not None:
        for item in root.findall("./channel/item"):
            title = clean_text(child_text(item, ["title"]))
            if not title:
                continue
            link = child_text(item, ["link", "guid"])
            if not allowed_url(source, link):
                continue
            published = parse_date(child_text(item, ["pubDate", "date", "dc:date"]))
            abstract = child_text(item, ["description", "summary"])
            records.append(source_record(source, title=title, url=link, published=published, abstract=abstract, authors=feed_authors(item)))
        return records

    for entry in root.findall(f".//{ATOM}entry"):
        title = clean_text(child_text(entry, [f"{ATOM}title"]))
        if not title:
            continue
        link = None
        link_node = entry.find(f"{ATOM}link")
        if link_node is not None:
            link = link_node.attrib.get("href")
        if not allowed_url(source, link):
            continue
        published = parse_date(child_text(entry, [f"{ATOM}published", f"{ATOM}updated"]))
        abstract = child_text(entry, [f"{ATOM}summary", f"{ATOM}content"])
        records.append(source_record(source, title=title, url=link, published=published, abstract=abstract, authors=feed_authors(entry)))
    return records


def parse_html_list(html_text: str, source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    base_url = str(source.get("homepage") or "")
    records: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    title_patterns = [
        r'<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>[^<]{20,240})</a>',
        r'<h[23][^>]*>\s*<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>.*?)</a>\s*</h[23]>',
    ]
    for pattern in title_patterns:
        for match in re.finditer(pattern, html_text, flags=re.I | re.S):
            title = clean_text(match.group("title"))
            if not plausible_title(title) or title.lower() in seen_titles:
                continue
            href = html.unescape(match.group("href"))
            if href.startswith("#") or href.lower().startswith("javascript:"):
                continue
            absolute_url = urljoin(base_url, href)
            if not allowed_url(source, absolute_url):
                continue
            seen_titles.add(title.lower())
            records.append(source_record(source, title=title, url=absolute_url))
            if len(records) >= limit:
                return records
    return records


def parse_nber_list(html_text: str, source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(r'<a[^>]+href=["\'](?P<href>/papers/w\d+)["\'][^>]*>(?P<title>.*?)</a>', html_text, flags=re.I | re.S):
        url = urljoin(str(source.get("homepage")), match.group("href"))
        title = clean_text(match.group("title"))
        if not plausible_title(title) or url in seen:
            continue
        seen.add(url)
        records.append(source_record(source, title=title, url=url))
        if len(records) >= limit:
            break
    return records


def parse_imf_list(html_text: str, source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = r'<a[^>]+href=["\'](?P<href>[^"\']*/en/Publications/WP/Issues/[^"\']+)["\'][^>]*>(?P<title>.*?)</a>'
    for match in re.finditer(pattern, html_text, flags=re.I | re.S):
        url = normalize_url(urljoin("https://www.imf.org", html.unescape(match.group("href"))))
        title = clean_text(match.group("title"))
        if not plausible_title(title):
            window = html_text[max(0, match.start() - 900) : min(len(html_text), match.end() + 900)]
            title = (
                first_match([r'<h[23][^>]*>(.*?)</h[23]>', r'class=["\'][^"\']*title[^"\']*["\'][^>]*>(.*?)<'], window)
                or title
            )
        if not url or url in seen or not plausible_title(title):
            continue
        seen.add(url)
        records.append(source_record(source, title=title, url=url))
        if len(records) >= limit:
            break
    return records


def parse_bis_list(html_text: str, source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = r'<a[^>]+href=["\'](?P<href>[^"\']*/publ/work\d+[^"\']*)["\'][^>]*>(?P<title>.*?)</a>'
    for match in re.finditer(pattern, html_text, flags=re.I | re.S):
        url = normalize_url(urljoin("https://www.bis.org", html.unescape(match.group("href"))))
        title = clean_text(match.group("title"))
        if not plausible_title(title):
            window = html_text[max(0, match.start() - 700) : min(len(html_text), match.end() + 1200)]
            title = (
                first_match(
                    [
                        r'<span[^>]+class=["\'][^"\']*title[^"\']*["\'][^>]*>(.*?)</span>',
                        r'<td[^>]*>\s*<a[^>]+/publ/work\d+[^>]*>.*?</a>\s*</td>\s*<td[^>]*>(.*?)</td>',
                        r'<a[^>]+/publ/work\d+[^>]*>.*?</a>\s*</[^>]+>\s*<[^>]+>(.*?)</[^>]+>',
                    ],
                    window,
                )
                or title
            )
        if not url or url in seen or not plausible_title(title):
            continue
        seen.add(url)
        records.append(source_record(source, title=title, url=url))
        if len(records) >= limit:
            break
    return records


def parse_world_bank_list(html_text: str, source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = r'<a[^>]+href=["\'](?P<href>[^"\']*/entities/publication/[^"\']+)["\'][^>]*>(?P<title>.*?)</a>'
    for match in re.finditer(pattern, html_text, flags=re.I | re.S):
        url = normalize_url(urljoin(str(source.get("homepage")), html.unescape(match.group("href"))))
        title = clean_text(match.group("title"))
        if not url or url in seen or not plausible_title(title):
            continue
        seen.add(url)
        records.append(source_record(source, title=title, url=url))
        if len(records) >= limit:
            break
    return records


def parse_repec_cesifo_list(html_text: str, source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = (
        r'<LI[^>]*class=["\'][^"\']*list-group-item[^"\']*["\'][^>]*>\s*'
        r'<B>\s*(?P<number>\d+)\s*<A\s+HREF=["\'](?P<href>/p/ces/ceswps/[^"\']+)["\']>(?P<title>.*?)</A></B>'
        r'(?P<tail>.*?)</LI>'
    )
    for match in re.finditer(pattern, html_text, flags=re.I | re.S):
        url = normalize_url(urljoin(str(source.get("homepage")), html.unescape(match.group("href"))))
        title = clean_text(match.group("title"))
        if not url or url in seen or not plausible_title(title):
            continue
        record = source_record(source, title=title, url=url)
        record["paper_number"] = match.group("number")
        tail = match.group("tail")
        authors = first_match([r'<I>\s*by\s*</I>\s*(.*?)(?:<BR|</LI|<span|$)'], tail)
        if authors:
            record["authors"] = [clean_text(part) for part in re.split(r"\s*&\s*|\s+and\s+|;", authors) if clean_text(part)][:12]
        year = first_match([r'\b(20\d{2})\b'], tail, flags=re.I)
        if year:
            record["published_online"] = f"{year}-01-01"
            record["available_online"] = f"{year}-01-01"
            record["date_source"] = "repec_series_year"
            record["date_confidence"] = "C"
        seen.add(url)
        records.append(record)
        if len(records) >= limit:
            break
    return records


def parse_repec_series_list(html_text: str, source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = (
        r'<LI[^>]*class=["\'][^"\']*list-group-item[^"\']*["\'][^>]*>\s*'
        r'(?:<B>\s*)?(?P<number>[^<\s]{1,40})?\s*'
        r'<A\s+HREF=["\'](?P<href>/p/[^"\']+)["\']>(?P<title>.*?)</A>'
        r'(?P<tail>.*?)</LI>'
    )
    for match in re.finditer(pattern, html_text, flags=re.I | re.S):
        url = normalize_url(urljoin(str(source.get("homepage")), html.unescape(match.group("href"))))
        title = clean_text(match.group("title"))
        if not url or url in seen or not plausible_title(title):
            continue
        record = source_record(source, title=title, url=url)
        number = clean_text(match.group("number") or "")
        if number and not number.startswith("<"):
            record["paper_number"] = number
        tail = match.group("tail")
        authors = first_match([r'<I>\s*by\s*</I>\s*(.*?)(?:<BR|</LI|<span|$)'], tail)
        if authors:
            record["authors"] = [clean_text(part) for part in re.split(r"\s*&\s*|\s+and\s+|;", authors) if clean_text(part)][:12]
        year = first_match([r'\b(20\d{2})\b'], tail, flags=re.I)
        if year:
            record["published_online"] = f"{year}-01-01"
            record["available_online"] = f"{year}-01-01"
            record["date_source"] = "repec_series_year"
            record["date_confidence"] = "C"
        seen.add(url)
        records.append(record)
        if len(records) >= limit:
            break
    return records


def parse_nep_issue_list(
    html_text: str,
    source: dict[str, Any],
    limit: int,
    *,
    issue_date: str | None = None,
    issue_url: str | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    issue_url = issue_url or str(source.get("homepage") or "")
    detail_pattern = (
        r'<li[^>]+class=["\'][^"\']*coblo_li[^"\']*["\'][^>]*>\s*'
        r'<div[^>]+id=["\'](?P<paper>p\d+)["\'][^>]*>\s*'
        r'<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>.*?)</a>\s*</div>'
        r'(?P<body>.*?)</li>'
    )
    for match in re.finditer(detail_pattern, html_text, flags=re.I | re.S):
        paper_number = clean_text(match.group("paper"))
        href = html.unescape(match.group("href"))
        url = normalize_url(urljoin(issue_url, href))
        title = clean_text(match.group("title"))
        if not url or url in seen or not plausible_nep_title(title):
            continue
        body = match.group("body")
        record = source_record(source, title=title, url=url, published=issue_date, abstract=nep_field(body, "Abstract"))
        record["source_url"] = issue_url
        record["date_source"] = "nep_issue_date" if issue_date else record.get("date_source")
        record["date_confidence"] = "B" if issue_date else record.get("date_confidence")
        record["paper_number"] = paper_number
        authors = nep_authors(body)
        if authors:
            record["authors"] = authors
        seen.add(url)
        records.append(record)
        if len(records) >= limit:
            return records
    patterns = [
        r'<a[^>]+href=["\'](?P<href>https://ideas\.repec\.org/p/[^"\']+)["\'][^>]*>(?P<title>.*?)</a>',
        r'<a[^>]+href=["\'](?P<href>/p/[^"\']+)["\'][^>]*>(?P<title>.*?)</a>',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, html_text, flags=re.I | re.S):
            href = html.unescape(match.group("href"))
            url = normalize_url(urljoin("https://ideas.repec.org", href))
            title = clean_text(match.group("title"))
            abstract = title if looks_like_abstract(title) else None
            if abstract:
                title = ""
            if not url or url in seen or not plausible_title(title):
                window = html_text[max(0, match.start() - 700) : min(len(html_text), match.end() + 900)]
                title = (
                    first_match([r'<b[^>]*>(.*?)</b>', r'<strong[^>]*>(.*?)</strong>', r'<h[23][^>]*>(.*?)</h[23]>'], window)
                    or title
                )
                if looks_like_abstract(title):
                    abstract = abstract or title
                    title = ""
            if not url or url in seen or not plausible_title(title):
                continue
            record = source_record(source, title=title, url=url, published=issue_date, abstract=abstract)
            record["date_source"] = "nep_issue_date" if issue_date else record.get("date_source")
            record["date_confidence"] = "B" if issue_date else record.get("date_confidence")
            record["paper_number"] = record.get("paper_number") or first_match([r"/p/([^/.]+/[^/.]+/[^/.]+)"], url)
            seen.add(url)
            records.append(record)
            if len(records) >= limit:
                return records
    anchor_pattern = (
        r'<li[^>]+class=["\'][^"\']*liblo_li[^"\']*["\'][^>]*>\s*'
        r'<a[^>]+class=["\'][^"\']*indoc[^"\']*["\'][^>]+href=["\'](?P<href>#[^"\']+)["\'][^>]*>(?P<title>.*?)</a>'
        r'(?P<tail>.*?)</li>'
    )
    for match in re.finditer(anchor_pattern, html_text, flags=re.I | re.S):
        href = html.unescape(match.group("href"))
        title = clean_text(match.group("title"))
        abstract = title if looks_like_abstract(title) else None
        if abstract:
            title = ""
        if not plausible_nep_title(title):
            title = f"{source.get('title') or 'RePEc NEP'} item {href.removeprefix('#')}"
        url = f"{issue_url}{href}"
        if url in seen:
            continue
        record = source_record(source, title=title, url=url, published=issue_date, abstract=abstract)
        record["url"] = url
        record["source_url"] = issue_url
        record["date_source"] = "nep_issue_date" if issue_date else record.get("date_source")
        record["date_confidence"] = "B" if issue_date else record.get("date_confidence")
        record["paper_number"] = href.removeprefix("#")
        tail = match.group("tail")
        authors = re.findall(r'aus=([^"&]+)', tail, flags=re.I)
        if authors:
            record["authors"] = [clean_text(html.unescape(author).replace("%20", " ")) for author in authors][:12]
        seen.add(url)
        records.append(record)
        if len(records) >= limit:
            return records
    return records


def parse_specialized_html(html_text: str, source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    source_id = str(source.get("id") or "")
    if source_id == "nber":
        return parse_nber_list(html_text, source, limit)
    if source_id == "cepr-dp":
        candidates: dict[int, dict[str, Any]] = {}
        for match in re.finditer(
            r'<a[^>]+href=["\'](?P<href>[^"\']*/publications/dp(?P<number>\d+)[^"\']*)["\'][^>]*>(?P<title>.*?)</a>',
            html_text,
            flags=re.I | re.S,
        ):
            title = clean_text(match.group("title"))
            url = normalize_url(urljoin(str(source.get("homepage") or ""), html.unescape(match.group("href"))))
            if not url or not plausible_title(title):
                continue
            number = int(match.group("number"))
            record = source_record(source, title=title, url=url)
            record["paper_number"] = f"DP{number}"
            candidates.setdefault(number, record)
        return [candidates[number] for number in sorted(candidates, reverse=True)[:limit]]
    if source_id == "imf-working-papers":
        return parse_imf_list(html_text, source, limit)
    if source_id == "bis-working-papers":
        return parse_bis_list(html_text, source, limit)
    if source_id == "world-bank-prwp":
        return parse_world_bank_list(html_text, source, limit)
    if source_id == "cesifo-working-papers":
        return parse_repec_cesifo_list(html_text, source, limit)
    if source_id.startswith("repec-nep-"):
        return parse_nep_issue_list(html_text, source, limit)
    if "ideas.repec.org/s/" in str(source.get("homepage") or ""):
        return parse_repec_series_list(html_text, source, limit)
    return []


def nested_values(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for child in value.values():
            values.extend(nested_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(nested_values(child))
    return values


def first_key_text(item: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return clean_text(value)
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str):
                return clean_text(first)
            if isinstance(first, dict):
                for nested_key in ("value", "name", "title"):
                    if isinstance(first.get(nested_key), str):
                        return clean_text(first[nested_key])
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        for key in keys:
            variants = [key, key.replace("_", "."), f"dc.{key}", f"dc.{key}.none"]
            for variant in variants:
                raw = metadata.get(variant)
                if isinstance(raw, list) and raw:
                    first = raw[0]
                    if isinstance(first, dict) and first.get("value"):
                        return clean_text(str(first["value"]))
    return None


def metadata_values(item: dict[str, Any], keys: list[str]) -> list[str]:
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        return []
    values: list[str] = []
    for key in keys:
        variants = [key, key.replace("_", "."), f"dc.{key}", f"dc.{key}.none"]
        for variant in variants:
            raw = metadata.get(variant)
            if isinstance(raw, list):
                for entry in raw:
                    if isinstance(entry, dict) and entry.get("value"):
                        values.append(clean_text(str(entry["value"])))
                    elif isinstance(entry, str):
                        values.append(clean_text(entry))
    return [value for value in values if value]


def first_url_text(item: dict[str, Any], source: dict[str, Any]) -> str | None:
    for key in ("url", "href", "link", "path", "canonical_url"):
        value = item.get(key)
        if isinstance(value, str):
            absolute = normalize_url(urljoin(str(source.get("homepage") or ""), value))
            if allowed_url(source, absolute):
                return absolute
    links = item.get("_links") or item.get("links")
    if isinstance(links, dict):
        for child in links.values():
            if isinstance(child, dict) and isinstance(child.get("href"), str):
                absolute = normalize_url(child["href"])
                if allowed_url(source, absolute):
                    return absolute
            if isinstance(child, list):
                for entry in child:
                    if isinstance(entry, dict) and isinstance(entry.get("href"), str):
                        absolute = normalize_url(entry["href"])
                        if allowed_url(source, absolute):
                            return absolute
    return None


def parse_world_bank_json_records(payload: Any, source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for value in nested_values(payload):
        if isinstance(value, dict) and isinstance(value.get("metadata"), dict):
            candidates.append(value)
    for item in candidates:
        title = (metadata_values(item, ["dc.title", "title"]) or [None])[0]
        if not title or not plausible_title(title):
            continue
        uuid = item.get("uuid") or item.get("id")
        url = f"https://openknowledge.worldbank.org/entities/publication/{uuid}" if isinstance(uuid, str) else first_url_text(item, source)
        if not url or url in seen or not allowed_url(source, url):
            continue
        # World Bank discover metadata sometimes carries repeated or mismatched
        # abstract values across search objects. Keep the source reliable by
        # using title/date/DOI here; detail-level abstract validation can be
        # added later.
        abstract = None
        date_value = (metadata_values(item, ["dc.date.issued", "date.issued", "issued"]) or [None])[0]
        record = source_record(source, title=title, url=url, published=parse_date(date_value), abstract=abstract)
        authors = list(dict.fromkeys(metadata_values(item, ["dc.contributor.author", "contributor.author", "author"])))
        if authors:
            record["authors"] = authors[:12]
        doi = (metadata_values(item, ["dc.identifier.doi", "identifier.doi", "doi"]) or [None])[0]
        if doi:
            record["doi"] = doi
        uri = (metadata_values(item, ["dc.identifier.uri", "identifier.uri"]) or [None])[0]
        if uri and uri.startswith("http"):
            record["source_url"] = uri
        bitstreams = item.get("bundles")
        if isinstance(bitstreams, list):
            for bundle in bitstreams:
                if not isinstance(bundle, dict):
                    continue
                for candidate in nested_values(bundle):
                    if isinstance(candidate, dict) and isinstance(candidate.get("uuid"), str):
                        record["pdf_url"] = f"https://openknowledge.worldbank.org/bitstreams/{candidate['uuid']}/download"
                        break
                if record.get("pdf_url"):
                    break
        record["paper_number"] = str(uuid) if uuid else record.get("paper_number")
        seen.add(url)
        records.append(record)
        if len(records) >= limit:
            break
    return records


def parse_json_records(payload: Any, source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    if source.get("id") == "world-bank-prwp":
        return parse_world_bank_json_records(payload, source, limit)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in nested_values(payload):
        if not isinstance(value, dict):
            continue
        title = first_key_text(value, ["title", "name", "label", "dc.title"])
        if not title or not plausible_title(title):
            continue
        url = first_url_text(value, source)
        if not url:
            # DSpace item UUID pages can be reconstructed from uuid.
            uuid = value.get("uuid") or value.get("id")
            if isinstance(uuid, str) and source.get("id") == "world-bank-prwp":
                url = f"https://openknowledge.worldbank.org/entities/publication/{uuid}"
        if not url or url in seen or not allowed_url(source, url):
            continue
        seen.add(url)
        date_value = first_key_text(value, ["date", "issued", "dateIssued", "publication_date", "dc.date.issued"])
        abstract = first_key_text(value, ["abstract", "description", "dc.description.abstract"])
        records.append(source_record(source, title=title, url=url, published=parse_date(date_value), abstract=abstract))
        if len(records) >= limit:
            break
    return records


def nber_number_from_text(text: object) -> int | None:
    match = re.search(r"\bw(\d{4,})\b", str(text or ""), flags=re.I)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def latest_nber_seen_number() -> int:
    max_number = 0
    candidates: list[str] = []
    seen_path = DATA_DIR / "seen.json"
    if seen_path.exists():
        try:
            seen = json.loads(seen_path.read_text(encoding="utf-8"))
        except Exception:
            seen = {}
        if isinstance(seen, dict):
            candidates.extend(str(key) for key in seen.keys())
            candidates.extend(json.dumps(value, ensure_ascii=False) for value in seen.values())
        elif isinstance(seen, list):
            candidates.extend(json.dumps(value, ensure_ascii=False) for value in seen)
    for folder in [DATA_DIR / "daily", DATA_DIR / "raw" / "working_papers"]:
        if not folder.exists():
            continue
        for path in folder.glob("*.json"):
            try:
                candidates.append(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    for text in candidates:
        for match in re.finditer(r"\bw(\d{4,})\b", text, flags=re.I):
            try:
                max_number = max(max_number, int(match.group(1)))
            except ValueError:
                continue
    return max_number


def parse_nber_api_result(result: dict[str, Any], source: dict[str, Any]) -> dict[str, Any] | None:
    url = first_url_text(result, source) or first_key_text(result, ["url", "path"])
    number = nber_number_from_text(url) or nber_number_from_text(result.get("title"))
    if not number:
        return None
    url = f"https://www.nber.org/papers/w{number}"
    title = first_key_text(result, ["title", "name", "label", "dc.title"])
    if not title or not plausible_title(title):
        return None
    date_value = first_key_text(result, ["displaydate", "date", "issued", "dateIssued", "publication_date", "dc.date.issued"])
    abstract = first_key_text(result, ["abstract", "description", "dc.description.abstract"])
    record = source_record(source, title=title, url=url, published=parse_date(date_value), abstract=abstract)
    record["paper_number"] = f"w{number}"
    record["doi"] = f"10.3386/w{number}"
    if authors := result.get("authors"):
        if isinstance(authors, list):
            record["authors"] = [clean_text(author) for author in authors if clean_text(author)][:12]
        elif isinstance(authors, str):
            record["authors"] = [item.strip() for item in clean_text(authors).split(",") if item.strip()][:12]
    return record


def fetch_nber_search_result(number: int, source: dict[str, Any], *, timeout: int) -> dict[str, Any] | None:
    query = f"page=1&perPage=20&q=w{number}"
    url = f"https://www.nber.org/api/v1/working_page_listing/contentType/working_paper/_/_/search?{query}"
    payload = fetch_json(url, timeout=timeout)
    for value in nested_values(payload):
        if not isinstance(value, dict):
            continue
        result_url = first_url_text(value, source) or first_key_text(value, ["url", "path"])
        if nber_number_from_text(result_url) == number:
            return value
    return None


def fetch_nber_direct_record(number: int, source: dict[str, Any], *, timeout: int) -> dict[str, Any] | None:
    url = f"https://www.nber.org/papers/w{number}"
    try:
        html_text = fetch_text(url, timeout=timeout)
    except Exception:
        return None
    title = (
        first_match([r'<h1[^>]*>(.*?)</h1>'], html_text)
        or (meta_values(html_text, ["citation_title", "dc.title", "og:title"]) or [None])[0]
        or first_match([r"<title>(.*?)</title>"], html_text)
    )
    title = clean_text(str(title or "").replace("| NBER", ""))
    if len(title) < 8 or title.casefold() in {"working paper", "nber working paper"}:
        return None
    record = source_record(source, title=title, url=url)
    record["paper_number"] = f"w{number}"
    record["doi"] = f"10.3386/w{number}"
    return record


def fetch_nber_number_scan(source: dict[str, Any], *, timeout: int, limit: int) -> list[dict[str, Any]]:
    previous = latest_nber_seen_number()
    if previous <= 1:
        return []
    found_by_number: dict[int, dict[str, Any]] = {}
    api_misses: list[int] = []
    consecutive_misses = 0
    max_scan = max(120, limit * 6)
    stop_after_misses = 30
    for number in range(previous + 1, previous + max_scan + 1):
        try:
            result = fetch_nber_search_result(number, source, timeout=timeout)
        except Exception:
            result = None
        if result:
            record = parse_nber_api_result(result, source)
            if record:
                found_by_number[number] = record
                consecutive_misses = 0
                continue
        api_misses.append(number)
        consecutive_misses += 1
        if consecutive_misses >= stop_after_misses:
            break
    if found_by_number:
        max_found = max(found_by_number)
        # The search API can skip valid papers inside the weekly range. Probe only
        # internal gaps by direct paper URL so the post-range 404 tail stays fast.
        for number in [item for item in api_misses if previous < item < max_found]:
            record = fetch_nber_direct_record(number, source, timeout=timeout)
            if record:
                found_by_number[number] = record
    found = list(found_by_number.values())
    found.sort(key=lambda item: nber_number_from_text(item.get("paper_number") or item.get("url")) or 0, reverse=True)
    return found


def specialized_api_urls(source: dict[str, Any]) -> list[str]:
    source_id = str(source.get("id") or "")
    if source_id == "nber":
        return [
            "https://www.nber.org/api/v1/working_page_listing/contentType/working_paper/_/_/search?page=1&perPage=50",
            "https://www.nber.org/api/v1/working_paper/working_paper_listing/_/_/search?page=1&perPage=50",
        ]
    if source_id == "world-bank-prwp":
        collection_id = str(source.get("homepage") or "").rstrip("/").split("/")[-1]
        return [
            "https://openknowledge.worldbank.org/server/api/discover/search/objects?query=%22Policy%20Research%20Working%20Paper%22&size=50&sort=dc.date.issued,DESC",
            f"https://openknowledge.worldbank.org/server/api/discover/search/objects?scope={collection_id}&size=50&sort=dc.date.issued,DESC",
            f"https://openknowledge.worldbank.org/server/api/core/collections/{collection_id}/items?size=50",
        ]
    return []


def fetch_specialized_api(source: dict[str, Any], *, timeout: int, limit: int) -> tuple[list[dict[str, Any]], str] | None:
    if str(source.get("id") or "") == "nber":
        scanned = fetch_nber_number_scan(source, timeout=timeout, limit=limit)
        if scanned:
            return scanned, "nber-number-scan"
    for url in specialized_api_urls(source):
        try:
            payload = fetch_json(url, timeout=timeout)
            records = parse_json_records(payload, source, limit)
            if records:
                return records, "specialized-api"
        except Exception:
            continue
    return None


def plausible_title(title: str) -> bool:
    bad_fragments = [
        "subscribe",
        "sign in",
        "login",
        "privacy",
        "cookie",
        "download",
        "publications",
        "working papers",
        "discussion papers",
    ]
    if len(title) < 20 or len(title.split()) < 4:
        return False
    lowered = title.lower()
    return not any(fragment in lowered for fragment in bad_fragments)


CEPR_CURRENT_LISTING_FLOOR = 20000
CEPR_OFFICIAL_LISTING_URLS = (
    "https://cepr.org/publications/discussion-papers/search-discussion-papers",
    "https://cepr.org/publications",
    "https://cepr.org/publications/discussion-papers",
    "https://cepr.org/publications/publication-series/discussion-papers",
)


def cepr_number(record: dict[str, Any]) -> int | None:
    text = " ".join(
        str(record.get(key) or "")
        for key in ("paper_number", "url", "title")
    )
    match = re.search(r"\bDP\s*(\d{4,})\b|/dp(\d+)(?:\D|$)", text, flags=re.I)
    if not match:
        return None
    value = match.group(1) or match.group(2)
    return int(value) if value else None


def is_current_cepr_listing_record(record: dict[str, Any]) -> bool:
    """Return whether a CEPR row belongs to the current DP-number series.

    This is deliberately a current-listing admission rule, not a global
    historical-record predicate. Older CEPR papers may still be valid durable
    history or genuine first discoveries from other accepted paths.
    """
    number = cepr_number(record)
    return number is not None and number >= CEPR_CURRENT_LISTING_FLOOR


def fetch_cepr_current_listing(
    source: dict[str, Any],
    *,
    timeout: int,
    limit: int,
) -> tuple[list[dict[str, Any]], str]:
    """Fetch CEPR current DPs from first-party pages and fail closed on stale surfaces."""
    urls = []
    for url in (str(source.get("homepage") or ""), *CEPR_OFFICIAL_LISTING_URLS):
        if url and url not in urls:
            urls.append(url)

    diagnostics: list[str] = []
    for url in urls:
        try:
            html_text = fetch_text(url, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - try the next official CEPR surface.
            diagnostics.append(f"{url}: {type(exc).__name__}")
            continue

        records = parse_specialized_html(html_text, source, limit)
        records = [record for record in records if not is_historical_cepr_record(record)]
        numbers = [number for record in records if (number := cepr_number(record)) is not None]
        if not numbers:
            diagnostics.append(f"{url}: no-current-dp")
            continue
        if max(numbers) < CEPR_CURRENT_LISTING_FLOOR:
            diagnostics.append(f"{url}: stale-max-DP{max(numbers)}")
            continue
        records = [record for record in records if is_current_cepr_listing_record(record)]
        if not records:
            diagnostics.append(f"{url}: no-current-series-dp")
            continue

        label = (
            "search"
            if "search-discussion-papers" in url
            else "publications"
            if url.rstrip("/") == "https://cepr.org/publications"
            else "discussion-papers"
            if url.rstrip("/") == "https://cepr.org/publications/discussion-papers"
            else "publication-series"
        )
        return records[:limit], f"cepr-official-html:{label}"

    detail = "; ".join(diagnostics[-4:]) or "no official listing surface returned current DPs"
    raise RuntimeError(f"CEPR current listing unavailable: {detail}")


def plausible_nep_title(title: str) -> bool:
    title = clean_text(title)
    if len(title) < 6:
        return False
    lowered = title.lower()
    bad_fragments = (
        "subscribe",
        "sign in",
        "login",
        "privacy",
        "cookie",
        "download",
    )
    return not any(fragment in lowered for fragment in bad_fragments)


def is_historical_cepr_record(record: dict[str, Any]) -> bool:
    if str(record.get("source_id") or "") != "cepr-dp":
        return False
    match = re.search(r"/dp(\d+)(?:\D|$)", str(record.get("url") or ""), flags=re.I)
    return bool(match and (int(match.group(1)) < 10000 or 13700 <= int(match.group(1)) <= 13799))


def nep_field(html_text: str, label: str) -> str | None:
    pattern = (
        rf'<td[^>]*class=["\']fina["\'][^>]*>\s*{re.escape(label)}:\s*</td>\s*'
        r'<td[^>]*class=["\']fiva["\'][^>]*>(?P<value>.*?)</td>'
    )
    match = re.search(pattern, html_text, flags=re.I | re.S)
    return clean_text(match.group("value")) if match else None


def nep_authors(html_text: str) -> list[str]:
    by_value = nep_field(html_text, "By")
    if not by_value:
        return []
    return [clean_text(part) for part in re.split(r"\s*;\s*", by_value) if clean_text(part)][:12]


def looks_like_abstract(value: str | None) -> bool:
    text = clean_text(value)
    if not text:
        return False
    lowered = text.casefold()
    abstract_starts = (
        "this paper ",
        "this study ",
        "we analyze ",
        "we analyse ",
        "we examine ",
        "we investigate ",
        "we find ",
        "using data ",
        "based on ",
    )
    return len(text) > 260 or any(lowered.startswith(prefix) for prefix in abstract_starts)


def allowed_url(source: dict[str, Any], url: str | None) -> bool:
    if not url:
        return not source.get("url_pattern") and not source.get("url_contains")
    homepage = str(source.get("homepage") or "").rstrip("/")
    if homepage and url.rstrip("/") == homepage:
        return False
    fragments = source.get("url_contains") or []
    if fragments:
        return any(str(fragment).lower() in url.lower() for fragment in fragments)
    pattern = source.get("url_pattern")
    if not pattern:
        return True
    return re.search(str(pattern), url, flags=re.I) is not None


def fetch_source(source: dict[str, Any], *, timeout: int, limit: int) -> tuple[list[dict[str, Any]], str]:
    source_id = str(source.get("id") or "")
    if source_id == "cepr-dp":
        return fetch_cepr_current_listing(source, timeout=timeout, limit=limit)

    api_result = fetch_specialized_api(source, timeout=timeout, limit=limit)
    if api_result:
        return api_result
    if source.get("feed"):
        xml_text = fetch_text(str(source["feed"]), timeout=timeout)
        records = parse_feed(xml_text, source)
        return records[:limit], "feed"
    html_text = fetch_text(str(source["homepage"]), timeout=timeout)
    if source_id.startswith("repec-nep-"):
        issue_match = re.search(r'href=["\'](?P<href>[^"\']*/' + re.escape(source_id.removeprefix("repec-")) + r'/20\d{2}-\d{2}-\d{2}[^"\']*)["\']', html_text, flags=re.I)
        if issue_match:
            issue_url = normalize_url(urljoin(str(source.get("homepage")), html.unescape(issue_match.group("href"))))
            issue_date = parse_date(issue_url)
            issue_html = fetch_text(str(issue_url), timeout=timeout)
            records = parse_nep_issue_list(issue_html, source, limit, issue_date=issue_date, issue_url=issue_url)
            if records:
                return records[:limit], "nep-issue"
    specialized = parse_specialized_html(html_text, source, limit)
    if specialized:
        return specialized[:limit], "specialized-html"
    discovered_feed = discover_feed(html_text, str(source.get("homepage") or ""))
    if discovered_feed:
        try:
            xml_text = fetch_text(discovered_feed, timeout=timeout)
            records = parse_feed(xml_text, {**source, "feed": discovered_feed})
            return records[:limit], "discovered-feed"
        except Exception:
            pass
    records = parse_html_list(html_text, source, limit)
    return records, "html"


def discover_feed(html_text: str, base_url: str) -> str | None:
    for match in re.finditer(r'<link[^>]+rel=["\'][^"\']*alternate[^"\']*["\'][^>]*>', html_text, flags=re.I):
        tag = match.group(0)
        type_match = re.search(r'type=["\']([^"\']+)["\']', tag, flags=re.I)
        if type_match and "rss" not in type_match.group(1).lower() and "atom" not in type_match.group(1).lower():
            continue
        href_match = re.search(r'href=["\']([^"\']+)["\']', tag, flags=re.I)
        if href_match:
            return urljoin(base_url, html.unescape(href_match.group(1)))
    for href in re.findall(r'href=["\']([^"\']*(?:rss|feed|atom)[^"\']*)["\']', html_text, flags=re.I):
        if href.lower().startswith("javascript:"):
            continue
        return urljoin(base_url, html.unescape(href))
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, default=DATA_DIR / "working_paper_sources.yml")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--stage", type=int, default=2, help="Fetch sources with stage <= this value.")
    parser.add_argument("--limit-per-source", type=int, default=20)
    parser.add_argument("--detail-limit", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--only", action="append", default=[], help="Fetch only the given source id. Repeatable.")
    args = parser.parse_args()

    output = args.output or DATA_DIR / "raw" / "working_papers" / f"{today_str()}.json"
    sources = [source for source in load_sources(args.sources) if int(source.get("stage") or 99) <= args.stage]
    if args.only:
        selected = set(args.only)
        sources = [source for source in sources if str(source.get("id") or "") in selected]
    all_records: list[dict[str, Any]] = []
    messages: list[str] = []
    failures = 0
    for source in sources:
        source_id = str(source.get("id") or "unknown")
        try:
            records, method = fetch_source(source, timeout=args.timeout, limit=args.limit_per_source)
            if args.detail_limit:
                enriched: list[dict[str, Any]] = []
                for index, record in enumerate(records):
                    if index < args.detail_limit or source_id == "nber":
                        record = enrich_record_from_detail(record, source, timeout=args.timeout)
                    enriched.append(record)
                records = enriched
            records = [record for record in records if not is_historical_cepr_record(record)]
            all_records.extend(records)
            messages.append(f"{source_id}: {len(records)} via {method}")
            record_source(f"working-paper:{source_id}", ok=True, count=len(records), message=method)
        except Exception as exc:  # noqa: BLE001 - source failures should not block the monitor.
            failures += 1
            message = f"{type(exc).__name__}: {exc}"
            messages.append(f"{source_id}: {message}")
            record_source(f"working-paper:{source_id}", ok=False, count=0, message=message)

    write_json(output, all_records)
    summary = "; ".join(messages)
    if failures:
        summary = f"partial_success failures={failures}; {summary}"
    record_source(
        "working-papers",
        # A non-empty aggregate is not proof that every configured source ran.
        # Keep the records we did obtain, but expose partial failures to the
        # operational status layer so they cannot be mistaken for completeness.
        ok=failures == 0,
        count=len(all_records),
        message=summary,
    )
    print(f"wrote {len(all_records)} working-paper records to {output}; failures={failures}")
    for message in messages:
        print(message)


if __name__ == "__main__":
    main()
