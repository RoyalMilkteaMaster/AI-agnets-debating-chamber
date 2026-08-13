"""Antigravity CLI boundary tests; no test calls Google."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hoya_market_agents.antigravity_adapter import (
    AntigravityAdapter,
    AntigravityBoundaryError,
    AntigravityEnvelopeError,
    AntigravityError,
    AntigravityLateResult,
    AntigravityNotReady,
    AntigravityPostSealSearch,
    AntigravitySchemaError,
    AntigravityTimeout,
    AntigravityTreeTermination,
    parse_envelope,
)
from hoya_market_agents.claude_adapter import (
    PROCESS_GROUP_RECLAIMED,
    PROCESS_TREE_TERMINATION_FAILED,
    ProcessRegistry,
)
from hoya_market_agents.prompt_builder import ResearchSnapshot, build_provider_prompt
from tests.test_claude_adapter import (
    FakeKillpg,
    FakeProcessGroup,
    FixedChildRunner,
    UnreclaimableProcess,
    deaf_killpg,
)


MODEL = "gemini-3.1-pro-high"
PROMPT = "請以繁體中文回報 BTC 的公開研究結論。"
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def stream_success(model=MODEL, structured_output=None, search_done=True):
    structured_output = structured_output or {"answer": "ready"}
    events = [
        {
            "event": "init",
            "conversation_id": "fixture-conversation",
            "init": {"model": model, "tools": ["search_web", "view_file"]},
        },
    ]
    if search_done:
        events.append(
            {
                "event": "step_update",
                "step_update": {
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "search_web",
                    "duration_seconds": 0.5,
                },
            }
        )
    events.append(
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "structured_output": structured_output,
                "duration_seconds": 2.5,
                "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            },
        }
    )
    return "\n".join(json.dumps(event) for event in events) + "\n"


class FakeRunner:
    def __init__(
        self,
        stream=None,
        stream_returncode=0,
        stream_stderr="",
        models_returncode=0,
        models_stderr="",
        models_output=MODEL + "\n",
        log_text=None,
        process_outcome=None,
    ):
        self.process_outcome = process_outcome
        self.stream = stream or stream_success()
        self.stream_returncode = stream_returncode
        self.stream_stderr = stream_stderr
        self.models_returncode = models_returncode
        self.models_stderr = models_stderr
        self.models_output = models_output
        self.log_text = log_text
        self.calls = []

    def __call__(self, argv, cwd, timeout):
        self.calls.append((list(argv), Path(cwd), timeout))
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, "1.1.9\n", "")
        if argv[1:] == ["models"]:
            return subprocess.CompletedProcess(
                argv, self.models_returncode, self.models_output, self.models_stderr
            )
        if self.log_text is not None:
            Path(argv[argv.index("--log-file") + 1]).write_text(
                self.log_text, encoding="utf-8"
            )
        completed = subprocess.CompletedProcess(
            argv, self.stream_returncode, self.stream, self.stream_stderr
        )
        if self.process_outcome is not None:
            # 只有 group-safe 的 runner 會多帶這個欄位；注入的 runner 不帶時
            # adapter 必須維持原本行為。
            completed.process_outcome = self.process_outcome
        return completed


class AntigravityAdapterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.code_root = root / "code"
        self.data_root = root / "data"
        self.cli = root / "agy"
        self.code_root.mkdir()
        self.data_root.mkdir()
        self.cli.write_text("fixture", encoding="utf-8")
        self.cli.chmod(0o700)
        self.runner = FakeRunner()
        self.adapter = AntigravityAdapter(
            cli_path=self.cli,
            code_root=self.code_root,
            data_root=self.data_root,
            runner=self.runner,
        )

    def attempt_dir(self, name="attempt-a1"):
        path = self.data_root / "runs" / "run-1" / "agents" / "news" / name
        path.mkdir(parents=True)
        return path

    def test_preflight_detects_version_login_model_search_and_contract(self):
        result = self.adapter.preflight(self.attempt_dir("preflight"))

        self.assertTrue(result.ready)
        self.assertEqual("1.1.9", result.version)
        self.assertEqual(MODEL, result.requested_model)
        self.assertEqual(MODEL, result.actual_model)
        self.assertEqual("high", result.effort)
        self.assertTrue(result.search_available)
        self.assertTrue(result.search_succeeded)
        self.assertEqual({"answer": "ready"}, result.structured_output)
        self.assertEqual(2.5, result.duration_seconds)
        self.assertEqual(14, result.usage["total_tokens"])

    def test_preflight_accepts_the_current_tab_separated_model_listing(self):
        self.runner.models_output = (
            MODEL + "\tGemini 3.1 Pro (High)\n"
            "gemini-3.1-pro-low\tGemini 3.1 Pro (Low)\n"
        )

        result = self.adapter.preflight(self.attempt_dir("tab-separated-models"))

        self.assertTrue(result.ready)
        self.assertEqual(MODEL, result.actual_model)

    def test_preflight_does_not_treat_a_space_separated_status_as_available(self):
        self.runner.models_output = MODEL + " unavailable\n"

        with self.assertRaisesRegex(AntigravityNotReady, "指定模型不存在"):
            self.adapter.preflight(self.attempt_dir("unavailable-model"))

    def test_call_uses_physical_schema_path_under_attempt_and_never_inline_schema(self):
        attempt = self.attempt_dir()
        result = self.adapter.invoke("public prompt", SCHEMA, attempt)

        argv, cwd, _ = self.runner.calls[-1]
        schema_argument = argv[argv.index("--json-schema") + 1]
        self.assertEqual(attempt.resolve(), cwd.resolve())
        self.assertEqual(attempt.resolve(), result.schema_path.parent.resolve())
        self.assertEqual(result.schema_path.resolve(), Path(schema_argument).resolve())
        self.assertTrue(result.schema_path.is_file())
        self.assertEqual(SCHEMA, json.loads(result.schema_path.read_text(encoding="utf-8")))
        self.assertFalse(any(argument.lstrip().startswith("{") for argument in argv))
        self.assertEqual(MODEL, argv[argv.index("--model") + 1])
        self.assertEqual("high", argv[argv.index("--effort") + 1])
        self.assertEqual("stream-json", argv[argv.index("--output-format") + 1])
        self.assertIn("--sandbox", argv)
        self.assertNotIn("--conversation", argv)
        self.assertNotIn("--continue", argv)
        self.assertTrue((attempt / "raw-envelope.jsonl").is_file())
        self.assertFalse(any(self.code_root.iterdir()))

    def test_each_round_is_stateless_and_replays_all_public_ids_verbatim(self):
        attempt_one = self.attempt_dir("round-1")
        attempt_two = self.attempt_dir("round-2")
        scope = SimpleNamespace(
            question="分析 BTC", assets=("BTC",), period_days=14, period_stated=True
        )
        seat = SimpleNamespace(seat_id="news", focus="news", output_dir="news")
        snapshot = ResearchSnapshot("fixed rules", "commit", "blob", "sha256")
        evidence = ({"evidence_id": "ev-1"}, {"evidence_id": "ev-2"})
        first_debate = ({"turn_id": "turn-1"},)
        second_debate = first_debate + ({"turn_id": "turn-2"},)
        first = build_provider_prompt(
            scope,
            seat,
            "vote",
            "gemini",
            evidence_snapshot=evidence,
            debate_snapshot=first_debate,
            research_snapshot=snapshot,
        ).text
        second = build_provider_prompt(
            scope,
            seat,
            "vote",
            "gemini",
            evidence_snapshot=evidence,
            debate_snapshot=second_debate,
            research_snapshot=snapshot,
        ).text

        self.adapter.invoke(first, SCHEMA, attempt_one)
        self.adapter.invoke(second, SCHEMA, attempt_two)

        first_argv = self.runner.calls[-2][0]
        second_argv = self.runner.calls[-1][0]
        self.assertEqual(first, first_argv[first_argv.index("--print") + 1])
        self.assertEqual(second, second_argv[second_argv.index("--print") + 1])
        for public_id in ("ev-1", "ev-2", "turn-1", "turn-2"):
            self.assertIn(public_id, second)
        self.assertNotIn("hidden_reasoning", second)
        self.assertNotIn("--conversation", second_argv)
        self.assertNotIn("--continue", second_argv)

    def test_attempt_outside_data_root_fails_before_running(self):
        outside = self.code_root / "attempt"
        outside.mkdir()

        with self.assertRaises(AntigravityBoundaryError):
            self.adapter.invoke("prompt", SCHEMA, outside)

        self.assertEqual([], self.runner.calls)

    def test_missing_cli_model_login_or_search_fail_closed(self):
        missing = AntigravityAdapter(
            cli_path=self.data_root / "missing",
            code_root=self.code_root,
            data_root=self.data_root,
            runner=self.runner,
        )
        with self.assertRaises(AntigravityNotReady):
            missing.preflight(self.attempt_dir("missing"))

        for index, runner in enumerate((
            FakeRunner(models_returncode=1, models_stderr="login required"),
            FakeRunner(models_output="gemini-3.1-pro-low\n"),
            FakeRunner(stream=stream_success(model="other-model")),
            FakeRunner(stream=stream_success().replace("search_web", "no_search")),
            FakeRunner(stream=stream_success(search_done=False)),
        )):
            adapter = AntigravityAdapter(
                cli_path=self.cli,
                code_root=self.code_root,
                data_root=self.data_root,
                runner=runner,
            )
            with self.assertRaises(AntigravityNotReady):
                adapter.preflight(self.attempt_dir("not-ready-{}".format(index)))

    def test_init_tool_listing_without_successful_search_event_is_not_ready(self):
        runner = FakeRunner(stream=stream_success(search_done=False))
        with self.assertRaises(AntigravityNotReady):
            self._adapter(runner).preflight(self.attempt_dir("no-search-event"))

    def test_a_no_search_call_refuses_a_reply_that_searched_anyway(self):
        # agy CLI 沒有關閉工具的旗標，search_web 永遠在 session 裡。能力層關不掉，
        # 就在結果層攔：封存後只要真的跑過 search_web，這份回覆一律不收。
        with self.assertRaises(AntigravityPostSealSearch):
            self._adapter(FakeRunner(stream=stream_success())).invoke(
                "prompt", SCHEMA, self.attempt_dir("post-seal-search"), allow_search=False
            )

    def test_a_no_search_call_accepts_a_reply_that_never_searched(self):
        runner = FakeRunner(stream=stream_success(search_done=False))

        result = self._adapter(runner).invoke(
            "prompt", SCHEMA, self.attempt_dir("post-seal-quiet"), allow_search=False
        )

        self.assertEqual({"answer": "ready"}, result.structured_output)
        self.assertFalse(result.search_succeeded)

    def test_raw_cli_log_is_sanitized_and_temp_removed(self):
        sensitive = (
            "account=user@example.com token=top-secret "
            "Authorization: Bearer bearer-secret "
            "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature\n"
        )
        attempt = self.attempt_dir("sanitized-log")

        self._adapter(FakeRunner(log_text=sensitive)).invoke("prompt", SCHEMA, attempt)

        persisted = (attempt / "agy.log").read_text(encoding="utf-8")
        for secret in (
            "user@example.com",
            "top-secret",
            "bearer-secret",
            "eyJhbGciOiJIUzI1NiJ9",
        ):
            self.assertNotIn(secret, persisted)
        self.assertIn("[REDACTED]", persisted)
        self.assertEqual([], list(attempt.glob(".agy-unredacted-*")))

    def test_timeout_sanitizes_log_and_removes_temp(self):
        attempt = self.attempt_dir("timeout-log")

        def timeout_runner(argv, cwd, timeout):
            Path(argv[argv.index("--log-file") + 1]).write_text(
                "email=user@example.com refresh_token=top-secret", encoding="utf-8"
            )
            raise subprocess.TimeoutExpired(argv, timeout)

        adapter = AntigravityAdapter(
            cli_path=self.cli,
            code_root=self.code_root,
            data_root=self.data_root,
            runner=timeout_runner,
        )
        with self.assertRaises(AntigravityTimeout):
            adapter.invoke("prompt", SCHEMA, attempt)

        persisted = (attempt / "agy.log").read_text(encoding="utf-8")
        self.assertNotIn("user@example.com", persisted)
        self.assertNotIn("top-secret", persisted)
        self.assertEqual([], list(attempt.glob(".agy-unredacted-*")))

    def test_process_error_sanitizes_log_and_removes_temp(self):
        attempt = self.attempt_dir("error-log")
        runner = FakeRunner(
            stream_returncode=1,
            stream_stderr="provider failed",
            log_text="account=user@example.com access_token=top-secret",
        )

        with self.assertRaises(AntigravityNotReady):
            self._adapter(runner).invoke("prompt", SCHEMA, attempt)

        persisted = (attempt / "agy.log").read_text(encoding="utf-8")
        self.assertNotIn("user@example.com", persisted)
        self.assertNotIn("top-secret", persisted)
        self.assertEqual([], list(attempt.glob(".agy-unredacted-*")))

    def test_timeout_non_success_malformed_schema_and_late_are_distinct(self):
        def timeout_runner(argv, cwd, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)

        timed = AntigravityAdapter(
            cli_path=self.cli,
            code_root=self.code_root,
            data_root=self.data_root,
            runner=timeout_runner,
        )
        with self.assertRaises(AntigravityTimeout):
            timed.invoke("prompt", SCHEMA, self.attempt_dir("timeout"))

        failed = FakeRunner(stream=stream_success().replace("SUCCESS", "ERROR"))
        with self.assertRaises(AntigravityEnvelopeError):
            self._adapter(failed).invoke("prompt", SCHEMA, self.attempt_dir("failed"))

        malformed = FakeRunner(stream="not-json\n")
        with self.assertRaises(AntigravityEnvelopeError):
            self._adapter(malformed).invoke(
                "prompt", SCHEMA, self.attempt_dir("malformed")
            )

        schema_error = FakeRunner(
            stream="", stream_returncode=1, stream_stderr="invalid json schema"
        )
        with self.assertRaises(AntigravitySchemaError):
            self._adapter(schema_error).invoke(
                "prompt", SCHEMA, self.attempt_dir("schema")
            )

        with self.assertRaises(AntigravityLateResult):
            self.adapter.invoke(
                "prompt", SCHEMA, self.attempt_dir("late"), late_check=lambda: True
            )

    def test_provider_errors_redact_account_and_token_text(self):
        runner = FakeRunner(
            models_returncode=1,
            models_stderr="login user@example.com token=super-secret",
        )
        with self.assertRaises(AntigravityNotReady) as raised:
            self._adapter(runner).preflight(self.attempt_dir("redaction"))

        message = str(raised.exception)
        self.assertNotIn("user@example.com", message)
        self.assertNotIn("super-secret", message)
        self.assertEqual("agy models 失敗 (exit 1)", message)

    def _adapter(self, runner):
        return AntigravityAdapter(
            cli_path=self.cli,
            code_root=self.code_root,
            data_root=self.data_root,
            runner=runner,
        )


class EnvelopeParserTest(unittest.TestCase):
    def test_parses_single_success_envelope(self):
        raw = json.dumps(
            {
                "status": "SUCCESS",
                "actual_model": MODEL,
                "structured_output": {"answer": "ok"},
                "duration_seconds": 1.25,
                "usage": {"total_tokens": 8},
                "tools": ["search_web"],
            }
        )

        parsed = parse_envelope(raw)

        self.assertEqual(MODEL, parsed.actual_model)
        self.assertEqual({"answer": "ok"}, parsed.structured_output)
        self.assertTrue(parsed.search_available)
        self.assertFalse(parsed.search_succeeded)

    def test_rejects_missing_structured_output(self):
        raw = json.dumps({"status": "SUCCESS", "actual_model": MODEL})
        with self.assertRaises(AntigravityEnvelopeError):
            parse_envelope(raw)


class AntigravityUnreclaimedTreeTest(unittest.TestCase):
    """整組回收不了時，Antigravity 邊界必須先 fail closed（Reviewer A-1 第三次）。"""

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

    def invoke(self, process_outcome, name):
        attempt_dir = self.data_root / "runs" / "run-1" / "agents" / "news" / name
        attempt_dir.mkdir(parents=True)
        adapter = AntigravityAdapter(
            cli_path=self.cli,
            code_root=self.code_root,
            data_root=self.data_root,
            runner=FakeRunner(process_outcome=process_outcome),
        )
        return adapter.invoke(PROMPT, SCHEMA, attempt_dir, require_search=True)

    def test_a_clean_looking_reply_is_refused_when_the_tree_was_not_reclaimed(self):
        # CLI 退出碼 0、envelope 合法、search_web 也真的跑過；唯一的問題是整組
        # 證明不了回收，這份回覆就永遠不可採用。
        with self.assertRaises(AntigravityTreeTermination) as caught:
            self.invoke(PROCESS_TREE_TERMINATION_FAILED, "unreclaimed")

        self.assertIsInstance(caught.exception, AntigravityError)
        self.assertEqual(PROCESS_TREE_TERMINATION_FAILED, str(caught.exception))

    def test_a_reclaimed_tree_still_admits_its_structured_output(self):
        result = self.invoke(PROCESS_GROUP_RECLAIMED, "reclaimed")

        self.assertEqual({"answer": "ready"}, result.structured_output)
        self.assertTrue(result.search_succeeded)

    def test_an_injected_runner_without_a_process_outcome_is_unchanged(self):
        result = self.invoke(None, "plain")

        self.assertEqual({"answer": "ready"}, result.structured_output)
        self.assertTrue(result.search_succeeded)


class CleanExitProcess:
    """``Popen`` duck type：自己乾淨收工，但整組證明不了已經回收。"""

    pid = 9501
    returncode = 0

    def communicate(self, input=None, timeout=None):
        return "agy stdout", ""

    def poll(self):
        return 0


class TerminatingRunnerOutcomeCarrierTest(unittest.TestCase):
    """``run_process`` 必須把 process 終局帶到 Antigravity 這一側。"""

    def test_the_completed_process_carries_the_process_outcome(self):
        process = CleanExitProcess()
        runner = FixedChildRunner(
            ProcessRegistry(killpg=deaf_killpg), process, key_source=lambda: "seat-key"
        )

        completed = runner.run_process(["fake-agy"], cwd=".", timeout=5)

        self.assertEqual(0, completed.returncode)
        self.assertEqual("agy stdout", completed.stdout)
        self.assertEqual(
            PROCESS_TREE_TERMINATION_FAILED,
            getattr(completed, "process_outcome", None),
        )


class TimedOutProcess:
    """``Popen`` duck type：第一次 communicate 逾時，整組回收後就收得到 EOF。"""

    def __init__(self, pid):
        self.pid = pid
        self.returncode = None
        self.communicate_calls = 0

    def communicate(self, input=None, timeout=None):
        self.communicate_calls += 1
        if self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(["fake-agy"], timeout)
        return "", ""

    def poll(self):
        return None


def raising_timeout_runner(process_outcome=None):
    """The group-safe runner's timeout shape, with or without a machine verdict."""

    def run(argv, cwd, timeout):
        error = subprocess.TimeoutExpired(list(argv), timeout)
        if process_outcome is not None:
            error.process_outcome = process_outcome
        raise error

    return run


