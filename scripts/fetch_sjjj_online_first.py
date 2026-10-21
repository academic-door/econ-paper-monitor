"""Bounded, independent first-party World Economy online-first discovery.

Separate from printed-issue magtech-toc: only publisher-listed online-first
members as_of a successful observation. The publisher prepublication date is
NOT first_seen; the canonical dedupe writer owns actual first discovery.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DATA_DIR, load_journals, today_str, write_json
from fetch_cn_journals import clean_text, clean_title, make_record, split_authors
from status import record_source

JOURNAL_ID = "journal-679eaa2a0c"
OFFICIAL_URL = "https://sjjj.magtech.com.cn/CN/online_first"
ARTICLE_PREFIX = "https://sjjj.magtech.com.cn/CN/abstract/abstract"
MAX_BYTES = 300_000
ITEM_RE = re.compile(r"""<li\b[^>]*\bid\s*=\s*["']art(\d+)["'][\s\S]*?</li>""", re.I)
TITLE_RE = re.compile(r"""class\s*=\s*["']j-title-1["'][^>]*>[\s\S]*?<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)</a>""", re.I)
AUTHOR_RE = re.compile(r"""class\s*=\s*["']j-author["'][^>]*>([\s\S]*?)</div>""", re.I)
ABSTRACT_RE = re.compile(r"""class\s*=\s*["']j-abstract["'][^>]*>([\s\S]*?)</div>""", re.I)
PREPUB_RE = re.compile(r"预出版日期\s*:\s*(20\d{2}-\d{2}-\d{2})")
PAGING_RE = re.compile(r"第\s*(\d+)\s*页\s*共\s*(\d+)\s*页\s*共\s*(\d+)\s*条记录")


class SourceBlocked(ValueError):
    """Authoritative visible roster cannot be fully verified."""


def fetch_official(*, timeout: int = 15) -> tuple[str, str, str]:
    """Use verified TLS; no redirect or unverified TLS fallback."""
    req = urllib.request.Request(OFFICIAL_URL, headers={
        "User-Agent": "AcademicDoor-SJJJ-OnlineFirst/1.0",
        "Accept": "text/html",
    })
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status != 200 or response.geturl() != OFFICIAL_URL:
            raise SourceBlocked("official online-first response status or URL changed")
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise SourceBlocked("official publisher response too large")
    try:
        page = raw.decode("utf-8")
    except UnicodeError as exc:
        raise SourceBlocked("publisher encoding not verified") from exc
    return page, hashlib.sha256(raw).hexdigest(), datetime.now(timezone.utc).isoformat()


def parse_roster(page: str, journal: dict[str, Any], *,
                 page_hash: str, observed_at: str) -> list[dict[str, Any]]:
    text = clean_text(page)
    if "优先出版" not in text or "最新在线预出版文章" not in text:
        raise SourceBlocked("publisher online-first scope statement missing")
    match = PAGING_RE.search(text)
    if not match:
        raise SourceBlocked("publisher paging and member total unavailable")
    current, total_pages, total = map(int, match.groups())
    if current != 1 or total_pages != 1 or total <= 0:
        raise SourceBlocked(f"publisher pagination not fully enumerated: {current}/{total_pages}, total={total}")
    blocks = list(ITEM_RE.finditer(page))
    if len(blocks) != total:
        raise SourceBlocked(f"publisher listed {total} but found {len(blocks)} article blocks")

    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    for item in blocks:
        article_id = item.group(1)
        block = item.group(0)
        title_match = TITLE_RE.search(block)
        authors_match = AUTHOR_RE.search(block)
        abstract_match = ABSTRACT_RE.search(block)
        date_match = PREPUB_RE.search(clean_text(block))
        if not title_match or not date_match:
            raise SourceBlocked(f"publisher member {article_id} missing title or date")
        url = urllib.parse.urljoin(OFFICIAL_URL, title_match.group(1))
        if url != f"{ARTICLE_PREFIX}{article_id}.shtml" or article_id in ids:
            raise SourceBlocked(f"publisher URL/ID mismatch: {article_id}")
        ids.add(article_id)
        prepublished = date_match.group(1)
        try:
            datetime.strptime(prepublished, "%Y-%m-%d")
        except ValueError as exc:
            raise SourceBlocked(f"invalid prepublication date for {article_id}") from exc
        record = make_record(
            journal, clean_title(title_match.group(2)), url,
            authors=split_authors(authors_match.group(1) if authors_match else None),
            abstract=clean_text(abstract_match.group(1)) if abstract_match else None,
            available_online=prepublished, published_online=prepublished,
            date_source="official_publish_date", source_url=OFFICIAL_URL,
        )
        if record is None:
            raise SourceBlocked(f"invalid publisher article {article_id}")
        record["raw_data"] = {
            "source_scope": "PUBLISHER_VISIBLE_ONLINE_FIRST_ONLY",
            "publisher_article_id": article_id,
            "observed_at": observed_at,
            "official_page_sha256": page_hash,
            "observed_total": total,
            "observed_page_count": total_pages,
        }
        rows.append(record)
    if len(rows) != total:
        raise SourceBlocked("publisher visible roster not completely parsed")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent SJJJ official visible online-first collector")
    parser.add_argument("--output", type=Path, default=DATA_DIR / "raw" / "cn" / f"{today_str()}-sjjj-online-first.json")
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()
    journal = next((j for j in load_journals() if j.get("id") == JOURNAL_ID), None)
    if not journal:
        record_source("sjjj-online-first", ok=False, count=0, message="owner journal config missing")
        return 2
    try:
        page, page_hash, observed_at = fetch_official(timeout=args.timeout)
        records = parse_roster(page, journal, page_hash=page_hash, observed_at=observed_at)
    except (SourceBlocked, OSError, TimeoutError, UnicodeError, ValueError) as exc:
        record_source("sjjj-online-first", ok=False, count=0,
                      message=f"VERIFICATION_BLOCKED: {type(exc).__name__}: {exc}")
        print(f"sjjj official online-first VERIFICATION_BLOCKED: {type(exc).__name__}: {exc}")
        return 2
    write_json(args.output, records)
    record_source("sjjj-online-first", ok=True, count=len(records),
                  message=f"PUBLISHER_VISIBLE_ONLINE_FIRST_ONLY as_of={observed_at} "
                          f"count={len(records)} sha256={page_hash}; no universal completeness claim")
    print(f"sjjj online-first observed={len(records)} as_of={observed_at}; raw={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
