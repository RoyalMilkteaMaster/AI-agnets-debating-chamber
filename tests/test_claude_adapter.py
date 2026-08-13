"""Ticket #6 Claude CLI behavior through an injectable process seam."""

import io
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from hoya_market_agents.claude_adapter import (
    CLAUDE_MODEL_ALIAS,
    CLAUDE_SEAT_SESSIONS,
    PROCESS_GROUP_NOT_RUNNING,
    PROCESS_GROUP_RECLAIMED,
    PROCESS_TREE_TERMINATION_FAILED,
    ClaudeAdapter,
    ClaudeAttemptRequest,
    ProcessOutput,
    ProcessRegistry,
    TerminatingRunner,
    mask_session_id,
    run_claude_preflight,
    validate_smoke_output,
)
from hoya_market_agents.cli import main


def envelope(
    seat_id,
    *,
    search_requests=1,
    model="claude-opus-5",
    message="public smoke result",
    structured_output=None,
):
    return json.dumps(
        {
            "is_error": False,
            "structured_output": structured_output
            or {"seat_id": seat_id, "search_ok": True, "message": message},
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
    def __init__(
        self,
        *,
        logged_in=True,
        model="claude-opus-5",
        search_requests=1,
        echo_resume=False,
    ):
        self.logged_in = logged_in
        self.model = model
        self.search_requests = search_requests
        self.echo_resume = echo_resume
        self.calls = []
        self.inputs = []
        self.markers = {}

    def run(self, args, *, input_text, cwd, timeout_seconds):
        args = tuple(args)
        self.calls.append(args)
        self.inputs.append(input_text)
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
        marker_match = re.search(r"checkpoint_marker=([a-z0-9-]+)", input_text)
        if marker_match:
            self.markers[session_id] = marker_match.group(1)
        message = (
            "resume-ok"
            if self.echo_resume and marker_match is None and "--resume" in args
            else self.markers.get(session_id, "public smoke result")
        )
        return completed(
            envelope(
                seat_id,
                search_requests=self.search_requests,
                model=self.model,
                message=message,
            )
        )


def completed(
    stdout="",
    stderr="",
    returncode=0,
    elapsed_ms=10,
    timed_out=False,
    process_outcome=None,
):
    return ProcessOutput(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        elapsed_ms=elapsed_ms,
        timed_out=timed_out,
        process_outcome=process_outcome,
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

    def request(self, seat_id="official-events", *, resume=False):
        attempt_dir = self.data_root / "runs" / "run-1" / "agents" / seat_id / "attempts" / "a1"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        return ClaudeAttemptRequest(
            seat_id=seat_id,
            attempt_id="{}-a1".format(seat_id),
            prompt="Return the public smoke result.",
            attempt_dir=attempt_dir,
            resume=resume,
            timeout_seconds=12,
            validator=validate_smoke_output,
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
        runner = ScriptedRunner([completed(envelope("official-events"))] * 2)
        adapter = self.adapter(runner)

        adapter.run(self.request())
        adapter.run(self.request(resume=True))

        first, resumed = (call["args"] for call in runner.calls)
        session_id = CLAUDE_SEAT_SESSIONS["official-events"]
        self.assertIn(("--model", CLAUDE_MODEL_ALIAS), tuple(zip(first, first[1:])))
        self.assertIn(("--session-id", session_id), tuple(zip(first, first[1:])))
        self.assertNotIn("--resume", first)
        self.assertIn(("--resume", session_id), tuple(zip(resumed, resumed[1:])))
        self.assertNotIn("--session-id", resumed)
        self.assertIn(("--tools", "WebSearch,WebFetch"), tuple(zip(first, first[1:])))
        self.assertIn(("--allowedTools", "WebSearch,WebFetch"), tuple(zip(first, first[1:])))
        self.assertNotIn("Bash", first)
        self.assertNotIn("Edit", first)

    def test_a_no_search_request_hands_the_seat_no_tools_at_all(self):
        # 封存後的辯論與報告只能依快照：搜尋在能力層關閉，不靠 prompt 叮嚀。
        runner = ScriptedRunner([completed(envelope("official-events"))] * 2)
        adapter = self.adapter(runner)
        request = self.request()
        sealed = ClaudeAttemptRequest(
            seat_id=request.seat_id,
            attempt_id=request.attempt_id,
            prompt=request.prompt,
            attempt_dir=request.attempt_dir,
            timeout_seconds=request.timeout_seconds,
            validator=request.validator,
            allow_search=False,
        )

        adapter.run(sealed)
        resumed = adapter.resumed_request(
            sealed,
            attempt_id="official-events-a2",
            prompt=sealed.prompt,
            attempt_dir=sealed.attempt_dir,
        )
        adapter.run(resumed)

        for call in runner.calls:
            args = call["args"]
            pairs = tuple(zip(args, args[1:]))
            with self.subTest(resume="--resume" in args):
                self.assertIn(("--tools", ""), pairs)
                self.assertIn(("--disallowedTools", "WebSearch,WebFetch"), pairs)
                self.assertNotIn("--allowedTools", args)
                self.assertNotIn(("--tools", "WebSearch,WebFetch"), pairs)
        self.assertFalse(resumed.allow_search)

    def test_only_an_attempt_directory_under_data_root_is_allowed(self):
        runner = ScriptedRunner([])
        adapter = self.adapter(runner)
        outside = ClaudeAttemptRequest(
            seat_id="official-events",
            attempt_id="official-events-a1",
            prompt="x",
            attempt_dir=self.code_root,
        )

        with self.assertRaisesRegex(ValueError, "Data Root"):
            adapter.run(outside)
        self.assertEqual([], runner.calls)

    def test_valid_output_records_actual_model_usage_and_safe_identity(self):
        runner = ScriptedRunner([completed(envelope("official-events"), elapsed_ms=321)])

        result = self.adapter(runner).run(self.request())

        self.assertEqual("ok", result.status)
        self.assertEqual("claude-opus-5", result.actual_model)
        self.assertEqual(321, result.elapsed_ms)
        self.assertEqual(1, result.web_search_requests)
        self.assertEqual(5, result.usage["input_tokens"])
        self.assertEqual("official-events", result.structured_output["seat_id"])
        self.assertNotEqual(CLAUDE_SEAT_SESSIONS["official-events"], result.masked_session_id)
        self.assertEqual(mask_session_id(CLAUDE_SEAT_SESSIONS["official-events"]), result.masked_session_id)

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
        invalid = json.loads(envelope("official-events"))
        del invalid["structured_output"]["search_ok"]
        adapter = self.adapter(
            ScriptedRunner([completed(json.dumps(wrong)), completed(json.dumps(invalid))])
        )

        self.assertEqual("invalid_schema", adapter.run(self.request()).status)
        self.assertEqual("invalid_schema", adapter.run(self.request()).status)

    def test_general_attempt_uses_its_explicit_contract_not_smoke_fields(self):
        structured = {"seat_id": "official-events", "cards": [{"evidence_id": "ev-1"}]}
        called = []

        def validate_cards(value):
            called.append(value)
            if not isinstance(value.get("cards"), list):
                raise ValueError("cards required")

        request = self.request()
        request = ClaudeAttemptRequest(
            seat_id=request.seat_id,
            attempt_id=request.attempt_id,
            prompt=request.prompt,
            attempt_dir=request.attempt_dir,
            timeout_seconds=request.timeout_seconds,
            json_schema={"type": "object"},
            validator=validate_cards,
        )
        adapter = self.adapter(
            ScriptedRunner(
                [completed(envelope("official-events", structured_output=structured))]
            )
        )

        result = adapter.run(request)

        self.assertEqual("ok", result.status)
        self.assertEqual([structured], called)

    def test_general_attempt_without_explicit_validator_fails_closed(self):
        request = self.request()
        request = ClaudeAttemptRequest(
            seat_id=request.seat_id,
            attempt_id=request.attempt_id,
            prompt=request.prompt,
            attempt_dir=request.attempt_dir,
            validator=None,
        )
        result = self.adapter(
            ScriptedRunner([completed(envelope("official-events"))])
        ).run(request)

        self.assertEqual("invalid_schema", result.status)
        self.assertEqual("explicit_validator_required", result.error)

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

    def test_preflight_rejects_unsafe_roots_before_creating_data_root(self):
        for data_root in (self.code_root, self.code_root / "nested-data"):
            runner = ScriptedRunner([])
            report = run_claude_preflight(
                seats=3,
                cli_path="/fake/claude",
                code_root=self.code_root,
                data_root=data_root,
                runner=runner,
                environ={},
                path_exists=lambda _: True,
            )

            self.assertFalse(report["ready"])
            self.assertEqual(["data_root_inside_code_root"], report["reasons"])
            self.assertEqual([], runner.calls)
        self.assertFalse((self.code_root / "nested-data").exists())

    def test_preflight_turns_data_root_permission_shape_errors_into_not_ready(self):
        blocked_root = Path(self._tmp.name) / "not-a-directory"
        blocked_root.write_text("file blocks mkdir", encoding="utf-8")

        report = run_claude_preflight(
            seats=3,
            cli_path="/fake/claude",
            code_root=self.code_root,
            data_root=blocked_root,
            runner=ScriptedRunner([]),
            environ={},
            path_exists=lambda _: True,
        )

        self.assertFalse(report["ready"])
        self.assertEqual(["data_root_unavailable"], report["reasons"])

    def test_preflight_rejects_missing_code_root_before_creating_data_root(self):
        missing_code_root = Path(self._tmp.name) / "missing-code"
        untouched_data_root = Path(self._tmp.name) / "untouched-data"

        report = run_claude_preflight(
            seats=3,
            cli_path="/fake/claude",
            code_root=missing_code_root,
            data_root=untouched_data_root,
            runner=ScriptedRunner([]),
            environ={},
            path_exists=lambda _: True,
        )

        self.assertEqual(["code_root_unavailable"], report["reasons"])
        self.assertFalse(untouched_data_root.exists())

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
        self.assertRegex(report["resume_checkpoint_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(3, report["concurrent_seats"])
        self.assertEqual({"claude-opus-5"}, {seat["actual_model"] for seat in report["seats"]})
        self.assertEqual({1}, {seat["web_search_requests"] for seat in report["seats"]})
        self.assertTrue(any("--resume" in call for call in runner.calls))
        first_session = CLAUDE_SEAT_SESSIONS["official-events"]
        self.assertNotIn(runner.markers[first_session], runner.inputs[-1])

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

    def test_resume_cannot_pass_by_echoing_a_literal_from_the_resume_prompt(self):
        runner = CapabilityRunner(echo_resume=True)

        report = run_claude_preflight(
            seats=3,
            cli_path="/fake/claude",
            code_root=self.code_root,
            data_root=self.data_root,
            runner=runner,
            environ={},
            path_exists=lambda _: True,
        )

        self.assertFalse(report["ready"])
        self.assertFalse(report["resume_ok"])
        self.assertIn("checkpoint_resume_failed", report["reasons"])
        self.assertNotIn("resume-ok", runner.inputs[-1])

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
        adapter = self.adapter(ScriptedRunner([completed(envelope("official-events"))]))
        resumed = adapter.resumed_request(
            request,
            attempt_id="official-events-a2",
            prompt="public checkpoint and debate history",
            attempt_dir=request.attempt_dir,
        )

        result = adapter.run(resumed)

        self.assertTrue(resumed.resume)
        self.assertEqual(request.seat_id, resumed.seat_id)
        self.assertEqual("official-events-a2", result.attempt_id)
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


JOIN_TIMEOUT_SECONDS = 10
# 證明「另一條執行緒真的被鎖擋住」只需要一個有界的觀察窗；拿掉鎖的話它會在
# 這段時間內跑完，斷言就轉紅。這不是用 sleep 猜競態。
RECLAIM_BLOCK_SECONDS = 0.2


class FakeProcessGroup:
    """One controllable POSIX process group behind a ``Popen`` duck type.

    ``pid`` is the group id: every provider invocation is spawned with
    ``start_new_session=True``, so its root leads its own session and group.
    ``poll`` reports the root alone — the answer that must never be mistaken for
    the whole group having been reclaimed.
    """

    def __init__(
        self, pgid, *, survives_term=False, survives_kill=False, root_exited=False
    ):
        self.pid = pgid
        self.survives_term = survives_term
        self.survives_kill = survives_kill
        self.root_exited = root_exited
        self.alive = True
        self.signals = []
        self.finished = threading.Event()

    def poll(self):
        return 0 if self.root_exited or not self.alive else None

    def wait(self, timeout=None):
        """``Popen.wait`` duck type: a worker blocks here until the group dies."""
        self.finished.wait(timeout)
        return self.poll()

    def exit_cleanly(self):
        """整個 group 自己收工：root 退出，也沒有留下任何子孫。"""
        self.root_exited = True
        self._gone()

    def deliver(self, number):
        self.signals.append(number)
        if number == signal.SIGKILL and self.survives_kill:
            return
        if number == signal.SIGTERM and self.survives_term:
            return
        self._gone()

    def _gone(self):
        self.alive = False
        self.finished.set()


class FakeKillpg:
    """``os.killpg`` over fake groups; signal 0 is the only liveness proof."""

    def __init__(self, *groups):
        self.groups = {group.pid: group for group in groups}
        self.calls = []

    def __call__(self, pgid, number):
        self.calls.append((pgid, number))
        group = self.groups.get(pgid)
        if group is None or not group.alive:
            raise ProcessLookupError(pgid)
        if number:
            group.deliver(number)

    def delivered(self):
        return [number for _, number in self.calls if number]


def deaf_killpg(pgid, number):
    """A group this process may not signal: nothing about it can be proven."""
    raise PermissionError(pgid)


class ProcessRegistryTest(unittest.TestCase):
    """終止與註冊會在兩條執行緒上競速：terminate 先到也必須停得掉後到的整組。"""

    def registry(self, *groups):
        self.killpg = FakeKillpg(*groups)
        return ProcessRegistry(killpg=self.killpg)

    def test_a_process_tracked_after_terminate_is_stopped_immediately(self):
        late = FakeProcessGroup(4101)
        registry = self.registry(late)

        self.assertFalse(registry.terminate("official-events-a1", grace_seconds=0))
        self.assertIs(late, registry.track("official-events-a1", late, grace_seconds=0))

        self.assertFalse(late.alive)
        # 這個 group 確實是被截止收尾停掉的，release 必須照實回報。
        self.assertTrue(registry.release("official-events-a1", late))

    def test_a_retry_after_a_delivered_terminate_is_stopped_too(self):
        first = FakeProcessGroup(4102)
        resumed = FakeProcessGroup(4103)
        registry = self.registry(first, resumed)
        registry.track("news-a1", first)

        self.assertTrue(registry.terminate("news-a1", grace_seconds=0))
        registry.release("news-a1", first)
        registry.track("news-a1", resumed, grace_seconds=0)

        self.assertFalse(first.alive)
        self.assertFalse(resumed.alive)

    def test_terminating_one_key_never_poisons_another_seat(self):
        untouched = FakeProcessGroup(4104)
        registry = self.registry(untouched)
        registry.terminate("news-a1", grace_seconds=0)

        registry.track("social-macro-a1", untouched)
        untouched.exit_cleanly()

        self.assertFalse(registry.release("social-macro-a1", untouched))
        self.assertEqual([], self.killpg.delivered())
        self.assertEqual(
            PROCESS_GROUP_NOT_RUNNING, registry.outcome("social-macro-a1")
        )

    def test_a_late_group_that_cannot_be_signalled_never_raises(self):
        registry = ProcessRegistry(killpg=deaf_killpg)
        deaf = FakeProcessGroup(4105)
        registry.terminate("derivatives-a1", grace_seconds=0)

        self.assertIs(deaf, registry.track("derivatives-a1", deaf, grace_seconds=0))
        self.assertEqual(
            PROCESS_TREE_TERMINATION_FAILED, registry.outcome("derivatives-a1")
        )


class ProcessGroupReclaimTest(unittest.TestCase):
    """回收的對象是整個 group：root 不是整棵樹，root poll 也不是消失的證明。"""

    def reclaim(self, group, key="news-a1"):
        self.killpg = FakeKillpg(group)
        registry = ProcessRegistry(killpg=self.killpg)
        registry.track(key, group)
        stopped = registry.terminate(key, grace_seconds=0)
        return registry, stopped

    def test_terminate_signals_the_whole_group_and_never_escalates_needlessly(self):
        group = FakeProcessGroup(5101)

        registry, stopped = self.reclaim(group)

        self.assertTrue(stopped)
        self.assertEqual([signal.SIGTERM], group.signals)
        self.assertEqual(PROCESS_GROUP_RECLAIMED, registry.outcome("news-a1"))

    def test_a_group_that_ignores_sigterm_is_killed_and_still_reclaimed(self):
        group = FakeProcessGroup(5102, survives_term=True)

        registry, stopped = self.reclaim(group)

        self.assertTrue(stopped)
        self.assertEqual([signal.SIGTERM, signal.SIGKILL], group.signals)
        self.assertEqual(PROCESS_GROUP_RECLAIMED, registry.outcome("news-a1"))

    def test_a_group_that_cannot_be_proven_gone_fails_closed(self):
        group = FakeProcessGroup(5103, survives_term=True, survives_kill=True)

        registry, stopped = self.reclaim(group)

        self.assertFalse(stopped)
        self.assertEqual(
            PROCESS_TREE_TERMINATION_FAILED, registry.outcome("news-a1")
        )

    def test_an_exited_root_is_not_proof_that_the_group_is_gone(self):
        # root 先退出、子孫還活著：只看 root PID 會回報「沒有東西需要回收」。
        group = FakeProcessGroup(5104, root_exited=True)

        registry, stopped = self.reclaim(group)

        self.assertTrue(stopped)
        self.assertEqual([signal.SIGTERM], group.signals)
        self.assertEqual(PROCESS_GROUP_RECLAIMED, registry.outcome("news-a1"))

    def test_the_same_invocation_answers_the_same_verdict_every_time(self):
        group = FakeProcessGroup(5105)

        registry, stopped = self.reclaim(group)
        repeated = registry.terminate("news-a1", grace_seconds=0)

        self.assertTrue(stopped)
        # 同一代重問得到同一個答案，而且不會再送第二次訊號。
        self.assertTrue(repeated)
        self.assertEqual([signal.SIGTERM], group.signals)
        self.assertEqual(
            [PROCESS_GROUP_RECLAIMED] * 3,
            [registry.outcome("news-a1") for _ in range(3)],
        )


class InvocationGenerationTest(unittest.TestCase):
    """同一個 attempt key 的第二次 invocation 是新的一代，不沿用前代終局。"""

    def test_a_resume_never_inherits_the_previous_generation_outcome(self):
        first = FakeProcessGroup(6101)
        second = FakeProcessGroup(6102)
        killpg = FakeKillpg(first, second)
        registry = ProcessRegistry(killpg=killpg)

        registry.track("onchain-a1", first)
        first.exit_cleanly()
        self.assertFalse(registry.release("onchain-a1", first))
        self.assertEqual(
            PROCESS_GROUP_NOT_RUNNING, registry.outcome("onchain-a1", 1)
        )

        # cancel 落在第一代收工之後、第二代註冊之前。
        registry.terminate("onchain-a1", grace_seconds=0)
        registry.track("onchain-a1", second, grace_seconds=0)

        self.assertEqual(
            PROCESS_GROUP_NOT_RUNNING, registry.outcome("onchain-a1", 1)
        )
        self.assertEqual(PROCESS_GROUP_RECLAIMED, registry.outcome("onchain-a1", 2))
        self.assertFalse(second.alive)

    def test_an_invocation_that_never_ran_has_no_outcome(self):
        registry = ProcessRegistry(killpg=FakeKillpg())

        self.assertIsNone(registry.outcome("never-started"))
        self.assertFalse(registry.terminate("never-started", grace_seconds=0))
        self.assertIsNone(registry.outcome("never-started"))


class ReclaimLockTest(unittest.TestCase):
    """同 key 的回收共用一把鎖並在鎖內重讀終局；不同 key 不得被序列化。"""

    def start(self, target):
        thread = threading.Thread(target=target)
        thread.start()
        self.addCleanup(thread.join, JOIN_TIMEOUT_SECONDS)
        return thread

    def test_a_clean_finish_is_not_rewritten_by_a_cancel_waiting_on_the_lock(self):
        group = FakeProcessGroup(7101)
        entered = threading.Event()
        gate = threading.Event()
        calls = []

        def killpg(pgid, number):
            calls.append(number)
            if number == 0 and not entered.is_set():
                entered.set()
                gate.wait(JOIN_TIMEOUT_SECONDS)
            raise ProcessLookupError(pgid)  # worker 已乾淨收工，整組都不在了

        registry = ProcessRegistry(killpg=killpg)
        registry.track("social-macro-a1", group)
        group.exit_cleanly()

        settled = self.start(lambda: registry.release("social-macro-a1", group))
        self.assertTrue(entered.wait(JOIN_TIMEOUT_SECONDS))
        cancelled = []
        late = self.start(
            lambda: cancelled.append(
                registry.terminate("social-macro-a1", grace_seconds=0)
            )
        )
        late.join(RECLAIM_BLOCK_SECONDS)
        self.assertTrue(late.is_alive())  # terminate 只能在 reclaim lock 外面等
        gate.set()
        settled.join(JOIN_TIMEOUT_SECONDS)
        late.join(JOIN_TIMEOUT_SECONDS)

        self.assertEqual([False], cancelled)
        self.assertEqual(
            PROCESS_GROUP_NOT_RUNNING, registry.outcome("social-macro-a1")
        )
        self.assertNotIn(signal.SIGTERM, calls)

    def test_the_same_key_never_reclaims_two_generations_at_once(self):
        first = FakeProcessGroup(7201)
        second = FakeProcessGroup(7202)
        killpg = FakeKillpg(first, second)
        entered = threading.Event()
        gate = threading.Event()
        active = []
        peak = []

        def gated(pgid, number):
            if number:
                active.append(pgid)
                peak.append(len(active))
                entered.set()
                gate.wait(JOIN_TIMEOUT_SECONDS)
                try:
                    killpg(pgid, number)
                finally:
                    active.pop()
                return
            killpg(pgid, number)

        registry = ProcessRegistry(killpg=gated)
        registry.track("derivatives-a1", first)

        cancelling = self.start(
            lambda: registry.terminate("derivatives-a1", grace_seconds=0)
        )
        self.assertTrue(entered.wait(JOIN_TIMEOUT_SECONDS))
        tracking = self.start(
            lambda: registry.track("derivatives-a1", second, grace_seconds=0)
        )
        tracking.join(RECLAIM_BLOCK_SECONDS)
        # poisoned track 的回收必須進同一把鎖，否則它會和 cancel 同時動手。
        self.assertTrue(tracking.is_alive())
        gate.set()
        cancelling.join(JOIN_TIMEOUT_SECONDS)
        tracking.join(JOIN_TIMEOUT_SECONDS)

        self.assertEqual([1, 1], peak)
        self.assertFalse(first.alive)
        self.assertFalse(second.alive)

    def test_two_different_keys_reclaim_in_parallel(self):
        first = FakeProcessGroup(7301)
        second = FakeProcessGroup(7302)
        killpg = FakeKillpg(first, second)
        barrier = threading.Barrier(2, timeout=JOIN_TIMEOUT_SECONDS)
        serialised = []

        def paired(pgid, number):
            if number:
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    serialised.append(pgid)
            killpg(pgid, number)

        registry = ProcessRegistry(killpg=paired)
        registry.track("onchain-a1", first)
        registry.track("news-a1", second)

        threads = [
            self.start(lambda: registry.terminate("onchain-a1", grace_seconds=0)),
            self.start(lambda: registry.terminate("news-a1", grace_seconds=0)),
        ]
        for thread in threads:
            thread.join(JOIN_TIMEOUT_SECONDS)

        self.assertEqual([], serialised)
        self.assertFalse(first.alive)
        self.assertFalse(second.alive)


class UnreclaimedTreeAdapterTest(unittest.TestCase):
    """整組回收不了時，adapter 邊界必須先 fail closed（Reviewer A-1 第二次）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.code_root = root / "code"
        self.data_root = root / "data"
        self.code_root.mkdir()
        self.data_root.mkdir()
        self.validator_calls = []

    def request(self, seat_id="official-events"):
        attempt_dir = self.data_root / "agents" / seat_id / "a1"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        return ClaudeAttemptRequest(
            seat_id=seat_id,
            attempt_id="{}-a1".format(seat_id),
            prompt="Return the public smoke result.",
            attempt_dir=attempt_dir,
            timeout_seconds=12,
            validator=self.validator_calls.append,
        )

    def run_with(self, output):
        adapter = ClaudeAdapter(
            runner=ScriptedRunner([output]),
            code_root=self.code_root,
            data_root=self.data_root,
        )
        return adapter.run(self.request())

    def test_a_clean_looking_result_is_refused_when_the_tree_was_not_reclaimed(self):
        # runner 沒有逾時、CLI 也回了合法 JSON，唯一的問題是整組證明不了回收。
        result = self.run_with(
            completed(
                envelope("official-events"),
                returncode=0,
                timed_out=False,
                process_outcome=PROCESS_TREE_TERMINATION_FAILED,
            )
        )

        self.assertNotEqual("ok", result.status)
        self.assertEqual(PROCESS_TREE_TERMINATION_FAILED, result.status)
        self.assertEqual(PROCESS_TREE_TERMINATION_FAILED, result.error)
        self.assertIsNone(result.structured_output)
        self.assertIsNotNone(result.scheduler_failure_kind)
        # 連 parse 與 validator 都不准跑：這份輸出永遠不可採用。
        self.assertEqual([], self.validator_calls)

    def test_a_reclaimed_tree_still_admits_its_structured_output(self):
        result = self.run_with(
            completed(
                envelope("official-events"),
                returncode=0,
                process_outcome=PROCESS_GROUP_RECLAIMED,
            )
        )

        self.assertEqual("ok", result.status)
        self.assertIsNotNone(result.structured_output)
        self.assertEqual(1, len(self.validator_calls))


class UnreclaimableProcess:
    """``Popen`` duck type：逾時後整組回收不了，子孫還握著管道不放。"""

    def __init__(self, pid=9401):
        self.pid = pid
        self.communicate_calls = 0
        self.unbounded_drain = threading.Event()

    def communicate(self, input=None, timeout=None):
        self.communicate_calls += 1
        if self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(["fake-cli"], timeout)
        if timeout is None:
            # 無界 drain：孫代還握著 stdout，這裡永遠等不到 EOF。
            self.unbounded_drain.set()
            threading.Event().wait(30)
            raise AssertionError("無界 drain 不該被呼叫")
        raise subprocess.TimeoutExpired(
            ["fake-cli"], timeout, output="partial-stdout", stderr="partial-stderr"
        )

    def poll(self):
        return None


class FixedChildRunner(TerminatingRunner):
    """The real runner with only the spawn syscall replaced."""

    def __init__(self, registry, process, key_source=None):
        super().__init__(registry, key_source=key_source)
        self.process = process

    def _spawn(self, args, cwd):
        return self.process


class TerminatingRunnerFailurePathTest(unittest.TestCase):
    """整組證明不了回收時，worker 不得卡在無界 drain 上（Reviewer B-1）。"""

    def test_an_unreclaimable_tree_drains_within_a_bound_and_reports_the_failure(self):
        process = UnreclaimableProcess()
        registry = ProcessRegistry(killpg=deaf_killpg)
        runner = FixedChildRunner(registry, process, key_source=lambda: "seat-key")
        outputs = []
        worker = threading.Thread(
            target=lambda: outputs.append(
                runner.run(
                    ["fake-cli"], input_text="", cwd=".", timeout_seconds=0.01
                )
            ),
            daemon=True,
        )

        worker.start()
        worker.join(JOIN_TIMEOUT_SECONDS)

        self.assertFalse(worker.is_alive())
        self.assertFalse(process.unbounded_drain.is_set())
        self.assertEqual(2, process.communicate_calls)
        [output] = outputs
        self.assertTrue(output.timed_out)
        self.assertIsNone(output.returncode)
        self.assertEqual("partial-stdout", output.stdout)
        self.assertEqual("partial-stderr", output.stderr)
        # 回收失敗必須以機器可讀的終局傳到 runner 邊界，不是靠訊息字串反推。
        self.assertEqual(PROCESS_TREE_TERMINATION_FAILED, output.process_outcome)
        self.assertEqual(PROCESS_TREE_TERMINATION_FAILED, registry.outcome("seat-key"))


# 假 CLI：印出自己的 pid／pgid／sid，並在退出前先把 root→child→grandchild
# 整棵樹建立起來，讓測試不必猜時序。descend 的兩層會一直睡下去，只有整組回收
# 才停得掉。這個 fixture 不接觸任何真實 Provider。
PREFLIGHT_TREE_CLI = """\
#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path

if sys.argv[1] == "descend":
    home = Path(sys.argv[2])
    depth = int(sys.argv[3])
else:
    home = Path({markers!r}) / str(os.getpid())
    home.mkdir(parents=True)
    depth = 2
(home / str(os.getpid())).write_text("x", encoding="utf-8")
if depth:
    subprocess.Popen(
        [sys.executable, __file__, "descend", str(home), str(depth - 1)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
if sys.argv[1] == "descend":
    time.sleep(600)
    sys.exit(0)
deadline = time.monotonic() + 10
while time.monotonic() < deadline and len(list(home.iterdir())) < 3:
    time.sleep(0.01)
print(os.getpid(), os.getpgid(0), os.getsid(0))
"""


class PreflightProcessGroupTest(unittest.TestCase):
    """正式 preflight 的預設 runner 必須與研究呼叫走同一套 group 契約（B-2）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.code_root = root / "code"
        self.data_root = root / "data"
        self.code_root.mkdir()
        self.markers = root / "markers"
        self.markers.mkdir()
        self.cli = root / "fake-claude"
        self.cli.write_text(
            PREFLIGHT_TREE_CLI.format(markers=str(self.markers)), encoding="utf-8"
        )
        self.cli.chmod(0o755)
        self.addCleanup(self.reap_survivors)

    def spawned_pids(self):
        return [
            int(path.name)
            for home in sorted(self.markers.iterdir())
            for path in sorted(home.iterdir())
        ]

    def reap_survivors(self):
        for pid in self.spawned_pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def run_preflight(self):
        return run_claude_preflight(
            seats=3,
            cli_path=self.cli,
            code_root=self.code_root,
            data_root=self.data_root,
            environ={},
        )

    def test_the_default_preflight_runner_spawns_its_own_session_and_group(self):
        report = self.run_preflight()

        pid, pgid, sid = (int(value) for value in report["cli_version"].split())
        self.assertEqual((pid, pid), (pgid, sid))
        self.assertNotEqual(os.getpgid(0), pgid)

    def test_the_default_preflight_runner_leaves_no_surviving_process_tree(self):
        self.run_preflight()

        pids = self.spawned_pids()
        self.assertEqual(6, len(pids))  # 兩次 CLI 呼叫，各 root＋child＋grandchild
        survivors = []
        for pid in pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            survivors.append(pid)
        self.assertEqual([], survivors)


if __name__ == "__main__":
    unittest.main()
