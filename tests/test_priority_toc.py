from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_priority_toc  # noqa: E402


TARGET_COUNT = sum(len(targets) for targets in fetch_priority_toc.TARGETS.values())


class PriorityTocFallbackTests(unittest.TestCase):
    """Guard the Crossref fallback wiring in ``main``.

    The fallback exists because OUP / MIT Press / Econometric Society block CI
    runners. A NameError in the call site disabled it silently: every target
    reported ``0`` and the fallback never ran, so REStud, REStat, and
    Econometrica lost both acquisition paths at once.
    """

    def _run_main(self, fetch_target_mock):
        journals = [{"id": journal_id} for journal_id in fetch_priority_toc.TARGETS]
        timeouts: list[int] = []

        def fake_fallback(journal, target, *, timeout, max_items):
            timeouts.append(timeout)
            return []

        with tempfile.TemporaryDirectory() as tmp:
            argv = [
                "fetch_priority_toc.py",
                "--timeout",
                "7",
                "--output",
                str(Path(tmp) / "priority-toc.json"),
            ]
            with mock.patch.object(fetch_priority_toc, "load_journals", return_value=journals), \
                    mock.patch.object(fetch_priority_toc, "fetch_target", fetch_target_mock), \
                    mock.patch.object(fetch_priority_toc, "fetch_crossref_fallback", fake_fallback), \
                    mock.patch.object(fetch_priority_toc, "record_source") as record_source, \
                    mock.patch.object(sys, "argv", argv):
                fetch_priority_toc.main()

        return timeouts, record_source

    def test_fallback_runs_when_publisher_page_raises(self) -> None:
        timeouts, record_source = self._run_main(
            mock.Mock(side_effect=RuntimeError("blocked-captcha"))
        )

        self.assertEqual(timeouts, [7] * TARGET_COUNT)
        self.assertTrue(record_source.called)
        self.assertFalse(record_source.call_args.kwargs["ok"])
        self.assertIn("journals", record_source.call_args.kwargs["details"])

    def test_fallback_runs_when_publisher_page_returns_nothing(self) -> None:
        timeouts, _ = self._run_main(mock.Mock(return_value=[]))

        self.assertEqual(timeouts, [7] * TARGET_COUNT)


