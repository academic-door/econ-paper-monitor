"""Fetch American Economic Association journal TOC pages.

AEA does not expose the simple RSS endpoints used by ScienceDirect/Wiley.
Their current-issue and forthcoming pages are stable enough to serve as a
fast-path source for AER, AEJ journals, JEP, JEL, and P&P.
"""

from __future__ import annotations

import argparse
import html
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from common import DATA_DIR, fetch_text, filter_journals_by_tier, load_journals, normalize_doi, now_iso, read_json, today_str, write_json
from sources.record import article_record
from status import record_source


BASE = "https://www.aeaweb.org"
SNAPSHOT_PATH = DATA_DIR / "aea_forthcoming_snapshot.json"
SENTINEL_PATH = DATA_DIR / "external_sentinel_alohomora.json"

AEA_CODES = {
    "american-economic-review": "aer",
    "american-economic-review-insights": "aeri",
    "journal-of-economic-literature": "jel",
    "journal-of-economic-perspectives": "jep",
    "american-economic-journal-applied-economics": "app",
    "american-economic-journal-economic-policy": "pol",
    "american-economic-journal-macroeconomics": "mac",
    "american-economic-journal-microeconomics": "mic",
    "american-economic-review-papers-and-proceedings": "pandp",
}


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(html.unescape(value))
    return re.sub(r"\s+", " ", value).strip()


def meta_values(html_text: str, name: str) -> list[str]:
    values: list[str] = []
    patterns = [
        rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']{re.escape(name)}["\']',
    ]
    for pattern in patterns:
        values.extend(clean_text(match) for match in re.findall(pattern, html_text, flags=re.I | re.S))
    return [value for value in values if value]


def parse_year_month(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(20\d{2})[/-](\d{1,2})(?:[/-](\d{1,2}))?", value)
    if not match:
        return None
    day = int(match.group(3) or 1)
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{day:02d}"


def article_links(html_text: str) -> list[tuple[str, str]]:
    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    pattern = r'<a[^>]+href=["\'](?P<href>/articles\?id=10\.1257/[^"\']+)["\'][^>]*>(?P<title>.*?)</a>'
    for match in re.finditer(pattern, html_text, flags=re.I | re.S):
        href = html.unescape(match.group("href")).replace("&amp;", "&")
        doi_match = re.search(r"[?&]id=(10\.1257/[^&#]+)", href, flags=re.I)
        if not doi_match:
            continue
        href = f"/articles?id={doi_match.group(1)}"
        title = clean_text(match.group("title"))
        if not title or title.casefold() == "front matter":
            continue
        url = urljoin(BASE, href)
        if url in seen:
            continue
        seen.add(url)
        links.append((url, title))
    return links


def doi_from_article_url(url: str) -> str | None:
    match = re.search(r"[?&]id=(10\.1257/[^&#]+)", url, flags=re.I)
    return normalize_doi(match.group(1)) if match else None


def sentinel_promotions(path: Path, run_date: str) -> dict[str, dict]:
    payload = read_json(path, {})
    items: list = []
    if isinstance(payload, dict):
        for key in ("missing_in_monitor_list", "missing_sample"):
            values = payload.get(key)
            if isinstance(values, list):
                items.extend(values)
    promotions: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict) or str(item.get("external_first_seen_date") or "") != run_date:
            continue
        doi = doi_from_article_url(str(item.get("link") or ""))
        if doi and doi.startswith("10.1257/"):
            promotions[doi] = item
    return promotions


def annotate_snapshot_records(
    records: list[dict],
    journal_id: str,
    snapshot: dict[str, list[str]],
    promotions: dict[str, dict],
) -> None:
    if not records:
        return
    existing = set(snapshot.get(journal_id) or [])
    initialized = journal_id in snapshot
    for record in records:
        doi = normalize_doi(record.get("doi")) or doi_from_article_url(str(record.get("url") or ""))
        key = doi or str(record.get("url") or "")
        if not key:
            continue
        promotion = promotions.get(doi or "")
        first_observed = key not in existing and (initialized or promotion is not None)
        raw_data = record.setdefault("raw_data", {})
        raw_data["aea_first_observed"] = first_observed
        raw_data["aea_snapshot_baseline"] = not initialized and promotion is None
        if promotion:
            raw_data["aea_external_first_seen_date"] = promotion.get("external_first_seen_date")
            if not record.get("authors") and promotion.get("author"):
                record["authors"] = [part.strip() for part in str(promotion["author"]).split(",") if part.strip()]
        existing.add(key)
    snapshot[journal_id] = sorted(existing)


