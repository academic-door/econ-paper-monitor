from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import render_site  # noqa: E402


def contaminated_cepr_record() -> dict:
    return {
        "id": "url:cepr-example",
        "title": "Example CEPR working paper",
        "url": "https://cepr.org/publications/dp99999",
        "journal": "Journal of Labor Economics",
        "journal_id": "source-cepr-dp",
        "source": "working_papers",
        "source_id": "cepr-dp",
        "source_type": "working_paper",
        "source_name": "CEPR Discussion Papers",
        "series": "CEPR Discussion Papers",
        "detected_at": "2026-09-18T10:00:00+00:00",
    }


def test_working_paper_public_source_uses_registered_source_identity() -> None:
    record = contaminated_cepr_record()

    assert render_site.public_source_title(record) == "CEPR Discussion Papers"


def test_cepr_card_and_filter_do_not_expose_formal_journal_as_source() -> None:
    record = contaminated_cepr_record()

    card = render_site.paper_events([record])
    toolbar = render_site.filter_toolbar([record], source_label="筛选期刊/来源", scope="search")

    assert '<span class="journal-chip">CEPR Discussion Papers</span>' in card
    assert '<option value="source-cepr-dp">CEPR Discussion Papers</option>' in toolbar
    assert '<option value="source-cepr-dp">Journal of Labor Economics</option>' not in toolbar


def test_static_detail_source_uses_cepr_series_identity() -> None:
    item = render_site.detail_item(contaminated_cepr_record())

    assert item["source"] == "CEPR Discussion Papers"
