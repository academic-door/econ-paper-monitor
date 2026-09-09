"""Scheduled backfill for recent priority working-paper metadata.

The filename is retained because the production workflow already invokes this
entrypoint. It repairs recent IZA and CEPR records from their official detail
pages without changing first-discovery timestamps or Daily bucket identity.
"""

from __future__ import annotations

import argparse
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

from common import DATA_DIR, read_json, today_str, write_json
from fetch_preprints import enrich_record_from_detail, load_sources


SCHEDULED_SOURCE_IDS = {"iza", "cepr-dp"}


def target_dates(days: int) -> set[str]:
    today = date.fromisoformat(today_str())
    return {(today - timedelta(days=offset)).isoformat() for offset in range(max(days, 1))}


def canonical_detail_url(source_id: str, url: str) -> str:
    """Canonicalize known legacy CEPR detail paths; leave other URLs untouched."""
    if source_id != "cepr-dp":
        return url
    parsed = urlparse(url)
    decoded_path = unquote(parsed.path)
    match = re.search(r"/publications/dp\d+", decoded_path, flags=re.I)
    if not match:
        return url
    canonical_path = match.group(0)
    return parsed._replace(path=canonical_path, query="", fragment="").geturl().rstrip("/")


def normalize_authors(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in str(value or "").split(";"):
            name = " ".join(part.split())
            key = name.casefold()
            if name and key not in seen:
                normalized.append(name)
                seen.add(key)
    return normalized[:12]


def needs_scheduled_repair(record: dict, source_id: str) -> bool:
    """Return whether the scheduled source contract has an actionable metadata gap."""
    missing_authors = not record.get("authors")
    missing_abstract = not str(record.get("abstract") or "").strip()
    if source_id == "iza":
        return missing_authors
    if source_id == "cepr-dp":
        return missing_authors or missing_abstract
    return False


def apply_metadata_state(record: dict) -> None:
    if record.get("authors"):
        record["authors"] = normalize_authors(list(record["authors"]))
        record["authors_status"] = None
        record["authors_status_code"] = "available"
    if str(record.get("abstract") or "").strip():
        record["abstract_completeness"] = "full"
        record["abstract_status"] = None
        record["abstract_status_code"] = "available"
        record["abstract_enrichment_status"] = "available"


def repair_record(record: dict, source: dict, *, timeout: int) -> tuple[bool, bool, bool]:
    """Repair one scheduled record and report changed/author/abstract enrichment."""
    source_id = str(source.get("id") or "")
    before_authors = list(record.get("authors") or [])
    before_abstract = str(record.get("abstract") or "")
    before_url = str(record.get("url") or "")
    original_title = record.get("title")

    canonical_url = canonical_detail_url(source_id, before_url)
    if canonical_url != before_url:
        record["url"] = canonical_url

    updated = enrich_record_from_detail(record, source, timeout=timeout)
    if original_title:
        updated["title"] = original_title
    apply_metadata_state(updated)

    after_authors = list(updated.get("authors") or [])
    after_abstract = str(updated.get("abstract") or "")
    after_url = str(updated.get("url") or "")
    changed = (
        after_authors != before_authors
        or after_abstract != before_abstract
        or after_url != before_url
    )
    authors_enriched = bool(after_authors and after_authors != before_authors)
    abstract_enriched = bool(after_abstract.strip() and after_abstract != before_abstract)
    return changed, authors_enriched, abstract_enriched


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--seen", type=Path, default=DATA_DIR / "seen.json")
    parser.add_argument("--sources", type=Path, default=DATA_DIR / "working_paper_sources.yml")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()

    sources = {
        str(source.get("id") or ""): source
        for source in load_sources(args.sources)
        if str(source.get("id") or "") in SCHEDULED_SOURCE_IDS
    }
    if not sources:
        print("Scheduled working-paper source definitions not found")
        return

    wanted = target_dates(args.days)
    changed_files = 0
    checked = 0
    enriched = 0
    abstracts_enriched = 0
    per_source_checked = {source_id: 0 for source_id in sources}
    per_source_enriched = {source_id: 0 for source_id in sources}
    per_source_abstracts_enriched = {source_id: 0 for source_id in sources}

    seen_payload = read_json(args.seen, {"papers": {}})
    papers = seen_payload.get("papers") if isinstance(seen_payload, dict) else {}
    seen_changed = False

    for path in sorted(args.daily_dir.glob("*.json"), reverse=True):
        if path.stem not in wanted or checked >= args.limit:
            continue
        payload = read_json(path, [])
        if not isinstance(payload, list):
            continue
        changed = False
        for record in payload:
            if checked >= args.limit:
                break
            source_id = str(record.get("source_id") or "")
            source = sources.get(source_id)
            if source is None or not record.get("url") or not needs_scheduled_repair(record, source_id):
                continue

            checked += 1
            per_source_checked[source_id] += 1
            record_changed, authors_added, abstract_added = repair_record(record, source, timeout=args.timeout)
            if not record_changed:
                continue

            changed = True
            if authors_added:
                enriched += 1
                per_source_enriched[source_id] += 1
            if abstract_added:
                abstracts_enriched += 1
                per_source_abstracts_enriched[source_id] += 1

            seen_key = str(record.get("id") or "")
            if seen_key and isinstance(papers, dict) and seen_key in papers:
                papers[seen_key].update(record)
                seen_changed = True

        if changed:
            write_json(path, payload)
            changed_files += 1

    if isinstance(papers, dict) and seen_changed:
        seen_payload["papers"] = papers
        write_json(args.seen, seen_payload)

    details = ", ".join(
        f"{source_id}:checked={per_source_checked[source_id]}/enriched={per_source_enriched[source_id]}"
        f"/abstracts={per_source_abstracts_enriched[source_id]}"
        for source_id in sorted(sources)
    )
    print(
        f"working-paper metadata backfill: checked={checked} enriched={enriched} "
        f"abstracts_enriched={abstracts_enriched} files={changed_files}; {details}"
    )


if __name__ == "__main__":
    main()
