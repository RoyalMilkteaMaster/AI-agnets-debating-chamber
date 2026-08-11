"""Ticket #9 public debate relay, lineage and persistence behavior."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from hoya_market_agents.debate_rules import VoteRound, debate_rules
from hoya_market_agents.debate_state_machine import (
    CoreOverrideError,
    DebateError,
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
from hoya_market_agents.research_scheduler import research_deadlines
from hoya_market_agents.seats import SEAT_IDS
from tests.fakes import FixedClock

# 取值來源改成 config/debate_rules.json 的載入器；斷言值刻意不動。
RULES = debate_rules()
DEBATE_START_MS = research_deadlines().seal_ms
VOTE_ROUND_OPEN_MS = tuple(
    DEBATE_START_MS + vote_round.open_offset_ms
    for vote_round in RULES.vote_rounds
)
FIRST_ROUND_OPEN_MS = VOTE_ROUND_OPEN_MS[0]
SECOND_ROUND_OPEN_MS = VOTE_ROUND_OPEN_MS[1]
FINAL_SETTLE_MS = DEBATE_START_MS + RULES.final_settle_offset_ms
FINAL_ROUND_END_MS = FINAL_SETTLE_MS - 1
FORCE_STOP_MS = FINAL_SETTLE_MS
BLIND_PASS_VOTES = RULES.unanimous_blind_pass_votes


RUN_ID = "20260801T073000Z-btc-debate"


class DebateHarness:
    """Small deterministic fixture shared with the threshold tests."""

    def __init__(
        self,
        test_case,
        question_type="single_asset_market_state",
        run_id=RUN_ID,
        rules=None,
    ):
        self.test_case = test_case
        self.temp = tempfile.TemporaryDirectory()
        test_case.addCleanup(self.temp.cleanup)
        self.rules = rules or RULES
        self.debate_start_ms = research_deadlines(question_type).seal_ms
        self.clock = FixedClock()
        self.clock.advance_ms(self.debate_start_ms)
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
            self.evidence, "2026-08-01T07:34:00Z", self.debate_start_ms
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
            debate_start_ms=self.debate_start_ms,
            rules=self.rules,
        )

    def advance_to(self, elapsed_ms):
        """Move the fake clock to one absolute T+ instant of the debate."""
        self.clock.advance_ms(elapsed_ms - self.machine.elapsed_ms)

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

    def speaking_seats(self, stances):
        """The seats this scenario gave a stance to; a shorter list means fewer.

        Ticket 03 之後「七席全部公開開場」本身就是盲投直過的觸發條件，所以要
        測辯論輪的房間必須說得出「只有前 n 席發言」——否則場景在第七則開場那
        一刻就結束了。
        """
        return list(SEAT_IDS)[: len(stances)]

    def challenges_and_responses(self, stances):
        challenges, incoming = self.challenge_plan(stances)
        for challenge in challenges:
            self.machine.relay(challenge)
        for index, seat_id in enumerate(self.speaking_seats(stances)):
            challenge = incoming[seat_id][0]
            self.machine.relay(
                self.message(
                    seat_id,
                    "response",
                    "response",
                    responds_to=[challenge["message_id"]],
                    target_seat_id=challenge["seat_id"],
                    stance=stances[index],
                )
            )

    def challenge_plan(self, stances):
        """Pair opposing seats, or rotate the roster when the room agrees."""
        if len(set(stances)) == 1:
            return self.rotation_plan(stances)
        seats = self.speaking_seats(stances)
        challenges = []
        incoming = {seat_id: [] for seat_id in seats}
        for index, seat_id in enumerate(seats):
            target_index = next(
                other
                for other in range(len(seats))
                if other != index and stances[other] != stances[index]
            )
            target = SEAT_IDS[target_index]
            challenge = self.message(
                seat_id,
                "challenge",
                "challenge-to-{}".format(target),
                target_seat_id=target,
                target_claim="{}-position".format(target),
                stance=stances[index],
            )
            challenges.append(challenge)
            incoming[target].append(challenge)
        for target_index, target in enumerate(seats):
            if incoming[target]:
                continue
            author_index = next(
                other
                for other in range(len(seats))
                if other != target_index and stances[other] != stances[target_index]
            )
            author = SEAT_IDS[author_index]
            challenge = self.message(
                author,
                "challenge",
                "extra-challenge-to-{}".format(target),
                target_seat_id=target,
                target_claim="{}-position".format(target),
                stance=stances[author_index],
            )
            challenges.append(challenge)
            incoming[target].append(challenge)
        return challenges, incoming

    def rotation_plan(self, stances):
        """The devil's-advocate round: each seat challenges the next in roster order."""
        seats = self.speaking_seats(stances)
        challenges = []
        incoming = {seat_id: [] for seat_id in seats}
        for index, seat_id in enumerate(seats):
            target = seats[(index + 1) % len(seats)]
            challenge = self.message(
                seat_id,
                "challenge",
                "challenge-to-{}".format(target),
                target_seat_id=target,
                target_claim="{}-position".format(target),
                stance=stances[index],
            )
            challenges.append(challenge)
            incoming[target].append(challenge)
        return challenges, incoming

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
        challenges, incoming = self.challenge_plan(stances)
        messages.extend(challenges)
        for index, seat_id in enumerate(self.speaking_seats(stances)):
            challenge = incoming[seat_id][0]
            messages.append(
                self.message(
                    seat_id,
                    "response",
                    "response",
                    responds_to=[challenge["message_id"]],
                    target_seat_id=challenge["seat_id"],
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


class UnanimousBlindPassTest(unittest.TestCase):
    """Ticket 03：opening 盲投收齊後 7/7 同立場即停止，開場票就是最終票。

    這一輪席位互不可見（driver 的 opening 波傳空 transcript），所以七席各自
    獨立得出同一個結論就是本系統能拿到的最強共識；architecture §11.3 因此讓它
    取代 §5.4「即使全票仍須一輪反方挑戰」。門檻與時點都來自
    ``config/debate_rules.json``，不是程式裡的字面值。
    """

    def test_seven_agreeing_blind_openings_stop_the_debate_at_once(self):
        harness = DebateHarness(self)

        harness.positions(["bullish"] * 7)

        summary = harness.machine.summary()
        self.assertTrue(harness.machine.stopped)
        self.assertEqual("unanimous_blind_pass", harness.machine.stop_reason)
        self.assertEqual("consensus", summary["consensus_status"])
        self.assertEqual("bullish", summary["adopted_stance"])
        self.assertEqual(7, summary["valid_vote_count"])
        self.assertEqual(7, summary["threshold_required"])
        self.assertEqual({"bullish": 7, "bearish": 0, "neutral": 0}, summary["tally"])
        self.assertEqual([], summary["dissent"])
        self.assertTrue(summary["market_conclusion_allowed"])
        self.assertFalse(summary["red_no_conclusion"])
        # 直過就是沒有挑戰輪；votes.json 必須照實說，不得謊報已完成質詢。
        self.assertFalse(summary["challenge_completed"])

    def test_the_blind_openings_become_the_seven_final_votes(self):
        # 直過＝開場即定案：每一席的最終票就是它自己那則開場原文，逐欄相等。
        harness = DebateHarness(self)

        harness.positions(["bullish"] * 7)

        for row in harness.machine.vote_table():
            with self.subTest(seat_id=row["seat_id"]):
                self.assertEqual("valid", row["state"])
                self.assertEqual("bullish", row["initial_stance"])
                self.assertEqual("bullish", row["final_stance"])
                self.assertEqual(
                    row["initial_public_reason"], row["final_public_reason"]
                )
                self.assertEqual(
                    row["initial_evidence_ids"], row["final_evidence_ids"]
                )
                self.assertFalse(row["stance_changed"])
                self.assertIsNone(row["stance_change_reason"])
                # 沒有人改票，vote_changes 就必須是空的；也只講過一句話。
                self.assertEqual([], row["vote_changes"])
                self.assertEqual(
                    ["{}-position".format(row["seat_id"])], row["message_ids"]
                )

    def test_the_persisted_record_holds_only_the_seven_openings(self):
        harness = DebateHarness(self)

        harness.positions(["bullish"] * 7)
        summary = harness.machine.persist()

        debate = [
            json.loads(line)
            for line in (harness.run.path / "debate.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        seat_messages = [
            entry for entry in debate if entry["event"] == "seat_message"
        ]
        self.assertEqual({"position"}, {entry["kind"] for entry in seat_messages})
        self.assertEqual(len(SEAT_IDS), len(seat_messages))
        self.assertEqual(
            set(SEAT_IDS), {entry["seat_id"] for entry in seat_messages}
        )
        votes = json.loads(
            (harness.run.path / "votes.json").read_text(encoding="utf-8")
        )
        self.assertEqual(summary, votes)
        self.assertEqual("unanimous_blind_pass", votes["stop_reason"])
        self.assertTrue(harness.machine.verify_public_history())

    def test_a_message_arriving_after_a_blind_pass_is_rejected_as_late(self):
        harness = DebateHarness(self)
        harness.positions(["bullish"] * 7)

        with self.assertRaises(LateMessageError):
            harness.machine.relay(
                harness.message(
                    SEAT_IDS[0],
                    "challenge",
                    "after-blind-pass",
                    stance="bullish",
                    target_seat_id=SEAT_IDS[1],
                    target_claim="{}-position".format(SEAT_IDS[1]),
                )
            )

    def test_a_six_one_blind_opening_keeps_the_ordinary_debate_rules(self):
        """驗收條件二在狀態機這一層：6/1 盲投照常進辯論，行為與現制一致。"""
        harness = DebateHarness(self)
        stances = ["bullish"] * 6 + ["bearish"]

        harness.positions(stances)

        self.assertFalse(harness.machine.stopped)
        self.assertIsNone(harness.machine.stop_reason)
        self.assertEqual(0, len(harness.machine.valid_votes()))

        harness.challenges_and_responses(stances)
        harness.finals(stances, count=6)
        harness.advance_to(SECOND_ROUND_OPEN_MS)
        harness.machine.tick()

        self.assertEqual("consensus_6_votes", harness.machine.stop_reason)
        self.assertEqual(6, harness.machine.summary()["threshold_required"])

    def test_a_room_still_missing_one_opening_does_not_pass_blind(self):
        # 「全席發布後」是硬條件：六席一致但第七席沒交卷，不算盲投直過。
        harness = DebateHarness(self)

        harness.positions(["bullish"] * 6)

        self.assertEqual({"bullish"}, harness.machine.opening_stances())
        self.assertFalse(harness.machine.stopped)

    def test_the_first_round_wall_is_the_last_instant_a_blind_pass_may_fire(self):
        # 時點來自設定檔的第一輪牆。牆上仍算開場階段，牆後就是辯論場了。
        on_time = DebateHarness(self, run_id="{}-on-the-wall".format(RUN_ID))
        on_time.advance_to(FIRST_ROUND_OPEN_MS)
        on_time.positions(["bullish"] * 7)

        self.assertEqual("unanimous_blind_pass", on_time.machine.stop_reason)
        self.assertEqual(FIRST_ROUND_OPEN_MS, on_time.machine.stop_elapsed_ms)

        late = DebateHarness(self, run_id="{}-past-the-wall".format(RUN_ID))
        late.advance_to(FIRST_ROUND_OPEN_MS + 1)

        with self.assertRaises(DebateLifecycleError):
            late.machine.relay(
                late.message(
                    SEAT_IDS[0], "position", "late-position", stance="bullish", round=0
                )
            )

    def test_a_room_that_already_debated_can_no_longer_pass_blind(self):
        """盲的定義：只要有人講過開場以外的話，這一場就不再是盲投。"""
        harness = DebateHarness(self)
        harness.positions(["bullish"] * 6)
        harness.machine.relay(
            harness.message(
                SEAT_IDS[0],
                "challenge",
                "scrutiny",
                stance="bullish",
                target_seat_id=SEAT_IDS[1],
                target_claim="{}-position".format(SEAT_IDS[1]),
            )
        )
        self.assertTrue(harness.machine.debate_rounds_started)

        harness.machine.relay(
            harness.message(
                SEAT_IDS[6], "position", "position", stance="bullish", round=0
            )
        )

        # 七席開場全數就位、而且全部同立場——唯一擋下直過的就是那則挑戰。
        self.assertEqual(
            {"bullish": 7, "bearish": 0, "neutral": 0},
            harness.machine.opening_tally(),
        )
        self.assertFalse(harness.machine.stopped)

    def test_a_lower_first_wall_threshold_does_not_turn_six_one_into_a_blind_pass(self):
        # 盲投直過固定要求七席同立場；較低門檻只在牆上開票時生效。
        current = debate_rules()
        rules = replace(
            current,
            vote_rounds=tuple(
                VoteRound(vote_round.open_offset_ms, 6 - index)
                for index, vote_round in enumerate(current.vote_rounds)
            ),
        )
        harness = DebateHarness(self, rules=rules)

        harness.positions(["bullish"] * 6 + ["bearish"])

        self.assertFalse(harness.machine.stopped)
        harness.advance_to(
            harness.debate_start_ms + rules.vote_rounds[0].open_offset_ms
        )
        harness.machine.tick()

        summary = harness.machine.summary()
        self.assertEqual("consensus_6_votes", harness.machine.stop_reason)
        self.assertEqual("bullish", summary["adopted_stance"])
        self.assertEqual(6, summary["threshold_required"])
        self.assertEqual({"bullish": 6, "bearish": 1, "neutral": 0}, summary["tally"])
        # 反對的那一席仍然是有效票，也仍然被記成異議，不會被抹掉。
        self.assertEqual(7, summary["valid_vote_count"])
        self.assertEqual(
            [{"seat_id": SEAT_IDS[6], "stance": "bearish",
              "public_reason": "public reason from counter-evidence"}],
            summary["dissent"],
        )


class DebateStateMachineTest(unittest.TestCase):
    def test_pre_wall_vote_table_does_not_leak_a_provisional_final_vote(self):
        harness = DebateHarness(self)
        harness.positions(["bullish"] * 4 + ["bearish"] * 3)
        harness.machine.relay(
            harness.message(
                SEAT_IDS[4],
                "final_vote",
                "provisional-change",
                stance="bullish",
                stance_change_reason="尚未開票的改票",
            )
        )

        row = harness.machine.vote_table()[4]
        self.assertEqual("provisional", row["state"])
        self.assertIsNone(row["final_stance"])
        self.assertEqual(0, harness.machine.summary()["valid_vote_count"])

    def test_public_debate_messages_are_appended_to_live_event_stream(self):
        harness = DebateHarness(self)
        message = harness.message(
            SEAT_IDS[0],
            "position",
            "position",
            stance="bullish",
            round=0,
        )

        harness.machine.relay(message)

        events = [
            json.loads(line)
            for line in (harness.run.path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual("debate_opened", events[0]["event"])
        self.assertEqual("seat_message", events[-1]["event"])
        self.assertEqual(SEAT_IDS[0], events[-1]["seat_id"])

    def test_scripted_gateway_runs_without_sleep_and_persists_consensus(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 7
        harness.machine.gateway = ScriptedDebateGateway(
            [
                harness.message(
                    seat_id,
                    "position",
                    "position",
                    stance=stance,
                    round=0,
                )
                for seat_id, stance in zip(SEAT_IDS, stances)
            ],
            harness.clock,
        )

        summary = harness.machine.run_debate()

        self.assertEqual("unanimous_blind_pass", summary["stop_reason"])
        self.assertTrue((harness.run.path / "debate.jsonl").is_file())
        self.assertTrue((harness.run.path / "votes.json").is_file())

    def test_opening_votes_remain_provisional_until_the_first_round_wall(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 6 + ["bearish"]

        harness.positions(stances)

        self.assertFalse(harness.machine.stopped)
        self.assertEqual(0, len(harness.machine.valid_votes()))
        harness.machine.relay(
            harness.message(
                SEAT_IDS[0], "final_vote", "latest-public-stance", stance="bullish"
            )
        )
        self.assertEqual(0, len(harness.machine.valid_votes()))

        harness.advance_to(FIRST_ROUND_OPEN_MS)
        harness.machine.tick()
        self.assertEqual(7, len(harness.machine.valid_votes()))

    def test_response_cannot_claim_a_challenge_addressed_to_another_seat(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 6 + ["bearish"]
        harness.positions(stances)
        challenge = harness.message(
            SEAT_IDS[0],
            "challenge",
            "to-minority",
            stance="bullish",
            target_seat_id=SEAT_IDS[6],
            target_claim="{}-position".format(SEAT_IDS[6]),
        )
        harness.machine.relay(challenge)

        with self.assertRaises(DebateLifecycleError):
            harness.machine.relay(
                harness.message(
                    SEAT_IDS[1],
                    "response",
                    "wrong-recipient",
                    stance="bullish",
                    target_seat_id=SEAT_IDS[0],
                    responds_to=[challenge["message_id"]],
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
        harness.advance_to(
            harness.debate_start_ms + harness.rules.vote_rounds[0].open_offset_ms
        )
        harness.machine.tick()
        table = {row["seat_id"]: row for row in harness.machine.vote_table()}

        changed = table[SEAT_IDS[0]]["vote_changes"][-1]
        self.assertEqual("bullish", changed["before"])
        self.assertEqual("bearish", changed["after"])
        self.assertEqual("counter evidence changed my vote", changed["reason"])
        unchanged = table[SEAT_IDS[1]]
        self.assertFalse(unchanged["stance_changed"])
        self.assertEqual("public reason from derivatives", unchanged["final_public_reason"])

    def test_later_unchanged_vote_keeps_the_reason_for_an_earlier_change(self):
        harness = DebateHarness(self)
        initial = ["bullish", "bullish", "bullish", "bearish", "bearish", "neutral", "neutral"]
        changed = list(initial)
        changed[0] = "bearish"
        reason = "counter evidence changed my vote"

        harness.complete_round(
            initial,
            changed,
            changed_reasons={SEAT_IDS[0]: reason},
        )
        harness.advance_to(FIRST_ROUND_OPEN_MS)
        harness.machine.tick()
        harness.advance_to(SECOND_ROUND_OPEN_MS - 1)
        harness.machine.relay(
            harness.message(
                SEAT_IDS[0],
                "final_vote",
                "maintained-after-change",
                stance="bearish",
                round=1,
            )
        )
        harness.advance_to(SECOND_ROUND_OPEN_MS)
        harness.machine.tick()

        row = {item["seat_id"]: item for item in harness.machine.vote_table()}[SEAT_IDS[0]]
        self.assertTrue(row["stance_changed"])
        self.assertEqual(reason, row["stance_change_reason"])
        self.assertEqual(reason, row["vote_changes"][0]["reason"])
        self.assertIsNone(row["vote_changes"][-1]["reason"])

    def test_change_after_the_official_ballot_does_not_replace_its_reason(self):
        harness = DebateHarness(self)
        initial = ["bullish", "bullish", "bullish", "bearish", "bearish", "neutral", "neutral"]
        first_votes = list(initial)
        first_votes[0] = "bearish"
        official_reason = "first ballot evidence changed my vote"

        harness.complete_round(
            initial,
            first_votes,
            changed_reasons={SEAT_IDS[0]: official_reason},
        )
        harness.advance_to(FIRST_ROUND_OPEN_MS)
        harness.machine.tick()
        harness.advance_to(SECOND_ROUND_OPEN_MS - 1)
        harness.machine.relay(
            harness.message(
                SEAT_IDS[0],
                "final_vote",
                "changed-after-official-ballot",
                stance="bullish",
                round=1,
                stance_change_reason="later evidence changed it back",
            )
        )

        row = {item["seat_id"]: item for item in harness.machine.vote_table()}[SEAT_IDS[0]]
        self.assertEqual("bearish", row["final_stance"])
        self.assertTrue(row["stance_changed"])
        self.assertEqual(official_reason, row["stance_change_reason"])

    def test_return_to_the_initial_stance_keeps_history_but_has_no_top_level_reason(self):
        harness = DebateHarness(self)
        initial = ["bullish", "bullish", "bullish", "bearish", "bearish", "neutral", "neutral"]
        first_votes = list(initial)
        first_votes[0] = "bearish"

        harness.complete_round(
            initial,
            first_votes,
            changed_reasons={SEAT_IDS[0]: "first ballot evidence changed my vote"},
        )
        harness.advance_to(FIRST_ROUND_OPEN_MS)
        harness.machine.tick()
        harness.advance_to(SECOND_ROUND_OPEN_MS - 1)
        harness.machine.relay(
            harness.message(
                SEAT_IDS[0],
                "final_vote",
                "returned-to-initial",
                stance="bullish",
                round=1,
                stance_change_reason="later evidence changed it back",
            )
        )
        harness.advance_to(SECOND_ROUND_OPEN_MS)
        harness.machine.tick()

        row = {item["seat_id"]: item for item in harness.machine.vote_table()}[SEAT_IDS[0]]
        self.assertEqual("bullish", row["final_stance"])
        self.assertFalse(row["stance_changed"])
        self.assertIsNone(row["stance_change_reason"])
        self.assertEqual(2, len(row["vote_changes"]))
        self.assertEqual(
            ["first ballot evidence changed my vote", "later evidence changed it back"],
            [change["reason"] for change in row["vote_changes"]],
        )

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
        harness.advance_to(FORCE_STOP_MS)
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
                        harness.message(
                            SEAT_IDS[1],
                            "position",
                            "invalid",
                            stance=invalid,
                            round=0,
                        )
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
        fresh.advance_to(FINAL_ROUND_END_MS + 1)
        with self.assertRaises(LateMessageError):
            fresh.machine.relay(
                fresh.message(
                    SEAT_IDS[0], "position", "after-final-window", stance="bullish", round=0
                )
            )

    def test_a_debate_start_override_must_match_the_run_s_own_seal(self):
        """Ticket R7: 比較題晚 30 秒封存，辯論起點只認該 run 實際 seal。"""
        harness = DebateHarness(self, run_id="{}-late-seal".format(RUN_ID))

        with self.assertRaises(EvidenceSnapshotMismatchError):
            DebateStateMachine(
                run=harness.run,
                clock=harness.clock,
                gateway=None,
                question_type="two_asset_comparison",
                evidence_records=harness.evidence,
                evidence_snapshot_sha256=harness.snapshot_sha,
                start_monotonic_ms=0,
                debate_start_ms=270_000,
            )

    def test_a_matching_debate_start_override_opens_the_room_at_that_instant(self):
        harness = DebateHarness(self, run_id="{}-matching-seal".format(RUN_ID))
        harness.advance_to(DEBATE_START_MS)

        machine = DebateStateMachine(
            run=harness.run,
            clock=harness.clock,
            gateway=None,
            question_type="single_asset_market_state",
            evidence_records=harness.evidence,
            evidence_snapshot_sha256=harness.snapshot_sha,
            start_monotonic_ms=0,
            debate_start_ms=DEBATE_START_MS,
        )

        self.assertEqual(DEBATE_START_MS, machine.debate_start_ms)

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
        harness.advance_to(
            harness.debate_start_ms + harness.rules.vote_rounds[0].open_offset_ms
        )
        harness.machine.tick()

        row = harness.machine.vote_table()[0]
        self.assertEqual(["spot-technical-a1", "spot-technical-a2"], row["attempt_ids"])
        self.assertEqual(7, len(harness.machine.vote_table()))
        self.assertEqual(7, len(harness.machine.valid_votes()))
        self.assertTrue(harness.machine.verify_public_history())

    def test_a_unanimous_room_may_scrutinise_any_other_seat(self):
        """Ticket R8: 全場一致是最強共識，不是流局；第一輪改判證據品質。

        Ticket 03 之後，七席全部發布開場會先觸發盲投直過，所以魔鬼代言人輪的
        場景是「還有一席沒發言」的一致房間——那正是 6 票以下維持原規則的情形。
        """
        harness = DebateHarness(self, "two_asset_comparison")
        harness.positions(["asset_a_stronger"] * 6)
        self.assertFalse(harness.machine.stopped)

        entry = harness.machine.relay(
            harness.message(
                SEAT_IDS[0],
                "challenge",
                "scrutiny",
                stance="asset_a_stronger",
                target_seat_id=SEAT_IDS[1],
                target_claim="{}-position".format(SEAT_IDS[1]),
            )
        )

        self.assertEqual("scrutiny", entry["challenge_mode"])

    def test_a_unanimous_room_still_refuses_a_seat_challenging_itself(self):
        harness = DebateHarness(self, "two_asset_comparison")
        harness.positions(["asset_a_stronger"] * 6)
        self.assertFalse(harness.machine.stopped)

        # 指名錯誤原因：LateMessageError 也是 DebateError，只斷言基底類別的話，
        # 「房間早就停了」會冒充成「自我挑戰被擋下」。
        with self.assertRaisesRegex(DebateError, "第一輪挑戰必須針對相反立場"):
            harness.machine.relay(
                harness.message(
                    SEAT_IDS[0],
                    "challenge",
                    "self-challenge",
                    stance="asset_a_stronger",
                    target_seat_id=SEAT_IDS[0],
                    target_claim="{}-position".format(SEAT_IDS[0]),
                )
            )

    def test_a_room_holding_two_stances_still_demands_an_opposing_target(self):
        harness = DebateHarness(self)
        harness.positions(["bullish"] * 6 + ["bearish"])

        with self.assertRaises(DebateError):
            harness.machine.relay(
                harness.message(
                    SEAT_IDS[0],
                    "challenge",
                    "same-stance",
                    stance="bullish",
                    target_seat_id=SEAT_IDS[1],
                    target_claim="{}-position".format(SEAT_IDS[1]),
                )
            )
        entry = harness.machine.relay(
            harness.message(
                SEAT_IDS[0],
                "challenge",
                "opposing",
                stance="bullish",
                target_seat_id=SEAT_IDS[6],
                target_claim="{}-position".format(SEAT_IDS[6]),
            )
        )

        self.assertEqual("opposing", entry["challenge_mode"])

    def test_a_unanimous_room_completes_its_round_and_reaches_six_votes(self):
        # 六席一致、第七席沒發言：直過不成立（未全席發布），照常跑挑戰輪。
        harness = DebateHarness(self, "two_asset_comparison")

        harness.complete_round(["asset_a_stronger"] * 6, count=6)
        harness.advance_to(
            harness.debate_start_ms + harness.rules.vote_rounds[1].open_offset_ms
        )
        harness.machine.tick()

        self.assertEqual("consensus_6_votes", harness.machine.stop_reason)
        self.assertEqual(6, len(harness.machine.valid_votes()))
        self.assertTrue(harness.machine.summary()["challenge_completed"])

    def test_message_after_consensus_is_rejected_as_late(self):
        harness = DebateHarness(self)
        stances = ["bullish"] * 6 + ["bearish"]
        harness.complete_round(stances, count=6)
        harness.advance_to(SECOND_ROUND_OPEN_MS)
        harness.machine.tick()
        self.assertTrue(harness.machine.stopped)

        with self.assertRaises(LateMessageError):
            harness.machine.relay(
                harness.message(SEAT_IDS[6], "final_vote", "late", stance="bearish")
            )


if __name__ == "__main__":
    unittest.main()
