"""Generate the Daily Door vNext page from the canonical daily archive."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

from date_provenance import provenance_text
from common import normalize_doi
from display_contract import display_titles

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DAILY_DIR = DATA_DIR / "daily"
DEFAULT_TEMPLATE = ROOT / "scripts" / "templates" / "daily_vnext.html"
DEFAULT_OUTPUT = ROOT / "docs" / "daily-vnext" / "index.html"
DEFAULT_REPORT = DATA_DIR / "daily_vnext_build_report.json"
BEIJING = ZoneInfo("Asia/Shanghai")

TOPIC_LABELS = {
    "applied_empirical": "应用实证",
    "public_political": "公共与政治经济学",
    "macro_monetary": "宏观与货币经济学",
    "labor": "劳动经济学",
    "macro": "宏观经济学",
    "macroeconomics": "宏观经济学",
    "finance": "金融",
    "trade": "国际贸易",
    "international_trade": "国际贸易",
    "development": "发展经济学",
    "labor": "劳动经济学",
    "labour": "劳动经济学",
    "behavior": "行为与组织",
    "behavioral": "行为与组织",
    "public": "公共与政治经济学",
    "public_economics": "公共与政治经济学",
    "political_economy": "公共与政治经济学",
    "agriculture_environment_resource": "农业、环境与资源经济学",
    "agriculture": "农业与食品",
    "environment": "环境与气候",
    "urban": "城市与区域",
    "inequality": "不平等",
    "education": "教育",
    "health": "健康经济学",
    "environment_climate": "环境与气候",
    "theory_game": "理论与博弈",
    "industrial_organization": "产业组织",
    "econometrics": "计量经济学",
    "behavior_organization": "行为与组织",
    "china": "中国研究",
    "chinese": "中国研究",
    "economic_history": "经济史",
    "environmental": "环境与气候",
    "experimental": "实验经济学",
    "game_theory": "理论与博弈",
    "general": "综合经济学",
    "international": "国际经济学",
    "law_comparative": "比较法与经济",
    "microeconomics": "微观经济学",
    "population": "人口经济学",
    "theory": "理论经济学",
}


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def parse_dt(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(BEIJING)


def first_seen(record: dict) -> datetime | None:
    return parse_dt(record.get("first_seen_at") or record.get("detected_at") or record.get("detected"))


def official_date(record: dict) -> str:
    return str(record.get("official_date") or record.get("available_online") or record.get("published_online") or record.get("issue_date") or "").strip()


def is_stale_catalogue_backfill(record: dict, date_value: str, days: int = 3) -> bool:
    """Keep clearly old catalogue records out of the Today discovery stream."""
    value = official_date(record)
    if not value:
        return False
    try:
        official = datetime.fromisoformat(value[:10]).date()
        target = datetime.fromisoformat(date_value).date()
    except ValueError:
        return False
    return official < target - timedelta(days=max(days, 1) - 1)


def source_name(record: dict) -> str:
    return str(record.get("journal") or record.get("source") or "未知来源").strip()


def content_type(record: dict) -> str:
    raw_type = str(record.get("source_type") or "").lower().strip()
    source = source_name(record).lower()
    source_id = str(record.get("source") or "").lower()
    if raw_type == "journal":
        return "journal"
    if "voxeu" in source or "cepr columns" in source or raw_type == "policy_commentary":
        return "column"
    if raw_type in {"working_paper", "policy_paper", "aggregator", "preprint"} or source_id == "working_papers":
        return "working"
    return "journal"


def type_label(value: str) -> str:
    return {"journal": "期刊论文", "working": "工作论文", "column": "研究专栏"}.get(value, "研究内容")


def is_china_related(record: dict) -> bool:
    return str(record.get("china_relevance_status") or "").lower() in {"confirmed", "likely", "true", "yes", "1"}


def topic_values(record: dict) -> list[str]:
    raw = record.get("fields") or record.get("topics") or record.get("ai_tags") or []
    if isinstance(raw, str):
        raw = [raw]
    output: list[str] = []
    for item in raw:
        key = str(item or "").strip().lower()
        label = TOPIC_LABELS.get(key)
        if label and label not in output:
            output.append(label)
    return output[:3]


def author_values(record: dict) -> list[str]:
    raw = record.get("authors") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def search_text(record: dict, labels: list[str]) -> str:
    values = [record.get(key) for key in ("title", "title_zh", "journal", "source", "doi", "url")]
    values.extend(author_values(record))
    values.extend(labels)
    values.extend(str(item) for item in (record.get("fields") or []) if item)
    values.append("true" if is_china_related(record) else "false")
    return " ".join(str(value or "") for value in values).lower()


def topic_keys(record: dict) -> list[str]:
    raw = record.get("fields") or record.get("topics") or record.get("ai_tags") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(item or "").strip().lower() for item in raw if str(item or "").strip()]


def warn_unknown_topics(records: list[dict]) -> list[str]:
    unknown = sorted({key for record in records for key in topic_keys(record) if key not in TOPIC_LABELS})
    for key in unknown:
        print(f"WARNING: unknown Daily vNext topic code: {key}", file=sys.stderr)
    return unknown


def compact_source(record: dict) -> str:
    source = source_name(record).strip()
    lower = source.lower()
    if "voxeu" in lower or "columns" in lower:
        return "VOXEU"
    if "cepr" in lower:
        return "CEPR"
    return re.sub(r"[^A-Za-z0-9& -]", "", source).strip().upper()[:28]


def hero_nodes_markup(records: list[dict]) -> str:
    candidates: list[dict] = []
    for record in records:
        seen = first_seen(record)
        source = compact_source(record)
        if seen and source:
            candidates.append({"time": seen.strftime("%H:%M"), "source": source, "kind": content_type(record)})
    if not candidates:
        return '<div class="hero-data-texture" aria-hidden="true" hidden></div>'
    selected = [candidates[0]]
    for candidate in candidates[1:]:
        if (candidate["time"], candidate["source"]) != (selected[0]["time"], selected[0]["source"]):
            selected.append(candidate)
            break
    if len(selected) == 1:
        for candidate in candidates[1:]:
            if candidate["source"] != selected[0]["source"]:
                selected.append(candidate)
                break
    slots = [("top:8%;left:4%", ""), ("top:18%;right:4%", "")]
    nodes = "".join(
        f'<span class="hero-data-node" style="{slots[index][0]}">{esc(item["time"])} / {esc(item["source"])}</span>'
        for index, item in enumerate(selected[:2])
    )
    return f'<div class="hero-data-texture" aria-hidden="true">{nodes}</div>'


def load_records(date_value: str) -> tuple[list[dict], int]:
    path = DAILY_DIR / f"{date_value}.json"
    if not path.exists():
        raise FileNotFoundError(f"canonical daily file is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"canonical daily JSON is invalid: {path}: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError(f"canonical daily JSON must be a list: {path}")
    archive_records = [dict(item) for item in payload if isinstance(item, dict)]
    if len(archive_records) != len(payload):
        raise ValueError(f"canonical daily JSON contains non-object records: {path}")
    invalid = [index for index, record in enumerate(archive_records) if not (record.get("title") and (record.get("url") or record.get("source_url")) and first_seen(record))]
    if invalid:
        raise ValueError(f"canonical daily records failed field validation at indexes: {invalid[:10]}")
    records = [
        record
        for record in archive_records
        if (
            first_seen(record)
            and first_seen(record).strftime("%Y-%m-%d") == date_value
            and not is_stale_catalogue_backfill(record, date_value)
        )
    ]
    records.sort(key=lambda item: first_seen(item) or datetime.min.replace(tzinfo=BEIJING), reverse=True)
    return records, len(archive_records)


class GeneratedPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paper_entries = 0
        self.has_html = False
        self.has_body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "html":
            self.has_html = True
        elif tag == "body":
            self.has_body = True
        elif any(name == "class" and value and "paper-entry" in value.split() for name, value in attrs):
            self.paper_entries += 1


def validate_generated_page(document: str, expected_count: int, output_path: Path) -> None:
    parser = GeneratedPageParser()
    parser.feed(document)
    parser.close()
    if not parser.has_html or not parser.has_body:
        raise ValueError("generated Daily vNext HTML is incomplete")
    if parser.paper_entries != expected_count:
        raise ValueError(f"generated paper count mismatch: html={parser.paper_entries}, data={expected_count}")
    script = """const fs = require('fs');