def enrich_article(url: str, fallback_title: str, timeout: int) -> dict[str, object]:
    try:
        html_text = fetch_text(url, timeout=timeout)
    except Exception:
        return {
            "title": fallback_title,
            "authors": [],
            "doi": None,
            "issue_date": None,
            "source_issue": None,
        }
    title = (meta_values(html_text, "citation_title") or [fallback_title])[0]
    authors = meta_values(html_text, "citation_author")[:12]
    doi = normalize_doi((meta_values(html_text, "citation_doi") or [None])[0])
    pub_date = parse_year_month((meta_values(html_text, "citation_publication_date") or [None])[0])
    abstract = (meta_values(html_text, "citation_abstract") or [None])[0]
    return {
        "title": title,
        "authors": authors,
        "doi": doi,
        "issue_date": pub_date,
        "source_issue": (meta_values(html_text, "citation_journal_title") or [None])[0],
        "abstract": abstract,
    }


def bounded_timeout(timeout: int, deadline: float | None) -> int:
    """Never start an HTTP call that could cross the overall AEA time budget."""
    if deadline is None:
        return timeout
    return max(0, min(timeout, int(deadline - time.monotonic())))


def fetch_journal(
    journal: dict,
    code: str,
    *,
    timeout: int,
    detail_limit: int,
    max_items: int,
    deadline: float | None = None,
) -> tuple[list[dict], list[str]]:
    records: list[dict] = []
    errors: list[str] = []
    pages = [
        ("forthcoming", f"{BASE}/journals/{code}/forthcoming"),
        ("current-issue", f"{BASE}/journals/{code}/current-issue"),
    ]
    seen_urls: set[str] = set()
    for page_kind, page_url in pages:
        request_timeout = bounded_timeout(timeout, deadline)
        if not request_timeout:
            errors.append(f"{page_kind}: BUDGET_EXHAUSTED (not observed)")
            continue
        try:
            html_text = fetch_text(page_url, timeout=request_timeout)
        except Exception as exc:
            errors.append(f"{page_kind}: {type(exc).__name__}: {exc}")
            continue
        page_count = 0
        for url, title in article_links(html_text):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            detail = {"title": title}
            if len(records) < detail_limit:
                detail_timeout = bounded_timeout(timeout, deadline)
                if detail_timeout:
                    detail = enrich_article(url, title, detail_timeout)
                else:
                    errors.append(f"{page_kind}: DETAIL_BUDGET_EXHAUSTED (metadata not observed)")
            doi = detail.get("doi") if isinstance(detail.get("doi"), str) else doi_from_article_url(url)
            records.append(
                article_record(
                    journal,
                    title=str(detail.get("title") or title),
                    url=url,
                    source="aea_toc",
                    source_url=page_url,
                    doi=doi,
                    authors=detail.get("authors") if isinstance(detail.get("authors"), list) else [],
                    abstract=detail.get("abstract") if isinstance(detail.get("abstract"), str) else None,
                    issue_date=detail.get("issue_date") if isinstance(detail.get("issue_date"), str) else None,
                    source_issue=detail.get("source_issue") if isinstance(detail.get("source_issue"), str) else None,
                    date_source="aea_forthcoming" if page_kind == "forthcoming" else "aea_current_issue",
                    date_confidence="A" if page_kind == "forthcoming" else "C",
                    raw_data={"aea_code": code, "aea_page": page_kind},
                )
            )
            page_count += 1
            if page_count >= max_items:
                break
    return records, errors


