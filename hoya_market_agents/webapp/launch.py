"""Starting a run: the one thing this web app does that is not reading.

Everything else under :mod:`hoya_market_agents.webapp` opens run artifacts for
reading. This module starts a separate ``launch`` process and then lets go of
it: the child owns the run directory and every byte written into it, and the web
app goes back to watching ``events.jsonl`` like any other reader. Killing the
browser, or the server, does not reach into a run that is already going.

The lock here is a *front-end* lock and its limits are the point:

* It refuses a second launch **submitted through this web app process** while a
  launch this web app process started is still running.
* It refuses nothing else. A run started from the CLI, or from a second copy of
  this server, is neither visible to it nor blocked by it.

It is not a mutual exclusion guarantee, and nothing further down provides one
either. A run id is ``<UTC start>-<asset slug>-<random token>``, so two launches
started at the same moment get *different* ids, *different* run directories and
*different* inboxes, and both proceed to completion. The write-once rules in
``run_store`` and ``codex_inbox`` stop a run from overwriting **itself**; they
have nothing to say about a second run.

So what this lock buys is one thing: pressing the button twice is answered with
a sentence instead of quietly starting a second set of seven seats against the
same quota. Anyone who needs a real guarantee that only one run exists at a time
has to look outside this module — and there is nothing here that claims to be it.

**What a submission is, and who decides what the run is about.** The ask bar is
a menu: the market comes from a ``<select>`` and the targets from that market's
own box (:func:`read_request`). Both are handed to the run outright, so the
question's prose decides neither — it is still quoted verbatim into the run,
because prompts and artifacts have to show what the user asked, but nothing
*infers* the subject from it any more. The seam that makes this possible already
exists: :func:`~hoya_market_agents.question.inspect_question` takes ``assets``
and ``asset_class`` from its caller and does not consult the text for either.

That seam is reachable in-process only — ``cli.py``'s ``launch`` subcommand has
no flag for it — and calling it in *this* process is not an option: a run is
owned by a child that outlives the browser, the stream and this server
(architecture §4.0.1). So the child this module spawns is **this module's own**
:func:`main`: the same interpreter, one argument list that names the market and
every target, and :func:`run_launch` called inside the child with both stated.
The process boundary is exactly where it was; what changed is that the child is
handed an answer instead of a sentence to guess from.
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .. import launcher as launcher_module
from ..run_store import resolve_run_dir
from ..question import (
    UnknownAssetError,
    UnsupportedQuestionError,
    inspect_question,
)

LAUNCH_LOG_NAME = "launch.log"

# The child a launch is: the same interpreter this server is running under, told
# to call this module's :func:`main`, so the child cannot end up on a different
# Python than the one that was tested. It is this module rather than
# ``hoya_market_agents launch`` because the stated-target seam has no command line
# flag — see the module docstring.
#
# **Why ``-c`` and not ``-m``.** Importing this package imports *this module* on
# the way to ``server``, so ``-m hoya_market_agents.webapp.launch`` finds it
# already in ``sys.modules`` and executes it a second time as ``__main__`` —
# ``runpy`` says exactly that, in a ``RuntimeWarning`` about unpredictable
# behaviour that would then head every launch log this web app writes. This line
# imports the module once and calls one function in it. It is a constant, not
# anything a request can influence: everything a person typed arrives as separate
# arguments and is read by :mod:`argparse`.
CHILD_BOOTSTRAP = (
    "import sys; from hoya_market_agents.webapp.launch import main; sys.exit(main())"
)

# The ask bar's field names. They are here, beside the reader that consumes them,
# because the form and the reader have to agree and only one of the two can own
# the spelling; :mod:`~hoya_market_agents.webapp.pages` renders whatever these
# say. Each market gets a box of its own — the page shows one at a time and the
# server reads the one the chosen market names, so which box is *visible* is a
# question nobody has to ask.
QUESTION_FIELD = "question"
ASSET_CLASS_FIELD = "asset_class"
TARGET_FIELD_PREFIX = "asset_"

# The child's own argument names. ``--asset`` is repeated once per target, so the
# order the user typed them in is the order the run id is built from.
QUESTION_ARGUMENT = "question"
ASSET_CLASS_ARGUMENT = "asset-class"
DATA_ROOT_ARGUMENT = "data-root"
TARGET_ARGUMENT = "asset"
LAUNCH_TOKEN_ARGUMENT = "launch-token"
HANDSHAKE_ARGUMENT = "launch-handshake"
HANDSHAKE_DIR_NAME = "launch-handshakes"

# What separates two targets in one box: a comma of either width, the ideographic
# comma, and plain whitespace. Every one of them is a character no identifier may
# contain, which is why splitting on them cannot break a target in half.
_TARGET_SEPARATORS = re.compile(r"[\s,，、]+")

PROBLEM_BLANK = "blank_question"
PROBLEM_NO_ASSET_CLASS = "no_asset_class"
PROBLEM_BLANK_TARGET = "blank_target"
PROBLEM_TARGET_REFUSED = "target_refused"

# What a reader is told when :class:`LaunchLock` refuses. It lives here rather
# than in the route because the sentence describes this lock's scope, and the
# scope is this module's to state.
BUSY_MESSAGE = (
    "本頁啟動的上一個 run 還在進行中，同一時間只允許一個。"
    "等它結束後再送出，或到「歷史與命中率」看目前進度。"
)


class LaunchLock:
    """At most one launch started by this web app process at a time.

    That sentence is the whole claim: it is scoped to launches this object
    started, and a run begun any other way is outside it (see the module
    docstring).

    ``busy`` is answered by asking the child process whether it has exited, not
    by a flag someone has to remember to clear, so a launch that finished — or
    crashed — releases the lock without anything else happening.
    """

    def __init__(self):
        self._guard = threading.Lock()
        self._process = None
        self._question = None
        self._stopping = False
        self._launches = {}

    def busy(self):
        """Is a launch this web app started still running?"""
        with self._guard:
            return self._running()

    def _running(self):
        return self._process is not None and self._process.poll() is None

    def claim(self, start):
        """Run ``start`` and keep its process, unless the lock is taken.

        ``start`` is called while the lock is held, so two requests arriving at
        once cannot both find the lock free and both spawn. It returns the
        process object; ``claim`` returns it too, or ``None`` when the lock was
        already taken and **nothing was started** — which is the part that
        matters, because a refusal that had already spawned would be no refusal.

        Two things take the lock: a launch that is still running, and a stop this
        server has already authorised. The second is why :meth:`reserve_stop`
        exists — see it for what that window is.
        """
        with self._guard:
            if self._stopping or self._running():
                return None
            process = start()
            self._process = process
            return process

    def claim_token(self, token, handshake_path, start):
        """Atomically reserve ``token`` and start exactly one child."""
        with self._guard:
            if self._stopping or self._running():
                return None
            self._discard_handshake(self._process_state())
            process = start()
            self._process = process
            self._launches[token] = {
                "process": process,
                "handshake_path": Path(handshake_path),
                "run_id": None,
                "failure": None,
            }
            return process

    def launch_status(self, token, data_root):
        """Return the status bound to ``token``; never infer a run from disk."""
        with self._guard:
            state = self._launches.get(token)
            if state is None:
                return None
            if state["run_id"] is not None:
                return {"status": "launched", "run_id": state["run_id"]}
            if state["failure"] is not None:
                return {"status": "failed", "reason": state["failure"]}
            handshake = _read_token_handshake(state["handshake_path"], token, data_root)
            if handshake is not None:
                state["run_id"] = handshake["run_id"]
                self._discard_handshake(state)
                return {"status": "launched", "run_id": state["run_id"]}
            code = state["process"].poll()
            if code is None:
                return {"status": "pending"}
            state["failure"] = "啟動程序未建立可驗證的 run。"
            self._discard_handshake(state)
            return {"status": "failed", "reason": state["failure"]}

    def _process_state(self):
        for state in self._launches.values():
            if state.get("process") is self._process:
                return state
        return None

    @staticmethod
    def _discard_handshake(state):
        if not state:
            return
        try:
            state["handshake_path"].unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def reserve_stop(self, allow_active_run):
        """Decide a stop and take the lock for it, in one step under one guard.

        A stop is not instantaneous: ``POST /shutdown`` authorises it, writes its
        reply, and only then asks the serving loop to end. Between the decision
        and the loop actually returning, this process is still accepting
        requests — so a ``POST /launch`` can arrive, find nothing running, and
        start an analysis that this server has already agreed to die on. Nobody
        was ever asked about interrupting *that* run, because at the moment the
        question was asked it did not exist.

        Reading the state and acting on it therefore cannot be two steps. This is
        one: it re-reads whether a run is going, refuses unless that is allowed,
        and on success marks the lock as stopping before releasing the guard.
        From that point :meth:`claim` starts nothing, so there is no window left
        for a launch to appear in.

        ``allow_active_run`` is the consent the caller carries: ``True`` only
        when a person has said, in so many words, to interrupt a run.

        Returns whether the stop is authorised. A caller that gets ``True`` and
        then cannot stop must hand the lock back with :meth:`release_stop`,
        otherwise this server refuses every future launch while still serving.

        ``self._running()`` rather than ``self.busy()`` on purpose: ``busy``
        takes this same guard, and a method that called it from in here would
        deadlock the moment a run existed.
        """
        with self._guard:
            if self._running() and not allow_active_run:
                return False
            self._stopping = True
            return True

    def release_stop(self):
        """Undo a reservation whose stop did not happen, so launching resumes."""
        with self._guard:
            self._stopping = False

    def note_question(self, question):
        """Remember what the running launch was asked, for the page to show."""
        with self._guard:
            self._question = question

    def state(self):
        """Return what the page shows about the launch this server started.

        ``returncode`` is ``None`` while it runs and the child's exit status
        once it has stopped; ``question`` is what was submitted. Both are
        ``None`` when this server has not started one.
        """
        with self._guard:
            if self._process is None:
                return {"running": False, "started": False, "returncode": None,
                        "question": None}
            code = self._process.poll()
            return {
                "running": code is None,
                "started": True,
                "returncode": code,
                "question": self._question,
            }


@dataclass(frozen=True)
class LaunchRequest:
    """One submission of the ask bar, as the menu stated it.

    ``asset_class`` is ``""`` when nothing was chosen — a real state the form can
    be in and the one :func:`launch_problem` refuses, not a missing value to
    guess at. ``targets`` is what was typed in that market's box, split but not
    yet normalised: normalising is
    :func:`~hoya_market_agents.question.normalize_asset`'s job and it happens
    behind the intake seam, so this object never holds a spelling the run does
    not.
    """

    question: str = ""
    asset_class: str = ""
    targets: tuple = field(default_factory=tuple)


def target_field(asset_class):
    """The form field one market's targets are typed into."""
    return TARGET_FIELD_PREFIX + asset_class


