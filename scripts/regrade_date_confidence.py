"""Regrade ``date_confidence`` from ``date_source`` under the Issue #22 policy.

Policy (option A):
* A: official channels with an explicit online/published date (publisher
  detail page / API, official RSS with a parsed date, CNKI RSS explicit date,
  AEA forthcoming list, PDF).
* B: official-channel alternate/inferred dates (e.g. T&F issue fallback,
  IZA month-level detail).
* C: registry metadata (Crossref / OpenAlex).
* D: issue / volume dates.
* F: no usable date, first-seen only, or any official date still in the
  future (treated as first-seen until the date arrives).

Idempotent, data line only: reads/writes ``data/**``.  Integrated into
``public_integrity`` full repair so every full update stays consistent.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from common import DATA_DIR, today_str, write_json

_A_PREFIXES = (
    "publisher",
    "rss_",
    "cnki_rss",
    "elsevier_article_api",
    "world_bank_detail_api",
    "aea_forthcoming",
    "pdf",
    "official_first_publish",
)
_B_EXACT = {"tandf_issue_date_fallback", "iza_detail_month", "institutional_online_date_verified"}
_C_EXACT = {"independent_bibliographic_online_date"}


def valid_iso(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def confidence_for(record: dict[str, Any], run_date: str) -> str:
    online = valid_iso(record.get("available_online")) or valid_iso(record.get("published_online"))
    issue = valid_iso(record.get("issue_date"))
    has_date = online is not None or issue is not None
    source = str(record.get("date_source") or "").casefold()
    if online is not None and online > date.fromisoformat(run_date):
        return "F"  # future official date: first-seen until the date arrives
    if not has_date or not source or source in {"unknown", "unknown_date"}:
        return "F"
    if source.startswith(_A_PREFIXES):
        return "A"
    if source in _B_EXACT:
        return "B"
    if source in _C_EXACT:
        return "C"
    if source.startswith("crossref"):
        return "D" if source.endswith("_issue") else "C"
    if source.startswith("openalex") or "unpaywall" in source:
        return "C"
    if source.startswith("nep_") or source.endswith("_issue"):
        return "D"
    return "F"


def regrade_records(records: list[dict[str, Any]], run_date: str) -> tuple[int, Counter]:
    changed = 0
    counts: Counter = Counter()
    for record in records:
        if not isinstance(record, dict):
            continue
        current = str(record.get("date_confidence") or "").upper()
        target = confidence_for(record, run_date)
        counts[target] += 1
        if current != target:
            record["date_confidence"] = target
            changed += 1
    return changed, counts


def regrade_daily_and_seen(
    data_dir: Path,
    *,
    run_date: str | None = None,
    limit: int | None = None,
    write: bool = True,
) -> dict[str, Any]:
    run_date = run_date or today_str()
    daily_dir = data_dir / "daily"
    changed_daily: list[str] = []
    total_counts: Counter = Counter()
    daily_changed = 0
    for path in sorted(daily_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            continue
        changed, counts = regrade_records(payload, run_date)
        total_counts.update(counts)
        if changed:
            if limit is not None and len(changed_daily) >= limit:
                continue
            if write:
                write_json(path, payload)
            changed_daily.append(path.name)
            daily_changed += changed

    seen_path = data_dir / "seen.json"
    seen_payload = json.loads(seen_path.read_text(encoding="utf-8"))
    papers = seen_payload.get("papers") if isinstance(seen_payload, dict) else {}
    seen_records = [papers[key] for key in papers if isinstance(papers.get(key), dict)]
    seen_changed, seen_counts = regrade_records(seen_records, run_date)
    total_counts.update(seen_counts)
    if seen_changed and write:
        write_json(seen_path, seen_payload)

    return {
        "daily_files_changed": len(changed_daily),
        "daily_records_changed": daily_changed,
        "seen_records_changed": seen_changed,
        "confidence_counts": dict(total_counts),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--date", default=today_str())
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args(argv)

    report = regrade_daily_and_seen(
        args.data_dir,
        run_date=args.date,
        limit=args.limit,
        write=not args.audit_only,
    )
    print(
        "date_confidence_regrade "
        f"daily_files={report['daily_files_changed']} "
        f"daily_records={report['daily_records_changed']} "
        f"seen_records={report['seen_records_changed']} "
        f"counts={json.dumps(report['confidence_counts'], sort_keys=True)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())