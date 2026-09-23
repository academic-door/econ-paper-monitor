"""Build the public RSS feed from daily paper archives."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from common import DATA_DIR, DOCS_DIR, html_escape, read_json, write_text


def load_records(daily_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not daily_dir.exists():
        return records
    for path in sorted(daily_dir.glob("*.json"), reverse=True):
        records.extend(read_json(path, []))
    return sorted(
        records,
        key=lambda item: (item.get("published_online") or "", item.get("detected_at") or ""),
        reverse=True,
    )


def item_xml(record: dict[str, Any]) -> str:
    link = record.get("url") or (f"https://doi.org/{record['doi']}" if record.get("doi") else "")
    title = record.get("title") or "Untitled paper"
    description = " · ".join(str(value) for value in [record.get("journal"), record.get("published_online")] if value)
    guid = record.get("id") or link or title
    return f"""    <item>
      <title>{html_escape(title)}</title>
      <link>{html_escape(link)}</link>
      <guid isPermaLink="false">{html_escape(guid)}</guid>
      <description>{html_escape(description)}</description>
    </item>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--output", type=Path, default=DOCS_DIR / "feed.xml")
    parser.add_argument("--site-url", default="https://example.com/econ-paper-monitor/")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    records = load_records(args.daily_dir)[: args.limit]
    items = "\n".join(item_xml(record) for record in records)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Econ Papers Daily / 每日之门</title>
    <link>{html_escape(args.site_url)}</link>
    <description>Econ Papers Daily / 每日之门：追踪重点经济学期刊和工作论文来源的最新论文。</description>
    <lastBuildDate>{format_datetime(datetime.now(UTC))}</lastBuildDate>
{items}
  </channel>
</rss>
"""
    write_text(args.output, xml)
    print(f"wrote {len(records)} feed items to {args.output}")


if __name__ == "__main__":
    main()
