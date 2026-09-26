#!/usr/bin/env python3
"""Bounded Jina transport-provenance qualification for Daily Door #339.

Runs existing acquisition code against current configured publisher targets and a
small sample of current metadata-retry records. It records transport class and
outcome only; credentials and response payloads are never persisted.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

import enrich_metadata
import fetch_priority_toc as priority
from common import DATA_DIR, load_journals, now, read_json, write_json

IMMEDIATE_RETIRE_KINDS = {"springer_online_first", "tandf_latest_articles"}
DETAIL_SAMPLE_BUCKETS = {"Elsevier", "Wiley", "OUP"}


def transport(url: str) -> str:
    return "jina" if url.startswith("https://r.jina.ai/") else "direct"


def safe_locator(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc + parsed.path[:120]


def run_target(journal: dict, target: dict, timeout: int, max_items: int) -> dict:
    events: list[dict] = []
    original = priority.fetch_one

    def traced(url: str, inner_timeout: int) -> str:
        try:
            text = original(url, inner_timeout)
        except Exception as exc:  # noqa: BLE001
            events.append(
                {
                    "transport": transport(url),
                    "locator": safe_locator(url),
                    "outcome": priority.classify_transport_error(exc),
                }
            )
            raise
        events.append(
            {
                "transport": transport(url),
                "locator": safe_locator(url),
                "outcome": "SUCCESS",
                "response_chars": len(text),
            }
        )
        return text

    priority.fetch_one = traced
    try:
        records, fallback_count, publisher_success, messages, failed, outcome = priority.fetch_target_with_fallback(
            journal,
            target,
            timeout=timeout,
            detail_limit=0,
            max_items=max_items,
        )
    finally:
        priority.fetch_one = original

    direct_success = any(e["transport"] == "direct" and e["outcome"] == "SUCCESS" for e in events)
    jina_success = any(e["transport"] == "jina" and e["outcome"] == "SUCCESS" for e in events)
    unique_jina_value = bool(jina_success and not direct_success and publisher_success and records)
    return {
        "journal_id": journal.get("id"),
        "kind": target.get("kind"),
        "direct_url": safe_locator(str(target.get("url") or "")),
        "events": events,
        "records": len(records),
        "crossref_fallback_records": fallback_count,
        "publisher_success": publisher_success,
        "failed": failed,
        "outcome": outcome,
        "unique_jina_value": unique_jina_value,
        "messages": messages[-4:],
    }


def load_retry_records(path: Path) -> list[dict]:
    payload = read_json(path, [])
    if isinstance(payload, dict):
        for key in ("items", "records", "queue"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
        return []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def detail_probe(record: dict, timeout: int) -> dict:
    bucket = enrich_metadata.publisher_bucket(record)
    url = ""
    for candidate in enrich_metadata.candidate_urls(record):
        host = urlparse(candidate).netloc.casefold()
        if host in {
            "www.sciencedirect.com",
            "sciencedirect.com",
            "onlinelibrary.wiley.com",
            "academic.oup.com",
        }:
            url = candidate
            break
    result = enrich_metadata.publisher_proxy_metadata(url, timeout) if url else {}
    return {
        "bucket": bucket,
        "doi": str(record.get("doi") or "")[:120],
        "locator": safe_locator(url) if url else "",
        "status": result.get("_status") or ("abstract" if result.get("abstract") else "empty"),
        "abstract_supplied": bool(result.get("abstract")),
        "unique_jina_value": bool(result.get("abstract")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DATA_DIR / "audits" / "jina_transport_qualification.json")
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--max-items", type=int, default=8)
    parser.add_argument("--detail-samples-per-bucket", type=int, default=2)
    args = parser.parse_args()

    journals = {str(j.get("id")): j for j in load_journals(DATA_DIR / "journals.yml")}
    toc_results: list[dict] = []
    immediate_retire: list[dict] = []
    for journal_id, targets in priority.TARGETS.items():
        journal = journals.get(journal_id)
        if not journal:
            continue
        for target in targets:
            if str(target.get("kind")) in IMMEDIATE_RETIRE_KINDS:
                immediate_retire.append({"journal_id": journal_id, "kind": target.get("kind")})
                continue
            if not any(str(url).startswith("https://r.jina.ai/") for url in (target.get("fallback_urls") or [])):
                continue
            toc_results.append(run_target(journal, target, args.timeout, args.max_items))

    detail_results: list[dict] = []
    per_bucket: dict[str, int] = {}
    for item in load_retry_records(DATA_DIR / "metadata_retry_queue.json"):
        record = item.get("record") if isinstance(item.get("record"), dict) else item
        if not isinstance(record, dict):
            continue
        if str(record.get("abstract") or "").strip():
            continue
        bucket = enrich_metadata.publisher_bucket(record)
        if bucket not in DETAIL_SAMPLE_BUCKETS:
            continue
        if per_bucket.get(bucket, 0) >= args.detail_samples_per_bucket:
            continue
        detail_results.append(detail_probe(record, args.timeout))
        per_bucket[bucket] = per_bucket.get(bucket, 0) + 1

    positive = [
        {"scope": "priority_toc", **row}
        for row in toc_results
        if row.get("unique_jina_value")
    ] + [
        {"scope": "publisher_detail", **row}
        for row in detail_results
        if row.get("unique_jina_value")
    ]

    report = {
        "generated_at": now(),
        "issue": 339,
        "policy": "RETIRE_BY_DEFAULT_PENDING_DAILY_PROVENANCE_GATE",
        "immediate_retire_configured": immediate_retire,
        "priority_toc": toc_results,
        "publisher_detail_samples": detail_results,
        "positive_unique_jina": positive,
        "decision": "RETAIN_EXACT_PROVEN_PATHS_ONLY" if positive else "RETIRE_ALL_DAILY_JINA",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, report)
    print(json.dumps({
        "decision": report["decision"],
        "priority_targets": len(toc_results),
        "detail_samples": len(detail_results),
        "positive_unique_jina": len(positive),
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
