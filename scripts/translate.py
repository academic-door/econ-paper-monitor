"""Translate missing English paper titles.

The script uses an OpenAI-compatible chat completions endpoint when configured.
Translations are cached by DOI/id/title to avoid repeated API cost.  DeepSeek
requests are additionally guarded at call time so a long run cannot drift into
a peak-pricing window.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ai_cost_control import paid_call_allowed, prepare_chat_payload, record_ai_usage
from common import DATA_DIR, ROOT, read_json, stable_id, write_json
from public_integrity import strip_title_prefix
from status import record_source


CACHE_PATH = DATA_DIR / "translation_cache.json"


def clean_title_zh(record: dict[str, Any]) -> bool:
    """Strip working-paper number prefixes (e.g. 'DP21895 ') from a translated
    title_zh. Translation carries the source title verbatim, so a CEPR/NBER
    number prefix can leak into the Chinese title and trip the public data
    integrity gate; paper_number is filled from the prefix when missing."""
    if not record.get("title_zh"):
        return False
    return strip_title_prefix(record)


def has_chinese(value: str | None) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in value or "")


def load_local_env() -> None:
    for env_path in (ROOT / ".env", ROOT / ".env.local"):
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def api_settings() -> tuple[str | None, str, str]:
    load_local_env()
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    key = deepseek_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("TRANSLATION_API_KEY")
    base_url = (
        os.environ.get("DEEPSEEK_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("TRANSLATION_BASE_URL")
        or ("https://api.deepseek.com/v1" if deepseek_key else "https://api.openai.com/v1")
    )
    model = (
        os.environ.get("TRANSLATION_MODEL")
        or os.environ.get("DEEPSEEK_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or ("deepseek-v4-flash" if deepseek_key else "gpt-4o-mini")
    )
    return key, base_url.rstrip("/"), model


def cache_key(record: dict[str, Any]) -> str:
    key = str(record.get("doi") or record.get("id") or stable_id(record)).casefold()
    if key:
        return key
    title = str(record.get("title") or "")
    return hashlib.sha1(title.encode("utf-8")).hexdigest()


def translate_title(title: str, key: str, base_url: str, model: str, timeout: int) -> str:
    payload = prepare_chat_payload(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是经济学论文标题翻译助手。只输出一个忠实、简洁、学术风格的中文标题，不要解释。",
                },
                {"role": "user", "content": title},
            ],
            "temperature": 0.1,
        },
        base_url,
        model,
    )
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    record_ai_usage("translation", data, base_url=base_url, model=model)
    translated = data["choices"][0]["message"]["content"].strip()
    return translated.strip("\"'“”")


def translate_abstract(abstract: str, key: str, base_url: str, model: str, timeout: int) -> str:
    payload = prepare_chat_payload(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是经济学论文摘要翻译助手。请忠实翻译为流畅、严谨的学术中文，保留专有名词、"
                        "缩写、模型名称和因果语义，不增加原文没有的信息。只输出中文摘要，不要解释。"
                    ),
                },
                {"role": "user", "content": abstract},
            ],
            "temperature": 0.1,
        },
        base_url,
        model,
    )
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    record_ai_usage("translation", data, base_url=base_url, model=model)
    translated = data["choices"][0]["message"]["content"].strip()
    return translated.strip("\"'“”")


def daily_paths(daily_dir: Path, date_filter: str | None) -> list[Path]:
    if date_filter:
        path = daily_dir / f"{date_filter}.json"
        return [path] if path.exists() else []
    return sorted(daily_dir.glob("*.json"), reverse=True)


def translate_records(
    records: list[dict[str, Any]],
    args: argparse.Namespace,
    key: str,
    base_url: str,
    model: str,
    cache_records: dict[str, Any],
    *,
    title_limit: int,
    abstract_limit: int,
    deadline: float | None,
) -> tuple[int, int, int, int, int, int]:
    records_to_translate = sorted(
        records,
        key=lambda record: str(record.get("detected_at") or record.get("first_seen") or ""),
        reverse=True,
    )
    changed = title_attempted = abstract_attempted = title_cached = abstract_cached = peak_deferred = 0
    for record in records_to_translate:
        if deadline and time.monotonic() >= deadline:
            break
        if title_attempted >= title_limit and abstract_attempted >= abstract_limit:
            break
        title = str(record.get("title") or "").strip()
        key_id = cache_key(record)
        cached_value = cache_records.get(key_id)
        if not isinstance(cached_value, dict):
            cached_value = {}

        if title and has_chinese(title):
            if record.get("title_zh") != title or record.get("translation_status") != "native_chinese":
                record["title_zh"] = title
                record["translation_status"] = "native_chinese"
                changed += 1
        elif title and not record.get("title_zh") and cached_value.get("title_zh"):
            record["title_zh"] = cached_value["title_zh"]
            record["translation_status"] = "title_translated_cached"
            changed += 1
            title_cached += 1
        elif title and not record.get("title_zh") and title_attempted < title_limit:
            if not paid_call_allowed(base_url, model):
                peak_deferred += 1
            else:
                title_attempted += 1
                try:
                    if args.sleep > 0 and title_attempted + abstract_attempted > 1:
                        time.sleep(args.sleep)
                    title_zh = translate_title(title, key, base_url, model, args.timeout)
                    record["title_zh"] = title_zh
                    record["translation_status"] = "title_translated"
                    cached_value.update({"title": title, "title_zh": title_zh, "model": model})
                    cache_records[key_id] = cached_value
                    changed += 1
                except (KeyError, urllib.error.URLError, TimeoutError, ValueError) as exc:
                    record["translation_status"] = f"title_failed: {exc}"
                    if args.stop_on_error:
                        raise

        if record.get("title_zh") and clean_title_zh(record):
            changed += 1

        abstract = str(record.get("abstract") or "").strip()
        if not abstract or has_chinese(abstract) or record.get("abstract_zh"):
            continue
        abstract_digest = hashlib.sha1(abstract.encode("utf-8")).hexdigest()
        if cached_value.get("abstract_zh") and cached_value.get("abstract_hash") == abstract_digest:
            record["abstract_zh"] = cached_value["abstract_zh"]
            record["translation_status"] = "abstract_translated_cached"
            changed += 1
            abstract_cached += 1
            continue
        if abstract_attempted >= abstract_limit or (deadline and time.monotonic() >= deadline):
            continue
        if not paid_call_allowed(base_url, model):
            peak_deferred += 1
            continue
        abstract_attempted += 1
        try:
            if args.sleep > 0 and title_attempted + abstract_attempted > 1:
                time.sleep(args.sleep)
            abstract_zh = translate_abstract(abstract, key, base_url, model, args.timeout)
            record["abstract_zh"] = abstract_zh
            record["translation_status"] = "abstract_translated"
            cached_value.update(
                {
                    "abstract": abstract,
                    "abstract_hash": abstract_digest,
                    "abstract_zh": abstract_zh,
                    "model": model,
                }
            )
            cache_records[key_id] = cached_value
            changed += 1
        except (KeyError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            record["translation_status"] = f"abstract_failed: {exc}"
            if args.stop_on_error:
                raise
    return title_attempted, abstract_attempted, changed, title_cached, abstract_cached, peak_deferred


def translate_daily_file(
    path: Path,
    args: argparse.Namespace,
    key: str,
    base_url: str,
    model: str,
    cache_records: dict[str, Any],
    *,
    title_limit: int,
    abstract_limit: int,
    deadline: float | None,
) -> tuple[int, int, int, int, int, int]:
    records = read_json(path, [])
    result = translate_records(
        records,
        args,
        key,
        base_url,
        model,
        cache_records,
        title_limit=title_limit,
        abstract_limit=abstract_limit,
        deadline=deadline,
    )
    if result[2] and not args.dry_run:
        write_json(path, records)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--seen", type=Path, default=DATA_DIR / "seen.json")
    parser.add_argument("--date", default=None)
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--abstract-limit", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--max-seconds", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args()

    key, base_url, model = api_settings()
    if not key:
        print("translation skipped: DEEPSEEK_API_KEY, OPENAI_API_KEY, or TRANSLATION_API_KEY is not configured")
        record_source("translation", ok=False, count=0, message="missing api key")
        return

    cache = read_json(CACHE_PATH, {"records": {}})
    cache_records = cache.setdefault("records", {})
    total_title_attempted = total_abstract_attempted = total_changed = 0
    total_title_cached = total_abstract_cached = total_peak_deferred = 0
    deadline = time.monotonic() + args.max_seconds if args.max_seconds else None
    seen_payload = read_json(args.seen, {"papers": {}})
    seen_papers = seen_payload.get("papers") if isinstance(seen_payload, dict) else None
    if isinstance(seen_papers, dict):
        seen_records = [record for record in seen_papers.values() if isinstance(record, dict)]
        result = translate_records(
            seen_records,
            args,
            key,
            base_url,
            model,
            cache_records,
            title_limit=args.limit,
            abstract_limit=args.abstract_limit,
            deadline=deadline,
        )
        (
            total_title_attempted,
            total_abstract_attempted,
            changed,
            total_title_cached,
            total_abstract_cached,
            total_peak_deferred,
        ) = result
        total_changed += changed
        if changed and not args.dry_run:
            write_json(args.seen, seen_payload)
    for path in daily_paths(args.daily_dir, args.date):
        if deadline and time.monotonic() >= deadline:
            break
        title_remaining = max(0, args.limit - total_title_attempted)
        abstract_remaining = max(0, args.abstract_limit - total_abstract_attempted)
        if title_remaining == 0 and abstract_remaining == 0:
            break
        title_attempted, abstract_attempted, changed, title_cached, abstract_cached, peak_deferred = translate_daily_file(
            path,
            args,
            key,
            base_url,
            model,
            cache_records,
            title_limit=title_remaining,
            abstract_limit=abstract_remaining,
            deadline=deadline,
        )
        total_title_attempted += title_attempted
        total_abstract_attempted += abstract_attempted
        total_changed += changed
        total_title_cached += title_cached
        total_abstract_cached += abstract_cached
        total_peak_deferred += peak_deferred
    if not args.dry_run:
        write_json(CACHE_PATH, cache)
    message = (
        f"title_attempted={total_title_attempted} abstract_attempted={total_abstract_attempted} "
        f"changed={total_changed} title_cached={total_title_cached} abstract_cached={total_abstract_cached} "
        f"peak_deferred={total_peak_deferred}"
    )
    record_source("translation", ok=True, count=total_changed, message=message)
    print(f"translation {message}")


if __name__ == "__main__":
    main()
