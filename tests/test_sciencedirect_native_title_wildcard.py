"""Focused RED regressions for ScienceDirect native title-wildcard discovery."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_sciencedirect_search as sd  # noqa: E402


JDE = {
    "id": "journal-of-development-economics",
    "title": "Journal of Development Economics",
    "short_name": "JDE",
    "publisher": "Elsevier",
    "issn": "0304-3878",
    "sources": [{"type": "rss", "url": None}],
}
TARGET = {
    "sourceTitle": "Journal of Development Economics",
    "pii": "S0304387826002075",
    "doi": "10.1016/j.jdeveco.2026.103924",
    "title": "Political career incentives and the environmental costs: Evidence from China’s promotion tournaments",
    "uri": "https://api.elsevier.com/content/article/pii/S0304387826002075",
    "authors": [
        {"name": "JingXuan Xu"},
        {"name": "Hao Xu"},
        {"name": "Guangrong Ma"},
    ],
    "publicationDate": "2026-12-01",
    "loadDate": "2026-09-10T00:00:00.000Z",
}


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class ScienceDirectNativeTitleWildcardTests(unittest.TestCase):
    def test_native_payload_uses_title_wildcard_pub_and_loaded_after(self):
        builder = getattr(sd, "sciencedirect_api_payload", None)
        self.assertIsNotNone(builder, "native Search v2 payload builder is required")
        with patch.object(sd, "today_str", return_value="2026-09-10"):
            payload = builder(JDE, days=4, max_items=10)
        self.assertEqual(payload["title"], "*")
        self.assertEqual(payload["pub"], "Journal of Development Economics")
        self.assertEqual(payload["loadedAfter"], "2026-09-07T00:00:00Z")
        self.assertEqual(payload["display"], {"offset": 0, "show": 10, "sortBy": "date"})

    def test_official_request_is_native_put_json_and_parses_results(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["method"] = request.get_method()
            captured["url"] = request.full_url
            captured["data"] = json.loads((request.data or b"{}").decode("utf-8"))
            captured["headers"] = dict(request.header_items())
            self.assertEqual(timeout, 12)
            return FakeResponse({"results": [TARGET]})

        with (
            patch.dict(
                sd.os.environ,
                {"ELSEVIER_API_KEY": "test-key", "ELSEVIER_INST_TOKEN": "test-token"},
                clear=False,
            ),
            patch.object(sd, "today_str", return_value="2026-09-10"),
            patch.object(sd.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            results = sd.fetch_sciencedirect_api(JDE, days=4, timeout=12, max_items=10)

        self.assertEqual(captured["method"], "PUT")
        self.assertEqual(captured["url"], sd.SCIENCEDIRECT_API_URL)
        self.assertEqual(captured["data"]["title"], "*")
        self.assertEqual(captured["data"]["pub"], JDE["title"])
        lower_headers = {key.casefold(): value for key, value in captured["headers"].items()}
        self.assertEqual(lower_headers["content-type"], "application/json")
        self.assertEqual(lower_headers["x-els-apikey"], "test-key")
        self.assertEqual(lower_headers["x-els-insttoken"], "test-token")
        self.assertEqual(results, [TARGET])

    def test_native_result_keeps_load_date_as_availability_not_publication(self):
        record = sd.api_result_record(TARGET, JDE)
        self.assertIsNotNone(record)
        self.assertEqual(record["doi"], TARGET["doi"])
        self.assertEqual(record["raw_data"]["pii"], TARGET["pii"])
        self.assertEqual(record["available_online"], "2026-09-10")
        self.assertIsNone(record.get("published_online"))
        self.assertEqual(record["date_source"], "sciencedirect_api_load_date")
        self.assertNotIn("first_seen", record)
        self.assertEqual(record["raw_data"]["sciencedirect_search_route"], "official_api_v2_put_title_wildcard")

    def test_native_result_rejects_wrong_publication(self):
        wrong = dict(TARGET)
        wrong["sourceTitle"] = "Economics Letters"
        self.assertIsNone(sd.api_result_record(wrong, JDE))


if __name__ == "__main__":
    unittest.main()
