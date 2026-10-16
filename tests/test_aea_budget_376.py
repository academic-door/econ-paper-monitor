"""Regression: AEA time budget must not silently erase partial first-party evidence."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_aea as aea


def test_aea_budget_expired_skips_pages_without_network(monkeypatch):
    monkeypatch.setattr(aea.time, "monotonic", lambda: 20.0)
    monkeypatch.setattr(aea, "fetch_text", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("network must not be attempted after budget")
    ))
    rows, errors = aea.fetch_journal(
        {"id": "american-economic-review", "title": "AER"},
        "aer", timeout=15, detail_limit=0, max_items=20, deadline=19.0,
    )
    assert rows == []
    assert len(errors) == 2
    assert all("BUDGET_EXHAUSTED" in error for error in errors)


def test_aea_budget_preserves_successful_page_but_marks_roster_due(monkeypatch):
    clock = [0.0]
    seen_calls = []

    def one_page(url, timeout):
        seen_calls.append((url, timeout))
        clock[0] = 10.0
        return '<a href="/articles?id=10.1257/aer.2026.1">Real research article</a>'

    monkeypatch.setattr(aea.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(aea, "fetch_text", one_page)
    monkeypatch.setattr(aea, "article_record", lambda journal, **values: dict(values))
    rows, errors = aea.fetch_journal(
        {"id": "american-economic-review", "title": "AER"},
        "aer", timeout=15, detail_limit=0, max_items=20, deadline=10.0,
    )
    assert len(rows) == 1
    assert rows[0]["doi"] == "10.1257/aer.2026.1"
    assert len(seen_calls) == 1
    assert len(errors) == 1 and "BUDGET_EXHAUSTED" in errors[0]
    status = aea.journal_status_entry(rows, errors)
    assert status["ok"] is True  # available partial source, not total outage
    assert status["roster_verification"] == "VERIFICATION_DUE"


def test_aea_budget_rotates_to_first_unverified_journal(monkeypatch):
    clock = [0.0]
    snapshot = {"journals": {}}
    outputs = []
    summaries = []
    seen = []
    journals = [
        {"id": "american-economic-review", "title": "AER"},
        {"id": "journal-of-economic-literature", "title": "JEL"},
        {"id": "journal-of-economic-perspectives", "title": "JEP"},
    ]

    monkeypatch.setattr(sys, "argv", ["fetch_aea.py", "--budget-seconds", "20"])
    monkeypatch.setattr(aea.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(aea, "today_str", lambda: "2026-10-16")
    monkeypatch.setattr(aea, "now_iso", lambda: "2026-10-16T14:00:00+00:00")
    monkeypatch.setattr(aea, "load_journals", lambda *args: journals)
    monkeypatch.setattr(aea, "filter_journals_by_tier", lambda rows, tier: rows)
    monkeypatch.setattr(aea, "read_json", lambda path, default=None: snapshot if path == aea.SNAPSHOT_PATH else {})
    monkeypatch.setattr(aea, "record_source", lambda *args, **kwargs: summaries.append(kwargs))

    def write_stub(path, data):
        if path == aea.SNAPSHOT_PATH:
            snapshot.clear()
            snapshot.update(data)
        else:
            outputs.append(data)
    monkeypatch.setattr(aea, "write_json", write_stub)

    def fetch_stub(journal, code, **kwargs):
        seen.append(journal["id"])
        clock[0] += 21  # exhaust the first request's remaining budget
        return ([{"url": "https://www.aeaweb.org/articles?id=10.1257/" + code + ".123",
                  "doi": "10.1257/" + code + ".123", "raw_data": {}}], [])
    monkeypatch.setattr(aea, "fetch_journal", fetch_stub)

    aea.main()
    assert seen == ["american-economic-review"]
    assert len(outputs[-1]) == 1  # successful partial records are not discarded
    assert snapshot["next_journal_index"] == 1
    assert set(snapshot["journals"]) == {"american-economic-review"}
    assert summaries[-1]["details"]["roster_verification"] == "VERIFICATION_DUE"
    assert summaries[-1]["details"]["journals"]["journal-of-economic-literature"]["ok"] is False
    assert "BUDGET_EXHAUSTED" in summaries[-1]["details"]["journals"]["journal-of-economic-literature"]["message"]

    clock[0] = 0.0
    aea.main()
    assert seen[-1] == "journal-of-economic-literature"
    assert snapshot["next_journal_index"] == 2
    assert set(snapshot["journals"]) == {
        "american-economic-review", "journal-of-economic-literature",
    }
