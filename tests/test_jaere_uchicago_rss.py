from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sources import registry  # noqa: E402
from common import load_journals  # noqa: E402
import audit_source_health  # noqa: E402


JAERE_ID = "journal-of-the-association-of-environmental-and-resource-economists"


def _jaere() -> dict:
    return next(journal for journal in load_journals() if journal["id"] == JAERE_ID)


def test_jaere_identity_keeps_authoritative_issns() -> None:
    journal = _jaere()

    assert journal["issn"] == "2333-5955"
    assert journal["print_issn"] == "2333-5955"
    assert journal["eissn"] == "2333-5963"
    assert journal["online_issn"] == "2333-5963"


def test_jaere_does_not_generate_known_404_uchicago_feed() -> None:
    journal = _jaere()

    urls = [feed["url"] for feed in registry.generated_official_rss_urls(journal)]

    assert (
        "https://www.journals.uchicago.edu/action/showFeed?type=etoc&feed=rss&jc=jaere"
        not in urls
    )


def test_jaere_supplemental_closed_contract_records_404() -> None:
    note = audit_source_health.SUPPLEMENTAL_CLOSED_NOTES[JAERE_ID]

    assert "404" in note
    assert "Crossref" in note
    assert "OpenAlex" in note
