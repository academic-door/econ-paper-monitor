"""Assess Fast Discovery recovery without confusing creation with completion.

The public Fast freshness gate is <=45m between run creations; a successful
long-running Fast may finish recently while its creation is already stale.
Watchdog still serializes writers and prevents duplicate active runs.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any


ACTIVE_STATUSES = {"pending", "requested", "queued", "waiting", "in_progress"}
# Internal margins leave a 15m external Watchdog tick + ~5m dispatch slack.
CREATION_MIN_MINUTES = 25
SUCCESS_MAX_AGE_MINUTES = 40


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(UTC) if result.tzinfo else None


def age_minutes(value: datetime | None, now: datetime) -> int | None:
    return None if value is None else max(0, int((now - value).total_seconds() // 60))


def assess_fast_runs(runs: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    active = sum(1 for run in runs if run.get("status") in ACTIVE_STATUSES)
    last_created = max(
        (dt for run in runs if (dt := parse_time(run.get("createdAt")))),
        default=None,
    )
    last_success = max(
        (dt for run in runs if run.get("conclusion") == "success"
         if (dt := parse_time(run.get("updatedAt")))),
        default=None,
    )
    creation_age = age_minutes(last_created, now)
    success_age = age_minutes(last_success, now)
    stale_creation = creation_age is None or creation_age >= CREATION_MIN_MINUTES
    stale_success = success_age is None or success_age >= SUCCESS_MAX_AGE_MINUTES
    return {
        "stale": stale_creation or stale_success,
        "active": active,
        "creation_age_minutes": creation_age,
        "success_age_minutes": success_age,
        "creation_due": stale_creation,
        "success_due": stale_success,
    }


def main() -> None:
    runs = json.load(sys.stdin)
    if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
        raise ValueError("Fast run list must contain objects")
    assessment = assess_fast_runs(runs, datetime.now(UTC))
    print(
        "Fast recovery: "
        f"creation_age={assessment['creation_age_minutes']}m "
        f"success_age={assessment['success_age_minutes']}m "
        f"active={assessment['active']} "
        f"creation_due={assessment['creation_due']} "
        f"success_due={assessment['success_due']}",
        file=sys.stderr,
    )
    print(f"stale={str(assessment['stale']).lower()}")
    print(f"active={assessment['active']}")


if __name__ == "__main__":
    main()
