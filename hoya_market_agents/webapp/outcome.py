"""Checking a finished prediction against what the market actually did.

This is the only module in the web app that **asks for** a live price, and the
only one in it that writes a run artifact. The module that actually opens the
socket is :mod:`~hoya_market_agents.quote_api_client`; this one holds the only
call site outside that client's own tests, which is a different claim and the
one worth enforcing here — a second caller would be a second policy about when a
price may be fetched and what a failed fetch means. Both facts are deliberate and
both are enforced from outside: ``tests/test_webapp.py`` scans every source file
in the package and asserts that the set naming
:mod:`~hoya_market_agents.quote_api_client` is exactly this module and the
client itself, with two false-positive checks so a scan that matched nothing
could not pass for a boundary that holds.

It writes one file that is **not** a run artifact:
:data:`SWEEP_CURSOR_NAME`, beside ``runs/`` rather than inside it. That is this
module's own bookkeeping about where the last sweep stopped and it holds nothing
a run recorded. Losing one costs one pass its place in the rotation; losing the
*ability to write* one costs a pass its cap instead, which is a slow page rather
than a run that never gets checked. See the comment on
:data:`SWEEP_CURSOR_NAME` for the invariant both of those are protecting.

**Nothing in the research pipeline can reach this.** A sweep starts from an HTTP
request to the statistics page and from nowhere else — there is no thread, no
timer and no hook in the run controller — so the archive-then-stop-searching
boundary a run enforces is never in the same call stack as a price lookup.

The record
----------

One run gets at most one ``outcome.json``, ever. It is written through
``run_store.RunDirectory``, whose ``os.link`` refuses a name that already
exists, so "write once" is the filesystem's answer rather than a check this
module makes and then hopes nobody raced. The check that happens first is only
there to produce the right sentence, and there are three of those because there
are three situations:

* the record is there and reads — already checked, and the verdict is quoted;
* the record is not there — this write may go ahead;
* the record is there and will not read — **not** the same as the second one.
  Treating it as "not checked yet" would invite this module to overwrite
  something that may hold the only copy of a real answer, so it is reported as
  what it is.

What is never written
---------------------

Three kinds of situation end without an invented verdict, and they do not end
the same way:

* **a price this module will not judge on.** Nothing is written at all: the run
  stays pending, the web app log says so, and the next pass asks again. Three
  situations, one outcome, because to a pending run they are the same outcome —
  the quote client refused, the injected quote callable raised something else
  (which costs that run and no other; see :func:`_priced_payload`), or a
  ``close`` came back that :func:`~hoya_market_agents.quote_api_client.
  is_usable_price` will not accept. **The last one is checked here rather than
  trusted from the seam**, because ``outcome.json`` cannot be corrected and
  ``float(True)`` is a perfectly ordinary-looking ``1.0``.
* **anything in** :data:`UNVERIFIABLE_REASONS`. Recorded as
  :data:`~hoya_market_agents.run_index.OUTCOME_UNVERIFIABLE` with its reason,
  which is a real answer distinct from "wrong". That table is the authority for
  which situations those are and this sentence is deliberately not a second copy
  of it — a hand-kept list here would drift the moment one is added.
* **a run nobody can date.** Left alone entirely, because a run that does not
  say when its period ended cannot be said to have ended.

What is never written yet
-------------------------

A run that has not finished, and one whose period has not run out, are refused
by **both** ways in — the sweep leaves them alone, and
:func:`record_manual_outcome` says which of the two conditions failed. Write-once
is what makes that a rule rather than a nicety: a verdict on a prediction that
has not happened is one no later record can correct, so the earliest moment it
may be written is the moment the prediction is over. A run nobody can date is
refused on both paths for the same reason.

``unverifiable`` is not counted in the hit rate. See
:func:`~hoya_market_agents.run_index.outcome_summary` for the denominator.
"""

import json
import os
import tempfile
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..question_package import MARKET_STANCES
from ..quote_api_client import (
    QuoteUnavailableError,
    available_close_day,
    daily_close,
    is_decimal_numeral,
    is_quotable,
    is_usable_price,
)
from ..run_index import (
    FINALIZED_MARKER_NAME,
    OUTCOME_HIT,
    OUTCOME_MISS,
    OUTCOME_RECORD_NAME,
    OUTCOME_UNREADABLE,
    OUTCOME_UNVERIFIABLE,
    OUTCOME_VERDICTS,
    RunIndexError,
    outcome_verdict,
    query_runs,
    upsert_run,
)
from ..run_store import ArtifactAlreadyExistsError, RunDirectory, resolve_run_dir

OUTCOME_SCHEMA_VERSION = 1

# How many runs one pass may fetch prices for. A page that reaches a network
# has to be bounded by something other than how many runs a Data Root happens
# to hold: twenty prices at the client's timeout is a page that is slow, two
# thousand is a page that never answers. What is left over is picked up by the
# next visit, because a run stays pending until it is recorded.
MAX_SWEEP_RUNS = 20

