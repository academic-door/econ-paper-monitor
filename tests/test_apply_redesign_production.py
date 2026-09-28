from __future__ import annotations

import tempfile
from pathlib import Path

from scripts.apply_redesign_production import (
    SCRIPT_ID,
    STYLE_ID,
    apply,
    process_html,
)


BASE_HTML = """<!doctype html>
<html><head>
<meta name="robots" content="index,follow">
<link rel="canonical" href="https://academic-door.github.io/econ-paper-monitor/recent72/">
<meta property="og:url" content="https://academic-door.github.io/econ-paper-monitor/recent72/">
</head><body>
<header class="site-header"><a class="wordmark" href="/">Econ Papers Daily</a>
<nav class="nav" id="primary-nav"><a href="/">今日</a><a href="/recent72/">最近72小时</a><a href="/journals/">期刊</a><a href="/working-papers/">工作论文</a><a href="/topics/china/">中国研究</a><a href="/search/">搜索</a></nav>
<span class="presence">在线</span></header>
<script>const epd_presence_client = true;</script>
</body></html>"""


def test_production_transform_preserves_indexing_and_canonical() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "index.html"
        path.write_text(BASE_HTML, encoding="utf-8")
        process_html(path)
        text = path.read_text(encoding="utf-8")

    assert 'content="index,follow"' in text
    assert 'rel="canonical" href="https://academic-door.github.io/econ-paper-monitor/recent72/"' in text
    assert 'property="og:url" content="https://academic-door.github.io/econ-paper-monitor/recent72/"' in text
    assert "Daily Door Redesign Preview v1 · 非正式页面" not in text
    assert "noindex,nofollow,noarchive" not in text
    assert f'id="{STYLE_ID}"' in text
    assert f'id="{SCRIPT_ID}"' in text
    assert "epd_presence_client" not in text


def test_production_transform_preserves_shared_home_interaction_script() -> None:
    shared_html = BASE_HTML.replace(
        "<script>const epd_presence_client = true;</script>",
        """<script>
const buttons = [...document.querySelectorAll('[data-filter]')];
window.__dailyVnextDebug = {getVisibleCount: () => 1};
const epd_presence_client = true;
</script>""",
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "index.html"
        path.write_text(shared_html, encoding="utf-8")
        process_html(path)
        text = path.read_text(encoding="utf-8")

    assert "querySelectorAll('[data-filter]')" in text
    assert "__dailyVnextDebug" in text
    assert "epd_presence_client" not in text
    assert "econ-paper-monitor-presence.academic-door.workers.dev/presence" not in text


def test_production_transform_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "index.html"
        path.write_text(BASE_HTML, encoding="utf-8")
        process_html(path)
        first = path.read_text(encoding="utf-8")
        process_html(path)
        second = path.read_text(encoding="utf-8")

    assert first == second
    assert second.count(f'id="{STYLE_ID}"') == 1
    assert second.count(f'id="{SCRIPT_ID}"') == 1


def test_apply_only_targets_approved_public_surfaces() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        targets = [
            root / "index.html",
            root / "recent72/index.html",
            root / "topics/china/index.html",
            root / "search/index.html",
            root / "working-papers/index.html",
            root / "journals/index.html",
            root / "journals/example/index.html",
        ]
        untouched = root / "paper/example/index.html"
        for path in [*targets, untouched]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(BASE_HTML, encoding="utf-8")

        assert apply(root) == len(targets)
        for path in targets:
            assert f'id="{STYLE_ID}"' in path.read_text(encoding="utf-8")
        assert f'id="{STYLE_ID}"' not in untouched.read_text(encoding="utf-8")
