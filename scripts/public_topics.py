"""Shared public article-topic projection.

Canonical source fields remain untouched for acquisition/routing. Public topic
badges must not present a working-paper source's coverage taxonomy as if it
classified every paper from that source.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKING_SOURCE_CONFIG = ROOT / "data" / "working_paper_sources.yml"
WORKING_SOURCE_TYPES = {"working_paper", "policy_paper", "aggregator", "preprint"}

TOPIC_RULES = {
    "agriculture": ["agricultur", "farm", "food", "rice", "dairy", "rural", "crop", "land use"],
    "environment": ["climate", "weather", "carbon", "emission", "environment", "forest", "pollution", "energy", "electricity"],
    "development": ["development", "poverty", "displacement", "household", "informal", "low-income"],
    "finance": ["finance", "financial", "bank", "stock", "market", "asset", "investor", "credit"],
    "macro": ["monetary", "inflation", "growth", "business cycle", "exchange rate", "macro", "productivity"],
    "labor": ["labor", "labour", "wage", "worker", "employment", "unemployment", "migration"],
    "public": ["tax", "public", "policy", "political", "government", "regulation", "welfare"],
    "trade": ["trade", "export", "import", "tariff", "global", "supply chain", "cross-border"],
    "urban": ["urban", "city", "cities", "housing", "regional"],
    "econometrics": ["estimator", "identification", "causal", "regression", "bayesian", "machine learning"],
    "theory": ["equilibrium", "game", "theory", "mechanism", "auction", "contract"],
    "behavior": ["behavior", "behaviour", "preferences", "consumer", "discrimination", "organization"],
    "health": ["health", "mortality", "hospital", "medical", "disease", "height"],
    "education": ["education", "school", "student", "teacher"],
    "firms": ["firm", "enterprise", "industrial", "outsourcing", "services", "innovation"],
    "inequality": ["inequality", "distribution", "mobility", "gender", "racial"],
    "history": ["history", "historical", "nineteenth", "twentieth"],
}

FIELD_TOPIC_MAP = {
    "agriculture_environment_resource": ["agriculture", "environment"],
    "public_political": ["public"],
    "industrial_organization": ["firms"],
    "game_theory": ["theory"],
    "economic_history": ["history"],
    "applied_empirical": ["econometrics"],
    "international": ["trade"],
    "international_trade": ["trade"],
    "macroeconomics": ["macro"],
    "macro_monetary": ["macro"],
    "environmental": ["environment"],
    "behavior_organization": ["behavior"],
    "microeconomics": ["theory"],
    "law_comparative": ["public"],
    "population": ["labor"],
    "chinese": [],
    "general": [],
}

def _values(value: object) -> list[str]:
    if not value:
        return []
    raw = [value] if isinstance(value, str) else value
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [str(item).strip().casefold() for item in raw if str(item).strip()]

def is_working_paper_record(record: dict[str, Any]) -> bool:
    source_type = str(record.get("source_type") or "").casefold()
    return str(record.get("source") or "") == "working_papers" or source_type in WORKING_SOURCE_TYPES

def working_source_id(record: dict[str, Any]) -> str:
    value = str(record.get("source_id") or "").strip()
    if value:
        return value.removeprefix("source-")
    journal_id = str(record.get("journal_id") or "").strip()
    return journal_id.removeprefix("source-") if journal_id.startswith("source-") else ""

@lru_cache(maxsize=4)
def working_source_default_fields(path_value: str = str(DEFAULT_WORKING_SOURCE_CONFIG)) -> dict[str, tuple[str, ...]]:
    path = Path(path_value)
    if not path.exists():
        return {}
    output: dict[str, tuple[str, ...]] = {}
    current_id = ""
    current_fields: list[str] = []
    in_fields = False

    def flush() -> None:
        nonlocal current_fields
        if current_id:
            output[current_id] = tuple(current_fields)
        current_fields = []

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- id:"):
            flush()
            current_id = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            in_fields = False
            continue
        if not current_id:
            continue
        if stripped == "fields:":
            current_fields = []
            in_fields = True
            continue
        if in_fields and stripped.startswith("- "):
            value = stripped[2:].strip().strip('"').strip("'").casefold()
            if value:
                current_fields.append(value)
            continue
        if in_fields and not raw_line.startswith("      "):
            in_fields = False
    flush()
    return output

def article_level_fields(record: dict[str, Any]) -> list[str]:
    fields = _values(record.get("fields"))
    if not fields or not is_working_paper_record(record):
        return fields
    defaults = set(working_source_default_fields().get(working_source_id(record), ()))
    if not defaults:
        return fields
    return [field for field in fields if field not in defaults]

def topic_keyword_matches(haystack: str, keyword: str) -> bool:
    start = 0
    while True:
        index = haystack.find(keyword, start)
        if index < 0:
            return False
        if index == 0 or not ("a" <= haystack[index - 1] <= "z" or "0" <= haystack[index - 1] <= "9"):
            return True
        start = index + 1

def _mapped_topic_codes(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        mapped = [value] if value in TOPIC_RULES else FIELD_TOPIC_MAP.get(value, [])
        for topic in mapped:
            if topic and topic not in output:
                output.append(topic)
    return output

def article_topic_codes(record: dict[str, Any], *, limit: int = 4) -> list[str]:
    explicit = _mapped_topic_codes(_values(record.get("topics")) + _values(record.get("ai_tags")))
    if explicit:
        return explicit[:limit]

    text_values = [record.get("title"), record.get("title_zh"), record.get("abstract"), record.get("abstract_zh")]
    if not is_working_paper_record(record):
        text_values.append(record.get("journal"))
    haystack = " ".join(str(value or "") for value in text_values).casefold()
    inferred: list[str] = []
    for topic, keywords in TOPIC_RULES.items():
        if any(topic_keyword_matches(haystack, keyword) for keyword in keywords):
            inferred.append(topic)
    if inferred:
        return inferred[:limit]

    fallback = _mapped_topic_codes(article_level_fields(record))
    if fallback:
        return fallback[:limit]
    return [] if is_working_paper_record(record) else ["development"]
