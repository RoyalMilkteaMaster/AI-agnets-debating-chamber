"""Ticket #10: confidence caps, prohibited advice and the timed report workflow."""

import unittest

from hoya_market_agents.report_contract import (
    ReportContractError,
    confidence_cap,
    validate_market_report,
)
from hoya_market_agents.report_fixtures import load_fixture
from hoya_market_agents.report_workflow import (
    CORE_DRAFT_LIMIT_MS,
    CORRECTION_WINDOW_MS,
    HARD_DEADLINE_MS,
    RENDER_WINDOW_MS,
    run_report_workflow,
)
from tests.fakes import FixedClock


def _scripted_core(reports, cost_ms, clock):
    """A fake Core Agent that burns injected clock time per attempt."""
    drafts = list(reports)
    costs = list(cost_ms)

    def author(attempt, errors):
        clock.advance_ms(costs[attempt - 1])
        return drafts[attempt - 1]

    return author


class ConfidenceCapTests(unittest.TestCase):
    def test_six_to_one_consensus_caps_at_yellow_green(self):
        fixture = load_fixture("consensus-6-1")
        self.assertEqual(confidence_cap(fixture["report"], fixture["sources"]), "yellow_green")

    def test_seven_to_zero_with_four_fresh_categories_caps_at_green(self):
        fixture = load_fixture("consensus-6-1")
        fixture["sources"]["votes"]["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
        self.assertEqual(confidence_cap(fixture["report"], fixture["sources"]), "green")

    def test_fewer_than_four_valid_votes_caps_at_red(self):
        fixture = load_fixture("insufficient-votes-3")
        self.assertEqual(confidence_cap(fixture["report"], fixture["sources"]), "red")

    def test_process_failure_caps_at_red(self):
        fixture = load_fixture("consensus-6-1")
        fixture["report"]["process_failure"] = True
        self.assertEqual(confidence_cap(fixture["report"], fixture["sources"]), "red")

    def test_four_valid_votes_caps_at_orange(self):
        fixture = load_fixture("consensus-6-1")
        fixture["sources"]["votes"]["valid_vote_count"] = 4
        self.assertEqual(confidence_cap(fixture["report"], fixture["sources"]), "orange")

    def test_single_source_dependence_caps_at_orange(self):
        fixture = load_fixture("consensus-6-1")
        for card in fixture["sources"]["evidence"]:
            card["source_origin"] = "one-and-only.example"
        self.assertEqual(confidence_cap(fixture["report"], fixture["sources"]), "orange")

    def test_five_of_seven_with_two_independent_categories_caps_at_yellow(self):
        fixture = load_fixture("consensus-6-1")
        fixture["sources"]["votes"]["valid_vote_count"] = 5
        self.assertEqual(confidence_cap(fixture["report"], fixture["sources"]), "yellow")

    def test_six_of_seven_with_three_reliable_categories_caps_at_yellow_green(self):
        fixture = load_fixture("consensus-6-1")
        fixture["sources"]["votes"]["valid_vote_count"] = 6
        self.assertEqual(confidence_cap(fixture["report"], fixture["sources"]), "yellow_green")

    def test_stale_evidence_downgrades_but_never_upgrades(self):
        fixture = load_fixture("consensus-6-1")
        fixture["sources"]["evidence"][0]["published_at_utc"] = "2020-01-01T00:00:00Z"
        self.assertEqual(confidence_cap(fixture["report"], fixture["sources"]), "yellow")

    def test_fatal_counterevidence_downgrades(self):
        fixture = load_fixture("consensus-6-1")
        fixture["sources"]["evidence"][-1]["fatal_counterevidence"] = True
        self.assertEqual(confidence_cap(fixture["report"], fixture["sources"]), "yellow")

    def test_low_source_quality_downgrades(self):
        fixture = load_fixture("consensus-6-1")
        fixture["sources"]["evidence"][1]["source_tier"] = 3
        self.assertEqual(confidence_cap(fixture["report"], fixture["sources"]), "yellow")

    def test_confidence_above_the_cap_is_rejected(self):
        fixture = load_fixture("insufficient-votes-3")
        fixture["report"]["confidence"] = {
            "level": "green",
            "icon": "🟢",
            "text": "資料充分",
        }
        with self.assertRaises(ReportContractError) as ctx:
            validate_market_report(fixture["report"], fixture["sources"])
        self.assertTrue(
            any("信心" in problem for problem in ctx.exception.problems),
            ctx.exception.problems,
        )

    def test_confidence_below_the_cap_is_accepted(self):
        fixture = load_fixture("consensus-6-1")
        fixture["report"]["confidence"] = {
            "level": "yellow",
            "icon": "🟡",
            "text": "Core 自行下調的信心說明。",
        }
        self.assertIsNotNone(validate_market_report(fixture["report"], fixture["sources"]))


class ProhibitedAdviceTests(unittest.TestCase):
    CASES = (
        ("price_target", "目標價 120000 美元"),
        ("guaranteed", "此區間保證上漲，不會回檔"),
        ("leverage", "建議使用 5 倍槓桿放大部位"),
        ("position_size", "請將倉位配置至資產的 30%"),
        ("direct_order", "現在請買進並於下週賣出"),
    )

    def test_prohibited_advice_fails_closed(self):
        for label, phrase in self.CASES:
            with self.subTest(case=label):
                fixture = load_fixture("consensus-6-1")
                fixture["report"]["judgement"] = phrase
                with self.assertRaises(ReportContractError) as ctx:
                    validate_market_report(fixture["report"], fixture["sources"])
                self.assertTrue(
                    any("禁止" in problem for problem in ctx.exception.problems),
                    ctx.exception.problems,
                )

    def test_prohibited_advice_is_detected_in_nested_fields(self):
        fixture = load_fixture("consensus-6-1")
        fixture["report"]["limitations"].append("保證上漲")
        with self.assertRaises(ReportContractError):
            validate_market_report(fixture["report"], fixture["sources"])

    def test_shipped_fixtures_contain_no_prohibited_advice(self):
        for case in ("consensus-6-1", "no-consensus-3-3-1", "insufficient-data"):
            with self.subTest(case=case):
                fixture = load_fixture(case)
                validate_market_report(fixture["report"], fixture["sources"])


class WorkflowTimingTests(unittest.TestCase):
    def test_windows_are_ninety_sixty_thirty_and_thirteen_minutes(self):
        self.assertEqual(CORE_DRAFT_LIMIT_MS, 90_000)
        self.assertEqual(CORRECTION_WINDOW_MS, 60_000)
        self.assertEqual(RENDER_WINDOW_MS, 30_000)
        self.assertEqual(HARD_DEADLINE_MS, 13 * 60_000)

    def test_first_pass_report_is_accepted(self):
        fixture = load_fixture("consensus-6-1")
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([fixture["report"]], [60_000], clock),
        )
        self.assertEqual(outcome.status, "accepted")
        self.assertEqual(outcome.corrections_used, 0)
        self.assertEqual(outcome.report["judgement"], fixture["report"]["judgement"])

    def test_late_core_draft_becomes_a_red_audit(self):
        fixture = load_fixture("consensus-6-1")
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([fixture["report"]], [90_001], clock),
        )
        self.assertEqual(outcome.status, "red_audit")
        self.assertEqual(outcome.report["confidence"]["level"], "red")
        self.assertTrue(any("90" in error for error in outcome.errors))

    def test_one_correction_is_accepted(self):
        fixture = load_fixture("consensus-6-1")
        broken = load_fixture("consensus-6-1")["report"]
        broken["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([broken, fixture["report"]], [10_000, 10_000], clock),
        )
        self.assertEqual(outcome.status, "corrected")
        self.assertEqual(outcome.corrections_used, 1)

    def test_render_may_use_the_full_thirty_second_window(self):
        fixture = load_fixture("consensus-6-1")
        clock = FixedClock()

        def renderer(report):
            clock.advance_ms(30_000)
            return report["run_id"]

        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([fixture["report"]], [10_000], clock),
            renderer=renderer,
        )
        self.assertEqual(outcome.status, "accepted")
        self.assertEqual(outcome.phase_elapsed_ms["render"], 30_000)

    def test_render_over_thirty_seconds_becomes_a_red_audit(self):
        fixture = load_fixture("consensus-6-1")
        clock = FixedClock()

        def renderer(report):
            clock.advance_ms(30_001)
            return report["run_id"]

        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([fixture["report"]], [10_000], clock),
            renderer=renderer,
        )
        self.assertEqual(outcome.status, "red_audit")
        self.assertTrue(any("30" in error for error in outcome.errors))

    def test_core_or_renderer_exception_becomes_a_red_audit(self):
        fixture = load_fixture("consensus-6-1")
        for failed_step in ("core", "renderer"):
            with self.subTest(step=failed_step):
                clock = FixedClock()

                def core(attempt, errors):
                    if failed_step == "core":
                        raise RuntimeError("provider failed")
                    return fixture["report"]

                def renderer(report):
                    if failed_step == "renderer":
                        raise RuntimeError("renderer failed")
                    return report

                outcome = run_report_workflow(clock, fixture["sources"], core, renderer)
                self.assertEqual(outcome.status, "red_audit")
                self.assertEqual(outcome.report["confidence"]["level"], "red")

    def test_second_failure_produces_a_red_audit_without_market_conclusion(self):
        fixture = load_fixture("consensus-6-1")
        broken = load_fixture("consensus-6-1")["report"]
        broken["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([broken, broken], [10_000, 10_000], clock),
        )
        self.assertEqual(outcome.status, "red_audit")
        self.assertEqual(outcome.corrections_used, 1)
        self.assertIsNone(outcome.report["adopted_stance"])
        self.assertFalse(outcome.report["direction_bearing"])
        self.assertTrue(outcome.report["validation_errors"])
        self.assertEqual(outcome.report["confidence"]["level"], "red")

    def test_at_most_one_correction_is_requested(self):
        fixture = load_fixture("consensus-6-1")
        broken = load_fixture("consensus-6-1")["report"]
        broken["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
        attempts = []
        clock = FixedClock()

        def author(attempt, errors):
            attempts.append(attempt)
            clock.advance_ms(1_000)
            return broken

        run_report_workflow(clock, fixture["sources"], author)
        self.assertEqual(attempts, [1, 2])

    def test_late_correction_becomes_a_red_audit(self):
        fixture = load_fixture("consensus-6-1")
        broken = load_fixture("consensus-6-1")["report"]
        broken["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([broken, fixture["report"]], [80_000, 61_000], clock),
        )
        self.assertEqual(outcome.status, "red_audit")
        self.assertTrue(any("60" in error for error in outcome.errors))

    def test_work_at_or_after_thirteen_minutes_is_a_late_failure(self):
        fixture = load_fixture("consensus-6-1")
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([fixture["report"]], [13 * 60_000], clock),
        )
        self.assertEqual(outcome.status, "red_audit")
        self.assertTrue(outcome.late)
        self.assertTrue(any("T+13" in error for error in outcome.errors))

    def test_red_audit_report_is_itself_contract_valid(self):
        fixture = load_fixture("consensus-6-1")
        broken = load_fixture("consensus-6-1")["report"]
        broken["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([broken, broken], [1_000, 1_000], clock),
        )
        self.assertIsNotNone(validate_market_report(outcome.report, fixture["sources"]))


if __name__ == "__main__":
    unittest.main()
