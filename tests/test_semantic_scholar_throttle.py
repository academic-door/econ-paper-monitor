"""Regression contract for Parent Decision 0015 shared-key pressure and circuit safety.

The conservative keyed probe rate and fast-trip threshold are grounded in the
2026-09-10 bounded live calibration recorded on #177.
"""

from __future__ import annotations

import sys
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import enrich_metadata as em  # noqa: E402
import recover_metadata_batch as rmb  # noqa: E402


class SemanticScholarThrottleTests(unittest.TestCase):
    def setUp(self) -> None:
        em.reset_semantic_scholar_throttle()

    def tearDown(self) -> None:
        em.reset_semantic_scholar_throttle()

    def test_keyed_target_uses_conservative_live_pressure_probe_rate(self) -> None:
        self.assertEqual(em.SS_PROVIDER_KEY_LIMIT_RPS, 1.0)
        self.assertLessEqual(em.SS_CLIENT_TARGET_RPS, 0.8)
        self.assertEqual(em.SS_CLIENT_TARGET_RPS, 0.2)
        self.assertGreaterEqual(em.SS_KEY_MIN_INTERVAL_SECONDS, 5.0)

    def test_retry_after_429_reenters_same_gate(self) -> None:
        headers = Message()
        headers["Retry-After"] = "2"
        too_many = urllib.error.HTTPError("https://example", 429, "Too Many Requests", headers, None)
        payload = {
            "abstract": "A sufficiently long recovered abstract after a paced Semantic Scholar retry. " * 2,
            "authors": [{"name": "Ada Example"}],
        }
        with patch.object(em, "_semantic_scholar_gate", return_value=True) as gate, patch.object(
            em, "fetch_json_retry", side_effect=[too_many, payload]
        ) as fetch, patch.object(em.time, "sleep") as sleep, patch.dict(
            em.os.environ, {"S2_API_KEY": "", "SEMANTIC_SCHOLAR_API_KEY": ""}, clear=False
        ):
            result = em.semantic_scholar_doi_metadata("10.1234/example", 1, retries=1)
        self.assertEqual(result.get("abstract_source"), "semantic_scholar")
        self.assertEqual(gate.call_count, 2)
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(any(call.args and call.args[0] >= 2 for call in sleep.call_args_list))
        state = em.semantic_scholar_throttle_state()
        self.assertEqual(state["rate_limited_count"], 0)
        self.assertEqual(state["consecutive_terminal_rate_limited"], 0)
        self.assertEqual(state["recent_rate_limited"], 1)
        self.assertEqual(state["last_retry_after_seconds"], 2.0)

    def test_keyed_429_is_terminal_without_hidden_retries(self) -> None:
        too_many = urllib.error.HTTPError("https://example", 429, "Too Many Requests", Message(), None)
        with patch.dict(em.os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "secret-value"}, clear=False), patch.object(
            em, "_semantic_scholar_gate", return_value=True
        ) as gate, patch.object(em, "fetch_json_retry", side_effect=too_many) as fetch, patch.object(
            em.time, "sleep"
        ) as sleep:
            result = em.semantic_scholar_doi_metadata("10.1234/keyed-pressure", 1, retries=2)
        self.assertEqual(result.get("_status"), "rate_limited")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(gate.call_count, 1)
        sleep.assert_not_called()
        state = em.semantic_scholar_throttle_state()
        self.assertEqual(state["keyed_max_retries"], 0)
        self.assertEqual(state["rate_limited_count"], 1)
        self.assertEqual(state["consecutive_terminal_rate_limited"], 1)
        self.assertEqual(state["recent_rate_limited"], 1)

    def test_success_does_not_erase_recent_throttle_pressure(self) -> None:
        for outcome in ["rate_limited", "success", "success", "success"]:
            em._record_semantic_scholar_outcome(outcome)
        before = em.semantic_scholar_throttle_state()
        em._record_semantic_scholar_outcome("success")
        after = em.semantic_scholar_throttle_state()
        self.assertEqual(before["rate_limited_count"], 1)
        self.assertEqual(after["rate_limited_count"], 1)
        self.assertEqual(after["recent_rate_limited"], 1)
        self.assertEqual(after["consecutive_terminal_rate_limited"], 0)

    def test_two_consecutive_terminal_429s_fast_trip_before_rolling_window(self) -> None:
        em._record_semantic_scholar_outcome("rate_limited")
        first = em.semantic_scholar_throttle_state()
        self.assertFalse(first["circuit_open"])
        self.assertEqual(first["consecutive_terminal_rate_limited"], 1)

        em._record_semantic_scholar_outcome("rate_limited")
        second = em.semantic_scholar_throttle_state()
        self.assertTrue(second["circuit_open"])
        self.assertEqual(second["fast_trip_after_consecutive_rate_limits"], 2)
        self.assertEqual(second["consecutive_terminal_rate_limited"], 2)
        self.assertEqual(second["recent_attempts"], 2)

        with patch.object(em, "fetch_json_retry") as fetch:
            result = em.semantic_scholar_doi_metadata("10.1234/fast-skipped", 1, retries=0)
        self.assertEqual(result.get("_status"), "skipped_rate_limited")
        fetch.assert_not_called()

    def test_non_rate_limited_terminal_outcome_resets_fast_trip_streak(self) -> None:
        em._record_semantic_scholar_outcome("rate_limited")
        em._record_semantic_scholar_outcome("not_found")
        em._record_semantic_scholar_outcome("rate_limited")
        state = em.semantic_scholar_throttle_state()
        self.assertFalse(state["circuit_open"])
        self.assertEqual(state["consecutive_terminal_rate_limited"], 1)

    def test_rolling_rate_limit_pressure_opens_circuit_and_skips_network(self) -> None:
        for outcome in ["rate_limited", "success", "rate_limited", "success", "rate_limited"] + ["success"] * 5:
            em._record_semantic_scholar_outcome(outcome)
        state = em.semantic_scholar_throttle_state()
        self.assertTrue(state["circuit_open"])
        self.assertEqual(state["recent_attempts"], 10)
        self.assertEqual(state["recent_rate_limited"], 3)
        self.assertEqual(state["consecutive_terminal_rate_limited"], 0)
        with patch.object(em, "fetch_json_retry") as fetch:
            result = em.semantic_scholar_doi_metadata("10.1234/skipped", 1, retries=1)
        self.assertEqual(result.get("_status"), "skipped_rate_limited")
        fetch.assert_not_called()

    def test_5xx_is_provider_error_not_rate_limit(self) -> None:
        server_error = urllib.error.HTTPError("https://example", 503, "Unavailable", None, None)
        with patch.object(em, "_semantic_scholar_gate", return_value=True), patch.object(
            em, "fetch_json_retry", side_effect=server_error
        ), patch.object(em.time, "sleep"):
            result = em.semantic_scholar_doi_metadata("10.1234/server", 1, retries=0)
        self.assertEqual(result.get("_status"), "provider_error")
        state = em.semantic_scholar_throttle_state()
        self.assertEqual(state["rate_limited_count"], 0)
        self.assertEqual(state["consecutive_terminal_rate_limited"], 0)

    def test_control_telemetry_is_safe_and_embedded_in_provider_health(self) -> None:
        with patch.dict(em.os.environ, {"S2_API_KEY": "secret-value"}, clear=False):
            state = em.semantic_scholar_throttle_state()
        self.assertEqual(state["endpoint_class"], "academic_graph_paper_details")
        self.assertEqual(state["workload_class"], "metadata_recovery")
        self.assertEqual(state["configured_concurrency"], "shared_global_start_gate")
        self.assertEqual(state["client_target_rps"], 0.2)
        self.assertEqual(state["fast_trip_after_consecutive_rate_limits"], 2)
        self.assertEqual(state["keyed_max_retries"], 0)
        self.assertNotIn("secret-value", repr(state))
        with patch.object(rmb, "semantic_scholar_throttle_state", return_value=state):
            health = rmb.summarize_provider_health(
                {"10.1/a": {"semantic-scholar": {"_status": "skipped_rate_limited"}}}
            )
        self.assertEqual(health["semantic-scholar"]["control"]["client_target_rps"], 0.2)
        self.assertEqual(health["semantic-scholar"]["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