# -- the fairness invariant, what it is conditional on, and its machinery -----
#
# **Every pending run is eventually checked** — under the preconditions named
# next, which are part of the claim rather than a disclaimer bolted to it. This
# used to be written as an unconditional sentence while several known situations
# broke it, and a guarantee that is quietly conditional is worse than a smaller
# one that holds: nobody can tell which of the two they are relying on.
#
# 在**單一 server**、``limit > 0``、index 可讀且完整、行程不中止、log 可寫、且
# cursor 能在後續 pass 正常讀寫的前提下，rotation 提供 eventual coverage。以下
# **不在此保證內**：多個 server 各自持有自己的 cursor、行程中途死亡、run 沒有進
# 到 index、非正的 ``limit``、以及寫 ``outcome.json`` 或寫 log 持續失敗。
#
# A cap alone does not keep it true. Pending runs are taken newest first, so a
# handful of recent runs whose quote keeps failing fill the cap on every pass and
# an older run behind them is never reached at all, however many times the page
# is opened. Oldest-first only moves the starvation to the other end. So the
# order rotates: each pass resumes where the last one stopped, and cap plus
# rotation together reach every pending run in at most ``ceil(pending / limit)``
# passes.
#
# Remembering where a pass stopped is what the cursor is for, and it has exactly
# two ways to fail. Both used to end in the same silent place — start again from
# the head of the queue, for ever — so both are answered here:
#
# * **The run it names is no longer pending** (somebody recorded it by hand).
#   The cursor names a *position in the run-id order*, not a list entry, so the
#   next pass resumes after that position whether or not the run is still there.
#   See :func:`_rotated`.
# * **It cannot be written.** Then the next pass has no memory, so the pass that
#   discovered this covers the whole remaining rotation itself, cap lifted. See
#   :func:`sweep_due_runs`. The cost is stated there and it is real.
#
# A third situation used to belong on that list and no longer does, which is why
# it is named here rather than quietly dropped: **one run's quote raising
# something that is not** :class:`~hoya_market_agents.quote_api_client.
# QuoteUnavailableError`. ``http.client.IncompleteRead`` from a connection cut
# mid-body was the ordinary way to reach it. It travelled out of
# :func:`sweep_due_runs` before :func:`_write_sweep_cursor` ever ran, so the pass
# ended, the cursor never moved, and every following pass began at the same run
# and died there — the exact starvation the rotation exists to remove, entered
# through a callable this module does not own. It is now isolated per run in
# :func:`_priced_payload`: counted as a quote failure, that run left pending, the
# rotation carried on.
#
# A fourth arrived by the same door and is worth naming because of who opened it:
# **the guard added to close the third one.** :func:`_priced_payload` asks
# :func:`~hoya_market_agents.quote_api_client.is_usable_price` about each end
# *after* the quotes are in hand, which is outside the ``except`` that isolates
# the run — so when that predicate itself raised, the pass ended before the
# cursor again. It did not need a hostile caller: ``10**1000`` is an ordinary
# :class:`int` that :func:`math.isfinite` cannot convert to :class:`float`. The
# answer was not to widen the ``except`` — that would relabel a broken guard as a
# quote failure — but to make the predicate **total**, so a guard on this path
# has no way to end a pass. The general form: *a check that runs outside the
# isolation is a starvation route, so it must not be able to raise.*
#
# What still ends a pass early is a failure to **write**: an ``outcome.json``
# whose write raises, or a log that cannot be written. The second is deliberate
# and belongs to :mod:`~hoya_market_agents.webapp.log` — a server that cannot
# record what it did stops rather than continues quietly; see
# :meth:`OutcomeCheck.run`. In both cases nothing was recorded, so every run
# involved is still pending and a later pass meets it again.
#
# The file lives beside ``runs/`` rather than inside one, because it is this
# module's own bookkeeping and not a run artifact.
SWEEP_CURSOR_NAME = "outcome-sweep-cursor.json"
SWEEP_CURSOR_FIELD = "after_run_id"

# Which ballot position claims which way the price goes. Drawn from
# ``question_package.MARKET_STANCES`` — the market ballot is the only one whose
# words name a direction at all — and deliberately smaller than it: ``neutral``
# is a real answer to the question asked, but it is not a claim a price can
# settle, so it has no entry here and lands as ``unverifiable``. Every other
# ballot in this project (comparison, event, open proposition) has no entry
# either, for the same reason.
DIRECTION_UP = "up"
DIRECTION_DOWN = "down"
DIRECTION_FLAT = "flat"
STANCE_DIRECTIONS = {
    MARKET_STANCES[0]: DIRECTION_UP,
    MARKET_STANCES[1]: DIRECTION_DOWN,
}

# Why a run could not be checked against a price. A closed set, because each one
# is a different thing to tell a reader and a lump labelled "could not check"
# would be the same sentence for three different situations.
REASON_NO_QUOTE_SOURCE = "asset_class_has_no_quote_source"
REASON_NOT_ONE_ASSET = "not_a_single_asset"
REASON_STANCE_NOT_DIRECTIONAL = "stance_is_not_directional"
UNVERIFIABLE_REASONS = {
    REASON_NO_QUOTE_SOURCE: "這一題的資產類別沒有可對照的公開報價來源，只能人工輸入結果。",
    REASON_NOT_ONE_ASSET: "這一題不是單一標的的漲跌題，沒有唯一一條價格可以對照，只能人工輸入結果。",
    REASON_STANCE_NOT_DIRECTIONAL: "採納的立場沒有對應的價格方向，無法用漲跌判定，只能人工輸入結果。",
}

