"""#371 historical CNKI observation integrity: exact-title, no backdating."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import reconcile_cnki_first_seen_371 as recovery  # noqa: E402


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path):
    by_date = {"2026-10-12": [], "2026-09-20": []}
    seen = {"papers": {}}
    for n, ((journal, title, pub_date), recovery_time) in enumerate(recovery.EVIDENCE.items()):
        key = f"url:fixture-{n}"
        record = {
            "id": key,
            "title": title,
            "journal": journal,
            "source": "cnki-rss",
            "available_online": pub_date,
            "published_online": pub_date,
            "first_seen": recovery_time,
            "detected_at": recovery_time,
            "date_source": "cnki_rss_pubdate",
            "raw_data": {"cnki_pubdate_raw": "verified source evidence"},
        }
        seen["papers"][key] = dict(record)
        by_date[pub_date].append(dict(record))
    original_older = {
        "id": "url:preexisting-slJY",
        "journal": recovery.SLJY,
        "title": "全国统一大市场发展进程的再测度",
        "source": "cnki-rss",
        "available_online": "2026-10-12",
        "first_seen": "2026-10-12T04:10:51+00:00",
        "detected_at": "2026-10-12T04:10:51+00:00",
    }
    seen["papers"][original_older["id"]] = dict(original_older)
    by_date["2026-10-12"].append(dict(original_older))
    _write(tmp_path / "seen.json", seen)
    for date, records in by_date.items():
        _write(tmp_path / "daily" / f"{date}.json", records)
    return original_older


def test_original_observation_restored_without_identity_date_or_other_record_mutation(tmp_path):
    old = _fixture(tmp_path)
    before_seen = json.loads((tmp_path / "seen.json").read_text(encoding="utf-8"))
    before_daily = {
        d: json.loads((tmp_path / "daily" / f"{d}.json").read_text(encoding="utf-8"))
        for d in ("2026-10-12", "2026-09-20")
    }
    assert recovery.reconcile(data_dir=tmp_path) == {"seen_repaired": 8, "daily_repaired": 8}
    after_seen = json.loads((tmp_path / "seen.json").read_text(encoding="utf-8"))
    for key, previous in before_seen["papers"].items():
        now = after_seen["papers"][key]
        if key == old["id"]:
            assert now == previous
            continue
        assert now["first_seen"] == recovery.ORIGINAL_OBSERVATION
        assert now["detected_at"] == recovery.ORIGINAL_OBSERVATION
        assert {k: v for k, v in now.items() if k not in {"first_seen", "detected_at"}} == {
            k: v for k, v in previous.items() if k not in {"first_seen", "detected_at"}
        }
    for d in before_daily:
        before, after = before_daily[d], json.loads((tmp_path / "daily" / f"{d}.json").read_text(encoding="utf-8"))
        assert len(after) == len(before)
        for a, b in zip(after, before):
            assert a["id"] == b["id"]
            assert a["first_seen"] == (b["first_seen"] if a["id"] == old["id"] else recovery.ORIGINAL_OBSERVATION)
            assert a["available_online"] == b["available_online"]
            assert a["date_source"] == b["date_source"] if "date_source" in b else "date_source" not in a

    unchanged = {
        path: path.read_bytes()
        for path in (tmp_path / "seen.json", *(tmp_path / "daily").glob("*.json"))
    }
    assert recovery.reconcile(data_dir=tmp_path) == {"seen_repaired": 0, "daily_repaired": 0}
    assert all(path.read_bytes() == payload for path, payload in unchanged.items())


@pytest.mark.parametrize("damage", ["missing", "duplicate", "unverified_time", "unmatched_id"])
def test_ambiguous_or_unverified_evidence_fails_closed_without_mutation(tmp_path, damage):
    _fixture(tmp_path)
    path = tmp_path / "daily" / "2026-10-12.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if damage == "missing":
        payload.pop(0)
    elif damage == "duplicate":
        payload.append(dict(payload[0]))
    elif damage == "unverified_time":
        payload[0]["first_seen"] = "2026-10-12T00:00:00+00:00"
    else:
        payload[0]["id"] = "url:wrong"
    _write(path, payload)
    before = {
        file: file.read_bytes()
        for file in (tmp_path / "seen.json", *(tmp_path / "daily").glob("*.json"))
    }
    with pytest.raises(ValueError):
        recovery.reconcile(data_dir=tmp_path)
    assert all(file.read_bytes() == original for file, original in before.items())
