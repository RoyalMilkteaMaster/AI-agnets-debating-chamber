"""Markdown and HTML must come from one report contract, and HTML must open offline."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from fakes import FixedClock, ScriptedTokenSource
from hoya_market_agents.fake_provider import FakeProvider
from hoya_market_agents.report_renderer import (
    REPORT_SCHEMA_VERSION,
    build_report,
    render_html,
    render_markdown,
)
from hoya_market_agents.run_controller import RunController
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.seats import SEAT_IDS

QUESTION = "分析 BTC 過去 14 日市場狀態"

# Anything that would make the browser fetch a resource at open time.
_RESOURCE_LOADERS = re.compile(
    r"<script\b|<link\b|<iframe\b|<img\b|\bsrc\s*=|@import|url\s*\(", re.IGNORECASE
)


class ReportRendererTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = self._execute_run(Path(self._tmp.name))
        self.report = json.loads((self.run_dir / "report.json").read_text(encoding="utf-8"))
        self.markdown = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.html = (self.run_dir / "report.html").read_text(encoding="utf-8")

    @staticmethod
    def _execute_run(data_root):
        controller = RunController(
            store=RunStore(data_root),
            provider=FakeProvider(),
            clock=FixedClock(auto_advance_ms=250),
            token_source=ScriptedTokenSource(["aaa111"]),
        )
        return controller.execute(QUESTION).run_dir

    def test_run_writes_both_rendered_reports(self):
        self.assertTrue((self.run_dir / "report.md").is_file())
        self.assertTrue((self.run_dir / "report.html").is_file())

    def test_report_contract_carries_the_audited_facts(self):
        self.assertEqual(REPORT_SCHEMA_VERSION, self.report["schema_version"])
        self.assertEqual(QUESTION, self.report["question"])
        self.assertEqual(["BTC"], self.report["assets"])
        self.assertEqual(14, self.report["period_days"])
        self.assertEqual("fake", self.report["provider_mode"])
        self.assertEqual(7, len(self.report["seats"]))
        self.assertEqual(7, sum(self.report["tally"].values()))

    def test_both_renderings_state_the_same_tally(self):
        for stance, count in self.report["tally"].items():
            expected = "{}：{}".format(stance, count)
            self.assertIn(expected, self.markdown)
            self.assertIn(expected, self.html)

    def test_both_renderings_name_all_seven_seats(self):
        for seat_id in SEAT_IDS:
            self.assertIn(seat_id, self.markdown)
            self.assertIn(seat_id, self.html)

    def test_both_renderings_keep_the_minority_stance_visible(self):
        minority = min(self.report["tally"], key=lambda stance: self.report["tally"][stance])
        minority_seats = [s["seat_id"] for s in self.report["seats"] if s["stance"] == minority]

        self.assertTrue(minority_seats)
        for seat_id in minority_seats:
            self.assertIn(seat_id, self.markdown)
            self.assertIn(seat_id, self.html)

    def test_both_renderings_disclose_the_fake_provider_and_scope_limits(self):
        for limit in self.report["scope_limits"]:
            self.assertIn(limit, self.markdown)
            self.assertIn(limit, self.html)

    def test_renderers_are_pure_functions_of_the_contract(self):
        self.assertEqual(self.markdown, render_markdown(self.report))
        self.assertEqual(self.html, render_html(self.report))

    def test_changing_the_contract_changes_both_renderings(self):
        altered = json.loads(json.dumps(self.report))
        altered["question"] = "分析 ETH 過去 30 日市場狀態"

        self.assertIn("分析 ETH 過去 30 日市場狀態", render_markdown(altered))
        self.assertIn("分析 ETH 過去 30 日市場狀態", render_html(altered))

    def test_html_is_self_contained_and_loads_nothing_at_open_time(self):
        self.assertIsNone(
            _RESOURCE_LOADERS.search(self.html),
            "report.html 不得在開啟時載入任何外部資源",
        )
        self.assertIn("<style>", self.html)

    def test_html_only_external_references_are_clickable_source_links(self):
        hrefs = re.findall(r'href="([^"]+)"', self.html)
        external = [href for href in hrefs if "://" in href]

        self.assertTrue(external, "來源網址必須維持可點擊")
        for href in external:
            self.assertTrue(href.startswith("https://fake.invalid"), href)

    def test_html_links_to_the_raw_audit_files_relatively(self):
        hrefs = re.findall(r'href="([^"]+)"', self.html)

        self.assertIn("evidence.jsonl", hrefs)
        self.assertIn("debate.jsonl", hrefs)

    def test_html_states_confidence_as_text_not_only_colour(self):
        self.assertIn(self.report["confidence"]["label"], self.html)
        self.assertIn(self.report["confidence"]["icon"], self.html)

    def test_html_escapes_contract_content(self):
        altered = json.loads(json.dumps(self.report))
        altered["question"] = '<script>alert("x")</script>'

        html = render_html(altered)

        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_every_cited_evidence_id_exists_in_the_evidence_snapshot(self):
        known = {
            json.loads(line)["evidence_id"]
            for line in (self.run_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        }
        for seat in self.report["seats"]:
            for evidence_id in seat["evidence_ids"]:
                self.assertIn(evidence_id, known)


class ReportContractTest(unittest.TestCase):
    def test_build_report_rejects_a_vote_count_that_is_not_seven(self):
        with self.assertRaises(ValueError):
            build_report(
                run_id="20260314T015926Z-btc-aaa111",
                question=QUESTION,
                assets=["BTC"],
                period_days=14,
                period_stated=True,
                provider_mode="fake",
                started_at_utc="2026-03-14T01:59:26Z",
                generated_at_utc="2026-03-14T01:59:40Z",
                evidence=[],
                debate=[],
                votes=[],
                tally={"bullish": 0, "bearish": 0, "neutral": 0},
                roster=[],
                scope_limits=[],
            )


if __name__ == "__main__":
    unittest.main()
