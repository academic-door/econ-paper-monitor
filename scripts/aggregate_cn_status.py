"""Aggregate per-journal Chinese source outputs into one status entry."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import DATA_DIR, load_journals, read_json, today_str
from status import load_status, now, record_source, save_status


CN_JOURNAL_IDS = [
    "journal-f69300dae2",
    "journal-679eaa2a0c",
    "journal-ba9f46c919",
    "journal-379b4022ce",
    "journal-bf2aa9381f",
    "journal-edcb877d78",
]


def aggregate_ok(rows: list[dict[str, Any]], found_outputs: int) -> bool:
    """Only report the group as healthy when every configured child succeeded."""
    return bool(rows) and found_outputs == len(rows) and all(bool(row.get("ok")) for row in rows)


def journal_result_row(
    journal_id: str,
    journal_title: str,
    payload: Any,
    status_payload: Any,
) -> dict[str, Any]:
    """Fail closed when a per-journal process is killed before writing output.

    The 75s outer timeout can leave neither JSON nor a sidecar status. Such a
    failed source must never have fallback_status=not-needed or ok=True.
    """
    exists = isinstance(payload, list)
    item = (
        status_payload[0]
        if isinstance(status_payload, list)
        and status_payload
        and isinstance(status_payload[0], dict)
        else {}
    )
    source_ok = exists and bool(item) and bool(item.get("ok"))
    if not exists:
        detail = str(item.get("message") or "").strip()
        message = "missing output" + (f"; {detail}" if detail else "")
        mode = "missing-output"
    elif not item:
        message = "missing source-status sidecar"
        mode = "missing-status"
    else:
        message = str(item.get("message") or ("ok" if source_ok else "source failed"))
        mode = item.get("mode") or "official-source"
    return {
        "journal_id": journal_id,
        "journal": journal_title,
        "ok": source_ok,
        "count": len(payload) if exists else 0,
        "mode": mode,
        "attempts": item.get("attempts", 0) if item else 0,
        "retryable": bool(item.get("retryable")) if source_ok else True,
        "fallback": None if source_ok else item.get("fallback") or "local-cnki-rss",
        "fallback_status": "not-needed" if source_ok else "queued",
        "message": message,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=today_str())
    parser.add_argument("--raw-dir", type=Path, default=DATA_DIR / "raw" / "cn")
    args = parser.parse_args()

    journals = {journal["id"]: journal for journal in load_journals()}
    rows: list[dict[str, Any]] = []
    total = 0
    found_outputs = 0
    for journal_id in CN_JOURNAL_IDS:
        journal = journals.get(journal_id, {})
        path = args.raw_dir / f"{args.date}-{journal_id}.json"
        payload = read_json(path, None)
        status_payload = read_json(path.with_suffix(".status.json"), [])
        exists = isinstance(payload, list)
        if exists:
            found_outputs += 1
        count = len(payload) if exists else 0
        total += count
        rows.append(
            journal_result_row(
                journal_id,
                journal.get("title") or journal_id,
                payload,
                status_payload,
            )
        )

    if found_outputs == 0:
        print("cn journal aggregate skipped: no per-journal raw outputs found")
        return

    successful = sum(1 for row in rows if row["ok"])
    ok = aggregate_ok(rows, found_outputs)
    message = "; ".join(f"{row['journal']}: {row['count']}" for row in rows)
    if not ok:
        message = f"partial_success successful={successful}/{len(rows)}; " + message
    record_source("cn-journals", ok=ok, count=total, message=message)
    status = load_status()
    status.setdefault("source_groups", {})["cn-journals"] = {
        "ok": ok,
        "count": total,
        "updated_at": now(),
        "successful": successful,
        "configured": len(rows),
        "retry_queue": [
            {
                "journal_id": row["journal_id"],
                "reason": row["message"],
                "fallback": row.get("fallback") or "local-cnki-rss",
            }
            for row in rows
            if not row["ok"]
        ],
        "journals": rows,
    }
    save_status(status)
    print(f"cn journal aggregate: {total} records")
    print(message)


if __name__ == "__main__":
    main()
