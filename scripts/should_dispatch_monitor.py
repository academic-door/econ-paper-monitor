"""Decide which monitor mode the watchdog should dispatch.

GitHub scheduled workflows can be delayed or occasionally skipped. This script
keeps the public monitor fresh by backfilling missed full windows, then falling
back to the hourly light run.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, time, timedelta, timezone

from status import load_status


BEIJING = timezone(timedelta(hours=8))


def parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_today_beijing(value: datetime | None, now_bj: datetime) -> bool:
    if value is None:
        return False
    return value.astimezone(BEIJING).date() == now_bj.date()


def parse_full_times(value: str) -> list[time]:
    windows: list[time] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            hour_text, minute_text = item.split(":", 1)
            windows.append(time(int(hour_text), int(minute_text)))
        except (TypeError, ValueError):
            continue
    return sorted(windows) or [time(8, 30)]


def latest_due_full_window(
    now_bj: datetime,
    windows: list[time],
    grace_minutes: int = 0,
) -> datetime | None:
    """Return the latest full slot whose recovery grace has elapsed.

    The returned datetime is the original scheduled slot, not the grace-shifted
    time. This lets a successful full run that finishes shortly after the slot
    satisfy the watchdog without requiring it to finish after the grace cutoff.
    """
    grace = timedelta(minutes=max(0, grace_minutes))
    candidates = [datetime.combine(now_bj.date(), item, tzinfo=BEIJING) for item in windows]
    due = [item for item in candidates if item + grace <= now_bj]
    if due:
        return max(due)
    return None


def latest_discovery_finish(last_light: datetime | None, last_full: datetime | None) -> datetime | None:
    """A full run is stronger freshness evidence than a core/light run."""
    candidates = [value for value in (last_light, last_full) if value is not None]
    return max(candidates) if candidates else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--light-min-minutes", type=int, default=50)
    parser.add_argument("--full-after-hour", type=int, default=8)
    parser.add_argument("--full-after-minute", type=int, default=30)
    parser.add_argument("--full-times", default="")
    parser.add_argument("--full-grace-minutes", type=int, default=0)
    args = parser.parse_args()

    status = load_status()
    workflow = status.get("workflow", {})
    now_utc = datetime.now(UTC)
    now_bj = now_utc.astimezone(BEIJING)

    last_full = parse_dt(str(workflow.get("last_full_finished_at", "")))
    full_times = parse_full_times(args.full_times or f"{args.full_after_hour:02d}:{args.full_after_minute:02d}")
    due_full = latest_due_full_window(now_bj, full_times, args.full_grace_minutes)
    if due_full and (last_full is None or last_full.astimezone(BEIJING) < due_full):
        print("full")
        return

    last_light = parse_dt(str(workflow.get("last_light_finished_at", "")))
    last_discovery = latest_discovery_finish(last_light, last_full)
    if last_discovery is None:
        print("light")
        return

    age = now_utc - last_discovery.astimezone(UTC)
    if age >= timedelta(minutes=args.light_min_minutes):
        print("light")
    else:
        print(f"skip:{int(age.total_seconds() // 60)}")


if __name__ == "__main__":
    main()
