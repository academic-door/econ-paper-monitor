"""Repair and audit the public data contract without touching presentation files."""

from __future__ import annotations

import argparse
import copy
import hashlib
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from common import (
    DATA_DIR,
    clean_abstract_text,
    normalize_doi,
    normalized_url_identity_keys,
    read_json,
    write_json,
)
from artifact_paths import PATH_FIELDS, repo_relative_path, sanitize_record_paths
from dedupe import is_source_navigation_noise


LEDGER_PATHS = (
    "ingestion_exclusion_ledger.json",
    "ingestion_retry_queue.json",
    "historical_backfill_pending.json",
    "pending_date_records.json",
    "metadata_retry_queue.json",
)

TITLE_PREFIX_PATTERNS = (
    re.compile(
        r"^\s*(?:\[?\s*)?(?P<number>"
        r"(?:NBER\s+Working\s+Paper|CEPR\s+(?:Discussion\s+Paper|DP)|"
        r"Discussion\s+Paper|Working\s+Paper|NBER|DP|WP)\s*"
        r"(?:Series\s*)?(?:No\.?|Number|#)?\s*[:#.-]?\s*"
        r"(?:\d{2,6}|[A-Z]{1,5}\d{2,6})(?:[./-]\d+)*"
        r")\s*(?:\]?\s*)?[:：.\-–—]?\s*",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?P<number>(?:NBER|CEPR|DP|WP)\s*\d{2,6})\s*[:：.\-–—]?\s*",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?P<number>(?:NBER|CEPR)?\s*(?:工作论文|讨论稿)\s*(?:第)?\s*\d{2,6}\s*(?:号)?)\s*[:：.\-–—]?\s*",
        flags=re.IGNORECASE,
    ),
)

ABSTRACT_SUFFIXES = (
    re.compile(
        r"\s*This is (?:only )?a preview of subscription content,?\s*"
        r"log in via an institution to check access\.?\s*$",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\s*(?:Sign|Log) in (?:via an institution )?to (?:read|view|check access).*$",
        flags=re.IGNORECASE,
    ),
    re.compile(r"\s*Subscribe (?:now )?to (?:read|continue reading|access).*$", flags=re.IGNORECASE),
    re.compile(r"\s*(?:Purchase|Buy) this article to (?:read|view|access).*$", flags=re.IGNORECASE),
)

ABSTRACT_BOILERPLATE_FRAGMENTS = (
    "this is a preview of subscription content",
    "log in via an institution to check access",
    "sign in to access",
    "subscribe to read",
    "subscription required",
    "purchase this article",
    "institutional access",
    "enable javascript to continue",
    "access denied",
)

DATE_FIELDS = ("official_date", "available_online", "published_online", "issue_date")
RICH_TEXT_FIELDS = ("title_zh", "abstract_zh", "publisher", "source_issue", "pdf_url")


def normalized_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def source_scope(record: dict[str, Any]) -> str:
    for field in ("source_id", "journal_id", "source", "journal"):
        value = str(record.get(field) or "").strip().casefold()
        if value:
            return value
    return "unknown"


def catalogue_identity_keys(record: dict[str, Any]) -> set[str]:
    """Identities used when reconciling ledgers with the seen catalogue."""
    keys = strong_identity_keys(record)
    title = normalized_title(record.get("title"))
    if not title:
        return keys
    for field in ("journal", "source_name", "source"):
        name = normalized_title(record.get(field))
        if name and name not in {"crossref", "working papers", "rss"}:
            keys.add(f"catalogue-title:{name}:{title}")
    return keys


def strong_identity_keys(record: dict[str, Any], *, include_source_title: bool = True) -> set[str]:
    keys: set[str] = set()
    doi = normalize_doi(record.get("doi"))
    if doi:
        keys.add(f"doi:{doi}")
    url = str(record.get("url") or record.get("source_url") or "").strip()
    for normalized_url in normalized_url_identity_keys(url):
        keys.add(f"url:{normalized_url}")
    raw = record.get("raw_data") if isinstance(record.get("raw_data"), dict) else {}
    pii = str(raw.get("pii") or record.get("pii") or "").strip().casefold()
    if not pii:
        match = re.search(r"/pii/([a-z0-9]+)", url, flags=re.IGNORECASE)
        pii = match.group(1).casefold() if match else ""
    if pii:
        keys.add(f"pii:{pii}")
    paper_number = str(record.get("paper_number") or "").strip().casefold()
    source_id = str(record.get("source_id") or "").strip().casefold()
    scope = source_scope(record)
    if paper_number and source_id:
        if source_id.startswith("repec-nep-"):
            # NEP anchors (p1, p2, ...) are positions inside a weekly issue,
            # not stable paper numbers. Include the issue URL so different
            # weeks can never collapse into one record.
            issue_url = str(record.get("source_url") or url.split("#", 1)[0]).strip().rstrip("/").casefold()
            if issue_url:
                keys.add(f"paper:{source_id}:{issue_url}:{paper_number}")
        else:
            keys.add(f"paper:{source_id}:{paper_number}")
    title = normalized_title(record.get("title"))
    if include_source_title and title and scope != "unknown":
        keys.add(f"source-title:{scope}:{title}")
    return keys


