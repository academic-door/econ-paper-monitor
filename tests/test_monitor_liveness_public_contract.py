from pathlib import Path


def test_optional_monitor_liveness_request_is_bounded() -> None:
    template = Path("scripts/templates/daily_vnext.html").read_text(encoding="utf-8")
    assert "const monitorController = new AbortController();" in template
    assert "window.setTimeout(() => monitorController.abort(), 5000)" in template
    assert "signal: monitorController.signal" in template
    assert ".finally(() => window.clearTimeout(monitorTimer));" in template
