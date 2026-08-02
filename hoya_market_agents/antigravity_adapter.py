"""Fail-closed Antigravity CLI boundary for the stateless Gemini seat.

Search capability, honestly
---------------------------
The other two providers can switch live search off for one call. ``agy`` cannot:
version 1.1.9 has no flag that removes a tool, and every session's ``init``
event lists ``search_web`` (verified against the real CLI). So the seal is
enforced one layer later instead of being claimed at the capability layer:
``invoke(..., allow_search=False)`` accepts a reply only if the transcript shows
no completed ``search_web`` step, and raises :class:`AntigravityPostSealSearch`
if the seat searched anyway. Any report of this system must say exactly that —
capability could not be closed, the result was refused instead.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


CLI_PATH = Path("/home/leslie/.local/bin/agy")
MODEL = "gemini-3.1-pro-high"
EFFORT = "high"
SEARCH_TOOL = "search_web"

PREFLIGHT_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "enum": ["ready"]}},
    "required": ["answer"],
    "additionalProperties": False,
}
PREFLIGHT_PROMPT = (
    "Use search_web exactly once to search for the official Google Antigravity CLI "
    "documentation page. This is a low-risk capability smoke only. After search_web "
    "returns successfully, return the required JSON with answer set to ready. If the "
    "search cannot complete, do not claim ready. Do not reveal account, OAuth, cookie, "
    "token, environment data, or hidden reasoning. Do not read or write files and do "
    "not run terminal commands."
)


class AntigravityError(RuntimeError):
    """Base class for safe provider failures."""


class AntigravityNotReady(AntigravityError):
    pass


class AntigravityBoundaryError(AntigravityError):
    pass


class AntigravityEnvelopeError(AntigravityError):
    pass


class AntigravitySchemaError(AntigravityError):
    pass


class AntigravityTimeout(AntigravityError):
    pass


class AntigravityLateResult(AntigravityError):
    pass


class AntigravityPostSealSearch(AntigravityError):
    """The seat ran a live search on a call that had to stay inside the seal."""


@dataclass(frozen=True)
class ParsedEnvelope:
    status: str
    structured_output: object
    actual_model: str | None
    duration_seconds: float | int | None
    usage: dict
    search_available: bool
    search_succeeded: bool


@dataclass(frozen=True)
class AdapterResult:
    structured_output: object
    actual_model: str | None
    duration_seconds: float | int | None
    usage: dict
    search_available: bool
    search_succeeded: bool
    schema_path: Path
    schema_sha256: str


@dataclass(frozen=True)
class PreflightResult:
    ready: bool
    version: str
    requested_model: str
    actual_model: str
    effort: str
    search_available: bool
    search_succeeded: bool
    structured_output: object
    duration_seconds: float | int | None
    usage: dict
    schema_path: Path
    schema_sha256: str


class AntigravityAdapter:
    """Run one fresh, schema-constrained Antigravity print invocation."""

    def __init__(
        self,
        cli_path=CLI_PATH,
        code_root=None,
        data_root=None,
        runner=None,
        model=MODEL,
        effort=EFFORT,
        timeout_seconds=60,
    ):
        self.cli_path = Path(cli_path)
        self.code_root = Path(code_root or Path(__file__).resolve().parent.parent)
        self.data_root = Path(data_root or self.code_root.parent / "hoya-bit-market-agents_data")
        self.runner = runner or _run_process
        self.model = model
        self.effort = effort
        self.timeout_seconds = timeout_seconds

    def preflight(self, attempt_dir):
        self._require_cli()
        attempt_dir = self._attempt_dir(attempt_dir)
        version = self._simple_command("--version").strip()
        if not version:
            raise AntigravityNotReady("agy version 無法辨識")
        models = {line.strip() for line in self._simple_command("models").splitlines()}
        if self.model not in models:
            raise AntigravityNotReady("指定模型不存在：{}".format(self.model))

        result = self.invoke(
            PREFLIGHT_PROMPT,
            PREFLIGHT_SCHEMA,
            attempt_dir,
            require_search=True,
        )
        if result.structured_output != {"answer": "ready"}:
            raise AntigravityNotReady("Antigravity structured preflight contract 不符")
        return PreflightResult(
            ready=True,
            version=version,
            requested_model=self.model,
            actual_model=result.actual_model,
            effort=self.effort,
            search_available=True,
            search_succeeded=True,
            structured_output=result.structured_output,
            duration_seconds=result.duration_seconds,
            usage=result.usage,
            schema_path=result.schema_path,
            schema_sha256=result.schema_sha256,
        )

    def invoke(
        self,
        prompt,
        schema,
        attempt_dir,
        late_check=None,
        require_search=False,
        allow_search=True,
    ):
        self._require_cli()
        attempt_dir = self._attempt_dir(attempt_dir)
        schema_path, schema_sha256 = _write_schema(attempt_dir, schema)
        raw_path = attempt_dir / "raw-envelope.jsonl"
        log_path = attempt_dir / "agy.log"
        raw_log_path = _temporary_log_path(attempt_dir)
        argv = [
            str(self.cli_path),
            "--print",
            str(prompt),
            "--model",
            self.model,
            "--effort",
            self.effort,
            "--output-format",
            "stream-json",
            "--json-schema",
            str(schema_path),
            "--print-timeout",
            "{}s".format(self.timeout_seconds),
            "--mode",
            "plan",
            "--sandbox",
            "--disable-slash-commands",
            "--log-file",
            str(raw_log_path),
        ]
        try:
            completed = self.runner(
                argv, cwd=attempt_dir, timeout=self.timeout_seconds + 10
            )
        except subprocess.TimeoutExpired as exc:
            raise AntigravityTimeout(
                "Antigravity 超過 {} 秒".format(self.timeout_seconds)
            ) from exc
        finally:
            _persist_sanitized_log(raw_log_path, log_path)

        raw = completed.stdout or ""
        _write_once(raw_path, raw.encode("utf-8"))
        if completed.returncode != 0:
            if "schema" in (completed.stderr or "").lower():
                raise AntigravitySchemaError("Antigravity schema rejected")
            raise AntigravityNotReady(
                "Antigravity process exit {}".format(completed.returncode)
            )

        parsed = parse_envelope(raw)
        if parsed.actual_model != self.model:
            raise AntigravityNotReady(
                "實際模型不符：預期 {}，實際 {}".format(
                    self.model, parsed.actual_model or "未回報"
                )
            )
        _require_search_policy(parsed, allow_search, require_search)
        if late_check is not None and late_check():
            raise AntigravityLateResult("Antigravity result 到達時接收窗口已關閉")
        return AdapterResult(
            structured_output=parsed.structured_output,
            actual_model=parsed.actual_model,
            duration_seconds=parsed.duration_seconds,
            usage=parsed.usage,
            search_available=parsed.search_available,
            search_succeeded=parsed.search_succeeded,
            schema_path=schema_path,
            schema_sha256=schema_sha256,
        )

    def _simple_command(self, command):
        try:
            completed = self.runner(
                [str(self.cli_path), command],
                cwd=self.data_root,
                timeout=min(self.timeout_seconds, 15),
            )
        except subprocess.TimeoutExpired as exc:
            raise AntigravityNotReady("agy {} timeout".format(command)) from exc
        if completed.returncode != 0:
            raise AntigravityNotReady(
                "agy {} 失敗 (exit {})".format(command, completed.returncode)
            )
        return completed.stdout or ""

    def _require_cli(self):
        if not self.cli_path.is_file() or not os.access(self.cli_path, os.X_OK):
            raise AntigravityNotReady("找不到可執行 Antigravity CLI：{}".format(self.cli_path))

    def _attempt_dir(self, attempt_dir):
        data_root = self.data_root.resolve()
        code_root = self.code_root.resolve()
        attempt = Path(attempt_dir).resolve()
        if not _is_within(attempt, data_root) or _is_within(attempt, code_root):
            raise AntigravityBoundaryError(
                "attempt 只能位於 Data Root 且不得位於 Code Root：{}".format(attempt)
            )
        attempt.mkdir(parents=True, exist_ok=True)
        return attempt


def _require_search_policy(parsed, allow_search, require_search):
    """Hold this call to its phase's search rule, in the only place it is known."""
    if not allow_search:
        if parsed.search_succeeded:
            raise AntigravityPostSealSearch(
                "封存後的呼叫仍執行了 search_web，這份回覆不採用"
            )
        return
    if not parsed.search_available:
        raise AntigravityNotReady("Antigravity session 未提供 search_web")
    if require_search and not parsed.search_succeeded:
        raise AntigravityNotReady("Antigravity search_web smoke 未成功")


