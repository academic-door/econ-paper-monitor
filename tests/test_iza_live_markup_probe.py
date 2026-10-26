"""Temporary bounded real-provider CI probe; remove before merging any fix."""
from __future__ import annotations

from urllib.error import HTTPError, URLError

import pytest

from scripts.common import fetch_text

IZA_PROBES = {
    "18991": "https://www.iza.org/publications/dp/18991/ai-adoption-by-firms-insights-from-the-literature-and-the-german-innovation-survey",
    "18992": "https://www.iza.org/publications/dp/18992/job-seekers-beliefs-and-preferences-over-wages-and-benefits-experimental-evidence-from-online-job-boards",
}

@pytest.mark.parametrize("number,url", IZA_PROBES.items())
def test_iza_detail_html_live_structure_probe(number: str, url: str) -> None:
    try:
        html = fetch_text(url, timeout=8)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        pytest.fail(f"IZA-{number} transport {type(exc).__name__} HTTP={getattr(exc, 'code', 'n/a')}")
    index = html.casefold().find("this paper provides" if number == "18991" else "we estimate how strongly")
    nearby = html[max(0,index-400):index+700] if index >= 0 else "not found"
    # The only copied source fragment is short structural HTML; do not log page text.
    structure = nearby.replace("\\n", " ")[:250] if index >= 0 else "NO ABSTRACT TEXT"
    if index < 0:
        pytest.fail(f"IZA-{number} HTML bytes={len(html)} ABSTRACT_TEXT_MISSING; structure={structure}")
    # RED probe returns structural evidence for a parser adaptation, not release acceptance.
    pytest.fail(f"IZA-{number} HTML bytes={len(html)} ABSTRACT_AT={index} STRUCTURE={structure}")
