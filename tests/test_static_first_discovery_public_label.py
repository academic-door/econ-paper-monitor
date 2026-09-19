from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "render_site.py"


def test_static_public_surfaces_use_site_first_discovery_wording() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")

    assert 'return f"本站首次发现 {detected_date(record)}' in renderer
    assert '"first_seen": "本站首次发现"' in renderer
    assert '"F": "F：仅本站首次发现"' in renderer
    assert '<div class="label">本站首次发现</div>' in renderer
    assert '本站首次发现时间' in renderer

    assert '"first_seen": "首次监测"' not in renderer
    assert '<div class="label">首次监测</div>' not in renderer
