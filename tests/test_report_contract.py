"""Ticket #10: the Core-authored report contract and its built-in fixtures."""

import unittest

from hoya_market_agents.report_contract import (
    CONFIDENCE_LEVELS,
    ReportContractError,
    validate_market_report,
)
from hoya_market_agents.report_fixtures import FIXTURE_CASES, load_fixture
from hoya_market_agents.seats import SEAT_IDS

REQUIRED_FIELDS = (
    "market_status",
    "period",
    "confidence",
    "tally",
    "consensus_status",
    "judgement",
    "limitations",
    "invalidation_conditions",
)


class FixtureMatrixTests(unittest.TestCase):
    def test_five_report_states_are_available_as_fixtures(self):
        self.assertEqual(
            set(FIXTURE_CASES),
            {
                "consensus-6-1",
                "no-consensus-3-3-1",
                "insufficient-votes-3",
                "insufficient-data",
                "cross-reference-failure",
            },
        )

    def test_every_valid_fixture_passes_the_contract(self):
        for case in ("consensus-6-1", "no-consensus-3-3-1", "insufficient-votes-3", "insufficient-data"):
            with self.subTest(case=case):
                fixture = load_fixture(case)
                report = validate_market_report(fixture["report"], fixture["sources"])
                for field in REQUIRED_FIELDS:
                    self.assertIn(field, report)

    def test_cross_reference_failure_fixture_is_rejected(self):
        fixture = load_fixture("cross-reference-failure")
        with self.assertRaises(ReportContractError) as ctx:
            validate_market_report(fixture["report"], fixture["sources"])
        self.assertTrue(
            any("evidence" in problem for problem in ctx.exception.problems),
            ctx.exception.problems,
        )

    def test_load_fixture_returns_independent_copies(self):
        first = load_fixture("consensus-6-1")
        first["report"]["judgement"] = "mutated"
        self.assertNotEqual(load_fixture("consensus-6-1")["report"]["judgement"], "mutated")


class SeatRowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_fixture("consensus-6-1")
        self.report = self.fixture["report"]
        self.sources = self.fixture["sources"]

    def test_all_seven_seat_rows_are_preserved_with_lineage(self):
        rows = self.report["seats"]
        self.assertEqual([row["seat_id"] for row in rows], list(SEAT_IDS))
        for row in rows:
            self.assertIn("initial_stance", row)
            self.assertIn("final_stance", row)
            self.assertIn("stance_changed", row)
            self.assertTrue(row["public_reason"].strip())
            self.assertIsInstance(row["replacement_attempt_ids"], list)
            self.assertIsInstance(row["support_evidence_ids"], list)
            self.assertIsInstance(row["counter_evidence_ids"], list)

    def test_every_evidence_row_carries_id_and_url(self):
        for card in self.report["evidence"]:
            self.assertTrue(card["evidence_id"])
            self.assertTrue(card["url"].startswith("https://"))

    def test_missing_seat_row_is_rejected(self):
        broken = load_fixture("consensus-6-1")
        broken["report"]["seats"] = broken["report"]["seats"][:-1]
        with self.assertRaises(ReportContractError):
            validate_market_report(broken["report"], broken["sources"])

    def test_tally_must_cross_reference_official_votes(self):
        broken = load_fixture("consensus-6-1")
        broken["report"]["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
        with self.assertRaises(ReportContractError):
            validate_market_report(broken["report"], broken["sources"])

    def test_seat_stance_must_cross_reference_official_votes(self):
        broken = load_fixture("consensus-6-1")
        broken["report"]["seats"][0]["final_stance"] = "bearish"
        with self.assertRaises(ReportContractError):
            validate_market_report(broken["report"], broken["sources"])

    def test_replacement_lineage_must_cross_reference_attempt_ids(self):
        broken = load_fixture("consensus-6-1")
        broken["report"]["seats"][0]["replacement_attempt_ids"] = ["attempt-invented"]
        with self.assertRaises(ReportContractError):
            validate_market_report(broken["report"], broken["sources"])


class HonestFailureTests(unittest.TestCase):
    def test_no_consensus_report_states_no_direction(self):
        fixture = load_fixture("no-consensus-3-3-1")
        report = validate_market_report(fixture["report"], fixture["sources"])
        self.assertEqual(report["consensus_status"], "no_consensus")
        self.assertIsNone(report["adopted_stance"])
        self.assertFalse(report["direction_bearing"])

    def test_insufficient_votes_report_is_red_and_directionless(self):
        fixture = load_fixture("insufficient-votes-3")
        report = validate_market_report(fixture["report"], fixture["sources"])
        self.assertEqual(report["confidence"]["level"], "red")
        self.assertIsNone(report["adopted_stance"])

    def test_directionless_report_may_not_adopt_a_stance(self):
        broken = load_fixture("no-consensus-3-3-1")
        broken["report"]["adopted_stance"] = "bullish"
        broken["report"]["direction_bearing"] = True
        with self.assertRaises(ReportContractError):
            validate_market_report(broken["report"], broken["sources"])

    def test_confidence_levels_are_the_five_approved_lights(self):
        self.assertEqual(
            CONFIDENCE_LEVELS,
            ("red", "orange", "yellow", "yellow_green", "green"),
        )


if __name__ == "__main__":
    unittest.main()
