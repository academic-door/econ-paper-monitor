"""Non-production regression tests for isolated SLJY first-party audit scheduling."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "local_admin" / "sljy_official_audit" / "run.py"
spec = importlib.util.spec_from_file_location("local_sljy_audit_runner", SCRIPT)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def fake_audit(*, good: bool) -> dict:
    return {
        "print_issue_roster_status": "RECONCILED_AS_OF" if good else "VERIFICATION_BLOCKED",
        "online_first_coverage_status": "NOT_VERIFIED",
        "observed_at": "2026-10-19T08:00:00+00:00" if good else None,
        "scope": "PUBLISHER_LISTED_CURRENT_PRINT_ISSUE_ONLY",
    }


def test_success_writes_immutable_observation_and_last_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "refresh_canonical_clone", lambda repo: "a" * 40)
    with patch.object(runner.subprocess, "run") as call:
        call.return_value.returncode = 0
        call.return_value.stdout = json.dumps(fake_audit(good=True))
        record = runner.evaluate(tmp_path)
    assert record["success"]
    assert record["canonical_commit"] == "a" * 40
    assert record["online_first_coverage_status"] == "NOT_VERIFIED"
    assert json.loads((tmp_path / "evidence" / "last_accepted.json").read_text())["success"]
    assert len(list((tmp_path / "evidence").glob("observation-*.json"))) == 1
    assert Path(call.call_args.args[0][1]).name == "audit_sljy_official.py"


def test_failure_keeps_last_accepted_observation_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "refresh_canonical_clone", lambda repo: "b" * 40)
    with patch.object(runner.subprocess, "run") as call:
        call.return_value.returncode = 0
        call.return_value.stdout = json.dumps(fake_audit(good=True))
        assert runner.evaluate(tmp_path)["success"]
    before = (tmp_path / "evidence" / "last_accepted.json").read_bytes()
    monkeypatch.setattr(runner, "refresh_canonical_clone", lambda repo: (_ for _ in ()).throw(RuntimeError("GitHub offline")))
    result = runner.evaluate(tmp_path)
    assert not result["success"]
    assert result["run_status"] == "VERIFICATION_BLOCKED"
    assert "GitHub offline" in result["error"]
    assert (tmp_path / "evidence" / "last_accepted.json").read_bytes() == before
    assert json.loads((tmp_path / "evidence" / "latest.json").read_text())["success"] is False


def test_source_block_does_not_promote_invalid_observation(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "refresh_canonical_clone", lambda repo: "b" * 40)
    with patch.object(runner.subprocess, "run") as call:
        call.return_value.returncode = 2
        call.return_value.stdout = json.dumps(fake_audit(good=False))
        result = runner.evaluate(tmp_path)
    assert not result["success"]
    assert result["run_status"] == "VERIFICATION_BLOCKED_OR_DIFF"
    assert not (tmp_path / "evidence" / "last_accepted.json").exists()


def test_observation_retention_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "MAX_EVIDENCE", 2)
    monkeypatch.setattr(runner, "refresh_canonical_clone", lambda repo: (_ for _ in ()).throw(RuntimeError("source unavailable")))
    for i in range(3):
        monkeypatch.setattr(runner, "utc_now", lambda i=i: f"2026-10-19T08:00:0{i}Z")
        runner.evaluate(tmp_path)
    assert len(list((tmp_path / "evidence").glob("observation-*.json"))) <= 2


def test_no_prod_repository_when_dedicated_clone_is_absent(tmp_path):
    with pytest.raises(RuntimeError, match="dedicated sparse audit clone"):
        runner.refresh_canonical_clone(tmp_path / "repo")


def test_clone_origin_mismatch_blocks_audit(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(runner, "command", lambda argv, **kwargs: "https://github.com/wrong/repository.git")
    with pytest.raises(RuntimeError, match="does not match"):
        runner.refresh_canonical_clone(repo)


def test_process_lock_serializes_independent_invocations(tmp_path):
    with runner.exclusive_lock(tmp_path / "lock"):
        with pytest.raises(RuntimeError, match="already running"):
            with runner.exclusive_lock(tmp_path / "lock"):
                pass
    with runner.exclusive_lock(tmp_path / "lock"):
        pass