# What one attempt to write a record ended as.
WRITTEN = "written"
ALREADY_RECORDED = "already_recorded"
RECORD_UNREADABLE = "record_unreadable"
NO_SUCH_RUN = "no_such_run"
REFUSED = "refused"
WRITE_STATES = (WRITTEN, ALREADY_RECORDED, RECORD_UNREADABLE, NO_SUCH_RUN, REFUSED)

SOURCE_OUTCOME = "webapp.outcome"
MANUAL_SOURCE = "manual"

_QUESTION_RECORD = "question.json"
_VOTES_RECORD = "votes.json"


class OutcomeWrite(namedtuple("OutcomeWrite", "state run_id verdict message")):
    """What one attempt to record an outcome did, and the sentence for a reader.

    ``verdict`` is what is on disk afterwards when there is one — the verdict
    just written, or the one that was already there and stopped this write. It
    is ``None`` for a state where no record exists.
    """

    @property
    def ok(self):
        return self.state == WRITTEN


class OutcomeCheck:
    """The expiry sweep, with every edge a test needs to hold still.

    ``now`` reads the clock, ``quote`` fetches one close, ``limit`` caps one
    pass and ``log`` receives what happened. The defaults are the real clock,
    the real quote client and no cap beyond :data:`MAX_SWEEP_RUNS`; a test
    replaces all four and never opens a socket.
    """

    def __init__(self, now=None, quote=None, limit=MAX_SWEEP_RUNS, log=None):
        self.now = now or _utc_now
        self.quote = quote or daily_close
        self.limit = limit
        self.log = log

    def run(self, data_root):
        """Sweep once and return the summary, or ``None`` if the sweep failed.

        **Every exception the sweep itself raises** is caught here and none
        reaches the page. This is a boundary in the same sense as the server's
        request guard: the statistics page's job is to show what is recorded, and
        a sweep that broke in a new way must not take that page down with it.
        Nothing is swallowed silently — the failure is logged with its type and
        message — and nothing is invented: a run whose check failed stays pending
        and is tried again on the next visit.

        **This is the last boundary, not the first one, and it must not be read
        as the one that handles a bad quote.** It cannot be: an exception that
        reaches here ended the whole pass, so it ended it *before*
        :func:`_write_sweep_cursor` ran and the next pass will begin at the same
        run and die in the same place. Logging that and returning ``None`` looks
        like handling and is exactly what let it repeat in silence. One run's
        quote failing is therefore isolated where it happens, in
        :func:`_priced_payload`, and what still arrives here is a sweep that
        could not proceed at all — an unreadable index in a way
        :class:`~hoya_market_agents.run_index.RunIndexError` does not cover, a
        ``limit`` that is not a number, a record that could not be written.

        **What this does not catch is the logging.** Both ``_record`` calls below
        are outside the ``try``, so a log that cannot be written raises
        :class:`~.log.WebappLogError` straight out of here, including from the
        branch that is reporting a sweep failure. That is deliberate and belongs
        to :mod:`~hoya_market_agents.webapp.log`, whose whole policy is that a
        server which cannot record what happened stops rather than continues
        quietly: catching it here would turn "the sweep failed and nobody will
        ever know" into the normal case. The server's own request guard is what
        turns it into a 500 instead of a traceback in the console.
        """
        try:
            summary = sweep_due_runs(
                data_root,
                now=self.now(),
                quote=self.quote,
                log=self.log,
                limit=self.limit,
            )
        except Exception as exc:  # noqa: BLE001 - the boundary is the point
            _record(
                self.log,
                "error",
                "outcome_sweep_failed",
                "到期檢查失敗（{}：{}）；沒有任何 run 被判定。".format(
                    type(exc).__name__, exc
                ),
            )
            return None
        if _worth_reporting(summary):
            _record(self.log, "info", "outcome_sweep", _sweep_sentence(summary))
        return summary