def parse_envelope(raw):
    """Parse either one JSON envelope or Antigravity's JSONL event envelope."""
    if not isinstance(raw, str) or not raw.strip():
        raise AntigravityEnvelopeError("Antigravity envelope 為空")
    try:
        records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise AntigravityEnvelopeError("Antigravity envelope 不是合法 JSON") from exc

    init = None
    envelope = None
    search_succeeded = False
    for record in records:
        if not isinstance(record, dict):
            raise AntigravityEnvelopeError("Antigravity envelope 必須為 JSON 物件")
        if record.get("event") == "init":
            init = record.get("init")
        elif record.get("event") == "result":
            envelope = record.get("result")
        elif record.get("event") == "step_update":
            update = record.get("step_update")
            if (
                isinstance(update, dict)
                and update.get("step_type") == "tool"
                and update.get("tool_name") == SEARCH_TOOL
                and update.get("state") == "DONE"
            ):
                search_succeeded = True
    if envelope is None and len(records) == 1:
        envelope = records[0]
    if not isinstance(envelope, dict):
        raise AntigravityEnvelopeError("Antigravity envelope 缺少 result")
    if envelope.get("status") != "SUCCESS":
        raise AntigravityEnvelopeError(
            "Antigravity status 不是 SUCCESS：{}".format(envelope.get("status"))
        )
    structured = envelope.get("structured_output")
    if not isinstance(structured, (dict, list)):
        raise AntigravityEnvelopeError("Antigravity envelope 缺少 structured_output")
    duration = envelope.get("duration_seconds")
    if duration is not None and (
        not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration < 0
    ):
        raise AntigravityEnvelopeError("duration_seconds 格式錯誤")
    usage = envelope.get("usage", {})
    if not isinstance(usage, dict):
        raise AntigravityEnvelopeError("usage 格式錯誤")

    metadata = init if isinstance(init, dict) else envelope
    model = metadata.get("model") or envelope.get("actual_model") or envelope.get("model")
    tools = metadata.get("tools", envelope.get("tools", ()))
    search_available = isinstance(tools, list) and SEARCH_TOOL in tools
    return ParsedEnvelope(
        status="SUCCESS",
        structured_output=structured,
        actual_model=model,
        duration_seconds=duration,
        usage=usage,
        search_available=search_available,
        search_succeeded=search_succeeded,
    )


