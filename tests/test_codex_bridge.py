"""The Codex bridge contract: 3 fixed GPT seats, fail closed, no Python agents."""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents import codex_bridge
from hoya_market_agents.cli import main
from hoya_market_agents.codex_bridge import (
    CODEX_SEAT_IDS,
    CORE_ROLE,
    CONTRACT_TEXT,
    SEAT_TOOL_POLICY,
    SOURCE_TIME_POLICY,
    STATUS_NOT_READY,
    STATUS_READY,
    TARGET_MODEL,
    CodexBridgeError,
    PreflightNotReadyError,
    build_codex_handoff,
    codex_seats,
    seal_seat_handoff,
    seal_public_checkpoint,
    seat_output_dir,
    validate_continuation_message,
    validate_public_checkpoint,
    verify_codex_preflight,
    write_codex_handoff,
)
from hoya_market_agents.question_package import build_question_package
from hoya_market_agents.run_store import RunStore

QUESTION = "分析 BTC 過去 14 日市場狀態"
RUN_ID = "20260801T000000Z-btc-abc123"
CREATED_AT = "2026-08-01T00:00:00Z"


def core_identity(**overrides):
    identity = {
        "role": CORE_ROLE,
        "model": TARGET_MODEL,
        "model_confirmed": True,
        "created_threads_by": "core",
    }
    identity.update(overrides)
    return identity


def threads(**overrides):
    value = {
        seat_id: {
            "thread_id": "thread-{}".format(seat_id),
            "actual_model": TARGET_MODEL,
            "model_confirmed": True,
            "capability_confirmed": True,
            "persistent": True,
            "dispatch_id": "dispatch-{}".format(seat_id),
            "tool_policy": dict(SEAT_TOOL_POLICY),
            "tool_policy_confirmed": True,
            "runtime_policy_receipt": "runtime-receipt-{}".format(seat_id),
        }
        for seat_id in CODEX_SEAT_IDS
    }
    value.update(overrides)
    return value


def evidence_card(seat_id="onchain", attempt_id="attempt-1", **overrides):
    card = {
        "schema_version": "1.0.0",
        "evidence_id": "{}-01".format(seat_id),
        "run_id": RUN_ID,
        "seat_id": seat_id,
        "attempt_id": attempt_id,
        "phase": "research",
        "created_at_utc": CREATED_AT,
        "elapsed_ms": 1000,
        "asset": "BTC",
        "category": "onchain",
        "statement": "公開測試證據。",
        "direction": "support",
        "source_url": "https://example.invalid/source",
        "source_origin": "example:source",
        "source_tier": 1,
        "published_at_utc": CREATED_AT,
        "retrieved_at_utc": CREATED_AT,
        "excerpt": "public value 1",
        "credibility_note": "unit contract fixture",
    }
    card.update(overrides)
    return card


