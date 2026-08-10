"""Minimal Claude Code CLI adapter and fail-closed preflight.

Claude performs research; this module only owns process identity, isolation,
timeouts and the structured-output boundary.  It never interprets a market
stance.

It also owns the two process seams every provider adapter shares.
``SubprocessRunner`` hands the child to ``subprocess.run``, so nothing can reach
it once it is away; ``TerminatingRunner`` keeps the ``Popen`` in a
``ProcessRegistry`` instead, which is what lets the T+3:50 sweep stop a provider
process that would otherwise keep burning a subscription after the deadline.
"""

import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path


CLAUDE_CLI_PATH = "/home/leslie/.local/bin/claude"
CLAUDE_MODEL_ALIAS = "opus"
# 一席一顆固定 session UUID，順序同 roster。提供者對調後 news 走 codex、onchain
# 走 claude（Spec R5），所以 news 的條目移除、onchain 拿一顆全新的 UUID：沿用
# news 那顆會把 news 席的 session 歷史接到 onchain 席身上。另兩席的 UUID 不得
# 重新產生，那是它們既有 session 的連續性。
CLAUDE_SEAT_SESSIONS = {
    "onchain": "a8577ee5-8faa-44c3-bb33-38f08ff48299",
    "official-events": "0eed52ad-0c61-462d-a61c-f4b45c9e545f",
    "social-macro": "2cfc5b87-ba9d-4788-9657-d8d79c87dcd3",
}

RESEARCH_TOOLS = "WebSearch,WebFetch"
# ``--tools ""`` 是 CLI 記載的「關閉全部內建工具」語法（claude 2.1.220 --help），
# 已用 /tmp 真實冒煙確認結構化輸出照樣回得來。--disallowedTools 再點名兩個搜尋
# 工具，未來預設值改了也搶不回來。
NO_TOOLS = ""

TERMINATED_AT_DEADLINE = "terminated_at_deadline"
TERMINATE_GRACE_SECONDS = 3
KILL_POLL_SECONDS = 0.05

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
                elapsed_ms=_elapsed_ms(started),
            )
        except subprocess.TimeoutExpired as exc:
            return ProcessOutput(
                returncode=None,
                stdout=_text(exc.stdout),
                stderr=_text(exc.stderr),
                elapsed_ms=_elapsed_ms(started),
                timed_out=True,
            )
        except OSError as exc:
            return ProcessOutput(
                returncode=127,
                stdout="",
                stderr=str(exc),
                elapsed_ms=_elapsed_ms(started),
            )


