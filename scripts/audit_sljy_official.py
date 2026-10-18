"""Read-only SLJY first-party *printed-issue* roster audit.

Two independently served first-party publisher pages must agree on ordered
article identity before a bounded as-of comparison with canonical Daily.
This does NOT observe, enumerate or certify an online-first article universe.
No local schedule, source status, first_seen or canonical archive is mutated.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HOME = "https://www.jqte.net/sljjjsjjyj/ch/index.aspx"
ISSUE_LIST = "https://www.jqte.net/sljjjsjjyj/ch/reader/issue_list.aspx"
JOURNAL_ID = "journal-edcb877d78"
MAX_BYTES = 300_000
ARTICLE_RE = re.compile(
    r"""<a\b[^>]*\bhref\s*=\s*(['"])([^'"]*view_abstract\.aspx\?file_no=(\d{8,12})&(?:amp;)?flag=1)\1[^>]*>(.*?)</a>""",
    re.I | re.S,
)
ISSUE_RE = re.compile(r"(20\d{2})\s*,\s*\(?(\d{1,2})\)?\s*:")


class ObservationBlocked(ValueError):
    """Publisher scope or transport cannot be independently verified."""


def plain(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def title_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    return re.sub(r"[\s\u200b\-\u2010-\u2015\u2212]+", "", text)


def observe_url(url: str, *, timeout: int = 12) -> tuple[str, str]:
    """Only canonical HTTPS first-party pages. TLS verification remains ON."""
    if url not in {HOME, ISSUE_LIST}:
        raise ObservationBlocked("URL not in the exact first-party allowlist")
    req = urllib.request.Request(url, headers={"User-Agent": "AcademicDoor-ReadOnly-SLJY-Audit/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        final = response.geturl()
        if final != url or response.status != 200:
            raise ObservationBlocked(f"unexpected publisher redirect/status: {response.status}")
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ObservationBlocked("publisher response exceeds bounded audit size")
        charset = response.headers.get_content_charset()
    decoded = None
    for encoding in dict.fromkeys([charset or "utf-8", "utf-8", "gb18030"]):
        try:
            decoded = raw.decode(encoding)
            break
        except (UnicodeError, LookupError):
            continue
    if decoded is None:
        raise ObservationBlocked("unsupported publisher page encoding")
    return decoded, hashlib.sha256(raw).hexdigest()


def extract_links(fragment: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    ids: set[str] = set()
    for match in ARTICLE_RE.finditer(fragment):
        file_no = match.group(3)
        title = plain(match.group(4))
        if not title or "下载" in title or file_no in ids:
            continue
        # The file number comes directly from the publisher's article URL.
        ids.add(file_no)
        result.append({"file_no": file_no, "title": title})
    return result


def parse_homepage(page: str) -> list[dict[str, str]]:
    marker = page.find("本刊最新目录")
    if marker < 0:
        raise ObservationBlocked("homepage has no latest-issue heading")
    scoped = page[marker : marker + 40_000]
    blocks = re.findall(r"""<li><div\s+class=['"]aList['"][^>]*>(.*?)</li>""", scoped, re.I | re.S)
    records: list[dict[str, str]] = []
    ids: set[str] = set()
    for block in blocks:
        matches = extract_links(block)
        issue_text = re.search(r"<em\b[^>]*>(.*?)</em>", block, re.I | re.S)
        if not matches or not issue_text:
            continue
        m = ISSUE_RE.search(plain(issue_text.group(1)))
        if not m:
            raise ObservationBlocked("article without an identifiable printed issue")
        year, issue = m.groups()
        item = matches[0]
        if item["file_no"] in ids:
            raise ObservationBlocked("duplicate official article identity")
        ids.add(item["file_no"])
        records.append({**item, "issue": f"{year}({int(issue)})", "source_issue": plain(issue_text.group(1))})
    if not records:
        raise ObservationBlocked("publisher latest-issue roster is empty or unparseable")
    issues = {r["issue"] for r in records}
    if len(issues) != 1:
        raise ObservationBlocked("homepage latest-issue section contains mixed issues")
    return records


def compare_pages(homepage: str, issue_page: str) -> list[dict[str, str]]:
    rows = parse_homepage(homepage)
    issue_rows = extract_links(issue_page)
    expected = [(r["file_no"], title_key(r["title"])) for r in rows]
    observed = [(r["file_no"], title_key(r["title"])) for r in issue_rows]
    if not observed or len(set(x[0] for x in issue_rows)) != len(issue_rows):
        raise ObservationBlocked("issue-list roster empty or duplicate")
    if expected != observed:
        raise ObservationBlocked("homepage / official issue-list order or member identity differs")
    return rows


def audit_archive(rows: list[dict[str, str]], daily_dir: Path) -> dict[str, Any]:
    if not daily_dir.is_dir():
        raise ObservationBlocked("canonical Daily archive directory unavailable")
    files = sorted(daily_dir.glob("20??-??-??.json"))
    if not files:
        raise ObservationBlocked("canonical Daily files unavailable")
    expected = {title_key(r["title"]): r for r in rows}
    if len(expected) != len(rows):
        raise ObservationBlocked("publisher title key collision")
    found: dict[str, list[dict[str, Any]]] = {key: [] for key in expected}
    for path in files:
        try:
            daily = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ObservationBlocked(f"unreadable Daily archive {path.name}: {type(exc).__name__}") from exc
        if not isinstance(daily, list):
            raise ObservationBlocked(f"Daily archive is not a record list: {path.name}")
        for record in daily:
            if not isinstance(record, dict) or record.get("journal_id") != JOURNAL_ID:
                continue
            key = title_key(str(record.get("title") or ""))
            if key in found:
                found[key].append({
                    "daily_file": path.name,
                    "first_seen": record.get("first_seen"),
                    "canonical_id": record.get("id"),
                    "published_online": record.get("published_online"),
                })
    missing = [{"file_no": v["file_no"], "title": v["title"]} for k, v in expected.items() if not found[k]]
    duplicates = [{"title": expected[k]["title"], "locations": [m["daily_file"] for m in entries]} for k, entries in found.items() if len(entries) > 1]
    return {"archives_scanned": len(files), "publisher_listed": len(rows),
            "unique_archive_matches": sum(bool(x) for x in found.values()),
            "missing": missing, "duplicate_matches": duplicates,
            "matched_first_seen": [
                {"file_no": r["file_no"], "first_seen": found[title_key(r["title"])][0]["first_seen"]}
                for r in rows if found[title_key(r["title"])]
            ]}


def evaluate(homepage: str, issue_page: str, *, daily_dir: Path,
             observation_time: str, hashes: dict[str, str]) -> dict[str, Any]:
    rows = compare_pages(homepage, issue_page)
    diff = audit_archive(rows, daily_dir)
    matched = not diff["missing"] and not diff["duplicate_matches"]
    return {
        "journal_id": JOURNAL_ID,
        "scope": "PUBLISHER_LISTED_CURRENT_PRINT_ISSUE_ONLY",
        "issue": rows[0]["issue"],
        "observed_at": observation_time,
        "first_party_pages": {"homepage": HOME, "issue_list": ISSUE_LIST},
        "page_sha256": hashes,
        "publisher_member_order": [{"ordinal": i + 1, "file_no": r["file_no"], "title": r["title"]}
                                   for i, r in enumerate(rows)],
        "print_issue_roster_status": "RECONCILED_AS_OF" if matched else "DIFF_OBSERVED_AS_OF",
        "online_first_coverage_status": "NOT_VERIFIED",
        "online_first_note": "These pages enumerate the printed latest issue, not a bounded official all-online-first roster.",
        "archive": diff,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Read-only first-party SLJY current printed-issue audit")
    p.add_argument("--daily-dir", type=Path, default=ROOT / "data" / "daily")
    p.add_argument("--home-html", type=Path, default=None, help="Offline test fixture; must pair with --issue-html")
    p.add_argument("--issue-html", type=Path, default=None)
    p.add_argument("--output", type=Path, help="Optional explicit evidence JSON path; canonical data is never changed")
    args = p.parse_args()
    try:
        if (args.home_html is None) != (args.issue_html is None):
            raise ObservationBlocked("both publisher fixture pages are required")
        as_of = datetime.now(timezone.utc).isoformat()
        hashes = {}
        if args.home_html:
            a = args.home_html.read_bytes()
            b = args.issue_html.read_bytes()
            homepage, issue_page = a.decode("utf-8"), b.decode("utf-8")
            hashes = {"homepage": hashlib.sha256(a).hexdigest(), "issue_list": hashlib.sha256(b).hexdigest()}
        else:
            homepage, hashes["homepage"] = observe_url(HOME)
            issue_page, hashes["issue_list"] = observe_url(ISSUE_LIST)
            as_of = datetime.now(timezone.utc).isoformat()
        result = evaluate(homepage, issue_page, daily_dir=args.daily_dir,
                          observation_time=as_of, hashes=hashes)
    except (OSError, ValueError, UnicodeError) as exc:
        result = {
            "scope": "PUBLISHER_LISTED_CURRENT_PRINT_ISSUE_ONLY",
            "print_issue_roster_status": "VERIFICATION_BLOCKED",
            "online_first_coverage_status": "NOT_VERIFIED",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "error": f"{type(exc).__name__}: {exc}",
        }
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    sys.stdout.write(payload)
    return 0 if result["print_issue_roster_status"] == "RECONCILED_AS_OF" else 2


if __name__ == "__main__":
    raise SystemExit(main())
