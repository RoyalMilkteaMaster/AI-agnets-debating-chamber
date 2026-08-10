"""The rebuildable SQLite index that answers "which runs were there?".

``_data/runs/index.db`` holds one row per finished run so history can be
searched by date, market, light or wording without walking the directory
tree. This module is its only writer.

**It is derived data, never a source of truth.** Everything in it is computed
from the run directories beside it, so it can be deleted at any time and
rebuilt by :func:`rebuild_index`, and it is not backed up. In particular
nothing here resolves a run id to a directory — that stays with
``run_store.resolve_run_dir``, which walks the disk. An index that had become
the way runs are found would stop being disposable the moment it fell behind.

One function turns a directory into a row — :func:`run_row` — and both write
paths call it: the live upsert at ``FINALIZED`` and the full rebuild. That is
what makes "a rebuild reproduces what the live path wrote" a property of the
shape of this module rather than of two implementations happening to agree.

What is *not* decided here:

* Which lights exist, how they rank and which vote count earns which one.
  ``report_contract.CONFIDENCE_LEVELS`` and ``config/debate_rules.json`` own
  that. The ``confidence_level`` column stores whatever the run's report said.
* Which asset classes exist. ``config/market_scopes.json`` and
  ``question.ASSET_CLASSES`` own that; the column stores what the run's
  ``question.json`` recorded.

So there is no ``CHECK`` constraint and no copy of either list below: editing
a configuration file must never require a schema migration, and a run whose
records name a value no current configuration declares is still indexed and
still found.

Concurrency: "single writer" describes ownership — no other module writes this
file. Mutual exclusion is a separate job, and it is done with one
:func:`fcntl.flock` on ``runs/.index.lock`` that both writing paths take: the
live upsert for its one row, and a rebuild for its whole scan *and* install
together. Holding it across both is what makes a rebuild safe to interleave
with a finishing run — see :func:`rebuild_index`.

``flock`` rather than anything built out of a file's existence: the kernel
owns the lock's lifetime and drops it when the descriptor closes, when the
process exits and when the process is killed, so there is never a question of
who is entitled to release it and never a state that has to be cleaned up to
be correct. The lock file itself means nothing, is never deleted, and is never
replaced — unlike the index, whose inode changes on every rebuild, which is
exactly why the lock cannot live on it.

Waiting is bounded by :data:`_BUSY_TIMEOUT_SECONDS`. A writer that cannot get
the lock in that time fails and says so; the live path turns that into a
logged warning rather than a lost row nobody hears about.

Readers do not take the lock and are never blocked by a writer.
:func:`os.replace` already gives them either the whole old index or the whole
new one.

Within a single index file, SQLite's own locking still applies, with the
default rollback journal — portable across the DrvFs mount a Windows-hosted
Data Root lives on, and leaving no ``-wal``/``-shm`` files beside the run
directories.

This module needs POSIX ``fcntl``, which is what the project already assumes:
every command in the runbook runs under WSL.

Errors: :func:`query_runs` raises :class:`RunIndexError` when the index is
absent, unreachable, unreadable or holds content it cannot decode, because the
remedy for all four is the same — rebuild. The write functions raise SQLite's
own errors, because their callers asked to write and are entitled to the
reason. :func:`index_finalized_run` is the one place that swallows them, and
it is used only where a failed index write must not cost a finished run.

**Nothing here turns "I could not look" into "there is nothing there."** A
directory that cannot be listed stops a rebuild rather than reading as empty.
An index that is missing, unopenable or corrupt is likewise never *examined
and then acted on*: a rebuild does not inspect what is at the path at all. It
builds a complete new index beside it and moves that into place, so there is
no verdict about the old file for anything to depend on, and no moment when
the path has no index at all.

That is the shape, and it matters more than the mechanism: a check made at
one instant and acted on at another is a decision about a file that may no
longer be the file. The way out is not a narrower window — it is having
nothing conditional left to get wrong.
"""

import errno
import fcntl
import json
import os
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .run_store import RUN_DIR_DATE_FORMAT, RunStore

INDEX_DB_NAME = "index.db"

# The name both writing paths lock. It is a plain file beside the index, and
# like the index, the claims and ``latest.json`` it is skipped by every reader
# that walks ``runs/`` on ``is_dir`` alone. It is created once and then left
# alone for good: nothing reads its contents, its existence is not a state,
# and deleting it would only mean the next writer makes it again.
INDEX_LOCK_NAME = ".index.lock"

# The run artifact a reader is sent to. The path is a naming fact — where this
# run's report belongs — and is recorded whether or not the file is currently
# there, so the column never depends on when the index happened to be built.
REPORT_FILE_NAME = "report.html"

