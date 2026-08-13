"""Routing, headers and the server's own lifecycle.

A route here does three things and no more: read the URL, call one function in
:mod:`~hoya_market_agents.webapp.views` or
:mod:`~hoya_market_agents.webapp.live`, and send what came back — through
:mod:`~hoya_market_agents.webapp.pages` for a page, and for a linked artifact
that file's own bytes with this site's navigation added to *the response* — the
paragraph after next. Nothing here decides which runs match, and **no code in
this module opens a file under** ``runs/`` **for writing**. Three of its routes
do cause such a write, in this process, by calling something that does it;
exactly which, and exactly which files, is the section below — this sentence is
about where the code lives, not about what a request can end up doing.

Three orderings in this module are decisions rather than style:

* **The log is opened before the socket is bound.** A port that is already
  taken is the failure most likely to greet a user, and a log opened after the
  bind is a log that can never hold it. :func:`create_webapp_server` therefore
  takes an already-open log as an argument instead of making one — there is no
  way to call it without having opened the log first.
* **A port in use is fatal.** The competition build left a server on 8765 and
  the next one quietly took another port, so the page a user opened was the old
  one. This build stops, says which port and what to do, and returns non-zero.
* **The stop button is answered before the serving loop is ended.** A server
  that stops first has nothing left to send the page with, so the reader who
  pressed it sees a browser error and cannot tell "it stopped" from "it broke".
  ``POST /shutdown`` therefore writes its whole reply, then asks the loop to end;
  ``server_stop`` is still written where it always was, by :func:`serve_webapp`
  as the loop returns. See :meth:`WebappHandler._shutdown` and :class:`ServerStop`.

Serving artifacts is by allowlist: a request can name only the two files the
detail page itself links to. The run id is resolved through
``run_store.resolve_run_dir``, which accepts only well formed ids, so no path
in a URL reaches the filesystem.

**The navigation those two pages arrive with was never written to disk.** A
run's files are read-only once written, and an offline bundle carrying this
site's links would hand dead ones to whoever it was shared with — so the five
tabs go into the response instead, as bytes, after the opening ``<body>``, with
the file itself untouched (ADR 0007, :func:`site_nav_fragment` and
:func:`artifact_with_site_nav`). What is inserted is self-contained: its own
class, its own landmark name, and its own ``<style>`` whose every value is a
design token and whose every selector begins with that class — so it looks like
this site on a run from any build, and it cannot repaint one element of the page
it landed on. There is no script in it, and the reply keeps ``script-src 'none'``.

Everything about it fails towards the reader's page. A page whose opening tag
cannot be located *for certain* is sent exactly as it is rather than refused
(:func:`body_tag_end`); navigation that cannot be assembled is left off rather
than turned into an error page where the report was; the ``<iframe>`` the detail
page previews a report in is answered without the tabs, because navigating a
preview panel is not navigating (:data:`EMBEDDED_DESTINATIONS`); and opening the
same file from disk shows the bundle's own two-page navigation, which is the
whole point. Nothing about this reaches the ``runs/`` guarantee above: the bytes
are added on the way out.

Two policies, not one. The history and detail pages carry no script and are sent
with :data:`CONTENT_SECURITY_POLICY`, which still says ``script-src 'none'``.
The live room needs one script, so :data:`LIVE_CONTENT_SECURITY_POLICY` allows
``script-src 'self'`` — a same-origin file this server serves, never
``'unsafe-inline'`` — and opens ``connect-src 'self'`` for the event stream it
reads. Those two are the only directives it loosens; it also drops ``frame-src``
entirely, which is a tightening, so the two policies differ in three places and
not two. ``/live.js`` is the only script any page on this site names.

Which requests write, exactly
-----------------------------

* ``POST /launch`` causes a run directory to be written, and even then not by
  this process: it starts a separate ``launch``, which owns the directory from
  then on.
* ``GET /history`` and ``POST /history`` both write under ``runs/`` **in this
  process**, and the two write different amounts. The hand-entered form of
  ``POST /history`` creates at most one file per submission, because a submission
  names one run. **The sweep inside** :func:`~.views.history_data` **creates one
  per due run it manages to price, up to**
  :data:`~hoya_market_agents.webapp.outcome.MAX_SWEEP_RUNS` **in one pass** — a
  single ``GET /history`` over a Data Root with two expired runs really does end
  with ``checked=2, recorded=2``. That cap is lifted for one pass when the
  sweep's cursor cannot be written, so the honest upper bound is "every pending
  run in the Data Root"; see
  :func:`~hoya_market_agents.webapp.outcome.sweep_due_runs`.

  This is the page that used to be ``/stats``. The two pages are one page now
  (Spec R1), and the writing moved with the hit rates it belongs to: nothing else
  visits the clock, so a build where ``/history`` did not sweep would be a build
  where no run is ever checked.

  What is always exactly one is the file *per run*: ``outcome.json``, written
  once, for a run whose period has run out. Nothing already written is touched,
  and no other route under ``runs/`` opens a file for writing. The per-run rule
  is the one the write-once guarantee rests on; "one file per request" was never
  true of the sweep and stating it that way made a bounded page look like a
  bounded number of writes.
* ``POST /run/<id>/export-pdf`` writes that run's ``report.pdf`` and
  ``debate.pdf`` **in this process**, and those two names are the only files it
  can ever open for writing. It is the narrowest of the three: it adds files
  nothing else in this project reads, so a run it has been used on verifies
  exactly as it did before. A failed export adds nothing at all — see
  :mod:`~hoya_market_agents.webapp.pdf_export`, which owns every part of that
  guarantee. ``GET`` on the same URL is a 404 and writes nothing: an export is a
  submission, and a URL that wrote because it was opened would be one a browser
  could be made to open.
* ``POST /settings`` writes ``config/debate_rules.json``, which is not under
  ``runs/`` at all.

Besides those, the files this server opens for writing are its own —
``_data/logs/webapp.jsonl``, the sweep's ``_data/outcome-sweep-cursor.json``,
and the launch child's ``_data/logs/launch.log``.
"""

import errno
import json
import secrets
import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..design_tokens import PALETTE, SCALE
from ..run_store import resolve_run_dir
from . import launch as launch_module
from . import live, outcome as outcome_module, pages, pdf_export, settings, views
from .log import open_webapp_log
from .outcome import OutcomeCheck
from .views import first_value

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

HTML_CONTENT_TYPE = "text/html; charset=utf-8"
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
CSS_CONTENT_TYPE = "text/css; charset=utf-8"
SCRIPT_CONTENT_TYPE = "text/javascript; charset=utf-8"
EVENT_STREAM_CONTENT_TYPE = "text/event-stream; charset=utf-8"

# Server-rendered webapp pages load the one same-origin stylesheet.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'none'; style-src 'self'; "
    "img-src 'self' data:; frame-src 'self'; frame-ancestors 'self'; "
    "form-action 'self'; base-uri 'none'"
)

# Offline report artifacts remain self-contained files with inline CSS. They are
# passed through unchanged apart from the already-approved response-only nav.
ARTIFACT_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
    "img-src 'self' data:; frame-src 'self'; frame-ancestors 'self'; "
    "form-action 'self'; base-uri 'none'"
)

# The live room. It differs from the policy above in three directives, and only
# two of those are a loosening: ``script-src`` goes from ``'none'`` to one
# same-origin file, and ``connect-src`` is added for the event stream. The third
# is a tightening — ``frame-src 'self'`` is dropped altogether, because the room
# embeds nothing. Inline script stays forbidden and so does every other origin.
# ``test_the_room_loosens_exactly_two_directives_and_no_others`` pins the two
# that loosen; this comment is what says the third exists and which way it goes.
LIVE_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; "
    "form-action 'self'; base-uri 'none'"
)

