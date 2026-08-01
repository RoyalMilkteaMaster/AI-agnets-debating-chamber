"""Ticket #9 public debate relay, lineage and persistence behavior."""

import json
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents.debate_state_machine import (
    CoreOverrideError,
    DebateLifecycleError,
    DebateStateMachine,
    DuplicateMessageError,
    EvidenceSnapshotMismatchError,
    LateMessageError,
    ReplacementHistoryError,
    ScriptedDebateGateway,
    StanceError,
    UnknownAttemptError,
    UnknownEvidenceError,
    content_sha256,
)
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.seats import SEAT_IDS
from tests.fakes import FixedClock


RUN_ID = "20260801T073000Z-btc-debate"


class DebateHarness:
    """Small deterministic fixture shared with the threshold tests."""

    def __init__(self, test_case, question_type="single_asset_market_state", run_id=RUN_ID):
        self.test_case = test_case
        self.temp = tempfile.TemporaryDirectory()
        test_case.addCleanup(self.temp.cleanup)
        self.clock = FixedClock()
        self.clock.advance_ms(300_000)
        self.run = RunStore(Path(self.temp.name)).create_run(run_id, SEAT_IDS)
        self.evidence = [
            {
                "evidence_id": "{}-01".format(seat_id),
                "seat_id": seat_id,
                "statement": "public evidence for {}".format(seat_id),
            }
            for seat_id in SEAT_IDS
        ]
        seal = self.run.seal_evidence_snapshot(
            self.evidence, "2026-08-01T07:35:00Z", 300_000
        )
        self.snapshot_sha = seal["sha256"]
        self.machine = DebateStateMachine(
            run=self.run,
            clock=self.clock,
            gateway=None,
            question_type=question_type,
            evidence_records=self.evidence,
            evidence_snapshot_sha256=self.snapshot_sha,
            start_monotonic_ms=0,
        )

    def message(self, seat_id, kind, suffix, **overrides):
        value = {
            "schema_version": "1.0.0",
            "message_id": "{}-{}".format(seat_id, suffix),
            "seat_id": seat_id,
            "attempt_id": "{}-a1".format(seat_id),
            "kind": kind,
            "round": 1,
            "evidence_snapshot_sha256": self.snapshot_sha,
            "evidence_ids": ["{}-01".format(seat_id)],
            "public_reason": "public reason from {}".format(seat_id),
            "stance_change_reason": None,
        }
        value.update(overrides)
        return value

    def positions(self, stances):
        for seat_id, stance in zip(SEAT_IDS, stances):
            neutral = self.neutral_context(stance)
            self.machine.relay(
                self.message(
                    seat_id, "position", "position", stance=stance, round=0, **neutral
                )
            )

    def challenges_and_responses(self, stances):
        challenge_ids = {}
        for index, seat_id in enumerate(SEAT_IDS):
            target_index = next(
                other
                for other in range(len(SEAT_IDS))
                if other != index and stances[other] != stances[index]
            )
            target = SEAT_IDS[target_index]
            challenge = self.message(
                seat_id,
                "challenge",
                "challenge",
                target_seat_id=target,
                target_claim="{}-position".format(target),
                stance=stances[index],
            )
            self.machine.relay(challenge)
            challenge_ids[seat_id] = challenge["message_id"]
        for index, seat_id in enumerate(SEAT_IDS):
            opposing = next(
                source
                for source in SEAT_IDS
                if stances[SEAT_IDS.index(source)] != stances[index]
            )
            self.machine.relay(
                self.message(
                    seat_id,
                    "response",
                    "response",
                    responds_to=[challenge_ids[opposing]],
                    target_seat_id=opposing,
                    stance=stances[index],
                )
            )

    def finals(self, stances, count=7, changed_reasons=None, attempt_overrides=None):
        changed_reasons = changed_reasons or {}
        attempt_overrides = attempt_overrides or {}
        for seat_id, stance in list(zip(SEAT_IDS, stances))[:count]:
            neutral = self.neutral_context(stance)
            message = self.message(
                seat_id,
                "final_vote",
                "final",
                stance=stance,
                stance_change_reason=changed_reasons.get(seat_id),
                **neutral,
            )
            message.update(attempt_overrides.get(seat_id, {}))
            self.machine.relay(message)

    def neutral_context(self, stance):
        if stance not in ("neutral", "no_clear_difference", "unclear_or_conditional"):
            return {}
        return {
            "conflicting_evidence_ids": [
                "{}-01".format(SEAT_IDS[0]),
                "{}-01".format(SEAT_IDS[1]),
            ],
            "uncertainty_reason": "the cited evidence conflicts",
            "change_trigger": "a fresh primary source resolving the conflict",
        }

    def complete_round(self, initial, final=None, count=7, changed_reasons=None):
        self.positions(initial)
        self.challenges_and_responses(initial)
        self.finals(final or initial, count=count, changed_reasons=changed_reasons)

    def script(self, stances, final_count):
        messages = [
            self.message(
                seat_id,
                "position",
                "position",
                stance=stance,
                round=0,
                **self.neutral_context(stance),
            )
            for seat_id, stance in zip(SEAT_IDS, stances)
        ]
        challenge_ids = {}
        for index, seat_id in enumerate(SEAT_IDS):
            target_index = next(
                other
                for other in range(len(SEAT_IDS))
                if other != index and stances[other] != stances[index]
            )
            target = SEAT_IDS[target_index]
            challenge = self.message(
                seat_id,
                "challenge",
                "challenge",
                target_seat_id=target,
                target_claim="{}-position".format(target),
                stance=stances[index],
            )
            messages.append(challenge)
            challenge_ids[seat_id] = challenge["message_id"]
        for index, seat_id in enumerate(SEAT_IDS):
            opposing = next(
                source
                for source in SEAT_IDS
                if stances[SEAT_IDS.index(source)] != stances[index]
            )
            messages.append(
                self.message(
                    seat_id,
                    "response",
                    "response",
                    responds_to=[challenge_ids[opposing]],
                    target_seat_id=opposing,
                    stance=stances[index],
                )
            )
        for seat_id, stance in list(zip(SEAT_IDS, stances))[:final_count]:
            messages.append(
                self.message(
                    seat_id,
                    "final_vote",
                    "final",
                    stance=stance,
                    stance_change_reason=None,
                    **self.neutral_context(stance),
                )
            )
        return messages


