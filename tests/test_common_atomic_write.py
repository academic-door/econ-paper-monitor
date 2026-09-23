import json
import sys

import pytest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import recent_cutoff, today_str, write_json


def test_concurrent_writes_use_independent_temporary_files(tmp_path):
    target = tmp_path / "status.json"

    def write(index):
        write_json(target, {"writer": index, "ok": True})

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(32)))

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert 0 <= payload["writer"] < 32


def test_monitor_run_date_override_freezes_today_and_recent_cutoff(monkeypatch):
    monkeypatch.setenv("MONITOR_RUN_DATE", "2026-09-23")

    assert today_str() == "2026-09-23"
    assert recent_cutoff(2) == "2026-09-21"


def test_monitor_run_date_override_rejects_non_iso_value(monkeypatch):
    monkeypatch.setenv("MONITOR_RUN_DATE", "2026/09/23")

    with pytest.raises(ValueError, match="MONITOR_RUN_DATE"):
        today_str()
