from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import render_site  # noqa: E402


RENDERER = ROOT / "scripts" / "render_site.py"


def test_static_public_surfaces_use_site_first_discovery_wording() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")

    assert 'return f"本站首次发现 {first_seen_date(record)}' in renderer
    assert '"first_seen": "本站首次发现"' in renderer
    assert '"F": "F：仅本站首次发现"' in renderer
    assert '<div class="label">本站首次发现</div>' in renderer
    assert '本站首次发现时间' in renderer

    assert '"first_seen": "首次监测"' not in renderer
    assert '<div class="label">首次监测</div>' not in renderer



def test_archive_bucket_does_not_rewrite_public_first_discovery() -> None:
    record = {
        "_daily_date": "2026-09-18",
        "first_seen": "2026-09-19T07:22:46+00:00",
        "detected_at": "2026-09-19T07:22:46+00:00",
        "available_online": "2026-09-18",
        "published_online": "2026-09-18",
        "date_source": "rss_published",
        "date_confidence": "A",
    }

    # Archive routing stays source-date based.
    assert render_site.detected_date(record) == "2026-09-18"

    # Public first-discovery copy stays anchored to the canonical timestamp.
    assert render_site.first_seen_date(record) == "2026-09-19"
    assert render_site.first_seen_time(record) == "15:22"
    assert render_site.detected_label(record) == "本站首次发现 2026-09-19 15:22"
    assert render_site.detection_lag_days(record) == 1


def test_archive_header_distinguishes_bucket_date_from_first_discovery() -> None:
    renderer = RENDERER.read_text(encoding="utf-8")

    assert "归档日期：" in renderer
    assert "条目中的“本站首次发现”按实际首次发现时间展示" in renderer
    assert "本站首次发现日期：" not in renderer
    assert "按本站首次发现日期组织的每日记录" not in renderer
    assert "按每日归档日期组织记录" in renderer
