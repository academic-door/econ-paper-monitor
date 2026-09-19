from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import load_journals  # noqa: E402
from sources import registry  # noqa: E402


JOURNAL_ID = "journal-of-economic-psychology"


def journal_of_economic_psychology() -> dict:
    journals = load_journals(ROOT / "data" / "journals.yml")
    return next(journal for journal in journals if journal["id"] == JOURNAL_ID)


def test_jep_formal_config_contract() -> None:
    journal = journal_of_economic_psychology()

    assert journal["title"] == "Journal of Economic Psychology"
    assert journal["short_name"] == "JEPsy"
    assert journal["priority_private"] == "B"
    assert journal["fields"] == ["behavior_organization"]
    assert journal["public_group"] == "产业、微观、行为与组织"
    assert journal["publisher"] == "Elsevier"
    assert journal["issn"] == "0167-4870"


def test_jep_uses_existing_sciencedirect_official_rss_rule(monkeypatch) -> None:
    journal = journal_of_economic_psychology()
    monkeypatch.setattr(
        registry,
        "crossref_issn_candidates",
        lambda journal, source_registry, registry_entry: [],
    )
    monkeypatch.setattr(registry, "load_registry", lambda: {"journals": {journal["id"]: {}}})

    feeds = registry.generated_official_rss_urls(journal)

    assert feeds[0] == {
        "url": "https://rss.sciencedirect.com/publication/science/01674870",
        "label": "ScienceDirect RSS",
        "type": "official",
    }


def test_public_monitor_list_includes_jep() -> None:
    monitor_list = (ROOT / "docs" / "journal-monitor-list.md").read_text(encoding="utf-8")

    assert "Journal of Economic Psychology" in monitor_list
    assert "| JEPsy" in monitor_list
