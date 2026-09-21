from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import date_provenance  # noqa: E402

RENDERER = ROOT / "scripts" / "render_site.py"


def test_public_date_rows_use_neutral_label_for_mixed_date_evidence() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")

    # Presentation-only contract: public_date_line() may truthfully surface
    # official-online, Crossref, issue-only, or acceptance-date evidence. The
    # outer UI label must not misclassify every one as an "official date".
    assert '<span class="meta-label">日期信息</span>' in renderer
    assert '<span class="meta-label">官方日期</span>' not in renderer
    assert '<div class="label">日期信息</div>' in renderer
    assert '<div class="label">官方日期</div>' not in renderer


def test_cepr_publisher_timestamp_has_explicit_public_provenance() -> None:
    record = {"date_source": "cepr_published_time"}

    assert date_provenance.date_source_label(record) == "CEPR 页面"
    assert date_provenance.date_kind_label(record) == "官方在线日期"
    assert (
        date_provenance.provenance_text(record, "2026-09-20")
        == "官方在线日期：2026-09-20 · 来源：CEPR 页面"
    )


def test_render_site_keeps_cepr_provenance_label_aligned() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")
    assert 'if date_source == "cepr_published_time":' in renderer
    assert 'return "CEPR 页面"' in renderer