def read_request(form):
    """Return the :class:`LaunchRequest` a submitted ask bar describes.

    ``form`` is shaped the way :func:`urllib.parse.parse_qs` returns one. The
    market is read first because it names the box the targets come from: the page
    carries a box per market and a browser submits all of them, so reading "the
    one that was filled in" would let a stale box from another market describe
    the run. Only the chosen market's box is read at all.
    """
    question = _typed(form, QUESTION_FIELD)
    asset_class = _typed(form, ASSET_CLASS_FIELD)
    box = _typed(form, target_field(asset_class)) if asset_class else ""
    return LaunchRequest(question, asset_class, _split_targets(box))


def _typed(form, name):
    """The first value submitted under ``name``, trimmed; ``""`` when absent."""
    values = form.get(name) or []
    return values[0].strip() if values and isinstance(values[0], str) else ""


def _split_targets(box):
    """The targets one box holds, in the order they were typed.

    A box takes more than one because the seam does — a two-target comparison is
    a run this product supports — and the separators are the ones the hint
    offers.
    """
    return tuple(part for part in _TARGET_SEPARATORS.split(box) if part)


def launch_problem(data_root, request):
    """Return ``(problem id, sentence)`` for why a launch cannot be asked for.

    Four things are checked and the order is the answer to "who is entitled to
    say this": a question was typed, a market was chosen, the intake authority
    accepts what was stated, the chosen market's box names at least one target,
    accept. Provider readiness is deliberately not a webapp launch gate.

    The intake sentence is quoted rather than written here: the form must refuse
    in the same words the run would have, or the reader is being told a second
    story about one rule. ``(None, None)`` means nothing refuses it, which is not a promise
    that the run will succeed; it is a promise that the *stated* answer is one
    the run would accept.

    **Why the intake is asked before the empty box is mentioned.** A market that
    no authority declares is the intake's to refuse, and it can only arrive from
    a hand-made ``POST`` — where the box is empty too. Asking about the box first
    would answer that request with "type a target", which is advice about the
    wrong field. An empty box is not a question the intake has an opinion on
    (stating no targets is legal), so nothing is lost by asking it second.

    Emptiness is measured on the trimmed text here rather than trusted to have
    been trimmed by whoever built the request: a page of spaces is a blank
    question however it arrived, and this function is the one that says so.
    """
    if not request.question.strip():
        return PROBLEM_BLANK, "請先輸入要分析的題目。"
    if not request.asset_class.strip():
        return PROBLEM_NO_ASSET_CLASS, "請先從選單選擇資產類別，再填標的。"
    refusal = _intake_refusal(request)
    if refusal is not None:
        return PROBLEM_TARGET_REFUSED, refusal
    if not request.targets:
        return PROBLEM_BLANK_TARGET, "請先輸入或從建議清單選擇要分析的標的。"
    return None, None


