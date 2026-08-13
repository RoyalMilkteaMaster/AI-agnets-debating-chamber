"""Minimal Claude Code CLI adapter and fail-closed preflight.

Claude performs research; this module only owns process identity, isolation,
timeouts and the structured-output boundary.  It never interprets a market
stance.

It also owns the two process seams every provider adapter shares.
``SubprocessRunner`` hands the child to ``subprocess.run``, so nothing can reach
it once it is away; ``TerminatingRunner`` spawns the child in its own POSIX
session and keeps that whole process group in a ``ProcessRegistry`` instead,
which is what lets the acceptance sweep reclaim a provider tree that would
otherwise keep burning a subscription after the deadline.
"""

import hashlib
import json
import os
import secrets
import signal
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .provider_cli import PROVIDER_CLAUDE, provider_cli_argv0


# WSL 的 ``PATH`` 說了算（ADR 0009）：這裡不得凍結任何一位開發者家目錄下的路徑。
CLAUDE_CLI_PATH = provider_cli_argv0(PROVIDER_CLAUDE)
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
# Ticket 03 起，一個 Codex 或 Antigravity 席也可能以 backup 身分打到 Claude。
# 那條 lane 沒有歷史 session 可以延續，但仍然需要一顆穩定且互不相同的 UUID：
# 同一席重跑要落回同一個 session，不同席永遠不得共用。由 seat_id 導出，所以不
# 必為每個 backup 手寫常數，也不可能誤用到上面三席的歷史 session。這張表本身
# 維持三席不動：賽前 preflight 檢的就是那三個固定 Claude 席。
CLAUDE_BACKUP_SESSION_PREFIX = "urn:hoya:claude-backup-session:"

RESEARCH_TOOLS = "WebSearch,WebFetch"
# ``--tools ""`` 是 CLI 記載的「關閉全部內建工具」語法（claude 2.1.220 --help），
# 已用 /tmp 真實冒煙確認結構化輸出照樣回得來。--disallowedTools 再點名兩個搜尋
# 工具，未來預設值改了也搶不回來。
NO_TOOLS = ""

TERMINATED_AT_DEADLINE = "terminated_at_deadline"
TERMINATE_GRACE_SECONDS = 3
KILL_POLL_SECONDS = 0.05
# ``SIGKILL`` 擋不掉，所以確認整組消失只需要收屍的時間，不需要再給一次寬限。
KILL_CONFIRM_SECONDS = 0.5
# 整組證明不了已回收時，還握著管道的子孫會讓 drain 永遠等不到 EOF：只給有界時間。
UNRECLAIMED_DRAIN_SECONDS = 1

# 一次 invocation 只會有這三種 process 終局，別的都不算證據。
PROCESS_GROUP_RECLAIMED = "process_group_reclaimed"
PROCESS_GROUP_NOT_RUNNING = "process_group_not_running"
PROCESS_TREE_TERMINATION_FAILED = "process_tree_termination_failed"

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
    # 這次 invocation 的 process group 終局，由 registry 提供；``None`` 表示這個
    # 執行縫不管理 process group。呼叫端讀機器值，不從訊息字串反推。
    process_outcome: str | None = None


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


class _Invocation:
    """One generation of one attempt key: its group identity and its verdict."""

    __slots__ = ("process", "pgid", "outcome", "at_deadline")

    def __init__(self, process):
        self.process = process
        self.pgid = _group_id(process)
        self.outcome = None
        self.at_deadline = False


