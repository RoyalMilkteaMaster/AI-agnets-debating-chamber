"""The web app's operational log: one JSON object per line, one file per day.

``_data/logs/webapp.jsonl`` holds the current day. When a day ends its records
move to ``webapp-YYYY-MM-DD.jsonl`` under that day's own date, and the directory
is kept to :data:`RETENTION_DAYS` dated files — today and the twenty-nine before
it — with anything older removed when the log is opened.

**The dates are UTC**, the same clock the ``timestamp`` field is stamped from,
so the day named on a file is the day named in every record inside it. Run
directories are filed under Taipei dates; these are not, and the two are not
meant to line up — a log is read by matching a record's timestamp, not by
matching a run.

The clock is injected. Rotation and retention are decisions about *when*, and a
module that reads ``datetime.now()`` can only be tested by waiting. That is
also why the day an existing file belongs to is read from the records inside it
rather than from its modification time: the records carry the injected clock's
day, the filesystem carries the wall clock's, and mixing the two would rotate
on a difference that means nothing.

The only names this module ever writes to or deletes are the ones it produces
itself: ``webapp.jsonl``, ``webapp-`` plus an ISO date plus ``.jsonl``, and the
short-lived ``.webapp.jsonl-<pid>-<hex>.rotating`` one rotation claims a
finished day under. Only the second of those is ever deleted by retention.
Anything else a Data Root's log directory happens to hold is left alone.

Two servers may share a Data Root, so rotation is written for that: a finished
day is claimed with an atomic rename, and the claimed file is then checked to be
the same file that was judged finished before any of its bytes are moved. Winning
a rename says the *name* was taken once; it does not say what was under it. See
:func:`_rotate_finished_day`.

**A ``.rotating`` file left behind is not collected automatically**, and that is
a decision rather than an omission. It holds real records — the failure that left
it there names its path — and retention never deletes it, so nothing is lost. But
a claim abandoned by a dead process and a claim in flight in a live one look
identical from outside: the name carries a pid, and a pid can be reused, so no
liveness test is sound. Re-filing a live claim would append the same records
twice, which is exactly the corruption the claim exists to prevent. Merging one
back is therefore an operator's decision, taken with the message in hand.

Failures here are raised, not absorbed. A server whose log cannot be written is
a server whose next problem will have no record, so :func:`open_webapp_log`
refuses rather than starting quietly without one.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from ..clock import UTC_FORMAT, SystemClock, iso_utc

LOG_DIR_NAME = "logs"

ACTIVE_LOG_NAME = "webapp.jsonl"

# The name a finished day gets. The pattern below is the same fact read
# backwards, and it is the *whole* set of names this module will delete.
ROTATED_NAME_TEMPLATE = "webapp-{}.jsonl"
LOG_DATE_FORMAT = "%Y-%m-%d"
_ROTATED_NAME_PATTERN = re.compile(r"\Awebapp-(\d{4}-\d{2}-\d{2})\.jsonl\Z")

# The name one process claims a finished day's bytes under while it moves them.
# Unique per process and per attempt, and deliberately outside
# ``_ROTATED_NAME_PATTERN`` so retention never considers it a day. It exists
# only between the rename and the append; a crash in between leaves it behind
# holding real records, which is why the failure message names it.
_CLAIM_NAME_TEMPLATE = ".{}-{}-{}.rotating"

# How many dated files the directory keeps, today included. Thirty days of a
# log means thirty of them: today and the twenty-nine before it. The cutoff
# below is derived from this number rather than written beside it.
RETENTION_DAYS = 30

# The approved record shape (architecture §11.8). It is a closed set: the
# writer builds exactly these keys, so a reader can rely on all five being
# present and on nothing else appearing.
RECORD_FIELDS = ("timestamp", "level", "event", "source", "message")

LEVEL_INFO = "INFO"
LEVEL_WARNING = "WARNING"
LEVEL_ERROR = "ERROR"


class WebappLogError(Exception):
    """Raised when the log cannot be prepared or written."""


def webapp_log_dir(data_root):
    """Return the directory the web app's log files live in."""
    return Path(data_root) / LOG_DIR_NAME


def rotated_log_name(day):
    """Return the file name a finished ``day``'s records are kept under."""
    return ROTATED_NAME_TEMPLATE.format(day.strftime(LOG_DATE_FORMAT))


def open_webapp_log(data_root, clock=None):
    """Prepare the log directory and return the log to write through.

    Ordering is load bearing and is the reason this is one function: a day that
    ended is rotated *before* anything is appended, so today's first record
    cannot land in yesterday's file, and expiry is applied *after* that, so an
    active file left over from beyond the window is rotated and then removed
    rather than living on under a name retention never inspects.

    Raises :class:`WebappLogError` when the directory cannot be made, a
    finished day cannot be rotated, or an expired file cannot be removed —
    starting a long-running server with a log that silently does not work is
    the failure this is meant to prevent.
    """
    clock = clock or SystemClock()
    directory = webapp_log_dir(data_root)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WebappLogError(
            "無法建立 log 目錄 {}（{}：{}）。".format(directory, type(exc).__name__, exc)
        ) from exc
    today = _day_of(clock)
    _rotate_finished_day(directory, today)
    _remove_expired_days(directory, today)
    return WebappLog(directory, clock)


