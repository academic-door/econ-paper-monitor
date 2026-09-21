"""Compare local coverage with Alohomora's public paper feed.

This is a local diagnostic sentinel, not an ingestion source.  It helps spot
candidate gaps in our journal monitor by comparing titles/DOIs against an
external public tracker.
"""

from __future__ import annotations

import json
import re
import ssl
import sys
import urllib.request
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from common import DATA_DIR, ROOT, normalize_doi, read_json, write_json, write_text
from status import record_source


ALO_API = "https://api3.alohomora.live"
OUT_PATH = ROOT / "local_admin" / "alohomora_coverage.json"
MD_OUT_PATH = ROOT / "local_admin" / "alohomora_coverage.md"
DATA_OUT_PATH = DATA_DIR / "external_sentinel_alohomora.json"
MISSING_CHINA_OUT_PATH = DATA_DIR / "missing_china_like.json"
SCOPE_POLICY_OUT_PATH = DATA_DIR / "journal_scope_policy.json"
CROSSREF_CACHE_PATH = DATA_DIR / "external_sentinel_crossref_cache.json"
OLD_BACKFLOW_DAYS = 14
CROSSREF_LOOKUP_LIMIT = 20

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


ALIASES = {
    "AE Latest": "Applied Economics",
    "AEJ: Applied Economics Forthcoming": "American Economic Journal: Applied Economics",
    "AEJ: Economic Policy Forthcoming": "American Economic Journal: Economic Policy",
    "AEJ: Macroeconomics Forthcoming": "American Economic Journal: Macroeconomics",
    "AER Forthcoming": "American Economic Review",
    "EL in Press": "Economics Letters",
    "EM in Press": "Economic Modelling",
    "EE in Press": "Energy Economics",
    "EEduR in Progress": "Economics of Education Review",
    "EI Early View": "Economic Inquiry",
    "EP in Progress": "Energy Policy",
    "FP in Progress": "Food Policy",
    "GEB in Press": "Games and Economic Behavior",
    "JBR in Progress": "Journal of Business Research",
    "JBES Latest": "Journal of Business and Economic Statistics",
    "JA&E in Press": "Journal of Accounting and Economics",
    "JAE Early View": "Journal of Applied Econometrics",
    "JBF in Press": "Journal of Banking and Finance",
    "JDE in Press": "Journal of Development Economics",
    "JEDC in Progress": "Journal of Economic Dynamics and Control",
    "JEBO in Press": "Journal of Economic Behavior and Organization",
    "JEEM in Press": "Journal of Environmental Economics and Management",
    "JFE in Progress": "Journal of Financial Economics",
    "JHE in Press": "Journal of Health Economics",
    "JASA Latest": "Journal of the American Statistical Association",
    "JCR Advance": "Journal of Consumer Research",
    "JDS Latest": "Journal of Data Science",
    "JEPsy in Progress": "Journal of Economic Psychology",
    "JES Early View": "Journal of Economic Surveys",
    "JFQA Accepted": "Journal of Financial and Quantitative Analysis",
    "JM Online First": "Journal of Marketing",
    "JOEG Advance": "Journal of Economic Geography",
    "JRU Online First": "Journal of Risk and Uncertainty",
    "J Macro in Progress": "Journal of Macroeconomics",
    "JPubE in Progress": "Journal of Public Economics",
    "JUE in Progress": "Journal of Urban Economics",
    "MS Advance": "Management Science",
    "OBES Early View": "Oxford Bulletin of Economics and Statistics",
    "RED in Progress": "Review of Economic Dynamics",
    "RES Advance": "Review of Economic Studies",
    "REStat Current": "Review of Economics and Statistics",
    "RIE Early View": "Review of International Economics",
    "RS Latest": "Regional Studies",
    "TAR Current": "The Accounting Review",
    "WE Early View": "The World Economy",
    "WD in Progress": "World Development",
}

ECON_SCOPE_EXTRA = {
    "Economics of Education Review",
    "Economic Inquiry",
    "Economic Policy",
    "Economics of Education Review",
    "Economic Modelling",
    "Energy Economics",
    "Energy Policy",
    "Oxford Bulletin of Economics and Statistics",
    "Review of International Economics",
}

