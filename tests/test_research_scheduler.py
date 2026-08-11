"""Observable research scheduling with no real waiting."""

import json
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents.contract_validator import validate_evidence_card
from hoya_market_agents.research_scheduler import (
    ACCEPT_RESULTS_UNTIL_MS,
    CHECKPOINT_MS,
    MILESTONES_MS,
    PRIMARY_ONLY_END_MS,
    REPLACEMENT_MS,
    SEAL_MS,
    START_RETRY_MS,
    WRAP_UP_WINDOW_MS,
    ResearchScheduler,
    research_deadlines,
)
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.seats import SEAT_IDS
from tests.fakes import FixedClock


RUN_ID = "20260801T073000Z-btc-ticket5"


class FakeProcessRunner:
    def __init__(self):
        self.start_behaviors = {}
        self.corrected_outputs = {}
        self.checkpoints = {}
        self.cancel_errors = {}
        self.terminate_errors = {}
        self.started = []
        self.cancelled = []
        self.terminated = []

    def start(self, attempt, checkpoint):
        self.started.append((attempt, checkpoint))
        behavior = self.start_behaviors.get(attempt.attempt_id, True)
        if isinstance(behavior, Exception):
            raise behavior
        return behavior

    def checkpoint(self, attempt_id):
        return self.checkpoints.get(attempt_id)

    def correct(self, attempt, raw_output, exact_error):
        value = self.corrected_outputs.get(attempt.attempt_id)
        if isinstance(value, Exception):
            raise value
        return value

    def cancel(self, attempt_id):
        self.cancelled.append(attempt_id)
        if attempt_id in self.cancel_errors:
            raise self.cancel_errors[attempt_id]

    def terminate(self, attempt_id):
        self.terminated.append(attempt_id)
        if attempt_id in self.terminate_errors:
            raise self.terminate_errors[attempt_id]


class JsonEvidenceGateway:
    def validate(self, attempt, raw_output):
        records = json.loads(raw_output)
        if not isinstance(records, list):
            raise ValueError("research payload 必須為陣列")
        for record in records:
            validate_evidence_card(record)
            if record["seat_id"] != attempt.seat_id:
                raise ValueError("seat_id mismatch")
            if record["attempt_id"] != attempt.attempt_id:
                raise ValueError("attempt_id mismatch")
        return records


class TrailingCommaRepairer:
    name = "fake-format-repair"

    def repair(self, attempt, raw_output, exact_error):
        return raw_output.replace(",]", "]")


class NoRepairer:
    name = "no-repair"

    def repair(self, attempt, raw_output, exact_error):
        return None


class SemanticRepairer:
    name = "invalid-semantic-repair"

    def __init__(self, replacement):
        self.replacement = replacement

    def repair(self, attempt, raw_output, exact_error):
        return self.replacement


def evidence_raw(attempt, suffix="01"):
    return json.dumps(
        [
            {
                "schema_version": "1.0.0",
                "evidence_id": "{}-{}".format(attempt.seat_id, suffix),
                "run_id": RUN_ID,
                "seat_id": attempt.seat_id,
                "attempt_id": attempt.attempt_id,
                "phase": "research",
                "created_at_utc": "2026-08-01T07:30:05Z",
                "elapsed_ms": 5_000,
                "asset": "BTC",
                "category": "news",
                "statement": "auditable fixture",
                "direction": "neutral",
                "source_url": "https://example.invalid/{}".format(suffix),
                "source_origin": "fixture:{}".format(suffix),
                "source_tier": 1,
                "published_at_utc": "2026-08-01T07:00:00Z",
                "retrieved_at_utc": "2026-08-01T07:30:05Z",
                "excerpt": "fixture value",
                "credibility_note": "deterministic test fixture",
            }
        ],
        ensure_ascii=False,
    )


