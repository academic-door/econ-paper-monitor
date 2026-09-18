"""Tests for the provider usage aggregation report."""

from __future__ import annotations

import json
import sys
from datetime import datetime as _datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_semantic_scholar_usage import build_usage  # noqa: E402

from datetime import datetime, timedelta, timezone  # noqa: E402

_UTC_NOW = datetime.now(timezone.utc)
_BJ_DATE = (_UTC_NOW + timedelta(hours=8)).date()
_TS_LATEST = f"{_BJ_DATE}T03:30:18-04:00"
_TS_RUN2 = f"{_BJ_DATE - timedelta(days=1)}T10:00:00+00:00"
_TS_RUN3 = f"{_BJ_DATE - timedelta(days=2)}T22:00:00+00:00"
_TS_KEEPALIVE = f"{_BJ_DATE}T03:30:20-04:00"
_EXPECTED_DAYS = [str(_BJ_DATE - timedelta(days=1)), str(_BJ_DATE)]


class _FixedNow(_datetime):
    @classmethod
    def now(cls, tz=None):
        return _datetime(2026, 8, 6, tzinfo=tz)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    write_json(
        data_dir / "metadata_provider_health.json",
        {
            "latest": {
                "candidates": 150,
                "checked_at": _TS_LATEST,
                "providers": {
                    "semantic-scholar": {
                        "api_key_configured": True,
                        "attempts": 150,
                        "available": 100,
                        "statuses": {"not_found": 20, "rate_limited": 30},
                    },
                    "elsevier": {
                        "api_key_configured": True,
                        "inst_token_configured": True,
                        "attempts": 150,
                        "available": 47,
                        "empty": 103,
                        "statuses": {"available": 47},
                        "rate_limit_headers": None,
                    },
                },
            },
            "runs": [
                {
                    "candidates": 150,
                    "checked_at": _TS_RUN2,
                    "providers": {
                        "semantic-scholar": {
                            "api_key_configured": True,
                            "attempts": 150,
                            "available": 120,
                            "statuses": {"not_found": 25, "rate_limited": 5},
                        },
                        "elsevier": {
                            "api_key_configured": True,
                            "inst_token_configured": True,
                            "attempts": 100,
                            "available": 40,
                            "empty": 60,
                            "statuses": {"available": 40},
                            "rate_limit_headers": {
                                "X-RateLimit-Limit": "5000",
                                "X-RateLimit-Remaining": "200",
                            },
                        },
                    },
                },
                {
                    "candidates": 150,
                    "checked_at": _TS_RUN3,
                    "providers": {
                        "semantic-scholar": {
                            "api_key_configured": False,
                            "attempts": 150,
                            "available": 90,
                            "statuses": {"not_found": 30, "rate_limited": 30},
                        },
                        "elsevier": {
                            "api_key_configured": True,
                            "inst_token_configured": True,
                            "attempts": 0,
                            "available": 0,
                            "empty": 0,
                            "statuses": {},
                            "rate_limit_headers": None,
                        },
                    },
                },
            ],
        },
    )
    return data_dir


def test_semantic_scholar_aggregates_totals_and_days(tmp_path: Path) -> None:
    data_dir = make_data_dir(tmp_path)

    usage = build_usage(data_dir)

    assert usage["key_configured"] is True
    assert usage["total"]["attempts"] == 450
    assert usage["total"]["available"] == 310
    assert usage["total"]["rate_limited"] == 65
    assert usage["total"]["not_found"] == 75
    assert usage["total"]["runs"] == 3
    assert usage["last_used_at"] == _TS_LATEST
    assert [row["date"] for row in usage["by_day"]] == _EXPECTED_DAYS
    assert usage["by_day"][0]["attempts"] == 300  # 08-03 22:00 UTC == Beijing 08-04
    assert usage["by_day"][0]["rate_limited"] == 35
    assert isinstance(usage["days_since_last_use"], int)


def test_elsevier_aggregates_weekly_and_rate_headers(tmp_path: Path) -> None:
    data_dir = make_data_dir(tmp_path)

    with patch("build_semantic_scholar_usage.datetime", _FixedNow):
        usage = build_usage(data_dir)
    els = usage["providers"]["elsevier"]

    assert els["api_key_configured"] is True
    assert els["inst_token_configured"] is True
    assert els["total"]["attempts"] == 250
    assert els["total"]["available"] == 87
    assert els["total"]["empty"] == 163
    assert els["weekly_requests_7d"] == 250
    assert els["last_used_at"] == _TS_LATEST
    assert els["rate_limit_headers"] == {
        "X-RateLimit-Limit": "5000",
        "X-RateLimit-Remaining": "200",
    }
    assert [row["date"] for row in els["by_day"]] == _EXPECTED_DAYS


def test_empty_health_is_honest(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    write_json(data_dir / "metadata_provider_health.json", {"runs": [], "latest": {}})

    usage = build_usage(data_dir)

    assert usage["total"]["attempts"] == 0
    assert usage["by_day"] == []
    assert usage["key_configured"] is False
    assert usage["days_since_last_use"] is None
    assert usage["providers"]["elsevier"]["weekly_requests_7d"] == 0
    assert usage["providers"]["elsevier"]["rate_limit_headers"] is None
