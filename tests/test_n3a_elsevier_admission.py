from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import load_journals  # noqa: E402


EXPECTED = {
    "Economic Modelling": {
        "priority_private": "B",
        "fields": {"applied_empirical"},
        "public_group": "金融、发展、实证应用",
        "issn": "0264-9993",
    },
    "Economics of Education Review": {
        "priority_private": "B",
        "fields": {"applied_empirical"},
        "public_group": "金融、发展、实证应用",
        "issn": "0272-7757",
    },
    "Energy Economics": {
        "priority_private": "B",
        "fields": {"environmental"},
        "public_group": "城市、宏观、人口、劳动、计量、环境、实验",
        "issn": "0140-9883",
    },
}


def test_n3a_first_elsevier_batch_is_admitted_with_bounded_contract():
    journals = {str(item.get("title") or ""): item for item in load_journals()}

    for title, expected in EXPECTED.items():
        assert title in journals, f"N3a journal missing from registry: {title}"
        journal = journals[title]
        assert "elsevier" in str(journal.get("publisher") or "").casefold()
        assert journal.get("priority_private") == expected["priority_private"]
        assert set(journal.get("fields") or []) == expected["fields"]
        assert journal.get("public_group") == expected["public_group"]
        assert journal.get("issn") == expected["issn"]
