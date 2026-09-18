"""Tests for the API usage page renderer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from render_semantic_scholar_usage import render_usage  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def sample_usage() -> dict:
    return {
        "updated_at": "2026-08-05T12:00:00+00:00",
        "key_configured": True,
        "providers": {
            "semantic-scholar": {
                "api_key_configured": True,
                "last_used_at": "2026-08-05T08:00:00+00:00",
                "weekly_requests_7d": 5095,
                "rate_limit_headers": None,
                "total": {
                    "attempts": 5095,
                    "available": 1356,
                    "not_found": 714,
                    "rate_limited": 405,
                    "skipped": 2620,
                    "http_error": 0,
                    "empty": 0,
                    "runs": 21,
                },
                "by_day": [
                    {
                        "date": "2026-08-05",
                        "attempts": 1350,
                        "available": 652,
                        "not_found": 567,
                        "rate_limited": 117,
                        "skipped": 14,
                        "http_error": 0,
                        "empty": 0,
                        "runs": 9,
                    }
                ],
            },
            "elsevier": {
                "api_key_configured": True,
                "inst_token_configured": True,
                "last_used_at": "2026-08-05T08:00:00+00:00",
                "weekly_requests_7d": 4495,
                "rate_limit_headers": None,
                "total": {
                    "attempts": 4495,
                    "available": 1594,
                    "empty": 2901,
                    "not_found": 0,
                    "rate_limited": 0,
                    "skipped": 0,
                    "http_error": 0,
                    "runs": 17,
                },
                "by_day": [
                    {
                        "date": "2026-08-05",
                        "attempts": 1350,
                        "available": 496,
                        "empty": 854,
                        "not_found": 0,
                        "rate_limited": 0,
                        "skipped": 0,
                        "http_error": 0,
                        "runs": 9,
                    }
                ],
            },
        },
    }


def test_renders_both_providers_with_one_row_cards_and_beijing_time(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    docs_dir = tmp_path / "docs"
    write_json(data_dir / "semantic_scholar_usage.json", sample_usage())

    out = render_usage(data_dir, docs_dir)

    assert out == docs_dir / "usage" / "index.html"
    html = out.read_text(encoding="utf-8")
    assert "学术 API 用量" in html
    assert "Semantic Scholar" in html and "Elsevier" in html
    assert "grid-template-columns:repeat(6,1fr)" in html  # six KPI cards on one row
    assert "2026-08-05 20:00 北京时间" in html  # Beijing time for 12:00 UTC
    assert "5,095" in html and "4,495" in html
    assert "16,000" in html and "20,000" in html
    assert "60 天" not in html
    assert "keep-alive" not in html.casefold()


def test_missing_usage_renders_placeholder(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    docs_dir = tmp_path / "docs"

    out = render_usage(data_dir, docs_dir)

    html = out.read_text(encoding="utf-8")
    assert "用量数据尚未生成" in html
    assert "学术 API 用量" in html


def test_old_shape_without_providers_falls_back(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    docs_dir = tmp_path / "docs"
    old = {
        "updated_at": "2026-08-05T12:00:00+00:00",
        "key_configured": True,
        "total": {"attempts": 5095, "available": 1356},
        "by_day": [
            {
                "date": "2026-08-05",
                "attempts": 1350,
                "available": 652,
                "not_found": 567,
                "rate_limited": 117,
                "skipped": 14,
                "http_error": 0,
                "empty": 0,
                "runs": 9,
            }
        ],
    }
    write_json(data_dir / "semantic_scholar_usage.json", old)

    html = render_usage(data_dir, docs_dir).read_text(encoding="utf-8")

    assert "Semantic Scholar" in html
    assert "5,095" in html
    assert "Elsevier" in html  # placeholder section, page must not crash


def test_usage_values_are_escaped(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    docs_dir = tmp_path / "docs"
    usage = sample_usage()
    usage["updated_at"] = "<script>alert(1)</script>"
    usage["providers"]["semantic-scholar"]["by_day"][0]["date"] = "<img src=x onerror=alert(1)>"
    write_json(data_dir / "semantic_scholar_usage.json", usage)

    html = render_usage(data_dir, docs_dir).read_text(encoding="utf-8")

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html