class PriorityTocTimeoutScopeTests(unittest.TestCase):
    """Every helper must use its own ``timeout`` parameter, not ``args``.

    Only ``main`` has an ``args`` namespace. A blanket find-replace of
    ``timeout=timeout`` into ``timeout=args.timeout`` turns every helper into a
    NameError at call time, which the CI-blocked publisher endpoints then hide
    behind a one-line status message.
    """

    def test_springer_online_first_targets_are_configured(self) -> None:
        expected = {
            "international-journal-of-game-theory",
            "economic-theory",
            "review-of-economic-design",
            "social-choice-and-welfare",
            "public-choice",
            "international-tax-and-public-finance",
            "journal-of-economic-growth",
            "journal-of-population-economics",
            "environmental-and-resource-economics",
        }
        self.assertTrue(expected.issubset(fetch_priority_toc.TARGETS))
        for journal_id in expected:
            self.assertEqual(
                fetch_priority_toc.TARGETS[journal_id][0]["kind"],
                "springer_online_first",
            )

    def test_no_helper_reads_the_args_namespace(self) -> None:
        source = Path(fetch_priority_toc.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        offenders = [
            (node.name, child.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name != "main"
            for child in ast.walk(node)
            if isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and child.value.id == "args"
        ]

        self.assertEqual(offenders, [])

    def test_fetch_toc_text_passes_its_own_timeout(self) -> None:
        response = mock.MagicMock()
        response.read.return_value = b"<html>ok</html>"
        response.headers.get_content_charset.return_value = "utf-8"
        response.__enter__.return_value = response

        with mock.patch.object(fetch_priority_toc.urllib.request, "urlopen", return_value=response) as urlopen:
            text = fetch_priority_toc.fetch_toc_text("https://example.invalid/toc", timeout=5)

        self.assertIn("ok", text)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 5)

    def test_http_200_challenge_page_uses_fallback(self) -> None:
        challenge = mock.MagicMock()
        challenge.read.return_value = b"<html><title>Just a moment...</title></html>"
        challenge.headers.get_content_charset.return_value = "utf-8"
        challenge.__enter__.return_value = challenge
        valid = mock.MagicMock()
        valid.read.return_value = b"<html><title>Forthcoming Papers</title></html>"
        valid.headers.get_content_charset.return_value = "utf-8"
        valid.__enter__.return_value = valid

        with mock.patch.object(
            fetch_priority_toc.urllib.request,
            "urlopen",
            side_effect=[challenge, valid],
        ) as urlopen:
            text = fetch_priority_toc.fetch_toc_text(
                "https://publisher.invalid/toc",
                timeout=5,
                fallback_urls=["https://mirror.invalid/toc"],
            )

        self.assertIn("Forthcoming Papers", text)
        self.assertEqual(urlopen.call_count, 2)

    def test_crossref_fallback_passes_its_own_timeout(self) -> None:
        response = mock.MagicMock()
        response.read.return_value = b'{"message": {"items": []}}'
        response.__enter__.return_value = response
        target = {"kind": "restud_advance", "fallback_issn": "0034-6527"}

        with mock.patch.object(fetch_priority_toc.urllib.request, "urlopen", return_value=response) as urlopen:
            records = fetch_priority_toc.fetch_crossref_fallback(
                {"id": "review-of-economic-studies"}, target, timeout=5, max_items=1
            )

        self.assertEqual(records, [])
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 5)

    def test_restud_official_homepage_extracts_new_article_links(self) -> None:
        html = """[New ### Macro Shocks and Firm Dynamics with Oligopolistic Financial Intermediaries 24 July 2026 Alessandro T. Villa](https://www.restud.com/macro-shocks-and-firm-dynamics-with-oligopolistic-financial-intermediaries/)"""
        links = fetch_priority_toc.article_links(html, "https://www.restud.com/")
        self.assertEqual(
            links,
            [
                (
                    "https://www.restud.com/macro-shocks-and-firm-dynamics-with-oligopolistic-financial-intermediaries/",
                    "Macro Shocks and Firm Dynamics with Oligopolistic Financial Intermediaries",
                )
            ],
        )

    def test_restud_detail_reads_published_time_and_author_from_jina_page(self) -> None:
        page = """Title: Macro Shocks and Firm Dynamics with Oligopolistic Financial Intermediaries\nPublished Time: 2026-07-24T16:07:56+00:00\nMarkdown Content:\n24 July 2026\n\nAlessandro T. Villa, Federal Reserve Bank of Chicago\n\nAbstract text."""
        with mock.patch.object(fetch_priority_toc, "fetch_toc_text", return_value=page):
            detail = fetch_priority_toc.enrich_detail("https://www.restud.com/example/", "Fallback", 5)
        self.assertEqual(detail["published_online"], "2026-07-24")
        self.assertEqual(detail["authors"], ["Alessandro T. Villa, Federal Reserve Bank of Chicago"])

    def test_restud_author_map_prefers_clean_card_authors(self) -> None:
        html = """<a href="/macro-shocks/"><p class="author-short">Alessandro T. Villa</p></a>"""
        self.assertEqual(
            fetch_priority_toc.restud_author_map(html, "https://www.restud.com/"),
            {"https://www.restud.com/macro-shocks": ["Alessandro T. Villa"]},
        )

    def test_restud_jina_page_extracts_abstract_paragraph(self) -> None:
        page = """Title: Example\nPublished Time: 2026-07-24T16:07:56+00:00\nMarkdown Content:\n24 July 2026\n\nAlessandro T. Villa\n\nMotivated by a secular increase in concentration, I develop a new macroeconomic model with heterogeneous firms and financial intermediaries. The model explains how market power affects investment and aggregate activity during crises."""
        self.assertIn("Motivated by a secular increase", fetch_priority_toc.restud_abstract_from_jina(page))

    def test_priority_journal_status_remains_usable_when_optional_page_is_blocked(self) -> None:
        source = Path(fetch_priority_toc.__file__).read_text(encoding="utf-8")
        self.assertIn('"ok": bool(state["count"])', source)

    def test_partial_harvest_is_not_reported_as_total_source_outage(self) -> None:
        source = Path(fetch_priority_toc.__file__).read_text(encoding="utf-8")
        self.assertIn("ok=failures == 0 or bool(records)", source)


