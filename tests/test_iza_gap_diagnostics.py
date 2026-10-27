"""Bounded IZA missing-abstract observability; pure fixtures, no live requests."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_preprints  # noqa: E402

URL = "https://www.iza.org/publications/dp/18991/official-title"
SOURCE = {"id": "iza"}


def record() -> dict:
    return {"url": URL, "title": "Original accepted title", "abstract": None,
            "first_seen": "2026-10-16T19:38:06Z"}


def test_iza_missing_abstract_transport_timeout_is_sanitized(capsys) -> None:
    item = record()
    with patch.object(fetch_preprints, "fetch_text", side_effect=TimeoutError("private network details")) as fetch:
        result = fetch_preprints.enrich_record_from_detail(item, SOURCE, timeout=10)
    output = capsys.readouterr().out
    assert fetch.call_count == 1
    assert result == item
    assert "dp=18991 status=transport_error error=TimeoutError http=none" in output
    assert "private network details" not in output


def test_iza_missing_abstract_http_failure_emits_status_only(capsys) -> None:
    item = record()
    failure = HTTPError(URL, 503, "secret response", None, None)
    with patch.object(fetch_preprints, "fetch_text", side_effect=failure):
        fetch_preprints.enrich_record_from_detail(item, SOURCE, timeout=10)
    output = capsys.readouterr().out
    assert "error=HTTPError http=503" in output
    assert "secret response" not in output
    assert URL not in output


def test_iza_success_without_abstract_is_distinct_from_transport_failure(capsys) -> None:
    item = record()
    html = "<html><body><h1>Accepted Page Title for Official Paper</h1></body></html>"
    with patch.object(fetch_preprints, "fetch_text", return_value=html) as fetch:
        result = fetch_preprints.enrich_record_from_detail(item, SOURCE, timeout=10)
    output = capsys.readouterr().out
    assert fetch.call_count == 1
    assert result.get("abstract") is None
    assert "dp=18991 status=transport_ok abstract=selector_miss chars=0" in output


def test_iza_success_with_abstract_logs_length_not_text(capsys) -> None:
    item = record()
    abstract = "We investigate the uptake of AI within firms with robust evidence. " * 5
    html = f'<div class="page-header__intro"><div><p>{abstract}</p></div></div>'
    with patch.object(fetch_preprints, "fetch_text", return_value=html) as fetch:
        result = fetch_preprints.enrich_record_from_detail(item, SOURCE, timeout=10)
    output = capsys.readouterr().out
    assert fetch.call_count == 1
    assert result["abstract"] == abstract.strip()
    assert "dp=18991 status=transport_ok abstract=candidate chars=" in output
    assert "We investigate" not in output
    assert item["first_seen"] == "2026-10-16T19:38:06Z"


def test_other_sources_remain_silent(capsys) -> None:
    item = record()
    with patch.object(fetch_preprints, "fetch_text", side_effect=TimeoutError("private")):
        fetch_preprints.enrich_record_from_detail(item, {"id": "cepr-dp"}, timeout=10)
    assert capsys.readouterr().out == ""
