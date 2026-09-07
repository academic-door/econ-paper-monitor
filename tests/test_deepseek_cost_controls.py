from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISCOVERY_WORKFLOW = ROOT / ".github" / "workflows" / "update.yml"
AI_WORKFLOW = ROOT / ".github" / "workflows" / "ai-enrichment.yml"


def read_discovery_workflow() -> str:
    return DISCOVERY_WORKFLOW.read_text(encoding="utf-8")


def read_ai_workflow() -> str:
    return AI_WORKFLOW.read_text(encoding="utf-8")


def test_full_schedule_is_even_six_hour_discovery_cadence() -> None:
    text = read_discovery_workflow()
    for cron in (
        '- cron: "30 18 * * *"',
        '- cron: "30 0 * * *"',
        '- cron: "30 6 * * *"',
        '- cron: "30 12 * * *"',
    ):
        assert cron in text
    assert 'FULL_SCHEDULES: "30 18 * * *|30 0 * * *|30 6 * * *|30 12 * * *"' in text
    assert "Beijing 02:30, 08:30, 14:30, and 20:30" in text


def test_deepseek_model_is_pinned_in_independent_ai_workflow() -> None:
    discovery = read_discovery_workflow()
    ai = read_ai_workflow()
    assert "DEEPSEEK_API_KEY" not in discovery
    assert "DEEPSEEK_MODEL: deepseek-v4-flash" in ai
    assert ai.count("DEEPSEEK_MODEL: deepseek-v4-flash") >= 2


def test_discovery_never_runs_paid_translation() -> None:
    discovery = read_discovery_workflow()
    ai = read_ai_workflow()
    assert "scripts/translate.py" not in discovery
    assert "scripts/ai_china_relevance.py" not in discovery
    assert "scripts/translate.py" in ai
    assert "scripts/ai_china_relevance.py" in ai


def test_rule_based_china_relevance_remains_in_discovery() -> None:
    discovery = read_discovery_workflow()
    assert "scripts/enrich_china_relevance.py --all" in discovery
