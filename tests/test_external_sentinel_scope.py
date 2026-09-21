import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from audit_alohomora_coverage import local_indexes, pii_from_value, scope_counts
from monitor_health import build_health


def test_external_scope_counts_keep_broader_candidates_out_of_formal_alarm():
    items = [
        {"scope_bucket": "our_scope"},
        {"scope_bucket": "econ_expand_candidate"},
        {"scope_bucket": "broader_relevant_candidate"},
        {"scope_bucket": "broader_out_of_scope"},
        {"scope_bucket": "unknown_scope"},
    ]

    assert scope_counts(items) == {
        "formal_scope_missing_count": 1,
        "econ_expand_candidate_count": 1,
        "broader_relevant_candidate_count": 1,
        "out_of_scope_reference_count": 1,
        "unknown_scope_candidate_count": 1,
    }


def test_health_uses_formal_scope_count_not_raw_external_candidate_count(tmp_path):
    data_dir = tmp_path / "data"
    (data_dir / "daily").mkdir(parents=True)
    (data_dir / "daily" / "2026-07-28.json").write_text("[]", encoding="utf-8")
    (data_dir / "formal_journal_audit.json").write_text('{"formal_journals": 87, "suspected_missed_journals": 0}', encoding="utf-8")
    (data_dir / "recent72_coverage_audit.json").write_text('{"missing": 0}', encoding="utf-8")
    (data_dir / "quality_report.json").write_text('{"totals": {}}', encoding="utf-8")
    (data_dir / "source_health.json").write_text('{"counts": {}, "coverage_counts": {}}', encoding="utf-8")
    (data_dir / "release_gate.json").write_text('{"ok": true}', encoding="utf-8")
    (data_dir / "local_cnki_status.json").write_text('{"state": "published"}', encoding="utf-8")
    (data_dir / "local_cnki_status.json").write_text('{"state": "published", "last_success_at": "2026-07-28T12:00:00+00:00"}', encoding="utf-8")
    (data_dir / "external_sentinel_alohomora.json").write_text(
        '{"current_possible_missing_count": 76, "formal_scope_missing_count": 0, '
        '"econ_expand_candidate_count": 12, "counts": {"in_scope_missing": 76}}',
        encoding="utf-8",
    )

    report = build_health(data_dir, date="2026-07-28")

    assert report["counts"]["external_sentinel_missing"] == 0
    assert report["counts"]["external_sentinel_econ_expand_candidates"] == 12
    assert report["ok"] is True


def test_sciencedirect_pii_identity_matches_title_variant() -> None:
    local = {
        "title": "Stable portfolios, costly churn",
        "url": "https://doi.org/10.1016/j.foodpol.2026.103168",
        "doi": "10.1016/j.foodpol.2026.103168",
        "pii": "S0306919226001375",
        "journal": "Food Policy",
    }
    titles, dois, piis = local_indexes([local])

    external_title = "Stable portfolios, costly churn: A high-frequency view of diversification and transition in rural Malawi"
    external_link = "https://www.sciencedirect.com/science/article/pii/S0306919226001375"
    pii = pii_from_value(external_link)

    assert titles.get(external_title) is None
    assert pii == "S0306919226001375"
    assert piis[pii] is local


def test_health_warns_on_formal_sentinel_miss_without_hard_failure(tmp_path):
    data_dir = tmp_path / "data"
    (data_dir / "daily").mkdir(parents=True)
    (data_dir / "daily" / "2026-07-28.json").write_text("[]", encoding="utf-8")
    (data_dir / "formal_journal_audit.json").write_text('{"formal_journals": 87, "suspected_missed_journals": 0}', encoding="utf-8")
    (data_dir / "recent72_coverage_audit.json").write_text('{"missing": 0}', encoding="utf-8")
    (data_dir / "quality_report.json").write_text('{"totals": {}}', encoding="utf-8")
    (data_dir / "source_health.json").write_text('{"counts": {}, "coverage_counts": {}}', encoding="utf-8")
    (data_dir / "release_gate.json").write_text('{"ok": true}', encoding="utf-8")
    (data_dir / "local_cnki_status.json").write_text('{"state": "published", "last_success_at": "2026-07-28T12:00:00+00:00"}', encoding="utf-8")
    (data_dir / "external_sentinel_alohomora.json").write_text(
        '{"formal_scope_missing_count": 1, "econ_expand_candidate_count": 12}',
        encoding="utf-8",
    )

    report = build_health(data_dir, date="2026-07-28")

    assert report["ok"] is True
    assert report["failures"] == []
    assert {"code": "external_sentinel_formal_scope_missing", "count": 1} in report["warnings"]
