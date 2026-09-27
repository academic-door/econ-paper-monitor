from __future__ import annotations

import json
import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dedupe  # noqa: E402
import apply_overrides  # noqa: E402
import enrich_metadata  # noqa: E402
import fetch_preprints  # noqa: E402
import normalize_records  # noqa: E402
import translate  # noqa: E402
from common import clean_abstract_text  # noqa: E402


class MetadataCompletenessTests(unittest.TestCase):
    def test_jats_abstract_heading_is_removed(self) -> None:
        value = "<jats:title>ABSTRACT</jats:title><jats:p>This study examines rural labor supply using panel data and a fixed-effects design.</jats:p>"

        cleaned = clean_abstract_text(value)

        self.assertEqual(
            cleaned,
            "This study examines rural labor supply using panel data and a fixed-effects design.",
        )

    def test_plain_abstract_label_is_removed_without_harming_normal_prose(self) -> None:
        self.assertEqual(clean_abstract_text("ABSTRACT This paper studies a policy reform."), "This paper studies a policy reform.")
        self.assertEqual(clean_abstract_text("Abstract: We estimate the causal effect."), "We estimate the causal effect.")
        self.assertEqual(clean_abstract_text("Abstract concepts shape the model."), "Abstract concepts shape the model.")

    def test_normalize_record_cleans_existing_abstract_markup(self) -> None:
        record = {"abstract": "<jats:title>ABSTRACT</jats:title><jats:p>This study reports the main result.</jats:p>"}

        self.assertTrue(normalize_records.normalize_record(record))
        self.assertEqual(record["abstract"], "This study reports the main result.")

    def test_manual_override_parser_preserves_author_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overrides.yml"
            path.write_text(
                'records:\n  "10.1234/test":\n    authors:\n      - "First Author"\n      - "Second Author"\n',
                encoding="utf-8",
            )

            records = apply_overrides.load_overrides(path)

            self.assertEqual(records["10.1234/test"]["authors"], ["First Author", "Second Author"])

    def test_manual_china_override_is_persistently_confirmed(self) -> None:
        record = {"china_relevance_status": "none", "china_related": None}

        changed = apply_overrides.apply_to_record(
            record,
            {
                "china_related": True,
                "china_reason": "研究对象为中国司法改革",
                "title_zh": "诉诸利维坦：跨区域司法管辖改革对企业投资的影响",
            },
        )

        self.assertTrue(changed)
        self.assertTrue(record["china_related"])
        self.assertEqual(record["china_related_source"], "manual")
        self.assertEqual(record["china_relevance_status"], "confirmed")

    def test_publisher_meta_extracts_all_authors(self) -> None:
        html = """
        <meta name="citation_author" content="First Author">
        <meta name="citation_author" content="Second Author">
        """

        metadata = enrich_metadata.extract_page_metadata(html)

        self.assertEqual(metadata["authors"], ["First Author", "Second Author"])

    @patch.object(enrich_metadata, "fetch_json")
    def test_crossref_metadata_includes_authors(self, fetch_json_mock) -> None:
        fetch_json_mock.return_value = {
            "message": {
                "author": [
                    {"given": "Jose", "family": "Apesteguia"},
                    {"given": "Miguel A.", "family": "Ballester"},
                ]
            }
        }

        metadata = enrich_metadata.crossref_doi_metadata("10.1257/mic.20240239", timeout=1)

        self.assertEqual(metadata["authors"], ["Jose Apesteguia", "Miguel A. Ballester"])

    @patch.object(enrich_metadata, "fetch_json_retry")
    def test_semantic_scholar_metadata_includes_abstract(self, fetch_retry_mock) -> None:
        fetch_retry_mock.return_value = {
            "abstract": "A sufficiently detailed public abstract for a newly deposited paper that provides enough context to pass metadata quality checks.",
            "authors": [{"name": "First Author"}],
            "publicationDate": "2026-07-16",
        }

        metadata = enrich_metadata.semantic_scholar_doi_metadata("10.1234/test", timeout=1)

        self.assertEqual(metadata["authors"], ["First Author"])
        self.assertIn("newly deposited paper", metadata["abstract"])
        self.assertEqual(metadata["abstract_source"], "semantic_scholar")

    def test_abstract_priority_prefers_new_missing_abstracts(self) -> None:
        newest_missing = {"detected_at": "2026-07-16T12:00:00+00:00", "abstract": ""}
        older_missing = {"detected_at": "2026-07-15T12:00:00+00:00", "abstract": ""}
        newest_present = {"detected_at": "2026-07-16T13:00:00+00:00", "abstract": "Already available"}

        ordered = sorted(
            [newest_present, older_missing, newest_missing],
            key=enrich_metadata.abstract_enrich_priority,
        )

        self.assertIs(ordered[0], newest_missing)
        self.assertIs(ordered[1], older_missing)

    @patch.object(enrich_metadata, "crossref_doi_metadata", return_value={"authors": ["Recovered Author"]})
    def test_author_only_enrichment_stops_after_crossref(self, _crossref_mock) -> None:
        record = {"doi": "10.1257/test", "authors": []}

        changed, status = enrich_metadata.enrich_author_record(record, timeout=1)

        self.assertTrue(changed)
        self.assertEqual(status, "authors-updated:crossref-doi")
        self.assertEqual(record["authors"], ["Recovered Author"])

    @patch.object(enrich_metadata, "fetch_json")
    def test_crossref_title_fallback_recovers_authors_and_doi(self, fetch_json_mock) -> None:
        fetch_json_mock.return_value = {
            "message": {
                "items": [{
                    "title": ["A distinctive working paper title"],
                    "DOI": "10.1234/example.1",
                    "author": [{"given": "First", "family": "Author"}],
                }]
            }
        }

        metadata = enrich_metadata.crossref_title_metadata("A distinctive working paper title", timeout=1)

        self.assertEqual(metadata["authors"], ["First Author"])
        self.assertEqual(metadata["doi"], "10.1234/example.1")

    def test_author_only_enrichment_marks_unresolved_source(self) -> None:
        record = {"source_id": "voxeu-cepr-columns", "url": "https://cepr.org/voxeu/columns/example", "authors": []}

        with patch.object(enrich_metadata, "fetch_text_and_url", side_effect=RuntimeError("blocked")):
            changed, status = enrich_metadata.enrich_author_record(record, timeout=1)

        self.assertTrue(changed)
        self.assertEqual(status, "authors-status-marked")
        self.assertEqual(record["authors_status"], "作者信息待核验")

    def test_rss_creator_is_preserved_as_author(self) -> None:
        xml = """<?xml version="1.0"?>
        <rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
          <channel><item>
            <title>A policy column with a sufficiently descriptive title</title>
            <link>https://cepr.org/voxeu/columns/example</link>
            <dc:creator>First Author</dc:creator>
            <dc:creator>Second Author</dc:creator>
          </item></channel>
        </rss>"""
        source = {
            "id": "voxeu-cepr-columns",
            "title": "VoxEU / CEPR Columns",
            "feed": "https://cepr.org/rss/vox-content",
            "homepage": "https://cepr.org/voxeu",
        }

        records = fetch_preprints.parse_feed(xml, source)

        self.assertEqual(records[0]["authors"], ["First Author", "Second Author"])

    def test_submission_instructions_are_not_papers(self) -> None:
        record = {"title": "Submission of Manuscripts to the Econometric Society Monograph Series"}
        self.assertTrue(dedupe.is_source_navigation_noise(record))

    def test_known_wiley_author_only_rss_item_is_not_a_paper(self) -> None:
        record = {
            "title": "Jay R. Ritter",
            "url": "https://onlinelibrary.wiley.com/doi/10.1111/jofi.70063?af=R",
        }
        self.assertTrue(dedupe.is_source_navigation_noise(record))

    def test_empty_seen_placeholder_is_not_a_paper(self) -> None:
        self.assertTrue(dedupe.is_source_navigation_noise({"journal": "管理世界"}))

    def test_seen_aliases_with_same_doi_are_collapsed(self) -> None:
        papers = {
            "doi:10.1016/test": {
                "title": "A sufficiently specific research paper title",
                "doi": "10.1016/test",
                "authors": ["First Author"],
                "first_seen": "2026-07-16T02:00:00+00:00",
            },
            "url:alias": {
                "title": "A sufficiently specific research paper title",
                "doi": "10.1016/test",
                "abstract": "A public abstract.",
                "first_seen": "2026-07-16T01:00:00+00:00",
            },
        }

        removed = dedupe.collapse_seen_duplicates(papers)

        self.assertEqual(removed, 1)
        self.assertEqual(len(papers), 1)
        record = next(iter(papers.values()))
        self.assertEqual(record["authors"], ["First Author"])
        self.assertEqual(record["abstract"], "A public abstract.")
        self.assertEqual(record["first_seen"], "2026-07-16T01:00:00+00:00")

    def test_url_contains_is_an_allowlist(self) -> None:
        source = {"homepage": "https://cepr.org/voxeu", "url_contains": ["/voxeu/"]}
        self.assertTrue(fetch_preprints.allowed_url(source, "https://cepr.org/voxeu/columns/example"))
        self.assertFalse(fetch_preprints.allowed_url(source, "https://cepr.org/multimedia/example"))

    def test_wiley_url_recovers_doi(self) -> None:
        record = {"url": "https://onlinelibrary.wiley.com/doi/10.1111/jmcb.13274?af=R"}
        self.assertTrue(normalize_records.normalize_record(record))
        self.assertEqual(record["doi"], "10.1111/jmcb.13274")

    def test_corrections_are_not_research_papers(self) -> None:
        self.assertTrue(dedupe.is_source_navigation_noise({"title": "Correction to: A Published Paper"}))

