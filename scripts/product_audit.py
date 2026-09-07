"""Product-quality audit for monitored paper records.

The audit is intentionally data-facing: it reports issues that affect what a
reader sees on the public site, not crawler implementation details.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from common import DATA_DIR, load_journals, read_json, today_str, write_json
from dedupe import is_source_navigation_noise, record_match_keys
from metadata_expectations import expected_missing_reason


CN_JOURNAL_IDS = {
    "journal-379b4022ce",
    "journal-edcb877d78",
    "journal-bf2aa9381f",
    "journal-f69300dae2",
    "journal-679eaa2a0c",
    "journal-ba9f46c919",
}


def has_chinese(value: str | None) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in value or "")


def is_working_paper(record: dict[str, Any]) -> bool:
    source_type = str(record.get("source_type") or "")
    return str(record.get("source") or "") == "working_papers" or source_type in {
        "working_paper", "policy_paper", "policy_commentary", "aggregator"
    }


def is_cn_journal(record: dict[str, Any]) -> bool:
    return str(record.get("journal_id") or "") in CN_JOURNAL_IDS or str(record.get("source") or "") == "cn-official"


def official_date(record: dict[str, Any]) -> str:
    return str(
        record.get("available_online")
        or record.get("published_online")
        or record.get("issue_date")
        or ""
    )


def online_date(record: dict[str, Any]) -> str:
    return str(record.get("available_online") or record.get("published_online") or "")


def parse_iso_date(value: str | None) -> date | None:
    """Parse a full ISO date; month-year labels (e.g. ``August 2026``) are not dates."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def valid_date_value(field: str, value: Any) -> bool:
    text = str(value or "").strip()
    if field == "issue_date" and re.fullmatch(r"\d{4}-\d{2}", text):
        return True
    try:
        return len(text) == 10 and date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def malformed_dates(record: dict[str, Any]) -> list[str]:
    bad: list[str] = []
    for field in ("accepted_date", "available_online", "published_online", "issue_date"):
        value = str(record.get(field) or "").strip()
        if not value:
            continue
        if not valid_date_value(field, value):
            bad.append(field)
    return bad


def malformed_first_seen(record: dict[str, Any]) -> bool:
    value = record.get("first_seen_at") or record.get("first_seen") or record.get("detected_at")
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return True
    return parsed.tzinfo is None


def canonical_date_age(record: dict[str, Any]) -> int | None:
    bucket = str(record.get("_daily_date") or "")
    official = official_date(record)
    try:
        return (date.fromisoformat(bucket) - date.fromisoformat(official)).days
    except ValueError:
        return None


def canonical_online_date_age(record: dict[str, Any]) -> int | None:
    bucket = str(record.get("_daily_date") or "")
    try:
        return (date.fromisoformat(bucket) - date.fromisoformat(online_date(record))).days
    except ValueError:
        return None


def strong_identity_keys(record: dict[str, Any]) -> set[str]:
    return {
        key
        for key in record_match_keys(record)
        if key.startswith(("doi:", "url:", "urlpaper:", "journal-title:", "working-title:", "cnki-title:"))
    }


