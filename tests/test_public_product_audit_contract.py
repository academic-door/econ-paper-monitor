from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "daily-vnext-browser-smoke.yml"
SMOKE = ROOT / "tests" / "daily_vnext_public_smoke.mjs"


def test_public_product_audit_runs_desktop_and_mobile() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "name: desktop" in workflow
    assert "viewport_width: 1440" in workflow
    assert "name: mobile" in workflow
    assert "viewport_width: 390" in workflow
    assert "VIEWPORT_WIDTH: ${{ matrix.viewport_width }}" in workflow


def test_public_product_audit_guards_live_qa_regressions() -> None:
    smoke = SMOKE.read_text(encoding="utf-8")

    for helper in (
        "assertNoPseudoDiscoveryTime",
        "assertSearchCountConsistency",
        "assertUniqueSourceFilterLabels",
        "assertNoStaleTodayBackfill",
        "assertNavigationLinksHealthy",
        "assertChinaCountConsistency",
    ):
        assert helper in smoke, f"missing Public Product Audit helper: {helper}"


def test_public_product_audit_stays_on_existing_browser_smoke_lane() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "runs-on: ubuntu-latest" in workflow
    assert "playwright@1.55.0" in workflow
    assert "npx playwright install --with-deps chromium" in workflow
    assert "schedule:" not in workflow
