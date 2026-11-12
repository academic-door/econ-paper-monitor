"""REStud 2026-11-11 exact official Accepted Papers authors, not WordPress editor."""

import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import apply_overrides


CASES = {
    "url:e17bee18c00c1236": (
        "Limits of Disclosure in Search Markets",
        ["Raphael Boleslavsky", "Silvana Krasteva"],
    ),
    "url:c2ed5c330c9b382a": (
        "Is the Customer Always Right? Customer Taste-Based Discrimination and Competition",
        ["Alon Y. Rubinstein"],
    ),
}


def test_verified_first_party_bylines_and_original_record_identity():
    overrides = apply_overrides.load_overrides(ROOT / "data/manual_overrides.yml")
    records = json.loads((ROOT / "data/daily/2026-11-11.json").read_text(encoding="utf-8"))
    actual = {record["id"]: record for record in records if record.get("id") in CASES}
    assert set(actual) == set(CASES)

    for record_id, (title, authors) in CASES.items():
        original = actual[record_id]
        assert original["journal"] == "Review of Economic Studies"
        assert original["title"] == title
        assert original.get("doi") is None
        assert original.get("raw_data", {}).get("publication_stage") == "restud_accepted"
        assert overrides[record_id]["authors"] == authors
        assert "Ann" not in authors

        protected = {
            field: deepcopy(original.get(field))
            for field in (
                "id", "detail_key", "first_seen", "available_online",
                "published_online", "date_source", "issue_date", "doi",
            )
        }
        missing = deepcopy(original)
        missing["authors"] = []
        missing["authors_status_code"] = "missing_retry"
        missing["authors_status"] = "作者信息待核验"
        assert apply_overrides.apply_to_record(missing, overrides[record_id])
        assert missing["authors"] == authors
        assert missing["authors_status_code"] == "available"
        assert not missing.get("authors_status")
        assert not apply_overrides.apply_to_record(missing, overrides[record_id])
        for field, value in protected.items():
            assert missing.get(field) == value

        simulated_seen = {"id": record_id, "authors": [], "first_seen": original["first_seen"]}
        assert apply_overrides.apply_to_record(simulated_seen, overrides[record_id])
        assert simulated_seen["authors"] == authors
        assert simulated_seen["first_seen"] == original["first_seen"]
