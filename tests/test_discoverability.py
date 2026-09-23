from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_discoverability  # noqa: E402


def seed_route(docs_dir: Path, route: str) -> None:
    path = docs_dir / ("index.html" if not route else f"{route}index.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<html><title>{route or 'root'}</title></html>", encoding="utf-8")


def test_discoverability_outputs_only_public_routes(monkeypatch, tmp_path):
    docs = tmp_path / "docs"
    for route in [
        "",
        "daily-vnext/",
        "recent72/",
        "search/",
        "journals/",
        "working-papers/",
        "sources/working-papers/",
        "daily/2026-09-22/",
        "daily/2026-09-23/",
        "daily/2026-09-30/",
        "journals/example-journal/",
        "journals/example-journal/recent7/",
        "fields/labor/",
        "topics/china/",
        "topics/china/recent7/",
        "paper/example-paper-0123456789ab/",
        "archive/",
        "admin/",
        "quality/",
        "paper-data/00/",
    ]:
        seed_route(docs, route)
    (docs / "paper.html").write_text("legacy detail shell", encoding="utf-8")

    monkeypatch.setattr(build_discoverability, "today_str", lambda: "2026-09-23")
    site_url = "https://academic-door.github.io/econ-paper-monitor/"
    routes = build_discoverability.build(docs, site_url)

    assert "daily/2026-09-22/" in routes
    assert "daily/2026-09-23/" in routes
    assert "daily/2026-09-30/" not in routes
    assert "paper/example-paper-0123456789ab/" in routes
    assert "archive/" not in routes
    assert "admin/" not in routes
    assert "quality/" not in routes
    assert all(not route.startswith("paper-data/") for route in routes)

    ns = {"sm": build_discoverability.SITEMAP_NS}
    tree = ET.parse(docs / "sitemap.xml")
    urls = [node.text for node in tree.findall("sm:url/sm:loc", ns)]
    assert f"{site_url}search/" in urls
    assert f"{site_url}daily/2026-09-23/" in urls
    assert f"{site_url}paper/example-paper-0123456789ab/" in urls
    assert all("/admin/" not in url and "/quality/" not in url and "/paper-data/" not in url for url in urls)
    assert all("/archive/" not in url for url in urls)

    robots = (docs / "robots.txt").read_text(encoding="utf-8")
    assert f"Sitemap: {site_url}sitemap.xml" in robots
    assert "Disallow: /econ-paper-monitor/admin/" in robots
    assert "Disallow: /econ-paper-monitor/quality/" in robots
    assert "Disallow: /econ-paper-monitor/paper-data/" in robots

    not_found = (docs / "404.html").read_text(encoding="utf-8")
    assert f'href="{site_url}"' in not_found
    assert f'href="{site_url}search/"' in not_found
    assert f'href="{site_url}recent72/"' in not_found
    assert "publishedDaily.has(match[1])" in not_found
    assert "2026-09-23" in not_found
    assert "2026-09-30" not in not_found
