"""Archive seen-only journal records into canonical daily archives.

Root cause: when a DOI-backed journal record is first detected without a valid
official online date, ``dedupe`` stores it in ``seen.json`` but cannot choose a
daily archive. Later full runs see the same record as "already exists in seen"
and only enrich seen, so it never reaches ``data/daily/*.json``. This repair
backfills those records once an official date is available, using the same
archive-date rules as ``dedupe``, and is a no-op for records that already have
a daily occurrence.
"""

from __future__ import annotations

import argparse
import copy
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DATA_DIR, load_journals, normalize_text, read_json, today_str, write_json
from dedupe import (
    build_daily_index,
    is_source_navigation_noise,
    matching_daily_records,
    merge_daily,
)
from public_integrity import normalize_public_record, sanitize_record_paths


JOURNAL_SOURCE_TYPES = {"journal", "journal_article", "article"}
WORKING_SOURCE_TYPES = {"working_paper", "policy_paper", "aggregator"}
JOURNAL_SOURCES = {"crossref", "priority_toc", "aea_toc", "rss", "cnki-rss"}


def valid_iso_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            datetime.strptime(text, "%Y-%m-%d")
            return text
        except ValueError:
            return None
    return None


def archive_date_for_seen_record(record: dict[str, Any], run_date: str) -> str | None:
    """Pick a trustworthy archive date without treating created dates as online.

    ``available_online`` can carry a Crossref placeholder date (for example an
    Elsevier DOI created in 2019), so published/issue dates take precedence and
    created-looking ``available_online`` values are ignored entirely.
    """
    date_source = str(record.get("date_source") or "").casefold()
    created_like = "created" in date_source
    candidates = [
        valid_iso_date(record.get("published_online")),
        valid_iso_date(record.get("issue_date")),
        None if created_like else valid_iso_date(record.get("available_online")),
    ]
    for value in candidates:
        if value and value <= run_date:
            return value
    return None


def formal_journal_identity(data_dir: Path) -> tuple[set[str], dict[str, str]]:
    path = data_dir / "journals.yml"
    if not path.exists():
        return set(), {}
    ids: set[str] = set()
    names: dict[str, str] = {}
    for journal in load_journals(path):
        journal_id = str(journal.get("id") or "").strip().casefold()
        if journal_id:
            ids.add(journal_id)
        for key in ("title", "short_name", "chinese_name"):
            value = normalize_text(str(journal.get(key) or ""))
            if value and journal_id:
                names.setdefault(value, journal_id)
        for alias in journal.get("aliases") or []:
            value = normalize_text(str(alias or ""))
            if value and journal_id:
                names.setdefault(value, journal_id)
    return ids, names


def is_journal_record(
    record: dict[str, Any],
    *,
    formal_ids: set[str] | None = None,
    formal_names: dict[str, str] | None = None,
) -> bool:
    source_type = str(record.get("source_type") or "").strip()
    if source_type in JOURNAL_SOURCE_TYPES:
        return True
    if source_type in WORKING_SOURCE_TYPES:
        return False
    if str(record.get("source") or "").strip().casefold() in JOURNAL_SOURCES:
        return True
    journal_id = str(record.get("journal_id") or "").strip().casefold()
    if journal_id and formal_ids and journal_id in formal_ids:
        return True
    journal_name = normalize_text(str(record.get("journal") or ""))
    return bool(journal_name and formal_names and journal_name in formal_names)


def repair_seen_only_archives(
    *,
    data_dir: Path = DATA_DIR,
    run_date: str | None = None,
) -> dict[str, Any]:
    run_date = run_date or today_str()
    seen_payload = read_json(data_dir / "seen.json", {"papers": {}})
    papers = seen_payload.get("papers") if isinstance(seen_payload, dict) else {}
    if not isinstance(papers, dict):
        raise ValueError("seen.json must be a {papers: {...}} payload")

    daily_dir = data_dir / "daily"
    formal_ids, formal_names = formal_journal_identity(data_dir)
    _daily_records_by_path, daily_index = build_daily_index(daily_dir)
    pending_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    archived: list[dict[str, Any]] = []
    added_identities: set[str] = set()
    skipped = {
        "already_in_daily": [],
        "no_official_date": [],
        "no_doi": [],
        "non_journal": [],
        "navigation_noise": [],
    }

    for record in papers.values():
        if not isinstance(record, dict):
            continue
        title = str(record.get("title") or "").strip()
        if not title:
            continue
        if is_source_navigation_noise(record):
            skipped["navigation_noise"].append(title)
            continue
        if not is_journal_record(record, formal_ids=formal_ids, formal_names=formal_names):
            skipped["non_journal"].append(title)
            continue
        doi = str(record.get("doi") or "").strip().casefold()
        if not doi:
            skipped["no_doi"].append(title)
            continue
        if matching_daily_records(daily_index, record):
            skipped["already_in_daily"].append(title)
            continue
        identity = f"doi:{doi}|{title.casefold()}"
        if identity in added_identities:
            skipped["already_in_daily"].append(title)
            continue
        archive_date = archive_date_for_seen_record(record, run_date)
        if not archive_date:
            skipped["no_official_date"].append(title)
            continue
        candidate = copy.deepcopy(record)
        if not candidate.get("source_type"):
            candidate["source_type"] = "journal"
        if not candidate.get("journal_id"):
            journal_name = normalize_text(str(candidate.get("journal") or ""))
            matched_id = formal_names.get(journal_name) if journal_name else None
            if matched_id:
                candidate["journal_id"] = matched_id
        sanitize_record_paths([candidate])
        normalize_public_record(candidate)
        pending_by_date[archive_date].append(candidate)
        added_identities.add(identity)
        archived.append(
            {
                "doi": candidate.get("doi"),
                "title": title,
                "archive_date": archive_date,
                "file": f"{archive_date}.json",
            }
        )

    changed_files: set[str] = set()
    for archive_date, dated_records in sorted(pending_by_date.items()):
        path = daily_dir / f"{archive_date}.json"
        existing = read_json(path, [])
        if not isinstance(existing, list):
            existing = []
        merged = merge_daily(existing, dated_records)
        write_json(path, merged)
        changed_files.add(path.name)

    return {
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "run_date": run_date,
        "archived_count": len(archived),
        "archived": archived,
        "changed_files": sorted(changed_files),
        "skipped": {key: len(value) for key, value in skipped.items()},
        "skipped_examples": {
            key: value[:5]
            for key, value in skipped.items()
            if value
        },
        "note": (
            "Only journal records with a DOI and an official date are archived; "
            "records without an official date stay honest and are not fabricated."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--date", default="")
    args = parser.parse_args()
    report = repair_seen_only_archives(
        data_dir=args.data_dir,
        run_date=args.date or None,
    )
    print(
        f"seen-only archive repair archived={report['archived_count']} "
        f"files={len(report['changed_files'])} "
        f"skipped={report['skipped']}"
    )


if __name__ == "__main__":
    main()
