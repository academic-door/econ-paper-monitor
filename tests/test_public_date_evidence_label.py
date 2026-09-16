from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
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