def sweep_due_runs(data_root, now, quote=None, log=None, limit=MAX_SWEEP_RUNS):
    """Record an outcome for every run whose analysis period has run out.

    Only runs the index shows as unrecorded are considered, and each one's
    record is re-read from disk before anything is written — the index is
    derived data and may be behind, and "behind" here would mean overwriting a
    verdict.

    Returns a count for every way one run can end, so a caller can say what
    happened without inferring it:

    ``not_due``
        Its period has not run out. Left alone, and does not use up ``limit``.
    ``no_deadline``
        Its records do not say when the period started or how long it was, so
        there is no date to compare against. Left alone — a run that cannot be
        dated has not been shown to have expired.
    ``already``/``unreadable``
        A record is already there, or is there and will not read.
    ``unverifiable``
        Recorded as :data:`~hoya_market_agents.run_index.OUTCOME_UNVERIFIABLE`
        with a reason from :data:`UNVERIFIABLE_REASONS`. No price was asked for.
    ``quote_failed``
        A price could not be had, or could be had and was not a price. **Nothing
        is written**: the run stays pending and the next pass tries again. All
        three ways of getting here are this one count because they are one thing
        to a reader of the number — no verdict, run still pending — and the log
        tells them apart by event: ``outcome_quote_failed`` (the client refused),
        ``outcome_quote_unexpected`` (the injected quote callable raised
        something else, isolated to this run) and ``outcome_quote_not_a_price``
        (a ``close`` that :func:`~hoya_market_agents.quote_api_client.
        is_usable_price` will not accept).
    ``recorded``
        Records written, of any verdict.
    ``checked``
        Due runs this pass actually looked at. ``limit`` caps it on an ordinary
        pass, and **not** on one whose cursor could not be written — see below,
        because a caller reading this number as "at most ``limit``" would be
        wrong exactly when the sweep is slowest.

    ``index_unavailable`` comes back ``True`` when there is no readable index to
    look in. That is not an empty sweep and is not reported as one — nothing was
    examined, and the counts are all zero because nothing could be.

    **Where a pass starts.** Pending runs are put in run-id order, newest first,
    and rotated so this pass begins just after where the previous one stopped
    (:data:`SWEEP_CURSOR_NAME`). The order is taken from the ids themselves
    rather than accepted from the index, because the rotation is only fair if the
    sequence it rotates is the same sequence every pass; a row whose indexed date
    disagreed with its id would otherwise make "resume after this position" mean
    two different things on two passes.

    **When the cursor cannot be written.** The next pass would then have no
    memory and would start from the head again, which is the starvation the
    rotation exists to remove — so this pass does not stop at ``limit``. It
    carries on through the rest of the rotation with the cap lifted, and says so
    in the log. **That is a slow page, deliberately**: in the worst case one
    quote round trip per pending run, so the statistics page can take
    ``pending × the quote client's timeout`` before it answers. A page that is
    slow while its Data Root is broken is the trade being made; a page that is
    quick and quietly never scores the oldest run is the one being refused.
    """
    quote = quote or daily_close
    summary = _empty_summary()
    try:
        rows = query_runs(data_root)
    except RunIndexError:
        summary["index_unavailable"] = True
        return summary
    pending = sorted(
        (row["run_id"] for row in rows if row["outcome"] is None), reverse=True
    )
    rotation = _rotated(pending, _read_sweep_cursor(data_root, log))
    examined = None
    unreached = []
    for position, run_id in enumerate(rotation):
        if summary["checked"] >= limit:
            unreached = rotation[position:]
            break
        _sweep_one(data_root, run_id, now, quote, log, summary)
        examined = run_id
    if examined is None:
        # Nothing was examined, so there is no place in the rotation to
        # remember and nothing for the next pass to be unfair about.
        return summary
    if _write_sweep_cursor(data_root, examined, log):
        return summary
    _sweep_the_rest_uncapped(data_root, unreached, now, quote, log, summary)
    return summary


def _sweep_the_rest_uncapped(data_root, unreached, now, quote, log, summary):
    """Check the runs the cap held back, because no next pass will reach them.

    Only reached when :func:`_write_sweep_cursor` failed. The cap and the cursor
    are one mechanism: the cap is safe *because* the cursor makes the next pass
    resume behind it, so a cap with no cursor is not a deferral, it is a run
    nobody ever checks. Lifting it here is what keeps "every pending run is
    eventually checked" true when the cheap way of keeping it true is gone.
    """
    if not unreached:
        return
    for run_id in unreached:
        _sweep_one(data_root, run_id, now, quote, log, summary)
    _record(
        log,
        "warning",
        "outcome_sweep_uncapped",
        "輪替游標寫不進去，這一輪改為不設上限掃完剩下的 {} 筆 pending（"
        "共檢查 {} 筆），以免它們在下一輪又被上限擋在後面而永遠等不到判定；"
        "這一輪的頁面會比平常慢。".format(len(unreached), summary["checked"]),
    )


def _rotated(run_ids, after):
    """Return ``run_ids`` beginning just after ``after``'s place, wrapping round.

    ``run_ids`` is in descending order, so "just after ``after``'s place" is the
    first id that sorts below it. **The cursor names a position, not an entry.**
    That is the difference between this and looking ``after`` up in the list: a
    run recorded by hand between two passes leaves the pending set, and a lookup
    then finds nothing and starts again from the head — which is the starvation
    the cursor is here to prevent, arriving by the one route nobody had to
    provoke. A position still exists after the thing that defined it is gone.

    ``after`` being ``None`` — no cursor yet, or one that would not read — leaves
    the order alone, the same answer as a fresh Data Root. ``after`` sorting
    above everything pending gives the whole list; sorting below everything gives
    the whole list too, having wrapped all the way round.
    """
    if after is None:
        return list(run_ids)
    resume = next(
        (position for position, run_id in enumerate(run_ids) if run_id < after),
        len(run_ids),
    )
    return list(run_ids[resume:]) + list(run_ids[:resume])


def _sweep_cursor_path(data_root):
    return Path(data_root) / SWEEP_CURSOR_NAME


