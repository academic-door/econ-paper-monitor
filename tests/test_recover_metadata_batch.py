from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import recover_metadata_batch  # noqa: E402

from recover_metadata_batch import (  # noqa: E402
    apply_recovery,
    decide_date_update,
    is_priority_abstract,
    is_placeholder_abstract,
    load_daily_candidates,
    record_priority,
    record_needs_date,
    run_recovery,
    write_provider_health,
)
from public_integrity import sanitize_record_paths  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def provider_metadata(date: str | None = "2026-07-28") -> dict:
    abstract = (
        "This paper uses a new dataset to estimate the causal effect of policy "
        "reform on firm outcomes. The results are robust to alternative "
        "specifications and heterogeneous effects across regions."
    )
    return {
        "openalex": {
            "authors": ["Alice Author", "Bob Author"],
            "abstract": abstract,
            "available_online": date,
            "published_online": date,
            "date_source": "openalex_publication_date",
            "date_confidence": "C",
        },
        "crossref": {
            "authors": ["Alice Author", "Bob Author"],
            "abstract": abstract,
            "available_online": date,
            "published_online": date,
            "date_source": "crossref_doi_published_online",
            "date_confidence": "C",
        },
        "semantic-scholar": {
            "authors": ["Alice Author", "Bob Author"],
            "abstract": abstract,
            "published_online": date,
        },
    }


def sample_record(**overrides) -> dict:
    record = {
        "id": "test-id",
        "title": "Test paper",
        "authors": [],
        "journal": "Test Journal",
        "source": "crossref",
        "source_type": "journal",
        "url": "https://doi.org/10.1234/test",
        "doi": "10.1234/test",
        "detail_key": "test-paper-abcdef123456",
        "fields": ["计量方法"],
        "first_seen": "2026-07-28T10:00:00+00:00",
        "abstract": None,
        "date_confidence": "C",
    }
    record.update(overrides)
    return record


def make_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    daily_dir = data_dir / "daily"
    daily_dir.mkdir(parents=True)
    write_json(daily_dir / "2026-07-31.json", [sample_record()])
    write_json(
        data_dir / "seen.json",
        {"papers": {"seen-test": sample_record()}},
    )
    write_json(
        data_dir / "metadata_retry_queue.json",
        {
            "records": [
                {
                    "identity": "doi:10.1234/test",
                    "reasons": ["missing_abstract", "weak_date_evidence"],
                    "title": "Test paper",
                }
            ]
        },
    )
    write_json(
        data_dir / "ingestion_retry_queue.json",
        {
            "records": [
                {
                    "title": "Test paper",
                    "doi": "10.1234/test",
                    "retry_status": "pending",
                    "stage": "retry_reingestion",
                }
            ]
        },
    )
    write_json(
        data_dir / "ingestion_exclusion_ledger.json",
        {
            "records": [
                {
                    "title": "Test paper",
                    "doi": "10.1234/test",
                    "retry_status": "pending",
                    "stage": "retry_reingestion",
                }
            ]
        },
    )
    write_json(data_dir / "pending_date_records.json", [])
    write_json(data_dir / "historical_backfill_pending.json", {"records": []})
    write_json(
        data_dir / "source_health.json",
        {"counts": {}, "coverage_counts": {}},
    )
    return data_dir


class TestPlaceholderAbstract:
    def test_none_is_placeholder(self):
        assert is_placeholder_abstract(None) is True

    def test_short_text_is_placeholder(self):
        assert is_placeholder_abstract("Short abstract") is True

    def test_boilerplate_is_placeholder(self):
        assert is_placeholder_abstract("Please login to access this article.") is True
        assert is_placeholder_abstract("This is a preview of subscription content, log in via an institution to check access.") is True

    def test_real_abstract_is_not_placeholder(self):
        text = (
            "We study the causal effect of minimum wages on employment using a "
            "regression discontinuity design. Our estimates are precise and "
            "robust across a wide range of specifications."
        )
        assert is_placeholder_abstract(text) is False


class TestNeedsDetection:
    def test_missing_date(self):
        assert record_needs_date({"date_confidence": "", "abstract": "x"}) is True
        assert record_needs_date({"date_confidence": "C"}) is True

    def test_strong_date(self):
        assert record_needs_date(
            {"date_confidence": "B", "available_online": "2026-07-28"}
        ) is False


