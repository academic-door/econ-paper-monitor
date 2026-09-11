from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import clean_historical_working_papers as cleanup  # noqa: E402


def _run_cleanup(record: dict, *, bucket: str = "2026-09-03") -> tuple[list[dict], list[dict]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        daily = root / "daily"
        daily.mkdir()
        pending = root / "pending.json"
        path = daily / f"{bucket}.json"
        path.write_text(json.dumps([record]), encoding="utf-8")
        argv = [
            "clean_historical_working_papers.py",
            "--daily-dir",
            str(daily),
            "--pending",
            str(pending),
        ]
        with patch.object(sys, "argv", argv):
            cleanup.main()
        kept = json.loads(path.read_text(encoding="utf-8"))
        queued = json.loads(pending.read_text(encoding="utf-8"))
        return kept, queued


def test_matching_first_seen_anchor_preserves_daily_discovery_bucket() -> None:
    first_seen = "2026-09-03T12:00:00+00:00"
    record = {
        "id": "url:745e78d1f96ad024",
        "title": "When Sectoral Recovery Fails to Aggregate",
        "source": "working_papers",
        "source_id": "cepr-dp",
        "url": "https://cepr.org/publications/dp21172",
        "available_online": "2026-02-14",
        "published_online": "2026-02-14",
        "date_confidence": "A",
        "first_seen": first_seen,
    }

    kept, queued = _run_cleanup(record)

    assert len(kept) == 1
    assert kept[0]["first_seen"] == first_seen
    assert kept[0]["url"] == "https://cepr.org/publications/dp21172"
    assert queued == []


def test_utc_evening_first_seen_anchors_next_beijing_daily_bucket() -> None:
    first_seen = "2026-06-18T20:31:17+00:00"
    record = {
        "id": "url:205e5e9de6ae0278",
        "title": "Mapping drought severity in Mexico using high-resolution satellite data",
        "source": "working_papers",
        "source_id": "oecd-working-papers",
        "url": "https://www.oecd.org/en/publications/mapping-drought-severity-in-mexico_f2a165e7-en.html",
        "available_online": "2025-12-01",
        "date_confidence": "A",
        "first_seen": first_seen,
    }

    kept, queued = _run_cleanup(record, bucket="2026-06-19")

    assert len(kept) == 1
    assert kept[0]["first_seen"] == first_seen
    assert queued == []


def test_unanchored_old_catalogue_record_still_moves_to_pending() -> None:
    record = {
        "title": "Historical catalogue item",
        "source": "working_papers",
        "source_id": "cepr-dp",
        "url": "https://cepr.org/publications/dp20322",
        "available_online": "2025-06-03",
        "date_confidence": "B",
    }

    kept, queued = _run_cleanup(record)

    assert kept == []
    assert len(queued) == 1


def test_genuinely_different_beijing_first_seen_does_not_suppress_cleanup() -> None:
    record = {
        "title": "Backflowed historical record",
        "source": "working_papers",
        "source_id": "cepr-dp",
        "url": "https://cepr.org/publications/dp20322",
        "available_online": "2025-06-03",
        "date_confidence": "B",
        "first_seen": "2026-09-02T10:00:00+00:00",
    }

    kept, queued = _run_cleanup(record, bucket="2026-09-03")

    assert kept == []
    assert len(queued) == 1


def test_legacy_low_number_cepr_rule_remains_stricter_than_anchor() -> None:
    record = {
        "title": "Old CEPR catalogue item",
        "source": "working_papers",
        "source_id": "cepr-dp",
        "url": "https://cepr.org/publications/dp8177",
        "date_confidence": "F",
        "first_seen": "2026-09-03T12:00:00+00:00",
    }

    kept, queued = _run_cleanup(record)

    assert kept == []
    assert len(queued) == 1
