"""Remove known navigation/editorial boilerplate from public archives."""

from __future__ import annotations

import argparse
import copy
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DATA_DIR, read_json, write_json
from dedupe import is_source_navigation_noise, record_match_keys


def quarantine_identity(record: dict[str, Any]) -> str:
    for prefix in ("doi:", "url:", "journal-title:", "source-title:"):
        value = next((key for key in sorted(record_match_keys(record)) if key.startswith(prefix)), None)
        if value:
            return value
    return str(record.get("detail_key") or record.get("id") or record.get("title") or "").casefold()


def clean_nonpaper_records(daily_dir: Path, seen_path: Path, ledger_path: Path) -> dict[str, int]:
    removed: dict[str, dict[str, Any]] = {}
    removed_daily = 0
    for path in daily_dir.glob("*.json"):
        payload = read_json(path, [])
        if not isinstance(payload, list):
            continue
        kept = []
        for record in payload:
            if isinstance(record, dict) and is_source_navigation_noise(record):
                item = copy.deepcopy(record)
                item["canonical_daily_date"] = path.stem
                removed.setdefault(quarantine_identity(item), item)
                removed_daily += 1
            else:
                kept.append(record)
        if len(kept) != len(payload):
            write_json(path, kept)

    seen = read_json(seen_path, {})
    removed_seen = 0
    if isinstance(seen, dict) and isinstance(seen.get("papers"), dict):
        kept_papers = {}
        for record_id, record in seen["papers"].items():
            if isinstance(record, dict) and is_source_navigation_noise(record):
                removed.setdefault(quarantine_identity(record), copy.deepcopy(record))
                removed_seen += 1
            else:
                kept_papers[record_id] = record
        if removed_seen:
            seen["papers"] = kept_papers
            write_json(seen_path, seen)
    elif isinstance(seen, list):
        kept_seen = []
        for record in seen:
            if isinstance(record, dict) and is_source_navigation_noise(record):
                removed.setdefault(quarantine_identity(record), copy.deepcopy(record))
                removed_seen += 1
            else:
                kept_seen.append(record)
        if removed_seen:
            write_json(seen_path, kept_seen)

    ledger_added = 0
    if removed:
        ledger = read_json(ledger_path, {"records": []})
        if not isinstance(ledger, dict):
            ledger = {"records": []}
        records = ledger.setdefault("records", [])
        existing = {
            quarantine_identity(record): record
            for record in records
            if isinstance(record, dict) and quarantine_identity(record)
        }
        removed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        ledger_updated = 0
        for identity, record in sorted(removed.items()):
            if not identity:
                continue
            audit_fields = {
                "seen": False,
                "duplicate": False,
                "stage": "source_rule",
                "reason": "publisher navigation or non-article source noise",
                "exclusion_status": "confirmed_nonpaper",
                "removed_from_canonical_at": removed_at,
            }
            prior = existing.get(identity)
            if prior is not None:
                if prior.get("exclusion_status") != "confirmed_nonpaper":
                    # A raw candidate can already be in the ledger as a
                    # duplicate of seen. Upgrade that same identity to an
                    # audited source exclusion without losing raw provenance.
                    prior.setdefault("prior_exclusion_reason", prior.get("reason"))
                    prior.setdefault("prior_exclusion_stage", prior.get("stage"))
                    prior.update(record)
                    prior.update(audit_fields)
                    ledger_updated += 1
                continue
            record.update(audit_fields)
            records.append(record)
            existing[identity] = record
            ledger_added += 1
        if ledger_added or ledger_updated:
            ledger["excluded_count"] = len(records)
            ledger["candidate_count"] = max(int(ledger.get("candidate_count") or 0), len(records))
            ledger["reason_counts"] = dict(
                sorted(Counter(str(record.get("reason") or "unknown") for record in records).items())
            )
            write_json(ledger_path, ledger)
    return {"daily": removed_daily, "seen": removed_seen, "ledger_added": ledger_added}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--seen", type=Path, default=DATA_DIR / "seen.json")
    parser.add_argument("--ledger", type=Path, default=DATA_DIR / "ingestion_exclusion_ledger.json")
    args = parser.parse_args()
    report = clean_nonpaper_records(args.daily_dir, args.seen, args.ledger)
    print(
        f"non-paper cleanup: daily={report['daily']} seen={report['seen']} "
        f"ledger_added={report['ledger_added']}"
    )


if __name__ == "__main__":
    main()
