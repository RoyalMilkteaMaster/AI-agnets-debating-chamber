"""Ticket 02 discrete vote walls and final-settle behavior."""

import json
import unittest
from dataclasses import replace

from hoya_market_agents.debate_rules import VoteRound, debate_rules
from hoya_market_agents.debate_state_machine import (
    DebateLifecycleError,
    LateMessageError,
    phase_at,
    required_votes_at,
)
from hoya_market_agents.research_scheduler import research_deadlines
from hoya_market_agents.seats import SEAT_IDS
from tests.test_debate_state_machine import DebateHarness


RULES = debate_rules()
SINGLE_SEAL_MS = research_deadlines("single_asset_market_state").seal_ms
COMPARISON_SEAL_MS = research_deadlines("two_asset_comparison").seal_ms
SINGLE_WALLS = tuple(
    SINGLE_SEAL_MS + vote_round.open_offset_ms
    for vote_round in RULES.vote_rounds
)
FINAL_SETTLE_MS = SINGLE_SEAL_MS + RULES.final_settle_offset_ms


class DebateScheduleTest(unittest.TestCase):
    def test_single_asset_uses_the_four_approved_vote_walls(self):
        self.assertEqual((300_000, 390_000, 480_000, 570_000), SINGLE_WALLS)
        self.assertEqual((7, 6, 5, 4), tuple(r.threshold for r in RULES.vote_rounds))
        self.assertEqual(600_000, FINAL_SETTLE_MS)

    def test_comparison_moves_the_whole_schedule_thirty_seconds(self):
        comparison_walls = tuple(
            COMPARISON_SEAL_MS + vote_round.open_offset_ms
            for vote_round in RULES.vote_rounds
        )
        self.assertEqual((330_000, 420_000, 510_000, 600_000), comparison_walls)
        self.assertEqual(630_000, COMPARISON_SEAL_MS + RULES.final_settle_offset_ms)

    def test_phase_and_threshold_helpers_accept_the_run_seal(self):
        self.assertEqual(
            "before_vote_round_1",
            phase_at(SINGLE_WALLS[0] - 1, seal_ms=SINGLE_SEAL_MS),
        )
        for index, (opened_at, vote_round) in enumerate(
            zip(SINGLE_WALLS, RULES.vote_rounds), start=1
        ):
            with self.subTest(round=index):
                self.assertEqual(
                    "vote_round_{}".format(index),
                    phase_at(opened_at, seal_ms=SINGLE_SEAL_MS),
                )
                self.assertEqual(
                    vote_round.threshold,
                    required_votes_at(opened_at, seal_ms=SINGLE_SEAL_MS),
                )
        self.assertEqual(
            "final_settle", phase_at(FINAL_SETTLE_MS, seal_ms=SINGLE_SEAL_MS)
        )


