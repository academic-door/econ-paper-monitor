"""Block a public release when discovery correctness is not proven.

Metadata gaps such as a missing abstract are reported for later enrichment;
they do not block the site.  Discovery-integrity failures do block publishing
because they can make the public "today" view misleading.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from clean_historical_working_papers import (
    has_first_discovery_anchor,
    is_historical_cepr,
    is_historical_record,
)
from common import DATA_DIR, read_json, today_str, write_json
from dedupe import record_match_keys


def valid_date_value(field: str, value: Any) -> bool:
    text = str(value or "").strip()
    if field == "issue_date" and re.fullmatch(r"\d{4}-\d{2}", text):
        return True
    try:
        return len(text) == 10 and date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path, [])
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("papers"), dict):
        return [dict(item) for item in payload["papers"].values() if isinstance(item, dict)]
    return []


def load_canonical_daily(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not path.exists():
        return [], "canonical_daily_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [], "canonical_daily_invalid"
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        return [], "canonical_daily_invalid"
    return [dict(item) for item in payload], None


def record_key(record: dict[str, Any]) -> str:
    return str(record.get("doi") or record.get("url") or record.get("id") or "").strip().casefold()


def is_working(record: dict[str, Any]) -> bool:
    return str(record.get("source") or "") == "working_papers" or str(record.get("source_type") or "") in {
        "working_paper", "policy_paper", "aggregator"
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    today = args.date or today_str()
    daily, canonical_error = load_canonical_daily(args.daily_dir / f"{today}.json")
    quality = read_json(args.quality_report, {})
    ingestion = read_json(args.ingestion_audit, {})
    formal = read_json(args.formal_audit, {})
    source_health = read_json(getattr(args, "source_health", DATA_DIR / "source_health.json"), {})
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if canonical_error:
        failures.append({"code": canonical_error, "count": 1})

    if not isinstance(source_health, dict) or not isinstance(source_health.get("counts"), dict) or not source_health.get("checked_at"):
        failures.append({"code": "formal_source_health_missing_or_invalid", "count": 1})

    source_counts = source_health.get("counts") or {}
    coverage_counts = source_health.get("coverage_counts") or {}
    unavailable = int(source_counts.get("unavailable") or 0)
    stale = int(source_counts.get("stale") or 0)
    if unavailable:
        failures.append({"code": "formal_sources_unavailable", "count": unavailable})
    if stale:
        failures.append({"code": "formal_sources_stale", "count": stale})
    crossref_only = int(coverage_counts.get("crossref_only") or 0)
    supplemental = int(coverage_counts.get("supplemental") or 0)
    degraded = int(source_counts.get("degraded") or 0)
    if crossref_only:
        warnings.append({"code": "formal_sources_crossref_only", "count": crossref_only})
    if supplemental:
        warnings.append({"code": "formal_sources_crossref_plus_recall", "count": supplemental})
    elif degraded:
        warnings.append({"code": "formal_sources_single_path", "count": degraded})

    strong_key_owner: dict[str, int] = {}
    duplicate_keys: set[str] = set()
    for index, record in enumerate(daily):
        keys = {
            key
            for key in record_match_keys(record)
            if key.startswith(("doi:", "url:", "urlpaper:", "journal-title:", "working-title:"))
        }
        for key in keys:
            owner = strong_key_owner.setdefault(key, index)
            if owner != index:
                duplicate_keys.add(key)
    duplicates = sorted(duplicate_keys)
    if duplicates:
        failures.append({"code": "duplicate_public_records", "count": len(duplicates), "examples": duplicates[:10]})

    missing = int(ingestion.get("new_today_missing_candidates") or 0)
    if missing:
        failures.append({"code": "ingestion_missing_candidates", "count": missing})
    if "raw_artifact_count" in ingestion and int(ingestion.get("raw_artifact_count") or 0) == 0:
        warnings.append({"code": "raw_fetch_empty", "count": 0})

    formal_missed = int(formal.get("suspected_missed_journals") or 0)
    if formal_missed:
        failures.append({"code": "formal_journal_candidates_not_archived", "count": formal_missed})

    source_type_errors = []
    malformed_dates = []
    for record in daily:
        working = is_working(record)
        source_type = str(record.get("source_type") or "")
        if working and source_type in {"journal_article", "article"}:
            source_type_errors.append(record.get("title"))
        if not working and source_type in {"working_paper", "policy_paper", "aggregator"}:
            source_type_errors.append(record.get("title"))
        for field in ("accepted_date", "available_online", "published_online", "issue_date"):
            value = str(record.get(field) or "").strip()
            if value and not valid_date_value(field, value):
                malformed_dates.append({"title": record.get("title"), "field": field, "value": value})
    if source_type_errors:
        failures.append({"code": "source_type_mismatch", "count": len(source_type_errors), "examples": source_type_errors[:10]})
    if malformed_dates:
        failures.append({"code": "malformed_public_dates", "count": len(malformed_dates), "examples": malformed_dates[:10]})

    today_date = date.fromisoformat(today)
    historical = []
    for record in daily:
        historical_cepr = is_historical_cepr(
            record,
            run_date=today,
            max_age_days=args.max_historical_days,
        )
        historical_by_date = (
            not has_first_discovery_anchor(record, today)
            and is_historical_record(
                record,
                run_date=today,
                max_age_days=args.max_historical_days,
            )
        )
        if not historical_cepr and not historical_by_date:
            continue

        official = str(record.get("available_online") or record.get("published_online") or "")[:10]
        try:
            age = (today_date - date.fromisoformat(official)).days if official else None
        except ValueError:
            age = None
        historical.append(
            {
                "title": record.get("title"),
                "official_date": official or None,
                "age_days": age,
            }
        )
    if historical:
        failures.append({"code": "historical_records_in_today", "count": len(historical), "examples": historical[:10]})

    missing_abstract = int((quality.get("totals") or {}).get("missing_abstract_today") or 0)
    if missing_abstract:
        warnings.append({"code": "missing_abstract_today", "count": missing_abstract})
    missing_authors = int((quality.get("totals") or {}).get("missing_authors_today_journals") or 0)
    if missing_authors:
        warnings.append({"code": "missing_authors_today_journals", "count": missing_authors})

    return {"date": today, "ok": not failures, "failures": failures, "warnings": warnings}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="")
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--quality-report", type=Path, default=DATA_DIR / "quality_report.json")
    parser.add_argument("--ingestion-audit", type=Path, default=DATA_DIR / "ingestion_audit.json")
    parser.add_argument("--formal-audit", type=Path, default=DATA_DIR / "formal_journal_audit.json")
    parser.add_argument("--source-health", type=Path, default=DATA_DIR / "source_health.json")
    parser.add_argument("--output", type=Path, default=DATA_DIR / "release_gate.json")
    parser.add_argument("--max-historical-days", type=int, default=14)
    args = parser.parse_args()
    report = run(args)
    write_json(args.output, report)
    print(f"release gate date={report['date']} ok={report['ok']} failures={len(report['failures'])} warnings={len(report['warnings'])}")
    for item in report["failures"]:
        print(f"FAIL {item}")
    for item in report["warnings"]:
        print(f"WARN {item}")
    if not report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