const html = fs.readFileSync(process.argv[1], 'utf8');
const scripts = [...html.matchAll(/<script[^>]*>([\\s\\S]*?)<\\/script>/g)].map(m => m[1]).filter(Boolean);
scripts.forEach(source => new Function(source));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".html", dir=output_path.parent, encoding="utf-8", delete=False) as html_file:
        html_file.write(document)
        html_path = Path(html_file.name)
    try:
        result = subprocess.run(["node", "-e", script, str(html_path)], capture_output=True, text=True, check=False)
        if result.returncode:
            raise ValueError(f"generated Daily vNext JavaScript validation failed: {result.stderr.strip()}")
    finally:
        html_path.unlink(missing_ok=True)


def detail_key(record: dict) -> str:
    """Return the stable public key used by static detail routes (mirrors render_site)."""
    canonical_key = str(record.get("canonical_detail_key") or record.get("detail_key") or "").strip()
    if re.search(r"-[0-9a-f]{12}$", canonical_key, re.IGNORECASE):
        return canonical_key
    title = str(record.get("title") or "paper").casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", title).strip("-") or "paper"
    slug = slug[:88].rstrip("-")
    identity = normalize_doi(record.get("doi")) or str(record.get("url") or "")
    if not identity:
        identity = f"{record.get('title') or ''}|{record.get('journal') or ''}"
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


