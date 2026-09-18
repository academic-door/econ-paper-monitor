from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import load_journals
from sources import registry


def rie():
    journals = load_journals(ROOT / "data" / "journals.yml")
    return next(journal for journal in journals if journal["id"] == "review-of-international-economics")


def test_rie_formal_config_contract():
    journal = rie()

    assert journal["title"] == "Review of International Economics"
    assert journal["short_name"] == "RIE"
    assert journal["priority_private"] == "B"
    assert journal["fields"] == ["international"]
    assert journal["public_group"] == "公共、政治、经济史与国际经济学"
    assert journal["publisher"].startswith("Wiley")
    assert journal["issn"] == "0965-7576"
    assert journal["print_issn"] == "0965-7576"
    assert journal["online_issn"] == "1467-9396"


def test_rie_uses_existing_wiley_official_rss_rule(monkeypatch):
    journal = rie()
    monkeypatch.setattr(
        registry,
        "crossref_issn_candidates",
        lambda journal, source_registry, registry_entry: ["14679396", "09657576"],
    )
    monkeypatch.setattr(registry, "load_registry", lambda: {"journals": {journal["id"]: {}}})

    feeds = registry.generated_official_rss_urls(journal)

    assert feeds[0] == {
        "url": "https://onlinelibrary.wiley.com/action/showFeed?jc=14679396&type=etoc&feed=rss",
        "label": "Wiley Online Library RSS",
        "type": "official",
    }
    assert any("jc=09657576" in feed["url"] for feed in feeds)


def test_public_monitor_list_includes_rie():
    monitor_list = (ROOT / "docs" / "journal-monitor-list.md").read_text(encoding="utf-8")

    assert "Review of International Economics" in monitor_list
    assert "| RIE" in monitor_list
