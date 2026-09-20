"""Deduplicate raw fetched records and write daily new-paper archives."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

import re

from common import BEIJING_TZ, DATA_DIR, normalize_doi, normalized_url_identity_keys, read_json, stable_id, today_str, write_json
from artifact_paths import repo_relative_path
from status import record_run, record_source


ENRICH_FIELDS = [
    "title_zh",
    "abstract",
    "abstract_zh",
    "authors",
    "publisher",
    "published_online",
    "available_online",
    "accepted_date",
    "issue_date",
    "source_issue",
    "date_precision",
    "date_source",
    "date_confidence",
    "doi",
    "url",
    "pdf_url",
]

DATE_SOURCE_RANK = {
    None: 0,
    "": 0,
    "issue_only": 1,
    "file_upload_date": 2,
    "crossref_published": 2,
    "crossref_created": 2,
    "crossref_issue": 2,
    "crossref_elsevier_created_online": 3,
    "crossref_doi_elsevier_created_online": 3,
    "crossref_doi_published_online": 3,
    "crossref_doi_published": 2,
    "crossref_doi_issue": 2,
    "crossref_published_online": 3,
    "publisher_meta": 4,
    "publisher_published_online": 4,
    "publisher_available_online": 4,
    "publisher_accepted_date": 4,
    "official_publish_date": 3,
    "rss_published": 4,
    "rss_description_online": 4,
    "cnki_rss_pubdate": 3,
    "world_bank_detail_api": 4,
    "iza_detail_month": 3,
    "repec_series_year": 2,
    "repec_detail_year": 3,
    "nep_issue_date": 3,
    "nep_first_seen_issue_date": 4,
    }


def iter_raw_records(raw_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not raw_dir.exists():
        return records
    for path in sorted(raw_dir.rglob("*.json")):
        payload = read_json(path, [])
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    if is_source_navigation_noise(item):
                        continue
                    # Keep an auditable repository-relative marker instead of an
                    # operator/CI absolute path so it can never leak a machine.
                    item["_raw_file"] = repo_relative_path(str(path))
                    records.append(item)
    return records


def is_source_navigation_noise(record: dict[str, Any]) -> bool:
    """Reject publisher navigation pages accidentally parsed as articles."""
    title = " ".join(str(record.get("title") or "").split()).casefold()
    source_id = str(record.get("source_id") or "")
    url = str(record.get("url") or "").casefold()
    doi = normalize_doi(record.get("doi"))
    # This DOI prefix belongs to an unrelated Indonesian journal that was
    # previously misclassified as Chicago's Journal of Law & Economics.
    if doi and doi.startswith("10.56347/jle."):
        return True
    if not title and not url and not doi:
        return True
    if doi in {"10.1111/jofi.70063", "10.1016/j.euroecorev.2026.105420"} or any(
        marker in url for marker in ("10.1111/jofi.70063", "10.1016/j.euroecorev.2026.105420")
    ):
        return True
    if (source_id == "brookings-economic-studies" or "brookings.edu/" in url) and "/articles/" not in url:
        return True
    if source_id == "voxeu-cepr-columns" and "/voxeu/" not in url:
        return True
    navigation_titles = {
        "supplemental appendix",
        "subscriptions",
        "annual reports",
        "editorial procedures and policies",
        "reviewer guidelines",
        "submission guidelines",
        "editorial board",
        "forthcoming papers",
        "about econometrica",
        "about the journal",
        "contact us",
        "issue information",
        "front matter",
        "back matter",
        "cover",
        "contents",
        "announcements",
        "correction",
        "editors' report",
        "editors’ report",
        "editors' notes",
        "editors’ notes",
        "american finance association",
        "report of independent auditor",
    }
    navigation_fragments = (
        "frontmatter of ",
        "backmatter of ",
        "recent referees",
        "turnaround times",
        "outstanding doctoral dissertation award",
        "issue information",
        "submission of manuscripts to ",
        "annual report of the president",
        "report of the editor of ",
        "correction to",
        "distinguished fellow",
        "distinguished life member",
        "cover and back matter",
        "cover and front matter",
        "abstracts of papers presented at",
        "summaries of doctoral dissertations",
        "prize in economic sciences in memory of alfred nobel",
        "学院简介",
        "出版社重点系列书",
    )
    journal_annual_report = bool(
        re.search(r"\breview of economics and statistics\b.*\bannual report\b", title)
    )
    return (
        title in navigation_titles
        or journal_annual_report
        or any(fragment in title for fragment in navigation_fragments)
    )


def merge_daily(existing: list[dict[str, Any]], new_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in existing + new_records:
        record_id = record.get("id") or stable_id(record)
        record["id"] = record_id
        match_id = find_matching_record_id(merged, record) or record_id
        if match_id in merged:
            enrich_record(merged[match_id], record)
        else:
            merged[record_id] = record
    return sorted(
        merged.values(),
        key=lambda item: (item.get("published_online") or "", item.get("detected_at") or ""),
        reverse=True,
    )


def valid_iso_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", text):
        return text
    return None


def discovery_date_for_run(value: Any) -> str:
    """Resolve a discovery timestamp to the Beijing calendar used by Daily."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    if stamp.tzinfo is None:
        return text[:10]
    return stamp.astimezone(BEIJING_TZ).date().isoformat()


