from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_daily_vnext
import render_site
import topic_contract


def nber_record(title: str, abstract: str = "") -> dict:
    return {
        "title": title,
        "abstract": abstract,
        "journal": "NBER Working Papers",
        "source": "working_papers",
        "source_id": "nber",
        "source_type": "working_paper",
        "fields": ["general", "macro", "finance", "labor", "health", "trade"],
    }


def test_nber_source_fields_are_recognized_as_source_scope() -> None:
    record = nber_record("A New Measurement Framework")

    assert topic_contract.fields_are_source_scope(record)
    assert topic_contract.public_article_topic_keys(record) == []


def test_article_text_infers_labor_without_leaking_nber_source_scope() -> None:
    record = nber_record("Unbundling Labor")

    topics = topic_contract.public_article_topic_keys(record)

    assert "labor" in topics
    assert "macro" not in topics
    assert "finance" not in topics


def test_policy_title_does_not_inherit_nber_macro_finance() -> None:
    record = nber_record("Using Policy Functions to Estimate Merger Impacts")

    topics = topic_contract.public_article_topic_keys(record)

    assert "public" in topics
    assert "macro" not in topics
    assert "finance" not in topics


def test_world_bank_source_name_is_not_article_topic_evidence() -> None:
    record = {
        "title": "Measurement and Administrative Data",
        "abstract": "",
        "journal": "World Bank Policy Research Working Papers",
        "source": "working_papers",
        "source_id": "world-bank-prwp",
        "source_type": "policy_paper",
        "fields": ["development", "trade", "environment", "public"],
    }

    assert topic_contract.public_article_topic_keys(record) == []


def test_explicit_paper_topics_override_source_scope() -> None:
    record = nber_record("A New Measurement Framework")
    record["topics"] = ["education"]

    assert topic_contract.public_article_topic_keys(record) == ["education"]


def test_daily_and_secondary_surfaces_share_article_topic_projection() -> None:
    record = nber_record("Unbundling Labor")

    assert build_daily_vnext.topic_values(record) == ["劳动经济学"]
    assert render_site.article_topics(record) == ["labor"]