SOURCE_REQUEST = "webapp.request"
SOURCE_SERVER = "webapp.server"
SOURCE_INDEX = "webapp.index"
SOURCE_LAUNCH = "webapp.launch"
SOURCE_STREAM = "webapp.stream"
SOURCE_SETTINGS = "webapp.settings"
SOURCE_OUTCOME = "webapp.outcome"
SOURCE_PDF = "webapp.pdf"

# The front door is the debate room — "辯論框才是我的專案主題". ``/live`` stays
# a working alias for it (the launcher's handshake URL ends in ``/live`` and
# "回到目前 run" points there); the history query, which used to be what ``/``
# fell through to, now has its own explicit route.
ROOT_PATH = "/"
LIVE_PATH = "/live"
HISTORY_PATH = "/history"
LIVE_SCRIPT_PATH = pages.LIVE_SCRIPT_PATH
LIVE_EVENTS_PATH = "/live/events"
LAUNCH_PATH = "/launch"
LAUNCH_STATUS_PATH = "/launch/status"
SETTINGS_PATH = "/settings"
STATIC_SITE_CSS_PATH = "/static/site.css"
STATIC_LIVE_JS_PATH = "/static/live.js"
STATIC_PATH_PREFIX = "/static/"

# The last segment of the one URL under ``/run/<id>/`` that is not a file of that
# run. It is read from :mod:`~hoya_market_agents.webapp.pages`, which renders the
# form that submits to it, so the action and the route are one spelling.
EXPORT_PDF_SEGMENT = pages.EXPORT_PDF_SEGMENT

# The one URL that ends this process, read from the module that renders its button
# for the same reason: a stop button pointing at a route nobody answers would 404
# rather than fail, and nothing else would say so.
SHUTDOWN_PATH = pages.SHUTDOWN_PATH

# The ownership contract, spelled here because this module is what publishes it
# (architecture §15.4). :mod:`~hoya_market_agents.webapp.runtime_control` is its
# one consumer and reads these names from here rather than repeating them: a
# producer and a consumer spelling the same contract separately is exactly the
# failure the contract exists to prevent.
#
# The direction of that import is the other reason they live here. This package's
# ``__init__`` imports this module, so a consumer that this module imported would
# already be loaded before ``python3 -m …runtime_control`` ran it — which Python
# answers with a ``RuntimeWarning`` on every start and every stop.
HEALTH_PATH = "/health"
RUNTIME_APP = "hoya-market-agents-webapp"
RUNTIME_OWNER = "wsl"

# What a stop must claim about the listener it believes it is talking to. Sent as
# an ordinary form body, because that is what ``POST /shutdown`` already accepts.
EXPECT_RUNTIME_FIELD = "expect_runtime"
EXPECT_INSTANCE_FIELD = "expect_instance"

# The third field, and the only one that is about a person rather than a process:
# "somebody was asked whether to interrupt the analysis that is running, and said
# yes". It is absent from every stop where nobody was asked.
#
# One spelling counts and it is this one. ``true``, ``1``, ``YES`` and ``on`` are
# all near misses, and a near miss deciding whether a running analysis is killed
# is not a contract — it is a coin toss with good intentions.
ALLOW_ACTIVE_RUN_FIELD = "allow_active_run"
ALLOW_ACTIVE_RUN_CONSENT = "yes"

# The retired statistics page. It is a redirect and nothing else: the hit rates
# it used to show are part of :data:`HISTORY_PATH` now (Spec R1), and a bookmark
# of this URL still gets its reader there.
STATS_PATH = "/stats"

# 302 rather than 301, as the endpoint contract says: a permanent redirect is
# cached by the browser for good, and a local server whose routes a reader may
# edit tomorrow should not leave a rule behind in a cache nobody can reach.
RETIRED_PAGE_STATUS = 302

# A form submission is answered with a page rather than a redirect, so this is
# the status of the one redirect that answers a submission: the launch, whose
# answer is the room. See :meth:`_redirect`.
SUBMISSION_REDIRECT_STATUS = 303

# Which state of a hand-entered outcome is the request's own conflict. A record
# that is already there is a 409 for the same reason a locked settings page is:
# nothing is wrong with what was submitted, it conflicts with what is already
# recorded. A submission this server will not take at all — an unknown verdict,
# a price that is not a number, a run id that names nothing — is a 200 with the
# reason on the page, exactly as a refused launch is.
OUTCOME_CONFLICT_STATES = (
    outcome_module.ALREADY_RECORDED,
    outcome_module.RECORD_UNREADABLE,
)

# A request body is a question someone typed, or the rule file's own fields sent
# back. Anything much longer than those is neither, and reading an unbounded
# body into memory because a client said so is how a local server becomes a way
# to fill a machine's RAM. A settings form that outgrew this would arrive as no
# form at all, so a test measures it against this number.
MAX_FORM_BYTES = 8 * 1024

# Which state of a save is the request's own conflict rather than guidance. A
# refused value is a 200 with the reason on the page, exactly as a refused
# launch is; a locked page is a 409, because nothing is wrong with what was
# submitted — it conflicts with what this Data Root is already doing.
SETTINGS_CONFLICT_STATES = (settings.LOCKED,)

# What each state of a PDF export is answered with. The split is the same one the
# rest of this module makes: a run that has not produced its pages yet, and a run
# that already has its PDFs, are both *guidance* — nothing is wrong with the
# request or with this server, and the page says which files are missing or already
# there — so both are 200, exactly as a refused launch is. A conversion that broke
# is this server failing to do what it was asked, which is the one case here that is
# genuinely a 5xx. ``RUN_MISSING`` never reaches this table: a URL naming no run is
# the same 404 the detail page gives.
#
# **An already-exported run is not a 409, and a run being exported is.** The
# difference is the one this module already draws for a launch: a request that
# conflicts with what this server is *doing right now* is a 409, and a request
# refused because of what is simply on disk is a 200 with the reason on the page.
# An export already under way is the first — the same answer, and the same status,
# as pressing the launch button twice.
EXPORT_STATUS = {
    pdf_export.EXPORTED: 200,
    pdf_export.SOURCE_MISSING: 200,
    pdf_export.ALREADY_EXPORTED: 200,
    pdf_export.IN_PROGRESS: 409,
    pdf_export.CONVERSION_FAILED: 500,
}

# The states where this server declined to write rather than tried and failed.
# They share one log event: what a reader of the log wants to tell apart is "it
# would not" from "it broke", and the message says which of the two reasons it was.
PDF_EXPORT_DECLINED = (
    pdf_export.SOURCE_MISSING,
    pdf_export.ALREADY_EXPORTED,
    pdf_export.IN_PROGRESS,
)

# What the injected bar is written against: its own class, its own accessible
# name, and its own name for the one link that administers this site rather than
# browses it (Spec R-003, told apart by where it sits rather than by a second
# landmark). All three are this module's own and none of them is the offline
# page's: that page already carries a ``.page-tabs`` bar whose accessible name is
# 主要頁面, and borrowing either would leave the response with two landmarks under
# one name and one element painted from two stylesheets.
SITE_NAV_CLASS = "hoya-site-nav"
SITE_NAV_LABEL = "站內導覽"
SITE_NAV_ADMIN_CLASS = "hoya-site-nav-admin"

