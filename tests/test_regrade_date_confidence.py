"""Tests for the Issue #22 date-confidence regrade policy."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from regrade_date_confidence import confidence_for, regrade_daily_and_seen  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


RUN_DATE = "2026-08-05"


def test_confidence_mapping_follows_policy_a():
    cases = [
        ({"date_source": "publisher_published_online", "available_online": "2026-08-01"}, "A"),
        ({"date_source": "publisher_meta:dc.date", "available_online": "2026-08-01"}, "A"),
        ({"date_source": "rss_description_online", "published_online": "2026-08-01"}, "A"),
        ({"date_source": "rss_published", "published_online": "2026-08-01"}, "A"),
        ({"date_source": "elsevier_article_api", "available_online": "2026-08-01"}, "A"),
        ({"date_source": "cnki_rss_pubdate", "available_online": "2026-08-01"}, "A"),
        ({"date_source": "aea_forthcoming", "available_online": "2026-08-01"}, "A"),
        ({"date_source": "official_first_publish", "available_online": "2026-08-01"}, "A"),
        ({"date_source": "tandf_issue_date_fallback", "available_online": "2026-08-01"}, "B"),
        ({"date_source": "iza_detail_month", "available_online": "2026-08-01"}, "B"),
        ({"date_source": "institutional_online_date_verified", "available_online": "2024-08-22"}, "B"),
        ({"date_source": "independent_bibliographic_online_date", "published_online": "2024-06-03"}, "C"),
        ({"date_source": "crossref_doi_published_online", "published_online": "2026-08-01"}, "C"),
        ({"date_source": "crossref_doi_elsevier_created_online", "available_online": "2026-08-01"}, "C"),
        ({"date_source": "openalex", "available_online": "2026-08-01"}, "C"),
        ({"date_source": "crossref_doi_issue", "issue_date": "2026-08-01"}, "D"),
        ({"date_source": "nep_issue", "issue_date": "2026-08-01"}, "D"),
        ({"date_source": "rss_description_online", "available_online": "September "}, "F"),
        ({"date_source": "publisher_published_online"}, "F"),
        ({}, "F"),
    ]
    for record, expected in cases:
        assert confidence_for(record, RUN_DATE) == expected, (record, expected)


def test_future_official_date_is_first_seen_until_it_arrives():
    record = {"date_source": "publisher_published_online", "available_online": "2026-08-10"}
    assert confidence_for(record, RUN_DATE) == "F"


def test_regrade_is_idempotent_and_writes_only_changes(tmp_path: Path):
    data_dir = tmp_path / "data"
    daily = data_dir / "daily"
    daily.mkdir(parents=True)
    write_json(
        daily / "2026-08-05.json",
        [
            {"date_source": "publisher_published_online", "available_online": "2026-08-01", "date_confidence": "B"},
            {"date_source": "crossref_doi_published_online", "published_online": "2026-08-01", "date_confidence": "C"},
            {"date_source": "publisher_published_online", "available_online": "2026-08-10", "date_confidence": "A"},
        ],
    )
    write_json(
        data_dir / "seen.json",
        {
            "papers": {
                "k1": {"date_source": "rss_description_online", "published_online": "2026-08-01", "date_confidence": "B"}
            }
        },
    )

    first = regrade_daily_and_seen(data_dir, run_date=RUN_DATE, write=True)
    assert first["daily_files_changed"] == 1
    assert first["daily_records_changed"] == 2  # B->A and future A->F
    assert first["seen_records_changed"] == 1

    second = regrade_daily_and_seen(data_dir, run_date=RUN_DATE, write=True)
    assert second["daily_files_changed"] == 0
    assert second["daily_records_changed"] == 0
    assert second["seen_records_changed"] == 0

    payload = json.loads((daily / "2026-08-05.json").read_text(encoding="utf-8"))
    assert payload[0]["date_confidence"] == "A"
    assert payload[2]["date_confidence"] == "F"

def test_restat_independent_source_confidence_is_restored_without_changing_discovery(tmp_path: Path):
    data_dir = tmp_path / "data"
    daily_dir = data_dir / "daily"
    first_seen = "2026-10-22T22:20:31+00:00"
    records = [
        {
            "id": "doi:10.1162/rest_a_01486", "doi": "10.1162/rest_a_01486",
            "available_online": "2024-08-22", "published_online": "2024-08-22",
            "date_source": "institutional_online_date_verified",
            "date_confidence": "F", "first_seen": first_seen,
        },
        {
            "id": "doi:10.1162/rest_a_01471", "doi": "10.1162/rest_a_01471",
            "available_online": "2024-06-03", "published_online": "2024-06-03",
            "date_source": "independent_bibliographic_online_date",
            "date_confidence": "F", "first_seen": "2026-10-22T22:50:14+00:00",
        },
    ]
    write_json(daily_dir / "2024-08-22.json", records[:1])
    write_json(daily_dir / "2024-06-03.json", records[1:])
    write_json(data_dir / "seen.json", {"papers": {r["id"]: dict(r) for r in records}})

    result = regrade_daily_and_seen(data_dir, run_date="2026-10-25", write=True)
    assert result["daily_records_changed"] == 2
    assert result["seen_records_changed"] == 2
    for day, expected, original in (
        ("2024-08-22", "B", records[0]),
        ("2024-06-03", "C", records[1]),
    ):
        updated = json.loads((daily_dir / f"{day}.json").read_text(encoding="utf-8"))[0]
        assert updated["date_confidence"] == expected
        assert updated["first_seen"] == original["first_seen"]
        assert updated["doi"] == original["doi"]
        assert updated["published_online"] == original["published_online"]
        assert updated["date_source"] == original["date_source"]

    assert regrade_daily_and_seen(data_dir, run_date="2026-10-25", write=True)["daily_records_changed"] == 0
    assert confidence_for(
        {"date_source": "institutional_online_date_verified", "available_online": "2026-10-26"},
        "2026-10-25",
    ) == "F"  # preserve existing future-date protection
    assert confidence_for({"date_source": "institutional_online_date_verified"}, "2026-10-25") == "F"