def archive_date_for_new_record(record: dict[str, Any], run_date: str) -> str | None:
    """Decide which daily archive should receive a newly seen record.

    RSS feeds often expose a long back catalog. On the first day a feed is
    enabled, those historical items are newly seen by the system but are not
    today's papers. Put dated RSS records under their source date and suppress
    undated RSS records from public daily archives.
    """
    source = str(record.get("source") or "")
    if source == "cnki-rss":
        source_date = valid_iso_date(record.get("available_online")) or valid_iso_date(record.get("published_online"))
        return source_date or None
    if source == "openalex_recall":
        # OpenAlex is a recall-only audit path. It can identify records that
        # authoritative sources missed, but it cannot establish first online
        # or first discovery for the public daily flow.
        return None
    if source in {"crossref", "priority_toc", "aea_toc"}:
        official_date = valid_iso_date(record.get("available_online")) or valid_iso_date(record.get("published_online"))
        if official_date:
            return official_date if official_date <= run_date else None
        issue_date = valid_iso_date(record.get("issue_date"))
        if issue_date:
            return issue_date if issue_date <= run_date else None
        raw_data = record.get("raw_data") if isinstance(record.get("raw_data"), dict) else {}
        if (
            source == "aea_toc"
            and str(record.get("date_source") or "") == "aea_forthcoming"
            and raw_data.get("aea_first_observed") is True
        ):
            return run_date
        return None
    if source != "rss":
        source_id = str(record.get("source_id") or "")
        if source_id.startswith("repec-nep-"):
            return valid_iso_date(record.get("available_online")) or valid_iso_date(record.get("published_online")) or run_date
        return run_date
    date_source = str(record.get("date_source") or (record.get("raw_data") or {}).get("crossref_date_source") or "")
    issue_only = "issue" in date_source or date_source in {"issue_only", "crossref_published", "crossref_created"}
    official = None if issue_only else valid_iso_date(record.get("available_online")) or valid_iso_date(record.get("published_online"))
    if not official:
        # An undated publisher RSS item may be a backfill or a navigation
        # result. Keep it available for later enrichment/seen dedupe, but do
        # not claim it was first discovered today without date evidence.
        return None
    # A feed may expose a forthcoming issue date before the paper is officially
    # online. Keep it in the seen catalogue, but never create a public future
    # archive or let it appear in today's discovery flow.
    return official if official <= run_date else None


def has_value(value: Any) -> bool:
    return value is not None and value != "" and value != []