# The bar's own declarations, and then ``(selector parts, declarations)`` for
# everything inside it. A table rather than one string because that is what makes
# the scoping structural: :func:`_scoped` is the only thing that turns these into
# selectors and it cannot produce one that does not begin with the bar's class, so
# no rule here can reach the page the bar was inserted into. Every value is a
# token, read from :mod:`~hoya_market_agents.design_tokens` at injection time by
# :func:`_site_nav_tokens` — this module owns no colour and no length.
SITE_NAV_BAR = (
    "display:flex;align-items:center;gap:var(--space-1);flex-wrap:wrap;margin:0;"
    "padding:var(--space-1);background:var(--surface);"
    "border:1px solid var(--border);border-radius:var(--radius-md);"
    "font-family:var(--font-sans);font-size:var(--size-sm);"
    "line-height:var(--line-base);"
)
SITE_NAV_RULES = (
    (
        ("a", "[role=link]"),
        "text-decoration:none;font-weight:700;white-space:nowrap;"
        "padding:var(--space-3) var(--space-4);border-radius:var(--radius-sm);",
    ),
    (("a",), "color:var(--link);"),
    (("a[aria-current=page]",), "background:var(--accent);color:var(--accent-text);"),
    (("[aria-disabled=true]",), "color:var(--muted);opacity:var(--dim);"),
    ((".{}".format(SITE_NAV_ADMIN_CLASS),), "margin-inline-start:auto;"),
)

# What one ``<body>`` scan has to step over. A comment can hold anything, an
# attribute value can hold ``>``, and a page where either is mistaken for the tag
# is a page with navigation inserted into the middle of its own markup.
COMMENT_OPEN = b"<!--"
COMMENT_CLOSE = b"-->"
BODY_OPEN = b"<body"
QUOTES = (b'"', b"'")

# What may follow ``<body`` and still leave it the start tag: the tag's own end,
# a self-closing slash, or the whitespace before an attribute. Anything else makes
# it a different element — ``<bodyguard>`` is the one this keeps the scan off.
BODY_OPEN_DELIMITERS = (b">", b"/", b" ", b"\t", b"\n", b"\r", b"\f")

# The ``Sec-Fetch-Dest`` values that mean this request is not a reader browsing to
# the page. The detail page shows a run's report in an ``<iframe>`` of the very
# URL the navigation would be added to, and a tab bar inside a preview panel
# navigates the panel — a reader who pressed 即時辯論 there would get the whole
# site inside a frame, which is the shape ADR 0007 rejected.
#
# These two and no more, because these two are the only ones this site's own
# Content-Security-Policy permits: ``frame-src 'self'`` allows a frame and
# ``default-src 'none'`` leaves ``<object>`` and ``<embed>`` with nothing to load.
# A request that names no destination at all is treated as a reader — the
# navigation is what this feature is for, and a header nobody sent must not be
# what makes it disappear.
EMBEDDED_DESTINATIONS = ("iframe", "frame")


class WebappError(Exception):
    """Raised when the server cannot start."""


class StreamSettings:
    """How long one event stream lives and how often it looks.

    A stream is bounded on purpose. ``EventSource`` reconnects by itself and
    hands back the cursor it had, so ending a connection costs a client nothing
    and costs this server one fewer thread that could otherwise outlive the run
    it was watching. ``sleeper`` and ``monotonic`` are seams: a test drives a
    whole stream without waiting for any of these numbers.
    """

    def __init__(
        self,
        poll_seconds=0.5,
        heartbeat_seconds=15.0,
        max_seconds=300.0,
        sleeper=time.sleep,
        monotonic=time.monotonic,
    ):
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.max_seconds = max_seconds
        self.sleeper = sleeper
        self.monotonic = monotonic


DEFAULT_STREAM_SETTINGS = StreamSettings()