# ``manifest.json`` is the very last thing ``debate_driver.run_after_seal``
# writes — after the reports, after ``latest.json`` — and the FINALIZED
# handshake is returned immediately afterwards. So on disk its presence means
# every earlier step of that run succeeded, and a directory without a readable
# one is a run that never got there: the live path would never have indexed
# it, so a rebuild must not either. That ordering is load bearing; moving any
# write after it would make this marker a claim rather than a fact.
FINALIZED_MARKER_NAME = "manifest.json"

# Ticket 12's record: what actually happened after the prediction's period ran
# out. Like ``manifest.json`` above, the name is another module's artifact and
# is spelled here because this module has to read it; unlike it, this one may
# not exist yet, and its absence is a state rather than a disqualification.
OUTCOME_RECORD_NAME = "outcome.json"

# The verdicts an ``outcome.json`` may record. Unlike ``asset_class`` and
# ``confidence_level``, which come from configuration files this module refuses
# to copy, this vocabulary is not configurable: it is the meaning of a column
# this module owns, and a value outside it is not a verdict in an unfamiliar
# dialect — it is a record that cannot be read as an answer.
OUTCOME_HIT = "hit"
OUTCOME_MISS = "miss"
OUTCOME_UNVERIFIABLE = "unverifiable"
OUTCOME_VERDICTS = (OUTCOME_HIT, OUTCOME_MISS, OUTCOME_UNVERIFIABLE)

# The two answers that are never written into a record, only derived from one.
#
# ``OUTCOME_UNREADABLE`` is the state Ticket 12 §④ exists for: a record that is
# there but will not decode is neither "already checked" nor "not checked yet".
# Calling it the latter would let the next sweep overwrite it, which is the one
# thing a write-once artifact is for; calling it the former would report a
# verdict nobody can read. It is its own answer and says so.
#
# ``OUTCOME_PENDING`` names the ``NULL`` column in :func:`outcome_summary`,
# because a count needs a key and ``None`` is not one a reader can be shown.
OUTCOME_UNREADABLE = "unreadable"
OUTCOME_PENDING = "pending"

# Every state one run can be counted under. Ordered so the two that decide the
# hit rate come first.
OUTCOME_STATES = OUTCOME_VERDICTS + (OUTCOME_UNREADABLE, OUTCOME_PENDING)

# The hit rate's denominator, written down once. A prediction that could not be
# checked and one that has not been checked yet are not failures, so neither is
# here — see :func:`outcome_summary`.
SCORED_OUTCOMES = (OUTCOME_HIT, OUTCOME_MISS)

