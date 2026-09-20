from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dedupe import ensure_daily_archive  # noqa: E402
from repair_seen_only_archives import repair_seen_only_archives  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def base_record(**overrides) -> dict:
    record = {
        "id": "doi:10.1016/j.example.2026.000001",
        "title": "Example in-press paper",
        "doi": "10.1016/j.example.2026.000001",
        "journal": "Journal of Examples",
        "journal_id": "journal-examples",
        "source": "crossref",
        "source_type": "journal",
        "available_online": "2026-07-30",
        "date_source": "crossref_doi_published_online",
        "date_confidence": "C",
        "abstract": "A complete abstract for the example paper.",
        "first_seen": "2026-07-30T12:00:00+00:00",
    }
    record.update(overrides)
    return record


def make_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    write_json(data_dir / "seen.json", {"papers": {}})
    (data_dir / "daily").mkdir(parents=True)
    return data_dir


def write_formal_journal_registry(data_dir: Path) -> None:
    (data_dir / "journals.yml").write_text(
        """journals:
  - id: "american-economic-review"
    title: "American Economic Review"
    short_name: "AER"
    aliases:
      - "AER"
    chinese_name: "美国经济评论"
    fields:
      - "economics"
    public_group: "经济学"
    priority_private: ""
    issn: "0002-8282"
    eissn: null
    print_issn: null
    online_issn: null
    publisher: "American Economic Association"
    sources:
      - type: crossref
        issn: "0002-8282"
""",
        encoding="utf-8",
    )


def test_same_run_crossref_enrichment_can_archive_to_official_history_date(tmp_path: Path):
    data_dir = make_data_dir(tmp_path)
    record = base_record(
        title="Contextually Private Mechanisms",
        doi="10.1257/aer.20240579",
        id="doi:10.1257/aer.20240579",
        journal="American Economic Review",
        source="crossref",
        available_online="2026-09-01",
        published_online="2026-09-01",
        first_seen="2026-09-20T06:43:48+00:00",
    )

    changed = ensure_daily_archive(
        data_dir / "daily",
        record,
        "2026-09-20",
        allow_same_run_journal_sources=True,
    )

    assert changed is True
    assert not (data_dir / "daily" / "2026-09-20.json").exists()
    archived = json.loads((data_dir / "daily" / "2026-09-01.json").read_text(encoding="utf-8"))
    assert [item["doi"] for item in archived] == ["10.1257/aer.20240579"]


def test_crossref_archive_stays_disabled_without_same_run_permission(tmp_path: Path):
    data_dir = make_data_dir(tmp_path)
    record = base_record(
        source="crossref",
        available_online="2026-09-01",
        published_online="2026-09-01",
    )

    changed = ensure_daily_archive(data_dir / "daily", record, "2026-09-20")

    assert changed is False
    assert not list((data_dir / "daily").glob("*.json"))


def test_sparse_seen_formal_journal_uses_registry_identity(tmp_path: Path):
    data_dir = make_data_dir(tmp_path)
    write_formal_journal_registry(data_dir)
    sparse = base_record(
        id="doi:10.1257/aer.20240579",
        doi="10.1257/aer.20240579",
        title="Contextually Private Mechanisms",
        journal="American Economic Review",
        source=None,
        source_type=None,
        journal_id=None,
        available_online="2026-09-01",
        published_online="2026-09-01",
        first_seen="2026-09-20T06:43:48+00:00",
    )
    write_json(
        data_dir / "seen.json",
        {"papers": {"doi:10.1257/aer.20240579": sparse}},
    )

    report = repair_seen_only_archives(data_dir=data_dir, run_date="2026-09-20")

    assert report["archived_count"] == 1
    assert report["changed_files"] == ["2026-09-01.json"]
    archived = json.loads((data_dir / "daily" / "2026-09-01.json").read_text(encoding="utf-8"))
    assert archived[0]["doi"] == "10.1257/aer.20240579"


def test_sparse_nonformal_seen_record_is_not_promoted(tmp_path: Path):
    data_dir = make_data_dir(tmp_path)
    write_formal_journal_registry(data_dir)
    sparse = base_record(
        id="doi:10.9999/not-formal",
        doi="10.9999/not-formal",
        title="Unregistered journal-like record",
        journal="Not A Formal Journal",
        source=None,
        source_type=None,
        journal_id=None,
        available_online="2026-09-01",
        published_online="2026-09-01",
    )
    write_json(data_dir / "seen.json", {"papers": {"doi:10.9999/not-formal": sparse}})

    report = repair_seen_only_archives(data_dir=data_dir, run_date="2026-09-20")

    assert report["archived_count"] == 0
    assert report["skipped"]["non_journal"] == 1
    assert not list((data_dir / "daily").glob("*.json"))


