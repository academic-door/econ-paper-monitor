"""Decision 0022 consumer fixture tests; production consumer is deliberately OFF.

The producer #97 status/public-artifact representation must be reconciled
against its actual deployed schema before adding any scheduled consumer step.
"""
from __future__ import annotations

import copy
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import reconcile_nber_accepted_week as consumer  # noqa: E402

OBSERVED = datetime(2026, 10, 11, 16, 20, tzinfo=UTC)


def digest(value: str) -> str:
    return hashlib.sha256(" ".join(value.split()).encode("utf-8")).hexdigest()


def complete_week() -> tuple[dict, dict]:
    # 44 normal high-number entries plus the late-arriving low number w35782.
    ids = [f"w{x}" for x in range(35840, 35884)] + ["w35782"]
    papers = []
    for number in ids:
        title = f"An economics research paper {number}"
        abstract = f"This paper studies research and evidence for {number}."
        papers.append({
            "number": number,
            "title": title,
            "authors": ["A. Researcher", "B. Scholar"],
            "abstract": abstract,
            "zh_title": f"经济学论文{number}",
            "zh_abstract": f"本文研究{number}的经济学问题。",
            "url": f"https://www.nber.org/papers/{number}",
            "week_date": "2026-10-05",
            "issue_date": "2026-10-05",
            "public_date": "2026-10-08",
            "translation_state": "valid",
            "zh_title_valid": True,
            "zh_abstract_valid": True,
            "source_text_sha256": consumer.english_source_sha256(title, abstract),
        })
    artifact = {
        "schema_version": 1,
        "week": "2026-10-05",
        "accepted": True,
        "source_complete": True,
        "source_kind": "api",
        "membership_basis": "official-nber-new-this-week-api",
        "source_evidence": "official New This Week marker; stable=True; official=45; retained=0",
        "paper_count": 45,
        "built_at": "2026-10-11 19:13:59 北京时间",
        "papers": papers,
    }
    artifact["papers_sha256"] = consumer.roster_sha256(papers)
    status = {
        "latest_week": artifact["week"],
        "latest_week_papers": 45,
        "latest_week_source": "api",
        "latest_week_source_complete": True,
        "latest_week_source_evidence": artifact["source_evidence"],
        "built_at": artifact["built_at"],
    }
    return artifact, status


def apply(artifact, status, *, seen=None, daily=None, cache=None):
    return consumer.reconcile(
        artifact, status,
        seen if seen is not None else {"papers": {}},
        daily if daily is not None else {},
        cache if cache is not None else {"records": {}},
        observation_time=OBSERVED,
    )


def test_full_accepted_45_membership_includes_low_number_late_record_and_actual_observation():
    a, s = complete_week()
    seen, days, cache, report = apply(a, s)
    assert len(seen["papers"]) == 45
    assert len(days["2026-10-12"]) == 45  # UTC 16:20 is Beijing October 12 00:20
    late = seen["papers"]["doi:10.3386/w35782"]
    assert late["first_seen"] == "2026-10-11T16:20:00+00:00"
    assert late["detected_at"] == late["first_seen_at"] == late["first_seen"]
    assert "published_online" not in late  # never invent official online date
    assert late["nber_source_public_date"] == "2026-10-08"
    assert late["date_confidence"] == "F"
    assert late["nber_reconciliation_provenance"] == "frontier-door"
    assert late["title_zh"] == "经济学论文w35782"
    assert cache["records"]["10.3386/w35782"]["upstream"] == "frontier-door"
    assert report["accepted_count"] == 45
    assert report["added"] == 45 and report["missing_after"] == 0
    assert report["cache_fields_updated"] == 90


def test_repeated_reconcile_is_idempotent_and_no_duplicate_discovery():
    a, s = complete_week()
    seen1, days1, cache1, report1 = apply(a, s)
    seen2, days2, cache2, report2 = apply(a, s, seen=seen1, daily=days1, cache=cache1)
    assert seen2 == seen1
    assert days2 == days1
    assert cache2 == cache1
    assert report2["added"] == 0
    assert report1["added"] == 45
    assert report2["matched_before"] == 45