def paper_markup(record: dict, date_value: str, previous_date: str | None, root_output: bool = True) -> tuple[str, str]:
    kind = content_type(record)
    labels = topic_values(record)
    china = is_china_related(record)
    seen = first_seen(record)
    seen_date = seen.strftime("%Y-%m-%d") if seen else date_value
    seen_time = seen.strftime("%H:%M") if seen else "--:--"
    date_markup = f'<span>{esc(seen_date)}</span>' if previous_date != seen_date else ""
    title_primary, title_secondary = display_titles(record)
    title_primary = title_primary or "未命名记录"
    authors = author_values(record)
    author_markup = f'          <p class="authors">{esc(", ".join(authors))}</p>\n' if authors else ""
    topic_markup = "".join(f'<span class="tag">{esc(label)}</span>' for label in labels)
    if china:
        topic_markup += '<span class="tag tag-china">与中国相关</span>'
    tags_markup = f'<div class="tags">{topic_markup}</div>' if topic_markup else ""
    original = str(record.get("url") or record.get("source_url") or "#")
    detail_href = f"{'' if root_output else '../'}paper/{detail_key(record)}/"
    doi = str(record.get("doi") or "").strip()
    official = official_date(record)
    official_markup = esc(provenance_text(record, official))
    detail_kind = type_label(kind)
    if kind == "working" and "cepr" in source_name(record).lower():
        detail_kind += " · CEPR Discussion Paper"
    elif kind == "working" and "nber" in source_name(record).lower():
        detail_kind += " · NBER Working Paper"
    details = f"{official_markup} · 本站首次发现：{esc(seen_date)} {esc(seen_time)} 北京时间"
    if doi:
        details += f" · DOI：{esc(doi)}"
    markup = f'''      <article class="paper-entry" data-motion="paper" data-paper-id="{esc(record.get("id"))}" data-kind="{kind}" data-china="{'true' if china else 'false'}" data-search="{esc(search_text(record, labels))}">
        <div class="paper-time"><time>{esc(seen_time)}</time>{date_markup}</div><div class="timeline-rail"><span class="timeline-dot" aria-hidden="true"></span></div>
        <div class="paper-body">
          <div class="paper-kicker"><span>{esc(detail_kind)}</span><span>{esc(source_name(record))}</span></div>
          <h3><a href="{esc(detail_href)}">{esc(title_primary)}</a></h3>
          {f'<p class="english-title">{esc(title_secondary)}</p>' if title_secondary else ''}
{author_markup}          <div class="paper-foot">{tags_markup}<a class="read-link" href="{esc(original)}" target="_blank" rel="noreferrer">打开原文 <span aria-hidden="true">↗</span></a></div>
          <details class="paper-details"><summary>查看来源与日期</summary><p>{details}</p></details><span class="paper-divider" aria-hidden="true"></span>
        </div>
      </article>'''
    return markup, seen_date