def _intake_refusal(request):
    """The intake authority's own sentence about this submission, or ``None``.

    Asked of :func:`~hoya_market_agents.question.inspect_question` with **both**
    answers stated, which is the same call the child makes: the market and the
    targets are the caller's, and the text is not consulted for either. So this
    is not the web app reading a wording — it is the web app asking the one
    module that decides whether a stated target is spelled like a target and a
    stated market is a market that exists.

    Asking here is what moves the refusal to the form. Without it the submission
    would be accepted, a process would start, and the same sentence would land in
    ``launch.log``, which nobody has open.
    """
    try:
        inspect_question(
            request.question,
            assets=list(request.targets),
            asset_class=request.asset_class,
        )
    except (UnknownAssetError, UnsupportedQuestionError) as exc:
        return str(exc)
    return None


def launch_log_path(data_root):
    """Where a launch this web app started writes what it printed."""
    return Path(data_root) / "logs" / LAUNCH_LOG_NAME


def launch_token():
    return secrets.token_urlsafe(24)


def handshake_path(data_root, token):
    return Path(data_root) / "logs" / HANDSHAKE_DIR_NAME / (token + ".json")


def start_launch(data_root, request, token=None, handshake=None, spawn=None):
    """Start one launch in its own process and return it.

    ``spawn`` is the seam: it is handed the argument list and the keyword
    arguments :class:`subprocess.Popen` would get, and a test passes one that
    records instead of forking. The child is put in its own session so closing
    the terminal that started the web app does not signal a run that is halfway
    through, and its output goes to the Data Root's own log rather than to this
    server's console, which nobody is watching.
    """
    spawn = spawn or subprocess.Popen
    data_root = Path(data_root)
    log_path = launch_log_path(data_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        return spawn(
            child_command(data_root, request, token=token, handshake=handshake),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def child_command(data_root, request, token=None, handshake=None):
    """The argument list the child is started with.

    Every value is written as ``--name=value`` rather than as two words, and that
    is not a style choice: a question is free text a person typed, and one
    starting with ``-`` handed to :mod:`argparse` as a separate word is read as an
    option and refused. Joined with ``=`` there is nothing to mistake it for.
    """
    flags = [
        _flag(QUESTION_ARGUMENT, request.question),
        _flag(ASSET_CLASS_ARGUMENT, request.asset_class),
        _flag(DATA_ROOT_ARGUMENT, str(data_root)),
    ] + [_flag(TARGET_ARGUMENT, target) for target in request.targets]
    if token is not None:
        flags.append(_flag(LAUNCH_TOKEN_ARGUMENT, token))
    if handshake is not None:
        flags.append(_flag(HANDSHAKE_ARGUMENT, str(handshake)))
    return [sys.executable, "-c", CHILD_BOOTSTRAP] + flags


def _flag(name, value):
    return "--{}={}".format(name, value)


def main(argv=None, launch=None):
    """Run one launch in **this** process; the child's entry point.

    Called by :data:`CHILD_BOOTSTRAP` in the child, and by a test with a
    ``launch`` of its own. ``argv`` defaults to this process's own arguments, so
    the child passes nothing.

    ``launch`` is the seam a test replaces, and what it is handed is the whole
    point of this ticket: ``assets`` and ``asset_class`` as the menu stated them.
    They are passed as a list and a string and never as ``None`` — ``None`` is
    how a caller says "read the wording", and a submission from a menu never
    means that.
    """
    launch = launch or webapp_run_launch
    arguments = _child_parser().parse_args(argv)
    out = None
    if arguments.launch_token and arguments.launch_handshake:
        out = TokenHandshakeWriter(
            sys.stdout, arguments.launch_token, Path(arguments.launch_handshake)
        )
    return launch(
        arguments.question,
        Path(arguments.data_root),
        assets=list(arguments.asset),
        asset_class=arguments.asset_class,
        out=out,
    )


def webapp_run_launch(question, data_root, **options):
    """Run the generic launcher with READY disabled only for this webapp child.

    The generic CLI retains its fail-closed READY contract. This process is the
    webapp-specific adapter approved by Ticket 06: provider availability is
    reported by each attempt after the run exists, so an absent certificate
    must not prevent the token-bound LAUNCHED handshake.

    ``run_launch`` has no public certificate-injection seam. The narrow adapter
    therefore replaces its loader for this synchronous call and restores it in
    ``finally``. The child executes one launch on one thread; the replacement is
    never present in the resident web server.
    """
    original_loader = launcher_module._load_ready_certificate
    launcher_module._load_ready_certificate = lambda _root: {}
    try:
        return launcher_module.run_launch(question, data_root, **options)
    finally:
        launcher_module._load_ready_certificate = original_loader


def _child_parser():
    parser = argparse.ArgumentParser(
        prog="webapp launch child",
        description="由 webapp 發問區啟動的一次 run：資產類別與標的由呼叫端指定。",
    )
    parser.add_argument("--" + QUESTION_ARGUMENT, required=True, help="使用者輸入的題目原文")
    parser.add_argument(
        "--" + ASSET_CLASS_ARGUMENT, required=True, help="選單選定的資產類別"
    )
    parser.add_argument("--" + DATA_ROOT_ARGUMENT, required=True, help="Data Root 路徑")
    parser.add_argument(
        "--" + TARGET_ARGUMENT,
        action="append",
        default=[],
        help="選單選定的分析標的；可重複，順序即 run id 的順序",
    )
    parser.add_argument("--" + LAUNCH_TOKEN_ARGUMENT)
    parser.add_argument("--" + HANDSHAKE_ARGUMENT)
    return parser


class TokenHandshakeWriter:
    """Mirror launcher output and atomically publish its first LAUNCHED line."""

    def __init__(self, stream, token, path):
        self.stream = stream
        self.token = token
        self.path = Path(path)
        self.buffer = ""
        self.published = False

    def write(self, text):
        self.stream.write(text)
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self._publish(line)
        return len(text)

    def flush(self):
        self.stream.flush()

    def _publish(self, line):
        if self.published:
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict) or payload.get("status") != "LAUNCHED":
            return
        payload["launch_token"] = self.token
        _atomic_write(self.path, payload)
        self.published = True


def _atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_token_handshake(path, token, data_root):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("status") != "LAUNCHED":
        return None
    if payload.get("launch_token") != token:
        return None
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return None
    run_dir = resolve_run_dir(data_root, run_id)
    if run_dir is None or not run_dir.is_dir():
        return None
    recorded = payload.get("run_dir")
    if not isinstance(recorded, str) or Path(recorded).resolve() != run_dir.resolve():
        return None
    return payload
