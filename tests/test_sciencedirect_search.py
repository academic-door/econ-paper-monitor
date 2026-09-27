from __future__ import annotations

import sys
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_sciencedirect_search  # noqa: E402


SAMPLE = """
1.   
Research article 
## [_Persistent_ global growth differences and euro area adjustment: Real activity, trade and the real exchange rate](http://www.sciencedirect.com/science/article/pii/S0022199626001030)

[_Journal_ of _International_ _Economics_](http://www.sciencedirect.com/science/journal/00221996)Available online 19 July 2026 
    1.   Adrian Ifrim
    2.   Robert Kollmann
    3.   Werner Roeger

2.   
Research article 
## [An unrelated result](http://www.sciencedirect.com/science/article/pii/S0000000000000000)

[Economic Modelling](http://www.sciencedirect.com/science/journal/02649993)Available online 19 July 2026 
    1.   Other Author
"""


class ScienceDirectSearchTests(unittest.TestCase):
    def test_parser_filters_by_journal_and_keeps_online_date(self) -> None:
        journal = {
            "id": "journal-of-international-economics",
            "title": "Journal of International Economics",
            "issn": "0022-1996",
        }

        records = fetch_sciencedirect_search.parse_search_results(SAMPLE, journal)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["pii"], "S0022199626001030")
        self.assertEqual(records[0]["available_online"], "2026-07-19")
        self.assertEqual(records[0]["authors"], ["Adrian Ifrim", "Robert Kollmann", "Werner Roeger"])
        self.assertTrue(records[0]["title"].startswith("Persistent global growth"))

    def test_month_only_issue_date_is_not_treated_as_online_date(self) -> None:
        self.assertIsNone(fetch_sciencedirect_search.parse_online_date("August 2026"))

    def test_status_message_reflects_official_api_key_state(self) -> None:
        with patch.dict(fetch_sciencedirect_search.os.environ, {"ELSEVIER_API_KEY": "test-key"}, clear=True):
            on_message = fetch_sciencedirect_search.build_status_message(2, 0, ["Journal A: 1"])
        with patch.dict(fetch_sciencedirect_search.os.environ, {}, clear=True):
            off_message = fetch_sciencedirect_search.build_status_message(2, 1, ["Journal A: blocked"])
        self.assertIn("elsevier_api_key=on", on_message)
        self.assertIn("route=official_api_v2_put_title_wildcard", on_message)
        self.assertIn("failures=0", on_message)
        self.assertIn("elsevier_api_key=off", off_message)
        self.assertIn("failures=1", off_message)

if __name__ == "__main__":
    unittest.main()
