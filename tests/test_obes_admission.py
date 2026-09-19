from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import load_journals
from sources import registry


def obes():
    journals = load_journals(ROOT / "data" / "journals.yml")
    return next(journal for journal in journals if journal["id"] == "oxford-bulletin-of-economics-and-statistics")


def test_obes_formal_config_contract():
    journal = obes()

    assert journal["title"] == "Oxford Bulletin of Economics and Statistics"
    assert journal["short_name"] == "OBES"
    assert journal["priority_private"] == "B"
    assert journal["fields"] == ["applied_empirical"]
    assert journal["public_group"] == "金融、发展、实证应用"
    assert journal["publisher"].startswith("Wiley")
    assert journal["issn"] == "0305-9049"
    assert journal["print_issn"] == "0305-9049"
    assert journal["online_issn"] == "1468-0084"


def test_obes_uses_existing_wiley_official_rss_rule(monkeypatch):
    journal = obes()
    monkeypatch.setattr(
        registry,
        "crossref_issn_candidates",
        lambda journal, source_registry, registry_entry: ["03059049"],
    )
    monkeypatch.setattr(registry, "load_registry", lambda: {"journals": {journal["id"]: {}}})

    feeds = registry.generated_official_rss_urls(journal)

    assert feeds[0] == {
        "url": "https://onlinelibrary.wiley.com/action/showFeed?jc=14680084&type=etoc&feed=rss",
        "label": "Wiley Online Library RSS",
        "type": "official",
    }
    assert any("jc=03059049" in feed["url"] for feed in feeds)


def test_public_monitor_list_includes_obes():
    monitor_list = (ROOT / "docs" / "journal-monitor-list.md").read_text(encoding="utf-8")

    assert "Oxford Bulletin of Economics and Statistics" in monitor_list
    assert "| OBES" in monitor_list
