from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_rss
from common import load_journals
from sources import registry


def main() -> None:
    journal = next(
        item
        for item in load_journals(ROOT / "data" / "journals.yml")
        if item["id"] == "review-of-international-economics"
    )
    feeds = registry.generated_official_rss_urls(journal)
    feed = next(
        (item for item in feeds if "jc=14679396" in item["url"]),
        feeds[0] if feeds else None,
    )
    if not feed:
        raise SystemExit("RIE probe: no Wiley official-generated feed")

    print(f"RIE probe feed={feed['url']}")
    records, error = fetch_rss.fetch_journal_feed(journal, feed)
    if error:
        raise SystemExit(f"RIE probe fetch failed: {error}")
    if not records:
        raise SystemExit("RIE probe parsed zero records")

    print(f"RIE probe records={len(records)}")
    for record in records[:5]:
        print(
            "RIE probe record "
            f"title={record.get('title')!r} "
            f"doi={record.get('doi')!r} "
            f"published_online={record.get('published_online')!r} "
            f"source={record.get('source')!r}"
        )
        if record.get("journal") != "Review of International Economics":
            raise SystemExit(f"RIE probe journal mismatch: {record.get('journal')!r}")


if __name__ == "__main__":
    main()
