from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_site


def sample_record(**overrides):
    record = {
        "title": "Static Detail Pilot Paper",
        "title_zh": "静态详情试点论文",
        "authors": ["Ada Economist", "Ben Researcher"],
        "journal": "Journal of Pilot Economics",
        "source_type": "journal",
        "first_seen_at": "2026-09-17T08:09:10+08:00",
        "online_date": "2026-09-16",
        "date_source": "publisher",
        "date_confidence": "A",
        "abstract": "A bounded static-detail pilot abstract.",
        "abstract_zh": "一个有界静态详情试点摘要。",
        "abstract_status": "ok",
        "doi": "10.1234/static.pilot",
        "url": "https://example.test/paper",
        "accepted_date": "2026-09-01",
    }
    record.update(overrides)
    return record


def test_static_detail_body_contains_core_record_without_client_rendering():
    item = render_site.detail_item(sample_record())
    html = render_site.static_detail_body(item)
    assert "Static Detail Pilot Paper" in html
    assert "Ada Economist" in html
    assert "Journal of Pilot Economics" in html
    assert "首次监测" in html
    assert "日期信息" in html
    assert "A bounded static-detail pilot abstract." in html
    assert "正在载入论文详情" not in html
    assert "fetch(" not in html


def test_static_detail_pilot_writes_bounded_deterministic_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(render_site, "DOCS_DIR", tmp_path)
    records = [
        sample_record(title=f"Pilot {index}", doi=f"10.1234/pilot.{index}", url=f"https://example.test/{index}")
        for index in range(render_site.STATIC_DETAIL_PILOT_LIMIT + 5)
    ]
    paths = render_site.write_static_detail_pilot(tmp_path, records)
    assert len(paths) == render_site.STATIC_DETAIL_PILOT_LIMIT
    assert all(path.name == "index.html" for path in paths)
    assert all(path.parent.parent.name == "paper" for path in paths)
    assert all(path.exists() for path in paths)
    assert len(list((tmp_path / "paper").glob("*/index.html"))) == render_site.STATIC_DETAIL_PILOT_LIMIT


def test_pilot_preserves_legacy_detail_links():
    record = sample_record()
    assert render_site.detail_url(record).startswith(f"{render_site.BASE}/paper.html?key=")