class DebateStateMachineTest(unittest.TestCase):
    def test_scripted_gateway_runs_without_sleep_and_persists_consensus(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 6 + ["bearish"]
        harness.machine.gateway = ScriptedDebateGateway(
            harness.script(stances, final_count=6), harness.clock
        )

        summary = harness.machine.run_debate()

        self.assertEqual("consensus_6_votes", summary["stop_reason"])
        self.assertTrue((harness.run.path / "debate.jsonl").is_file())
        self.assertTrue((harness.run.path / "votes.json").is_file())

    def test_initial_six_votes_remain_provisional_until_challenge_and_response(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 6 + ["bearish"]

        harness.positions(stances)

        self.assertFalse(harness.machine.stopped)
        self.assertEqual(0, len(harness.machine.valid_votes()))
        with self.assertRaises(DebateLifecycleError):
            harness.machine.relay(
                harness.message(
                    SEAT_IDS[0], "final_vote", "too-early", stance="bullish"
                )
            )

    def test_changed_and_unchanged_votes_keep_reasons_and_before_after(self):
        harness = DebateHarness(self)
        initial = ["bullish", "bullish", "bearish", "bearish", "neutral", "neutral", "neutral"]
        final = list(initial)
        final[0] = "bearish"

        harness.complete_round(
            initial,
            final,
            changed_reasons={SEAT_IDS[0]: "counter evidence changed my vote"},
        )
        table = {row["seat_id"]: row for row in harness.machine.vote_table()}

        changed = table[SEAT_IDS[0]]["vote_changes"][-1]
        self.assertEqual("bullish", changed["before"])
        self.assertEqual("bearish", changed["after"])
        self.assertEqual("counter evidence changed my vote", changed["reason"])
        unchanged = table[SEAT_IDS[1]]
        self.assertFalse(unchanged["stance_changed"])
        self.assertEqual("public reason from derivatives", unchanged["final_public_reason"])

    def test_relay_keeps_verbatim_content_and_hash_even_if_caller_mutates_input(self):
        harness = DebateHarness(self)
        content = harness.message(
            SEAT_IDS[0], "position", "position", stance="bullish", round=0
        )
        expected = json.loads(json.dumps(content))
        expected_hash = content_sha256(expected)

        entry = harness.machine.relay(content)
        content["public_reason"] = "core rewrote it"

        self.assertEqual(expected, entry["content"])
        self.assertEqual(expected_hash, entry["content_sha256"])
        self.assertTrue(harness.machine.verify_public_history())

    def test_persist_writes_complete_audit_files_once(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 3 + ["bearish"] * 2 + ["neutral"] * 2
        harness.complete_round(stances)
        harness.clock.advance_ms(300_000)
        harness.machine.tick()

        summary = harness.machine.persist()
        debate = [
            json.loads(line)
            for line in (harness.run.path / "debate.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        votes = json.loads((harness.run.path / "votes.json").read_text(encoding="utf-8"))

        self.assertEqual(7, len(summary["votes"]))
        self.assertEqual(summary, votes)
        self.assertEqual(harness.machine.entries, debate)
        self.assertTrue(harness.machine.verify_public_history())

    def test_comparison_and_event_use_distinct_three_stance_enums(self):
        cases = (
            ("two_asset_comparison", "asset_a_stronger", "bullish"),
            ("event_impact", "positive", "asset_a_stronger"),
        )
        for index, (question_type, valid, invalid) in enumerate(cases):
            with self.subTest(question_type=question_type):
                harness = DebateHarness(self, question_type, "{}-{}".format(RUN_ID, index))
                harness.machine.relay(
                    harness.message(
                        SEAT_IDS[0],
                        "position",
                        "valid",
                        stance=valid,
                        round=0,
                        **harness.neutral_context(valid),
                    )
                )
                with self.assertRaises(StanceError):
                    harness.machine.relay(
                        harness.message(SEAT_IDS[1], "position", "invalid", stance=invalid, round=0)
                    )

    def test_neutral_position_requires_conflicts_uncertainty_and_change_trigger(self):
        harness = DebateHarness(self)

        with self.assertRaises(DebateLifecycleError):
            harness.machine.relay(
                harness.message(
                    SEAT_IDS[0], "position", "neutral", stance="neutral", round=0
                )
            )

    def test_duplicate_unknown_evidence_snapshot_and_core_override_fail_closed(self):
        harness = DebateHarness(self)
        first = harness.message(SEAT_IDS[0], "position", "same", stance="bullish", round=0)
        harness.machine.relay(first)
        with self.assertRaises(DuplicateMessageError):
            harness.machine.relay(first)

        with self.assertRaises(UnknownEvidenceError):
            harness.machine.relay(
                harness.message(
                    SEAT_IDS[1],
                    "position",
                    "unknown-evidence",
                    stance="bearish",
                    round=0,
                    evidence_ids=["not-in-snapshot"],
                )
            )
        with self.assertRaises(EvidenceSnapshotMismatchError):
            harness.machine.relay(
                harness.message(
                    SEAT_IDS[1],
                    "position",
                    "bad-snapshot",
                    stance="bearish",
                    round=0,
                    evidence_snapshot_sha256="0" * 64,
                )
            )
        with self.assertRaises(CoreOverrideError):
            harness.machine.relay(
                harness.message(
                    SEAT_IDS[1], "position", "core", stance="bearish", round=0
                ),
                actor="core",
            )

    def test_unknown_attempt_and_replacement_without_full_history_fail_closed(self):
        harness = DebateHarness(self)
        with self.assertRaises(UnknownAttemptError):
            harness.machine.relay(
                harness.message(
                    SEAT_IDS[0],
                    "position",
                    "bad-attempt",
                    stance="bullish",
                    round=0,
                    attempt_id="rogue-attempt",
                )
            )

        harness.machine.relay(
            harness.message(SEAT_IDS[0], "position", "position", stance="bullish", round=0)
        )
        with self.assertRaises(ReplacementHistoryError):
            harness.machine.relay(
                harness.message(
                    SEAT_IDS[0],
                    "position",
                    "replacement",
                    stance="bullish",
                    round=0,
                    attempt_id="{}-a2".format(SEAT_IDS[0]),
                )
            )
        self.assertEqual(["spot-technical-a1"], harness.machine.vote_table()[0]["attempt_ids"])

    def test_changed_vote_without_change_reason_fails_closed(self):
        harness = DebateHarness(self)
        initial = ["bullish"] * 3 + ["bearish"] * 2 + ["neutral"] * 2
        harness.positions(initial)
        harness.challenges_and_responses(initial)

        with self.assertRaises(DebateLifecycleError):
            harness.machine.relay(
                harness.message(
                    SEAT_IDS[0], "final_vote", "changed", stance="bearish"
                )
            )

    def test_tampered_sealed_snapshot_and_message_after_final_window_fail_closed(self):
        harness = DebateHarness(self)
        snapshot_path = harness.run.path / "snapshots" / "evidence.jsonl"
        snapshot_path.write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(EvidenceSnapshotMismatchError):
            DebateStateMachine(
                run=harness.run,
                clock=harness.clock,
                gateway=None,
                question_type="single_asset_market_state",
                evidence_records=harness.evidence,
                evidence_snapshot_sha256=harness.snapshot_sha,
                start_monotonic_ms=0,
            )

        fresh = DebateHarness(self, run_id="{}-late-window".format(RUN_ID))
        fresh.clock.advance_ms(285_001)
        with self.assertRaises(LateMessageError):
            fresh.machine.relay(
                fresh.message(
                    SEAT_IDS[0], "position", "after-final-window", stance="bullish", round=0
                )
            )

    def test_replacement_replay_keeps_one_seat_vote_not_an_attempt_vote(self):
        harness = DebateHarness(self)
        initial = ["bullish"] * 3 + ["bearish"] * 2 + ["neutral"] * 2
        harness.positions(initial)
        harness.challenges_and_responses(initial)
        replay_hash = harness.machine.public_history_sha256
        harness.finals(
            initial,
            attempt_overrides={
                SEAT_IDS[0]: {
                    "attempt_id": "{}-a2".format(SEAT_IDS[0]),
                    "replayed_history_sha256": replay_hash,
                }
            },
        )

        row = harness.machine.vote_table()[0]
        self.assertEqual(["spot-technical-a1", "spot-technical-a2"], row["attempt_ids"])
        self.assertEqual(7, len(harness.machine.vote_table()))
        self.assertEqual(7, len(harness.machine.valid_votes()))
        self.assertTrue(harness.machine.verify_public_history())

    def test_message_after_consensus_is_rejected_as_late(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 6 + ["bearish"]
        harness.complete_round(stances, count=6)
        self.assertTrue(harness.machine.stopped)

        with self.assertRaises(LateMessageError):
            harness.machine.relay(
                harness.message(SEAT_IDS[6], "final_vote", "late", stance="bearish")
            )


if __name__ == "__main__":
    unittest.main()