TABLE_NAME = "runs"
COLUMNS = (
    "run_id",
    "run_date",
    "question",
    "slug",
    "asset_class",
    "assets",
    "question_type",
    "confidence_level",
    "adopted_stance",
    "tally",
    "consensus_status",
    "report_path",
    "outcome",
)
# Columns holding a structure rather than a scalar. They are stored as JSON
# text and handed back decoded, so no caller has to know that.
_JSON_COLUMNS = ("assets", "tally")
# ``run_id`` identifies the row, so it is the one column an upsert never
# assigns. Every other column — ``outcome`` included since Ticket 12 — is
# derived from the run directory by ``run_row`` and is written on every upsert.
#
# Ticket 08 excluded ``outcome`` here, because at that point a verdict existed
# nowhere but in this file and re-indexing a run would have erased it. Ticket 12
# gave the verdict a home on disk (:data:`OUTCOME_RECORD_NAME`), which is what
# that exclusion was waiting for: a rebuild empties the table, so a value only
# this file held could never have survived one anyway. Now that ``run_row``
# reads it back, the live path and the rebuild agree by construction — which is
# the property this module is built around — and there is no column left that
# has to be tiptoed around.
_UPSERT_COLUMNS = tuple(name for name in COLUMNS if name != "run_id")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    run_date TEXT NOT NULL,
    question TEXT,
    slug TEXT,
    asset_class TEXT,
    assets TEXT,
    question_type TEXT,
    confidence_level TEXT,
    adopted_stance TEXT,
    tally TEXT,
    consensus_status TEXT,
    report_path TEXT,
    outcome TEXT
)
"""

# A keyword search is a substring search over words the user typed. SQLite's
# LIKE spends ``%`` and ``_`` on wildcards, so both — and the escape character
# itself — are escaped before the pattern is built, and the statement declares
# the escape character it was built with.
_LIKE_ESCAPE = "\\"

# Long enough that a second process finishing its own run waits for the lock
# rather than losing its row, short enough that nothing waits on a stuck
# writer for a whole run's worth of time.
_BUSY_TIMEOUT_SECONDS = 10.0

# How often a waiting writer retries the lock. Short enough that the wait is
# dominated by the holder's work rather than by this, long enough not to spin.
_LOCK_POLL_SECONDS = 0.005

# What "someone else holds it" arrives as. The set is the union of what each
# of the two code paths behind ``fcntl.flock`` actually promises, because
# which one runs is a property of the platform, not of this module:
#
#   * Native ``flock(2)`` — what Linux and Apple provide, and what runs here.
#     ``man 2 flock``: "EWOULDBLOCK: The file is locked and the LOCK_NB flag
#     was selected." CPython calls it directly and keeps its errno.
#   * ``fcntl(F_SETLK)`` — CPython's emulation where there is no native
#     ``flock``; Python's own docstring says so ("On some systems, this
#     function is emulated using fcntl()"). A record lock reports contention
#     as ``EACCES`` or ``EAGAIN``.
#
# POSIX permits ``EWOULDBLOCK == EAGAIN`` but does not require it, so naming
# only two of the three would leave the native path's documented errno out on
# any platform that keeps them distinct. On Linux all three collapse to two
# values and this costs nothing.
#
# Matching on numbers rather than on exception classes is deliberate: Python
# raises ``EAGAIN`` as ``BlockingIOError`` and ``EACCES`` as
# ``PermissionError``, so a handler written around a class covers one path's
# half of this set and turns the other path's real contention into a hard
# error.
_CONTENTION_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK})

# A rebuild assembles the new index here before moving it onto the real name.
# The scratch lives in ``runs/`` because ``os.replace`` needs both names on
# one filesystem, and it is a *file* with a leading dot, so every reader that
# walks that directory — this module's own scan, ``run_store``'s resolver and
# the dashboard listing — skips it on ``is_dir`` alone, exactly as they skip
# ``latest.json``, the claims and the index itself. ``mkstemp`` mints the rest
# of the name, so no two rebuilds can be handed the same one.
_SCRATCH_PREFIX = ".index.db."
_SCRATCH_SUFFIX = ".tmp"


class RunIndexError(Exception):
    """Raised when the index cannot be used."""


class RunIndexBusyError(RunIndexError):
    """Raised when another writer held the index lock for too long."""


def index_db_path(data_root):
    """Return the index file, which lives beside the runs it indexes."""
    return RunStore(data_root).runs_root / INDEX_DB_NAME


def index_lock_path(data_root):
    """Return the file both writing paths lock, beside the index."""
    return RunStore(data_root).runs_root / INDEX_LOCK_NAME


def run_row(run_dir):
    """Return one run directory's row, or ``None`` if it holds no finished run.

    Each column has exactly one source, and there are no fallbacks between
    them — a column whose own record is missing or unreadable comes back
    empty rather than quietly filled from somewhere else:

    ``run_date``
        The name of the directory's parent, which ``run_store`` created from
        the run's Taipei date. Taken from the folder rather than recomputed
        from the run id — whose timestamp is UTC — so the column cannot
        disagree with the folder a user has to open to find the run. A run
        started at 16:07 UTC belongs to the next Taipei day, and that is the
        day it is filed under here.
    ``slug``
        The label between the ``HHMM`` and the digest in the directory name,
        exactly as it is on disk. Empty when the name carries no label.
    ``question``, ``question_type``, ``assets``
        ``manifest.json``, the run's own final record.
    ``asset_class``
        ``question.json``, the only record that carries it.
    ``adopted_stance``, ``tally``, ``consensus_status``
        ``votes.json``.
    ``confidence_level``
        ``report.json``'s ``confidence.level``. Which levels exist is not this
        module's business; whatever the report said is stored.
    ``report_path``
        Where this run's report belongs, relative to the runs root, as
        ``<date folder>/<run directory>/report.html``. It is derived from the
        two path components, not from a directory listing, so it is the same
        whenever it is computed.
    ``outcome``
        ``outcome.json``, through :func:`outcome_verdict` — ``None`` when the
        run has not been checked yet, a verdict when it has, and
        :data:`OUTCOME_UNREADABLE` when the record is there but will not read.
        **Reading it from disk here is what keeps the index disposable**: a
        rebuild empties the table, so a verdict this function did not recover
        would be erased by the very command an operator is told to run when the
        index looks wrong.

    ``None`` comes back when ``manifest.json`` is absent, unreadable, not a
    JSON object, or names no run id — a row keyed on nothing could not be
    upserted, and a run that never reached its manifest never reached
    ``FINALIZED`` either.
    """
    run_dir = Path(run_dir)
    manifest = _read_json(run_dir / FINALIZED_MARKER_NAME)
    if not isinstance(manifest, dict):
        return None
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return None
    question_record = _read_json(run_dir / "question.json")
    votes = _read_json(run_dir / "votes.json")
    report = _read_json(run_dir / "report.json")
    confidence = _field(report, "confidence")
    date_folder = run_dir.parent.name
    return {
        "run_id": run_id,
        "run_date": date_folder,
        "question": manifest.get("question"),
        "slug": _directory_label(run_dir.name),
        "asset_class": _field(question_record, "asset_class"),
        "assets": manifest.get("assets"),
        "question_type": manifest.get("question_type"),
        "confidence_level": _field(confidence, "level"),
        "adopted_stance": _field(votes, "adopted_stance"),
        "tally": _field(votes, "tally"),
        "consensus_status": _field(votes, "consensus_status"),
        "report_path": "{}/{}/{}".format(date_folder, run_dir.name, REPORT_FILE_NAME),
        "outcome": outcome_verdict(run_dir),
    }


def outcome_verdict(run_dir):
    """Return what this run's outcome record says, telling three states apart.

    * ``None`` — there is no record. The run has not been checked yet.
    * one of :data:`OUTCOME_VERDICTS` — it was checked, and this is the answer.
    * :data:`OUTCOME_UNREADABLE` — a record is there and cannot be read as an
      answer: it will not open, is not UTF-8, is not JSON, is not an object, or
      names a verdict outside the closed set above.

    The third is not folded into either of the others on purpose. Reporting it
    as "not checked yet" would invite the next sweep to write over a record
    that may hold something, and reporting it as a verdict would put a number
    on a page that nothing on disk supports.

    Only ``FileNotFoundError`` means "absent" — every other ``OSError`` means a
    record that exists and could not be read. The distinction is made from the
    one read attempt rather than from a separate existence check, so there is no
    instant between the two for the answer to change in.
    """
    path = Path(run_dir) / OUTCOME_RECORD_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        return OUTCOME_UNREADABLE
    try:
        record = json.loads(text)
    except json.JSONDecodeError:
        return OUTCOME_UNREADABLE
    if not isinstance(record, dict):
        return OUTCOME_UNREADABLE
    verdict = record.get("verdict")
    return verdict if verdict in OUTCOME_VERDICTS else OUTCOME_UNREADABLE


def upsert_run(data_root, run_dir):
    """Write one finished run's row and return whether there was one to write.

    Takes the writer lock, so a rebuild that is midway through cannot discard
    this row by installing an index built before it existed. Waiting is
    bounded: :class:`RunIndexBusyError` after
    :data:`_BUSY_TIMEOUT_SECONDS`, because a finishing run must never be held
    up indefinitely by the index. That is *one* budget covering both waits
    this call can make — the lock and then SQLite — so the total is bounded by
    it rather than by twice it.

    **The row is read from disk inside the lock**, for the same reason the
    rebuild does its whole scan inside it: a row worked out beforehand
    describes the run as it was while this call was still queuing, and by the
    time the lock arrives that run may have been deleted. Writing it then
    would put back a row for something that is no longer there — against an
    index whose whole promise is that it says what is on disk.

    ``False`` means the directory holds no finished run, and nothing was
    written — not that a write was attempted and failed. A write that fails
    raises, so a caller who wants the reason gets it; the caller that must not
    be stopped by one uses :func:`index_finalized_run` instead.
    """
    deadline = time.monotonic() + _BUSY_TIMEOUT_SECONDS
    with _writer_lock(RunStore(data_root).runs_root, deadline):
        row = run_row(run_dir)
        if row is None:
            return False
        with _writing(data_root, deadline) as conn:
            _upsert(conn, row)
    return True


def index_finalized_run(data_root, run_dir, err=None):
    """Index one run that has just finished; never let that stop the run.

    The index is rebuildable, and this call happens after the run's own
    records are already on disk, so a failure here costs a query convenience
    and nothing else — the row comes back on the next
    :func:`rebuild_index`. Everything that ``Exception`` covers is treated as
    an index failure and reported on ``err``: SQLite refusing a read-only or
    locked database or a full disk (``sqlite3.OperationalError``), a file that
    is not a database (``sqlite3.DatabaseError``), a missing or unusable
    directory (``OSError``), and anything raised while reading the run's own
    records.

    A run whose own records cannot be read is reported too, even though it
    raises nothing. Everywhere else an unreadable directory is just "not a
    finished run", but this function is only ever called at the point where a
    run *has* finished, so finding nothing to index there is a failure like
    any other and has to leave the same trace.

    ``KeyboardInterrupt`` and ``SystemExit`` are not index failures and are
    not caught: they are ``BaseException``, they mean the process is being
    shut down, and swallowing one would turn an interrupt into a silent
    warning. Nothing here can replace the caller's own outcome either — the
    only thing this function raises is what it deliberately declines to catch.

    Returns whether a row was written.
    """
    try:
        written = upsert_run(data_root, run_dir)
    except Exception as exc:
        _warn(
            err,
            "run 索引寫入失敗，本次 run 不受影響（{}）：{}：{}".format(
                run_dir, type(exc).__name__, exc
            ),
        )
        return False
    if not written:
        _warn(
            err,
            "run 索引找不到這次 run 的 {}，沒有寫入任何列（{}）".format(
                FINALIZED_MARKER_NAME, run_dir
            ),
        )
    return written


def _warn(err, message):
    """Report one index problem without ever becoming a problem of its own."""
    print(
        "警告：{}；稍後可用 index-backfill 重建。".format(message),
        file=err if err is not None else sys.stderr,
    )


def rebuild_index(data_root):
    """Rebuild the whole index from the run directories, and say what it found.

    The index afterwards is exactly what is on disk: rows for runs that have
    since been deleted go away, and rows for runs the live path never managed
    to write appear. Rows are built by :func:`run_row`, the same function the
    live path uses, so a rebuilt index and a live-written one hold the same
    values by construction.

    **The new index is assembled in full beside the old one and moved onto its
    name with a single** :func:`os.replace`. Nothing here reads, judges or
    deletes whatever is already at that name — a corrupt index, an index that
    cannot be opened, and a perfectly good index are all handled the same way,
    by being superseded. That is what makes "a rebuild repairs corruption"
    true without any step that could act on a stale conclusion about a file
    that has since changed, and it means the path holds a complete, usable
    index at every instant: there is no window where it is missing or
    half-written. A concurrent rebuild can only ever install *another*
    complete index built from the same directories.

    A directory that holds no finished run is listed in ``skipped`` and does
    not stop the scan — Ticket 07 leaves half-built directories behind on
    purpose, and one of them must not cost the index every other run. Claim
    files, ``latest.json``, the index and any scratch file are files rather
    than directories and are never considered.

    A directory that cannot be *listed* is a different matter and stops the
    rebuild before anything is installed: the scan is what decides the whole
    contents, so a listing that silently came back empty would publish an
    empty index instead of failing. An interruption does the same. In both
    cases the index already in place is untouched.

    **The scan and the install are held under the same lock**, which is what
    stops a run finishing in between from disappearing. A row written before
    this rebuild took the lock describes a run whose ``manifest.json`` was
    already on disk, so the scan finds it; a row written after cannot be
    written until the install is done, so it lands in the new index. Locking
    only the install would leave a gap between those two, and a row written
    inside it would be superseded without anyone being told.

    Returns ``{"indexed": int, "skipped": [str], "unexpected_date_folders":
    [str]}``. Each skipped entry is the directory's ``<date folder>/<name>``;
    the last list names folders under ``runs/`` that are not Taipei dates,
    whose runs are still indexed but whose ``run_date`` column will be
    whatever the folder is called.
    """
    runs_root = RunStore(data_root).runs_root
    runs_root.mkdir(parents=True, exist_ok=True)
    with _writer_lock(runs_root, time.monotonic() + _BUSY_TIMEOUT_SECONDS):
        scratch = _new_scratch_index(runs_root)
        try:
            summary = _fill_index(scratch, runs_root)
            os.replace(scratch, index_db_path(data_root))
        finally:
            _discard_scratch(scratch)
    return summary


@contextmanager
def _writer_lock(runs_root, deadline):
    """Hold the one lock both writing paths take, for the whole block.

    ``flock`` on a descriptor this call opened. Closing that descriptor is
    what releases it, and the kernel does that on exit and on a kill too, so
    a writer that dies mid-rebuild costs the next one nothing: there is no
    stale lock to notice, diagnose or clean up, and no question of who is
    allowed to release it. That is the whole reason this is not built out of
    a file's existence — Ticket 07 spent five rounds on what happens when it
    is.

    The lock is on ``.index.lock`` rather than on the index, because a
    rebuild replaces the index: a lock held on that inode would guard a file
    that is about to stop being the index, while the name it was protecting
    changed hands underneath.
    """
    runs_root.mkdir(parents=True, exist_ok=True)
    handle = os.open(runs_root / INDEX_LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        _take_lock(handle, runs_root, deadline)
        yield
    finally:
        os.close(handle)


def _take_lock(handle, runs_root, deadline):
    """Wait for the lock until ``deadline``, and only for the right reason.

    Contention is retried; everything else comes straight back out with its
    own errno. Waiting out the whole budget and then announcing "another
    process is holding the lock" because of a bad descriptor would be
    answering a question nobody asked, and sending an operator to look for a
    competitor that never existed.

    Contention is :data:`_CONTENTION_ERRNOS` — a set of numbers covering both
    of the code paths behind ``fcntl.flock``, not one exception class. See
    that constant for which path promises which errno and why the difference
    matters.
    """
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in _CONTENTION_ERRNOS:
                raise
            if time.monotonic() >= deadline:
                raise RunIndexBusyError(
                    "另一個程序占用 run 索引寫入鎖 {}，等待逾時；這次沒有寫入。".format(
                        runs_root / INDEX_LOCK_NAME
                    )
                ) from None
            time.sleep(min(_LOCK_POLL_SECONDS, max(0.0, deadline - time.monotonic())))


def _remaining(deadline):
    """Return what is left of the budget, or refuse now if it is spent."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise RunIndexBusyError("等待 run 索引寫入的時間已用盡；這次沒有寫入。")
    return left


