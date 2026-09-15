from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backfill_iza_authors as scheduled_backfill  # noqa: E402


def _record(source_id: str, suffix: str) -> dict:
    return {
        "id": f"{source_id}:{suffix}",
        "source": "working_papers",
        "source_id": source_id,
        "source_type": "working_paper",
        "url": f"https://example.test/{source_id}/{suffix}",
        "authors": [],
        "abstract": "",
        "first_seen": "2026-09-15T01:00:00+00:00",
    }


def test_global_limit_does_not_starve_later_scheduled_source(tmp_path: Path, monkeypatch) -> None:
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    records = [_record("iza", str(index)) for index in range(20)]
    records.append(_record("cepr-dp", "target"))
    (daily_dir / "2026-09-15.json").write_text(json.dumps(records), encoding="utf-8")

    seen_path = tmp_path / "seen.json"
    seen_path.write_text(json.dumps({"papers": {record["id"]: dict(record) for record in records}}), encoding="utf-8")

    attempted: list[str] = []

    monkeypatch.setattr(scheduled_backfill, "today_str", lambda: "2026-09-15")
    monkeypatch.setattr(scheduled_backfill, "queued_oecd_targets", lambda: {})
    monkeypatch.setattr(
        scheduled_backfill,
        "load_sources",
        lambda _path: [
            {"id": "iza"},
            {"id": "cepr-dp"},
            {"id": "oecd-working-papers"},
        ],
    )

    def fake_repair(record: dict, source: dict, *, timeout: int) -> tuple[bool, bool, bool]:
        del timeout
        source_id = str(source["id"])
        attempted.append(source_id)
        if source_id == "iza":
            record["authors"] = ["Recovered Author"]
            return True, True, False
        record["abstract"] = "Recovered authoritative abstract"
        return True, False, True

    monkeypatch.setattr(scheduled_backfill, "repair_record", fake_repair)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill_iza_authors.py",
            "--daily-dir",
            str(daily_dir),
            "--seen",
            str(seen_path),
            "--days",
            "1",
            "--limit",
            "20",
        ],
    )

    scheduled_backfill.main()

    assert len(attempted) == 20
    assert "cepr-dp" in attempted
