"""Repair workflow status fields from recorded history."""

from __future__ import annotations

from pathlib import Path

from status import STATUS_PATH, load_status, save_status, sync_owner_workflow_freshness


def repair_status(path: Path = STATUS_PATH) -> dict:
    status = load_status(path)
    workflow = status.setdefault("workflow", {})
    sync_owner_workflow_freshness(workflow)
    save_status(status, path)
    return status


def main() -> None:
    workflow = repair_status()["workflow"]
    print(
        "workflow status repaired:",
        "full=", workflow.get("last_full_finished_at"),
        "light=", workflow.get("last_light_finished_at"),
    )


if __name__ == "__main__":
    main()