def webapp_handler_class(
    data_root,
    log,
    stream=None,
    lock=None,
    spawn=None,
    rules_path=None,
    outcome_check=None,
    convert_pdf=None,
    stop=None,
    instance=None,
    live_clock=None,
):
    """Return the request handler for one Data Root and one open log.

    Returned rather than configured on the server so a test can drive a real
    request through the real routing without a listening socket — which is also
    why nothing in here reads ``self.server``.

    ``lock`` is shared by every request this handler serves, because "one run at
    a time" is a statement about the server and not about one request. ``spawn``
    and ``stream`` are the two seams that keep the tests off real processes and
    real clocks. ``rules_path`` is the third: the settings page edits the Code
    Root's own ``config/debate_rules.json`` unless a caller names another file.
    ``outcome_check`` is the fourth, and the only one that reaches a network:
    it carries the clock and the quote source the statistics page's expiry sweep
    uses, so a test drives a whole verification without a socket.

    ``convert_pdf`` is the fifth and the only one that would otherwise start a
    browser: ``None`` means the real Edge converter, and every test hands in one
    of its own, so no test in this project prints a PDF.

    ``stop`` is the sixth: the callable ``POST /shutdown`` reaches for once its
    reply is out. :func:`create_webapp_server` always wires it to the server it
    just bound — that is what keeps this handler from touching ``self.server``.
    ``None`` means what it says, and is the state a handler built for a test is
    in: there is no serving loop, so there is nothing to stop, and the endpoint
    records that rather than implying it stopped one.

    ``instance`` is the seventh, and the only one whose default is a random
    value: it is what ``GET /health`` publishes and what a conditional
    ``POST /shutdown`` must name, so one handler class is one listener for as
    long as it serves. A test that needs to speak about a particular listener
    hands one in; nothing else does, because a value someone could predict would
    let a stop meant for the listener that has gone land on the one that
    replaced it.
    """
    root = Path(data_root)
    stream = stream or DEFAULT_STREAM_SETTINGS
    lock = lock if lock is not None else launch_module.LaunchLock()
    rules = Path(settings.RULES_PATH if rules_path is None else rules_path)
    checker = outcome_check if outcome_check is not None else OutcomeCheck(log=log)
    runtime_instance = instance or secrets.token_hex(8)
    live_clock = live_clock or live.utc_now

    class WebappHandler(BaseHTTPRequestHandler):
        server_version = "HoyaBitWebapp/1.0"
        launch_lock = lock

        def do_GET(self):
            split = urlsplit(self.path)
            self._guarded(split.path, lambda: self._route(split.path, split.query))

        def do_POST(self):
            split = urlsplit(self.path)
            self._guarded(split.path, lambda: self._post(split.path))

        def _guarded(self, path, work):
            try:
                work()
            except Exception as exc:  # noqa: BLE001 - the boundary is the point
                # An unhandled error in a resident server is otherwise invisible:
                # the thread dies, the browser shows nothing, and no record is
                # left. The page says only that it failed; the log says what.
                log.error(
                    "request_failed",
                    SOURCE_REQUEST,
                    "{} {}：{}".format(path, type(exc).__name__, exc),
                )
                self._send_page(
                    500, pages.render_not_found_page("這個頁面在產生時發生錯誤，詳情見 webapp.jsonl。")
                )

        def _route(self, path, raw_query):
            query = parse_qs(raw_query, keep_blank_values=True)
            if path in (STATIC_SITE_CSS_PATH, STATIC_LIVE_JS_PATH):
                self._static(path)
                return
            if path.startswith(STATIC_PATH_PREFIX):
                self._not_found(path, "這個靜態資產不在本站白名單。")
                return
            if path == HEALTH_PATH:
                self._health()
                return
            if path in (ROOT_PATH, LIVE_PATH):
                self._live(query)
                return
            if path == HISTORY_PATH:
                self._history(query)
                return
            if path == LIVE_SCRIPT_PATH:
                self._live_script()
                return
            if path == LIVE_EVENTS_PATH:
                self._live_events(query)
                return
            if path == LAUNCH_STATUS_PATH:
                self._launch_status(query)
                return
            if path == SETTINGS_PATH:
                self._settings()
                return
            if path == STATS_PATH:
                self._redirect(HISTORY_PATH, status=RETIRED_PAGE_STATUS)
                return
            if path == SHUTDOWN_PATH:
                # Answered here rather than left to fall through to "this URL is
                # not a page of this site", which would be false: the URL exists
                # and does something, just not when it is opened. A stop that a
                # link could trigger is a stop any page could trigger for you.
                self._not_found(path, "這個網址只接受表單送出，不會用 GET 關閉伺服器。")
                return
            segments = _run_segments(path)
            if len(segments) == 1:
                self._detail(path, segments[0])
                return
            if _names_the_export(segments):
                # Answered here rather than left to the artifact allowlist below,
                # which would say "this run has no export-pdf to open" — true of a
                # file, and misleading about a URL that does exist and does
                # something. Either way nothing is written; this is about the
                # sentence, not about the guarantee.
                self._not_found(path, "這個網址只接受表單送出，不會用 GET 產生 PDF。")
                return
            if len(segments) == 2:
                self._artifact(path, segments[0], segments[1])
                return
            self._not_found(path, "這個網址不屬於本站的任何頁面。")

        def _post(self, path):
            if path == LAUNCH_PATH:
                self._launch()
                return
            if path == SETTINGS_PATH:
                self._save_settings()
                return
            if path == HISTORY_PATH:
                self._record_outcome()
                return
            if path == SHUTDOWN_PATH:
                self._shutdown()
                return
            segments = _run_segments(path)
            if _names_the_export(segments):
                self._export_pdf(path, segments[0])
                return
            self._not_found(path, "這個網址不接受表單送出。")

        # -- reading, and the one page that also writes --------------------

        def _history(self, query, write=None, status=200):
            """Sweep what has expired, then show everything that follows from it.

            The sweep is inside :func:`views.history_data`, and it is why this is
            the one ``GET`` in this server that can create a file. It creates
            exactly one *kind* of file — a run's ``outcome.json`` — and at most
            one of those per run, once, for ever. **How many it creates in one
            request is not one**: it is one per due run it manages to price. See
            the module docstring's "Which requests write, exactly". Nothing
            already written is touched either way.
            """
            data = views.history_data(root, query, outcome_check=checker, write=write)
            if data["state"] != views.STATE_OK:
                log.warning("index_unavailable", SOURCE_INDEX, data["reason"])
            self._send_page(status, pages.render_history_page(data))

        def _detail(self, path, run_id):
            data = views.run_data(root, run_id)
            if data is None:
                self._not_found(path, "沒有這個 run_id 的執行紀錄。")
                return
            self._send_page(200, self._run_page(run_id, data))

        def _run_page(self, run_id, data, export=None):
            """One run's page, drawn with what is in its directory right now.

            The look for existing PDFs happens here rather than in
            :mod:`~hoya_market_agents.webapp.pages`, which does no I/O, and it is
            the same question the export refuses on — so the button this page
            offers and the answer a submission gets cannot disagree.
            """
            return pages.render_run_page(
                data,
                export=export,
                exported=pdf_export.existing_targets(root, run_id),
            )

        def _artifact(self, path, run_id, name):
            """Send one run's own offline page, with this site's tabs on it.

            The file is read and the navigation is added to the *response*: the
            two files a run produces are written once and never touched again,
            and one that carried this site's links would hand dead ones to
            whoever it was shared with (ADR 0007). Which run the two report tabs
            open is this one — a reader is looking at it — rather than the newest
            run with a report, which is what a page about no run points at.
            """
            body = views.artifact_bytes(root, run_id, name)
            if body is None:
                self._not_found(path, "這個 run 沒有可以開啟的 {}。".format(name))
                return
            self._send(
                200,
                HTML_CONTENT_TYPE,
                self._with_site_nav(run_id, name, body),
                policy=ARTIFACT_CONTENT_SECURITY_POLICY,
            )

        def _with_site_nav(self, run_id, name, artifact):
            """One offline page's bytes, as the request that asked for them reads.

            A frame gets the file and nothing added; see
            :data:`EMBEDDED_DESTINATIONS` for why the same URL answers those two
            differently. So does a request whose navigation could not be built —
            which is the point of :meth:`_site_nav` being allowed to answer
            ``None`` rather than raise.
            """
            nav = self._site_nav(run_id, name)
            if nav is None:
                return artifact
            return artifact_with_site_nav(artifact, nav)

        def _site_nav(self, run_id, name):
            """This run's five tabs, or ``None`` when they cannot be assembled.

            **The reader's page outranks the navigation on it.** By the time this
            runs the file has been read and is in hand, so anything that goes
            wrong here is a choice between a report with no tabs on it and a 500
            page where the report was — and the second answer is not one this
            server is entitled to give for a file it is holding. The generic
            boundary would give it: :meth:`_guarded` turns an exception into an
            error page, which is right for a page this server assembles and wrong
            for a page it is only passing on. So the exception stops here, and
            into the log, which is the only place left to say it.
            """
            if self.headers.get("Sec-Fetch-Dest") in EMBEDDED_DESTINATIONS:
                return None
            try:
                return site_nav_fragment(
                    run_id, run_artifacts(root, run_id), current=name
                )
            except Exception as exc:  # noqa: BLE001 - the boundary is the point
                log.warning(
                    "artifact_nav_unavailable",
                    SOURCE_REQUEST,
                    "{} 的站內導覽無法產生（{}：{}），已原樣送出離線頁。".format(
                        run_id, type(exc).__name__, exc
                    ),
                )
                return None

        # -- the live room -------------------------------------------------

        def _live(self, query):
            data = live.live_snapshot(
                root, first_value(query, "run"), clock=live_clock
            )
            self._send_page(
                200,
                pages.render_live_page(
                    data,
                    launch=self.launch_lock.state(),
                    suggestions=views.target_suggestions(root),
                ),
                policy=LIVE_CONTENT_SECURITY_POLICY,
            )

        def _live_script(self):
            self._send(
                200,
                SCRIPT_CONTENT_TYPE,
                pages.live_script().encode("utf-8"),
                policy=LIVE_CONTENT_SECURITY_POLICY,
            )

        def _static(self, path):
            """Serve only the two approved frontend assets, never an arbitrary path."""
            if path == STATIC_SITE_CSS_PATH:
                self._send(
                    200,
                    CSS_CONTENT_TYPE,
                    pages.stylesheet().encode("utf-8"),
                )
                return
            self._send(
                200,
                SCRIPT_CONTENT_TYPE,
                pages.live_script().encode("utf-8"),
                policy=LIVE_CONTENT_SECURITY_POLICY,
            )

        def _live_events(self, query):
            """Stream one run's events until it ends, the client goes, or time is up.

            Two failures end a stream and neither is allowed to become anything
            larger. A reader who closed the tab is not an event: the writes stop
            raising into a log nobody needs. Anything else is recorded and the
            stream stops — a half-sent stream is what an ``EventSource``
            reconnects from, and a 500 page written into the middle of one would
            be neither an error page nor a stream.

            Both are caught here rather than left to the generic boundary,
            because by this point the response has already begun. Nothing in
            either path reaches a run directory: this method reads a log file
            and writes to a socket (architecture §4.0.1).
            """
            try:
                self._stream(query)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except Exception as exc:  # noqa: BLE001 - the boundary is the point
                log.error(
                    "stream_failed",
                    SOURCE_STREAM,
                    "{}：{}".format(type(exc).__name__, exc),
                )

        def _stream(self, query):
            # The response opens before anything that can fail, so a reader
            # always gets a stream to reconnect to rather than a closed socket.
            self._begin_stream()
            run_id, run_dir = live.resolve_live_run(root, first_value(query, "run"))
            if run_dir is None:
                self._frame("waiting", None, {"state": live.STATUS_WAITING})
                return
            room, offset, missed, resumed = live.open_room(
                run_dir,
                live.read_question(run_dir),
                cursor=self._requested_cursor(query),
                run_id=run_id,
            )
            self._frame(
                "append" if resumed else "snapshot",
                live.make_cursor(run_id, offset),
                _room_payload(
                    room, missed, run_id, offset, run_dir, live.read_question(run_dir),
                    live_clock,
                ),
            )
            self._follow(run_id, run_dir, room, offset)

        def _requested_cursor(self, query):
            """``Last-Event-ID`` wins over ``?after``.

            The query parameter is what the freshly rendered page knew when it
            was drawn; the header is what the browser held when the connection
            it already had dropped. When both are present the header is the
            newer of the two by construction.
            """
            return self.headers.get("Last-Event-ID") or first_value(query, "after")

        def _follow(self, run_id, run_dir, room, offset):
            events_path = run_dir / live.EVENTS_RECORD
            started = stream.monotonic()
            last_write = started
            while True:
                entries, offset = live.read_events(events_path, offset)
                was_debate_started = room.debate_started
                fresh = room.ingest([record for _, record in entries])
                now = stream.monotonic()
                if fresh or room.debate_started != was_debate_started:
                    self._frame("append", live.make_cursor(run_id, offset),
                                _room_payload(
                                    room, fresh, run_id, offset, run_dir,
                                    live.read_question(run_dir), live_clock,
                                ))
                    last_write = now
                if not entries and live.run_finished(run_dir):
                    payload = _room_payload(
                        room, [], run_id, offset, run_dir,
                        live.read_question(run_dir), live_clock,
                    )
                    payload["outcome"] = live.run_outcome(root, run_id)
                    payload["state"] = live.STATUS_FINISHED
                    payload["completion"] = live.completion_for(run_dir, run_id)
                    self._frame("done", live.make_cursor(run_id, offset), payload)
                    return
                if now - last_write >= stream.heartbeat_seconds:
                    self._write(": 保持連線\n\n")
                    last_write = now
                if now - started >= stream.max_seconds:
                    return
                stream.sleeper(stream.poll_seconds)

        def _begin_stream(self):
            self.send_response(200)
            self.send_header("Content-Type", EVENT_STREAM_CONTENT_TYPE)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            self.end_headers()
            # Say how soon to come back before anything else, so the number is
            # already in hand if the very first frame is also the last one.
            self._write("retry: 2000\n\n")

        def _frame(self, name, cursor, payload):
            lines = ["event: {}".format(name)]
            if cursor is not None:
                lines.append("id: {}".format(cursor))
            lines.append(
                "data: {}".format(json.dumps(payload, ensure_ascii=False))
            )
            self._write("\n".join(lines) + "\n\n")

        def _write(self, text):
            self.wfile.write(text.encode("utf-8"))
            self.wfile.flush()

        # -- the one write -------------------------------------------------

        def _launch(self):
            wants_json = "application/json" in (self.headers.get("Accept") or "")
            request = launch_module.read_request(_form_body(self))
            problem, sentence = launch_module.launch_problem(root, request)
            if problem is not None:
                log.warning("launch_refused", SOURCE_LAUNCH, sentence)
                if wants_json:
                    self._send_json(
                        400,
                        {"status": "failed", "problem": problem, "reason": sentence},
                    )
                else:
                    self._send_page(
                        200,
                        pages.render_launch_problem_page(
                            sentence, report_run=views.latest_report_run(root)
                        ),
                    )
                return
            token = launch_module.launch_token()
            handshake = launch_module.handshake_path(root, token)
            process = self.launch_lock.claim_token(
                token,
                handshake,
                lambda: launch_module.start_launch(
                    root,
                    request,
                    token=token,
                    handshake=handshake,
                    spawn=spawn,
                ),
            )
            if process is None:
                # 409 rather than 200: nothing is wrong with the Data Root or
                # the question, the request simply conflicts with what this
                # server is already doing. The guidance states are 200.
                log.warning(
                    "launch_refused", SOURCE_LAUNCH, launch_module.BUSY_MESSAGE
                )
                if wants_json:
                    self._send_json(
                        409,
                        {"status": "busy", "reason": launch_module.BUSY_MESSAGE},
                    )
                else:
                    self._send_page(
                        409,
                        pages.render_launch_problem_page(
                            launch_module.BUSY_MESSAGE,
                            report_run=views.latest_report_run(root),
                        ),
                    )
                return
            self.launch_lock.note_question(request.question)
            log.info("launch_started", SOURCE_LAUNCH, "已在背景啟動一次 launch")
            if wants_json:
                self._send_json(202, {"status": "pending", "launch_token": token})
            else:
                self._redirect(LIVE_PATH)

        def _launch_status(self, query):
            token = first_value(query, "token")
            status = self.launch_lock.launch_status(token, root)
            if status is None:
                self._send_json(404, {"status": "unknown"})
                return
            self._send_json(200, status)

        # -- the other write -----------------------------------------------

        def _settings(self):
            self._send_page(
                200,
                pages.render_settings_page(
                    settings.settings_data(root, rules_path=rules)
                ),
            )

        def _save_settings(self):
            """Hand one submitted form to :func:`settings.save_rules` and say so.

            The page is rendered again rather than redirected to, because a
            refusal has to come back with what was typed still in the boxes —
            a redirect would answer a wrong number with an empty form.
            """
            submitted = {
                name: values[0] for name, values in _form_body(self).items() if values
            }
            outcome = settings.save_rules(rules, submitted, root)
            _log_settings(log, outcome, rules)
            status = 409 if outcome.state in SETTINGS_CONFLICT_STATES else 200
            self._send_page(
                status,
                pages.render_settings_page(
                    settings.settings_data(
                        root,
                        rules_path=rules,
                        # A save that went through is shown from the file: what
                        # is stored is the answer to "what did that do", and the
                        # text that was typed is only the answer to "what do I
                        # have to fix".
                        submitted=None if outcome.state == settings.SAVED else submitted,
                        outcome=outcome,
                    )
                ),
            )

        # -- the third write -----------------------------------------------

        def _record_outcome(self):
            """Take one hand-entered result, then redraw the page around it.

            The clock is the sweep's own (``checker.now``), not a second
            reading of the wall clock: the page decides which runs are due
            enough to offer, and a write judged against a different instant
            could refuse what the form had just listed.

            The page comes back with no query conditions, because the submission
            carried none: this form's fields are a run and a verdict, and a
            filter guessed at here would answer with a list the reader never
            asked for. The run just recorded is therefore on the page.
            """
            form = _form_body(self)
            written = outcome_module.record_manual_outcome(
                root,
                first_value(form, "run_id") or "",
                first_value(form, "verdict") or "",
                now=checker.now(),
                note=first_value(form, "note"),
                actual_price=first_value(form, "actual_price"),
                log=log,
            )
            if not written.ok:
                log.warning("outcome_manual_refused", SOURCE_OUTCOME, written.message)
            self._history(
                {},
                write=written,
                status=409 if written.state in OUTCOME_CONFLICT_STATES else 200,
            )

        # -- the fourth write, and the only one that adds a .pdf -------------

        def _export_pdf(self, path, run_id):
            """Export one run's two PDFs and answer with that run's own page.

            The page rather than a redirect, for the reason every submission here
            is answered with one: what the button did has to arrive with the
            button, and a redirect would answer a failed conversion with a page
            that says nothing about it.

            Every decision about the filesystem belongs to
            :func:`~hoya_market_agents.webapp.pdf_export.export_run_pdfs` — which
            run, which files, whether anything was written, and the sentence for
            it. What is decided here is a status and a page, and the run's detail
            is read *after* the export so the page shows the directory as it now
            is.
            """
            result = pdf_export.export_run_pdfs(root, run_id, convert=convert_pdf)
            _log_pdf_export(log, result, run_id)
            # Two ways there is no page to answer with, and one sentence for both,
            # which is the one the detail route gives: the id named nothing to
            # begin with, or the directory stopped being resolvable between the
            # export and this read. Neither is worth telling a reader apart, and
            # neither may be answered with a page assembled from nothing.
            data = (
                None
                if result.state == pdf_export.RUN_MISSING
                else views.run_data(root, run_id)
            )
            if data is None:
                self._not_found(path, "沒有這個 run_id 的執行紀錄。")
                return
            self._send_page(
                EXPORT_STATUS[result.state],
                self._run_page(run_id, data, export=result),
            )

        # -- who owns this port ---------------------------------------------

        def _health(self):
            """Publish the ownership contract, and exactly it.

            Four fields, no more: the one module that reads this fails closed on
            anything it does not recognise, so a fifth field added here for a
            reader's convenience would be a field that reader has to be taught to
            ignore. ``active_run`` is asked of the launch lock rather than
            remembered, for the reason the lock itself gives — a run that
            finished or crashed releases it without anyone clearing a flag.
            """
            self._send_json(200, {
                "app": RUNTIME_APP,
                "runtime_owner": RUNTIME_OWNER,
                "instance": runtime_instance,
                "active_run": lock.busy(),
            })

        # -- the one route that ends this process ---------------------------

        def _shutdown(self):
            """Check the claim, send the closed page, then ask the loop to end.

            The check is here, at the moment the ``POST`` is handled, and not at
            the moment the caller last looked: that gap is the whole failure this
            precondition exists for. A stop aimed at the listener that answered
            ``/health`` two seconds ago must not land on the one that replaced it,
            so a claim naming another instance is a ``409`` and this server keeps
            serving.

            A submission carrying no claim at all is the page's own button, which
            is same-origin and is talking to the server that drew it. It keeps
            the behaviour it had; the public scripts always claim, because they
            are the ones that cannot see which listener they reached.

            The two statements after the check are in the order the endpoint
            contract names, and the order is the whole feature: this reply is the
            last thing this server will ever write, so it is written while it
            still can be.

            Nothing here writes ``server_stop``. That record belongs to the end of
            :func:`serve_webapp`, which is where it was before this endpoint
            existed and where ``Ctrl+C`` still produces it — one stop path, one
            record, whichever way the stop was asked for.
            """
            form = _form_body(self)
            refusal = self._claim_refusal(form)
            if refusal is not None:
                self._reject_stop(refusal)
                return
            # Re-read at the moment the POST is handled — and take the lock in the
            # same step. A client that saw ``active_run: false`` a second ago and
            # asked nobody is a client whose information is now out of date, so
            # this is a conflict in the same sense a replaced instance is; and a
            # decision that did not also take the lock would leave a window for a
            # launch to start in before the loop actually ends.
            if not lock.reserve_stop(self._consented(form)):
                self._reject_stop(
                    "目前有分析正在進行，而這次關閉沒有帶明確同意，伺服器維持運行。"
                )
                return
            self._send_page(200, pages.render_shutdown_page())
            self._stop_serving()

        def _reject_stop(self, reason):
            log.warning("shutdown_claim_rejected", SOURCE_SERVER, reason)
            self._send_page(409, pages.render_not_found_page(reason))

        def _claim_refusal(self, form):
            """Why this stop is not for this listener, or ``None`` if it is.

            A submission with neither field is the in-page button and claims
            nothing. One with either field is a public script's, and a script
            that names half a precondition has not established one — so a claim
            missing its other half is refused rather than half-honoured.
            """
            runtime = first_value(form, EXPECT_RUNTIME_FIELD)
            claimed = first_value(form, EXPECT_INSTANCE_FIELD)
            if runtime is None and claimed is None:
                return None
            if runtime != RUNTIME_OWNER:
                return "這次關閉宣告的 runtime 是 {}，這台 webapp 屬於 {}。".format(
                    runtime, RUNTIME_OWNER
                )
            if claimed != runtime_instance:
                return (
                    "這次關閉宣告的 instance 已經不是現在在聽的這一個，"
                    "伺服器維持運行。"
                )
            return None

        def _consented(self, form):
            """Did somebody actually agree to interrupt a running analysis?

            Exact equality with the one approved spelling, so every other value —
            including the empty string a checkbox sends when nothing was ticked —
            is "no". The default answer to this question is no, and a value this
            server does not recognise is not an answer at all.
            """
            return (
                first_value(form, ALLOW_ACTIVE_RUN_FIELD) == ALLOW_ACTIVE_RUN_CONSENT
            )

        def _stop_serving(self):
            """Reach for the stop seam, and record how that went.

            **This is the one place in this module that must not let anything
            reach** :meth:`_guarded`, and the reason is the ordering the endpoint
            exists for: the reply is already on the wire by the time this runs, so
            the generic boundary's answer — a 500 page — would arrive as a second
            response inside a stream that already has one. That is not an error
            page. It is two replies, and a reader gets neither.

            So the exception stops here, in the same shape
            :meth:`_live_events` catches its own: locally, because the response has
            begun, and into the log because that is the only place left to say it.
            The three outcomes are three events, for the reason
            :func:`_log_pdf_export` gives — "it would not", "it broke" and "it
            stopped" are three different things to find in a log afterwards, and
            only the middle one is this server failing at what it was asked.

            **The two outcomes that are not a stop hand the lock back**, and that
            is the other half of the reservation :meth:`_shutdown` took. A server
            that answered "已關閉", kept serving, and then refused every launch
            for the rest of its life would be a worse failure than the one the
            reservation prevents — and the only sign of it would be a busy page
            on a server with nothing running.
            """
            if stop is None:
                log.warning(
                    "server_stop_unavailable",
                    SOURCE_SERVER,
                    "這個 handler 沒有可停止的伺服器，只送出了關閉頁面。",
                )
                lock.release_stop()
                return
            log.info(
                "shutdown_requested", SOURCE_SERVER, "已送出關閉頁面，正在停止監聽"
            )
            try:
                stop()
            except Exception as exc:  # noqa: BLE001 - the boundary is the point
                log.error(
                    "server_stop_failed",
                    SOURCE_SERVER,
                    "關閉頁面已送出，但停止監聽失敗（{}：{}）。"
                    "伺服器可能還在跑，請看這一行之後有沒有 server_stop。".format(
                        type(exc).__name__, exc
                    ),
                )
                lock.release_stop()

        # -- sending -------------------------------------------------------

        def _not_found(self, path, what):
            # The path is recorded because it is the whole diagnostic; the query
            # string is not, because that is what the user typed.
            log.warning("request_not_found", SOURCE_REQUEST, path)
            self._send_page(
                404,
                # A page about no run carries the newest report like the rest of
                # the site (Spec R-002). This page is assembled from a sentence
                # rather than from page data, so the Data Root is this route's to
                # hand over; the boundary above renders the same page with
                # nothing handed over, because that reply must not read anything.
                pages.render_not_found_page(
                    what, report_run=views.latest_report_run(root)
                ),
            )

        def _redirect(self, location, status=SUBMISSION_REDIRECT_STATUS):
            self.send_response(status)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            self.end_headers()

        def _send_page(self, status, html, policy=CONTENT_SECURITY_POLICY):
            self._send(status, HTML_CONTENT_TYPE, html.encode("utf-8"), policy=policy)

        def _send_json(self, status, payload):
            self._send(
                status,
                JSON_CONTENT_TYPE,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )

        def _send(self, status, content_type, body, policy=CONTENT_SECURITY_POLICY):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", policy)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            """Silence the access log; this server keeps its own record."""

        def log_error(self, message_format, *args):
            """Route the protocol-level complaints into the web app's own log."""
            log.warning(
                "request_error",
                SOURCE_REQUEST,
                message_format % args if args else message_format,
            )

    return WebappHandler


