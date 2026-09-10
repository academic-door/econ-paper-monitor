"""Enrich daily records from publisher article pages.

This step is intentionally best-effort. It should improve metadata when
publisher pages are accessible, but never block the monitor when a site uses
Cloudflare, CAPTCHA, or institutional access controls.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Any

from common import (
    BEIJING_TZ,
    DATA_DIR,
    clean_abstract_text,
    date_from_parts,
    fetch_json,
    fetch_json_retry,
    fetch_text,
    normalize_doi,
    read_json,
    today_str,
    write_json,
)
from public_integrity import strong_identity_keys
from status import load_status, now, record_source, save_status


MONTHS = {
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

DATE_CAPTURE = (
    r"[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d{2}"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2}"
    r"|20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?"
)

SS_MIN_INTERVAL_SECONDS = 0.25
SS_PROVIDER_KEY_LIMIT_RPS = 1.0
# Live calibration on 2026-09-10 remained heavily rate-limited at both
# 0.4 RPS and 0.2 RPS.  Under active shared-key pressure, 0.2 RPS is a
# conservative probe rate rather than a claim that this rate is currently
# sustainable; the fast circuit below stops optional S2 work quickly when
# the shared bucket remains backpressured.
SS_CLIENT_TARGET_RPS = 0.2
SS_KEY_MIN_INTERVAL_SECONDS = 1.0 / SS_CLIENT_TARGET_RPS
SS_RATE_LIMIT_SKIP_AFTER = 20  # backward-compatible absolute pressure telemetry
SS_RATE_LIMIT_WINDOW = 20
SS_RATE_LIMIT_MIN_SAMPLES = 10
SS_RATE_LIMIT_OPEN_RATIO = 0.30
SS_FAST_TRIP_CONSECUTIVE_RATE_LIMITS = 2
SS_FAST_TRIP_TERMINAL_RATE_LIMIT_BUDGET = 4
# Under the current keyed shared-bucket pressure, hidden retries amplify 429s
# before the circuit can protect later DOI calls. Keep unkeyed behavior
# backward-compatible, but make a keyed 429 terminal for this optional source.
SS_KEY_MAX_RETRIES = 0
SS_MAX_RETRY_BACKOFF_SECONDS = 30.0
# Start pacing and circuit-state mutation use different locks: the start gate
# may wait, but provider responses must still be able to update circuit state.
_SS_GATE_LOCK = threading.Lock()
_SS_LOCK = threading.Lock()
_SS_LAST_REQUEST = 0.0
_SS_RATE_LIMITED_COUNT = 0
_SS_CONSECUTIVE_TERMINAL_RATE_LIMITS = 0
_SS_RECENT_OUTCOMES: deque[str] = deque(maxlen=SS_RATE_LIMIT_WINDOW)
_SS_LAST_RETRY_AFTER_SECONDS = 0.0


def clean_text(value: str) -> str:
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html_lib.unescape(value)).strip()


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(20\d{2})", text)
    if match:
        month = MONTHS.get(match.group(1).casefold())
        if month:
            return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(2)):02d}"
    match = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})", text)
    if match:
        month = MONTHS.get(match.group(2).casefold())
        if month:
            return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(1)):02d}"
    return None


def crossref_created_date(item: dict[str, Any], *, utc: bool = False) -> str | None:
    created = item.get("created")
    if not isinstance(created, dict):
        return None
    date_time = created.get("date-time")
    if isinstance(date_time, str) and date_time:
        try:
            parsed = datetime.fromisoformat(date_time.replace("Z", "+00:00"))
            target_tz = timezone.utc if utc else BEIJING_TZ
            return parsed.astimezone(target_tz).date().isoformat()
        except ValueError:
            pass
    return date_from_parts(created)


def parse_meta(html: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for tag in re.findall(r"<meta\b[^>]*>", html, flags=re.I):
        key_match = re.search(r"(?:name|property)=['\"]([^'\"]+)['\"]", tag, flags=re.I)
        content_match = re.search(r"content=['\"]([^'\"]*)['\"]", tag, flags=re.I)
        if key_match and content_match:
            meta[key_match.group(1).casefold()] = content_match.group(1).strip()
    return meta


def meta_values(html: str, names: tuple[str, ...]) -> list[str]:
    wanted = {name.casefold() for name in names}
    values: list[str] = []
    for tag in re.findall(r"<meta\b[^>]*>", html, flags=re.I):
        key_match = re.search(r"(?:name|property)=['\"]([^'\"]+)['\"]", tag, flags=re.I)
        content_match = re.search(r"content=['\"]([^'\"]*)['\"]", tag, flags=re.I)
        if not key_match or not content_match or key_match.group(1).casefold() not in wanted:
            continue
        value = clean_text(content_match.group(1))
        if value and value not in values:
            values.append(value)
    return values


def fetch_text_and_url(url: str, timeout: int) -> tuple[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        final_url = response.geturl()
    for encoding in (charset, "utf-8", "gb18030"):
        try:
            return payload.decode(encoding), final_url
        except Exception:
            continue
    return payload.decode("utf-8", errors="replace"), final_url


def extract_elsevier_pii(*values: str | None) -> str | None:
    haystack = " ".join(value or "" for value in values)
    haystack = urllib.parse.unquote(html_lib.unescape(haystack))
    match = re.search(r"\b(S\d{14,18}[0-9X])\b", haystack, flags=re.I)
    return match.group(1).upper() if match else None


def extract_page_metadata(html: str) -> dict[str, Any]:
    meta = parse_meta(html)
    text = clean_text(html)
    result: dict[str, Any] = {}
    authors = meta_values(html, ("citation_author", "dc.creator", "dc.contributor.author"))
    if authors:
        result["authors"] = authors[:12]

    meta_date_fields = (
        ("available_online", "citation_online_date"),
        ("published_online", "article:published_time"),
        ("published_online", "dc.date"),
        ("published_online", "dc.date.issued"),
        ("published_online", "dc.date.available"),
        ("published_online", "prism.publicationdate"),
        ("published_online", "citation_publication_date"),
        ("accepted_date", "citation_acceptance_date"),
        ("accepted_date", "citation_accepted_date"),
        ("accepted_date", "dc.date.accepted"),
    )
    for field, key in meta_date_fields:
        parsed = parse_date(meta.get(key))
        if parsed:
            result.setdefault(field, parsed)
            if field in {"available_online", "published_online"}:
                result.setdefault("available_online", parsed)
                result.setdefault("published_online", parsed)
            result.setdefault("date_source", f"publisher_meta:{key}")
            result.setdefault("date_confidence", "A")

    patterns = [
        ("accepted_date", rf"(?:Accepted|Accepted on|Date accepted|录用日期|接受日期)\s*[:：]?\s*({DATE_CAPTURE})"),
        ("available_online", rf"(?:Available online|Online available|Article available online|上线日期|网络首发)\s*[:：]?\s*({DATE_CAPTURE})"),
        ("published_online", rf"(?:First published|Published online|Published Online|Publication date|Published|发布日期|出版日期)\s*[:：]?\s*({DATE_CAPTURE})"),
    ]
    for field, pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        parsed = parse_date(match.group(1)) if match else None
        if parsed:
            result[field] = parsed
            if field in {"available_online", "published_online"}:
                result["available_online"] = parsed
                result["published_online"] = parsed
            result["date_source"] = f"publisher_{field}"
            result["date_confidence"] = "A"
    citation_abstract = clean_abstract_text(meta.get("citation_abstract"))
    if len(citation_abstract) > 80:
        result.setdefault("abstract", citation_abstract)
        result.setdefault("abstract_source", "publisher_meta:citation_abstract")
    if "abstract" not in result:
        for key in ("dc.description", "description", "og:description"):
            abstract = clean_abstract_text(meta.get(key))
            visibly_truncated = bool(re.search(r"(?:\.{3,}|…+)\s*$", abstract))
            if len(abstract) > 80 and not visibly_truncated:
                result.setdefault("abstract", abstract)
                result.setdefault("abstract_source", f"publisher_meta:{key}")
                break
    if "abstract" not in result:
        abstract_patterns = (
            r'<div\b[^>]*class=["\'][^"\']*\babstract\b[^"\']*["\'][^>]*>[\s\S]*?<h[1-6]\b[^>]*>\s*Abstract\s*</h[1-6]>([\s\S]*?)</div>\s*</div>',
            r'<section\b[^>]*class=["\'][^"\']*\babstract\b[^"\']*["\'][^>]*>[\s\S]*?<h[1-6]\b[^>]*>\s*Abstract\s*</h[1-6]>([\s\S]*?)</section>',
        )
        for pattern in abstract_patterns:
            match = re.search(pattern, html, flags=re.I)
            abstract = clean_abstract_text(match.group(1)) if match else ""
            if len(abstract) > 80:
                result["abstract"] = abstract
                result["abstract_source"] = "publisher_body:abstract"
                break
    return result


def extract_markdown_abstract(markdown: str) -> str | None:
    match = re.search(r"(?ims)^##\s+Abstract\s*$\s*(.*?)(?=^##\s+|\Z)", markdown)
    if not match:
        return None
    abstract = match.group(1)
    abstract = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", abstract)
    abstract = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", abstract)
    abstract = re.sub(r"[*_`#>]", " ", abstract)
    abstract = clean_abstract_text(abstract)
    return abstract if len(abstract) > 80 else None


def fetch_elsevier_json(url: str, timeout: int, api_key: str = "", insttoken: str = "") -> dict[str, Any]:
    headers = {
        "User-Agent": "econ-paper-monitor/1.0 (https://github.com/academic-door/econ-paper-monitor)",
        "Accept": "application/json",
    }
    if api_key:
        headers["X-ELS-APIKey"] = api_key
    if insttoken:
        headers["X-ELS-Insttoken"] = insttoken
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def nested_text(value: Any) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return clean_text(" ".join(nested_text(item) for item in value))
    if isinstance(value, dict):
        preferred = ("ce:para", "para", "$", "_", "content")
        parts = [nested_text(value[key]) for key in preferred if key in value]
        if not any(parts):
            parts = [nested_text(item) for key, item in value.items() if not str(key).startswith("@")]
        return clean_text(" ".join(part for part in parts if part))
    return ""


def extract_elsevier_api_abstract(response: dict[str, Any], core: dict[str, Any]) -> str | None:
    candidates: list[Any] = [core.get("dc:description")]
    item = response.get("item")
    if isinstance(item, dict):
        bibrecord = item.get("bibrecord")
        if isinstance(bibrecord, dict):
            head = bibrecord.get("head")
            if isinstance(head, dict):
                abstracts = head.get("abstracts")
                if isinstance(abstracts, dict):
                    candidates.append(abstracts.get("abstract"))
                elif abstracts:
                    candidates.append(abstracts)
    original_text = response.get("originalText")
    if isinstance(original_text, str):
        match = re.search(r"<ce:abstract\b[^>]*>([\s\S]*?)</ce:abstract>", original_text, flags=re.I)
        if match:
            candidates.append(match.group(1))
    for candidate in candidates:
        abstract = clean_abstract_text(nested_text(candidate))
        if len(abstract) > 80:
            return abstract
    return None


def elsevier_api_online_date(core: dict[str, Any]) -> dict[str, str]:
    """Return the publisher online date from Elsevier coredata when exposed."""
    result: dict[str, str] = {}
    display_date = str(core.get("prism:coverDisplayDate") or "")
    cover_date = str(core.get("prism:coverDate") or "")
    display_parsed = parse_date(display_date)
    cover_parsed = parse_date(cover_date)
    if display_parsed and "available online" in display_date.casefold():
        result["available_online"] = display_parsed
        result["published_online"] = display_parsed
        result["date_source"] = "elsevier_article_api"
        result["date_confidence"] = "A"
    elif display_parsed and (not cover_parsed or display_parsed != cover_parsed):
        # A full display date without the phrase is a provisional publisher
        # date. coverDate is usually the issue date and must not be surfaced.
        result["available_online"] = display_parsed
        result["published_online"] = display_parsed
        result["date_source"] = "elsevier_article_api_display_date"
        result["date_confidence"] = "C"
    return result


def elsevier_api_metadata(doi: str, timeout: int) -> dict[str, str]:
    encoded_doi = urllib.parse.quote(doi, safe="")
    api_key = (os.environ.get("ELSEVIER_API_KEY") or os.environ.get("ELS_API_KEY") or "").strip()
    insttoken = (os.environ.get("ELSEVIER_INSTTOKEN") or "").strip()
    query = {"httpAccept": "application/json"}
    if api_key:
        query["view"] = "FULL"
    url = f"https://api.elsevier.com/content/article/doi/{encoded_doi}?{urllib.parse.urlencode(query)}"
    try:
        payload = fetch_elsevier_json(url, timeout=timeout, api_key=api_key, insttoken=insttoken)
    except Exception:
        return {}
    response = payload.get("full-text-retrieval-response") if isinstance(payload, dict) else None
    core = response.get("coredata") if isinstance(response, dict) else None
    if not isinstance(core, dict):
        return {}
    result: dict[str, str] = {}
    pii = extract_elsevier_pii(str(core.get("prism:url") or ""), str(core.get("pii") or ""))
    if pii:
        result["pii"] = pii
    result.update(elsevier_api_online_date(core))
    abstract = extract_elsevier_api_abstract(response, core)
    if abstract:
        result["abstract"] = abstract
        result["abstract_source"] = "elsevier_article_api_full" if api_key else "elsevier_article_api"
    return result


def publisher_proxy_metadata(url: str, timeout: int) -> dict[str, str]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.casefold()
    allowed_hosts = {
        "www.sciencedirect.com",
        "sciencedirect.com",
        "onlinelibrary.wiley.com",
        "www.tandfonline.com",
        "tandfonline.com",
        "academic.oup.com",
        "link.springer.com",
    }
    if host not in allowed_hosts:
        return {}
    target = f"http://{host}{parsed.path}"
    if parsed.query:
        target += f"?{parsed.query}"
    jina_key = os.environ.get("JINA_API_KEY") or ""
    proxy_headers = {"Authorization": f"Bearer {jina_key}"} if jina_key else None
    markdown = ""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            markdown = fetch_text(f"https://r.jina.ai/{target}", timeout=timeout, headers=proxy_headers)
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in {429, 500, 502, 503, 504} and attempt == 0:
                time.sleep(2)
                continue
            if exc.code in {429, 500, 502, 503, 504}:
                return {"_status": "proxy-rate-limited", "_status_code": exc.code}
            return {"_status": "proxy-request-failed", "_status_code": exc.code}
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == 0:
                time.sleep(2)
                continue
    if not markdown and last_error is not None:
        return {"_status": "proxy-request-failed", "_error": f"{type(last_error).__name__}: {last_error}"}
    lowered = markdown.casefold()
    if "are you a robot" in lowered or "requiring captcha" in lowered or "captcha challenge" in lowered:
        return {"_status": "blocked-captcha"}
    abstract = extract_markdown_abstract(markdown)
    if not abstract:
        return {"_status": "abstract-not-exposed"}
    return {"abstract": abstract, "abstract_source": "publisher_page_via_readonly_proxy"}


def crossref_doi_metadata(doi: str, timeout: int) -> dict[str, Any]:
    try:
        payload = fetch_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}", timeout=timeout)
        item = payload.get("message") or {}
    except Exception:
        return {}
    published_online = date_from_parts(item.get("published-online"))
    published = date_from_parts(item.get("published"))
    published_print = date_from_parts(item.get("published-print"))
    issued = date_from_parts(item.get("issued"))
    created = crossref_created_date(item)
    created_online_utc = crossref_created_date(item, utc=True)
    issue_date = published_print or published or issued
    result: dict[str, Any] = {}
    authors = []
    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue
        name = clean_text(" ".join(str(author.get(key) or "") for key in ("given", "family")))
        if name and name not in authors:
            authors.append(name)
    if authors:
        result["authors"] = authors[:12]
    if published_online:
        result["available_online"] = published_online
        result["published_online"] = published_online
        result["date_source"] = "crossref_doi_published_online"
        result["date_confidence"] = "C"
    elif doi.startswith("10.1016/") and created_online_utc:
        result["available_online"] = created_online_utc
        result["published_online"] = created_online_utc
        if issue_date:
            result["issue_date"] = issue_date
        result["date_source"] = "crossref_doi_elsevier_created_online"
        result["date_confidence"] = "C"
    elif published:
        result["issue_date"] = published
        result["date_source"] = "crossref_doi_published"
        result["date_confidence"] = "C"
    elif published_print or issued:
        result["issue_date"] = published_print or issued or ""
        result["date_source"] = "crossref_doi_issue"
        result["date_confidence"] = "D"
    elif created:
        result["issue_date"] = created
        result["date_source"] = "crossref_doi_created"
        result["date_confidence"] = "D"
    abstract = clean_abstract_text(item.get("abstract"))
    if len(abstract) > 80:
        result["abstract"] = abstract
        result["abstract_source"] = "crossref_doi"
    return {key: value for key, value in result.items() if value}


def openalex_abstract(index: Any) -> str | None:
    if not isinstance(index, dict):
        return None
    positions: list[tuple[int, str]] = []
    for word, indexes in index.items():
        if not isinstance(indexes, list):
            continue
        for pos in indexes:
            try:
                positions.append((int(pos), str(word)))
            except Exception:
                continue
    if not positions:
        return None
    return " ".join(word for _, word in sorted(positions))


def openalex_doi_metadata(doi: str, timeout: int) -> dict[str, Any]:
    try:
        payload = fetch_json(f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi)}", timeout=timeout)
    except Exception:
        return {}
    published = payload.get("publication_date")
    result: dict[str, Any] = {}
    authors = []
    for authorship in payload.get("authorships") or []:
        author = authorship.get("author") if isinstance(authorship, dict) else None
        name = clean_text(str(author.get("display_name") or "")) if isinstance(author, dict) else ""
        if name and name not in authors:
            authors.append(name)
    if authors:
        result["authors"] = authors[:12]
    parsed = parse_date(str(published)) if published else None
    if parsed:
        result["available_online"] = parsed
        result["published_online"] = parsed
        result["date_source"] = "openalex_publication_date"
        result["date_confidence"] = "C"
    abstract = openalex_abstract(payload.get("abstract_inverted_index"))
    abstract = clean_abstract_text(abstract)
    if len(abstract) > 80:
        result["abstract"] = abstract
        result["abstract_source"] = "openalex"
    return result


def reset_semantic_scholar_throttle() -> None:
    """Reset process-local Semantic Scholar pacing/circuit state."""
    global _SS_LAST_REQUEST, _SS_RATE_LIMITED_COUNT, _SS_CONSECUTIVE_TERMINAL_RATE_LIMITS, _SS_LAST_RETRY_AFTER_SECONDS
    with _SS_LOCK:
        _SS_LAST_REQUEST = 0.0
        _SS_RATE_LIMITED_COUNT = 0
        _SS_CONSECUTIVE_TERMINAL_RATE_LIMITS = 0
        _SS_RECENT_OUTCOMES.clear()
        _SS_LAST_RETRY_AFTER_SECONDS = 0.0


def _semantic_scholar_api_key() -> str:
    """Return the configured Semantic Scholar API key, if any (never logged)."""
    return (
        os.environ.get("S2_API_KEY")
        or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
        or ""
    ).strip()


def _semantic_scholar_circuit_open_locked() -> bool:
    if _SS_CONSECUTIVE_TERMINAL_RATE_LIMITS >= SS_FAST_TRIP_CONSECUTIVE_RATE_LIMITS:
        return True
    if _SS_RATE_LIMITED_COUNT >= SS_FAST_TRIP_TERMINAL_RATE_LIMIT_BUDGET:
        return True
    if _SS_RATE_LIMITED_COUNT >= SS_RATE_LIMIT_SKIP_AFTER:
        return True
    samples = len(_SS_RECENT_OUTCOMES)
    if samples < SS_RATE_LIMIT_MIN_SAMPLES:
        return False
    limited = sum(1 for outcome in _SS_RECENT_OUTCOMES if outcome == "rate_limited")
    return (limited / samples) >= SS_RATE_LIMIT_OPEN_RATIO


def semantic_scholar_throttle_state() -> dict[str, Any]:
    """Return non-secret owner-local control telemetry for the shared S2 key."""
    with _SS_LOCK:
        key_configured = bool(_semantic_scholar_api_key())
        samples = len(_SS_RECENT_OUTCOMES)
        recent_limited = sum(
            1 for outcome in _SS_RECENT_OUTCOMES if outcome == "rate_limited"
        )
        return {
            "provider": "semantic-scholar",
            "endpoint_class": "academic_graph_paper_details",
            "workload_class": "metadata_recovery",
            "key_configured": key_configured,
            "provider_rate_limit_rps": SS_PROVIDER_KEY_LIMIT_RPS if key_configured else None,
            "client_target_rps": SS_CLIENT_TARGET_RPS if key_configured else None,
            "min_interval_seconds": (
                SS_KEY_MIN_INTERVAL_SECONDS if key_configured else SS_MIN_INTERVAL_SECONDS
            ),
            "configured_concurrency": "shared_global_start_gate",
            "rate_limited_count": _SS_RATE_LIMITED_COUNT,
            "consecutive_terminal_rate_limited": _SS_CONSECUTIVE_TERMINAL_RATE_LIMITS,
            "fast_trip_after_consecutive_rate_limits": SS_FAST_TRIP_CONSECUTIVE_RATE_LIMITS,
            "fast_trip_terminal_rate_limit_budget": SS_FAST_TRIP_TERMINAL_RATE_LIMIT_BUDGET,
            "keyed_max_retries": SS_KEY_MAX_RETRIES if key_configured else None,
            "skip_after": SS_RATE_LIMIT_SKIP_AFTER,
            "recent_window": SS_RATE_LIMIT_WINDOW,
            "recent_attempts": samples,
            "recent_rate_limited": recent_limited,
            "recent_rate_limited_ratio": round(recent_limited / samples, 4) if samples else 0.0,
            "circuit_open": _semantic_scholar_circuit_open_locked(),
            "last_retry_after_seconds": _SS_LAST_RETRY_AFTER_SECONDS or None,
        }


def _semantic_scholar_gate(*, allow_when_tripped: bool = False) -> bool:
    """Serialize starts without blocking provider outcomes from updating circuit state."""
    global _SS_LAST_REQUEST
    with _SS_GATE_LOCK:
        with _SS_LOCK:
            if not allow_when_tripped and _semantic_scholar_circuit_open_locked():
                return False
            interval = (
                SS_KEY_MIN_INTERVAL_SECONDS if _semantic_scholar_api_key() else SS_MIN_INTERVAL_SECONDS
            )
            last_request = _SS_LAST_REQUEST
        now = time.monotonic()
        wait = interval - (now - last_request)
        if wait > 0:
            time.sleep(wait)
        # A response from the prior start can arrive while this worker waits.
        # Recheck after the wait so queued workers cannot outrun the circuit.
        with _SS_LOCK:
            if not allow_when_tripped and _semantic_scholar_circuit_open_locked():
                return False
            _SS_LAST_REQUEST = time.monotonic()
    return True


def _semantic_scholar_retry_after(headers: Any) -> float:
    value = headers.get("Retry-After") if headers else None
    try:
        return max(0.0, float(str(value or "").strip()))
    except (TypeError, ValueError):
        return 0.0


def _record_semantic_scholar_outcome(
    outcome: str,
    *,
    retry_after: float = 0.0,
    terminal_rate_limit: bool = True,
) -> None:
    """Record attempt-level pressure; absolute count tracks exhausted DOI calls."""
    global _SS_RATE_LIMITED_COUNT, _SS_CONSECUTIVE_TERMINAL_RATE_LIMITS, _SS_LAST_RETRY_AFTER_SECONDS
    with _SS_LOCK:
        _SS_RECENT_OUTCOMES.append(outcome)
        if outcome == "rate_limited":
            if terminal_rate_limit:
                _SS_RATE_LIMITED_COUNT += 1
                _SS_CONSECUTIVE_TERMINAL_RATE_LIMITS += 1
        else:
            # Reset only the fast terminal-429 streak. Rolling pressure and
            # the absolute exhausted-DOI counter remain auditable.
            _SS_CONSECUTIVE_TERMINAL_RATE_LIMITS = 0
        if retry_after > 0:
            _SS_LAST_RETRY_AFTER_SECONDS = retry_after


def _record_semantic_scholar_throttle() -> None:
    """Backward-compatible helper used by focused tests."""
    _record_semantic_scholar_outcome("rate_limited")


def _reset_semantic_scholar_throttle_burst() -> None:
    """Compatibility no-op: one success must not erase recent throttle pressure."""
    return None


def semantic_scholar_doi_metadata(doi: str, timeout: int, *, retries: int = 2) -> dict[str, Any]:
    fields = urllib.parse.urlencode({"fields": "abstract,authors,publicationDate,externalIds"})
    api_key = _semantic_scholar_api_key()
    headers = {"x-api-key": api_key} if api_key else {}
    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/"
        f"DOI:{urllib.parse.quote(doi)}?{fields}"
    )
    requested_retries = max(0, retries)
    effective_retries = min(requested_retries, SS_KEY_MAX_RETRIES) if api_key else requested_retries
    attempts = effective_retries + 1
    payload: dict[str, Any] = {}
    for attempt in range(attempts):
        if not _semantic_scholar_gate(allow_when_tripped=attempt > 0):
            return {
                "_status": "skipped_rate_limited",
                "_provider": "semantic-scholar",
                "_circuit_open": True,
            }
        try:
            payload = fetch_json_retry(
                url,
                timeout=timeout,
                retries=0,
                headers=headers,
            )
            _record_semantic_scholar_outcome("success")
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                _record_semantic_scholar_outcome("not_found")
                return {"_status": "not_found", "_provider": "semantic-scholar"}
            if exc.code == 429:
                retry_after = _semantic_scholar_retry_after(exc.headers)
                exhausted = attempt + 1 >= attempts
                _record_semantic_scholar_outcome(
                    "rate_limited",
                    retry_after=retry_after,
                    terminal_rate_limit=exhausted,
                )
                if exhausted:
                    return {
                        "_status": "rate_limited",
                        "_status_code": exc.code,
                        "_provider": "semantic-scholar",
                        "_retry_after_seconds": retry_after or None,
                    }
                wait = max(1.5 * (2**attempt), retry_after)
                time.sleep(min(wait, SS_MAX_RETRY_BACKOFF_SECONDS))
                continue
            if exc.code in {500, 502, 503, 504}:
                _record_semantic_scholar_outcome("provider_error")
                if attempt + 1 < attempts:
                    time.sleep(min(1.5 * (2**attempt), SS_MAX_RETRY_BACKOFF_SECONDS))
                    continue
                return {
                    "_status": "provider_error",
                    "_status_code": exc.code,
                    "_provider": "semantic-scholar",
                }
            _record_semantic_scholar_outcome("http_error")
            return {
                "_status": "http_error",
                "_status_code": exc.code,
                "_provider": "semantic-scholar",
            }
        except urllib.error.URLError as exc:
            _record_semantic_scholar_outcome("network_error")
            if attempt + 1 < attempts:
                time.sleep(min(1.5 * (2**attempt), SS_MAX_RETRY_BACKOFF_SECONDS))
                continue
            return {
                "_status": "error",
                "_error": f"{type(exc).__name__}: {exc}",
                "_provider": "semantic-scholar",
            }
        except Exception as exc:  # noqa: BLE001
            _record_semantic_scholar_outcome("error")
            return {
                "_status": "error",
                "_error": f"{type(exc).__name__}: {exc}",
                "_provider": "semantic-scholar",
            }
    result: dict[str, Any] = {}
    abstract = clean_abstract_text(payload.get("abstract"))
    if len(abstract) > 80:
        result["abstract"] = abstract
        result["abstract_source"] = "semantic_scholar"
    authors = []
    for author in payload.get("authors") or []:
        name = clean_text(str(author.get("name") or "")) if isinstance(author, dict) else ""
        if name and name not in authors:
            authors.append(name)
    if authors:
        result["authors"] = authors[:12]
    published = parse_date(str(payload.get("publicationDate") or ""))
    if published:
        result["published_online"] = published
    return result

def crossref_title_metadata(title: str, timeout: int) -> dict[str, Any]:
    query = urllib.parse.urlencode({"query.title": title, "rows": 3})
    try:
        payload = fetch_json(f"https://api.crossref.org/works?{query}", timeout=timeout)
    except Exception:
        return {}
    normalized = " ".join(title.casefold().split())
    for item in (payload.get("message") or {}).get("items") or []:
        candidate = " ".join(str(item.get("title", [""])[0] or "").casefold().split())
        if not candidate or (normalized not in candidate and candidate not in normalized):
            continue
        authors = []
        for author in item.get("author") or []:
            if not isinstance(author, dict):
                continue
            name = clean_text(" ".join(str(author.get(key) or "") for key in ("given", "family")))
            if name and name not in authors:
                authors.append(name)
        result: dict[str, Any] = {"authors": authors[:12]} if authors else {}
        doi = normalize_doi(item.get("DOI"))
        if doi:
            result["doi"] = doi
        return result
    return {}


def unpaywall_doi_metadata(doi: str, timeout: int) -> dict[str, str]:
    email = os.environ.get("UNPAYWALL_EMAIL") or os.environ.get("CROSSREF_MAILTO") or "econ-paper-monitor@example.com"
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(email)}"
    try:
        payload = fetch_json(url, timeout=timeout)
    except Exception:
        return {}
    result: dict[str, str] = {}
    parsed = parse_date(str(payload.get("published_date") or "")) if payload.get("published_date") else None
    if parsed:
        result["available_online"] = parsed
        result["published_online"] = parsed
        result["date_source"] = "unpaywall_published_date"
        result["date_confidence"] = "C"
    return result


def append_date_evidence(record: dict[str, Any], source: str, metadata: dict[str, Any]) -> bool:
    if not metadata:
        return False
    raw_data = record.setdefault("raw_data", {})
    if not isinstance(raw_data, dict):
        raw_data = {}
        record["raw_data"] = raw_data
    evidence = raw_data.setdefault("date_evidence", [])
    if not isinstance(evidence, list):
        evidence = []
        raw_data["date_evidence"] = evidence
    item = {
        "source": source,
        "date_source": metadata.get("date_source"),
        "published_online": metadata.get("published_online"),
        "available_online": metadata.get("available_online"),
        "issue_date": metadata.get("issue_date"),
        "accepted_date": metadata.get("accepted_date"),
        "date_confidence": metadata.get("date_confidence"),
    }
    signature = (item["source"], item["date_source"], item["published_online"], item["issue_date"], item["accepted_date"])
    existing = {
        (entry.get("source"), entry.get("date_source"), entry.get("published_online"), entry.get("issue_date"), entry.get("accepted_date"))
        for entry in evidence
        if isinstance(entry, dict)
    }
    if signature not in existing:
        evidence.append({key: value for key, value in item.items() if value})
        return True
    return False


def api_fallback_metadata(record: dict[str, Any], doi: str, timeout: int) -> tuple[dict[str, Any], str]:
    providers = [
        ("crossref-doi", crossref_doi_metadata),
        ("openalex", openalex_doi_metadata),
        ("semantic-scholar", semantic_scholar_doi_metadata),
        ("unpaywall", unpaywall_doi_metadata),
    ]
    first: dict[str, Any] = {}
    first_source = "api-fallback-empty"
    evidence_changed = False
    for source, getter in providers:
        metadata = getter(doi, timeout)
        evidence_changed = append_date_evidence(record, source, metadata) or evidence_changed
        if metadata and not first:
            first = metadata
            first_source = f"{source}-fallback"
        elif metadata and "abstract" in metadata and "abstract" not in first:
            first["abstract"] = metadata["abstract"]
            first["abstract_source"] = metadata.get("abstract_source", source)
        if metadata.get("authors") and not first.get("authors"):
            first["authors"] = metadata["authors"]
    if evidence_changed:
        first["_evidence_changed"] = "true"
    return first, first_source


def merge_metadata(record: dict[str, Any], metadata: dict[str, Any]) -> bool:
    changed = False
    confidence_rank = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4, "unknown": 5, "": 6}
    existing_confidence = str(record.get("date_confidence") or "")
    incoming_confidence = str(metadata.get("date_confidence") or "")
    protect_existing_dates = bool(record.get("available_online") or record.get("published_online")) and (
        confidence_rank.get(incoming_confidence, 6) > confidence_rank.get(existing_confidence, 6)
    )
    date_fields = {
        "accepted_date",
        "available_online",
        "published_online",
        "issue_date",
        "date_source",
        "date_confidence",
    }
    for field, value in metadata.items():
        if field == "_evidence_changed":
            changed = True
            continue
        if protect_existing_dates and field in date_fields and record.get(field):
            continue
        if value and record.get(field) != value:
            record[field] = value
            changed = True
    return changed


def should_enrich(record: dict[str, Any]) -> bool:
    source_type = str(record.get("source_type") or "")
    if str(record.get("source") or "") == "working_papers" or source_type in {"working_paper", "policy_paper", "aggregator"}:
        return False
    if record.get("date_confidence") == "A" and record.get("accepted_date"):
        return False
    url = record.get("url") or (f"https://doi.org/{record['doi']}" if record.get("doi") else None)
    return bool(url and str(url).startswith(("http://", "https://")))


def candidate_urls(record: dict[str, Any]) -> list[str]:
    urls = []
    if record.get("url"):
        urls.append(str(record["url"]))
    doi = record.get("doi")
    if doi:
        doi = str(doi).strip()
        urls.append(f"https://doi.org/{doi}")
        if doi.startswith("10.1080/"):
            urls.append(f"https://www.tandfonline.com/doi/abs/{doi}")
            urls.append(f"https://www.tandfonline.com/doi/full/{doi}")
        if doi.startswith("10.1016/"):
            pii = extract_elsevier_pii(record.get("pii"), record.get("url"), record.get("source_url"))
            if pii:
                urls.append(f"https://www.sciencedirect.com/science/article/pii/{pii}")
        if doi.startswith("10.1093/"):
            urls.append(f"https://academic.oup.com/search-results?page=1&q={doi}")
        if doi.startswith("10.1111/") or doi.startswith("10.1002/"):
            urls.append(f"https://onlinelibrary.wiley.com/doi/full/{doi}")
        if doi.startswith("10.1007/"):
            urls.append(f"https://link.springer.com/article/{doi}")
    return list(dict.fromkeys(urls))


def publisher_bucket(record: dict[str, Any]) -> str:
    doi = str(record.get("doi") or "").strip().lower()
    url = " ".join(str(record.get(key) or "").lower() for key in ("url", "source_url"))
    journal = str(record.get("journal") or "").lower()
    haystack = f"{doi} {url} {journal}"
    if doi.startswith("10.1016/") or "sciencedirect.com" in haystack or "elsevier" in haystack:
        return "Elsevier"
    if doi.startswith("10.1080/") or "tandfonline.com" in haystack or "taylor" in haystack:
        return "Taylor & Francis"
    if doi.startswith(("10.1111/", "10.1002/")) or "onlinelibrary.wiley.com" in haystack or "wiley" in haystack:
        return "Wiley"
    if doi.startswith("10.1093/") or "academic.oup.com" in haystack or "oxford" in haystack:
        return "OUP"
    if doi.startswith("10.1007/") or "link.springer.com" in haystack or "springer" in haystack:
        return "Springer"
    return "Other"


def has_ab_date(record: dict[str, Any]) -> bool:
    confidence = str(record.get("date_confidence") or "")
    return confidence in {"A", "B"} and bool(
        record.get("official_date")
        or record.get("available_online")
        or record.get("published_online")
        or record.get("issue_date")
    )


def needs_date_recovery(record: dict[str, Any]) -> bool:
    confidence = str(record.get("date_confidence") or "unknown")
    has_official_date = any(
        record.get(field)
        for field in ("official_date", "available_online", "published_online", "issue_date")
    )
    return not has_official_date or confidence in {"", "C", "D", "F", "unknown"}


def enrich_priority(record: dict[str, Any]) -> tuple[int, int, int, int, float]:
    bucket = publisher_bucket(record)
    core_rank = {"Elsevier": 0, "Springer": 1, "Taylor & Francis": 2, "Wiley": 3, "OUP": 4}.get(bucket, 8)
    confidence = str(record.get("date_confidence") or "F")
    weak_date = 0 if not has_ab_date(record) or confidence in {"C", "D", "F", "unknown"} else 1
    missing_authors = 0 if not record.get("authors") else 1
    missing_abstract = 0 if not str(record.get("abstract") or "").strip() else 1
    try:
        detected_rank = -datetime.fromisoformat(str(record.get("detected_at") or record.get("first_seen") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        detected_rank = 0.0
    return (missing_authors, weak_date, missing_abstract, core_rank, detected_rank)


def abstract_enrich_priority(record: dict[str, Any]) -> tuple[int, float, int]:
    missing_abstract = 0 if not str(record.get("abstract") or "").strip() else 1
    try:
        detected_rank = -datetime.fromisoformat(
            str(record.get("detected_at") or record.get("first_seen") or "").replace("Z", "+00:00")
        ).timestamp()
    except ValueError:
        detected_rank = 0.0
    bucket = publisher_bucket(record)
    core_rank = {"Elsevier": 0, "Taylor & Francis": 1, "Wiley": 2, "OUP": 3}.get(bucket, 8)
    return (missing_abstract, detected_rank, core_rank)


def enrich_record(record: dict[str, Any], timeout: int, allow_proxy_abstract: bool = True) -> tuple[bool, str]:
    urls = candidate_urls(record)
    if not urls:
        return False, "missing-url"
    metadata: dict[str, str] = {}
    last_status = "no-metadata"
    doi = str(record.get("doi") or "").strip()
    resolved_elsevier_pii = False
    elsevier_api_attempted = False
    missing_abstract = not str(record.get("abstract") or "").strip()
    changed = False
    if doi.startswith("10.1016/") and not has_ab_date(record):
        metadata = crossref_doi_metadata(doi, timeout)
        evidence_changed = append_date_evidence(record, "crossref-doi", metadata)
        if metadata:
            metadata_changed = merge_metadata(record, metadata)
            changed = evidence_changed or metadata_changed or changed
            last_status = "crossref-doi-fallback"
            if not missing_abstract:
                return changed, last_status
            metadata = {}
        if evidence_changed:
            changed = True
            last_status = "crossref-doi-evidence"

    if missing_abstract and publisher_bucket(record) == "Elsevier":
        if doi.startswith("10.1016/"):
            elsevier_api_attempted = True
            elsevier_metadata = elsevier_api_metadata(doi, timeout)
            if elsevier_metadata:
                changed = append_date_evidence(record, "elsevier-article-api", elsevier_metadata) or changed
                changed = merge_metadata(record, elsevier_metadata) or changed
                if elsevier_metadata.get("pii"):
                    resolved_elsevier_pii = True
                last_status = "elsevier-article-api"
        pii = extract_elsevier_pii(record.get("pii"), record.get("url"), record.get("source_url"))
        if allow_proxy_abstract and pii:
            proxy_url = f"https://www.sciencedirect.com/science/article/pii/{pii}"
            proxy_metadata = publisher_proxy_metadata(proxy_url, timeout)
            if proxy_metadata.get("abstract"):
                changed = merge_metadata(record, proxy_metadata) or changed
                return changed, "publisher-proxy-abstract"
            return changed, str(proxy_metadata.get("_status") or "abstract-proxy-empty")
        if pii:
            return changed, "elsevier-metadata-only"
    for url in urls:
        try:
            html, final_url = fetch_text_and_url(str(url), timeout)
            pii = extract_elsevier_pii(final_url, html) if doi.startswith("10.1016/") else None
            if pii and record.get("pii") != pii:
                record["pii"] = pii
                resolved_elsevier_pii = True
                pii_url = f"https://www.sciencedirect.com/science/article/pii/{pii}"
                if pii_url not in urls:
                    urls.append(pii_url)
            metadata = extract_page_metadata(html)
            if metadata:
                break
        except Exception as exc:  # noqa: BLE001
            last_status = type(exc).__name__
            continue
    if not metadata:
        if doi:
            metadata, api_status = api_fallback_metadata(record, doi, timeout)
            if metadata:
                last_status = api_status
        if resolved_elsevier_pii:
            changed = True
    if metadata:
        changed = merge_metadata(record, metadata) or changed

    if not elsevier_api_attempted and doi.startswith("10.1016/") and (
        missing_abstract or not has_ab_date(record) or str(record.get("date_confidence") or "") in {"C", "D", "F", "unknown"}
    ):
        elsevier_metadata = elsevier_api_metadata(doi, timeout)
        if elsevier_metadata:
            changed = append_date_evidence(record, "elsevier-article-api", elsevier_metadata) or changed
            changed = merge_metadata(record, elsevier_metadata) or changed
            if elsevier_metadata.get("pii"):
                resolved_elsevier_pii = True
            last_status = "elsevier-article-api"

    if missing_abstract and allow_proxy_abstract and not str(record.get("abstract") or "").strip():
        proxy_url = ""
        pii = extract_elsevier_pii(record.get("pii"), record.get("url"), record.get("source_url"))
        if pii:
            proxy_url = f"https://www.sciencedirect.com/science/article/pii/{pii}"
        else:
            for candidate in candidate_urls(record):
                if urllib.parse.urlparse(candidate).netloc.casefold() in {
                    "onlinelibrary.wiley.com",
                    "www.tandfonline.com",
                    "tandfonline.com",
                    "academic.oup.com",
                }:
                    proxy_url = candidate
                    break
        proxy_metadata = publisher_proxy_metadata(proxy_url, timeout) if proxy_url else {}
        if proxy_metadata:
            changed = merge_metadata(record, proxy_metadata) or changed
            last_status = "publisher-proxy-abstract"

    if missing_abstract and doi and not str(record.get("abstract") or "").strip():
        api_metadata, api_status = api_fallback_metadata(record, doi, timeout)
        abstract = api_metadata.get("abstract")
        if abstract:
            record["abstract"] = abstract
            record["abstract_source"] = api_metadata.get("abstract_source", api_status)
            changed = True
            last_status = "abstract-api-fallback"
        elif api_metadata.get("_evidence_changed"):
            changed = True
            last_status = "abstract-api-no-abstract"
    changed = correct_tandf_date(record) or changed
    if resolved_elsevier_pii and not changed:
        changed = True
    if not metadata and not changed:
        return False, last_status
    if last_status.endswith("-fallback"):
        return changed, last_status
    return changed, "updated" if changed else "metadata-unchanged"


def enrich_abstract_record(record: dict[str, Any], timeout: int) -> tuple[bool, str]:
    source_id = str(record.get("source_id") or "")
    needs_readonly_date_retry = source_id in {"cepr-dp", "fed-feds"} and str(
        record.get("date_confidence") or ""
    ) in {"F", "unknown"}
    if str(record.get("abstract") or "").strip() and record.get("authors") and not needs_readonly_date_retry:
        return False, "abstract-present"
    changed = False
    if source_id in {"cepr-dp", "fed-feds"}:
        from fetch_preprints import enrich_record_from_proxy

        before_abstract = str(record.get("abstract") or "")
        before_authors = list(record.get("authors") or [])
        before_date = (
            record.get("published_online"),
            record.get("available_online"),
            record.get("official_date"),
            record.get("date_source"),
            record.get("date_confidence"),
        )
        enrich_record_from_proxy(record, source_id, timeout=timeout)
        if str(record.get("abstract") or "").strip() and str(record.get("abstract") or "") != before_abstract:
            return True, "abstract-updated:readonly-proxy"
        if list(record.get("authors") or []) != before_authors:
            changed = True
        after_date = (
            record.get("published_online"),
            record.get("available_online"),
            record.get("official_date"),
            record.get("date_source"),
            record.get("date_confidence"),
        )
        if after_date != before_date:
            changed = True
    bucket = publisher_bucket(record)
    doi = str(record.get("doi") or "").strip()
    proxy_url = ""
    if doi:
        for source, getter in (
            ("crossref-doi", crossref_doi_metadata),
            ("openalex", openalex_doi_metadata),
            ("semantic-scholar", semantic_scholar_doi_metadata),
        ):
            metadata = getter(doi, timeout)
            if not metadata:
                continue
            changed = append_date_evidence(record, source, metadata) or changed
            changed = merge_metadata(record, metadata) or changed
            if str(record.get("abstract") or "").strip():
                return changed, f"metadata-updated:{source}"
    if bucket == "Elsevier":
        if doi.startswith("10.1016/"):
            metadata = elsevier_api_metadata(doi, timeout)
            if metadata:
                changed = append_date_evidence(record, "elsevier-article-api", metadata) or changed
                changed = merge_metadata(record, metadata) or changed
        pii = extract_elsevier_pii(record.get("pii"), record.get("url"), record.get("source_url"))
        if pii:
            proxy_url = f"https://www.sciencedirect.com/science/article/pii/{pii}"
    elif bucket in {"Taylor & Francis", "Wiley", "OUP", "Springer"}:
        for candidate in candidate_urls(record):
            if urllib.parse.urlparse(candidate).netloc.casefold() in {
                "onlinelibrary.wiley.com",
                "www.tandfonline.com",
                "tandfonline.com",
                "academic.oup.com",
                "link.springer.com",
            }:
                proxy_url = candidate
                break
    if not proxy_url:
        return changed, "abstract-route-missing" if not changed else "metadata-only"
    metadata = publisher_proxy_metadata(proxy_url, timeout)
    if not metadata.get("abstract"):
        proxy_status = str(metadata.get("_status") or "abstract-proxy-empty")
        return changed, proxy_status if not changed else f"metadata-only:{proxy_status}"
    changed = merge_metadata(record, metadata) or changed
    return changed, "abstract-updated"


def update_abstract_attempt_status(record: dict[str, Any], status: str) -> bool:
    """Expose an honest compact state while delayed metadata indexes catch up."""
    if str(record.get("abstract") or "").strip():
        changed = record.pop("abstract_status", None) is not None
        if record.get("abstract_status_code") != "available":
            record["abstract_status_code"] = "available"
            changed = True
        if record.get("abstract_completeness") != "full":
            record["abstract_completeness"] = "full"
            changed = True
        if record.get("abstract_enrichment_status") != "available":
            record["abstract_enrichment_status"] = "available"
            changed = True
        return changed

    changed = False
    public_status = "摘要暂未公开，系统将自动重试"
    if record.get("abstract_status") != public_status:
        record["abstract_status"] = public_status
        changed = True
    if record.get("abstract_status_code") != "missing_retry":
        record["abstract_status_code"] = "missing_retry"
        changed = True
    if record.get("abstract_completeness") != "missing":
        record["abstract_completeness"] = "missing"
        changed = True
    if record.get("abstract_enrichment_status") != status:
        record["abstract_enrichment_status"] = status
        changed = True
    return changed


def queue_metadata_retry(record: dict[str, Any], status: str) -> bool:
    state = {
        "status": "queued",
        "reason": status,
        "attempted_at": now(),
        "fallbacks": ["crossref-doi", "openalex", "readonly-proxy"],
    }
    if record.get("metadata_retry_state") == state:
        return False
    record["metadata_retry_state"] = state
    return True


def load_retry_identity_keys(path: Path | None) -> set[str]:
    if path is None:
        return set()
    payload = read_json(path, {"records": []})
    records = payload.get("records") if isinstance(payload, dict) else []
    keys: set[str] = set()
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        keys.update(str(value).casefold() for value in record.get("identity_keys") or [] if value)
        identity = str(record.get("identity") or "").strip().casefold()
        if identity:
            keys.add(identity)
        keys.update(strong_identity_keys(record))
    return keys


def enrich_author_record(record: dict[str, Any], timeout: int) -> tuple[bool, str]:
    if record.get("authors"):
        return False, "authors-present"
    doi = str(record.get("doi") or "").strip()
    if not doi:
        for url in candidate_urls(record):
            match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", url, flags=re.I)
            if match:
                doi = normalize_doi(match.group(0)) or ""
                if doi:
                    record["doi"] = doi
                    break
    if doi:
        for source, getter in (("crossref-doi", crossref_doi_metadata), ("openalex", openalex_doi_metadata)):
            metadata = getter(doi, timeout)
            if metadata.get("authors"):
                changed = merge_metadata(record, metadata)
                return changed, f"authors-updated:{source}"
    if not doi and str(record.get("source_id") or "") in {"oecd-working-papers", "cepr-dp"}:
        metadata = crossref_title_metadata(str(record.get("title") or ""), timeout)
        if metadata.get("authors"):
            changed = merge_metadata(record, metadata)
            return changed, "authors-updated:crossref-title"
    for url in candidate_urls(record):
        try:
            page_html, _ = fetch_text_and_url(url, timeout)
        except Exception:
            continue
        metadata = extract_page_metadata(page_html)
        if not metadata.get("authors") and record.get("source_id") == "iza":
            from fetch_preprints import iza_detail_authors

            metadata["authors"] = iza_detail_authors(page_html)
        if metadata.get("authors"):
            changed = merge_metadata(record, metadata)
            return changed, "authors-updated:publisher-page"
    if record.get("source_id") in {"fed-feds", "cepr-dp"}:
        from fetch_preprints import enrich_record_from_proxy

        before = list(record.get("authors") or [])
        enrich_record_from_proxy(record, str(record.get("source_id")), timeout=timeout)
        if record.get("authors") and record.get("authors") != before:
            return True, "authors-updated:readonly-proxy"
    if not record.get("authors") and not record.get("authors_status"):
        source_id = str(record.get("source_id") or "")
        record["authors_status"] = (
            "官方页面未列出个人作者"
            if source_id == "oecd-working-papers"
            else "作者信息待核验"
        )
        return True, "authors-status-marked"
    return False, "authors-not-found"


def record_publisher_group(stats: dict[str, dict[str, Any]]) -> None:
    status = load_status()
    publishers = []
    for core_publisher in ("Elsevier", "Taylor & Francis", "Wiley", "OUP"):
        stats.setdefault(
            core_publisher,
            {"attempted": 0, "changed": 0, "ab_dates": 0, "failures": 0, "status_counts": Counter()},
        )
    for publisher, item in sorted(stats.items()):
        attempted = int(item.get("attempted") or 0)
        ab_dates = int(item.get("ab_dates") or 0)
        failures = int(item.get("failures") or 0)
        status_counts = item.get("status_counts") or {}
        top_status = ", ".join(f"{key}:{value}" for key, value in Counter(status_counts).most_common(4))
        publishers.append(
            {
                "publisher": publisher,
                "attempted": attempted,
                "changed": int(item.get("changed") or 0),
                "ab_dates": ab_dates,
                "success_rate": round(ab_dates / attempted, 4) if attempted else 0,
                "failures": failures,
                "degraded": failures > 0,
                "retryable": failures > 0,
                "fallbacks": ["crossref-doi", "openalex", "readonly-proxy"],
                "statuses": dict(sorted(status_counts.items())),
                "message": top_status,
            }
        )
    status.setdefault("source_groups", {})["publisher-detail"] = {
        "updated_at": now(),
        "publishers": publishers,
    }
    save_status(status)


def correct_tandf_date(record: dict[str, Any]) -> bool:
    doi = str(record.get("doi") or "")
    if not doi.startswith("10.1080/"):
        return False
    issue_date = record.get("issue_date")
    current = record.get("available_online") or record.get("published_online")
    if not issue_date or not current:
        return False
    try:
        issue = date.fromisoformat(str(issue_date))
        online = date.fromisoformat(str(current))
    except ValueError:
        return False
    if not (date(2020, 1, 1) <= issue <= online and (online - issue).days <= 14):
        return False
    changed = False
    for field in ("available_online", "published_online"):
        if record.get(field) != issue.isoformat():
            record[field] = issue.isoformat()
            changed = True
    if changed:
        record["date_source"] = "tandf_issue_date_fallback"
        record["date_confidence"] = "B"
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--seen", type=Path, default=DATA_DIR / "seen.json")
    parser.add_argument("--date", default=today_str())
    parser.add_argument("--doi", default=None, help="Only enrich the matching DOI (useful for retries and audits).")
    parser.add_argument("--latest-days", type=int, default=1)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--proxy-abstract-limit", type=int, default=20)
    parser.add_argument("--abstract-only", action="store_true", help="Skip slow publisher HTML and only run abstract fallbacks.")
    parser.add_argument("--authors-only", action="store_true", help="Only backfill records whose author list is missing.")
    parser.add_argument("--date-only", action="store_true", help="Only retry records with missing or weak official-date evidence.")
    parser.add_argument("--source-id", default=None, help="Only process records from one source id during a targeted retry.")
    parser.add_argument(
        "--retry-queue",
        type=Path,
        default=None,
        help="Only process records whose durable identities occur in this metadata retry queue.",
    )
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    try:
        anchor = date.fromisoformat(args.date)
    except ValueError:
        anchor = date.fromisoformat(today_str())
    paths = [
        args.daily_dir / f"{(anchor - timedelta(days=offset)).isoformat()}.json"
        for offset in range(max(1, args.latest_days))
    ]
    attempted = changed = 0
    messages: list[str] = []
    publisher_stats: dict[str, dict[str, Any]] = {}
    records_by_path: dict[Path, Any] = {}
    candidates: list[tuple[Path, dict[str, Any]]] = []
    daily_identities: set[str] = set()
    retry_identity_keys = load_retry_identity_keys(args.retry_queue)
    retry_filter_enabled = args.retry_queue is not None

    def selected_by_retry_queue(record: dict[str, Any]) -> bool:
        return not retry_filter_enabled or bool(strong_identity_keys(record).intersection(retry_identity_keys))

    def identity(record: dict[str, Any]) -> str:
        doi = str(record.get("doi") or "").strip().casefold()
        if doi:
            return f"doi:{doi}"
        url = str(record.get("url") or "").strip().casefold()
        if url:
            return f"url:{url}"
        return f"title:{str(record.get('journal') or '').casefold()}:{str(record.get('title') or '').casefold()}"

    for path in paths:
        records = read_json(path, [])
        records_by_path[path] = records
        daily_identities.update(identity(record) for record in records if isinstance(record, dict))
        candidates.extend(
            (path, record)
            for record in records
            if (args.authors_only or args.abstract_only or args.date_only or should_enrich(record))
            and selected_by_retry_queue(record)
            and (not args.authors_only or not record.get("authors"))
            and (not args.date_only or needs_date_recovery(record))
            and (not args.source_id or str(record.get("source_id") or "") == args.source_id)
            and (not args.doi or str(record.get("doi") or "").strip().casefold() == args.doi.strip().casefold())
        )
    seen_payload = read_json(args.seen, {"papers": {}})
    seen_papers = seen_payload.get("papers") if isinstance(seen_payload, dict) else None
    if isinstance(seen_papers, dict):
        records_by_path[args.seen] = seen_payload
        oldest = anchor - timedelta(days=max(1, args.latest_days) - 1)
        for record in seen_papers.values():
            if not isinstance(record, dict) or identity(record) in daily_identities:
                continue
            seen_date = str(record.get("first_seen") or "")[:10]
            try:
                is_recent = date.fromisoformat(seen_date) >= oldest
            except ValueError:
                is_recent = False
            if (
                (
                    is_recent
                    or (args.authors_only and not record.get("authors"))
                    or (args.date_only and needs_date_recovery(record))
                )
                and (args.authors_only or args.abstract_only or args.date_only or should_enrich(record))
                and selected_by_retry_queue(record)
                and (not args.source_id or str(record.get("source_id") or "") == args.source_id)
                and (not args.authors_only or not record.get("authors"))
                and (not args.date_only or needs_date_recovery(record))
                and (not args.doi or str(record.get("doi") or "").strip().casefold() == args.doi.strip().casefold())
            ):
                candidates.append((args.seen, record))
    priority = abstract_enrich_priority if args.abstract_only else enrich_priority
    candidates.sort(key=lambda item: priority(item[1]))

    changed_paths: set[Path] = set()
    selected: list[tuple[Path, dict[str, Any], bool]] = []
    proxy_abstract_attempted = 0
    for path, record in candidates[: max(0, args.limit)]:
        bucket = publisher_bucket(record)
        needs_proxy = not str(record.get("abstract") or "").strip() and bucket in {
            "Elsevier",
            "Springer",
            "Taylor & Francis",
            "Wiley",
            "OUP",
        }
        allow_proxy = needs_proxy and proxy_abstract_attempted < max(0, args.proxy_abstract_limit)
        if allow_proxy:
            proxy_abstract_attempted += 1
        selected.append((path, record, allow_proxy))

    def run_candidate(item: tuple[Path, dict[str, Any], bool]) -> tuple[Path, dict[str, Any], bool, str, Exception | None]:
        path, record, allow_proxy = item
        try:
            if args.authors_only:
                did_change, status = enrich_author_record(record, args.timeout)
            elif args.abstract_only:
                did_change, status = enrich_abstract_record(record, args.timeout)
                did_change = update_abstract_attempt_status(record, status) or did_change
            elif args.date_only:
                if str(record.get("source_id") or "") in {"cepr-dp", "fed-feds"}:
                    did_change, status = enrich_abstract_record(record, args.timeout)
                else:
                    did_change, status = enrich_record(record, args.timeout, allow_proxy_abstract=False)
            else:
                did_change, status = enrich_record(record, args.timeout, allow_proxy_abstract=allow_proxy)
        except Exception as exc:  # noqa: BLE001
            return path, record, False, type(exc).__name__, exc
        return path, record, did_change, status, None

    attempted = len(selected)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        results = executor.map(run_candidate, selected)
        for path, record, did_change, status, error in results:
            bucket = publisher_bucket(record)
            stats = publisher_stats.setdefault(
                bucket,
                {"attempted": 0, "changed": 0, "ab_dates": 0, "failures": 0, "status_counts": Counter()},
            )
            stats["attempted"] += 1
            if error is not None:
                stats["failures"] += 1
                stats["status_counts"][status] += 1
                if queue_metadata_retry(record, status):
                    changed_paths.add(path)
                messages.append(f"{path.stem} {record.get('journal')}: {status}")
                continue
            changed += int(did_change)
            if did_change:
                changed_paths.add(path)
            stats["changed"] += int(did_change)
            stats["status_counts"][status] += 1
            record_has_ab_date = has_ab_date(record)
            if record_has_ab_date:
                stats["ab_dates"] += 1
            if not record_has_ab_date and status not in {"updated", "metadata-unchanged", "tandf-date-corrected"}:
                stats["failures"] += 1
            if status not in {"no-dates", "no-metadata"}:
                messages.append(f"{path.stem} {record.get('journal')}: {status}")

    for path in sorted(changed_paths):
        write_json(path, records_by_path[path])
    total_failures = sum(int(item.get("failures") or 0) for item in publisher_stats.values())
    record_source(
        "publisher-detail",
        ok=total_failures == 0,
        count=changed,
        message=f"attempted={attempted}; proxy_abstract_attempted={proxy_abstract_attempted}; " + "; ".join(messages[-20:]),
        details={
            "attempted": attempted,
            "failures": total_failures,
            "retryable": total_failures > 0,
            "fallbacks": ["crossref-doi", "openalex", "readonly-proxy"],
        },
    )
    record_publisher_group(publisher_stats)
    print(f"publisher detail attempted={attempted} changed={changed}")


if __name__ == "__main__":
    main()
