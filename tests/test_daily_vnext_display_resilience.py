import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def make_record(index: int, *, authors=True, doi=True, topics=True):
    record = {
        "id": f"fixture-{index}",
        "title": f"A long English research title for fixture {index}",
        "title_zh": f"用于展示稳定性测试的超长中文研究标题 {index}",
        "url": f"https://example.com/paper/{index}",
        "source": "fixture-source",
        "journal": "Fixture Journal",
        "source_type": "journal" if index % 3 else "working_paper",
        "first_seen_at": f"2026-07-29T{index % 24:02d}:15:00+08:00",
        "china_relevance_status": "confirmed" if index == 1 else "none",
    }
    if authors:
        record["authors"] = [f"Author {index}"] + [f"Additional author {n}" for n in range(8)]
    if doi:
        record["doi"] = f"10.1234/fixture.{index}"
    if topics:
        record["fields"] = ["macro", "finance", "trade", "development"]
    return record


@pytest.fixture
def daily_builder(monkeypatch, tmp_path):
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import build_daily_vnext

    daily_dir = tmp_path / "data" / "daily"
    daily_dir.mkdir(parents=True)
    monkeypatch.setattr(build_daily_vnext, "DAILY_DIR", daily_dir)
    return build_daily_vnext, daily_dir


@pytest.mark.parametrize("count", [0, 1, 20, 50])
def test_fixture_sizes_render_without_layout_contract_breaks(daily_builder, tmp_path, count):
    builder, daily_dir = daily_builder
    records = [make_record(i) for i in range(count)]
    (daily_dir / "2026-07-29.json").write_text(json.dumps(records), encoding="utf-8")
    output = tmp_path / "docs" / "daily-vnext" / "index.html"
    report = tmp_path / "report.json"

    result = builder.build("2026-07-29", ROOT / "scripts" / "templates" / "daily_vnext.html", output, report)

    html = output.read_text(encoding="utf-8")
    assert len(re.findall(r'class="paper-entry\b', html)) == count
    assert result["final_render_count"] == count
    assert "今日暂无新发现" in html if count == 0 else "今日研究时间流" in html


def test_missing_optional_fields_are_hidden_and_search_index_stays_compact(daily_builder, tmp_path):
    builder, daily_dir = daily_builder
    record = make_record(1, authors=False, doi=False, topics=False)
    record["china_relevance_status"] = "none"
    record["abstract"] = "A very long abstract that must never be copied into data-search."
    record["summary"] = "A summary that must never be copied into data-search."
    (daily_dir / "2026-07-29.json").write_text(json.dumps([record]), encoding="utf-8")
    output = tmp_path / "index.html"

    builder.build("2026-07-29", ROOT / "scripts" / "templates" / "daily_vnext.html", output, tmp_path / "report.json")
    html = output.read_text(encoding="utf-8")
    entry = re.search(r'<article class="paper-entry".*?</article>', html, re.S).group(0)
    assert 'class="authors"' not in entry
    assert "DOI：" not in entry
    assert 'class="tags"' not in entry
    assert "A very long abstract" not in entry
    assert "A summary that must never" not in entry


def test_failed_generation_preserves_previous_valid_output(daily_builder, tmp_path):
    builder, daily_dir = daily_builder
    (daily_dir / "2026-07-29.json").write_text(json.dumps([make_record(1)]), encoding="utf-8")
    output = tmp_path / "index.html"
    report = tmp_path / "report.json"
    template = ROOT / "scripts" / "templates" / "daily_vnext.html"
    builder.build("2026-07-29", template, output, report)
    before = output.read_bytes()
    (daily_dir / "2026-07-29.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid"):
        builder.build("2026-07-29", template, output, report)
    assert output.read_bytes() == before


