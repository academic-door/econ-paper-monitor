"""Build one machine-readable health summary for maintainers and auditors."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DATA_DIR, read_json, today_str, write_json

try:
    from audit_local_cnki_status import inspect_status
except ModuleNotFoundError:  # package import during tests
    from scripts.audit_local_cnki_status import inspect_status


def count_records(path: Path) -> int:
    payload = read_json(path, [])
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict) and isinstance(payload.get("papers"), dict):
        return len(payload["papers"])
    return 0


def build_health(data_dir: Path = DATA_DIR, *, date: str | None = None) -> dict[str, Any]:
    day = date or today_str()
    quality = read_json(data_dir / "quality_report.json", {})
    formal = read_json(data_dir / "formal_journal_audit.json", {})
    recent = read_json(data_dir / "recent72_coverage_audit.json", {})
    source_health = read_json(data_dir / "source_health.json", {})
    gate = read_json(data_dir / "release_gate.json", {})
    local_cnki = read_json(data_dir / "local_cnki_status.json", {})
    check_now = datetime.fromisoformat(f"{day}T23:59:59+00:00") if date else None
    local_cnki_check = inspect_status(
        data_dir / "local_cnki_status.json",
        now=check_now,
        max_age_hours=30.0,
    )
    sentinel = read_json(data_dir / "external_sentinel_alohomora.json", {})
    quality_totals = quality.get("totals") if isinstance(quality, dict) else {}
    source_counts = source_health.get("counts") if isinstance(source_health, dict) else {}
    coverage_counts = source_health.get("coverage_counts") if isinstance(source_health, dict) else {}

    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    recent_missing = int(recent.get("missing") or 0) if isinstance(recent, dict) else 0
    formal_missing = int(formal.get("suspected_missed_journals") or 0) if isinstance(formal, dict) else 0
    duplicates = int(quality_totals.get("duplicates_by_url_or_doi") or 0) if isinstance(quality_totals, dict) else 0
    unavailable = int(source_counts.get("unavailable") or 0) if isinstance(source_counts, dict) else 0
    stale = int(source_counts.get("stale") or 0) if isinstance(source_counts, dict) else 0

    for code, value in (
        ("recent72_missing", recent_missing),
        ("formal_journal_missing", formal_missing),
        ("duplicate_public_records", duplicates),
        ("formal_sources_unavailable", unavailable),
        ("formal_sources_stale", stale),
    ):
        if value:
            failures.append({"code": code, "count": value})

    if not isinstance(gate, dict) or gate.get("ok") is not True:
        failures.append({"code": "release_gate_not_ok", "count": 1})
    crossref_only = int(coverage_counts.get("crossref_only") or 0) if isinstance(coverage_counts, dict) else 0
    supplemental = int(coverage_counts.get("supplemental") or 0) if isinstance(coverage_counts, dict) else 0
    degraded = int(source_counts.get("degraded") or 0) if isinstance(source_counts, dict) else 0
    missing_abstract_today = int(quality_totals.get("missing_abstract_today") or 0) if isinstance(quality_totals, dict) else 0
    missing_authors_today = int(quality_totals.get("missing_authors_today_journals") or 0) if isinstance(quality_totals, dict) else 0
    if crossref_only:
        warnings.append({"code": "formal_sources_crossref_only", "count": crossref_only})
    if supplemental:
        warnings.append({"code": "formal_sources_crossref_plus_recall", "count": supplemental})
    elif degraded:
        warnings.append({"code": "formal_sources_degraded", "count": degraded})
    if missing_abstract_today:
        warnings.append({"code": "missing_abstract_today", "count": missing_abstract_today})
    if missing_authors_today:
        warnings.append({"code": "missing_authors_today_journals", "count": missing_authors_today})
    if local_cnki.get("state") not in {"success", "published"}:
        warnings.append({"code": "local_cnki_not_published", "state": local_cnki.get("state") or "unknown"})
    elif not local_cnki_check.get("ok"):
        warnings.append({
            "code": "local_cnki_stale",
            "state": local_cnki_check.get("code") or "unknown",
            "age_hours": local_cnki_check.get("age_hours"),
        })

    sentinel_counts = sentinel.get("counts") if isinstance(sentinel, dict) else {}
    if not isinstance(sentinel_counts, dict):
        sentinel_counts = {}
    if isinstance(sentinel, dict):
        # Accept both the current nested schema and older top-level reports.
        sentinel_counts = {
            "formal_scope_missing": sentinel.get("formal_scope_missing_count", sentinel_counts.get("formal_scope_missing", sentinel_counts.get("in_scope_missing", 0))),
            "econ_expand_candidates": sentinel.get("econ_expand_candidate_count", sentinel_counts.get("econ_expand_candidates", 0)),
            "broader_relevant_candidates": sentinel.get("broader_relevant_candidate_count", sentinel_counts.get("broader_relevant_candidates", 0)),
            "out_of_scope_references": sentinel.get("out_of_scope_reference_count", sentinel_counts.get("out_of_scope_references", 0)),
        }
    formal_sentinel_missing = int(sentinel_counts.get("formal_scope_missing") or 0)
    if formal_sentinel_missing:
        warnings.append({"code": "external_sentinel_formal_scope_missing", "count": formal_sentinel_missing})
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": day,
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
        "counts": {
            "today": count_records(data_dir / "daily" / f"{day}.json"),
            "seen": count_records(data_dir / "seen.json"),
            "formal_journals": int(formal.get("formal_journals") or 0) if isinstance(formal, dict) else 0,
            "recent72_missing": recent_missing,
            "formal_journal_missing": formal_missing,
            "duplicates": duplicates,
            "source_healthy": int(source_counts.get("healthy") or 0) if isinstance(source_counts, dict) else 0,
            "source_degraded": degraded,
            "source_crossref_only": crossref_only,
            "missing_abstract_today": missing_abstract_today,
            "missing_authors_today_journals": missing_authors_today,
            # Only formal-scope candidates are a monitor-quality alarm.  The
            # external sentinel intentionally includes broader candidates.
            "external_sentinel_missing": formal_sentinel_missing,
            "external_sentinel_econ_expand_candidates": int(sentinel_counts.get("econ_expand_candidates") or 0),
            "external_sentinel_broader_relevant_candidates": int(sentinel_counts.get("broader_relevant_candidates") or 0),
            "external_sentinel_out_of_scope_references": int(sentinel_counts.get("out_of_scope_references") or 0),
        },
        "source_health": {
            "checked_at": source_health.get("checked_at") if isinstance(source_health, dict) else None,
            "coverage_debt": source_health.get("coverage_debt") if isinstance(source_health, dict) else {},
        },
        "local_cnki": {
            "state": local_cnki.get("state"),
            "ok": local_cnki.get("ok"),
            "last_success_at": local_cnki.get("last_success_at"),
            "count": local_cnki.get("count"),
            "fresh": local_cnki_check.get("ok"),
            "freshness_code": local_cnki_check.get("code"),
            "age_hours": local_cnki_check.get("age_hours"),
            "source_health": local_cnki_check.get("source_health"),
        },
        "sentinel": {
            "checked_at": sentinel.get("checked_at") if isinstance(sentinel, dict) else None,
            "counts": sentinel_counts,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output", type=Path, default=DATA_DIR / "monitor_health.json")
    parser.add_argument("--date", default="")
    args = parser.parse_args()
    report = build_health(args.data_dir, date=args.date or None)
    write_json(args.output, report)
    print(f"monitor health date={report['date']} ok={report['ok']} failures={len(report['failures'])} warnings={len(report['warnings'])}")
    if report["failures"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
