"""Regression #389: the serial Fast writer must rehome date-corrected Daily records."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FAST_WORKFLOW = ROOT / ".github" / "workflows" / "fast-discovery.yml"
ARCHIVE_REPAIR = ROOT / "scripts" / "repair_official_date_archives.py"

CASES = {
    "10.1162/rest_a_01471": ("2024-06-03", "C", "independent_bibliographic_online_date"),
    "10.1162/rest_a_01486": ("2024-08-22", "B", "institutional_online_date_verified"),
}


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_fast_writer_rehomes_corrected_dates_before_release_gate() -> None:
    workflow = FAST_WORKFLOW.read_text(encoding="utf-8")
    steps = [
        "      - name: Apply manual overrides",
        "      - name: Normalize daily records",
        "      - name: Repair corrected archive dates",
        "      - name: Remove already-seen RSS backflow",
        "      - name: Enforce existing release gate",
    ]
    positions = [workflow.index(step) for step in steps]
    assert positions == sorted(positions)
    assert "python scripts/repair_official_date_archives.py --max-files 45" in workflow
    # Uses existing producer and existing repair code: no second writer or new schedule.
    assert "group: paper-monitor-main-writer" in workflow


def test_recent_legacy_restat_records_rehome_without_first_seen_changes(tmp_path: Path) -> None:
    daily = tmp_path / "daily"
    old_first_seen = "2026-10-22T22:50:14+00:00"
    records = []
    for doi, (first_online, grade, source) in CASES.items():
        records.append({
            "id": f"doi:{doi}",
            "doi": doi,
            "title": f"RESTAT fixture {doi}",
            "source": "crossref",
            "source_type": "journal",
            "journal_id": "review-of-economics-and-statistics",
            "available_online": first_online,
            "published_online": first_online,
            "issue_date": "2026-10-13",
            "date_source": source,
            "date_confidence": grade,
            "first_seen": old_first_seen,
        })
    _write_json(daily / "2026-10-13.json", records)

    for _ in range(2):
        subprocess.run(
            [sys.executable, str(ARCHIVE_REPAIR), "--daily-dir", str(daily), "--max-files", "45"],
            check=True,
            capture_output=True,
            text=True,
        )
    assert json.loads((daily / "2026-10-13.json").read_text(encoding="utf-8")) == []

    for doi, (date, grade, source) in CASES.items():
        rows = json.loads((daily / f"{date}.json").read_text(encoding="utf-8"))
        assert len(rows) == 1, (date, rows)
        row = rows[0]
        assert row["id"] == f"doi:{doi}"
        assert row["first_seen"] == old_first_seen
        assert row["date_confidence"] == grade
        assert row["date_source"] == source
        assert row["available_online"] == date
        assert row["published_online"] == date
        assert row["issue_date"] == "2026-10-13"
