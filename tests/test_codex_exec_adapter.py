"""Ticket T8 Codex ``exec`` adapter behaviour; every test runs fully offline.

The only seam is the injected process runner: it records the argv the adapter
built and writes the scripted ``--output-last-message`` file, exactly as the
real CLI does.
"""

import json
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents.claude_adapter import (
    PROCESS_GROUP_RECLAIMED,
    PROCESS_TREE_TERMINATION_FAILED,
    ProcessOutput,
)
from hoya_market_agents.codex_exec_adapter import (
    CODEX_LAST_MESSAGE_NAME,
    CODEX_MODEL,
    CODEX_SCHEMA_NAME,
    CODEX_TIMEOUT_SECONDS,
    CodexExecAdapter,
    CodexExecError,
    CodexExecOutputError,
    CodexExecProcessError,
    CodexExecTimeout,
    CodexExecTreeTerminationError,
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

    def __init__(
        self,
        last_message=None,
        returncode=0,
        stdout="",
        stderr="",
        timed_out=False,
        process_outcome=None,
    ):
        self.last_message = last_message
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.process_outcome = process_outcome
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
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
            elapsed_ms=4_200,
            process_outcome=self.process_outcome,
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
        self.assertIn("--json", args)

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

    def test_only_matched_search_started_and_successful_completed_count_as_proof(self):
        stdout = "\n".join(
            json.dumps(event)
            for event in (
                {"type": "item.started", "item": {"id": "search-1", "type": "web_search"}},
                {"type": "item.completed", "item": {"id": "search-1", "type": "web_search"}},
                {"type": "item.started", "item": {"id": "search-2", "type": "web_search"}},
                {"type": "item.completed", "item": {"id": "search-2", "type": "web_search"}},
            )
        )
        runner = FakeCodexRunner(
            last_message=json.dumps(ENVELOPE, ensure_ascii=False), stdout=stdout
        )

        result = self.invoke(runner)

        self.assertEqual(2, result.search_invocations)
        self.assertEqual("matched", result.search_parse_status)
        self.assertEqual(0, result.malformed_event_count)

    def test_url_or_search_claim_in_final_prose_is_not_proof(self):
        stdout = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "answer-1",
                    "type": "agent_message",
                    "text": "I searched https://example.invalid and found evidence.",
                },
            }
        )
        runner = FakeCodexRunner(last_message=json.dumps(ENVELOPE), stdout=stdout)

        result = self.invoke(runner)

        self.assertEqual(0, result.search_invocations)
        self.assertEqual("no_search", result.search_parse_status)

    def test_tool_use_without_a_result_fails_closed(self):
        stdout = json.dumps(
            {"type": "item.started", "item": {"id": "search-1", "type": "web_search"}}
        )

        result = self.invoke(
            FakeCodexRunner(last_message=json.dumps(ENVELOPE), stdout=stdout)
        )

        self.assertEqual(0, result.search_invocations)
        self.assertEqual("missing_result", result.search_parse_status)

    def test_orphan_result_fails_closed(self):
        stdout = json.dumps(
            {"type": "item.completed", "item": {"id": "search-1", "type": "web_search"}}
        )

        result = self.invoke(
            FakeCodexRunner(last_message=json.dumps(ENVELOPE), stdout=stdout)
        )

        self.assertEqual(0, result.search_invocations)
        self.assertEqual("orphan_result", result.search_parse_status)

    def test_error_result_fails_closed(self):
        stdout = "\n".join(
            json.dumps(event)
            for event in (
                {"type": "item.started", "item": {"id": "search-1", "type": "web_search"}},
                {
                    "type": "item.completed",
                    "item": {"id": "search-1", "type": "web_search", "error": "blocked"},
                },
            )
        )

        result = self.invoke(
            FakeCodexRunner(last_message=json.dumps(ENVELOPE), stdout=stdout)
        )

        self.assertEqual(0, result.search_invocations)
        self.assertEqual("error_result", result.search_parse_status)

    def test_malformed_event_invalidates_an_otherwise_matching_pair(self):
        stdout = "\n".join(
            (
                json.dumps({"type": "item.started", "item": {"id": "search-1", "type": "web_search"}}),
                "not-json https://example.invalid",
                json.dumps({"type": "item.completed", "item": {"id": "search-1", "type": "web_search"}}),
            )
        )

        result = self.invoke(
            FakeCodexRunner(last_message=json.dumps(ENVELOPE), stdout=stdout)
        )

        self.assertEqual(0, result.search_invocations)
        self.assertEqual("malformed", result.search_parse_status)
        self.assertEqual(1, result.malformed_event_count)

    def test_empty_event_stream_fails_closed(self):
        result = self.invoke(FakeCodexRunner(last_message=json.dumps(ENVELOPE)))

        self.assertEqual(0, result.search_invocations)
        self.assertEqual("no_search", result.search_parse_status)

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


class CodexUnreclaimedTreeTest(unittest.TestCase):
    """整組回收不了時，Codex 邊界必須先 fail closed（Reviewer A-1 第三次）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work_dir = Path(self._tmp.name) / "work"
        self.cli = Path(self._tmp.name) / "codex"

    def invoke(self, runner):
        return CodexExecAdapter(cli_path=self.cli, runner=runner).invoke(
            PROMPT, SCHEMA, self.work_dir
        )

    def runner(self, process_outcome):
        # CLI 本身完全正常：退出碼 0、last message 合法、轉錄還留著搜尋證據。
        return FakeCodexRunner(
            last_message=json.dumps(ENVELOPE, ensure_ascii=False),
            returncode=0,
            stdout="\n".join(
                (
                    json.dumps({"type": "item.started", "item": {"id": "search-1", "type": "web_search"}}),
                    json.dumps({"type": "item.completed", "item": {"id": "search-1", "type": "web_search"}}),
                )
            ),
            process_outcome=process_outcome,
        )

    def test_a_clean_looking_invocation_is_refused_when_the_tree_was_not_reclaimed(self):
        runner = self.runner(PROCESS_TREE_TERMINATION_FAILED)

        with self.assertRaises(CodexExecTreeTerminationError) as caught:
            self.invoke(runner)

        self.assertIsInstance(caught.exception, CodexExecError)
        self.assertEqual(PROCESS_TREE_TERMINATION_FAILED, str(caught.exception))

    def test_the_last_message_is_never_read_when_the_tree_was_not_reclaimed(self):
        # 這次連 last message 檔都不存在：只要讀了就會變成 CodexExecOutputError，
        # 拿到 tree 終局就證明 parse 與 search proof 都沒有跑到。
        runner = FakeCodexRunner(
            last_message=None,
            returncode=0,
            stdout=json.dumps(
                {"type": "item.started", "item": {"id": "search-1", "type": "web_search"}}
            ),
            process_outcome=PROCESS_TREE_TERMINATION_FAILED,
        )

        with self.assertRaises(CodexExecTreeTerminationError):
            self.invoke(runner)

    def test_a_reclaimed_tree_still_returns_its_result_and_search_proof(self):
        result = self.invoke(self.runner(PROCESS_GROUP_RECLAIMED))

        self.assertEqual(ENVELOPE, result.structured_output)
        self.assertEqual(1, result.search_invocations)

    def test_a_runner_without_a_process_outcome_is_unchanged(self):
        result = self.invoke(self.runner(None))

        self.assertEqual(ENVELOPE, result.structured_output)
        self.assertEqual(1, result.search_invocations)


if __name__ == "__main__":
    unittest.main()
