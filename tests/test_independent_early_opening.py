import json
import io
import hashlib
import queue
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.fakes import FixedClock

from hoya_market_agents.debate_driver import DebateDriver
from hoya_market_agents.launcher import _build_research_scheduler
from hoya_market_agents.question_package import build_question_package
from hoya_market_agents.real_provider import PROVIDER_MODELS
from hoya_market_agents.recovery_state_machine import ProviderCandidate
from hoya_market_agents.research_scheduler import ResearchScheduler, SEAL_MS
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.seats import SEAT_IDS


RUN_ID = "20260813T020000Z-btc-ticket04"
QUESTION = "BTC 過去 14 日的市場狀態如何？"
CARD_STAMP = "2026-08-13T02:00:01Z"


def evidence_card(seat_id, attempt_id, suffix="01"):
    return {
        "schema_version": "1.0.0",
        "evidence_id": "{}-{}".format(seat_id, suffix),
        "run_id": RUN_ID,
        "seat_id": seat_id,
        "attempt_id": attempt_id,
        "phase": "research",
        "created_at_utc": CARD_STAMP,
        "elapsed_ms": 1_000,
        "asset": "BTC",
        "category": seat_id,
        "statement": "Ticket 04 的離線證據。",
        "direction": "support",
        "source_url": "https://fake.invalid/{}/{}".format(seat_id, suffix),
        "source_origin": "ticket04-fixture",
        "source_tier": 1,
        "published_at_utc": CARD_STAMP,
        "retrieved_at_utc": CARD_STAMP,
        "excerpt": "fixture {}".format(suffix),
        "credibility_note": "只用於離線測試。",
    }


def envelope(attempt, cards=None):
    cards = cards or [evidence_card(attempt.seat_id, attempt.attempt_id)]
    return json.dumps(
        {"seat_id": attempt.seat_id, "evidence_cards": cards},
        ensure_ascii=False,
    )


def opening_payload(seat_id):
    return {
        "seat_id": seat_id,
        "stance": "bullish",
        "public_reason": "本席依據已採用證據提出獨立開場立場。",
        "evidence_ids": ["{}-01".format(seat_id)],
        "conflicting_evidence_ids": None,
        "uncertainty_reason": None,
        "change_trigger": None,
    }


def records_digest(records):
    text = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class JsonGateway:
    def validate(self, attempt, raw_output):
        payload = json.loads(raw_output)
        if payload["seat_id"] != attempt.seat_id:
            raise ValueError("seat mismatch")
        return payload["evidence_cards"]


class NoRepairer:
    def repair(self, attempt, raw_output, exact_error):
        return None


class RecordingRunner:
    def __init__(self, results, opening_reply=None):
        self.results = results
        self.opening_reply = opening_reply
        self.research_calls = []
        self.opening_calls = []
        self.cancelled = []

    def start(self, attempt, checkpoint):
        self.research_calls.append((attempt, checkpoint))
        return True

    def start_debate(self, dispatch):
        self.opening_calls.append(dispatch)
        if self.opening_reply == "failure":
            self.results.put(
                (
                    "debate_failure",
                    dispatch.dispatch_id,
                    "provider_timeout",
                    "SECRET stderr credential=do-not-persist",
                )
            )
        elif self.opening_reply == "success":
            self.results.put(
                (
                    "provider_lineage",
                    dispatch.seat_id,
                    {
                        "phase": "opening",
                        "seat_id": dispatch.seat_id,
                        "dispatch_id": dispatch.dispatch_id,
                        "research_attempt_id": dispatch.research_attempt_id,
                        "provider": dispatch.provider,
                        "requested_provider": dispatch.provider,
                        "requested_model": dispatch.requested_model,
                        "actual_model": dispatch.research_actual_model,
                        "adopted_evidence_sha256": dispatch.adopted_evidence_sha256,
                        "elapsed_ms": 9,
                    },
                )
            )
            self.results.put(
                (
                    "debate_result",
                    dispatch.dispatch_id,
                    json.dumps(opening_payload(dispatch.seat_id), ensure_ascii=False),
                )
            )
        return True

    def checkpoint(self, attempt_id):
        return None

    def correct(self, attempt, raw_output, exact_error):
        return None

    def cancel(self, dispatch_id):
        self.cancelled.append(dispatch_id)

    def terminate(self, dispatch_id):
        self.cancelled.append(dispatch_id)


class IndependentEarlyOpeningTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name) / "data"
        self.data_root.mkdir()
        self.clock = FixedClock()
        self.store = RunStore(self.data_root)
        self.run = self.store.create_run(RUN_ID, SEAT_IDS, question=QUESTION)
        self.package = build_question_package(QUESTION)
        self.results = queue.Queue()

    def build_flow(self, *, seats=("news",), opening_reply=None, callback=True):
        runner = RecordingRunner(self.results, opening_reply=opening_reply)
        driver = DebateDriver(
            run=self.run,
            clock=self.clock,
            runner=runner,
            results_queue=self.results,
            package=self.package,
            evidence_records=None,
            snapshot_sha256=None,
            start_monotonic_ms=0,
            started_at_utc=self.clock.utc_now(),
            sleeper=lambda seconds: None,
            err=io.StringIO(),
        )
        adoptions = []

        def adopted(result):
            adoptions.append(result)
            if callback:
                driver.start_early_opening(result)

        scheduler = ResearchScheduler(
            run=self.run,
            clock=self.clock,
            gateway=JsonGateway(),
            process_runner=runner,
            format_repairer=NoRepairer(),
            primary_models={seat_id: PROVIDER_MODELS["codex"] for seat_id in seats},
            replacement_models={seat_id: None for seat_id in seats},
            seat_ids=seats,
            seat_providers={seat_id: "codex" for seat_id in seats},
            backup_candidates={
                seat_id: ProviderCandidate("claude", PROVIDER_MODELS["claude"])
                for seat_id in seats
            },
            on_adopted=adopted,
        )
        scheduler.start()
        return runner, driver, scheduler, adoptions

    def drain_opening_messages(self, driver):
        while not self.results.empty():
            self.assertTrue(driver.accept_provider_message(self.results.get_nowait()))

    # -- one absolute Opening wall -----------------------------------------

    def test_a_late_adoption_gets_only_the_time_left_before_the_opening_wall(self):
        runner, driver, scheduler, _ = self.build_flow()
        attempt = scheduler.attempts["news-a1"]
        scheduler.record_lineage(attempt.attempt_id, "codex", "gpt-5.6-sol")
        self.clock.advance_ms(300_000)

        self.assertEqual(
            "adopted", scheduler.submit_result(attempt.attempt_id, envelope(attempt))
        )

        [dispatch] = runner.opening_calls
        wall = driver.turns["opening"].collect_until_ms
        self.assertEqual(300_000, dispatch.opening_started_elapsed_ms)
        # Not a fresh 17-minute grant measured from this adoption.
        self.assertEqual(wall, dispatch.opening_deadline_elapsed_ms)
        self.assertEqual((wall - 300_000) / 1_000.0, dispatch.timeout_seconds)

    def test_timeout_emitted_deadline_and_pending_wall_are_one_absolute_value(self):
        runner, driver, scheduler, _ = self.build_flow()
        attempt = scheduler.attempts["news-a1"]
        scheduler.record_lineage(attempt.attempt_id, "codex", "gpt-5.6-sol")

        self.assertEqual(
            "adopted", scheduler.submit_result(attempt.attempt_id, envelope(attempt))
        )

        [dispatch] = runner.opening_calls
        wall = driver.turns["opening"].collect_until_ms
        # The wall the await/cancel/terminate loop actually enforces.
        self.assertEqual(
            wall, driver.pending[dispatch.dispatch_id].turn.collect_until_ms
        )
        self.assertEqual(wall, dispatch.opening_deadline_elapsed_ms)
        self.assertEqual(wall / 1_000.0, dispatch.timeout_seconds)
        emitted = {
            event["opening_deadline_elapsed_ms"]
            for event in (
                json.loads(line)
                for line in (self.run.path / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            )
            if "opening_deadline_elapsed_ms" in event
        }
        self.assertEqual({wall}, emitted)

    def test_a_half_second_of_budget_is_not_rounded_up_past_the_wall(self):
        """500 ms left must stay 0.5 s.

        Rounding a sub-second remainder up to a one-second floor hands the
        provider a budget that ends past the absolute wall. The adapter takes
        float seconds, so the exact remainder is expressible.
        """
        _, driver, _, _ = self.build_flow()
        turn = driver.turns["opening"]
        self.clock.advance_ms(turn.collect_until_ms - 500)

        self.assertEqual(0.5, driver._timeout_seconds(turn))

        # 牆上與牆後不得再發明預算；既有 dispatch 守衛仍是 fail closed 的那一道。
        self.clock.advance_ms(500)
        self.assertEqual(0.0, driver._timeout_seconds(turn))

    def test_a_refusing_cancel_still_gets_its_terminate(self):
        """Cancel and terminate are independent best-effort reclaims.

        Sharing one ``try`` lets a refused cancel skip the terminate entirely,
        which leaves the provider process group running past the wall.
        """
        _, driver, _, _ = self.build_flow()

        class HalfBrokenRunner:
            def __init__(self):
                self.terminated = []

            def cancel(self, dispatch_id):
                raise RuntimeError("cancel refused")

            def terminate(self, dispatch_id):
                self.terminated.append(dispatch_id)

        stopper = HalfBrokenRunner()
        driver.runner = stopper

        driver._cancel("news-opening")

        self.assertEqual(["news-opening"], stopper.terminated)

    def test_a_refused_cancel_cannot_carry_an_opening_past_the_wall(self):
        runner, driver, scheduler, _ = self.build_flow()
        attempt = scheduler.attempts["news-a1"]
        scheduler.record_lineage(attempt.attempt_id, "codex", "gpt-5.6-sol")
        self.assertEqual(
            "adopted", scheduler.submit_result(attempt.attempt_id, envelope(attempt))
        )
        [dispatch] = runner.opening_calls
        wall = driver.turns["opening"].collect_until_ms

        class RefusingRunner:
            def cancel(self, dispatch_id):
                raise RuntimeError("cancel refused")

            def terminate(self, dispatch_id):
                raise RuntimeError("terminate refused")

        driver.runner = RefusingRunner()
        self.clock.advance_ms(wall)

        # The wall has passed; abandoning must complete despite the refusal.
        driver._abandon([dispatch.dispatch_id], "deadline_missed")

        self.assertNotIn(dispatch.dispatch_id, driver.pending)
        self.assertFalse(driver.start_early_opening(driver.opening_adoptions["news"]))
        self.assertEqual(1, len(runner.opening_calls))

    def test_adopted_edge_dispatches_one_independent_opening_immediately(self):
        runner, driver, scheduler, _ = self.build_flow(
            seats=("news", "official-events"), opening_reply="failure"
        )
        attempt = scheduler.attempts["news-a1"]
        scheduler.record_lineage(
            attempt.attempt_id,
            provider="codex",
            actual_model="gpt-5.6-sol",
        )
        research_output = envelope(attempt)
        self.clock.advance_ms(12_345)

        self.assertEqual("adopted", scheduler.submit_result(attempt.attempt_id, research_output))

        [dispatch] = runner.opening_calls
        self.assertEqual("opening", dispatch.phase)
        self.assertEqual("news-opening", dispatch.dispatch_id)
        self.assertEqual(attempt.attempt_id, dispatch.research_attempt_id)
        self.assertEqual(("codex", "gpt-5.6-sol"), (dispatch.provider, dispatch.requested_model))
        self.assertEqual("gpt-5.6-sol", dispatch.research_actual_model)
        # The Opening inherits the opening turn's own wall; it is not granted a
        # fresh budget measured from this adoption.
        wall = driver.turns["opening"].collect_until_ms
        self.assertEqual(12_345, dispatch.opening_started_elapsed_ms)
        self.assertEqual(wall, dispatch.opening_deadline_elapsed_ms)
        self.assertEqual((wall - 12_345) / 1_000.0, dispatch.timeout_seconds)
        self.assertNotEqual(research_output, dispatch.prompt)
        self.assertNotIn("evidence_cards", dispatch.schema.get("properties", {}))
        self.assertIn("news-01", dispatch.prompt)
        self.assertIn("開場", dispatch.prompt)

        # Mutation proof: removing the callback at the adopted edge makes the
        # first assertion above fail because no Opening invocation exists.
        self.assertEqual("diagnostic", scheduler.submit_result(attempt.attempt_id, research_output))
        self.assertEqual(1, len(runner.opening_calls))

        failed = scheduler.attempts["official-events-a1"]
        scheduler.report_failure(failed.attempt_id, "provider_timeout", "research failed")
        self.assertEqual(1, len(runner.opening_calls))

        self.drain_opening_messages(driver)
        self.assertEqual("adopted", scheduler.attempt_outcomes[attempt.attempt_id]["terminal_outcome"])
        persisted = (self.run.path / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"phase": "research"', persisted)
        self.assertIn('"phase": "opening"', persisted)
        artifacts = "\n".join(
            path.read_text(encoding="utf-8")
            for path in self.run.path.rglob("*")
            if path.is_file()
        )
        self.assertNotIn("SECRET", artifacts)
        self.assertNotIn("credential=", artifacts)
        self.assertNotIn(dispatch.prompt, artifacts)

    def test_backup_adoption_routes_opening_through_the_backup_lineage(self):
        runner, _, scheduler, _ = self.build_flow()
        primary = scheduler.attempts["news-a1"]

        backup = scheduler.report_failure(
            primary.attempt_id, "provider_timeout", "primary timed out"
        )
        self.assertEqual([], runner.opening_calls)
        scheduler.record_lineage(
            backup.attempt_id,
            provider="claude",
            actual_model="claude-opus-5",
        )
        cards = [
            evidence_card("news", backup.attempt_id, "01"),
            evidence_card("news", backup.attempt_id, "02"),
        ]

        self.assertEqual("adopted", scheduler.submit_result(backup.attempt_id, envelope(backup, cards)))

        [dispatch] = runner.opening_calls
        self.assertEqual(backup.attempt_id, dispatch.research_attempt_id)
        self.assertEqual("claude", dispatch.provider)
        self.assertEqual(PROVIDER_MODELS["claude"], dispatch.requested_model)
        self.assertEqual("claude-opus-5", dispatch.research_actual_model)
        for card in cards:
            self.assertIn(card["evidence_id"], dispatch.prompt)

    def test_unsealed_tampered_illegal_or_expired_seed_fails_closed(self):
        runner, driver, scheduler, adoptions = self.build_flow(callback=False)
        attempt = scheduler.attempts["news-a1"]
        scheduler.record_lineage(attempt.attempt_id, "codex", "gpt-5.6-sol")
        self.assertEqual("adopted", scheduler.submit_result(attempt.attempt_id, envelope(attempt)))
        [adoption] = adoptions

        self.assertFalse(
            driver.start_early_opening(
                replace(adoption, adopted_evidence_sha256="0" * 64)
            )
        )
        self.assertFalse(driver.start_early_opening(replace(adoption, records=())))
        self.assertEqual([], runner.opening_calls)

        self.clock.advance_ms(17 * 60_000)
        self.assertFalse(driver.start_early_opening(adoption))
        self.assertEqual([], runner.opening_calls)

    def test_forged_or_foreign_adoption_fails_against_authoritative_artifact(self):
        runner, driver, scheduler, adoptions = self.build_flow(callback=False)
        attempt = scheduler.attempts["news-a1"]
        scheduler.record_lineage(attempt.attempt_id, "codex", "gpt-5.6-sol")
        self.assertEqual("adopted", scheduler.submit_result(attempt.attempt_id, envelope(attempt)))
        [adoption] = adoptions

        forged_card = dict(adoption.records[0], statement="caller replaced both records and digest")
        forged_records = (forged_card,)
        rejected = (
            replace(
                adoption,
                records=forged_records,
                adopted_evidence_sha256=records_digest(forged_records),
            ),
            replace(adoption, run_id="20260813T020000Z-btc-foreign"),
            replace(adoption, seat_id="official-events"),
            replace(adoption, attempt_id="news-foreign-attempt"),
        )
        for candidate in rejected:
            with self.subTest(candidate=candidate):
                self.assertFalse(driver.start_early_opening(candidate))
        self.assertEqual([], runner.opening_calls)

        malformed_card = dict(adoption.records[0])
        malformed_card.pop("source_url")
        validated_path = (
            self.run.path
            / "agents"
            / adoption.seat_id
            / "attempts"
            / adoption.attempt_id
            / "validated.json"
        )
        validated_path.write_text(
            json.dumps(
                {"schema_version": "1.0.0", "records": [malformed_card]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        malformed_records = (malformed_card,)
        self.assertFalse(
            driver.start_early_opening(
                replace(
                    adoption,
                    records=malformed_records,
                    adopted_evidence_sha256=records_digest(malformed_records),
                )
            )
        )
        self.assertEqual([], runner.opening_calls)

    def test_missing_actual_lineage_keeps_research_adopted_but_dispatches_nothing(self):
        runner, _, scheduler, _ = self.build_flow()
        attempt = scheduler.attempts["news-a1"]

        self.assertEqual("adopted", scheduler.submit_result(attempt.attempt_id, envelope(attempt)))

        self.assertEqual([], runner.opening_calls)
        self.assertEqual(
            "adopted",
            scheduler.attempt_outcomes[attempt.attempt_id]["terminal_outcome"],
        )
        events = [
            event
            for event in scheduler.events
            if event.get("event") == "adopted_result_observer_failed"
        ]
        self.assertEqual(1, len(events))
        [event] = events
        self.assertEqual("opening_actual_lineage_missing", event["failure_code"])
        self.assertEqual("opening", event["observer_phase"])
        self.assertEqual(
            ["actual_model", "actual_provider"], event["missing_lineage_fields"]
        )
        self.assertNotIn("actual_provider", event)
        self.assertNotIn("actual_model", event)

    def test_unusable_opening_outputs_record_one_sanitized_terminal_failure_each(self):
        seats = ("news", "official-events", "social-macro")
        runner, driver, scheduler, _ = self.build_flow(seats=seats)
        replies = {
            "news": "{malformed SECRET credential=do-not-persist",
            "official-events": json.dumps(
                {
                    "seat_id": "official-events",
                    "stance": "bullish",
                    "secret_field": "SECRET credential=do-not-persist",
                }
            ),
            "social-macro": json.dumps(opening_payload("news"), ensure_ascii=False),
        }
        expected_codes = {
            "news": "opening_output_malformed",
            "official-events": "opening_output_schema_invalid",
            "social-macro": "opening_seat_id_mismatch",
        }

        for seat_id in seats:
            attempt = scheduler.attempts["{}-a1".format(seat_id)]
            scheduler.record_lineage(attempt.attempt_id, "codex", "gpt-5.6-sol")
            self.assertEqual(
                "adopted", scheduler.submit_result(attempt.attempt_id, envelope(attempt))
            )
            dispatch_id = "{}-opening".format(seat_id)
            self.assertTrue(
                driver.accept_provider_message(
                    (
                        "provider_lineage",
                        seat_id,
                        {
                            "phase": "opening",
                            "seat_id": seat_id,
                            "dispatch_id": dispatch_id,
                            "provider": "codex",
                            "requested_provider": "codex",
                            "requested_model": PROVIDER_MODELS["codex"],
                            "actual_model": "gpt-5.6-sol",
                            "research_attempt_id": "{}-a1".format(seat_id),
                            "adopted_evidence_sha256": driver.opening_adoptions[
                                seat_id
                            ].adopted_evidence_sha256,
                        },
                    )
                )
            )
            message = ("debate_result", dispatch_id, replies[seat_id])
            self.assertTrue(driver.accept_provider_message(message))
            self.assertFalse(driver.accept_provider_message(message))

        events = [
            json.loads(line)
            for line in (self.run.path / "events.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        failures = [
            event
            for event in events
            if event.get("event") == "opening_invocation_failed"
        ]
        self.assertEqual(3, len(failures))
        self.assertEqual(
            expected_codes,
            {event["seat_id"]: event["failure_code"] for event in failures},
        )
        for event in failures:
            seat_id = event["seat_id"]
            self.assertEqual("opening", event["phase"])
            self.assertEqual("{}-opening".format(seat_id), event["dispatch_id"])
            self.assertEqual(event["dispatch_id"], event["opening_invocation_id"])
            self.assertEqual("{}-a1".format(seat_id), event["research_attempt_id"])
            self.assertEqual("codex", event["provider"])
            self.assertEqual(PROVIDER_MODELS["codex"], event["requested_model"])
            self.assertEqual("gpt-5.6-sol", event["research_actual_model"])
            self.assertEqual("failed", event["terminal_outcome"])
            self.assertEqual("terminal", event["failure_state"])
        for seat_id in seats:
            self.assertEqual(
                "adopted",
                scheduler.attempt_outcomes["{}-a1".format(seat_id)]["terminal_outcome"],
            )
        artifacts = "\n".join(
            path.read_text(encoding="utf-8")
            for path in self.run.path.rglob("*")
            if path.is_file()
        )
        self.assertNotIn("SECRET", artifacts)
        self.assertNotIn("credential=", artifacts)

    def test_opening_actual_lineage_must_match_the_adopted_research_attempt(self):
        seats = ("news", "official-events", "social-macro")
        runner, driver, scheduler, _ = self.build_flow(seats=seats)
        dispatches = {}
        for seat_id in seats:
            attempt = scheduler.attempts["{}-a1".format(seat_id)]
            scheduler.record_lineage(attempt.attempt_id, "codex", "gpt-5.6-sol")
            self.assertEqual(
                "adopted", scheduler.submit_result(attempt.attempt_id, envelope(attempt))
            )
            dispatches[seat_id] = "{}-opening".format(seat_id)

        mismatch = {
            "news": ("claude", "gpt-5.6-sol"),
            "official-events": ("codex", "gpt-5.6-terra"),
        }
        for seat_id, (provider, model) in mismatch.items():
            self.assertTrue(
                driver.accept_provider_message(
                    (
                        "provider_lineage",
                        seat_id,
                        {
                            "phase": "opening",
                            "seat_id": seat_id,
                            "dispatch_id": dispatches[seat_id],
                            "research_attempt_id": "{}-a1".format(seat_id),
                            "provider": provider,
                            "requested_provider": "codex",
                            "requested_model": PROVIDER_MODELS["codex"],
                            "actual_model": model,
                            "adopted_evidence_sha256": driver.opening_adoptions[
                                seat_id
                            ].adopted_evidence_sha256,
                        },
                    )
                )
            )
            self.assertFalse(
                driver.accept_provider_message(
                    (
                        "debate_result",
                        dispatches[seat_id],
                        json.dumps(opening_payload(seat_id), ensure_ascii=False),
                    )
                )
            )

        self.assertTrue(
            driver.accept_provider_message(
                (
                    "debate_result",
                    dispatches["social-macro"],
                    json.dumps(opening_payload("social-macro"), ensure_ascii=False),
                )
            )
        )

        events = [
            json.loads(line)
            for line in (self.run.path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        failures = [event for event in events if event.get("event") == "opening_invocation_failed"]
        self.assertEqual(
            {
                "news": "opening_lineage_binding_mismatch",
                "official-events": "opening_lineage_binding_mismatch",
                "social-macro": "opening_actual_lineage_missing",
            },
            {event["seat_id"]: event["failure_code"] for event in failures},
        )
        self.assertEqual({}, driver.positions)

    def test_opening_lineage_cannot_cross_bind_another_seats_dispatch(self):
        runner, driver, scheduler, _ = self.build_flow(
            seats=("news", "official-events")
        )
        for seat_id in ("news", "official-events"):
            attempt = scheduler.attempts["{}-a1".format(seat_id)]
            scheduler.record_lineage(attempt.attempt_id, "codex", "gpt-5.6-sol")
            self.assertEqual(
                "adopted", scheduler.submit_result(attempt.attempt_id, envelope(attempt))
            )

        self.assertTrue(
            driver.accept_provider_message(
                (
                    "provider_lineage",
                    "news",
                    {
                        "phase": "opening",
                        "seat_id": "news",
                        "dispatch_id": "official-events-opening",
                        "research_attempt_id": "news-a1",
                        "provider": "codex",
                        "requested_provider": "codex",
                        "requested_model": PROVIDER_MODELS["codex"],
                        "actual_model": "gpt-5.6-sol",
                        "adopted_evidence_sha256": "0" * 64,
                    },
                )
            )
        )
        self.assertFalse(
            driver.accept_provider_message(
                (
                    "debate_result",
                    "official-events-opening",
                    json.dumps(opening_payload("official-events"), ensure_ascii=False),
                )
            )
        )
        self.assertNotIn("official-events", driver.positions)
        failures = [
            json.loads(line)
            for line in (self.run.path / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if '"event": "opening_invocation_failed"' in line
        ]
        self.assertEqual(1, len(failures))
        self.assertEqual("official-events", failures[0]["seat_id"])
        self.assertEqual(
            "opening_lineage_binding_mismatch", failures[0]["failure_code"]
        )

    def test_abandoning_a_pending_opening_records_one_terminal_failure(self):
        runner, driver, scheduler, _ = self.build_flow()
        attempt = scheduler.attempts["news-a1"]
        scheduler.record_lineage(attempt.attempt_id, "codex", "gpt-5.6-sol")
        self.assertEqual("adopted", scheduler.submit_result(attempt.attempt_id, envelope(attempt)))

        driver._abandon(["news-opening"], "deadline_missed")
        driver._abandon(["news-opening"], "deadline_missed")

        events = [
            json.loads(line)
            for line in (self.run.path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        failures = [event for event in events if event.get("event") == "opening_invocation_failed"]
        self.assertEqual(1, len(failures))
        self.assertEqual("opening_deadline_missed", failures[0]["failure_code"])
        self.assertEqual("failed", failures[0]["terminal_outcome"])
        self.assertEqual(["news-opening", "news-opening"], runner.cancelled)
        self.assertEqual("adopted", scheduler.attempt_outcomes[attempt.attempt_id]["terminal_outcome"])

    def test_production_scheduler_builder_wires_the_adopted_opening_callback(self):
        class RecordingEarlyDriver:
            def __init__(self):
                self.calls = []

            def start_early_opening(self, adoption):
                self.calls.append(adoption)

        runner = RecordingRunner(self.results)
        driver = RecordingEarlyDriver()
        scheduler = _build_research_scheduler(
            run=self.run,
            clock=self.clock,
            runner=runner,
            package=self.package,
            early_driver=driver,
        )

        marker = object()
        scheduler.on_adopted(marker)

        self.assertEqual([marker], driver.calls)

    def test_late_cancelled_and_non_adopted_research_never_dispatch_opening(self):
        runner, _, scheduler, _ = self.build_flow()
        attempt = scheduler.attempts["news-a1"]

        self.clock.advance_ms(SEAL_MS)
        scheduler.tick()
        self.assertEqual(
            "cancelled",
            scheduler.attempt_outcomes[attempt.attempt_id]["terminal_outcome"],
        )
        self.assertEqual("late", scheduler.submit_result(attempt.attempt_id, envelope(attempt)))
        self.assertEqual([], runner.opening_calls)

    def test_mutation_without_the_adopted_edge_callback_is_killed(self):
        runner, _, scheduler, _ = self.build_flow(callback=False)
        attempt = scheduler.attempts["news-a1"]

        self.assertEqual("adopted", scheduler.submit_result(attempt.attempt_id, envelope(attempt)))

        # This is the isolated mutation: the adopted callback is absent.  The
        # Ticket 04 acceptance assertion must fail, proving the positive test
        # depends on that exact edge rather than a later post-seal dispatch.
        with self.assertRaises(AssertionError):
            self.assertEqual(1, len(runner.opening_calls))

    def test_completed_opening_waits_for_the_global_seal_before_publication(self):
        runner, driver, scheduler, adoptions = self.build_flow(opening_reply="success")
        attempt = scheduler.attempts["news-a1"]
        scheduler.record_lineage(attempt.attempt_id, "codex", "gpt-5.6-sol")
        self.assertEqual("adopted", scheduler.submit_result(attempt.attempt_id, envelope(attempt)))
        self.drain_opening_messages(driver)

        self.assertIsNone(driver.machine)
        events = [
            json.loads(line)
            for line in (self.run.path / "events.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        self.assertFalse(any(event.get("event") == "seat_message" for event in events))

        self.clock.advance_ms(SEAL_MS)
        scheduler.tick()
        records = list(scheduler.adopted_records["news"])
        self.run.write_jsonl("evidence.jsonl", records, source="ticket04 sealed evidence")
        driver.activate_after_seal(records, scheduler.seal["sha256"])

        self.assertEqual("bullish", driver.machine.seats["news"].initial["stance"])
        opening = next(
            entry
            for entry in driver.machine.entries
            if entry.get("event") == "seat_message" and entry.get("seat_id") == "news"
        )
        self.assertEqual(attempt.attempt_id, opening["content"]["research_attempt_id"])
        self.assertEqual(SEAL_MS, opening["elapsed_ms"])
        self.assertFalse(driver.start_early_opening(adoptions[0]))
        self.assertEqual(1, len(runner.opening_calls))

    def test_a_seat_adopted_at_the_seal_never_gets_an_opening_dispatch(self):
        """The seal is the boundary, and here it is the only guard that can say so.

        This seat spent no earlier Opening and is nowhere near its 17-minute
        budget, so neither the already-dispatched nor the expiry guard can
        refuse it. Without this the seal guard can be deleted outright and
        every other Ticket 04 assertion still passes.
        """
        runner, driver, scheduler, adoptions = self.build_flow(callback=False)
        attempt = scheduler.attempts["news-a1"]
        scheduler.record_lineage(attempt.attempt_id, "codex", "gpt-5.6-sol")
        self.assertEqual("adopted", scheduler.submit_result(attempt.attempt_id, envelope(attempt)))
        [adoption] = adoptions

        # The seal instant has arrived; the state machine is not open yet.
        self.clock.advance_ms(SEAL_MS)
        self.assertIsNone(driver.machine)
        self.assertFalse(driver.start_early_opening(adoption))

        # ...and once the sealed snapshot opens it, the answer does not change.
        scheduler.tick()
        driver.activate_after_seal(
            list(scheduler.adopted_records["news"]), scheduler.seal["sha256"]
        )
        self.assertFalse(driver.start_early_opening(adoption))

        self.assertEqual([], runner.opening_calls)
        self.assertNotIn("news", driver.opening_adoptions)

    def test_each_opening_prompt_carries_only_its_own_seats_evidence(self):
        """R-010: an Opening reads this seat's own adopted evidence, nobody else's."""
        runner, _, scheduler, _ = self.build_flow(seats=("news", "official-events"))
        for seat_id in ("news", "official-events"):
            attempt = scheduler.attempts["{}-a1".format(seat_id)]
            scheduler.record_lineage(attempt.attempt_id, "codex", "gpt-5.6-sol")
            self.assertEqual(
                "adopted",
                scheduler.submit_result(attempt.attempt_id, envelope(attempt)),
            )

        prompts = {dispatch.seat_id: dispatch.prompt for dispatch in runner.opening_calls}
        self.assertEqual({"news", "official-events"}, set(prompts))
        self.assertIn("news-01", prompts["news"])
        self.assertNotIn("official-events-01", prompts["news"])
        self.assertIn("official-events-01", prompts["official-events"])
        self.assertNotIn("news-01", prompts["official-events"])


class SharedStartCoordinateTest(unittest.TestCase):
    """The run has one absolute start; the driver never captures its own.

    ``run_launch`` builds the DebateDriver before ``ResearchScheduler.start``.
    While both read the clock for themselves, every tick between the two reads
    is drift: the driver's ``elapsed_ms`` and the scheduler's disagree for the
    whole run, so an Opening dispatched at the adopted edge is measured against
    a different origin than the deadline that adopted it.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name) / "data"
        self.data_root.mkdir()
        # Every reading moves the clock, which is exactly what makes two
        # separate captures land on two different coordinates.
        self.clock = FixedClock(auto_advance_ms=1_000)
        self.store = RunStore(self.data_root)
        self.run = self.store.create_run(RUN_ID, SEAT_IDS, question=QUESTION)
        self.package = build_question_package(QUESTION)
        self.results = queue.Queue()

    def test_the_scheduler_starts_on_the_caller_s_one_absolute_coordinate(self):
        runner = RecordingRunner(self.results)
        started_at_utc = self.clock.utc_now()
        start_monotonic_ms = self.clock.monotonic_ms()
        driver = DebateDriver(
            run=self.run,
            clock=self.clock,
            runner=runner,
            results_queue=self.results,
            package=self.package,
            evidence_records=None,
            snapshot_sha256=None,
            start_monotonic_ms=start_monotonic_ms,
            started_at_utc=started_at_utc,
            sleeper=lambda seconds: None,
            err=io.StringIO(),
        )
        scheduler = ResearchScheduler(
            run=self.run,
            clock=self.clock,
            gateway=JsonGateway(),
            process_runner=runner,
            format_repairer=NoRepairer(),
            primary_models={"news": PROVIDER_MODELS["codex"]},
            replacement_models={"news": None},
            seat_ids=("news",),
            seat_providers={"news": "codex"},
            backup_candidates={
                "news": ProviderCandidate("claude", PROVIDER_MODELS["claude"])
            },
            on_adopted=driver.start_early_opening,
        )

        scheduler.start(
            started_at_utc=started_at_utc, start_monotonic_ms=start_monotonic_ms
        )

        self.assertEqual(start_monotonic_ms, scheduler.start_monotonic_ms)
        self.assertEqual(started_at_utc, scheduler.started_at_utc)
        self.assertEqual(driver.start_monotonic_ms, scheduler.start_monotonic_ms)


if __name__ == "__main__":
    unittest.main()
