"""JARE's dedicated official check stays observable when the broad batch times out."""

from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from unittest import mock
from urllib.error import URLError

import audit_source_health
import fetch_priority_toc as toc


JARE = "journal-of-agricultural-and-resource-economics"
OTHER = "theoretical-economics"
NOW = datetime(2026, 11, 10, 4, 35, tzinfo=timezone.utc)
FRESH = "2026-11-10T04:30:00+00:00"
OLDER = "2026-11-08T10:21:26+00:00"


def entry(timestamp, journal, *, publisher_ok, ok=True, outcome="SUCCESS"):
    return {
        "ok": ok,
        "updated_at": timestamp,
        "journals": {
            journal: {
                "ok": bool(publisher_ok),
                "publisher_ok": publisher_ok,
                "count": 17 if publisher_ok else 0,
                "outcomes": [outcome],
            }
        },
    }


def specialized(status, journal=JARE):
    return audit_source_health.specialized_paths(
        journal, status, NOW, 36 / 24
    )


def test_latest_dedicated_success_recovers_jare_from_stale_broad_timeout():
    status = {
        "sources": {
            "priority-toc": entry(OLDER, JARE, publisher_ok=False, ok=False, outcome="NETWORK_FAILURE"),
            "priority-toc:jare": entry(FRESH, JARE, publisher_ok=True),
        }
    }
    paths, checked, failed = specialized(status)
    assert paths == ["priority-toc"]
    assert checked == [FRESH]
    assert failed == []


def test_latest_dedicated_failure_does_not_promote_stale_publisher_success():
    status = {
        "sources": {
            "priority-toc": entry(OLDER, JARE, publisher_ok=True),
            "priority-toc:jare": entry(FRESH, JARE, publisher_ok=False, ok=False, outcome="NETWORK_FAILURE"),
        }
    }
    paths, checked, failed = specialized(status)
    assert paths == []
    assert checked == [FRESH]
    assert failed == [{"path": "priority-toc", "message": "NETWORK_FAILURE"}]


def test_more_recent_broad_success_can_override_older_dedicated_failure():
    status = {
        "sources": {
            "priority-toc": entry("2026-11-10T04:33:00+00:00", JARE, publisher_ok=True),
            "priority-toc:jare": entry("2026-11-10T04:20:00+00:00", JARE, publisher_ok=False, ok=False, outcome="NETWORK_FAILURE"),
        }
    }
    paths, checked, failed = specialized(status)
    assert paths == ["priority-toc"]
    assert checked == ["2026-11-10T04:33:00+00:00"]
    assert failed == []


def test_jare_dedicated_result_never_authenticates_other_formal_journal():
    status = {
        "sources": {
            "priority-toc": entry(FRESH, OTHER, publisher_ok=False, ok=False, outcome="PARSER_FAILURE"),
            "priority-toc:jare": entry(FRESH, JARE, publisher_ok=True),
        }
    }
    paths, _, failed = specialized(status, OTHER)
    assert paths == []
    assert failed == [{"path": "priority-toc", "message": "PARSER_FAILURE"}]


def test_full_run_has_bounded_separate_publisher_step_and_durable_raw_file():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/update.yml"
    ).read_text(encoding="utf-8")
    start = workflow.index("- name: Fetch JARE official Published Online (bounded)")
    end = workflow.index("- name: Fetch priority publisher TOCs", start)
    focused = workflow[start:end]
    assert "steps.mode.outputs.mode == 'full'" in focused
    assert "timeout 75s" in focused
    assert "--journal journal-of-agricultural-and-resource-economics" in focused
    assert '"priority-toc:jare"' in focused
    assert 'data/raw/priority-toc-jare/' in focused
    assert "consult per-journal publisher_ok" in focused
    assert "HOSTED_CHECK_INCOMPLETE" in focused


def test_jare_official_source_never_falls_back_to_unverified_tls():
    with mock.patch.object(toc.urllib.request, "urlopen", side_effect=URLError("TLS error")) as open_url:
        with mock.patch.object(toc.ssl, "_create_unverified_context") as unsafe:
            try:
                toc.fetch_one("https://jareonline.org/preprint-online/", timeout=6)
            except toc.PublisherTransportError as error:
                assert error.outcome == "NETWORK_FAILURE"
            else:
                raise AssertionError("a failed strict TLS request must not be accepted")
            assert open_url.call_count == 1
            unsafe.assert_not_called()
