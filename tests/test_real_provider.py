"""Ticket T2 real seat runner behaviour; no test contacts a real provider.

Both provider seams are injected: ``ClaudeAdapter(runner=...)`` receives
scripted ``ProcessOutput`` values and ``AntigravityAdapter(runner=...)``
receives hand-built stream-json envelopes.
"""

import json
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from hoya_market_agents.antigravity_adapter import AntigravityAdapter
from hoya_market_agents.claude_adapter import (
    CLAUDE_SEAT_SESSIONS,
    TERMINATED_AT_DEADLINE,
    ClaudeAdapter,
    ProcessOutput,
    ProcessRegistry,
    TerminatingRunner,
)
from hoya_market_agents.codex_exec_adapter import (
    CodexExecOutputError,
    CodexExecProcessError,
    CodexExecResult,
    CodexExecTimeout,
)
from hoya_market_agents.contract_validator import MAX_EVIDENCE_CARDS_PER_SEAT
from hoya_market_agents.question_package import build_question_package
from hoya_market_agents.real_provider import (
    ANTIGRAVITY_SEAT_IDS,
    CLAUDE_SEAT_IDS,
    CLAUDE_TIMEOUT_SECONDS,
    CODEX_MODES,
    CODEX_SEAT_IDS,
    LOCAL_WORKER_COUNT,
    POST_SEAL_SEARCH,
    PRIMARY_MODELS,
    REPLACEMENT_MODELS,
    RESEARCH_ENVELOPE_SCHEMA,
    DebateDispatch,
    RealEvidenceGateway,
    RealProviderError,
    RealSeatRunner,
    TrailingCommaRepairer,
    build_attempt_prompt,
    validate_research_envelope_shape,
)
from hoya_market_agents.recovery_state_machine import ResearchAttempt, SeatRecoveryState
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.seats import SEAT_IDS, load_roster

QUESTION = "BTC 過去 14 日的市場狀態如何？"
STAMP = "2026-08-01T02:00:00Z"
DEBATE_SCHEMA = {
    "type": "object",
    "properties": {"seat_id": {"type": "string"}, "stance": {"type": "string"}},
    "required": ["seat_id", "stance"],
    "additionalProperties": False,
}
SLEEP_ARGV = [sys.executable, "-c", "import time; time.sleep(30)"]
JOIN_TIMEOUT_SECONDS = 10


def evidence_card(run_id, seat_id, attempt_id, index=1):
    return {
        "schema_version": "1.0.0",
        "evidence_id": "{}-{:02d}".format(seat_id, index),
        "run_id": run_id,
        "seat_id": seat_id,
        "attempt_id": attempt_id,
        "phase": "research",
        "created_at_utc": STAMP,
        "elapsed_ms": 1_000,
        "asset": "BTC",
        "category": "spot-price",
        "statement": "測試用證據陳述，僅驗證 runner 行為。",
        "direction": "support",
        "source_url": "https://fake.invalid/{}/{}".format(seat_id, index),
        "source_origin": "fake-source:{}-{}".format(seat_id, index),
        "source_tier": 1,
        "published_at_utc": STAMP,
        "retrieved_at_utc": STAMP,
        "excerpt": "close 68,420",
        "credibility_note": "測試資料，不是真實市場證據。",
    }


def envelope(run_id, seat_id, attempt_id):
    return {
        "seat_id": seat_id,
        "evidence_cards": [evidence_card(run_id, seat_id, attempt_id)],
    }


def claude_stdout(
    structured_output,
    actual_model="claude-opus-5",
    web_search_requests=2,
    web_fetch_requests=1,
):
    return json.dumps(
        {
            "is_error": False,
            "structured_output": structured_output,
            "usage": {
                "input_tokens": 5,
                "output_tokens": 7,
                "server_tool_use": {
                    "web_search_requests": web_search_requests,
                    "web_fetch_requests": web_fetch_requests,
                },
            },
            "modelUsage": {actual_model: {"canonicalModel": actual_model}},
        },
        ensure_ascii=False,
    )


def claude_ok(run_id, seat_id, attempt_id, **proof):
    return ProcessOutput(
        returncode=0,
        stdout=claude_stdout(envelope(run_id, seat_id, attempt_id), **proof),
        stderr="",
        elapsed_ms=12,
    )


def claude_process_error():
    return ProcessOutput(returncode=1, stdout="", stderr="session busy", elapsed_ms=3)


def claude_timeout():
    return ProcessOutput(
        returncode=None, stdout="", stderr="", elapsed_ms=270_000, timed_out=True
    )


def claude_malformed():
    return ProcessOutput(returncode=0, stdout="not json at all", stderr="", elapsed_ms=9)


def agy_stream(structured_output, search_succeeded=True):
    events = [
        {
            "event": "init",
            "init": {"model": "gemini-3.1-pro-high", "tools": ["search_web"]},
        },
    ]
    if search_succeeded:
        events.append(
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "tool",
                    "tool_name": "search_web",
                    "state": "DONE",
                },
            }
        )
    events += [
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "structured_output": structured_output,
                "duration_seconds": 2.5,
                "usage": {"input_tokens": 10, "output_tokens": 4},
            },
        },
    ]
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"


