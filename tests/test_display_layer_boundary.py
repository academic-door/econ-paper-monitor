from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_only_homepage_generator_can_target_root_homepage():
    renderer = (ROOT / "scripts" / "render_site.py").read_text(encoding="utf-8")
    homepage = (ROOT / "scripts" / "build_daily_vnext.py").read_text(encoding="utf-8")
    assert 'args.docs_dir / "index.html"' not in renderer
    assert 'args.docs_dir / "classic"' not in renderer
    assert 'ROOT / "docs" / "index.html"' in homepage


def test_display_workflow_delegates_to_docs_only_publisher():
    workflow = (ROOT / ".github" / "workflows" / "render-site.yml").read_text(encoding="utf-8")
    publisher = (ROOT / "scripts" / "render_published_site.sh").read_text(encoding="utf-8")
    assert 'paths:\n      - "data/**"' not in workflow
    assert "scripts/render_published_site.sh" in workflow
    assert "scripts/render_site.py" in workflow
    assert "scripts/build_daily_vnext.py" in workflow
    assert "scripts/display_contract.py" in workflow
    assert "scripts/build_feed.py" in workflow
    assert "bash scripts/render_published_site.sh" in workflow
    assert "git add docs" in publisher
    assert "git add data" not in publisher
    assert "git push origin HEAD:main" in publisher


def test_monitor_and_display_workflows_cannot_form_a_push_loop():
    monitor = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    display = (ROOT / ".github" / "workflows" / "render-site.yml").read_text(encoding="utf-8")
    assert "git add data docs" not in monitor
    assert "git add data" in monitor
    assert "bash scripts/render_published_site.sh" in monitor
    assert "gh workflow run render-site.yml" not in monitor
    assert 'paths:\n      - "data/**"' not in display
    assert '      - "docs/**"' not in display


def test_non_classic_navigation_has_no_archive_entry():
    renderer = (ROOT / "scripts" / "render_site.py").read_text(encoding="utf-8")
    template = (ROOT / "scripts" / "templates" / "daily_vnext.html").read_text(encoding="utf-8")
    assert 'href="{BASE}/archive/"' not in renderer
    assert 'href="../archive/"' not in template


def test_classic_surface_is_not_owned_by_display_renderer():
    renderer = (ROOT / "scripts" / "render_site.py").read_text(encoding="utf-8")
    assert 'args.docs_dir / "classic"' not in renderer


def test_archive_is_a_noindex_search_compatibility_page():
    renderer = (ROOT / "scripts" / "render_site.py").read_text(encoding="utf-8")
    assert 'meta name="robots" content="noindex,follow"' in renderer
    assert 'meta http-equiv="refresh" content="0;url={BASE}/search/"' in renderer


def test_public_templates_and_admin_pages_are_retired():
    assert not (ROOT / "docs" / "daily-vnext" / "template.html").exists()
    assert not (ROOT / "docs" / "quality").exists()
    assert not (ROOT / "docs" / "admin").exists()


def test_secondary_renderer_owns_detail_page_and_shared_title_contract():
    renderer = (ROOT / "scripts" / "render_site.py").read_text(encoding="utf-8")
    assert 'args.docs_dir / "paper.html"' in renderer
    assert '"title_primary": title_primary' in renderer
    assert '"title_secondary": title_secondary' in renderer
    assert "item.title_primary || item.title_zh || item.title" in renderer


def test_renderer_accepts_relative_isolated_docs_directory(tmp_path):
    output = ROOT / ".test-render-output" / tmp_path.name
    try:
        subprocess.run(
            [sys.executable, "scripts/render_site.py", "--docs-dir", str(output.relative_to(ROOT))],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert (output / "paper.html").exists()
        assert (output / "recent72" / "index.html").exists()
    finally:
        shutil.rmtree(output.parent, ignore_errors=True)