def _read_sweep_cursor(data_root, log=None):
    """Return the run id the last pass stopped at, or ``None``.

    A cursor that is absent is the ordinary first pass and says nothing. Every
    other way of not getting an answer is logged, because they are not that:

    * it will not open or is not JSON;
    * it is there but is not a regular file — a directory under that name reads
      as "absent" to :meth:`~pathlib.Path.is_file` and would otherwise be the
      quietest permanent failure in this module;
    * it is JSON this module did not write. ``{}`` and ``[]`` parse perfectly
      well and carry no position, and "valid but not mine" silently resetting
      the rotation is the same lost fairness as a parse error, so it gets the
      same visibility.

    None of them stops the sweep: the pass runs from the top of the rotation,
    and — provided the *write* at the end of the pass succeeds — the cost is the
    one rotation. When the write does not succeed either, see
    :func:`_sweep_the_rest_uncapped`.
    """
    path = _sweep_cursor_path(data_root)
    if not path.is_file():
        if path.exists() or path.is_symlink():
            _record(
                log,
                "warning",
                "outcome_sweep_cursor_invalid",
                "到期檢查的輪替游標 {} 不是一般檔案，讀不出上一輪停在哪裡；"
                "這一輪從頭掃，不影響判定。".format(path),
            )
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        _record(
            log,
            "warning",
            "outcome_sweep_cursor_unreadable",
            "到期檢查的輪替游標 {} 讀不到（{}：{}）；這一輪從頭掃，不影響判定。".format(
                path, type(exc).__name__, exc
            ),
        )
        return None
    value = payload.get(SWEEP_CURSOR_FIELD) if isinstance(payload, dict) else None
    if isinstance(value, str) and value:
        return value
    _record(
        log,
        "warning",
        "outcome_sweep_cursor_invalid",
        "到期檢查的輪替游標 {} 是合法 JSON，但沒有可用的 {}（內容 {}）；"
        "這一輪從頭掃，不影響判定。".format(
            path, SWEEP_CURSOR_FIELD, _bounded(payload)
        ),
    )
    return None


def _bounded(payload, limit=120):
    """Quote an unusable cursor payload without letting it fill the log."""
    text = repr(payload)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _write_sweep_cursor(data_root, run_id, log=None):
    """Record where this pass stopped, atomically. Return whether it landed.

    Written to a uniquely named temporary file in the same directory and moved
    onto the name, so two servers sweeping at once end with one whole cursor
    rather than with half of each.

    **The return value is load bearing**, not a courtesy: ``False`` means the
    next pass will start from the head of the rotation, so the caller has to
    make this pass cover what the cap held back. It is the only signal that
    exists for that, which is why the failure is not merely logged.
    """
    if run_id is None:
        return False
    path = _sweep_cursor_path(data_root)
    text = json.dumps({SWEEP_CURSOR_FIELD: run_id}, ensure_ascii=False) + "\n"
    handle = None
    try:
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=".{}-".format(path.name),
            suffix=".tmp",
            delete=False,
        )
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, str(path))
        return True
    except OSError as exc:
        if handle is not None:
            Path(handle.name).unlink(missing_ok=True)
        _record(
            log,
            "warning",
            "outcome_sweep_cursor_failed",
            "到期檢查的輪替游標 {} 寫不進去（{}：{}）；下一輪沒有位置可以接續，"
            "因此這一輪改為不設上限掃完剩下的 pending。".format(
                path, type(exc).__name__, exc
            ),
        )
        return False


def _sweep_one(data_root, run_id, now, quote, log, summary):
    run_dir = resolve_run_dir(data_root, run_id)
    if run_dir is None:
        summary["missing_dir"] += 1
        return
    recorded = outcome_verdict(run_dir)
    if recorded == OUTCOME_UNREADABLE:
        summary["unreadable"] += 1
        return
    if recorded is not None:
        summary["already"] += 1
        return
    question = _read_json(run_dir / _QUESTION_RECORD)
    started, due = _period(question)
    if due is None:
        summary["no_deadline"] += 1
        return
    if now < due:
        summary["not_due"] += 1
        return
    summary["checked"] += 1
    stance = _field(_read_json(run_dir / _VOTES_RECORD), "adopted_stance")
    reason = _unverifiable_reason(question, stance)
    if reason is not None:
        payload = _unverifiable_payload(run_id, question, stance, reason, due, now)
        summary["unverifiable"] += 1
    else:
        payload = _priced_payload(run_id, question, stance, started, due, now, quote, log)
        if payload is None:
            summary["quote_failed"] += 1
            return
    written = _write_record(data_root, run_id, run_dir, payload, log)
    if written.ok:
        summary["recorded"] += 1
    elif written.state == RECORD_UNREADABLE:
        summary["unreadable"] += 1
    else:
        summary["already"] += 1