def _new_scratch_index(runs_root):
    """Create this rebuild's own empty scratch file and return its path."""
    handle, name = tempfile.mkstemp(
        prefix=_SCRATCH_PREFIX, suffix=_SCRATCH_SUFFIX, dir=runs_root
    )
    os.close(handle)
    return Path(name)


def _fill_index(database, runs_root):
    """Write every finished run into ``database`` and close it completely.

    The commit is what makes the file on disk complete — SQLite removes the
    rollback journal as part of it, so a committed database is already one
    file and not two. The *close* is for the two things the commit does not
    do: it releases the descriptor, so a rebuild does not leak one per call,
    and it lets the caller move the file on platforms that refuse to rename
    something still open. Neither is visible in the installed bytes, which is
    exactly why it is worth saying which is which.
    """
    indexed = 0
    skipped = []
    unexpected = []
    conn = sqlite3.connect(database, timeout=_BUSY_TIMEOUT_SECONDS)
    try:
        with conn:
            conn.execute(_SCHEMA)
            for date_dir in _child_dirs(runs_root):
                if not _is_date_folder(date_dir.name):
                    unexpected.append(date_dir.name)
                day_indexed, day_skipped = _index_one_day(conn, date_dir)
                indexed += day_indexed
                skipped += day_skipped
    finally:
        conn.close()
    return {
        "indexed": indexed,
        "skipped": skipped,
        "unexpected_date_folders": unexpected,
    }


