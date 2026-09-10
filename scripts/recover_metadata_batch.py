"""Batch recovery of missing abstracts, authors, and official dates.

This is an independent data-line tool. It deliberately does not modify
``scripts/enrich_metadata.py`` or any presentation code.

Recovery uses three independent metadata indexes:

* OpenAlex
* Crossref
* Semantic Scholar
* Elsevier Article API (when ELSEVIER_API_KEY is configured)

Date confidence is strict:

* A single source online date stays at confidence ``C``.
* Only OpenAlex and Crossref agreeing on the same online date, with a
  plausible year, may upgrade to confidence ``B``.
* ``accepted_date``, ``first_seen``, ``issue_date``, and detection time are
  never treated as an official online date.
* Existing ``A``/``B`` dates are never downgraded.

When run without ``--dry-run`` the script writes recovered fields back to the
canonical daily archives and ``seen.json``, then reconciles the metadata retry
queue, ingestion retry queue, and ingestion exclusion ledger. It always writes
``data/metadata_recovery_batch_audit.json`` with measured before/after counts.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from common import (
    BEIJING_TZ,
    DATA_DIR,
    clean_abstract_text,
    normalize_doi,
    now_iso,
    read_json,
    write_json,
)
from enrich_metadata import (
    crossref_doi_metadata,
    elsevier_api_online_date,
    extract_elsevier_api_abstract,
    extract_elsevier_pii,
    openalex_doi_metadata,
    semantic_scholar_doi_metadata,
    semantic_scholar_throttle_state,
)
from public_integrity import canonical_route_key, has_abstract_boilerplate, sanitize_record_paths, strong_identity_keys
from audit_metadata_recovery import audit_metadata_recovery
from reconcile_retry_queues import reconcile_retry_queue


WEAK_DATE_CONFIDENCE = {"", "C", "D", "F", "unknown"}
STRONG_DATE_CONFIDENCE = {"A", "B"}
ABSTRACT_MIN_LEN = 50
YEAR_MIN, YEAR_MAX = 1990, 2030
DATE_FIELDS = ("official_date", "available_online", "published_online", "issue_date")
PRIORITY_ABSTRACT_STATUSES = {
    "blocked-captcha",
    "elsevier-metadata-only",
    "proxy-request-failed",
    "abstract-not-exposed",
}
EMPTY_BURST_RETRY_AFTER = 10
EMPTY_BURST_WAIT_SECONDS = 5.0
ELSEVIER_MIN_INTERVAL_SECONDS = 0.3
ELSEVIER_MAX_RETRIES = 1
ELSEVIER_MAX_BACKOFF_SECONDS = 60.0
_ELSEVIER_LOCK = threading.Lock()
_ELSEVIER_LAST_REQUEST = 0.0
_URLOPEN = urllib.request.urlopen


def is_placeholder_abstract(value: Any) -> bool:
    """Return True when text is missing, too short, or boilerplate-only."""
    if value is None:
        return True
    text = " ".join(str(value).split())
    if len(text) < ABSTRACT_MIN_LEN:
        return True
    if has_abstract_boilerplate(text):
        return True
    lower = text.casefold()
    prefixes = (
        "abstractthis ",
        "abstract this ",
        "abstract. this ",
        "abstract : ",
        "abstractabstract",
    )
    if lower.startswith(prefixes):
        return True
    return False


def record_needs_abstract(record: dict[str, Any]) -> bool:
    return is_placeholder_abstract(record.get("abstract"))


def record_needs_authors(record: dict[str, Any]) -> bool:
    authors = record.get("authors")
    if not authors:
        return True
    if isinstance(authors, list):
        return len(authors) == 0
    if isinstance(authors, str):
        return not authors.strip()
    return True


def record_has_official_date(record: dict[str, Any]) -> bool:
    return any(str(record.get(field) or "").strip() for field in DATE_FIELDS)


def record_needs_date(record: dict[str, Any]) -> bool:
    confidence = str(record.get("date_confidence") or "")
    if confidence in STRONG_DATE_CONFIDENCE:
        return False
    return True


def is_priority_abstract(record: dict[str, Any]) -> bool:
    """True for recent in-press Elsevier / CAPTCHA-backed abstract debt."""
    if not record_needs_abstract(record):
        return False
    status = str(record.get("abstract_enrichment_status") or "").strip()
    if status in PRIORITY_ABSTRACT_STATUSES:
        return True
    publisher_text = " ".join(
        str(record.get(field) or "")
        for field in ("publisher", "journal", "source", "source_id")
    ).casefold()
    return "elsevier" in publisher_text or "sciencedirect" in publisher_text


def needs_recovery(record: dict[str, Any]) -> tuple[bool, bool, bool]:
    return (
        record_needs_abstract(record),
        record_needs_authors(record),
        record_needs_date(record),
    )


def first_seen_timestamp(record: dict[str, Any]) -> float:
    value = str(
        record.get("first_seen")
        or record.get("first_seen_at")
        or record.get("detected_at")
        or ""
    )
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def record_priority(record: dict[str, Any]) -> tuple[int, int, int, int, int, float]:
    needs_abs, needs_auth, needs_date = needs_recovery(record)
    # Fresh records, in-press Elsevier/CAPTCHA abstracts, and fully missing
    # dates are processed first. Negate the timestamp so newer sorts first.
    return (
        0 if needs_abs else 1,
        0 if is_priority_abstract(record) else 1,
        0 if needs_date else 1,
        0 if not record_has_official_date(record) else 1,
        0 if needs_auth else 1,
        -first_seen_timestamp(record),
    )


def load_daily_candidates(
    daily_dir: Path,
    *,
    limit: int,
    recent_days: int,
) -> tuple[
    list[tuple[Path, dict[str, Any]]],
    dict[str, list[tuple[Path, dict[str, Any]]]],
    dict[Path, list[dict[str, Any]]],
]:
    """Load DOI-backed daily records that need recovery.

    Returns ``(candidates, records_by_doi, payloads_by_path)`` where
    ``records_by_doi`` maps a normalized DOI to every daily occurrence and
    ``payloads_by_path`` maps each loaded daily file to its in-memory record
    list (so recovery can be written back without re-reading stale bytes).
    """
    records_by_doi: dict[str, list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
    payloads_by_path: dict[Path, list[dict[str, Any]]] = {}
    candidate_dois: list[str] = []
    seen_dois: set[str] = set()
    cutoff = ""
    if recent_days and recent_days > 0:
        cutoff = (datetime.now(BEIJING_TZ).date() - timedelta(days=max(0, recent_days - 1))).isoformat()

    for path in sorted(daily_dir.glob("*.json")):
        if cutoff and path.stem < cutoff:
            continue
        payload = read_json(path, [])
        if not isinstance(payload, list):
            continue
        payloads_by_path[path] = payload
        for record in payload:
            if not isinstance(record, dict):
                continue
            doi = normalize_doi(record.get("doi"))
            if not doi:
                continue
            records_by_doi[doi].append((path, record))
            if doi in seen_dois:
                continue
            needs_abs, needs_auth, needs_date = needs_recovery(record)
            if not (needs_abs or needs_auth or needs_date):
                continue
            seen_dois.add(doi)
            candidate_dois.append(doi)

    # Deterministic, recovery-priority sort, then apply the limit.
    candidate_dois.sort(key=lambda doi: record_priority(records_by_doi[doi][0][1]))
    selected = candidate_dois if limit <= 0 else candidate_dois[: max(0, limit)]
    records_by_doi = {
        doi: records_by_doi[doi]
        for doi in selected
    }
    candidates = [
        (path, record)
        for doi in selected
        for path, record in records_by_doi[doi]
    ]
    return candidates, dict(records_by_doi), payloads_by_path


def reasonable_year(value: Any) -> bool:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value or "").strip())
    if not match:
        return False
    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))
    if not (YEAR_MIN <= year <= YEAR_MAX):
        return False
    if month < 1 or month > 12 or day < 1 or day > 31:
        return False
    return True


def online_date_from_metadata(provider: str, metadata: dict[str, Any]) -> str | None:
    """Return only publisher-online dates; issue/created dates are never online."""
    if provider == "crossref":
        return str(
            metadata.get("available_online")
            or metadata.get("published_online")
            or ""
        ).strip() or None
    if provider in {"openalex", "semantic-scholar", "elsevier"}:
        return str(
            metadata.get("available_online")
            or metadata.get("published_online")
            or ""
        ).strip() or None
    return None


def decide_date_update(
    record: dict[str, Any],
    providers: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Apply strict date confidence rules and return a date update dict."""
    existing_confidence = str(record.get("date_confidence") or "")
    if existing_confidence in STRONG_DATE_CONFIDENCE:
        return None

    oa_date = online_date_from_metadata("openalex", providers.get("openalex", {}))
    cr_date = online_date_from_metadata("crossref", providers.get("crossref", {}))
    ss_date = online_date_from_metadata("semantic-scholar", providers.get("semantic-scholar", {}))
    els_date = online_date_from_metadata("elsevier", providers.get("elsevier", {}))
    els_confidence = str(providers.get("elsevier", {}).get("date_confidence") or "")

    # Elsevier's explicit "Available online" phrase is publisher-official and
    # must never be downgraded by cross-validation.
    if els_confidence == "A" and els_date and reasonable_year(els_date):
        return {
            "available_online": els_date,
            "published_online": els_date,
            "date_source": "elsevier_article_api",
            "date_confidence": "A",
        }

    # Two independent sources agreeing on the same plausible date upgrade to B.
    for left, right, source in (
        (oa_date, cr_date, "openalex+crossref_crossvalidated"),
        (els_date, cr_date, "elsevier+crossref_crossvalidated"),
    ):
        if (
            left
            and right
            and left == right
            and reasonable_year(left)
        ):
            return {
                "available_online": left,
                "published_online": left,
                "date_source": source,
                "date_confidence": "B",
            }

    # A single source may only fill a missing date, staying at C. It can never
    # replace or upgrade an existing C and must never touch an A/B date.
    has_online_date = any(
        str(record.get(field) or "").strip()
        for field in ("official_date", "available_online", "published_online")
    )
    if has_online_date:
        return None
    for candidate, source in (
        (oa_date, "openalex_publication_date"),
        (cr_date, "crossref_doi_published_online"),
        (ss_date, "semantic_scholar_publication_date"),
        (els_date, "elsevier_article_api_display_date"),
    ):
        if candidate and reasonable_year(candidate):
            return {
                "available_online": candidate,
                "published_online": candidate,
                "date_source": source,
                "date_confidence": "C",
            }
    return None


