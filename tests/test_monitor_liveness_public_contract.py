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

def test_monitor_liveness_observer_does_not_edge_cache_github_history() -> None:
    worker = Path("infra/presence-worker.js").read_text(encoding="utf-8")
    html_start = worker.index("async function fetchScheduleRunsFromWorkflowPages")
    api_start = worker.index("async function refreshMonitorLiveness")
    html_block = worker[html_start:api_start]
    api_fetch_start = worker.index("const upstream = await fetch(GITHUB_SCHEDULE_RUNS_URL", api_start)
    api_fetch_end = worker.index("if (!upstream.ok)", api_fetch_start)
    api_block = worker[api_fetch_start:api_fetch_end]

    assert 'cf: { cacheTtl: 0 }' in html_block
    assert 'cacheEverything: true' not in html_block
    assert '"Cache-Control": "no-cache"' in html_block

    assert 'cf: { cacheTtl: 0 }' in api_block
    assert 'cacheEverything: true' not in api_block
    assert '"Cache-Control": "no-cache"' in api_block