OUT_OF_SCOPE_HINTS = {
    "Biometrika",
    "Ecological Economics",
    "Journal of Business Research",
    "Journal of Consumer Research",
    "Journal of Data Science",
    "Journal of Marketing",
    "Review of Accounting Studies",
    "The Accounting Review",
}

BROADER_BUT_RELEVANT_HINTS = {
    "Journal of Accounting and Economics",
    "Journal of Business and Economic Statistics",
    "Journal of Business Research",
    "Journal of Economic Geography",
    "Journal of Economic Psychology",
    "Journal of Economic Surveys",
    "Journal of Financial and Quantitative Analysis",
    "Journal of the American Statistical Association",
    "Regional Studies",
}


def fetch_json(url: str, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "econ-paper-monitor-audit/1.0"})
    with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_crossref_doi(doi: str, cache: dict[str, Any]) -> dict[str, Any]:
    doi = normalize_doi(doi) or ""
    if not doi:
        return {}
    if doi in cache:
        return cache[doi] if isinstance(cache[doi], dict) else {}
    url = f"https://api.crossref.org/works/{doi}"
    try:
        payload = fetch_json(url, timeout=5)
        message = payload.get("message") if isinstance(payload, dict) else {}
        cache[doi] = message if isinstance(message, dict) else {}
    except Exception as exc:  # noqa: BLE001 - external sentinel must not break the monitor.
        cache[doi] = {"_error": f"{type(exc).__name__}: {exc}"}
    return cache[doi] if isinstance(cache[doi], dict) else {}


def norm_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (value or "").lower())).strip()


def doi_from_url(value: str | None) -> str:
    match = re.search(r"(10\.\d{4,9}/[^\s\"<>]+)", value or "", re.I)
    if not match:
        return ""
    return re.split(r"[?&#]", match.group(1), maxsplit=1)[0].lower().rstrip(").,;")


def pii_from_value(value: str | None) -> str:
    match = re.search(r"(?:/pii/|\b)(S\d{15,}[A-Z0-9]*)\b", value or "", re.I)
    return match.group(1).upper() if match else ""


def parse_date_parts(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list) or not parts[0]:
        return None
    year = int(parts[0][0])
    month = int(parts[0][1]) if len(parts[0]) > 1 else 1
    day = int(parts[0][2]) if len(parts[0]) > 2 else 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def crossref_official_date(work: dict[str, Any]) -> tuple[str | None, str | None]:
    for key in ("published-online", "published-print", "published", "created", "deposited"):
        parsed = parse_date_parts(work.get(key))
        if parsed:
            return parsed, f"crossref_{key}"
    return None, None


def parse_any_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    months = {
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
    match = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})", text)
    if match and match.group(2).casefold() in months:
        return f"{int(match.group(3)):04d}-{months[match.group(2).casefold()]:02d}-{int(match.group(1)):02d}"
    match = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(20\d{2})", text)
    if match and match.group(1).casefold() in months:
        return f"{int(match.group(3)):04d}-{months[match.group(1).casefold()]:02d}-{int(match.group(2)):02d}"
    return None


def day_delta(later: str | None, earlier: str | None) -> int | None:
    if not later or not earlier:
        return None
    try:
        return (date.fromisoformat(later) - date.fromisoformat(earlier)).days
    except ValueError:
        return None


def public_date(record: dict[str, Any]) -> str | None:
    for key in ("available_online", "published_online", "accepted_date", "issue_date", "_daily_date"):
        parsed = parse_any_date(record.get(key))
        if parsed:
            return parsed
    return None


def local_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted((DATA_DIR / "daily").glob("*.json")):
        payload = read_json(path, [])
        if isinstance(payload, list):
            for record in payload:
                if isinstance(record, dict):
                    record["_daily_date"] = path.stem
                    records.append(record)
    return records


def seen_records() -> list[dict[str, Any]]:
    """Load records already discovered but intentionally excluded from public flow."""
    payload = read_json(DATA_DIR / "seen.json", {})
    if not isinstance(payload, dict):
        return []
    payload = payload.get("papers", payload)
    if not isinstance(payload, dict):
        return []
    records: list[dict[str, Any]] = []
    for record in payload.values():
        if not isinstance(record, dict):
            continue
        item = dict(record)
        first_seen = parse_any_date(item.get("first_seen"))
        if first_seen:
            item["_local_first_seen_date"] = first_seen
        records.append(item)
    return records


