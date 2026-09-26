from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import render_site  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def _journal_record() -> dict:
    return {
        "id": "j1",
        "doi": "10.1234/china.journal",
        "title": "China journal example",
        "url": "https://doi.org/10.1234/china.journal",
        "journal": "Example Journal",
        "journal_id": "example-journal",
        "source_type": "journal",
        "detected_at": "2026-09-19T10:00:00+00:00",
        "fields": ["china"],
        "china_relevance_status": "confirmed",
    }


def _working_record() -> dict:
    return {
        "id": "wp1",
        "title": "China working paper example",
        "url": "https://cepr.org/publications/dp99998",
        "journal": "CEPR Discussion Papers",
        "journal_id": "source-cepr-dp",
        "source": "working_papers",
        "source_id": "cepr-dp",
        "source_type": "working_paper",
        "detected_at": "2026-09-19T11:00:00+00:00",
        "fields": ["china"],
        "china_relevance_status": "confirmed",
    }


def test_china_page_counts_use_one_unique_public_projection(monkeypatch) -> None:
    monkeypatch.setattr(render_site, "today_str", lambda: "2026-09-19")
    journal = _journal_record()
    duplicate = dict(journal, id="j1-duplicate")
    working = _working_record()
    records = [journal, duplicate, working]

    html = render_site.china_topic_body(records, records, records)

    assert '<p>2 篇</p>' in html
    assert 'href="#china-journals"><strong>1</strong><span>期刊论文</span>' in html
    assert 'href="#china-working"><strong>1</strong><span>工作论文</span>' in html


def test_public_product_audit_checks_china_count_consistency() -> None:
    smoke = (ROOT / "tests" / "daily_vnext_public_smoke.mjs").read_text(encoding="utf-8")

    assert "assertChinaCountConsistency" in smoke



def test_china_page_excludes_policy_commentary_from_journal_working_split(monkeypatch) -> None:
    monkeypatch.setattr(render_site, "today_str", lambda: "2026-09-19")
    journal = _journal_record()
    working = _working_record()
    commentary = {
        "id": "column1",
        "title": "China research commentary",
        "url": "https://cepr.org/voxeu/columns/china-example",
        "journal": "VoxEU / CEPR Columns",
        "journal_id": "source-voxeu-cepr-columns",
        "source": "working_papers",
        "source_id": "voxeu-cepr-columns",
        "source_type": "policy_commentary",
        "detected_at": "2026-09-19T12:00:00+00:00",
        "fields": ["china"],
        "china_relevance_status": "confirmed",
    }

    html = render_site.china_topic_body(
        [journal, working, commentary],
        [journal, working, commentary],
        [journal, working, commentary],
    )

    assert "<p>2 篇</p>" in html
    assert 'href="#china-journals"><strong>1</strong><span>期刊论文</span>' in html
    assert 'href="#china-working"><strong>1</strong><span>工作论文</span>' in html
    assert "China research commentary" not in html
