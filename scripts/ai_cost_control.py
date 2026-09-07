"""Shared paid-AI cost controls for Daily Door.

DeepSeek peak pricing is time-of-request, not workflow-start based.  Keep the
policy here so translation and classification cannot drift into a peak window
mid-run.  Local/cached work never calls this guard and therefore remains
available at any time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from common import BEIJING_TZ, DATA_DIR, read_json, write_json


AI_USAGE_PATH = DATA_DIR / "ai_cost_usage.json"
PRICING_VERSION = "deepseek-v4-2026-08-17"

# CNY per 1M tokens from the DeepSeek V4 peak/off-peak pricing table.
DEEPSEEK_PRICES_CNY = {
    "deepseek-v4-flash": {
        "off_peak": {"cache_hit": 0.05, "cache_miss": 1.5, "output": 4.5},
        "peak": {"cache_hit": 0.10, "cache_miss": 3.0, "output": 9.0},
    },
    "deepseek-v4-pro": {
        "off_peak": {"cache_hit": 0.15, "cache_miss": 4.5, "output": 13.5},
        "peak": {"cache_hit": 0.30, "cache_miss": 9.0, "output": 27.0},
    },
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime:
    current = value or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def is_deepseek(base_url: str, model: str) -> bool:
    return "api.deepseek.com" in (base_url or "").casefold() or (model or "").casefold().startswith("deepseek-")


def deepseek_pricing_window(now: datetime | None = None) -> str:
    """Return peak/off_peak using DeepSeek's published UTC weekday windows.

    Peak is Monday-Friday [01:00, 04:00) and [06:00, 10:00) UTC.  Weekends
    and every other time are off-peak.
    """
    current = _as_utc(now)
    if current.weekday() >= 5:
        return "off_peak"
    hour = current.hour
    if 1 <= hour < 4 or 6 <= hour < 10:
        return "peak"
    return "off_peak"


def paid_call_allowed(base_url: str, model: str, now: datetime | None = None) -> bool:
    """Only DeepSeek is price-window gated; configured fallbacks stay usable."""
    return not is_deepseek(base_url, model) or deepseek_pricing_window(now) == "off_peak"


def prepare_chat_payload(payload: dict[str, Any], base_url: str, model: str) -> dict[str, Any]:
    """Force inexpensive non-thinking mode for DeepSeek translation/classification."""
    prepared = dict(payload)
    if is_deepseek(base_url, model):
        prepared["thinking"] = {"type": "disabled"}
    return prepared


def usage_tokens(response_data: dict[str, Any]) -> dict[str, int]:
    usage = response_data.get("usage") if isinstance(response_data, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    prompt_total = int(usage.get("prompt_tokens") or 0)
    cache_hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    explicit_miss = usage.get("prompt_cache_miss_tokens")
    cache_miss = int(explicit_miss) if explicit_miss is not None else max(0, prompt_total - cache_hit)
    completion = int(usage.get("completion_tokens") or 0)
    details = usage.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}
    reasoning = int(details.get("reasoning_tokens") or 0)
    return {
        "prompt_tokens": prompt_total,
        "prompt_cache_hit_tokens": cache_hit,
        "prompt_cache_miss_tokens": cache_miss,
        "completion_tokens": completion,
        "reasoning_tokens": reasoning,
    }


def estimate_deepseek_cost_cny(model: str, tokens: dict[str, int], window: str) -> float | None:
    prices = DEEPSEEK_PRICES_CNY.get(model.casefold())
    if not prices or window not in prices:
        return None
    rate = prices[window]
    cost = (
        tokens.get("prompt_cache_hit_tokens", 0) * rate["cache_hit"]
        + tokens.get("prompt_cache_miss_tokens", 0) * rate["cache_miss"]
        + tokens.get("completion_tokens", 0) * rate["output"]
    ) / 1_000_000
    return round(cost, 8)


def _empty_task_bucket() -> dict[str, Any]:
    return {
        "requests": 0,
        "prompt_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "estimated_cost_cny": 0.0,
        "unknown_cost_requests": 0,
        "peak_requests": 0,
        "off_peak_requests": 0,
        "providers": {},
        "models": {},
    }


def _add_bucket(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in (
        "requests",
        "prompt_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "unknown_cost_requests",
        "peak_requests",
        "off_peak_requests",
    ):
        target[key] = int(target.get(key) or 0) + int(source.get(key) or 0)
    target["estimated_cost_cny"] = round(
        float(target.get("estimated_cost_cny") or 0.0) + float(source.get("estimated_cost_cny") or 0.0), 8
    )
    for mapping_key in ("providers", "models"):
        target.setdefault(mapping_key, {})
        for name, count in (source.get(mapping_key) or {}).items():
            target[mapping_key][name] = int(target[mapping_key].get(name) or 0) + int(count or 0)


def _rolling_30d(days: dict[str, Any], now: datetime) -> dict[str, Any]:
    cutoff = now.astimezone(BEIJING_TZ).date() - timedelta(days=29)
    total = _empty_task_bucket()
    by_task: dict[str, dict[str, Any]] = {}
    for date_key, day_payload in days.items():
        try:
            day_date = datetime.fromisoformat(date_key).date()
        except ValueError:
            continue
        if day_date < cutoff or not isinstance(day_payload, dict):
            continue
        for task, bucket in day_payload.items():
            if not isinstance(bucket, dict):
                continue
            task_total = by_task.setdefault(task, _empty_task_bucket())
            _add_bucket(task_total, bucket)
            _add_bucket(total, bucket)
    return {"total": total, "by_task": by_task}


def record_ai_usage(
    task: str,
    response_data: dict[str, Any],
    *,
    base_url: str,
    model: str,
    path: Path = AI_USAGE_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist compact daily token/cost aggregates for one paid API response."""
    current = _as_utc(now)
    provider = "deepseek" if is_deepseek(base_url, model) else "other"
    window = deepseek_pricing_window(current) if provider == "deepseek" else "not_applicable"
    tokens = usage_tokens(response_data)
    cost = estimate_deepseek_cost_cny(model, tokens, window) if provider == "deepseek" else None

    payload = read_json(path, {})
    if not isinstance(payload, dict):
        payload = {}
    payload["version"] = 1
    payload["currency"] = "CNY"
    payload["pricing_version"] = PRICING_VERSION
    payload["updated_at"] = current.replace(microsecond=0).isoformat()
    days = payload.setdefault("days", {})
    day_key = current.astimezone(BEIJING_TZ).date().isoformat()
    day = days.setdefault(day_key, {})
    bucket = day.setdefault(task, _empty_task_bucket())

    delta = _empty_task_bucket()
    delta["requests"] = 1
    for key, value in tokens.items():
        delta[key] = value
    delta["estimated_cost_cny"] = float(cost or 0.0)
    delta["unknown_cost_requests"] = 1 if cost is None else 0
    if window == "peak":
        delta["peak_requests"] = 1
    elif window == "off_peak":
        delta["off_peak_requests"] = 1
    delta["providers"] = {provider: 1}
    delta["models"] = {model: 1}
    _add_bucket(bucket, delta)

    payload["rolling_30d"] = _rolling_30d(days, current)
    write_json(path, payload)
    return payload