class TandfLatestTargetTests(unittest.TestCase):
    def test_tandf_latest_targets_are_configured(self) -> None:
        expected = {
            "applied-economics": ("0003-6846", "raec20"),
            "journal-of-business-and-economic-statistics": ("0735-0015", "ubes20"),
        }
        for journal_id, (issn, code) in expected.items():
            self.assertIn(journal_id, fetch_priority_toc.TARGETS)
            target = fetch_priority_toc.TARGETS[journal_id][0]
            self.assertEqual(target["kind"], "tandf_latest_articles")
            self.assertEqual(target["fallback_issn"], issn)
            self.assertIn(code, target["url"])
            self.assertTrue(any(url.startswith("https://r.jina.ai/") for url in target["fallback_urls"]))

    def test_applied_economics_article_links_reject_other_tandf_dois(self) -> None:
        html = """
        <a href="/doi/full/10.1080/00036846.2026.1234567">A valid Applied Economics article</a>
        <a href="/doi/full/10.1080/07350015.2026.7654321">A JBES article on the same platform</a>
        <a href="/action/journalInformation?journalCode=raec20">Journal information</a>
        """
        links = fetch_priority_toc.article_links(
            html,
            "https://www.tandfonline.com/action/showAxaArticles?journalCode=raec20",
        )
        self.assertEqual(
            links,
            [(
                "https://www.tandfonline.com/doi/full/10.1080/00036846.2026.1234567",
                "A valid Applied Economics article",
            )],
        )

    def test_jbes_article_links_reject_other_tandf_dois(self) -> None:
        html = """
        <a href="/doi/full/10.1080/07350015.2026.7654321">A valid JBES article title</a>
        <a href="/doi/full/10.1080/00036846.2026.1234567">An Applied Economics article</a>
        """
        links = fetch_priority_toc.article_links(
            html,
            "https://www.tandfonline.com/toc/ubes20/0/ja",
        )
        self.assertEqual(
            links,
            [(
                "https://www.tandfonline.com/doi/full/10.1080/07350015.2026.7654321",
                "A valid JBES article title",
            )],
        )


class JaereJustAcceptedTargetTests(unittest.TestCase):
    def test_jaere_just_accepted_target_is_configured(self) -> None:
        journal_id = "journal-of-the-association-of-environmental-and-resource-economists"
        self.assertIn(journal_id, fetch_priority_toc.TARGETS)
        target = fetch_priority_toc.TARGETS[journal_id][0]
        self.assertEqual(target["kind"], "uchicago_just_accepted")
        self.assertEqual(target["fallback_issn"], "2333-5955")
        self.assertIn("/toc/jaere/0/ja", target["url"])
        self.assertTrue(any(url.startswith("https://r.jina.ai/") for url in target["fallback_urls"]))

    def test_jaere_links_accept_uchicago_doi_and_reject_navigation(self) -> None:
        html = """
        <a href="/doi/10.1086/742967">Do Prior Residents Benefit from Energy Booms?</a>
        <a href="/doi/10.1080/07350015.2026.7654321">An unrelated external DOI</a>
        <a href="/journals/jaere/about">About the Journal of Environmental and Resource Economists</a>
        """
        links = fetch_priority_toc.article_links(
            html,
            "https://www.journals.uchicago.edu/toc/jaere/0/ja",
        )
        self.assertEqual(
            links,
            [(
                "https://www.journals.uchicago.edu/doi/10.1086/742967",
                "Do Prior Residents Benefit from Energy Booms?",
            )],
        )


class LocalCnkiLogPathTests(unittest.TestCase):
    def test_status_log_path_is_repo_relative(self) -> None:
        import local_cnki_update

        value = local_cnki_update.log_path_for_status()

        self.assertFalse(Path(value).is_absolute())
        self.assertNotIn(":\\", value)
        self.assertTrue(value.endswith("local-cnki-update.log"))


class AdditionalEconometricSocietyTargetTests(unittest.TestCase):
    def test_forthcoming_targets_cover_configured_journals(self) -> None:
        self.assertIn("theoretical-economics", fetch_priority_toc.TARGETS)
        self.assertIn("quantitative-economics", fetch_priority_toc.TARGETS)
        self.assertEqual(fetch_priority_toc.TARGETS["theoretical-economics"][0]["fallback_issn"], "1933-6837")
        self.assertEqual(fetch_priority_toc.TARGETS["quantitative-economics"][0]["fallback_issn"], "1759-7323")

    def test_oup_advance_targets_cover_crossref_only_oup_journals(self) -> None:
        expected = {
            "quarterly-journal-of-economics",
            "economic-journal",
            "journal-of-the-european-economic-association",
            "journal-of-law-economics-and-organization",
            "review-of-financial-studies",
            "european-review-of-agricultural-economics",
        }
        self.assertTrue(expected.issubset(fetch_priority_toc.TARGETS))
        self.assertTrue(all(fetch_priority_toc.TARGETS[j][0]["kind"] == "oup_advance_articles" for j in expected))

    def test_links_are_filtered_by_journal_doi_prefix(self) -> None:
        html = '<a href="https://doi.org/10.3982/TE9999">A theoretical result</a><a href="https://doi.org/10.3982/QE9999">A quantitative result</a>'
        theoretical = fetch_priority_toc.article_links(
            html,
            "https://www.econometricsociety.org/publications/theoretical-economics/forthcoming-papers",
        )
        quantitative = fetch_priority_toc.article_links(
            html,
            "https://www.econometricsociety.org/publications/quantitative-economics/forthcoming-papers",
        )
        self.assertEqual(len(theoretical), 1)
        self.assertIn("TE9999", theoretical[0][0])
        self.assertEqual(len(quantitative), 1)
        self.assertIn("QE9999", quantitative[0][0])

    def test_econometric_society_cards_extract_primary_pdf_and_authors(self) -> None:
        html = """
        <div class="article" id="forthcoming_Social-Learning">
          <h3 class="article_title">The Social Learning Barrier</h3>
          <p>Brandl, Florian</p>
          <div class="article_actions">
            <a href="/publications/econometrica/forthcoming-papers/0000/00/00/Social-Learning/file/24769-2.pdf">View</a>
            <a href="/publications/econometrica/forthcoming-papers/0000/00/00/Social-Learning/supp/24769SUPP.pdf">Supplemental Appendix</a>
          </div>
        </div>
        """
        base = "https://www.econometricsociety.org/publications/econometrica/forthcoming-papers"
        links = fetch_priority_toc.article_links(html, base)
        authors = fetch_priority_toc.econometric_society_author_map(html, base)
        self.assertEqual(len(links), 1)
        self.assertIn("/file/24769-2.pdf", links[0][0])
        self.assertEqual(links[0][1], "The Social Learning Barrier")
        self.assertEqual(authors[links[0][0]], ["Brandl, Florian"])