def _log_settings(log, outcome, rules_path):
    """Record one save under the state it ended in, never as a bare 'happened'.

    Three events rather than one, because "the rules changed", "an edit was
    refused" and "the page is locked" are three different things to find in a
    log afterwards.
    """
    if outcome.state == settings.SAVED:
        log.info("settings_saved", SOURCE_SETTINGS, "已寫入 {}".format(rules_path))
        return
    if outcome.state == settings.LOCKED:
        log.warning("settings_locked", SOURCE_SETTINGS, outcome.message)
        return
    log.warning("settings_refused", SOURCE_SETTINGS, outcome.message)


def _log_pdf_export(log, result, run_id):
    """Record one export under the state it ended in, never as a bare 'happened'.

    Three events, because the outcomes are three different things to find in a log
    afterwards: a run gained two files, this server declined to do it (nothing to
    convert yet, or the PDFs are already there), and a conversion broke. Only the
    last is this server's failure, and only it is an ``ERROR``.
    """
    message = "{}：{}".format(run_id, result.message)
    if result.ok:
        log.info("pdf_exported", SOURCE_PDF, message)
        return
    if result.state in PDF_EXPORT_DECLINED:
        log.warning("pdf_export_refused", SOURCE_PDF, message)
        return
    if result.state == pdf_export.RUN_MISSING:
        log.warning("pdf_export_no_such_run", SOURCE_PDF, message)
        return
    log.error("pdf_export_failed", SOURCE_PDF, message)


