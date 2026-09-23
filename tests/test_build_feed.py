from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_feed  # noqa: E402


def test_feed_branding_changes_without_item_identity_drift(monkeypatch, tmp_path):
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    record = {
        "id": "doi:10.1234/example",
        "title": "Example paper",
        "url": "https://example.test/paper",
        "journal": "Example Journal",
        "published_online": "2026-09-23",
    }
    (daily_dir / "2026-09-23.json").write_text(json.dumps([record]), encoding="utf-8")
    output = tmp_path / "feed.xml"
    site_url = "https://academic-door.github.io/econ-paper-monitor/"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_feed.py",
            "--daily-dir",
            str(daily_dir),
            "--output",
            str(output),
            "--site-url",
            site_url,
        ],
    )

    build_feed.main()

    root = ET.parse(output).getroot()
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("title") == "Econ Papers Daily / 每日之门"
    assert channel.findtext("description") == "Econ Papers Daily / 每日之门：追踪重点经济学期刊和工作论文来源的最新论文。"
    assert channel.findtext("link") == site_url
    assert "经济学论文雷达" not in (channel.findtext("title") or "")
    assert "经济学论文雷达" not in (channel.findtext("description") or "")

    item = channel.find("item")
    assert item is not None
    assert item.findtext("link") == record["url"]
    assert item.findtext("guid") == record["id"]
    assert item.find("guid").attrib == {"isPermaLink": "false"}
