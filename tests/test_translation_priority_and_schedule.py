from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ai_cost_control  # noqa: E402
import translate  # noqa: E402


def test_ai_enrichment_cron_uses_off_peak_boundary_slots() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ai-enrichment.yml").read_text(encoding="utf-8")
    assert 'cron: "5 4,10,16,22 * * *"' in workflow
    for hour in (4, 10, 16, 22):
        assert ai_cost_control.deepseek_pricing_window(
            datetime(2026, 9, 21, hour, 5, tzinfo=UTC)
        ) == "off_peak"


def test_current_daily_gets_paid_translation_budget_before_seen_backlog(tmp_path: Path, monkeypatch) -> None:
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    current = {
        "id": "current",
        "title": "Current public title",
        "detected_at": "2026-09-21T00:20:01+00:00",
        "first_seen": "2026-09-21T00:20:01+00:00",
    }
    (daily_dir / "2026-09-21.json").write_text(
        json.dumps([current]),
        encoding="utf-8",
    )

    # Give the backlog a later timestamp so the old seen-first implementation
    # would consume the one-call title budget before reaching Daily.
    seen_path = tmp_path / "seen.json"
    seen_path.write_text(
        json.dumps(
            {
                "papers": {
                    "old": {
                        "id": "old",
                        "title": "Older backlog title",
                        "detected_at": "2026-09-21T00:30:00+00:00",
                        "first_seen": "2026-09-21T00:30:00+00:00",
                    },
                    "current": dict(current),
                }
            }
        ),
        encoding="utf-8",
    )

    cache_path = tmp_path / "translation_cache.json"
    cache_path.write_text(json.dumps({"records": {}}), encoding="utf-8")

    calls: list[str] = []
    monkeypatch.setattr(translate, "CACHE_PATH", cache_path)
    monkeypatch.setattr(
        translate,
        "api_settings",
        lambda: ("test-key", "https://api.deepseek.com/v1", "deepseek-v4-flash"),
    )
    monkeypatch.setattr(translate, "paid_call_allowed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        translate,
        "translate_title",
        lambda title, *_args, **_kwargs: calls.append(title) or "当前公开标题",
    )
    monkeypatch.setattr(translate, "record_source", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "translate.py",
            "--daily-dir",
            str(daily_dir),
            "--seen",
            str(seen_path),
            "--limit",
            "1",
            "--abstract-limit",
            "0",
            "--sleep",
            "0",
        ],
    )

    translate.main()

    daily = json.loads((daily_dir / "2026-09-21.json").read_text(encoding="utf-8"))
    seen = json.loads(seen_path.read_text(encoding="utf-8"))["papers"]

    assert calls == ["Current public title"]
    assert daily[0]["title_zh"] == "当前公开标题"
    assert seen["current"]["title_zh"] == "当前公开标题"
    assert "title_zh" not in seen["old"]


def test_peak_deferred_state_is_explicit(tmp_path: Path, monkeypatch, capsys) -> None:
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    (daily_dir / "2026-09-21.json").write_text(
        json.dumps(
            [
                {
                    "id": "current",
                    "title": "Current public title",
                    "detected_at": "2026-09-21T00:20:01+00:00",
                    "first_seen": "2026-09-21T00:20:01+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )
    seen_path = tmp_path / "seen.json"
    seen_path.write_text(json.dumps({"papers": {}}), encoding="utf-8")
    cache_path = tmp_path / "translation_cache.json"
    cache_path.write_text(json.dumps({"records": {}}), encoding="utf-8")

    messages: list[str] = []
    monkeypatch.setattr(translate, "CACHE_PATH", cache_path)
    monkeypatch.setattr(
        translate,
        "api_settings",
        lambda: ("test-key", "https://api.deepseek.com/v1", "deepseek-v4-flash"),
    )
    monkeypatch.setattr(translate, "paid_call_allowed", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        translate,
        "record_source",
        lambda _name, **kwargs: messages.append(str(kwargs.get("message") or "")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "translate.py",
            "--daily-dir",
            str(daily_dir),
            "--seen",
            str(seen_path),
            "--limit",
            "1",
            "--abstract-limit",
            "0",
            "--sleep",
            "0",
        ],
    )

    translate.main()
    output = capsys.readouterr().out

    assert "state=peak_deferred" in output
    assert any("state=peak_deferred" in message for message in messages)
