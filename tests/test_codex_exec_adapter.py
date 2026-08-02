"""Ticket T8 Codex ``exec`` adapter behaviour; every test runs fully offline.

The only seam is the injected process runner: it records the argv the adapter
built and writes the scripted ``--output-last-message`` file, exactly as the
real CLI does.
"""

import json
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents.claude_adapter import ProcessOutput
from hoya_market_agents.codex_exec_adapter import (
    CODEX_LAST_MESSAGE_NAME,
    CODEX_MODEL,
    CODEX_SCHEMA_NAME,
    CODEX_TIMEOUT_SECONDS,
    SEARCH_INVOCATION_MARKER,
    CodexExecAdapter,
    CodexExecError,
    CodexExecOutputError,
    CodexExecProcessError,
    CodexExecTimeout,
)

PROMPT = "請以繁體中文回報 BTC 現貨技術面證據。"
SCHEMA = {
    "type": "object",
    "properties": {"seat_id": {"type": "string"}},
    "required": ["seat_id"],
    "additionalProperties": False,
}
ENVELOPE = {"seat_id": "spot-technical", "evidence_cards": [{"evidence_id": "x-01"}]}


class FakeCodexRunner:
    """Codex CLI seam: records the invocation and writes the last message file."""

    def __init__(self, last_message=None, returncode=0, stderr="", timed_out=False):
        self.last_message = last_message
        self.returncode = returncode
        self.stderr = stderr
        self.timed_out = timed_out
        self.calls = []

    def run(self, args, *, input_text, cwd, timeout_seconds):
        args = list(args)
        self.calls.append(
            {
                "args": args,
                "input_text": input_text,
                "cwd": Path(cwd),
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.timed_out:
            return ProcessOutput(
                returncode=None, stdout="", stderr=self.stderr, elapsed_ms=270_000,
                timed_out=True,
            )
        if self.last_message is not None:
            target = Path(args[args.index("-o") + 1])
            target.write_text(self.last_message, encoding="utf-8")
        return ProcessOutput(
            returncode=self.returncode, stdout="", stderr=self.stderr, elapsed_ms=4_200
        )


class CodexExecAdapterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work_dir = Path(self._tmp.name) / "work"
        self.cli = Path(self._tmp.name) / "codex"

    def build_adapter(self, runner):
        return CodexExecAdapter(cli_path=self.cli, runner=runner)

    def invoke(self, runner):
        return self.build_adapter(runner).invoke(PROMPT, SCHEMA, self.work_dir)

    def test_successful_invocation_returns_the_parsed_last_message(self):
        runner = FakeCodexRunner(last_message=json.dumps(ENVELOPE, ensure_ascii=False))

        result = self.invoke(runner)

        self.assertEqual(ENVELOPE, result.structured_output)
        self.assertEqual(4_200, result.elapsed_ms)
        self.assertEqual(self.work_dir / CODEX_LAST_MESSAGE_NAME, result.last_message_path)

    def test_argv_pins_model_sandbox_schema_and_last_message_file(self):
        runner = FakeCodexRunner(last_message=json.dumps(ENVELOPE, ensure_ascii=False))

        result = self.invoke(runner)

        [call] = runner.calls
        args = call["args"]
        self.assertEqual([str(self.cli), "exec"], args[:2])
        self.assertEqual(PROMPT, args[-1])
        for flag, value in (
            ("-m", CODEX_MODEL),
            ("-s", "read-only"),
            ("-C", str(self.work_dir)),
            ("--output-schema", str(result.schema_path)),
            ("-o", str(result.last_message_path)),
            ("--color", "never"),
            ("-c", "tools.web_search=true"),
        ):
            with self.subTest(flag=flag):
                self.assertIn(flag, args)
                self.assertEqual(value, args[args.index(flag) + 1])
        self.assertIn("--skip-git-repo-check", args)

    def test_a_no_search_invocation_switches_the_capability_off(self):
        # T+4:00 封存後只能讀已封存的證據：搜尋要在能力層關掉，不是在 prompt 拜託。
        runner = FakeCodexRunner(last_message=json.dumps(ENVELOPE, ensure_ascii=False))

        self.build_adapter(runner).invoke(
            PROMPT, SCHEMA, self.work_dir, allow_search=False
        )

        [call] = runner.calls
        args = call["args"]
        self.assertIn("tools.web_search=false", args)
        self.assertNotIn("tools.web_search=true", args)

    def test_stdin_is_closed_so_codex_never_waits_for_piped_input(self):
        runner = FakeCodexRunner(last_message=json.dumps(ENVELOPE, ensure_ascii=False))

        self.invoke(runner)

        [call] = runner.calls
        self.assertEqual("", call["input_text"])
        self.assertEqual(self.work_dir, call["cwd"])
        self.assertEqual(CODEX_TIMEOUT_SECONDS, call["timeout_seconds"])

    def test_schema_file_is_written_into_the_work_directory(self):
        runner = FakeCodexRunner(last_message=json.dumps(ENVELOPE, ensure_ascii=False))

        result = self.invoke(runner)

        self.assertEqual(self.work_dir / CODEX_SCHEMA_NAME, result.schema_path)
        self.assertEqual(
            SCHEMA, json.loads(result.schema_path.read_text(encoding="utf-8"))
        )

    def test_search_invocations_are_counted_from_the_stderr_transcript(self):
        stderr = "thinking\n{} BTC ETF 流入\n[2026-08-01] {} BTC 未平倉\n".format(
            SEARCH_INVOCATION_MARKER, SEARCH_INVOCATION_MARKER
        )
        runner = FakeCodexRunner(
            last_message=json.dumps(ENVELOPE, ensure_ascii=False), stderr=stderr
        )

        result = self.invoke(runner)

        self.assertEqual(2, result.search_invocations)

    def test_a_transcript_without_a_live_search_counts_zero_invocations(self):
        runner = FakeCodexRunner(
            last_message=json.dumps(ENVELOPE, ensure_ascii=False),
            stderr="thinking\ncodex 已完成\n",
        )

        result = self.invoke(runner)

        self.assertEqual(0, result.search_invocations)

    # ---------- failure mapping ----------

    def test_timeout_raises_the_timeout_failure_kind(self):
        with self.assertRaises(CodexExecTimeout) as caught:
            self.invoke(FakeCodexRunner(timed_out=True))

        self.assertEqual("timeout", caught.exception.failure_kind)
        self.assertIn(str(CODEX_TIMEOUT_SECONDS), str(caught.exception))

    def test_nonzero_exit_raises_process_error_with_a_stderr_summary(self):
        stderr = "codex 認證失效" + "x" * 500

        with self.assertRaises(CodexExecProcessError) as caught:
            self.invoke(FakeCodexRunner(returncode=1, stderr=stderr))

        message = str(caught.exception)
        self.assertEqual("process_error", caught.exception.failure_kind)
        self.assertIn("exit 1", message)
        self.assertIn("codex 認證失效", message)
        self.assertNotIn(stderr, message)
        self.assertLess(len(message), len(stderr))

    def test_missing_last_message_file_raises_provider_error(self):
        with self.assertRaises(CodexExecOutputError) as caught:
            self.invoke(FakeCodexRunner(last_message=None))

        self.assertEqual("provider_error", caught.exception.failure_kind)
        self.assertIn(CODEX_LAST_MESSAGE_NAME, str(caught.exception))

    def test_unusable_last_message_content_raises_provider_error(self):
        cases = {
            "empty": "   \n",
            "not_json": "抱歉，我無法完成這次研究。",
            "not_an_object": json.dumps([ENVELOPE], ensure_ascii=False),
        }
        for name, last_message in cases.items():
            with self.subTest(case=name):
                self.setUp()
                with self.assertRaises(CodexExecOutputError) as caught:
                    self.invoke(FakeCodexRunner(last_message=last_message))
                self.assertEqual("provider_error", caught.exception.failure_kind)

    def test_every_failure_shares_one_catchable_base_class(self):
        for exception_class in (
            CodexExecTimeout,
            CodexExecProcessError,
            CodexExecOutputError,
        ):
            with self.subTest(exception_class=exception_class.__name__):
                self.assertTrue(issubclass(exception_class, CodexExecError))


if __name__ == "__main__":
    unittest.main()
