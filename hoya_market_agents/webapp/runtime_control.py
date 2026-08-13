"""The one consumer of ``GET /health``: who owns 127.0.0.1:8765 right now.

Every public entry point of this product — ``START-HERE.sh``, ``STOP-HERE.sh``
and the two Windows shortcuts behind them — asks this module and nothing else.
That is the whole reason it exists: the ownership contract is a JSON document,
and a JSON document parsed in three languages is three answers waiting to
disagree. Bash reads the ``key=value`` lines :func:`main` prints; PowerShell
reads nothing at all, because it only calls Bash.

**Fail closed is the default, not a branch.** Exactly one shape of answer means
"this is our WSL webapp": HTTP 200, a JSON object, ``app`` and ``runtime_owner``
spelled as :mod:`~hoya_market_agents.webapp.server` spells them — the producer
owns those names and this module reads them from there — a non-empty
string ``instance``, and an
``active_run`` that is a JSON boolean. Everything else — a 404, a 500, an HTML
page, ``{not json``, a missing field, ``"false"`` where ``false`` was promised —
is :data:`FOREIGN`, and a foreign listener is never started over, never stopped
and never replaced. Only a connection that is refused is :data:`FREE`.

``instance`` is a short-lived precondition and nothing else. It is not a secret,
not authentication and not persistent state: it exists so that a stop aimed at
the listener that answered ``/health`` cannot land on the listener that replaced
it half a second later. The server re-checks it when the ``POST`` arrives, which
is why :func:`shutdown` can be honest about a ``409``.
"""

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# The three answers. There is no fourth: "malformed" and "unknown" are ways of
# being foreign, and treating them as their own state is how a caller ends up
# with a branch that starts a second server on somebody else's port.
FREE = "free"
OWNED = "owned"
FOREIGN = "foreign"

# Nothing here waits on a person, so the timeout is short. A local listener that
# cannot answer ``/health`` in this long is not one we are going to stop.
TIMEOUT_SECONDS = 5.0

# A health document is four short fields. Reading more than this from a stranger
# on a local port is how a probe becomes a way to fill this process's memory.
MAX_HEALTH_BYTES = 64 * 1024


class Runtime:
    """What is listening on one port: :data:`FREE`, :data:`OWNED` or foreign.

    ``instance`` and ``active_run`` are filled in only when the state is
    :data:`OWNED`; ``reason`` carries the one line a foreign listener is
    reported with, and is empty otherwise.
    """

    def __init__(self, state, instance=None, active_run=None, reason=""):
        self.state = state
        self.instance = instance
        self.active_run = active_run
        self.reason = reason


class StopResult:
    """What came back from a stop that carried a precondition."""

    def __init__(self, stopped, status=None, reason=""):
        self.stopped = stopped
        self.status = status
        self.reason = reason


def probe(host=None, port=None, timeout=TIMEOUT_SECONDS):
    """Ask one port who it belongs to, and refuse to guess when it will not say."""
    contract = _contract()
    host, port = _endpoint(host, port)
    try:
        with urlopen(_url(host, port, contract.HEALTH_PATH), timeout=timeout) as answer:
            body = answer.read(MAX_HEALTH_BYTES)
    except HTTPError as exc:
        return _foreign("{}:{} 上的程式回應 HTTP {}，不是本專案的 webapp。".format(
            host, port, exc.code
        ))
    except URLError as exc:
        return _unreachable(host, port, exc.reason)
    except OSError as exc:
        return _unreachable(host, port, exc)
    return _read_health(host, port, body)


def shutdown(instance, host=None, port=None, timeout=TIMEOUT_SECONDS,
             allow_active_run=False):
    """Ask the listener that answered ``/health`` — and only it — to stop.

    The claim travels with the request so the server can re-check it at the
    moment it handles the ``POST``. A listener that has been replaced since the
    probe answers ``409``, and this returns that rather than reporting a stop
    that did not happen.

    ``allow_active_run`` is the same shape of promise about a different thing:
    the server re-reads whether an analysis is running when the ``POST`` lands,
    and interrupts one only if this says somebody agreed to it. It is **left out
    of the body entirely** unless it is true, because a field that is always sent
    is a field that has stopped meaning anything.
    """
    contract = _contract()
    host, port = _endpoint(host, port)
    fields = {
        contract.EXPECT_RUNTIME_FIELD: contract.RUNTIME_OWNER,
        contract.EXPECT_INSTANCE_FIELD: instance,
    }
    if allow_active_run:
        fields[contract.ALLOW_ACTIVE_RUN_FIELD] = contract.ALLOW_ACTIVE_RUN_CONSENT
    body = urlencode(fields).encode("utf-8")
    request = Request(
        _url(host, port, contract.SHUTDOWN_PATH),
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=timeout) as answer:
            answer.read(MAX_HEALTH_BYTES)
            return StopResult(True, answer.status)
    except HTTPError as exc:
        return StopResult(False, exc.code, _one_line(
            "{}:{} 拒絕了這次關閉（HTTP {}）：它已經不是剛才確認過的那一個 instance。"
            .format(host, port, exc.code)
        ))
    except URLError as exc:
        return StopResult(False, None, _one_line(
            "無法把關閉請求送到 {}:{}（{}）。".format(host, port, exc.reason)
        ))
    except OSError as exc:
        return StopResult(False, None, _one_line(
            "無法把關閉請求送到 {}:{}（{}）。".format(host, port, exc)
        ))


