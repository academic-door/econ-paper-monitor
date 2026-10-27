"""Offline regression fixtures for the G21 observed IZA official abstract DOM."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_preprints  # noqa: E402
from backfill_iza_authors import repair_record  # noqa: E402


SOURCE = {"id": "iza"}
ROOT = "https://www.iza.org/publications/dp/"
DATE = "2026-10-16T19:38:06+00:00"
SEO = "The IZA website publishes research insights, discussion papers, and independent policy evidence. Please explore our latest research and other recent publications."


def official_html(number: str, paragraphs: tuple[str, ...], *, paper_number: str | None = None) -> str:
    paragraphs_html = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        "<html><head>"
        f'<meta name="description" content="{SEO}">'
        "</head><body>"
        f"<div>IZA Discussion Paper No. {paper_number or number}</div>"
        "<main><div class='pt-[20px] font-inter'>"
        f'<div class="element-copyexpandable cms-richtext">{paragraphs_html}</div>'
        "</div></main></body></html>"
    )


def record(number: str) -> dict:
    return {
        "id": "url:accepted-immutable",
        "url": f"{ROOT}{number}/accepted-official-title",
        "source_id": "iza",
        "title": "Accepted official title",
        "authors": ["Original Author"],
        "abstract": None,
        "first_seen": DATE,
        "published_online": "2026-10-16",
        "date_source": "original-source-record",
    }


PAR1 = "We examine firm decisions under a range of market and labor conditions, documenting heterogeneous effects from the original survey observations and published evidence. "
PAR2 = "The results are specific to the study population and sample design, with transparent discussion of the estimation strategy and the key limitations of the evidence. "
PAR3 = "Additional estimates illustrate how measured effects vary with worker experience and institutional setting across multiple research designs. "


def test_real_iza_body_container_beats_identical_generic_seo(capsys) -> None:
    """Use first-party multi-paragraph body, not generic 153-char metadata."""
    for number, texts in [("18991", (PAR1, PAR2)), ("18992", (PAR1, PAR2, PAR3))]:
        item = record(number)
        html = official_html(number, texts)
        with patch.object(fetch_preprints, "fetch_text", return_value=html) as fetch:
            result = fetch_preprints.enrich_record_from_detail(item, SOURCE, timeout=10)
        assert fetch.call_count == 1
        assert result["abstract"] == " ".join(texts).strip()
        assert SEO not in result["abstract"]
        assert result["first_seen"] == DATE
        assert result["authors"] == ["Original Author"]
        assert "origin=iza_body" in capsys.readouterr().out


def test_iza_abstract_only_repair_preserves_source_truth_identity_and_dates() -> None:
    item = record("18991")
    original = {key: item[key] for key in ("id", "url", "title", "authors", "first_seen", "published_online", "date_source")}
    html = official_html("18991", (PAR1, PAR2))
    with patch.object(fetch_preprints, "fetch_text", return_value=html) as fetch:
        changed, authors, abstracts = repair_record(item, SOURCE, timeout=10)
    assert fetch.call_count == 1
    assert (changed, authors, abstracts) == (True, False, True)
    assert item["abstract"] == " ".join((PAR1, PAR2)).strip()
    assert item["abstract_source"] == "iza_official_detail"
    assert item["abstract_status_code"] == "available"
    for key, value in original.items():
        assert item[key] == value


def test_generic_iza_meta_never_becomes_an_official_abstract(capsys) -> None:
    item = record("18992")
    html = f'<html><head><meta name="description" content="{SEO}"></head><body>IZA Discussion Paper No. 18992</body></html>'
    with patch.object(fetch_preprints, "fetch_text", return_value=html) as fetch:
        result = fetch_preprints.enrich_record_from_detail(item, SOURCE, timeout=10)
    assert fetch.call_count == 1
    assert result["abstract"] is None
    assert "origin=none" in capsys.readouterr().out
    assert item["first_seen"] == DATE


def test_wrong_publisher_paper_identity_is_rejected(capsys) -> None:
    item = record("18991")
    html = official_html("18991", (PAR1, PAR2), paper_number="18992")
    with patch.object(fetch_preprints, "fetch_text", return_value=html):
        result = fetch_preprints.enrich_record_from_detail(item, SOURCE, timeout=10)
    assert result["abstract"] is None
    assert "origin=none" in capsys.readouterr().out


def test_absent_or_ambiguous_body_is_not_promoted() -> None:
    assert fetch_preprints.iza_official_body_abstract("<body><p>generic</p></body>", dp_number="18991") is None
    duplicated = official_html("18991", (PAR1, PAR2)).replace(
        "</main>", f'<div class="element-copyexpandable cms-richtext"><p>{PAR1}</p><p>{PAR2}</p></div></main>'
    )
    assert fetch_preprints.iza_official_body_abstract(duplicated, dp_number="18991") is None


def test_non_iza_description_fallback_remains_compatible() -> None:
    item = {**record("18991"), "source_id": "cepr-dp"}
    html = f'<html><head><meta name="description" content="{SEO}"></head><body><h1>Real publisher title</h1></body></html>'
    with patch.object(fetch_preprints, "fetch_text", return_value=html):
        result = fetch_preprints.enrich_record_from_detail(item, {"id": "cepr-dp"}, timeout=10)
    assert result["abstract"] == SEO
