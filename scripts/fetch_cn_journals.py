"""Fetch official Chinese journal article records.

The Chinese journal sites are heterogeneous and several do not expose RSS.
This module therefore uses conservative, site-specific parsers. A record is
kept only when it looks like a real article detail page or official TOC item.

Date policy:
- available_online: exact online/first-published date from official source.
- published_online: exact official publication date when available.
- source_issue: issue/page string when the site only exposes issue metadata.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import ssl
import socket
import time
import http.cookiejar
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from common import DATA_DIR, fetch_text, filter_journals_by_tier, load_journals, now_iso, today_str, write_json
from status import load_status, now, record_source, save_status


DETAIL_LIMIT = 0
DETAIL_ATTEMPTED = 0
GLSJ_LAST_NOTE = ""
GLSJ_FETCH_BUDGET_SECONDS = 55

CN_HOME_URLS = {
    "journal-f69300dae2": "https://zgncjj.ajcass.com/#/",
    "journal-679eaa2a0c": "https://sjjj.magtech.com.cn/CN/home",
    "journal-ba9f46c919": "https://erj.ajcass.com/#/",
    "journal-379b4022ce": "https://glsj.chinajournal.net.cn/WKB/WebPublication/index.aspx?mid=glsj",
    "journal-bf2aa9381f": "http://ciejournal.ajcass.com/?jumpnotice=201606280001",
    "journal-edcb877d78": "https://www.jqte.net/sljjjsjjyj/ch/index.aspx",
}

AJCASS_JOURNAL_IDS = {
    "journal-f69300dae2": "201606270007",
    "journal-ba9f46c919": "201803050001",
}

AJCASS_LIST_ENDPOINTS = {
    "journal-f69300dae2": "IssueContentApi/GetIssueNormalSearch",
    "journal-ba9f46c919": "IssueContentApi/GetIssueSimpleSearch",
}

NOISE_TEXT = (
    "数据库",
    "征文",
    "会议",
    "新闻",
    "规范",
    "说明",
    "投稿",
    "采编",
    "影响因子",
    "获评",
    "复现包",
    "补充材料",
    "期刊征文",
    "公告",
    "通知",
    "目录",
    "欢迎订阅",
    "征订",
    "——评《",
)


def all_journal_sources_ok(rows: list[dict[str, Any]]) -> bool:
    """Report group health only when every configured child source succeeds."""
    return bool(rows) and all(bool(row.get("ok")) for row in rows)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def decode_payload(payload: bytes, charset: str | None = None) -> str:
    candidates = []
    if charset:
        candidates.append(charset)
    candidates.extend(["utf-8", "gb18030", "gbk"])
    for candidate in dict.fromkeys(candidates):
        try:
            return payload.decode(candidate)
        except Exception:
            continue
    return payload.decode("utf-8", errors="replace")


def fetch_text_partial(url: str, timeout: int = 25, max_bytes: int = 300_000) -> str:
    """Read enough HTML for TOC parsing without letting slow sites stall."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    contexts = [None]
    if url.startswith("https://"):
        contexts.append(ssl._create_unverified_context())
    last_error: Exception | None = None
    def read_with(open_func: Any, context: Any = None) -> str | None:
        payload = bytearray()
        charset = None
        try:
            open_kwargs = {"timeout": timeout}
            if context is not None:
                open_kwargs["context"] = context
            with open_func(request, **open_kwargs) as response:  # type: ignore[arg-type]
                charset = response.headers.get_content_charset()
                while len(payload) < max_bytes:
                    try:
                        chunk = response.read(min(65536, max_bytes - len(payload)))
                    except (TimeoutError, socket.timeout):
                        break
                    if not chunk:
                        break
                    payload.extend(chunk)
            if payload:
                return decode_payload(bytes(payload), charset)
        except Exception as exc:  # noqa: BLE001
            nonlocal last_error
            last_error = exc
            if payload:
                return decode_payload(bytes(payload), charset)
        return None

    for context in contexts:
        result = read_with(urllib.request.urlopen, context)
        if result is not None:
            return result
    for context in contexts:
        handlers: list[Any] = [urllib.request.ProxyHandler({})]
        if context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=context))
        opener = urllib.request.build_opener(*handlers)
        result = read_with(opener.open)
        if result is not None:
            return result
    if last_error:
        raise last_error
    return ""


def clean_title(value: str | None) -> str:
    value = clean_text(value)
    value = re.sub(r"^(摘要|Abstract|论文|标题)[:：]\s*", "", value, flags=re.I)
    return value.strip(" -_|\u3000")


def has_chinese(value: str | None) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in value or "")


def is_noise_title(title: str) -> bool:
    return len(title) < 4 or any(word in title for word in NOISE_TEXT)


def normalize_url(url: str, base_url: str) -> str:
    return urllib.parse.urljoin(base_url, html.unescape(url)).split("#", 1)[0]


