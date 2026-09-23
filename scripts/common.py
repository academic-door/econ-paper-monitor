"""Shared helpers for the econ-paper-monitor MVP pipeline."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import ssl
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

USER_AGENT = "AcademicDoorPaperMonitor/1.0 (https://github.com/academic-door/econ-paper-monitor)"
BEIJING_TZ = timezone(timedelta(hours=8))
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})


def beijing_today() -> date:
    override = os.environ.get("MONITOR_RUN_DATE", "").strip()
    if override:
        try:
            parsed = date.fromisoformat(override)
        except ValueError as exc:
            raise ValueError("MONITOR_RUN_DATE must be an ISO date (YYYY-MM-DD)") from exc
        if parsed.isoformat() != override:
            raise ValueError("MONITOR_RUN_DATE must be an ISO date (YYYY-MM-DD)")
        return parsed
    return datetime.now(BEIJING_TZ).date()


def today_str() -> str:
    return beijing_today().isoformat()


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    ensure_parent(path)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(path, payload)


def write_text(path: Path, payload: str) -> None:
    ensure_parent(path)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    last_error: OSError | None = None
    for attempt in range(24):
        try:
            tmp.replace(path)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(min(1.5, 0.25 * (attempt + 1)))
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    if last_error:
        raise last_error


def clean_abstract_text(value: Any) -> str:
    """Convert publisher/JATS abstract markup into display-ready prose."""
    text = html.unescape(str(value or ""))
    text = re.sub(
        r"<(?:[\w.-]+:)?title\b[^>]*>\s*(?:abstract|摘要)\s*</(?:[\w.-]+:)?title\s*>",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(r"<script\b[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style\b[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"^(?:ABSTRACT|Abstract)\s*(?:[:：.\-–—]\s*|\s+(?=(?:This|We|The|Using|Based|Drawing|Our|In|As|An?|To)\b))",
        "",
        text,
    ).strip()
    text = re.sub(r"^摘要\s*(?:[:：]\s*|\s+(?=(?:本文|本研究|本论文|我们)\b))", "", text).strip()
    return text


def normalized_url_identity_keys(value: Any) -> set[str]:
    """Return URL identities while removing tracking parameters only.

    Query values such as ``contentId`` and ``file_no`` identify articles on
    Chinese journal sites and must never be dropped. Hash-router fragments can
    also contain their own query string, so generic ``split('?')`` logic is
    unsafe here.
    """
    raw = str(value or "").strip().rstrip("/").casefold()
    if not raw:
        return set()
    keys = {raw}
    parsed = urllib.parse.urlsplit(raw)
    tracking = {"af", "dgcid", "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term"}
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(key, item) for key, item in query if key.casefold() not in tracking]
    if len(filtered) != len(query):
        cleaned = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(filtered), parsed.fragment)
        ).rstrip("/")
        if cleaned:
            keys.add(cleaned)
    return keys


def fetch_json(
    url: str,
    params: dict[str, str | int] | None = None,
    timeout: int = 30,
    headers: dict[str, str] | None = None,
) -> Any:
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.urlencode(params)}"
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError:
        if not url.startswith("https://"):
            raise
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return json.loads(response.read().decode("utf-8"))


def fetch_json_retry(
    url: str,
    *,
    timeout: int = 30,
    retries: int = 2,
    backoff: float = 1.5,
    retry_statuses: set[int] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """Fetch JSON with bounded retries for rate limits and transient errors.

    Kept separate from :func:`fetch_json` so routine enrichment calls do not
    inherit extra latency. HTTP 429/5xx and network errors are retried with
    exponential backoff; ``Retry-After`` is honoured when the server sends it.
    """
    statuses = set(retry_statuses) if retry_statuses else set(RETRYABLE_HTTP_STATUSES)
    attempts = max(0, retries) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return fetch_json(url, timeout=timeout, headers=headers)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in statuses or attempt + 1 >= attempts:
                raise
            wait = backoff * (2**attempt)
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after:
                try:
                    wait = max(wait, float(retry_after))
                except ValueError:
                    pass
            time.sleep(min(wait, 30.0))
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(backoff * (2**attempt), 30.0))
    raise last_error  # type: ignore[misc]


def fetch_text(url: str, timeout: int = 30, headers: dict[str, str] | None = None) -> str:
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    charset = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.URLError:
        if not url.startswith("https://"):
            raise
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            payload = response.read()
            charset = response.headers.get_content_charset() or "utf-8"

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


def polite_sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def parse_scalar(value: str) -> str | None:
    value = value.strip()
    if value == "null":
        return None
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def load_journals(path: Path = DATA_DIR / "journals.yml") -> list[dict[str, Any]]:
    journals: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    list_key: str | None = None
    source: dict[str, Any] | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "journals:":
            continue
        if line.startswith("  - id:"):
            if current:
                journals.append(current)
            current = {
                "id": parse_scalar(stripped.removeprefix("- id:").strip()),
                "aliases": [],
                "fields": [],
                "sources": [],
            }
            list_key = None
            source = None
            continue
        if current is None:
            continue
        if line.startswith("    ") and not line.startswith("      "):
            key, _, value = stripped.partition(":")
            if value == "":
                list_key = key
                source = None
                continue
            current[key] = parse_scalar(value.strip())
            list_key = None
            source = None
            continue
        if line.startswith("      - ") and list_key in {"aliases", "fields"}:
            current[list_key].append(parse_scalar(stripped.removeprefix("- ").strip()))
            continue
        if line.startswith("      - ") and list_key == "sources":
            key, _, value = stripped.removeprefix("- ").partition(":")
            source = {key.strip(): parse_scalar(value.strip())}
            current["sources"].append(source)
            continue
        if line.startswith("        ") and source is not None:
            key, _, value = stripped.partition(":")
            source[key.strip()] = parse_scalar(value.strip())

    if current:
        journals.append(current)
    return journals


def load_simple_yaml(path: Path) -> dict[str, Any]:
    """Parse the small project YAML files without adding a dependency."""
    if not path.exists():
        return {}
    root: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if not raw_line.startswith(" ") and raw_line.strip().endswith(":"):
            current_key = raw_line.strip()[:-1].strip('"')
            root[current_key] = []
            continue
        if current_key and raw_line.strip().startswith("- "):
            root[current_key].append(parse_scalar(raw_line.strip()[2:].strip()))
            continue
        if current_key == "records" and raw_line.startswith("  ") and not raw_line.startswith("    "):
            root.setdefault("records", {})
    if "records:" in path.read_text(encoding="utf-8"):
        # The manual override file has a simple nested mapping; use json-like
        # parsing via PyYAML when available, otherwise fall back to a narrow parser.
        try:
            import yaml  # type: ignore

            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            return loaded or {}
        except Exception:
            return {}
    return root


def filter_journals_by_tier(journals: list[dict[str, Any]], tier: str | None) -> list[dict[str, Any]]:
    if not tier or tier == "full":
        return journals
    tiers = load_simple_yaml(DATA_DIR / "monitor_tiers.yml")
    selected_ids = set(tiers.get(tier, []))
    if not selected_ids:
        return journals
    return [journal for journal in journals if journal.get("id") in selected_ids]


def render_journals_yml(journals: list[dict[str, Any]]) -> str:
    lines = [
        "# Generated/updated by econ-paper-monitor scripts.",
        "# priority_private is for local cadence/ranking only; do not display it on public pages.",
        "journals:",
    ]
    for journal in journals:
        lines.extend(
            [
                f"  - id: {yaml_quote(str(journal['id']))}",
                f"    title: {yaml_quote(str(journal['title']))}",
                f"    short_name: {yaml_quote(str(journal['short_name']))}",
                "    aliases:",
            ]
        )
        for alias in journal.get("aliases", []):
            lines.append(f"      - {yaml_quote(str(alias))}")
        lines.extend(
            [
                f"    chinese_name: {yaml_quote(str(journal.get('chinese_name') or journal['title']))}",
                "    fields:",
            ]
        )
        for field in journal.get("fields", []):
            lines.append(f"      - {yaml_quote(str(field))}")
        lines.extend(
            [
                f"    public_group: {yaml_quote(str(journal.get('public_group') or '未分类'))}",
                f"    priority_private: {yaml_quote(str(journal.get('priority_private') or ''))}",
                f"    issn: {yaml_quote(str(journal['issn'])) if journal.get('issn') else 'null'}",
                f"    eissn: {yaml_quote(str(journal['eissn'])) if journal.get('eissn') else 'null'}",
                f"    print_issn: {yaml_quote(str(journal['print_issn'])) if journal.get('print_issn') else 'null'}",
                f"    online_issn: {yaml_quote(str(journal['online_issn'])) if journal.get('online_issn') else 'null'}",
                f"    publisher: {yaml_quote(str(journal['publisher'])) if journal.get('publisher') else 'null'}",
                "    sources:",
            ]
        )
        for source in journal.get("sources", []):
            lines.append(f"      - type: {source.get('type') or 'unknown'}")
            if "url" in source:
                lines.append(f"        url: {yaml_quote(str(source['url'])) if source.get('url') else 'null'}")
            if "issn" in source:
                lines.append(f"        issn: {yaml_quote(str(source['issn'])) if source.get('issn') else 'null'}")
    return "\n".join(lines) + "\n"


def write_journals(path: Path, journals: list[dict[str, Any]]) -> None:
    ensure_parent(path)
    path.write_text(render_journals_yml(journals), encoding="utf-8")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value).casefold()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def stable_id(record: dict[str, Any]) -> str:
    doi = normalize_doi(record.get("doi"))
    if doi:
        return "doi:" + doi
    url = (record.get("url") or "").strip()
    if url:
        return "url:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    title = normalize_text(record.get("title"))
    journal = normalize_text(record.get("journal"))
    digest = hashlib.sha1(f"{title}|{journal}".encode("utf-8")).hexdigest()[:16]
    return "title:" + digest


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lower()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value)
    return value or None


def first_text(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return None


def date_from_parts(parts: Any) -> str | None:
    try:
        values = parts["date-parts"][0]
    except (KeyError, IndexError, TypeError):
        return None
    if len(values) < 3:
        return None
    year = int(values[0])
    month = int(values[1])
    day = int(values[2])
    return date(year, month, day).isoformat()


def recent_cutoff(days: int) -> str:
    return (beijing_today() - timedelta(days=days)).isoformat()


def html_escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)