def replace_section(document: str, class_name: str, replacement: str) -> str:
    pattern = rf'    <section class="{re.escape(class_name)}"[^>]*>.*?</section>'
    updated, count = re.subn(pattern, replacement, document, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"template section not found: {class_name}")
    return updated


def build(date_value: str, template_path: Path, output_path: Path, report_path: Path, dry_run: bool = False) -> dict:
    records, archive_file_count = load_records(date_value)
    unknown_topics = warn_unknown_topics(records)
    counts = Counter(content_type(record) for record in records)
    china_count = sum(is_china_related(record) for record in records)
    source_counts = Counter(source_name(record) for record in records)
    latest = first_seen(records[0]) if records else None
    latest_label = latest.strftime("%H:%M") if latest else "暂无更新"
    official_today = sum(official_date(record)[:10] == date_value for record in records if official_date(record))
    report = {
        "date": date_value,
        "canonical_file": str(DAILY_DIR / f"{date_value}.json"),
        "archive_file_count": archive_file_count,
        "excluded_archive_records": archive_file_count - len(records),
        "final_render_count": len(records),
        "today_first_seen_count": len(records),
        "today_official_date_count": official_today,
        "category_counts": dict(counts),
        "source_counts": dict(source_counts),
        "china_related_count": china_count,
        "latest_first_seen_beijing": latest.isoformat() if latest else None,
        "unknown_topic_codes": unknown_topics,
    }
    if not dry_run:
        document = template_path.read_text(encoding="utf-8")
        root_output = output_path.resolve() == (ROOT / "docs" / "index.html").resolve()
        site_prefix = "./" if root_output else "../"
        document = document.replace('href="../', f'href="{site_prefix}')
        document = document.replace('src="../', f'src="{site_prefix}')
        document = re.sub(
            r'<link rel="canonical" href="[^"]+">',
            f'<link rel="canonical" href="https://academic-door.github.io/econ-paper-monitor/{"" if root_output else "daily-vnext/"}">',
            document,
            count=1,
        )
        document = re.sub(r'DAILY DOOR / [^<]+', f'DAILY DOOR / {esc(date_value)}', document, count=1)
        document = re.sub(r'<p class="hero-lede"[^>]*>.*?</p>', '<p class="hero-lede">每日追踪重点经济学期刊、工作论文与研究专栏，沿着时间流发现值得打开的研究。</p>', document, count=1, flags=re.S)
        document = re.sub(r'<div class="hero-data-texture"[^>]*>.*?</div>', hero_nodes_markup(records), document, count=1, flags=re.S)
        document = re.sub(r'<div class="hero-total"[^>]*>.*?</div>', f'<div class="hero-total" data-count="{len(records)}">{len(records)}</div>', document, count=1, flags=re.S)
        hero_note = f'{counts["journal"]} 篇期刊论文 · {counts["working"]} 篇工作论文 · {counts["column"]} 篇研究专栏'
        document = re.sub(r'<div class="hero-note">.*?</div>', f'<div class="hero-note">{esc(hero_note)}</div>', document, count=1, flags=re.S)
        overview = f'''    <section class="overview" aria-label="今日概览"><div class="overview-item"><strong>{counts["journal"]}</strong><span>期刊论文</span></div><div class="overview-item"><strong>{counts["working"]}</strong><span>工作论文</span></div><div class="overview-item"><strong>{counts["column"]}</strong><span>研究专栏</span></div><div class="overview-item"><strong>{china_count}</strong><span>与中国相关</span></div></section>'''
        document = replace_section(document, "overview", overview)
        flow_head = f'''    <section class="flow-head"><div><h2>今日研究时间流</h2><p class="result-status" data-result-status aria-live="polite">今日共 {len(records)} 项研究内容</p></div><p>更新于 {esc(latest_label)} 北京时间 · 按本站首次发现时间</p></section>'''
        document = replace_section(document, "flow-head", flow_head)
        filters = '''    <section class="filters" aria-label="论文筛选"><button class="filter" type="button" data-filter="all" aria-pressed="true">全部</button><button class="filter" type="button" data-filter="journal" aria-pressed="false">期刊论文</button><button class="filter" type="button" data-filter="working" aria-pressed="false">工作论文</button><button class="filter" type="button" data-filter="column" aria-pressed="false">研究专栏</button><button class="filter" type="button" data-filter="china" aria-pressed="false">中国研究</button><input class="search" type="search" aria-label="搜索标题、作者、期刊、DOI或主题" placeholder="搜索标题、作者、期刊、DOI 或主题"></section>'''
        document = replace_section(document, "filters", filters)
        paper_parts: list[str] = []
        previous_date: str | None = None
        for record in records:
            part, current_date = paper_markup(record, date_value, previous_date, root_output)
            paper_parts.append(part)
            previous_date = current_date
        paper_html = "\n".join(paper_parts)
        empty_state = (
            '<div class="empty-state" data-empty-state><h3>今日暂无新发现</h3>'
            '<p>监测任务已完成，当前没有符合今日日期的新记录。历史论文仍可在全站监测与归档中查看。</p></div>'
            if not records
            else '<div class="empty-state" data-empty-state hidden><h3>没有找到匹配的论文</h3><p>尝试更换关键词，或切换论文类型。</p><button class="clear-filters" type="button" data-clear>清除筛选</button></div>'
        )
        timeline = f'''    <section class="timeline" aria-live="polite">{paper_html}{empty_state}</section>'''
        document = replace_section(document, "timeline", timeline)
        document = re.sub(r'<div class="result-status"[^>]*>.*?</div>', f'<div class="result-status" data-result-status aria-live="polite">今日共 {len(records)} 项研究内容</div>', document, count=1, flags=re.S)
        search_lengths = [len(value) for value in re.findall(r'data-search="([^"]*)"', document)]
        report["data_search"] = {
            "html_bytes": len(document.encode("utf-8")),
            "max_length": max(search_lengths, default=0),
            "average_length": round(sum(search_lengths) / len(search_lengths), 2) if search_lengths else 0,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        validate_generated_page(document, len(records), output_path)
        with tempfile.NamedTemporaryFile("w", suffix=".html", dir=output_path.parent, encoding="utf-8", delete=False) as temp_file:
            temp_file.write(document)
            temp_path = Path(temp_file.name)
        try:
            os.replace(temp_path, output_path)
        finally:
            temp_path.unlink(missing_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.buffer.write((json.dumps(report, ensure_ascii=False) + "\n").encode("utf-8"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=datetime.now(BEIJING).strftime("%Y-%m-%d"))
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    build(args.date, args.template, args.output, args.report, args.dry_run)


if __name__ == "__main__":
    main()
