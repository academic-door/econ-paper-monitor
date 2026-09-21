from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import clean_historical_working_papers as cleaner  # noqa: E402


def _record(*, source: str = "working_papers", source_id: str = "cepr-dp", url: str, official: str) -> dict:
    return {
        "id": f"url:{url}",
        "title": "Example paper",
        "source": source,
        "source_id": source_id,
        "source_type": "working_paper" if source == "working_papers" else "journal",
        "url": url,
        "available_online": official,
        "published_online": official,
        "date_confidence": "A",
        "first_seen": "2026-09-21T00:20:01+00:00",
    }


def _run(tmp_path: Path, monkeypatch, records: list[dict]) -> tuple[list[dict], list[dict]]:
    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / "2026-09-21.json").write_text(json.dumps(records), encoding="utf-8")
    pending = tmp_path / "pending.json"
    pending.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "clean_historical_working_papers.py",
            "--daily-dir",
            str(daily),
            "--pending",
            str(pending),
            "--max-age-days",
            "14",
        ],
    )
    cleaner.main()
    return (
        json.loads((daily / "2026-09-21.json").read_text(encoding="utf-8")),
        json.loads(pending.read_text(encoding="utf-8")),
    )


def test_old_cepr_rediscovery_is_quarantined_even_when_first_seen_is_today(tmp_path: Path, monkeypatch) -> None:
    record = _record(
        url="https://cepr.org/publications/dp13798",
        official="2019-06-13",
    )
    kept, pending = _run(tmp_path, monkeypatch, [record])

    assert kept == []
    assert len(pending) == 1
    assert pending[0]["url"].endswith("/dp13798")
    assert pending[0]["first_seen"] == "2026-09-21T00:20:01+00:00"
    assert "legacy-catalogue" in pending[0]["pending_reason"]


def test_current_cepr_paper_remains_eligible_for_today(tmp_path: Path, monkeypatch) -> None:
    record = _record(
        url="https://cepr.org/publications/dp21958",
        official="2026-09-20",
    )
    kept, pending = _run(tmp_path, monkeypatch, [record])

    assert [item["url"] for item in kept] == ["https://cepr.org/publications/dp21958"]
    assert pending == []


def test_old_journal_first_discovery_semantics_are_unchanged(tmp_path: Path, monkeypatch) -> None:
    record = _record(
        source="crossref",
        source_id="journal-example",
        url="https://doi.org/10.1234/example",
        official="2019-06-13",
    )
    kept, pending = _run(tmp_path, monkeypatch, [record])

    assert len(kept) == 1
    assert pending == []