def _discard_scratch(scratch):
    """Remove this rebuild's own scratch files, and nothing else, ever.

    The two names it touches are both derived from the one ``mkstemp`` minted
    for this call, so no interleaving can point this at the live index — the
    only way to reach that name from here would be to compute it, and it is
    never computed.

    Its own ``OSError`` is swallowed on purpose. This runs in a ``finally``,
    where raising would replace whatever the caller was already failing with;
    Ticket 07 §9 ① is the same mistake made in a cleanup path. The worst
    outcome of swallowing is a leftover temp file, which is a file, so no
    reader of ``runs/`` mistakes it for a run.
    """
    for path in (scratch, scratch.with_name(scratch.name + "-journal")):
        try:
            path.unlink()
        except OSError:
            pass


def _index_one_day(conn, date_dir):
    """Index one date folder; return how many runs went in and what did not."""
    indexed = 0
    skipped = []
    for run_dir in _child_dirs(date_dir):
        row = run_row(run_dir)
        if row is None:
            skipped.append("{}/{}".format(date_dir.name, run_dir.name))
            continue
        _upsert(conn, row)
        indexed += 1
    return indexed, skipped


def _is_date_folder(name):
    """Whether ``name`` is a date in the format ``run_store`` names folders with."""
    try:
        datetime.strptime(name, RUN_DIR_DATE_FORMAT)
    except ValueError:
        return False
    return True


