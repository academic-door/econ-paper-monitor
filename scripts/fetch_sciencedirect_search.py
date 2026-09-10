"""Fetch newly available Elsevier articles before RSS and Crossref catch up."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from common import DATA_DIR, load_journals, today_str, write_json
from sources.record import article_record
from status import record_source


SEARCH_BASE = "https://r.jina.ai/http://www.sciencedirect.com/search"
SCIENCEDIRECT_API_URL = "https://api.elsevier.com/content/search/sciencedirect"
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept": "text/plain,text/markdown;q=0.9,*/*;q=0.8",
}
CAPTCHA_MARKERS = (
    "are you a robot",
    "captcha challenge",
    "requiring captcha",
    "just a moment",
)

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def clean_markdown(value: str | None) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"[_*`]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalized_name(value: str | None) -> str:
    text = clean_markdown(value).casefold().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalized_issn(value: str | None) -> str:
    return re.sub(r"[^0-9X]", "", str(value or "").upper())


def parse_online_date(value: str | None) -> str | None:
    text = clean_markdown(value)
    match = re.search(r"Available online\s+(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", text, flags=re.I)
    if not match:
        return None
    month = MONTHS.get(match.group(2).casefold())
    if not month:
        return None
    return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(1)):02d}"


def parse_iso_date(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.match(r"^(20\d{2}-\d{2}-\d{2})", text)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return None


def fetch_text(url: str, timeout: int) -> str:
    headers = dict(BROWSER_HEADERS)
    jina_key = os.environ.get("JINA_API_KEY") or ""
    if jina_key:
        headers["Authorization"] = f"Bearer {jina_key}"
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = response.read()
            except Exception:
                context = ssl._create_unverified_context()
                with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                    payload = response.read()
            return payload.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == 0:
                time.sleep(2)
    if last_error is not None:
        raise last_error
    raise RuntimeError("ScienceDirect search fetch returned no response")


def elsevier_api_headers() -> dict[str, str]:
    api_key = os.environ.get("ELSEVIER_API_KEY") or ""
    if not api_key:
        raise RuntimeError("sciencedirect-api-key-missing")
    headers = {
        "User-Agent": "econ-paper-monitor/1.0 (https://github.com/academic-door/econ-paper-monitor)",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-ELS-APIKey": api_key,
    }
    inst_token = os.environ.get("ELSEVIER_INST_TOKEN") or ""
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token
    return headers


def api_show_limit(max_items: int) -> int:
    requested = max(1, int(max_items or 1))
    for candidate in (10, 25, 50, 100):
        if requested <= candidate:
            return candidate
    return 100


def sciencedirect_api_payload(journal: dict[str, Any], *, days: int, max_items: int) -> dict[str, Any]:
    cutoff = date.fromisoformat(today_str()) - timedelta(days=max(1, days) - 1)
    return {
        "pub": str(journal["title"]),
        "loadedAfter": f"{cutoff.isoformat()}T00:00:00Z",
        "display": {
            "offset": 0,
            "show": api_show_limit(max_items),
            "sortBy": "date",
        },
    }


def fetch_sciencedirect_api(journal: dict[str, Any], *, days: int, timeout: int, max_items: int) -> list[dict[str, Any]]:
    body = json.dumps(
        sciencedirect_api_payload(journal, days=days, max_items=max_items),
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        SCIENCEDIRECT_API_URL,
        data=body,
        headers=elsevier_api_headers(),
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("sciencedirect-api-invalid-response")
    results = payload.get("results")
    if not isinstance(results, list):
        message = clean_markdown(payload.get("message"))
        raise ValueError(f"sciencedirect-api-invalid-results{': ' + message if message else ''}")
    return [item for item in results if isinstance(item, dict)]


def api_result_record(item: dict[str, Any], journal: dict[str, Any]) -> dict[str, Any] | None:
    source_title = clean_markdown(item.get("sourceTitle"))
    if source_title and normalized_name(source_title) != normalized_name(str(journal.get("title") or "")):
        return None

    pii = clean_markdown(item.get("pii")).upper()
    doi = clean_markdown(item.get("doi")).casefold() or None
    title = clean_markdown(item.get("title"))
    if not title or (not pii and not doi):
        return None

    uri = str(item.get("uri") or "").strip()
    if not uri and pii:
        uri = f"https://www.sciencedirect.com/science/article/pii/{pii}"
    if uri.startswith("http://"):
        uri = "https://" + uri[len("http://"):]

    authors: list[str] = []
    for entry in item.get("authors") or []:
        if isinstance(entry, dict):
            author = clean_markdown(entry.get("name"))
        else:
            author = clean_markdown(entry)
        if author and author not in authors:
            authors.append(author)

    publication_date = parse_iso_date(str(item.get("publicationDate") or ""))
    load_date = parse_iso_date(str(item.get("loadDate") or ""))
    available_online = load_date or publication_date
    date_source = "sciencedirect_api_load_date" if load_date else "sciencedirect_api_publication_date"

    return article_record(
        journal,
        title=title,
        url=uri or None,
        source="sciencedirect_search",
        source_url=SCIENCEDIRECT_API_URL,
        doi=doi,
        authors=authors[:12],
        published_online=publication_date,
        available_online=available_online,
        date_source=date_source,
        date_confidence="B" if available_online else "F",
        raw_data={
            "pii": pii or None,
            "sciencedirect_search_route": "official_api_v2",
            "sciencedirect_search_journal": journal["title"],
            "sciencedirect_search_issn": journal.get("issn"),
            "sciencedirect_api_publication_date": publication_date,
            "sciencedirect_api_load_date": str(item.get("loadDate") or "").strip() or None,
        },
    )


def fetch_journal_via_api(
    journal: dict[str, Any],
    *,
    days: int,
    timeout: int,
    max_items: int,
) -> tuple[list[dict[str, Any]], str]:
    raw = fetch_sciencedirect_api(journal, days=days, timeout=timeout, max_items=max_items)
    records: list[dict[str, Any]] = []
    cutoff = date.fromisoformat(today_str()) - timedelta(days=max(1, days) - 1)
    for item in raw:
        load_date = parse_iso_date(str(item.get("loadDate") or ""))
        if load_date:
            try:
                if date.fromisoformat(load_date) < cutoff:
                    continue
            except ValueError:
                continue
        record = api_result_record(item, journal)
        if record is None:
            continue
        records.append(record)
        if len(records) >= max_items:
            break
    return records, f"{journal['title']}: {len(records)} via official-api-v2"


def parse_search_results(markdown: str, journal: dict[str, Any]) -> list[dict[str, Any]]:
    headings = list(
        re.finditer(
            r"(?m)^##\s+\[(?P<title>.+?)\]\((?P<url>https?://(?:www\.)?sciencedirect\.com/science/article/pii/(?P<pii>S[0-9A-Z]+))\)\s*$",
            markdown,
            flags=re.I,
        )
    )
    expected_name = normalized_name(str(journal.get("title") or ""))
    expected_issn = normalized_issn(str(journal.get("issn") or ""))
    records: list[dict[str, Any]] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        block = markdown[heading.end():end]
        journal_match = re.search(
            r"\[(?P<journal>[^\]]+)\]\(https?://(?:www\.)?sciencedirect\.com/science/journal/(?P<issn>[0-9X]+)\)(?P<label>[^\n]*)",
            block,
            flags=re.I,
        )
        if not journal_match:
            continue
        result_name = normalized_name(journal_match.group("journal"))
        result_issn = normalized_issn(journal_match.group("issn"))
        if result_name != expected_name and result_issn != expected_issn:
            continue
        online_date = parse_online_date(journal_match.group("label"))
        if not online_date:
            continue
        authors = []
        for match in re.finditer(r"(?m)^\s{4}\d+\.\s+(?P<author>[^\n]+)$", block):
            author = clean_markdown(match.group("author"))
            if author and author not in authors:
                authors.append(author)
        records.append(
            {
                "title": clean_markdown(heading.group("title")),
                "url": heading.group("url").replace("http://", "https://"),
                "pii": heading.group("pii").upper(),
                "authors": authors[:12],
                "available_online": online_date,
            }
        )
    return records


def elsevier_core_metadata(pii: str, timeout: int) -> dict[str, str]:
    url = f"https://api.elsevier.com/content/article/pii/{urllib.parse.quote(pii)}?httpAccept=application%2Fjson"
    headers = {
        "User-Agent": "econ-paper-monitor/1.0 (https://github.com/academic-door/econ-paper-monitor)",
        "Accept": "application/json",
    }
    api_key = os.environ.get("ELSEVIER_API_KEY") or ""
    if api_key:
        headers["X-ELS-APIKey"] = api_key
    inst_token = os.environ.get("ELSEVIER_INST_TOKEN") or ""
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}
    core = (payload.get("full-text-retrieval-response") or {}).get("coredata") if isinstance(payload, dict) else None
    if not isinstance(core, dict):
        return {}
    online_date = parse_online_date(str(core.get("prism:coverDisplayDate") or ""))
    return {
        "title": clean_markdown(core.get("dc:title")),
        "doi": str(core.get("prism:doi") or "").strip().casefold(),
        "journal": clean_markdown(core.get("prism:publicationName")),
        "available_online": online_date,
    }


def target_journals(journals: list[dict[str, Any]], only: set[str]) -> list[dict[str, Any]]:
    targets = []
    for journal in journals:
        if only and str(journal.get("id") or "") not in only:
            continue
        if "elsevier" not in str(journal.get("publisher") or "").casefold():
            continue
        if str(journal.get("priority_private") or "") not in {"A", "B"}:
            continue
        if not journal.get("issn"):
            continue
        targets.append(journal)
    return targets


def fetch_journal_via_proxy(
    journal: dict[str, Any],
    *,
    days: int,
    timeout: int,
    max_items: int,
) -> tuple[list[dict[str, Any]], str]:
    query = urllib.parse.urlencode({"pub": journal["title"], "show": "100", "sortBy": "date"})
    source_url = f"{SEARCH_BASE}?{query}"
    markdown = fetch_text(source_url, timeout)
    lowered = markdown.casefold()
    if any(marker in lowered for marker in CAPTCHA_MARKERS):
        raise ValueError("sciencedirect-search blocked-captcha")
    if not markdown.strip():
        raise RuntimeError("sciencedirect-search empty response")
    parsed = parse_search_results(markdown, journal)
    cutoff = date.fromisoformat(today_str()) - timedelta(days=max(1, days) - 1)
    records: list[dict[str, Any]] = []
    for item in parsed:
        try:
            if date.fromisoformat(str(item["available_online"])) < cutoff:
                continue
        except ValueError:
            continue
        metadata = elsevier_core_metadata(str(item["pii"]), timeout)
        doi = metadata.get("doi") or None
        title = metadata.get("title") or str(item["title"])
        online_date = metadata.get("available_online") or str(item["available_online"])
        records.append(
            article_record(
                journal,
                title=title,
                url=str(item["url"]),
                source="sciencedirect_search",
                source_url=source_url,
                doi=doi,
                authors=item["authors"] if isinstance(item.get("authors"), list) else [],
                published_online=online_date,
                available_online=online_date,
                date_source="sciencedirect_search_available_online",
                date_confidence="B",
                raw_data={
                    "pii": item["pii"],
                    "sciencedirect_search_route": "readonly_proxy",
                    "sciencedirect_search_journal": journal["title"],
                    "sciencedirect_search_issn": journal.get("issn"),
                },
            )
        )
        if len(records) >= max_items:
            break
    return records, f"{journal['title']}: {len(records)} via readonly-proxy"


def fetch_journal(journal: dict[str, Any], *, days: int, timeout: int, max_items: int) -> tuple[list[dict[str, Any]], str]:
    if os.environ.get("ELSEVIER_API_KEY"):
        try:
            return fetch_journal_via_api(
                journal,
                days=days,
                timeout=timeout,
                max_items=max_items,
            )
        except Exception as api_exc:  # noqa: BLE001 - preserve current fallback path.
            try:
                records, message = fetch_journal_via_proxy(
                    journal,
                    days=days,
                    timeout=timeout,
                    max_items=max_items,
                )
            except Exception as proxy_exc:  # noqa: BLE001 - retain both failure layers.
                raise RuntimeError(
                    f"official-api={type(api_exc).__name__}: {api_exc}; "
                    f"readonly-proxy={type(proxy_exc).__name__}: {proxy_exc}"
                ) from proxy_exc
            return records, f"{message}; api_fallback={type(api_exc).__name__}: {api_exc}"
    return fetch_journal_via_proxy(
        journal,
        days=days,
        timeout=timeout,
        max_items=max_items,
    )


def run_journal(
    journal: dict[str, Any],
    *,
    days: int,
    timeout: int,
    max_items: int,
) -> tuple[list[dict[str, Any]], str, Exception | None]:
    """Run one journal and return (records, message, error) for aggregation."""
    try:
        items, message = fetch_journal(
            journal,
            days=days,
            timeout=timeout,
            max_items=max_items,
        )
        return items, message, None
    except Exception as exc:  # noqa: BLE001 - keep the scheduled job moving.
        return [], f"{journal['title']}: {type(exc).__name__}: {exc}", exc


def build_status_message(journal_count: int, failures: int, messages: list[str]) -> str:
    """Compose source-health status with current official-API and proxy capability."""
    jina_key = os.environ.get("JINA_API_KEY") or ""
    elsevier_key = os.environ.get("ELSEVIER_API_KEY") or ""
    return (
        f"journals={journal_count} failures={failures} "
        f"elsevier_api_key={'on' if elsevier_key else 'off'} "
        f"jina_key={'on' if jina_key else 'off'}; " + "; ".join(messages[-12:])
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journals", type=Path, default=DATA_DIR / "journals.yml")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--days", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--max-items-per-journal", type=int, default=15)
    parser.add_argument("--only", default="", help="Comma-separated journal ids")
    args = parser.parse_args()

    only = {item.strip() for item in args.only.split(",") if item.strip()}
    journals = target_journals(load_journals(args.journals), only)
    output = args.output or DATA_DIR / "raw" / "sciencedirect-search" / f"{today_str()}.json"
    records: list[dict[str, Any]] = []
    messages: list[str] = []
    failures = 0

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        for items, message, error in executor.map(
            lambda journal: run_journal(
                journal,
                days=args.days,
                timeout=args.timeout,
                max_items=args.max_items_per_journal,
            ),
            journals,
        ):
            records.extend(items)
            messages.append(message)
            failures += int(error is not None)

    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        pii = str((record.get("raw_data") or {}).get("pii") or record.get("url") or "")
        unique[pii] = record
    records = list(unique.values())
    write_json(output, records)
    record_source(
        "sciencedirect-search",
        ok=bool(records) or failures == 0,
        count=len(records),
        message=build_status_message(len(journals), failures, messages),
    )
    print(f"wrote {len(records)} ScienceDirect search records to {output}")
    for message in messages:
        print(message)


if __name__ == "__main__":
    main()
