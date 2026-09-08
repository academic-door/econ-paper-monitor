"""Contract tests for verified Oxford Academic Advance Articles RSS sources."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_source_health  # noqa: E402


OUP_ADVANCE_RSS = {
    "quarterly-journal-of-economics": "https://academic.oup.com/rss/site_5504/advanceAccess_3365.xml",
    "economic-journal": "https://academic.oup.com/rss/site_6182/advanceAccess_4014.xml",
    "journal-of-the-european-economic-association": "https://academic.oup.com/rss/site_5571/advanceAccess_3427.xml",
    "journal-of-law-economics-and-organization": "https://academic.oup.com/rss/site_5475/advanceAccess_3336.xml",
    "review-of-financial-studies": "https://academic.oup.com/rss/site_5511/advanceAccess_3372.xml",
    "european-review-of-agricultural-economics": "https://academic.oup.com/rss/site_5453/advanceAccess_3314.xml",
}


def journal_block(config: str, journal_id: str) -> str:
    marker = f'  - id: "{journal_id}"'
    start = config.index(marker)
    end = config.find("\n  - id: ", start + len(marker))
    return config[start:] if end == -1 else config[start:end]


def test_verified_oup_journals_configure_official_advance_rss() -> None:
    config = (ROOT / "data" / "journals.yml").read_text(encoding="utf-8")
    for journal_id, feed_url in OUP_ADVANCE_RSS.items():
        block = journal_block(config, journal_id)
        assert feed_url in block, f"{journal_id} is missing verified Oxford Academic Advance Articles RSS"


def test_verified_oup_rss_journals_are_not_supplemental_closed() -> None:
    stale_closures = set(OUP_ADVANCE_RSS) & set(audit_source_health.SUPPLEMENTAL_CLOSED_NOTES)
    assert not stale_closures, f"verified OUP RSS sources still marked supplemental-closed: {sorted(stale_closures)}"