def evidence_batch_raw(attempt, count, duplicate_ids=False, trailing_comma=False):
    template = json.loads(evidence_raw(attempt))[0]
    records = []
    for index in range(count):
        suffix = "01" if duplicate_ids else "{:02d}".format(index + 1)
        records.append(
            {
                **template,
                "evidence_id": "{}-{}".format(attempt.seat_id, suffix),
                "source_url": "https://example.invalid/{}".format(index + 1),
                "source_origin": "fixture:{}".format(index + 1),
            }
        )
    raw = json.dumps(records, ensure_ascii=False)
    return raw[:-1] + ",]" if trailing_comma else raw


class ResearchSchedulerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clock = FixedClock()
        self.runner = FakeProcessRunner()
        self.run = RunStore(Path(self._tmp.name)).create_run(RUN_ID, SEAT_IDS)
        primary = {seat_id: "primary-{}".format(seat_id) for seat_id in SEAT_IDS}
        replacement = {
            seat_id: "replacement-{}".format(seat_id) for seat_id in SEAT_IDS
        }
        self.scheduler = ResearchScheduler(
            run=self.run,
            clock=self.clock,
            gateway=JsonEvidenceGateway(),
            process_runner=self.runner,
            format_repairer=TrailingCommaRepairer(),
            primary_models=primary,
            replacement_models=replacement,
        )

    def advance_to(self, elapsed_ms):
        self.clock.advance_ms(elapsed_ms - self.scheduler.elapsed_ms)
        self.scheduler.tick()

    def test_the_research_phase_reserves_thirty_seconds_for_delivery(self):
        self.assertEqual(
            (0, 30_000, 90_000, 120_000, 155_000, 320_000, 350_000, 360_000),
            MILESTONES_MS,
        )
        self.assertEqual(30_000, START_RETRY_MS)
        self.assertEqual(90_000, PRIMARY_ONLY_END_MS)
        self.assertEqual(120_000, CHECKPOINT_MS)
        self.assertEqual(155_000, REPLACEMENT_MS)
        self.assertEqual(30_000, WRAP_UP_WINDOW_MS)
        self.assertEqual(350_000, ACCEPT_RESULTS_UNTIL_MS)
        self.assertEqual(360_000, SEAL_MS)

    def test_all_seven_start_at_zero_and_every_milestone_is_auditable(self):
        self.scheduler.start()
        for milestone in MILESTONES_MS[1:]:
            self.advance_to(milestone)

        starts = [event for event in self.scheduler.events if event["event"] == "attempt_started"]
        timeline = [
            event["elapsed_ms"]
            for event in self.scheduler.events
            if event["event"] == "milestone"
        ]
        self.assertEqual(7, len([event for event in starts if event["elapsed_ms"] == 0]))
        self.assertEqual(list(MILESTONES_MS), timeline)
        self.assertEqual("search_closed", self.scheduler.source_policy)
        self.assertIsNotNone(self.scheduler.seal)
        self.assertIn("search_hard_stopped", [event["event"] for event in self.scheduler.events])
        for event in self.scheduler.events:
            self.assertTrue(
                {"run_id", "seat_id", "attempt_id", "phase", "created_at_utc", "elapsed_ms"}
                <= set(event)
            )

    def test_secondary_sources_are_enabled_only_after_ninety_seconds(self):
        self.scheduler.start()
        self.advance_to(PRIMARY_ONLY_END_MS - 1)
        self.assertEqual("primary_only", self.scheduler.source_policy)

        self.advance_to(PRIMARY_ONLY_END_MS)

        self.assertEqual("trusted_secondary_allowed", self.scheduler.source_policy)
        self.assertIn(
            "trusted_secondary_sources_enabled",
            [event["event"] for event in self.scheduler.events],
        )

    def test_search_stop_starts_wrap_up_without_closing_result_intake(self):
        self.scheduler.start()
        attempt = self.scheduler.recovery.seats["news"].attempts[0]
        self.advance_to(self.scheduler.deadlines.search_stop_ms)

        self.assertEqual("wrap_up_only", self.scheduler.source_policy)
        self.assertEqual(
            "adopted", self.scheduler.submit_result(attempt.attempt_id, evidence_raw(attempt))
        )
        self.assertIn(
            "research_wrap_up_started",
            [event["event"] for event in self.scheduler.events],
        )

    def test_unstarted_attempt_retries_same_model_at_thirty_seconds(self):
        self.runner.start_behaviors["news-a1"] = False
        self.scheduler.start()

        self.advance_to(START_RETRY_MS)

        attempts = self.scheduler.recovery.seats["news"].attempts
        self.assertEqual(["news-a1", "news-a2"], [item.attempt_id for item in attempts])
        self.assertEqual(attempts[0].model, attempts[1].model)
        self.assertEqual("same_model_retry", attempts[1].kind)

    def test_explicit_startup_errors_retry_then_replace_immediately(self):
        self.runner.start_behaviors.update(
            {
                "news-a1": RuntimeError("provider unavailable"),
                "news-a2": RuntimeError("provider still unavailable"),
            }
        )

        self.scheduler.start()

        attempts = self.scheduler.recovery.seats["news"].attempts
        self.assertEqual(
            ["primary", "same_model_retry", "cross_model_replacement"],
            [item.kind for item in attempts],
        )
        self.assertNotEqual(attempts[1].model, attempts[2].model)
        self.assertEqual(
            2,
            len([event for event in self.scheduler.events if event["event"] == "startup_error"]),
        )

    def test_timeout_uses_same_model_then_cross_model_with_lineage(self):
        self.scheduler.start()
        primary = self.scheduler.recovery.seats["news"].attempts[0]

        retry = self.scheduler.report_failure(primary.attempt_id, "timeout", "30s idle")
        replacement = self.scheduler.report_failure(
            retry.attempt_id, "process_error", "process exited 9"
        )

        self.assertEqual(primary.model, retry.model)
        self.assertNotEqual(retry.model, replacement.model)
        self.assertEqual(primary.attempt_id, replacement.original_attempt_id)
        self.assertEqual(retry.attempt_id, replacement.parent_attempt_id)
        names = [event["event"] for event in self.scheduler.events]
        self.assertIn("timeout", names)
        self.assertIn("process_error", names)

    def test_provider_error_is_audited_and_retried_immediately(self):
        self.scheduler.start()
        primary = self.scheduler.recovery.seats["news"].attempts[0]

        retry = self.scheduler.report_failure(
            primary.attempt_id, "provider_error", "provider returned 503"
        )

        self.assertEqual("same_model_retry", retry.kind)
        event = [item for item in self.scheduler.events if item["event"] == "provider_error"]
        self.assertEqual(0, event[0]["elapsed_ms"])
        self.assertEqual("provider returned 503", event[0]["error"])

    def test_failure_callback_syncs_cutoff_before_any_recovery(self):
        self.scheduler.start()
        primary = self.scheduler.recovery.seats["news"].attempts[0]
        self.clock.advance_ms(SEAL_MS)

        result = self.scheduler.report_failure(
            primary.attempt_id, "timeout", "arrived after research deadline"
        )

        self.assertIsNone(result)
        self.assertEqual(
            ["news-a1"],
            [item.attempt_id for item in self.scheduler.recovery.seats["news"].attempts],
        )
        self.assertIsNotNone(self.scheduler.seal)
        names = [event["event"] for event in self.scheduler.events]
        self.assertIn("failure_ignored_after_cutoff", names)
        self.assertNotIn(
            SEAL_MS,
            [
                event["elapsed_ms"]
                for event in self.scheduler.events
                if event["event"] == "attempt_launch_requested"
            ],
        )

    def test_result_callback_syncs_seal_and_discards_without_prior_tick(self):
        self.scheduler.start()
        primary = self.scheduler.recovery.seats["news"].attempts[0]
        self.clock.advance_ms(SEAL_MS)

        result = self.scheduler.submit_result(primary.attempt_id, evidence_raw(primary))

        self.assertEqual("late", result)
        self.assertIsNotNone(self.scheduler.seal)
        self.assertFalse(self.scheduler.adopted_records)
        self.assertEqual(
            ["news-a1"],
            [item.attempt_id for item in self.scheduler.recovery.seats["news"].attempts],
        )

    def test_checkpoint_is_saved_and_passed_to_later_replacement(self):
        self.scheduler.start()
        self.runner.checkpoints["news-a1"] = {"public_claims": ["checkpoint"]}
        self.advance_to(CHECKPOINT_MS)
        self.advance_to(REPLACEMENT_MS)
        retry = self.scheduler.recovery.seats["news"].attempts[-1]

        replacement = self.scheduler.report_failure(
            retry.attempt_id, "process_error", "retry crashed"
        )

        self.assertEqual({"public_claims": ["checkpoint"]}, replacement.checkpoint)
        checkpoint_file = self.run.path / "agents" / "news" / "checkpoint-{}.json".format(CHECKPOINT_MS)
        self.assertEqual(
            "news-a1", json.loads(checkpoint_file.read_text(encoding="utf-8"))["attempt_id"]
        )

    def test_first_valid_attempt_wins_and_other_success_is_diagnostic(self):
        self.scheduler.start()
        primary = self.scheduler.recovery.seats["news"].attempts[0]
        retry = self.scheduler.report_failure(primary.attempt_id, "timeout", "slow")

        first = self.scheduler.submit_result(retry.attempt_id, evidence_raw(retry, "retry"))
        second = self.scheduler.submit_result(primary.attempt_id, evidence_raw(primary, "primary"))

        self.assertEqual("adopted", first)
        self.assertEqual("diagnostic", second)
        adopted = json.loads(
            (self.run.path / "agents" / "news" / "adopted.json").read_text(encoding="utf-8")
        )
        self.assertEqual(retry.attempt_id, adopted["attempt_id"])
        self.assertTrue(
            (self.run.path / "diagnostics" / "attempts" / "news-a1.json").is_file()
        )

    def test_malformed_output_gets_one_original_correction_then_succeeds(self):
        self.scheduler.start()
        attempt = self.scheduler.recovery.seats["news"].attempts[0]
        self.runner.corrected_outputs[attempt.attempt_id] = evidence_raw(attempt)

        result = self.scheduler.submit_result(attempt.attempt_id, "not-json")

        self.assertEqual("adopted", result)
        names = [event["event"] for event in self.scheduler.events]
        self.assertEqual(1, names.count("original_format_correction_requested"))
        self.assertIn("original_format_correction_valid", names)

    def test_format_repair_is_non_voting_audited_and_format_only(self):
        self.scheduler.start()
        attempt = self.scheduler.recovery.seats["news"].attempts[0]
        malformed = evidence_raw(attempt)[:-1] + ",]"

        result = self.scheduler.submit_result(attempt.attempt_id, malformed)

        self.assertEqual("adopted", result)
        audit = self.run.path / "diagnostics" / "format-repairs" / "news-a1.json"
        payload = json.loads(audit.read_text(encoding="utf-8"))
        self.assertEqual("fake-format-repair", payload["operator"])
        self.assertNotEqual(payload["before_sha256"], payload["after_sha256"])

    def test_unrepairable_output_triggers_recovery(self):
        self.scheduler.format_repairer = NoRepairer()
        self.scheduler.start()
        attempt = self.scheduler.recovery.seats["news"].attempts[0]

        result = self.scheduler.submit_result(attempt.attempt_id, "not-json")

        self.assertEqual("unrepairable", result)
        self.assertEqual(
            "same_model_retry", self.scheduler.recovery.seats["news"].attempts[-1].kind
        )

    def test_nine_cards_are_malformed_and_recovered_not_raised(self):
        self.scheduler.format_repairer = NoRepairer()
        self.scheduler.start()
        attempt = self.scheduler.recovery.seats["news"].attempts[0]

        result = self.scheduler.submit_result(
            attempt.attempt_id, evidence_batch_raw(attempt, 9)
        )

        self.assertEqual("unrepairable", result)
        self.assertIn("malformed_output", [event["event"] for event in self.scheduler.events])
        self.assertEqual(
            "same_model_retry", self.scheduler.recovery.seats["news"].attempts[-1].kind
        )

    def test_duplicate_evidence_ids_are_malformed_and_recovered_not_raised(self):
        self.scheduler.format_repairer = NoRepairer()
        self.scheduler.start()
        attempt = self.scheduler.recovery.seats["news"].attempts[0]

        result = self.scheduler.submit_result(
            attempt.attempt_id,
            evidence_batch_raw(attempt, 2, duplicate_ids=True),
        )

        self.assertEqual("unrepairable", result)
        malformed = [
            event for event in self.scheduler.events if event["event"] == "malformed_output"
        ]
        self.assertIn("重複", malformed[0]["error"])

    def test_original_correction_also_enforces_whole_seat_contract(self):
        self.scheduler.format_repairer = NoRepairer()
        self.scheduler.start()
        attempt = self.scheduler.recovery.seats["news"].attempts[0]
        self.runner.corrected_outputs[attempt.attempt_id] = evidence_batch_raw(attempt, 9)

        result = self.scheduler.submit_result(attempt.attempt_id, "not-json")

        self.assertEqual("unrepairable", result)
        self.assertIn(
            "original_format_correction_invalid",
            [event["event"] for event in self.scheduler.events],
        )

    def test_format_repair_also_enforces_whole_seat_contract(self):
        self.scheduler.start()
        attempt = self.scheduler.recovery.seats["news"].attempts[0]

        result = self.scheduler.submit_result(
            attempt.attempt_id,
            evidence_batch_raw(attempt, 9, trailing_comma=True),
        )

        self.assertEqual("unrepairable", result)
        self.assertIn(
            "format_repair_invalid",
            [event["event"] for event in self.scheduler.events],
        )

    def test_format_repair_cannot_change_market_content(self):
        self.scheduler.start()
        attempt = self.scheduler.recovery.seats["news"].attempts[0]
        self.scheduler.format_repairer = SemanticRepairer(evidence_raw(attempt))

        result = self.scheduler.submit_result(attempt.attempt_id, "not-json")

        self.assertEqual("unrepairable", result)
        self.assertIn(
            "format_repair_rejected_semantic_change",
            [event["event"] for event in self.scheduler.events],
        )

    def test_cutoff_discards_late_result_and_audits_cancel_errors(self):
        self.scheduler.start()
        attempt = self.scheduler.recovery.seats["news"].attempts[0]
        self.runner.cancel_errors[attempt.attempt_id] = RuntimeError("cancel failed")
        self.runner.terminate_errors[attempt.attempt_id] = RuntimeError("terminate failed")
        self.advance_to(ACCEPT_RESULTS_UNTIL_MS)

        result = self.scheduler.submit_result(attempt.attempt_id, evidence_raw(attempt))

        self.assertEqual("late", result)
        self.assertFalse(self.scheduler.adopted_records)
        names = [event["event"] for event in self.scheduler.events]
        self.assertIn("late_result_discarded", names)
        self.assertIn("process_cancel_error", names)
        self.assertIn("process_terminate_error", names)
        self.assertTrue(any((self.run.path / "late").iterdir()))

    def test_snapshot_seal_is_unchanged_by_post_seal_result(self):
        self.scheduler.start()
        attempt = self.scheduler.recovery.seats["news"].attempts[0]
        self.assertEqual("adopted", self.scheduler.submit_result(attempt.attempt_id, evidence_raw(attempt)))
        self.advance_to(SEAL_MS)
        before = dict(self.scheduler.seal)

        late = self.scheduler.submit_result(attempt.attempt_id, evidence_raw(attempt, "late"))

        self.assertEqual("late", late)
        self.assertEqual(before, self.run.verify_evidence_snapshot())
        self.assertEqual(1, before["record_count"])