def duplicate_groups(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    owners: dict[str, int] = {}
    groups: dict[int, list[dict[str, Any]]] = {}
    for index, record in enumerate(records):
        matched = {owners[key] for key in strong_identity_keys(record) if key in owners}
        owner = min(matched) if matched else index
        groups.setdefault(owner, []).append(record)
        for key in strong_identity_keys(record):
            owners.setdefault(key, owner)
    return [group for group in groups.values() if len(group) > 1]


def looks_like_abstract(value: str | None) -> bool:
    text = " ".join(str(value or "").split())
    lowered = text.casefold()
    starts = (
        "this paper ",
        "this study ",
        "we analyze ",
        "we analyse ",
        "we examine ",
        "we investigate ",
        "using data ",
        "based on ",
    )
    return len(text) > 260 or any(lowered.startswith(prefix) for prefix in starts)


def daily_paths(daily_dir: Path) -> list[Path]:
    return sorted(daily_dir.glob("*.json"), reverse=True)


def load_records(daily_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in daily_paths(daily_dir):
        payload = read_json(path, [])
        if not isinstance(payload, list):
            continue
        for record in payload:
            record = dict(record)
            record["_daily_date"] = path.stem
            records.append(record)
    return records


def record_label(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": record.get("_daily_date"),
        "title": record.get("title"),
        "journal": record.get("journal"),
        "url": record.get("url") or (f"https://doi.org/{record.get('doi')}" if record.get("doi") else None),
        "date_confidence": record.get("date_confidence"),
        "date_source": record.get("date_source"),
        "official_date": official_date(record),
        "source_issue": record.get("source_issue"),
    }


def expected_gap_label(record: dict[str, Any], field: str) -> dict[str, Any]:
    return {
        **record_label(record),
        "expectation_reason": expected_missing_reason(record, field),
    }


def audit(records: list[dict[str, Any]], formal_journal_ids: set[str] | None = None) -> dict[str, Any]:
    today = today_str()
    today_date = date.fromisoformat(today)
    today_records = [record for record in records if record.get("_daily_date") == today]
    journal_today = [record for record in today_records if not is_working_paper(record)]
    working_today = [record for record in today_records if is_working_paper(record)]
    confidence = Counter(str(record.get("date_confidence") or "unknown") for record in records)
    date_source = Counter(str(record.get("date_source") or "unknown") for record in records)
    by_source = Counter(str(record.get("source") or record.get("source_id") or "unknown") for record in records)
    missing_abstract = [record for record in records if not str(record.get("abstract") or "").strip()]
    missing_abstract_today = [record for record in today_records if not str(record.get("abstract") or "").strip()]
    recent_records = records[:500]
    missing_abstract_recent = [record for record in recent_records if not str(record.get("abstract") or "").strip()]
    missing_abstract_recent_expected = [
        record for record in missing_abstract_recent if expected_missing_reason(record, "abstract")
    ]
    missing_abstract_recent_actionable = [
        record for record in missing_abstract_recent if not expected_missing_reason(record, "abstract")
    ]
    missing_authors = [record for record in records if not record.get("authors")]
    missing_authors_today = [record for record in today_records if not record.get("authors")]
    missing_authors_recent = [record for record in recent_records if not record.get("authors")]
    missing_authors_recent_expected = [
        record for record in missing_authors_recent if expected_missing_reason(record, "authors")
    ]
    missing_authors_recent_actionable = [
        record for record in missing_authors_recent if not expected_missing_reason(record, "authors")
    ]
    missing_authors_today_journals = [record for record in journal_today if not record.get("authors")]
    missing_abstract_by_journal = Counter(str(record.get("journal") or record.get("source_id") or "unknown") for record in missing_abstract)

    duplicates = duplicate_groups(records)

    low_conf_today = [
        record
        for record in today_records
        if str(record.get("date_confidence") or "F") in {"D", "F", "unknown"}
    ]
    cn_issue_only_today = [
        record
        for record in journal_today
        if is_cn_journal(record)
        and str(record.get("date_source") or "") == "issue_only"
        and not official_date(record)
    ]
    abstract_titles = [record for record in records if looks_like_abstract(record.get("title"))]
    malformed_date_records = [record for record in records if malformed_dates(record)]
    malformed_first_seen_records = [record for record in records if malformed_first_seen(record)]
    historical_records = [
        record
        for record in records
        if canonical_date_age(record) is not None
        and canonical_date_age(record) > 14
        and str(record.get("date_confidence") or "") not in {"F", "unknown"}
    ]
    future_official_records = [
        record
        for record in records
        if (parsed_online := parse_iso_date(online_date(record))) is not None
        and parsed_online > today_date
        and str(record.get("date_confidence") or "") not in {"F", "unknown"}
    ]
    nonpaper_records = [record for record in records if is_source_navigation_noise(record)]
    required_fields = ("id", "title", "authors", "journal", "source", "source_type", "url", "fields")

    def missing_schema_fields(record: dict[str, Any]) -> list[str]:
        missing = [field for field in required_fields if field not in record]
        missing.extend(
            field
            for field in ("id", "title", "journal", "source", "source_type", "url")
            if field in record and not str(record.get(field) or "").strip()
        )
        return sorted(set(missing))

    missing_required = [
        {"record": record, "fields": missing_schema_fields(record)}
        for record in records
        if missing_schema_fields(record)
    ]
    allowed_source_types = {"journal", "working_paper", "policy_paper", "policy_commentary", "aggregator"}
    source_type_errors = [
        record
        for record in records
        if str(record.get("source_type") or "") not in allowed_source_types
        or (str(record.get("source") or "") == "working_papers" and str(record.get("source_type") or "") == "journal")
    ]
    invalid_journal_ids = [
        record
        for record in records
        if formal_journal_ids is not None
        and str(record.get("source_type") or "") == "journal"
        and str(record.get("journal_id") or "") not in formal_journal_ids
    ]
    untranslated_recent = [
        record
        for record in recent_records
        if record.get("title") and not has_chinese(str(record.get("title"))) and not record.get("title_zh")
    ]
    china_candidates = [record for record in records if record.get("china_relevance_status") == "candidate"]
    china_public = [record for record in records if record.get("china_related") is True or record.get("china_relevance_status") == "confirmed"]
    crossref_created_today = [
        record
        for record in today_records
        if "created" in str(record.get("date_source") or "").casefold()
        or "created" in str((record.get("raw_data") or {}).get("crossref_date_source") or "").casefold()
    ]
    crossref_fallback_today = [
        record for record in today_records if "crossref" in str(record.get("date_source") or "").casefold()
    ]
    cnki_rss_today = [
        record
        for record in today_records
        if str(record.get("source") or "").casefold() == "cnki-rss"
        or str(record.get("date_source") or "").casefold().startswith("cnki_rss")
    ]

    return {
        "generated_for": today,
        "totals": {
            "records": len(records),
            "today_records": len(today_records),
            "today_journal_records": len(journal_today),
            "today_working_papers": len(working_today),
            "recent_sample_records": len(recent_records),
            "china_related_public": len(china_public),
            "china_candidates": len(china_candidates),
            "duplicates_by_url_or_doi": len(duplicates),
            "crossref_created_today": len(crossref_created_today),
            "crossref_fallback_today": len(crossref_fallback_today),
            "cnki_rss_today": len(cnki_rss_today),
            "missing_abstract": len(missing_abstract),
            "missing_abstract_today": len(missing_abstract_today),
            "missing_abstract_recent": len(missing_abstract_recent),
            "missing_abstract_recent_actionable": len(missing_abstract_recent_actionable),
            "missing_abstract_recent_expected": len(missing_abstract_recent_expected),
            "missing_authors": len(missing_authors),
            "missing_authors_today": len(missing_authors_today),
            "missing_authors_today_journals": len(missing_authors_today_journals),
            "missing_authors_recent": len(missing_authors_recent),
            "missing_authors_recent_actionable": len(missing_authors_recent_actionable),
            "missing_authors_recent_expected": len(missing_authors_recent_expected),
            "historical_records_in_bucket": len(historical_records),
            "future_official_date_in_bucket": len(future_official_records),
            "nonpaper_records": len(nonpaper_records),
            "missing_required_fields": len(missing_required),
            "malformed_first_seen": len(malformed_first_seen_records),
            "source_type_errors": len(source_type_errors),
            "invalid_journal_ids": len(invalid_journal_ids),
        },
        "date_confidence": dict(confidence),
        "date_source_top": dict(date_source.most_common(20)),
        "source_top": dict(by_source.most_common(20)),
        "issues": {
            "today_low_confidence": [record_label(record) for record in low_conf_today[:50]],
            "today_cn_issue_only": [record_label(record) for record in cn_issue_only_today[:50]],
            "abstract_as_title": [record_label(record) for record in abstract_titles[:50]],
            "untranslated_recent": [record_label(record) for record in untranslated_recent[:50]],
            "missing_abstract_today": [record_label(record) for record in missing_abstract_today[:50]],
            "missing_abstract_recent": [record_label(record) for record in missing_abstract_recent[:50]],
            "missing_abstract_recent_actionable": [record_label(record) for record in missing_abstract_recent_actionable[:50]],
            "missing_abstract_recent_expected": [expected_gap_label(record, "abstract") for record in missing_abstract_recent_expected[:50]],
            "missing_authors_today": [record_label(record) for record in missing_authors_today[:50]],
            "missing_authors_recent": [record_label(record) for record in missing_authors_recent[:50]],
            "missing_authors_recent_actionable": [record_label(record) for record in missing_authors_recent_actionable[:50]],
            "missing_authors_recent_expected": [expected_gap_label(record, "authors") for record in missing_authors_recent_expected[:50]],
            "duplicate_examples": [[record_label(record) for record in group[:5]] for group in duplicates[:20]],
            "malformed_dates": [
                {**record_label(record), "fields": malformed_dates(record)}
                for record in malformed_date_records[:50]
            ],
            "malformed_first_seen": [record_label(record) for record in malformed_first_seen_records[:50]],
            "historical_records_in_bucket": [record_label(record) for record in historical_records[:50]],
            "future_official_date_in_bucket": [record_label(record) for record in future_official_records[:50]],
            "nonpaper_records": [record_label(record) for record in nonpaper_records[:50]],
            "missing_required_fields": [
                {**record_label(item["record"]), "fields": item["fields"]}
                for item in missing_required[:50]
            ],
            "source_type_errors": [record_label(record) for record in source_type_errors[:50]],
            "invalid_journal_ids": [record_label(record) for record in invalid_journal_ids[:50]],
        },
        "abstracts": {
            "total": len(records),
            "missing": len(missing_abstract),
            "available": len(records) - len(missing_abstract),
            "missing_rate": round(len(missing_abstract) / len(records), 4) if records else 0,
            "missing_today": len(missing_abstract_today),
            "missing_recent": len(missing_abstract_recent),
            "missing_recent_actionable": len(missing_abstract_recent_actionable),
            "missing_recent_expected": len(missing_abstract_recent_expected),
            "missing_by_journal_top": dict(missing_abstract_by_journal.most_common(30)),
        },
        "authors": {
            "total": len(records),
            "missing": len(missing_authors),
            "available": len(records) - len(missing_authors),
            "missing_today": len(missing_authors_today),
            "missing_today_journals": len(missing_authors_today_journals),
            "missing_recent": len(missing_authors_recent),
            "missing_recent_actionable": len(missing_authors_recent_actionable),
            "missing_recent_expected": len(missing_authors_recent_expected),
        },
        "risk_signals": {
            "crossref_created_today": [record_label(record) for record in crossref_created_today[:50]],
            "crossref_fallback_today": [record_label(record) for record in crossref_fallback_today[:50]],
            "cnki_rss_today": [record_label(record) for record in cnki_rss_today[:50]],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--output", type=Path, default=DATA_DIR / "quality_report.json")
    args = parser.parse_args()
    records = load_records(args.daily_dir)
    formal_journal_ids = {str(journal.get("id") or "") for journal in load_journals()}
    report = audit(records, formal_journal_ids)
    write_json(args.output, report)
    totals = report["totals"]
    print(
        "quality audit "
        f"records={totals['records']} today={totals['today_records']} "
        f"today_journals={totals['today_journal_records']} today_wp={totals['today_working_papers']} "
        f"duplicates={totals['duplicates_by_url_or_doi']}"
    )


if __name__ == "__main__":
    main()