class TestDateDiscipline:
    def test_single_openalex_stays_c(self):
        record = sample_record()
        update = decide_date_update(
            record,
            {"openalex": provider_metadata()["openalex"], "crossref": {}, "semantic-scholar": {}},
        )
        assert update["date_confidence"] == "C"
        assert update["date_source"] == "openalex_publication_date"

    def test_single_crossref_stays_c(self):
        record = sample_record()
        update = decide_date_update(
            record,
            {"openalex": {}, "crossref": provider_metadata()["crossref"], "semantic-scholar": {}},
        )
        assert update["date_confidence"] == "C"
        assert update["date_source"] == "crossref_doi_published_online"

    def test_existing_c_date_is_preserved_without_two_source_agreement(self):
        record = sample_record(
            available_online="2020-01-02",
            published_online="2020-01-02",
            date_confidence="C",
            date_source="publisher_page",
        )
        update = decide_date_update(
            record,
            {"openalex": provider_metadata("2021-05-05")["openalex"], "crossref": {}, "semantic-scholar": {}},
        )
        assert update is None
        assert record["available_online"] == "2020-01-02"
        assert record["date_source"] == "publisher_page"

    def test_two_independent_sources_upgrade_to_b(self):
        record = sample_record()
        update = decide_date_update(record, provider_metadata("2026-07-28"))
        assert update["date_confidence"] == "B"
        assert update["date_source"] == "openalex+crossref_crossvalidated"
        assert update["available_online"] == "2026-07-28"

    def test_elsevier_phrase_date_is_not_downgraded(self):
        record = sample_record()
        metadata = provider_metadata("2026-08-06")
        metadata["elsevier"] = {
            "available_online": "2026-08-06",
            "published_online": "2026-08-06",
            "date_source": "elsevier_article_api",
            "date_confidence": "A",
        }
        update = decide_date_update(record, metadata)
        assert update["date_confidence"] == "A"
        assert update["date_source"] == "elsevier_article_api"
        assert update["available_online"] == "2026-08-06"

    def test_elsevier_provisional_date_upgrades_with_crossref(self):
        record = sample_record()
        metadata = provider_metadata("2026-08-06")
        metadata["openalex"] = {}
        metadata["elsevier"] = {
            "available_online": "2026-08-06",
            "published_online": "2026-08-06",
            "date_source": "elsevier_article_api_display_date",
            "date_confidence": "C",
        }
        update = decide_date_update(record, metadata)
        assert update["date_confidence"] == "B"
        assert update["date_source"] == "elsevier+crossref_crossvalidated"
        assert update["available_online"] == "2026-08-06"

    def test_single_elsevier_provisional_date_fills_missing_at_c(self):
        record = sample_record(
            available_online="",
            published_online="",
            date_confidence="F",
        )
        update = decide_date_update(
            record,
            {
                "openalex": {},
                "crossref": {},
                "semantic-scholar": {},
                "elsevier": {
                    "available_online": "2026-08-06",
                    "published_online": "2026-08-06",
                    "date_source": "elsevier_article_api_display_date",
                    "date_confidence": "C",
                },
            },
        )
        assert update["date_confidence"] == "C"
        assert update["date_source"] == "elsevier_article_api_display_date"
        assert update["available_online"] == "2026-08-06"

    def test_conflicting_sources_stay_c(self):
        record = sample_record()
        metadata = provider_metadata("2026-07-28")
        metadata["crossref"]["available_online"] = "2026-07-27"
        metadata["crossref"]["published_online"] = "2026-07-27"
        update = decide_date_update(record, metadata)
        assert update["date_confidence"] == "C"

    def test_unreasonable_year_never_upgrades(self):
        record = sample_record()
        metadata = provider_metadata("1900-01-01")
        update = decide_date_update(record, metadata)
        assert update is None

    def test_accepted_date_is_never_used_as_online_date(self):
        record = sample_record(
            accepted_date="2026-06-01",
            date_confidence="A",
        )
        update = decide_date_update(record, provider_metadata())
        assert update is None

    def test_existing_a_date_is_never_downgraded(self):
        record = sample_record(
            available_online="2026-07-01",
            published_online="2026-07-01",
            date_confidence="A",
        )
        update = decide_date_update(record, provider_metadata())
        assert update is None

    def test_existing_b_date_is_never_downgraded(self):
        record = sample_record(
            available_online="2026-07-01",
            published_online="2026-07-01",
            date_confidence="B",
        )
        update = decide_date_update(record, provider_metadata())
        assert update is None

    def test_apply_recovery_single_source_never_overwrites_existing_c(self):
        record = sample_record(
            available_online="2020-01-02",
            published_online="2020-01-02",
            date_confidence="C",
            date_source="publisher_page",
        )
        providers = {
            "openalex": provider_metadata("2021-05-05")["openalex"],
            "crossref": {},
            "semantic-scholar": {},
        }
        changed, fields = apply_recovery(record, providers)
        assert "date" not in fields
        assert record["available_online"] == "2020-01-02"
        assert record["date_source"] == "publisher_page"