def _run_segments(path):
    """Return what follows ``/run/`` in ``path``, or ``[]`` for any other path.

    One reading for both methods. ``GET`` resolves a run's page and its two files
    through this, and ``POST`` resolves the export, so "which run is this URL
    about" is decoded once rather than twice in ways that could drift.
    """
    segments = [unquote(segment) for segment in path.split("/")[1:]]
    if len(segments) < 2 or segments[0] != "run":
        return []
    return segments[1:]


def _names_the_export(segments):
    """Whether these ``/run/`` segments are the export endpoint's, not a file's."""
    return len(segments) == 2 and segments[1] == EXPORT_PDF_SEGMENT


def artifact_with_site_nav(artifact, nav):
    """Return one offline page's bytes with ``nav`` after its opening ``<body>``.

    Bytes in, bytes out. A page this server did not write is never decoded,
    re-encoded or reflowed on the way through — the only bytes that move are the
    ones the opening tag was already followed by — so a reader gets that file
    plus one element and nothing else, and the file itself is not opened for
    writing here or anywhere else in this module (ADR 0007).

    **A page whose opening tag cannot be found comes back exactly as it
    arrived**, and "cannot be found" is deliberately wider than "is not there":
    see :func:`body_tag_end`. The navigation is something the response adds; a
    page that cannot take it is still that reader's page, and either refusing to
    serve one or guessing at a position would trade what was asked for against
    what was added.
    """
    end = body_tag_end(artifact)
    if end is None:
        return artifact
    return artifact[:end] + nav.encode("utf-8") + artifact[end:]