def calculate_detail_key(record: dict[str, Any]) -> str:
    title = str(record.get("title") or "paper").casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", title).strip("-") or "paper"
    slug = slug[:88].rstrip("-")
    identity = normalize_doi(record.get("doi")) or str(record.get("url") or "")
    if not identity:
        identity = f"{record.get('title') or ''}|{record.get('journal') or ''}"
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


def ensure_detail_key(record: dict[str, Any]) -> bool:
    if str(record.get("detail_key") or "").strip():
        return False
    record["detail_key"] = calculate_detail_key(record)
    return True


def canonical_route_key(record: dict[str, Any], fallback: str = "") -> str:
    """Strong public route key: record-derived (DOI) key when a distinct non-empty
    normalized DOI exists, else the legacy fallback. Prevents distinct-DOI records
    from sharing a route key via an ambiguous legacy fallback detail_key."""
    if normalize_doi(record.get("doi")):
        return calculate_detail_key(record)
    return fallback

def strip_title_prefix(record: dict[str, Any]) -> bool:
    title_zh = str(record.get("title_zh") or "").strip()
    if not title_zh:
        return False
    for pattern in TITLE_PREFIX_PATTERNS:
        match = pattern.match(title_zh)
        if not match:
            continue
        clean = title_zh[match.end() :].strip()
        if len(clean) < 2:
            return False
        number = " ".join(str(match.group("number") or "").split())
        if number and not record.get("paper_number"):
            compact = re.search(r"(?:DP|WP)\s*(\d{2,6})", number, flags=re.IGNORECASE)
            record["paper_number"] = f"{compact.group(0).replace(' ', '').upper()}" if compact else number
        record["title_zh"] = clean
        return True
    return False


def has_abstract_boilerplate(value: Any) -> bool:
    text = " ".join(str(value or "").split()).casefold()
    return any(fragment in text for fragment in ABSTRACT_BOILERPLATE_FRAGMENTS)


def clean_abstract(record: dict[str, Any]) -> bool:
    raw = record.get("abstract")
    if raw in (None, ""):
        return False
    text = clean_abstract_text(raw)
    changed = text != raw
    preview = False
    for pattern in ABSTRACT_SUFFIXES:
        cleaned, count = pattern.subn("", text)
        if count:
            text = cleaned.strip()
            preview = True
            changed = True
    if has_abstract_boilerplate(text):
        text = ""
        changed = True
        record["abstract_enrichment_status"] = "boilerplate-removed"
    compact = " ".join(text.split())
    if compact in {"~", "-", "n/a", "na"} or (len(compact) < 20 and not re.search(r"[。！？]", compact)):
        text = ""
        changed = True
        record["abstract_enrichment_status"] = "non-abstract-removed"
    if text.endswith(("...", "…")):
        preview = True
    if preview:
        for key, value in (
            ("abstract_completeness", "preview"),
            ("abstract_truncated", True),
            ("abstract_status_code", "preview_truncated"),
            ("abstract_status", "来源仅提供摘要预览，系统将自动重试完整摘要"),
        ):
            if record.get(key) != value:
                record[key] = value
                changed = True
    if text:
        if record.get("abstract") != text:
            record["abstract"] = text
            changed = True
    else:
        if record.get("abstract") is not None:
            record["abstract"] = None
            changed = True
        if record.get("abstract_zh") is not None:
            record["abstract_zh"] = None
            changed = True
    return changed


def set_metadata_statuses(record: dict[str, Any]) -> bool:
    changed = False
    authors = record.get("authors")
    if not isinstance(authors, list):
        record["authors"] = []
        authors = []
        changed = True
    if authors:
        if record.get("authors_status_code") != "available":
            record["authors_status_code"] = "available"
            changed = True
        if record.get("authors_status") in {"作者信息待核验", "官方页面未列出个人作者"}:
            record.pop("authors_status", None)
            changed = True
    else:
        if record.get("authors_status_code") != "missing_retry":
            record["authors_status_code"] = "missing_retry"
            changed = True
        if not record.get("authors_status"):
            record["authors_status"] = "作者信息待核验"
            changed = True

    abstract = str(record.get("abstract") or "").strip()
    if abstract and record.get("abstract_completeness") != "preview":
        for key, value in (
            ("abstract_status_code", "available"),
            ("abstract_completeness", "full"),
        ):
            if record.get(key) != value:
                record[key] = value
                changed = True
        if record.get("abstract_status") in {
            "摘要暂未公开，系统将自动重试",
            "来源仅提供摘要预览，系统将自动重试完整摘要",
        }:
            record.pop("abstract_status", None)
            changed = True
        if record.pop("abstract_truncated", None) is not None:
            changed = True
    elif not abstract:
        for key, value in (
            ("abstract_status_code", "missing_retry"),
            ("abstract_completeness", "missing"),
            ("abstract_status", "摘要暂未公开，系统将自动重试"),
        ):
            if record.get(key) != value:
                record[key] = value
                changed = True

    has_date = any(str(record.get(field) or "").strip() for field in DATE_FIELDS)
    date_status = "available" if has_date else "missing_retry"
    if record.get("official_date_status") != date_status:
        record["official_date_status"] = date_status
        changed = True
    if not has_date:
        if record.get("date_source") != "unknown":
            record["date_source"] = "unknown"
            changed = True
        if record.get("date_confidence") != "F":
            record["date_confidence"] = "F"
            changed = True
    return changed