def _priced_payload(run_id, question, stance, started, due, now, quote, log):
    """Return the record for a run two prices settle, or ``None`` if they do not.

    Both prices are fetched before anything is judged, so a settle price that
    cannot be had leaves no half-record behind claiming a baseline nobody
    compared against.

    Each end is asked for as the last day that class had **already printed** a
    close for at that instant (:func:`~hoya_market_agents.quote_api_client.
    available_close_day`), not as the calendar date the instant falls on. The
    two are different, and the difference is a look-ahead: a US run created at
    02:00Z would otherwise take its baseline from a close that printed at 20:00Z
    the same day — eighteen hours of what the prediction was supposed to
    forecast, folded into the number it is scored from.

    **``quote`` is a seam, and a seam is a claim about the caller.** Whatever it
    hands back is checked here before anything is judged or written, for the one
    reason that outranks how well behaved the default client is: ``outcome.json``
    is written once and cannot be corrected. Both ends are checked, and the check
    on each is the same
    :func:`~hoya_market_agents.quote_api_client.is_usable_price` the fetched close
    and the hand-typed price already pass — not the text grammar that precedes it
    on those two paths, because a ``close`` from a quote client was never text.
    So a ``close`` of ``True`` — which ``float`` turns into the perfectly ordinary
    price ``1.0``, and ``>`` compares happily against — settles no direction here.
    It costs two lines at the write boundary of a permanent record; getting it
    wrong costs the record.

    **Whatever ``quote`` raises costs this run and no other.** Only
    :class:`~hoya_market_agents.quote_api_client.QuoteUnavailableError` is a
    failure this module recognises; anything else — a transport error the client
    did not wrap, a stub in a test, a bug — used to travel out of
    :func:`sweep_due_runs` and end the whole pass before its cursor was written,
    so the next pass began at the same run and the rotation never moved. That is
    the starvation the cursor exists to prevent, arriving through a callable this
    module does not own. It is caught here, counted as a quote failure, and the
    rotation carries on.

    **The price check below sits outside that ``except``, so it must not raise.**
    It is asked after the quotes are in hand, which puts it past the only net in
    this function — and an exception from a *guard* ends the pass in exactly the
    place the paragraph above describes, cursor unwritten, rotation stuck. It did:
    ``is_usable_price`` was handed ``10**1000``, an ordinary :class:`int` that
    :func:`math.isfinite` cannot convert, and the starvation came back through the
    guard added to close it. That is why that predicate is **total** rather than
    merely correct about numbers; see its docstring. Moving these two lines inside
    the ``except`` would not be the fix — it would relabel a broken guard as a
    quote failure and hide it.
    """
    asset_class = _field(question, "asset_class")
    symbol = _assets(question)[0]
    try:
        baseline = quote(asset_class, symbol, available_close_day(asset_class, started))
        settle = quote(asset_class, symbol, available_close_day(asset_class, due))
    except QuoteUnavailableError as exc:
        _record(
            log,
            "warning",
            "outcome_quote_failed",
            "{} 取價失敗，維持未驗證：{}".format(run_id, exc),
        )
        return None
    except Exception as exc:  # noqa: BLE001 - one run's failure is one run's
        _record(
            log,
            "error",
            "outcome_quote_unexpected",
            "{} 取價時發生未預期的錯誤（{}：{}），維持未驗證；"
            "這一輪的其餘 pending 照常檢查。".format(run_id, type(exc).__name__, exc),
        )
        return None
    for label, side in (("baseline", baseline), ("settle", settle)):
        if not is_usable_price(side.close):
            _record(
                log,
                "error",
                "outcome_quote_not_a_price",
                "{} 的 {} 收盤價 {!r} 不是有效價格，維持未驗證；"
                "{} 只能寫一次，不會拿它去判定。".format(
                    run_id, label, side.close, OUTCOME_RECORD_NAME
                ),
            )
            return None
    direction = _direction(baseline.close, settle.close)
    verdict = OUTCOME_HIT if STANCE_DIRECTIONS[stance] == direction else OUTCOME_MISS
    return dict(
        _common(run_id, question, stance, due, now, "auto"),
        verdict=verdict,
        predicted_direction=STANCE_DIRECTIONS[stance],
        actual_direction=direction,
        asset=symbol,
        source=settle.source,
        baseline=_side(baseline),
        settle=_side(settle),
    )


def _unverifiable_payload(run_id, question, stance, reason, due, now):
    return dict(
        _common(run_id, question, stance, due, now, "auto"),
        verdict=OUTCOME_UNVERIFIABLE,
        reason=reason,
        note=UNVERIFIABLE_REASONS[reason],
        source=None,
    )


def _common(run_id, question, stance, due, now, recorded_by):
    return {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "run_id": run_id,
        "recorded_by": recorded_by,
        "recorded_at_utc": _iso(now),
        "due_at_utc": _iso(due) if due is not None else None,
        "asset_class": _field(question, "asset_class"),
        "assets": _assets(question),
        "predicted_stance": stance,
    }


def _side(quote):
    """One end of the comparison, with enough to check the verdict by hand."""
    return {
        "price": quote.close,
        "day": quote.day,
        "priced_on": quote.priced_on,
        "source": quote.source,
        "url": quote.url,
        "summary": quote.summary,
    }


def _direction(baseline, settle):
    if settle > baseline:
        return DIRECTION_UP
    if settle < baseline:
        return DIRECTION_DOWN
    return DIRECTION_FLAT


def _unverifiable_reason(question, stance):
    """Return why no price can settle this run, or ``None`` when one can.

    Three questions in the order that makes each answer true on its own: does
    this class have a source at all, is there exactly one thing to price, and
    does the adopted position claim a direction. A run that passes all three is
    the only kind a price is ever fetched for.
    """
    if not is_quotable(_field(question, "asset_class")):
        return REASON_NO_QUOTE_SOURCE
    if len(_assets(question)) != 1:
        return REASON_NOT_ONE_ASSET
    if stance not in STANCE_DIRECTIONS:
        return REASON_STANCE_NOT_DIRECTIONAL
    return None