def local_indexes(
    records: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    titles = {norm_title(str(record.get("title") or "")): record for record in records if record.get("title")}
    dois = {normalize_doi(str(record.get("doi") or "")): record for record in records if normalize_doi(str(record.get("doi") or ""))}
    piis: dict[str, dict[str, Any]] = {}
    for record in records:
        doi = normalize_doi(doi_from_url(str(record.get("url") or "")))
        if doi:
            dois[doi] = record
        raw_data = record.get("raw_data") if isinstance(record.get("raw_data"), dict) else {}
        for value in (
            record.get("pii"),
            raw_data.get("pii"),
            record.get("url"),
            record.get("source_url"),
        ):
            pii = pii_from_value(str(value or ""))
            if pii:
                piis[pii] = record
    return titles, dois, piis


def is_china_like(record: dict[str, Any]) -> bool:
    blob = " ".join(str(record.get(key) or "") for key in ("title", "author", "journal"))
    return bool(re.search(r"\b(china|chinese|rmb|renminbi|hong kong|taiwan)\b", blob, re.I))


def likely_gap_reason(journal_label: str, mapped_journal: str, link: str | None) -> str:
    label = journal_label.lower()
    mapped = mapped_journal.lower()
    url = str(link or "").lower()
    if "early view" in label and ("wiley" in url or "10.1111/" in url):
        return "缺少 Wiley Early View 快速源或未把该期刊加入快速源。"
    if "forthcoming" in label and "aeaweb.org" in url:
        return "AEA forthcoming 页面更新快于 Crossref/RSS，需要把 forthcoming 作为快速源。"
    if "in press" in label or "in progress" in label:
        return "出版社 in press/in progress 页面比 Crossref 更早，需强化 Publisher TOC/PII/RSS 链路。"
    if "latest" in label and "tandfonline.com" in url:
        return "T&F latest 页面可能早于 Crossref，需要补 latest/current issue 抽检。"
    if mapped in {item.lower() for item in ECON_SCOPE_EXTRA}:
        return "属于经济学扩展清单，应决定是否纳入正式监测或外部哨兵。"
    return "需人工复核：可能是清单外来源，也可能是期刊别名未映射。"


def scope_bucket(mapped_journal: str, in_monitor_list: bool) -> str:
    if in_monitor_list:
        return "our_scope"
    if mapped_journal in ECON_SCOPE_EXTRA:
        return "econ_expand_candidate"
    if mapped_journal in BROADER_BUT_RELEVANT_HINTS:
        return "broader_relevant_candidate"
    if mapped_journal in OUT_OF_SCOPE_HINTS:
        return "broader_out_of_scope"
    return "unknown_scope"


def scope_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Return explicit scope counts for the external comparison radar.

    The external feed is deliberately broader than our formal journal list.
    Keep its raw candidate count for diagnosis, but expose formal-scope gaps as
    the only count that can be used as a monitor-quality alarm.
    """
    counts = {
        "formal_scope_missing_count": 0,
        "econ_expand_candidate_count": 0,
        "broader_relevant_candidate_count": 0,
        "out_of_scope_reference_count": 0,
        "unknown_scope_candidate_count": 0,
    }
    bucket_to_key = {
        "our_scope": "formal_scope_missing_count",
        "econ_expand_candidate": "econ_expand_candidate_count",
        "broader_relevant_candidate": "broader_relevant_candidate_count",
        "broader_out_of_scope": "out_of_scope_reference_count",
        "unknown_scope": "unknown_scope_candidate_count",
    }
    for item in items:
        key = bucket_to_key.get(str(item.get("scope_bucket") or ""))
        if key:
            counts[key] += 1
    return counts


def monitor_names() -> set[str]:
    path = DATA_DIR / "journals.yml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    names = set()
    for match in re.finditer(r"(?m)^\s*(?:-\s*)?(?:name|journal|title):\s*[\"']?([^\"'\n#]+)", text):
        name = norm_title(match.group(1).strip())
        if name:
            names.add(name)
    return names


def source_type(record: dict[str, Any]) -> str:
    if str(record.get("source_type") or "") == "journal":
        return "journal"
    source = str(record.get("source") or "").casefold()
    if source == "working_papers":
        return "working_paper"
    return str(record.get("source_type") or "unknown")


def recent_local_not_in_external(
    records: list[dict[str, Any]],
    remote_titles: set[str],
    remote_dois: set[str],
    known_names: set[str],
    limit: int = 80,
) -> list[dict[str, Any]]:
    local_only: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: item.get("_daily_date") or "", reverse=True):
        if source_type(record) != "journal":
            continue
        title_key = norm_title(str(record.get("title") or ""))
        doi_key = normalize_doi(str(record.get("doi") or "")) or normalize_doi(doi_from_url(str(record.get("url") or ""))) or ""
        if (title_key and title_key in remote_titles) or (doi_key and doi_key in remote_dois):
            continue
        mapped = str(record.get("journal") or "")
        local_only.append(
            {
                "date": record.get("_daily_date"),
                "official_date": public_date(record),
                "journal": mapped,
                "mapped_journal": mapped,
                "in_monitor_list": norm_title(mapped) in known_names,
                "scope_bucket": scope_bucket(mapped, norm_title(mapped) in known_names),
                "title": record.get("title"),
                "author": ", ".join(record.get("authors") or []) if isinstance(record.get("authors"), list) else record.get("authors"),
                "link": record.get("url") or (f"https://doi.org/{record.get('doi')}" if record.get("doi") else None),
                "doi": record.get("doi"),
                "date_source": record.get("date_source"),
                "reason": "我方已监测到，但 Alohomora 当前返回列表未匹配；说明对方不是完整标准答案。",
            }
        )
        if len(local_only) >= limit:
            break
    return local_only


def md_link(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "Untitled").replace("|", "\\|")
    link = str(item.get("link") or "")
    return f"[{title}]({link})" if link else title


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Alohomora 覆盖对比清单",
        "",
        "这份清单用于外部哨兵审计，不把 Alohomora 当作正式入库源。Alohomora 覆盖范围更广；我们的判断以经济学监测清单为准。",
        "",
        "## 总览",
        "",
        f"- Alohomora 当前返回：{summary['alo_count']} 篇",
        f"- 本地已匹配：{summary['matched_local_daily']} 篇",
        f"- 未匹配候选：{summary['possible_missing_count']} 篇",
        f"- 旧文回流：{summary['old_backflow_count']} 篇",
        f"- 我们当前清单内疑似漏抓：{summary['missing_in_monitor_list_count']} 篇",
        f"- 经济学扩展候选：{summary['missing_econ_expand_count']} 篇",
        f"- 更广但可能相关候选：{summary['missing_broader_relevant_count']} 篇",
        f"- 标题疑似中国相关：{summary['missing_china_like_count']} 篇",
        f"- 我方领先/对方未覆盖样本：{summary['local_not_in_external_count']} 篇",
        "",
        "## 外部发现：我们清单内疑似漏抓",
        "",
        "| 外部发现 | 官方日期 | 滞后天数 | 来源标签 | 映射期刊 | 论文 | 可能原因 |",
        "|---|---|---:|---|---|---|---|",
    ]
    for item in summary["missing_in_monitor_list"][:80]:
        lines.append(
            f"| {item.get('external_first_seen_date') or item.get('date') or ''} | {item.get('official_date') or ''} | "
            f"{item.get('lag_days') if item.get('lag_days') is not None else ''} | {item.get('journal') or ''} | "
            f"{item.get('mapped_journal') or ''} | {md_link(item)} | {item.get('reason') or ''} |"
        )
    if not summary["missing_in_monitor_list"]:
        lines.append("|  |  |  |  |  | 暂无 |  |")

    lines += [
        "",
        "## 外部发现：旧文回流",
        "",
        f"官方日期比外部发现日期早超过 {OLD_BACKFLOW_DAYS} 天的记录，不作为对方更快或我方漏抓。",
        "",
        "| 外部发现 | 官方日期 | 滞后天数 | 来源标签 | 映射期刊 | 论文 |",
        "|---|---|---:|---|---|---|",
    ]
    for item in summary["old_backflow"][:80]:
        lines.append(
            f"| {item.get('external_first_seen_date') or item.get('date') or ''} | {item.get('official_date') or ''} | "
            f"{item.get('lag_days') if item.get('lag_days') is not None else ''} | {item.get('journal') or ''} | "
            f"{item.get('mapped_journal') or ''} | {md_link(item)} |"
        )
    if not summary["old_backflow"]:
        lines.append("|  |  |  |  |  | 暂无 |")

    lines += [
        "",
        "## 经济学扩展候选",
        "",
        "这些不一定属于当前正式清单，但和经济学/应用经济学相近，可评估是否纳入。",
        "",
        "| 日期 | 来源标签 | 映射期刊 | 论文 | 建议 |",
        "|---|---|---|---|---|",
    ]
    for item in summary["missing_econ_expand"][:80]:
        lines.append(
            f"| {item.get('date') or ''} | {item.get('journal') or ''} | "
            f"{item.get('mapped_journal') or ''} | {md_link(item)} | {item.get('reason') or ''} |"
        )
    if not summary["missing_econ_expand"]:
        lines.append("|  |  |  | 暂无 |  |")

    lines += [
        "",
        "## 更广但可能相关候选",
        "",
        "这些来源不属于当前核心经济学期刊监测，但和商科、金融、会计、统计或应用研究相关。建议只做外部哨兵，不直接进入首页核心流。",
        "",
        "| 日期 | 来源标签 | 映射期刊 | 论文 | 建议 |",
        "|---|---|---|---|---|",
    ]
    for item in summary["missing_broader_relevant"][:80]:
        lines.append(
            f"| {item.get('date') or ''} | {item.get('journal') or ''} | "
            f"{item.get('mapped_journal') or ''} | {md_link(item)} | {item.get('reason') or ''} |"
        )
    if not summary["missing_broader_relevant"]:
        lines.append("|  |  |  | 暂无 |  |")

    lines += [
        "",
        "## 暂不建议纳入的更广覆盖",
        "",
        "Alohomora 覆盖更广，包含商科、会计、营销、生态、统计等。它们可作为灵感，但不应直接扩大我们的核心监测范围。",
        "",
        "| 来源标签 | 数量 | 说明 |",
        "|---|---:|---|",
    ]
    for journal, count in summary["broader_out_of_scope_by_journal"]:
        lines.append(f"| {journal} | {count} | 当前不属于已确认经济学核心清单 |")

    lines += [
        "",
        "## 我方领先：本地已监测，对方当前未匹配",
        "",
        "| 日期 | 官方日期 | 期刊 | 论文 | 说明 |",
        "|---|---|---|---|---|",
    ]
    for item in summary["local_not_in_external"][:80]:
        lines.append(
            f"| {item.get('date') or ''} | {item.get('official_date') or ''} | "
            f"{item.get('mapped_journal') or ''} | {md_link(item)} | {item.get('reason') or ''} |"
        )
    if not summary["local_not_in_external"]:
        lines.append("|  |  |  | 暂无 |  |")

    lines += [
        "",
        "## 标题疑似中国相关但未匹配",
        "",
        "| 外部发现 | 官方日期 | 滞后天数 | 来源标签 | 映射期刊 | 论文 | 范围 |",
        "|---|---|---:|---|---|---|---|",
    ]
    for item in summary["missing_china_like"][:50]:
        lines.append(
            f"| {item.get('external_first_seen_date') or item.get('date') or ''} | {item.get('official_date') or ''} | "
            f"{item.get('lag_days') if item.get('lag_days') is not None else ''} | {item.get('journal') or ''} | "
            f"{item.get('mapped_journal') or ''} | {md_link(item)} | {item.get('scope_bucket') or ''} |"
        )
    if not summary["missing_china_like"]:
        lines.append("|  |  |  |  |  | 暂无 |  |")

    return "\n".join(lines) + "\n"


def main() -> None:
    remote_payload = fetch_json(ALO_API)
    papers = [item for item in remote_payload.get("papers", []) if isinstance(item, dict)]
    records = local_records()
    # A record may be intentionally absent from the public daily flow (for
    # example AEA forthcoming). It is still a successful first discovery and
    # must count as matched in the external-sentinel audit.
    match_records = records + seen_records()
    titles, dois, piis = local_indexes(match_records)
    known_names = monitor_names()
    remote_titles = {norm_title(str(paper.get("title") or "")) for paper in papers if paper.get("title")}
    remote_dois = {normalize_doi(doi_from_url(str(paper.get("link") or ""))) for paper in papers if doi_from_url(str(paper.get("link") or ""))}
    remote_dois = {doi for doi in remote_dois if doi}
    crossref_cache = read_json(CROSSREF_CACHE_PATH, {})
    if not isinstance(crossref_cache, dict):
        crossref_cache = {}

    missing: list[dict[str, Any]] = []
    matched = 0
    matched_items: list[dict[str, Any]] = []
    crossref_lookups = 0
    for paper in papers:
        title = norm_title(str(paper.get("title") or ""))
        link = str(paper.get("link") or "")
        doi = normalize_doi(doi_from_url(link)) or ""
        pii = pii_from_value(link)
        local_match = titles.get(title) or (dois.get(doi) if doi else None) or (piis.get(pii) if pii else None)
        if local_match:
            matched += 1
            local_date = public_date(local_match)
            external_first_seen_date = parse_any_date(paper.get("fdate")) or parse_any_date(paper.get("date"))
            matched_items.append(
                {
                    "title": paper.get("title"),
                    "link": paper.get("link"),
                    "journal": paper.get("journal"),
                    "mapped_journal": ALIASES.get(str(paper.get("journal") or ""), str(paper.get("journal") or "")),
                    "external_first_seen_date": external_first_seen_date,
                    "local_first_seen_date": local_match.get("_daily_date") or local_match.get("_local_first_seen_date"),
                    "official_date": local_date,
                    "lead_days_local_minus_external": day_delta(str(local_match.get("_daily_date") or ""), external_first_seen_date),
                }
            )
            continue
        journal_label = str(paper.get("journal") or "")
        mapped = ALIASES.get(journal_label, journal_label)
        bucket = scope_bucket(mapped, norm_title(mapped) in known_names)
        external_first_seen_date = parse_any_date(paper.get("fdate")) or parse_any_date(paper.get("date"))
        official_date = None
        official_date_source = None
        should_lookup_date = is_china_like(paper) or bucket in {
            "our_scope",
            "econ_expand_candidate",
            "broader_relevant_candidate",
        }
        if doi and should_lookup_date and crossref_lookups < CROSSREF_LOOKUP_LIMIT:
            crossref_lookups += 1
            official_date, official_date_source = crossref_official_date(fetch_crossref_doi(doi, crossref_cache))
        lag_days = day_delta(external_first_seen_date, official_date)
        timeliness_bucket = "old_backflow" if lag_days is not None and lag_days > OLD_BACKFLOW_DAYS else "current_or_unknown"
        missing.append(
            {
                "date": paper.get("date"),
                "first_seen_at": paper.get("fdate"),
                "external_first_seen_date": external_first_seen_date,
                "official_date": official_date,
                "official_date_source": official_date_source,
                "lag_days": lag_days,
                "timeliness_bucket": timeliness_bucket,
                "journal": journal_label,
                "mapped_journal": mapped,
                "in_monitor_list": norm_title(mapped) in known_names,
                "scope_bucket": bucket,
                "china_like": is_china_like(paper),
                "title": paper.get("title"),
                "author": paper.get("author"),
                "link": paper.get("link"),
                "tier": paper.get("tier"),
                "reason": likely_gap_reason(journal_label, mapped, paper.get("link")),
            }
        )

    current_missing = [item for item in missing if item["timeliness_bucket"] != "old_backflow"]
    old_backflow = [item for item in missing if item["timeliness_bucket"] == "old_backflow"]
    missing_in_monitor = [item for item in current_missing if item["scope_bucket"] == "our_scope"]
    missing_econ_expand = [item for item in current_missing if item["scope_bucket"] == "econ_expand_candidate"]
    missing_broader_relevant = [item for item in current_missing if item["scope_bucket"] == "broader_relevant_candidate"]
    broader_out = [item for item in missing if item["scope_bucket"] == "broader_out_of_scope"]
    current_scope_counts = scope_counts(current_missing)
    local_not_in_external = recent_local_not_in_external(records, remote_titles, remote_dois, known_names)
    scope_policy = {
        "formal_monitor_count": len(known_names),
        "formal_monitor_names": sorted(known_names),
        "econ_expand_candidate_names": sorted(ECON_SCOPE_EXTRA),
        "broader_relevant_candidate_names": sorted(BROADER_BUT_RELEVANT_HINTS),
        "reference_only_names": sorted(OUT_OF_SCOPE_HINTS),
        "old_backflow_days": OLD_BACKFLOW_DAYS,
    }
    summary = {
        "source": ALO_API,
        "alo_count": len(papers),
        "matched_local_daily": matched,
        "possible_missing_count": len(missing),
        "current_possible_missing_count": len(current_missing),
        **current_scope_counts,
        "old_backflow_count": len(old_backflow),
        "missing_china_like_count": sum(1 for item in current_missing if item["china_like"]),
        "missing_in_monitor_list_count": len(missing_in_monitor),
        "missing_econ_expand_count": len(missing_econ_expand),
        "missing_broader_relevant_count": len(missing_broader_relevant),
        "broader_out_of_scope_count": len(broader_out),
        "counts": {
            "current_possible_missing": len(current_missing),
            "formal_scope_missing": current_scope_counts["formal_scope_missing_count"],
            "econ_expand_candidates": current_scope_counts["econ_expand_candidate_count"],
            "broader_relevant_candidates": current_scope_counts["broader_relevant_candidate_count"],
            "out_of_scope_references": current_scope_counts["out_of_scope_reference_count"],
            "unknown_scope_candidates": current_scope_counts["unknown_scope_candidate_count"],
        },
        "local_not_in_external_count": len(local_not_in_external),
        "missing_by_journal": Counter(item["journal"] for item in missing).most_common(50),
        "missing_china_like": [item for item in current_missing if item["china_like"]][:50],
        "missing_in_monitor_list": missing_in_monitor[:120],
        "missing_econ_expand": missing_econ_expand[:120],
        "missing_broader_relevant": missing_broader_relevant[:120],
        "old_backflow": old_backflow[:120],
        "local_not_in_external": local_not_in_external[:120],
        "matched_sample": matched_items[:80],
        "scope_policy": scope_policy,
        "crossref_lookup_limit": CROSSREF_LOOKUP_LIMIT,
        "crossref_lookups": crossref_lookups,
        "broader_out_of_scope_by_journal": Counter(item["journal"] for item in broader_out).most_common(50),
        "missing_sample": missing[:50],
    }
    write_json(OUT_PATH, summary)
    write_json(DATA_OUT_PATH, summary)
    write_json(SCOPE_POLICY_OUT_PATH, scope_policy)
    write_json(CROSSREF_CACHE_PATH, crossref_cache)
    write_json(
        MISSING_CHINA_OUT_PATH,
        {
            "source": ALO_API,
            "count": summary["missing_china_like_count"],
            "high_priority_count": sum(
                1
                for item in summary["missing_china_like"]
                if item.get("scope_bucket") in {"our_scope", "econ_expand_candidate", "broader_relevant_candidate"}
            ),
            "items": summary["missing_china_like"],
        },
    )
    write_text(MD_OUT_PATH, render_markdown(summary))
    record_source(
        "external-sentinel:alohomora",
        ok=True,
        count=summary["possible_missing_count"],
        message=(
            f"alo={summary['alo_count']} matched={summary['matched_local_daily']} "
            f"in_monitor={summary['missing_in_monitor_list_count']} "
            f"china_like={summary['missing_china_like_count']} "
            f"old_backflow={summary['old_backflow_count']} local_only={summary['local_not_in_external_count']}"
        ),
    )
    print(
        "alohomora coverage: "
        f"alo={summary['alo_count']} matched={summary['matched_local_daily']} "
        f"missing={summary['possible_missing_count']} "
        f"china_like={summary['missing_china_like_count']} "
        f"in_monitor_list={summary['missing_in_monitor_list_count']} "
        f"econ_expand={summary['missing_econ_expand_count']} "
        f"broader_relevant={summary['missing_broader_relevant_count']} "
        f"broader_out={summary['broader_out_of_scope_count']}"
    )
    print(OUT_PATH)
    print(MD_OUT_PATH)


if __name__ == "__main__":
    main()
