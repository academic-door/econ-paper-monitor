from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sources import registry  # noqa: E402
from common import load_journals  # noqa: E402


JAERE_ID = "journal-of-the-association-of-environmental-and-resource-economists"


def _jaere() -> dict:
    return next(journal for journal in load_journals() if journal["id"] == JAERE_ID)


def test_jaere_identity_has_authoritative_issns() -> None:
    journal = _jaere()

    assert journal["issn"] == "2333-5955"
    assert journal["print_issn"] == "2333-5955"
    assert journal["eissn"] == "2333-5963"
    assert journal["online_issn"] == "2333-5963"


def test_jaere_generates_official_uchicago_rss() -> None:
    journal = _jaere()

    feeds = registry.generated_official_rss_urls(journal)
    urls = [feed["url"] for feed in feeds]

    assert (
        "https://www.journals.uchicago.edu/action/showFeed?type=etoc&feed=rss&jc=jaere"
        in urls
    )
    chicago = next(
        feed for feed in feeds
        if feed["url"].endswith("feed=rss&jc=jaere")
    )
    assert chicago["label"] == "Chicago Journals RSS"
    assert chicago["type"] == "official"


def test_jaere_uses_existing_uchicago_code_map() -> None:
    assert registry.UCHICAGO_JOURNAL_CODES[JAERE_ID] == "jaere"
