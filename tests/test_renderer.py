"""Ticket #10: Markdown/HTML are pure renderings of one validated report."""

import json
import inspect
import re
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents.cli import main
from hoya_market_agents.report_contract import validate_market_report
from hoya_market_agents.report_fixtures import load_fixture
from hoya_market_agents.report_renderer import (
    render_market_html,
    render_market_markdown,
)
import hoya_market_agents.report_renderer as report_renderer_module
from hoya_market_agents.seats import SEAT_IDS


def _validated(case="consensus-6-1"):
    fixture = load_fixture(case)
    return validate_market_report(fixture["report"], fixture["sources"])


class FieldParityTests(unittest.TestCase):
    def setUp(self):
        self.report = _validated()
        self.markdown = render_market_markdown(self.report)
        self.html = render_market_html(self.report)

    def test_required_fields_appear_in_both_renderings(self):
        expected = [
            self.report["market_status"],
            self.report["period"]["label"],
            self.report["confidence"]["text"],
            self.report["confidence"]["icon"],
            self.report["judgement"],
        ]
        expected += self.report["limitations"]
        expected += self.report["invalidation_conditions"]
        for value in expected:
            with self.subTest(value=value):
                self.assertIn(value, self.markdown)
                self.assertIn(value, self.html)

    def test_full_tally_appears_in_both_renderings(self):
        for stance, count in self.report["tally"].items():
            entry = "{}：{}".format(stance, count)
            self.assertIn(entry, self.markdown)
            self.assertIn(entry, self.html)

    def test_all_seven_seat_rows_appear_in_both_renderings(self):
        for row in self.report["seats"]:
            self.assertIn(row["seat_id"], self.markdown)
            self.assertIn(row["seat_id"], self.html)
            self.assertIn(row["public_reason"], self.markdown)
            self.assertIn(row["public_reason"], self.html)
        self.assertEqual(len(SEAT_IDS), len(self.report["seats"]))

    def test_every_evidence_id_and_url_appears_in_both_renderings(self):
        for card in self.report["evidence"]:
            self.assertIn(card["evidence_id"], self.markdown)
            self.assertIn(card["evidence_id"], self.html)
            self.assertIn(card["url"], self.markdown)
            self.assertIn(card["url"], self.html)

    def test_renderers_are_pure_functions_of_one_report(self):
        self.assertEqual(render_market_markdown(self.report), self.markdown)
        self.assertEqual(render_market_html(self.report), self.html)

    def test_no_consensus_rendering_states_the_absence_of_direction(self):
        report = _validated("no-consensus-3-3-1")
        for text in (render_market_markdown(report), render_market_html(report)):
            self.assertIn(report["consensus_status"], text)
            self.assertIn("無方向", text)


class HtmlSafetyTests(unittest.TestCase):
    def setUp(self):
        self.report = _validated()
        self.html = render_market_html(self.report)

    def test_html_is_offline_with_no_script_or_external_resource(self):
        lowered = self.html.lower()
        for forbidden in ("<script", "javascript:", "onclick=", "@import", "cdn", "fonts.googleapis"):
            self.assertNotIn(forbidden, lowered)
        self.assertNotIn("<link", lowered)
        self.assertNotIn('src="http', lowered)

    def test_only_evidence_source_links_leave_the_document(self):
        hrefs = re.findall(r'href="([^"]+)"', self.html)
        allowed = {card["url"] for card in self.report["evidence"]}
        for href in hrefs:
            self.assertIn(href, allowed)

    def test_styles_are_inline_in_a_single_file(self):
        self.assertIn("<style>", self.html)
        self.assertEqual(self.html.count("<style>"), 1)

    def test_print_css_is_present(self):
        self.assertIn("@media print", self.html)

    def test_semantic_headings_are_accessible(self):
        self.assertEqual(self.html.count("<h1"), 1)
        self.assertIn("<h2", self.html)
        self.assertIn('lang="zh-Hant"', self.html)
        self.assertIn("<table", self.html)
        self.assertIn("<th ", self.html)
        self.assertIn("<caption>", self.html)

    def test_status_is_conveyed_by_text_and_icon_not_colour_alone(self):
        self.assertIn(self.report["confidence"]["icon"], self.html)
        self.assertIn(self.report["confidence"]["text"], self.html)
        self.assertIn('aria-label="信心 ', self.html)

    def test_first_screen_carries_the_decision_facts(self):
        first_screen = self.html.split("<!--first-screen-end-->")[0]
        self.assertIn(self.report["market_status"], first_screen)
        self.assertIn(self.report["period"]["label"], first_screen)
        self.assertIn(self.report["confidence"]["text"], first_screen)
        self.assertIn(self.report["judgement"], first_screen)
        for stance, count in self.report["tally"].items():
            self.assertIn("{}：{}".format(stance, count), first_screen)
        for condition in self.report["invalidation_conditions"]:
            self.assertIn(condition, first_screen)

    def test_markup_is_escaped(self):
        report = _validated()
        report["judgement"] = "<b>不應成為標籤</b>"
        self.assertIn("&lt;b&gt;", render_market_html(report))

    def test_unvalidated_unsafe_url_is_never_an_active_link(self):
        report = _validated()
        report["evidence"][0]["url"] = "javascript:alert(1)"
        html = render_market_html(report)
        self.assertNotIn('href="javascript:', html.lower())

    def test_market_renderer_has_one_implementation_and_one_stylesheet(self):
        source = inspect.getsource(report_renderer_module)
        self.assertEqual(source.count("def render_market_markdown("), 1)
        self.assertEqual(source.count("def render_market_html("), 1)
        self.assertEqual(source.count("_MARKET_CSS ="), 1)


class RenderFixtureCommandTests(unittest.TestCase):
    def test_command_writes_report_json_markdown_html_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["render-fixture", "--case", "consensus-6-1", "--output-dir", tmp])
            self.assertEqual(code, 0)
            out = Path(tmp)
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["consensus_status"], "consensus")
            self.assertIn(report["market_status"], (out / "report.md").read_text(encoding="utf-8"))
            self.assertIn(report["market_status"], (out / "report.html").read_text(encoding="utf-8"))
            audit = json.loads((out / "audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["case"], "consensus-6-1")
            self.assertEqual(audit["status"], "accepted")
            for name in ("report.json", "report.md", "report.html"):
                self.assertRegex(audit["hash_lineage"][name], r"^[0-9a-f]{64}$")
            self.assertRegex(audit["hash_lineage"]["sources"], r"^[0-9a-f]{64}$")

    def test_command_is_deterministic(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            main(["render-fixture", "--case", "consensus-6-1", "--output-dir", first])
            main(["render-fixture", "--case", "consensus-6-1", "--output-dir", second])
            for name in ("report.json", "report.md", "report.html"):
                self.assertEqual(
                    (Path(first) / name).read_text(encoding="utf-8"),
                    (Path(second) / name).read_text(encoding="utf-8"),
                )

    def test_cross_reference_failure_case_renders_a_red_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["render-fixture", "--case", "cross-reference-failure", "--output-dir", tmp])
            self.assertEqual(code, 1)
            report = json.loads((Path(tmp) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["confidence"]["level"], "red")
            self.assertIsNone(report["adopted_stance"])
            self.assertTrue(report["validation_errors"])


if __name__ == "__main__":
    unittest.main()
