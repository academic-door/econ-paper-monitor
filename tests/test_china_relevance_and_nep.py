from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_ingestion  # noqa: E402
import dedupe  # noqa: E402
import enrich_china_relevance  # noqa: E402
import render_site  # noqa: E402


class ChinaRelevanceAndNepTests(unittest.TestCase):
    def test_nep_paper_number_is_scoped_to_issue(self) -> None:
        first = {
            "source_id": "repec-nep-mac",
            "paper_number": "p8",
            "source_url": "https://nep.repec.org/nep-mac/2026-06-15",
            "url": "https://nep.repec.org/nep-mac/2026-06-15#p8",
        }
        second = {
            "source_id": "repec-nep-mac",
            "paper_number": "p8",
            "source_url": "https://nep.repec.org/nep-mac/2026-06-22",
            "url": "https://nep.repec.org/nep-mac/2026-06-22#p8",
        }
        self.assertFalse(dedupe.record_match_keys(first) & dedupe.record_match_keys(second))

    def test_nep_article_and_anchor_match_within_same_issue(self) -> None:
        anchor = {
            "source_id": "repec-nep-mac",
            "paper_number": "p8",
            "source_url": "https://nep.repec.org/nep-mac/2026-06-15",
            "url": "https://nep.repec.org/nep-mac/2026-06-15#p8",
        }
        article = {
            "source_id": "repec-nep-mac",
            "paper_number": "p8",
            "source_url": "https://nep.repec.org/nep-mac/2026-06-15",
            "url": "https://econpapers.repec.org/RePEc:ris:kiepwe:022515",
        }
        self.assertTrue(dedupe.record_match_keys(anchor) & dedupe.record_match_keys(article))

    def test_author_name_and_source_label_are_not_direct_china_evidence(self) -> None:
        record = {
            "title": "A study of banking risk",
            "authors": ["China Smith"],
            "source_issue": "RePEc NEP China",
        }
        self.assertFalse(enrich_china_relevance.has_explicit_china_signal(record))

    def test_single_incidental_china_mention_is_only_candidate(self) -> None:
        record = {
            "title": "Global inflation spillovers",
            "abstract": "All EU regions are exposed to shocks from non-EU countries, namely Russia and China.",
        }
        self.assertEqual(enrich_china_relevance.classify(record)[0], "candidate")

    def test_direct_china_application_is_confirmed(self) -> None:
        record = {
            "title": "Public Pollution Information and Private Health Insurance Purchase",
            "abstract": "We study the staggered rollout of China's real-time air pollution monitoring program.",
        }
        self.assertEqual(enrich_china_relevance.classify(record)[0], "confirmed")

    def test_deepseek_shock_is_confirmed_without_literal_china(self) -> None:
        record = {
            "title": "Low-Cost AI and the Value of Compute Scarcity: Evidence from the DeepSeek Shock",
        }
        self.assertEqual(enrich_china_relevance.classify(record)[0], "confirmed")

    def test_detail_key_uses_the_same_shard_prefix_as_paper_page(self) -> None:
        record = {
            "title": "Scarred by nature",
            "doi": "10.1234/example",
        }
        key = render_site.detail_key(record)
        self.assertEqual(key[-12:-10], key[-12:][:2])
        self.assertIn(
            "cache: 'no-cache'",
            render_site.paper_detail_body(),
        )

    def test_renminbi_is_confirmed_as_direct_china_evidence(self) -> None:
        record = {
            "abstract": "We study how the renminbi can become an international reserve currency.",
        }
        self.assertEqual(enrich_china_relevance.classify(record)[0], "confirmed")

    def test_chinese_sounding_authors_alone_do_not_confirm_currency_paper(self) -> None:
        record = {
            "title": "A two-pronged approach to currency internationalization",
            "authors": ["Qing Liu", "Wenlan Luo", "Jiatong Niu"],
        }
        self.assertNotEqual(enrich_china_relevance.classify(record)[0], "confirmed")

    def test_ai_exclusion_remains_stable_during_rule_refresh(self) -> None:
        record = {
            "title": "A study of the United States",
            "china_related": False,
            "china_related_source": "ai",
            "china_relevance_reason": "研究对象为美国，不涉及中国",
        }
        updates, status = enrich_china_relevance.classification_updates(record)
        self.assertEqual(status, "none")
        self.assertFalse(updates["china_related"])
        self.assertEqual(updates["china_related_source"], "ai")

    def test_detail_page_separates_acceptance_from_online_date(self) -> None:
        """The acceptance-date caveat must stay on the paper detail page.

        This used to be asserted against ``render_site.paper_detail_body``.
        Detail rendering is emitted by the secondary-page renderer, so the
        guard follows that production source rather than generated output.
        """
        template = render_site.paper_detail_body()

        self.assertIn("日期信息", template)
        self.assertIn("接受日期", template)
        self.assertIn("不等同于正式上线", template)
        self.assertLess(template.index("日期信息"), template.index("${accepted}"))

    def test_editorial_board_is_suppressed_from_ingestion_audit(self) -> None:
        self.assertTrue(dedupe.is_source_navigation_noise({"title": "Editorial Board"}))

    def test_detail_key_is_stable_and_uses_doi_identity(self) -> None:
        record = {"title": "DP515 Should Rules be Simple?", "doi": "https://doi.org/10.1007/bf00373063"}
        self.assertEqual(render_site.detail_key(record), "dp515-should-rules-be-simple-003f08625c04")

    def test_detail_key_prefers_canonical_data_contract(self) -> None:
        record = {
            "title": "Changed title",
            "doi": "10.1234/changed",
            "detail_key": "canonical-paper-0123456789ab",
        }
        self.assertEqual(render_site.detail_key(record), record["detail_key"])

    def test_detail_key_falls_back_to_url_identity(self) -> None:
        record = {"title": "The Price of Borrowing for College", "url": "https://www.iza.org/publications/dp/18768/the-price-of-borrowing-for-college-student-loan-interest-rates-education"}
        self.assertEqual(render_site.detail_key(record), "the-price-of-borrowing-for-college-ed6159e3fc4e")


if __name__ == "__main__":
    unittest.main()
