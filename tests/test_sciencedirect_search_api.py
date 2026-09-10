"""Regression contract for Elsevier native early discovery and quota telemetry under Parent Decision 0015."""

from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_sciencedirect_search as sd  # noqa: E402

JDE = {
    "id": "journal-of-development-economics",
    "title": "Journal of Development Economics",
    "short_name": "JDE",
    "issn": "0304-3878",
    "publisher": "Elsevier",
    "priority_private": "A",
    "fields": ["development"],
}
TARGET = {
    "sourceTitle": "Journal of Development Economics",
    "pii": "S0304387826002075",
    "doi": "10.1016/j.jdeveco.2026.103924",
    "title": "Political career incentives and the environmental costs: Evidence from China’s promotion tournaments",
    "uri": "https://api.elsevier.com/content/article/pii/S0304387826002075",
    "authors": [{"name": "JingXuan Xu"}, {"name": "Hao Xu"}, {"name": "Guangrong Ma"}],
    "publicationDate": "2026-12-01",
    "loadDate": "2026-09-10T00:00:00.000Z",
}

class FakeResponse:
    def __init__(self, payload: dict, headers: Message | None = None):
        self._payload = payload
        self.headers = headers or Message()
    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False

class ScienceDirectApiTests(unittest.TestCase):
    def setUp(self):
        sd.reset_elsevier_search_telemetry()
    def tearDown(self):
        sd.reset_elsevier_search_telemetry()

    def test_native_payload_and_parent_rate_ceiling(self):
        with patch.object(sd, "today_str", return_value="2026-09-10"):
            payload = sd.sciencedirect_api_payload(JDE, days=4, max_items=15)
        self.assertEqual(payload["title"], "*")
        self.assertEqual(payload["pub"], JDE["title"])
        self.assertEqual(payload["loadedAfter"], "2026-09-07T00:00:00Z")
        self.assertEqual(payload["display"], {"offset": 0, "show": 25, "sortBy": "date"})
        self.assertEqual(sd.ELSEVIER_SEARCH_PROVIDER_RPS, 2.0)
        self.assertLessEqual(sd.ELSEVIER_SEARCH_CLIENT_TARGET_RPS, 1.6)
        self.assertGreaterEqual(sd.ELSEVIER_SEARCH_MIN_INTERVAL_SECONDS, 0.625)

    def test_official_put_uses_existing_credentials_parses_results_and_records_quota(self):
        headers = Message()
        headers["X-RateLimit-Limit"] = "20000"
        headers["X-RateLimit-Remaining"] = "19969"
        headers["X-RateLimit-Reset"] = "1789056000"
        response = FakeResponse({"results": [TARGET]}, headers)
        with patch.object(sd.urllib.request, "urlopen", return_value=response) as urlopen_mock, patch.object(
            sd, "today_str", return_value="2026-09-10"
        ), patch.dict(os.environ, {"ELSEVIER_API_KEY": "api-key", "ELSEVIER_INST_TOKEN": "inst-token"}, clear=True):
            entries, source_url = sd.fetch_sciencedirect_api(JDE, days=4, timeout=7, max_items=15)
        self.assertEqual(entries, [TARGET])
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(json.loads(request.data.decode("utf-8"))["title"], "*")
        self.assertEqual(request.headers.get("X-els-apikey"), "api-key")
        self.assertEqual(request.headers.get("X-els-insttoken"), "inst-token")
        self.assertEqual(source_url, sd.SCIENCEDIRECT_API_URL)
        control = sd.elsevier_search_telemetry()
        self.assertEqual(control["provider_rate_limit_rps"], 2.0)
        self.assertEqual(control["client_target_rps"], 1.6)
        self.assertEqual(control["quota_limit"], "20000")
        self.assertEqual(control["quota_remaining_min"], 19969)
        self.assertEqual(control["quota_reset"], "1789056000")

    def test_target_record_keeps_load_date_as_availability_only(self):
        record = sd.api_result_record(TARGET, JDE)
        assert record is not None
        self.assertEqual(record["doi"], TARGET["doi"])
        self.assertEqual(record["authors"], ["JingXuan Xu", "Hao Xu", "Guangrong Ma"])
        self.assertEqual(record["available_online"], "2026-09-10")
        self.assertIsNone(record["published_online"])
        self.assertEqual(record["date_source"], "sciencedirect_api_load_date")
        self.assertNotIn("first_seen", record)
        self.assertEqual(record["raw_data"]["sciencedirect_search_route"], "official_api_v2_put_title_wildcard")

    def test_wrong_publication_is_rejected(self):
        wrong = dict(TARGET)
        wrong["sourceTitle"] = "Economics Letters"
        self.assertIsNone(sd.api_result_record(wrong, JDE))

    def test_legacy_string_author_shape_is_supported(self):
        item = dict(TARGET)
        item["authors"] = {"author": "Alice Author"}
        record = sd.api_result_record(item, JDE)
        assert record is not None
        self.assertEqual(record["authors"], ["Alice Author"])

    def test_quota_exceeded_429_is_observable_and_not_retried(self):
        headers = Message()
        headers["X-ELS-Status"] = "QUOTA_EXCEEDED"
        headers["X-RateLimit-Reset"] = "1789056000"
        exc = urllib.error.HTTPError(sd.SCIENCEDIRECT_API_URL, 429, "Too Many Requests", headers, None)
        with patch.object(sd.urllib.request, "urlopen", side_effect=exc) as urlopen_mock, patch.dict(
            os.environ, {"ELSEVIER_API_KEY": "api-key"}, clear=True
        ):
            with self.assertRaisesRegex(RuntimeError, "QUOTA_EXCEEDED"):
                sd.fetch_sciencedirect_api(JDE, days=4, timeout=5, max_items=10)
        self.assertEqual(urlopen_mock.call_count, 1)
        control = sd.elsevier_search_telemetry()
        self.assertEqual(control["quota_exceeded_429"], 1)
        self.assertEqual(control["throttled_429"], 0)
        self.assertEqual(control["quota_reset"], "1789056000")

    def test_throttle_429_retries_once_and_is_distinct_from_quota(self):
        headers = Message()
        headers["Retry-After"] = "1"
        exc = urllib.error.HTTPError(sd.SCIENCEDIRECT_API_URL, 429, "Too Many Requests", headers, None)
        response = FakeResponse({"results": []})
        with patch.object(sd.urllib.request, "urlopen", side_effect=[exc, response]) as urlopen_mock, patch.object(
            sd.time, "sleep"
        ) as sleep_mock, patch.dict(os.environ, {"ELSEVIER_API_KEY": "api-key"}, clear=True):
            entries, _ = sd.fetch_sciencedirect_api(JDE, days=4, timeout=5, max_items=10)
        self.assertEqual(entries, [])
        self.assertEqual(urlopen_mock.call_count, 2)
        sleep_mock.assert_called_once()
        control = sd.elsevier_search_telemetry()
        self.assertEqual(control["quota_exceeded_429"], 0)
        self.assertEqual(control["throttled_429"], 1)
        self.assertEqual(control["last_retry_after"], "1")

    def test_api_error_still_falls_back_to_readonly_proxy(self):
        proxy_record = {"title": "Recovered by existing proxy", "raw_data": {"pii": "S1"}}
        with patch.object(sd, "fetch_journal_via_api", side_effect=RuntimeError("api unavailable")), patch.object(
            sd, "fetch_journal_via_proxy", return_value=([proxy_record], "JDE: 1 via readonly-proxy")
        ) as proxy_mock, patch.dict(os.environ, {"ELSEVIER_API_KEY": "api-key"}, clear=True):
            records, message = sd.fetch_journal(JDE, days=4, timeout=5, max_items=10)
        proxy_mock.assert_called_once()
        self.assertEqual(records, [proxy_record])
        self.assertIn("api_fallback=RuntimeError", message)

    def test_dual_failure_remains_visible(self):
        with patch.object(sd, "fetch_journal_via_api", side_effect=RuntimeError("api unavailable")), patch.object(
            sd, "fetch_journal_via_proxy", side_effect=RuntimeError("proxy unavailable")
        ), patch.dict(os.environ, {"ELSEVIER_API_KEY": "api-key"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "official-api=RuntimeError: api unavailable"):
                sd.fetch_journal(JDE, days=4, timeout=5, max_items=10)

    def test_status_exposes_safe_control_and_quota_telemetry(self):
        headers = Message()
        headers["X-RateLimit-Limit"] = "20000"
        headers["X-RateLimit-Remaining"] = "19900"
        sd._record_elsevier_search_rate(headers)
        with patch.dict(os.environ, {"ELSEVIER_API_KEY": "api-key", "JINA_API_KEY": "jina-key"}, clear=True):
            message = sd.build_status_message(28, 0, ["JDE: 3 via official-api-v2-put-title-wildcard"])
        self.assertIn("client_target_rps=1.6", message)
        self.assertIn("min_interval_seconds=0.625", message)
        self.assertIn("quota_limit=20000", message)
        self.assertIn("quota_remaining_min=19900", message)
        self.assertIn("failures=0", message)

if __name__ == "__main__":
    unittest.main()