def elsevier_env_credentials() -> tuple[str, str]:
    api_key = (os.environ.get("ELSEVIER_API_KEY") or os.environ.get("ELS_API_KEY") or "").strip()
    inst_token = (
        os.environ.get("ELSEVIER_INST_TOKEN")
        or os.environ.get("ELSEVIER_INSTTOKEN")
        or ""
    ).strip()
    return api_key, inst_token


def _els_throttle() -> None:
    global _ELSEVIER_LAST_REQUEST
    with _ELSEVIER_LOCK:
        wait = ELSEVIER_MIN_INTERVAL_SECONDS - (time.monotonic() - _ELSEVIER_LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _ELSEVIER_LAST_REQUEST = time.monotonic()


def _els_request(
    url: str,
    timeout: int,
    api_key: str,
    inst_token: str,
) -> tuple[str, dict[str, str]]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "econ-paper-monitor/1.0 (https://github.com/academic-door/econ-paper-monitor)",
    }
    if api_key:
        headers["X-ELS-APIKey"] = api_key
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token
    request = urllib.request.Request(url, headers=headers)
    with _URLOPEN(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        response_headers = dict(response.headers.items())
    return body, response_headers


def _els_retry_after(value: Any) -> float:
    try:
        return max(0.0, float(str(value or "").strip()))
    except ValueError:
        return 0.0


def _els_rate_limit(headers: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in (
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
        "X-RateLimit-Used",
    ):
        value = (headers or {}).get(key)
        if value is not None:
            result[key] = str(value)
    return result


def elsevier_doi_metadata(doi: str, timeout: int) -> dict[str, Any]:
    """Fetch title/abstract from the Elsevier Article API.

    Returns ``_status`` in available/not_found/http_error/rate_limited/
    not_configured and never prints credentials.  A 429 is retried once with
    ``Retry-After`` backoff; ``X-RateLimit-*`` headers are returned for
    provider-health aggregation.
    """
    api_key, inst_token = elsevier_env_credentials()
    if not api_key:
        return {"_status": "not_configured"}
    encoded_doi = urllib.parse.quote(doi, safe="")
    url = (
        "https://api.elsevier.com/content/article/doi/"
        f"{encoded_doi}?httpAccept=application%2Fjson"
    )
    body = ""
    response_headers: dict[str, str] = {}
    for attempt in range(ELSEVIER_MAX_RETRIES + 1):
        _els_throttle()
        try:
            body, response_headers = _els_request(url, timeout, api_key, inst_token)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < ELSEVIER_MAX_RETRIES:
                retry_after = _els_retry_after((exc.headers or {}).get("Retry-After"))
                if retry_after:
                    time.sleep(min(retry_after, ELSEVIER_MAX_BACKOFF_SECONDS))
                continue
            status = (
                "rate_limited"
                if exc.code == 429
                else ("not_found" if exc.code == 404 else "http_error")
            )
            return {"_status": status, "_rate_limit": _els_rate_limit(exc.headers)}
        except Exception:
            return {"_status": "http_error"}
    try:
        payload = json.loads(body) if body else {}
    except ValueError:
        return {"_status": "http_error", "_rate_limit": _els_rate_limit(response_headers)}
    response = payload.get("full-text-retrieval-response") if isinstance(payload, dict) else None
    core = response.get("coredata") if isinstance(response, dict) else None
    if not isinstance(core, dict):
        return {"_status": "not_found", "_rate_limit": _els_rate_limit(response_headers)}
    result: dict[str, Any] = {
        "_status": "available",
        "_rate_limit": _els_rate_limit(response_headers),
    }
    title = str(core.get("dc:title") or "").strip()
    if title:
        result["title"] = title
    pii = extract_elsevier_pii(str(core.get("prism:url") or ""), str(core.get("pii") or ""))
    if pii:
        result["pii"] = pii
    result.update(elsevier_api_online_date(core))
    abstract = extract_elsevier_api_abstract(response, core)
    if abstract:
        result["abstract"] = abstract
        result["abstract_source"] = "elsevier_article_api_full" if api_key else "elsevier_article_api"
    return result


def fetch_metadata_for_doi(
    doi: str,
    timeout: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Fetch metadata from every configured provider for one DOI."""
    providers: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    getters = (
        ("openalex", openalex_doi_metadata),
        ("crossref", crossref_doi_metadata),
        ("semantic-scholar", semantic_scholar_doi_metadata),
    )
    if doi.startswith("10.1016/"):
        getters = getters + (("elsevier", elsevier_doi_metadata),)
    for name, getter in getters:
        try:
            metadata = getter(doi, timeout) or {}
            providers[name] = metadata if isinstance(metadata, dict) else {}
        except Exception as exc:  # noqa: BLE001
            errors[name] = f"{type(exc).__name__}: {exc}"
            providers[name] = {}
    return providers, errors


def summarize_provider_health(
    providers_by_doi: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Summarize per-provider availability and failure statuses for auditing."""
    names = ("openalex", "crossref", "semantic-scholar", "elsevier")
    health: dict[str, dict[str, Any]] = {}
    for name in names:
        attempts = available = empty = 0
        statuses: Counter[str] = Counter()
        rate_limits: list[dict[str, str]] = []
        for providers in providers_by_doi.values():
            metadata = providers.get(name) or {}
            attempts += 1
            status = str(metadata.get("_status") or "")
            if status == "available":
                statuses[status] += 1
                available += 1
            elif status:
                statuses[status] += 1
            elif metadata:
                available += 1
            else:
                empty += 1
            rate_limit = metadata.get("_rate_limit")
            if isinstance(rate_limit, dict) and rate_limit:
                rate_limits.append(rate_limit)
        health[name] = {
            "attempts": attempts,
            "available": available,
            "empty": empty,
            "statuses": dict(statuses),
            "rate_limited": int(statuses.get("rate_limited", 0)),
            "skipped": int(statuses.get("skipped_rate_limited", 0)),
            "failed": attempts - available - empty,
        }
        if rate_limits:
            health[name]["rate_limit_headers"] = min(
                rate_limits,
                key=lambda item: _rate_limit_remaining(item),
            )
    health["semantic-scholar"]["api_key_configured"] = bool(
        (os.environ.get("S2_API_KEY") or os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or "").strip()
    )
    health["semantic-scholar"]["control"] = semantic_scholar_throttle_state()
    elsevier_api_key, elsevier_inst_token = elsevier_env_credentials()
    health["elsevier"]["api_key_configured"] = bool(elsevier_api_key)
    health["elsevier"]["inst_token_configured"] = bool(elsevier_inst_token)
    return health


def _rate_limit_remaining(rate_limit: dict[str, str]) -> int:
    try:
        return int(rate_limit.get("X-RateLimit-Remaining") or 0)
    except ValueError:
        return 0


def write_provider_health(
    data_dir: Path,
    provider_health: dict[str, dict[str, Any]],
    *,
    candidates: int,
    recovered_fields: Counter[str],
    checked_at: str,
) -> None:
    """Persist a bounded provider-health history for operational audit."""
    path = data_dir / "metadata_provider_health.json"
    payload = read_json(path, {"runs": []})
    if not isinstance(payload, dict):
        payload = {"runs": []}
    runs = payload.get("runs")
    if not isinstance(runs, list):
        runs = []
    runs.append(
        {
            "checked_at": checked_at,
            "candidates": candidates,
            "recovered": dict(recovered_fields),
            "providers": provider_health,
        }
    )
    payload["runs"] = runs[-20:]
    payload["latest"] = runs[-1]
    write_json(path, payload)


def apply_recovery(
    record: dict[str, Any],
    providers: dict[str, dict[str, Any]],
) -> tuple[bool, list[str]]:
    """Apply recovered fields to a record. Returns (changed, recovered_fields)."""
    changed = False
    recovered: list[str] = []
    needs_abs, needs_auth, needs_date = needs_recovery(record)
    manual_abstract = bool(
        str(record.get("abstract_source") or "").startswith("manual-")
        and str(record.get("abstract") or "").strip()
    )

    if needs_abs and not manual_abstract:
        for source, metadata in (
            ("openalex", providers.get("openalex", {})),
            ("crossref", providers.get("crossref", {})),
            ("semantic-scholar", providers.get("semantic-scholar", {})),
            ("elsevier", providers.get("elsevier", {})),
        ):
            abstract = clean_abstract_text(metadata.get("abstract"))
            if is_placeholder_abstract(abstract):
                continue
            record["abstract"] = abstract
            record["abstract_source"] = metadata.get("abstract_source", source)
            record["abstract_status_code"] = "available"
            record["abstract_completeness"] = "full"
            record.pop("abstract_status", None)
            record.pop("abstract_truncated", None)
            recovered.append("abstract")
            changed = True
            break

    if needs_auth:
        for source, metadata in (
            ("openalex", providers.get("openalex", {})),
            ("crossref", providers.get("crossref", {})),
            ("semantic-scholar", providers.get("semantic-scholar", {})),
        ):
            authors = metadata.get("authors")
            if isinstance(authors, list) and authors:
                record["authors"] = authors[:12]
                record["authors_status_code"] = "available"
                record.pop("authors_status", None)
                recovered.append("authors")
                changed = True
                break

    if needs_date:
        update = decide_date_update(record, providers)
        if update:
            for key, value in update.items():
                if record.get(key) != value:
                    record[key] = value
                    changed = True
            record["official_date_status"] = "available"
            recovered.append("date")

    return changed, recovered


def update_seen_records(
    seen_payload: Any,
    records_by_doi: dict[str, list[tuple[Path, dict[str, Any]]]],
    providers_by_doi: dict[str, dict[str, dict[str, Any]]],
) -> tuple[bool, int]:
    """Apply the same recovery to matching seen records."""
    if not isinstance(seen_payload, dict):
        return False, 0
    papers = seen_payload.get("papers")
    if not isinstance(papers, dict):
        return False, 0

    doi_to_recover = {
        doi: providers_by_doi[doi]
        for doi in providers_by_doi
        if doi in records_by_doi
    }
    # Also allow matching by detail_key from any recovered daily record.
    detail_keys = {
        str(record.get("detail_key") or ""): doi
        for doi, occurrences in records_by_doi.items()
        for _, record in occurrences
        if record.get("detail_key") and doi in doi_to_recover
    }

    changed = False
    updated = 0
    for seen_key, record in papers.items():
        if not isinstance(record, dict):
            continue
        doi = normalize_doi(record.get("doi"))
        matched_doi = None
        if doi and doi in doi_to_recover:
            matched_doi = doi
        elif detail_keys.get(str(record.get("detail_key") or "")):
            matched_doi = detail_keys[str(record.get("detail_key") or "")]
        if not matched_doi:
            continue
        did_change, _ = apply_recovery(record, doi_to_recover[matched_doi])
        if did_change:
            changed = True
            updated += 1
    return changed, updated


def reconcile_metadata_queue(
    queue_path: Path,
    seen_payload: Any,
    records_by_doi: dict[str, list[tuple[Path, dict[str, Any]]]],
    providers_by_doi: dict[str, dict[str, dict[str, Any]]],
) -> tuple[bool, int]:
    """Remove resolved items from the metadata retry queue and keep history."""
    queue = read_json(queue_path, {"records": []})
    if not isinstance(queue, dict):
        return False, 0
    records = queue.get("records")
    if not isinstance(records, list):
        return False, 0

    seen_papers = seen_payload.get("papers") if isinstance(seen_payload, dict) else None
    recovered_dois = set(providers_by_doi)

    def find_canonical(identity: str) -> dict[str, Any] | None:
        if identity.startswith("doi:"):
            doi = normalize_doi(identity.removeprefix("doi:"))
            if doi in records_by_doi:
                return records_by_doi[doi][0][1]
        if isinstance(seen_papers, dict):
            for record in seen_papers.values():
                if not isinstance(record, dict):
                    continue
                if str(record.get("doi") or "").strip().casefold() == identity.removeprefix("doi:").casefold():
                    return record
        return None

    resolved: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    resolved_at = now_iso()
    for item in records:
        if not isinstance(item, dict):
            pending.append(item)
            continue
        identity = str(item.get("identity") or "")
        if identity.startswith("doi:") and identity.removeprefix("doi:") in recovered_dois:
            # Only resolve when the underlying record is now complete.
            canonical = find_canonical(identity)
            if canonical is not None:
                needs_abs, needs_auth, needs_date = needs_recovery(canonical)
                if not (needs_abs or needs_auth or needs_date):
                    resolved_item = copy.deepcopy(item)
                    resolved_item.update(
                        {
                            "retry_status": "resolved",
                            "resolved_at": resolved_at,
                            "resolution_identity": identity,
                            "resolution_location": "seen",
                            "canonical_detail_key": canonical_route_key(item, canonical.get("detail_key")),
                        }
                    )
                    resolved.append(resolved_item)
                    continue
        pending.append(item)

    if not resolved and len(pending) == len(records):
        return False, 0

    history = queue.get("resolved_records") if isinstance(queue.get("resolved_records"), list) else []
    existing = {
        str(entry.get("resolution_identity") or entry.get("identity") or "").casefold()
        for entry in history
        if isinstance(entry, dict)
    }
    for entry in resolved:
        key = str(entry.get("resolution_identity") or entry.get("identity") or "").casefold()
        if key and key not in existing:
            history.append(entry)
            existing.add(key)

    queue["records"] = pending
    queue["resolved_records"] = history
    queue["total_candidates"] = len(pending)
    queue["generated_at"] = resolved_at
    queue["note"] = (
        "Resolved candidates are retained in resolved_records for auditability; "
        "records contains only pending work."
    )
    write_json(queue_path, queue)
    return True, len(resolved)


def run_recovery(
    *,
    data_dir: Path,
    limit: int,
    recent_days: int,
    timeout: int,
    workers: int,
    dry_run: bool,
) -> dict[str, Any]:
    daily_dir = data_dir / "daily"
    seen_path = data_dir / "seen.json"
    queue_path = data_dir / "metadata_retry_queue.json"

    candidates, records_by_doi, payloads_by_path = load_daily_candidates(
        daily_dir,
        limit=limit,
        recent_days=recent_days,
    )
    candidate_dois = sorted(records_by_doi.keys())
    if not candidate_dois:
        before = audit_metadata_recovery(data_dir)
        return {
            "checked_at": now_iso(),
            "mode": "dry-run" if dry_run else "write",
            "candidates": 0,
            "recovered": {"abstracts": 0, "authors": 0, "dates": 0},
            "errors": {"api": {}, "no_doi": 0},
            "failure_reasons": Counter({"no_candidates": 1}),
            "files": {"daily_changed": 0, "seen_updated": 0, "queue_resolved": 0, "ledger_resolved": 0},
            "before": before,
            "after": before,
        }

    seen_payload = read_json(seen_path, {"papers": {}})
    before = audit_metadata_recovery(data_dir)
    providers_by_doi: dict[str, dict[str, dict[str, Any]]] = {}
    api_errors: dict[str, int] = Counter()
    recovered_fields = Counter()
    shortfall = Counter()
    date_confidence_changes = Counter()
    failure_reasons = Counter()
    changed_records_by_path: dict[Path, list[dict[str, Any]]] = defaultdict(list)

    def process_doi(doi: str) -> tuple[str, dict[str, Any]]:
        providers, errors = fetch_metadata_for_doi(doi, timeout)
        for name in errors:
            api_errors[name] += 1
        return doi, providers

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(process_doi, doi): doi for doi in candidate_dois}
        for future in as_completed(futures):
            doi, providers = future.result()
            providers_by_doi[doi] = providers
    provider_health = summarize_provider_health(providers_by_doi)

    # A provider that returns only empty responses for the whole batch is most
    # likely a transient API outage. Retry it once after a short backoff and
    # record the empty burst honestly in provider health.
    retried_after_empty: dict[str, bool] = {}
    for name in ("openalex",):
        health = provider_health.get(name) or {}
        attempts = int(health.get("attempts") or 0)
        if (
            attempts > 0
            and int(health.get("available") or 0) == 0
            and int(health.get("empty") or 0) >= min(EMPTY_BURST_RETRY_AFTER, attempts)
        ):
            time.sleep(EMPTY_BURST_WAIT_SECONDS)
            with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
                retry_futures = {
                    executor.submit(openalex_doi_metadata, doi, timeout): doi for doi in candidate_dois
                }
                for future in as_completed(retry_futures):
                    doi = retry_futures[future]
                    metadata = future.result() or {}
                    if isinstance(metadata, dict) and metadata:
                        providers_by_doi[doi][name] = metadata
            retried_after_empty[name] = True
    if retried_after_empty:
        provider_health = summarize_provider_health(providers_by_doi)
        for name in retried_after_empty:
            provider_health[name]["empty_burst"] = True
            provider_health[name]["retried_after_empty"] = True
    else:
        for name, health in provider_health.items():
            health["empty_burst"] = bool(
                int(health.get("attempts") or 0) > 0
                and int(health.get("available") or 0) == 0
                and int(health.get("empty") or 0) > 0
            )
            health["retried_after_empty"] = False

    # Apply recovery to every canonical daily occurrence.
    for doi, occurrences in records_by_doi.items():
        if doi not in providers_by_doi:
            continue
        providers = providers_by_doi[doi]
        for path, record in occurrences:
            before_needs = needs_recovery(record)
            did_change, fields = apply_recovery(record, providers)
            if did_change:
                changed_records_by_path[path].append(record)
            for field in fields:
                recovered_fields[field] += 1
            if "date" in fields:
                date_confidence_changes[str(record.get("date_confidence") or "")] += 1
            if before_needs[0] and "abstract" not in fields:
                shortfall["abstract_not_recovered"] += 1
            if before_needs[1] and "authors" not in fields:
                shortfall["authors_not_recovered"] += 1
            if before_needs[2] and "date" not in fields:
                shortfall["date_not_recovered"] += 1
            after_needs = needs_recovery(record)
            if before_needs == after_needs and not fields:
                # No provider returned usable metadata for this record.
                has_any = any(providers.values())
                failure_reasons["api_no_usable_metadata" if has_any else "all_apis_empty"] += 1

    if not dry_run:
        daily_changed = 0
        for path, records in changed_records_by_path.items():
            payload = payloads_by_path.get(path)
            if isinstance(payload, list):
                sanitize_record_paths(payload)
                write_json(path, payload)
                daily_changed += 1
        seen_changed, seen_updated = update_seen_records(
            seen_payload,
            records_by_doi,
            providers_by_doi,
        )
        seen_sanitized = False
        if isinstance(seen_payload, dict):
            seen_papers = seen_payload.get("papers")
            if isinstance(seen_papers, dict):
                seen_sanitized = bool(sanitize_record_paths(list(seen_papers.values())))
        if seen_changed or seen_sanitized:
            write_json(seen_path, seen_payload)
        queue_changed, queue_resolved = reconcile_metadata_queue(
            queue_path,
            seen_payload,
            records_by_doi,
            providers_by_doi,
        )
        ledger_report = reconcile_retry_queue(data_dir)
        ledger_resolved = int(ledger_report.get("resolved_now") or 0)
        write_provider_health(
            data_dir,
            provider_health,
            candidates=len(candidate_dois),
            recovered_fields=recovered_fields,
            checked_at=now_iso(),
        )
    else:
        daily_changed = 0
        seen_updated = 0
        queue_resolved = 0
        ledger_resolved = 0

    after = audit_metadata_recovery(data_dir)

    report = {
        "checked_at": now_iso(),
        "mode": "dry-run" if dry_run else "write",
        "candidates": len(candidate_dois),
        "daily_occurrences": len(candidates),
        "providers": {
            name: sum(1 for providers in providers_by_doi.values() if providers.get(name))
            for name in ("openalex", "crossref", "semantic-scholar", "elsevier")
        },
        "provider_health": provider_health,
        "provider_empty_burst": retried_after_empty,
        "recovered": dict(recovered_fields),
        "date_confidence_changes": dict(date_confidence_changes),
        "shortfall": dict(shortfall),
        "errors": {
            "api": dict(api_errors),
            "no_doi": 0,
        },
        "failure_reasons": dict(failure_reasons),
        "files": {
            "daily_changed": daily_changed,
            "seen_updated": seen_updated,
            "queue_resolved": queue_resolved,
            "ledger_resolved": ledger_resolved,
        },
        "before": before,
        "after": after,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum unique DOI candidates.")
    parser.add_argument("--recent-days", type=int, default=5000)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    report = run_recovery(
        data_dir=args.data_dir,
        limit=args.limit,
        recent_days=args.recent_days,
        timeout=args.timeout,
        workers=args.workers,
        dry_run=args.dry_run,
    )
    output = args.output or args.data_dir / "metadata_recovery_batch_audit.json"
    write_json(output, report)

    print(
        f"metadata batch recovery mode={report['mode']} candidates={report['candidates']} "
        f"abstracts={report['recovered'].get('abstract', 0)} "
        f"authors={report['recovered'].get('authors', 0)} "
        f"dates={report['recovered'].get('date', 0)} "
        f"daily_changed={report['files']['daily_changed']} "
        f"seen_updated={report['files']['seen_updated']} "
        f"queue_resolved={report['files']['queue_resolved']} "
        f"ledger_resolved={report['files']['ledger_resolved']}"
    )


if __name__ == "__main__":
    main()