class FakeClaudeRunner:
    """Scripted Claude CLI seam keyed by seat, recording every invocation."""

    def __init__(self, outputs=None):
        self.outputs = {seat: list(items) for seat, items in (outputs or {}).items()}
        self.calls = []

    def run(self, args, *, input_text, cwd, timeout_seconds):
        args = tuple(args)
        session_flag = "--session-id" if "--session-id" in args else "--resume"
        session_id = args[args.index(session_flag) + 1]
        seat_id = next(
            seat
            for seat, configured in CLAUDE_SEAT_SESSIONS.items()
            if configured == session_id
        )
        self.calls.append(
            {
                "seat_id": seat_id,
                "args": args,
                "prompt": input_text,
                "cwd": Path(cwd),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.outputs[seat_id].pop(0)


class FakeAgyRunner:
    """Antigravity CLI seam returning one hand-built stream-json envelope."""

    def __init__(self, stream="", returncode=0, stderr="", timed_out=False):
        self.stream = stream
        self.returncode = returncode
        self.stderr = stderr
        self.timed_out = timed_out
        self.calls = []

    def __call__(self, argv, cwd, timeout):
        self.calls.append((list(argv), Path(cwd), timeout))
        if self.timed_out:
            raise subprocess.TimeoutExpired(list(argv), timeout)
        return subprocess.CompletedProcess(
            list(argv), self.returncode, self.stream, self.stderr
        )


class FakeCodexAdapter:
    """``codex exec`` seam: one scripted structured output or one scripted error."""

    def __init__(self, structured_output=None, error=None, search_invocations=1):
        self.structured_output = structured_output
        self.error = error
        self.search_invocations = search_invocations
        self.calls = []

    def invoke(self, prompt, schema, work_dir, allow_search=True):
        work_dir = Path(work_dir)
        self.calls.append(
            {
                "prompt": prompt,
                "schema": schema,
                "work_dir": work_dir,
                "allow_search": allow_search,
            }
        )
        if self.error is not None:
            raise self.error
        return CodexExecResult(
            structured_output=self.structured_output,
            elapsed_ms=4_200,
            schema_path=work_dir / "codex-output-schema.json",
            last_message_path=work_dir / "codex-last-message.txt",
            search_invocations=self.search_invocations,
        )


class FakeProcess:
    """``Popen`` duck type：terminate 讓等待中的 worker 立刻收工。"""

    def __init__(self):
        self.finished = threading.Event()
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True
        self.finished.set()

    def kill(self):
        self.killed = True
        self.finished.set()

    def poll(self):
        return 0 if self.finished.is_set() else None


class UnstoppableProcess(FakeProcess):
    """A provider process whose signals fail; cancellation must still not raise."""

    def terminate(self):
        raise OSError("no such process")

    def kill(self):
        raise OSError("no such process")


class BlockingCodexAdapter:
    """Registers a live process the way ``TerminatingRunner`` does, then blocks."""

    def __init__(self, registry, process, key_source=None):
        self.registry = registry
        self.process = process
        self.key_source = key_source or threading.get_ident
        self.key = None
        self.started = threading.Event()

    def invoke(self, prompt, schema, work_dir, allow_search=True):
        key = self.key_source()
        self.key = key
        self.registry.track(key, self.process)
        self.started.set()
        try:
            self.process.finished.wait(timeout=JOIN_TIMEOUT_SECONDS)
        finally:
            self.registry.release(key, self.process)
        raise CodexExecProcessError(
            "codex exec 非零結束（exit -15）：{}".format(TERMINATED_AT_DEADLINE)
        )


class TerminatingRunnerTest(unittest.TestCase):
    """真實 subprocess：截止後還在燒訂閱的 provider 進程必須真的停得下來。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cwd = Path(self._tmp.name)
        self.registry = ProcessRegistry()
        self.runner = TerminatingRunner(self.registry, key_source=lambda: "seat-key")

    def run_in_thread(self, argv, timeout_seconds=30):
        outputs = []
        worker = threading.Thread(
            target=lambda: outputs.append(
                self.runner.run(
                    argv, input_text="", cwd=self.cwd, timeout_seconds=timeout_seconds
                )
            )
        )
        worker.start()
        self.addCleanup(worker.join, JOIN_TIMEOUT_SECONDS)
        return worker, outputs

    def test_terminate_stops_a_live_process_and_marks_the_deadline(self):
        worker, outputs = self.run_in_thread(SLEEP_ARGV)

        # 收尾與註冊在兩條執行緒上競速。terminate 先毒住 key，所以無論它落在
        # 註冊之前或之後，這個 sleep 進程都停得掉，測試也不必去猜順序。
        self.registry.terminate("seat-key")
        worker.join(timeout=JOIN_TIMEOUT_SECONDS)

        self.assertFalse(worker.is_alive())
        [output] = outputs
        self.assertNotEqual(0, output.returncode)
        self.assertFalse(output.timed_out)
        self.assertIn(TERMINATED_AT_DEADLINE, output.stderr)

    def test_a_finished_process_leaves_nothing_to_terminate(self):
        output = self.runner.run(
            [sys.executable, "-c", "print('done')"],
            input_text="",
            cwd=self.cwd,
            timeout_seconds=30,
        )

        self.assertEqual(0, output.returncode)
        self.assertIn("done", output.stdout)
        self.assertNotIn(TERMINATED_AT_DEADLINE, output.stderr)
        self.assertFalse(self.registry.terminate("seat-key"))

    def test_timeout_kills_the_child_and_reports_the_timeout(self):
        output = self.runner.run(
            SLEEP_ARGV, input_text="", cwd=self.cwd, timeout_seconds=0.5
        )

        self.assertTrue(output.timed_out)
        self.assertIsNone(output.returncode)
        self.assertFalse(self.registry.terminate("seat-key"))

    def test_a_missing_executable_reports_a_process_error_instead_of_raising(self):
        output = self.runner.run(
            [str(self.cwd / "not-a-binary")],
            input_text="",
            cwd=self.cwd,
            timeout_seconds=5,
        )

        self.assertEqual(127, output.returncode)
        self.assertTrue(output.stderr)

    def test_the_antigravity_callable_shape_is_terminable_too(self):
        completed = self.runner.run_process(
            [sys.executable, "-c", "print('agy')"], cwd=self.cwd, timeout=30
        )

        self.assertEqual(0, completed.returncode)
        self.assertIn("agy", completed.stdout)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.runner.run_process(SLEEP_ARGV, cwd=self.cwd, timeout=0.5)

    def test_terminating_an_unstoppable_process_never_raises(self):
        self.registry.track("seat-key", UnstoppableProcess())

        self.assertFalse(self.registry.terminate("seat-key"))


class RealSeatRunnerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.code_root = root / "code"
        self.data_root = root / "data"
        self.code_root.mkdir()
        self.data_root.mkdir()
        self.cli = root / "agy"
        self.cli.write_text("fixture", encoding="utf-8")
        self.cli.chmod(0o700)
        self.run_id = "20260801T020000Z-btc-abc123"
        self.store = RunStore(self.data_root)
        self.run = self.store.create_run(self.run_id, SEAT_IDS)
        self.inbox_requests = self.data_root / "inbox" / self.run_id / "requests"
        self.results = queue.Queue()
        self.scope = build_question_package(QUESTION)
        self.claude_runner = FakeClaudeRunner()
        self.agy_runner = FakeAgyRunner()
        self.codex_adapter = FakeCodexAdapter()

    def attempt(self, seat_id, model=None, kind="primary", attempt_id=None):
        attempt_id = attempt_id or "{}-a1".format(seat_id)
        return ResearchAttempt(
            attempt_id=attempt_id,
            seat_id=seat_id,
            model=model or PRIMARY_MODELS[seat_id],
            kind=kind,
            original_attempt_id=attempt_id,
        )

    def build_runner(
        self, claude_outputs=None, agy_runner=None, codex_adapter=None, codex_mode="inbox"
    ):
        """Every provider seam is injected, so no test can reach a real CLI."""
        self.claude_runner = FakeClaudeRunner(claude_outputs)
        self.agy_runner = agy_runner or self.agy_runner
        self.codex_adapter = codex_adapter or self.codex_adapter
        runner = RealSeatRunner(
            self.run,
            self.data_root,
            self.code_root,
            self.results,
            self.scope,
            self.inbox_requests,
            claude_adapter=ClaudeAdapter(
                runner=self.claude_runner,
                code_root=self.code_root,
                data_root=self.data_root,
            ),
            antigravity_adapter=AntigravityAdapter(
                cli_path=self.cli,
                code_root=self.code_root,
                data_root=self.data_root,
                runner=self.agy_runner,
            ),
            codex_adapter=self.codex_adapter,
            codex_mode=codex_mode,
        )
        self.addCleanup(runner.shutdown)
        return runner

    def drain(self, runner, expected):
        runner.shutdown()
        messages = []
        while len(messages) < expected:
            messages.append(self.results.get(timeout=10))
        self.assertTrue(self.results.empty())
        return messages

    def test_four_local_seats_start_in_parallel_and_report_valid_results(self):
        attempts = {
            seat_id: self.attempt(seat_id)
            for seat_id in CLAUDE_SEAT_IDS + ANTIGRAVITY_SEAT_IDS
        }
        counter = attempts["counter-evidence"]
        runner = self.build_runner(
            claude_outputs={
                seat_id: [claude_ok(self.run_id, seat_id, attempts[seat_id].attempt_id)]
                for seat_id in CLAUDE_SEAT_IDS
            },
            agy_runner=FakeAgyRunner(
                stream=agy_stream(
                    envelope(self.run_id, counter.seat_id, counter.attempt_id)
                )
            ),
        )

        for attempt in attempts.values():
            self.assertIs(True, runner.start(attempt, None))
        messages = self.drain(runner, 4)

        gateway = RealEvidenceGateway(self.run_id, self.scope.assets)
        by_attempt = {message[1]: message for message in messages}
        self.assertEqual(
            sorted(attempt.attempt_id for attempt in attempts.values()),
            sorted(by_attempt),
        )
        for attempt in attempts.values():
            kind, _, raw_output = by_attempt[attempt.attempt_id]
            self.assertEqual("result", kind)
            cards = gateway.validate(attempt, raw_output)
            self.assertEqual([attempt.seat_id], [card["seat_id"] for card in cards])

    def test_claude_work_directory_never_collides_with_the_attempts_directory(self):
        attempt = self.attempt("news")
        runner = self.build_runner(
            claude_outputs={"news": [claude_ok(self.run_id, "news", attempt.attempt_id)]}
        )

        runner.start(attempt, None)
        self.drain(runner, 1)

        work_dir = self.claude_runner.calls[0]["cwd"]
        self.assertEqual(
            self.run.path / "agents" / "news" / "work" / attempt.attempt_id, work_dir
        )
        self.assertNotIn("attempts", work_dir.parts)
        self.assertTrue(
            self.run.record_attempt(
                "news", attempt.attempt_id, "[]", {"schema_version": "1.0.0", "records": []}
            )
        )

    def test_claude_request_carries_the_frozen_research_output_contract(self):
        attempt = self.attempt("social-macro")
        runner = self.build_runner(
            claude_outputs={
                "social-macro": [claude_ok(self.run_id, "social-macro", attempt.attempt_id)]
            }
        )

        runner.start(attempt, None)
        self.drain(runner, 1)

        call = self.claude_runner.calls[0]
        self.assertEqual(225, CLAUDE_TIMEOUT_SECONDS)
        self.assertEqual(CLAUDE_TIMEOUT_SECONDS, call["timeout_seconds"])
        self.assertIn(
            json.dumps(RESEARCH_ENVELOPE_SCHEMA, separators=(",", ":")), call["args"]
        )
        prompt = call["prompt"]
        self.assertIn(self.run_id, prompt)
        self.assertIn(attempt.attempt_id, prompt)
        self.assertIn("evidence_cards", prompt)
        self.assertIn("不得建立任何額外 agent", prompt)

    def test_a_comparison_question_moves_the_claude_timeout_with_its_own_wall(self):
        """Ticket R7: 兩幣題的收件牆在 T+4:20，研究呼叫的 timeout 跟著它走。"""
        self.scope = build_question_package("比較 BTC 與 ETH 過去 14 日的相對強弱")
        attempt = self.attempt("news")
        runner = self.build_runner(
            claude_outputs={"news": [claude_ok(self.run_id, "news", attempt.attempt_id)]}
        )

        runner.start(attempt, None)
        self.drain(runner, 1)

        self.assertEqual(255, self.claude_runner.calls[0]["timeout_seconds"])

    def test_claude_process_error_is_retried_once_in_the_same_session(self):
        attempt = self.attempt("official-events")
        runner = self.build_runner(
            claude_outputs={
                "official-events": [
                    claude_process_error(),
                    claude_ok(self.run_id, "official-events", attempt.attempt_id),
                ]
            }
        )

        runner.start(attempt, None)
        messages = self.drain(runner, 1)

        self.assertEqual("result", messages[0][0])
        self.assertEqual(2, len(self.claude_runner.calls))
        first, second = self.claude_runner.calls
        # 首次呼叫建立 session；resume 必須沿用同一個 work dir，
        # 因為 Claude session 依專案目錄隔離，換目錄 resume 找不到 session。
        self.assertIn("--session-id", first["args"])
        self.assertNotIn("--resume", first["args"])
        self.assertIn("--resume", second["args"])
        self.assertEqual(first["cwd"], second["cwd"])

    def test_claude_failures_map_to_the_scheduler_failure_kinds(self):
        cases = {
            "timeout": [claude_timeout()],
            "process_error": [claude_process_error(), claude_process_error()],
            "provider_error": [claude_malformed()],
        }
        for expected_kind, outputs in cases.items():
            with self.subTest(failure_kind=expected_kind):
                self.setUp()
                attempt = self.attempt("news")
                runner = self.build_runner(claude_outputs={"news": outputs})

                runner.start(attempt, None)
                messages = self.drain(runner, 1)

                kind, attempt_id, failure_kind, message = messages[0]
                self.assertEqual("failure", kind)
                self.assertEqual(attempt.attempt_id, attempt_id)
                self.assertEqual(expected_kind, failure_kind)
                self.assertTrue(message)

    def test_claude_result_from_another_model_is_refused(self):
        attempt = self.attempt("news")
        runner = self.build_runner(
            claude_outputs={
                "news": [
                    claude_ok(
                        self.run_id,
                        "news",
                        attempt.attempt_id,
                        actual_model="claude-sonnet-4-5",
                    )
                ]
            }
        )

        runner.start(attempt, None)
        messages = self.drain(runner, 1)

        kind, attempt_id, failure_kind, message = messages[0]
        self.assertEqual("failure", kind)
        self.assertEqual(attempt.attempt_id, attempt_id)
        self.assertEqual("provider_error", failure_kind)
        self.assertIn("actual_model_mismatch:claude-sonnet-4-5", message)

    def test_claude_result_without_live_research_proof_is_refused(self):
        attempt = self.attempt("official-events")
        runner = self.build_runner(
            claude_outputs={
                "official-events": [
                    claude_ok(
                        self.run_id,
                        "official-events",
                        attempt.attempt_id,
                        web_search_requests=0,
                        web_fetch_requests=0,
                    )
                ]
            }
        )

        runner.start(attempt, None)
        messages = self.drain(runner, 1)

        kind, _, failure_kind, message = messages[0]
        self.assertEqual("failure", kind)
        self.assertEqual("provider_error", failure_kind)
        self.assertIn("no_live_research_proof", message)

    def test_claude_web_fetch_alone_still_proves_live_research(self):
        attempt = self.attempt("social-macro")
        runner = self.build_runner(
            claude_outputs={
                "social-macro": [
                    claude_ok(
                        self.run_id,
                        "social-macro",
                        attempt.attempt_id,
                        web_search_requests=0,
                        web_fetch_requests=1,
                    )
                ]
            }
        )

        runner.start(attempt, None)
        messages = self.drain(runner, 1)

        self.assertEqual("result", messages[0][0])

    def test_claude_model_usage_search_count_alone_still_proves_live_research(self):
        # 真實 CLI 可能把搜尋次數記在 modelUsage 而非 usage.server_tool_use；
        # 判準必須與賽前 READY 閘門同一套（result.web_search_requests 對帳值）。
        attempt = self.attempt("news")
        stdout = json.dumps(
            {
                "is_error": False,
                "structured_output": envelope(self.run_id, "news", attempt.attempt_id),
                "usage": {"input_tokens": 5, "output_tokens": 7},
                "modelUsage": {
                    "claude-opus-5": {
                        "canonicalModel": "claude-opus-5",
                        "webSearchRequests": 2,
                    }
                },
            },
            ensure_ascii=False,
        )
        runner = self.build_runner(
            claude_outputs={
                "news": [
                    ProcessOutput(returncode=0, stdout=stdout, stderr="", elapsed_ms=12)
                ]
            }
        )

        runner.start(attempt, None)
        messages = self.drain(runner, 1)

        self.assertEqual("result", messages[0][0])

    def test_cancel_blocks_the_claude_resume_from_spawning_a_new_process(self):
        # cancel 之後的 resume 會生出沒人會再 terminate 的新進程；
        # 必須只呼叫一次 CLI，且結果被抑制。
        attempt = self.attempt("official-events")
        runner = self.build_runner(
            claude_outputs={
                "official-events": [claude_process_error(), claude_process_error()]
            }
        )

        runner.cancel(attempt.attempt_id)
        runner.start(attempt, None)
        runner.shutdown(wait=True)

        self.assertEqual(1, len(self.claude_runner.calls))
        self.assertTrue(self.results.empty())

    def test_antigravity_result_without_a_successful_search_is_refused(self):
        attempt = self.attempt("counter-evidence")
        runner = self.build_runner(
            agy_runner=FakeAgyRunner(
                stream=agy_stream(
                    envelope(self.run_id, attempt.seat_id, attempt.attempt_id),
                    search_succeeded=False,
                )
            )
        )

        runner.start(attempt, None)
        messages = self.drain(runner, 1)

        kind, attempt_id, failure_kind, message = messages[0]
        self.assertEqual("failure", kind)
        self.assertEqual(attempt.attempt_id, attempt_id)
        self.assertEqual("provider_error", failure_kind)
        self.assertIn("search_web", message)

    def test_codex_result_without_a_search_invocation_is_refused(self):
        attempt = self.attempt("onchain")
        runner = self.build_runner(
            codex_mode="cli",
            codex_adapter=FakeCodexAdapter(
                structured_output=envelope(
                    self.run_id, attempt.seat_id, attempt.attempt_id
                ),
                search_invocations=0,
            ),
        )

        runner.start(attempt, None)
        messages = self.drain(runner, 1)

        kind, attempt_id, failure_kind, message = messages[0]
        self.assertEqual("failure", kind)
        self.assertEqual(attempt.attempt_id, attempt_id)
        self.assertEqual("provider_error", failure_kind)
        self.assertIn("no_live_research_proof", message)

    def test_antigravity_timeout_and_provider_error_map_to_failure_kinds(self):
        cases = {
            "timeout": FakeAgyRunner(timed_out=True),
            "provider_error": FakeAgyRunner(stream="", returncode=2, stderr="boom"),
        }
        for expected_kind, agy_runner in cases.items():
            with self.subTest(failure_kind=expected_kind):
                self.setUp()
                attempt = self.attempt("counter-evidence")
                runner = self.build_runner(agy_runner=agy_runner)

                runner.start(attempt, None)
                messages = self.drain(runner, 1)

                kind, attempt_id, failure_kind, message = messages[0]
                self.assertEqual("failure", kind)
                self.assertEqual(attempt.attempt_id, attempt_id)
                self.assertEqual(expected_kind, failure_kind)
                self.assertTrue(message)

    def test_antigravity_work_directory_is_fresh_and_outside_attempts(self):
        attempt = self.attempt("counter-evidence")
        runner = self.build_runner(
            agy_runner=FakeAgyRunner(
                stream=agy_stream(
                    envelope(self.run_id, attempt.seat_id, attempt.attempt_id)
                )
            )
        )

        runner.start(attempt, None)
        self.drain(runner, 1)

        work_dir = self.agy_runner.calls[0][1]
        self.assertEqual(
            self.run.path
            / "agents"
            / "counter-evidence"
            / "work"
            / attempt.attempt_id,
            work_dir,
        )
        self.assertNotIn("attempts", work_dir.parts)
        self.assertTrue((work_dir / "raw-envelope.jsonl").is_file())

    def test_codex_inbox_mode_returns_literal_true_and_writes_the_request_once(self):
        runner = self.build_runner(codex_mode="inbox")
        attempt = self.attempt("spot-technical", kind="primary")

        started = runner.start(attempt, None)

        self.assertIs(True, started)
        target = self.inbox_requests / "spot-technical-a1.json"
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(self.run_id, payload["run_id"])
        self.assertEqual("spot-technical", payload["seat_id"])
        self.assertEqual("spot-technical-a1", payload["attempt_id"])
        self.assertEqual("primary", payload["kind"])
        self.assertIsNone(payload["reason"])
        self.assertTrue(self.results.empty())
        self.assertEqual([], self.codex_adapter.calls)
        with self.assertRaises(RealProviderError):
            runner.start(attempt, None)

    def test_codex_cli_mode_answers_through_the_queue_and_still_audits_the_request(self):
        attempt = self.attempt("onchain")
        runner = self.build_runner(
            codex_mode="cli",
            codex_adapter=FakeCodexAdapter(
                structured_output=envelope(
                    self.run_id, attempt.seat_id, attempt.attempt_id
                )
            ),
        )

        self.assertIs(True, runner.start(attempt, None))
        messages = self.drain(runner, 1)

        kind, attempt_id, raw_output = messages[0]
        self.assertEqual("result", kind)
        self.assertEqual(attempt.attempt_id, attempt_id)
        cards = RealEvidenceGateway(self.run_id, self.scope.assets).validate(
            attempt, raw_output
        )
        self.assertEqual([attempt.seat_id], [card["seat_id"] for card in cards])
        # The audit trail stays intact even though nothing reads the inbox now.
        self.assertTrue((self.inbox_requests / "onchain-a1.json").is_file())

    def test_codex_cli_mode_sends_the_shared_prompt_into_a_fresh_work_directory(self):
        attempt = self.attempt("derivatives")
        runner = self.build_runner(
            codex_mode="cli",
            codex_adapter=FakeCodexAdapter(
                structured_output=envelope(
                    self.run_id, attempt.seat_id, attempt.attempt_id
                )
            ),
        )

        runner.start(attempt, None)
        self.drain(runner, 1)

        [call] = self.codex_adapter.calls
        self.assertEqual(RESEARCH_ENVELOPE_SCHEMA, call["schema"])
        self.assertEqual(
            self.run.path / "agents" / "derivatives" / "work" / attempt.attempt_id,
            call["work_dir"],
        )
        self.assertNotIn("attempts", call["work_dir"].parts)
        prompt = call["prompt"]
        self.assertIn(self.run_id, prompt)
        self.assertIn(attempt.attempt_id, prompt)
        self.assertIn("evidence_cards", prompt)
        self.assertIn("不得建立任何額外 agent", prompt)

    def test_codex_cli_failures_map_to_the_scheduler_failure_kinds(self):
        cases = {
            "timeout": CodexExecTimeout("codex exec 超過 270 秒"),
            "process_error": CodexExecProcessError("codex exec 非零結束（exit 1）：boom"),
            "provider_error": CodexExecOutputError("codex exec 未寫出 last message 檔"),
        }
        for expected_kind, error in cases.items():
            with self.subTest(failure_kind=expected_kind):
                self.setUp()
                attempt = self.attempt("spot-technical")
                runner = self.build_runner(
                    codex_mode="cli", codex_adapter=FakeCodexAdapter(error=error)
                )

                runner.start(attempt, None)
                messages = self.drain(runner, 1)

                kind, attempt_id, failure_kind, message = messages[0]
                self.assertEqual("failure", kind)
                self.assertEqual(attempt.attempt_id, attempt_id)
                self.assertEqual(expected_kind, failure_kind)
                self.assertTrue(message)

    def test_codex_mode_defaults_to_the_cli_channel_and_rejects_unknown_modes(self):
        runner = RealSeatRunner(
            self.run,
            self.data_root,
            self.code_root,
            self.results,
            self.scope,
            self.inbox_requests,
            codex_adapter=FakeCodexAdapter(),
        )
        self.addCleanup(runner.shutdown)

        self.assertEqual("cli", runner.codex_mode)
        self.assertEqual(("cli", "inbox"), CODEX_MODES)
        with self.assertRaises(RealProviderError):
            self.build_runner(codex_mode="carrier-pigeon")

    def test_local_worker_pool_can_hold_all_seven_seats_at_once(self):
        self.assertGreaterEqual(LOCAL_WORKER_COUNT, len(SEAT_IDS))

    def test_every_replacement_model_differs_from_its_primary_model(self):
        for seat_id in SEAT_IDS:
            with self.subTest(seat_id=seat_id):
                self.assertNotEqual(PRIMARY_MODELS[seat_id], REPLACEMENT_MODELS[seat_id])

    def test_cross_model_replacement_is_reachable_for_every_seat(self):
        for seat_id in SEAT_IDS:
            with self.subTest(seat_id=seat_id):
                state = SeatRecoveryState(
                    seat_id=seat_id,
                    primary_model=PRIMARY_MODELS[seat_id],
                    replacement_model=REPLACEMENT_MODELS[seat_id],
                )
                primary = state.primary()
                retry = state.recover(primary.attempt_id, "process_error")
                replacement = state.recover(retry.attempt_id, "process_error")

                self.assertEqual("cross_model_replacement", replacement.kind)
                self.assertEqual(REPLACEMENT_MODELS[seat_id], replacement.model)

    def test_undispatchable_replacement_attempt_raises_at_start(self):
        runner = self.build_runner()
        for seat_id in ("news", "spot-technical", "counter-evidence"):
            with self.subTest(seat_id=seat_id):
                attempt = self.attempt(
                    seat_id,
                    model=REPLACEMENT_MODELS[seat_id],
                    kind="cross_model_replacement",
                    attempt_id="{}-a3".format(seat_id),
                )
                with self.assertRaises(RealProviderError):
                    runner.start(attempt, None)
        self.assertTrue(self.results.empty())
        self.assertFalse(list(self.inbox_requests.glob("*-a3.json")))

    def test_checkpoint_and_correct_report_no_public_channel(self):
        runner = self.build_runner()
        attempt = self.attempt("news")

        self.assertIsNone(runner.checkpoint(attempt.attempt_id))
        self.assertIsNone(runner.correct(attempt, "[]", "boom"))

    def test_cancel_and_terminate_never_raise_and_silence_late_workers(self):
        attempt = self.attempt("news")
        runner = self.build_runner(
            claude_outputs={"news": [claude_ok(self.run_id, "news", attempt.attempt_id)]}
        )

        self.assertIsNone(runner.cancel("never-started"))
        self.assertIsNone(runner.terminate("never-started"))
        runner.cancel(attempt.attempt_id)
        runner.start(attempt, None)
        runner.shutdown()

        self.assertTrue(self.results.empty())

    def test_cancel_terminates_the_provider_process_still_burning_at_the_deadline(self):
        attempt = self.attempt("spot-technical")
        process = FakeProcess()
        runner = self.build_runner(codex_mode="cli")
        runner.codex_adapter = BlockingCodexAdapter(
            runner.process_registry, process, runner.worker_key
        )

        runner.start(attempt, None)
        self.assertTrue(runner.codex_adapter.started.wait(timeout=JOIN_TIMEOUT_SECONDS))
        self.assertIsNone(runner.cancel(attempt.attempt_id))
        runner.shutdown()

        # 註冊鍵是 attempt 而不是 pool 執行緒：執行緒會被別席重用，毒不得。
        self.assertEqual(attempt.attempt_id, runner.codex_adapter.key)
        self.assertTrue(process.terminated)
        self.assertTrue(self.results.empty())

    def test_a_provider_process_registered_after_the_cancel_is_stopped_too(self):
        # F3 的競態：cancel 落在 _is_cancelled 檢查之後、resume 進程註冊之前。
        # key 是 attempt 而不是 pool 執行緒，所以毒不到別席。
        attempt = self.attempt("official-events")
        runner = self.build_runner()
        late = FakeProcess()
        other = FakeProcess()

        runner.cancel(attempt.attempt_id)
        runner.process_registry.track(attempt.attempt_id, late)
        runner.process_registry.track("news-a1", other)

        self.assertTrue(late.terminated)
        self.assertFalse(other.terminated)

    def test_terminate_never_raises_when_the_provider_process_cannot_be_stopped(self):
        attempt = self.attempt("derivatives")
        process = UnstoppableProcess()
        runner = self.build_runner(codex_mode="cli")
        runner.codex_adapter = BlockingCodexAdapter(
            runner.process_registry, process, runner.worker_key
        )

        runner.start(attempt, None)
        self.assertTrue(runner.codex_adapter.started.wait(timeout=JOIN_TIMEOUT_SECONDS))

        self.assertIsNone(runner.terminate(attempt.attempt_id))
        process.finished.set()  # 訊號失敗時只能靠 provider 自己結束
        runner.shutdown()
        self.assertTrue(self.results.empty())

    # ---------- debate turns (Ticket T6) ----------

    def dispatch(self, seat_id, slug="r1", schema=None):
        return DebateDispatch(
            dispatch_id="{}-{}".format(seat_id, slug),
            seat_id=seat_id,
            prompt="辯論 prompt for {}".format(seat_id),
            schema=schema or DEBATE_SCHEMA,
            validator=lambda value: value,
            timeout_seconds=45,
        )

    def test_a_debate_turn_answers_on_the_debate_message_contract(self):
        dispatch = self.dispatch("news")
        runner = self.build_runner(
            claude_outputs={
                "news": [
                    ProcessOutput(
                        returncode=0,
                        stdout=claude_stdout(
                            {"seat_id": "news", "stance": "bullish"},
                            web_search_requests=0,
                            web_fetch_requests=0,
                        ),
                        stderr="",
                        elapsed_ms=8,
                    )
                ]
            }
        )

        self.assertIs(True, runner.start_debate(dispatch))
        messages = self.drain(runner, 2)

        self.assertEqual(
            ("provider_lineage", "news"), (messages[0][0], messages[0][1])
        )
        self.assertEqual("claude-opus-5", messages[0][2]["actual_model"])
        kind, dispatch_id, raw_output = messages[1]
        self.assertEqual("debate_result", kind)
        self.assertEqual("news-r1", dispatch_id)
        self.assertEqual({"seat_id": "news", "stance": "bullish"}, json.loads(raw_output))
        # 辯論 turn 一定是全新 invocation，永遠不 resume。
        [call] = self.claude_runner.calls
        self.assertIn("--session-id", call["args"])
        self.assertNotIn("--resume", call["args"])
        self.assertEqual(45, call["timeout_seconds"])

    def test_every_debate_turn_calls_its_provider_with_search_switched_off(self):
        # T+4:00 之後只能依封存證據：三個 provider 的辯論呼叫都不得帶著搜尋能力。
        claude = self.dispatch("news")
        codex = self.dispatch("onchain")
        antigravity = self.dispatch("counter-evidence")
        runner = self.build_runner(
            codex_mode="cli",
            claude_outputs={
                "news": [
                    ProcessOutput(
                        returncode=0,
                        stdout=claude_stdout(
                            {"seat_id": "news", "stance": "bullish"},
                            web_search_requests=0,
                            web_fetch_requests=0,
                        ),
                        stderr="",
                        elapsed_ms=8,
                    )
                ]
            },
            codex_adapter=FakeCodexAdapter(
                structured_output={"seat_id": "onchain", "stance": "bearish"},
                search_invocations=0,
            ),
            agy_runner=FakeAgyRunner(
                stream=agy_stream(
                    {"seat_id": "counter-evidence", "stance": "bearish"},
                    search_succeeded=False,
                )
            ),
        )

        for dispatch in (claude, codex, antigravity):
            runner.start_debate(dispatch)
        self.drain(runner, 6)

        [claude_call] = self.claude_runner.calls
        args = claude_call["args"]
        self.assertIn(("--tools", ""), tuple(zip(args, args[1:])))
        self.assertNotIn("WebSearch,WebFetch", args[args.index("--tools") + 1])
        self.assertNotIn("--allowedTools", args)
        self.assertIs(False, self.codex_adapter.calls[0]["allow_search"])
        # agy 的 argv 沒有任何可關閉工具的旗標，能力層關不掉；它的 no-search 契約
        # 由「跑過 search_web 就拒收」那條測試證明，不是靠 argv。
        self.assertEqual(1, len(self.agy_runner.calls))

    def test_a_codex_debate_turn_that_searched_after_the_seal_is_refused(self):
        # 能力已關還印出搜尋行，代表這份回覆不是只依快照產生：拒收，不當證據。
        runner = self.build_runner(
            codex_mode="cli",
            codex_adapter=FakeCodexAdapter(
                structured_output={"seat_id": "onchain", "stance": "bearish"},
                search_invocations=1,
            ),
        )

        runner.start_debate(self.dispatch("onchain"))
        messages = self.drain(runner, 2)

        kind, dispatch_id, failure_kind, message = messages[1]
        self.assertEqual("debate_failure", kind)
        self.assertEqual("onchain-r1", dispatch_id)
        self.assertEqual("provider_error", failure_kind)
        self.assertIn(POST_SEAL_SEARCH, message)

    def test_an_antigravity_debate_turn_that_searched_after_the_seal_is_refused(self):
        # agy 關不掉 search_web，只能在結果層攔：跑過就是違規，不收。
        runner = self.build_runner(
            agy_runner=FakeAgyRunner(
                stream=agy_stream(
                    {"seat_id": "counter-evidence", "stance": "bearish"},
                    search_succeeded=True,
                )
            )
        )

        runner.start_debate(self.dispatch("counter-evidence"))
        messages = self.drain(runner, 1)

        kind, dispatch_id, failure_kind, message = messages[0]
        self.assertEqual("debate_failure", kind)
        self.assertEqual("counter-evidence-r1", dispatch_id)
        self.assertEqual("provider_error", failure_kind)
        self.assertIn(POST_SEAL_SEARCH, message)

    def test_a_claude_debate_turn_that_searched_after_the_seal_is_refused(self):
        # 能力層已經關掉 WebSearch，回報卻仍有檢索次數：三個 provider 的結果層
        # 防線必須對稱，claude 不能只靠 --tools 就算數。
        dispatch = self.dispatch("news")
        runner = self.build_runner(
            claude_outputs={
                "news": [
                    ProcessOutput(
                        returncode=0,
                        stdout=claude_stdout(
                            {"seat_id": "news", "stance": "bullish"},
                            web_search_requests=1,
                            web_fetch_requests=0,
                        ),
                        stderr="",
                        elapsed_ms=8,
                    )
                ]
            }
        )

        runner.start_debate(dispatch)
        messages = self.drain(runner, 2)

        # lineage 照舊誠實回報是誰跑的，但這份回覆不得成為辯論內容。
        self.assertEqual("provider_lineage", messages[0][0])
        kind, dispatch_id, failure_kind, message = messages[1]
        self.assertEqual("debate_failure", kind)
        self.assertEqual("news-r1", dispatch_id)
        self.assertEqual("provider_error", failure_kind)
        self.assertIn(POST_SEAL_SEARCH, message)

    def test_a_failed_debate_turn_publishes_the_scheduler_failure_kind(self):
        dispatch = self.dispatch("social-macro")
        runner = self.build_runner(
            claude_outputs={"social-macro": [claude_timeout()]}
        )

        runner.start_debate(dispatch)
        messages = self.drain(runner, 2)

        kind, dispatch_id, failure_kind, message = messages[1]
        self.assertEqual("debate_failure", kind)
        self.assertEqual("social-macro-r1", dispatch_id)
        self.assertEqual("timeout", failure_kind)
        self.assertIn("claude_cli_timeout", message)

    def test_a_codex_debate_turn_runs_in_a_fresh_work_directory(self):
        dispatch = self.dispatch("onchain", slug="opening")
        runner = self.build_runner(
            codex_mode="cli",
            codex_adapter=FakeCodexAdapter(
                structured_output={"seat_id": "onchain", "stance": "bearish"},
                search_invocations=0,
            ),
        )

        runner.start_debate(dispatch)
        messages = self.drain(runner, 2)

        # 辯論不再上網，所以沒有 search 證明也照樣採用。
        self.assertEqual("debate_result", messages[1][0])
        [call] = self.codex_adapter.calls
        self.assertEqual(DEBATE_SCHEMA, call["schema"])
        self.assertEqual(
            self.run.path / "agents" / "onchain" / "work" / "onchain-opening",
            call["work_dir"],
        )

    def test_an_antigravity_debate_turn_reports_its_actual_model(self):
        dispatch = self.dispatch("counter-evidence", slug="r2")
        runner = self.build_runner(
            agy_runner=FakeAgyRunner(
                stream=agy_stream(
                    {"seat_id": "counter-evidence", "stance": "bearish"},
                    search_succeeded=False,
                )
            )
        )

        runner.start_debate(dispatch)
        messages = self.drain(runner, 2)

        self.assertEqual("gemini-3.1-pro-high", messages[0][2]["actual_model"])
        self.assertEqual(2_500, messages[0][2]["elapsed_ms"])
        self.assertEqual("debate_result", messages[1][0])

    def test_inbox_mode_has_no_debate_channel_for_the_codex_seats(self):
        runner = self.build_runner(codex_mode="inbox")

        self.assertIs(False, runner.start_debate(self.dispatch("spot-technical")))
        self.assertIs(True, runner.start_debate(self.dispatch("counter-evidence")))
        runner.shutdown()

    def test_a_cancelled_debate_turn_never_reaches_the_queue(self):
        dispatch = self.dispatch("official-events")
        runner = self.build_runner(
            claude_outputs={
                "official-events": [
                    ProcessOutput(
                        returncode=0,
                        stdout=claude_stdout(
                            {"seat_id": "official-events"},
                            web_search_requests=0,
                            web_fetch_requests=0,
                        ),
                        stderr="",
                        elapsed_ms=8,
                    )
                ]
            }
        )

        runner.cancel(dispatch.dispatch_id)
        runner.start_debate(dispatch)
        runner.shutdown()

        # lineage 仍然誠實回報是誰跑的，但被取消的內容不得再進入辯論。
        messages = []
        while not self.results.empty():
            messages.append(self.results.get_nowait())
        self.assertEqual(["provider_lineage"], [item[0] for item in messages])

    def test_the_core_report_adapter_shares_the_terminable_process_registry(self):
        runner = self.build_runner()

        adapter = runner.core_report_adapter(85)

        self.assertEqual(85, adapter.timeout_seconds)
        self.assertIs(runner.process_registry, adapter.runner.registry)


class RealEvidenceGatewayTest(unittest.TestCase):
    def setUp(self):
        self.run_id = "20260801T020000Z-btc-abc123"
        self.attempt = ResearchAttempt(
            attempt_id="news-a1",
            seat_id="news",
            model=PRIMARY_MODELS["news"],
            kind="primary",
            original_attempt_id="news-a1",
        )
        self.gateway = RealEvidenceGateway(self.run_id, ("BTC",))
        self.card = evidence_card(self.run_id, "news", "news-a1")

    def envelope_text(self, cards):
        return json.dumps(
            {"seat_id": "news", "evidence_cards": cards}, ensure_ascii=False
        )

    def test_accepts_the_envelope_object(self):
        raw = json.dumps(
            {"seat_id": "news", "evidence_cards": [self.card]}, ensure_ascii=False
        )

        self.assertEqual([self.card], self.gateway.validate(self.attempt, raw))

    def test_accepts_a_bare_evidence_card_array(self):
        raw = json.dumps([self.card], ensure_ascii=False)

        self.assertEqual([self.card], self.gateway.validate(self.attempt, raw))

    def test_rejects_an_envelope_declaring_another_seat(self):
        raw = json.dumps(
            {"seat_id": "onchain", "evidence_cards": [self.card]}, ensure_ascii=False
        )

        with self.assertRaises(ValueError):
            self.gateway.validate(self.attempt, raw)

    def test_rejects_a_card_whose_lineage_does_not_match_the_attempt(self):
        card = dict(self.card, attempt_id="news-a2")
        raw = json.dumps({"seat_id": "news", "evidence_cards": [card]}, ensure_ascii=False)

        with self.assertRaises(ValueError):
            self.gateway.validate(self.attempt, raw)

    def test_rejects_a_non_object_non_array_payload(self):
        with self.assertRaises(ValueError):
            self.gateway.validate(self.attempt, json.dumps("not evidence"))

    def test_rejects_a_card_belonging_to_another_run(self):
        card = dict(self.card, run_id="20260801T020000Z-btc-other1")

        with self.assertRaises(ValueError):
            self.gateway.validate(self.attempt, self.envelope_text([card]))

    def test_rejects_a_card_about_an_asset_outside_the_question(self):
        card = dict(self.card, asset="ETH")

        with self.assertRaises(ValueError):
            self.gateway.validate(self.attempt, self.envelope_text([card]))

    def test_rejects_an_empty_envelope_so_a_silent_seat_is_never_adopted(self):
        with self.assertRaises(ValueError):
            self.gateway.validate(self.attempt, self.envelope_text([]))

    def test_rejects_a_bare_empty_array_too(self):
        with self.assertRaises(ValueError):
            self.gateway.validate(self.attempt, json.dumps([]))

    def test_rejects_more_cards_than_one_seat_may_submit(self):
        cards = [
            evidence_card(self.run_id, "news", "news-a1", index=index)
            for index in range(MAX_EVIDENCE_CARDS_PER_SEAT + 1)
        ]

        with self.assertRaises(ValueError):
            self.gateway.validate(self.attempt, self.envelope_text(cards))

    def test_requires_the_run_and_asset_binding_at_construction(self):
        for run_id, assets in ((None, ("BTC",)), (self.run_id, ())):
            with self.subTest(run_id=run_id, assets=assets):
                with self.assertRaises(RealProviderError):
                    RealEvidenceGateway(run_id, assets)


class ResearchEnvelopeContractTest(unittest.TestCase):
    def test_schema_is_a_closed_object_requiring_seat_id(self):
        self.assertEqual("object", RESEARCH_ENVELOPE_SCHEMA["type"])
        self.assertIs(False, RESEARCH_ENVELOPE_SCHEMA["additionalProperties"])
        self.assertEqual(
            {"seat_id", "evidence_cards"}, set(RESEARCH_ENVELOPE_SCHEMA["properties"])
        )
        self.assertEqual(
            {"seat_id", "evidence_cards"}, set(RESEARCH_ENVELOPE_SCHEMA["required"])
        )

    def test_shallow_validator_accepts_an_envelope_and_rejects_a_bare_array(self):
        card = evidence_card("run", "news", "news-a1")
        value = {"seat_id": "news", "evidence_cards": [card]}

        self.assertEqual(value, validate_research_envelope_shape(value))
        with self.assertRaises((TypeError, ValueError)):
            validate_research_envelope_shape([card])
        with self.assertRaises((TypeError, ValueError)):
            validate_research_envelope_shape({"seat_id": "news", "evidence_cards": []})


class TrailingCommaRepairerTest(unittest.TestCase):
    def setUp(self):
        self.repairer = TrailingCommaRepairer()
        self.attempt = ResearchAttempt(
            attempt_id="news-a1",
            seat_id="news",
            model=PRIMARY_MODELS["news"],
            kind="primary",
            original_attempt_id="news-a1",
        )

    def test_removes_only_commas_before_a_closing_bracket(self):
        raw = '{"seat_id": "news", "evidence_cards": [1, 2,],}'

        repaired = self.repairer.repair(self.attempt, raw, "trailing comma")

        self.assertEqual('{"seat_id": "news", "evidence_cards": [1, 2]}', repaired)

    def test_returns_none_when_there_is_nothing_to_repair(self):
        raw = '{"seat_id": "news"}'

        self.assertIsNone(self.repairer.repair(self.attempt, raw, "boom"))


class ResearchPromptVocabularyTest(unittest.TestCase):
    """Ticket T12a: the research prompt carries the drawn ballot, not market words.

    ``_output_instructions`` names only EvidenceCard fields, so the stance
    vocabulary reaches a research seat through the question package alone.
    """

    def _prompt(self, question):
        package = build_question_package(question)
        seat = load_roster()[0]
        attempt_id = "{}-a1".format(seat.seat_id)
        attempt = ResearchAttempt(
            attempt_id=attempt_id,
            seat_id=seat.seat_id,
            model=PRIMARY_MODELS[seat.seat_id],
            kind="primary",
            original_attempt_id=attempt_id,
        )
        return package, build_attempt_prompt(package, seat, "run-1", attempt)

    def test_research_prompt_states_the_ballot_of_the_drawn_question_type(self):
        for question in (
            "分析 BTC 過去 14 日市場狀態",
            "比較 BTC 與 ETH 過去 14 日的相對強弱",
            "分析監管事件對 BTC 的影響",
            "分析 BTC 是否值得長期持有",
        ):
            package, prompt = self._prompt(question)
            with self.subTest(question_type=package.question_type):
                for stance, label in package.stance_labels.items():
                    self.assertIn(stance, prompt)
                    self.assertIn(label, prompt)

    def test_research_prompt_keeps_market_words_out_of_a_comparison_run(self):
        _, prompt = self._prompt("比較 BTC 與 ETH 過去 14 日的相對強弱")

        self.assertNotIn("bullish", prompt)
        self.assertNotIn("偏多", prompt)
        # EvidenceCard direction is a card's relation to its own claim, not a
        # market stance, so it stays support/oppose/neutral for every type.
        self.assertIn("direction 只能是 support、oppose、neutral", prompt)


class SeatGroupTest(unittest.TestCase):
    def test_seat_groups_cover_the_seven_frozen_seats_exactly_once(self):
        grouped = CODEX_SEAT_IDS + CLAUDE_SEAT_IDS + ANTIGRAVITY_SEAT_IDS

        self.assertEqual(sorted(SEAT_IDS), sorted(grouped))
        self.assertEqual(len(SEAT_IDS), len(set(grouped)))


if __name__ == "__main__":
    unittest.main()
