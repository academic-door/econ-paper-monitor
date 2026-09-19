from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_daily_vnext import first_seen, paper_markup  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def sample_record() -> dict:
    return {
        "id": "doi:10.1111/obes.70128",
        "title": "Does Financial Inclusion Mitigate Social Exclusion?",
        "journal": "Oxford Bulletin of Economics and Statistics",
        "source_type": "journal",
        "url": "https://doi.org/10.1111/obes.70128",
        "doi": "10.1111/obes.70128",
        "official_date": "2026-09-15",
        "date_source": "rss_published",
        "date_confidence": "A",
        "first_seen_at": "2026-09-19T08:42:32+08:00",
    }


def test_public_label_preserves_canonical_first_seen_value() -> None:
    record = sample_record()

    seen = first_seen(record)

    assert seen is not None
    assert seen.isoformat() == "2026-09-19T08:42:32+08:00"


def test_paper_details_label_site_first_discovery_not_generic_monitoring() -> None:
    markup, seen_date = paper_markup(sample_record(), "2026-09-19", None)

    assert seen_date == "2026-09-19"
    assert "本站首次发现：2026-09-19 08:42 北京时间" in markup
    assert "首次监测：" not in markup
    assert "来源日期 2026-09-15" in markup or "2026-09-15" in markup


def test_timeline_copy_names_site_first_discovery_semantics() -> None:
    builder = (ROOT / "scripts" / "build_daily_vnext.py").read_text(encoding="utf-8")

    assert "按本站首次发现时间" in builder
    assert "按首次监测时间" not in builder
