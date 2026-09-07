from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ai_cost_control  # noqa: E402
import translate  # noqa: E402


class AiCostControlTests(unittest.TestCase):
    def test_weekday_peak_boundaries_are_half_open(self) -> None:
        monday = datetime(2026, 9, 7, tzinfo=UTC)
        self.assertEqual(ai_cost_control.deepseek_pricing_window(monday.replace(hour=0, minute=59)), "off_peak")
        self.assertEqual(ai_cost_control.deepseek_pricing_window(monday.replace(hour=1)), "peak")
        self.assertEqual(ai_cost_control.deepseek_pricing_window(monday.replace(hour=3, minute=59)), "peak")
        self.assertEqual(ai_cost_control.deepseek_pricing_window(monday.replace(hour=4)), "off_peak")
        self.assertEqual(ai_cost_control.deepseek_pricing_window(monday.replace(hour=6)), "peak")
        self.assertEqual(ai_cost_control.deepseek_pricing_window(monday.replace(hour=9, minute=59)), "peak")
        self.assertEqual(ai_cost_control.deepseek_pricing_window(monday.replace(hour=10)), "off_peak")

    def test_weekend_is_always_off_peak(self) -> None:
        saturday = datetime(2026, 9, 12, 7, 30, tzinfo=UTC)
        self.assertEqual(ai_cost_control.deepseek_pricing_window(saturday), "off_peak")

    def test_only_deepseek_is_price_window_gated(self) -> None:
        peak = datetime(2026, 9, 7, 7, 30, tzinfo=UTC)
        self.assertFalse(
            ai_cost_control.paid_call_allowed(
                "https://api.deepseek.com/v1", "deepseek-v4-flash", peak
            )
        )
        self.assertTrue(
            ai_cost_control.paid_call_allowed(
                "https://api.openai.com/v1", "gpt-4o-mini", peak
            )
        )

    def test_api_settings_use_deepseek_model_only_with_deepseek_key(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "DEEPSEEK_MODEL": "deepseek-v4-flash",
            "OPENAI_API_KEY": "openai-secret",
            "OPENAI_MODEL": "gpt-test",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(translate, "load_local_env"):
            key, base_url, model = translate.api_settings()
        self.assertEqual(key, "deepseek-secret")
        self.assertEqual(base_url, "https://api.deepseek.com/v1")
        self.assertEqual(model, "deepseek-v4-flash")

    def test_api_settings_ignore_deepseek_model_when_falling_back_to_openai(self) -> None:
        env = {
            "DEEPSEEK_MODEL": "deepseek-v4-flash",
            "OPENAI_API_KEY": "openai-secret",
            "OPENAI_BASE_URL": "https://example.openai.test/v1",
            "OPENAI_MODEL": "gpt-test",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(translate, "load_local_env"):
            key, base_url, model = translate.api_settings()
        self.assertEqual(key, "openai-secret")
        self.assertEqual(base_url, "https://example.openai.test/v1")
        self.assertEqual(model, "gpt-test")

    def test_deepseek_payload_disables_thinking_without_touching_fallback(self) -> None:
        payload = {"model": "deepseek-v4-flash", "messages": []}
        prepared = ai_cost_control.prepare_chat_payload(
            payload, "https://api.deepseek.com/v1", "deepseek-v4-flash"
        )
        self.assertEqual(prepared["thinking"], {"type": "disabled"})
        self.assertNotIn("thinking", payload)
        fallback = ai_cost_control.prepare_chat_payload(
            {"model": "gpt-4o-mini"}, "https://api.openai.com/v1", "gpt-4o-mini"
        )
        self.assertNotIn("thinking", fallback)

    def test_flash_cost_estimate_uses_official_usd_rates(self) -> None:
        tokens = {
            "prompt_cache_hit_tokens": 200,
            "prompt_cache_miss_tokens": 800,
            "completion_tokens": 500,
        }
        self.assertAlmostEqual(
            ai_cost_control.estimate_deepseek_cost_usd(
                "deepseek-v4-flash", tokens, "off_peak"
            ),
            0.0005074,
            places=10,
        )
        self.assertAlmostEqual(
            ai_cost_control.estimate_deepseek_cost_usd(
                "deepseek-v4-flash", tokens, "peak"
            ),
            0.0010148,
            places=10,
        )

    def test_usage_telemetry_persists_daily_and_rolling_totals(self) -> None:
        response = {
            "usage": {
                "prompt_tokens": 1000,
                "prompt_cache_hit_tokens": 200,
                "prompt_cache_miss_tokens": 800,
                "completion_tokens": 500,
                "completion_tokens_details": {"reasoning_tokens": 0},
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "usage.json"
            ai_cost_control.record_ai_usage(
                "translation",
                response,
                base_url="https://api.deepseek.com/v1",
                model="deepseek-v4-flash",
                path=path,
                now=datetime(2026, 9, 7, 10, 15, tzinfo=UTC),
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
        day = payload["days"]["2026-09-07"]["translation"]
        self.assertEqual(payload["currency"], "USD")
        self.assertEqual(payload["pricing_source"], ai_cost_control.PRICING_SOURCE)
        self.assertEqual(day["requests"], 1)
        self.assertEqual(day["off_peak_requests"], 1)
        self.assertEqual(day["reasoning_tokens"], 0)
        self.assertAlmostEqual(day["estimated_cost_usd"], 0.0005074, places=10)
        self.assertEqual(payload["rolling_30d"]["total"]["requests"], 1)

    def test_translation_cache_still_applies_when_paid_calls_are_deferred(self) -> None:
        args = argparse.Namespace(sleep=0.0, timeout=5, stop_on_error=False)
        records = [
            {
                "id": "cached",
                "title": "Cached title",
                "abstract": "Cached abstract",
                "first_seen": "2026-09-07T01:00:00Z",
            },
            {
                "id": "uncached",
                "title": "Needs paid translation",
                "abstract": "Needs a paid abstract translation",
                "first_seen": "2026-09-07T00:59:00Z",
            },
        ]
        cache = {
            "cached": {
                "title_zh": "缓存标题",
                "abstract_zh": "缓存摘要",
                "abstract_hash": __import__("hashlib").sha1(b"Cached abstract").hexdigest(),
            }
        }
        with patch.object(translate, "paid_call_allowed", return_value=False):
            result = translate.translate_records(
                records,
                args,
                "secret",
                "https://api.deepseek.com/v1",
                "deepseek-v4-flash",
                cache,
                title_limit=10,
                abstract_limit=10,
                deadline=None,
            )
        self.assertEqual(records[0]["title_zh"], "缓存标题")
        self.assertEqual(records[0]["abstract_zh"], "缓存摘要")
        self.assertNotIn("title_zh", records[1])
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1], 0)
        self.assertGreaterEqual(result[5], 1)


if __name__ == "__main__":
    unittest.main()
