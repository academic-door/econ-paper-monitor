"""Regression: failed JQTE attempts cannot be reported as healthy source coverage."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import aggregate_cn_status as aggregate
import fetch_cn_journals as cn


SLJY = "journal-edcb877d78"


def test_missing_output_queues_fallback_instead_of_not_needed() -> None:
    row = aggregate.journal_result_row(SLJY, "数量经济技术经济研究", None, [])
    assert row["ok"] is False
    assert row["count"] == 0
    assert row["mode"] == "missing-output"
    assert row["attempts"] == 0
    assert row["retryable"] is True
    assert row["fallback"] == "local-cnki-rss"
    assert row["fallback_status"] == "queued"
    assert "missing output" in row["message"]


def test_present_output_without_status_is_not_verified() -> None:
    row = aggregate.journal_result_row(SLJY, "数量经济技术经济研究", [], [])
    assert row["ok"] is False
    assert row["mode"] == "missing-status"
    assert row["fallback_status"] == "queued"


def test_valid_error_status_is_preserved() -> None:
    error = {"ok": False, "message": "HTTPError: HTTP Error 502", "mode": "error",
             "attempts": 1, "retryable": True, "fallback_status": "queued"}
    row = aggregate.journal_result_row(SLJY, "数量经济技术经济研究", [], [error])
    assert not row["ok"]
    assert "502" in row["message"]
    assert row["attempts"] == 1
    assert row["fallback_status"] == "queued"


def test_valid_success_keeps_not_needed_and_count() -> None:
    row = aggregate.journal_result_row(SLJY, "数量经济技术经济研究",
                                       [{"title": "A"}], [{"ok": True, "mode": "jqte-toc", "attempts": 1}])
    assert row["ok"] and row["count"] == 1
    assert row["fallback"] is None
    assert row["fallback_status"] == "not-needed"


def test_jqte_request_uses_bounded_timeout() -> None:
    journal = {"id": SLJY, "title": "数量经济技术经济研究", "fields": []}
    with patch.object(cn, "fetch_text_partial", return_value="") as fetch:
        records, mode = cn.fetch_journal(journal, cn.CN_HOME_URLS[SLJY], 20)
    assert records == []
    assert mode == "jqte-toc"
    fetch.assert_called_once_with(cn.CN_HOME_URLS[SLJY], timeout=12)


def test_empty_jqte_toc_is_source_failure_not_complete(tmp_path, monkeypatch) -> None:
    journal = {"id": SLJY, "title": "数量经济技术经济研究", "fields": []}
    monkeypatch.setattr(cn, "load_journals", lambda _: [journal])
    monkeypatch.setattr(cn, "filter_journals_by_tier", lambda journals, tier: journals)
    monkeypatch.setattr(cn, "CN_HOME_URLS", {SLJY: "https://www.jqte.net/sljjjsjjyj/ch/index.aspx"})
    monkeypatch.setattr(cn, "fetch_journal_with_retry",
                        lambda *args, **kwargs: ([], "jqte-toc", 1))
    monkeypatch.setattr(cn, "record_source", lambda *args, **kwargs: None)
    monkeypatch.setattr(cn, "load_status", lambda: {})
    captured = {}
    monkeypatch.setattr(cn, "save_status", lambda status: captured.update(status))
    output = tmp_path / "sljy.json"
    monkeypatch.setattr(sys, "argv", ["fetch_cn_journals.py", "--only", SLJY,
                                     "--output", str(output), "--retries", "0"])
    cn.main()
    group = captured["source_groups"]["cn-journals"]
    assert group["ok"] is False
    row = group["journals"][0]
    assert row["ok"] is False
    assert row["fallback_status"] == "queued"
    assert row["fallback"] == "local-cnki-rss"
    assert "no verifiable issue members" in row["message"]
