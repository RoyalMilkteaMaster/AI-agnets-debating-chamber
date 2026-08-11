"""Headless Codex CLI boundary for the three GPT research seats.

``codex exec`` performs the research; this module owns only the process shape,
the structured-output boundary and the failure vocabulary. It never interprets a
market stance.

The seat's answer never comes back on stdout: ``codex exec`` streams its session
transcript there and writes the final message to the file named by
``--output-last-message``. That file is the single source of truth here.

Live web search is a capability of the invocation, switched on the command line
rather than left to ``~/.codex/config.toml``: a research seat must not silently
degrade to training-data recall because a local config changed, and a call after
the evidence seal must not be able to search at all no matter what its prompt
says. ``invoke(..., allow_search=False)`` sends ``-c tools.web_search=false``.
Asking is not proof either way, so the stderr transcript is counted too: every
live search prints one ``web search: <query>`` line, and that count is reported
as ``search_invocations`` — evidence of research for a research call, and
evidence of a violation for a sealed one.

Failure vocabulary
------------------
Every failure is a ``CodexExecError`` carrying the scheduler's ``failure_kind``,
so the caller maps one exception class instead of branching on symptoms::

    CodexExecTimeout       -> "timeout"
    CodexExecProcessError  -> "process_error"
    CodexExecOutputError   -> "provider_error"
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .claude_adapter import SubprocessRunner

CODEX_CLI_PATH = "/home/leslie/.local/bin/codex"
CODEX_MODEL = "gpt-5.6-sol"
CODEX_SANDBOX_MODE = "read-only"
# Standalone default. Formal research runs inject the question-specific timeout
# derived from research_deadlines so this adapter cannot end before the shared wall.
CODEX_TIMEOUT_SECONDS = 345
CODEX_SCHEMA_NAME = "codex-output-schema.json"
CODEX_LAST_MESSAGE_NAME = "codex-last-message.txt"
STDERR_SUMMARY_CHARS = 200
# codex 的搜尋能力開關；-c 覆寫本機 config，所以每次呼叫都自己決定要不要能上網。
SEARCH_CAPABILITY_KEY = "tools.web_search"
# codex exec 每次動用網路搜尋，會在 stderr 轉錄印出一行 "web search: <query>"。
# 來源版本：codex-cli 0.146.0；未來版本改了字串就從這個常數開始查。
SEARCH_INVOCATION_MARKER = "web search:"


class CodexExecError(RuntimeError):
    """Base class for safe Codex failures; carries the scheduler failure kind."""

    failure_kind = "provider_error"


class CodexExecTimeout(CodexExecError):
    failure_kind = "timeout"


class CodexExecProcessError(CodexExecError):
    failure_kind = "process_error"


class CodexExecOutputError(CodexExecError):
    failure_kind = "provider_error"


@dataclass(frozen=True)
class CodexExecResult:
    structured_output: dict
    elapsed_ms: int
    schema_path: Path
    last_message_path: Path
    search_invocations: int = 0


class CodexExecAdapter:
    """Run one fresh, schema-constrained ``codex exec`` invocation."""

    def __init__(
        self,
        cli_path=CODEX_CLI_PATH,
        runner=None,
        model=CODEX_MODEL,
        timeout_seconds=CODEX_TIMEOUT_SECONDS,
    ):
        self.cli_path = Path(cli_path)
        self.runner = runner or SubprocessRunner()
        self.model = model
        self.timeout_seconds = timeout_seconds

    def invoke(self, prompt, schema, work_dir, allow_search=True):
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        schema_path = _write_schema(work_dir, schema)
        last_message_path = work_dir / CODEX_LAST_MESSAGE_NAME
        output = self.runner.run(
            self._command(
                prompt, work_dir, schema_path, last_message_path, allow_search
            ),
            # codex exec reads stdin when it is a pipe; an empty string closes it
            # immediately so a headless seat can never hang waiting for input.
            input_text="",
            cwd=work_dir,
            timeout_seconds=self.timeout_seconds,
        )
        if output.timed_out:
            raise CodexExecTimeout(
                "codex exec 超過 {} 秒".format(self.timeout_seconds)
            )
        if output.returncode != 0:
            raise CodexExecProcessError(
                _with_summary(
                    "codex exec 非零結束（exit {}）".format(output.returncode),
                    output.stderr,
                )
            )
        return CodexExecResult(
            structured_output=_read_last_message(last_message_path),
            elapsed_ms=output.elapsed_ms,
            schema_path=schema_path,
            last_message_path=last_message_path,
            search_invocations=_count_search_invocations(output.stderr),
        )

    def _command(
        self, prompt, work_dir, schema_path, last_message_path, allow_search
    ):
        return [
            str(self.cli_path),
            "exec",
            "-m",
            self.model,
            "-s",
            CODEX_SANDBOX_MODE,
            "--skip-git-repo-check",
            "-c",
            _search_capability(allow_search),
            "-C",
            str(work_dir),
            "--output-schema",
            str(schema_path),
            "-o",
            str(last_message_path),
            "--color",
            "never",
            str(prompt),
        ]


def _search_capability(allow_search):
    """Switch the live-search capability itself, never just the prompt wording."""
    return "{}={}".format(SEARCH_CAPABILITY_KEY, "true" if allow_search else "false")


def _write_schema(work_dir, schema):
    try:
        content = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
    except (TypeError, ValueError) as exc:
        raise CodexExecOutputError("codex output schema 無法序列化") from exc
    path = work_dir / CODEX_SCHEMA_NAME
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except FileExistsError as exc:
        raise CodexExecOutputError(
            "codex attempt artifact 不得覆寫：{}".format(path)
        ) from exc
    except OSError as exc:
        raise CodexExecOutputError("codex output schema 無法寫入：{}".format(exc)) from exc
    return path


def _read_last_message(path):
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CodexExecOutputError(
            "codex exec 未寫出 last message 檔：{}".format(path)
        ) from exc
    if not raw.strip():
        raise CodexExecOutputError("codex exec 的 last message 為空：{}".format(path))
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CodexExecOutputError(
            _with_summary("codex exec 的 last message 不是合法 JSON", raw)
        ) from exc
    if not isinstance(payload, dict):
        raise CodexExecOutputError("codex exec 的 last message 必須是 JSON 物件")
    return payload


def _count_search_invocations(stderr):
    """Count the live searches the transcript proves this seat actually ran."""
    marker = SEARCH_INVOCATION_MARKER.lower()
    return sum(1 for line in (stderr or "").splitlines() if marker in line.lower())


def _with_summary(message, text):
    """失敗訊息帶上現場摘要，診斷時不必重跑一次真實 provider。"""
    summary = (text or "").strip()
    if not summary:
        return message
    return "{}：{}".format(message, summary[:STDERR_SUMMARY_CHARS])