class AntigravityTimeoutOutcomeTest(unittest.TestCase):
    """逾時與整組回收失敗同時成立時，永久不可採用的終局優先（Reviewer B-1）。"""

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

    def invoke(self, runner, name):
        attempt_dir = self.data_root / "runs" / "run-1" / "agents" / "news" / name
        attempt_dir.mkdir(parents=True)
        adapter = AntigravityAdapter(
            cli_path=self.cli,
            code_root=self.code_root,
            data_root=self.data_root,
            runner=runner,
        )
        return adapter.invoke(PROMPT, SCHEMA, attempt_dir, require_search=True)

    def test_run_process_carries_the_failed_verdict_on_its_timeout(self):
        process = UnreclaimableProcess(pid=9601)
        runner = FixedChildRunner(
            ProcessRegistry(killpg=deaf_killpg), process, key_source=lambda: "seat-key"
        )

        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            runner.run_process(["fake-agy"], cwd=".", timeout=0.01)

        self.assertEqual(
            PROCESS_TREE_TERMINATION_FAILED,
            getattr(caught.exception, "process_outcome", None),
        )
        # 有界 drain 仍然成立，沒有回到無界等待。
        self.assertFalse(process.unbounded_drain.is_set())
        self.assertEqual(2, process.communicate_calls)

    def test_run_process_carries_the_reclaimed_verdict_on_its_timeout(self):
        process = TimedOutProcess(9602)
        runner = FixedChildRunner(
            ProcessRegistry(killpg=FakeKillpg(FakeProcessGroup(9602))),
            process,
            key_source=lambda: "seat-key",
        )

        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            runner.run_process(["fake-agy"], cwd=".", timeout=0.01)

        self.assertEqual(
            PROCESS_GROUP_RECLAIMED,
            getattr(caught.exception, "process_outcome", None),
        )

    def test_a_timeout_whose_tree_was_not_reclaimed_is_refused_permanently(self):
        with self.assertRaises(AntigravityTreeTermination) as caught:
            self.invoke(
                raising_timeout_runner(PROCESS_TREE_TERMINATION_FAILED), "unreclaimed"
            )

        self.assertIsInstance(caught.exception, AntigravityError)
        self.assertEqual(PROCESS_TREE_TERMINATION_FAILED, str(caught.exception))

    def test_a_timeout_whose_tree_was_reclaimed_stays_a_plain_timeout(self):
        with self.assertRaises(AntigravityTimeout):
            self.invoke(raising_timeout_runner(PROCESS_GROUP_RECLAIMED), "reclaimed")

    def test_an_injected_runner_timeout_without_a_verdict_stays_a_plain_timeout(self):
        with self.assertRaises(AntigravityTimeout):
            self.invoke(raising_timeout_runner(), "plain")


if __name__ == "__main__":
    unittest.main()