class VoteThresholdTest(unittest.TestCase):
    def test_a_vote_change_stays_frozen_until_the_next_wall_opens(self):
        harness = DebateHarness(self)
        harness.positions(["bullish"] * 4 + ["bearish"] * 3)
        harness.advance_to(SINGLE_WALLS[0])
        harness.machine.tick()

        harness.machine.relay(
            harness.message(
                SEAT_IDS[4],
                "final_vote",
                "between-r1-r2",
                round=1,
                stance="bullish",
                stance_change_reason="新證據支持改票",
            )
        )

        self.assertEqual(4, harness.machine.summary()["tally"]["bullish"])
        self.assertEqual(
            "bearish", harness.machine.vote_table()[4]["final_stance"]
        )

        harness.advance_to(SINGLE_WALLS[1])
        harness.machine.tick()
        self.assertEqual(5, harness.machine.summary()["tally"]["bullish"])
        self.assertEqual(
            "bullish", harness.machine.vote_table()[4]["final_stance"]
        )

    def test_six_one_opening_waits_for_round_two_without_challenge_gate(self):
        harness = DebateHarness(self)
        harness.positions(["bullish"] * 6 + ["bearish"])

        self.assertFalse(harness.machine.stopped)
        self.assertEqual({}, harness.machine.valid_votes())

        harness.advance_to(SINGLE_WALLS[0])
        harness.machine.tick()
        self.assertFalse(harness.machine.stopped)
        self.assertEqual(7, len(harness.machine.valid_votes()))

        harness.advance_to(SINGLE_WALLS[1])
        harness.machine.tick()
        summary = harness.machine.summary()
        self.assertEqual("consensus_6_votes", summary["stop_reason"])
        self.assertEqual("consensus", summary["consensus_status"])
        self.assertEqual("bullish", summary["adopted_stance"])

    def test_five_votes_are_not_continuously_counted_between_walls(self):
        harness = DebateHarness(self)
        harness.positions(["bullish"] * 5 + ["bearish"] * 2)

        harness.advance_to(SINGLE_WALLS[1] + 1)
        harness.machine.tick()
        self.assertFalse(harness.machine.stopped)

        harness.advance_to(SINGLE_WALLS[2])
        harness.machine.tick()
        self.assertEqual("consensus_5_votes", harness.machine.stop_reason)
        self.assertEqual(SINGLE_WALLS[2], harness.machine.stop_elapsed_ms)

    def test_four_votes_settle_at_round_four_with_an_orange_threshold(self):
        harness = DebateHarness(self)
        harness.positions(["bullish"] * 4 + ["bearish"] * 3)

        harness.advance_to(SINGLE_WALLS[3])
        harness.machine.tick()

        summary = harness.machine.summary()
        self.assertEqual("consensus_4_votes", summary["stop_reason"])
        self.assertEqual(4, summary["threshold_required"])
        self.assertEqual("bullish", summary["adopted_stance"])

    def test_final_settle_adopts_a_late_four_vote_leader(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 3 + ["bearish"] * 3 + ["neutral"]
        harness.positions(stances)
        harness.advance_to(SINGLE_WALLS[3])
        harness.machine.tick()
        self.assertFalse(harness.machine.stopped)

        harness.machine.relay(
            harness.message(
                SEAT_IDS[6],
                "final_vote",
                "late-settle-change",
                round=3,
                stance="bullish",
                stance_change_reason="完整證據支持改票",
            )
        )
        harness.advance_to(FINAL_SETTLE_MS)
        harness.machine.tick()

        summary = harness.machine.summary()
        self.assertEqual("forced_stop_4_votes", summary["stop_reason"])
        self.assertEqual("consensus", summary["consensus_status"])
        self.assertEqual("bullish", summary["adopted_stance"])

    def test_final_settle_with_three_two_two_is_red_no_consensus(self):
        harness = DebateHarness(self)
        harness.positions(["bullish"] * 3 + ["bearish"] * 2 + ["neutral"] * 2)
        harness.advance_to(FINAL_SETTLE_MS)
        harness.machine.tick()

        summary = harness.machine.summary()
        self.assertEqual("forced_stop_no_consensus", summary["stop_reason"])
        self.assertEqual("no_consensus", summary["consensus_status"])
        self.assertTrue(summary["red_no_conclusion"])
        self.assertFalse(summary["market_conclusion_allowed"])
        self.assertEqual(7, len(summary["dissent"]))

    def test_final_settle_with_only_three_votes_is_an_insufficient_vote_failure(self):
        harness = DebateHarness(self)
        harness.positions(["bullish"] * 3)
        harness.advance_to(FINAL_SETTLE_MS)
        harness.machine.tick()

        summary = harness.machine.summary()
        self.assertEqual(
            "forced_stop_insufficient_valid_votes", summary["stop_reason"]
        )
        self.assertEqual(
            "failed_insufficient_valid_votes", summary["consensus_status"]
        )
        self.assertEqual(3, summary["valid_vote_count"])
        self.assertTrue(summary["red_no_conclusion"])

    def test_exact_round_wall_settles_before_an_incoming_vote_change(self):
        harness = DebateHarness(self)
        harness.positions(["bullish"] * 5 + ["bearish"] * 2)
        harness.advance_to(SINGLE_WALLS[2])
        incoming = harness.message(
            SEAT_IDS[0],
            "final_vote",
            "on-wall-change",
            round=2,
            stance="bearish",
            stance_change_reason="too late for this opening",
        )

        with self.assertRaises(LateMessageError):
            harness.machine.relay(incoming)

        persisted = json.loads(
            (harness.run.path / "votes.json").read_text(encoding="utf-8")
        )
        self.assertEqual("consensus_5_votes", persisted["stop_reason"])
        self.assertEqual(5, persisted["tally"]["bullish"])
        self.assertEqual("message_rejected", harness.machine.entries[-1]["event"])

    def test_round_count_and_stop_reason_come_from_a_three_round_rule_array(self):
        rules = replace(
            RULES,
            vote_rounds=(
                VoteRound(open_offset_ms=10, threshold=7),
                VoteRound(open_offset_ms=20, threshold=5),
                VoteRound(open_offset_ms=30, threshold=3),
            ),
            final_settle_offset_ms=40,
        )
        harness = DebateHarness(self, rules=rules)
        harness.positions(["bullish"] * 3 + ["bearish"] * 2 + ["neutral"] * 2)

        harness.advance_to(harness.debate_start_ms + 30)
        harness.machine.tick()

        self.assertEqual("consensus_3_votes", harness.machine.stop_reason)
        self.assertEqual(harness.debate_start_ms + 30, harness.machine.stop_elapsed_ms)

    def test_a_single_round_rule_accepts_round_one_after_its_wall(self):
        rules = replace(
            RULES,
            vote_rounds=(VoteRound(open_offset_ms=10, threshold=7),),
            final_settle_offset_ms=20,
        )
        harness = DebateHarness(self, rules=rules)
        harness.positions(["bullish"] * 6 + ["bearish"])
        harness.advance_to(harness.debate_start_ms + 10)
        harness.machine.tick()

        entry = harness.machine.relay(
            harness.message(
                SEAT_IDS[6],
                "final_vote",
                "single-round-change",
                round=1,
                stance="bullish",
                stance_change_reason="結算前的新證據",
            )
        )
        self.assertEqual(1, entry["round"])

        with self.assertRaisesRegex(DebateLifecycleError, "第 1 輪"):
            harness.machine.relay(
                harness.message(
                    SEAT_IDS[5],
                    "final_vote",
                    "bad-single-round-number",
                    round=2,
                    stance="bullish",
                )
            )


if __name__ == "__main__":
    unittest.main()
