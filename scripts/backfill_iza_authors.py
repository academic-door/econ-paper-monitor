"""Fair bounded scheduler for recent priority working-paper metadata repair.

The source-specific repair implementations remain in ``backfill_iza_authors_legacy``.
This entrypoint only selects eligible records fairly across the already-authorized
IZA, CEPR, and OECD scheduled sources before invoking those existing repairs.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import backfill_iza_authors_legacy as _legacy
from backfill_iza_authors_legacy import *  # noqa: F401,F403
from backfill_iza_authors_legacy import _url_identity


# Keep the public module boundary stable for existing tests/callers that patch
# ``backfill_iza_authors.<dependency>``.  The implementation functions were
# moved unchanged into the legacy helper only to keep this scheduling patch
# small; before invoking them, mirror patchable leaf globals into that module.
_LEGACY_TARGET_DATES = _legacy.target_dates
_LEGACY_QUEUED_OECD_TARGETS = _legacy.queued_oecd_targets
_LEGACY_ENRICH_OECD = _legacy.enrich_oecd_from_readonly_transports
_LEGACY_REPAIR_RECORD = _legacy.repair_record


def _sync_legacy_leaf_globals() -> None:
    for name in (
        "DATA_DIR",
        "today_str",
        "fetch_json",
        "fetch_text",
        "read_json",
        "write_json",
        "load_sources",
        "enrich_record_from_detail",
        "canonical_detail_url",
        "has_oecd_proxy_navigation_contamination",
        "apply_metadata_state",
        "oecd_doi_from_url",
        "parse_oecd_proxy_markdown",
        "first_seen_daily_bucket",
    ):
        setattr(_legacy, name, globals()[name])


def target_dates(days: int) -> set[str]:
    _sync_legacy_leaf_globals()
    return _LEGACY_TARGET_DATES(days)


def queued_oecd_targets() -> dict[str, set[str]]:
    _sync_legacy_leaf_globals()
    return _LEGACY_QUEUED_OECD_TARGETS()


def enrich_oecd_from_readonly_transports(record: dict, *, timeout: int) -> dict:
    _sync_legacy_leaf_globals()
    return _LEGACY_ENRICH_OECD(record, timeout=timeout)


def repair_record(record: dict, source: dict, *, timeout: int) -> tuple[bool, bool, bool]:
    _sync_legacy_leaf_globals()
    original_oecd_fallback = _legacy.enrich_oecd_from_readonly_transports
    _legacy.enrich_oecd_from_readonly_transports = globals()["enrich_oecd_from_readonly_transports"]
    try:
        return _LEGACY_REPAIR_RECORD(record, source, timeout=timeout)
    finally:
        _legacy.enrich_oecd_from_readonly_transports = original_oecd_fallback


def fair_source_candidates(
    candidates: list[tuple[Path, dict, dict]],
    limit: int,
) -> list[tuple[Path, dict, dict]]:
    """Select a bounded deterministic round-robin across sources with pending work."""
    if limit <= 0:
        return []

    by_source: dict[str, list[tuple[Path, dict, dict]]] = {}
    source_order: list[str] = []
    for candidate in candidates:
        source_id = str(candidate[2].get("id") or "")
        if source_id not in by_source:
            by_source[source_id] = []
            source_order.append(source_id)
        by_source[source_id].append(candidate)

    offsets = {source_id: 0 for source_id in source_order}
    selected: list[tuple[Path, dict, dict]] = []
    while len(selected) < limit:
        progressed = False
        for source_id in source_order:
            offset = offsets[source_id]
            queue = by_source[source_id]
            if offset >= len(queue):
                continue
            selected.append(queue[offset])
            offsets[source_id] = offset + 1
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected


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

    payloads: dict[Path, list] = {}
    candidates: list[tuple[Path, dict, dict]] = []
    for path in sorted(args.daily_dir.glob("*.json"), reverse=True):
        is_recent = path.stem in wanted
        oecd_identities = oecd_targets.get(path.stem, set())
        if not is_recent and not oecd_identities:
            continue
        payload = read_json(path, [])
        if not isinstance(payload, list):
            continue
        payloads[path] = payload
        for record in payload:
            if not isinstance(record, dict):
                continue
            source_id = str(record.get("source_id") or "")
            source = sources.get(source_id)
            if source is None or not record.get("url") or not needs_scheduled_repair(record, source_id):
                continue
            if not is_recent:
                identity = _url_identity(record)
                if source_id != "oecd-working-papers" or identity not in oecd_identities:
                    continue
            candidates.append((path, record, source))

    changed_paths: set[Path] = set()
    for path, record, source in fair_source_candidates(candidates, args.limit):
        source_id = str(source.get("id") or "")
        checked += 1
        per_source_checked[source_id] += 1
        record_changed, authors_added, abstract_added = repair_record(record, source, timeout=args.timeout)
        if not record_changed:
            continue

        changed_paths.add(path)
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

    for path in sorted(changed_paths):
        write_json(path, payloads[path])
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
