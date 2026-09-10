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

import fetch_sciencedirect_search  # noqa: E402


JDE = {
    "id": "journal-of-development-economics",
    "title": "Journal of Development Economics",
    "short_name": "JDE",
    "issn": "0304-3878",
    "publisher": "Elsevier",
    "priority_private": "A",
    "fields": ["development"],
}


class FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ScienceDirectApiTests(unittest.TestCase):
    def test_official_fielded_url_uses_source_title_and_original_load_date(self) -> None:
        with patch.object(fetch_sciencedirect_search, "today_str", return_value="2026-09-10"):
            url = fetch_sciencedirect_search.official_search_url(JDE, days=4, max_items=15)

        parsed = fetch_sciencedirect_search.urllib.parse.urlparse(url)
        params = fetch_sciencedirect_search.urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "api.elsevier.com")
        self.assertEqual(parsed.path, "/content/search/sciencedirect")
        self.assertEqual(
            params["query"],
            ["srctitle(Journal of Development Economics) AND orig-load-date AFT 20260907"],
        )
        self.assertEqual(params["count"], ["25"])
        self.assertEqual(params["sort"], ["coverDate"])

    def test_official_request_uses_get_existing_credentials_and_parses_entries(self) -> None:
        payload = {
            "search-results": {
                "entry": [
                    {
                        "load-date": "2026-09-10T01:15:22Z",
                        "dc:title": "Political career incentives and the environmental costs: Evidence from China’s promotion tournaments",
                        "prism:publicationName": "Journal of Development Economics",
                        "prism:doi": "10.1016/j.jdeveco.2026.103924",
                        "pii": "S0304387826002075",
                        "authors": {
                            "author": [
                                {"given-name": "JingXuan", "surname": "Xu"},
                                {"given-name": "Hao", "surname": "Xu"},
                                {"given-name": "Guangrong", "surname": "Ma"},
                            ]
                        },
                    }
                ]
            }
        }
        response = FakeResponse(json.dumps(payload).encode("utf-8"))
        with (
            patch.object(fetch_sciencedirect_search.urllib.request, "urlopen", return_value=response) as urlopen_mock,
            patch.object(fetch_sciencedirect_search, "today_str", return_value="2026-09-10"),
            patch.dict(
                os.environ,
                {"ELSEVIER_API_KEY": "api-key", "ELSEVIER_INST_TOKEN": "inst-token"},
                clear=True,
            ),
        ):
            entries, source_url = fetch_sciencedirect_search.fetch_sciencedirect_api(
                JDE, days=4, timeout=7, max_items=15
            )

        self.assertEqual(len(entries), 1)
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.headers.get("X-els-apikey"), "api-key")
        self.assertEqual(request.headers.get("X-els-insttoken"), "inst-token")
        self.assertEqual(request.full_url, source_url)

    def test_entry_becomes_canonical_record_with_load_date_only_as_availability(self) -> None:
        item = {
            "load-date": "2026-09-10T01:15:22Z",
            "dc:title": "Political career incentives and the environmental costs: Evidence from China’s promotion tournaments",
            "prism:publicationName": "Journal of Development Economics",
            "prism:doi": "10.1016/j.jdeveco.2026.103924",
            "pii": "S0304387826002075",
            "authors": {
                "author": [
                    {"given-name": "JingXuan", "surname": "Xu"},
                    {"given-name": "Hao", "surname": "Xu"},
                    {"given-name": "Guangrong", "surname": "Ma"},
                ]
            },
        }

        record = fetch_sciencedirect_search.api_result_record(item, JDE, "https://api.elsevier.com/example")

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["doi"], "10.1016/j.jdeveco.2026.103924")
        self.assertEqual(record["authors"], ["JingXuan Xu", "Hao Xu", "Guangrong Ma"])
        self.assertIsNone(record["published_online"])
        self.assertEqual(record["available_online"], "2026-09-10")
        self.assertEqual(record["date_source"], "sciencedirect_api_load_date")
        self.assertEqual(record["date_confidence"], "B")
        self.assertEqual(record["raw_data"]["sciencedirect_search_route"], "official_api_v2_get_fielded")
        self.assertEqual(record["raw_data"]["sciencedirect_api_load_date"], "2026-09-10T01:15:22Z")
        self.assertNotIn("first_seen", record)

    def test_legacy_string_author_shape_is_supported(self) -> None:
        item = {
            "load-date": "2026-09-10T01:15:22Z",
            "dc:title": "New JDE paper",
            "prism:publicationName": "Journal of Development Economics",
            "prism:doi": "10.1016/j.jdeveco.2026.103998",
            "pii": "S0304387826001998",
            "authors": {"author": "Alice Author"},
        }
        record = fetch_sciencedirect_search.api_result_record(item, JDE)
        assert record is not None
        self.assertEqual(record["authors"], ["Alice Author"])

    def test_429_quota_headers_are_preserved_in_error(self) -> None:
        headers = Message()
        headers["X-ELS-Status"] = "QUOTA_EXCEEDED"
        headers["X-RateLimit-Reset"] = "1789056000"
        exc = urllib.error.HTTPError(
            "https://api.elsevier.com/content/search/sciencedirect",
            429,
            "Too Many Requests",
            headers,
            None,
        )
        with (
            patch.object(fetch_sciencedirect_search.urllib.request, "urlopen", side_effect=exc),
            patch.dict(os.environ, {"ELSEVIER_API_KEY": "api-key"}, clear=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "QUOTA_EXCEEDED") as raised:
                fetch_sciencedirect_search.fetch_sciencedirect_api(JDE, days=4, timeout=5, max_items=10)
        self.assertIn("X-RateLimit-Reset=1789056000", str(raised.exception))

    def test_429_without_quota_marker_retries_once_for_throttle(self) -> None:
        headers = Message()
        headers["Retry-After"] = "1"
        exc = urllib.error.HTTPError(
            "https://api.elsevier.com/content/search/sciencedirect",
            429,
            "Too Many Requests",
            headers,
            None,
        )
        response = FakeResponse(json.dumps({"search-results": {"entry": []}}).encode("utf-8"))
        with (
            patch.object(fetch_sciencedirect_search.urllib.request, "urlopen", side_effect=[exc, response]) as urlopen_mock,
            patch.object(fetch_sciencedirect_search.time, "sleep") as sleep_mock,
            patch.dict(os.environ, {"ELSEVIER_API_KEY": "api-key"}, clear=True),
        ):
            entries, _ = fetch_sciencedirect_search.fetch_sciencedirect_api(JDE, days=4, timeout=5, max_items=10)
        self.assertEqual(entries, [])
        self.assertEqual(urlopen_mock.call_count, 2)
        sleep_mock.assert_called_once()

    def test_api_error_still_falls_back_to_readonly_proxy(self) -> None:
        proxy_record = {"title": "Recovered by existing proxy", "raw_data": {"pii": "S1"}}
        with (
            patch.object(fetch_sciencedirect_search, "fetch_journal_via_api", side_effect=RuntimeError("api unavailable")),
            patch.object(
                fetch_sciencedirect_search,
                "fetch_journal_via_proxy",
                return_value=([proxy_record], "Journal of Development Economics: 1 via readonly-proxy"),
            ) as proxy_mock,
            patch.dict(os.environ, {"ELSEVIER_API_KEY": "api-key"}, clear=True),
        ):
            records, message = fetch_sciencedirect_search.fetch_journal(JDE, days=4, timeout=5, max_items=10)
        proxy_mock.assert_called_once()
        self.assertEqual(records, [proxy_record])
        self.assertIn("api_fallback=RuntimeError", message)

    def test_api_and_proxy_failure_are_both_visible(self) -> None:
        with (
            patch.object(fetch_sciencedirect_search, "fetch_journal_via_api", side_effect=RuntimeError("api unavailable")),
            patch.object(fetch_sciencedirect_search, "fetch_journal_via_proxy", side_effect=RuntimeError("proxy unavailable")),
            patch.dict(os.environ, {"ELSEVIER_API_KEY": "api-key"}, clear=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "official-api=RuntimeError: api unavailable"):
                fetch_sciencedirect_search.fetch_journal(JDE, days=4, timeout=5, max_items=10)

    def test_status_exposes_official_api_and_proxy_capability(self) -> None:
        with patch.dict(os.environ, {"ELSEVIER_API_KEY": "api-key", "JINA_API_KEY": "jina-key"}, clear=True):
            message = fetch_sciencedirect_search.build_status_message(
                28, 0, ["JDE: 2 via official-api-v2-get-fielded"]
            )
        self.assertIn("elsevier_api_key=on", message)
        self.assertIn("jina_key=on", message)
        self.assertIn("failures=0", message)


if __name__ == "__main__":
    unittest.main()
