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
    AntigravityLateResult,
    AntigravityNotReady,
    AntigravitySchemaError,
    AntigravityTimeout,
    parse_envelope,
)
from hoya_market_agents.prompt_builder import ResearchSnapshot, build_provider_prompt


MODEL = "gemini-3.1-pro-high"
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def stream_success(model=MODEL, structured_output=None):
    structured_output = structured_output or {"answer": "ready"}
    events = [
        {
            "event": "init",
            "conversation_id": "fixture-conversation",
            "init": {"model": model, "tools": ["search_web", "view_file"]},
        },
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "structured_output": structured_output,
                "duration_seconds": 2.5,
                "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            },
        },
    ]
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
    ):
        self.stream = stream or stream_success()
        self.stream_returncode = stream_returncode
        self.stream_stderr = stream_stderr
        self.models_returncode = models_returncode
        self.models_stderr = models_stderr
        self.models_output = models_output
        self.calls = []

    def __call__(self, argv, cwd, timeout):
        self.calls.append((list(argv), Path(cwd), timeout))
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, "1.1.9\n", "")
        if argv[1:] == ["models"]:
            return subprocess.CompletedProcess(
                argv, self.models_returncode, self.models_output, self.models_stderr
            )
        return subprocess.CompletedProcess(
            argv, self.stream_returncode, self.stream, self.stream_stderr
        )


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
        self.assertEqual({"answer": "ready"}, result.structured_output)
        self.assertEqual(2.5, result.duration_seconds)
        self.assertEqual(14, result.usage["total_tokens"])

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
        )):
            adapter = AntigravityAdapter(
                cli_path=self.cli,
                code_root=self.code_root,
                data_root=self.data_root,
                runner=runner,
            )
            with self.assertRaises(AntigravityNotReady):
                adapter.preflight(self.attempt_dir("not-ready-{}".format(index)))

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

    def test_rejects_missing_structured_output(self):
        raw = json.dumps({"status": "SUCCESS", "actual_model": MODEL})
        with self.assertRaises(AntigravityEnvelopeError):
            parse_envelope(raw)


if __name__ == "__main__":
    unittest.main()