def normalize_public_authors(record: dict[str, Any]) -> bool:
    authors = record.get("authors")
    if not isinstance(authors, list):
        return False
    normalized: list[str] = []
    for raw in authors:
        for value in re.split(r"\s*;\s*", str(raw or "")):
            value = value.strip()
            if value and value not in normalized:
                normalized.append(value)
    if normalized == authors:
        return False
    record["authors"] = normalized[:20]
    return True


def normalize_public_record(record: dict[str, Any]) -> bool:
    changed = normalize_public_authors(record)
    changed = strip_title_prefix(record) or changed
    changed = clean_abstract(record) or changed
    changed = set_metadata_statuses(record) or changed
    changed = ensure_detail_key(record) or changed
    return changed


def _timestamp(record: dict[str, Any]) -> str:
    return str(record.get("first_seen_at") or record.get("first_seen") or record.get("detected_at") or "")


def _value_score(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    return len(str(value or ""))


def _crossref_issue_only(record: dict[str, Any]) -> bool:
    """A Crossref 'published' issue date cannot assert first online publication.

    Normalization already downgrades this provisional metadata. Guard merges
    against an older seen copy resurrecting its C grade on alternating runs.
    """
    if str(record.get("source") or "") != "crossref":
        return False
    if str(record.get("date_source") or "") not in {"crossref_published", "crossref_issue"}:
        return False
    raw = record.get("raw_data")
    raw_date_source = raw.get("crossref_date_source") if isinstance(raw, dict) else None
    if raw_date_source not in (None, "", "crossref_published", "crossref_issue"):
        return False
    if any(record.get(field) for field in ("available_online", "official_date", "accepted_date")):
        return False
    issue = record.get("issue_date")
    published = record.get("published_online")
    return bool(issue) and (not published or published == issue)


def _canonicalize_crossref_issue_only(record: dict[str, Any]) -> bool:
    if not _crossref_issue_only(record):
        return False
    changed = False
    for field, value in (
        ("published_online", None),
        ("date_source", "crossref_issue"),
        ("date_confidence", "D"),
    ):
        if record.get(field) != value:
            record[field] = value
            changed = True
    return changed


def merge_record(primary: dict[str, Any], alias: dict[str, Any]) -> bool:
    # Harmonize evidence before rank-based daily/seen merges. The canonical
    # discovery clock is deliberately outside this date-confidence repair.
    changed = _canonicalize_crossref_issue_only(primary)
    alias_issue_only = _crossref_issue_only(alias)
    aliases = set(primary.get("identity_aliases") or [])
    for record in (primary, alias):
        # Historical seen IDs were once derived from reusable NEP issue
        # positions and can point at a different paper. They are storage keys,
        # not public identities, so never propagate them as aliases.
        for value in (record.get("doi"), record.get("url"), record.get("detail_key")):
            if value:
                aliases.add(str(value))
    if aliases and sorted(aliases) != primary.get("identity_aliases"):
        primary["identity_aliases"] = sorted(aliases)
        changed = True

    for field in ("first_seen_at", "first_seen", "detected_at"):
        values = [str(value) for value in (primary.get(field), alias.get(field)) if value]
        if values:
            earliest = min(values)
            if primary.get(field) != earliest:
                primary[field] = earliest
                changed = True

    for field in ("doi", *RICH_TEXT_FIELDS):
        if not primary.get(field) and alias.get(field):
            primary[field] = copy.deepcopy(alias[field])
            changed = True
    for field in ("authors", "fields", "ai_tags"):
        if _value_score(alias.get(field)) > _value_score(primary.get(field)):
            primary[field] = copy.deepcopy(alias[field])
            changed = True
    if _value_score(alias.get("abstract")) > _value_score(primary.get("abstract")):
        primary["abstract"] = alias.get("abstract")
        for field in ("abstract_source", "abstract_enrichment_status"):
            if alias.get(field):
                primary[field] = alias[field]
        changed = True

    confidence_rank = {"": 0, "F": 0, "unknown": 0, "D": 1, "C": 2, "B": 3, "A": 4}
    incoming_rank = confidence_rank.get(str(alias.get("date_confidence") or ""), 0)
    if alias_issue_only:
        incoming_rank = min(incoming_rank, confidence_rank["D"])
    current_rank = confidence_rank.get(str(primary.get("date_confidence") or ""), 0)
    for field in DATE_FIELDS:
        if field == "published_online" and alias_issue_only:
            continue  # Never resurrect the issue date as independent online.
        if alias.get(field) and (not primary.get(field) or incoming_rank > current_rank):
            primary[field] = alias[field]
            changed = True
    if incoming_rank > current_rank:
        for field in ("date_source", "date_confidence", "date_precision"):
            if alias.get(field):
                primary[field] = alias[field]
                changed = True

    # Prefer an official article URL over a transient CNKI query or bare DOI URL.
    current_url = str(primary.get("url") or "")
    incoming_url = str(alias.get("url") or "")
    def url_rank(value: str) -> int:
        if not value:
            return 0
        if "kns.cnki.net" in value:
            return 1
        if "doi.org" in value:
            return 2
        return 3
    if url_rank(incoming_url) > url_rank(current_url):
        primary["url"] = incoming_url
        changed = True

    for field, value in alias.items():
        if field.startswith("_") or field in primary or value in (None, "", []):
            continue
        primary[field] = copy.deepcopy(value)
        changed = True
    return changed


def _find_owner(index: dict[str, int], record: dict[str, Any]) -> int | None:
    return next((index[key] for key in sorted(strong_identity_keys(record)) if key in index), None)


def collapse_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    removed = 0
    for record in records:
        owner = _find_owner(index, record)
        if owner is None:
            kept.append(record)
            owner = len(kept) - 1
        else:
            merge_record(kept[owner], record)
            removed += 1
        for key in strong_identity_keys(kept[owner]):
            index.setdefault(key, owner)
    return kept, removed


def collapse_daily(daily_dir: Path) -> tuple[int, int]:
    owner_by_key: dict[str, tuple[Path, dict[str, Any]]] = {}
    payloads: dict[Path, list[dict[str, Any]]] = {}
    removed = touched = 0
    for path in sorted(daily_dir.glob("*.json")):
        payload = read_json(path, [])
        if not isinstance(payload, list):
            continue
        kept: list[dict[str, Any]] = []
        for record in payload:
            if not isinstance(record, dict):
                continue
            owner = next(
                (owner_by_key[key] for key in sorted(strong_identity_keys(record)) if key in owner_by_key),
                None,
            )
            if owner is None:
                kept.append(record)
                owner = (path, record)
            else:
                merge_record(owner[1], record)
                removed += 1
            for key in strong_identity_keys(owner[1]):
                owner_by_key.setdefault(key, owner)
        payloads[path] = kept
    # Owner records can be enriched by aliases encountered in later files, so
    # compare and write only after the complete history has been traversed.
    for path, payload in payloads.items():
        if read_json(path, []) != payload:
            write_json(path, payload)
            touched += 1
    return removed, touched


def iter_daily(daily_dir: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    for path in sorted(daily_dir.glob("*.json")):
        payload = read_json(path, [])
        if isinstance(payload, list):
            for record in payload:
                if isinstance(record, dict):
                    yield path, record


def _build_seen_index(papers: dict[str, dict[str, Any]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for key, record in papers.items():
        index.setdefault(str(key).casefold(), key)
        for identity in strong_identity_keys(record):
            index.setdefault(identity, key)
        for identity in catalogue_identity_keys(record):
            index.setdefault(identity, key)
        for alias in record.get("identity_aliases") or []:
            index.setdefault(str(alias).casefold(), key)
        title = " ".join(str(record.get("title") or "").casefold().split())
        journal = " ".join(str(record.get("journal") or "").casefold().split())
        if title and journal:
            index.setdefault(f"journal-title:{journal}:{title}", key)
        if title and str(record.get("source") or "") == "working_papers":
            index.setdefault(f"working-title:{title}", key)
    return index


def _collapse_seen_to_fixed_point(papers: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], int]:
    total_removed = 0
    current = papers
    for _attempt in range(5):
        collapsed, removed = collapse_records([record for record in current.values() if isinstance(record, dict)])
        rebuilt: dict[str, dict[str, Any]] = {}
        for record in collapsed:
            key = str(record.get("id") or "").strip() or f"detail:{calculate_detail_key(record)}"
            candidate = key
            suffix = 1
            while candidate in rebuilt and rebuilt[candidate] is not record:
                suffix += 1
                candidate = f"{key}:{suffix}"
            rebuilt[candidate] = record
        current = rebuilt
        total_removed += removed
        if removed == 0:
            break
    return current, total_removed


def repair_seen(daily_dir: Path, seen_path: Path) -> tuple[int, int, int]:
    payload = read_json(seen_path, {"papers": {}})
    papers = payload.setdefault("papers", {})
    original_count = len(papers)
    papers, removed = _collapse_seen_to_fixed_point(papers)
    index = _build_seen_index(papers)
    seeded = 0
    daily_updates: dict[Path, list[dict[str, Any]]] = {}
    for path in sorted(daily_dir.glob("*.json")):
        rows = read_json(path, [])
        path_changed = False
        for record in rows if isinstance(rows, list) else []:
            if not isinstance(record, dict):
                continue
            seen_key = next((index[key] for key in sorted(strong_identity_keys(record)) if key in index), None)
            if seen_key is None:
                seen_key = str(record.get("id") or "").strip() or f"detail:{calculate_detail_key(record)}"
                candidate = seen_key
                suffix = 1
                while candidate in papers:
                    suffix += 1
                    candidate = f"{seen_key}:{suffix}"
                seen_key = candidate
                papers[seen_key] = copy.deepcopy(record)
                seeded += 1
            else:
                path_changed = merge_record(record, papers[seen_key]) or path_changed
                merge_record(papers[seen_key], record)
                canonical_detail = str(record.get("detail_key") or papers[seen_key].get("detail_key") or "")
                if canonical_detail:
                    if record.get("detail_key") != canonical_detail:
                        record["detail_key"] = canonical_detail
                        path_changed = True
                    papers[seen_key]["detail_key"] = canonical_detail
            for identity in strong_identity_keys(papers[seen_key]):
                index.setdefault(identity, seen_key)
        if path_changed:
            daily_updates[path] = rows
    for path, rows in daily_updates.items():
        write_json(path, rows)
    papers, post_merge_removed = _collapse_seen_to_fixed_point(papers)
    removed += post_merge_removed
    final_index = _build_seen_index(papers)
    # Merging may have introduced a stronger DOI identity. Reconcile the final
    # canonical detail key back into every daily copy.
    for path in sorted(daily_dir.glob("*.json")):
        rows = read_json(path, [])
        path_changed = False
        for record in rows if isinstance(rows, list) else []:
            if not isinstance(record, dict):
                continue
            seen_key = next(
                (final_index[key] for key in sorted(strong_identity_keys(record)) if key in final_index),
                None,
            )
            if seen_key is None:
                continue
            canonical_detail = str(record.get("detail_key") or papers[seen_key].get("detail_key") or calculate_detail_key(record))
            if record.get("detail_key") != canonical_detail:
                record["detail_key"] = canonical_detail
                path_changed = True
            if papers[seen_key].get("detail_key") != canonical_detail:
                papers[seen_key]["detail_key"] = canonical_detail
        if path_changed:
            write_json(path, rows)
    payload["papers"] = papers
    write_json(seen_path, payload)
    return max(removed, original_count + seeded - len(papers)), seeded, len(papers)


def apply_version_relationships(daily_dir: Path, seen_path: Path) -> int:
    seen_payload = read_json(seen_path, {"papers": {}})
    papers = seen_payload.get("papers") if isinstance(seen_payload, dict) else {}
    all_records: list[dict[str, Any]] = [record for _, record in iter_daily(daily_dir)]
    all_records.extend(record for record in papers.values() if isinstance(record, dict))
    by_title: dict[str, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    for record in all_records:
        title = normalized_title(record.get("title"))
        if title:
            by_title[title].setdefault((source_scope(record), str(record.get("source_type") or "")), record)

    relationships: dict[str, tuple[str, list[str]]] = {}
    group_count = 0
    for title, versions in by_title.items():
        source_scopes = {scope for scope, _kind in versions}
        source_types = {kind for _scope, kind in versions}
        if len(source_scopes) < 2 or "journal" not in source_types or not source_types.intersection(
            {"working_paper", "policy_paper", "aggregator", "preprint"}
        ):
            continue
        group_count += 1
        group_key = f"version:{hashlib.sha1(title.encode('utf-8')).hexdigest()[:16]}"
        version_keys = sorted({str(record.get("detail_key") or calculate_detail_key(record)) for record in versions.values()})
        for record in versions.values():
            relationships[str(record.get("detail_key") or calculate_detail_key(record))] = (
                group_key,
                [key for key in version_keys if key != str(record.get("detail_key") or calculate_detail_key(record))],
            )

    def update_record(record: dict[str, Any]) -> bool:
        detail = str(record.get("detail_key") or calculate_detail_key(record))
        relation = relationships.get(detail)
        changed = False
        if relation:
            if record.get("version_group_key") != relation[0]:
                record["version_group_key"] = relation[0]
                changed = True
            if record.get("related_versions") != relation[1]:
                record["related_versions"] = relation[1]
                changed = True
        else:
            for key in ("version_group_key", "related_versions"):
                if record.pop(key, None) is not None:
                    changed = True
        return changed

    for path in sorted(daily_dir.glob("*.json")):
        rows = read_json(path, [])
        changed = False
        for record in rows if isinstance(rows, list) else []:
            if isinstance(record, dict) and update_record(record):
                changed = True
        if changed:
            write_json(path, rows)
    seen_changed = False
    for record in papers.values():
        if isinstance(record, dict) and update_record(record):
            seen_changed = True
    if seen_changed:
        write_json(seen_path, seen_payload)
    return group_count


def iter_ledger_records(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        yield from (item for item in payload if isinstance(item, dict))
    elif isinstance(payload, dict):
        records = payload.get("records")
        if isinstance(records, list):
            yield from (item for item in records if isinstance(item, dict))


def normalize_ledgers(data_dir: Path) -> int:
    changed_count = 0
    for name in LEDGER_PATHS:
        path = data_dir / name
        if not path.exists():
            continue
        payload = read_json(path, {})
        changed = False
        records = list(iter_ledger_records(payload))
        for record in records:
            if record.get("title") or record.get("doi") or record.get("url"):
                changed = normalize_public_record(record) or changed
        changed = bool(sanitize_record_paths(records)) or changed
        if changed:
            write_json(path, payload)
            changed_count += 1
    return changed_count


def repair_false_seen_ledger(data_dir: Path, seen_path: Path) -> tuple[int, int, int]:
    """Requeue legacy dedupe decisions that do not resolve to a seen record."""
    ledger_path = data_dir / "ingestion_exclusion_ledger.json"
    if not ledger_path.exists():
        return 0, 0, 0
    seen_payload = read_json(seen_path, {"papers": {}})
    papers = seen_payload.get("papers") if isinstance(seen_payload, dict) else {}
    seen_index = _build_seen_index(papers if isinstance(papers, dict) else {})
    payload = read_json(ledger_path, {})
    requeued: list[dict[str, Any]] = []
    relinked = 0
    noise_reclassified = 0
    changed = False
    for record in iter_ledger_records(payload):
        if not record.get("seen"):
            continue
        matched_keys = {str(value).casefold() for value in record.get("matched_keys") or []}
        owner_key = next((seen_index[key] for key in sorted(matched_keys) if key in seen_index), None)
        if owner_key is None:
            owner_key = next(
                (seen_index[key] for key in sorted(catalogue_identity_keys(record)) if key in seen_index),
                None,
            )
        if owner_key is not None:
            owner = papers.get(owner_key, {}) if isinstance(papers, dict) else {}
            canonical_detail = str(owner.get("detail_key") or "") if isinstance(owner, dict) else ""
            if record.get("matched_seen_key") != owner_key:
                record["matched_seen_key"] = owner_key
                changed = True
                relinked += 1
            if canonical_detail and record.get("canonical_detail_key") != canonical_detail:
                record["canonical_detail_key"] = canonical_detail
                changed = True
            continue
        if is_source_navigation_noise(record):
            record["seen"] = False
            record["duplicate"] = False
            record["stage"] = "source_rule"
            record["reason"] = "non-paper editorial or navigation record"
            record["exclusion_status"] = "confirmed_nonpaper"
            record.pop("retry_status", None)
            record.pop("retry_reason", None)
            changed = True
            noise_reclassified += 1
            continue
        identities = sorted(strong_identity_keys(record))
        record["seen"] = False
        record["duplicate"] = False
        record["stage"] = "retry_reingestion"
        record["reason"] = "legacy dedupe identity did not resolve to a canonical seen record"
        record["retry_status"] = "pending"
        record["retry_reason"] = "legacy_url_identity_false_positive"
        record["matched_keys"] = identities
        requeued.append(copy.deepcopy(record))
        changed = True
    if not changed:
        return 0, 0, 0
    write_json(ledger_path, payload)

    queue_path = data_dir / "ingestion_retry_queue.json"
    queue = read_json(queue_path, {"records": []})
    if not isinstance(queue, dict):
        queue = {"records": []}
    records = queue.setdefault("records", [])
    existing = {
        tuple(sorted(strong_identity_keys(item)))
        for item in records
        if isinstance(item, dict) and strong_identity_keys(item)
    }
    added = 0
    for record in requeued:
        identity = tuple(sorted(strong_identity_keys(record)))
        if identity and identity in existing:
            continue
        records.append(record)
        if identity:
            existing.add(identity)
        added += 1
    queue["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    queue["note"] = "Candidates requeued after an earlier seen-dedupe decision could not be resolved."
    queue["total_candidates"] = len(records)
    write_json(queue_path, queue)
    return added, relinked, noise_reclassified


def _duplicate_counts(records: Iterable[dict[str, Any]]) -> tuple[int, int]:
    groups: dict[tuple[Any, ...], int] = defaultdict(int)
    for record in records:
        # Records with a distinct non-empty DOI are canonical-identity distinct:
        # group them by DOI so same-title/same-source records with different DOIs
        # are NOT rejected as duplicates, while true duplicates (same DOI) remain.
        doi = normalize_doi(record.get("doi"))
        if doi:
            groups[("doi", doi)] += 1
            continue
        title = normalized_title(record.get("title"))
        if title:
            groups[("src", source_scope(record), title)] += 1
    duplicate_groups = sum(count > 1 for count in groups.values())
    duplicate_records = sum(max(0, count - 1) for count in groups.values())
    return duplicate_groups, duplicate_records


def has_redundant_composite_authors(record: dict[str, Any]) -> bool:
    authors = record.get("authors")
    if not isinstance(authors, list) or len(authors) < 2:
        return False
    individual = {
        str(author).strip().casefold()
        for author in authors
        if str(author).strip() and ";" not in str(author)
    }
    for author in authors:
        parts = [part.strip().casefold() for part in str(author).split(";") if part.strip()]
        if len(parts) > 1 and all(part in individual for part in parts):
            return True
    return False


def audit_integrity(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    daily_records = [record for _, record in iter_daily(data_dir / "daily")]
    seen_payload = read_json(data_dir / "seen.json", {"papers": {}})
    papers = seen_payload.get("papers") if isinstance(seen_payload, dict) else {}
    seen_records = [record for record in papers.values() if isinstance(record, dict)]
    seen_index = _build_seen_index(papers)
    seen_detail_keys = {str(record.get("detail_key") or "") for record in seen_records if record.get("detail_key")}
    daily_orphans = []
    for record in daily_records:
        matched = any(key in seen_index for key in strong_identity_keys(record))
        detail = str(record.get("detail_key") or "")
        if not matched or not detail or detail not in seen_detail_keys:
            daily_orphans.append(detail or str(record.get("id") or record.get("title") or ""))

    ledger_orphans = []
    for name in LEDGER_PATHS:
        payload = read_json(data_dir / name, {})
        for record in iter_ledger_records(payload):
            if not record.get("seen"):
                continue
            matched_seen_key = str(record.get("matched_seen_key") or "")
            canonical_detail = str(record.get("canonical_detail_key") or "")
            matched_keys = {str(value).casefold() for value in record.get("matched_keys") or []}
            matched = (
                matched_seen_key in papers
                or canonical_detail in seen_detail_keys
                or bool(matched_keys.intersection(seen_index))
                or any(key in seen_index for key in catalogue_identity_keys(record))
            )
            if not matched:
                ledger_orphans.append(str(record.get("detail_key") or record.get("title") or ""))

    daily_duplicate_groups, daily_duplicate_records = _duplicate_counts(daily_records)
    seen_duplicate_groups, seen_duplicate_records = _duplicate_counts(seen_records)
    daily_detail = [str(record.get("canonical_detail_key") or record.get("detail_key") or "") for record in daily_records]
    seen_detail = [str(record.get("canonical_detail_key") or record.get("detail_key") or "") for record in seen_records]
    prefix_count = sum(
        bool(str(record.get("title_zh") or ""))
        and any(pattern.match(str(record.get("title_zh") or "")) for pattern in TITLE_PREFIX_PATTERNS)
        for record in daily_records + seen_records
    )
    boilerplate = sum(has_abstract_boilerplate(record.get("abstract")) for record in daily_records + seen_records)
    redundant_composite_authors = sum(
        has_redundant_composite_authors(record) for record in daily_records + seen_records
    )
    machine_path_leaks = sum(
        1
        for record in daily_records + seen_records
        for field in PATH_FIELDS
        if isinstance(record.get(field), str)
        and record[field]
        and repo_relative_path(record[field]) != record[field]
    )
    for name in LEDGER_PATHS:
        payload = read_json(data_dir / name, {})
        machine_path_leaks += sum(
            1
            for record in iter_ledger_records(payload)
            for field in PATH_FIELDS
            if isinstance(record.get(field), str)
            and record[field]
            and repo_relative_path(record[field]) != record[field]
        )
    missing_status = {
        "authors": sum(not record.get("authors") and not record.get("authors_status_code") for record in daily_records),
        "abstract": sum(not record.get("abstract") and not record.get("abstract_status_code") for record in daily_records),
        "official_date": sum(
            not any(record.get(field) for field in DATE_FIELDS) and not record.get("official_date_status")
            for record in daily_records
        ),
    }
    version_groups = {str(record.get("version_group_key")) for record in daily_records if record.get("version_group_key")}
    return {
        "daily_records": len(daily_records),
        "seen_records": len(seen_records),
        "same_source_title_duplicate_groups": daily_duplicate_groups + seen_duplicate_groups,
        "same_source_title_duplicate_records": daily_duplicate_records + seen_duplicate_records,
        "daily_same_source_title_duplicate_records": daily_duplicate_records,
        "seen_same_source_title_duplicate_records": seen_duplicate_records,
        "canonical_detail_key_missing": sum(not key for key in daily_detail),
        "canonical_detail_key_duplicate": len(daily_detail) - len(set(daily_detail)),
        "seen_detail_key_missing": sum(not key for key in seen_detail),
        "seen_detail_key_duplicate": len(seen_detail) - len(set(seen_detail)),
        "daily_seen_orphan_keys": len(daily_orphans),
        "ledger_orphan_keys": len(ledger_orphans),
        "orphan_keys": len(daily_orphans) + len(ledger_orphans),
        "title_zh_number_prefixes": prefix_count,
        "boilerplate_abstracts": boilerplate,
        "redundant_composite_authors": redundant_composite_authors,
        "machine_path_leaks": machine_path_leaks,
        "metadata_missing_status": missing_status,
        "version_relationship_groups": len(version_groups),
    }


def count_leak_fields(records: Iterable[dict[str, Any]]) -> int:
    """Count machine-path leaks in one batch of records (口径复用)."""
    total = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        for field in PATH_FIELDS:
            value = record.get(field)
            if isinstance(value, str) and value and repo_relative_path(value) != value:
                total += 1
    return total


def audit_machine_path_leaks(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    """Per-layer machine_path_leaks audit used for the 4134 口径复核."""
    daily_records = [record for _, record in iter_daily(data_dir / "daily")]
    seen_payload = read_json(data_dir / "seen.json", {"papers": {}})
    papers = seen_payload.get("papers") if isinstance(seen_payload, dict) else {}
    seen_records = [record for record in papers.values() if isinstance(record, dict)]
    ledger_records: list[dict[str, Any]] = []
    for name in LEDGER_PATHS:
        payload = read_json(data_dir / name, {})
        ledger_records.extend(iter_ledger_records(payload))
    counts = {
        "daily": count_leak_fields(daily_records),
        "seen": count_leak_fields(seen_records),
        "ledgers": count_leak_fields(ledger_records),
    }
    counts["total"] = sum(counts.values())
    return {
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "method": "PATH_FIELDS=(_raw_file,raw_file,source_file) + repo_relative_path per daily/seen/ledger record",
        "counts": counts,
    }


def repair_public_integrity(data_dir: Path = DATA_DIR, *, write_report: bool = True) -> dict[str, Any]:
    before = audit_integrity(data_dir)
    leak_before = audit_machine_path_leaks(data_dir)
    daily_dir = data_dir / "daily"
    seen_path = data_dir / "seen.json"
    daily_removed, daily_files = collapse_daily(daily_dir)
    seen_removed, seen_seeded, _seen_total = repair_seen(daily_dir, seen_path)

    for path in sorted(daily_dir.glob("*.json")):
        rows = read_json(path, [])
        changed = False
        for record in rows if isinstance(rows, list) else []:
            if isinstance(record, dict) and normalize_public_record(record):
                changed = True
        changed = bool(sanitize_record_paths(rows)) or changed
        if changed:
            write_json(path, rows)
    seen_payload = read_json(seen_path, {"papers": {}})
    seen_records = seen_payload.get("papers") if isinstance(seen_payload, dict) else {}
    seen_changed = False
    for record in seen_records.values():
        if isinstance(record, dict) and normalize_public_record(record):
            seen_changed = True
    seen_changed = bool(sanitize_record_paths(list(seen_records.values()))) or seen_changed
    if seen_changed:
        write_json(seen_path, seen_payload)

    from repair_seen_only_archives import repair_seen_only_archives as _archive_seen_only
    from regrade_date_confidence import regrade_daily_and_seen as _regrade_dates

    archive_report = _archive_seen_only(data_dir=data_dir)
    seen_only_archived = int(archive_report.get("archived_count") or 0)
    seen_only_changed_files = len(archive_report.get("changed_files") or [])

    date_report = _regrade_dates(data_dir=data_dir)

    version_groups = apply_version_relationships(daily_dir, seen_path)
    ledger_requeued, ledger_relinked, ledger_noise = repair_false_seen_ledger(data_dir, seen_path)
    ledger_files = normalize_ledgers(data_dir)
    after = audit_integrity(data_dir)
    leak_after = audit_machine_path_leaks(data_dir)
    write_json(
        data_dir / "machine_path_leak_audit.json",
        {
            "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "method": leak_before["method"],
            "before": leak_before["counts"],
            "after": leak_after["counts"],
            "removed": {
                layer: max(0, leak_before["counts"][layer] - leak_after["counts"][layer])
                for layer in ("daily", "seen", "ledgers", "total")
            },
        },
    )
    report_path = data_dir / "public_integrity_audit.json"
    previous = read_json(report_path, {})
    migration_baseline = copy.deepcopy(previous.get("migration_baseline") or before)
    for key, value in before.items():
        migration_baseline.setdefault(key, value)
    baseline_missing = migration_baseline.get("metadata_missing_status", {})
    current_missing = after.get("metadata_missing_status", {})
    report = {
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "migration_baseline": migration_baseline,
        "migration_delta": {
            "canonical_records_removed": max(0, migration_baseline.get("daily_records", 0) - after["daily_records"]),
            "seen_records_net_removed": max(0, migration_baseline.get("seen_records", 0) - after["seen_records"]),
            "same_source_title_duplicates_removed": max(
                0,
                migration_baseline.get("same_source_title_duplicate_records", 0)
                - after["same_source_title_duplicate_records"],
            ),
            "detail_keys_added": max(
                0,
                migration_baseline.get("canonical_detail_key_missing", 0)
                - after["canonical_detail_key_missing"],
            ),
            "ledger_orphans_repaired": max(
                0, migration_baseline.get("ledger_orphan_keys", 0) - after["ledger_orphan_keys"]
            ),
            "title_prefixes_removed": max(
                0, migration_baseline.get("title_zh_number_prefixes", 0) - after["title_zh_number_prefixes"]
            ),
            "boilerplate_abstracts_removed": max(
                0, migration_baseline.get("boilerplate_abstracts", 0) - after["boilerplate_abstracts"]
            ),
            "machine_path_leaks_removed": max(
                0, migration_baseline.get("machine_path_leaks", 0) - after["machine_path_leaks"]
            ),
            "metadata_statuses_added": {
                key: max(0, int(baseline_missing.get(key, 0)) - int(current_missing.get(key, 0)))
                for key in ("authors", "abstract", "official_date")
            },
        },
        "before_run": before,
        "current": after,
        "repairs": {
            "daily_duplicates_removed": daily_removed,
            "daily_files_changed": daily_files,
            "seen_duplicates_removed": seen_removed,
            "seen_records_seeded_from_daily": seen_seeded,
            "ledger_files_changed": ledger_files,
            "ledger_records_requeued": ledger_requeued,
            "ledger_records_relinked": ledger_relinked,
            "ledger_nonpaper_reclassified": ledger_noise,
            "seen_only_archived": seen_only_archived,
            "seen_only_changed_files": seen_only_changed_files,
            "version_relationship_groups": version_groups,
            "date_confidence_daily_files": date_report["daily_files_changed"],
            "date_confidence_daily_records": date_report["daily_records_changed"],
            "date_confidence_seen_records": date_report["seen_records_changed"],
        },
    }
    if write_report:
        write_json(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    if args.audit_only:
        current = audit_integrity(args.data_dir)
        leak_audit = audit_machine_path_leaks(args.data_dir)
        write_json(args.data_dir / "machine_path_leak_audit.json", leak_audit)
        print(current)
        print(leak_audit)
        if any(
            (
                current["same_source_title_duplicate_records"],
                current["canonical_detail_key_missing"],
                current["canonical_detail_key_duplicate"],
                current["daily_seen_orphan_keys"],
                current["ledger_orphan_keys"],
                current["title_zh_number_prefixes"],
                current["boilerplate_abstracts"],
                current["redundant_composite_authors"],
                current["machine_path_leaks"],
                sum(current["metadata_missing_status"].values()),
            )
        ):
            raise SystemExit(2)
        return
    report = repair_public_integrity(args.data_dir)
    print(f"public integrity current={report['current']} repairs={report['repairs']}")


if __name__ == "__main__":
    main()
