from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_daily_vnext
import render_site


def sample_record(**overrides):
    record = {
        "id": "static-migration",
        "title": "Static Detail Migration Paper",
        "title_zh": "静态详情迁移论文",
        "authors": ["Ada Economist", "Ben Researcher"],
        "journal": "Journal of Migration Economics",
        "source_type": "journal",
        "_daily_date": "2026-09-18",
        "detected_at": "2026-09-18T08:09:10+08:00",
        "first_seen_at": "2026-09-18T08:09:10+08:00",
        "available_online": "2026-09-17",
        "date_source": "publisher",
        "date_confidence": "A",
        "abstract": "A full static-detail migration abstract.",
        "doi": "10.1234/static.migration",
        "url": "https://example.test/paper",
    }
    record.update(overrides)
    return record


def test_render_site_canonical_detail_url_is_static_route():
    record = sample_record()
    key = render_site.detail_key(record)

    assert render_site.detail_url(record) == f"{render_site.BASE}/paper/{key}/"


def test_daily_vnext_canonical_detail_links_use_static_routes():
    record = sample_record()
    key = build_daily_vnext.detail_key(record)

    root_markup, _ = build_daily_vnext.paper_markup(record, "2026-09-18", None, root_output=True)
    nested_markup, _ = build_daily_vnext.paper_markup(record, "2026-09-18", None, root_output=False)

    assert f'href="paper/{key}/"' in root_markup
    assert f'href="../paper/{key}/"' in nested_markup
    assert "paper.html?key=" not in root_markup
    assert "paper.html?key=" not in nested_markup


def test_full_static_detail_writer_covers_all_unique_public_records(tmp_path, monkeypatch):
    monkeypatch.setattr(render_site, "DOCS_DIR", tmp_path)
    records = [
        sample_record(
            id=f"paper-{index}",
            title=f"Migration Paper {index}",
            doi=f"10.1234/migration.{index}",
            url=f"https://example.test/{index}",
        )
        for index in range(19)
    ]

    paths = render_site.write_static_detail_pages(tmp_path, records)

    assert len(paths) == 19
    assert len(list((tmp_path / "paper").glob("*/index.html"))) == 19
    assert all(path.exists() for path in paths)
    assert all("正在载入论文详情" not in path.read_text(encoding="utf-8") for path in paths)
