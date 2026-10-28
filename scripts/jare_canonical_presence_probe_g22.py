"""Read-only, ephemeral #404 exact JARE syndication presence probe; NEVER merge into main."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEEDLES = (
    "dynamics of us farm income distribution under weather shocks",
    "10.22004/ag.econ.376286",
    "repec:ags:jlaare:376286",
    "ageconsearch.umn.edu/record/376286",
)
FIELDS = ("title", "doi", "id", "url", "source_url", "detail_key", "identity_aliases", "paper_number")

def matches(row):
    if not isinstance(row, dict):
        return False
    for key in FIELDS:
        value = row.get(key)
        if isinstance(value, list):
            value = " ".join(str(v) for v in value)
        val = str(value or "").casefold()
        if any(needle in val for needle in NEEDLES):
            return True
    return False

def read(path):
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)

def main():
    files = sorted((ROOT / "data" / "daily").glob("*.json"))
    rows = 0
    failures = []
    found = []
    for path in files:
        try:
            payload = read(path)
            if not isinstance(payload, list):
                failures.append(path.name)
                continue
            for row in payload:
                if isinstance(row, dict):
                    rows += 1
                    if matches(row):
                        found.append((path.name, row))
        except (OSError, ValueError):
            failures.append(path.name)
    print(f"JARE-G22 canonical_daily_files={len(files)} canonical_daily_rows={rows} parse_failures={len(failures)} daily_matches={len(found)}")
    for name, row in found[:10]:
        print(f"JARE-G22 daily_match shard={name} id={row.get('id')} doi={row.get('doi')} first_seen={row.get('first_seen')} title={row.get('title')}")
    seen = read(ROOT / "data" / "seen.json")
    papers = seen.get("papers") if isinstance(seen, dict) else None
    assert isinstance(papers, dict), "Expected canonical seen.json {papers: {...}} schema"
    items = list(papers.values())
    found_seen = [row for row in items if matches(row)]
    print(f"JARE-G22 canonical_seen_items={len(items)} canonical_seen_matches={len(found_seen)}")
    for row in found_seen[:10]:
        print(f"JARE-G22 seen_match id={row.get('id')} doi={row.get('doi')} first_seen={row.get('first_seen')} title={row.get('title')}")
    if failures:
        print("JARE-G22 parse_failed_shards=" + ",".join(failures[:20]))
    assert len(files) > 500 and rows > 1000 and len(items) > 10000, "Incomplete canonical scan"
    assert not failures, "Canonical Daily contains unparseable shards"

if __name__ == "__main__":
    main()
