"""2026-11-09 REStud first-party Accepted Papers bylines; no RSS editor authors."""

import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import apply_overrides


EXACT = {
    "url:e9c34fc008c56361": ("Lauri Kytömaa", "The Roles of Borrower Private Information and Mortgage Relief Design in Foreclosure Prevention"),
    "url:21424c332ac4310c": ("Francis Wong", "Taxing Homeowners Who Can’t or Won’t Borrow"),
}


def test_two_verified_rest_author_overrides_are_exact_and_no_wordpress_editor():
    overrides = apply_overrides.load_overrides(ROOT / "data/manual_overrides.yml")
    for paper_id, (name, title) in EXACT.items():
        assert overrides[paper_id]["authors"] == [name]
        assert "Ann" not in overrides[paper_id]["authors"]


def test_real_canonical_idempotence_and_first_seen_date_invariance():
    overrides = apply_overrides.load_overrides(ROOT / "data/manual_overrides.yml")
    rows = json.loads((ROOT / "data/daily/2026-11-09.json").read_text(encoding="utf-8"))
    selected = {row["id"]: row for row in rows if row.get("id") in EXACT}
    assert set(selected) == set(EXACT)
    for paper_id, (name, expected_title) in EXACT.items():
        original = selected[paper_id]
        assert original["title"] == expected_title
        assert original["journal"] == "Review of Economic Studies"
        assert original.get("doi") is None
        before = {
            key: deepcopy(original.get(key))
            for key in ("first_seen", "published_online", "available_online", "date_source", "id", "detail_key")
        }
        # The historical record may already have been restored naturally:
        # test the missing state as well as a second idempotent application.
        simulated_missing = deepcopy(original)
        simulated_missing["authors"] = []
        simulated_missing["authors_status_code"] = "missing_retry"
        simulated_missing["authors_status"] = "作者信息待核验"
        assert apply_overrides.apply_to_record(simulated_missing, overrides[paper_id])
        assert simulated_missing["authors"] == [name]
        assert simulated_missing["authors_status_code"] == "available"
        assert not simulated_missing.get("authors_status")
        assert not apply_overrides.apply_to_record(simulated_missing, overrides[paper_id])
        for key, value in before.items():
            assert simulated_missing.get(key) == value

        simulated_seen = {"id": paper_id, "authors": [], "first_seen": original["first_seen"]}
        assert apply_overrides.apply_to_record(simulated_seen, overrides[paper_id])
        assert simulated_seen["authors"] == [name]
        assert simulated_seen["first_seen"] == original["first_seen"]