class TestCandidateSelection:
    def test_limit_and_doi_filter(self, tmp_path: Path):
        daily_dir = tmp_path / "daily"
        daily_dir.mkdir(parents=True)
        complete = sample_record(doi="10.1234/complete", abstract="A real abstract with enough length to be complete.", authors=["A. Author"], date_confidence="A", available_online="2026-07-28", published_online="2026-07-28")
        missing = sample_record(doi="10.1234/missing")
        no_doi = sample_record(doi=None)
        write_json(daily_dir / "2026-07-31.json", [complete, missing, no_doi])

        candidates, records_by_doi, _ = load_daily_candidates(
            daily_dir,
            limit=1,
            recent_days=5000,
        )
        assert len(records_by_doi) == 1
        assert "10.1234/missing" in records_by_doi
        assert candidates[0][1]["doi"] == "10.1234/missing"


class TestPriorityAndPathHygiene:
    def test_priority_prefers_captcha_elsevier_and_missing_dates(self):
        captcha = sample_record(
            abstract=None,
            authors=[],
            doi="10.1234/captcha",
            abstract_enrichment_status="blocked-captcha",
            publisher="Elsevier",
            date_confidence="B",
            available_online="2026-07-28",
            published_online="2026-07-28",
        )
        plain = sample_record(
            abstract=None,
            authors=[],
            doi="10.1234/plain",
            date_confidence="B",
            available_online="2026-07-28",
            published_online="2026-07-28",
        )
        missing_date = sample_record(
            abstract="A sufficiently long abstract that satisfies the completeness threshold for this test record.",
            authors=["A. Author"],
            doi="10.1234/date",
            date_confidence="",
            available_online=None,
            published_online=None,
        )
        weak_c = sample_record(
            abstract="A sufficiently long abstract that satisfies the completeness threshold for this test record.",
            authors=["A. Author"],
            doi="10.1234/weak",
            date_confidence="C",
            available_online="2026-07-28",
            published_online="2026-07-28",
        )
        assert is_priority_abstract(captcha) is True
        assert is_priority_abstract(plain) is False
        assert record_priority(captcha)[:3] == (0, 0, 1)
        assert record_priority(plain)[:3] == (0, 1, 1)
        assert record_priority(missing_date)[2] == 0
        assert record_priority(missing_date)[3] < record_priority(weak_c)[3]

    def test_path_sanitize_preserves_detail_key(self):
        record = sample_record(detail_key="stable-detail-key-abcdef123456")
        record["_raw_file"] = r"E:\BaiduSyncdisk\Work\econ-paper-monitor\data\raw\2026-07-31\crossref.json"
        changed = sanitize_record_paths([record])
        assert changed == 1
        assert record["detail_key"] == "stable-detail-key-abcdef123456"
        assert "BaiduSyncdisk" not in record["_raw_file"]

    def test_provider_health_history_is_bounded(self, tmp_path):
        from collections import Counter

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        for index in range(25):
            write_provider_health(
                data_dir,
                {
                    "semantic-scholar": {
                        "attempts": 1,
                        "available": 0,
                        "empty": 0,
                        "statuses": {"rate_limited": 1},
                        "rate_limited": 1,
                        "skipped": 0,
                        "failed": 1,
                        "api_key_configured": False,
                    }
                },
                candidates=1,
                recovered_fields=Counter({"abstract": 0}),
                checked_at=f"2026-08-01T{index:02d}:00:00+00:00",
            )
        payload = json.loads((data_dir / "metadata_provider_health.json").read_text(encoding="utf-8"))
        assert len(payload["runs"]) == 20
        assert payload["latest"] == payload["runs"][-1]

    def test_provider_health_counts_skipped_and_api_key(self, tmp_path):
        data_dir = make_data_dir(tmp_path)
        with patch("recover_metadata_batch.openalex_doi_metadata", return_value=provider_metadata()["openalex"]), patch(
            "recover_metadata_batch.crossref_doi_metadata", return_value=provider_metadata()["crossref"]
        ), patch(
            "recover_metadata_batch.semantic_scholar_doi_metadata",
            return_value={"_status": "skipped_rate_limited", "_provider": "semantic-scholar"},
        ), patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "test-key"}, clear=False):
            report = run_recovery(
                data_dir=data_dir,
                limit=50,
                recent_days=5000,
                timeout=10,
                workers=2,
                dry_run=False,
            )
        health = report["provider_health"]["semantic-scholar"]
        assert health["skipped"] == 1
        assert health["api_key_configured"] is True
        assert health["statuses"]["skipped_rate_limited"] == 1

    def test_openalex_empty_burst_retries_and_records(self, tmp_path):
        data_dir = make_data_dir(tmp_path)
        openalex_calls = {"count": 0}

        def fake_openalex(doi, timeout):
            openalex_calls["count"] += 1
            if openalex_calls["count"] <= 1:
                return {}
            return provider_metadata()["openalex"]

        with patch("recover_metadata_batch.openalex_doi_metadata", side_effect=fake_openalex), patch(
            "recover_metadata_batch.crossref_doi_metadata", return_value=provider_metadata()["crossref"]
        ), patch(
            "recover_metadata_batch.semantic_scholar_doi_metadata",
            return_value=provider_metadata()["semantic-scholar"],
        ), patch.object(recover_metadata_batch, "EMPTY_BURST_RETRY_AFTER", 2), patch.object(
            recover_metadata_batch.time, "sleep"
        ):
            report = run_recovery(
                data_dir=data_dir,
                limit=50,
                recent_days=5000,
                timeout=10,
                workers=2,
                dry_run=False,
            )

        assert openalex_calls["count"] == 2
        assert report["provider_empty_burst"]["openalex"] is True
        health = report["provider_health"]["openalex"]
        assert health["available"] == 1
        assert health["empty_burst"] is True
        assert health["retried_after_empty"] is True
        persisted = json.loads((data_dir / "metadata_provider_health.json").read_text(encoding="utf-8"))
        assert persisted["latest"]["providers"]["openalex"]["retried_after_empty"] is True


