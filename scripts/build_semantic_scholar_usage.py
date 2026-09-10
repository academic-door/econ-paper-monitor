"""Aggregate provider API key usage into a daily report.

Reads ``data/metadata_provider_health.json`` (per-run provider health) and
``data/semantic_scholar_keepalive.json`` (daily keep-alive state) and writes
``data/semantic_scholar_usage.json`` with per-day and cumulative request
counts for the monitored providers (``semantic-scholar`` and ``elsevier``).

The report powers a self-hosted usage page; Semantic Scholar and Elsevier do
not expose a combined usage dashboard.  No credentials are read or printed.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from common import BEIJING_TZ, DATA_DIR, now_iso, read_json, write_json

PROVIDERS = ("semantic-scholar", "elsevier")


def _beijing_date(value: Any) -> str | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.astimezone(BEIJING_TZ).date().isoformat()


def _aggregate(
    runs: list[Any], latest: dict[str, Any], provider: str
) -> dict[str, Any]:
    by_day: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "attempts": 0,
            "available": 0,
            "empty": 0,
            "not_found": 0,
            "rate_limited": 0,
            "skipped": 0,
            "http_error": 0,
            "provider_error": 0,
            "runs": 0,
        }
    )
    totals = {
        "attempts": 0,
        "available": 0,
        "empty": 0,
        "not_found": 0,
        "rate_limited": 0,
        "skipped": 0,
        "http_error": 0,
        "provider_error": 0,
        "runs": 0,
    }
    last_used_at: str | None = None
    api_key_configured: bool | None = None
    inst_token_configured: bool | None = None
    rate_limit_headers: dict[str, Any] | None = None

    def absorb(run: dict[str, Any]) -> None:
        nonlocal last_used_at, api_key_configured, inst_token_configured, rate_limit_headers
        providers = run.get("providers") if isinstance(run, dict) else {}
        entry = providers.get(provider) if isinstance(providers, dict) else {}
        if not isinstance(entry, dict) or not entry:
            return
        checked = run.get("checked_at")
        attempts = int(entry.get("attempts") or 0)
        if attempts > 0 and checked:
            if last_used_at is None or str(checked) > last_used_at:
                last_used_at = str(checked)
        if "api_key_configured" in entry:
            api_key_configured = bool(entry["api_key_configured"])
        if "inst_token_configured" in entry:
            inst_token_configured = bool(entry["inst_token_configured"])
        headers = entry.get("rate_limit_headers")
        if isinstance(headers, dict) and headers:
            if rate_limit_headers is None or _remaining(headers) < _remaining(rate_limit_headers):
                rate_limit_headers = headers
        statuses = entry.get("statuses") if isinstance(entry.get("statuses"), dict) else {}
        row = {
            "attempts": attempts,
            "available": int(entry.get("available") or 0),
            "empty": int(entry.get("empty") or 0),
            "not_found": int(statuses.get("not_found") or 0),
            "rate_limited": int(statuses.get("rate_limited") or 0),
            "skipped": int(statuses.get("skipped_rate_limited") or 0),
            "http_error": int(statuses.get("http_error") or 0),
            "provider_error": int(statuses.get("provider_error") or 0),
        }
        day = _beijing_date(checked)
        if day is not None:
            for key, value in row.items():
                by_day[day][key] += value
            by_day[day]["runs"] += 1
        for key, value in row.items():
            totals[key] += value
        totals["runs"] += 1

    for run in runs:
        absorb(run)
    absorb(latest)

    by_day_list = [
        {"date": day, **by_day[day]}
        for day in sorted(day for day in by_day if day)
    ]

    today = datetime.now(BEIJING_TZ).date()
    weekly = sum(
        row["attempts"]
        for day, row in by_day.items()
        if datetime.fromisoformat(day).date() >= today - timedelta(days=6)
    )

    days_since_last_use: int | None = None
    if last_used_at:
        used = _beijing_date(last_used_at)
        if used:
            days_since_last_use = max(0, (today - datetime.fromisoformat(used).date()).days)

    return {
        "api_key_configured": bool(api_key_configured),
        "inst_token_configured": bool(inst_token_configured),
        "last_used_at": last_used_at,
        "days_since_last_use": days_since_last_use,
        "weekly_requests_7d": weekly,
        "rate_limit_headers": rate_limit_headers,
        "total": totals,
        "by_day": by_day_list,
    }


def _remaining(headers: dict[str, Any]) -> int:
    try:
        return int(headers.get("X-RateLimit-Remaining") or 0)
    except (TypeError, ValueError):
        return 0


def build_usage(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    health = read_json(data_dir / "metadata_provider_health.json", {})
    keepalive = read_json(data_dir / "semantic_scholar_keepalive.json", {})
    if not isinstance(health, dict):
        health = {}
    if not isinstance(keepalive, dict):
        keepalive = {}

    runs = health.get("runs") if isinstance(health.get("runs"), list) else []
    latest = health.get("latest") if isinstance(health.get("latest"), dict) else {}

    providers = {
        name: _aggregate(runs, latest, name)
        for name in PROVIDERS
    }

    ss = providers["semantic-scholar"]
    last_keepalive_at = keepalive.get("checked_at")
    keepalive_ok = keepalive.get("ok")
    keepalive_reason = str(keepalive.get("reason") or "missing")

    return {
        "updated_at": now_iso(),
        # Backward-compatible top-level summary (semantic-scholar only).
        "key_configured": bool(ss["api_key_configured"]),
        "last_used_at": ss["last_used_at"],
        "last_keepalive_at": last_keepalive_at,
        "last_keepalive_ok": keepalive_ok,
        "last_keepalive_reason": keepalive_reason,
        "days_since_last_use": ss["days_since_last_use"],
        "total": ss["total"],
        "by_day": ss["by_day"],
        # Full per-provider report consumed by the usage page.
        "providers": providers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)

    usage = build_usage(args.data_dir)
    write_json(args.data_dir / "semantic_scholar_usage.json", usage)
    summary = " ".join(
        f"{name}: attempts={usage['providers'][name]['total']['attempts']}"
        for name in PROVIDERS
    )
    print(
        f"provider_usage updated_at={usage['updated_at']} {summary} "
        f"days={len(usage['by_day'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())