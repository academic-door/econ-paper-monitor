"""Keep real research titles with platform terminology in Chinese official sources."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_cn_journals  # noqa: E402


RURAL_PAPER = "平台经济发展助推实数深度融合的机制与效应研究"


def test_official_source_preserves_real_platform_economy_research_title():
    journal = {
        "id": "journal-f69300dae2",
        "title": "中国农村经济",
        "short_name": "中国农村经济",
        "fields": ["chinese"],
    }
    assert not fetch_cn_journals.is_noise_title(RURAL_PAPER)
    record = fetch_cn_journals.make_record(
        journal,
        RURAL_PAPER,
        "https://zgncjj.ajcass.com/Article/real-research",
        authors=["屠西伟", "郭朝先"],
        published_online="2026-09-20",
        date_source="official",
    )
    assert record is not None
    assert record["title"] == RURAL_PAPER
    assert record["journal"] == "中国农村经济"
    assert record["published_online"] == "2026-09-20"
    assert record["source"] == "cn-official"


@pytest.mark.parametrize(
    "title",
    [
        "投稿平台使用说明",
        "期刊征文通知",
        "会议新闻公告",
        "欢迎订阅本刊",
        "数据库使用说明",
    ],
)
def test_official_source_still_rejects_real_notice_noise(title):
    assert fetch_cn_journals.is_noise_title(title)
