from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_ai_provider as benchmark  # noqa: E402


class BenchmarkAiProviderTests(unittest.TestCase):
    def test_deterministic_sample_is_stable_and_seeded(self) -> None:
        records = [{"id": value} for value in ["a", "b", "c", "d"]]
        first = benchmark.deterministic_sample(records, 3, "seed-a")
        second = benchmark.deterministic_sample(list(reversed(records)), 3, "seed-a")
        other = benchmark.deterministic_sample(records, 3, "seed-b")
        self.assertEqual([r["id"] for r in first], [r["id"] for r in second])
        self.assertNotEqual([r["id"] for r in first], [r["id"] for r in other])

    def test_qwen_37_flash_cost_uses_input_tier(self) -> None:
        self.assertAlmostEqual(
            benchmark.estimate_qwen_cost_usd("qwen3.7-flash", 1_000, 500),
            0.000083,
            places=10,
        )
        self.assertAlmostEqual(
            benchmark.estimate_qwen_cost_usd("qwen3.7-flash", 40_000, 1_000),
            0.00365,
            places=10,
        )
        self.assertIsNone(benchmark.estimate_qwen_cost_usd("unknown-model", 1_000, 500))

    def test_parse_json_response_accepts_fenced_object(self) -> None:
        parsed = benchmark.parse_json_response(
            '```json\n{"verdict":"yes","confidence":0.9,"reason":"中国数据"}\n```'
        )
        self.assertEqual(parsed["verdict"], "yes")

    def test_qwen_call_disables_thinking(self) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}],"usage":{}}'

        def fake_urlopen(request, timeout):
            captured["payload"] = __import__("json").loads(request.data.decode("utf-8"))
            return FakeResponse()

        with patch.object(benchmark.urllib.request, "urlopen", side_effect=fake_urlopen):
            content, _, _ = benchmark.call_chat(
                key="secret",
                base_url="https://example.test/v1",
                model="qwen3.7-flash",
                messages=[{"role": "user", "content": "hello"}],
                temperature=0,
                timeout=5,
            )
        self.assertEqual(content, "ok")
        self.assertIs(captured["payload"]["enable_thinking"], False)

    def test_summary_requires_manual_translation_review(self) -> None:
        results = [
            {
                "task": "translation",
                "ok": True,
                "has_chinese": True,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "estimated_cost_usd": 0.0001,
                "elapsed_ms": 100,
            },
            {
                "task": "china_relevance",
                "ok": True,
                "valid_verdict": True,
                "label_agreement": True,
                "prompt_tokens": 20,
                "completion_tokens": 5,
                "estimated_cost_usd": 0.0002,
                "elapsed_ms": 200,
            },
        ]
        summary = benchmark.summarize(results)
        self.assertTrue(summary["automated_gate_pass"])
        self.assertEqual(summary["routing_decision"], "manual_translation_review_required")
        self.assertEqual(summary["prompt_tokens"], 30)
        self.assertAlmostEqual(summary["estimated_cost_usd"], 0.0003, places=10)


if __name__ == "__main__":
    unittest.main()
