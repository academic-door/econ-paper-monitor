"""Lightweight run status helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common import DATA_DIR, read_json, write_json


STATUS_PATH = DATA_DIR / "status.json"


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def load_status(path: Path = STATUS_PATH) -> dict[str, Any]:
    return read_json(path, {"runs": [], "sources": {}})


def save_status(status: dict[str, Any], path: Path = STATUS_PATH) -> None:
    write_json(path, status)


def record_source(
    source_id: str,
    *,
    ok: bool,
    count: int = 0,
    message: str = "",
    details: dict[str, Any] | None = None,
    path: Path = STATUS_PATH,
) -> None:
    status = load_status(path)
    entry = {
        "ok": ok,
        "count": count,
        "message": message,
        "updated_at": now(),
    }
    if details:
        entry.update(details)
    status.setdefault("sources", {})[source_id] = entry
    save_status(status, path)


def record_run(summary: dict[str, Any], path: Path = STATUS_PATH) -> None:
    status = load_status(path)
    runs = status.setdefault("runs", [])
    runs.insert(0, {"updated_at": now(), **summary})
    del runs[20:]
    save_status(status, path)


def sync_owner_workflow_freshness(workflow: dict[str, Any]) -> None:
    """Reconcile denormalized core/full clocks from GitHub-owned Update history.

    The local CNKI lane uses mode=light for the UI, but cannot advance either
    core monitor clock. Clear inherited local-only counters when history is
    present rather than preserving an unsafe pre-fix value.
    """
    history = workflow.get("history")
    if not isinstance(history, list) or not history:
        return

    last_by_mode: dict[str, str] = {}
    for entry in history:
        if not isinstance(entry, dict):
            continue
        if entry.get("event") not in {"schedule", "workflow_dispatch"} or not entry.get("run_id"):
            continue
        mode = str(entry.get("mode") or "")
        finished_at = str(entry.get("finished_at") or "")
        if mode and finished_at and mode not in last_by_mode:
            last_by_mode[mode] = finished_at

    for mode, key in (
        ("full", "last_full_finished_at"),
        ("light", "last_light_finished_at"),
        ("single", "last_single_finished_at"),
    ):
        if last_by_mode.get(mode):
            workflow[key] = last_by_mode[mode]
        else:
            workflow.pop(key, None)


def record_workflow_run(summary: dict[str, Any], path: Path = STATUS_PATH) -> None:
    """Store the latest end-to-end workflow context for dashboards."""
    status = load_status(path)
    workflow = status.setdefault("workflow", {})
    workflow.update(summary)
    workflow["updated_at"] = summary.get("updated_at") or now()
    mode = summary.get("mode")
    finished_at = summary.get("finished_at") or workflow.get("updated_at") or now()

    history = workflow.setdefault("history", [])
    history.insert(
        0,
        {
            "mode": mode or "unknown",
            "mode_label": summary.get("mode_label") or mode or "unknown",
            "event": summary.get("event") or "",
            "schedule": summary.get("schedule") or "",
            "run_id": summary.get("run_id") or "",
            "run_url": summary.get("run_url") or "",
            "finished_at": finished_at,
        },
    )
    del history[30:]

    sync_owner_workflow_freshness(workflow)
    save_status(status, path)