# -- reading one answer -----------------------------------------------------


def _read_health(host, port, body):
    contract = _contract()
    where = "{}:{}".format(host, port)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _foreign("{} 上的程式回應的不是合法 JSON，不是本專案的 webapp。".format(where))
    if not isinstance(payload, dict):
        return _foreign("{} 上的 /health 回應不是 JSON 物件。".format(where))
    if payload.get("app") != contract.RUNTIME_APP:
        return _foreign("{} 上的程式自稱 app={}，不是 {}。".format(
            where, _short(payload.get("app")), contract.RUNTIME_APP
        ))
    if payload.get("runtime_owner") != contract.RUNTIME_OWNER:
        return _foreign("{} 上的 webapp 由 {} 擁有，不是 {}。".format(
            where, _short(payload.get("runtime_owner")), contract.RUNTIME_OWNER
        ))
    instance = payload.get("instance")
    if not isinstance(instance, str) or not instance.strip():
        return _foreign("{} 上的 /health 沒有給出非空的 instance。".format(where))
    active_run = payload.get("active_run")
    # ``isinstance(True, int)`` is true and ``isinstance(1, bool)`` is not, which
    # is exactly the distinction the contract asks for: ``1`` and ``"true"`` are
    # not ``true``, and a run stopped on one of them was stopped on a guess.
    if not isinstance(active_run, bool):
        return _foreign("{} 上的 /health 的 active_run 不是 JSON boolean。".format(where))
    return Runtime(OWNED, instance=instance.strip(), active_run=active_run)


def _unreachable(host, port, reason):
    """A refused connection is a free port; anything else is not an invitation."""
    if isinstance(reason, ConnectionRefusedError):
        return Runtime(FREE)
    return _foreign("無法讀取 {}:{} 的 /health（{}），不確定它屬於誰。".format(
        host, port, reason
    ))


def _foreign(reason):
    return Runtime(FOREIGN, reason=_one_line(reason))


def _one_line(text):
    """One line, because the seam Bash reads is one ``key=value`` per line."""
    return " ".join(str(text).split())


def _short(value, limit=40):
    """A stranger's own words, quoted back at a length this process chooses."""
    text = _one_line(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _url(host, port, path):
    return "http://{}:{}{}".format(host, port, path)


def _contract():
    """The module that publishes the contract, imported when it is needed.

    Not at the top, and the reason is the shell entry points: this package's
    ``__init__`` imports ``server``, so importing it here at module scope would
    make ``python3 -m …runtime_control`` find itself already loaded and print a
    ``RuntimeWarning`` in front of every start and every stop a user runs.
    """
    from . import server

    return server


def _endpoint(host, port):
    """Fill in whichever half the caller left out."""
    server = _contract()

    return (server.DEFAULT_HOST if host is None else host,
            server.DEFAULT_PORT if port is None else port)


# -- the seam the shell entry points read -----------------------------------


def main(argv=None, out=None):
    """``probe`` and ``stop``, printed as ``key=value`` lines for Bash.

    ``probe`` always exits ``0``: which of the three states a port is in is the
    answer, not a failure. ``stop`` exits non-zero when the server did not stop,
    which includes the ``409`` a replaced listener answers with.
    """
    write = (lambda line: out(line)) if out is not None else sys.stdout.write
    parser = argparse.ArgumentParser(
        prog="runtime-control",
        description="讀 /health 判斷 127.0.0.1 上的 webapp 屬於誰，並在確認後關閉它。",
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("probe", help="印出 state／instance／active_run／reason")
    stop = commands.add_parser("stop", help="以 expect_runtime＋expect_instance 關閉")
    stop.add_argument("--instance", required=True)
    stop.add_argument(
        "--allow-active-run",
        action="store_true",
        help="有人明確同意中斷正在進行的分析；不給就不會送出同意",
    )
    for name in ("probe", "stop"):
        # The address may be written before or after the sub-command; a user
        # typing it on the wrong side is not a usage error worth a stack trace.
        sub = commands.choices[name]
        sub.add_argument("--host", default=None)
        sub.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    if args.command == "probe":
        host, port = _endpoint(args.host, args.port)
        found = probe(host=host, port=port)
        for field, value in (
            ("state", found.state),
            ("instance", "" if found.instance is None else found.instance),
            ("active_run", _yes_no(found.active_run)),
            # The address is printed rather than spelled in Bash, so the port
            # this product listens on stays a Python constant with one home.
            ("url", _url(host, port, "/")),
            ("reason", found.reason),
        ):
            write("{}={}\n".format(field, value))
        return 0

    result = shutdown(
        args.instance, host=args.host, port=args.port,
        allow_active_run=args.allow_active_run,
    )
    write("stopped={}\n".format(_yes_no(result.stopped)))
    write("status={}\n".format("" if result.status is None else result.status))
    write("reason={}\n".format(result.reason))
    return 0 if result.stopped else 1


def _yes_no(value):
    if value is None:
        return ""
    return "yes" if value else "no"


if __name__ == "__main__":  # pragma: no cover - the shell entry points' door
    raise SystemExit(main())
