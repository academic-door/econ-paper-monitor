#!/usr/bin/env python3
"""Bounded Jina transport-provenance qualification for Daily Door #339.

Runs existing acquisition code against current configured publisher targets and a
small sample of current metadata-retry records. It records transport class and
outcome only; credentials and response payloads are never persisted.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import backfill_iza_authors_legacy as legacy_wp
import enrich_metadata
import fetch_cnki_rss
import fetch_priority_toc as priority
import qualify_jina_working_papers as working_probe
from common import DATA_DIR, load_journals, read_json, write_json

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
            detail_limit=2 if str(target.get("kind") or "") == "restud_official_accepted" else 0,
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

    working_paper_results: list[dict] = []
    working_sources = {
        str(source.get("id") or ""): source
        for source in working_probe.fetch_preprints.load_sources(DATA_DIR / "working_paper_sources.yml")
    }
    for source_id in ("cepr-dp", "fed-feds"):
        source = working_sources.get(source_id)
        if not source:
            continue
        try:
            current_records, mode = working_probe.fetch_preprints.fetch_source(source, timeout=args.timeout, limit=2)
        except Exception as exc:  # noqa: BLE001
            working_paper_results.append({"source_id": source_id, "listing_error": type(exc).__name__, "unique_jina_value": False})
            continue
        for record in current_records[:2]:
            if not record.get("url"):
                continue
            row = working_probe.probe(source, record, args.timeout)
            row["listing_mode"] = mode
            working_paper_results.append(row)

    residual_results: list[dict] = []

    cnki_sources = fetch_cnki_rss.load_cnki_sources(DATA_DIR / "cnki_rss_sources.yml")
    if cnki_sources:
        sample = cnki_sources[0]
        try:
            xml_text, fetched_mode = fetch_cnki_rss.fetch_cnki_text(sample)
            transport_name = str(fetched_mode).split(":", 1)[0]
            residual_results.append({
                "scope": "cnki_rss",
                "journal_id": sample.get("journal_id"),
                "transport": transport_name,
                "payload_chars": len(xml_text),
                "unique_jina_value": transport_name == "jina-relay",
            })
        except Exception as exc:  # noqa: BLE001
            residual_results.append({
                "scope": "cnki_rss",
                "journal_id": sample.get("journal_id"),
                "outcome": type(exc).__name__,
                "unique_jina_value": False,
            })

    status = read_json(DATA_DIR / "status.json", {})
    sd_status = ((status.get("sources") or {}).get("sciencedirect-search") or {}) if isinstance(status, dict) else {}
    sd_message = str(sd_status.get("message") or "")
    residual_results.append({
        "scope": "sciencedirect_search",
        "count": sd_status.get("count"),
        "ok": sd_status.get("ok"),
        "production_route": "official_api_v2_put_title_wildcard" if "route=official_api_v2_put_title_wildcard" in sd_message else "unknown",
        "jina_result_observed": "via readonly-proxy" in sd_message,
        "unique_jina_value": "via readonly-proxy" in sd_message,
    })

    oecd_source = working_sources.get("oecd-working-papers")
    oecd_record = None
    if oecd_source:
        for daily_path in sorted((DATA_DIR / "daily").glob("*.json"), reverse=True):
            payload = read_json(daily_path, [])
            if not isinstance(payload, list):
                continue
            for candidate in payload:
                if not isinstance(candidate, dict) or str(candidate.get("source_id") or "") != "oecd-working-papers":
                    continue
                host = urlparse(str(candidate.get("url") or "")).netloc.casefold()
                if host in {"www.oecd.org", "oecd.org", "www.oecd-ilibrary.org", "oecd-ilibrary.org"}:
                    oecd_record = candidate
                    break
            if oecd_record:
                break
    if oecd_source and oecd_record:
        direct_record = copy.deepcopy(oecd_record)
        working_probe.fetch_preprints.enrich_record_from_detail(direct_record, oecd_source, timeout=args.timeout)
        with_residual = copy.deepcopy(direct_record)
        events: list[dict] = []
        original_fetch_text = legacy_wp.fetch_text

        def traced_oecd_fetch(url: str, *, timeout: int, **kwargs):
            try:
                value = original_fetch_text(url, timeout=timeout, **kwargs)
            except Exception as exc:  # noqa: BLE001
                events.append({"transport": transport(url), "outcome": type(exc).__name__})
                raise
            events.append({"transport": transport(url), "outcome": "SUCCESS"})
            return value

        legacy_wp.fetch_text = traced_oecd_fetch
        try:
            legacy_wp.enrich_oecd_from_readonly_transports(with_residual, timeout=args.timeout)
        finally:
            legacy_wp.fetch_text = original_fetch_text
        evidence_fields = ("abstract", "published_online", "available_online", "official_date")
        gained = [
            field for field in evidence_fields
            if with_residual.get(field) and with_residual.get(field) != direct_record.get(field)
        ]
        jina_success = any(event.get("transport") == "jina" and event.get("outcome") == "SUCCESS" for event in events)
        residual_results.append({
            "scope": "oecd_metadata_repair",
            "locator": safe_locator(str(oecd_record.get("url") or "")),
            "events": events,
            "gained_fields": gained,
            "unique_jina_value": bool(jina_success and gained),
        })
    else:
        residual_results.append({
            "scope": "oecd_metadata_repair",
            "outcome": "no-current-official-host-candidate",
            "unique_jina_value": False,
        })

    positive = [
        {"scope": "priority_toc", **row}
        for row in toc_results
        if row.get("unique_jina_value")
    ] + [
        {"scope": "publisher_detail", **row}
        for row in detail_results
        if row.get("unique_jina_value")
    ] + [
        {"scope": "working_paper_detail", **row}
        for row in working_paper_results
        if row.get("unique_jina_value")
    ] + [
        row
        for row in residual_results
        if row.get("unique_jina_value")
    ]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "issue": 339,
        "policy": "RETIRE_BY_DEFAULT_PENDING_DAILY_PROVENANCE_GATE",
        "immediate_retire_configured": immediate_retire,
        "priority_toc": toc_results,
        "publisher_detail_samples": detail_results,
        "working_paper_detail_samples": working_paper_results,
        "residual_daily_paths": residual_results,
        "positive_unique_jina": positive,
        "decision": "RETAIN_EXACT_PROVEN_PATHS_ONLY" if positive else "RETIRE_ALL_DAILY_JINA",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, report)
    print(json.dumps({
        "decision": report["decision"],
        "priority_targets": len(toc_results),
        "detail_samples": len(detail_results),
        "working_paper_samples": len(working_paper_results),
        "residual_paths": len(residual_results),
        "positive_unique_jina": len(positive),
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
