from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "render_site.py"
TEMPLATE = ROOT / "scripts" / "templates" / "daily_vnext.html"


def test_public_nav_uses_one_task_oriented_order_without_visible_rss() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")

    home_order = [
        'href="../">今日</a>',
        'href="../recent72/">最近72小时</a>',
        'href="../journals/">期刊</a>',
        'href="../working-papers/">工作论文</a>',
        'href="../topics/china/">中国研究</a>',
        'href="../search/">搜索</a>',
    ]
    secondary_order = [
        'href="{BASE}/">今日</a>',
        'href="{BASE}/recent72/">最近72小时</a>',
        'href="{BASE}/journals/">期刊</a>',
        'href="{BASE}/working-papers/">工作论文</a>',
        'href="{BASE}/topics/china/">中国研究</a>',
        'href="{BASE}/search/">搜索</a>',
    ]
    assert [template.index(item) for item in home_order] == sorted(template.index(item) for item in home_order)
    assert [renderer.index(item) for item in secondary_order] == sorted(renderer.index(item) for item in secondary_order)
    assert '<a href="../feed.xml">RSS</a>' not in template
    assert '<a href="{BASE}/feed.xml">RSS</a>' not in renderer


def test_secondary_stats_do_not_repeat_section_rule() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")

    assert '.stats,.audit-grid{display:grid' in renderer
    assert '.stats,.audit-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;border-top:' not in renderer
    assert '.audit-grid{border-top:1px solid var(--line)}' in renderer


def test_secondary_records_gain_progressive_scroll_reveal() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")

    assert "def secondary_motion_script()" in renderer
    assert "IntersectionObserver" in renderer
    assert "MutationObserver" in renderer
    assert "prefers-reduced-motion: reduce" in renderer
    assert "reveal-pending" in renderer
    assert "reveal-visible" in renderer
