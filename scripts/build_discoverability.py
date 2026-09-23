"""Build bounded public discoverability files for the generated static site."""

from __future__ import annotations

import argparse
import html
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from common import DOCS_DIR, today_str, write_text


DEFAULT_SITE_URL = "https://academic-door.github.io/econ-paper-monitor/"
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

FIXED_PUBLIC_ROUTES = (
    "",
    "daily-vnext/",
    "recent72/",
    "search/",
    "journals/",
    "working-papers/",
    "working-papers/today/",
    "working-papers/recent7/",
    "working-papers/china/",
    "sources/working-papers/",
)

PUBLIC_ROUTE_PATTERNS = (
    "daily/*/index.html",
    "journals/*/index.html",
    "journals/*/recent7/index.html",
    "fields/*/index.html",
    "topics/*/index.html",
    "topics/*/recent7/index.html",
    "paper/*/index.html",
)


def normalized_site_url(site_url: str) -> str:
    value = site_url.strip()
    if not value:
        raise ValueError("site URL is required")
    return value if value.endswith("/") else value + "/"


def route_index_path(docs_dir: Path, route: str) -> Path:
    return docs_dir / ("index.html" if not route else f"{route}index.html")


def route_from_index(docs_dir: Path, path: Path) -> str:
    relative = path.relative_to(docs_dir).as_posix()
    if relative == "index.html":
        return ""
    if not relative.endswith("/index.html"):
        raise ValueError(f"not an index route: {relative}")
    return relative[: -len("index.html")]


def is_publishable_daily_route(route: str, current_day: str) -> bool:
    match = re.fullmatch(r"daily/(\d{4}-\d{2}-\d{2})/", route)
    if not match:
        return True
    return match.group(1) <= current_day


def collect_public_routes(docs_dir: Path, *, current_day: str | None = None) -> list[str]:
    current_day = current_day or today_str()
    routes: set[str] = set()

    for route in FIXED_PUBLIC_ROUTES:
        if route_index_path(docs_dir, route).is_file():
            routes.add(route)

    for pattern in PUBLIC_ROUTE_PATTERNS:
        for path in docs_dir.glob(pattern):
            if not path.is_file():
                continue
            route = route_from_index(docs_dir, path)
            if is_publishable_daily_route(route, current_day):
                routes.add(route)

    return sorted(routes)


def write_sitemap(docs_dir: Path, site_url: str, routes: list[str]) -> None:
    ET.register_namespace("", SITEMAP_NS)
    root = ET.Element(f"{{{SITEMAP_NS}}}urlset")
    for route in routes:
        url = ET.SubElement(root, f"{{{SITEMAP_NS}}}url")
        loc = ET.SubElement(url, f"{{{SITEMAP_NS}}}loc")
        loc.text = urljoin(site_url, route)
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8") + "\n"
    write_text(docs_dir / "sitemap.xml", payload)


def write_robots(docs_dir: Path, site_url: str) -> None:
    project_path = "/" + urlsplit(site_url).path.strip("/") + "/"
    payload = "\n".join(
        [
            "User-agent: *",
            f"Allow: {project_path}",
            f"Disallow: {project_path}admin/",
            f"Disallow: {project_path}quality/",
            f"Disallow: {project_path}paper-data/",
            f"Sitemap: {urljoin(site_url, 'sitemap.xml')}",
            "",
        ]
    )
    write_text(docs_dir / "robots.txt", payload)


def write_404(docs_dir: Path, site_url: str, routes: list[str]) -> None:
    daily_dates = sorted(
        route.split("/")[1]
        for route in routes
        if re.fullmatch(r"daily/\d{4}-\d{2}-\d{2}/", route)
    )
    site_json = json.dumps(site_url, ensure_ascii=False)
    dates_json = json.dumps(daily_dates, ensure_ascii=False)
    site_html = html.escape(site_url, quote=True)
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,follow">
  <title>页面未找到 · Econ Papers Daily / 每日之门</title>
  <style>
    body{{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:720px;margin:10vh auto;padding:0 24px;line-height:1.65;color:#171717}}
    h1{{font-size:2rem;line-height:1.2}} nav{{display:flex;gap:14px;flex-wrap:wrap;margin-top:24px}} a{{color:inherit;text-underline-offset:3px}}
  </style>
</head>
<body>
  <main>
    <p>Econ Papers Daily / 每日之门</p>
    <h1>这个页面不存在</h1>
    <p>链接可能已经更新。你可以回到今天、全站检索或最近 72 小时继续浏览。</p>
    <nav aria-label="404 导航">
      <a href="{site_html}">Today</a>
      <a href="{site_html}search/">Search</a>
      <a href="{site_html}recent72/">Recent72</a>
    </nav>
  </main>
  <script>
    (() => {{
      const siteRoot = {site_json};
      const publishedDaily = new Set({dates_json});
      const match = window.location.pathname.match(/\/archive\/(\d{{4}}-\d{{2}}-\d{{2}})\/?$/);
      if (match && publishedDaily.has(match[1])) {{
        window.location.replace(new URL("daily/" + match[1] + "/", siteRoot).href);
      }}
    }})();
  </script>
</body>
</html>
"""
    write_text(docs_dir / "404.html", page)


def build(docs_dir: Path, site_url: str) -> list[str]:
    site_url = normalized_site_url(site_url)
    routes = collect_public_routes(docs_dir)
    write_sitemap(docs_dir, site_url, routes)
    write_robots(docs_dir, site_url)
    write_404(docs_dir, site_url, routes)
    return routes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    args = parser.parse_args()

    routes = build(args.docs_dir.resolve(), args.site_url)
    print(f"discoverability routes={len(routes)} sitemap={args.docs_dir / 'sitemap.xml'}")


if __name__ == "__main__":
    main()
