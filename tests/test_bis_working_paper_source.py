from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from fetch_preprints import allowed_url, load_sources, parse_feed


ROOT = Path(__file__).parents[1]


def bis_source() -> dict:
    sources = load_sources(ROOT / "data" / "working_paper_sources.yml")
    return next(source for source in sources if source.get("id") == "bis-working-papers")


def test_bis_working_paper_source_uses_current_official_feed_and_filters_other_research() -> None:
    source = bis_source()

    assert source["homepage"] == "https://www.bis.org/publications/working-paper"
    assert source["feed"] == "https://www.bis.org/doclist/bis_fsi_publs.rss"

    working_paper_url = (
        "https://www.bis.org/publications/working-paper-1376-"
        "what-determines-banks-excess-demand-reserves"
    )
    assert allowed_url(source, working_paper_url)
    assert not allowed_url(source, "https://www.bis.org/publications/fsi-insights-78")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>BIS Working Paper No 1376 What determines banks' excess demand for reserves?</title>
          <link>{working_paper_url}</link>
          <pubDate>Mon, 07 Sep 2026 00:00:00 GMT</pubDate>
        </item>
        <item>
          <title>FSI Insights No 78 Simple, resilient and proportional</title>
          <link>https://www.bis.org/publications/fsi-insights-78</link>
          <pubDate>Thu, 03 Sep 2026 00:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>"""

    records = parse_feed(xml, source)

    assert len(records) == 1
    assert records[0]["url"] == working_paper_url
    assert records[0]["paper_number"] == "1376"
    assert records[0]["published_online"] == "2026-09-07"