class ProcessRegistry:
    """Thread-safe map from an attempt key to the process group running for it.

    The worker thread tracks and releases; the deadline sweep runs on another
    thread and only calls :meth:`terminate`. Reclaiming is always best effort in
    the sense that it never raises, but it is never optimistic: the only proof
    that a tree is gone is that its whole POSIX group has stopped existing, so a
    root that exited first says nothing about its surviving descendants. What
    cannot be proven reclaimed settles as ``process_tree_termination_failed``.

    A key is *poisoned* the moment it is terminated, and stays poisoned for the
    rest of the run: a terminate landing in the gap between one process ending
    and its retry starting would otherwise signal nothing, and the retry would
    burn the whole timeout after the deadline. A poisoned key therefore reclaims
    every group tracked afterwards, on arrival. Keys must be attempt-scoped for
    that to be safe — a thread-scoped key would poison whichever seat inherits
    that pooled thread next.

    Each :meth:`track` of a key opens a new *generation*, so the same-session
    Claude resume gets its own verdict instead of inheriting the one its
    predecessor settled. Every generation settles exactly once: track, release,
    terminate and the poisoned-track reclaim all take that key's reclaim lock
    and re-read the settled outcome after they get it, which is what keeps a
    late cancel from rewriting a clean finish as a failure. The lock is per key,
    so one seat's grace period never delays another seat's reclaim.
    """

    def __init__(self, killpg=None):
        # killpg 是唯一的 OS 邊界；測試以可控的 fake group 表取代它。
        self._killpg = killpg or os.killpg
        # 這把鎖只保護下面幾張表，送訊號與等寬限期時一律不持有它。
        self._lock = threading.Lock()
        self._reclaim_locks = {}
        self._invocations = {}  # key -> [_Invocation]，索引 +1 就是 generation
        self._poisoned = set()

    def track(self, key, process, grace_seconds=TERMINATE_GRACE_SECONDS):
        """Adopt ``process``'s group as a new generation of ``key``."""
        with self._reclaim_lock(key):
            with self._lock:
                generations = self._invocations.setdefault(key, [])
                generations.append(_Invocation(process))
                generation = len(generations)
                poisoned = key in self._poisoned
            if poisoned:
                self._settle(key, generation, grace_seconds, True)
        return process

    def release(self, key, process):
        """Settle the generation running ``process``; report a deadline stop.

        「乾淨收工」也要確認過才算數：root 收工卻留下子孫的話，這裡就是最後一個
        能回收它們的地方。
        """
        with self._reclaim_lock(key):
            generation = self._generation_of(key, process)
            if generation is None:
                return False
            self._settle(key, generation, TERMINATE_GRACE_SECONDS, False)
            with self._lock:
                return self._invocations[key][generation - 1].at_deadline

    def terminate(self, key, grace_seconds=TERMINATE_GRACE_SECONDS):
        """Poison the key, then report whether a live group was reclaimed."""
        with self._lock:
            self._poisoned.add(key)
        return self._reclaim(key, grace_seconds, True) == PROCESS_GROUP_RECLAIMED

    def reclaim(self, key, grace_seconds=TERMINATE_GRACE_SECONDS):
        """Reclaim this generation's group without poisoning the key."""
        return self._reclaim(key, grace_seconds, False)

    def outcome(self, key, generation=None):
        """This invocation's single terminal outcome; ``None`` until it settles."""
        with self._lock:
            generations = self._invocations.get(key) or []
            if generation is None:
                generation = len(generations)
            if not 1 <= generation <= len(generations):
                return None
            return generations[generation - 1].outcome

    def _reclaim(self, key, grace_seconds, at_deadline):
        with self._reclaim_lock(key):
            with self._lock:
                generations = self._invocations.get(key)
                if not generations:
                    return PROCESS_GROUP_NOT_RUNNING
                generation = len(generations)
            return self._settle(key, generation, grace_seconds, at_deadline)

    def _settle(self, key, generation, grace_seconds, at_deadline):
        """Give one generation its single verdict; caller holds the reclaim lock."""
        with self._lock:
            invocation = self._invocations[key][generation - 1]
            if invocation.outcome is not None:
                return invocation.outcome  # 拿到鎖後重讀：這一代早就有終局了
            process, pgid = invocation.process, invocation.pgid
        outcome = _reclaim_group(self._killpg, pgid, process, grace_seconds)
        with self._lock:
            invocation.outcome = outcome
            invocation.at_deadline = at_deadline
        return outcome

    def _reclaim_lock(self, key):
        """One reclaim lock per key: same key serialises, other keys never wait."""
        with self._lock:
            return self._reclaim_locks.setdefault(key, threading.Lock())

    def _generation_of(self, key, process):
        with self._lock:
            generations = self._invocations.get(key) or []
            for index in reversed(range(len(generations))):
                if generations[index].process is process:
                    return index + 1
        return None


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
                process, input_text, timeout_seconds, lambda: self.registry.reclaim(key)
            )
        finally:
            terminated = self.registry.release(key, process)
        return ProcessOutput(
            returncode=None if timed_out else process.returncode,
            stdout=stdout,
            stderr=_with_deadline_note(stderr, terminated),
            elapsed_ms=_elapsed_ms(started),
            timed_out=timed_out,
            process_outcome=self.registry.outcome(key),
        )

    def run_process(self, argv, cwd, timeout):
        """Antigravity's runner callable: a ``CompletedProcess`` or a timeout."""
        output = self.run(argv, input_text="", cwd=cwd, timeout_seconds=timeout)
        if output.timed_out:
            expired = subprocess.TimeoutExpired(list(argv), timeout)
            # 逾時也可能同時回收失敗，那個終局比「只是逾時」強：附在例外上，
            # 讀取端才不必從訊息反推。
            expired.process_outcome = output.process_outcome
            raise expired
        completed = subprocess.CompletedProcess(
            list(argv), output.returncode, output.stdout, output.stderr
        )
        # ``CompletedProcess`` 沒有這個欄位，掛上去是把 group 終局帶過邊界最小的
        # 相容做法：注入的 runner 只回標準 CompletedProcess 時，讀取端取到 None。
        completed.process_outcome = output.process_outcome
        return completed

    def _spawn(self, args, cwd):
        return subprocess.Popen(
            list(args),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            text=True,
            # 每次 invocation 自成一個 POSIX session：root 的 pid 就是整棵樹的
            # process group id，截止收尾才有一個可回收的身分可以保存。
            start_new_session=True,
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
    # 研究階段要搜尋；證據封存之後的呼叫必須明確關掉這個能力。
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


def claude_session_id(seat_id):
    """The fixed Claude session UUID one seat's invocations belong to.

    The three roster Claude seats keep the UUIDs their history is already in.
    Any other seat is here as a Ticket 03 backup, and gets its own derived —
    stable, seat-specific, never shared — session instead.
    """
    if not isinstance(seat_id, str) or not seat_id.strip():
        raise ValueError("seat_id 必須為非空字串：{!r}".format(seat_id))
    fixed = CLAUDE_SEAT_SESSIONS.get(seat_id)
    if fixed is not None:
        return fixed
    return str(uuid.uuid5(uuid.NAMESPACE_URL, CLAUDE_BACKUP_SESSION_PREFIX + seat_id))


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
        if output.process_outcome == PROCESS_TREE_TERMINATION_FAILED:
            # 整組證明不了回收就永遠不可採用：連 parse 與 validator 都不跑，
            # 一份看起來乾淨的 JSON 也不能讓這個 attempt 變成有效輸出。
            return ClaudeAttemptResult(
                status=PROCESS_TREE_TERMINATION_FAILED,
                error=PROCESS_TREE_TERMINATION_FAILED,
                **common,
            )
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
        return claude_session_id(seat_id)

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
    # 正式 preflight 和研究呼叫用同一個 group-safe 執行縫：真的 CLI 也要在自己的
    # POSIX session 裡啟動並可整組回收，否則它留下的子孫沒有人收得掉。
    runner = runner or TerminatingRunner()
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

    Search is a capability, not a request: after the evidence seal the debate and
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


def _communicate(process, input_text, timeout_seconds, reclaim):
    """Drain the child, reclaiming its group on timeout so nothing outlives the seat."""
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout_seconds)
        return _text(stdout), _text(stderr), False
    except subprocess.TimeoutExpired:
        # 整組先回收：只殺 root 的話，還握著 stdout 的孫代會讓下面的 drain
        # 永遠等不到 EOF。
        stdout, stderr = _drain(process, reclaim())
        return stdout, stderr, True


