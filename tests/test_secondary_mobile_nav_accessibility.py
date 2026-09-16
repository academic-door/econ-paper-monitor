from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "render_site.py"


def test_secondary_mobile_menu_accessible_name_tracks_open_state() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")

    # Secondary/detail pages share menu_script(); keep the control name aligned
    # with the action available after each open/close transition.
    assert "menu.setAttribute('aria-label', open ? '关闭导航' : '打开导航');" in renderer