def query_runs(
    data_root,
    *,
    date_from=None,
    date_to=None,
    asset_class=None,
    confidence_level=None,
    keyword=None,
    limit=None,
):
    """Return the indexed runs matching every filter given, newest first.

    ``date_from``/``date_to`` are Taipei dates in ``YYYY-MM-DD`` and both ends
    are included. ``asset_class`` and ``confidence_level`` match exactly and
    accept any string: no list of permitted values lives here. ``keyword`` is
    a literal substring of the question — ``%``, ``_`` and ``\\`` in it mean
    themselves, not LIKE wildcards. Filters combine; a filter left at ``None``
    is not applied, which is not the same as passing an empty string.

    ``limit`` caps the number of rows and must not be negative. ``None`` is
    how a caller asks for all of them; SQLite would read a negative limit as
    the same thing, so a front end passing a number straight through from a
    user would silently lift its own cap. Zero is a real answer and returns
    nothing.

    Every row comes back with all of :data:`COLUMNS`, with ``assets`` and
    ``tally`` decoded back into a list and a dict.

    Raises :class:`ValueError` for a negative ``limit``, and
    :class:`RunIndexError` when the index is missing, cannot be opened, cannot
    be read, or holds a value that will not decode. A query never creates the
    database: an absent index is reported, not invented.
    """
    if limit is not None and limit < 0:
        raise ValueError(
            "limit 不得為負數（{!r}）；不限制筆數請用 None。".format(limit)
        )
    clauses = []
    values = []
    if date_from is not None:
        clauses.append("run_date >= ?")
        values.append(date_from)
    if date_to is not None:
        clauses.append("run_date <= ?")
        values.append(date_to)
    if asset_class is not None:
        clauses.append("asset_class = ?")
        values.append(asset_class)
    if confidence_level is not None:
        clauses.append("confidence_level = ?")
        values.append(confidence_level)
    if keyword is not None:
        clauses.append("question LIKE ? ESCAPE '{}'".format(_LIKE_ESCAPE))
        values.append(_contains_pattern(keyword))
    statement = "SELECT {} FROM {}".format(", ".join(COLUMNS), TABLE_NAME)
    if clauses:
        statement += " WHERE " + " AND ".join(clauses)
    statement += " ORDER BY run_date DESC, run_id DESC"
    if limit is not None:
        statement += " LIMIT ?"
        values.append(limit)
    # Opening is inside the translation too: a database the caller has no
    # permission to open fails here, not at execute time, and a caller that
    # only knows about RunIndexError would otherwise see a raw SQLite error.
    conn = None
    try:
        conn = _connect_for_read(data_root)
        return [_decode(row) for row in conn.execute(statement, values)]
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise RunIndexError(
            "run 索引 {} 無法讀取（{}：{}）；請執行 index-backfill 重建。".format(
                index_db_path(data_root), type(exc).__name__, exc
            )
        ) from exc
    finally:
        if conn is not None:
            conn.close()


