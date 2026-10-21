"""Regression: an observed SJJJ online-first roster must survive the Daily pipeline.

The publisher's old prepublication dates are source evidence, not first_seen.
The first-party online-first URL is a real article, not a Chinese site menu.
"""
from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_ingestion
import clean_cn_noise
import clean_historical_working_papers
import dedupe
import release_gate
import normalize_records
import remove_seen_backflow

DATE = "2026-10-21"
DETECTED = "2026-10-21T07:02:00+00:00"
OLD_SEEN = "2026-06-15T14:57:56+00:00"
JOURNAL = "journal-679eaa2a0c"
ROOT = "https://sjjj.magtech.com.cn/CN/abstract/abstract"
TITLES = {
    "1193": "打开比较优势技术来源的“黑箱”：中间投入品结构的视角",
    "1192": "专精特新政策与创新质量——基于上市公司发明专利文本的经验证据",
    "1191": "改革落实的传导机制：企业感知的视角",
    "1190": "何以测智？测度人工智能的一种新方法及其优越性",
    "1189": "城市中心跨越世纪的记忆与嬗变：从历史城墙到现代天际",
    "1162": "数字经济新业态与包容性就业基于外卖配送站点入驻的研究",
}


def _raw(article_id: str) -> dict:
    prepub = "2026-01-13" if article_id == "1162" else "2026-04-10"
    return {
        "title": TITLES[article_id],
        "journal": "世界经济",
        "journal_id": JOURNAL,
        "source_type": "journal",
        "source": "cn-official",
        "url": f"{ROOT}{article_id}.shtml",
        "source_url": "https://sjjj.magtech.com.cn/CN/online_first",
        "detected_at": DETECTED,
        "available_online": prepub,
        "published_online": prepub,
        "date_source": "official_publish_date",
        "date_confidence": "A",
        "raw_data": {
            "source_scope": "PUBLISHER_VISIBLE_ONLINE_FIRST_ONLY",
            "publisher_article_id": article_id,
            "observed_at": DETECTED,
            "observed_total": 6,
            "observed_page_count": 1,
        },
    }


