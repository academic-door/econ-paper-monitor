"""JARE current first-party Elementor card layout and legacy compatibility."""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_priority_toc as toc


def current_card(rest_id: int, title: str, person: str, published: str) -> str:
    abstract = (
        "This synthetic official preprint abstract examines producer behavior "
        "and effects on regional economic outcomes. It reports a reproducible "
        "research design, key results, and the robustness of the interpretation."
    )
    return (
        f'<div class="e-loop-item post-{rest_id} preprints2022">'
        f'<h3 class="elementor-heading-title elementor-size-default">'
        f'<a href="https://ageconsearch.umn.edu/record/{rest_id}/files/item.pdf" target="_blank">'
        f'{title}<span class="jare-sr jare-sr-newtab"> (opens in a new tab)</span></a></h3>'
        '<div class="elementor-element jare-by-w jare-by-w--plain elementor-widget">'
        f'<div class="elementor-widget-container">By {person}</div></div>'
        '<div class="elementor-element jare-abstract elementor-widget">'
        f'<div class="elementor-widget-container jare-clamp is-clamped" id="jare-abs-{rest_id}">'
        f'{abstract}</div><button class="jare-more">Show full abstract</button></div>'
        f'<div class="jare-eyebrow"><p>Advance article Published online {published}</p></div>'
        '</div>'
    )


LIVE_FIXTURE = (
    current_card(
        3232,
        "Buffering Supply Shocks in Successive Oligopoly: Market Power and Welfare Implications",
        "Kim, Youngjune; , Tian Xia; Pendell, Dustin L.",
        "September 15, 2026",
    )
    + current_card(
        3231,
        "US Consumer Milk Product and Environmental Impact Attribute Preferences",
        "Wolf, Christopher A.; af Sandeberg, Agnes",
        "August 24, 2026",
    )
)


def test_current_h3_cards_preserve_source_identity_authors_abstract_and_date():
    result = toc.jare_advance_blocks(LIVE_FIXTURE, "https://jareonline.org/preprint-online/")
    assert len(result) == 2
    first, second = result
    assert first["title"] == "Buffering Supply Shocks in Successive Oligopoly: Market Power and Welfare Implications"
    assert "opens in a new tab" not in first["title"]
    assert first["url"] == "https://ageconsearch.umn.edu/record/3232/files/item.pdf"
    assert first["authors"] == ["Kim, Youngjune", "Tian Xia", "Pendell, Dustin L."]
    assert first["published_online"] == "2026-09-15"
    assert first["abstract"].startswith("This synthetic official preprint abstract")
    assert "Show full abstract" not in first["abstract"]
    assert second["published_online"] == "2026-08-24"
    assert second["authors"] == ["Wolf, Christopher A.", "af Sandeberg, Agnes"]


def test_current_html_fallback_when_official_rest_route_fails():
    journal = {
        "id": "journal-of-agricultural-and-resource-economics",
        "title": "Journal of Agricultural and Resource Economics",
    }
    target = toc.TARGETS[journal["id"]][0]
    with mock.patch.object(
        toc,
        "fetch_toc_text",
        side_effect=[toc.PublisherTransportError("NETWORK_FAILURE", "connection timed out"), LIVE_FIXTURE],
    ) as request:
        records = toc.fetch_target(journal, target, timeout=5, detail_limit=12, max_items=40)
    assert request.call_count == 2
    assert len(records) == 2
    assert all(x["source_url"] == "https://jareonline.org/preprint-online/" for x in records)
    assert all(x["raw_data"]["jare_acquisition"] == "html-fallback" for x in records)
    assert all(x["raw_data"]["jare_rest_outcome"] == "NETWORK_FAILURE" for x in records)
    assert records[0]["date_source"] == "jare_published_online"


def test_unknown_card_without_date_or_abstract_does_not_invent_fields():
    minimal = (
        '<h3 class="elementor-heading-title"><a '
        'href="https://ageconsearch.umn.edu/record/100/files/preprint.pdf">'
        'Minimal Verified Title</a></h3>'
    )
    result = toc.jare_advance_blocks(minimal, "https://jareonline.org/preprint-online/")
    assert len(result) == 1
    assert result[0]["published_online"] is None
    assert result[0]["authors"] == []
    assert result[0]["abstract"] is None
