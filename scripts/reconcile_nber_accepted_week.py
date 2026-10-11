"""Decision 0022: bounded, default-disabled ③ accepted NBER week → ② Daily consumer.

The upstream contract is public, complete, accepted and tied to a matching
site_status build. Never infer membership from feed.json or private artifacts.
No production workflow invokes this script until producer #97 is live-accepted.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import urllib.request
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from common import DATA_DIR, read_json, write_json

NBER_WEEK_URL = "https://academic-door.github.io/nber-working-papers-cn/data/latest_week.json"
NBER_STATUS_URL = "https://academic-door.github.io/nber-working-papers-cn/data/site_status.json"
BEIJING = timezone(timedelta(hours=8))
NUMBER_RE = re.compile(r"^w\d{4,6}$")
URL_RE = re.compile(r"^https://www\.nber\.org/papers/(w\d{4,6})$")
DOI_RE = re.compile(r"^10\.3386/(w\d{4,6})$")
WEEK_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
MAX_PAPERS = 150
MAX_BYTES = 900_000


class UnacceptedWeek(ValueError):
    """Fail open as an owner source warning, not as a Daily release failure."""


def canon_english(value: Any) -> str:
    return " ".join(str(value or "").split())


def digest(value: Any) -> str:
    return hashlib.sha256(canon_english(value).encode("utf-8")).hexdigest()


def canonical_number(paper: dict[str, Any]) -> str:
    number = str(paper.get("number") or "").lower()
    url = str(paper.get("url") or "")
    doi = str(paper.get("doi") or f"10.3386/{number}").lower()
    match_url = URL_RE.fullmatch(url)
    match_doi = DOI_RE.fullmatch(doi)
    if not NUMBER_RE.fullmatch(number) or not match_url or not match_doi:
        raise UnacceptedWeek("invalid_nber_identity")
    if match_url.group(1) != number or match_doi.group(1) != number:
        raise UnacceptedWeek("conflicting_nber_identity")
    return number


def existing_nber_number(record: dict[str, Any]) -> str | None:
    """Strictly join by NBER identifiers, never an unscoped title match."""
    candidates: set[str] = set()
    for key, pattern in (("paper_number", NUMBER_RE), ("doi", DOI_RE), ("url", URL_RE)):
        raw = str(record.get(key) or "").strip().lower()
        m = pattern.fullmatch(raw)
        if m:
            candidates.add(m.group(1) if key != "paper_number" else raw)
    rid = str(record.get("id") or "").casefold()
    if rid.startswith("doi:"):
        m = DOI_RE.fullmatch(rid[4:])
        if m:
            candidates.add(m.group(1))
    if len(candidates) != 1:
        return None
    if str(record.get("source_id") or "").lower() not in {"", "nber"}:
        # Third-party versions of the same title/DOI are not Daily NBER records.
        return None
    return next(iter(candidates))


def validate_week(artifact: Any, status: Any) -> tuple[str, str, list[dict[str, Any]]]:
    if not isinstance(artifact, dict) or artifact.get("schema_version") != 1:
        raise UnacceptedWeek("schema_version")
    if artifact.get("accepted") is not True or artifact.get("source_complete") is not True:
        raise UnacceptedWeek("week_not_accepted")
    week = artifact.get("week")
    if not isinstance(week, str) or not WEEK_RE.fullmatch(week):
        raise UnacceptedWeek("invalid_week")
    try:
        datetime.strptime(week, "%Y-%m-%d")
    except ValueError as exc:
        raise UnacceptedWeek("invalid_week_date") from exc
    for key in ("source_kind", "membership_basis", "source_evidence"):
        if not isinstance(artifact.get(key), str) or not artifact[key].strip():
            raise UnacceptedWeek("missing_" + key)
    marker = str(artifact.get("build_id") or artifact.get("built_at") or "").strip()
    if not marker:
        raise UnacceptedWeek("missing_build_identity")
    papers = artifact.get("papers")
    expected = artifact.get("paper_count")
    if not isinstance(expected, int) or isinstance(expected, bool) or not 1 <= expected <= MAX_PAPERS:
        raise UnacceptedWeek("invalid_expected_count")
    if not isinstance(papers, list) or len(papers) != expected:
        raise UnacceptedWeek("incomplete_roster")
    if not isinstance(status, dict):
        raise UnacceptedWeek("missing_site_status")
    # Status projection is deliberately strict pending producer #97's final
    # published schema. Never guess readiness from a generic HTTP 200.
    accepted_status = status.get("latest_week")
    if not isinstance(accepted_status, dict) or accepted_status.get("accepted") is not True:
        raise UnacceptedWeek("status_not_accepted")
    if accepted_status.get("source_complete") is not True:
        raise UnacceptedWeek("status_incomplete")
    if accepted_status.get("week") != week or accepted_status.get("paper_count") != expected:
        raise UnacceptedWeek("status_membership_mismatch")
    status_marker = str(accepted_status.get("build_id") or accepted_status.get("built_at") or "").strip()
    if marker != status_marker:
        raise UnacceptedWeek("status_build_mismatch")
    seen: set[str] = set()
    for paper in papers:
        if not isinstance(paper, dict):
            raise UnacceptedWeek("invalid_paper")
        number = canonical_number(paper)
        if number in seen:
            raise UnacceptedWeek("duplicate_membership")
        seen.add(number)
        if not canon_english(paper.get("title")):
            raise UnacceptedWeek("missing_english_title")
        if not isinstance(paper.get("authors"), list) or any(
            not isinstance(a, str) for a in paper["authors"]
        ):
            raise UnacceptedWeek("invalid_authors")
        for key in ("week_date", "issue_date", "public_date"):
            val = paper.get(key)
            if val is not None and val != "" and (
                not isinstance(val, str) or not WEEK_RE.fullmatch(val)
            ):
                raise UnacceptedWeek("invalid_source_date")
        if paper.get("week_date") and paper["week_date"] != week:
            raise UnacceptedWeek("paper_week_mismatch")
    return week, marker, papers


def translation_value(paper: dict[str, Any], field: str, en_field: str) -> str | None:
    value = paper.get("zh_title" if field == "title" else "zh_abstract")
    evidence = paper.get("translation_validity")
    slot = evidence.get(field) if isinstance(evidence, dict) else None
    if not isinstance(value, str) or not value.strip() or not isinstance(slot, dict):
        return None
    if slot.get("accepted") is not True:
        return None
    if slot.get("english_sha256") != digest(paper.get(en_field)):
        return None
    if not re.search(r"[\u4e00-\u9fff]", value):
        return None
    return value.strip()


def new_daily_record(paper: dict[str, Any], number: str, now: datetime, week: str) -> dict[str, Any]:
    stamp = now.astimezone(UTC).replace(microsecond=0).isoformat()
    return {
        "id": f"doi:10.3386/{number}",
        "title": canon_english(paper["title"]),
        "authors": list(paper.get("authors") or []),
        "abstract": canon_english(paper.get("abstract")) or None,
        "doi": f"10.3386/{number}",
        "url": f"https://www.nber.org/papers/{number}",
        "paper_number": number,
        "journal": "NBER Working Papers",
        "journal_id": "source-nber",
        "publisher": "NBER Working Papers",
        "series": "NBER Working Papers",
        "source": "working_papers",
        "source_type": "working_paper",
        "source_id": "nber",
        "source_name": "NBER Working Papers",
        "source_url": "https://www.nber.org/papers",
        "fields": ["general"],
        "first_seen": stamp,
        "first_seen_at": stamp,
        "detected_at": stamp,
        "date_source": "first_observed",
        "date_confidence": "F",
        "nber_accepted_week": week,
        "nber_source_week_date": paper.get("week_date") or week,
        "nber_source_public_date": paper.get("public_date"),
        "nber_source_issue_date": paper.get("issue_date"),
        "nber_reconciliation_provenance": "nber_accepted_reconciliation",
    }


def attach_accepted_paper(record: dict[str, Any], paper: dict[str, Any], week: str,
                          marker: str) -> dict[str, str]:
    """Only enrich missing fields; existing owner/editorial text always wins."""
    prior_title = canon_english(record.get("title"))
    prior_abstract = canon_english(record.get("abstract"))
    if not prior_title:
        record["title"] = canon_english(paper["title"])
    if not prior_abstract and canon_english(paper.get("abstract")):
        record["abstract"] = canon_english(paper["abstract"])
    if not record.get("authors") and paper.get("authors"):
        record["authors"] = list(paper["authors"])
    if not record.get("doi"):
        record["doi"] = f"10.3386/{paper['number']}"
    if not record.get("url"):
        record["url"] = paper["url"]
    if not record.get("paper_number"):
        record["paper_number"] = paper["number"]
    record["nber_accepted_week"] = week
    record["nber_source_week_date"] = paper.get("week_date") or week
    record["nber_source_public_date"] = paper.get("public_date")
    record["nber_source_issue_date"] = paper.get("issue_date")
    record["nber_reconciliation_provenance"] = "frontier-door"
    record["nber_reconciliation_build"] = marker
    result: dict[str, str] = {}
    for field, en in (("title", "title"), ("abstract", "abstract")):
        target = "title_zh" if field == "title" else "abstract_zh"
        value = translation_value(paper, field, en)
        if value and not record.get(target) and canon_english(record.get(en)) == canon_english(paper.get(en)):
            record[target] = value
            result[field] = value
    return result


def update_translation_cache(cache: dict[str, Any], paper: dict[str, Any],
                             reused: dict[str, str], week: str, marker: str) -> int:
    if not reused:
        return 0
    key = f"10.3386/{paper['number']}"
    records = cache.setdefault("records", {})
    old = records.get(key)
    if not isinstance(old, dict):
        old = {}
    changed = 0
    for field, value in reused.items():
        zh = "title_zh" if field == "title" else "abstract_zh"
        en = canon_english(paper["title" if field == "title" else "abstract"])
        # A cache entry whose source text differs may contain owner corrections
        # or a different version. It is never silently overwritten.
        existing_english = old.get("title" if field == "title" else "abstract")
        if old.get(zh) or (existing_english and canon_english(existing_english) != en):
            continue
        old[zh] = value
        old["title" if field == "title" else "abstract"] = en
        if field == "abstract":
            old["abstract_hash"] = hashlib.sha1(en.encode("utf-8")).hexdigest()
        changed += 1
    if changed:
        old["model"] = "frontier-door-accepted"
        old["upstream"] = "frontier-door"
        old["upstream_week"] = week
        old["upstream_build"] = marker
        records[key] = old
    return changed


def reconcile(artifact: Any, status: Any, seen: dict[str, Any],
              daily: dict[str, list[dict[str, Any]]], cache: dict[str, Any],
              *, observation_time: datetime) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, Any]]:
    """Pure staged reconciliation. Input payloads remain untouched on rejection."""
    week, marker, papers = validate_week(artifact, status)
    if observation_time.tzinfo is None:
        raise ValueError("observation_time must be timezone aware")
    result_seen = copy.deepcopy(seen)
    result_daily = copy.deepcopy(daily)
    result_cache = copy.deepcopy(cache)
    seen_papers = result_seen.setdefault("papers", {})
    if not isinstance(seen_papers, dict):
        raise ValueError("malformed Daily seen catalogue")
    idx: dict[str, list[dict[str, Any]]] = {}
    for record in seen_papers.values():
        if isinstance(record, dict) and (n := existing_nber_number(record)):
            idx.setdefault(n, []).append(record)
    daily_idx: dict[str, list[dict[str, Any]]] = {}
    for archive in result_daily.values():
        for record in archive:
            if isinstance(record, dict) and (n := existing_nber_number(record)):
                daily_idx.setdefault(n, []).append(record)
    added = matched = conflicts = translated = cache_hits = 0
    missing_before = 0
    for paper in papers:
        number = canonical_number(paper)
        matches = idx.get(number, [])
        daily_matches = daily_idx.get(number, [])
        if len(matches) > 1 or len(daily_matches) > 1:
            conflicts += 1
            continue
        if matches or daily_matches:
            matched += 1
        else:
            missing_before += 1
            entry = new_daily_record(paper, number, observation_time, week)
            seen_papers[entry["id"]] = copy.deepcopy(entry)
            idx[number] = [seen_papers[entry["id"]]]
            day = observation_time.astimezone(BEIJING).date().isoformat()
            result_daily.setdefault(day, []).append(entry)
            daily_idx[number] = [entry]
            added += 1
            matches, daily_matches = idx[number], daily_idx[number]
        reused: dict[str, str] = {}
        # Owner-reviewed cache content takes precedence over an otherwise
        # eligible upstream translation, when it matches the *same* English.
        local_cache = result_cache.get("records", {}).get(f"10.3386/{number}", {})
        if not isinstance(local_cache, dict):
            local_cache = {}
        for record in matches + daily_matches:
            for field, en in (("title", "title"), ("abstract", "abstract")):
                target = "title_zh" if field == "title" else "abstract_zh"
                if (not record.get(target) and local_cache.get(target)
                    and canon_english(local_cache.get(en)) == canon_english(record.get(en) or paper.get(en))
                    and canon_english(record.get(en) or paper.get(en)) == canon_english(paper.get(en))):
                    record[target] = local_cache[target]
            accepted = attach_accepted_paper(record, paper, week, marker)
            reused.update(accepted)
            translated += len(accepted)
        cache_hits += update_translation_cache(result_cache, paper, reused, week, marker)
    summary = {
        "state": "accepted_reconciled",
        "upstream": "frontier-door",
        "week": week,
        "build": marker,
        "accepted_count": len(papers),
        "matched_before": matched,
        "missing_before": missing_before,
        "added": added,
        "conflicts": conflicts,
        "missing_after": max(0, missing_before - added) + conflicts,
        "translation_fields_reused": translated,
        "cache_fields_updated": cache_hits,
        "observation_time": observation_time.astimezone(UTC).replace(microsecond=0).isoformat(),
    }
    return result_seen, result_daily, result_cache, summary


def limited_public_json(url: str, max_bytes: int = MAX_BYTES) -> Any:
    if url not in {NBER_WEEK_URL, NBER_STATUS_URL}:
        raise ValueError("unapproved ③ URL")
    request = urllib.request.Request(url, headers={"User-Agent": "AcademicDoor-NBER-readonly/1"})
    with urllib.request.urlopen(request, timeout=10) as response:
        content = response.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise UnacceptedWeek("oversized_upstream")
    return json.loads(content.decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--site-status", type=Path)
    parser.add_argument("--from-public", action="store_true")
    parser.add_argument("--enable-write", action="store_true",
                        help="Operator gate; no production workflow calls this until ③ #97 accepted.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    if args.from_public and (args.artifact or args.site_status):
        parser.error("public and fixture sources are mutually exclusive")
    if not args.from_public and (not args.artifact or not args.site_status):
        parser.error("supply both fixed fixture paths or --from-public")
    try:
        if args.from_public:
            upstream = limited_public_json(NBER_WEEK_URL)
            status = limited_public_json(NBER_STATUS_URL)
        else:
            upstream = read_json(args.artifact, None)
            status = read_json(args.site_status, None)
        seen = read_json(args.data_dir / "seen.json", {"papers": {}})
        cache = read_json(args.data_dir / "translation_cache.json", {"records": {}})
        daily_dir = args.data_dir / "daily"
        daily = {p.stem: read_json(p, []) for p in sorted(daily_dir.glob("*.json"))}
        seen_new, daily_new, cache_new, summary = reconcile(
            upstream, status, seen, daily, cache,
            observation_time=datetime.now(UTC),
        )
        if args.enable_write:
            write_json(args.data_dir / "seen.json", seen_new)
            for day, payload in daily_new.items():
                if payload != daily.get(day):
                    write_json(daily_dir / f"{day}.json", payload)
            if cache_new != cache:
                write_json(args.data_dir / "translation_cache.json", cache_new)
            write_json(args.data_dir / "nber_accepted_reconciliation_status.json", summary)
        print(json.dumps({**summary, "mode": "write" if args.enable_write else "fixture_preview"}))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # An upstream outage/unready status cannot block normal Daily products.
        summary = {"state": "upstream_unavailable_or_unaccepted", "reason": type(exc).__name__,
                   "detail": str(exc)[:120], "mode": "no_write"}
        print(json.dumps(summary))
        # Not currently integrated with workflow or product gate; exit success.


if __name__ == "__main__":
    main()