def body_tag_end(page):
    """Return the offset just past the first real ``<body ...>``, or ``None``.

    A scan rather than a pattern, because two shapes of ordinary HTML defeat any
    pattern short enough to read. ``<!-- <body> -->`` is a comment and not a
    place to put anything, so comments are stepped over whole. And a ``>`` inside
    a quoted attribute value does not end a tag — ``<body data-note="1 > 0">``
    ends six bytes later than it looks like it does — so the tag's end is found
    by :func:`_start_tag_end`, which tracks the quoting.

    ``None`` is the answer whenever this cannot say where the tag ends *for
    certain*: no ``<body>`` at all, a comment that is never closed, an attribute
    list that runs off the end of the file. Every one of those is a page that
    goes out untouched, which is the direction this has to fail in — inserting
    into the middle of somebody's markup is worse than not inserting at all.
    """
    at = page.find(b"<")
    while at != -1:
        if page.startswith(COMMENT_OPEN, at):
            at = _after_comment(page, at)
            continue
        if _opens_the_body(page, at):
            return _start_tag_end(page, at)
        at = page.find(b"<", at + 1)
    return None


def _after_comment(page, at):
    """Where the comment beginning at ``at`` leaves off, or ``-1`` if it never does."""
    closed = page.find(COMMENT_CLOSE, at + len(COMMENT_OPEN))
    if closed == -1:
        return -1
    return page.find(b"<", closed + len(COMMENT_CLOSE))


def _opens_the_body(page, at):
    """Whether the ``<`` at ``at`` begins a ``<body>`` start tag."""
    if page[at : at + len(BODY_OPEN)].lower() != BODY_OPEN:
        return False
    return page[at + len(BODY_OPEN) : at + len(BODY_OPEN) + 1] in BODY_OPEN_DELIMITERS


def _start_tag_end(page, at):
    """Where the start tag beginning at ``at`` ends, with quoted values honoured."""
    quote_byte = None
    for index in range(at, len(page)):
        byte = page[index : index + 1]
        if byte == quote_byte:
            quote_byte = None
        elif quote_byte is not None:
            continue
        elif byte in QUOTES:
            quote_byte = byte
        elif byte == b">":
            return index + 1
    return None


def run_artifacts(data_root, run_id):
    """Which of one run's two linked files are on disk, or ``None`` for no run.

    The narrowest question the injected navigation asks, and the whole of it: two
    names and two booleans, answered by looking. It is deliberately not
    :func:`~.views.run_data`, which answers "everything about this run" by
    reading four records and a JSONL — a page that is being served as bytes has
    no use for a seat view, and a run whose ``votes.json`` had gone strange would
    have taken the reader's report down with it.
    """
    run_dir = resolve_run_dir(data_root, run_id)
    if run_dir is None:
        return None
    return {name: (run_dir / name).is_file() for name in views.LINKED_ARTIFACTS}


def site_nav_fragment(run_id, artifacts, current=None):
    """Return the five tabs, and the style that paints them, as one block of HTML.

    Self-contained because of where it lands: a page written months ago, whose
    stylesheet this module cannot know. The block therefore brings its own paint
    — one ``<style>`` whose every selector begins with :data:`SITE_NAV_CLASS` and
    whose every value is a token declared *on the bar itself* — so a bar dropped
    on a run from the old paper-white build looks like the rest of this site
    (Spec R-004), and a run from the current build is painted by exactly the same
    block rather than by whichever of two stylesheets happens to win.

    **Zero script and zero inline event handler**, which is what lets the reply
    keep ``script-src 'none'``. ``<style>`` is not script and the artifact policy
    already allows an inline one; nothing here is an ``on*`` attribute, a
    ``javascript:`` target, or anything a page could be made to execute.

    ``artifacts`` is :func:`run_artifacts`'s answer and ``None`` is accepted:
    a run with nothing to open gets the announced-but-inert tabs any page with
    nothing to open gets, rather than a bar that is shorter than the one on the
    page before it. ``current`` is the file being read, so its tab is marked as
    the page it is.
    """
    tabs = "".join(
        _site_nav_tab(target, label, extra)
        for target, label, extra in _site_nav_entries(run_id, artifacts, current)
    )
    return '{}<nav class="{}" aria-label="{}">{}</nav>'.format(
        _site_nav_style(), SITE_NAV_CLASS, escape(SITE_NAV_LABEL, quote=True), tabs
    )