def test_existing_direct_nber_records_preserve_chronology_and_human_zh():
    a, s = complete_week()
    number = "w35782"
    direct = {
        "id": "old-local-stable-id",
        "source_id": "nber",
        "paper_number": number,
        "url": f"https://www.nber.org/papers/{number}",
        "doi": f"10.3386/{number}",
        "title": f"An economics research paper {number}",
        "abstract": f"This paper studies research and evidence for {number}.",
        "title_zh": "人工定稿标题",
        "abstract_zh": "人工审校摘要",
        "first_seen": "2026-10-06T02:05:00+00:00",
        "first_seen_at": "2026-10-06T02:05:00+00:00",
        "detected_at": "2026-10-06T02:05:00+00:00",
        "source": "working_papers",
        "date_source": "source_list_date",
        "published_online": "2026-10-06",
    }
    seen = {"papers": {"old-local-stable-id": copy.deepcopy(direct), "unrelated": {"title": "Other work"}}}
    days = {"2026-10-06": [copy.deepcopy(direct)]}
    seen2, days2, cache, report = apply(a, s, seen=seen, daily=days)
    same = seen2["papers"]["old-local-stable-id"]
    assert same["first_seen"] == "2026-10-06T02:05:00+00:00"
    assert same["first_seen_at"] == same["detected_at"] == same["first_seen"]
    assert same["id"] == "old-local-stable-id"
    assert same["published_online"] == "2026-10-06"
    assert same["date_source"] == "source_list_date"
    assert same["title_zh"] == "人工定稿标题"
    assert same["abstract_zh"] == "人工审校摘要"
    assert days2["2026-10-06"][0]["title_zh"] == "人工定稿标题"
    assert seen2["papers"]["unrelated"]["title"] == "Other work"
    assert report["matched_before"] == 1 and report["added"] == 44
    assert "10.3386/w35782" not in cache.get("records", {})


def test_conflicting_duplicate_daily_identity_does_not_auto_repair():
    a, s = complete_week()
    n = "w35782"
    direct = {"source_id": "nber", "paper_number": n, "title": "Local"}
    _, _, _, report = apply(a, s, daily={"2026-10-05": [copy.deepcopy(direct), copy.deepcopy(direct)]})
    assert report["conflicts"] == 1
    assert report["missing_after"] == 1


def test_unaccepted_incomplete_wrong_status_or_duplicate_fail_before_any_write():
    a, s = complete_week()
    modifications = [
        lambda x, y: x.__setitem__("accepted", False),
        lambda x, y: x.__setitem__("source_complete", False),
        lambda x, y: x.__setitem__("schema_version", 99),
        lambda x, y: x["papers"].pop(),
        lambda x, y: x["papers"].__setitem__(-1, copy.deepcopy(x["papers"][0])),
        lambda x, y: x["papers"][0].__setitem__("url", "https://evil.example/w35782"),
        lambda x, y: y.__setitem__("built_at", "other-run"),
        lambda x, y: y.__setitem__("latest_week_source_complete", False),
        lambda x, y: y.__setitem__("latest_week_papers", 44),
        lambda x, y: y.pop("latest_week"),
    ]
    for mutation in modifications:
        art, status = copy.deepcopy(a), copy.deepcopy(s)
        mutation(art, status)
        with pytest.raises(consumer.UnacceptedWeek):
            apply(art, status)


