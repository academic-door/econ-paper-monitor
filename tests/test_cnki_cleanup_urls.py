"""Regression: public CNKI article URLs must not be purged as navigation noise."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import clean_cn_noise  # noqa: E402


CNKI_ARTICLE_URL = (
    "https://kns.cnki.net/kcms2/article/abstract?"
    "v=9TisLvyceEOu_HoFD0nVcsIhqdWHREXjmyUEp6kE86By"
)


def test_cnki_first_party_article_detail_is_not_navigation():
    assert clean_cn_noise.is_article_url(CNKI_ARTICLE_URL)
    assert clean_cn_noise.is_article_url("http://kns.cnki.net/kcms2/article/abstract?v=valid")
    assert not clean_cn_noise.is_article_url(
        "https://kns.cnki.net/knavi/journals/SLJY/detail"
    )
    assert not clean_cn_noise.is_article_url(
        "https://example.org/?redirect=https://kns.cnki.net/kcms2/article/abstract"
    )
    assert not clean_cn_noise.is_article_url(
        "https://kns.cnki.net.evil.example/kcms2/article/abstract?v=fake"
    )


def test_legitimate_rss_article_keeps_seen_identity_and_daily_chronology(tmp_path):
    seen = tmp_path / "seen.json"
    daily = tmp_path / "2026-10-12.json"
    key = "url:7379790c62fd32a7"
    paper = {
        "id": key,
        "title": "因地制宜发展新质生产力的理论逻辑与路径范式",
        "journal": "数量经济技术经济研究",
        "journal_id": "journal-edcb877d78",
        "source": "cnki-rss",
        "source_type": "journal",
        "url": CNKI_ARTICLE_URL,
        "first_seen": "2026-10-12T10:11:16+00:00",
        "detected_at": "2026-10-12T10:11:16+00:00",
        "published_online": "2026-10-12",
        "date_source": "cnki_rss_pubdate",
    }
    seen.write_text(json.dumps({"papers": {key: paper}}, ensure_ascii=False), encoding="utf-8")
    daily.write_text(json.dumps([paper], ensure_ascii=False), encoding="utf-8")

    assert clean_cn_noise.clean_seen(seen) == 0
    assert clean_cn_noise.clean_daily(daily) == 0
    retained = json.loads(seen.read_text(encoding="utf-8"))["papers"][key]
    displayed = json.loads(daily.read_text(encoding="utf-8"))[0]
    assert retained["first_seen"] == "2026-10-12T10:11:16+00:00"
    assert displayed["detected_at"] == "2026-10-12T10:11:16+00:00"
    assert retained["published_online"] == "2026-10-12"


def test_same_cnki_article_route_is_valid_for_all_six_journals(tmp_path):
    for journal in sorted(clean_cn_noise.CN_JOURNAL_NAMES):
        path = tmp_path / "seen.json"
        path.write_text(
            json.dumps(
                {"papers": {"paper": {"title": "有效的中文学术论文题目",
                    "journal": journal, "url": CNKI_ARTICLE_URL}}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        assert clean_cn_noise.clean_seen(path) == 0
        assert "paper" in json.loads(path.read_text(encoding="utf-8"))["papers"]


def test_cnki_navigation_and_site_news_still_get_removed(tmp_path):
    seen = tmp_path / "seen.json"
    seen.write_text(
        json.dumps(
            {"papers": {
                "navigation": {"title": "期刊页面", "journal": "数量经济技术经济研究",
                    "url": "https://kns.cnki.net/knavi/journals/SLJY/detail"},
                "spoof": {"title": "有效的中文学术论文题目", "journal": "经济研究",
                    "url": "https://kns.cnki.net.evil.example/kcms2/article/abstract?v=bad"},
                "news": {"title": "征文通知", "journal": "管理世界",
                    "url": CNKI_ARTICLE_URL},
                "article": {"title": "真正的经济学论文", "journal": "世界经济",
                    "url": CNKI_ARTICLE_URL},
            }},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert clean_cn_noise.clean_seen(seen) == 3
    assert set(json.loads(seen.read_text(encoding="utf-8"))["papers"]) == {"article"}

def test_verified_rural_economics_platform_paper_is_not_filtered(tmp_path):
    path = tmp_path / "seen.json"
    daily = tmp_path / "2026-09-20.json"
    article = {
        "title": "平台经济发展助推实数深度融合的机制与效应研究",
        "journal": "中国农村经济",
        "journal_id": "journal-f69300dae2",
        "source": "cnki-rss",
        "url": CNKI_ARTICLE_URL,
        "published_online": "2026-09-20",
        "first_seen": "2026-10-12T10:11:16+00:00",
    }
    path.write_text(json.dumps({"papers": {"paper": article}}, ensure_ascii=False), encoding="utf-8")
    daily.write_text(json.dumps([article], ensure_ascii=False), encoding="utf-8")
    assert clean_cn_noise.clean_seen(path) == 0
    assert clean_cn_noise.clean_daily(daily) == 0

    # Actual site announcements retain the stricter noise filters.
    article["title"] = "平台公告"
    path.write_text(json.dumps({"papers": {"news": article}}, ensure_ascii=False), encoding="utf-8")
    assert clean_cn_noise.clean_seen(path) == 1