def extract_date(text: str) -> str | None:
    patterns = [
        r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?",
        r"(20\d{2})(\d{2})(\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if not match:
            continue
        try:
            year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            return None
    return None


def extract_upload_date(path: str | None) -> str | None:
    if not path:
        return None
    matches = re.findall(r"(20\d{2})(\d{2})(\d{2})\d{0,6}", path)
    if not matches:
        return None
    for year_text, month_text, day_text in reversed(matches):
        try:
            year, month, day = (int(year_text), int(month_text), int(day_text))
            return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            continue
    return None


def first_nonempty(*values: str | None) -> str | None:
    for value in values:
        if value and value.strip():
            return value.strip()
    return None


def issue_key(source_issue: str | None) -> tuple[int, int] | None:
    text = clean_text(source_issue)
    if not text:
        return None
    patterns = [
        r"(20\d{2})\s*年\s*,?\s*第\s*(\d{1,2})\s*期",
        r"(20\d{2})\s*年\s*(\d{1,2})\s*期",
        r"(20\d{2})\s*,\s*\d+\s*\(\s*(\d{1,2})\s*\)",
        r"(20\d{2})\s*,\s*\(\s*(\d{1,2})\s*\)",
        r"(20\d{2})\s*年第\s*(\d{1,2})\s*期",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def keep_latest_issue(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed = [(issue_key(record.get("source_issue")), record) for record in records]
    keys = [key for key, _record in keyed if key is not None]
    if not keys:
        return records
    latest = max(keys)
    current_year = int(today_str()[:4])
    if latest[0] < current_year:
        return []
    latest_records = [record for key, record in keyed if key == latest]
    # Keep undated records only when the source produced no usable issue keys.
    return latest_records


def latest_issue_note(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> str | None:
    keyed = [issue_key(record.get("source_issue")) for record in before]
    keys = [key for key in keyed if key is not None]
    if not keys:
        return None
    latest = max(keys)
    current_year = int(today_str()[:4])
    if latest[0] < current_year:
        return f"stale-latest {latest[0]}年第{latest[1]}期 excluded"
    if len(after) != len(before):
        return f"latest-issue {len(after)}/{len(before)}"
    return None


def make_record(
    journal: dict[str, Any],
    title: str,
    url: str,
    *,
    authors: list[str] | None = None,
    abstract: str | None = None,
    available_online: str | None = None,
    published_online: str | None = None,
    source_issue: str | None = None,
    date_source: str = "issue_only",
    source_url: str | None = None,
    doi: str | None = None,
) -> dict[str, Any] | None:
    title = clean_title(title)
    if is_noise_title(title):
        return None
    doi = doi.strip() if doi else None
    if doi and not doi.lower().startswith("10."):
        doi = None
    return {
        "title": title,
        "title_zh": title if has_chinese(title) else None,
        "abstract": clean_text(abstract) or None,
        "abstract_zh": clean_text(abstract) if has_chinese(abstract) else None,
        "authors": authors or [],
        "journal": journal["title"],
        "journal_short": journal.get("short_name"),
        "journal_id": journal["id"],
        "source_type": "journal",
        "source": "cn-official",
        "source_url": source_url or url,
        "publisher": journal.get("publisher"),
        "published_online": published_online,
        "available_online": available_online,
        "source_issue": source_issue,
        "date_source": date_source,
        "detected_at": now_iso(),
        "doi": doi,
        "url": url,
        "pdf_url": None,
        "fields": journal.get("fields", []),
        "ai_tags": [],
        "translation_status": "native_chinese" if has_chinese(title) else "missing_title",
    }


def parse_meta(html_text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for tag in re.findall(r"<meta\b[^>]*>", html_text, flags=re.I):
        key_match = re.search(r"(?:name|property)=['\"]([^'\"]+)['\"]", tag, flags=re.I)
        content_match = re.search(r"content=['\"]([^'\"]*)['\"]", tag, flags=re.I)
        if key_match and content_match:
            meta[key_match.group(1).lower()] = html.unescape(content_match.group(1)).strip()
    return meta


def parse_detail_meta(html_text: str) -> tuple[str | None, list[str], str | None, str | None, str | None]:
    meta = parse_meta(html_text)
    title = first_nonempty(
        meta.get("citation_title"),
        meta.get("dc.title"),
        meta.get("og:title"),
        clean_text(re.search(r"<title[^>]*>([\s\S]*?)</title>", html_text, flags=re.I).group(1))
        if re.search(r"<title[^>]*>([\s\S]*?)</title>", html_text, flags=re.I)
        else None,
    )
    authors = [value for key, value in meta.items() if key.startswith("citation_author") and value]
    doi = first_nonempty(meta.get("citation_doi"), meta.get("dc.identifier"), meta.get("doi"))
    online = first_nonempty(meta.get("citation_online_date"), meta.get("article:published_time"))
    published = first_nonempty(meta.get("citation_publication_date"), meta.get("dc.date"))
    return title, authors, doi, extract_date(online or ""), extract_date(published or "")


def split_authors(value: str | None) -> list[str]:
    text = clean_text(value)
    text = re.sub(r"^\[|\]$", "", text)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[，,;；、]\s*", text) if item.strip()]


def parse_world_economy(html_text: str, journal: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    base_url = CN_HOME_URLS[journal["id"]]
    blocks = re.findall(r'<li[^>]+id=["\']art\d+["\'][\s\S]*?</li>', html_text, flags=re.I)
    records: list[dict[str, Any]] = []
    for block in blocks:
        title_match = re.search(r'class=["\']j-title-1["\'][\s\S]*?<a[^>]+href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>', block, flags=re.I)
        if not title_match:
            continue
        href, title_html = title_match.groups()
        url = normalize_url(href, base_url)
        if "/CN/Y" not in url:
            continue
        authors_match = re.search(r'class=["\']j-author["\'][^>]*>([\s\S]*?)</div>', block, flags=re.I)
        issue_match = re.search(r'class=["\']j-volumn["\'][^>]*>([\s\S]*?)</span>', block, flags=re.I)
        abstract_match = re.search(r'class=["\']j-abstract["\'][^>]*>([\s\S]*?)</div>', block, flags=re.I)
        published_online = None
        try:
            detail_text = fetch_text_partial(url, timeout=12, max_bytes=220_000)
            date_match = re.search(r"发布日期\s*(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)", clean_text(detail_text))
            published_online = extract_date(date_match.group(1)) if date_match else None
        except Exception:
            published_online = None
        record = make_record(
            journal,
            clean_title(title_html),
            url,
            authors=split_authors(authors_match.group(1) if authors_match else None),
            abstract=abstract_match.group(1) if abstract_match else None,
            published_online=published_online,
            source_issue=clean_text(issue_match.group(1)) if issue_match else None,
            date_source="official_publish_date" if published_online else "issue_only",
            source_url=base_url,
        )
        if record:
            enrich_cn_detail(record)
            records.append(record)
        if len(records) >= limit:
            break
    return records


def parse_jqte(html_text: str, journal: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    base_url = CN_HOME_URLS[journal["id"]]
    marker = html_text.find("本刊最新目录")
    main = html_text[marker : marker + 30000] if marker >= 0 else html_text
    matches = re.findall(r"<li><div class=\"aList\">([\s\S]*?)</li>", main, flags=re.I)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in matches:
        title_match = re.search(r"<a[^>]+href=['\"]([^'\"]*reader/view_abstract\.aspx\?file_no=[^'\"]+)['\"][^>]*>([\s\S]*?)</a>", block, flags=re.I)
        if not title_match:
            continue
        href, title_html = title_match.groups()
        url = normalize_url(href, base_url)
        if url in seen:
            continue
        seen.add(url)
        authors_match = re.search(r"<span[^>]*>\s*(?:\[|【)([\s\S]*?)(?:\]|】)\s*</span>", block, flags=re.I)
        issue_match = re.search(r"<em[^>]*>([\s\S]*?)</em>", block, flags=re.I)
        record = make_record(
            journal,
            clean_title(title_html),
            url,
            authors=split_authors(authors_match.group(1) if authors_match else None),
            source_issue=clean_text(issue_match.group(1)) if issue_match else None,
            date_source="issue_only",
            source_url=base_url,
        )
        if record:
            enrich_cn_detail(record)
            records.append(record)
        if len(records) >= limit:
            break
    return records


def parse_cie(html_text: str, journal: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    base_url = CN_HOME_URLS[journal["id"]]
    main = html_text
    marker = main.find("当期目录")
    if marker >= 0:
        main = main[marker : marker + 50000]
    matches = re.findall(r'<table[\s\S]*?</table>', main, flags=re.I)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in matches:
        title_match = re.search(r'<a[^>]+href=["\']([^"\']*Magazine/Show\?id=\d+[^"\']*)["\'][^>]*font-weight:\s*bold[^>]*>([\s\S]*?)</a>', block, flags=re.I)
        if not title_match:
            continue
        href, title_html = title_match.groups()
        url = normalize_url(href, base_url)
        if url in seen:
            continue
        seen.add(url)
        text_tail = clean_text(block)
        issue_match = re.search(r"(20\d{2}\s*年,\s*第\s*\d+\s*期\s*[:：]\s*\d+\s*-\s*\d+\s*页)", text_tail)
        authors_match = re.search(r"</a>\s*<span[^>]*>([\s\S]*?)</span>", block, flags=re.I)
        record = make_record(
            journal,
            clean_title(title_html),
            url,
            authors=split_authors(authors_match.group(1) if authors_match else None),
            source_issue=clean_text(issue_match.group(1)) if issue_match else None,
            date_source="issue_only",
            source_url=base_url,
        )
        if record:
            enrich_cn_detail(record)
            records.append(record)
        if len(records) >= limit:
            break
    return records


class SimpleLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self._href = dict(attrs).get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            text = clean_title("".join(self._text))
            if text:
                self.links.append((self._href, text))
            self._href = None
            self._text = []


def fetch_json_api(path: str, params: dict[str, Any], *, method: str = "GET", timeout: int = 12) -> Any:
    url = "https://api.ajcass.com/api/" + path.lstrip("/")
    data = None
    if method == "POST":
        data = json.dumps(params, ensure_ascii=False).encode("utf-8")
    else:
        query = urllib.parse.urlencode(params)
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
        method=method,
    )
    last_error: Exception | None = None
    for attempt in range(3):
        for context in (None, ssl._create_unverified_context()):
            try:
                open_kwargs = {"timeout": timeout + attempt * 4}
                if context is not None:
                    open_kwargs["context"] = context
                with urllib.request.urlopen(request, **open_kwargs) as response:  # type: ignore[arg-type]
                    return json.loads(response.read().decode("utf-8", errors="replace"))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
    if last_error:
        raise last_error
    raise RuntimeError("empty API response")


def walk_json(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if any(key.lower() in {"title", "papertitle", "name", "subject"} for key in value):
            items.append(value)
        for child in value.values():
            items.extend(walk_json(child))
    elif isinstance(value, list):
        for child in value:
            items.extend(walk_json(child))
    return items


def parse_ajcass_api(journal: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    journal_id = AJCASS_JOURNAL_IDS[journal["id"]]
    endpoint = AJCASS_LIST_ENDPOINTS[journal["id"]]
    params = {"JournalID": int(journal_id), "curr": 1, "limit": limit}
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    payload = fetch_json_api(endpoint, params, method="POST")
    for item in walk_json(payload):
        title = first_nonempty(
            str(item.get("title")) if item.get("title") else None,
            str(item.get("Title")) if item.get("Title") else None,
            str(item.get("name")) if item.get("name") else None,
        )
        if not title:
            continue
        content_id = first_nonempty(
            str(item.get("contentId")) if item.get("contentId") else None,
            str(item.get("contentID")) if item.get("contentID") else None,
            str(item.get("id")) if item.get("id") else None,
            str(item.get("ID")) if item.get("ID") else None,
        )
        base_url = CN_HOME_URLS[journal["id"]].split("#", 1)[0].rstrip("/")
        url = f"{base_url}/#/detail?contentId={urllib.parse.quote(content_id)}" if content_id else base_url
        if url in seen:
            continue
        seen.add(url)
        year = item.get("year")
        volume = item.get("volume")
        issue_no = item.get("issue")
        start_page = item.get("startPageName") or item.get("startPageNum")
        end_page = item.get("endPageName") or item.get("pageNum")
        if year and volume and issue_no and start_page and end_page:
            issue_fallback = f"{year}, {volume}({issue_no}): {start_page}-{end_page}"
        elif year and volume and issue_no and start_page:
            issue_fallback = f"{year}, {volume}({issue_no}): {start_page}"
        elif year and issue_no:
            issue_fallback = f"{year}年第{issue_no}期"
        else:
            issue_fallback = None
        source_issue = first_nonempty(
            str(item.get("yearVolumeIssue")).replace("-0", "") if item.get("yearVolumeIssue") else None,
            issue_fallback,
        )
        file_path = first_nonempty(str(item.get("filePath")) if item.get("filePath") else None)
        pdf_url = urllib.parse.urljoin("https://api.ajcass.com", file_path) if file_path else None
        upload_date = extract_upload_date(file_path)
        record = make_record(
            journal,
            title,
            url,
            authors=split_authors(first_nonempty(str(item.get("authorsName")) if item.get("authorsName") else None, str(item.get("authors")) if item.get("authors") else None)),
            abstract=first_nonempty(str(item.get("abstract")) if item.get("abstract") else None),
            published_online=upload_date,
            source_issue=source_issue,
            date_source="file_upload_date" if upload_date else "issue_only",
            source_url="https://api.ajcass.com/api/" + endpoint,
        )
        if record:
            record["pdf_url"] = pdf_url
            records.append(record)
        if len(records) >= limit:
            return records
    return records


def fetch_glsj(journal: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    base = "https://glsj.cbpt.cnki.net/WKB2/WebPublication/"
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
        "Referer": base + "index.aspx?mid=glsj",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    opener.open(urllib.request.Request(base + "index.aspx?mid=glsj", headers=headers), timeout=20).read()
    records: list[dict[str, Any]] = []
    latest_page = ""
    latest_endpoint = ""
    for year in range(2026, 2022, -1):
        for issue in range(12, 0, -1):
            endpoint = f"getThisIssuePaperInfo.ashx?y={year}&i={issue}"
            try:
                page = opener.open(urllib.request.Request(base + endpoint, headers=headers), timeout=10).read().decode("utf-8", errors="replace")
            except Exception:
                continue
            if "暂无内容" in page or len(page) < 200:
                continue
            latest_page = page
            latest_endpoint = endpoint
            break
        if latest_page:
            break
    if not latest_page:
        return records
    blocks = re.findall(r"<li>\s*<h3>[\s\S]*?</li>", latest_page, flags=re.I)
    for block in blocks:
        title_match = re.search(r'<a[^>]+href=["\']([^"\']*paperDigest\.aspx\?paperID=[^"\']+)["\'][^>]*>([\s\S]*?)</a>', block, flags=re.I)
        if not title_match:
            continue
        href, title_html = title_match.groups()
        authors_match = re.search(r"<samp>([\s\S]*?)</samp>", block, flags=re.I)
        issue_match = re.search(r"<span>([\s\S]*?)<a", block, flags=re.I)
        record = make_record(
            journal,
            title_html,
            normalize_url(href, base),
            authors=split_authors(authors_match.group(1) if authors_match else None),
            source_issue=clean_text(issue_match.group(1)) if issue_match else None,
            date_source="issue_only",
            source_url=base + latest_endpoint,
        )
        if record:
            records.append(record)
        if len(records) >= limit:
            return records
    return records


def extract_date(text: str) -> str | None:  # type: ignore[no-redef]
    patterns = [
        r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?",
        r"(20\d{2})(\d{2})(\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if not match:
            continue
        try:
            year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            continue
    return None


def split_authors(value: str | None) -> list[str]:  # type: ignore[no-redef]
    text = clean_text(value)
    text = re.sub(r"^\[|\]$", "", text)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[;；、，,\s]+", text) if item.strip()]


def issue_key(source_issue: str | None) -> tuple[int, int] | None:  # type: ignore[no-redef]
    text = clean_text(source_issue)
    if not text:
        return None
    patterns = [
        r"(20\d{2})\s*年\s*,?\s*第?\s*(\d{1,2})\s*期",
        r"(20\d{2})\s*年\s*(\d{1,2})\s*期",
        r"(20\d{2})\s*,\s*\d+\s*\(\s*(\d{1,2})\s*\)",
        r"(20\d{2})\s*,\s*\(\s*(\d{1,2})\s*\)",
        r"(20\d{2})年第\s*(\d{1,2})期",
        r"(20\d{2})年(\d{1,2})期",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def latest_issue_note(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> str | None:  # type: ignore[no-redef]
    keys = [key for key in (issue_key(record.get("source_issue")) for record in before) if key is not None]
    if not keys:
        return None
    latest = max(keys)
    current_year = int(today_str()[:4])
    if latest[0] < current_year:
        return f"stale-latest {latest[0]}年第{latest[1]}期 excluded"
    if len(after) != len(before):
        return f"latest-issue {len(after)}/{len(before)}"
    return None


def enrich_cn_detail(record: dict[str, Any]) -> None:
    global DETAIL_ATTEMPTED
    if DETAIL_LIMIT <= 0 or DETAIL_ATTEMPTED >= DETAIL_LIMIT:
        return
    url = record.get("url")
    if not url:
        return
    DETAIL_ATTEMPTED += 1
    try:
        detail_text = fetch_text_partial(str(url), timeout=12, max_bytes=260_000)
    except Exception:
        return
    title, authors, doi, online, published = parse_detail_meta(detail_text)
    if doi and not record.get("doi"):
        record["doi"] = doi
    if authors and not record.get("authors"):
        record["authors"] = authors
    if title and not record.get("title"):
        record["title"] = title
    date_value = online or published
    if date_value and not (record.get("available_online") or record.get("published_online")):
        record["available_online"] = date_value
        record["published_online"] = date_value
        record["date_source"] = "official_publish_date"
    meta = parse_meta(detail_text)
    abstract = meta.get("citation_abstract") or meta.get("dc.description") or meta.get("description")
    if abstract and not record.get("abstract"):
        record["abstract"] = clean_text(abstract)
        if has_chinese(abstract):
            record["abstract_zh"] = clean_text(abstract)


def remaining_timeout(deadline: float, cap: int) -> int | None:
    remaining = int(deadline - time.monotonic())
    return min(cap, remaining) if remaining > 0 else None


def current_issue_candidates() -> list[int]:
    """Search the current plausible issue first; a future issue is lower priority."""
    month = datetime.now(ZoneInfo("Asia/Shanghai")).month
    return [month] + ([month + 1] if month < 12 else []) + list(range(month - 1, 0, -1))


def glsj_validation_page(page: str) -> bool:
    """CBPT can return HTTP-200 login/challenge instead of a publisher TOC."""
    return "showValidateCode.aspx" in page or "login.css" in page


def fetch_glsj_archive_issue(journal: dict[str, Any], limit: int, *, deadline: float) -> list[dict[str, Any]]:
    """Read Management World's public archive TOC when the AJAX endpoint is blocked.

    The public chinajournal host exposes the 2026 issue pages reliably even
    when the older CBPT ``.ashx`` endpoint returns an empty/validation page.
    """
    global GLSJ_LAST_NOTE
    current_year = int(today_str()[:4])
    validation_seen = False
    bases = (
        "https://glsj.chinajournal.net.cn/WKB2/WebPublication/",
        "https://glsj.chinajournal.net.cn/WKB/WebPublication/",
    )
    for base in bases:
        for issue in current_issue_candidates():
            request_timeout = remaining_timeout(deadline, 5)
            if request_timeout is None:
                GLSJ_LAST_NOTE = "time-budget-exhausted"
                return []
            url = f"{base}wkTextContent.aspx?colType=4&yt={current_year}&st={issue:02d}"
            try:
                page = fetch_text_partial(url, timeout=request_timeout, max_bytes=900_000)
            except Exception:
                continue
            if glsj_validation_page(page):
                # Try one different first-party archive base before declaring
                # verification blocked; never scan all issues on a CAPTCHA.
                validation_seen = True
                break
            if "paperDigest.aspx" not in page:
                continue
            page_issue_match = re.search(r"(20\d{2})年\s*(\d{1,2})期(?:目次)?", clean_text(page))
            page_issue = (
                f"{page_issue_match.group(1)}年第{int(page_issue_match.group(2))}期"
                if page_issue_match
                else f"{current_year}年第{issue}期"
            )
            blocks = [
                block
                for block in re.findall(r"<li\b[\s\S]*?</li>", page, flags=re.I)
                if "paperDigest.aspx" in block
            ]
            records: list[dict[str, Any]] = []
            for block in blocks:
                title_match = re.search(
                    r'<a[^>]+href=["\']([^"\']*paperDigest\.aspx\?paperID=[^"\']+)["\'][^>]*>([\s\S]*?)</a>',
                    block,
                    flags=re.I,
                )
                if not title_match:
                    continue
                href, title_html = title_match.groups()
                authors_match = re.search(r"<samp>([\s\S]*?)</samp>", block, flags=re.I)
                record = make_record(
                    journal,
                    title_html,
                    normalize_url(href, base),
                    authors=split_authors(authors_match.group(1) if authors_match else None),
                    source_issue=page_issue,
                    date_source="issue_only",
                    source_url=url,
                )
                if record:
                    records.append(record)
                if len(records) >= limit:
                    return records
            if records:
                return records
    if validation_seen:
        GLSJ_LAST_NOTE = "archive-validation"
    return []


def fetch_glsj(journal: dict[str, Any], limit: int) -> list[dict[str, Any]]:  # type: ignore[no-redef]
    """Fetch Management World current-year issue data when CBPT allows it.

    The CNKI/CBPT site often returns a validation page. In that case we fail
    fast instead of looping over old issues and slowing every monitor run.
    """
    global GLSJ_LAST_NOTE
    GLSJ_LAST_NOTE = ""
    deadline = time.monotonic() + GLSJ_FETCH_BUDGET_SECONDS
    base = "https://glsj.cbpt.cnki.net/WKB2/WebPublication/"
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
        "Referer": base + "index.aspx?mid=glsj",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    records: list[dict[str, Any]] = []
    try:
        opener.open(
            urllib.request.Request(base + "index.aspx?mid=glsj", headers=headers),
            timeout=remaining_timeout(deadline, 6) or 1,
        ).read()
    except Exception:
        GLSJ_LAST_NOTE = "cbpt-index-unavailable; trying-public-archive"
    try:
        first_endpoint = "getFirstPublishPaperInfo.ashx"
        first_page = opener.open(
            urllib.request.Request(base + first_endpoint, headers=headers),
            timeout=remaining_timeout(deadline, 4) or 1,
        ).read().decode("utf-8", errors="replace")
    except Exception:
        first_page = ""
    if first_page and glsj_validation_page(first_page):
        GLSJ_LAST_NOTE = "first-publish-validation"
        return records
    if first_page and "暂无内容" not in first_page and "鏆傛棤鍐呭" not in first_page and "paperDigest.aspx" in first_page:
        blocks = re.findall(r"<li>\s*<h3>[\s\S]*?</li>", first_page, flags=re.I)
        for block in blocks:
            title_match = re.search(r'<a[^>]+href=["\']([^"\']*paperDigest\.aspx\?paperID=[^"\']+)["\'][^>]*>([\s\S]*?)</a>', block, flags=re.I)
            if not title_match:
                continue
            href, title_html = title_match.groups()
            authors_match = re.search(r"<samp>([\s\S]*?)</samp>", block, flags=re.I)
            date_value = extract_date(clean_text(block))
            record = make_record(
                journal,
                title_html,
                normalize_url(href, base),
                authors=split_authors(authors_match.group(1) if authors_match else None),
                source_issue="网络首发",
                date_source="official_first_publish",
                source_url=base + first_endpoint,
            )
            if record:
                if date_value:
                    record["available_online"] = date_value
                    record["date_confidence"] = "A"
                enrich_cn_detail(record)
                records.append(record)
            if len(records) >= limit:
                return records
        if records:
            return records
    current_year = int(today_str()[:4])
    latest_page = ""
    latest_endpoint = ""
    for issue in current_issue_candidates():
        request_timeout = remaining_timeout(deadline, 3)
        if request_timeout is None:
            GLSJ_LAST_NOTE = "time-budget-exhausted"
            return records
        endpoint = f"getThisIssuePaperInfo.ashx?y={current_year}&i={issue}"
        try:
            page = opener.open(
                urllib.request.Request(base + endpoint, headers=headers),
                timeout=request_timeout,
            ).read().decode("utf-8", errors="replace")
        except Exception:
            continue
        if glsj_validation_page(page):
            GLSJ_LAST_NOTE = "current-issue-validation"
            return records
        if "暂无内容" in page or "paperDigest.aspx" not in page or len(page) < 200:
            continue
        latest_page = page
        latest_endpoint = endpoint
        break
    if not latest_page:
        archive_records = fetch_glsj_archive_issue(journal, limit, deadline=deadline)
        if GLSJ_LAST_NOTE in {"archive-validation", "time-budget-exhausted"}:
            return records
        if archive_records:
            issue_text = str(archive_records[0].get("source_issue") or "")
            issue_match = re.search(r"第(\d+)期", issue_text)
            issue_number = issue_match.group(1) if issue_match else "?"
            GLSJ_LAST_NOTE = f"archive-toc {current_year}年第{issue_number}期"
            return archive_records
        previous_year = current_year - 1
        for issue in range(12, 0, -1):
            request_timeout = remaining_timeout(deadline, 2)
            if request_timeout is None:
                GLSJ_LAST_NOTE = "time-budget-exhausted"
                return records
            endpoint = f"getThisIssuePaperInfo.ashx?y={previous_year}&i={issue}"
            try:
                page = opener.open(
                    urllib.request.Request(base + endpoint, headers=headers),
                    timeout=request_timeout,
                ).read().decode("utf-8", errors="replace")
            except Exception:
                continue
            if glsj_validation_page(page):
                GLSJ_LAST_NOTE = "captcha-or-validation"
                return records
            count = page.count("paperDigest.aspx")
            if count:
                GLSJ_LAST_NOTE = f"current-year-empty; previous-latest {previous_year}-{issue:02d} has {count} papers excluded"
                return records
        GLSJ_LAST_NOTE = "current-year-empty"
        return records
    blocks = re.findall(r"<li>\s*<h3>[\s\S]*?</li>", latest_page, flags=re.I)
    for block in blocks:
        title_match = re.search(r'<a[^>]+href=["\']([^"\']*paperDigest\.aspx\?paperID=[^"\']+)["\'][^>]*>([\s\S]*?)</a>', block, flags=re.I)
        if not title_match:
            continue
        href, title_html = title_match.groups()
        authors_match = re.search(r"<samp>([\s\S]*?)</samp>", block, flags=re.I)
        issue_match = re.search(r"<span>([\s\S]*?)<a", block, flags=re.I)
        record = make_record(
            journal,
            title_html,
            normalize_url(href, base),
            authors=split_authors(authors_match.group(1) if authors_match else None),
            source_issue=clean_text(issue_match.group(1)) if issue_match else None,
            date_source="issue_only",
            source_url=base + latest_endpoint,
        )
        if record:
            enrich_cn_detail(record)
            records.append(record)
        if len(records) >= limit:
            return records
    return records


def parse_world_economy(html_text: str, journal: dict[str, Any], limit: int) -> list[dict[str, Any]]:  # type: ignore[no-redef]
    base_url = CN_HOME_URLS[journal["id"]]
    blocks = re.findall(r'<li[^>]+id=["\']art\d+["\'][\s\S]*?</li>', html_text, flags=re.I)
    records: list[dict[str, Any]] = []
    for block in blocks:
        title_match = re.search(r'class=["\']j-title-1["\'][\s\S]*?<a[^>]+href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>', block, flags=re.I)
        if not title_match:
            continue
        href, title_html = title_match.groups()
        url = normalize_url(href, base_url)
        if "/CN/Y" not in url:
            continue
        authors_match = re.search(r'class=["\']j-author["\'][^>]*>([\s\S]*?)</div>', block, flags=re.I)
        issue_match = re.search(r'class=["\']j-volumn["\'][^>]*>([\s\S]*?)</span>', block, flags=re.I)
        abstract_match = re.search(r'class=["\']j-abstract["\'][^>]*>([\s\S]*?)</div>', block, flags=re.I)
        record = make_record(
            journal,
            clean_title(title_html),
            url,
            authors=split_authors(authors_match.group(1) if authors_match else None),
            abstract=abstract_match.group(1) if abstract_match else None,
            source_issue=clean_text(issue_match.group(1)) if issue_match else None,
            date_source="issue_only",
            source_url=base_url,
        )
        if record:
            enrich_cn_detail(record)
            records.append(record)
        if len(records) >= limit:
            break
    return records


def fetch_journal(journal: dict[str, Any], url: str, limit: int) -> tuple[list[dict[str, Any]], str]:
    journal_id = journal["id"]
    if journal_id in AJCASS_JOURNAL_IDS:
        records = parse_ajcass_api(journal, limit)
        return records, "ajcass-api" if records else "ajcass-api-empty"
    if journal_id == "journal-379b4022ce":
        records = fetch_glsj(journal, limit)
        if records:
            return records, "glsj-archive-toc" if GLSJ_LAST_NOTE.startswith("archive-toc") else "glsj-ajax"
        if GLSJ_LAST_NOTE:
            return records, f"glsj-current-year-empty, {GLSJ_LAST_NOTE}"
        return records, "glsj-captcha-or-empty"

    # JQTE can consume the whole 75s per-journal outer deadline after
    # repeated HTTPS/proxy failures (four request routes times 25 seconds).
    # Bound each route to 12s; the workflow also disables retries/details
    # for this one source so a genuine failure is recorded and committed.
    html_text = fetch_text_partial(
        url, timeout=12 if journal_id == "journal-edcb877d78" else 25
    )
    if journal_id == "journal-679eaa2a0c":
        return parse_world_economy(html_text, journal, limit), "magtech-toc"
    if journal_id == "journal-edcb877d78":
        return parse_jqte(html_text, journal, limit), "jqte-toc"
    if journal_id == "journal-bf2aa9381f":
        return parse_cie(html_text, journal, limit), "cie-toc"
    return [], "unsupported"


def fetch_journal_with_retry(
    journal: dict[str, Any],
    url: str,
    limit: int,
    *,
    retries: int,
    retry_delay: float,
) -> tuple[list[dict[str, Any]], str, int]:
    """Retry transient official-site failures before declaring degradation."""
    last_error: Exception | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            records, mode = fetch_journal(journal, url, limit)
            # A consumed Management World time budget is not a verified empty
            # issue and retrying it would exceed the workflow's hard budget.
            return records, mode, attempt + 1
        except (TimeoutError, OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = exc
            transient = not isinstance(exc, urllib.error.HTTPError) or exc.code in {429, 500, 502, 503, 504}
            if not transient or attempt >= max(0, retries):
                raise
            time.sleep(max(0.0, retry_delay) * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("Chinese journal fetch returned no result")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journals", type=Path, default=DATA_DIR / "journals.yml")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--limit-per-journal", type=int, default=20)
    parser.add_argument("--detail-limit", type=int, default=0)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--tier", default=None)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-delay", type=float, default=1.0)
    args = parser.parse_args()
    global DETAIL_LIMIT, DETAIL_ATTEMPTED
    DETAIL_LIMIT = args.detail_limit
    DETAIL_ATTEMPTED = 0

    journals_by_id = {journal["id"]: journal for journal in filter_journals_by_tier(load_journals(args.journals), args.tier)}
    output = args.output or DATA_DIR / "raw" / "cn" / f"{today_str()}.json"
    records: list[dict[str, Any]] = []
    messages: list[str] = []
    journal_summaries: list[dict[str, Any]] = []

    selected_urls = CN_HOME_URLS
    if args.only:
        selected = set(args.only)
        selected_urls = {key: value for key, value in CN_HOME_URLS.items() if key in selected}

    for journal_id, url in selected_urls.items():
        journal = journals_by_id.get(journal_id)
        if not journal:
            messages.append(f"{journal_id}: missing journal config")
            journal_summaries.append(
                {
                    "journal_id": journal_id,
                    "journal": journal_id,
                    "ok": False,
                    "count": 0,
                    "mode": "missing-config",
                    "message": "missing journal config",
                }
            )
            continue
        try:
            fetched, mode, attempts = fetch_journal_with_retry(
                journal,
                url,
                args.limit_per_journal,
                retries=args.retries,
                retry_delay=args.retry_delay,
            )
            if journal_id in CN_HOME_URLS:
                before = len(fetched)
                original = list(fetched)
                fetched = keep_latest_issue(fetched)
                note = latest_issue_note(original, fetched)
                if note:
                    mode = f"{mode}, {note}"
            records.extend(fetched)
            messages.append(f"{journal_id}: {len(fetched)} via {mode}")
            source_ok = not (
                (journal_id == "journal-379b4022ce" and not fetched)
                # An empty JQTE TOC cannot establish an official member set.
                # Keep the local CNKI supplement eligible for retry.
                or (journal_id == "journal-edcb877d78" and not fetched)
            )
            journal_summaries.append(
                {
                    "journal_id": journal_id,
                    "journal": journal.get("title") or journal_id,
                    "ok": source_ok,
                    "count": len(fetched),
                    "mode": mode,
                    "attempts": attempts,
                    "retryable": not source_ok,
                    "fallback": "local-cnki-rss" if not source_ok else None,
                    "fallback_status": "queued" if not source_ok else "not-needed",
                    "message": (
                        (
                            "official JQTE returned no verifiable issue members"
                            if journal_id == "journal-edcb877d78"
                            else "CBPT/CNKI returned captcha or validation page; needs authenticated or alternate source"
                        )
                        if not source_ok
                        else f"{len(fetched)} via {mode}"
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            messages.append(f"{journal_id}: error {type(exc).__name__}: {exc}")
            journal_summaries.append(
                {
                    "journal_id": journal_id,
                    "journal": journal.get("title") or journal_id,
                    "ok": False,
                    "count": 0,
                    "mode": "error",
                    "attempts": args.retries + 1,
                    "retryable": True,
                    "fallback": "local-cnki-rss",
                    "fallback_status": "queued",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )

    write_json(output, records)
    write_json(output.with_suffix(".status.json"), journal_summaries)
    successful = sum(1 for item in journal_summaries if item.get("ok"))
    ok = all_journal_sources_ok(journal_summaries)
    if successful != len(journal_summaries):
        messages.insert(0, f"partial_success successful={successful}/{len(journal_summaries)}")
    if DETAIL_LIMIT:
        messages.append(f"detail-attempted={DETAIL_ATTEMPTED}/{DETAIL_LIMIT}")
    record_source("cn-journals", ok=ok, count=len(records), message="; ".join(messages))
    status = load_status()
    status.setdefault("source_groups", {})["cn-journals"] = {
        "ok": ok,
        "count": len(records),
        "updated_at": now(),
        "journals": journal_summaries,
    }
    save_status(status)
    print(f"wrote {len(records)} Chinese journal records to {output}")
    for message in messages:
        print(message)


if __name__ == "__main__":
    main()
