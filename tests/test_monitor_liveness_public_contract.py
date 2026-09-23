from pathlib import Path


def test_monitor_liveness_stays_off_the_public_homepage() -> None:
    template = Path("scripts/templates/daily_vnext.html").read_text(encoding="utf-8")
    assert "/monitor-liveness" not in template
    assert "data-monitor-liveness" not in template
    assert "monitorController" not in template
    assert "stale_workflows" not in template
    assert "数据更新延迟" not in template
    assert "状态待确认" not in template
    assert "页面内容可能" not in template
