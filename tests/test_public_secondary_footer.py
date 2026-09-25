from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "render_site.py"


class PublicSecondaryFooterTests(unittest.TestCase):
    def test_secondary_footer_exposes_public_navigation_not_migration_labels(self) -> None:
        source = RENDERER.read_text(encoding="utf-8")

        self.assertNotIn('href="{BASE}/classic/">旧版</a>', source)
        self.assertNotIn('href="{BASE}/daily-vnext/">Daily vNext</a>', source)
        self.assertIn('rel="alternate" type="application/rss+xml" title="Econ Papers Daily RSS" href="{BASE}/feed.xml"', source)
        self.assertNotIn('href="{BASE}/feed.xml">订阅 RSS</a>', source)
        self.assertNotIn('>RSS</a>', source)


if __name__ == "__main__":
    unittest.main()