def _drain(process, outcome):
    """Read what is left of the child's pipes after its group was reclaimed.

    整組已證明回收時沒有人再寫入，等 EOF 是有限的；證明不了回收就不能無界等待，
    只收有界時間內拿得到的內容，讓逾時仍然是有界的終止。
    """
    if outcome != PROCESS_TREE_TERMINATION_FAILED:
        stdout, stderr = process.communicate()
        return _text(stdout), _text(stderr)
    try:
        stdout, stderr = process.communicate(timeout=UNRECLAIMED_DRAIN_SECONDS)
    except subprocess.TimeoutExpired as exc:
        return _text(exc.stdout), _text(exc.stderr)
    return _text(stdout), _text(stderr)


def _with_deadline_note(stderr, terminated):
    """截止時被終止的呼叫要在現場留下記號，事後不必重跑就知道原因。"""
    if not terminated:
        return stderr
    if not stderr.strip():
        return TERMINATED_AT_DEADLINE
    return "{}：{}".format(TERMINATED_AT_DEADLINE, stderr)


def _group_id(process):
    """The reclaimable identity saved at spawn time.

    ``start_new_session=True`` makes the root its own session and group leader,
    so its pid *is* the group id — and it stays valid after the root itself has
    been reaped, as long as one descendant is still in the group.
    """
    pid = getattr(process, "pid", None)
    return pid if isinstance(pid, int) and pid > 0 else None


def _reclaim_group(killpg, pgid, process, grace_seconds):
    """SIGTERM the whole group, keep the grace, then SIGKILL whatever is left."""
    if pgid is None or _group_is_empty(killpg, pgid, process):
        return PROCESS_GROUP_NOT_RUNNING
    _deliver(killpg, pgid, signal.SIGTERM)
    if _wait_for_empty_group(killpg, pgid, process, grace_seconds):
        return PROCESS_GROUP_RECLAIMED
    _deliver(killpg, pgid, signal.SIGKILL)
    if _wait_for_empty_group(
        killpg, pgid, process, min(grace_seconds, KILL_CONFIRM_SECONDS)
    ):
        return PROCESS_GROUP_RECLAIMED
    return PROCESS_TREE_TERMINATION_FAILED


def _group_is_empty(killpg, pgid, process):
    """整組已回收的唯一證明：signal 0 找不到這個 process group。"""
    # 先收屍：還沒被 wait 的 root zombie 會讓整組看起來還在，但那不是證明。
    _poll(process)
    try:
        killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False  # 訊號送不進去就什麼都證明不了，一律當成還沒回收
    return False


def _wait_for_empty_group(killpg, pgid, process, seconds):
    deadline = time.monotonic() + seconds
    while True:
        if _group_is_empty(killpg, pgid, process):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(KILL_POLL_SECONDS)


def _deliver(killpg, pgid, number):
    try:
        killpg(pgid, number)
    except OSError:  # 送訊號永遠是 best effort；證明留給 _group_is_empty
        pass


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
