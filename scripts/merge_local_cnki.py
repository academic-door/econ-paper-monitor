"""Merge one verified CNKI fetch without rebuilding the global raw index."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import DATA_DIR, read_json, stable_id, today_str, write_json
from dedupe import (
    archive_date_for_new_record,
    build_seen_index,
    enrich_record,
    find_matching_seen_id,
    merge_daily,
    record_match_keys,
    seed_seen_from_daily_match,
)


def iter_daily_matches(daily_dir: Path, record: dict[str, Any]):
    incoming_keys = record_match_keys(record)
    for path in sorted(daily_dir.glob("*.json")):
        records = read_json(path, [])
        if not isinstance(records, list):
            continue
        for index, existing in enumerate(records):
            if isinstance(existing, dict) and incoming_keys & record_match_keys(existing):
                yield path, records, index, existing


def merge_records(records: list[dict[str, Any]], *, seen_path: Path, daily_dir: Path, run_date: str) -> dict[str, int]:
    payload = read_json(seen_path, {"papers": {}})
    seen_papers = payload.setdefault("papers", {})
    seen_index = build_seen_index(seen_papers)
    changed_seen = False
    changed_daily: dict[Path, list[dict[str, Any]]] = {}
    new_count = updated_count = archived_count = 0

    for incoming in records:
        if not isinstance(incoming, dict):
            continue
        record = dict(incoming)
        record.pop("_raw_file", None)
        record_id = stable_id(record)
        record["id"] = record_id
        seen_id = record_id if record_id in seen_papers else find_matching_seen_id(seen_index, record)
        daily_match = next(iter(iter_daily_matches(daily_dir, record)), None)

        if seen_id and seen_id in seen_papers:
            if enrich_record(seen_papers[seen_id], record):
                changed_seen = True
                updated_count += 1
        elif daily_match:
            _, _, _, existing = daily_match
            seen_id = seen_id or record_id
            seen_papers[seen_id] = seed_seen_from_daily_match(record, existing)
            enrich_record(seen_papers[seen_id], record)
            seen_index = build_seen_index(seen_papers)
            changed_seen = True
            new_count += 1
        else:
            seen_papers[record_id] = {
                "title": record.get("title"),
                "journal": record.get("journal"),
                "doi": record.get("doi"),
                "url": record.get("url"),
                "first_seen": record.get("detected_at"),
            }
            enrich_record(seen_papers[record_id], record)
            seen_index = build_seen_index(seen_papers)
            changed_seen = True
            new_count += 1

        if daily_match:
            path, daily_records, _, existing = daily_match
            if enrich_record(existing, record):
                changed_daily[path] = daily_records
                updated_count += 1
            continue

        archive_date = archive_date_for_new_record(record, run_date)
        if archive_date:
            path = daily_dir / f"{archive_date}.json"
            # Multiple newly observed articles can share one Beijing date.
            # Keep the pending merged bucket: re-reading the original file for
            # every item silently discards all but the final article in a run.
            existing = changed_daily.get(path)
            if existing is None:
                existing = read_json(path, [])
            changed_daily[path] = merge_daily(existing if isinstance(existing, list) else [], [record])
            archived_count += 1

    if changed_seen:
        write_json(seen_path, payload)
    for path, daily_records in changed_daily.items():
        write_json(path, daily_records)
    return {"input": len(records), "new": new_count, "updated": updated_count, "archived": archived_count, "seen": len(seen_papers)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--seen", type=Path, default=DATA_DIR / "seen.json")
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--date", default=today_str())
    args = parser.parse_args()
    records = read_json(args.input, [])
    result = merge_records(records if isinstance(records, list) else [], seen_path=args.seen, daily_dir=args.daily_dir, run_date=args.date)
    print(" ".join(f"{key}={value}" for key, value in result.items()))


if __name__ == "__main__":
    main()
