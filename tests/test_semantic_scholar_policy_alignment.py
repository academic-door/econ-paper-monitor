from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_semantic_scholar_usage import build_usage
from render_semantic_scholar_usage import render_usage


def test_update_workflow_uses_only_canonical_semantic_scholar_secret():
    workflow = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")

    assert "Semantic Scholar key keep-alive" not in workflow
    assert "semantic_scholar_keepalive.py" not in workflow
    assert "secrets.S2_API_KEY" not in workflow
    assert "secrets.SEMANTIC_SCHOLAR_API_KEY" in workflow


def test_synthetic_keepalive_artifacts_are_retired():
    assert not (ROOT / "scripts" / "semantic_scholar_keepalive.py").exists()
    assert not (ROOT / "data" / "semantic_scholar_keepalive.json").exists()


def test_runtime_code_no_longer_reads_legacy_s2_alias():
    enrich = (ROOT / "scripts" / "enrich_metadata.py").read_text(encoding="utf-8")
    recover = (ROOT / "scripts" / "recover_metadata_batch.py").read_text(encoding="utf-8")

    assert 'os.environ.get("S2_API_KEY")' not in enrich
    assert 'os.environ.get("S2_API_KEY")' not in recover
    assert 'os.environ.get("SEMANTIC_SCHOLAR_API_KEY")' in enrich
    assert 'os.environ.get("SEMANTIC_SCHOLAR_API_KEY")' in recover


def test_usage_report_is_grounded_only_in_legitimate_provider_health(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "metadata_provider_health.json").write_text(
        json.dumps(
            {
                "latest": {
                    "checked_at": "2026-09-18T10:48:01+00:00",
                    "providers": {
                        "semantic-scholar": {
                            "api_key_configured": True,
                            "attempts": 4,
                            "available": 2,
                            "statuses": {"rate_limited": 2},
                        }
                    },
                },
                "runs": [],
            }
        ),
        encoding="utf-8",
    )
    # A stale legacy artifact must no longer influence the report.
    (data_dir / "semantic_scholar_keepalive.json").write_text(
        json.dumps(
            {
                "checked_at": "2026-01-01T00:00:00+00:00",
                "ok": True,
                "reason": "rate_limited",
            }
        ),
        encoding="utf-8",
    )

    usage = build_usage(data_dir)

    assert usage["key_configured"] is True
    assert usage["total"]["attempts"] == 4
    assert usage["last_used_at"] == "2026-09-18T10:48:01+00:00"
    assert not any("keepalive" in key.casefold() for key in usage)


def test_usage_page_does_not_publish_inactivity_keepalive_claim(tmp_path: Path):
    data_dir = tmp_path / "data"
    docs_dir = tmp_path / "docs"
    data_dir.mkdir(parents=True)
    (data_dir / "semantic_scholar_usage.json").write_text(
        json.dumps(
            {
                "updated_at": "2026-09-18T10:48:01+00:00",
                "key_configured": True,
                "providers": {
                    "semantic-scholar": {
                        "api_key_configured": True,
                        "last_used_at": "2026-09-18T10:48:01+00:00",
                        "weekly_requests_7d": 4,
                        "rate_limit_headers": None,
                        "total": {
                            "attempts": 4,
                            "available": 2,
                            "empty": 0,
                            "not_found": 0,
                            "rate_limited": 2,
                            "skipped": 0,
                            "http_error": 0,
                            "provider_error": 0,
                            "runs": 1,
                        },
                        "by_day": [],
                    },
                    "elsevier": {},
                },
            }
        ),
        encoding="utf-8",
    )

    html = render_usage(data_dir, docs_dir).read_text(encoding="utf-8")

    assert "keep-alive" not in html.casefold()
    assert "60 天" not in html
    assert "闲置约 60 天" not in html


def test_health_monitor_does_not_read_synthetic_keepalive_contract():
    source = (ROOT / "scripts" / "open_health_issues.py").read_text(encoding="utf-8")

    assert "semantic_scholar_keepalive.json" not in source
    assert "Semantic Scholar key keep-alive" not in source
    assert "prunes keys inactive" not in source
