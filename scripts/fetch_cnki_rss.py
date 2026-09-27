"""Fetch CNKI RSS feeds for Chinese economics journals.

CNKI RSS is useful for discovery but is not uniformly first-published evidence.
Records therefore keep a distinct date_source so the public site can label the
date as "CNKI RSS date" instead of "online date".
"""

from __future__ import annotations

import argparse
import html
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from datetime import datetime, timedelta

from common import BEIJING_TZ, DATA_DIR, load_journals, parse_scalar, today_str, write_json
from sources.record import article_record
from status import load_status, now, record_source, save_status

try:
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


NOISE_TITLE_PATTERNS = (
    "欢迎订阅",
    "征稿",
    "启事",
    "公告",
    "声明",
    "稿约",
    "目录",
    "书评",
    "评《",
    "读《",
    "有感",
    "编者按",
)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def child_text_any(node: ElementTree.Element, names: list[str]) -> str | None:
    wanted = {name.casefold() for name in names}
    for child in list(node):
        if local_name(child.tag) in wanted and child.text:
            return child.text.strip()
    return None


def child_link_any(node: ElementTree.Element) -> str | None:
    for child in list(node):
        if local_name(child.tag) != "link":
            continue
        if child.attrib.get("href"):
            return child.attrib["href"].strip()
        if child.text:
            return child.text.strip()
    guid = child_text_any(node, ["guid", "identifier"])
    return guid.strip() if guid else None


def parse_cnki_pubdate(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING_TZ)
        return parsed.astimezone(BEIJING_TZ).date().isoformat()
    except Exception:
        match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", value)
        if not match:
            return None
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def split_authors(value: str | None) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[;；、,，\s]+", text) if item.strip()]


def is_noise(title: str, authors: list[str]) -> bool:
    if len(title) < 4:
        return True
    if any(pattern in title for pattern in NOISE_TITLE_PATTERNS):
        return True
    if not authors and ("《" in title or "期刊" in title or "订阅" in title):
        return True
    return False


def load_cnki_sources(path: Path) -> list[dict[str, Any]]:
    feeds: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "feeds:":
            continue
        if line.startswith("  - "):
            if current:
                feeds.append(current)
            key, _, value = stripped.removeprefix("- ").partition(":")
            current = {key.strip(): parse_scalar(value.strip())}
            continue
        if current is not None and line.startswith("    "):
            key, _, value = stripped.partition(":")
            current[key.strip()] = parse_scalar(value.strip())
    if current:
        feeds.append(current)
    return feeds


def cnki_proxy_url(base: str, source_url: str) -> str:
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}url={urllib.parse.quote(source_url, safe='')}"


def fetch_cnki_text(source: dict[str, Any]) -> tuple[str, str]:
    """Fetch CNKI RSS through direct and optional controlled relay paths."""
    urls = [str(source.get("url") or "")]
    fallback_url = str(source.get("fallback_url") or "")
    if fallback_url and fallback_url not in urls:
        urls.append(fallback_url)
    code = str(source.get("code") or "")
    profiles = [
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Referer": f"https://kns.cnki.net/knavi/journals/{code}/detail",
            "Cache-Control": "no-cache",
        },
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15",
            "Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Referer": "https://www.cnki.net/",
        },
    ]
    proxy_base = os.environ.get("CNKI_RSS_PROXY_BASE", "").strip()
    last_error: Exception | None = None
    attempts: list[str] = []
    for url in urls:
        if not url:
            continue
        candidates = [(url, "direct")]
        if proxy_base:
            candidates.append((cnki_proxy_url(proxy_base, url), "relay"))
        for candidate_url, transport in candidates:
            for attempt in range(3 if transport == "direct" else 2):
                request = urllib.request.Request(candidate_url, headers=profiles[attempt % len(profiles)])
                for context in (None, ssl._create_unverified_context()):
                    try:
                        kwargs: dict[str, Any] = {"timeout": 30}
                        if context is not None:
                            kwargs["context"] = context
                        with urllib.request.urlopen(request, **kwargs) as response:  # type: ignore[arg-type]
                            status_code = int(getattr(response, "status", 200) or 200)
                            if status_code >= 400:
                                raise urllib.error.HTTPError(candidate_url, status_code, "HTTP error", response.headers, None)
                            payload = response.read()
                            charset = response.headers.get_content_charset() or "utf-8"
                            for candidate in dict.fromkeys([charset, "utf-8", "gb18030", "gbk"]):
                                try:
                                    return payload.decode(candidate), f"{transport}:{url}"
                                except Exception:
                                    continue
                            return payload.decode("utf-8", errors="replace"), f"{transport}:{url}"
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        attempts.append(f"{transport}:{type(exc).__name__}:{exc}")
                if attempt < (2 if transport == "direct" else 1):
                    time.sleep(1.5 * (attempt + 1))
    if last_error:
        raise RuntimeError(f"CNKI RSS unavailable after direct/relay retries: {'; '.join(attempts[-8:])}") from last_error
    raise RuntimeError("empty CNKI RSS URL list")


