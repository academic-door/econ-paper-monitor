"""Apply manual metadata corrections to daily archives and seen.json."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import DATA_DIR, normalize_doi, parse_scalar, read_json, stable_id, write_json
from status import record_source


def record_key(record: dict[str, Any]) -> str | None:
    doi = normalize_doi(record.get("doi"))
    if doi:
        return doi
    return record.get("id") or stable_id(record)


def apply_to_record(record: dict[str, Any], override: dict[str, Any]) -> bool:
    changed = False
    china_related_value = override.get("china_related")
    if isinstance(china_related_value, str):
        china_related_value = china_related_value.strip().casefold() == "true"
    field_map = {
        "title_zh": "title_zh",
        "abstract": "abstract",
        "abstract_zh": "abstract_zh",
        "abstract_source": "abstract_source",
        "abstract_status": "abstract_status",
        "china_related": "china_related",
        "china_reason": "china_reason",
        "date_confidence": "date_confidence",
        "date_source": "date_source",
        "accepted_date": "accepted_date",
        "available_online": "available_online",
        "published_online": "published_online",
        "authors": "authors",
        "authors_status": "authors_status",
        "doi": "doi",
        "pii": "pii",
    }
    for source_key, target_key in field_map.items():
        if source_key not in override:
            continue
        value = override[source_key]
        if source_key == "china_related" and isinstance(value, str):
            value = value.strip().casefold() == "true"
        if record.get(target_key) != value:
            record[target_key] = value
            changed = True
    # A verified abstract must not retain the prior missing/retry UI status.
    # This only applies when the manual override explicitly supplies nonempty
    # abstract text; never infer availability from a publisher URL alone.
    if isinstance(override.get("abstract"), str) and override["abstract"].strip():
        for field, value in (
            ("abstract_status_code", "available"),
            ("abstract_completeness", "full"),
            ("abstract_enrichment_status", "available"),
        ):
            if record.get(field) != value:
                record[field] = value
                changed = True
        for stale_field in ("abstract_status", "abstract_truncated"):
            if stale_field in record:
                record.pop(stale_field)
                changed = True
    if override.get("title_zh") and record.get("translation_status") != "manual_title":
        record["translation_status"] = "manual_title"
        changed = True
    if china_related_value is True and record.get("china_related_source") != "manual":
        record["china_related_source"] = "manual"
        changed = True
    if china_related_value is True:
        if record.get("china_relevance_status") != "confirmed":
            record["china_relevance_status"] = "confirmed"
            changed = True
        reason = override.get("china_reason")
        if reason and record.get("china_relevance_reason") != reason:
            record["china_relevance_reason"] = reason
            changed = True
    elif china_related_value is False:
        if record.get("china_relevance_status") != "none":
            record["china_relevance_status"] = "none"
            changed = True
        reason = override.get("china_reason")
        if reason and record.get("china_relevance_reason") != reason:
            record["china_relevance_reason"] = reason
            changed = True
    return changed


def daily_paths(daily_dir: Path, date_filter: str | None) -> list[Path]:
    if date_filter:
        path = daily_dir / f"{date_filter}.json"
        return [path] if path.exists() else []
    return sorted(daily_dir.glob("*.json"))


def load_overrides(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    current_key: str | None = None
    current_list_field: str | None = None
    in_records = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped == "records:":
            in_records = True
            continue
        if not in_records:
            continue
        if line.startswith("  ") and not line.startswith("    "):
            key = stripped.rstrip(":").strip('"')
            records[key] = {}
            current_key = key
            current_list_field = None
            continue
        if current_key and current_list_field and line.startswith("      - "):
            records[current_key][current_list_field].append(parse_scalar(stripped[2:].strip()))
            continue
        if current_key and line.startswith("    "):
            field, _, value = stripped.partition(":")
            field = field.strip()
            if not value.strip():
                records[current_key][field] = []
                current_list_field = field
            else:
                records[current_key][field] = parse_scalar(value.strip())
                current_list_field = None
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overrides", type=Path, default=DATA_DIR / "manual_overrides.yml")
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--seen", type=Path, default=DATA_DIR / "seen.json")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    overrides = load_overrides(args.overrides)
    changed = 0
    touched = 0
    for path in daily_paths(args.daily_dir, args.date):
        records = read_json(path, [])
        path_changed = False
        for record in records:
            key = record_key(record)
            override = overrides.get(key or "")
            if isinstance(override, dict) and apply_to_record(record, override):
                changed += 1
                path_changed = True
        if path_changed:
            write_json(path, records)
            touched += 1

    seen = read_json(args.seen, {"papers": {}})
    for key, override in overrides.items():
        if not isinstance(override, dict):
            continue
        normalized_key = normalize_doi(key)
        seen_key = f"doi:{normalized_key}" if normalized_key and normalized_key.startswith("10.") else key
        entry = seen.get("papers", {}).get(seen_key)
        if isinstance(entry, dict) and apply_to_record(entry, override):
            changed += 1
    write_json(args.seen, seen)
    record_source("manual-overrides", ok=True, count=changed, message=f"files={touched}")
    print(f"manual overrides changed={changed} files={touched}")


if __name__ == "__main__":
    main()
