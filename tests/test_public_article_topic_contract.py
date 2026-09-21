from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_daily_vnext
import public_topics
import render_site


NBER_FIELDS = ["general", "macro", "finance", "labor", "health", "trade"]


def nber_record(*, title: str, abstract: str = "", **extra) -> dict:
    record = {
        "title": title,
        "abstract": abstract,
        "journal": "NBER Working Papers",
        "journal_id": "source-nber",
        "source": "working_papers",
        "source_id": "nber",
        "source_type": "working_paper",
        "fields": list(NBER_FIELDS),
        "china_relevance_status": "none",
    }
    record.update(extra)
    return record


def test_source_default_fields_are_not_article_level_topics() -> None:
    record = nber_record(title="Unbundling Labor")

    assert public_topics.article_level_fields(record) == []
    assert public_topics.article_topic_codes(record) == ["labor"]
    assert render_site.article_topics(record) == ["labor"]
    assert build_daily_vnext.topic_values(record) == ["劳动经济学"]


def test_unrelated_nber_paper_does_not_inherit_macro_or_finance_badges() -> None:
    record = nber_record(
        title="Airline Merger and Consumer Welfare",
        abstract="We study airline competition and consumer welfare after a merger.",
    )

    topics = public_topics.article_topic_codes(record)
    assert "macro" not in topics
    assert "finance" not in topics
    assert "labor" not in topics
    assert "behavior" in topics or "public" in topics


def test_explicit_paper_level_tags_override_source_scope_defaults() -> None:
    record = nber_record(title="A Neutral Working Paper", ai_tags=["finance"])

    assert public_topics.article_topic_codes(record) == ["finance"]
    assert build_daily_vnext.topic_values(record) == ["金融"]


def test_extra_article_field_survives_source_default_stripping() -> None:
    record = nber_record(title="A Neutral Working Paper", fields=NBER_FIELDS + ["urban"])

    assert public_topics.article_level_fields(record) == ["urban"]
    assert public_topics.article_topic_codes(record) == ["urban"]


def test_daily_search_text_does_not_index_raw_source_scope_fields() -> None:
    record = nber_record(title="Unbundling Labor")
    labels = build_daily_vnext.topic_values(record)

    value = build_daily_vnext.search_text(record, labels)
    assert "general" not in value
    assert "综合经济学" not in value
    assert "金融" not in value
    assert "劳动经济学" in value


def test_working_source_config_parser_reads_nber_defaults() -> None:
    defaults = public_topics.working_source_default_fields()

    assert defaults["nber"] == tuple(NBER_FIELDS)


def test_formal_record_keeps_existing_field_fallback() -> None:
    record = {
        "title": "A Neutral Article",
        "journal": "Example Journal",
        "source_type": "journal_article",
        "fields": ["development"],
    }

    assert public_topics.article_topic_codes(record) == ["development"]


def test_daily_maps_shared_firms_and_history_topics() -> None:
    firms = nber_record(title="Firms and Innovation")
    history = nber_record(title="Historical Banking and Economic Change")

    assert "企业与产业" in build_daily_vnext.topic_values(firms)
    assert "经济史" in build_daily_vnext.topic_values(history)
