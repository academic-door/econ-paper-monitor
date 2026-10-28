"""G22 N2 diagnostic only; never merge. Read-only hosted test of production parser.

Runs exact six currently missing canonical Springer's records; each attempt uses
the existing real verified_springer_tax_abstract function, at most one 8s HTTPS
GET, 1 MiB. No publisher abstract contents, cookies or credentials in logs.
"""
from __future__ import annotations
import json
from pathlib import Path
from enrich_metadata import verified_springer_tax_abstract

ROOT = Path(__file__).resolve().parents[1]
DOIS = (
    "10.1007/s10797-026-10011-7",
    "10.1007/s10797-026-10004-6",
    "10.1007/s10797-026-10009-1",
    "10.1007/s10797-026-10008-2",
    "10.1007/s10797-026-09999-9",
    "10.1007/s10797-026-10010-8",
)

def main() -> None:
    records = []
    for day in ("2026-10-27", "2026-10-24", "2026-10-22"):
        records.extend(json.loads((ROOT / "data" / "daily" / f"{day}.json").read_text(encoding="utf-8")))
    for doi in DOIS:
        matches = [record for record in records if str(record.get("doi") or "").lower() == doi]
        if len(matches) != 1:
            print("G22_N2_PRODUCTION_PARSER "+json.dumps({"doi":doi,"record_count":len(matches),"accepted":False}),flush=True)
            continue
        record = matches[0]
        if record.get("abstract"):
            print("G22_N2_PRODUCTION_PARSER "+json.dumps({"doi":doi,"already_present":True,"accepted":True}),flush=True)
            continue
        result=verified_springer_tax_abstract(record, timeout=8)
        print("G22_N2_PRODUCTION_PARSER "+json.dumps({
            "doi":doi,
            "accepted":bool(result.get("abstract")),
            "abstract_len":len(result.get("abstract") or ""),
            "abstract_source":result.get("abstract_source"),
            "record_first_seen_unchanged":bool(record.get("first_seen")),
            "read_only":True,
        },sort_keys=True),flush=True)
    print("G22_N2_PRODUCTION_PARSER COMPLETE writes=0",flush=True)

if __name__ == "__main__":
    main()
