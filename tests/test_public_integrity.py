from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from public_integrity import (  # noqa: E402
    audit_integrity,
    audit_machine_path_leaks,
    repair_public_integrity,
    merge_record,
)

def record(
    title: str,
    record_id: str,
    *,
    journal_id: str,
    source_type: str = "journal",
    doi: str | None = None,
    url: str | None = None,
) -> dict:
    return {
        "id": record_id,
        "title": title,
        "title_zh": title,
        "authors": ["Author"],
        "journal": journal_id,
        "journal_id": journal_id,
        "source_id": journal_id,
        "source": "crossref" if source_type == "journal" else "working_papers",
        "source_type": source_type,
        "doi": doi,
        "url": url or f"https://example.test/{record_id}",
        "fields": ["general"],
        "detected_at": "2026-07-01T00:00:00+00:00",
        "available_online": "2026-07-01",
        "date_confidence": "B",
        "date_source": "publisher_published_online",
    }

def write_dataset(root: Path, daily: dict[str, list[dict]], seen: dict[str, dict]) -> None:
    (root / "daily").mkdir(parents=True)
    for day, rows in daily.items():
        (root / "daily" / f"{day}.json").write_text(json.dumps(rows), encoding="utf-8")
    (root / "seen.json").write_text(json.dumps({"papers": seen}), encoding="utf-8")
    for name in (
        "ingestion_exclusion_ledger.json",
        "historical_backfill_pending.json",
        "pending_date_records.json",
        "metadata_retry_queue.json",
    ):
        (root / name).write_text('{"records": []}', encoding="utf-8")