class ProcessRegistry:
    """Thread-safe map from a worker key to the child process it is running.

    The worker thread tracks and releases; the deadline sweep runs on another
    thread and only calls :meth:`terminate`. Stopping a process is always best
    effort: a provider that already died must never break the T+3:50 sweep.

    A key is *poisoned* the moment it is terminated, and stays poisoned for the
    rest of the run: a terminate landing in the gap between one process ending
    and its retry starting would otherwise signal nothing, and the retry would
    burn the whole timeout after the deadline. A poisoned key therefore stops
    every process tracked afterwards, at once, inside the registry lock. Keys
    must be attempt-scoped for that to be safe — a thread-scoped key would
    poison whichever seat inherits that pooled thread next.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._processes = {}
        self._terminated = set()
        self._poisoned = set()

    def track(self, key, process):
        """Adopt ``process``; a key already terminated stops it on arrival."""
        with self._lock:
            self._processes[key] = process
            if key not in self._poisoned:
                self._terminated.discard(key)
                return process
            self._terminated.add(key)
            _stop(process, TERMINATE_GRACE_SECONDS)
        return process

    def release(self, key, process):
        """Forget ``process`` and report whether this registry terminated it."""
        with self._lock:
            if self._processes.get(key) is process:
                del self._processes[key]
            terminated = key in self._terminated
            self._terminated.discard(key)
        return terminated

    def terminate(self, key, grace_seconds=TERMINATE_GRACE_SECONDS):
        """Poison the key, then report whether a signal reached a live process."""
        with self._lock:
            self._poisoned.add(key)
            process = self._processes.get(key)
            if process is None:
                return False
            self._terminated.add(key)
        return _stop(process, grace_seconds)


class TerminatingRunner:
    """Process seam whose child can be stopped from another thread.

    ``run`` is the ``SubprocessRunner`` interface used by the Claude and Codex
    adapters; ``run_process`` is the ``CompletedProcess`` callable shape the
    Antigravity adapter expects. Both key the registry by the calling worker, so
    one runner serves every seat in the pool.
    """

    def __init__(self, registry=None, key_source=None):
        self.registry = registry or ProcessRegistry()
        self.key_source = key_source or threading.get_ident

    def run(self, args, *, input_text, cwd, timeout_seconds):
        started = time.monotonic()
        try:
            process = self._spawn(args, cwd)
        except OSError as exc:
            return ProcessOutput(
                returncode=127,
                stdout="",
                stderr=str(exc),
                elapsed_ms=_elapsed_ms(started),
            )
        key = self.key_source()
        self.registry.track(key, process)
        try:
            stdout, stderr, timed_out = _communicate(
                process, input_text, timeout_seconds
            )
        finally:
            terminated = self.registry.release(key, process)
        return ProcessOutput(
            returncode=None if timed_out else process.returncode,
            stdout=stdout,
            stderr=_with_deadline_note(stderr, terminated),
            elapsed_ms=_elapsed_ms(started),
            timed_out=timed_out,
        )

    def run_process(self, argv, cwd, timeout):
        """Antigravity's runner callable: a ``CompletedProcess`` or a timeout."""
        output = self.run(argv, input_text="", cwd=cwd, timeout_seconds=timeout)
        if output.timed_out:
            raise subprocess.TimeoutExpired(list(argv), timeout)
        return subprocess.CompletedProcess(
            list(argv), output.returncode, output.stdout, output.stderr
        )

    def _spawn(self, args, cwd):
        return subprocess.Popen(
            list(args),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            text=True,
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
    # 研究階段要搜尋；T+4:00 封存之後的呼叫必須明確關掉這個能力。
    allow_search: bool = True


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
        if _data_root_is_unsafe(self.code_root, self.data_root):
            raise ValueError("Data Root 不得等於或位於 Code Root 內")
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
            allow_search=request.allow_search,
        )

    def _command(self, request, session_id):
        command = [
            self.cli_path,
            "-p",
            "--model",
            CLAUDE_MODEL_ALIAS,
            "--permission-mode",
            "dontAsk",
        ]
        command += _tool_flags(request.allow_search)
        command += [
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
        if not isinstance(structured, dict) or structured.get("seat_id") != request.seat_id:
            return ClaudeAttemptResult(status="invalid_schema", error="structured_output_invalid", **common)
        if request.validator is None:
            return ClaudeAttemptResult(
                status="invalid_schema",
                error="explicit_validator_required",
                **common,
            )
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
    try:
        code_root = Path(code_root).resolve()
        data_root = Path(data_root).resolve()
    except (OSError, RuntimeError):
        return _preflight_report(False, None, [], ["path_resolution_failed"], False)
    reasons = []
    if not code_root.is_dir():
        reasons.append("code_root_unavailable")
    if _data_root_is_unsafe(code_root, data_root):
        reasons.append("data_root_inside_code_root")
    if seats != 3:
        reasons.append("claude_seats_must_equal_3")
    if environ.get("ANTHROPIC_API_KEY"):
        reasons.append("ANTHROPIC_API_KEY_present")
    try:
        if not path_exists(Path(cli_path)):
            reasons.append("claude_cli_missing")
    except OSError:
        reasons.append("claude_cli_path_check_failed")
    if reasons:
        return _preflight_report(False, None, [], reasons, False)

    try:
        data_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return _preflight_report(False, None, [], ["data_root_unavailable"], False)
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
    resume_checkpoint_sha256 = None
    session_root = data_root / "sessions" / "claude"
    try:
        session_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return _preflight_report(
            False,
            version.stdout.strip().splitlines()[0],
            [],
            ["claude_session_directory_unavailable"],
            False,
        )
    try:
        adapter = ClaudeAdapter(
            runner=runner,
            code_root=code_root,
            data_root=data_root,
            cli_path=cli_path,
        )
        requests = []
        search_nonce = "hoya-{:x}".format(time.time_ns())
        checkpoint_markers = {
            seat_id: "hoya-checkpoint-{}".format(secrets.token_hex(8))
            for seat_id in CLAUDE_SEAT_SESSIONS
        }
        for seat_id in CLAUDE_SEAT_SESSIONS:
            attempt_dir = session_root / seat_id
            try:
                attempt_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                reasons.append("{}:session_directory_unavailable".format(seat_id))
                continue
            marker = checkpoint_markers[seat_id]
            requests.append(
                ClaudeAttemptRequest(
                    seat_id=seat_id,
                    attempt_id="{}-preflight-a1".format(seat_id),
                    prompt=(
                        "This is a tool capability test. You MUST invoke WebSearch exactly once "
                        "with the unique query 'Claude Code CLI official docs {} {}'; prior "
                        "knowledge or a previous search is not acceptable. Return seat_id={!r}, "
                        "search_ok=true only after the WebSearch tool returns successfully, and "
                        "the public checkpoint_marker={}. Set message to exactly the marker value "
                        "after '=' (without a label). If you did not invoke WebSearch, return "
                        "search_ok=false but keep that exact marker value in message."
                    ).format(search_nonce, seat_id, seat_id, marker),
                    attempt_dir=attempt_dir,
                    timeout_seconds=45,
                    validator=validate_smoke_output,
                )
            )
        if reasons:
            return _preflight_report(
                False,
                version.stdout.strip().splitlines()[0],
                [],
                reasons,
                False,
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
                        "tool returns. The public checkpoint_marker={}; set message to exactly "
                        "the marker value after '=' (without a label)."
                    ).format(
                        search_nonce,
                        request.seat_id,
                        checkpoint_markers[request.seat_id],
                    ),
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
            elif result.structured_output.get("message") != checkpoint_markers[seat_id]:
                reasons.append("{}:checkpoint_marker_missing".format(seat_id))
            summaries.append(_seat_summary(result))

        first = requests[0]
        expected_marker = checkpoint_markers[first.seat_id]
        if (
            results[first.seat_id].status == "ok"
            and results[first.seat_id].structured_output.get("message") == expected_marker
        ):
            resumed = adapter.run(
                adapter.resumed_request(
                    first,
                    attempt_id="{}-preflight-a2".format(first.seat_id),
                    prompt=(
                        "Resume the same public session. Recover the exact checkpoint_marker "
                        "value from the immediately preceding public history; it is deliberately "
                        "not repeated here. Return seat_id={!r}, search_ok=true, and that exact "
                        "marker value in message, without adding a label or explanation."
                    ).format(first.seat_id),
                    attempt_dir=first.attempt_dir,
                )
            )
            resume_ok = (
                resumed.status == "ok"
                and resumed.structured_output.get("seat_id") == first.seat_id
                and resumed.structured_output.get("message") == expected_marker
                and "opus" in (resumed.actual_model or "")
            )
            resume_checkpoint_sha256 = hashlib.sha256(
                expected_marker.encode("utf-8")
            ).hexdigest()
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
        resume_checkpoint_sha256,
    )


