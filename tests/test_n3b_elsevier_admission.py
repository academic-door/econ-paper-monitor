from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import load_journals  # noqa: E402


# This tranche reuses the existing Elsevier acquisition lane; the test only locks admission metadata.
PUBLIC_GROUP = "城市、宏观、人口、劳动、计量、环境、实验"

EXPECTED = {
    "Energy Policy": {
        "priority_private": "B",
        "fields": {"environmental"},
        "public_group": PUBLIC_GROUP,
        "issn": "0301-4215",
        "eissn": None,
    },
    "Structural Change and Economic Dynamics": {
        "priority_private": "B",
        "fields": {"macroeconomics"},
        "public_group": PUBLIC_GROUP,
        "issn": "0954-349X",
        "eissn": "1873-6017",
    },
}


def test_n3b_second_elsevier_batch_is_admitted_with_bounded_contract() -> None:
    journals = {str(item.get("title") or ""): item for item in load_journals()}

    for title, expected in EXPECTED.items():
        assert title in journals, f"N3b journal missing from registry: {title}"
        journal = journals[title]
        assert "elsevier" in str(journal.get("publisher") or "").casefold()
        assert journal.get("priority_private") == expected["priority_private"]
        assert set(journal.get("fields") or []) == expected["fields"]
        assert journal.get("public_group") == expected["public_group"]
        assert journal.get("issn") == expected["issn"]
        assert journal.get("eissn") == expected["eissn"]

        crossref_issns = {
            str(source.get("issn") or "")
            for source in (journal.get("sources") or [])
            if source.get("type") == "crossref"
        }
        assert expected["issn"] in crossref_issns