class JhrTargetTests(unittest.TestCase):
    def test_jhr_targets_are_configured(self) -> None:
        self.assertIn("journal-of-human-resources", fetch_priority_toc.TARGETS)
        kinds = [target["kind"] for target in fetch_priority_toc.TARGETS["journal-of-human-resources"]]
        self.assertEqual(kinds, ["jhr_early_recent", "jhr_current"])
        self.assertEqual(
            fetch_priority_toc.TARGETS["journal-of-human-resources"][0]["fallback_issn"],
            "0022-166X",
        )

    def test_jhr_early_card_parses_title_authors_doi_date(self) -> None:
        html = """<li class="highwire-cite">
          <a href="/content/early/2026/08/07/jhr.0925-14532R2" class="highwire-cite-linked-title"><span class="highwire-cite-title">Monopsony, Efficiency, and the Regularization of Undocumented Immigrants</span></a>
          <div class="highwire-cite-authors"><span class="highwire-citation-authors"><span class="highwire-citation-author first" data-delta="0">George J. Borjas</span> and <span class="highwire-citation-author" data-delta="1">Anthony Edo</span></span></div>
          <div class="highwire-cite-metadata"><span class="highwire-cite-metadata-journal highwire-cite-metadata">Published online before print </span><span class="highwire-cite-metadata-date highwire-cite-metadata">August 11, 2026, </span><span class="highwire-cite-metadata-pages highwire-cite-metadata">jhr.0925-14532R2; </span><span class="highwire-cite-metadata-doi highwire-cite-metadata">DOI: https://doi.org/10.3368/jhr.0925-14532R2 </span></div>
        </li>"""
        blocks = fetch_priority_toc.jhr_article_blocks(
            html,
            "https://jhr.uwpress.org/content/early/recent",
        )
        self.assertEqual(len(blocks), 1)
        self.assertEqual(
            blocks[0]["title"],
            "Monopsony, Efficiency, and the Regularization of Undocumented Immigrants",
        )
        self.assertEqual(blocks[0]["authors"], ["George J. Borjas", "Anthony Edo"])
        self.assertEqual(blocks[0]["doi"], "10.3368/jhr.0925-14532r2")
        self.assertEqual(blocks[0]["published_online"], "2026-08-11")


if __name__ == "__main__":
    unittest.main()


class JinaHeaderTests(unittest.TestCase):
    """The JINA mirror only helps when the API key is attached in CI."""

    def test_jina_url_gets_bearer_when_key_set(self):
        with mock.patch.dict("os.environ", {"JINA_API_KEY": "secret-key"}, clear=False):
            headers = fetch_priority_toc.jina_headers("https://r.jina.ai/http://academic.oup.com/qje/advance-articles")
        self.assertEqual(headers.get("Authorization"), "Bearer secret-key")
        self.assertIn("text/markdown", headers.get("Accept", ""))

    def test_non_jina_url_has_no_auth_header(self):
        with mock.patch.dict("os.environ", {"JINA_API_KEY": "secret-key"}, clear=False):
            headers = fetch_priority_toc.jina_headers("https://academic.oup.com/qje/advance-articles")
        self.assertNotIn("Authorization", headers)

    def test_no_key_no_auth_header(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            headers = fetch_priority_toc.jina_headers("https://r.jina.ai/http://example.com")
        self.assertNotIn("Authorization", headers)