def journal_status_entry(records: list[dict], errors: list[str]) -> dict[str, object]:
    """Keep a journal usable when one optional AEA page is unavailable."""
    return {
        "ok": bool(records) or not errors,
        "count": len(records),
        "message": "; ".join(errors) if errors else "ok",
        # Source transport or partial records never establish official roster parity.
        "roster_verification": "VERIFICATION_DUE" if errors else "NOT_RECONCILED",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journals", type=Path, default=DATA_DIR / "journals.yml")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--tier", default=None)
    parser.add_argument("--only", default=None, help="Comma-separated journal ids to fetch.")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--detail-limit", type=int, default=8)
    parser.add_argument("--max-items-per-journal", type=int, default=30)
    parser.add_argument(
        "--budget-seconds", type=int, default=0,
        help="Cooperative wall-clock ceiling below the workflow's hard timeout (0 disables).",
    )
    args = parser.parse_args()
    if args.budget_seconds < 0:
        parser.error("--budget-seconds must be nonnegative")
    deadline = time.monotonic() + args.budget_seconds if args.budget_seconds else None

    output = args.output or DATA_DIR / "raw" / "aea" / f"{today_str()}.json"
    run_date = today_str()
    snapshot_payload = read_json(SNAPSHOT_PATH, {"journals": {}})
    snapshot = snapshot_payload.get("journals") if isinstance(snapshot_payload, dict) else None
    if not isinstance(snapshot, dict):
        snapshot = {}
    promotions = sentinel_promotions(SENTINEL_PATH, run_date)
    records: list[dict] = []
    messages: list[str] = []
    journal_status: dict[str, dict[str, object]] = {}
    failures = 0
    journals = filter_journals_by_tier(load_journals(args.journals), args.tier)
    if args.only:
        selected = {value.strip() for value in args.only.split(",") if value.strip()}
        journals = [journal for journal in journals if str(journal.get("id") or "") in selected]
    # Round-robin starts ensure one slow journal cannot starve later official sources.
    start_index = int(snapshot_payload.get("next_journal_index") or 0) % len(journals) if journals else 0
    journals = journals[start_index:] + journals[:start_index]
    resume_index = None
    for position, journal in enumerate(journals):
        code = AEA_CODES.get(str(journal.get("id") or ""))
        if not code:
            continue
        if deadline is not None and bounded_timeout(args.timeout, deadline) == 0:
            fetched, errors = [], ["not_attempted: BUDGET_EXHAUSTED (not observed)"]
        else:
            fetched, errors = fetch_journal(
                journal,
                code,
                timeout=args.timeout,
                detail_limit=args.detail_limit,
                max_items=args.max_items_per_journal,
                deadline=deadline,
            )
        if resume_index is None and any("BUDGET_EXHAUSTED" in error for error in errors):
            resume_index = (start_index + position) % len(journals)
        annotate_snapshot_records(fetched, str(journal.get("id") or ""), snapshot, promotions)
        records.extend(fetched)
        journal_id = str(journal.get("id") or "")
        journal_status[journal_id] = journal_status_entry(fetched, errors)
        if errors:
            failures += len(errors)
            messages.append(f"{journal.get('id')}: {len(fetched)} ({'; '.join(errors)})")
        else:
            messages.append(f"{journal.get('id')}: {len(fetched)}")

    write_json(output, records)
    # Only actually observed identities enter the snapshot; skipped journals
    # remain unverified. Retrying begins with the first budget-limited journal.
    next_index = resume_index if resume_index is not None else ((start_index + 1) % len(journals) if journals else 0)
    write_json(SNAPSHOT_PATH, {
        "updated_at": now_iso(),
        "journals": snapshot,
        "next_journal_index": next_index,
    })
    record_source(
        "aea-toc",
        # A failed optional page must not turn a successful partial harvest
        # into an operational outage. The per-journal entries retain the
        # exact failure message for audit purposes.
        ok=failures == 0 or bool(records),
        count=len(records),
        message="; ".join(messages) or str(output),
        details={
            "journals": journal_status,
            "partial_failures": failures,
            "roster_verification": "VERIFICATION_DUE" if failures else "NOT_RECONCILED",
            "budget_seconds": args.budget_seconds,
        },
    )
    print(f"wrote {len(records)} AEA records to {output}")
    for message in messages:
        print(message)


if __name__ == "__main__":
    main()