def _tool_flags(allow_search):
    """Hand the seat its tools, or none at all.

    Search is a capability, not a request: after the T+4:00 seal the debate and
    the report may only read sealed evidence, so the sealed call is given no
    tools instead of being asked nicely in the prompt to stay offline.
    """
    if allow_search:
        return ["--tools", RESEARCH_TOOLS, "--allowedTools", RESEARCH_TOOLS]
    return ["--tools", NO_TOOLS, "--disallowedTools", RESEARCH_TOOLS]


def mask_session_id(session_id):
    return "{}-…-{}".format(session_id[:8], session_id[-4:])


def validate_smoke_output(value):
    """Validate only the preflight envelope; research requests supply their own validator."""
    if not isinstance(value.get("search_ok"), bool):
        raise ValueError("search_ok must be boolean")
    if not isinstance(value.get("message"), str) or not value["message"].strip():
        raise ValueError("message must be non-empty")
    return value


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


def _preflight_report(
    ready,
    version,
    seats,
    reasons,
    resume_ok,
    resume_checkpoint_sha256=None,
):
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
        "resume_checkpoint_sha256": resume_checkpoint_sha256,
        "isolation": {
            "working_directory": "stable Data Root seat directory",
            "tools": RESEARCH_TOOLS.split(","),
            "code_root_write_tools": False,
        },
        "reasons": reasons,
    }


def _communicate(process, input_text, timeout_seconds):
    """Drain the child, killing it on timeout so no process outlives the seat."""
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout_seconds)
        return _text(stdout), _text(stderr), False
    except subprocess.TimeoutExpired:
        _signal(process, "kill")
        stdout, stderr = process.communicate()
        return _text(stdout), _text(stderr), True


def _with_deadline_note(stderr, terminated):
    """截止時被終止的呼叫要在現場留下記號，事後不必重跑就知道原因。"""
    if not terminated:
        return stderr
    if not stderr.strip():
        return TERMINATED_AT_DEADLINE
    return "{}：{}".format(TERMINATED_AT_DEADLINE, stderr)


def _stop(process, grace_seconds):
    """Signal one process; report whether the signal was actually delivered."""
    if not _signal(process, "terminate"):
        return False
    _escalate_to_kill(process, grace_seconds)
    return True


def _escalate_to_kill(process, grace_seconds):
    """寬限期在背景等待：截止收尾這條執行緒不能被卡住。"""
    threading.Thread(
        target=_kill_after_grace,
        args=(process, grace_seconds),
        name="hoya-terminate",
        daemon=True,
    ).start()


def _kill_after_grace(process, grace_seconds):
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if _poll(process) is not None:
            return
        time.sleep(KILL_POLL_SECONDS)
    _signal(process, "kill")


def _signal(process, name):
    try:
        getattr(process, name)()
    except Exception:  # 停止外部進程永遠是 best effort
        return False
    return True


def _poll(process):
    try:
        return process.poll()
    except Exception:  # 查不到狀態就當它已結束，不再升級
        return 0


def _elapsed_ms(started):
    return int((time.monotonic() - started) * 1000)


def _text(value):
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)


def _data_root_is_unsafe(code_root, data_root):
    return data_root == code_root or data_root.is_relative_to(code_root)
