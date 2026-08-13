"""Headless Codex CLI boundary for the three GPT research seats.

``codex exec`` performs the research; this module owns only the process shape,
the structured-output boundary and the failure vocabulary. It never interprets a
market stance.

The seat's answer never comes back on stdout: ``codex exec --json`` streams
machine-readable lifecycle events there and writes the final message to the file
named by ``--output-last-message``. That file is the single source of truth for
the answer; the JSONL stream is the single source of truth for search proof.

Live web search is a capability of the invocation, switched on the command line
rather than left to ``~/.codex/config.toml``: a research seat must not silently
degrade to training-data recall because a local config changed, and a call after
the evidence seal must not be able to search at all no matter what its prompt
says. ``invoke(..., allow_search=False)`` sends ``-c tools.web_search=false``.
Asking is not proof either way. A search counts only when the JSONL stream has a
matching ``item.started`` and non-error ``item.completed`` for the same
``web_search`` item id. Prose, URLs and stderr are never proof.

Failure vocabulary
------------------
Every failure is a ``CodexExecError`` carrying the scheduler's ``failure_kind``,
so the caller maps one exception class instead of branching on symptoms::

    CodexExecTimeout             -> "timeout"
    CodexExecProcessError        -> "process_error"
    CodexExecOutputError         -> "provider_error"
    CodexExecTreeTerminationError-> "provider_error"
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .claude_adapter import PROCESS_TREE_TERMINATION_FAILED, SubprocessRunner
from .provider_cli import PROVIDER_CODEX, provider_cli_argv0

# WSL 的 ``PATH`` 說了算（ADR 0009）：這裡不得凍結任何一位開發者家目錄下的路徑。
CODEX_CLI_PATH = provider_cli_argv0(PROVIDER_CODEX)
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
SEARCH_ITEM_TYPE = "web_search"
SEARCH_EVENT_STARTED = "item.started"
SEARCH_EVENT_COMPLETED = "item.completed"
SEARCH_ERROR_STATUSES = frozenset(("cancelled", "error", "failed"))


class CodexExecError(RuntimeError):
    """Base class for safe Codex failures; carries the scheduler failure kind."""

    failure_kind = "provider_error"


class CodexExecTimeout(CodexExecError):
    failure_kind = "timeout"


class CodexExecProcessError(CodexExecError):
    failure_kind = "process_error"


class CodexExecOutputError(CodexExecError):
    failure_kind = "provider_error"


class CodexExecEmptyOutputError(CodexExecOutputError):
    """交卷檔在，但裡面沒有東西。

    「沒交」與「交了但看不懂」是兩種不同的故障，修的方向也不同，所以它們必須
    是兩個型別——擠在同一個型別裡，穩定 failure code 就只能二選一猜錯一半。
    """


class CodexExecTreeTerminationError(CodexExecError):
    """整組回收不了：這次 invocation 的輸出永遠不可採用。"""


@dataclass(frozen=True)
class CodexExecResult:
    structured_output: dict
    elapsed_ms: int
    schema_path: Path
    last_message_path: Path
    search_invocations: int = 0
    search_parse_status: str = "no_search"
    malformed_event_count: int = 0
    search_activity_count: int = 0


@dataclass(frozen=True)
class CodexSearchProof:
    search_invocations: int
    parse_status: str
    malformed_event_count: int
    search_activity_count: int


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

    def invoke(self, prompt, schema, work_dir, allow_search=True, timeout_seconds=None):
        """``timeout_seconds`` overrides this call only; never the adapter.

        A debate turn's budget is whatever its own wall leaves, and turns share
        one adapter, so writing the budget onto ``self`` would let one seat's
        deadline follow another seat's call.
        """
        timeout_seconds = (
            self.timeout_seconds if timeout_seconds is None else timeout_seconds
        )
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
            timeout_seconds=timeout_seconds,
        )
        if output.process_outcome == PROCESS_TREE_TERMINATION_FAILED:
            # 整組證明不了回收就永遠不可採用：last message 不讀、schema 不驗、
            # 轉錄裡的搜尋紀錄也不得當成這一席的研究證明。
            raise CodexExecTreeTerminationError(PROCESS_TREE_TERMINATION_FAILED)
        if output.timed_out:
            raise CodexExecTimeout(
                "codex exec 超過 {} 秒".format(timeout_seconds)
            )
        if output.returncode != 0:
            raise CodexExecProcessError(
                _with_summary(
                    "codex exec 非零結束（exit {}）".format(output.returncode),
                    output.stderr,
                )
            )
        proof = parse_codex_search_proof(output.stdout)
        return CodexExecResult(
            structured_output=_read_last_message(last_message_path),
            elapsed_ms=output.elapsed_ms,
            schema_path=schema_path,
            last_message_path=last_message_path,
            search_invocations=proof.search_invocations,
            search_parse_status=proof.parse_status,
            malformed_event_count=proof.malformed_event_count,
            search_activity_count=proof.search_activity_count,
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
            "--json",
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
        raise CodexExecEmptyOutputError(
            "codex exec 的 last message 為空：{}".format(path)
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CodexExecOutputError(
            _with_summary("codex exec 的 last message 不是合法 JSON", raw)
        ) from exc
    if not isinstance(payload, dict):
        raise CodexExecOutputError("codex exec 的 last message 必須是 JSON 物件")
    return payload


def parse_codex_search_proof(stdout):
    """Validate matched Codex JSONL search lifecycle events, failing closed.

    One corrupt line or one incomplete/error lifecycle invalidates the whole
    proof. This prevents a good event elsewhere in the stream from laundering a
    tool-use-only, orphan or failed search.
    """
    started = set()
    completed = set()
    failed = set()
    malformed = 0
    orphan_result = False
    search_activity = set()

    for line in (stdout or "").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            malformed += 1
            continue
        if not isinstance(event, dict):
            malformed += 1
            continue

        event_type = event.get("type")
        if event_type not in (SEARCH_EVENT_STARTED, SEARCH_EVENT_COMPLETED):
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            malformed += 1
            continue
        if item.get("type") != SEARCH_ITEM_TYPE:
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            malformed += 1
            continue
        search_activity.add(item_id)

        if event_type == SEARCH_EVENT_STARTED:
            if item_id in started or item_id in completed or item_id in failed:
                malformed += 1
                continue
            started.add(item_id)
            continue

        if item_id in completed or item_id in failed:
            malformed += 1
            continue
        if item_id not in started:
            orphan_result = True
            continue
        if _search_result_failed(item):
            failed.add(item_id)
        else:
            completed.add(item_id)

    if malformed:
        status = "malformed"
        verified = 0
    elif orphan_result:
        status = "orphan_result"
        verified = 0
    elif failed:
        status = "error_result"
        verified = 0
    elif started != completed:
        status = "missing_result"
        verified = 0
    elif completed:
        status = "matched"
        verified = len(completed)
    else:
        status = "no_search"
        verified = 0

    return CodexSearchProof(
        search_invocations=verified,
        parse_status=status,
        malformed_event_count=malformed,
        search_activity_count=len(search_activity),
    )


def _search_result_failed(item):
    return (
        item.get("error") not in (None, False, "")
        or item.get("success") is False
        or str(item.get("status", "")).lower() in SEARCH_ERROR_STATUSES
    )


def _with_summary(message, text):
    """失敗訊息帶上現場摘要，診斷時不必重跑一次真實 provider。"""
    summary = (text or "").strip()
    if not summary:
        return message
    return "{}：{}".format(message, summary[:STDERR_SUMMARY_CHARS])