[First Author](https://example.com/first), Second Author, and Third Author

**Abstract:**

This is a sufficiently long public abstract for a Federal Reserve working paper and should be retained by the metadata fallback.

**Keywords:** Testing

**DOI**: https://doi.org/10.17016/FEDS.2026.999
"""
        record = {"url": "https://www.federalreserve.gov/econres/feds/example.htm"}

        fetch_preprints.enrich_record_from_proxy(record, "fed-feds", timeout=1)

        self.assertEqual(record["authors"], ["First Author", "Second Author", "Third Author"])
        self.assertEqual(record["doi"], "10.17016/FEDS.2026.999")
        self.assertIn("sufficiently long public abstract", record["abstract"])

**Authors**

[First Author](https://cepr.org/about/people/first-author), [Second Author](https://cepr.org/about/people/second-author)

**Abstract**

This is a sufficiently long CEPR abstract describing the paper, its data, identification strategy, and main findings for a public metadata page.

**Keywords**

Energy transition

[Advisory Board](https://cepr.org/about/people/advisory-board)
"""
        authors, abstract = fetch_preprints.parse_cepr_proxy_markdown(markdown)

        self.assertEqual(authors, ["First Author", "Second Author"])
        self.assertIn("sufficiently long CEPR abstract", abstract or "")

    def test_seen_only_abstract_can_be_translated(self, _translate_mock) -> None:
        records = [
            {
                "title": "A seen-only paper",
                "title_zh": "一篇仅存在于已监测记录中的论文",
                "abstract": "This public abstract is available in English and should be translated for the paper detail page.",
                "first_seen": "2026-07-16T01:00:00+00:00",
                "doi": "10.1257/test",
            }
        ]
        args = argparse.Namespace(sleep=0, timeout=1, stop_on_error=False, dry_run=False)

        result = translate.translate_records(
            records,
            args,
            "test-key",
            "https://example.com",
            "test-model",
            {},
            title_limit=1,
            abstract_limit=1,
            deadline=None,
        )

        self.assertEqual(result[1], 1)
        self.assertEqual(records[0]["abstract_zh"], "这是最近仅存在于已监测记录中的论文摘要翻译。")

    @patch.object(enrich_metadata, "record_publisher_group")
    @patch.object(enrich_metadata, "record_source")
    @patch.object(enrich_metadata, "openalex_doi_metadata", return_value={})
    @patch.object(enrich_metadata, "crossref_doi_metadata", return_value={"authors": ["Recovered Author"]})
    def test_recent_seen_only_record_is_enriched(
        self,
        _crossref_mock,
        _openalex_mock,
        _record_source_mock,
        _publisher_group_mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily = root / "daily"
            daily.mkdir()
            (daily / "2026-07-16.json").write_text("[]", encoding="utf-8")
            seen = root / "seen.json"
            seen.write_text(
                json.dumps(
                    {
                        "papers": {
                            "doi:10.1257/test": {
                                "title": "A recent seen-only paper",
                                "doi": "10.1257/test",
                                "url": "https://doi.org/10.1257/test",
                                "first_seen": "2026-07-16T01:00:00+00:00",
                                "authors": [],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            argv = [
                "enrich_metadata.py",
                "--daily-dir",
                str(daily),
                "--seen",
                str(seen),
                "--date",
                "2026-07-16",
                "--latest-days",
                "1",
                "--limit",
                "1",
                "--abstract-only",
            ]

            with patch.object(sys, "argv", argv):
                enrich_metadata.main()

            payload = json.loads(seen.read_text(encoding="utf-8"))
            self.assertEqual(payload["papers"]["doi:10.1257/test"]["authors"], ["Recovered Author"])


if __name__ == "__main__":
    unittest.main()
