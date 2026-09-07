"""Use a low-cost LLM pass to resolve China-relevance candidates.

Only candidate records are sent. Results are cached by DOI/id, so the same
paper is not charged repeatedly. DeepSeek calls are deferred during published
peak-pricing windows while cached decisions remain usable at any time.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ai_cost_control import paid_call_allowed, prepare_chat_payload, record_ai_usage
from common import DATA_DIR, read_json, stable_id, today_str, write_json
from status import record_source
from translate import api_settings


CACHE_PATH = DATA_DIR / "china_relevance_cache.json"


def record_key(record: dict[str, Any]) -> str:
    return str(record.get("doi") or record.get("id") or stable_id(record)).casefold()


def candidate_payload(record: dict[str, Any]) -> str:
    payload = {
        "title": record.get("title"),
        "title_zh": record.get("title_zh"),
        "journal": record.get("journal"),
        "authors": record.get("authors"),
        "abstract": record.get("abstract"),
        "abstract_zh": record.get("abstract_zh"),
        "candidate_reason": record.get("china_relevance_reason"),
    }
    return json.dumps(payload, ensure_ascii=False)


def has_direct_china_evidence(record: dict[str, Any]) -> bool:
    text = " ".join(
        str(value or "")
        for value in [
            record.get("title"),
            record.get("title_zh"),
            record.get("abstract"),
            record.get("abstract_zh"),
            record.get("journal"),
        ]
    ).casefold()
    for phrase in ["发展中国家", "发展中经济体", "最不发达国家"]:
        text = text.replace(phrase, "")
    patterns = [
        r"\bchina\b",
        r"\bchinese\b",
        r"\bprc\b",
        r"\bmainland china\b",
        r"\bhong kong\b",
        r"\btaiwan\b",
        r"\bbeijing\b",
        r"\bshanghai\b",
        r"\bguangdong\b",
        r"\bchina shock\b",
        r"\bhukou\b",
    ]
    if any(re.search(pattern, text, flags=re.I) for pattern in patterns):
        return True
    return any(keyword in text for keyword in ["中国", "香港", "台湾"])


def parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def ask_model(record: dict[str, Any], key: str, base_url: str, model: str, timeout: int) -> dict[str, Any]:
    prompt = (
        "请判断这篇经济学论文是否与中国研究直接相关。只输出 JSON："
        '{"verdict":"yes/no/uncertain","confidence":0-1,"reason":"简短中文理由"}。\n'
        "判定标准：如果研究对象、数据、制度背景、政策背景或核心应用是中国、"
        "中国企业、中国人群、中国地区、香港或台湾，verdict=yes。"
        "如果只是作者姓名像中文但研究主题不明确，verdict=uncertain。"
        "如果明确不是中国研究，verdict=no。\n\n"
        + candidate_payload(record)
    )
    payload = prepare_chat_payload(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是严谨的经济学文献分类助手，只输出有效 JSON。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
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
    record_ai_usage("china_relevance", data, base_url=base_url, model=model)
    return parse_json_response(data["choices"][0]["message"]["content"])


def apply_decision(record: dict[str, Any], decision: dict[str, Any]) -> bool:
    verdict = str(decision.get("verdict") or "uncertain").casefold()
    try:
        confidence = float(decision.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    reason = str(decision.get("reason") or "AI 判定")
    if verdict == "yes" and confidence >= 0.75 and has_direct_china_evidence(record):
        updates = {
            "china_related": True,
            "china_related_source": "ai",
            "china_relevance_status": "confirmed",
            "china_relevance_reason": reason,
        }
    elif verdict == "yes" and confidence >= 0.75:
        updates = {
            "china_related": False,
            "china_related_source": "ai",
            "china_relevance_status": "none",
            "china_relevance_reason": "AI 倾向判定为中国相关，但题名/摘要/元数据缺少直接中国证据，已按保守规则排除",
        }
    elif verdict == "no" and confidence >= 0.85:
        updates = {
            "china_related": False,
            "china_related_source": "ai",
            "china_relevance_status": "none",
            "china_relevance_reason": reason,
        }
    else:
        updates = {
            "china_relevance_status": "candidate",
            "china_relevance_reason": reason,
        }
    changed = False
    for field, value in updates.items():
        if record.get(field) != value:
            record[field] = value
            changed = True
    return changed


def daily_paths(daily_dir: Path, date_filter: str | None, latest_days: int) -> list[Path]:
    paths = sorted(daily_dir.glob("*.json"), reverse=True)
    if date_filter:
        path = daily_dir / f"{date_filter}.json"
        return [path] if path.exists() else []
    if latest_days > 0:
        return paths[:latest_days]
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--date", default=today_str())
    parser.add_argument("--all", action="store_true", help="Process all daily archives.")
    parser.add_argument("--latest-days", type=int, default=14, help="When --all is set, limit to newest N daily files; 0 means all.")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()

    key, base_url, model = api_settings()
    if not key:
        record_source("ai-china-relevance", ok=True, count=0, message="skipped: missing api key")
        print("ai china relevance skipped: missing api key")
        return

    cache = read_json(CACHE_PATH, {"records": {}})
    cache_records = cache.setdefault("records", {})
    attempted = changed = confirmed = auto_no = peak_deferred = 0
    date_filter = None if args.all else args.date
    changed_paths = []
    for path in daily_paths(args.daily_dir, date_filter, args.latest_days):
        records = read_json(path, [])
        if not isinstance(records, list):
            continue
        path_changed = False
        for record in records:
            if attempted >= args.limit:
                break
            if record.get("china_relevance_status") != "candidate":
                continue
            key_id = record_key(record)
            try:
                decision = cache_records.get(key_id)
                if not decision:
                    if not paid_call_allowed(base_url, model):
                        peak_deferred += 1
                        continue
                    decision = ask_model(record, key, base_url, model, args.timeout)
                    cache_records[key_id] = decision
                    attempted += 1
                if apply_decision(record, decision):
                    changed += 1
                    path_changed = True
                if record.get("china_related") is True:
                    confirmed += 1
                if record.get("china_related") is False:
                    auto_no += 1
            except (json.JSONDecodeError, KeyError, urllib.error.URLError, TimeoutError, ValueError) as exc:
                cache_records[key_id] = {"verdict": "uncertain", "confidence": 0, "reason": f"AI 判定失败：{exc}"}
                continue
        if path_changed:
            write_json(path, records)
            changed_paths.append(str(path))
        if attempted >= args.limit:
            break

    write_json(CACHE_PATH, cache)
    message = (
        f"attempted={attempted} changed={changed} auto_no={auto_no} files={len(changed_paths)} "
        f"peak_deferred={peak_deferred}"
    )
    record_source("ai-china-relevance", ok=True, count=confirmed, message=message)
    print(
        f"ai china relevance attempted={attempted} changed={changed} confirmed={confirmed} "
        f"auto_no={auto_no} peak_deferred={peak_deferred}"
    )


if __name__ == "__main__":
    main()