class ResearchDeadlinesTest(unittest.TestCase):
    """Ticket R7: one authority decides每一種題型的收件牆與封存時刻。"""

    def test_single_asset_event_overall_and_open_use_the_six_minute_seal(self):
        for question_type in (
            "single_asset_market_state",
            "overall_market_state",
            "event_impact",
            "open_proposition",
            "single_asset",
            "market",
            "event",
        ):
            with self.subTest(question_type=question_type):
                deadlines = research_deadlines(question_type)

                self.assertEqual(320_000, deadlines.search_stop_ms)
                self.assertEqual(350_000, deadlines.accept_until_ms)
                self.assertEqual(360_000, deadlines.seal_ms)

    def test_two_asset_comparison_gets_thirty_more_seconds(self):
        # 比較題維持比一般題多 30 秒，且同樣保留最後 30 秒交卷。
        for question_type in ("two_asset_comparison", "comparison"):
            with self.subTest(question_type=question_type):
                deadlines = research_deadlines(question_type)

                self.assertEqual(350_000, deadlines.search_stop_ms)
                self.assertEqual(380_000, deadlines.accept_until_ms)
                self.assertEqual(390_000, deadlines.seal_ms)

    def test_an_unrecorded_question_type_falls_back_to_the_tightest_schedule(self):
        self.assertEqual(research_deadlines(), research_deadlines(None))
        self.assertEqual(research_deadlines(), research_deadlines("not-a-question-type"))

    def test_only_question_specific_walls_move_and_deadlines_are_frozen(self):
        comparison = research_deadlines("two_asset_comparison")

        self.assertEqual(
            (0, 30_000, 90_000, 120_000, 155_000, 350_000, 380_000, 390_000),
            comparison.milestones_ms,
        )
        self.assertEqual(MILESTONES_MS, research_deadlines().milestones_ms)
        with self.assertRaises(Exception):
            comparison.seal_ms = 1


