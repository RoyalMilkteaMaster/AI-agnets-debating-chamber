"""Ticket #6 Claude CLI behavior through an injectable process seam."""

import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from hoya_market_agents.claude_adapter import (
    CLAUDE_MODEL_ALIAS,
    CLAUDE_SEAT_SESSIONS,
    ClaudeAdapter,
    ClaudeAttemptRequest,
    ProcessOutput,
    mask_session_id,
    run_claude_preflight,
)
from hoya_market_agents.cli import main


def envelope(seat_id, *, search_requests=1, model="claude-opus-5"):
    return json.dumps(
        {
            "is_error": False,
            "structured_output": {
                "seat_id": seat_id,
                "search_ok": True,
                "message": "public smoke result",
            },
            "usage": {
                "input_tokens": 5,
                "output_tokens": 7,
                "server_tool_use": {"web_search_requests": search_requests},
            },
            "modelUsage": {
                model: {
                    "canonicalModel": model,
                    "inputTokens": 5,
                    "outputTokens": 7,
                    "webSearchRequests": search_requests,
                }
            },
        }
    )


class ScriptedRunner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def run(self, args, *, input_text, cwd, timeout_seconds):
        self.calls.append(
            {
                "args": tuple(args),
                "input_text": input_text,
                "cwd": Path(cwd),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.outputs.pop(0)


class CapabilityRunner:
    def __init__(self, *, logged_in=True, model="claude-opus-5", search_requests=1):
        self.logged_in = logged_in
        self.model = model
        self.search_requests = search_requests
        self.calls = []

    def run(self, args, *, input_text, cwd, timeout_seconds):
        args = tuple(args)
        self.calls.append(args)
        if args[-1] == "--version":
            return completed("2.1.220 (Claude Code)\n")
        if "auth" in args:
            return completed(
                json.dumps(
                    {
                        "loggedIn": self.logged_in,
                        "authMethod": "claude.ai" if self.logged_in else "none",
                        "subscriptionType": "max" if self.logged_in else None,
                    }
                )
            )
        flag = "--resume" if "--resume" in args else "--session-id"
        session_id = args[args.index(flag) + 1]
        seat_id = next(
            seat for seat, configured in CLAUDE_SEAT_SESSIONS.items() if configured == session_id
        )
        return completed(
            envelope(
                seat_id,
                search_requests=self.search_requests,
                model=self.model,
            )
        )


def completed(stdout="", stderr="", returncode=0, elapsed_ms=10, timed_out=False):
    return ProcessOutput(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        elapsed_ms=elapsed_ms,
        timed_out=timed_out,
    )


class ClaudeAdapterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.code_root = root / "code"
        self.data_root = root / "data"
        self.code_root.mkdir()
        self.data_root.mkdir()

    def request(self, seat_id="spot-technical", *, resume=False):
        attempt_dir = self.data_root / "runs" / "run-1" / "agents" / seat_id / "attempts" / "a1"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        return ClaudeAttemptRequest(
            seat_id=seat_id,
            attempt_id="{}-a1".format(seat_id),
            prompt="Return the public smoke result.",
            attempt_dir=attempt_dir,
            resume=resume,
            timeout_seconds=12,
        )

    def adapter(self, runner):
        return ClaudeAdapter(
            runner=runner,
            code_root=self.code_root,
            data_root=self.data_root,
            cli_path="/home/leslie/.local/bin/claude",
        )

    def test_three_fixed_sessions_are_distinct_and_commands_pin_opus(self):
        self.assertEqual(3, len(CLAUDE_SEAT_SESSIONS))
        self.assertEqual(3, len(set(CLAUDE_SEAT_SESSIONS.values())))
        runner = ScriptedRunner([completed(envelope("spot-technical"))] * 2)
        adapter = self.adapter(runner)

        adapter.run(self.request())
        adapter.run(self.request(resume=True))

        first, resumed = (call["args"] for call in runner.calls)
        session_id = CLAUDE_SEAT_SESSIONS["spot-technical"]
        self.assertIn(("--model", CLAUDE_MODEL_ALIAS), tuple(zip(first, first[1:])))
        self.assertIn(("--session-id", session_id), tuple(zip(first, first[1:])))
        self.assertNotIn("--resume", first)
        self.assertIn(("--resume", session_id), tuple(zip(resumed, resumed[1:])))
        self.assertNotIn("--session-id", resumed)
        self.assertIn(("--tools", "WebSearch,WebFetch"), tuple(zip(first, first[1:])))
        self.assertIn(("--allowedTools", "WebSearch,WebFetch"), tuple(zip(first, first[1:])))
        self.assertNotIn("Bash", first)
        self.assertNotIn("Edit", first)

    def test_only_an_attempt_directory_under_data_root_is_allowed(self):
        runner = ScriptedRunner([])
        adapter = self.adapter(runner)
        outside = ClaudeAttemptRequest(
            seat_id="spot-technical",
            attempt_id="spot-technical-a1",
            prompt="x",
            attempt_dir=self.code_root,
        )

        with self.assertRaisesRegex(ValueError, "Data Root"):
            adapter.run(outside)
        self.assertEqual([], runner.calls)

    def test_valid_output_records_actual_model_usage_and_safe_identity(self):
        runner = ScriptedRunner([completed(envelope("spot-technical"), elapsed_ms=321)])

        result = self.adapter(runner).run(self.request())

        self.assertEqual("ok", result.status)
        self.assertEqual("claude-opus-5", result.actual_model)
        self.assertEqual(321, result.elapsed_ms)
        self.assertEqual(1, result.web_search_requests)
        self.assertEqual(5, result.usage["input_tokens"])
        self.assertEqual("spot-technical", result.structured_output["seat_id"])
        self.assertNotEqual(CLAUDE_SEAT_SESSIONS["spot-technical"], result.masked_session_id)
        self.assertEqual(mask_session_id(CLAUDE_SEAT_SESSIONS["spot-technical"]), result.masked_session_id)

    def test_empty_malformed_nonzero_and_timeout_are_explicit(self):
        outputs = [
            completed(""),
            completed("not-json"),
            completed("{}", stderr="failed", returncode=9),
            completed("partial", timed_out=True, elapsed_ms=12_000),
        ]
        adapter = self.adapter(ScriptedRunner(outputs))

        statuses = [adapter.run(self.request()).status for _ in outputs]

        self.assertEqual(
            ["empty_output", "malformed_output", "process_error", "timeout"],
            statuses,
        )

    def test_schema_and_seat_identity_fail_closed(self):
        wrong = json.loads(envelope("wrong-seat"))
        invalid = json.loads(envelope("spot-technical"))
        del invalid["structured_output"]["search_ok"]
        adapter = self.adapter(
            ScriptedRunner([completed(json.dumps(wrong)), completed(json.dumps(invalid))])
        )

        self.assertEqual("invalid_schema", adapter.run(self.request()).status)
        self.assertEqual("invalid_schema", adapter.run(self.request()).status)

    def test_three_seats_run_concurrently_without_session_mixing(self):
        barrier = threading.Barrier(3)
        lock = threading.Lock()
        active = 0
        peak = 0

        class ConcurrentRunner:
            def run(inner_self, args, *, input_text, cwd, timeout_seconds):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                barrier.wait(timeout=2)
                seat_id = Path(cwd).parts[-3]
                time.sleep(0.01)
                with lock:
                    active -= 1
                return completed(envelope(seat_id))

        adapter = self.adapter(ConcurrentRunner())
        requests = [self.request(seat_id) for seat_id in CLAUDE_SEAT_SESSIONS]

        results = adapter.run_concurrent(requests)

        self.assertEqual(3, peak)
        self.assertEqual(set(CLAUDE_SEAT_SESSIONS), set(results))
        for seat_id, result in results.items():
            self.assertEqual(seat_id, result.structured_output["seat_id"])
            self.assertEqual(mask_session_id(CLAUDE_SEAT_SESSIONS[seat_id]), result.masked_session_id)

    def test_preflight_rejects_api_key_without_invoking_claude(self):
        runner = ScriptedRunner([])

        report = run_claude_preflight(
            seats=3,
            cli_path="/home/leslie/.local/bin/claude",
            code_root=self.code_root,
            data_root=self.data_root,
            runner=runner,
            environ={"ANTHROPIC_API_KEY": "secret-must-not-appear"},
            path_exists=lambda _: True,
        )

        self.assertFalse(report["ready"])
        self.assertEqual(["ANTHROPIC_API_KEY_present"], report["reasons"])
        self.assertNotIn("secret-must-not-appear", json.dumps(report))
        self.assertEqual([], runner.calls)

    def test_preflight_fails_closed_when_cli_or_subscription_login_is_missing(self):
        missing_runner = ScriptedRunner([])
        missing = run_claude_preflight(
            seats=3,
            cli_path="/missing/claude",
            code_root=self.code_root,
            data_root=self.data_root,
            runner=missing_runner,
            environ={},
            path_exists=lambda _: False,
        )
        auth_runner = CapabilityRunner(logged_in=False)
        logged_out = run_claude_preflight(
            seats=3,
            cli_path="/fake/claude",
            code_root=self.code_root,
            data_root=self.data_root,
            runner=auth_runner,
            environ={},
            path_exists=lambda _: True,
        )

        self.assertEqual(["claude_cli_missing"], missing["reasons"])
        self.assertEqual([], missing_runner.calls)
        self.assertEqual(["claude_ai_max_login_required"], logged_out["reasons"])
        self.assertIsNone(logged_out["auth"])

    def test_preflight_reports_ready_only_after_opus_search_and_resume(self):
        runner = CapabilityRunner()

        report = run_claude_preflight(
            seats=3,
            cli_path="/fake/claude",
            code_root=self.code_root,
            data_root=self.data_root,
            runner=runner,
            environ={},
            path_exists=lambda _: True,
        )

        self.assertTrue(report["ready"])
        self.assertTrue(report["resume_ok"])
        self.assertEqual(3, report["concurrent_seats"])
        self.assertEqual({"claude-opus-5"}, {seat["actual_model"] for seat in report["seats"]})
        self.assertEqual({1}, {seat["web_search_requests"] for seat in report["seats"]})
        self.assertTrue(any("--resume" in call for call in runner.calls))

        repeated = run_claude_preflight(
            seats=3,
            cli_path="/fake/claude",
            code_root=self.code_root,
            data_root=self.data_root,
            runner=runner,
            environ={},
            path_exists=lambda _: True,
        )
        self.assertTrue(repeated["ready"])

    def test_preflight_fails_closed_for_wrong_model_or_missing_search(self):
        wrong_model = run_claude_preflight(
            seats=3,
            cli_path="/fake/claude",
            code_root=self.code_root,
            data_root=self.data_root,
            runner=CapabilityRunner(model="claude-sonnet-5", search_requests=0),
            environ={},
            path_exists=lambda _: True,
        )

        self.assertFalse(wrong_model["ready"])
        self.assertEqual(
            3,
            len([reason for reason in wrong_model["reasons"] if "actual_model_not_opus" in reason]),
        )

    def test_resume_hook_keeps_seat_identity_and_failure_maps_for_scheduler(self):
        request = self.request()
        adapter = self.adapter(ScriptedRunner([completed(envelope("spot-technical"))]))
        resumed = adapter.resumed_request(
            request,
            attempt_id="spot-technical-a2",
            prompt="public checkpoint and debate history",
            attempt_dir=request.attempt_dir,
        )

        result = adapter.run(resumed)

        self.assertTrue(resumed.resume)
        self.assertEqual(request.seat_id, resumed.seat_id)
        self.assertEqual("spot-technical-a2", result.attempt_id)
        self.assertIsNone(result.scheduler_failure_kind)
        timeout = self.adapter(
            ScriptedRunner([completed(timed_out=True)])
        ).run(self.request())
        self.assertEqual("timeout", timeout.scheduler_failure_kind)

    def test_cli_preflight_contract_accepts_required_command_shape(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        fake_report = {
            "ready": True,
            "status": "READY",
            "provider": "claude",
            "seats": [],
            "reasons": [],
        }

        from unittest.mock import patch

        with patch("hoya_market_agents.cli.run_claude_preflight", return_value=fake_report):
            exit_code = main(
                ["preflight", "--provider", "claude", "--seats", "3"],
                stdout=stdout,
                stderr=stderr,
            )

        self.assertEqual(0, exit_code)
        self.assertIn('"status": "READY"', stdout.getvalue())
        self.assertEqual("", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