def parse_feed(
    xml_text: str,
    journal: dict[str, Any],
    source: dict[str, Any],
    *,
    max_age_days: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = ElementTree.fromstring(xml_text)
    channel = root.find("channel")
    items = root.findall("./channel/item") if channel is not None else []
    channel_pubdate = child_text_any(channel, ["pubDate"]) if channel is not None else None
    records: list[dict[str, Any]] = []
    filtered = 0
    stale = 0
    cutoff = (datetime.now(BEIJING_TZ).date() - timedelta(days=max_age_days)).isoformat()
    latest_item_date: str | None = None
    latest_research_date: str | None = None
    latest_title: str | None = None

    for item in items:
        title = clean_text(child_text_any(item, ["title"]))
        authors = split_authors(child_text_any(item, ["author", "dc:creator", "creator"]))
        link = child_link_any(item)
        pubdate_raw = child_text_any(item, ["pubDate", "date", "dc:date"])
        rss_date = parse_cnki_pubdate(pubdate_raw)
        description = clean_text(child_text_any(item, ["description", "summary"]))
        if rss_date and (latest_item_date is None or rss_date > latest_item_date):
            latest_item_date = rss_date
        if not title or is_noise(title, authors):
            filtered += 1
            continue
        if rss_date and rss_date < cutoff:
            stale += 1
            continue
        if rss_date and (latest_research_date is None or rss_date > latest_research_date):
            latest_research_date = rss_date
            latest_title = title
        record = article_record(
            journal,
            title=title,
            url=link,
            source="cnki-rss",
            source_url=str(source.get("url") or ""),
            authors=authors,
            abstract=description or None,
            published_online=rss_date,
            available_online=rss_date,
            date_source="cnki_rss_pubdate" if rss_date else "cnki_rss_undated",
            date_confidence="A" if rss_date else "F",
            raw_data={
                "cnki_code": source.get("code"),
                "cnki_feed_url": source.get("url"),
                "cnki_pubdate_raw": pubdate_raw,
                "cnki_channel_pubdate_raw": channel_pubdate,
                "cnki_priority": source.get("priority"),
            },
        )
        record["title_zh"] = title
        record["abstract_zh"] = description or None
        record["translation_status"] = "native_chinese"
        records.append(record)
    summary = {
        "journal_id": source.get("journal_id"),
        "journal": journal.get("title") or source.get("name"),
        "ok": True,
        "count": len(records),
        "filtered": filtered,
        "mode": "cnki-rss",
        "latest_item_date": latest_item_date,
        "latest_research_date": latest_research_date,
        "latest_title": latest_title,
        "channel_updated_at": parse_cnki_pubdate(channel_pubdate),
        "message": f"accepted={len(records)} filtered={filtered} stale={stale} latest_research={latest_research_date or 'none'}",
    }
    return records, summary


def source_health_counts(summaries: list[dict[str, Any]]) -> dict[str, int | bool]:
    """Summarize source availability for callers that enforce publish gates."""
    successful = sum(1 for item in summaries if item.get("ok"))
    failed = len(summaries) - successful
    return {
        "ok": bool(successful),
        "selected_sources": len(summaries),
        "successful_sources": successful,
        "failed_sources": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, default=DATA_DIR / "cnki_rss_sources.yml")
    parser.add_argument("--journals", type=Path, default=DATA_DIR / "journals.yml")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--max-items-per-feed", type=int, default=80)
    parser.add_argument("--max-age-days", type=int, default=45)
    parser.add_argument(
        "--require-all-sources",
        action="store_true",
        help="Exit nonzero when any selected source fails; used by the authoritative local runner.",
    )
    args = parser.parse_args()

    output = args.output or DATA_DIR / "raw" / "cnki-rss" / f"{today_str()}.json"
    journals = {journal["id"]: journal for journal in load_journals(args.journals)}
    selected = load_cnki_sources(args.sources)
    if args.only:
        only = set(args.only)
        selected = [source for source in selected if source.get("journal_id") in only or source.get("code") in only]

    records: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for source in selected:
        journal_id = str(source.get("journal_id") or "")
        journal = journals.get(journal_id)
        if not journal:
            summaries.append(
                {
                    "journal_id": journal_id,
                    "journal": source.get("name") or journal_id,
                    "ok": False,
                    "count": 0,
                    "filtered": 0,
                    "mode": "missing-config",
                    "message": "missing journal config",
                }
            )
            continue
        try:
            xml_text, fetched_url = fetch_cnki_text(source)
            source = {**source, "url": fetched_url}
            fetched, summary = parse_feed(xml_text, journal, source, max_age_days=args.max_age_days)
            if args.max_items_per_feed:
                fetched = fetched[: args.max_items_per_feed]
                summary["count"] = len(fetched)
            records.extend(fetched)
            summaries.append(summary)
        except Exception as exc:  # noqa: BLE001
            summaries.append(
                {
                    "journal_id": journal_id,
                    "journal": journal.get("title") or source.get("name") or journal_id,
                    "ok": False,
                    "count": 0,
                    "filtered": 0,
                    "mode": "cnki-rss-error",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )

    write_json(output, records)
    write_json(output.with_suffix(".status.json"), summaries)
    health = source_health_counts(summaries)
    successful_sources = [item for item in summaries if item.get("ok")]
    ok = bool(health["ok"])
    total = len(records)
    message = "; ".join(f"{item.get('journal')}: {item.get('message')}" for item in summaries)
    record_source("cnki-rss", ok=ok, count=total, message=message)
    status = load_status()
    status.setdefault("source_groups", {})["cnki-rss"] = {
        "ok": ok,
        "count": total,
        "selected_sources": health["selected_sources"],
        "successful_sources": health["successful_sources"],
        "failed_sources": health["failed_sources"],
        "updated_at": now(),
        "journals": summaries,
    }
    save_status(status)
    print(f"wrote {total} CNKI RSS records to {output}")
    for item in summaries:
        print(f"{item.get('journal')}: {item.get('message')}")
    if summaries and (not successful_sources or (args.require_all_sources and health["failed_sources"])):
        if not successful_sources:
            reason = "every configured source failed"
        else:
            reason = f"{health['failed_sources']} configured source(s) failed"
        print(f"CNKI RSS {reason}; refusing to publish a local supplement.")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