@pytest.mark.parametrize(
    ("title", "title_zh", "primary", "secondary"),
    [
        ("English title", "中文标题", "中文标题", "English title"),
        ("English title", "", "English title", None),
        ("Same title", "Same title", "Same title", None),
    ],
)
def test_shared_title_contract_controls_primary_and_secondary_titles(
    daily_builder, tmp_path, title, title_zh, primary, secondary
):
    builder, daily_dir = daily_builder
    record = make_record(1)
    record.update({"title": title, "title_zh": title_zh})
    (daily_dir / "2026-07-29.json").write_text(json.dumps([record]), encoding="utf-8")
    output = tmp_path / "index.html"
    builder.build("2026-07-29", ROOT / "scripts" / "templates" / "daily_vnext.html", output, tmp_path / "report.json")
    html = output.read_text(encoding="utf-8")
    assert f">{primary}</a>" in html
    if secondary:
        assert f'class="english-title">{secondary}</p>' in html
    else:
        assert 'class="english-title"' not in html


def test_runtime_liveness_alert_does_not_shift_page_content():
    template = (ROOT / "scripts" / "templates" / "daily_vnext.html").read_text(encoding="utf-8")

    assert ".runtime-alert{position:fixed;" in template
    assert 'data-monitor-liveness role="status" aria-live="polite" hidden' in template
    assert "monitorTarget.hidden = false;" in template


def test_homepage_template_defers_offscreen_rendering_and_lazy_motion():
    template = (ROOT / "scripts" / "templates" / "daily_vnext.html").read_text(encoding="utf-8")
    assert "content-visibility:auto" in template
    assert "contain-intrinsic-size:auto 220px" in template
    assert "IntersectionObserver" in template
    assert "rootMargin: '1200px 0px'" in template


def test_display_build_does_not_modify_data(tmp_path):
    data_files = sorted((ROOT / "data").rglob("*"))
    before = {
        path.relative_to(ROOT / "data"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in data_files
        if path.is_file()
    }
    assert before

    import subprocess
    subprocess.run(
        ["python", "scripts/render_site.py", "--docs-dir", str(tmp_path / "docs")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    after = {
        path.relative_to(ROOT / "data"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in data_files
        if path.is_file()
    }
    assert after == before


def test_secondary_pages_use_vnext_shell_and_preserve_classic(tmp_path):
    import subprocess

    classic = ROOT / "docs" / "classic" / "index.html"
    before_classic = hashlib.sha256(classic.read_bytes()).hexdigest()

    subprocess.run(
        ["python", "scripts/render_site.py", "--docs-dir", str(tmp_path / "docs")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    for relative in [
        "recent72/index.html",
        "topics/china/index.html",
        "journals/index.html",
        "working-papers/index.html",
        "search/index.html",
    ]:
        html = (tmp_path / "docs" / relative).read_text(encoding="utf-8")
        assert 'class="site-header"' in html
        assert 'class="secondary-page"' in html
        assert 'class="context-nav"' in html
        assert 'class="sidebar"' not in html
        assert 'data-filter-scope="' in html or "journal-table" in html

    archive_html = (tmp_path / "docs" / "archive" / "index.html").read_text(encoding="utf-8")
    assert 'name="robots" content="noindex,follow"' in archive_html
    assert 'http-equiv="refresh" content="0;url=../search/"' in archive_html
    assert 'class="site-header"' not in archive_html

    assert hashlib.sha256(classic.read_bytes()).hexdigest() == before_classic


def test_secondary_page_titles_prefer_chinese_and_keep_original():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import render_site

    record = make_record(1)
    rendered = render_site.paper_events([record], scope="fixture")

    assert f">{record['title_zh']}</a></h3>" in rendered
    assert f'<p class="title-original">{record["title"]}</p>' in rendered
    assert rendered.index(record["title_zh"]) < rendered.index(record["title"])

    record.pop("title_zh")
    fallback = render_site.paper_events([record], scope="fixture")
    assert f">{record['title']}</a></h3>" in fallback
    assert 'class="title-original"' not in fallback
