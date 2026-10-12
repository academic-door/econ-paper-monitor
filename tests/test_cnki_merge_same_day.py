"""Ensure multiple CNKI papers with the same publication day are not overwritten."""
from __future__ import annotations
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import clean_cn_noise
import merge_local_cnki

def _record(i):
    title = "中文期刊文章" + str(i)
    return {"title": title, "journal": "数量经济技术经济研究",
            "journal_id": "journal-edcb877d78", "source": "cnki-rss",
            "source_type": "journal",
            "url": "https://kns.cnki.net/kcms2/article/abstract?v=valid" + str(i),
            "detected_at": "2026-10-12T10:11:16+00:00",
            "published_online": "2026-10-12", "available_online": "2026-10-12",
            "date_source": "cnki_rss_pubdate"}

def test_same_day_cnki_merge_retains_every_new_record(tmp_path):
    daily = tmp_path / "daily"
    daily.mkdir()
    seen = tmp_path / "seen.json"
    seen.write_text('{"papers": {}}', encoding="utf-8")
    batch = [_record(i) for i in range(9)]
    result = merge_local_cnki.merge_records(batch, seen_path=seen, daily_dir=daily, run_date="2026-10-12")
    path = daily / "2026-10-12.json"
    records = json.loads(path.read_text(encoding="utf-8"))
    assert result["archived"] == 9
    assert len(records) == 9
    assert {r["title"] for r in records} == {r["title"] for r in batch}
    assert clean_cn_noise.clean_daily(path) == 0
    assert clean_cn_noise.clean_seen(seen) == 0
    assert len(json.loads(seen.read_text(encoding="utf-8"))["papers"]) == 9

    merge_local_cnki.merge_records(batch, seen_path=seen, daily_dir=daily, run_date="2026-10-12")
    records_again = json.loads(path.read_text(encoding="utf-8"))
    assert len(records_again) == 9