def record_manual_outcome(
    data_root, run_id, verdict, now=None, note=None, actual_price=None, log=None
):
    """Write one outcome a person judged, under the same write-once rule.

    The fallback for a price that cannot be fetched, and the only way an
    ``unverifiable`` run ever gets a verdict. It corrects nothing: a run that
    already has a record is refused, and changing one is a deliberate act
    outside this program rather than a form submission.

    **A run has to be over before it can be judged.** ``outcome.json`` is
    write-once, so a verdict entered while the prediction still has time to run
    is not a mistake anyone can take back — the record that would correct it is
    the record that is already there. The two conditions are checked before
    anything is written and the refusal says which one failed; see
    :func:`manual_entry_refusal`.

    ``actual_price`` is optional and is kept as typed only if it reads as a
    finite positive number; anything else refuses the whole submission rather
    than storing a record with a field nobody can trust.
    """
    if verdict not in OUTCOME_VERDICTS:
        return OutcomeWrite(
            REFUSED,
            run_id,
            None,
            "「{}」不是可以記錄的結果；可用的是 {}。".format(
                verdict, "、".join(OUTCOME_VERDICTS)
            ),
        )
    price, price_problem = _manual_price(actual_price)
    if price_problem is not None:
        return OutcomeWrite(REFUSED, run_id, None, price_problem)
    run_dir = resolve_run_dir(data_root, run_id)
    if run_dir is None:
        return OutcomeWrite(
            NO_SUCH_RUN, run_id, None, "找不到 run_id {} 的執行紀錄。".format(run_id)
        )
    question = _read_json(run_dir / _QUESTION_RECORD)
    now = now or _utc_now()
    refusal = manual_entry_refusal(run_dir, question, now)
    if refusal is not None:
        return OutcomeWrite(REFUSED, run_id, None, refusal)
    stance = _field(_read_json(run_dir / _VOTES_RECORD), "adopted_stance")
    _, due = _period(question)
    payload = dict(
        _common(run_id, question, stance, due, now, MANUAL_SOURCE),
        verdict=verdict,
        source=MANUAL_SOURCE,
        note=(note or "").strip() or None,
        actual_price=price,
    )
    return _write_record(data_root, run_id, run_dir, payload, log)


def manual_entry_refusal(run_dir, question, now):
    """Return why this run may not be judged by hand yet, or ``None``.

    Three conditions, each with its own sentence, because "you cannot enter
    this yet" for three different reasons is three different things to do about
    it:

    * the run has not finalized — no :data:`~hoya_market_agents.run_index.
      FINALIZED_MARKER_NAME`, which is the same marker
      :func:`~.live.run_finished` and ``run_index`` read, so a run that is
      still debating is not offered a verdict;
    * the run does not say when its period ends, so nobody can show it has
      ended — the sweep's ``no_deadline`` branch, reached from the other side;
    * the period has not run out yet.

    The order matters only in which sentence a reader gets first, and it goes
    from the coarsest fact to the finest.

    This is the one place the question is answered. :func:`record_manual_
    outcome` refuses on it and :func:`~.views.history_data` filters the form's
    own list of runs with it, so a form cannot offer what a write would refuse.
    """
    if not (Path(run_dir) / FINALIZED_MARKER_NAME).is_file():
        return (
            "這個 run 還沒有完成（找不到 {}），還不能對答案；"
            "{} 只能寫一次，寫早了就改不回來。".format(
                FINALIZED_MARKER_NAME, OUTCOME_RECORD_NAME
            )
        )
    _, due = _period(question)
    if due is None:
        return (
            "這個 run 沒有記錄期限（{} 的 created_at_utc 或 period_days 缺漏或不合法），"
            "無法判斷是否已經到期，因此不接受人工結果。".format(_QUESTION_RECORD)
        )
    if now < due:
        return (
            "這個 run 要到 {} 才到期，現在是 {}，還不能對答案；"
            "{} 只能寫一次，寫早了就改不回來。".format(
                _iso(due), _iso(now), OUTCOME_RECORD_NAME
            )
        )
    return None


def manual_entry_allowed(data_root, run_id, now):
    """Whether ``run_id`` is a run the manual form may offer, right now.

    The list side of :func:`manual_entry_refusal`: it resolves the directory
    and reads the run's own ``question.json``, so a caller holding only an
    indexed row can ask the same question the write will ask.
    """
    run_dir = resolve_run_dir(data_root, run_id)
    if run_dir is None:
        return False
    question = _read_json(run_dir / _QUESTION_RECORD)
    return manual_entry_refusal(run_dir, question, now) is None


