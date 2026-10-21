"""Remove obvious Chinese-journal website news/navigation noise."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from common import DATA_DIR, read_json, today_str, write_json
from status import record_source


CN_JOURNALS = {
    "journal-379b4022ce",
    "journal-edcb877d78",
    "journal-bf2aa9381f",
    "journal-f69300dae2",
    "journal-679eaa2a0c",
    "journal-ba9f46c919",
}

CN_JOURNAL_NAMES = {"管理世界", "数量经济技术经济研究", "中国工业经济", "中国农村经济", "世界经济", "经济研究"}

ARTICLE_URL_PATTERNS = (
    "reader/view_abstract.aspx?file_no=",
    "view_abstract.aspx?file_no=",
    "sjjj.magtech.com.cn/CN/Y",
    "ciejournal.ajcass.com/Magazine/Show?id=",
    "paperDigest.aspx?paperID=",
    "ajcass.com/#/detail",
    "ajcass.com/#/enDetail",
)

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
    "欢迎订阅",
    "征订",
    "目录",
    "编委",
    "启事",
)

NOISE_URL = (
    "CommonBlock/SiteContentList",
    "mp.weixin.qq.com",
    "nssd.cn",
    "find.cb.cnki.net",
    ".doc",
    ".docx",
)


def is_article_url(url: str) -> bool:
    # CNKI RSS uses an article-detail route, not a journal's legacy ToC URL.
    # Match the actual host/path, not a substring that a navigation/spoof URL
    # could include in its query string. CNKI online-first records otherwise
    # get wrongly removed from the durable seen catalogue on full Update.
    parsed = urlsplit(url.strip())
    if (
        parsed.scheme in {"http", "https"}
        and parsed.hostname == "kns.cnki.net"
        and parsed.path.rstrip("/").casefold() == "/kcms2/article/abstract"
    ):
        return True
    # SJJJ first-party online-first articles use a different canonical Magtech
    # detail route from printed-issue /CN/Y... pages. Only trust the exact host
    # and article path; a publisher URL embedded in an unrelated query is noise.
    if (
        parsed.scheme == "https"
        and parsed.hostname == "sjjj.magtech.com.cn"
        and re.fullmatch(r"/CN/abstract/abstract\d+\.shtml", parsed.path, re.I)
        and not parsed.query
        and not parsed.fragment
    ):
        return True
    return any(pattern.lower() in url.lower() for pattern in ARTICLE_URL_PATTERNS)


def is_noise_record(record: dict[str, Any]) -> bool:
    if record.get("journal_id") not in CN_JOURNALS:
        return False
    title = str(record.get("title") or "")
    url = str(record.get("url") or "")
    if any(noise in title for noise in NOISE_TEXT):
        return True
    if any(noise.lower() in url.lower() for noise in NOISE_URL):
        return True
    if "#" in url and "ajcass.com/#/" not in url:
        return True
    if is_article_url(url):
        return False
    if record.get("source_issue") and record.get("url"):
        return False
    return not (record.get("available_online") or record.get("published_online"))


def issue_key(source_issue: str | None) -> tuple[int, int] | None:
    text = str(source_issue or "")
    patterns = [
        r"(20\d{2})\s*年\s*,?\s*第?\s*(\d{1,2})\s*期",
        r"(20\d{2})\s*年第\s*(\d{1,2})\s*期",
        r"(20\d{2})\s*年\s*(\d{1,2})\s*期",
        r"(20\d{2})\s*年\s*(\d{1,2})\s*月",
        r"(20\d{2})\s*-\s*(\d{1,2})",
        r"(20\d{2})\s*/\s*(\d{1,2})",
        r"(20\d{2})\s*年\s*,?\s*第?\s*(\d{1,2})\s*期",
        r"(20\d{2})\s*年\s*(\d{1,2})\s*期",
        r"(20\d{2})\s*,\s*\d+\s*\(\s*(\d{1,2})\s*\)",
        r"(20\d{2})\s*,\s*\(\s*(\d{1,2})\s*\)",
        r"(20\d{2})\s*年第\s*(\d{1,2})\s*期",
        r"(20\d{2})年(\d{1,2})期",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def latest_issue_filter(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_journal: dict[str, tuple[int, int]] = {}
    for record in records:
        journal_id = record.get("journal_id")
        if journal_id not in CN_JOURNALS:
            continue
        key = issue_key(record.get("source_issue"))
        if key is None:
            continue
        latest_by_journal[journal_id] = max(latest_by_journal.get(journal_id, key), key)
    if not latest_by_journal:
        return records
    current_year = int(today_str()[:4])
    kept = []
    for record in records:
        journal_id = record.get("journal_id")
        key = issue_key(record.get("source_issue"))
        if journal_id in latest_by_journal and key is not None and key != latest_by_journal[journal_id]:
            continue
        if journal_id in latest_by_journal and key is not None and key[0] < current_year:
            continue
        kept.append(record)
    return kept


def clean_daily(path: Path) -> int:
    records = read_json(path, [])
    kept = [record for record in records if not is_noise_record(record)]
    kept = latest_issue_filter(kept)
    removed = len(records) - len(kept)
    if removed:
        write_json(path, kept)
    return removed


def clean_seen(path: Path) -> int:
    seen = read_json(path, {"papers": {}})
    papers = seen.setdefault("papers", {})
    removed = []
    for key, value in list(papers.items()):
        title = str(value.get("title") or "")
        journal = str(value.get("journal") or "")
        url = str(value.get("url") or "")
        if journal not in CN_JOURNAL_NAMES:
            continue
        if any(noise in title for noise in NOISE_TEXT):
            removed.append(key)
            continue
        if url and not is_article_url(url):
            removed.append(key)
    for key in removed:
        papers.pop(key, None)
    if removed:
        write_json(path, seen)
    return len(removed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--seen", type=Path, default=DATA_DIR / "seen.json")
    args = parser.parse_args()

    daily_removed = 0
    for path in args.daily_dir.glob("*.json"):
        daily_removed += clean_daily(path)
    seen_removed = clean_seen(args.seen)
    record_source("clean-cn-noise", ok=True, count=daily_removed + seen_removed, message=f"daily_removed={daily_removed} seen_removed={seen_removed}")
    print(f"removed daily={daily_removed} seen={seen_removed}")


if __name__ == "__main__":
    main()
