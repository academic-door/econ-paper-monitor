from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from render_site import article_topics


def test_price_does_not_match_rice_agriculture_keyword() -> None:
    record = {
        "title": "Using Policy Functions to Estimate Merger Impacts",
        "abstract": "We estimate equilibrium pricing policy functions and price effects in airline markets.",
        "journal": "NBER Working Papers",
        "fields": [],
    }
    assert "agriculture" not in article_topics(record)


def test_rice_still_matches_agriculture_keyword() -> None:
    record = {
        "title": "Rice Productivity and Rural Household Income",
        "abstract": "We study rice production and farm productivity.",
        "journal": "Working Paper",
        "fields": [],
    }
    assert "agriculture" in article_topics(record)


def test_multiword_topic_rule_keeps_phrase_matching() -> None:
    record = {
        "title": "Land Use Regulation and Housing Supply",
        "abstract": "",
        "journal": "Working Paper",
        "fields": [],
    }
    assert "agriculture" in article_topics(record)
