from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts" / "templates" / "daily_vnext.html"


def template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_mobile_menu_has_explicit_44px_touch_target() -> None:
    template = template_text()

    assert ".menu{display:none;min-width:44px;min-height:44px;" in template


def test_mobile_menu_accessible_name_tracks_open_state() -> None:
    template = template_text()

    assert "menu.setAttribute('aria-label', open ? '关闭导航' : '打开导航');" in template
