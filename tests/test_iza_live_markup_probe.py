"""Temporary bounded live IZA markup probe; remove before merge."""
from __future__ import annotations

import re
from urllib.error import HTTPError, URLError

import pytest

from scripts.common import fetch_text
from scripts.fetch_preprints import first_match, meta_values

IZA_PROBES = {
    "18991": "https://www.iza.org/publications/dp/18991/ai-adoption-by-firms-insights-from-the-literature-and-the-german-innovation-survey",
    "18992": "https://www.iza.org/publications/dp/18992/job-seekers-beliefs-and-preferences-over-wages-and-benefits-experimental-evidence-from-online-job-boards",
}

@pytest.mark.parametrize("number,url", IZA_PROBES.items())
def test_iza_detail_html_live_structure_probe(number: str, url: str) -> None:
    try:
        html = fetch_text(url, timeout=8)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        pytest.fail(f"IZA-{number} TRANSPORT {type(exc).__name__} status={getattr(exc, 'code', 'na')}")

    text_needle = "This paper provides" if number == "18991" else "We estimate how strongly"
    positions = [m.start() for m in re.finditer(re.escape(text_needle), html, re.I)]
    body_start = html.lower().find("<body")
    actual = next((p for p in reversed(positions) if p > body_start), -1)
    descriptions = meta_values(html, ["citation_abstract", "dc.description", "description", "og:description"])
    # Generic parser selectors on present production code.
    patterns = [
        r'<div[^>]+class=["\\'][^"\\']*page-header__intro[^"\\']*["\\'][^>]*>\\s*<div[^>]*>\\s*<p[^>]*>(.*?)</p>',
        r'<h2[^>]*>\\s*Abstract\\s*</h2>\\s*<p[^>]*>(.*?)</p>',
        r'<div[^>]+class=["\\'][^"\\']*abstract[^"\\']*["\\'][^>]*>(.*?)</div>',
        r'<section[^>]+class=["\\'][^"\\']*abstract[^"\\']*["\\'][^>]*>(.*?)</section>',
    ]
    matched = [len(first_match([p], html) or "") for p in patterns]
    # Just the short boundary before the body text, strip any long textual data.
    prefix = html[max(0, actual-360):actual] if actual >= 0 else ""
    structure = re.sub(r'(?<=>)[^<>]{30,}', 'TEXT', prefix)
    structure = " ".join(structure.split())[-330:]
    pytest.fail(
        f"IZA-{number} markup bytes={len(html)} BODY={body_start} POS={positions[:6]} "
        f"DESC_LENS={[len(x) for x in descriptions]} SELECTOR_LENS={matched} "
        f"BODY_STRUCTURE={structure}"
    )
