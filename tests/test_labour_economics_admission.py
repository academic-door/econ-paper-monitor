from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import load_journals  # noqa: E402
from sources import registry  # noqa: E402


JOURNAL_ID = "labour-economics"


def labour_economics() -> dict:
    journals = load_journals(ROOT / "data" / "journals.yml")
    return next(journal for journal in journals if journal["id"] == JOURNAL_ID)


def test_labour_economics_formal_config_contract() -> None:
    journal = labour_economics()

    assert journal["title"] == "Labour Economics"
    assert journal["short_name"] == "LabEcon"
    assert journal["priority_private"] == "B"
    assert journal["fields"] == ["labor"]
    assert journal["public_group"] == "城市、宏观、人口、劳动、计量、环境、实验"
    assert journal["publisher"] == "Elsevier"
    assert journal["issn"] == "0927-5371"


def test_labour_economics_uses_existing_sciencedirect_official_rss_rule(monkeypatch) -> None:
    journal = labour_economics()
    monkeypatch.setattr(
        registry,
        "crossref_issn_candidates",
        lambda journal, source_registry, registry_entry: [],
    )
    monkeypatch.setattr(registry, "load_registry", lambda: {"journals": {journal["id"]: {}}})

    feeds = registry.generated_official_rss_urls(journal)

    assert feeds[0] == {
        "url": "https://rss.sciencedirect.com/publication/science/09275371",
        "label": "ScienceDirect RSS",
        "type": "official",
    }


def test_public_monitor_list_includes_labour_economics() -> None:
    monitor_list = (ROOT / "docs" / "journal-monitor-list.md").read_text(encoding="utf-8")

    assert "Labour Economics" in monitor_list
    assert "| LabEcon" in monitor_list
