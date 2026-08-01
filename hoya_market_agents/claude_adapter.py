"""Minimal Claude Code CLI adapter and fail-closed preflight.

Claude performs research; this module only owns process identity, isolation,
timeouts and the structured-output boundary.  It never interprets a market
stance.
"""

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path


CLAUDE_CLI_PATH = "/home/leslie/.local/bin/claude"
CLAUDE_MODEL_ALIAS = "opus"
CLAUDE_SEAT_SESSIONS = {
    "spot-technical": "86d4610c-3380-5a99-853c-10d742fb679c",
    "derivatives": "2aa972a3-1a82-5d56-8c6a-22dc77c97121",
    "onchain": "59bfb4b2-28b7-59ca-92bc-c296e9325380",
}

SMOKE_SCHEMA = {
    "type": "object",
    "properties": {
        "seat_id": {"type": "string"},
        "search_ok": {"type": "boolean"},
        "message": {"type": "string"},
    },
    "required": ["seat_id", "search_ok", "message"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ProcessOutput:
    returncode: int | None
    stdout: str
    stderr: str
    elapsed_ms: int
    timed_out: bool = False


class SubprocessRunner:
    """Small injectable seam around ``subprocess.run``."""

    def run(self, args, *, input_text, cwd, timeout_seconds):
        started = time.monotonic()
        try:
            result = subprocess.run(
                list(args),
                input=input_text,
                cwd=str(cwd),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            return ProcessOutput(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return ProcessOutput(
                returncode=None,
                stdout=_text(exc.stdout),
                stderr=_text(exc.stderr),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                timed_out=True,
            )
        except OSError as exc:
            return ProcessOutput(
                returncode=127,
                stdout="",
                stderr=str(exc),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )


@dataclass(frozen=True)
class ClaudeAttemptRequest:
    seat_id: str
    attempt_id: str
    prompt: str
    attempt_dir: Path
    resume: bool = False
    timeout_seconds: float = 90
    json_schema: dict = field(default_factory=lambda: dict(SMOKE_SCHEMA))
    validator: object = None


@dataclass(frozen=True)
class ClaudeAttemptResult:
    seat_id: str
    attempt_id: str
    masked_session_id: str
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    elapsed_ms: int
    actual_model: str | None = None
    usage: dict = field(default_factory=dict)
    web_search_requests: int = 0
    structured_output: dict | None = None
    error: str | None = None

    @property
    def scheduler_failure_kind(self):
        if self.status == "ok":
            return None
        if self.status == "timeout":
            return "timeout"
        if self.status == "process_error":
            return "process_error"
        return "provider_error"


class ClaudeAdapter:
    """Build and execute one isolated Claude seat invocation."""

    def __init__(self, *, runner, code_root, data_root, cli_path=CLAUDE_CLI_PATH):
        self.runner = runner
        self.code_root = Path(code_root).resolve()
        self.data_root = Path(data_root).resolve()
        self.cli_path = str(cli_path)

    def run(self, request):
        session_id = self._session_id(request.seat_id)
        attempt_dir = Path(request.attempt_dir).resolve()
        self._validate_attempt_dir(attempt_dir)
        output = self.runner.run(
            self._command(request, session_id),
            input_text=request.prompt,
            cwd=attempt_dir,
            timeout_seconds=request.timeout_seconds,
        )
        return self._result(request, session_id, output)

    def run_concurrent(self, requests):
        requests = tuple(requests)
        with ThreadPoolExecutor(max_workers=len(requests) or 1) as executor:
            results = executor.map(self.run, requests)
        return {result.seat_id: result for result in results}

    def resumed_request(self, request, *, attempt_id, prompt, attempt_dir):
        """Create the same-seat checkpoint continuation used by scheduler hooks."""
        return ClaudeAttemptRequest(
            seat_id=request.seat_id,
            attempt_id=attempt_id,
            prompt=prompt,
            attempt_dir=Path(attempt_dir),
            resume=True,
            timeout_seconds=request.timeout_seconds,
            json_schema=request.json_schema,
            validator=request.validator,
        )

    def _command(self, request, session_id):
        command = [
            self.cli_path,
            "-p",
            "--model",
            CLAUDE_MODEL_ALIAS,
            "--permission-mode",
            "dontAsk",
            "--tools",
            "WebSearch,WebFetch",
            "--allowedTools",
            "WebSearch,WebFetch",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(request.json_schema, separators=(",", ":")),
        ]
        command += ["--resume", session_id] if request.resume else ["--session-id", session_id]
        return command

    def _result(self, request, session_id, output):
        common = {
            "seat_id": request.seat_id,
            "attempt_id": request.attempt_id,
            "masked_session_id": mask_session_id(session_id),
            "exit_code": output.returncode,
            "stdout": output.stdout,
            "stderr": output.stderr,
            "elapsed_ms": output.elapsed_ms,
        }
        if output.timed_out:
            return ClaudeAttemptResult(status="timeout", error="claude_cli_timeout", **common)
        if output.returncode != 0:
            return ClaudeAttemptResult(status="process_error", error="claude_cli_nonzero_exit", **common)
        if not output.stdout.strip():
            return ClaudeAttemptResult(status="empty_output", error="claude_cli_empty_stdout", **common)
        try:
            envelope = json.loads(output.stdout)
        except json.JSONDecodeError:
            return ClaudeAttemptResult(status="malformed_output", error="claude_cli_invalid_json", **common)
        if not isinstance(envelope, dict) or envelope.get("is_error") is True:
            return ClaudeAttemptResult(status="provider_error", error="claude_cli_error_envelope", **common)
        structured = envelope.get("structured_output")
        if structured is None and isinstance(envelope.get("result"), dict):
            structured = envelope["result"]
        if not _valid_structured_output(structured, request.seat_id):
            return ClaudeAttemptResult(status="invalid_schema", error="structured_output_invalid", **common)
        if request.validator is not None:
            try:
                request.validator(structured)
            except (TypeError, ValueError):
                return ClaudeAttemptResult(
                    status="invalid_schema",
                    error="contract_validator_rejected",
                    **common,
                )
        usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
        model_usage = envelope.get("modelUsage")
        return ClaudeAttemptResult(
            status="ok",
            actual_model=_actual_model(model_usage),
            usage=usage,
            web_search_requests=_web_search_requests(usage, model_usage),
            structured_output=structured,
            **common,
        )

    def _session_id(self, seat_id):
        try:
            return CLAUDE_SEAT_SESSIONS[seat_id]
        except KeyError as exc:
            raise ValueError("seat_id 不是三個固定 Claude 席：{}".format(seat_id)) from exc

    def _validate_attempt_dir(self, attempt_dir):
        if not attempt_dir.is_dir() or not attempt_dir.is_relative_to(self.data_root):
            raise ValueError("Claude attempt 目錄必須位於 Data Root 內且已存在")
        if attempt_dir.is_relative_to(self.code_root):
            raise ValueError("Claude attempt 不得使用 Code Root")


def run_claude_preflight(
    *,
    seats,
    cli_path=CLAUDE_CLI_PATH,
    code_root,
    data_root,
    runner=None,
    environ=None,
    path_exists=None,
):
    """Run the real, minimal subscription/search/structured/resume smoke."""
    runner = runner or SubprocessRunner()
    environ = os.environ if environ is None else environ
    path_exists = Path.is_file if path_exists is None else path_exists
    code_root = Path(code_root).resolve()
    data_root = Path(data_root).resolve()
    reasons = []
    if seats != 3:
        reasons.append("claude_seats_must_equal_3")
    if environ.get("ANTHROPIC_API_KEY"):
        reasons.append("ANTHROPIC_API_KEY_present")
    if not path_exists(Path(cli_path)):
        reasons.append("claude_cli_missing")
    if reasons:
        return _preflight_report(False, None, [], reasons, False)

    data_root.mkdir(parents=True, exist_ok=True)
    version = runner.run(
        [str(cli_path), "--version"],
        input_text="",
        cwd=data_root,
        timeout_seconds=10,
    )
    if version.returncode != 0 or not version.stdout.strip():
        return _preflight_report(False, None, [], ["claude_version_unavailable"], False)

    auth = runner.run(
        [str(cli_path), "auth", "status", "--json"],
        input_text="",
        cwd=data_root,
        timeout_seconds=10,
    )
    try:
        auth_status = json.loads(auth.stdout) if auth.returncode == 0 else {}
    except json.JSONDecodeError:
        auth_status = {}
    if not (
        auth_status.get("loggedIn") is True
        and auth_status.get("authMethod") == "claude.ai"
        and auth_status.get("subscriptionType") == "max"
    ):
        return _preflight_report(
            False,
            version.stdout.strip().splitlines()[0],
            [],
            ["claude_ai_max_login_required"],
            False,
        )

    summaries = []
    resume_ok = False
    session_root = data_root / "sessions" / "claude"
    session_root.mkdir(parents=True, exist_ok=True)
    try:
        adapter = ClaudeAdapter(
            runner=runner,
            code_root=code_root,
            data_root=data_root,
            cli_path=cli_path,
        )
        requests = []
        search_nonce = "hoya-{:x}".format(time.time_ns())
        for seat_id in CLAUDE_SEAT_SESSIONS:
            attempt_dir = session_root / seat_id
            attempt_dir.mkdir(parents=True, exist_ok=True)
            requests.append(
                ClaudeAttemptRequest(
                    seat_id=seat_id,
                    attempt_id="{}-preflight-a1".format(seat_id),
                    prompt=(
                        "This is a tool capability test. You MUST invoke WebSearch exactly once "
                        "with the unique query 'Claude Code CLI official docs {} {}'; prior "
                        "knowledge or a previous search is not acceptable. Return seat_id={!r}, "
                        "search_ok=true only after the WebSearch tool returns successfully, and "
                        "a short public message. If you did not invoke WebSearch, return false."
                    ).format(search_nonce, seat_id, seat_id),
                    attempt_dir=attempt_dir,
                    timeout_seconds=45,
                )
            )
        results = adapter.run_concurrent(requests)
        # A fixed UUID may already exist from an earlier preflight.  Retry that
        # same logical seat by resuming it; never mint another identity or vote.
        for request in requests:
            if results[request.seat_id].status != "process_error":
                continue
            results[request.seat_id] = adapter.run(
                adapter.resumed_request(
                    request,
                    attempt_id="{}-retry".format(request.attempt_id),
                    prompt=request.prompt,
                    attempt_dir=request.attempt_dir,
                )
            )
        for request in requests:
            result = results[request.seat_id]
            if result.status != "ok" or result.web_search_requests > 0:
                continue
            results[request.seat_id] = adapter.run(
                adapter.resumed_request(
                    request,
                    attempt_id="{}-search-retry".format(request.attempt_id),
                    prompt=(
                        "Search capability retry: MUST invoke WebSearch once for the unique "
                        "query '{} retry'. Return seat_id={!r}, search_ok=true only after the "
                        "tool returns, and a short public message."
                    ).format(search_nonce, request.seat_id),
                    attempt_dir=request.attempt_dir,
                )
            )
        for seat_id in CLAUDE_SEAT_SESSIONS:
            result = results[seat_id]
            if result.status != "ok":
                reasons.append("{}:{}".format(seat_id, result.status))
            elif "opus" not in (result.actual_model or ""):
                reasons.append("{}:actual_model_not_opus".format(seat_id))
            elif result.web_search_requests < 1 or result.structured_output.get("search_ok") is not True:
                reasons.append("{}:search_unavailable".format(seat_id))
            summaries.append(_seat_summary(result))

        first = requests[0]
        if results[first.seat_id].status == "ok":
            resumed = adapter.run(
                adapter.resumed_request(
                    first,
                    attempt_id="{}-preflight-a2".format(first.seat_id),
                    prompt=(
                        "Resume the same public session. Return seat_id={!r}, search_ok=true, "
                        "and message='resume-ok'."
                    ).format(first.seat_id),
                    attempt_dir=first.attempt_dir,
                )
            )
            resume_ok = (
                resumed.status == "ok"
                and resumed.structured_output.get("seat_id") == first.seat_id
            )
            if not resume_ok:
                reasons.append("checkpoint_resume_failed")

    finally:
        # The stable directories provide cwd identity for --resume. Claude has
        # no write-capable tool, so any file here is an isolation failure.
        if any(path.is_file() for path in session_root.rglob("*")):
            reasons.append("claude_session_directory_write_detected")

    return _preflight_report(
        not reasons,
        version.stdout.strip().splitlines()[0],
        summaries,
        reasons,
        resume_ok,
    )


def mask_session_id(session_id):
    return "{}-…-{}".format(session_id[:8], session_id[-4:])


def _valid_structured_output(value, expected_seat_id):
    return (
        isinstance(value, dict)
        and value.get("seat_id") == expected_seat_id
        and isinstance(value.get("search_ok"), bool)
        and isinstance(value.get("message"), str)
        and bool(value["message"].strip())
    )


def _actual_model(model_usage):
    if not isinstance(model_usage, dict):
        return None
    models = []
    for key, value in model_usage.items():
        if not isinstance(value, dict):
            continue
        model = value.get("canonicalModel") or key
        if "opus" in model:
            return model
        models.append(model)
    return models[0] if len(models) == 1 else None


def _web_search_requests(usage, model_usage):
    server = usage.get("server_tool_use") if isinstance(usage, dict) else None
    value = server.get("web_search_requests", 0) if isinstance(server, dict) else 0
    usage_count = value if isinstance(value, int) and not isinstance(value, bool) else 0
    model_count = 0
    if isinstance(model_usage, dict):
        for details in model_usage.values():
            count = details.get("webSearchRequests", 0) if isinstance(details, dict) else 0
            if isinstance(count, int) and not isinstance(count, bool):
                model_count += count
    return max(usage_count, model_count)


def _seat_summary(result):
    return {
        "seat_id": result.seat_id,
        "session_id": result.masked_session_id,
        "model_alias": CLAUDE_MODEL_ALIAS,
        "actual_model": result.actual_model,
        "status": result.status,
        "exit_code": result.exit_code,
        "elapsed_ms": result.elapsed_ms,
        "web_search_requests": result.web_search_requests,
        "schema_valid": result.status == "ok",
        "usage": {
            key: result.usage[key]
            for key in ("input_tokens", "output_tokens")
            if isinstance(result.usage.get(key), int)
        },
    }


def _preflight_report(ready, version, seats, reasons, resume_ok):
    return {
        "ready": ready,
        "status": "READY" if ready else "NOT READY",
        "provider": "claude",
        "cli_version": version,
        "auth": (
            "claude.ai Max"
            if version is not None and "claude_ai_max_login_required" not in reasons
            else None
        ),
        "model_alias": CLAUDE_MODEL_ALIAS,
        "seats": seats,
        "concurrent_seats": len(seats),
        "resume_ok": resume_ok,
        "isolation": {
            "working_directory": "stable Data Root seat directory",
            "tools": ["WebSearch", "WebFetch"],
            "code_root_write_tools": False,
        },
        "reasons": reasons,
    }


def _text(value):
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)