class TestRecoveryPersistence:
    def test_write_mode_updates_all_stores(self, tmp_path: Path):
        data_dir = make_data_dir(tmp_path)
        with patch("recover_metadata_batch.openalex_doi_metadata", return_value=provider_metadata()["openalex"]), patch(
            "recover_metadata_batch.crossref_doi_metadata", return_value=provider_metadata()["crossref"]
        ), patch(
            "recover_metadata_batch.semantic_scholar_doi_metadata",
            return_value=provider_metadata()["semantic-scholar"],
        ):
            report = run_recovery(
                data_dir=data_dir,
                limit=50,
                recent_days=5000,
                timeout=10,
                workers=2,
                dry_run=False,
            )

        assert report["recovered"]["abstract"] == 1
        assert report["recovered"]["authors"] == 1
        assert report["recovered"]["date"] == 1
        assert report["files"]["daily_changed"] == 1
        assert report["files"]["seen_updated"] == 1
        assert report["files"]["queue_resolved"] == 1
        assert report["files"]["ledger_resolved"] == 1

        daily = json.loads((data_dir / "daily" / "2026-07-31.json").read_text(encoding="utf-8"))
        assert daily[0]["abstract"]
        assert daily[0]["date_confidence"] == "B"
        assert daily[0]["abstract_status_code"] == "available"
        seen = json.loads((data_dir / "seen.json").read_text(encoding="utf-8"))
        assert seen["papers"]["seen-test"]["date_confidence"] == "B"
        queue = json.loads((data_dir / "metadata_retry_queue.json").read_text(encoding="utf-8"))
        assert queue["records"] == []
        assert len(queue["resolved_records"]) == 1

    def test_dry_run_writes_nothing(self, tmp_path: Path):
        data_dir = make_data_dir(tmp_path)
        before_daily = (data_dir / "daily" / "2026-07-31.json").read_bytes()
        before_seen = (data_dir / "seen.json").read_bytes()
        before_queue = (data_dir / "metadata_retry_queue.json").read_bytes()
        with patch("recover_metadata_batch.openalex_doi_metadata", return_value=provider_metadata()["openalex"]), patch(
            "recover_metadata_batch.crossref_doi_metadata", return_value=provider_metadata()["crossref"]
        ), patch(
            "recover_metadata_batch.semantic_scholar_doi_metadata",
            return_value=provider_metadata()["semantic-scholar"],
        ):
            run_recovery(
                data_dir=data_dir,
                limit=50,
                recent_days=5000,
                timeout=10,
                workers=2,
                dry_run=True,
            )
        assert (data_dir / "daily" / "2026-07-31.json").read_bytes() == before_daily
        assert (data_dir / "seen.json").read_bytes() == before_seen
        assert (data_dir / "metadata_retry_queue.json").read_bytes() == before_queue

    def test_docs_tree_unchanged(self, tmp_path: Path):
        data_dir = make_data_dir(tmp_path)
        before = tree_hash(ROOT / "docs")
        with patch("recover_metadata_batch.openalex_doi_metadata", return_value=provider_metadata()["openalex"]), patch(
            "recover_metadata_batch.crossref_doi_metadata", return_value=provider_metadata()["crossref"]
        ), patch(
            "recover_metadata_batch.semantic_scholar_doi_metadata",
            return_value=provider_metadata()["semantic-scholar"],
        ):
            run_recovery(
                data_dir=data_dir,
                limit=50,
                recent_days=5000,
                timeout=10,
                workers=2,
                dry_run=False,
            )
        assert tree_hash(ROOT / "docs") == before

    def test_rerun_skips_already_resolved_dois(self, tmp_path: Path):
        data_dir = make_data_dir(tmp_path)
        with patch("recover_metadata_batch.openalex_doi_metadata", return_value=provider_metadata()["openalex"]), patch(
            "recover_metadata_batch.crossref_doi_metadata", return_value=provider_metadata()["crossref"]
        ), patch(
            "recover_metadata_batch.semantic_scholar_doi_metadata",
            return_value=provider_metadata()["semantic-scholar"],
        ):
            first = run_recovery(
                data_dir=data_dir,
                limit=50,
                recent_days=5000,
                timeout=10,
                workers=2,
                dry_run=False,
            )
            second = run_recovery(
                data_dir=data_dir,
                limit=50,
                recent_days=5000,
                timeout=10,
                workers=2,
                dry_run=False,
            )

        assert first["recovered"]["abstract"] == 1
        assert second["candidates"] == 0
        assert second["files"]["daily_changed"] == 0
        assert second["files"]["queue_resolved"] == 0
        queue = json.loads((data_dir / "metadata_retry_queue.json").read_text(encoding="utf-8"))
        assert len(queue["resolved_records"]) == 1

    def test_recovery_sanitizes_machine_paths(self, tmp_path: Path):
        data_dir = make_data_dir(tmp_path)
        daily_path = data_dir / "daily" / "2026-07-31.json"
        daily = json.loads(daily_path.read_text(encoding="utf-8"))
        daily[0]["_raw_file"] = r"E:\BaiduSyncdisk\Work\econ-paper-monitor\data\raw\2026-07-31\crossref.json"
        daily_path.write_text(json.dumps(daily), encoding="utf-8")
        seen = json.loads((data_dir / "seen.json").read_text(encoding="utf-8"))
        seen["papers"]["seen-test"]["source_file"] = "/home/runner/work/econ-paper-monitor/econ-paper-monitor/data/raw/x.json"
        (data_dir / "seen.json").write_text(json.dumps(seen), encoding="utf-8")

        with patch("recover_metadata_batch.openalex_doi_metadata", return_value=provider_metadata()["openalex"]), patch(
            "recover_metadata_batch.crossref_doi_metadata", return_value=provider_metadata()["crossref"]
        ), patch(
            "recover_metadata_batch.semantic_scholar_doi_metadata",
            return_value=provider_metadata()["semantic-scholar"],
        ):
            report = run_recovery(
                data_dir=data_dir,
                limit=50,
                recent_days=5000,
                timeout=10,
                workers=2,
                dry_run=False,
            )

        daily_after = json.dumps(json.loads(daily_path.read_text(encoding="utf-8")), ensure_ascii=False)
        seen_after = json.dumps(json.loads((data_dir / "seen.json").read_text(encoding="utf-8")), ensure_ascii=False)
        assert "BaiduSyncdisk" not in daily_after
        assert "home/runner" not in seen_after
        assert report["after"]["integrity"]["machine_path_leaks"] == 0

    def test_provider_health_reports_semantic_scholar_status(self, tmp_path: Path):
        data_dir = make_data_dir(tmp_path)
        with patch("recover_metadata_batch.openalex_doi_metadata", return_value=provider_metadata()["openalex"]), patch(
            "recover_metadata_batch.crossref_doi_metadata", return_value=provider_metadata()["crossref"]
        ), patch(
            "recover_metadata_batch.semantic_scholar_doi_metadata",
            return_value={"_status": "not_found", "_provider": "semantic-scholar"},
        ):
            report = run_recovery(
                data_dir=data_dir,
                limit=50,
                recent_days=5000,
                timeout=10,
                workers=2,
                dry_run=False,
            )

        health = report["provider_health"]["semantic-scholar"]
        assert health["attempts"] == 1
        assert health["available"] == 0
        assert health["failed"] == 1
        assert health["statuses"]["not_found"] == 1
        persisted = json.loads((data_dir / "metadata_provider_health.json").read_text(encoding="utf-8"))
        assert persisted["latest"]["providers"]["semantic-scholar"]["statuses"]["not_found"] == 1