def _write_schema(attempt_dir, schema):
    try:
        content = (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise AntigravitySchemaError("schema 無法序列化") from exc
    path = attempt_dir / "input-schema.json"
    _write_once(path, content)
    return path.resolve(), hashlib.sha256(content).hexdigest()


def _write_once(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except FileExistsError as exc:
        raise AntigravityBoundaryError("attempt artifact 不得覆寫：{}".format(path)) from exc


def _temporary_log_path(attempt_dir):
    descriptor, name = tempfile.mkstemp(prefix=".agy-unredacted-", dir=attempt_dir)
    os.close(descriptor)
    return Path(name)


def _persist_sanitized_log(raw_path, safe_path):
    try:
        raw = raw_path.read_text(encoding="utf-8", errors="replace")
        _atomic_write_once(safe_path, _sanitize_log(raw).encode("utf-8"))
    finally:
        raw_path.unlink(missing_ok=True)


def _sanitize_log(text):
    text = re.sub(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+[A-Z0-9._~+/-]+=*", "Bearer [REDACTED]", text)
    text = re.sub(
        r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\b",
        "[REDACTED]",
        text,
    )
    return re.sub(
        r"(?i)([\"']?(?:access[_-]?token|refresh[_-]?token|id[_-]?token|oauth|"
        r"token|cookie|jwt|authorization|credential|client[_-]?secret|password|"
        r"account|email|username|user)[\"']?\s*[:=]\s*)"
        r"(?:[\"'][^\"']*[\"']|[^\s,;]+)",
        r"\1[REDACTED]",
        text,
    )


def _atomic_write_once(path, content):
    if path.exists():
        raise AntigravityBoundaryError("attempt artifact 不得覆寫：{}".format(path))
    descriptor, temporary_name = tempfile.mkstemp(prefix=".safe-log-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run_process(argv, cwd, timeout):
    return subprocess.run(
        argv,
        cwd=str(cwd),
        timeout=timeout,
        capture_output=True,
        text=True,
        check=False,
    )


def _is_within(path, parent):
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
