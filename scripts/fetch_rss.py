"""Fetch publisher RSS/Atom feeds configured in journals.yml."""

from __future__ import annotations

import argparse
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
import re

from common import DATA_DIR, fetch_text, filter_journals_by_tier, load_journals, today_str, write_json
from sources.record import article_record
from sources.registry import load_registry, save_registry, feeds_for_journal
from status import record_source


ATOM = "{http://www.w3.org/2005/Atom}"
JFQA_FORTHCOMING_FEED = "https://jfqa.org/category/forthcoming-articles/feed/"



def fetch_feed_with_retry(url: str, timeout: int = 30, attempts: int = 2) -> str:
    """Fetch an RSS/Atom feed with one bounded retry for transient failures."""
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return fetch_text(url, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - keep the scheduled job moving.
            last_error = exc
            if attempt + 1 < max(1, attempts):
                time.sleep(2.0)
    if last_error is not None:
        raise last_error
    raise RuntimeError("RSS feed returned no response")


def fetch_journal_feed(journal: dict[str, Any], source: dict[str, str]) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch one feed, retrying once after a pause when a publisher blocks.

    Publisher WAFs (UChicago/Atypon, Springer) intermittently return 403 or a
    challenge page to shared CI IPs; a single bounded retry clears most
    transient blocks without hammering the endpoint.
    """
    last_error = ""
    for attempt in range(2):
        try:
            xml_text = fetch_feed_with_retry(source["url"])
            if not feed_identity_matches(xml_text, journal, source["url"]):
                raise ValueError("RSS feed identity does not match configured journal")
            return parse_feed(xml_text, journal, source["url"]), None
        except Exception as exc:  # noqa: BLE001 - retry once, then surface.
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == 0:
                time.sleep(3.0)
    return [], last_error

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def child_text(node: ElementTree.Element, names: list[str]) -> str | None:
    for name in names:
        child = node.find(name)
        if child is not None and child.text:
            return child.text.strip()
    return None


def child_text_any(node: ElementTree.Element, names: list[str]) -> str | None:
    wanted = {name.casefold() for name in names}
    for child in list(node):
        if local_name(child.tag) not in wanted:
            continue
        if child.text and child.text.strip():
            return child.text.strip()
        # Some feeds (e.g. Springer search.rss) nest the payload in element
        # children such as <description><p>...</p></description>.
        nested = " ".join(part for part in child.itertext() if part and part.strip())
        if nested.strip():
            return nested.strip()
    return None


def child_texts_any(node: ElementTree.Element, names: list[str]) -> list[str]:
    wanted = {name.casefold() for name in names}
    values: list[str] = []
    for child in list(node):
        if local_name(child.tag) in wanted and child.text:
            cleaned = clean_text(child.text)
            if cleaned:
                values.append(cleaned)
    return values


def child_link_any(node: ElementTree.Element) -> str | None:
    for child in list(node):
        if local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        if child.text:
            return child.text.strip()
    guid = child_text_any(node, ["guid", "identifier"])
    return guid


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        match = re.search(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}", value)
        if match:
            parts = re.split(r"[-/.]", match.group(0))
            return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        match = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})", value)
        if match:
            month = MONTHS.get(match.group(2).casefold())
            if month:
                return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(1)):02d}"
        match = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(20\d{2})", value)
        if match:
            month = MONTHS.get(match.group(1).casefold())
            if month:
                return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(2)):02d}"
        # Do not truncate labels such as ``September 2026`` into a value that
        # looks date-like but is not ISO-8601. Month-only issue dates are
        # handled explicitly by parse_month_date at the call site.
        return None


def parse_month_date(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    match = re.search(r"([A-Za-z]{3,9})\s+(20\d{2})", text)
    if match:
        month = MONTHS.get(match.group(1).casefold())
        if month:
            return f"{int(match.group(2)):04d}-{month:02d}-01"
    match = re.search(r"(20\d{2})\s*[-/年]\s*(\d{1,2})", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-01"
    return None


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def metadata_from_description(description: str | None) -> dict[str, Any]:
    text = clean_text(description)
    result: dict[str, Any] = {}
    if not text:
        return result
    available_match = re.search(r"(?:Available online|Online available|Article available online)\s*:?\s*(.+?20\d{2})", text, flags=re.I)
    parsed_date = parse_date(available_match.group(1)) if available_match else None
    if parsed_date:
        result["published_online"] = parsed_date
        result["available_online"] = parsed_date
        result["date_source"] = "rss_description_online"
        result["date_confidence"] = "A"
    publication_match = re.search(r"Publication date:\s*(.+?20\d{2})", text, flags=re.I)
    publication_value = publication_match.group(1) if publication_match else None
    issue_date = parse_date(publication_value) or parse_month_date(publication_value)
    if issue_date:
        result["issue_date"] = issue_date
        if issue_date.endswith("-01") and publication_value and not re.search(r"\d{1,2}\s+[A-Za-z]{3,9}|[A-Za-z]{3,9}\s+\d{1,2}", publication_value):
            result["date_precision"] = "month"
    source_match = re.search(r"Source:\s*(.*?)(?:Author\(s\):|$)", text, flags=re.I)
    if source_match:
        result["source_issue"] = clean_text(source_match.group(1))
    authors_match = re.search(r"Author\(s\):\s*(.*)$", text, flags=re.I)
    if authors_match:
        authors = [clean_text(part) for part in re.split(r"\s*,\s*|\s+and\s+", authors_match.group(1)) if clean_text(part)]
        if authors:
            result["authors"] = authors[:12]
    return result


def normalize_authors(values: list[str]) -> list[str]:
    authors: list[str] = []
    for value in values:
        # Some RDF feeds concatenate personal names with affiliations without
        # separators. Keeping that blob as one author is worse than leaving it
        # empty, because DOI enrichment can then replace it with authoritative
        # Crossref names.
        if re.search(
            r"(?:University|College|School|Department|Institute|Centre|Center|"
            r"National Bureau|National Economic|Research and Policy|University of)",
            value,
            flags=re.I,
        ):
            continue
        for part in re.split(r"\s*;\s*|\s+ and\s+|\s*,\s*(?=[A-Z][A-Za-z'.-]+(?:\s|$))", value):
            cleaned = clean_text(part)
            if cleaned and cleaned not in authors:
                authors.append(cleaned)
    return authors[:12]


def extract_pii(*values: str | None) -> str | None:
    for value in values:
        text = str(value or "")
        match = re.search(r"/pii/(S[0-9A-Z]+)", text, flags=re.I)
        if match:
            return match.group(1).upper()
        match = re.search(r"\b(S\d{15,}[A-Z0-9]*)\b", text, flags=re.I)
        if match:
            return match.group(1).upper()
    return None


def extract_doi(*values: str | None) -> str | None:
    for value in values:
        text = clean_text(value)
        match = re.search(r"(?:doi\s*:\s*|doi\.org/)(10\.\d{4,9}/[^\s<>&\"']+)", text, flags=re.I)
        if match:
            return match.group(1).rstrip(".,;)").casefold()
    return None


def parse_feed(xml_text: str, journal: dict[str, Any], feed_url: str) -> list[dict[str, Any]]:
    if "<html" in xml_text[:500].casefold():
        raise ValueError("feed URL returned HTML, not RSS/Atom")
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        # A few publisher feeds contain bare ampersands or control characters
        # in titles/descriptions. Repair only those XML lexical defects; a
        # malformed document with broken tags must still fail closed.
        repaired = re.sub(r"&(?!#(?:\d+|x[0-9a-fA-F]+);|amp;|lt;|gt;|quot;|apos;)", "&amp;", xml_text)
        repaired = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", repaired)
        root = ElementTree.fromstring(repaired)
    records: list[dict[str, Any]] = []

    if root.tag.endswith("rss") or root.find("channel") is not None:
        items = root.findall("./channel/item")
        for item in items:
            title = child_text_any(item, ["title"])
            link = child_link_any(item)
            guid = child_text_any(item, ["guid", "identifier"])
            published = parse_date(child_text_any(item, ["pubDate", "date", "dc:date", "updated", "published"]))
            description = child_text_any(item, ["description", "summary", "content"])
            authors = child_texts_any(item, ["creator", "author", "dc:creator"])
            if journal.get("id") == "journal-of-financial-and-quantitative-analysis" and feed_url == JFQA_FORTHCOMING_FEED:
                # WordPress dc:creator is "JFQA Admin", not the article author.
                # The publisher gives actual paper authors before the diamond
                # separating its byline from the article abstract.
                byline = (description or "").split("♦", 1)[0].strip() if "♦" in (description or "") else ""
                authors = [part.strip() for part in re.split(r"\s+and\s+|\s*,\s*", byline) if part.strip()]
            identifier = child_text_any(item, ["identifier", "doi"])
            record = make_record(title, link, published, journal, feed_url, description=description, guid=guid, identifier=identifier, authors=authors)
            if journal.get("id") == "journal-of-financial-and-quantitative-analysis" and feed_url == JFQA_FORTHCOMING_FEED:
                # WordPress post date evidences first-party availability of a
                # forthcoming paper, NOT its later Cambridge formal online date.
                record["published_online"] = None
                record["raw_data"]["publication_stage"] = "jfqa_forthcoming"
            records.append(record)
        return [record for record in records if record["title"]]

    entries = root.findall(f".//{ATOM}entry")
    for entry in entries:
        title = child_text(entry, [f"{ATOM}title"])
        link = None
        link_node = entry.find(f"{ATOM}link")
        if link_node is not None:
            link = link_node.attrib.get("href")
        published = parse_date(child_text(entry, [f"{ATOM}published", f"{ATOM}updated"]))
        description = child_text(entry, [f"{ATOM}summary", f"{ATOM}content"])
        authors = [
            clean_text(name.text)
            for author in entry.findall(f"{ATOM}author")
            for name in [author.find(f"{ATOM}name")]
            if name is not None and name.text
        ]
        records.append(make_record(title, link, published, journal, feed_url, description=description, authors=authors))
    if entries:
        return [record for record in records if record["title"]]

    # Taylor & Francis and some CN platforms expose RSS 1.0/RDF feeds where
    # namespaced <item> nodes are not under /channel.
    for item in root.iter():
        if local_name(item.tag) != "item":
            continue
        title = child_text_any(item, ["title"])
        link = child_link_any(item)
        published = parse_date(child_text_any(item, ["date", "pubDate", "updated", "published"]))
        description = child_text_any(item, ["description", "summary", "content"])
        authors = child_texts_any(item, ["creator", "author", "dc:creator"])
        identifier = child_text_any(item, ["identifier", "doi"])
        records.append(make_record(title, link, published, journal, feed_url, description=description, identifier=identifier, authors=authors))
    return [record for record in records if record["title"]]


def feed_identity_matches(xml_text: str, journal: dict[str, Any], feed_url: str | None = None) -> bool:
    """Reject a valid RSS feed that belongs to a different journal.

    Some publisher platforms reuse short journal codes.  A successful HTTP/XML
    response is therefore not enough: when the feed exposes a publication name
    or source, it must agree with the configured journal.  Generic Springer
    feeds only say "Latest Results" and are intentionally left to their
    configured facet URL.
    """
    root = ElementTree.fromstring(xml_text)
    def compact_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.replace("&", " and ").casefold())

    expected = compact_name(str(journal.get("title") or ""))
    if not expected:
        return True
    candidates: list[str] = []
    channel = next((node for node in root if local_name(node.tag) == "channel"), None)
    if channel is not None:
        channel_title = next((node for node in channel if local_name(node.tag) == "title"), None)
        if channel_title is not None and channel_title.text:
            compact = compact_name(clean_text(channel_title.text))
            if compact and compact not in {"latestresults", "thelatestcontentavailablefromspringer"}:
                candidates.append(compact)
    for node in root.iter():
        name = local_name(node.tag)
        if name not in {"publicationname", "source", "channel"}:
            continue
        value = clean_text(node.text)
        if value and value.casefold() not in {"latest results", "the latest content available from springer"}:
            compact = compact_name(value)
            if compact:
                candidates.append(compact)
    # The JFQA first-party WordPress Forthcoming channel is not titled with
    # the full journal name. Only this exact official feed may use its short
    # channel identity; other journal feeds retain the strict generic check.
    if journal.get("id") == "journal-of-financial-and-quantitative-analysis" and feed_url == JFQA_FORTHCOMING_FEED:
        return "forthcomingarticlesjfqa" in candidates
    if not candidates:
        return True
    return any(expected in candidate or candidate in expected for candidate in candidates)


def make_record(
    title: str | None,
    link: str | None,
    published: str | None,
    journal: dict[str, Any],
    feed_url: str,
    description: str | None = None,
    guid: str | None = None,
    identifier: str | None = None,
    authors: list[str] | None = None,
) -> dict[str, Any]:
    description_metadata = metadata_from_description(description)
    parsed_authors = normalize_authors(authors or []) or description_metadata.get("authors")
    pii = extract_pii(link, guid, description)
    guid_doi = extract_doi(guid)
    if guid_doi is None and guid and re.fullmatch(r"10\.\d{4,9}/[^\s]+", guid.strip()):
        guid_doi = guid.strip().rstrip(".,;)").casefold()
    doi = extract_doi(identifier, link, description) or guid_doi
    record = {
        **article_record(
            journal,
            title=clean_text(title),
            url=link,
            source="rss",
            source_url=feed_url,
            doi=doi,
            authors=parsed_authors,
            published_online=published or description_metadata.get("published_online"),
            available_online=published or description_metadata.get("available_online"),
            issue_date=description_metadata.get("issue_date"),
            source_issue=description_metadata.get("source_issue"),
            date_source="rss_published" if published else description_metadata.get("date_source"),
            date_confidence="A" if published else description_metadata.get("date_confidence", "F"),
            raw_data={"rss_feed_url": feed_url, "rss_guid": guid, "pii": pii},
        )
    }
    if description_metadata.get("date_precision"):
        record["date_precision"] = description_metadata["date_precision"]
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journals", type=Path, default=DATA_DIR / "journals.yml")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-items-per-feed", type=int, default=120)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--tier", default=None)
    args = parser.parse_args()

    output = args.output or DATA_DIR / "raw" / "rss" / f"{today_str()}.json"
    journals = filter_journals_by_tier(load_journals(args.journals), args.tier)
    if args.only:
        selected_ids = set(args.only)
        journals = [journal for journal in journals if journal.get("id") in selected_ids]
    selected = journals[: args.limit] if args.limit else journals
    records: list[dict[str, Any]] = []
    messages: list[str] = []
    attempted_feeds = 0
    successful_feeds = 0
    registry = load_registry()

    for journal in selected:
        feeds, feed_status = feeds_for_journal(journal, discover=args.discover)
        registry_entry = registry.setdefault("journals", {}).setdefault(journal["id"], {})
        registry_entry["last_rss_status"] = feed_status
        registry_entry["last_checked_at"] = today_str()
        if not feeds:
            registry_entry["last_rss_count"] = 0
            registry_entry.pop("last_rss_error", None)
            continue
        journal_count = 0
        errors: list[str] = []
        for source in feeds:
            attempted_feeds += 1
            fetched, error = fetch_journal_feed(journal, source)
            if error is None:
                successful_feeds += 1
                if args.max_items_per_feed:
                    fetched = fetched[: args.max_items_per_feed]
                records.extend(fetched)
                journal_count += len(fetched)
            else:
                errors.append(error)
            # Polite pacing: publishers commonly rate-limit rapid same-host bursts.
            time.sleep(0.6)
        registry_entry["last_rss_count"] = journal_count
        if errors and not journal_count:
            registry_entry["last_rss_error"] = errors[-1]
            messages.append(f"{journal.get('title')}: {errors[-1]}")
        else:
            registry_entry.pop("last_rss_error", None)
        messages.append(f"{journal.get('title')}: {journal_count} via {feed_status}")

    save_registry(registry)
    write_json(output, records)
    # A single successful feed does not establish that the selected RSS set
    # completed. Per-journal registry entries retain the usable paths, while
    # this aggregate status must surface partial failures for auditing.
    rss_ok = attempted_feeds == 0 or successful_feeds == attempted_feeds
    record_source(
        "rss",
        ok=rss_ok,
        count=len(records),
        message=(f"feeds={successful_feeds}/{attempted_feeds}; " + "; ".join(messages[-20:])).strip("; ") or str(output),
    )
    print(f"wrote {len(records)} RSS records to {output}")
    for message in messages:
        print(message)


if __name__ == "__main__":
    main()
