from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import load_journals  # noqa: E402


EXPECTED = {
    "Economic Modelling": {
        "priority_private": "B",
        "taxonomy": "applied_empirical",
        "issn": {"0264-9993", "1873-6122"},
    },
    "Economics of Education Review": {
        "priority_private": "B",
        "taxonomy": "applied_empirical",
        "issn": {"0272-7757"},
    },
    "Energy Economics": {
        "priority_private": "B",
        "taxonomy": "environmental",
        "issn": {"0140-9883"},
    },
}


def test_n3a_first_elsevier_batch_is_admitted_with_bounded_contract():
    journals = {str(item.get("title") or ""): item for item in load_journals()}

    for title, expected in EXPECTED.items():
        assert title in journals, f"N3a journal missing from registry: {title}"
        journal = journals[title]
        assert "elsevier" in str(journal.get("publisher") or "").casefold()
        assert journal.get("priority_private") == expected["priority_private"]
        assert journal.get("taxonomy") == expected["taxonomy"]
        configured_issn = journal.get("issn")
        if isinstance(configured_issn, str):
            configured = {configured_issn}
        else:
            configured = {str(value) for value in (configured_issn or [])}
        assert expected["issn"] <= configured