def test_archives_seen_only_record_into_official_date_file(tmp_path: Path):
    data_dir = make_data_dir(tmp_path)
    write_json(
        data_dir / "seen.json",
        {"papers": {"doi:10.1016/j.example.2026.000001": base_record()}},
    )

    report = repair_seen_only_archives(data_dir=data_dir, run_date="2026-08-02")

    assert report["archived_count"] == 1
    assert report["changed_files"] == ["2026-07-30.json"]
    daily = json.loads((data_dir / "daily" / "2026-07-30.json").read_text(encoding="utf-8"))
    assert len(daily) == 1
    assert daily[0]["abstract"] == "A complete abstract for the example paper."
    assert daily[0]["detail_key"]
    assert "_raw_file" not in daily[0]


def test_repair_is_idempotent(tmp_path: Path):
    data_dir = make_data_dir(tmp_path)
    write_json(
        data_dir / "seen.json",
        {"papers": {"doi:10.1016/j.example.2026.000001": base_record()}},
    )

    first = repair_seen_only_archives(data_dir=data_dir, run_date="2026-08-02")
    second = repair_seen_only_archives(data_dir=data_dir, run_date="2026-08-02")

    assert first["archived_count"] == 1
    assert second["archived_count"] == 0
    assert second["skipped"]["already_in_daily"] == 1
    daily = json.loads((data_dir / "daily" / "2026-07-30.json").read_text(encoding="utf-8"))
    assert len(daily) == 1


def test_skips_records_without_official_date_without_fabrication(tmp_path: Path):
    data_dir = make_data_dir(tmp_path)
    write_json(
        data_dir / "seen.json",
        {
            "papers": {
                "doi:10.1016/j.example.2026.000002": base_record(
                    id="doi:10.1016/j.example.2026.000002",
                    doi="10.1016/j.example.2026.000002",
                    title="Undated paper",
                    available_online=None,
                )
            }
        },
    )

    report = repair_seen_only_archives(data_dir=data_dir, run_date="2026-08-02")

    assert report["archived_count"] == 0
    assert report["skipped"]["no_official_date"] == 1
    assert not list((data_dir / "daily").glob("*.json"))


def test_skips_non_journal_and_missing_doi(tmp_path: Path):
    data_dir = make_data_dir(tmp_path)
    write_json(
        data_dir / "seen.json",
        {
            "papers": {
                "wp-1": base_record(
                    id="wp-1",
                    title="Working paper",
                    source_type="working_paper",
                ),
                "no-doi": base_record(
                    id="no-doi",
                    title="No DOI paper",
                    doi=None,
                ),
            }
        },
    )

    report = repair_seen_only_archives(data_dir=data_dir, run_date="2026-08-02")

    assert report["archived_count"] == 0
    assert report["skipped"]["non_journal"] == 1
    assert report["skipped"]["no_doi"] == 1


def test_skips_record_already_present_in_daily(tmp_path: Path):
    data_dir = make_data_dir(tmp_path)
    write_json(
        data_dir / "seen.json",
        {"papers": {"doi:10.1016/j.example.2026.000001": base_record()}},
    )
    write_json(
        data_dir / "daily" / "2026-07-30.json",
        [base_record(abstract="Existing abstract")],
    )

    report = repair_seen_only_archives(data_dir=data_dir, run_date="2026-08-02")

    assert report["archived_count"] == 0
    assert report["skipped"]["already_in_daily"] == 1


def test_created_placeholder_available_online_is_not_used_as_archive_date(tmp_path: Path):
    data_dir = make_data_dir(tmp_path)
    write_json(
        data_dir / "seen.json",
        {
            "papers": {
                "doi:10.1016/j.jfineco.2026.000001": base_record(
                    id="doi:10.1016/j.jfineco.2026.000001",
                    doi="10.1016/j.jfineco.2026.000001",
                    title="Elsevier in-press paper",
                    available_online="2019-01-01",
                    published_online="2026-07-25",
                    issue_date="2026-07-25",
                    date_source="crossref_elsevier_created_online",
                )
            }
        },
    )

    report = repair_seen_only_archives(data_dir=data_dir, run_date="2026-08-02")

    assert report["archived_count"] == 1
    assert report["changed_files"] == ["2026-07-25.json"]
    daily = json.loads((data_dir / "daily" / "2026-07-25.json").read_text(encoding="utf-8"))
    assert daily[0]["doi"] == "10.1016/j.jfineco.2026.000001"
    assert not (data_dir / "daily" / "2019-01-01.json").exists()
