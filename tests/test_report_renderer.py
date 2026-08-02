"""Markdown and HTML must come from one report contract, and HTML must open offline."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.fakes import FixedClock, ScriptedTokenSource
from hoya_market_agents.debate_state_machine import stances_for
from hoya_market_agents.fake_provider import FakeProvider
from hoya_market_agents.question_package import build_question_package
from hoya_market_agents.report_fixtures import load_fixture
from hoya_market_agents.report_renderer import (
    REPORT_SCHEMA_VERSION,
    build_report,
    render_html,
    render_market_html,
    render_market_markdown,
    render_markdown,
    resolve_stance_labels,
    stance_labels_for,
)
from hoya_market_agents.run_controller import RunController
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.seats import SEAT_IDS

QUESTION_BY_TYPE = {
    "single_asset_market_state": "分析 BTC 過去 14 日市場狀態",
    "two_asset_comparison": "比較 BTC 與 ETH 過去 14 日的相對強弱",
    "event_impact": "分析監管事件對 BTC 的影響",
    "open_proposition": "分析 BTC 是否值得長期持有",
}

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


class StanceVocabularyTest(unittest.TestCase):
    """Ticket T12a: report wording follows the drawn question type, not bullish/bearish."""

    def test_every_question_type_keeps_its_own_ballot_wording(self):
        expected = {
            "single_asset_market_state": ["偏多", "偏空", "方向不明"],
            "two_asset_comparison": ["BTC較優", "ETH較優", "無明顯差異"],
            "event_impact": ["利多", "利空", "不明或有條件"],
            "open_proposition": ["正方", "反方", "無法決定"],
        }
        for question_type, question in QUESTION_BY_TYPE.items():
            package = build_question_package(question)
            with self.subTest(question_type=question_type):
                self.assertEqual(question_type, package.question_type)
                labels = stance_labels_for(stances_for(question_type), package.assets)
                self.assertEqual(expected[question_type], list(labels.values()))
                # The renderer must agree with the package the seats were given.
                self.assertEqual(package.stance_labels, labels)

    def test_comparison_report_names_both_assets_in_the_tally_and_seats(self):
        report = _comparison_report()

        markdown = render_market_markdown(report)
        html = render_market_html(report)

        for text in (markdown, html):
            self.assertIn("BTC較優：6", text)
            self.assertIn("ETH較優：1", text)
            self.assertIn("無明顯差異：0", text)
            # 只有 Core 撰寫的散文可以保留原句；票數與各席立場不得再出現市場詞彙。
            self.assertNotIn("偏多：", text)
            self.assertNotIn("最終立場：偏多", text)
            self.assertNotIn("asset_a_stronger", text)
        self.assertIn("最終立場：BTC較優", markdown)
        self.assertIn("最終 BTC較優", html)

    def test_comparison_labels_degrade_without_asset_names(self):
        labels = stance_labels_for(stances_for("two_asset_comparison"), ())

        self.assertEqual(["前者較優", "後者較優", "無明顯差異"], list(labels.values()))

    def test_unknown_stance_renders_verbatim_instead_of_failing(self):
        report = _comparison_report()
        report["tally"] = {"asset_a_stronger": 6, "surprise_stance": 1}

        self.assertIn("surprise_stance：1", render_market_markdown(report))

    def test_resolved_labels_prefer_the_ballot_the_run_recorded(self):
        stances = stances_for("two_asset_comparison")
        provided = {
            "asset_a_stronger": "BTC較優",
            "asset_b_stronger": "ETH較優",
            "no_clear_difference": "無明顯差異",
        }

        self.assertEqual(provided, resolve_stance_labels(stances, (), provided))
        self.assertEqual(
            stance_labels_for(stances, ("BTC", "ETH")),
            resolve_stance_labels(stances, ("BTC", "ETH"), {"asset_a_stronger": "BTC較優"}),
        )
        self.assertEqual(
            stance_labels_for(stances, ("BTC", "ETH")),
            resolve_stance_labels(stances, ("BTC", "ETH"), None),
        )


def _comparison_report():
    """The consensus fixture re-cast onto the two-asset comparison ballot."""
    fixture = load_fixture("consensus-6-1")
    report = json.loads(json.dumps(fixture["report"]))
    mapping = {"bullish": "asset_a_stronger", "bearish": "asset_b_stronger"}
    report["assets"] = ["BTC", "ETH"]
    report["tally"] = {
        "asset_a_stronger": report["tally"]["bullish"],
        "asset_b_stronger": report["tally"]["bearish"],
        "no_clear_difference": report["tally"]["neutral"],
    }
    report["adopted_stance"] = mapping[report["adopted_stance"]]
    for seat in report["seats"]:
        for field in ("initial_stance", "final_stance"):
            seat[field] = mapping.get(seat[field], seat[field])
    return report


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