class TestElsevierPiiRecovery:
    def test_loads_doi_less_sciencedirect_pii_candidates_only(self, tmp_path: Path):
        daily_dir = tmp_path / "daily"
        daily_dir.mkdir()
        pii_only = sample_record(
            doi=None,
            url="https://www.sciencedirect.com/science/article/pii/S0264999326002385?dgcid=rss_sd_all",
            authors=["Alice Author"],
            abstract=None,
            date_confidence="F",
        )
        doi_backed = sample_record(
            doi="10.1016/j.econmod.2026.106001",
            url="https://www.sciencedirect.com/science/article/pii/S0264999326002999",
            authors=["Bob Author"],
            abstract=None,
            date_confidence="F",
        )
        write_json(daily_dir / "2026-09-16.json", [pii_only, doi_backed])

        candidates, records_by_pii, _ = recover_metadata_batch.load_elsevier_pii_candidates(
            daily_dir,
            limit=10,
            recent_days=5000,
        )

        assert len(candidates) == 1
        assert set(records_by_pii) == {"s0264999326002385"}
        assert candidates[0][1]["doi"] is None

    def test_pii_api_parses_abstract_and_publisher_online_date(self):
        core = {
            "dc:title": "PII-only Elsevier Paper",
            "prism:url": "https://api.elsevier.com/content/article/pii/S0264999326002385",
            "pii": "S0264999326002385",
            "prism:coverDisplayDate": "Available online 16 September 2026",
            "dc:description": (
                "This is a sufficiently long Elsevier abstract for a DOI-less PII record. "
                "It contains enough substantive text to pass the recovery threshold safely."
            ),
        }
        payload = json.dumps({"full-text-retrieval-response": {"coredata": core}})
        with patch.dict(os.environ, {"ELSEVIER_API_KEY": "k"}, clear=False), patch(
            "recover_metadata_batch._els_request", return_value=(payload, {})
        ) as request_mock, patch.object(recover_metadata_batch.time, "sleep"):
            meta = recover_metadata_batch.elsevier_pii_metadata("S0264999326002385", 10)

        assert meta["_status"] == "available"
        assert meta["available_online"] == "2026-09-16"
        assert meta["date_confidence"] == "A"
        assert "sufficiently long Elsevier abstract" in meta["abstract"]
        assert "/content/article/pii/S0264999326002385" in request_mock.call_args.args[0]

    def test_run_recovery_repairs_pii_only_record_without_identity_rewrite(self, tmp_path: Path):
        data_dir = tmp_path / "data"
        daily_dir = data_dir / "daily"
        daily_dir.mkdir(parents=True)
        pii_url = "https://www.sciencedirect.com/science/article/pii/S0264999326002385?dgcid=rss_sd_all"
        record = sample_record(
            doi=None,
            url=pii_url,
            authors=["Alice Author"],
            abstract=None,
            official_date="2026-10-01",
            date_confidence="F",
            first_seen="2026-09-16T09:21:48+00:00",
        )
        write_json(daily_dir / "2026-09-16.json", [record])
        write_json(data_dir / "seen.json", {"papers": {"seen-pii": dict(record)}})
        write_json(
            data_dir / "metadata_retry_queue.json",
            {"records": [{"identity": f"url:{pii_url.casefold()}", "url": pii_url, "title": record["title"], "reasons": ["missing_abstract", "weak_date_evidence"]}]},
        )
        write_json(data_dir / "ingestion_retry_queue.json", {"records": [], "resolved_records": []})
        write_json(data_dir / "ingestion_exclusion_ledger.json", {"records": []})
        write_json(data_dir / "pending_date_records.json", [])
        write_json(data_dir / "historical_backfill_pending.json", {"records": []})
        write_json(data_dir / "source_health.json", {"counts": {}, "coverage_counts": {}})

        recovered_abstract = (
            "This recovered publisher abstract is sufficiently long and authoritative for the "
            "PII-only Elsevier record while preserving the existing canonical identity."
        )
        with patch(
            "recover_metadata_batch.elsevier_pii_metadata",
            return_value={
                "_status": "available",
                "pii": "S0264999326002385",
                "abstract": recovered_abstract,
                "abstract_source": "elsevier_article_api_full",
                "available_online": "2026-09-16",
                "published_online": "2026-09-16",
                "date_source": "elsevier_article_api",
                "date_confidence": "A",
            },
        ):
            report = run_recovery(
                data_dir=data_dir,
                limit=0,
                pii_limit=10,
                recent_days=5000,
                timeout=10,
                workers=1,
                dry_run=False,
            )

        persisted = json.loads((daily_dir / "2026-09-16.json").read_text(encoding="utf-8"))[0]
        seen = json.loads((data_dir / "seen.json").read_text(encoding="utf-8"))["papers"]["seen-pii"]
        assert report["doi_candidates"] == 0
        assert report["pii_candidates"] == 1
        assert report["recovered"]["abstract"] >= 1
        assert report["recovered"]["date"] >= 1
        assert persisted["abstract"] == recovered_abstract
        assert persisted["date_confidence"] == "A"
        assert persisted["available_online"] == "2026-09-16"
        assert persisted["doi"] is None
        assert persisted["first_seen"] == "2026-09-16T09:21:48+00:00"
        assert seen["doi"] is None
        assert seen["first_seen"] == "2026-09-16T09:21:48+00:00"


