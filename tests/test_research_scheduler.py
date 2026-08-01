"""Observable five-minute research scheduling with no real waiting."""

import json
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents.contract_validator import validate_evidence_card
from hoya_market_agents.research_scheduler import MILESTONES_MS, ResearchScheduler
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
        self.advance_to(89_999)
        self.assertEqual("primary_only", self.scheduler.source_policy)

        self.advance_to(90_000)

        self.assertEqual("trusted_secondary_allowed", self.scheduler.source_policy)
        self.assertIn(
            "trusted_secondary_sources_enabled",
            [event["event"] for event in self.scheduler.events],
        )

    def test_unstarted_attempt_retries_same_model_at_thirty_seconds(self):
        self.runner.start_behaviors["news-a1"] = False
        self.scheduler.start()

        self.advance_to(30_000)

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

    def test_checkpoint_is_saved_and_passed_to_later_replacement(self):
        self.scheduler.start()
        self.runner.checkpoints["news-a1"] = {"public_claims": ["checkpoint"]}
        self.advance_to(150_000)
        self.advance_to(210_000)
        retry = self.scheduler.recovery.seats["news"].attempts[-1]

        replacement = self.scheduler.report_failure(
            retry.attempt_id, "process_error", "retry crashed"
        )

        self.assertEqual({"public_claims": ["checkpoint"]}, replacement.checkpoint)
        checkpoint_file = self.run.path / "agents" / "news" / "checkpoint-150000.json"
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
        self.advance_to(285_000)

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
        self.advance_to(300_000)
        before = dict(self.scheduler.seal)

        late = self.scheduler.submit_result(attempt.attempt_id, evidence_raw(attempt, "late"))

        self.assertEqual("late", late)
        self.assertEqual(before, self.run.verify_evidence_snapshot())
        self.assertEqual(1, before["record_count"])


if __name__ == "__main__":
    unittest.main()
