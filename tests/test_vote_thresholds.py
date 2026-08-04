"""Ticket #9 absolute 6/5/4 consensus thresholds with a fake clock."""

import json
import unittest

from hoya_market_agents.debate_state_machine import (
    CHALLENGE_DEADLINE_MS,
    DEBATE_START_MS,
    FINAL_ROUND_END_MS,
    FINAL_ROUND_START_MS,
    FORCE_STOP_MS,
    ROUND_ONE_WINDOW_MS,
    THRESHOLD_FIVE_FROM_MS,
    LateMessageError,
    phase_at,
    required_votes_at,
)
from hoya_market_agents.seats import SEAT_IDS
from tests.test_debate_state_machine import DebateHarness


class DebateScheduleTest(unittest.TestCase):
    """Ticket R8 修訂二：第二輪提前到 T+6:00，門檻降五仍在 T+8:00。"""

    def test_every_debate_wall_matches_the_approved_schedule(self):
        self.assertEqual(240_000, DEBATE_START_MS)
        # 第一輪牆是相對制：封存 ＋ 3 分鐘（單幣預設 T+7:00、比較題 T+7:30）。
        self.assertEqual(180_000, ROUND_ONE_WINDOW_MS)
        self.assertEqual(420_000, CHALLENGE_DEADLINE_MS)
        self.assertEqual(480_000, THRESHOLD_FIVE_FROM_MS)
        self.assertEqual(525_000, FINAL_ROUND_START_MS)
        self.assertEqual(585_000, FINAL_ROUND_END_MS)
        self.assertEqual(600_000, FORCE_STOP_MS)

    def test_the_second_round_opens_at_six_minutes_and_still_needs_six_votes(self):
        # 第二輪提前開始不等於門檻提前放寬：T+8:00 之前仍然要六票。
        self.assertEqual("first_round", phase_at(CHALLENGE_DEADLINE_MS - 1))
        self.assertEqual("first_round_closed", phase_at(CHALLENGE_DEADLINE_MS))
        self.assertEqual(6, required_votes_at(CHALLENGE_DEADLINE_MS))
        self.assertEqual(6, required_votes_at(THRESHOLD_FIVE_FROM_MS - 1))
        self.assertEqual(5, required_votes_at(THRESHOLD_FIVE_FROM_MS))

    def test_the_second_round_owns_the_gap_between_the_first_and_final_walls(self):
        # r2 迄與 r3 起是同一時刻：第二輪關門的那一秒最後一輪才開門。
        self.assertEqual("first_round", phase_at(CHALLENGE_DEADLINE_MS - 1))
        self.assertEqual("first_round_closed", phase_at(CHALLENGE_DEADLINE_MS))
        self.assertEqual("five_vote_threshold", phase_at(FINAL_ROUND_START_MS - 1))
        self.assertEqual("final_round", phase_at(FINAL_ROUND_START_MS))


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

        harness.advance_to(THRESHOLD_FIVE_FROM_MS)
        harness.machine.tick()

        self.assertEqual(THRESHOLD_FIVE_FROM_MS, harness.machine.stop_elapsed_ms)
        self.assertEqual("consensus_5_votes", harness.machine.stop_reason)
        self.assertEqual("bullish", harness.machine.summary()["adopted_stance"])

    def test_four_votes_are_adopted_only_at_t10_force_stop(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 4 + ["bearish"] * 3
        harness.complete_round(stances)
        harness.advance_to(FORCE_STOP_MS)

        harness.machine.tick()

        summary = harness.machine.summary()
        self.assertEqual("consensus", summary["consensus_status"])
        self.assertEqual("bullish", summary["adopted_stance"])
        self.assertEqual("forced_stop_4_votes", summary["stop_reason"])
        self.assertEqual(FORCE_STOP_MS, summary["stop_elapsed_ms"])

    def test_three_two_two_is_no_consensus_and_preserves_every_position_as_dissent(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 3 + ["bearish"] * 2 + ["neutral"] * 2
        harness.complete_round(stances)
        harness.advance_to(FORCE_STOP_MS)

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
        harness.advance_to(FORCE_STOP_MS)

        harness.machine.tick()

        summary = harness.machine.summary()
        self.assertEqual("failed_insufficient_valid_votes", summary["consensus_status"])
        self.assertTrue(summary["red_no_conclusion"])
        self.assertFalse(summary["market_conclusion_allowed"])
        self.assertIsNone(summary["adopted_stance"])
        self.assertEqual(7, len(summary["votes"]))
        self.assertEqual(3, summary["valid_vote_count"])

    def test_thresholds_are_absolute_not_a_fraction_of_available_seats(self):
        self.assertEqual(6, required_votes_at(THRESHOLD_FIVE_FROM_MS - 1))
        self.assertEqual(5, required_votes_at(THRESHOLD_FIVE_FROM_MS))
        self.assertEqual(4, required_votes_at(FORCE_STOP_MS))
        self.assertEqual("final_round", phase_at(FINAL_ROUND_START_MS))
        self.assertEqual("after_final_round", phase_at(FINAL_ROUND_END_MS + 1))

    def test_exact_t7_settles_existing_five_before_rejecting_incoming_vote_change(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 5 + ["bearish"] * 2
        harness.complete_round(stances, count=5)
        harness.advance_to(THRESHOLD_FIVE_FROM_MS)
        incoming = harness.message(
            SEAT_IDS[0],
            "final_vote",
            "t7-change",
            round=2,
            stance="bearish",
            stance_change_reason="late counterclaim",
        )

        with self.assertRaises(LateMessageError):
            harness.machine.relay(incoming)

        persisted = json.loads(
            (harness.run.path / "votes.json").read_text(encoding="utf-8")
        )
        self.assertEqual("consensus_5_votes", persisted["stop_reason"])
        self.assertEqual(5, persisted["tally"]["bullish"])
        self.assertEqual("bullish", persisted["votes"][0]["final_stance"])
        self.assertEqual("message_rejected", harness.machine.entries[-1]["event"])

    def test_exact_t10_callback_force_stops_and_persists_before_incoming_change(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 4 + ["bearish"] * 3
        harness.complete_round(stances)
        harness.advance_to(FORCE_STOP_MS)
        incoming = harness.message(
            SEAT_IDS[0],
            "final_vote",
            "t10-change",
            round=3,
            stance="bearish",
            stance_change_reason="too late",
        )

        with self.assertRaises(LateMessageError):
            harness.machine.relay(incoming)

        persisted = json.loads(
            (harness.run.path / "votes.json").read_text(encoding="utf-8")
        )
        self.assertEqual("forced_stop_4_votes", persisted["stop_reason"])
        self.assertEqual("bullish", persisted["adopted_stance"])
        self.assertEqual(4, persisted["tally"]["bullish"])
        self.assertEqual("bullish", persisted["votes"][0]["final_stance"])


if __name__ == "__main__":
    unittest.main()
