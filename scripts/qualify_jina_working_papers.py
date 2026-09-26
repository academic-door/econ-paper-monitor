#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import fetch_preprints
from common import DATA_DIR, write_json

FIELDS = ("authors", "abstract", "doi", "published_online", "available_online", "official_date")


def probe(source: dict, record: dict, timeout: int) -> dict:
    direct = copy.deepcopy(record)
    original_proxy = fetch_preprints.enrich_record_from_proxy
    try:
        fetch_preprints.enrich_record_from_proxy = lambda current, _source_id, *, timeout: current
        fetch_preprints.enrich_record_from_detail(direct, source, timeout=timeout)
    finally:
        fetch_preprints.enrich_record_from_proxy = original_proxy

    with_proxy = copy.deepcopy(record)
    fetch_preprints.enrich_record_from_detail(with_proxy, source, timeout=timeout)

    gained = [
        field for field in FIELDS
        if with_proxy.get(field) and with_proxy.get(field) != direct.get(field)
    ]
    return {
        "source_id": source.get("id"),
        "url": record.get("url"),
        "direct": {field: bool(direct.get(field)) for field in FIELDS},
        "with_current_proxy": {field: bool(with_proxy.get(field)) for field in FIELDS},
        "gained_fields": gained,
        "unique_jina_value": bool(gained),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=12)
    args = parser.parse_args()

    sources = {
        str(source.get("id") or ""): source
        for source in fetch_preprints.load_sources(DATA_DIR / "working_paper_sources.yml")
    }
    results = []
    for source_id in ("cepr-dp", "fed-feds"):
        source = sources.get(source_id)
        if not source:
            continue
        try:
            records, mode = fetch_preprints.fetch_source(source, timeout=args.timeout, limit=2)
        except Exception as exc:
            results.append({"source_id": source_id, "listing_error": type(exc).__name__, "unique_jina_value": False})
            continue
        for record in records[:2]:
            if not record.get("url"):
                continue
            row = probe(source, record, args.timeout)
            row["listing_mode"] = mode
            results.append(row)

    report = {
        "results": results,
        "positive_unique_jina": [row for row in results if row.get("unique_jina_value")],
    }
    write_json(args.output, report)
    print(json.dumps({"samples": len(results), "positive": len(report["positive_unique_jina"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