def raw_handoff(seat_id="onchain", attempt_id="attempt-1", **overrides):
    payload = {
        "schema_version": "1.0.0",
        "run_id": RUN_ID,
        "seat_id": seat_id,
        "attempt_id": attempt_id,
        "phase": "research",
        "evidence_cards": [evidence_card(seat_id, attempt_id)],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def write_ready_preflight(run):
    payload = build_codex_handoff(
        run_id=RUN_ID,
        package=build_question_package(QUESTION),
        core=core_identity(),
        threads=threads(),
        created_at_utc=CREATED_AT,
    )
    write_codex_handoff(run, payload)
    return payload


def continuation_message(**overrides):
    message = {
        "claim_id": "claim-1",
        "evidence_ids": ["ev-1"],
        "stance": "bullish",
        "public_reason": "現貨結構仍在上升通道。",
        "responds_to": ["claim-0"],
        "stance_change_reason": None,
    }
    message.update(overrides)
    return message


class CodexHandoffTest(unittest.TestCase):
    def setUp(self):
        self.package = build_question_package(QUESTION)

    def build(self, **overrides):
        kwargs = {
            "run_id": RUN_ID,
            "package": self.package,
            "core": core_identity(),
            "threads": threads(),
            "created_at_utc": CREATED_AT,
        }
        kwargs.update(overrides)
        return build_codex_handoff(**kwargs)

    def test_exactly_three_fixed_gpt_seats_are_mapped(self):
        payload = self.build()

        self.assertEqual(
            ("spot-technical", "derivatives", "onchain"),
            tuple(seat["seat_id"] for seat in payload["seats"]),
        )
        self.assertEqual(3, len(payload["seats"]))
        self.assertEqual(STATUS_READY, payload["status"])

    def test_every_seat_shares_identical_prompt_package_and_policy_hashes(self):
        payload = self.build()

        shared = {seat["shared_prompt_sha256"] for seat in payload["seats"]}
        self.assertEqual(1, len(shared))
        self.assertEqual(payload["shared_prompt_sha256"], shared.pop())
        for seat in payload["seats"]:
            self.assertEqual(payload["question_package_sha256"], seat["question_package_sha256"])
            self.assertEqual(payload["research_snapshot"]["sha256"], seat["research_snapshot_sha256"])
            self.assertEqual(payload["contract_text_sha256"], seat["contract_text_sha256"])
            self.assertEqual(payload["source_time_policy_sha256"], seat["source_time_policy_sha256"])
        self.assertIn(CONTRACT_TEXT, payload["shared_prompt"])
        self.assertIn(SOURCE_TIME_POLICY, payload["shared_prompt"])

    def test_only_role_focus_and_own_output_path_differ_between_seats(self):
        payload = self.build()

        for field in ("role", "focus", "output_path", "thread_id", "seat_id"):
            values = [seat[field] for seat in payload["seats"]]
            self.assertEqual(3, len(set(values)), field)
        for seat in payload["seats"]:
            self.assertIn(seat["seat_id"], seat["output_path"])

    def test_auditable_seat_thread_and_actual_model_metadata_is_recorded(self):
        payload = self.build()

        for seat in payload["seats"]:
            self.assertTrue(seat["thread_id"])
            self.assertEqual(TARGET_MODEL, seat["target_model"])
            self.assertEqual(TARGET_MODEL, seat["actual_model"])
            self.assertTrue(seat["model_confirmed"])
            self.assertTrue(seat["persistent"])

    def test_unknown_wrong_or_unconfirmed_model_is_not_ready_without_fallback(self):
        cases = {
            "unknown": {"actual_model": "unknown"},
            "wrong": {"actual_model": "GPT-5.1"},
            "unconfirmed": {"model_confirmed": False},
            "no_capability": {"capability_confirmed": False},
            "no_thread": {"thread_id": ""},
            "not_persistent": {"persistent": False},
        }
        for label, override in cases.items():
            with self.subTest(case=label):
                broken = threads()
                broken["derivatives"] = dict(broken["derivatives"], **override)
                with self.assertRaises(PreflightNotReadyError) as ctx:
                    self.build(threads=broken)
                self.assertEqual(STATUS_NOT_READY, ctx.exception.status)
                self.assertNotIn(TARGET_MODEL.lower(), str(ctx.exception).lower().replace(
                    TARGET_MODEL.lower(), "", 1
                ))

    def test_core_role_or_model_that_cannot_be_confirmed_is_not_ready(self):
        for override in ({"role": "seat"}, {"model": "GPT-5.1"}, {"model_confirmed": False}):
            with self.subTest(override=override):
                with self.assertRaises(PreflightNotReadyError):
                    self.build(core=core_identity(**override))

    def test_core_metadata_is_an_exact_four_field_allowlist(self):
        with self.assertRaises(PreflightNotReadyError):
            self.build(core=core_identity(debug=True))

    def test_dispatch_policy_requires_no_tools_and_runtime_evidence(self):
        cases = (
            {"tool_policy": {**SEAT_TOOL_POLICY, "allowed_tools": ["filesystem"]}},
            {"tool_policy_confirmed": False},
            {"runtime_policy_receipt": ""},
        )
        for override in cases:
            with self.subTest(override=override):
                broken = threads()
                broken["onchain"] = dict(broken["onchain"], **override)
                with self.assertRaises(PreflightNotReadyError):
                    self.build(threads=broken)

    def test_dispatch_policy_is_auditable_in_preflight_artifact(self):
        payload = self.build()

        self.assertEqual(SEAT_TOOL_POLICY, payload["dispatch_tool_policy"])
        self.assertRegex(payload["dispatch_tool_policy_sha256"], r"^[0-9a-f]{64}$")
        for seat in payload["seats"]:
            self.assertEqual([], seat["tool_policy"]["allowed_tools"])
            self.assertFalse(seat["tool_policy"]["filesystem_access"])
            self.assertFalse(seat["tool_policy"]["secret_access"])
            self.assertTrue(seat["tool_policy_confirmed"])
            self.assertTrue(seat["runtime_policy_receipt"])
            self.assertRegex(
                seat["runtime_policy_receipt_sha256"], r"^[0-9a-f]{64}$"
            )

    def test_invalid_created_at_timestamp_is_rejected(self):
        with self.assertRaises(CodexBridgeError):
            self.build(created_at_utc="not-utc")

    def test_seat_set_must_be_exactly_the_three_codex_seats(self):
        extra = threads()
        extra["news"] = dict(extra["onchain"], thread_id="thread-news")
        with self.assertRaises(PreflightNotReadyError):
            self.build(threads=extra)

        missing = threads()
        del missing["onchain"]
        with self.assertRaises(PreflightNotReadyError):
            self.build(threads=missing)

    def test_unsupported_question_package_is_rejected_before_launch(self):
        with self.assertRaises(CodexBridgeError):
            self.build(package={"question": "隨便問問"})

    def test_python_never_creates_or_impersonates_codex_agents(self):
        source = Path(codex_bridge.__file__).read_text(encoding="utf-8")
        for forbidden in ("subprocess", "Popen", "os.system", "spawn", "openai", "requests"):
            self.assertNotIn(forbidden, source)
        self.assertFalse(
            [name for name in dir(codex_bridge) if name.startswith("create_agent")]
        )


class ContinuationMessageTest(unittest.TestCase):
    def test_public_continuation_message_is_accepted(self):
        message = continuation_message()
        self.assertEqual(message, validate_continuation_message(message))

    def test_missing_required_public_field_is_rejected(self):
        for field in (
            "claim_id",
            "evidence_ids",
            "stance",
            "public_reason",
            "responds_to",
        ):
            with self.subTest(field=field):
                message = continuation_message()
                del message[field]
                with self.assertRaises(CodexBridgeError):
                    validate_continuation_message(message)

    def test_stance_change_requires_a_public_stance_change_reason(self):
        with self.assertRaises(CodexBridgeError):
            validate_continuation_message(
                continuation_message(stance_change_reason=""), previous_stance="bearish"
            )
        accepted = validate_continuation_message(
            continuation_message(stance_change_reason="新增交易所流出證據。"),
            previous_stance="bearish",
        )
        self.assertEqual("bullish", accepted["stance"])

    def test_hidden_chain_of_thought_fields_are_refused_and_never_stored(self):
        for field in (
            "chain_of_thought",
            "reasoning_trace",
            "hidden_reasoning",
            "thinking",
            "scratchpad",
            "internal_monologue",
        ):
            with self.subTest(field=field):
                with self.assertRaises(CodexBridgeError) as ctx:
                    validate_continuation_message(continuation_message(**{field: "secret"}))
                self.assertIn(field, str(ctx.exception))

    def test_unknown_extra_field_is_refused(self):
        with self.assertRaises(CodexBridgeError):
            validate_continuation_message(continuation_message(tool_calls=[]))


class PublicCheckpointTest(unittest.TestCase):
    def checkpoint(self, **overrides):
        value = {
            "checkpoint_id": "derivatives-cp-1",
            "thread_id": "thread-derivatives",
            "evidence_ids": ["derivatives-01"],
            "public_status": "已完成一手來源檢查。",
            "provisional_stance": "bearish",
        }
        value.update(overrides)
        return value

    def test_public_checkpoint_accepts_only_auditable_fields(self):
        checkpoint = self.checkpoint()
        self.assertEqual(checkpoint, validate_public_checkpoint(checkpoint))
        for field in ("thinking", "chain_of_thought", "scratchpad", "tool_calls"):
            with self.subTest(field=field):
                with self.assertRaises(CodexBridgeError):
                    validate_public_checkpoint(self.checkpoint(**{field: "secret"}))

    def test_public_checkpoint_is_write_once_inside_the_seat_attempt(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            run = RunStore(temporary_directory).create_run(RUN_ID, CODEX_SEAT_IDS)
            write_ready_preflight(run)
            record = seal_public_checkpoint(
                run, "derivatives", "derivatives-codex-1", self.checkpoint()
            )

            self.assertIn("agents/derivatives/attempts/", record["path"])
            self.assertTrue((run.path / record["path"]).is_file())
            with self.assertRaises(CodexBridgeError):
                seal_public_checkpoint(
                    run, "derivatives", "derivatives-codex-1", self.checkpoint()
                )

    def test_checkpoint_must_match_sealed_preflight_seat_thread_mapping(self):
        cases = (
            ("derivatives", self.checkpoint(thread_id="thread-onchain")),
            ("onchain", self.checkpoint()),
        )
        for seat_id, checkpoint in cases:
            with self.subTest(seat_id=seat_id, thread_id=checkpoint["thread_id"]):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    run = RunStore(temporary_directory).create_run(
                        RUN_ID, CODEX_SEAT_IDS
                    )
                    write_ready_preflight(run)
                    with self.assertRaises(CodexBridgeError):
                        seal_public_checkpoint(
                            run,
                            seat_id,
                            "{}-codex-1".format(seat_id),
                            checkpoint,
                        )

    def test_checkpoint_without_sealed_preflight_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            run = RunStore(temporary_directory).create_run(RUN_ID, CODEX_SEAT_IDS)
            target = run.path / "agents/derivatives/attempts/derivatives-codex-1"
            with self.assertRaises(CodexBridgeError):
                seal_public_checkpoint(
                    run, "derivatives", "derivatives-codex-1", self.checkpoint()
                )
            self.assertFalse(target.exists())


class SeatIsolationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.store = RunStore(self.data_root)
        self.run = self.store.create_run(RUN_ID, CODEX_SEAT_IDS)

    def test_seat_output_dir_is_inside_its_own_data_root_attempt_directory(self):
        path = seat_output_dir(self.data_root, RUN_ID, "derivatives")

        self.assertTrue(str(path).startswith(str(self.data_root)))
        self.assertIn("derivatives", path.parts)
        self.assertNotIn(codex_bridge.CODE_ROOT.name, path.relative_to(self.data_root).parts)

    def test_a_seat_may_not_write_code_root_secrets_or_another_seat(self):
        for seat_id, target in (
            ("derivatives", codex_bridge.CODE_ROOT / "hoya_market_agents" / "cli.py"),
            ("derivatives", self.data_root / "secrets.env"),
            ("derivatives", seat_output_dir(self.data_root, RUN_ID, "onchain") / "raw.txt"),
            ("derivatives", seat_output_dir(self.data_root, RUN_ID, "derivatives") / ".." / "x"),
        ):
            with self.subTest(target=str(target)):
                with self.assertRaises(CodexBridgeError):
                    codex_bridge.assert_seat_write_allowed(
                        target, self.data_root, RUN_ID, seat_id
                    )

    def test_code_root_cannot_be_used_as_data_root(self):
        with self.assertRaises(CodexBridgeError):
            codex_bridge.seat_output_dir(codex_bridge.CODE_ROOT, RUN_ID, "onchain")

    def test_seat_may_write_its_own_attempt_file(self):
        target = seat_output_dir(self.data_root, RUN_ID, "derivatives") / "raw.txt"
        self.assertEqual(
            target.resolve(),
            codex_bridge.assert_seat_write_allowed(target, self.data_root, RUN_ID, "derivatives"),
        )

    def test_raw_handoff_bytes_and_sha256_are_preserved_exactly(self):
        raw = raw_handoff()
        record = seal_seat_handoff(self.run, "onchain", "attempt-1", raw)

        stored = (self.run.path / record["path"]).read_bytes()
        self.assertEqual(raw.encode("utf-8"), stored)
        self.assertEqual(hashlib.sha256(raw.encode("utf-8")).hexdigest(), record["sha256"])
        self.assertEqual(len(raw.encode("utf-8")), record["bytes"])
        self.assertIn("agents/onchain/", record["path"])

    def test_core_may_not_rewrite_a_sealed_seat_handoff(self):
        seal_seat_handoff(self.run, "onchain", "attempt-1", raw_handoff())
        with self.assertRaises(CodexBridgeError):
            seal_seat_handoff(self.run, "onchain", "attempt-1", raw_handoff())

    def test_invalid_or_private_raw_handoff_writes_zero_bytes(self):
        attempt_root = seat_output_dir(self.data_root, RUN_ID, "onchain")
        cases = ["not json"]
        for field in (
            "hidden",
            "private",
            "chain_of_thought",
            "secret",
            "api_key",
            "unknown_field",
        ):
            cases.append(raw_handoff(**{field: "must-not-land"}))
            cases.append(
                raw_handoff(
                    evidence_cards=[evidence_card(**{field: "must-not-land"})]
                )
            )

        for index, raw in enumerate(cases):
            attempt_id = "invalid-{}".format(index)
            if raw != "not json":
                payload = json.loads(raw)
                payload["attempt_id"] = attempt_id
                payload["evidence_cards"][0]["attempt_id"] = attempt_id
                raw = json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ) + "\n"
            with self.subTest(attempt_id=attempt_id):
                with self.assertRaises(CodexBridgeError):
                    seal_seat_handoff(self.run, "onchain", attempt_id, raw)
                self.assertFalse((attempt_root / attempt_id).exists())


class PreflightVerificationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.store = RunStore(self.data_root)
        self.run = self.store.create_run(RUN_ID, CODEX_SEAT_IDS)
        self.payload = build_codex_handoff(
            run_id=RUN_ID,
            package=build_question_package(QUESTION),
            core=core_identity(),
            threads=threads(),
            created_at_utc=CREATED_AT,
        )

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def verify_cli(self, run_id=RUN_ID):
        return self.run_cli(
            "verify-preflight",
            "--provider",
            "codex",
            "--run-id",
            run_id,
            "--data-root",
            str(self.data_root),
        )

    def test_verify_reads_an_already_written_artifact_and_reports_ready(self):
        write_codex_handoff(self.run, self.payload)

        code, out, err = self.verify_cli()

        self.assertEqual(0, code, err)
        self.assertIn(STATUS_READY, out)
        self.assertIn(TARGET_MODEL, out)
        for seat_id in CODEX_SEAT_IDS:
            self.assertIn(seat_id, out)

    def test_verify_fails_closed_when_no_artifact_was_written(self):
        code, out, err = self.verify_cli()

        self.assertEqual(1, code)
        self.assertIn(STATUS_NOT_READY, err)
        self.assertEqual("", out)

    def test_verify_fails_closed_on_tampered_model_metadata(self):
        broken = json.loads(json.dumps(self.payload))
        broken["seats"][1]["actual_model"] = "GPT-5.1"
        write_codex_handoff(self.run, broken)

        code, out, err = self.verify_cli()

        self.assertEqual(1, code)
        self.assertIn(STATUS_NOT_READY, err)
        self.assertEqual("", out)

    def test_verify_fails_closed_on_tampered_shared_prompt_hash(self):
        broken = json.loads(json.dumps(self.payload))
        broken["shared_prompt_sha256"] = "0" * 64
        write_codex_handoff(self.run, broken)

        code, out, err = self.verify_cli()

        self.assertEqual(1, code)
        self.assertIn(STATUS_NOT_READY, err)
        self.assertEqual("", out)

    def test_verify_fails_closed_on_tampered_dispatch_tool_policy(self):
        broken = json.loads(json.dumps(self.payload))
        broken["seats"][0]["tool_policy"]["allowed_tools"] = ["filesystem"]
        write_codex_handoff(self.run, broken)

        code, out, err = self.verify_cli()

        self.assertEqual(1, code)
        self.assertIn(STATUS_NOT_READY, err)
        self.assertEqual("", out)

    def test_verify_fails_closed_when_a_seat_is_missing(self):
        broken = json.loads(json.dumps(self.payload))
        del broken["seats"][2]
        write_codex_handoff(self.run, broken)

        self.assertEqual(1, self.verify_cli()[0])

    def test_verify_never_spawns_agents(self):
        write_codex_handoff(self.run, self.payload)
        result = verify_codex_preflight(self.data_root, RUN_ID)

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual(3, len(result["seats"]))
        self.assertFalse(list((self.run.path / "agents" / "onchain" / "attempts").iterdir()))


if __name__ == "__main__":
    unittest.main()
