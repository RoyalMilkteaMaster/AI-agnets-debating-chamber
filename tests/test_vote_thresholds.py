"""Ticket #9 absolute 6/5/4 consensus thresholds with a fake clock."""

import unittest

from hoya_market_agents.debate_state_machine import phase_at, required_votes_at
from hoya_market_agents.seats import SEAT_IDS
from tests.test_debate_state_machine import DebateHarness


class VoteThresholdTest(unittest.TestCase):
    def test_six_votes_stop_only_after_first_round_challenge(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 6 + ["bearish"]
        harness.positions(stances)
        self.assertFalse(harness.machine.stopped)

        harness.challenges_and_responses(stances)
        harness.finals(stances, count=6)

        self.assertEqual("consensus", harness.machine.summary()["consensus_status"])
        self.assertEqual("bullish", harness.machine.summary()["adopted_stance"])
        self.assertEqual("consensus_6_votes", harness.machine.stop_reason)

    def test_five_votes_do_not_stop_before_t7_then_stop_on_tick(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 5 + ["bearish"] * 2
        harness.complete_round(stances, count=5)
        self.assertFalse(harness.machine.stopped)

        harness.clock.advance_ms(120_000)
        harness.machine.tick()

        self.assertEqual(420_000, harness.machine.stop_elapsed_ms)
        self.assertEqual("consensus_5_votes", harness.machine.stop_reason)
        self.assertEqual("bullish", harness.machine.summary()["adopted_stance"])

    def test_four_votes_are_adopted_only_at_t10_force_stop(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 4 + ["bearish"] * 3
        harness.complete_round(stances)
        harness.clock.advance_ms(300_000)

        harness.machine.tick()

        summary = harness.machine.summary()
        self.assertEqual("consensus", summary["consensus_status"])
        self.assertEqual("bullish", summary["adopted_stance"])
        self.assertEqual("forced_stop_4_votes", summary["stop_reason"])
        self.assertEqual(600_000, summary["stop_elapsed_ms"])

    def test_three_two_two_is_no_consensus_and_preserves_every_position_as_dissent(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 3 + ["bearish"] * 2 + ["neutral"] * 2
        harness.complete_round(stances)
        harness.clock.advance_ms(300_000)

        harness.machine.tick()

        summary = harness.machine.summary()
        self.assertEqual("no_consensus", summary["consensus_status"])
        self.assertIsNone(summary["adopted_stance"])
        self.assertFalse(summary["market_conclusion_allowed"])
        self.assertEqual("forced_stop_no_consensus", summary["stop_reason"])
        self.assertEqual(7, len(summary["dissent"]))

    def test_fewer_than_four_valid_votes_is_red_with_no_market_conclusion(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 3 + ["bearish"] * 2 + ["neutral"] * 2
        harness.complete_round(stances, count=3)
        harness.clock.advance_ms(300_000)

        harness.machine.tick()

        summary = harness.machine.summary()
        self.assertEqual("failed_insufficient_valid_votes", summary["consensus_status"])
        self.assertTrue(summary["red_no_conclusion"])
        self.assertFalse(summary["market_conclusion_allowed"])
        self.assertIsNone(summary["adopted_stance"])
        self.assertEqual(7, len(summary["votes"]))
        self.assertEqual(3, summary["valid_vote_count"])

    def test_thresholds_are_absolute_not_a_fraction_of_available_seats(self):
        self.assertEqual(6, required_votes_at(389_999))
        self.assertEqual(5, required_votes_at(420_000))
        self.assertEqual(4, required_votes_at(600_000))
        self.assertEqual("final_round", phase_at(510_000))
        self.assertEqual("after_final_round", phase_at(585_001))


if __name__ == "__main__":
    unittest.main()
