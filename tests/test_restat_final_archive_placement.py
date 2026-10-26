"""#389: late metadata enrichment must not reverse verified RESTAT archive dates."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import apply_overrides  # noqa: E402
import repair_official_date_archives  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_full_update_reapplies_overrides_between_enrichment_and_archive_placement() -> None:
    workflow = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    start = workflow.index("- name: Normalize enriched metadata")
    reapply = workflow.index("- name: Re-apply verified DOI overrides after enrichment")
    relocate = workflow.index("- name: Repair enriched official-date archives")
    assert start < reapply < relocate
    assert workflow[start:relocate].count("run: python scripts/apply_overrides.py") == 1
    assert workflow.count("- name: Re-apply verified DOI overrides after enrichment") == 1


def test_crossref_re_enrichment_then_verified_overrides_restores_correct_historical_shards(
    tmp_path: Path,
) -> None:
    """Simulate full Update's Crossref 2026 fallback overwriting the two verified 2024 dates."""
    data = tmp_path / "data"
    daily_dir = data / "daily"
    target_day = "2026-10-13"
    examples = (
        ("10.1162/rest_a_01486", "2024-08-22", "B", "2026-10-22T22:20:31+00:00"),
        ("10.1162/rest_a_01471", "2024-06-03", "C", "2026-10-22T22:50:14+00:00"),
    )
    records = []
    for doi, _date, _confidence, first_seen in examples:
        records.append({
            "id": f"doi:{doi}",
            "doi": doi,
            "journal_id": "review-of-economics-and-statistics",
            "title": f"RESTAT {doi}",
            "source": "crossref",
            "source_type": "journal",
            "published_online": target_day,
            "available_online": target_day,
            "issue_date": target_day,
            "date_source": "crossref_published_online",
            "date_confidence": "C",
            "first_seen": first_seen,
        })
    write_json(daily_dir / f"{target_day}.json", records)
    write_json(data / "seen.json", {
        "papers": {r["id"]: dict(r) for r in records}
    })

    # The final sequence uses existing owner-local executables; no new collector.
    with mock.patch.object(sys, "argv", [
        "apply_overrides.py",
        "--overrides", str(ROOT / "data" / "manual_overrides.yml"),
        "--daily-dir", str(daily_dir),
        "--seen", str(data / "seen.json"),
    ]):
        apply_overrides.main()
    with mock.patch.object(sys, "argv", [
        "repair_official_date_archives.py", "--daily-dir", str(daily_dir),
    ]), mock.patch.object(repair_official_date_archives, "today_str", return_value="2026-10-26"):
        repair_official_date_archives.main()

    assert json.loads((daily_dir / f"{target_day}.json").read_text(encoding="utf-8")) == []
    seen = json.loads((data / "seen.json").read_text(encoding="utf-8"))["papers"]
    for doi, date, confidence, first_seen in examples:
        archived = json.loads((daily_dir / f"{date}.json").read_text(encoding="utf-8"))
        assert len(archived) == 1
        record = archived[0]
        assert record["doi"] == doi
        assert record["published_online"] == record["available_online"] == date
        assert record["date_confidence"] == confidence
        assert record["first_seen"] == first_seen
        assert record["issue_date"] == target_day
        assert seen[f"doi:{doi}"]["first_seen"] == first_seen
        assert seen[f"doi:{doi}"]["published_online"] == date

    # Repeated natural runs must neither duplicate nor move these historical records.
    with mock.patch.object(sys, "argv", [
        "repair_official_date_archives.py", "--daily-dir", str(daily_dir),
    ]), mock.patch.object(repair_official_date_archives, "today_str", return_value="2026-10-26"):
        repair_official_date_archives.main()
    assert all(len(json.loads((daily_dir / f"{date}.json").read_text(encoding="utf-8"))) == 1 for _, date, _, _ in examples)