def _manual_price(raw):
    """Return ``(price or None, problem or None)`` for a hand-typed price.

    "Is it a number" and "is it a price" are two questions, asked in three steps
    and in this order — the same order the fetched close goes through in
    :func:`~hoya_market_agents.quote_api_client._parse_close`:
    :func:`~hoya_market_agents.quote_api_client.is_decimal_numeral`
    (**a text grammar, before the conversion**) → :func:`float` →
    :func:`~hoya_market_agents.quote_api_client.is_usable_price` (**a numeric
    check, after it**).

    Only the first is before the conversion, and only the first can be. ``float``
    first would turn ``True`` into ``1.0`` before anything could object, and
    ``1.0`` is a perfectly good price — the guard would be looking at a number
    that no longer remembers it was a boolean. The second asks whether a number
    is a usable price, so a number is what it wants. What arrives here is a form
    control's value or nothing at all, so the accepted input is text; see
    :func:`~hoya_market_agents.quote_api_client.is_decimal_numeral` for why that
    is written as what is allowed rather than as a list of what is not.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    if not is_decimal_numeral(raw):
        return None, "實際價格 {!r} 不是數字；這次沒有寫入。".format(raw)
    price = float(raw.strip())
    if not is_usable_price(price):
        return None, "實際價格 {!r} 不是有效價格；這次沒有寫入。".format(raw)
    return price, None


def _write_record(data_root, run_id, run_dir, payload, log):
    """Write ``outcome.json`` once, and say exactly which of the three it was.

    The state is read before the write for the sentence, and the write itself is
    what actually guarantees the rule: ``RunDirectory.write_json`` links a fresh
    file onto the name and raises when the name is taken, so two writers racing
    end with one record and one refusal rather than with whichever finished
    last.
    """
    existing = outcome_verdict(run_dir)
    if existing == OUTCOME_UNREADABLE:
        return _unreadable_write(run_id, run_dir)
    if existing is not None:
        return _already_write(run_id, existing)
    try:
        RunDirectory(run_id, run_dir, data_root).write_json(
            OUTCOME_RECORD_NAME, payload, source="ticket 12 outcome verification"
        )
    except ArtifactAlreadyExistsError:
        # Lost the race between the check above and the link. Whoever won holds
        # the record; report theirs rather than claiming this one landed.
        landed = outcome_verdict(run_dir)
        if landed == OUTCOME_UNREADABLE:
            return _unreadable_write(run_id, run_dir)
        return _already_write(run_id, landed)
    _record(
        log,
        "info",
        "outcome_manual_recorded" if payload["recorded_by"] == MANUAL_SOURCE else "outcome_recorded",
        "{} 已記錄結果：{}".format(run_id, payload["verdict"]),
    )
    # The record is on disk before this runs, so the index is catching up with
    # a fact rather than being the place the fact lives — which is why failing
    # to do it is a logged warning and not a lost verdict.
    reindex_outcome(data_root, run_id, log)
    return OutcomeWrite(
        WRITTEN,
        run_id,
        payload["verdict"],
        "已寫入 {}，結果是 {}。".format(OUTCOME_RECORD_NAME, payload["verdict"]),
    )


def _already_write(run_id, verdict):
    return OutcomeWrite(
        ALREADY_RECORDED,
        run_id,
        verdict,
        "{} 已經對過答案，結果是 {}；{} 不會被覆寫。".format(
            run_id, verdict, OUTCOME_RECORD_NAME
        ),
    )


def _unreadable_write(run_id, run_dir):
    return OutcomeWrite(
        RECORD_UNREADABLE,
        run_id,
        OUTCOME_UNREADABLE,
        "{} 的 {} 存在但讀不懂，因此這次沒有寫入，也不代表這個 run 還沒有結果；"
        "請人工檢查 {}。".format(run_id, OUTCOME_RECORD_NAME, run_dir / OUTCOME_RECORD_NAME),
    )


def reindex_outcome(data_root, run_id, log=None):
    """Refresh one run's indexed row so the statistics page sees a new record.

    A failure here costs a page its freshness and nothing else — the record is
    already on disk and ``index-backfill`` recovers the row — so it is logged
    rather than raised, exactly as ``run_index.index_finalized_run`` treats the
    same situation at the end of a run.
    """
    run_dir = resolve_run_dir(data_root, run_id)
    if run_dir is None:
        return False
    try:
        return upsert_run(data_root, run_dir)
    except Exception as exc:  # noqa: BLE001 - the index is rebuildable
        _record(
            log,
            "warning",
            "outcome_index_failed",
            "{} 的結果已寫入磁碟，但索引沒更新（{}：{}）；可用 index-backfill 重建。".format(
                run_id, type(exc).__name__, exc
            ),
        )
        return False


# -- reading a run's own records ---------------------------------------------


def _period(question):
    """Return ``(started, due)`` in UTC, or ``(started, None)`` when undatable.

    ``None`` for ``due`` is "this run does not say when its period ended", which
    is not "it has not ended". Both are left alone by the sweep, and only this
    one is counted as a run nobody can date — because guessing a period would
    make the verdict, the prices and the dates on the record all fiction.
    """
    started = _parse_utc(_field(question, "created_at_utc"))
    days = _field(question, "period_days")
    if started is None or isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        return started, None
    return started, started + timedelta(days=days)


def _parse_utc(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        moment = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _assets(question):
    values = _field(question, "assets")
    if not isinstance(values, list):
        return []
    return [value.strip() for value in values if isinstance(value, str) and value.strip()]


def _field(record, name):
    return record.get(name) if isinstance(record, dict) else None


def _read_json(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


# -- summary and log ---------------------------------------------------------

_SUMMARY_COUNTS = (
    "checked",
    "recorded",
    "unverifiable",
    "quote_failed",
    "not_due",
    "no_deadline",
    "already",
    "unreadable",
    "missing_dir",
)


def _empty_summary():
    summary = {name: 0 for name in _SUMMARY_COUNTS}
    summary["index_unavailable"] = False
    return summary


def _worth_reporting(summary):
    """Whether this pass did anything a reader of the log would want to find.

    A pass that only found runs whose period has not run out is the ordinary
    case and happens on every page view; recording it would bury the passes that
    did something under thousands that did not.
    """
    if summary is None or summary["index_unavailable"]:
        return False
    return any(
        summary[name]
        for name in ("recorded", "quote_failed", "no_deadline", "unreadable", "missing_dir")
    )


def _sweep_sentence(summary):
    return "到期檢查：" + "、".join(
        "{} {}".format(name, summary[name]) for name in _SUMMARY_COUNTS
    )


def _record(log, level, event, message):
    if log is None:
        return
    getattr(log, level)(event, SOURCE_OUTCOME, message)


def _iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now():
    return datetime.now(timezone.utc)