def test_unaccepted_stale_zh_and_english_mismatch_never_enter_cache():
    a, s = complete_week()
    # ③ explicit stale translation cannot be propagated at all.
    stale_paper = a["papers"][0]
    stale_paper["translation_state"] = "stale"
    stale_paper["zh_title_valid"] = False
    stale_paper["zh_abstract_valid"] = False
    stale_paper["zh_title"] = None
    stale_paper["zh_abstract"] = None
    no_abstract = a["papers"][1]
    no_abstract["zh_abstract_valid"] = False
    no_abstract["zh_abstract"] = None
    no = a["papers"][2]
    no["zh_title_valid"] = False
    no["zh_title"] = None
    a["papers_sha256"] = consumer.roster_sha256(a["papers"])
    # A preexisting English revision cannot inherit ③ text from old EN.
    existing = {
        "id": "local",
        "source_id": "nber",
        "paper_number": no["number"],
        "title": "Locally updated and revised English title",
    }
    seen, days, cache, report = apply(a, s, seen={"papers": {"local": existing}})
    assert not seen["papers"]["doi:10.3386/w35840"].get("title_zh")
    assert not seen["papers"]["doi:10.3386/w35841"].get("abstract_zh")
    assert not seen["papers"]["local"].get("title_zh")
    assert "title_zh" not in cache["records"].get("10.3386/w35840", {})
    assert "abstract_zh" not in cache["records"].get("10.3386/w35841", {})
    assert report["added"] == 44
    # Tampering with source English/hash must reject the complete artifact.
    tampered = copy.deepcopy(a)
    tampered["papers"][0]["source_text_sha256"] = "0" * 64
    tampered["papers_sha256"] = consumer.roster_sha256(tampered["papers"])
    with pytest.raises(consumer.UnacceptedWeek):
        apply(tampered, s)


def test_existing_cache_corrections_are_never_overwritten():
    a, s = complete_week()
    preexisting = {"records": {
        "10.3386/w35782": {"title": "Old English version", "title_zh": "人工缓存修订"}
    }}
    seen, _, cache, _ = apply(a, s, cache=preexisting)
    assert cache["records"]["10.3386/w35782"]["title_zh"] == "人工缓存修订"
    assert cache["records"]["10.3386/w35782"]["title"] == "Old English version"
    assert seen["papers"]["doi:10.3386/w35782"]["first_seen"].startswith("2026-10-11")


def test_existing_matching_owner_reviewed_cache_beats_upstream_reuse():
    a, s = complete_week()
    item = a["papers"][0]
    cache_input = {"records": {
        "10.3386/" + item["number"]: {
            "title": item["title"],
            "title_zh": "经人工校对的缓存标题",
            "abstract": item["abstract"],
            "abstract_hash": hashlib.sha1(item["abstract"].encode("utf-8")).hexdigest(),
            "abstract_zh": "经人工校对的缓存摘要",
            "model": "reviewed-owner",
        }
    }}
    seen, _, cache, _ = apply(a, s, cache=cache_input)
    entry = seen["papers"]["doi:10.3386/" + item["number"]]
    assert entry["title_zh"] == "经人工校对的缓存标题"
    assert entry["abstract_zh"] == "经人工校对的缓存摘要"
    assert cache["records"]["10.3386/" + item["number"]] == cache_input["records"]["10.3386/" + item["number"]]


def test_frontier_title_cache_requires_matching_english_before_paid_call():
    import argparse
    import translate
    options = argparse.Namespace(sleep=0, timeout=1, stop_on_error=True)
    cache = {"10.3386/w35782": {
        "upstream": "frontier-door",
        "title": "Canonical accepted English",
        "title_zh": "已接受的中文",
    }}
    revised = [{"doi": "10.3386/w35782", "title": "Materially revised English"}]
    result = translate.translate_records(
        revised, options, "unused", "", "", cache,
        title_limit=0, abstract_limit=0, deadline=None,
    )
    assert revised[0].get("title_zh") is None
    assert result[0] == 0 and result[3] == 0
    accepted = [{"doi": "10.3386/w35782", "title": "Canonical accepted English"}]
    result = translate.translate_records(
        accepted, options, "unused", "", "", cache,
        title_limit=0, abstract_limit=0, deadline=None,
    )
    assert accepted[0]["title_zh"] == "已接受的中文"
    assert result[3] == 1


def test_no_workflow_activates_public_consumer_before_producer_97():
    workflow = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    ai_workflow = (ROOT / ".github" / "workflows" / "ai-enrichment.yml").read_text(encoding="utf-8")
    assert "reconcile_nber_accepted_week.py" not in workflow
    assert "reconcile_nber_accepted_week.py" not in ai_workflow
    assert consumer.NBER_WEEK_URL.endswith("/data/latest_week.json")
    assert consumer.NBER_STATUS_URL.endswith("/data/site_status.json")