def test_doi_backfill_merges_same_source_title_and_repairs_orphan(tmp_path: Path) -> None:
    title = "A sufficiently specific economics paper title"
    old = record(title, "url:old", journal_id="example-journal", url="https://example.test/old")
    enriched = record(
        title,
        "doi:10.1234/example",
        journal_id="example-journal",
        doi="10.1234/example",
        url="https://doi.org/10.1234/example",
    )
    enriched["abstract"] = "This is the complete publisher abstract for the economics paper."
    write_dataset(
        tmp_path,
        {"2026-07-01": [old], "2026-07-02": [enriched]},
        {"url:old": dict(old), "doi:10.1234/example": dict(enriched)},
    )

    report = repair_public_integrity(tmp_path)

    rows = json.loads((tmp_path / "daily" / "2026-07-01.json").read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["doi"] == "10.1234/example"
    assert rows[0]["abstract"].startswith("This is the complete")
    assert json.loads((tmp_path / "daily" / "2026-07-02.json").read_text(encoding="utf-8")) == []
    seen = json.loads((tmp_path / "seen.json").read_text(encoding="utf-8"))["papers"]
    assert len(seen) == 1
    assert report["current"]["same_source_title_duplicate_records"] == 0
    assert report["current"]["orphan_keys"] == 0
    assert report["migration_delta"]["canonical_records_removed"] == 1

def test_working_paper_and_journal_versions_are_retained_and_related(tmp_path: Path) -> None:
    title = "A paper that later appeared in a peer reviewed journal"
    working = record(title, "wp", journal_id="cepr-dp", source_type="working_paper")
    journal = record(title, "journal", journal_id="example-journal", doi="10.1234/version")
    write_dataset(
        tmp_path,
        {"2026-07-01": [working], "2026-07-02": [journal]},
        {"wp": dict(working), "journal": dict(journal)},
    )

    report = repair_public_integrity(tmp_path)

    rows = []
    for path in (tmp_path / "daily").glob("*.json"):
        rows.extend(json.loads(path.read_text(encoding="utf-8")))
    assert len(rows) == 2
    assert {row["source_type"] for row in rows} == {"working_paper", "journal"}
    assert len({row["version_group_key"] for row in rows}) == 1
    assert all(len(row["related_versions"]) == 1 for row in rows)
    assert report["current"]["version_relationship_groups"] == 1

def test_repec_nep_issue_positions_are_not_stable_paper_numbers(tmp_path: Path) -> None:
    first = record(
        "First weekly issue paper",
        "first",
        journal_id="repec-nep-dev",
        source_type="aggregator",
        url="https://nep.repec.org/nep-dev/2026-06-08#p7",
    )
    first.update({"source_id": "repec-nep-dev", "paper_number": "p7", "source_url": "https://nep.repec.org/nep-dev/2026-06-08"})
    second = record(
        "Different paper in the next weekly issue",
        "second",
        journal_id="repec-nep-dev",
        source_type="aggregator",
        url="https://nep.repec.org/nep-dev/2026-06-15#p7",
    )
    second.update({"source_id": "repec-nep-dev", "paper_number": "p7", "source_url": "https://nep.repec.org/nep-dev/2026-06-15"})
    write_dataset(
        tmp_path,
        {"2026-06-08": [first], "2026-06-15": [second]},
        {"first": dict(first), "second": dict(second)},
    )

    report = repair_public_integrity(tmp_path)

    assert report["current"]["daily_records"] == 2
    assert report["current"]["seen_records"] == 2
    rows = json.loads((tmp_path / "daily" / "2026-06-08.json").read_text(encoding="utf-8"))
    assert "second" not in rows[0].get("identity_aliases", [])

def test_unresolved_legacy_seen_dedupe_is_requeued(tmp_path: Path) -> None:
    kept = record("Canonical paper", "kept", journal_id="example-journal")
    write_dataset(tmp_path, {"2026-07-01": [kept]}, {"kept": dict(kept)})
    orphan = record(
        "Distinct Chinese journal candidate",
        "candidate",
        journal_id="cn-journal",
        url="https://example.cn/#/detail?contentId=unique-123",
    )
    orphan.update(
        {
            "seen": True,
            "duplicate": True,
            "stage": "seen_dedupe",
            "matched_keys": ["url:https://example.cn/#/detail"],
            "raw_file": r"E:\\private-checkout\\data\\raw\\cn\\candidate.json",
            "official_date": "2026-07-01",
        }
    )
    (tmp_path / "ingestion_exclusion_ledger.json").write_text(
        json.dumps({"records": [orphan]}), encoding="utf-8"
    )

    report = repair_public_integrity(tmp_path)

    ledger = json.loads((tmp_path / "ingestion_exclusion_ledger.json").read_text(encoding="utf-8"))
    row = ledger["records"][0]
    assert row["seen"] is False
    assert row["duplicate"] is False
    assert row["stage"] == "retry_reingestion"
    assert row["retry_reason"] == "legacy_url_identity_false_positive"
    queue = json.loads((tmp_path / "ingestion_retry_queue.json").read_text(encoding="utf-8"))
    assert len(queue["records"]) == 1
    assert queue["records"][0]["raw_file"] == "candidate.json"
    assert queue["records"][0]["official_date_status"] == "available"
    assert row["raw_file"] == "candidate.json"
    assert report["current"]["ledger_orphan_keys"] == 0

def test_ledger_relinks_by_journal_title_when_seen_has_internal_journal_id(tmp_path: Path) -> None:
    kept = record("A journal paper already in the catalogue", "kept", journal_id="journal-internal-id")
    kept["journal"] = "Example Economic Review"
    write_dataset(tmp_path, {"2026-07-01": [kept]}, {"kept": dict(kept)})
    candidate = record(
        kept["title"],
        "candidate",
        journal_id="missing-from-ledger",
        url="https://publisher.test/article-version",
    )
    candidate.update(
        {
            "source": "Example Economic Review",
            "journal": None,
            "journal_id": None,
            "source_id": None,
            "seen": True,
            "duplicate": True,
            "stage": "seen_dedupe",
            "matched_keys": ["url:legacy-unresolved-key"],
        }
    )
    (tmp_path / "ingestion_exclusion_ledger.json").write_text(
        json.dumps({"records": [candidate]}), encoding="utf-8"
    )

    report = repair_public_integrity(tmp_path)

    ledger = json.loads((tmp_path / "ingestion_exclusion_ledger.json").read_text(encoding="utf-8"))
    row = ledger["records"][0]
    assert row["seen"] is True
    assert row["duplicate"] is True
    assert row["matched_seen_key"] == "kept"
    assert row["canonical_detail_key"]
    assert report["repairs"]["ledger_records_requeued"] == 0

    second = repair_public_integrity(tmp_path)
    assert second["repairs"]["seen_duplicates_removed"] == 0
    assert second["repairs"]["seen_records_seeded_from_daily"] == 0
    assert second["repairs"]["ledger_records_requeued"] == 0
    assert second["repairs"]["ledger_records_relinked"] == 0

def test_editor_report_is_excluded_instead_of_requeued(tmp_path: Path) -> None:
    kept = record("Canonical paper", "kept", journal_id="example-journal")
    write_dataset(tmp_path, {"2026-07-01": [kept]}, {"kept": dict(kept)})
    report_row = record(
        "Report of the Editor of The Journal of Finance for the Year 2025",
        "editor-report",
        journal_id="journal-of-finance",
    )
    report_row.update({"seen": True, "duplicate": True, "stage": "seen_dedupe", "matched_keys": []})
    (tmp_path / "ingestion_exclusion_ledger.json").write_text(
        json.dumps({"records": [report_row]}), encoding="utf-8"
    )

    report = repair_public_integrity(tmp_path)

    ledger = json.loads((tmp_path / "ingestion_exclusion_ledger.json").read_text(encoding="utf-8"))
    row = ledger["records"][0]
    assert row["stage"] == "source_rule"
    assert row["exclusion_status"] == "confirmed_nonpaper"
    assert report["repairs"]["ledger_records_requeued"] == 0
    assert report["repairs"]["ledger_nonpaper_reclassified"] == 1

def test_title_prefix_boilerplate_preview_and_missing_metadata_are_explicit(tmp_path: Path) -> None:
    item = record("Paper title", "paper", journal_id="cepr-dp", source_type="working_paper")
    item["title_zh"] = "DP21768 论文中文标题"
    item["authors"] = []
    item["available_online"] = None
    item["abstract"] = (
        "This paper reports a real empirical result. "
        "This is a preview of subscription content, log in via an institution to check access."
    )
    write_dataset(tmp_path, {"2026-07-01": [item]}, {})

    repair_public_integrity(tmp_path)

    row = json.loads((tmp_path / "daily" / "2026-07-01.json").read_text(encoding="utf-8"))[0]
    assert row["title_zh"] == "论文中文标题"
    assert "subscription content" not in row["abstract"]
    assert row["abstract_completeness"] == "preview"
    assert row["abstract_truncated"] is True
    assert row["authors_status_code"] == "missing_retry"
    assert row["official_date_status"] == "missing_retry"

def test_redundant_composite_author_entry_is_split_and_deduplicated(tmp_path: Path) -> None:
    item = record("Paper title", "paper", journal_id="example-journal")
    item["authors"] = ["First Author", "Second Author", "First Author; Second Author"]
    write_dataset(tmp_path, {"2026-07-01": [item]}, {"paper": dict(item)})

    report = repair_public_integrity(tmp_path)

    row = json.loads((tmp_path / "daily" / "2026-07-01.json").read_text(encoding="utf-8"))[0]
    assert row["authors"] == ["First Author", "Second Author"]
    assert report["current"]["redundant_composite_authors"] == 0

def test_repair_archives_seen_only_journal_record_into_daily(tmp_path: Path) -> None:
    seen_rec = record(
        "Seen only economics paper",
        "doi:10.1234/seen-only",
        journal_id="example-journal",
        doi="10.1234/seen-only",
    )
    seen_rec["abstract"] = "Complete abstract for the seen only economics paper."
    write_dataset(tmp_path, {}, {"doi:10.1234/seen-only": seen_rec})

    report = repair_public_integrity(tmp_path)

    rows = json.loads((tmp_path / "daily" / "2026-07-01.json").read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["doi"] == "10.1234/seen-only"
    assert rows[0]["abstract"].startswith("Complete abstract")
    assert report["repairs"]["seen_only_archived"] == 1

def test_checked_in_public_data_has_zero_integrity_failures() -> None:
    report = audit_integrity(ROOT / "data")
    assert report["same_source_title_duplicate_records"] == 0
    assert report["canonical_detail_key_missing"] == 0
    assert report["canonical_detail_key_duplicate"] == 0
    assert report["daily_seen_orphan_keys"] == 0
    assert report["ledger_orphan_keys"] == 0
    assert report["title_zh_number_prefixes"] == 0
    assert report["boilerplate_abstracts"] == 0
    assert report["redundant_composite_authors"] == 0
    assert report["machine_path_leaks"] == 0
    assert sum(report["metadata_missing_status"].values()) == 0

def test_machine_path_leak_audit_counts_by_layer(tmp_path: Path) -> None:
    daily_rec = record("Daily paper", "daily-1", journal_id="example-journal")
    daily_rec["_raw_file"] = r"E:\BaiduSyncdisk\Work\econ-paper-monitor\data\raw\2026-07-01\x.json"
    seen_rec = record("Seen paper", "seen-1", journal_id="example-journal")
    seen_rec["source_file"] = "/home/runner/work/econ-paper-monitor/econ-paper-monitor/data/raw/y.json"
    write_dataset(tmp_path, {"2026-07-01": [daily_rec]}, {"seen-1": seen_rec})
    ledger = tmp_path / "ingestion_exclusion_ledger.json"
    ledger.write_text(
        json.dumps({"records": [{"title": "Ledger paper", "raw_file": r"C:\Users\local\data\raw\z.json"}]}),
        encoding="utf-8",
    )

    leak = audit_machine_path_leaks(tmp_path)
    assert leak["counts"]["daily"] == 1
    assert leak["counts"]["seen"] == 1
    assert leak["counts"]["ledgers"] == 1
    assert leak["counts"]["total"] == 3
    assert audit_integrity(tmp_path)["machine_path_leaks"] == leak["counts"]["total"]

def test_unchanged_identity_keeps_stable_detail_key():
    import public_integrity as pi
    rec = {"title": "Stable", "journal": "J", "doi": "10.1000/stable"}
    pi.ensure_detail_key(rec)
    first = rec["detail_key"]
    assert pi.ensure_detail_key(rec) is False
    assert rec["detail_key"] == first

def test_duplicate_counts_distinct_dois_not_duplicates():
    import public_integrity as pi
    recs = [
        {"title": "Book Reviews", "journal": "Journal of Economic Literature", "source": "journal", "doi": "10.1257/jel.45.4.1024.r19"},
        {"title": "Book Reviews", "journal": "Journal of Economic Literature", "source": "journal", "doi": "10.1257/jel.45.4.1024.r26"},
    ]
    assert pi._duplicate_counts(recs) == (0, 0)

def test_duplicate_counts_same_doi_is_duplicate():
    import public_integrity as pi
    recs = [
        {"title": "A paper", "journal": "J", "source": "journal", "doi": "10.1000/abc"},
        {"title": "A paper", "journal": "J", "source": "journal", "doi": "10.1000/abc"},
    ]
    assert pi._duplicate_counts(recs) == (1, 1)

def test_duplicate_counts_no_doi_same_title_is_duplicate():
    import public_integrity as pi
    recs = [
        {"title": "Same title", "journal": "J", "source": "journal"},
        {"title": "Same title", "journal": "J", "source": "journal"},
    ]
    assert pi._duplicate_counts(recs) == (1, 1)


def test_effective_route_collision_disambiguated():
    import public_integrity as pi, render_site as rs
    a={"title":"Book Reviews","journal":"Journal of Economic Literature","source":"journal","detail_key":"book-reviews-b0eeb21102be","doi":"10.1257/jel.45.4.1024.r19"}
    b={"title":"Book Reviews","journal":"Journal of Economic Literature","source":"journal","detail_key":"book-reviews-b0eeb21102be","doi":"10.1257/jel.45.4.1024.r26"}
    # legacy linkage detail_key preserved; distinct strong route key in canonical_detail_key
    a["canonical_detail_key"]=pi.calculate_detail_key(a)
    b["canonical_detail_key"]=pi.calculate_detail_key(b)
    assert a["detail_key"]==b["detail_key"]
    assert rs.detail_url(a)!=rs.detail_url(b)
    assert rs.detail_key(a)!=rs.detail_key(b)

def test_route_producer_parity():
    import render_site as rs, build_daily_vnext as bdv
    rec={"title":"Book Reviews","journal":"Journal of Economic Literature","doi":"10.1257/jel.45.4.1024.r19","detail_key":"book-reviews-b0eeb21102be","canonical_detail_key":"book-reviews-5c9d0e9fff10"}
    assert rs.detail_key(rec)==bdv.detail_key(rec)

def test_same_doi_same_route():
    import render_site as rs
    a={"doi":"10.1000/abc","title":"X","detail_key":"d-x"}
    b={"doi":"10.1000/abc","title":"X","detail_key":"d-x"}
    assert rs.detail_key(a)==rs.detail_key(b)

def test_legacy_unambiguous_record_no_churn():
    import public_integrity as pi, render_site as rs
    rec={"title":"Unique title","journal":"J","doi":"10.1000/xyz"}
    pi.ensure_detail_key(rec)
    assert rec.get("canonical_detail_key") is None
    key=rs.detail_key(rec)
    assert rs.detail_key(rec)==key
def test_canonical_route_key_distinct_dois_do_not_collide():
    import public_integrity as pi, render_site as rs
    a={"title":"Book Reviews","journal":"Journal of Economic Literature","source":"journal","detail_key":"book-reviews-b0eeb21102be","doi":"10.1257/jel.45.4.1024.r23"}
    b={"title":"Book Reviews","journal":"Journal of Economic Literature","source":"journal","detail_key":"book-reviews-b0eeb21102be","doi":"10.1257/jel.45.4.1024.r26"}
    ka=pi.canonical_route_key(a, a.get("detail_key"))
    kb=pi.canonical_route_key(b, b.get("detail_key"))
    assert ka != kb
    a["canonical_detail_key"]=ka; b["canonical_detail_key"]=kb
    assert rs.detail_url(a) != rs.detail_url(b)

def test_canonical_route_key_idempotent_and_fallback():
    import public_integrity as pi
    r={"detail_key":"legacy-key","doi":"10.1000/abc"}
    k1=pi.canonical_route_key(r, "legacy-key"); k2=pi.canonical_route_key(dict(r), "legacy-key")
    assert k1 == k2 == pi.calculate_detail_key(r)
    nodoi={"detail_key":"legacy-key","title":"T","journal":"J"}
    assert pi.canonical_route_key(nodoi, "legacy-key") == "legacy-key"


def test_crossref_issue_confidence_no_longer_flaps_from_legacy_seen() -> None:
    # Exact production JEEM cohort: archived D, seen had stale C for
    # Crossref published == issue without independent online evidence.
    primary = {
        "source": "crossref", "doi": "10.1016/j.jeem.2026.103338",
        "date_source": "crossref_issue", "date_confidence": "D",
        "published_online": None, "issue_date": "2026-06-01",
        "available_online": None,
        "raw_data": {"crossref_date_source": "crossref_published"},
        "first_seen": "2026-06-16T18:16:44+00:00",
    }
    stale_seen = dict(primary, date_source="crossref_published",
                      date_confidence="C", published_online="2026-06-01")
    first_seen = primary["first_seen"]
    merge_record(primary, stale_seen)
    assert (primary["date_source"], primary["date_confidence"], primary["published_online"]) == (
        "crossref_issue", "D", None
    )
    assert merge_record(stale_seen, primary) is True
    assert (stale_seen["date_source"], stale_seen["date_confidence"], stale_seen["published_online"]) == (
        "crossref_issue", "D", None
    )
    assert merge_record(primary, stale_seen) is False
    assert merge_record(stale_seen, primary) is False
    assert primary["first_seen"] == stale_seen["first_seen"] == first_seen


def test_crossref_rawless_issue_provenance_becomes_stable() -> None:
    legacy = {
        "source": "crossref", "doi": "10.1016/j.euroecorev.2026.105384",
        "date_source": "crossref_published", "date_confidence": "C",
        "published_online": "2026-07-01", "issue_date": "2026-07-01",
        "available_online": None, "first_seen": "2026-06-16T14:15:11-04:00",
    }
    merge_record(legacy, {})
    assert legacy["date_source"] == "crossref_issue"
    assert legacy["date_confidence"] == "D"
    assert legacy["published_online"] is None
    assert legacy["issue_date"] == "2026-07-01"
    assert legacy["first_seen"] == "2026-06-16T14:15:11-04:00"
    assert not merge_record(legacy, {})


def test_explicit_crossref_online_date_may_legitimately_upgrade_issue_confidence() -> None:
    primary = {
        "source": "crossref", "doi": "10.1016/j.jeem.2026.103338",
        "date_source": "crossref_issue", "date_confidence": "D",
        "issue_date": "2026-06-01",
    }
    verified = {
        "source": "crossref", "doi": primary["doi"],
        "date_source": "crossref_published_online", "date_confidence": "C",
        "published_online": "2026-05-24", "issue_date": "2026-06-01",
        "available_online": "2026-05-24",
        "raw_data": {"crossref_date_source": "crossref_published_online"},
    }
    merge_record(primary, verified)
    assert primary["date_source"] == "crossref_published_online"
    assert primary["date_confidence"] == "C"
    assert primary["available_online"] == "2026-05-24"
    assert primary["published_online"] == "2026-05-24"


def test_verified_publisher_online_evidence_ignores_crossref_issue_alias() -> None:
    publisher = {
        "source": "crossref", "doi": "10.1016/j.jeem.2026.103338",
        "date_source": "publisher_published_online", "date_confidence": "A",
        "published_online": "2026-05-24", "available_online": "2026-05-24",
        "issue_date": "2026-06-01",
    }
    stale = {
        "source": "crossref", "doi": publisher["doi"],
        "date_source": "crossref_published", "date_confidence": "C",
        "published_online": "2026-06-01", "issue_date": "2026-06-01",
    }
    merge_record(publisher, stale)
    assert publisher["date_source"] == "publisher_published_online"
    assert publisher["date_confidence"] == "A"
    assert publisher["published_online"] == "2026-05-24"
