from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RUNTIME_FILES = [
    ROOT / "scripts" / "fetch_priority_toc.py",
    ROOT / "scripts" / "enrich_metadata.py",
    ROOT / "scripts" / "fetch_preprints.py",
    ROOT / "scripts" / "fetch_sciencedirect_search.py",
    ROOT / "scripts" / "fetch_cnki_rss.py",
    ROOT / ".github" / "workflows" / "update.yml",
    ROOT / ".github" / "workflows" / "fast-discovery.yml",
]


def test_daily_runtime_has_no_jina_transport_or_secret_binding() -> None:
    offenders: list[str] = []
    for path in RUNTIME_FILES:
        text = path.read_text(encoding="utf-8")
        for token in ("r.jina.ai", "JINA_API_KEY", "readonly-proxy"):
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)}:{token}")
    assert offenders == []


def test_priority_targets_do_not_configure_retired_mirror_fallbacks() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import fetch_priority_toc  # noqa: E402

    offenders = [
        journal_id
        for journal_id, targets in fetch_priority_toc.TARGETS.items()
        for target in targets
        if any("jina.ai" in str(url) for url in (target.get("fallback_urls") or []))
    ]
    assert offenders == []