def is_bad_abstract(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = " ".join(value.split()).casefold()
    boilerplates = [
        "founded in 1920, the nber is a private",
        "the federal reserve board of governors in washington dc",
    ]
    return any(fragment in normalized for fragment in boilerplates)


def looks_like_abstract_title(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = " ".join(value.split())
    if not text:
        return False
    lowered = text.casefold()
    return len(text) > 260 or lowered.startswith(
        (
            "this paper ",
            "this study ",
            "we analyze ",
            "we analyse ",
            "we examine ",
            "we investigate ",
            "using data ",
            "based on ",
        )
    )


def is_repec_placeholder(value: Any) -> bool:
    text = str(value or "")
    return "RePEc NEP" in text and " item p" in text

_FULLWIDTH_TO_ASCII = str.maketrans({
    "：": ":", "，": ",", "（": "(", "）": ")", "；": ";", "、": ",", "　": " ",
})


def title_key_text(value: Any) -> str:
    """Collapse whitespace and unify common fullwidth punctuation for identity keys."""
    return " ".join(str(value or "").casefold().translate(_FULLWIDTH_TO_ASCII).split())


def record_match_keys(record: dict[str, Any]) -> set[str]:
    keys: set[str] = {record.get("id") or stable_id(record)}
    source = str(record.get("source") or "").casefold()
    doi = normalize_doi(record.get("doi"))
    if doi:
        keys.add(f"doi:{doi}")
    raw = record.get("raw_data") if isinstance(record.get("raw_data"), dict) else {}
    pii = str(raw.get("pii") or record.get("pii") or "").strip().casefold()
    if pii and re.fullmatch(r"s[0-9a-z]+", pii):
        keys.add(f"pii:{pii}")
    url = str(record.get("url") or "").strip().rstrip("/")
    if url:
        normalized_url = url.casefold()
        for identity_url in normalized_url_identity_keys(url):
            keys.add(f"url:{identity_url}")
        for pattern in (
            r"nber\.org/papers/(w\d+)",
            r"iza\.org/publications/dp/(\d+)",
            r"cepr\.org/publications/(dp\d+)",
            r"federalreserve\.gov/econres/feds/([^/.?#]+)",
            r"econpapers\.repec\.org/repec:([^?#]+)",
            r"ideas\.repec\.org/p/([^?#]+)",
        ):
            match = re.search(pattern, normalized_url, flags=re.I)
            if match:
                keys.add(f"urlpaper:{match.group(1).strip('/').casefold()}")
    title = title_key_text(record.get("title"))
    journal = title_key_text(record.get("journal"))
    if record.get("source_type") == "journal" and title and journal and len(title) > 20:
        keys.add(f"journal-title:{journal}:{title}")
    source_scope = str(record.get("source_id") or record.get("journal_id") or source or journal).casefold()
    if title and source_scope and len(title) > 20 and not is_repec_placeholder(title):
        # Title identity is scoped to the journal or working-paper series. The
        # same title at CEPR/NBER and in a journal is a version relationship,
        # not a duplicate that should be deleted.
        keys.add(f"source-title:{source_scope}:{title}")
    source_id = str(record.get("source_id") or "")
    paper_number = str(record.get("paper_number") or "")
    if source_id and paper_number:
        if source_id.casefold().startswith("repec-nep-"):
            # NEP reuses p1, p2, ... in every weekly issue.  The issue URL is
            # therefore part of the paper identity; otherwise a new issue can
            # enrich an older, unrelated paper with the new paper's abstract.
            issue_url = str(record.get("source_url") or url.split("#", 1)[0]).strip().rstrip("/").casefold()
            if issue_url:
                keys.add(f"paper:{source_id.casefold()}:{issue_url}:{paper_number.casefold()}")
        else:
            keys.add(f"paper:{source_id.casefold()}:{paper_number.casefold()}")
    if source in {"cnki-rss", "cn-official"}:
        if title and journal:
            keys.add(f"cnki-title:{journal}:{title}")
    return keys


def find_matching_record_id(records: dict[str, dict[str, Any]], incoming: dict[str, Any]) -> str | None:
    incoming_keys = record_match_keys(incoming)
    for record_id, existing in records.items():
        if incoming_keys & record_match_keys(existing):
            return record_id
    return None


def covered_by_derived_doi(record: dict[str, Any], daily_records: list[dict[str, Any]]) -> bool:
    """True when a combined Crossref item (e.g. JEL 'Book Reviews') is covered
    by split child records in the daily archive whose DOIs derive from the
    parent DOI (parent + '.rN'). Prevents ingestion/formal audits from flagging
    the parent as a missing candidate after dedupe split it into children."""
    doi = normalize_doi(record.get("doi"))
    if not doi:
        return False
    prefix = f"{doi}."
    return any(str(item.get("doi") or "").startswith(prefix) for item in daily_records)


def build_seen_index(seen_papers: dict[str, dict[str, Any]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for record_id, record in seen_papers.items():
        record.setdefault("id", record_id)
        for key in record_match_keys(record):
            index.setdefault(key, record_id)
    return index


def find_matching_seen_id(index: dict[str, str], incoming: dict[str, Any]) -> str | None:
    for key in record_match_keys(incoming):
        if key in index:
            return index[key]
    return None


def add_seen_index(index: dict[str, str], record_id: str, record: dict[str, Any]) -> None:
    record.setdefault("id", record_id)
    for key in record_match_keys(record):
        index.setdefault(key, record_id)


def build_daily_index(daily_dir: Path) -> tuple[dict[Path, list[dict[str, Any]]], dict[str, list[tuple[Path, dict[str, Any]]]]]:
    path_records: dict[Path, list[dict[str, Any]]] = {}
    index: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for path in daily_dir.glob("*.json"):
        records = read_json(path, [])
        if not isinstance(records, list):
            continue
        path_records[path] = records
        for record in records:
            for key in record_match_keys(record):
                index.setdefault(key, []).append((path, record))
    return path_records, index


def prune_navigation_noise_from_seen(seen_papers: dict[str, dict[str, Any]]) -> int:
    removed = 0
    for record_id, record in list(seen_papers.items()):
        if is_source_navigation_noise(record):
            seen_papers.pop(record_id, None)
            removed += 1
    return removed


def collapse_seen_duplicates(seen_papers: dict[str, dict[str, Any]]) -> int:
    """Merge aliases already present in the persistent seen-paper index."""
    index: dict[str, str] = {}
    removed = 0
    for record_id, record in list(seen_papers.items()):
        record.setdefault("id", record_id)
        match_id = find_matching_seen_id(index, record)
        if match_id is None:
            add_seen_index(index, record_id, record)
            continue
        existing = seen_papers[match_id]
        enrich_record(existing, record)
        first_seen = min(
            (value for value in (existing.get("first_seen"), record.get("first_seen")) if value),
            default=None,
        )
        if first_seen:
            existing["first_seen"] = first_seen
        seen_papers.pop(record_id, None)
        add_seen_index(index, match_id, existing)
        removed += 1
    return removed


def collapse_daily_duplicates(daily_dir: Path) -> int:
    """Collapse aliases across all canonical day files into the earliest bucket."""
    canonical: dict[str, dict[str, Any]] = {}
    owner_paths: dict[str, Path] = {}
    key_owner: dict[str, str] = {}
    path_payloads: dict[Path, list[dict[str, Any]]] = {}
    changed: dict[Path, list[dict[str, Any]]] = {}
    removed = 0

    for path in sorted(daily_dir.glob("*.json")):
        records = read_json(path, [])
        if not isinstance(records, list):
            continue
        kept: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                kept.append(record)
                continue
            keys = record_match_keys(record)
            owner_id = next((key_owner[key] for key in keys if key in key_owner), None)
            if owner_id is None:
                owner_id = str(record.get("id") or stable_id(record))
                record["id"] = owner_id
                canonical[owner_id] = record
                owner_paths[owner_id] = path
                kept.append(record)
                for key in keys:
                    key_owner.setdefault(key, owner_id)
                continue

            owner = canonical[owner_id]
            owner_changed = enrich_record(owner, record)
            first_seen = min(
                (value for value in (owner.get("first_seen"), record.get("first_seen")) if value),
                default=None,
            )
            if first_seen:
                owner_changed = owner.get("first_seen") != first_seen or owner_changed
                owner["first_seen"] = first_seen
            if owner.get("source") == "cnki-rss" and record.get("source") == "cn-official":
                owner_changed = owner.get("url") != record.get("url") or owner_changed
                owner["url"] = record.get("url") or owner.get("url")
                owner["source_url"] = record.get("source_url") or owner.get("source_url")
            if owner_changed:
                owner_path = owner_paths[owner_id]
                if owner_path in path_payloads:
                    changed[owner_path] = path_payloads[owner_path]
            for key in record_match_keys(owner) | keys:
                key_owner.setdefault(key, owner_id)
            removed += 1
        if len(kept) != len(records):
            changed[path] = kept
        path_payloads[path] = kept

    for path, records in changed.items():
        write_json(path, records)
    return removed


def prune_navigation_noise_from_daily(daily_dir: Path) -> int:
    removed = 0
    for path in daily_dir.glob("*.json"):
        records = read_json(path, [])
        if not isinstance(records, list):
            continue
        filtered = [record for record in records if not is_source_navigation_noise(record)]
        if len(filtered) == len(records):
            continue
        removed += len(records) - len(filtered)
        write_json(path, filtered)
    return removed


def seed_seen_from_daily_match(record: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    seed = {
        "title": existing.get("title") or record.get("title"),
        "journal": existing.get("journal") or record.get("journal"),
        "doi": existing.get("doi") or record.get("doi"),
        "url": existing.get("url") or record.get("url"),
        "first_seen": existing.get("detected_at") or record.get("detected_at"),
    }
    enrich_record(seed, existing)
    enrich_record(seed, record)
    return seed


def matching_daily_records(
    index: dict[str, list[tuple[Path, dict[str, Any]]]],
    record: dict[str, Any],
) -> list[tuple[Path, dict[str, Any]]]:
    matches: list[tuple[Path, dict[str, Any]]] = []
    seen: set[tuple[Path, int]] = set()
    for key in record_match_keys(record):
        for path, existing in index.get(key, []):
            marker = (path, id(existing))
            if marker in seen:
                continue
            seen.add(marker)
            matches.append((path, existing))
    return matches


def enrich_record(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    changed = False
    if incoming.get("source_id") == "world-bank-prwp" and has_value(incoming.get("title")) and existing.get("title") != incoming.get("title"):
        existing["title"] = incoming["title"]
        existing.pop("title_zh", None)
        existing.pop("translation_status", None)
        changed = True
    if (
        incoming.get("source_id") == "fed-feds"
        and has_value(incoming.get("title"))
        and str(existing.get("title") or "").strip().casefold() == "board of governors of the federal reserve system"
        and str(incoming.get("title") or "").strip().casefold() != "board of governors of the federal reserve system"
    ):
        existing["title"] = incoming["title"]
        existing.pop("title_zh", None)
        existing.pop("translation_status", None)
        changed = True
    if (
        incoming.get("source_id") == "world-bank-prwp"
        and existing.get("abstract")
        and existing.get("abstract_source") != "world_bank_detail_api"
        and incoming.get("abstract_source") != "world_bank_detail_api"
    ):
        existing["abstract"] = None
        changed = True
    if (
        str(incoming.get("source_id") or "").startswith("repec-nep-")
        and has_value(incoming.get("title"))
        and not looks_like_abstract_title(incoming.get("title"))
        and (is_repec_placeholder(existing.get("title")) or looks_like_abstract_title(existing.get("title")))
    ):
        existing["title"] = incoming["title"]
        existing["url"] = incoming.get("url") or existing.get("url")
        existing.pop("title_zh", None)
        existing.pop("translation_status", None)
        existing.pop("title_parse_status", None)
        existing.pop("public_visible", None)
        changed = True
    if incoming.get("source_id") == "world-bank-prwp" and has_value(incoming.get("authors")) and existing.get("authors") != incoming.get("authors"):
        existing["authors"] = incoming["authors"]
        changed = True
    incoming_date_source = incoming.get("date_source")
    existing_date_source = existing.get("date_source")
    if has_value(incoming.get("published_online")) and (
        not has_value(existing.get("published_online"))
        or DATE_SOURCE_RANK.get(incoming_date_source, 1) > DATE_SOURCE_RANK.get(existing_date_source, 1)
        or incoming_date_source == existing_date_source
    ):
        if existing.get("published_online") != incoming.get("published_online"):
            existing["published_online"] = incoming["published_online"]
            changed = True
        if incoming_date_source and existing.get("date_source") != incoming_date_source:
            existing["date_source"] = incoming_date_source
            changed = True
        if has_value(incoming.get("date_confidence")) and existing.get("date_confidence") != incoming.get("date_confidence"):
            existing["date_confidence"] = incoming["date_confidence"]
            changed = True
    if is_bad_abstract(existing.get("abstract")) and not has_value(incoming.get("abstract")):
        existing["abstract"] = None
        changed = True
    for field in ENRICH_FIELDS:
        if field == "abstract" and is_bad_abstract(existing.get(field)) and has_value(incoming.get(field)) and not is_bad_abstract(incoming.get(field)):
            existing[field] = incoming[field]
            changed = True
            continue
        if not has_value(existing.get(field)) and has_value(incoming.get(field)):
            existing[field] = incoming[field]
            changed = True
    if existing.get("translation_status") in {None, "missing_abstract"} and incoming.get("translation_status") == "native_chinese":
        existing["translation_status"] = incoming["translation_status"]
        changed = True
    return changed


def enrich_existing_daily(daily_dir: Path, record: dict[str, Any]) -> bool:
    record_id = stable_id(record)
    incoming_keys = record_match_keys(record)
    changed = False
    for path in daily_dir.glob("*.json"):
        records = read_json(path, [])
        path_changed = False
        for existing in records:
            existing_keys = record_match_keys(existing)
            if (existing.get("id") or stable_id(existing)) == record_id or incoming_keys & existing_keys:
                if enrich_record(existing, record):
                    path_changed = True
                    changed = True
        if path_changed:
            write_json(path, records)
    return changed


def exists_in_daily(daily_dir: Path, record: dict[str, Any]) -> bool:
    record_id = record.get("id") or stable_id(record)
    incoming_keys = record_match_keys(record)
    for path in daily_dir.glob("*.json"):
        records = read_json(path, [])
        for existing in records:
            existing_keys = record_match_keys(existing)
            if (existing.get("id") or stable_id(existing)) == record_id or incoming_keys & existing_keys:
                return True
    return False


def ensure_daily_archive(
    daily_dir: Path,
    record: dict[str, Any],
    run_date: str,
    *,
    allow_same_run_journal_sources: bool = False,
    canonical_first_seen: str | None = None,
) -> bool:
    source = str(record.get("source") or "")
    source_id = str(record.get("source_id") or "")
    eligible = source in {"rss", "cnki-rss"} or source_id.startswith("repec-nep-")
    if allow_same_run_journal_sources and source in {"crossref", "priority_toc", "aea_toc"}:
        eligible = True
    if not eligible:
        return False
    archive_date = archive_date_for_new_record(record, run_date)
    if not archive_date or exists_in_daily(daily_dir, record):
        return False
    archive_record = dict(record)
    if canonical_first_seen:
        archive_record["first_seen"] = canonical_first_seen
        if archive_record.get("first_seen_at"):
            archive_record["first_seen_at"] = min(
                str(archive_record["first_seen_at"]),
                canonical_first_seen,
            )
    daily_path = daily_dir / f"{archive_date}.json"
    existing_daily = read_json(daily_path, [])
    write_json(daily_path, merge_daily(existing_daily, [archive_record]))
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, default=DATA_DIR / "raw")
    parser.add_argument("--seen", type=Path, default=DATA_DIR / "seen.json")
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--date", default=today_str())
    args = parser.parse_args()

    seen = read_json(args.seen, {"papers": {}})
    seen_papers = seen.setdefault("papers", {})
    pruned = prune_navigation_noise_from_seen(seen_papers)
    pruned += collapse_seen_duplicates(seen_papers)
    pruned += prune_navigation_noise_from_daily(args.daily_dir)
    pruned += collapse_daily_duplicates(args.daily_dir)
    seen_index = build_seen_index(seen_papers)
    daily_records_by_path, daily_index = build_daily_index(args.daily_dir)
    touched_daily_paths: set[Path] = set()
    new_records_by_date: dict[str, list[dict[str, Any]]] = {}
    enriched = 0
    suppressed = 0

    for record in iter_raw_records(args.raw_dir):
        record_id = stable_id(record)
        record["id"] = record_id
        seen_id = record_id
        if record_id not in seen_papers:
            seen_id = find_matching_seen_id(seen_index, record) or record_id
        daily_matches = matching_daily_records(daily_index, record)
        if seen_id not in seen_papers and daily_matches:
            _, first_existing = daily_matches[0]
            seen_papers[seen_id] = seed_seen_from_daily_match(record, first_existing)
            add_seen_index(seen_index, seen_id, seen_papers[seen_id])
        if seen_id in seen_papers:
            seen_entry = seen_papers[seen_id]
            if enrich_record(seen_entry, record):
                enriched += 1
                add_seen_index(seen_index, seen_id, seen_entry)
            if daily_matches:
                for path, existing in daily_matches:
                    if enrich_record(existing, record):
                        enriched += 1
                        touched_daily_paths.add(path)
            else:
                # A record already discovered before the run date is backflow
                # from an RSS/TOC re-publication, not a new discovery. Do not
                # re-archive it into today's bucket: that pollutes the daily
                # archive and forces remove_seen_backflow to move it out again,
                # leaving the bucket empty and tripping ingestion gates at
                # month boundaries (issue-dated papers from a new volume).
                canonical_first_seen = str(
                    (seen_entry or {}).get("first_seen")
                    or (seen_entry or {}).get("first_seen_at")
                    or (seen_entry or {}).get("detected_at")
                    or ""
                )
                seen_first = discovery_date_for_run(canonical_first_seen)
                is_backflow = bool(seen_first and seen_first < args.date)
                same_run_seen = seen_first == args.date
                if not is_backflow and ensure_daily_archive(
                    args.daily_dir,
                    record,
                    args.date,
                    allow_same_run_journal_sources=same_run_seen,
                    canonical_first_seen=canonical_first_seen or None,
                ):
                    enriched += 1
                    daily_records_by_path, daily_index = build_daily_index(args.daily_dir)
            continue
        seen_papers[record_id] = {
            "title": record.get("title"),
            "journal": record.get("journal"),
            "doi": record.get("doi"),
            "url": record.get("url"),
            "first_seen": record.get("detected_at"),
        }
        enrich_record(seen_papers[record_id], record)
        add_seen_index(seen_index, record_id, seen_papers[record_id])
        record.pop("_raw_file", None)
        archive_date = archive_date_for_new_record(record, args.date)
        if archive_date:
            new_records_by_date.setdefault(archive_date, []).append(record)
        else:
            suppressed += 1

    for path in sorted(touched_daily_paths):
        write_json(path, daily_records_by_path[path])

    daily_total = 0
    for archive_date, dated_records in sorted(new_records_by_date.items()):
        daily_path = args.daily_dir / f"{archive_date}.json"
        existing_daily = read_json(daily_path, [])
        daily_records = merge_daily(existing_daily, dated_records)
        write_json(daily_path, daily_records)
        if archive_date == args.date:
            daily_total = len(daily_records)
    if args.date not in new_records_by_date:
        daily_path = args.daily_dir / f"{args.date}.json"
        daily_total = len(read_json(daily_path, []))

    write_json(args.seen, seen)
    new_total = sum(len(items) for items in new_records_by_date.values())
    record_source("dedupe", ok=True, count=new_total, message=f"daily_total={daily_total} seen={len(seen_papers)} enriched={enriched} suppressed={suppressed} pruned={pruned}")
    record_run({"new": new_total, "daily_total": daily_total, "seen": len(seen_papers), "enriched": enriched, "suppressed": suppressed, "pruned": pruned})
    print(f"new={new_total} daily_total={daily_total} seen={len(seen_papers)} enriched={enriched} suppressed={suppressed} pruned={pruned}")


if __name__ == "__main__":
    main()
