"""Regression for business-day rollover between acquisition and site rendering."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "render_published_site.sh"


def run_render(tmp_path: Path, day: str, archive: str | None) -> subprocess.CompletedProcess[str]:
    daily = tmp_path / "data" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    if archive is not None:
        (daily / f"{archive}.json").write_text("[]\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir(exist_ok=True)
    # Stop immediately *after* the real script's canonical-date validation.
    # No full render, data mutation, network access or real git writes.
    (tmp_path / "scripts" / "render_site.py").write_text(
        "raise SystemExit(73)\n", encoding="utf-8"
    )
    env = dict(os.environ, MONITOR_RUN_DATE=day, RUNNER_TEMP=str(tmp_path))
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )


def test_midnight_rollover_renders_frozen_previous_business_date(tmp_path: Path) -> None:
    # At render time the Beijing clock can already be 2026-10-19.
    # The producer's 2026-10-18 canonical archive must still validate.
    result = run_render(tmp_path, "2026-10-18", "2026-10-18")
    assert result.returncode == 73  # reached test-controlled renderer
    assert "Canonical input validated: data/daily/2026-10-18.json (0 records)" in result.stdout
    assert "2026-10-19.json" not in result.stdout + result.stderr


def test_missing_frozen_archive_defers_without_publishing_stale_day(tmp_path: Path) -> None:
    result = run_render(tmp_path, "2026-10-19", "2026-10-18")
    assert result.returncode == 0
    assert "Daily render deferred" in result.stdout
    assert "site unchanged" in result.stdout
    assert not (tmp_path / "docs").exists()


@pytest.mark.parametrize("invalid", ["2026/10/18", "2026-10-99", "../2026-10-18", "2026-1-2"])
def test_invalid_frozen_date_fails_closed(tmp_path: Path, invalid: str) -> None:
    result = run_render(tmp_path, invalid, "2026-10-18")
    assert result.returncode != 0
    assert "invalid frozen Beijing business date" in result.stdout + result.stderr


def test_invalid_existing_daily_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "data" / "daily"
    path.mkdir(parents=True)
    (path / "2026-10-18.json").write_text('{"not": "a daily record list"}', encoding="utf-8")
    result = run_render(tmp_path, "2026-10-18", None)
    assert result.returncode != 0
    assert "not a list of records" in result.stderr + result.stdout
