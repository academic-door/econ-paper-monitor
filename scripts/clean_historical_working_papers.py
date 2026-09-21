"""Move clearly historical working-paper catalogue items out of public pages."""

from __future__ import annotations

import argparse
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from artifact_paths import sanitize_record_paths
from common import BEIJING_TZ, DATA_DIR, read_json, stable_id, today_str, write_json


CEPR_NUMBER = re.compile(r"/dp(\d+)(?:\D|$)", flags=re.I)
ISO_DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")


def first_seen_daily_bucket(value: Any) -> str | None:
    """Map first_seen to the repository's canonical Beijing Daily bucket."""
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


def is_historical_cepr(
    record: dict[str, Any],
    *,
    run_date: str,
    max_age_days: int,
) -> bool:
    """Reject stale CEPR catalogue rediscovery from the public Today flow.

    CEPR pages can expose old catalogue links when the listing surface drifts or
    a recovery path changes. A fresh first_seen timestamp is not enough to make
    a years-old Discussion Paper a current Today item.

    Keep the legacy DP-number guard for undated ancient records, and also trust
    explicit CEPR/publisher date evidence even when normalization has left the
    confidence marker at F.
    """
    if str(record.get("source_id") or "") != "cepr-dp":
        return False
    match = CEPR_NUMBER.search(str(record.get("url") or ""))
    if match and int(match.group(1)) < 10000:
        return True

    official = str(
        record.get("available_online")
        or record.get("published_online")
        or record.get("issue_date")
        or ""
    )[:10]
    if not ISO_DATE.fullmatch(official):
        return False
    try:
        age_days = (date.fromisoformat(run_date) - date.fromisoformat(official)).days
    except ValueError:
        return False
    if age_days <= max_age_days:
        return False

    confidence = str(record.get("date_confidence") or "")
    source = str(record.get("date_source") or "")
    trusted_cepr_sources = {
        "cepr_published_time",
        "publisher_detail",
        "source_list_date",
    }
    return confidence not in {"F", "unknown"} or source in trusted_cepr_sources


def has_first_discovery_anchor(record: dict[str, Any], bucket_date: str) -> bool:
    """Return True when first_seen belongs to this canonical Beijing Daily bucket.

    Daily files are keyed by Beijing date, while ``first_seen`` is persisted as
    an offset-aware timestamp (normally UTC). Metadata enrichment may later
    reveal an official publication date months or years earlier; that evidence
    must not rewrite the accepted first-discovery timeline.
    """
    if not ISO_DATE.fullmatch(bucket_date):
        return False
    try:
        date.fromisoformat(bucket_date)
    except ValueError:
        return False
    return first_seen_daily_bucket(record.get("first_seen")) == bucket_date


def is_historical_working_paper(record: dict[str, Any], *, run_date: str, max_age_days: int) -> bool:
    if str(record.get("source") or "") != "working_papers":
        return False
    return is_historical_record(record, run_date=run_date, max_age_days=max_age_days)


def is_historical_record(record: dict[str, Any], *, run_date: str, max_age_days: int) -> bool:
    if str(record.get("date_confidence") or "") in {"F", "unknown"}:
        return False
    official = str(record.get("available_online") or record.get("published_online") or record.get("issue_date") or "")[:10]
    if not ISO_DATE.fullmatch(official):
        return False
    try:
        return (date.fromisoformat(run_date) - date.fromisoformat(official)).days > max_age_days
    except ValueError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--pending", type=Path, default=DATA_DIR / "pending_date_records.json")
    parser.add_argument("--max-age-days", type=int, default=14)
    args = parser.parse_args()

    pending = read_json(args.pending, [])
    pending = pending if isinstance(pending, list) else []
    pending_keys = {str(item.get("id") or stable_id(item)) for item in pending if isinstance(item, dict)}
    moved = 0
    changed_files = 0
    for path in sorted(args.daily_dir.glob("*.json")):
        payload = read_json(path, [])
        if not isinstance(payload, list):
            continue
        kept: list[dict[str, Any]] = []
        file_changed = False
        for record in payload:
            historical_reason = None
            if (
                isinstance(record, dict)
                and is_historical_cepr(
                    record,
                    run_date=path.stem,
                    max_age_days=args.max_age_days,
                )
            ):
                historical_reason = "historical CEPR catalogue item outside the public discovery window"
            elif (
                isinstance(record, dict)
                and not has_first_discovery_anchor(record, path.stem)
                and is_historical_record(record, run_date=path.stem, max_age_days=args.max_age_days)
            ):
                historical_reason = "record has an official date older than the public discovery window"
            if historical_reason:
                record = dict(record)
                record["id"] = record.get("id") or stable_id(record)
                record["pending_reason"] = historical_reason
                if record["id"] not in pending_keys:
                    pending.append(record)
                    pending_keys.add(record["id"])
                moved += 1
                file_changed = True
            else:
                kept.append(record)
        if file_changed:
            write_json(path, kept)
            changed_files += 1
    sanitize_record_paths(pending)
    write_json(args.pending, pending)
    print(f"historical working-paper cleanup: moved={moved} files={changed_files} pending={len(pending)} date={today_str()}")


if __name__ == "__main__":
    main()
