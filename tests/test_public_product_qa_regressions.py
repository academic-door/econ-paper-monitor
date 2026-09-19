from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import render_site  # noqa: E402


def test_missing_discovery_time_never_renders_monitor_as_a_time() -> None:
    record = {
        "_daily_date": "2026-09-18",
        "title": "DP10004 Curriculum and Ideology",
        "journal": "CEPR Discussion Papers",
        "journal_id": "source-cepr-dp",
        "source_type": "working_paper",
        "url": "https://cepr.org/publications/dp10004",
    }

    assert render_site.detected_time(record) == ""
    assert render_site.detected_label(record) == "本站首次发现 2026-09-18"

    html = render_site.paper_events([record])
    assert '<div class="time">—</div>' in html
    assert "本站首次发现 2026-09-18 监测" not in html


def test_today_home_excludes_clearly_old_catalogue_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render_site, "today_str", lambda: "2026-09-19")
    old_backfill = {
        "title": "Old CEPR catalogue item",
        "detected_at": "2026-09-19T05:12:00+00:00",
        "available_online": "2025-03-05",
        "published_online": "2025-03-05",
        "date_source": "publisher_detail",
        "date_confidence": "A",
    }
    recent_discovery = {
        "title": "Recent delayed discovery",
        "detected_at": "2026-09-19T05:12:00+00:00",
        "available_online": "2026-09-18",
        "published_online": "2026-09-18",
        "date_source": "publisher_detail",
        "date_confidence": "A",
    }

    assert render_site.is_today_home_flow_record(old_backfill) is False
    assert render_site.is_today_home_flow_record(recent_discovery) is True


def test_search_summary_and_lazy_result_count_use_same_unique_records() -> None:
    records = []
    for index in range(41):
        records.append(
            {
                "id": f"paper-{index}",
                "title": f"Paper {index}",
                "doi": f"10.1234/example.{index}",
                "journal": "Example Journal",
                "journal_id": "example-journal",
                "source_type": "journal_article",
                "detected_at": "2026-09-19T00:00:00+00:00",
            }
        )
    records.append(dict(records[0]))

    html = render_site.search_body(records)

    assert re.search(r"<p>41 条</p>", html)
    assert "浏览全部 41 篇" in html
    assert "42 条" not in html


def test_large_verified_discovery_lag_is_labeled_as_historical_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render_site, "today_str", lambda: "2026-09-19")
    record = {
        "_daily_date": "2026-09-18",
        "title": "DP10004 Curriculum and Ideology",
        "detected_at": "",
        "available_online": "2014-06-01",
        "published_online": "2014-06-01",
        "date_source": "publisher_detail",
        "date_confidence": "A",
    }

    assert render_site.detection_lag_days(record) is not None
    assert "历史补录" in render_site.detection_lag_chip(record)
    assert "日期需核验" not in render_site.detection_lag_chip(record)
