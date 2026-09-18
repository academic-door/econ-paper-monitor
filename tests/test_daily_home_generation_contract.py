from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_daily_vnext import build  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def page_info(path: Path) -> tuple[str, int, int, str]:
    html = path.read_text(encoding="utf-8")
    date = re.search(r"DAILY DOOR / (\d{4}-\d{2}-\d{2})", html)
    count = re.search(r'<div class="hero-total" data-count="(\d+)">', html)
    entries = len(re.findall(r'class="paper-entry"', html))
    flow = "今日研究时间流" if "今日研究时间流" in html else ""
    assert date, f"Daily date missing from {path}"
    assert count, f"Daily count missing from {path}"
    return date.group(1), int(count.group(1)), entries, flow


def test_root_and_vnext_share_the_new_daily_generation_contract(tmp_path):
    archives = sorted((ROOT / "data" / "daily").glob("*.json"), reverse=True)
    date_value = next(
        path.stem
        for path in archives
        if path.read_text(encoding="utf-8").strip() not in {"", "[]"}
    )
    template = ROOT / "scripts" / "templates" / "daily_vnext.html"
    root = tmp_path / "index.html"
    vnext = tmp_path / "daily-vnext" / "index.html"
    build(date_value, template, root, tmp_path / "root-report.json")
    build(date_value, template, vnext, tmp_path / "vnext-report.json")
    classic = ROOT / "docs" / "classic" / "index.html"

    root_html = root.read_text(encoding="utf-8")
    vnext_html = vnext.read_text(encoding="utf-8")
    classic_html = classic.read_text(encoding="utf-8")
    root_info = page_info(root)
    vnext_info = page_info(vnext)

    for path, html in ((root, root_html), (vnext, vnext_html)):
        assert "今日之门" in html, f"New Daily hero missing from {path}"
        assert "今日研究时间流" in html, f"New flow heading missing from {path}"
        assert 'class="sidebar"' not in html, f"Legacy sidebar leaked into {path}"
        title_links = re.findall(r"<h3><a href=\"([^\"]+)\"", html)
        assert title_links, f"No title links rendered in {path}"
        assert all("paper/" in link and link.endswith("/") and "paper.html?key=" not in link for link in title_links), (
            f"Title links must point to canonical static detail routes in {path}"
        )
        read_links = re.findall(r'class="read-link" href="([^"]+)"', html)
        assert read_links, f"No read links rendered in {path}"
        assert all(not link.startswith("paper.html") for link in read_links), (
            f"Read links must keep pointing at the original source in {path}"
        )

    assert 'class="sidebar"' in classic_html, "Classic page no longer preserves the legacy sidebar"
    assert root_info[:3] == vnext_info[:3], "Root and vNext Daily date/counts diverged"
    assert root_info[1] == root_info[2], "Root hero count differs from rendered entries"
    assert vnext_info[1] == vnext_info[2], "vNext hero count differs from rendered entries"
