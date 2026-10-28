"""Ephemeral read-only #404 syndicated JARE record identity audit (never merge)."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEEDLE = "dynamics of us farm income distribution under weather shocks"
RECORD = "376286"
DOI = "10.22004/ag.econ.376286"

def matched(obj):
    if not isinstance(obj, dict):
        return False
    vals = [obj.get("title"), obj.get("doi"), obj.get("url"), obj.get("source_url")]
    return any(NEEDLE in str(x or "").lower() or RECORD in str(x or "") or DOI in str(x or "").lower() for x in vals)

def main():
    canonical = ROOT / "data/daily"
    files = sorted(canonical.glob("*.json"))
    found = []
    rows = 0
    failed = 0
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, list):
                failed += 1
                continue
            for item in payload:
                if isinstance(item, dict):
                    rows += 1
                    if matched(item):
                        found.append((path.stem, item))
        except (OSError, ValueError):
            failed += 1
    print(f"JARE-376286 daily_files={len(files)} daily_rows={rows} parse_failures={failed} candidate_matches={len(found)}")
    for date, item in found[:8]:
        print(f"JARE-376286 daily_match date={date} first_seen={item.get('first_seen') or 'none'} journal_id={item.get('journal_id') or 'none'} source={item.get('source') or 'none'}")
    seen_path = ROOT / "data/seen.json"
    seen = json.loads(seen_path.read_text(encoding="utf-8-sig"))
    seen_items = list(seen.values()) if isinstance(seen, dict) else seen if isinstance(seen, list) else []
    seen_found = [item for item in seen_items if matched(item)]
    print(f"JARE-376286 seen_items={len(seen_items)} seen_matches={len(seen_found)}")
    for item in seen_found[:8]:
        print(f"JARE-376286 seen_match first_seen={item.get('first_seen') or 'none'} journal_id={item.get('journal_id') or 'none'} source={item.get('source') or 'none'}")
    # This is a diagnostic, not a product acceptance test. No data mutation.
    assert failed == 0, f"Unparseable canonical daily files: {failed}"


if __name__ == "__main__":
    main()