class ComparisonSchedulerTest(unittest.TestCase):
    """The same seven seats, driven by the comparison question's deadlines."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clock = FixedClock()
        self.runner = FakeProcessRunner()
        self.run = RunStore(Path(self._tmp.name)).create_run(RUN_ID, SEAT_IDS)
        self.deadlines = research_deadlines("two_asset_comparison")
        self.scheduler = ResearchScheduler(
            run=self.run,
            clock=self.clock,
            gateway=JsonEvidenceGateway(),
            process_runner=self.runner,
            format_repairer=TrailingCommaRepairer(),
            primary_models={seat_id: "primary-{}".format(seat_id) for seat_id in SEAT_IDS},
            replacement_models={
                seat_id: "replacement-{}".format(seat_id) for seat_id in SEAT_IDS
            },
            deadlines=self.deadlines,
        )

    def advance_to(self, elapsed_ms):
        self.clock.advance_ms(elapsed_ms - self.scheduler.elapsed_ms)
        self.scheduler.tick()

    def test_all_seven_start_at_zero_and_every_milestone_is_auditable(self):
        self.scheduler.start()
        for milestone in self.deadlines.milestones_ms[1:]:
            self.advance_to(milestone)

        timeline = [
            event["elapsed_ms"]
            for event in self.scheduler.events
            if event["event"] == "milestone"
        ]
        self.assertEqual(list(self.deadlines.milestones_ms), timeline)
        self.assertEqual(390_000, self.scheduler.seal["elapsed_ms"])

    def test_a_result_during_comparison_wrap_up_is_still_accepted(self):
        self.scheduler.start()
        attempt = self.scheduler.recovery.seats["news"].attempts[0]
        self.advance_to(350_000)

        self.assertEqual(
            "adopted", self.scheduler.submit_result(attempt.attempt_id, evidence_raw(attempt))
        )
        self.assertEqual("wrap_up_only", self.scheduler.source_policy)

    def test_the_receiving_wall_still_closes_ten_seconds_before_the_seal(self):
        self.scheduler.start()
        attempt = self.scheduler.recovery.seats["news"].attempts[0]
        self.advance_to(380_000)

        self.assertEqual(
            "late", self.scheduler.submit_result(attempt.attempt_id, evidence_raw(attempt))
        )
        self.assertIsNone(self.scheduler.seal)


if __name__ == "__main__":
    unittest.main()
