"""Shared public article-topic projection for Daily Door surfaces."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

TOPIC_RULES: dict[str, tuple[str, ...]] = {
    "agriculture": ("agricultur", "farm", "food", "rice", "dairy", "rural", "crop", "land use"),
    "environment": ("climate", "weather", "carbon", "emission", "environment", "forest", "pollution", "energy", "electricity"),
    "development": ("development", "poverty", "displacement", "household", "informal", "low-income"),
    "finance": ("finance", "financial", "bank", "stock", "market", "asset", "investor", "credit"),
    "macro": ("monetary", "inflation", "growth", "business cycle", "exchange rate", "macro", "productivity"),
    "labor": ("labor", "labour", "wage", "worker", "employment", "unemployment", "migration"),
    "public": ("tax", "public", "policy", "political", "government", "regulation", "welfare"),
    "trade": ("trade", "export", "import", "tariff", "global", "supply chain", "cross-border"),
    "urban": ("urban", "city", "cities", "housing", "regional"),
    "econometrics": ("estimator", "identification", "causal", "regression", "bayesian", "machine learning"),
    "theory": ("equilibrium", "game", "theory", "mechanism", "auction", "contract"),
    "behavior": ("behavior", "behaviour", "preferences", "consumer", "discrimination", "organization"),
    "health": ("health", "mortality", "hospital", "medical", "disease", "height"),
    "education": ("education", "school", "student", "teacher"),
    "firms": ("firm", "enterprise", "industrial", "outsourcing", "services", "innovation"),
    "inequality": ("inequality", "distribution", "mobility", "gender", "racial"),
    "history": ("history", "historical", "nineteenth", "twentieth"),
}

FIELD_TOPIC_FALLBACK: dict[str, tuple[str, ...]] = {
    "agriculture_environment_resource": ("agriculture", "environment"),
    "public_political": ("public",),
    "industrial_organization": ("firms",),
    "game_theory": ("theory",),
    "economic_history": ("history",),
    "applied_empirical": ("econometrics",),
    "international": ("trade",),
}

KNOWN_TOPIC_KEYS = frozenset(TOPIC_RULES)


def _topic_keyword_matches(haystack: str, keyword: str) -> bool:
    """Match at an ASCII word start while preserving configured stem prefixes."""
    start = 0
    while True:
        index = haystack.find(keyword, start)
        if index < 0:
            return False
        if index == 0 or not ("a" <= haystack[index - 1] <= "z" or "0" <= haystack[index - 1] <= "9"):
            return True
        start = index + 1


@lru_cache(maxsize=1)
def working_source_default_fields() -> dict[str, tuple[str, ...]]:
    """Load source coverage fields without requiring a YAML dependency."""
    path = DATA_DIR / "working_paper_sources.yml"
    if not path.exists():
        return {}

    defaults: dict[str, list[str]] = {}
    source_id: str | None = None
    in_fields = False

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- id:"):
            source_id = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            defaults.setdefault(source_id, [])
            in_fields = False
            continue
        if source_id is None:
            continue
        if stripped == "fields:":
            in_fields = True
            continue
        if in_fields and raw_line.startswith("      - "):
            value = stripped.removeprefix("- ").strip().strip('"').strip("'")
            if value:
                defaults[source_id].append(value)
            continue
        if in_fields and not raw_line.startswith("      "):
            in_fields = False

    return {key: tuple(values) for key, values in defaults.items()}


def is_working_paper_record(record: dict[str, Any]) -> bool:
    source_type = str(record.get("source_type") or "")
    return str(record.get("source") or "") == "working_papers" or source_type in {
        "working_paper",
        "policy_paper",
        "policy_commentary",
        "aggregator",
        "preprint",
    }


def source_default_fields(record: dict[str, Any]) -> tuple[str, ...]:
    source_id = str(record.get("source_id") or "").removeprefix("source-")
    return working_source_default_fields().get(source_id, ())


def fields_are_source_scope(record: dict[str, Any]) -> bool:
    if not is_working_paper_record(record):
        return False
    fields = tuple(str(field) for field in (record.get("fields") or []))
    defaults = source_default_fields(record)
    return bool(fields and defaults and fields == defaults)


def _explicit_topic_keys(record: dict[str, Any]) -> list[str]:
    output: list[str] = []
    for field_name in ("topics", "ai_tags"):
        raw = record.get(field_name) or []
        if isinstance(raw, str):
            raw = [raw]
        for value in raw:
            key = str(value or "").strip().casefold()
            if key in KNOWN_TOPIC_KEYS and key not in output:
                output.append(key)
    return output


def _text_topic_keys(record: dict[str, Any]) -> list[str]:
    values = [
        record.get("title"),
        record.get("title_zh"),
        record.get("abstract"),
        record.get("abstract_zh"),
    ]
    if not is_working_paper_record(record):
        values.append(record.get("journal"))
    haystack = " ".join(str(value or "") for value in values).casefold()
    return [
        topic
        for topic, keywords in TOPIC_RULES.items()
        if any(_topic_keyword_matches(haystack, keyword) for keyword in keywords)
    ]


def _field_fallback_keys(record: dict[str, Any]) -> list[str]:
    output: list[str] = []
    for field in [str(value) for value in (record.get("fields") or [])]:
        mapped = FIELD_TOPIC_FALLBACK.get(field)
        if mapped is None:
            mapped = (field,) if field in KNOWN_TOPIC_KEYS else ()
        for topic in mapped:
            if topic not in output:
                output.append(topic)
    return output


def public_article_topic_keys(record: dict[str, Any], *, limit: int = 4) -> list[str]:
    """Return paper-level public topics without exposing source coverage fields."""
    output = _explicit_topic_keys(record)
    for topic in _text_topic_keys(record):
        if topic not in output:
            output.append(topic)
    if output:
        return output[:limit]

    if fields_are_source_scope(record):
        return []

    fallback = _field_fallback_keys(record)
    if fallback:
        return fallback[:limit]

    # Preserve the legacy formal-record fallback while avoiding a fabricated
    # Development tag for working papers whose source taxonomy was suppressed.
    return [] if is_working_paper_record(record) else ["development"]
