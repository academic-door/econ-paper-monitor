"""Scheduled backfill for recent priority working-paper metadata.

The filename is retained because the production workflow already invokes this
entrypoint. It repairs recent IZA, CEPR, and OECD records from their official
detail pages without changing first-discovery timestamps or Daily bucket identity.
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

from common import BEIJING_TZ, DATA_DIR, read_json, today_str, write_json
from fetch_preprints import enrich_record_from_detail, enrich_record_from_proxy, load_sources


SCHEDULED_SOURCE_IDS = {"iza", "cepr-dp", "oecd-working-papers"}


def target_dates(days: int) -> set[str]:
    today = date.fromisoformat(today_str())
    return {(today - timedelta(days=offset)).isoformat() for offset in range(max(days, 1))}


def first_seen_daily_bucket(value: object) -> str | None:
    """Map a UTC/offset first_seen timestamp to the canonical Beijing Daily day."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return observed.astimezone(BEIJING_TZ).date().isoformat()


def queued_oecd_targets() -> dict[str, set[str]]:
    """Return retry-queued OECD identities keyed by their canonical Daily bucket."""
    payload = read_json(DATA_DIR / "metadata_retry_queue.json", {"records": []})
    records = payload.get("records") if isinstance(payload, dict) else []
    targets: dict[str, set[str]] = {}
    if not isinstance(records, list):
        return targets

    for entry in records:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("source_id") or "") != "oecd-working-papers":
            continue
        if bool(entry.get("historical_backfill")):
            continue
        identity = str(entry.get("identity") or "").strip().casefold()
        bucket = first_seen_daily_bucket(entry.get("first_seen"))
        if not identity or not bucket:
            continue
        targets.setdefault(bucket, set()).add(identity)
    return targets


def canonical_detail_url(source_id: str, url: str) -> str:
    """Canonicalize known legacy detail paths; leave unrelated URLs untouched."""
    parsed = urlparse(url)
    if source_id == "oecd-working-papers":
        host = parsed.netloc.casefold()
        if host in {"oecd-ilibrary.org", "www.oecd-ilibrary.org"} and parsed.path.startswith("/en/publications/"):
            return parsed._replace(scheme="https", netloc="www.oecd.org", query="", fragment="").geturl().rstrip("/")
        return url
    if source_id != "cepr-dp":
        return url
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
    if source_id in {"cepr-dp", "oecd-working-papers"}:
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
    if source_id == "cepr-dp" and not str(updated.get("abstract") or "").strip():
        updated = enrich_record_from_proxy(updated, source_id, timeout=timeout)
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


def _url_identity(record: dict) -> str:
    return f"url:{str(record.get('url') or '').strip().casefold()}"


def durable_oecd_seen_targets(papers: dict) -> dict[str, set[str]]:
    """Select only durable OECD double-gaps that match the bounded #173 repair scope.

    The rolling retry queue can legitimately age out old records.  ``seen`` is
    the durable first-discovery identity store, so an accepted OECD record that
    still has weak/unknown date evidence and is missing both authors and abstract
    remains recoverable even when it is no longer present in today's retry queue.
    Records with partial metadata (for example authors already known) are not
    promoted into this bounded recovery lane.
    """
    targets: dict[str, set[str]] = {}
    if not isinstance(papers, dict):
        return targets
    for record in papers.values():
        if not isinstance(record, dict):
            continue
        if str(record.get("source_id") or "") != "oecd-working-papers":
            continue
        if str(record.get("source") or "") not in {"", "working_papers"}:
            continue
        if str(record.get("source_type") or "") not in {"", "policy_paper"}:
            continue
        if record.get("authors") or str(record.get("abstract") or "").strip():
            continue
        if str(record.get("date_confidence") or "") not in {"", "F", "unknown"}:
            continue
        identity = _url_identity(record)
        bucket = first_seen_daily_bucket(record.get("first_seen"))
        if identity == "url:" or not bucket:
            continue
        targets.setdefault(bucket, set()).add(identity)
    return targets


def merge_oecd_targets(*target_maps: dict[str, set[str]]) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for mapping in target_maps:
        for bucket, identities in mapping.items():
            merged.setdefault(bucket, set()).update(identities)
    return merged


def restore_oecd_targets_from_seen(
    *,
    daily_dir: Path,
    papers: dict,
    oecd_targets: dict[str, set[str]],
) -> set[Path]:
    """Restore accepted first-discovery OECD rows lost from Daily but retained in seen."""
    restored_paths: set[Path] = set()
    if not oecd_targets or not isinstance(papers, dict):
        return restored_paths

    for bucket, identities in oecd_targets.items():
        path = daily_dir / f"{bucket}.json"
        payload = read_json(path, [])
        if not isinstance(payload, list):
            continue
        existing_ids = {str(item.get("id") or "") for item in payload if isinstance(item, dict)}
        existing_urls = {_url_identity(item) for item in payload if isinstance(item, dict)}
        file_changed = False

        for seen_record in papers.values():
            if not isinstance(seen_record, dict):
                continue
            if str(seen_record.get("source_id") or "") != "oecd-working-papers":
                continue
            if first_seen_daily_bucket(seen_record.get("first_seen")) != bucket:
                continue
            identity = _url_identity(seen_record)
            record_id = str(seen_record.get("id") or "")
            if identity not in identities:
                continue
            if (record_id and record_id in existing_ids) or identity in existing_urls:
                continue
            restored = dict(seen_record)
            payload.append(restored)
            if record_id:
                existing_ids.add(record_id)
            existing_urls.add(identity)
            file_changed = True

        if file_changed:
            write_json(path, payload)
            restored_paths.add(path)

    return restored_paths


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
    papers = papers if isinstance(papers, dict) else {}
    seen_changed = False

    oecd_targets = merge_oecd_targets(
        queued_oecd_targets(),
        durable_oecd_seen_targets(papers),
    )
    restored_paths = restore_oecd_targets_from_seen(
        daily_dir=args.daily_dir,
        papers=papers,
        oecd_targets=oecd_targets,
    )
    changed_files += len(restored_paths)

    for path in sorted(args.daily_dir.glob("*.json"), reverse=True):
        is_recent = path.stem in wanted
        oecd_identities = oecd_targets.get(path.stem, set())
        if (not is_recent and not oecd_identities) or checked >= args.limit:
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
            if not is_recent:
                identity = _url_identity(record)
                if source_id != "oecd-working-papers" or identity not in oecd_identities:
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
            if seen_key and seen_key in papers:
                papers[seen_key].update(record)
                seen_changed = True

        if changed:
            write_json(path, payload)
            if path not in restored_paths:
                changed_files += 1

    if seen_changed:
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