class TestElsevierProvider:
    def _payload(self) -> str:
        core = {
            "dc:title": "Example Elsevier Paper",
            "prism:url": "https://api.elsevier.com/content/article/pii/S1234567890123456",
            "pii": "S1234567890123456",
            "dc:description": (
                "This is a sufficiently long Elsevier abstract that describes the methods "
                "and results of the paper in enough detail to pass the minimum length "
                "threshold required for metadata recovery."
            ),
        }
        return json.dumps({"full-text-retrieval-response": {"coredata": core}})

    def test_parses_title_abstract_and_rate_headers(self):
        headers = {
            "X-RateLimit-Limit": "2000",
            "X-RateLimit-Remaining": "1998",
            "X-RateLimit-Reset": "1700000000",
        }
        with patch.dict(os.environ, {"ELSEVIER_API_KEY": "k", "ELSEVIER_INST_TOKEN": "t"}, clear=False), patch(
            "recover_metadata_batch._els_request", return_value=(self._payload(), headers)
        ), patch.object(recover_metadata_batch.time, "sleep"):
            meta = recover_metadata_batch.elsevier_doi_metadata("10.1016/j.jdeveco.2026.103880", 10)

        assert meta["_status"] == "available"
        assert meta["title"] == "Example Elsevier Paper"
        assert "sufficiently long Elsevier abstract" in meta["abstract"]
        assert meta["abstract_source"] == "elsevier_article_api_full"
        assert meta["_rate_limit"]["X-RateLimit-Remaining"] == "1998"

    def test_extracts_publisher_available_online_date(self):
        core = {
            "dc:title": "Example Elsevier Paper",
            "prism:url": "https://api.elsevier.com/content/article/pii/S1234567890123456",
            "pii": "S1234567890123456",
            "prism:coverDisplayDate": "Available online 6 August 2026",
        }
        payload = json.dumps({"full-text-retrieval-response": {"coredata": core}})
        with patch.dict(os.environ, {"ELSEVIER_API_KEY": "k", "ELSEVIER_INST_TOKEN": "t"}, clear=False), patch(
            "recover_metadata_batch._els_request", return_value=(payload, {})
        ), patch.object(recover_metadata_batch.time, "sleep"):
            meta = recover_metadata_batch.elsevier_doi_metadata("10.1016/j.jdeveco.2026.103880", 10)

        assert meta["_status"] == "available"
        assert meta["available_online"] == "2026-08-06"
        assert meta["published_online"] == "2026-08-06"
        assert meta["date_source"] == "elsevier_article_api"
        assert meta["date_confidence"] == "A"

    def test_not_configured_skips_network(self):
        with patch.dict(os.environ, {}, clear=True):
            meta = recover_metadata_batch.elsevier_doi_metadata("10.1016/j.x.1", 10)
        assert meta["_status"] == "not_configured"

    def test_404_maps_not_found(self):
        with patch.dict(os.environ, {"ELSEVIER_API_KEY": "k"}, clear=False), patch(
            "recover_metadata_batch._els_request",
            side_effect=urllib.error.HTTPError("https://api.elsevier.com/x", 404, "Not Found", {}, None),
        ), patch.object(recover_metadata_batch.time, "sleep"):
            meta = recover_metadata_batch.elsevier_doi_metadata("10.1016/j.x.404", 10)
        assert meta["_status"] == "not_found"

    def test_429_retries_once_then_succeeds(self):
        with patch.dict(os.environ, {"ELSEVIER_API_KEY": "k"}, clear=False), patch(
            "recover_metadata_batch._els_request",
            side_effect=[
                urllib.error.HTTPError(
                    "https://api.elsevier.com/x",
                    429,
                    "Too Many Requests",
                    {"Retry-After": "2"},
                    None,
                ),
                (self._payload(), {"X-RateLimit-Remaining": "1999"}),
            ],
        ), patch.object(recover_metadata_batch.time, "sleep") as sleep_mock:
            meta = recover_metadata_batch.elsevier_doi_metadata("10.1016/j.x.429", 10)

        assert meta["_status"] == "available"
        sleep_mock.assert_any_call(2.0)

    def test_429_twice_marks_rate_limited(self):
        with patch.dict(os.environ, {"ELSEVIER_API_KEY": "k"}, clear=False), patch(
            "recover_metadata_batch._els_request",
            side_effect=[
                urllib.error.HTTPError(
                    "https://api.elsevier.com/x",
                    429,
                    "Too Many Requests",
                    {"Retry-After": "2"},
                    None,
                ),
                urllib.error.HTTPError(
                    "https://api.elsevier.com/x",
                    429,
                    "Too Many Requests",
                    {"X-RateLimit-Remaining": "0"},
                    None,
                ),
            ],
        ), patch.object(recover_metadata_batch.time, "sleep"):
            meta = recover_metadata_batch.elsevier_doi_metadata("10.1016/j.x.429", 10)

        assert meta["_status"] == "rate_limited"

    def test_apply_recovery_uses_elsevier_abstract_when_others_empty(self):
        record = sample_record(abstract=None)
        providers = {
            "openalex": {},
            "crossref": {},
            "semantic-scholar": {},
            "elsevier": {
                "abstract": provider_metadata()["openalex"]["abstract"],
                "abstract_source": "elsevier_article_api_full",
                "_status": "available",
            },
        }

        changed, fields = apply_recovery(record, providers)

        assert changed is True
        assert "abstract" in fields
        assert record["abstract_source"] == "elsevier_article_api_full"

    def test_apply_recovery_never_overwrites_manual_abstract(self):
        record = sample_record(
            abstract="Manual preview text.",
            abstract_source="manual-publisher",
            abstract_completeness="preview",
            abstract_truncated=True,
            abstract_status_code="preview_truncated",
            date_confidence="B",
            available_online="2026-07-28",
            published_online="2026-07-28",
            date_source="publisher_published_online",
        )
        before = record["abstract"]
        providers = {
            "openalex": {"abstract": provider_metadata()["openalex"]["abstract"]},
            "crossref": {},
            "semantic-scholar": {},
            "elsevier": {
                "abstract": provider_metadata()["openalex"]["abstract"],
                "abstract_source": "elsevier_article_api_full",
                "_status": "available",
            },
        }

        changed, fields = apply_recovery(record, providers)

        assert changed is False
        assert record["abstract"] == before
        assert "abstract" not in fields

    def test_summarize_health_records_elsevier_rate_limits(self):
        providers_by_doi = {
            "10.1016/a": {
                "elsevier": {
                    "_status": "available",
                    "_rate_limit": {"X-RateLimit-Limit": "2000", "X-RateLimit-Remaining": "1500"},
                }
            },
            "10.1016/b": {
                "elsevier": {
                    "_status": "available",
                    "_rate_limit": {"X-RateLimit-Limit": "2000", "X-RateLimit-Remaining": "1200"},
                }
            },
        }

        health = recover_metadata_batch.summarize_provider_health(providers_by_doi)
        elsevier = health["elsevier"]

        assert elsevier["available"] == 2
        assert elsevier["rate_limit_headers"]["X-RateLimit-Remaining"] == "1200"

    def test_fetch_metadata_only_uses_elsevier_for_10_1016(self):
        with patch.dict(os.environ, {"ELSEVIER_API_KEY": "k"}, clear=False), patch(
            "recover_metadata_batch.openalex_doi_metadata", return_value={}
        ), patch("recover_metadata_batch.crossref_doi_metadata", return_value={}), patch(
            "recover_metadata_batch.semantic_scholar_doi_metadata", return_value={}
        ), patch(
            "recover_metadata_batch.elsevier_doi_metadata",
            return_value={"_status": "available"},
        ) as els_mock:
            providers, _ = recover_metadata_batch.fetch_metadata_for_doi("10.1111/x.1", 10)
            assert "elsevier" not in providers
            els_mock.assert_not_called()

            providers_elsevier, _ = recover_metadata_batch.fetch_metadata_for_doi(
                "10.1016/j.jdeveco.2026.103880",
                10,
            )
            assert "elsevier" in providers_elsevier
            assert els_mock.call_count == 1
