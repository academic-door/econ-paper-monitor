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
    assert "本站首次发现" in html
    assert "日期信息" in html
    assert "A bounded static-detail pilot abstract." in html
    assert "正在载入论文详情" not in html
    assert "fetch(" not in html


def test_legacy_detail_shell_remains_available_for_existing_query_links():
    html = render_site.paper_detail_body()

    assert "URLSearchParams" in html
    assert "paper-data/" in html
    assert "正在载入论文详情" in html