def outcome_summary(data_root):
    """Return how the indexed predictions turned out, in total and by light.

    ``{"totals": counts, "by_level": {confidence level: counts}}``. Each count
    block holds one entry per :data:`OUTCOME_STATES`, plus:

    ``total``
        Every run counted, whatever state it is in.
    ``scored``
        :data:`SCORED_OUTCOMES` — hits plus misses, and nothing else.
    ``hit_rate``
        ``hit / scored``, or ``None`` when ``scored`` is zero.

    **The denominator is the point.** A prediction nobody could check against a
    price is not a prediction that was wrong, and neither is one whose period
    has not run out yet, so ``unverifiable``, ``pending`` and ``unreadable`` are
    counted and shown but never divided by. Adding a run in any of those three
    states leaves ``hit_rate`` exactly where it was.

    ``None`` is a real key in ``by_level``: a run whose report named no light is
    still a run, and dropping it would make the levels stop adding up to the
    totals. Which lights exist is still not this module's business — levels are
    whatever the rows hold — so the caller orders them.

    Built on :func:`query_runs` rather than on aggregate SQL of its own, so
    there is one statement in this module that reads the index and one set of
    failure modes behind it. Raises :class:`RunIndexError` for the same reasons
    ``query_runs`` does: an index that cannot be read is reported, never counted
    as an empty one.
    """
    totals = _empty_counts()
    by_level = {}
    for row in query_runs(data_root):
        state = row["outcome"] if row["outcome"] in OUTCOME_STATES else None
        if state is None:
            state = OUTCOME_PENDING if row["outcome"] is None else OUTCOME_UNREADABLE
        level = by_level.setdefault(row["confidence_level"], _empty_counts())
        for counts in (totals, level):
            counts[state] += 1
            counts["total"] += 1
    for counts in [totals] + list(by_level.values()):
        _finish_counts(counts)
    return {"totals": totals, "by_level": by_level}


def _empty_counts():
    counts = {state: 0 for state in OUTCOME_STATES}
    counts["total"] = 0
    return counts


