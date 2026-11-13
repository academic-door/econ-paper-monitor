"""Quarantine one exact Management World training-course notice, not AI research."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from clean_nonpaper_records import clean_nonpaper_records
from dedupe import is_source_navigation_noise


URL = (
    "https://glsj.cbpt.cnki.net/WKB2/WebPublication/paperDigest.aspx?"
    "paperID=30fe9ff3-0f09-44cb-b064-bffa3b465618"
)
TITLE = "“《管理世界》AI工作坊”系列培训课程"
ID = "url:8c012d16a3d3bff1"


def test_exact_source_scoped_notice_only():
    workshop = {"id": ID, "title": TITLE, "journal": "管理世界", "url": URL}
    assert is_source_navigation_noise(workshop)
    assert not is_source_navigation_noise(
        {**workshop, "title": "AI工作坊对经济学研究者技能形成的影响：随机实验"}
    )
    assert not is_source_navigation_noise(
        {**workshop, "url": URL.replace("30fe9ff3", "aaaa0000")}
    )
    assert not is_source_navigation_noise({**workshop, "journal": "经济研究"})
    assert not is_source_navigation_noise(
        {"title": "The impact of AI workshop training on firm productivity",
         "journal": "Applied Economics", "url": "https://doi.org/10.1000/ai-training"}
    )


def test_exact_notice_quarantined_idempotently_with_first_seen_preserved(tmp_path: Path):
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    workshop = {
        "id": ID, "title": TITLE, "journal": "管理世界", "url": URL,
        "source_type": "journal", "source": "cn-official",
        "first_seen": "2026-11-12T11:52:33+00:00",
        "detail_key": "ai-8c012d16a3d3",
    }
    research = {
        "id": "url:research", "title": "AI工作坊对经济发展影响的实证研究",
        "journal": "管理世界", "url": "https://example.org/research",
        "first_seen": "2026-11-12T11:00:00+00:00",
    }
    daily_path = daily_dir / "2026-11-12.json"
    daily_path.write_text(json.dumps([workshop, research], ensure_ascii=False), encoding="utf-8")
    seen_path = tmp_path / "seen.json"
    seen_path.write_text(
        json.dumps({"papers": {x["id"]: deepcopy(x) for x in (workshop, research)}}, ensure_ascii=False),
        encoding="utf-8",
    )
    ledger_path = tmp_path / "ingestion_exclusion_ledger.json"
    ledger_path.write_text('{"records": []}', encoding="utf-8")

    first = clean_nonpaper_records(daily_dir, seen_path, ledger_path)
    assert first == {"daily": 1, "seen": 1, "ledger_added": 1}
    assert [x["id"] for x in json.loads(daily_path.read_text(encoding="utf-8"))] == [research["id"]]
    assert list(json.loads(seen_path.read_text(encoding="utf-8"))["papers"]) == [research["id"]]
    quarantined = json.loads(ledger_path.read_text(encoding="utf-8"))["records"]
    assert len(quarantined) == 1
    assert quarantined[0]["id"] == ID
    assert quarantined[0]["title"] == TITLE
    assert quarantined[0]["first_seen"] == workshop["first_seen"]
    assert quarantined[0]["detail_key"] == workshop["detail_key"]
    assert quarantined[0]["exclusion_status"] == "confirmed_nonpaper"
    assert clean_nonpaper_records(daily_dir, seen_path, ledger_path) == {
        "daily": 0, "seen": 0, "ledger_added": 0,
    }


def test_fast_discovery_commits_exclusion_evidence_with_canonical_data():
    workflow = (ROOT / ".github/workflows/fast-discovery.yml").read_text(encoding="utf-8")
    assert "git diff --quiet -- data/seen.json data/daily data/ingestion_exclusion_ledger.json" in workflow
    assert "git add data/seen.json data/daily data/ingestion_exclusion_ledger.json" in workflow


def test_existing_raw_duplicate_ledger_row_is_upgraded_without_losing_provenance(tmp_path: Path):
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    workshop = {
        "id": ID, "title": TITLE, "journal": "管理世界", "url": URL,
        "source_type": "journal", "source": "cn-official",
        "first_seen": "2026-11-12T11:52:33+00:00", "detail_key": "ai-8c012d16a3d3",
    }
    (daily_dir / "2026-11-12.json").write_text(
        json.dumps([workshop], ensure_ascii=False), encoding="utf-8"
    )
    seen_path = tmp_path / "seen.json"
    seen_path.write_text(
        json.dumps({"papers": {ID: workshop}}, ensure_ascii=False), encoding="utf-8"
    )
    prior_reason = "matching DOI, URL, paper identity, or title already exists in seen catalogue"
    ledger_path = tmp_path / "ingestion_exclusion_ledger.json"
    raw_duplicate = {
        "title": TITLE, "journal": "管理世界", "url": URL,
        "source_type": "journal", "source": "管理世界",
        "stage": "seen_dedupe", "reason": prior_reason,
        "duplicate": True, "seen": True,
        "raw_file": "data/raw/cn/test-source.json",
    }
    ledger_path.write_text(
        json.dumps({"records": [raw_duplicate], "excluded_count": 1}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = clean_nonpaper_records(daily_dir, seen_path, ledger_path)
    assert result == {"daily": 1, "seen": 1, "ledger_added": 0}
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["excluded_count"] == 1
    assert len(ledger["records"]) == 1
    entry = ledger["records"][0]
    assert entry["id"] == ID
    assert entry["exclusion_status"] == "confirmed_nonpaper"
    assert entry["stage"] == "source_rule"
    assert entry["reason"] == "publisher navigation or non-article source noise"
    assert entry["prior_exclusion_reason"] == prior_reason
    assert entry["prior_exclusion_stage"] == "seen_dedupe"
    assert entry["raw_file"] == "data/raw/cn/test-source.json"
    assert entry["first_seen"] == workshop["first_seen"]
    assert entry["detail_key"] == workshop["detail_key"]
    assert clean_nonpaper_records(daily_dir, seen_path, ledger_path) == {
        "daily": 0, "seen": 0, "ledger_added": 0,
    }