def test_online_first_new_five_survive_dedupe_quarantine_noise_and_release_gate(
    tmp_path: Path, monkeypatch
) -> None:
    raw = tmp_path / "raw"
    (raw / "cn").mkdir(parents=True)
    daily = tmp_path / "daily"
    daily.mkdir()
    raw_records = [_raw(article_id) for article_id in TITLES]
    (raw / "cn" / f"{DATE}-sjjj-online-first.json").write_text(
        json.dumps(raw_records, ensure_ascii=False), encoding="utf-8"
    )
    # 1191 was already discovered from CNKI/printed issue and must not get
    # today's duplicate or a rewritten historical first_seen.
    old = {
        "title": TITLES["1191"],
        "journal": "世界经济",
        "journal_id": JOURNAL,
        "source_type": "journal",
        "source": "cnki-rss",
        "url": "https://sjjj.magtech.com.cn/CN/Y2026/V49/I6/61",
        "available_online": "2026-06-10",
        "published_online": "2026-06-10",
        "date_source": "cnki_rss_pubdate",
        "detected_at": OLD_SEEN,
        "first_seen": OLD_SEEN,
    }
    old["id"] = dedupe.stable_id(old)
    (daily / "2026-06-10.json").write_text(
        json.dumps([old], ensure_ascii=False), encoding="utf-8"
    )
    seen_path = tmp_path / "seen.json"
    seen_path.write_text(
        json.dumps({"papers": {old["id"]: old}}, ensure_ascii=False), encoding="utf-8"
    )

    monkeypatch.setattr(dedupe, "record_run", lambda *_a, **_kw: None)
    monkeypatch.setattr(dedupe, "record_source", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        sys, "argv",
        ["dedupe.py", "--raw-dir", str(raw), "--daily-dir", str(daily),
         "--seen", str(seen_path), "--date", DATE],
    )
    dedupe.main()
    first = json.loads((daily / f"{DATE}.json").read_text(encoding="utf-8"))
    assert len(first) == 5
    assert {r["url"] for r in first} == {
        _raw(i)["url"] for i in TITLES if i != "1191"
    }
    assert all(r["first_seen"] == DETECTED for r in first)
    assert all(r["published_online"] != DATE for r in first)

    monkeypatch.setattr(
        sys, "argv",
        ["clean_historical_working_papers.py", "--daily-dir", str(daily),
         "--pending", str(tmp_path / "pending.json")],
    )
    clean_historical_working_papers.main()
    pending = json.loads((tmp_path / "pending.json").read_text(encoding="utf-8"))
    assert pending == []

    monkeypatch.setattr(clean_cn_noise, "record_source", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        sys, "argv",
        ["clean_cn_noise.py", "--daily-dir", str(daily), "--seen", str(seen_path)],
    )
    clean_cn_noise.main()

    # Replay the next production stages: normalization and seen-backflow
    # filtering must preserve all genuine observed first discoveries.
    monkeypatch.setattr(normalize_records, "record_source", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        sys, "argv",
        ["normalize_records.py", "--daily-dir", str(daily), "--date", DATE],
    )
    normalize_records.main()
    monkeypatch.setattr(remove_seen_backflow, "record_source", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        sys, "argv",
        ["remove_seen_backflow.py", "--daily-dir", str(daily),
         "--seen", str(seen_path), "--date", DATE,
         "--future-records", str(tmp_path / "future.json")],
    )
    remove_seen_backflow.main()

    # Idempotent second run must not create duplicates or rewrite first_seen.
    monkeypatch.setattr(
        sys, "argv",
        ["dedupe.py", "--raw-dir", str(raw), "--daily-dir", str(daily),
         "--seen", str(seen_path), "--date", DATE],
    )
    dedupe.main()
    today = json.loads((daily / f"{DATE}.json").read_text(encoding="utf-8"))
    past = json.loads((daily / "2026-06-10.json").read_text(encoding="utf-8"))
    assert len(today) == 5 and len(past) == 1
    assert past[0]["first_seen"] == OLD_SEEN
    assert all(row["first_seen"] == DETECTED for row in today)
    seen = json.loads(seen_path.read_text(encoding="utf-8"))["papers"]
    assert len(seen) == 6

    monkeypatch.setattr(audit_ingestion, "DATA_DIR", tmp_path)
    monkeypatch.setattr(audit_ingestion, "record_source", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        sys, "argv",
        ["audit_ingestion.py", "--date", DATE, "--raw-dir", str(raw),
         "--daily-dir", str(daily), "--output", str(tmp_path / "audit.json"),
         "--ledger-output", str(tmp_path / "ledger.json")],
    )
    audit_ingestion.main()
    audit = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))
    assert audit["new_today_missing_candidates"] == 0

    (tmp_path / "quality.json").write_text('{"totals": {}}', encoding="utf-8")
    (tmp_path / "formal.json").write_text(
        '{"suspected_missed_journals": 0}', encoding="utf-8"
    )
    (tmp_path / "source_health.json").write_text(
        json.dumps({"checked_at": DETECTED, "counts": {"unavailable": 0, "stale": 0},
                    "coverage_counts": {}}), encoding="utf-8"
    )
    gate = release_gate.run(Namespace(
        date=DATE, daily_dir=daily, ingestion_audit=tmp_path / "audit.json",
        formal_audit=tmp_path / "formal.json", quality_report=tmp_path / "quality.json",
        source_health=tmp_path / "source_health.json", max_historical_days=14,
    ))
    assert gate["ok"], gate["failures"]


def test_sjjj_article_route_is_real_but_list_and_spoof_routes_are_not() -> None:
    assert clean_cn_noise.is_article_url(f"{ROOT}1193.shtml")
    assert not clean_cn_noise.is_article_url(
        f"https://untrusted.example/path?next={ROOT}1193.shtml"
    )
    assert not clean_cn_noise.is_article_url("https://sjjj.magtech.com.cn/CN/online_first")
    assert not clean_cn_noise.is_article_url(f"{ROOT}1193.shtml#not-an-article")