def _finish_counts(counts):
    """Add the derived two: how many were scored, and what share of them hit."""
    counts["scored"] = sum(counts[state] for state in SCORED_OUTCOMES)
    # ``None`` rather than ``0.0``: nobody has been right none of the time
    # before anybody has been checked, and a page showing 0% would be a
    # confident answer to a question that has no answer yet.
    counts["hit_rate"] = (
        counts[OUTCOME_HIT] / counts["scored"] if counts["scored"] else None
    )


def _contains_pattern(keyword):
    """Return the LIKE pattern matching questions containing ``keyword``.

    The escape character is escaped first; doing it later would escape the
    backslashes this function itself just added.
    """
    escaped = keyword.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
    for wildcard in ("%", "_"):
        escaped = escaped.replace(wildcard, _LIKE_ESCAPE + wildcard)
    return "%" + escaped + "%"


def _upsert(conn, row):
    conn.execute(
        "INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
        "ON CONFLICT(run_id) DO UPDATE SET {assignments}".format(
            table=TABLE_NAME,
            columns=", ".join(COLUMNS),
            placeholders=", ".join("?" * len(COLUMNS)),
            assignments=", ".join(
                "{0} = excluded.{0}".format(name) for name in _UPSERT_COLUMNS
            ),
        ),
        [_encode(name, row[name]) for name in COLUMNS],
    )


def _child_dirs(parent):
    """Return ``parent``'s subdirectories in name order, or raise trying.

    ``is_dir`` is the whole filter, which is what keeps claim files,
    ``latest.json`` and this database out — the same test ``run_store`` and
    the dashboard listing rely on. Names are not matched against a pattern:
    the run directory naming rules belong to ``run_store``, and a directory
    that holds no finished run is refused by :func:`run_row` anyway.

    An ``OSError`` from the listing is deliberately *not* caught. The only
    caller is the rebuild, and there an empty answer is not a harmless
    guess — it is a decision to delete every row the scan did not find. One
    unreadable directory would empty the index and report success. Letting it
    out instead leaves the transaction to roll back and the old index intact.

    ``os.scandir`` rather than ``Path.iterdir`` for the same reason:
    ``DirEntry.is_dir`` raises when it cannot tell, where ``Path.is_dir``
    answers ``False``. An entry that disappeared mid-scan is the one case
    that really is "not a directory here", and it stays quiet.
    """
    with os.scandir(parent) as entries:
        found = [Path(entry.path) for entry in entries if entry.is_dir()]
    return sorted(found, key=lambda path: path.name)


def _connect_for_write(data_root, timeout_seconds):
    database = index_db_path(data_root)
    database.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(database, timeout=timeout_seconds)


@contextmanager
def _writing(data_root, deadline):
    """Open the index for one transaction, committing only on a clean exit.

    SQLite gets what is left of the caller's budget, not a fresh one of its
    own. The lock this call already waited for excludes every writer that
    goes through this module, so anything still holding SQLite's own lock is
    outside the single-writer model — worth waiting a little for, not worth
    doubling the caller's wait for.
    """
    conn = _connect_for_write(data_root, _remaining(deadline))
    try:
        with conn:
            conn.execute(_SCHEMA)
            yield conn
    finally:
        conn.close()


def _connect_for_read(data_root):
    database = index_db_path(data_root)
    if not database.is_file():
        raise RunIndexError(
            "找不到 run 索引 {}；請先執行 index-backfill 重建。".format(database)
        )
    return _read_only_connection(database)


def _read_only_connection(database):
    """Open ``database`` for reading only.

    Read-only so a query can never bring an index into existence, and so a
    reader cannot be what corrupts one. The path is percent-encoded because a
    URI ends its filename at ``?`` or ``#``: an unencoded Data Root containing
    either would silently open a *different* database and answer from it.
    (A space needs no encoding to work, and is encoded anyway.)
    """
    return sqlite3.connect(
        "file:{}?mode=ro".format(quote(str(database))),
        uri=True,
        timeout=_BUSY_TIMEOUT_SECONDS,
    )


def _decode(stored):
    record = dict(zip(COLUMNS, stored))
    for name in _JSON_COLUMNS:
        if record[name] is not None:
            record[name] = json.loads(record[name])
    return record


def _encode(name, value):
    if name in _JSON_COLUMNS and value is not None:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _directory_label(directory_name):
    """Return the label between the leading ``HHMM`` and the trailing digest."""
    fields = directory_name.split("-")
    return "-".join(fields[1:-1])


def _field(record, name):
    return record.get(name) if isinstance(record, dict) else None


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
