from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import render_site  # noqa: E402
import build_daily_vnext  # noqa: E402


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


def test_today_home_excludes_clearly_old_catalogue_backfill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old_backfill = {
        "id": "old",
        "title": "Old CEPR catalogue item",
        "url": "https://cepr.org/publications/dp19997",
        "first_seen_at": "2026-09-19T13:12:00+08:00",
        "available_online": "2025-03-05",
        "published_online": "2025-03-05",
        "date_source": "publisher_detail",
        "date_confidence": "A",
    }
    recent_discovery = {
        "id": "recent",
        "title": "Recent delayed discovery",
        "url": "https://example.org/recent",
        "first_seen_at": "2026-09-19T13:13:00+08:00",
        "available_online": "2026-09-18",
        "published_online": "2026-09-18",
        "date_source": "publisher_detail",
        "date_confidence": "A",
    }

    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    (daily_dir / "2026-09-19.json").write_text(
        __import__("json").dumps([old_backfill, recent_discovery]),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_daily_vnext, "DAILY_DIR", daily_dir)

    records, archive_count = build_daily_vnext.load_records("2026-09-19")

    assert archive_count == 2
    assert [record["id"] for record in records] == ["recent"]

    monkeypatch.setattr(render_site, "today_str", lambda: "2026-09-19")
    secondary_old = dict(old_backfill, detected_at=old_backfill["first_seen_at"])
    secondary_recent = dict(recent_discovery, detected_at=recent_discovery["first_seen_at"])
    assert render_site.is_today_home_flow_record(secondary_old) is False
    assert render_site.is_today_home_flow_record(secondary_recent) is True


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


def test_today_home_keeps_old_weak_metadata_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    weak_old = {
        "id": "weak-old",
        "title": "Old date from weak metadata only",
        "url": "https://example.org/weak-old",
        "first_seen_at": "2026-09-19T14:00:00+08:00",
        "available_online": "2025-03-05",
        "published_online": "2025-03-05",
        "date_source": "crossref_published_online",
        "date_confidence": "C",
    }

    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    (daily_dir / "2026-09-19.json").write_text(
        __import__("json").dumps([weak_old]),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_daily_vnext, "DAILY_DIR", daily_dir)

    records, _ = build_daily_vnext.load_records("2026-09-19")
    assert [record["id"] for record in records] == ["weak-old"]

    monkeypatch.setattr(render_site, "today_str", lambda: "2026-09-19")
    secondary = dict(weak_old, detected_at=weak_old["first_seen_at"])
    assert render_site.is_today_home_flow_record(secondary) is True



def test_policy_commentary_never_enters_working_paper_or_journal_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(render_site, "today_str", lambda: "2026-09-26")
    commentary = {
        "id": "commentary",
        "title": "Quantifying financial repression through the lens of portfolio choice: A century of evidence",
        "url": "https://cepr.org/voxeu/columns/example",
        "journal": "VoxEU / CEPR Columns",
        "journal_id": "source-voxeu-cepr-columns",
        "source": "working_papers",
        "source_id": "voxeu-cepr-columns",
        "source_type": "policy_commentary",
        "detected_at": "2026-09-26T00:53:06+00:00",
    }
    working = {
        "id": "working",
        "title": "Actual working paper",
        "url": "https://example.org/wp",
        "journal": "IMF Working Papers",
        "journal_id": "source-imf-working-papers",
        "source": "working_papers",
        "source_id": "imf-working-papers",
        "source_type": "working_paper",
        "detected_at": "2026-09-26T01:00:00+00:00",
    }
    journal = {
        "id": "journal",
        "title": "Actual journal article",
        "doi": "10.1234/example",
        "url": "https://doi.org/10.1234/example",
        "journal": "Example Journal",
        "journal_id": "example-journal",
        "source_type": "journal_article",
        "detected_at": "2026-09-26T01:10:00+00:00",
    }

    assert render_site.public_content_type(commentary) == "commentary"
    assert render_site.is_policy_commentary(commentary) is True
    assert render_site.is_working_paper(commentary) is False
    assert render_site.is_journal_article(commentary) is False
    assert build_daily_vnext.content_type(commentary) == "column"

    assert [record["id"] for record in render_site.working_paper_records([commentary, working, journal])] == ["working"]

    working_html = render_site.working_papers_body([commentary, working, journal], view="today")
    assert "Actual working paper" in working_html
    assert "Quantifying financial repression" not in working_html

    search_html = render_site.search_body([commentary, working, journal])
    assert "Quantifying financial repression" in search_html
    assert "研究评论" in search_html
    assert "<strong>1</strong><span>期刊论文</span>" in search_html
    assert "<strong>1</strong><span>工作论文/机构研究</span>" in search_html
