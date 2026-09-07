"""Read-only benchmark for OpenAI-compatible AI providers used by Daily Door.

The benchmark never writes canonical Daily data. It samples records that already
have accepted production outputs, calls a candidate provider with the same task
contracts, and emits side-by-side results for routing review.
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

from common import DATA_DIR, read_json


PRICING_VERSION = "qwen-us-virginia-global-list-2026-09-07"
PRICING_SOURCE = "https://www.alibabacloud.com/help/en/model-studio/model-pricing"

# USD per 1M tokens for Global models served from Model Studio US (Virginia).
# Use list price and ignore cache discounts so benchmark estimates are conservative.
QWEN_PRICE_TIERS: dict[str, tuple[tuple[int, float, float], ...]] = {
    "qwen3.7-flash": (
        (32_000, 0.028, 0.110),
        (256_000, 0.083, 0.330),
        (1_000_000, 0.165, 0.660),
    ),
    "qwen3.7-flash-2026-07-15": (
        (32_000, 0.028, 0.110),
        (256_000, 0.083, 0.330),
        (1_000_000, 0.165, 0.660),
    ),
    "qwen-flash": (
        (128_000, 0.022, 0.216),
        (256_000, 0.087, 0.861),
        (1_000_000, 0.173, 1.721),
    ),
    "qwen-flash-2025-07-28": (
        (128_000, 0.022, 0.216),
        (256_000, 0.087, 0.861),
        (1_000_000, 0.173, 1.721),
    ),
}

TRANSLATION_SYSTEM = "你是经济学论文标题翻译助手。只输出一个忠实、简洁、学术风格的中文标题，不要解释。"
CHINA_SYSTEM = "你是严谨的经济学文献分类助手，只输出有效 JSON。"


def has_chinese(value: str | None) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in value or "")


def stable_case_key(record: dict[str, Any]) -> str:
    return str(record.get("doi") or record.get("id") or record.get("title") or "").casefold()


def deterministic_sample(records: list[dict[str, Any]], limit: int, seed: str) -> list[dict[str, Any]]:
    def sort_key(record: dict[str, Any]) -> str:
        raw = f"{seed}|{stable_case_key(record)}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    return sorted(records, key=sort_key)[: max(0, limit)]


def daily_records(daily_dir: Path, latest_days: int) -> list[dict[str, Any]]:
    paths = sorted(daily_dir.glob("*.json"), reverse=True)
    if latest_days > 0:
        paths = paths[:latest_days]
    records: list[dict[str, Any]] = []
    for path in paths:
        payload = read_json(path, [])
        if not isinstance(payload, list):
            continue
        for record in payload:
            if isinstance(record, dict):
                copy = dict(record)
                copy["_benchmark_source_file"] = path.name
                records.append(copy)
    return records


def translation_cases(records: list[dict[str, Any]], limit: int, seed: str) -> list[dict[str, Any]]:
    eligible = [
        record
        for record in records
        if str(record.get("title") or "").strip()
        and not has_chinese(str(record.get("title") or ""))
        and str(record.get("title_zh") or "").strip()
    ]
    return deterministic_sample(eligible, limit, seed)


def baseline_china_label(record: dict[str, Any]) -> str | None:
    if record.get("china_related") is True:
        return "yes"
    if record.get("china_related") is False:
        return "no"
    return None


def china_cases(records: list[dict[str, Any]], limit: int, seed: str) -> list[dict[str, Any]]:
    eligible = [
        record
        for record in records
        if baseline_china_label(record) in {"yes", "no"}
        and str(record.get("china_relevance_status") or "") in {"confirmed", "none"}
        and str(record.get("title") or "").strip()
    ]
    return deterministic_sample(eligible, limit, seed)


def candidate_payload(record: dict[str, Any]) -> str:
    return json.dumps(
        {
            "title": record.get("title"),
            "title_zh": record.get("title_zh"),
            "journal": record.get("journal"),
            "authors": record.get("authors"),
            "abstract": record.get("abstract"),
            "abstract_zh": record.get("abstract_zh"),
            "candidate_reason": record.get("china_relevance_reason"),
        },
        ensure_ascii=False,
    )


def china_prompt(record: dict[str, Any]) -> str:
    return (
        "请判断这篇经济学论文是否与中国研究直接相关。只输出 JSON："
        '{"verdict":"yes/no/uncertain","confidence":0-1,"reason":"简短中文理由"}。\n'
        "判定标准：如果研究对象、数据、制度背景、政策背景或核心应用是中国、"
        "中国企业、中国人群、中国地区、香港或台湾，verdict=yes。"
        "如果只是作者姓名像中文但研究主题不明确，verdict=uncertain。"
        "如果明确不是中国研究，verdict=no。\n\n"
        + candidate_payload(record)
    )


def parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("model response is not a JSON object")
    return parsed


def usage_tokens(response: dict[str, Any]) -> tuple[int, int]:
    usage = response.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def estimate_qwen_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    tiers = QWEN_PRICE_TIERS.get(model.casefold())
    if not tiers:
        return None
    for max_input, input_price, output_price in tiers:
        if prompt_tokens <= max_input:
            return round(
                (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000,
                10,
            )
    return None


def call_chat(
    *,
    key: str,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    timeout: int,
) -> tuple[str, dict[str, Any], float]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if model.casefold().startswith("qwen"):
        payload["enable_thinking"] = False
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    content = str(data["choices"][0]["message"]["content"]).strip()
    return content, data, elapsed_ms


def run_translation_case(
    record: dict[str, Any], *, key: str, base_url: str, model: str, timeout: int
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "task": "translation",
        "id": stable_case_key(record),
        "source_file": record.get("_benchmark_source_file"),
        "title": record.get("title"),
        "baseline": record.get("title_zh"),
    }
    try:
        content, response, elapsed_ms = call_chat(
            key=key,
            base_url=base_url,
            model=model,
            messages=[
                {"role": "system", "content": TRANSLATION_SYSTEM},
                {"role": "user", "content": str(record.get("title") or "")},
            ],
            temperature=0.1,
            timeout=timeout,
        )
        candidate = content.strip("\"'“”")
        prompt_tokens, completion_tokens = usage_tokens(response)
        result.update(
            {
                "ok": True,
                "candidate": candidate,
                "has_chinese": has_chinese(candidate),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "elapsed_ms": elapsed_ms,
                "estimated_cost_usd": estimate_qwen_cost_usd(model, prompt_tokens, completion_tokens),
            }
        )
    except (KeyError, json.JSONDecodeError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        result.update({"ok": False, "error": str(exc)})
    return result


def run_china_case(
    record: dict[str, Any], *, key: str, base_url: str, model: str, timeout: int
) -> dict[str, Any]:
    expected = baseline_china_label(record)
    result: dict[str, Any] = {
        "task": "china_relevance",
        "id": stable_case_key(record),
        "source_file": record.get("_benchmark_source_file"),
        "title": record.get("title"),
        "baseline_label": expected,
    }
    try:
        content, response, elapsed_ms = call_chat(
            key=key,
            base_url=base_url,
            model=model,
            messages=[
                {"role": "system", "content": CHINA_SYSTEM},
                {"role": "user", "content": china_prompt(record)},
            ],
            temperature=0.0,
            timeout=timeout,
        )
        decision = parse_json_response(content)
        verdict = str(decision.get("verdict") or "uncertain").casefold()
        prompt_tokens, completion_tokens = usage_tokens(response)
        result.update(
            {
                "ok": True,
                "decision": decision,
                "valid_verdict": verdict in {"yes", "no", "uncertain"},
                "label_agreement": verdict == expected,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "elapsed_ms": elapsed_ms,
                "estimated_cost_usd": estimate_qwen_cost_usd(model, prompt_tokens, completion_tokens),
            }
        )
    except (KeyError, json.JSONDecodeError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        result.update({"ok": False, "error": str(exc)})
    return result


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    translations = [r for r in results if r.get("task") == "translation"]
    china = [r for r in results if r.get("task") == "china_relevance"]
    ok = [r for r in results if r.get("ok") is True]
    known_costs = [float(r["estimated_cost_usd"]) for r in ok if r.get("estimated_cost_usd") is not None]
    elapsed = [float(r["elapsed_ms"]) for r in ok if r.get("elapsed_ms") is not None]
    china_agreement = (
        sum(1 for r in china if r.get("ok") and r.get("label_agreement")) / len(china)
        if china
        else None
    )
    return {
        "requests": len(results),
        "successful_requests": len(ok),
        "translation_cases": len(translations),
        "translation_chinese_rate": (
            sum(1 for r in translations if r.get("ok") and r.get("has_chinese")) / len(translations)
            if translations
            else None
        ),
        "china_cases": len(china),
        "china_valid_response_rate": (
            sum(1 for r in china if r.get("ok") and r.get("valid_verdict")) / len(china)
            if china
            else None
        ),
        "china_label_agreement_rate": china_agreement,
        "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in ok),
        "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in ok),
        "estimated_cost_usd": round(sum(known_costs), 10) if known_costs else None,
        "mean_latency_ms": round(sum(elapsed) / len(elapsed), 1) if elapsed else None,
        "automated_gate_pass": bool(results)
        and len(ok) == len(results)
        and all(r.get("has_chinese") for r in translations)
        and all(r.get("valid_verdict") for r in china)
        and (china_agreement is None or china_agreement >= 0.95),
        "routing_decision": "manual_translation_review_required" if translations else "no_translation_cases",
    }


def prepared_cases_payload(
    title_cases: list[dict[str, Any]], china_records: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "translation": [
            {
                "id": stable_case_key(record),
                "source_file": record.get("_benchmark_source_file"),
                "title": record.get("title"),
                "baseline": record.get("title_zh"),
            }
            for record in title_cases
        ],
        "china_relevance": [
            {
                "id": stable_case_key(record),
                "source_file": record.get("_benchmark_source_file"),
                "title": record.get("title"),
                "baseline_label": baseline_china_label(record),
            }
            for record in china_records
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--latest-days", type=int, default=30)
    parser.add_argument("--translation-cases", type=int, default=12)
    parser.add_argument("--china-cases", type=int, default=12)
    parser.add_argument("--seed", default="daily-door-qwen-v1")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--model", default=os.environ.get("QWEN_MODEL") or "qwen3.7-flash")
    parser.add_argument("--base-url", default=os.environ.get("QWEN_BASE_URL"))
    args = parser.parse_args()

    records = daily_records(args.daily_dir, args.latest_days)
    titles = translation_cases(records, args.translation_cases, args.seed)
    china = china_cases(records, args.china_cases, args.seed)
    if args.prepare_only:
        payload: dict[str, Any] = {
            "mode": "prepare_only",
            "seed": args.seed,
            "cases": prepared_cases_payload(titles, china),
        }
    else:
        key = os.environ.get("QWEN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
        if not key:
            raise SystemExit("QWEN_API_KEY or DASHSCOPE_API_KEY is required")
        if not args.base_url:
            raise SystemExit(
                "QWEN_BASE_URL or --base-url is required; use the workspace-specific "
                "Model Studio compatible-mode/v1 endpoint for your region"
            )
        results: list[dict[str, Any]] = []
        for record in titles:
            results.append(
                run_translation_case(
                    record, key=key, base_url=args.base_url, model=args.model, timeout=args.timeout
                )
            )
        for record in china:
            results.append(
                run_china_case(
                    record, key=key, base_url=args.base_url, model=args.model, timeout=args.timeout
                )
            )
        payload = {
            "mode": "live",
            "provider": "qwen" if args.model.casefold().startswith("qwen") else "openai_compatible",
            "model": args.model,
            "pricing_version": PRICING_VERSION if args.model.casefold() in QWEN_PRICE_TIERS else None,
            "pricing_source": PRICING_SOURCE if args.model.casefold() in QWEN_PRICE_TIERS else None,
            "seed": args.seed,
            "summary": summarize(results),
            "results": results,
        }

    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
