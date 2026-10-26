"""Temporary bounded live IZA markup probe; remove before merge."""
from __future__ import annotations

import re
from urllib.error import HTTPError, URLError
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import fetch_text
from fetch_preprints import meta_values

IZA_PROBES = {
    "18991": "https://www.iza.org/publications/dp/18991/ai-adoption-by-firms-insights-from-the-literature-and-the-german-innovation-survey",
    "18992": "https://www.iza.org/publications/dp/18992/job-seekers-beliefs-and-preferences-over-wages-and-benefits-experimental-evidence-from-online-job-boards",
}

@pytest.mark.parametrize("number,url", IZA_PROBES.items())
def test_iza_detail_html_live_structure_probe(number: str, url: str) -> None:
    try:
        html = fetch_text(url, timeout=12)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        pytest.fail(f"IZA-{number} TRANSPORT {type(exc).__name__} status={getattr(exc, 'code', 'na')}")

    text_needle = "This paper provides" if number == "18991" else "We estimate how strongly"
    positions = [m.start() for m in re.finditer(re.escape(text_needle), html, re.I)]
    body_start = html.lower().find("<body")
    actual = next((p for p in reversed(positions) if p > body_start), -1)
    descriptions = meta_values(html, ["citation_abstract", "dc.description", "description", "og:description"])
    # Structural markers only; no full abstract text is logged.
    patterns = ["page-header__intro", r"<h2[^>]*>\\s*Abstract", r"class=.{0,35}abstract", r"<meta"]
    matched = [len(re.findall(p, html, re.I | re.S)) for p in patterns]
    # Just the short boundary before the body text, strip any long textual data.
    prefix = html[max(0, actual-360):actual] if actual >= 0 else ""
    structure = re.sub(r'(?<=>)[^<>]{30,}', 'TEXT', prefix)
    structure = " ".join(structure.split())[-330:]
    pytest.fail(
        f"IZA-{number} markup bytes={len(html)} BODY={body_start} POS={positions[:6]} "
        f"DESC_LENS={[len(x) for x in descriptions]} SELECTOR_LENS={matched} "
        f"BODY_STRUCTURE={structure}"
    )