class WebappLog:
    """An open web app log. Every write is one line and is flushed.

    Flushed because the point of this file is to be readable while the server
    is still running; buffered records would only appear once it stopped, which
    is exactly when nobody needs them any more.
    """

    def __init__(self, directory, clock=None):
        self.directory = Path(directory)
        self.clock = clock or SystemClock()
        self._handle = None
        self._day = None

    @property
    def path(self):
        """The file the next record will be appended to."""
        return self.directory / ACTIVE_LOG_NAME

    def info(self, event, source, message):
        """Record something that went as intended."""
        self._write(LEVEL_INFO, event, source, message)

    def warning(self, event, source, message):
        """Record something the operator should know went differently."""
        self._write(LEVEL_WARNING, event, source, message)

    def error(self, event, source, message):
        """Record something that stopped working."""
        self._write(LEVEL_ERROR, event, source, message)

    def close(self):
        """Release the file. Writing again reopens it."""
        if self._handle is None:
            return
        self._handle.close()
        self._handle = None

    def _write(self, level, event, source, message):
        moment = self.clock.utc_now()
        today = moment.astimezone(timezone.utc).date()
        self._roll_into(today)
        record = {
            "timestamp": iso_utc(moment),
            "level": level,
            "event": event,
            "source": source,
            "message": message,
        }
        try:
            if self._handle is None:
                self._handle = open(self.path, "a", encoding="utf-8")
            self._handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._handle.flush()
        except OSError as exc:
            raise WebappLogError(
                "無法寫入 log {}（{}：{}）。".format(self.path, type(exc).__name__, exc)
            ) from exc
        self._day = today

    def _roll_into(self, today):
        """Rotate first if the open file belongs to an earlier day.

        A server outlives a day, so this belongs on the write and not only on
        the open. The day already written is remembered rather than re-read:
        after the first record this object knows it as a fact.
        """
        current = self._day if self._day is not None else _recorded_day(self.path)
        if current is None or current == today:
            return
        self.close()
        _rotate_finished_day(self.directory, today)
        self._day = None


def _day_of(clock):
    return clock.utc_now().astimezone(timezone.utc).date()


def _recorded_day(path):
    """Return the day of the newest dated record in ``path``, or ``None``."""
    return _recorded_day_and_identity(path)[0]


def _recorded_day_and_identity(path):
    """Return ``(day of the newest dated record, os.stat_result)`` for ``path``.

    ``(None, None)`` means there is no record to date the file by: the file is
    absent, empty, unreadable, or holds nothing this module wrote. Such a file is
    left where it is — rotation moves days, and a file with no day is not one.

    **The two answers come off the same open file on purpose.** A day read from
    one ``open`` and an identity read from another are two facts about two
    possibly different files, and rotation's whole correctness argument is that
    the file it moves is the file it judged. ``os.fstat`` on the handle the bytes
    came from cannot be about anything else. See :func:`_rotate_finished_day`.
    """
    try:
        with open(path, "rb") as handle:
            identity = os.fstat(handle.fileno())
            text = handle.read().decode("utf-8")
    except (OSError, UnicodeError):
        return None, None
    for line in reversed(text.splitlines()):
        day = _record_day(line)
        if day is not None:
            return day, identity
    return None, identity


def _record_day(line):
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    stamp = record.get("timestamp") if isinstance(record, dict) else None
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.strptime(stamp, UTC_FORMAT).date()
    except ValueError:
        return None


