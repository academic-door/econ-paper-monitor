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
    def test_official_api_request_uses_existing_elsevier_credentials_and_loaded_after(self) -> None:
        payload = {"resultsFound": 0, "results": []}
        response = FakeResponse(json.dumps(payload).encode("utf-8"))

        with (
            patch.object(fetch_sciencedirect_search.urllib.request, "urlopen", return_value=response) as urlopen_mock,
            patch.object(fetch_sciencedirect_search, "today_str", return_value="2026-09-10"),
            patch.dict(
                os.environ,
                {
                    "ELSEVIER_API_KEY": "api-key",
                    "ELSEVIER_INST_TOKEN": "inst-token",
                },
                clear=True,
            ),
        ):
            results = fetch_sciencedirect_search.fetch_sciencedirect_api(
                JDE,
                days=4,
                timeout=7,
                max_items=15,
            )

        self.assertEqual(results, [])
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, fetch_sciencedirect_search.SCIENCEDIRECT_API_URL)
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.headers.get("X-els-apikey"), "api-key")
        self.assertEqual(request.headers.get("X-els-insttoken"), "inst-token")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["pub"], "Journal of Development Economics")
        self.assertEqual(body["loadedAfter"], "2026-09-07T00:00:00Z")
        self.assertEqual(body["display"], {"offset": 0, "show": 25, "sortBy": "date"})

    def test_api_result_becomes_canonical_sciencedirect_record_with_honest_date_provenance(self) -> None:
        item = {
            "authors": [
                {"order": 0, "name": "Hao Xu"},
                {"order": 1, "name": "Jingxuan Xu"},
            ],
            "doi": "10.1016/j.jdeveco.2026.103999",
            "loadDate": "2026-09-10T01:15:22Z",
            "pii": "S0304387826001999",
            "publicationDate": "2026-09-10",
            "sourceTitle": "Journal of Development Economics",
            "title": "Career Incentives and the Environmental Cost: Evidence from China’s Promotion Tournaments",
            "uri": "https://www.sciencedirect.com/science/article/pii/S0304387826001999",
        }

        record = fetch_sciencedirect_search.api_result_record(item, JDE)

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["doi"], "10.1016/j.jdeveco.2026.103999")
        self.assertEqual(record["authors"], ["Hao Xu", "Jingxuan Xu"])
        self.assertEqual(record["published_online"], "2026-09-10")
        self.assertEqual(record["available_online"], "2026-09-10")
        self.assertEqual(record["date_source"], "sciencedirect_api_load_date")
        self.assertEqual(record["date_confidence"], "B")
        self.assertEqual(record["source"], "sciencedirect_search")
        self.assertEqual(record["raw_data"]["sciencedirect_search_route"], "official_api_v2")
        self.assertEqual(record["raw_data"]["sciencedirect_api_load_date"], "2026-09-10T01:15:22Z")
        self.assertNotIn("first_seen", record)

    def test_api_load_date_is_used_only_as_explicit_fallback_provenance(self) -> None:
        item = {
            "authors": [{"order": 0, "name": "Alice Author"}],
            "doi": "10.1016/j.jdeveco.2026.103998",
            "loadDate": "2026-09-10T01:15:22Z",
            "pii": "S0304387826001998",
            "publicationDate": "",
            "sourceTitle": "Journal of Development Economics",
            "title": "New JDE paper",
            "uri": "https://www.sciencedirect.com/science/article/pii/S0304387826001998",
        }

        record = fetch_sciencedirect_search.api_result_record(item, JDE)

        assert record is not None
        self.assertIsNone(record["published_online"])
        self.assertEqual(record["available_online"], "2026-09-10")
        self.assertEqual(record["date_source"], "sciencedirect_api_load_date")
        self.assertNotIn("first_seen", record)

    def test_api_error_falls_back_to_existing_readonly_proxy_route(self) -> None:
        api_error = urllib.error.HTTPError(
            fetch_sciencedirect_search.SCIENCEDIRECT_API_URL,
            429,
            "Too Many Requests",
            Message(),
            None,
        )
        proxy_record = {"title": "Recovered by existing proxy", "raw_data": {"pii": "S1"}}

        with (
            patch.object(fetch_sciencedirect_search, "fetch_journal_via_api", side_effect=api_error) as api_mock,
            patch.object(
                fetch_sciencedirect_search,
                "fetch_journal_via_proxy",
                return_value=([proxy_record], "Journal of Development Economics: 1 via readonly-proxy"),
            ) as proxy_mock,
            patch.dict(os.environ, {"ELSEVIER_API_KEY": "api-key"}, clear=True),
        ):
            records, message = fetch_sciencedirect_search.fetch_journal(
                JDE,
                days=4,
                timeout=5,
                max_items=10,
            )

        api_mock.assert_called_once()
        proxy_mock.assert_called_once()
        self.assertEqual(records, [proxy_record])
        self.assertIn("api_fallback=HTTPError", message)

    def test_successful_api_zero_is_not_treated_as_failure_or_forced_to_proxy(self) -> None:
        with (
            patch.object(
                fetch_sciencedirect_search,
                "fetch_journal_via_api",
                return_value=([], "Journal of Development Economics: 0 via official-api-v2"),
            ) as api_mock,
            patch.object(fetch_sciencedirect_search, "fetch_journal_via_proxy") as proxy_mock,
            patch.dict(os.environ, {"ELSEVIER_API_KEY": "api-key"}, clear=True),
        ):
            records, message = fetch_sciencedirect_search.fetch_journal(
                JDE,
                days=4,
                timeout=5,
                max_items=10,
            )

        api_mock.assert_called_once()
        proxy_mock.assert_not_called()
        self.assertEqual(records, [])
        self.assertIn("official-api-v2", message)

    def test_api_and_proxy_failure_are_both_visible(self) -> None:
        with (
            patch.object(
                fetch_sciencedirect_search,
                "fetch_journal_via_api",
                side_effect=RuntimeError("api unavailable"),
            ),
            patch.object(
                fetch_sciencedirect_search,
                "fetch_journal_via_proxy",
                side_effect=urllib.error.HTTPError("https://r.jina.ai/x", 402, "Payment Required", Message(), None),
            ),
            patch.dict(os.environ, {"ELSEVIER_API_KEY": "api-key"}, clear=True),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                r"official-api=RuntimeError: api unavailable; readonly-proxy=HTTPError",
            ):
                fetch_sciencedirect_search.fetch_journal(
                    JDE,
                    days=4,
                    timeout=5,
                    max_items=10,
                )

    def test_update_workflow_passes_existing_elsevier_credentials(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "update.yml").read_text(
            encoding="utf-8"
        )
        marker = "- name: Fetch ScienceDirect in-press search"
        start = workflow.index(marker)
        block = workflow[start: workflow.index("- name: Fetch Crossref priority metadata", start)]
        self.assertIn("ELSEVIER_API_KEY: ${{ secrets.ELSEVIER_API_KEY }}", block)
        self.assertIn("ELSEVIER_INST_TOKEN: ${{ secrets.ELSEVIER_INST_TOKEN }}", block)

    def test_status_message_exposes_official_api_and_proxy_capability(self) -> None:
        with patch.dict(
            os.environ,
            {"ELSEVIER_API_KEY": "api-key", "JINA_API_KEY": "jina-key"},
            clear=True,
        ):
            message = fetch_sciencedirect_search.build_status_message(28, 0, ["JDE: 2 via official-api-v2"])

        self.assertIn("elsevier_api_key=on", message)
        self.assertIn("jina_key=on", message)
        self.assertIn("failures=0", message)


if __name__ == "__main__":
    unittest.main()