def _site_nav_entries(run_id, artifacts, current):
    """``(target, label, extra attributes)`` for the five tabs, in the order shown.

    The three tables this reads are :mod:`~hoya_market_agents.webapp.pages`'s, so
    which pages this site has is answered in one place and the bar on an offline
    page cannot list a page the header does not. ``target`` is ``None`` for a tab
    with nothing behind it.
    """
    on_disk = artifacts or {}
    entries = [(target, label, "") for target, label in pages.BROWSE_TABS]
    entries += [
        (
            _artifact_path(run_id, artifact) if on_disk.get(artifact) else None,
            label,
            ' aria-current="page"' if artifact == current else "",
        )
        for label, artifact in pages.RUN_ARTIFACT_TABS
    ]
    admin_target, admin_label = pages.SETTINGS_TAB
    entries.append(
        (admin_target, admin_label, ' class="{}"'.format(SITE_NAV_ADMIN_CLASS))
    )
    return entries


def _site_nav_tab(target, label, extra):
    """One tab: a link where there is something to open, and an inert one until then.

    The unavailable one is not an ``<a>`` for the reason
    :func:`~.pages._artifact_tab` gives: a link that announces itself as disabled
    and then navigates anyway is worse than no link, and with no ``href`` and no
    ``tabindex`` this one is neither followed nor tabbed to.
    """
    if target is None:
        return '<span role="link" aria-disabled="true">{}</span>'.format(escape(label))
    return '<a href="{}"{}>{}</a>'.format(
        escape(target, quote=True), extra, escape(label)
    )


def _artifact_path(run_id, name):
    """The URL of one run's own file, spelled the way the site's header spells it."""
    return "/run/{}/{}".format(quote(str(run_id), safe=""), name)


def _site_nav_style():
    """The block that paints the bar, and provably nothing else on the page."""
    bar = ".{}{{{}{}}}".format(SITE_NAV_CLASS, _site_nav_tokens(), SITE_NAV_BAR)
    inside = "".join(
        "{}{{{}}}".format(_scoped(parts), declarations)
        for parts, declarations in SITE_NAV_RULES
    )
    return "<style>{}{}</style>".format(bar, inside)


def _scoped(parts):
    """Selectors for what is inside the bar, each one rooted at the bar's class."""
    return ",".join(".{} {}".format(SITE_NAV_CLASS, part) for part in parts)


def _site_nav_tokens():
    """The site's tokens, declared on the bar rather than on ``:root``.

    Which is the whole of "scoped": custom properties inherit, so declaring them
    here paints everything inside the bar and leaves every other element on the
    page reading whatever its own sheet said — including a page that declares the
    same names with different values, and a page that declares none of them.
    """
    return "".join(
        "--{}:{};".format(name.replace("_", "-"), value)
        for name, value in sorted({**PALETTE, **SCALE}.items())
    )


def _room_payload(
    room, messages, run_id, offset, run_dir=None, question=None, clock=None
):
    """What one frame carries: the new messages, and the counts they changed."""
    question = question if isinstance(question, dict) else {}
    elapsed_ms = (
        live.authoritative_elapsed_ms(run_dir, question, clock=clock)
        if run_dir is not None
        else room.latest_elapsed_ms()
    )
    question_type = question.get("question_type")
    return {
        "run_id": run_id,
        "messages": list(messages),
        "seats": room.seat_views(),
        "tally": room.tally_views(),
        "round": room.latest_round(),
        "elapsed_ms": elapsed_ms,
        "debate_started": room.debate_started or (
            live.debate_start_remaining_ms(elapsed_ms, question_type) is None
        ),
        "debate_start_remaining_ms": live.debate_start_remaining_ms(
            elapsed_ms, question_type, room.debate_started
        ),
        "cursor": live.make_cursor(run_id, offset),
    }


def _form_body(handler):
    """Return the submitted form as ``parse_qs`` would, refusing an oversized one."""
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return {}
    if length <= 0 or length > MAX_FORM_BYTES:
        return {}
    raw = handler.rfile.read(length)
    return parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)


class ServerStop:
    """The one way a request can end the serving loop.

    It exists because of an ordering that cannot be satisfied in one step: the
    handler class has to be built before the server, and the server is what a
    request needs in order to stop it. This holds the empty slot in between, so
    the handler is handed a plain callable and never reads ``self.server`` — which
    is what keeps every route in this module drivable by a test with no socket.

    :meth:`request` **waits for the loop to end**, because that is what
    ``BaseServer.shutdown`` does. It is called from the thread serving one request
    while ``serve_forever`` runs in another, which is the only arrangement it is
    sound in: the loop is free to notice and return, and the reply this request
    already sent is on the wire either way. Calling it on a server that never
    started serving would wait for an event nothing is going to set, so nothing
    here calls it on one.

    Two rules come with it, and both are :func:`create_webapp_server`'s to keep:
    :meth:`attach` before anything is served, and one of these per server. An
    unattached one raises rather than quietly doing nothing — a stop button that
    answered "已關閉" and left the server running would be the one failure worth
    hiding least, and there is no state of this class that can produce it.
    """

    def __init__(self):
        self._server = None

    def attach(self, server):
        """Name the bound server whose loop :meth:`request` is allowed to end."""
        self._server = server

    def request(self):
        """Ask the serving loop to stop, and return once it has."""
        self._server.shutdown()


def create_webapp_server(
    data_root, log, host=DEFAULT_HOST, port=DEFAULT_PORT, stream=None, spawn=None
):
    """Bind the local server, or raise :class:`WebappError` saying why not.

    ``log`` is required and must already be open: the failure this function can
    have is exactly the one that has to be recorded.

    Every server this function returns can be stopped through
    ``POST /shutdown``: it is the one place that owns both halves of that wiring,
    so there is no way to bind a server here whose stop button is a page with
    nothing behind it. A bind that fails attaches nothing, because there is
    nothing to attach.
    """
    if not 0 <= port <= 65535:
        raise WebappError("port {} 不在 0–65535 範圍內。".format(port))
    stop = ServerStop()
    handler = webapp_handler_class(
        data_root, log, stream=stream, spawn=spawn, stop=stop.request
    )
    try:
        server = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        message = _bind_failure(host, port, exc)
        log.error("server_start_failed", SOURCE_SERVER, message)
        raise WebappError(message) from exc
    stop.attach(server)
    return server


def _bind_failure(host, port, exc):
    if exc.errno == errno.EADDRINUSE:
        return (
            "{}:{} 已被占用，webapp 不會自動改用其他埠。"
            "請先關掉占用該埠的程式，或用 --port 指定另一個埠。".format(host, port)
        )
    return (
        "無法在 {}:{} 啟動 webapp（{}：{}）。"
        "請確認該埠可用，或用 --port 指定另一個埠。".format(
            host, port, type(exc).__name__, exc
        )
    )


def serve_webapp(data_root, port=DEFAULT_PORT, host=DEFAULT_HOST, clock=None, out=None):
    """Open the log, bind, and serve until interrupted.

    Raises :class:`~hoya_market_agents.webapp.log.WebappLogError` when the log
    cannot be prepared and :class:`WebappError` when the port cannot be bound;
    both are the caller's to report and neither leaves a half-started server.
    """
    log = open_webapp_log(data_root, clock=clock)
    try:
        log.info("server_start", SOURCE_SERVER, "準備在 {}:{} 啟動".format(host, port))
        server = create_webapp_server(data_root, log, host=host, port=port)
    except BaseException:
        log.close()
        raise
    url = "http://{}:{}/".format(host, server.server_address[1])
    log.info("server_listening", SOURCE_SERVER, url)
    if out is not None:
        print("AI agnets debating chamber（首頁）：{}".format(url), file=out)
        print("歷史與命中率：{}history".format(url), file=out)
        print("按 Ctrl+C 停止。", file=out)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log.info("server_stop", SOURCE_SERVER, "已停止 {}".format(url))
        log.close()
    return url