def _rotate_finished_day(directory, today):
    """Move an active file from an earlier day onto that day's own name.

    **The move is claimed before it is made.** Two servers sharing a Data Root
    both notice the same finished day, and read-then-append-then-unlink lets
    both read the same bytes: the day's file ends up holding every record
    twice, and whichever unlinks second fails with ``FileNotFoundError`` on a
    file the other already dealt with. Neither is a real failure and one of
    them is data corruption.

    So the claim comes first, and it is :func:`os.rename` onto a name only this
    process could have chosen. A rename is atomic and the source can only be
    consumed once: whoever renames wins the bytes outright, and everyone else
    is told the source is gone. That is the same shape ``run_store`` uses for a
    write-once artifact — one atomic filesystem operation decides it, rather
    than a check somebody hopes nobody raced — and it needs no lock file that a
    crash could leave behind holding the day hostage.

    ``FileNotFoundError`` from the claim is therefore the ordinary answer for
    "another writer rotated this already", not an error: the day did move, and
    the work this call would have done is done.

    **A rename is atomic on the pathname, not on the file, and that is not the
    same thing.** ``webapp.jsonl`` is a name today's writer recreates the moment
    it is free, so the loser of the race above can still rename *successfully* —
    and what it renames is the brand new file, which it then files away under the
    day it read off the old one. Today's records land in yesterday's file and the
    active log disappears. Winning the rename proves the name was taken exactly
    once; it proves nothing about *which* file was under it.

    So the claim is checked against the file that was judged, two ways, and both
    have to agree before a single byte is appended:

    * the same file — :func:`os.path.samestat` on the identity
      :func:`_recorded_day_and_identity` took off the very handle the day was
      read from, which is the shape ``tests/test_debate_rules.py`` uses to answer
      "is this the same file" against a hardlinked alias;
    * still saying the same day — the claimed bytes are dated again. Inode
      numbers are reused after an unlink, so identity alone has a hole in it, and
      re-dating closes that hole with one read this function was going to make
      anyway.

    A claim that fails either check is handed back (:func:`_hand_back_claim`) and
    this call returns as the loser it is.
    """
    active = directory / ACTIVE_LOG_NAME
    written_on, inspected = _recorded_day_and_identity(active)
    if written_on is None or written_on == today:
        return
    claim = directory / _CLAIM_NAME_TEMPLATE.format(ACTIVE_LOG_NAME, os.getpid(), uuid4().hex)
    try:
        os.rename(active, claim)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WebappLogError(
            "無法輪替 log {}（{}：{}）。".format(active, type(exc).__name__, exc)
        ) from exc
    claimed_day, claimed = _recorded_day_and_identity(claim)
    if (
        claimed is None
        or not os.path.samestat(inspected, claimed)
        or claimed_day != written_on
    ):
        _hand_back_claim(claim, active, written_on)
        return
    try:
        # Appending, not replacing: two runs of the server can both end up
        # rotating onto the same day's name, and the second must not erase what
        # the first put there. Only the claimed bytes are appended, and only
        # this process holds them, so nothing is appended twice.
        with open(directory / rotated_log_name(written_on), "ab") as rotated:
            rotated.write(claim.read_bytes())
        claim.unlink()
    except OSError as exc:
        raise WebappLogError(
            "無法輪替 log {}（{}：{}）；當日紀錄暫存在 {}。".format(
                active, type(exc).__name__, exc, claim
            )
        ) from exc


def _hand_back_claim(claim, active, written_on):
    """Put a claim back under the active name, having claimed the wrong file.

    ``os.link`` and not ``os.rename``: the point of this move is *not* to
    overwrite. If a third writer has already put a file at ``active`` there is
    nothing here that may replace it, and ``os.link`` is the primitive that says
    so — it refuses a name that exists, which is the same guarantee
    ``run_store`` leans on for a write-once artifact. The link then makes the
    bytes reachable under both names and the claim's name is dropped.

    Raising when the name is taken rather than merging is deliberate. Merging
    would mean interleaving two writers' records by guesswork, and this module's
    policy is that a log which cannot be kept correctly stops rather than
    continues quietly. The message names the file the records are sitting in, so
    nothing is lost — only unattended.
    """
    try:
        os.link(str(claim), str(active))
    except FileExistsError as exc:
        raise WebappLogError(
            "log 輪替搬走的不是先前判定屬於 {} 的那一份檔案，而 {} 已經被別的 "
            "writer 重建，無法歸還；那一份紀錄留在 {}，需要人工併回。".format(
                written_on.strftime(LOG_DATE_FORMAT), active, claim
            )
        ) from exc
    except OSError as exc:
        raise WebappLogError(
            "log 輪替搬走的不是先前判定的那一份檔案，歸還 {} 失敗（{}：{}）；"
            "那一份紀錄留在 {}。".format(active, type(exc).__name__, exc, claim)
        ) from exc
    try:
        claim.unlink()
    except OSError as exc:
        raise WebappLogError(
            "log 輪替已把檔案歸還 {}，但清不掉暫存名稱 {}（{}：{}）；"
            "紀錄沒有遺失，該暫存檔可以手動刪除。".format(
                active, claim, type(exc).__name__, exc
            )
        ) from exc


def _remove_expired_days(directory, today):
    """Delete rotated files whose day is past the retention window.

    The window is :data:`RETENTION_DAYS` **dated files counting today**, so the
    oldest one kept is ``RETENTION_DAYS - 1`` days ago and the day before it is
    the first one removed. Counting today's own file is what makes "thirty
    days" thirty files; a cutoff of ``today - RETENTION_DAYS`` keeps both ends
    and leaves thirty-one.
    """
    cutoff = today - timedelta(days=RETENTION_DAYS - 1)
    for entry in sorted(directory.iterdir()):
        day = _rotated_day(entry)
        if day is None or day >= cutoff:
            continue
        try:
            entry.unlink()
        except OSError as exc:
            raise WebappLogError(
                "無法刪除逾期 log {}（{}：{}）。".format(entry, type(exc).__name__, exc)
            ) from exc


def _rotated_day(entry):
    """Return the day a rotated log file is named for, or ``None``.

    ``None`` covers everything this module did not name: the active file, other
    logs a Data Root may hold, directories, and a name that fits the template
    but carries a date that does not exist.
    """
    if not entry.is_file():
        return None
    matched = _ROTATED_NAME_PATTERN.match(entry.name)
    if matched is None:
        return None
    try:
        return datetime.strptime(matched.group(1), LOG_DATE_FORMAT).date()
    except ValueError:
        return None
