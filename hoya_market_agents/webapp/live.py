"""One run's public chat, read forward out of its append-only event log.

``events.jsonl`` is written by appending whole lines and is never rewritten, so
this module reads it the way it is written: from a byte offset to the end, and
only up to the last complete line. A half-written line is not an event yet and
is left for the next read, which is why the cursor is the offset *after* the
newline of the last line that parsed.

That cursor is the whole reconnection story. A stream sends it as the SSE event
id; a client that comes back hands it over as ``Last-Event-ID`` and gets only
what was appended since. Nothing re-reads the file to compare it with what it
saw last, so the cost of staying connected does not grow with the run.

The cursor carries the run id as well as the offset — ``<run_id>@<offset>`` —
because an offset alone is meaningful only inside one file. A cursor from a
different run, or one past the end of this one, is not resumed from: the answer
to it is a fresh snapshot, which is also the answer for a client that has never
connected. The separator is ``@`` rather than anything with a meaning in a URL,
because a cursor is handed back two ways: in the ``Last-Event-ID`` header, and
in a query string when a freshly rendered page opens its first connection.

What the room shows is public and derived: seat messages, the stance each seat
currently holds, the tally over this ballot's own options, and the changes
between them. A ``seat_message`` is read for the named public fields listed in
:meth:`ChatRoom._add` and for nothing else, so whatever else an event record
happens to carry does not reach a page through here.

Reading is all this module does — it opens no file for writing.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ..contract_validator import ContractViolationError, validate_run_manifest

from ..debate_rules import debate_rules
from ..debate_state_machine import DebateError, stances_for
from ..question_package import MARKET_STANCES
from ..report_renderer import resolve_stance_labels
from ..report_workflow import HARD_DEADLINE_MS
from ..research_scheduler import PRIMARY_ONLY_END_MS, research_deadlines
from ..run_index import RunIndexError, query_runs
from ..run_store import RUN_DIR_DATE_FORMAT, resolve_run_dir
from ..seats import SEAT_IDS, seat_identities, seat_profiles
from . import views

# The complete window is seventeen minutes and the report is due at fifteen. These
# two are the product's fixed frame, not debate rules: they are absent from
# config/debate_rules.json and the settings page does not touch them, so they
# are constants here rather than reads of the rule authority. ``run_verifier``
# report workflow is the single authority for the report hard deadline.
TOTAL_WINDOW_MS = 17 * 60_000
REPORT_DEADLINE_MS = HARD_DEADLINE_MS

# The most recent history runs the room's picker offers. The picker is a way
# back to a finished run, not a report of every run ever, so it is capped.
RUN_PICKER_LIMIT = 50

NO_LEADER_HEADLINE = "尚未形成單一領先"

EVENTS_RECORD = "events.jsonl"
QUESTION_RECORD = "question.json"

# A run is finished when its manifest is there: the manifest is written last and
# is the same marker ``run_index`` treats as "this run is over".
FINISHED_MARKER = "manifest.json"

SEAT_MESSAGE_EVENT = "seat_message"

# The three additive research events one seat card is projected from. They are
# read for lineage only: a seat card never becomes a second, competing account
# of what a seat *said*, and an event carrying none of these fields is ignored.
ATTEMPT_LAUNCH_EVENT = "attempt_launch_requested"
ATTEMPT_OUTCOME_EVENT = "attempt_outcome_recorded"
ATTEMPT_LINEAGE_EVENT = "attempt_lineage_recorded"
RECOVERY_EXHAUSTED_EVENT = "recovery_exhausted"
ATTEMPT_EVENTS = (
    ATTEMPT_LAUNCH_EVENT,
    ATTEMPT_OUTCOME_EVENT,
    ATTEMPT_LINEAGE_EVENT,
    RECOVERY_EXHAUSTED_EVENT,
)

# 舊 run 沒有這些欄位。中性地說「未記錄」，不要猜 attempt kind、實際模型或失敗
# 原因——猜錯的除錯線索比沒有線索更貴。
UNRECORDED_LABEL = "未記錄"
ATTEMPT_KIND_LABELS = {
    "primary": "主要",
    "backup": "備援",
    "same_model_retry": "同模型重試",
    "cross_model_replacement": "跨模型替補",
}
ATTEMPT_OUTCOME_LABELS = {
    "adopted": "已採用",
    "superseded": "已被取代",
    "failed": "失敗",
    "cancelled": "已取消",
    "late_discarded": "逾時捨棄",
}

STATUS_WAITING = "waiting"
STATUS_RUNNING = "running"
STATUS_FINISHED = "finished"

# One class per ballot position, in the ballot's own order. A question type is
# drawn on the spot and ships its own words, so the colour follows the position
# rather than the word. A ballot with more positions than there are classes
# gets the neutral-unknown treatment for the extras rather than no class.
STANCE_CLASSES = ("stance-affirm", "stance-oppose", "stance-abstain")
UNKNOWN_STANCE_CLASS = "stance-unknown"

MESSAGE_KIND_LABELS = {
    "position": "初始立場",
    "challenge": "反方挑戰",
    "response": "回應挑戰",
    "final_vote": "最終投票",
}

NO_STANCE_LABEL = "尚未表態"
NO_REASON_LABEL = "未提供公開理由"
WAITING_LABEL = "等待派工"

# A public reason opens with its own conclusion: ``debate_driver`` requires the
# first sentence of every ``public_reason`` to be a 30-60 字 核心結論, so the
# brief a message shows before it is opened is that first sentence and the cut
# is a sentence end rather than a character count. The cap below is the answer
# for a reason that never ends a sentence at all, and only for that.
FULL_STOPS = "。！？"
ASCII_STOPS = ".!?"
BRIEF_LIMIT = 60
BRIEF_ELLIPSIS = "…"

# Not ``#``: a cursor also travels in a query string, and a fragment marker
# would be cut off there by every URL parser including this server's.
CURSOR_SEPARATOR = "@"


# -- the append-only reader ---------------------------------------------------


def read_events(path, offset=0):
    """Return ``([(cursor offset, record), ...], offset after the last one)``.

    Only whole lines are consumed. The returned offset is where the next read
    starts, so feeding it back in reads exactly what was appended in between and
    nothing twice.

    A line that is not JSON, or is JSON but not an object, is skipped without
    moving the offset back: it was written, it is past, and it is not an event
    this module can show. A path that will not open — not written yet, gone, or
    unreadable — reads as no events at the offset it was asked about; a run
    directory exists before its first event does, and a stream that treated the
    gap as an error would end for a reason that resolves itself.
    """
    try:
        with open(path, "rb") as stream:
            stream.seek(offset)
            raw = stream.read()
    except OSError:
        return [], offset
    entries = []
    consumed = offset
    parts = raw.split(b"\n")
    parts.pop()  # whatever follows the last newline is not a complete line
    for part in parts:
        consumed += len(part) + 1
        record = _decode(part)
        if record is not None:
            entries.append((consumed, record))
    return entries, consumed


def _decode(part):
    try:
        value = json.loads(part.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def make_cursor(run_id, offset):
    """Return the SSE event id for ``offset`` bytes into ``run_id``'s events."""
    return "{}{}{}".format(run_id, CURSOR_SEPARATOR, offset)


def resume_offset(cursor, run_id, current_offset):
    """Return the offset a client's cursor may resume from, or ``None``.

    ``None`` means "send a snapshot instead". It is the answer for a cursor
    that is absent, is not text, carries no separator, names another run, has
    an offset that is not a number, is negative, or points past the bytes that
    exist. Resuming from a cursor that means something else would silently drop
    the messages in between, which is the one failure a reconnecting reader
    cannot see.
    """
    if not isinstance(cursor, str) or CURSOR_SEPARATOR not in cursor:
        return None
    named, _, raw_offset = cursor.rpartition(CURSOR_SEPARATOR)
    if named != run_id:
        return None
    try:
        offset = int(raw_offset)
    except ValueError:
        return None
    if offset < 0 or offset > current_offset:
        return None
    return offset


# -- the room -----------------------------------------------------------------


class ChatRoom:
    """A run's public chat, built by feeding it events in the order they were written.

    The room holds what has been fed to it and nothing else: it never opens the
    file. That is what lets one connection read the log once, then follow it in
    increments, and still have a whole room to describe.

    ``asset_class`` is the run's own, and it decides which profile set names the
    seven seats (ADR 0006). It is read **once per room** rather than once per
    message: a room names a seat for every message it holds, and the roster is
    the authority for that name, so resolving it per lookup turned one page
    render into dozens of reads. A run recorded before the field existed, and the
    waiting room that has no run at all, pass ``None`` — which
    :func:`~hoya_market_agents.seats.profile_set_for` reads as the open set, a
    filled set of its own rather than a fallback to nothing.
    """

    def __init__(self, stance_options, stance_labels, asset_class=None):
        self.asset_class = asset_class
        self.seat_fields = seat_fields(asset_class)
        self.stance_options = tuple(stance_options)
        self.stance_labels = dict(stance_labels)
        self.stance_classes = {
            stance: (
                STANCE_CLASSES[index]
                if index < len(STANCE_CLASSES)
                else UNKNOWN_STANCE_CLASS
            )
            for index, stance in enumerate(self.stance_options)
        }
        self.messages = []
        self.changes = []
        self.stances = {}
        self.attempts = {seat_id: _empty_attempt_state() for seat_id in SEAT_IDS}
        self.debate_started = False

    def ingest(self, records):
        """Add every seat message among ``records``; return just the new views.

        Two things are dropped: an event that is not a ``seat_message``, and a
        ``seat_message`` naming a seat outside the fixed seven. The rest of
        ``events.jsonl`` — the rejections, the milestones — is not shown
        anywhere in this room, so the seat panel says what a seat last *said*
        and is silent about a seat that has not spoken.

        The research attempt events are the one exception, and they are not
        messages: they feed the seat's own lineage line (Spec R-008) rather than
        the feed, so a seat that retried on a second provider is still one seat
        with one card. They are never returned as fresh messages.
        """
        fresh = []
        for record in records:
            event = record.get("event")
            if event == "debate_opened":
                self.debate_started = True
                continue
            if record.get("seat_id") not in SEAT_IDS:
                continue
            if event in ATTEMPT_EVENTS:
                self._add_attempt(record)
                continue
            if event != SEAT_MESSAGE_EVENT:
                continue
            fresh.append(self._add(record))
        return fresh

    def _add_attempt(self, record):
        """Fold one research attempt event into its seat's single lineage view.

        Only an outcome event settles ``terminal_outcome``: a seat whose result
        was adopted keeps saying so even when a second attempt fails afterwards,
        because the failure belongs to that other attempt and was never this
        seat's answer.
        """
        state = self.attempts[record["seat_id"]]
        event = record.get("event")
        attempt_id = record.get("attempt_id")
        if event == RECOVERY_EXHAUSTED_EVENT:
            state["exhausted"] = True
            return
        if not isinstance(attempt_id, str):
            return
        attempt = state["by_id"].setdefault(attempt_id, _empty_attempt(attempt_id))
        if event == ATTEMPT_LAUNCH_EVENT:
            if attempt_id not in state["order"]:
                state["order"].append(attempt_id)
            attempt["provider"] = record.get("provider")
            attempt["requested_model"] = record.get("requested_model")
            attempt["attempt_kind"] = record.get("attempt_kind")
            return
        if event == ATTEMPT_LINEAGE_EVENT:
            attempt["actual_provider"] = record.get("actual_provider")
            attempt["actual_model"] = record.get("actual_model")
            return
        if attempt["terminal_outcome"] is not None:
            return
        attempt["terminal_outcome"] = record.get("terminal_outcome")
        attempt["failure_code"] = record.get("failure_code")
        attempt["failure_message"] = record.get("failure_message")
        attempt["adopted"] = record.get("adopted") is True
        if attempt["adopted"]:
            state["adopted_attempt_id"] = attempt_id

    def attempt_view(self, seat_id):
        """The one attempt line this seat's card shows, adopted source first."""
        state = self.attempts[seat_id]
        shown = state["adopted_attempt_id"] or (
            state["order"][-1] if state["order"] else None
        )
        attempt = state["by_id"].get(shown) or _empty_attempt(None)
        adopted = attempt["adopted"]
        view = dict(
            attempt,
            attempt_count=len(state["order"]),
            adopted=adopted,
            exhausted=state["exhausted"] and not adopted,
        )
        view["label"] = _attempt_label(view)
        return view

    def _add(self, record):
        """Read one seat message's public fields, and only those.

        ``seat_id``, ``kind``, ``round``, ``stance``, ``public_reason``,
        ``evidence_ids``, ``elapsed_ms`` and ``stance_change_reason``. Nothing
        else in the record is copied into the view, so nothing else can be
        rendered from it.

        ``public_brief`` is not read from the record but derived here from the
        reason, which is what keeps the first page and every later frame of the
        stream showing one and the same brief.
        """
        seat_id = record["seat_id"]
        stance = record.get("stance")
        reason = record.get("public_reason") or NO_REASON_LABEL
        known = stance in self.stance_labels
        before = self.stances.get(seat_id)
        changed = known and before is not None and before != stance
        view = {
            "seq": len(self.messages),
            "round": _integer(record.get("round")),
            "kind": record.get("kind"),
            "kind_label": MESSAGE_KIND_LABELS.get(record.get("kind"), "公開發言"),
            "stance": stance if known else None,
            "stance_label": self.stance_labels.get(stance, NO_STANCE_LABEL),
            "stance_class": self.stance_classes.get(stance, UNKNOWN_STANCE_CLASS),
            "public_reason": reason,
            "public_brief": first_sentence(reason),
            "evidence_ids": _string_list(record.get("evidence_ids")),
            "elapsed_ms": _integer(record.get("elapsed_ms")) or 0,
            "changed": changed,
            "change_label": self._change_label(before, stance, known, changed),
        }
        view.update(self.seat_fields[seat_id])
        self.messages.append(view)
        if known and before != stance:
            self.stances[seat_id] = stance
            self.changes.append(
                {
                    "seat_id": seat_id,
                    "seat_label": self.seat_fields[seat_id]["seat_label"],
                    "before": before,
                    "before_label": self.stance_labels.get(before, NO_STANCE_LABEL),
                    "after": stance,
                    "after_label": self.stance_labels[stance],
                    "after_class": self.stance_classes.get(
                        stance, UNKNOWN_STANCE_CLASS
                    ),
                    "changed": changed,
                    "reason": record.get("stance_change_reason") or reason,
                    "elapsed_ms": _integer(record.get("elapsed_ms")) or 0,
                }
            )
        return view

    def _change_label(self, before, stance, known, changed):
        if not known:
            return None
        if before is None:
            return "首次表態"
        if not changed:
            return None
        return "{} → {}".format(
            self.stance_labels.get(before, NO_STANCE_LABEL), self.stance_labels[stance]
        )

    def tally_views(self):
        """Return one entry per ballot position, in the ballot's own order."""
        counts = {stance: 0 for stance in self.stance_options}
        for stance in self.stances.values():
            if stance in counts:
                counts[stance] += 1
        return [
            {
                "stance": stance,
                "label": self.stance_labels.get(stance, stance),
                "class": self.stance_classes.get(stance, UNKNOWN_STANCE_CLASS),
                "count": counts[stance],
            }
            for stance in self.stance_options
        ]

    def seat_views(self):
        """Return the seven seats in their fixed order, whether they spoke or not.

        A seat with nothing to show is still shown: the panel is a roll call,
        and a missing row reads as a seat that does not exist rather than one
        that has not spoken.
        """
        latest = {}
        counts = {seat_id: 0 for seat_id in SEAT_IDS}
        for message in self.messages:
            latest[message["seat_id"]] = message
            counts[message["seat_id"]] += 1
        views_out = []
        for seat_id in SEAT_IDS:
            message = latest.get(seat_id)
            stance = self.stances.get(seat_id)
            view = {
                "stance": stance,
                "stance_label": self.stance_labels.get(stance, NO_STANCE_LABEL),
                "stance_class": self.stance_classes.get(stance, UNKNOWN_STANCE_CLASS),
                "status": message["kind_label"] if message else WAITING_LABEL,
                "message_count": counts[seat_id],
                "attempt": self.attempt_view(seat_id),
            }
            view.update(self.seat_fields[seat_id])
            views_out.append(view)
        return views_out

    def latest_round(self):
        """The highest round any message declared, or ``None`` if none did."""
        rounds = [m["round"] for m in self.messages if m["round"] is not None]
        return max(rounds) if rounds else None

    def latest_elapsed_ms(self):
        return self.messages[-1]["elapsed_ms"] if self.messages else 0


def _empty_attempt_state():
    return {"order": [], "by_id": {}, "adopted_attempt_id": None, "exhausted": False}


def _empty_attempt(attempt_id):
    """A seat with no recorded attempt, and every old run's missing field."""
    return {
        "attempt_id": attempt_id,
        "provider": None,
        "requested_model": None,
        "actual_provider": None,
        "actual_model": None,
        "attempt_kind": None,
        "terminal_outcome": None,
        "failure_code": None,
        "failure_message": None,
        "adopted": False,
    }


def _attempt_label(view):
    """One short line: the adopted source, or the last thing that went wrong.

    One line, never a panel: a seat card that fills with error text pushes the
    six other seats off the screen, and the detail is already in the run's own
    events. An attempt with nothing recorded says :data:`UNRECORDED_LABEL`
    rather than guessing a kind or a failure the old run never wrote down.
    """
    if view["attempt_count"] == 0 and view["attempt_id"] is None:
        return UNRECORDED_LABEL
    source = _attempt_source(view)
    if view["adopted"]:
        return "已採用：{}".format(source)
    if view["exhausted"]:
        return "備援已用盡：{}".format(view["failure_code"] or UNRECORDED_LABEL)
    outcome = view["terminal_outcome"]
    if outcome is None:
        return "研究中：{}".format(source)
    label = ATTEMPT_OUTCOME_LABELS.get(outcome, outcome)
    if view["failure_code"]:
        return "{}：{}（{}）".format(label, source, view["failure_code"])
    return "{}：{}".format(label, source)


def _attempt_source(view):
    provider = view["actual_provider"] or view["provider"] or UNRECORDED_LABEL
    kind = ATTEMPT_KIND_LABELS.get(view["attempt_kind"])
    return "{}（{}）".format(provider, kind) if kind else provider


def seat_fields(asset_class=None):
    """The shown identity of all seven seats, for one run's asset class.

    One roster read, from the one module that holds seat names
    (:func:`~hoya_market_agents.seats.seat_identities` and
    :func:`~hoya_market_agents.seats.seat_profiles`). The web app keeps no
    table of its own: a name edited in ``config/agent_roster.json`` reaches the
    next render, and a 台股 run is named from the stock set rather than from
    whichever set happened to be the module-level default.

    ``seat_label`` and ``seat_blurb`` are read off **one** profile object, so the
    name and the sentence under it are the same set's by construction and travel
    together in every frame the room hands out. They used to be able to come
    apart: the sentence was rendered once into the page and cached by the script,
    while the names arrived with each frame — so the waiting room's open-set
    sentence survived the handover to a real run, and a page pinned to one run
    could show that run's sentence under the newest run's names.

    Keyed on :data:`~hoya_market_agents.seats.SEAT_IDS`, so the order is the
    fixed one and a lookup for anything else raises rather than reaching a page
    as a blank seat. Both callers filter to those first — :meth:`ChatRoom.ingest`
    drops a message from any other seat, and :meth:`ChatRoom.seat_views` walks
    the fixed seven.
    """
    identities = seat_identities(asset_class)
    profiles = seat_profiles(asset_class)
    return {
        seat_id: {
            "seat_id": seat_id,
            "seat_label": profiles[seat_id].display_name,
            "seat_blurb": profiles[seat_id].blurb,
            "agent_name": identities[seat_id].display_name,
            "agent_number": identities[seat_id].agent_number,
            "avatar": identities[seat_id].avatar,
            "provider": identities[seat_id].provider,
        }
        for seat_id in SEAT_IDS
    }


# -- resolving which run is on screen ----------------------------------------


def newest_run_id(data_root):
    """Return the id of the most recently created run, or ``None``.

    Newest is decided by the run's **own id**, which opens with a UTC timestamp
    accurate to the second. A directory name is not that authority: it opens
    with ``HHMM``, so two runs started in the same minute are indistinguishable
    by it and the comparison used to fall through to the question slug — which
    is how a 02:00:01 run called ``zzz`` beat a 02:00:59 run called ``aaa``.
    Two runs **less than** a minute apart need no concurrency to land in the same
    minute: 02:00:01 and 02:00:59 are fifty-eight seconds apart and both read as
    ``0200``. A full minute apart would land in two different ones.

    The date folder is still the outer key, and only the newest date folder
    holding a readable id is opened: a folder is the Taipei date of the same
    instant the id carries (ADR 0005), so a later id can never be filed under
    an earlier date. Within that folder every ``question.json`` is read and the
    largest id wins. ``question.json`` is written before any seat is
    dispatched, so a run is found before it has anything an index could hold;
    a directory that has not got that far offers no id and is skipped.
    """
    run_id, _ = _newest_run(data_root)
    return run_id


def _newest_run(data_root):
    """Return ``(run id, run directory)`` for the newest run, or ``(None, None)``.

    The one place the "which run is newest" comparison is made, so
    :func:`newest_run_id`, :func:`in_progress_run_id` and :func:`live_snapshot`
    cannot come to three different answers about the same Data Root.
    """
    runs_root = Path(data_root) / "runs"
    for date_dir in _date_dirs_newest_first(runs_root):
        dated = []
        for run_dir in _child_dirs(date_dir):
            record = _read_json(run_dir / QUESTION_RECORD)
            run_id = record.get("run_id") if isinstance(record, dict) else None
            if isinstance(run_id, str) and run_id:
                dated.append((run_id, run_dir))
        if dated:
            return max(dated, key=lambda entry: entry[0])
    return None, None


def _date_dirs_newest_first(runs_root):
    dated = [path for path in _child_dirs(runs_root) if _is_run_date(path.name)]
    dated.sort(key=lambda path: path.name, reverse=True)
    return dated


def _is_run_date(name):
    """``runs/`` holds one folder per Taipei date (ADR 0005).

    Anything else that turns up in there — ``latest.json``, ``index.db``, a
    stray directory — is not a date and holds no runs. The format comes from
    ``run_store`` rather than a pattern written again here.
    """
    try:
        datetime.strptime(name, RUN_DIR_DATE_FORMAT)
    except ValueError:
        return False
    return True


def _child_dirs(parent):
    try:
        return [path for path in parent.iterdir() if path.is_dir()]
    except OSError:
        return []


def run_finished(run_dir, run_id=None):
    """Has this run written a canonical marker that says it is over?

    One reading of :data:`FINISHED_MARKER`, so the live room, the stream and the
    settings page cannot come to three different answers about the same run.
    """
    if run_id is None:
        question = read_question(run_dir)
        run_id = question.get("run_id") if isinstance(question, dict) else None
    return _validated_final_manifest(run_dir, run_id) is not None


def in_progress_run_id(data_root):
    """Return the id of the run this Data Root has under way, or ``None``.

    "Under way" is exactly what the live room shows as
    :data:`STATUS_RUNNING`: the newest run — by the timestamp in its own id,
    see :func:`newest_run_id` — exists and has not written its manifest. It
    follows that one run and only that one: **an older run that never
    finished, while a newer one did, is not reported here**, the same blind
    spot :func:`live_snapshot` has and for the same reason. Anything that locks
    on this answer inherits the blind spot; :func:`~.settings.locked_by` says
    so in its own words.

    Unlike :class:`~.launch.LaunchLock`, this sees a run whoever started it: the
    question is what the Data Root is doing, not what this process did.
    """
    run_id, run_dir = resolve_live_run(data_root)
    if run_dir is None or run_finished(run_dir):
        return None
    return run_id


def resolve_live_run(data_root, run_id=None):
    """Return ``(run id, run directory)`` for the run to watch, or ``(None, None)``.

    Naming a run pins it; naming none follows the newest one. ``(None, None)``
    covers a Data Root with no runs at all and a named run that does not exist,
    because the page for both is the same page: there is nothing to watch.
    """
    if run_id is None:
        run_id = newest_run_id(data_root)
    if run_id is None:
        return None, None
    run_dir = resolve_run_dir(data_root, run_id)
    if run_dir is None or not run_dir.is_dir():
        return None, None
    return run_id, run_dir


def ballot_for(question):
    """Return ``(stance options, labels)`` for the ballot this run was drawn on.

    The state machine stays the authority for which stances a question type has;
    the run's own recorded wording wins over the derived wording; a question
    recorded before the type was is read as a market question.
    """
    question = question if isinstance(question, dict) else {}
    try:
        options = tuple(stances_for(question.get("question_type")))
    except DebateError:
        options = MARKET_STANCES
    labels = resolve_stance_labels(
        options, question.get("assets") or (), question.get("stance_labels")
    )
    return options, labels


# -- what a page and a stream are handed --------------------------------------


def read_question(run_dir):
    """Return the run's ``question.json`` payload, or an empty one."""
    return _read_json(run_dir / QUESTION_RECORD) or {}


def _asset_class_of(question):
    """The run's recorded asset class, or ``None`` when it recorded none."""
    return question.get("asset_class") if isinstance(question, dict) else None


def open_room(run_dir, question, cursor=None, run_id=None):
    """Return ``(room, offset, messages the client still needs, resumed?)``.

    One full read, once per connection. Every event so far goes into the room,
    because the tally and the seat roll describe the whole run and not the tail
    of it — a client that joins at message ninety still has to be told the score.
    What the client is *sent* is a separate question: one holding a cursor this
    run can honour already has everything written before it, so only what came
    after is handed back, and ``resumed`` is ``True``.

    A cursor that cannot be honoured — see :func:`resume_offset` — is not an
    error. It is answered with the whole room, which is also the answer for a
    client that has never connected.

    The room is opened on this run's own ``asset_class``, which is what names its
    seven seats: the same field ``ballot_for`` neighbours read for the stance
    vocabulary, read here for the profile set (ADR 0006). A run recorded before
    the field existed has none, and reads the open set.
    """
    room = ChatRoom(*ballot_for(question), asset_class=_asset_class_of(question))
    entries, offset = read_events(run_dir / EVENTS_RECORD)
    resume = resume_offset(cursor, run_id, offset)
    if resume is None:
        return room, offset, room.ingest([record for _, record in entries]), False
    room.ingest([record for end, record in entries if end <= resume])
    fresh = room.ingest([record for end, record in entries if end > resume])
    return room, offset, fresh, True


# -- the timeline, the phase and the focus, read live from the authorities ----


def rule_timeline(question_type=None):
    """Return the public milestone timeline, read live from the authorities.

    Every vote count and every debate wall is read from
    :func:`~hoya_market_agents.debate_rules.debate_rules` **at call time**, so a
    rule saved on the settings page reaches the next render rather than a value
    frozen at import — which is the one thing the old dashboard's
    ``RULES = rules_for()`` got wrong and ``tests/test_debate_rules.py`` guards.

    The seal and every vote wall move with the question type: a two-asset
    comparison seals thirty seconds later than a single-asset run, and schema
    v2 expresses every wall as an offset from that seal. The seal instant comes
    from :func:`~hoya_market_agents.research_scheduler.research_deadlines` — the
    sole authority for it — while the offsets and thresholds come from the
    reload-aware rule object read above.

    The opening milestone is T+0; the switch to trusted secondary sources is
    :data:`research_scheduler.PRIMARY_ONLY_END_MS`; and the two closing
    milestones are the product's own :data:`REPORT_DEADLINE_MS` and
    :data:`TOTAL_WINDOW_MS`.
    """
    rules = debate_rules()
    deadlines = research_deadlines(question_type)
    seal_ms = deadlines.seal_ms
    milestones = [
        {"at_ms": 0, "label": "開始多方蒐證", "required_votes": None},
        {"at_ms": PRIMARY_ONLY_END_MS, "label": "允許可信二手來源", "required_votes": None},
        {
            "at_ms": deadlines.search_stop_ms,
            "label": "停止新增搜尋並整理已找到資料",
            "required_votes": None,
        },
        {
            "at_ms": deadlines.accept_until_ms,
            "label": "研究結果停止收件",
            "required_votes": None,
        },
        {
            "at_ms": seal_ms,
            "label": "封存證據並整理開場票",
            "required_votes": None,
        },
        {"at_ms": REPORT_DEADLINE_MS, "label": "正式報告期限", "required_votes": None},
        {"at_ms": TOTAL_WINDOW_MS, "label": "人工閱讀準備結束", "required_votes": None},
    ]
    milestones += [
        {
            "at_ms": seal_ms + vote_round.open_offset_ms,
            "label": "第 {} 輪開票".format(index),
            "required_votes": vote_round.threshold,
            "round": index,
        }
        for index, vote_round in enumerate(rules.vote_rounds, start=1)
    ]
    milestones.append(
        {
            "at_ms": seal_ms + rules.final_settle_offset_ms,
            "label": "硬停結算",
            "required_votes": rules.vote_rounds[-1].threshold,
        }
    )
    return sorted(milestones, key=lambda entry: entry["at_ms"])


def threshold_label(elapsed_ms, question_type=None):
    """The consensus threshold in force now, in words, from the rule authority.

    Read live like the timeline, and read as a phrase rather than a bare number
    because before the debate opens there is no threshold to state — a seat that
    has not been asked to vote cannot be short of votes. "Before the debate
    opens" is this run's actual seal, from
    :func:`~hoya_market_agents.research_scheduler.research_deadlines`, so a
    comparison run that seals thirty seconds later does not read as voting before its
    timeline still says the evidence is not sealed.
    """
    seal_ms = research_deadlines(question_type).seal_ms
    if elapsed_ms < seal_ms:
        return "尚未進入投票"
    return "{} 票".format(
        debate_rules().required_votes_at(elapsed_ms, seal_ms=seal_ms)
    )


def phase_label(elapsed_ms, timeline, state):
    """Name the phase as the milestone currently in force — one vocabulary.

    The phase is the last milestone the run has passed, so it is drawn from the
    same timeline the panel shows rather than a second set of phase words. A
    finished run is past all of them and says so; a run that has not begun is
    before the first.
    """
    if state == STATUS_FINISHED:
        return "已完成"
    if state == STATUS_WAITING:
        return "等待新的 run"
    passed = [rule for rule in timeline if elapsed_ms >= rule["at_ms"]]
    return passed[-1]["label"] if passed else "尚未開始"


def next_milestone(elapsed_ms, timeline):
    """The next milestone the run has not reached, or ``None`` past the last one."""
    for rule in timeline:
        if rule["at_ms"] > elapsed_ms:
            return {
                "label": rule["label"],
                "at_ms": rule["at_ms"],
                "remaining_ms": rule["at_ms"] - elapsed_ms,
            }
    return None


def focus_state(tally, upcoming, finished, outcome):
    """The leading stance, the score in words, the light, and what comes next.

    The leader and the score are read off the same ``tally`` the live panel
    draws, so the focus bar and the tally can never disagree. ``confidence_level``
    is the finished run's raw level or ``None`` — the render layer owns the word
    for it, so there is no second copy of the light vocabulary here. A run with
    one clear leader names it; a tie says so rather than picking one.
    """
    highest = max((entry["count"] for entry in tally), default=0)
    leaders = [entry for entry in tally if entry["count"] == highest and highest > 0]
    outcome = outcome or {}
    if finished and outcome.get("consensus_status") == "consensus" and outcome.get("adopted_label"):
        headline = "已達共識：{}".format(outcome["adopted_label"])
    elif len(leaders) == 1:
        headline = "目前{}領先".format(leaders[0]["label"])
    else:
        headline = NO_LEADER_HEADLINE
    tally_text = "｜".join(
        "{} {}".format(entry["label"], entry["count"]) for entry in tally
    ) or "票數尚未開始累計"
    return {
        "headline": headline,
        "tally_text": tally_text,
        "confidence_level": outcome.get("confidence_level") if finished else None,
        "next_label": "查看下一規則" if upcoming is None else upcoming["label"],
    }


def run_options(data_root, current_run_id):
    """Return the history runs the picker offers, newest first, from the index.

    The one path to a run listing is :func:`~hoya_market_agents.run_index.query_runs`
    — the room does not walk the runs tree for a second, disagreeing list. An
    absent or unreadable index is not an error of this room: a run may be live
    before it is indexed, so the picker simply offers nothing to go back to and
    the room still names the run it is watching.
    """
    try:
        rows = query_runs(data_root, limit=RUN_PICKER_LIMIT)
    except RunIndexError:
        return []
    options = []
    for row in rows:
        run_id = row.get("run_id")
        if not isinstance(run_id, str):
            continue
        options.append(
            {
                "run_id": run_id,
                "label": _option_label(row),
                "selected": run_id == current_run_id,
            }
        )
    return options


def _option_label(row):
    marks = [row.get("question") or "未記錄題目"]
    if row.get("report_path"):
        marks.append("有報告")
    return "｜".join(marks)


def _live_snapshot(data_root, run_id=None, clock=None):
    run_id, run_dir = resolve_live_run(data_root, run_id)
    if run_dir is None:
        return _waiting_snapshot(data_root)
    question = read_question(run_dir)
    room, offset, _, _ = open_room(run_dir, question)
    finished = run_finished(run_dir, run_id)
    state = STATUS_FINISHED if finished else STATUS_RUNNING
    elapsed_ms = authoritative_elapsed_ms(run_dir, question, clock=clock)
    # The seal milestone and the vote gate move with this run's question type;
    # ``ballot_for`` already reads the same field for the stance vocabulary.
    question_type = question.get("question_type") if isinstance(question, dict) else None
    tally = room.tally_views()
    timeline = rule_timeline(question_type)
    upcoming = next_milestone(elapsed_ms, timeline)
    outcome = run_outcome(data_root, run_id) if finished else None
    evidence, _ = views._read_evidence(run_dir)
    return {
        "state": state,
        "data_root": str(Path(data_root)),
        "run_id": run_id,
        "run_href": "/run/{}".format(run_id),
        "question": question.get("question") or run_id,
        "assets": _string_list(question.get("assets")),
        "asset_class": _asset_class_of(question),
        "messages": list(room.messages),
        "seats": room.seat_views(),
        "tally": tally,
        "changes": list(room.changes),
        "round": room.latest_round(),
        "elapsed_ms": elapsed_ms,
        "debate_started": room.debate_started or elapsed_ms >= research_deadlines(question_type).seal_ms,
        "debate_start_remaining_ms": debate_start_remaining_ms(
            elapsed_ms, question_type, room.debate_started
        ),
        "cursor": make_cursor(run_id, offset),
        "outcome": outcome,
        "report_available": (run_dir / "report.html").is_file(),
        "debate_report_available": (run_dir / "debate.html").is_file(),
        "run_options": run_options(data_root, run_id),
        "rules": timeline,
        "phase_label": phase_label(elapsed_ms, timeline, state),
        "threshold_label": threshold_label(elapsed_ms, question_type),
        "next_rule": upcoming,
        "focus": focus_state(tally, upcoming, finished, outcome),
        "evidence": evidence,
        "total_remaining_ms": max(0, TOTAL_WINDOW_MS - elapsed_ms),
        "report_remaining_ms": max(0, REPORT_DEADLINE_MS - elapsed_ms),
        "completion": completion_for(run_dir, run_id),
    }


def live_snapshot(data_root, run_id=None, clock=None):
    """Project one exact run using its question timestamp as the clock origin."""
    return _live_snapshot(data_root, run_id, clock=clock)


def _waiting_snapshot(data_root):
    timeline = rule_timeline()
    upcoming = next_milestone(0, timeline)
    return {
        "state": STATUS_WAITING,
        "data_root": str(Path(data_root)),
        "run_id": None,
        "run_href": None,
        "question": None,
        "assets": [],
        "asset_class": None,
        "messages": [],
        "seats": ChatRoom(MARKET_STANCES, {}).seat_views(),
        "tally": [],
        "changes": [],
        "round": None,
        "elapsed_ms": 0,
        "debate_started": False,
        "debate_start_remaining_ms": None,
        "cursor": None,
        "outcome": None,
        "report_available": False,
        "debate_report_available": False,
        "run_options": run_options(data_root, None),
        "rules": timeline,
        "phase_label": phase_label(0, timeline, STATUS_WAITING),
        "threshold_label": threshold_label(0),
        "next_rule": upcoming,
        "focus": focus_state([], upcoming, False, None),
        "evidence": [],
        "total_remaining_ms": TOTAL_WINDOW_MS,
        "report_remaining_ms": REPORT_DEADLINE_MS,
        "completion": None,
    }


def utc_now():
    return datetime.now(timezone.utc)


def authoritative_elapsed_ms(run_dir, question, clock=None):
    """Return the run clock, frozen only by a matching valid manifest."""
    manifest = _validated_final_manifest(run_dir, question.get("run_id"))
    if manifest is not None:
        frozen = _integer(manifest.get("elapsed_ms"))
        return frozen
    created = _parse_utc(question.get("created_at_utc"))
    if created is None:
        return 0
    now = (clock or utc_now)()
    if not isinstance(now, datetime):
        return 0
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0, int((now.astimezone(timezone.utc) - created).total_seconds() * 1000))


def debate_start_remaining_ms(elapsed_ms, question_type=None, opened=False):
    remaining = research_deadlines(question_type).seal_ms - elapsed_ms
    return None if opened or remaining <= 0 else remaining


def completion_for(run_dir, run_id):
    """Name the records this finished run actually produced, and no others.

    The report is what completion *means* — without one there is no completion
    to announce. The full debate record is separate: a run can end having written
    a report and no debate record, and offering one anyway would hand the reader
    a link to a file that was never written, dressed as this run's conclusion.
    So it is named only when this run's own manifest binds it by hash, and is
    ``None`` otherwise — the same answer, in the same field, either way.
    """
    manifest = _validated_final_manifest(run_dir, run_id)
    if manifest is None:
        return None
    report_href = _bound_artifact_href(run_dir, run_id, manifest, views.REPORT_ARTIFACT)
    if report_href is None:
        return None
    return {
        "report_href": report_href,
        "debate_href": _bound_artifact_href(
            run_dir, run_id, manifest, views.DEBATE_ARTIFACT
        ),
    }


def _bound_artifact_href(run_dir, run_id, manifest, name):
    """The href of one artifact this manifest binds by hash, else ``None``.

    A file the manifest does not name, or names with another digest, is not this
    run's record: it is a stray or a rewrite, and either way the room does not
    link to it.
    """
    path = Path(run_dir) / name
    if not path.is_file() or path.is_symlink():
        return None
    record = manifest["artifacts"].get(name)
    if not isinstance(record, dict):
        return None
    try:
        if path.stat().st_size <= 0:
            return None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
    if record.get("path") != name or record.get("sha256") != digest:
        return None
    return "/run/{}/{}".format(run_id, name)


def _validated_final_manifest(run_dir, run_id):
    """Return one canonical matching manifest, else fail closed as unfinished."""
    manifest = _read_json(Path(run_dir) / FINISHED_MARKER)
    if not manifest or manifest.get("run_id") != run_id:
        return None
    try:
        validate_run_manifest(manifest)
    except ContractViolationError:
        return None
    return manifest


def _parse_utc(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError:
        return None


def run_outcome(data_root, run_id):
    """Return the finished run's light and consensus, or ``None``.

    The numbers come from :func:`views.run_data`, which is already the one place
    a run's own records are read and labelled; asking it again here would be a
    second opinion about the same four files.
    """
    data = views.run_data(data_root, run_id)
    if data is None:
        return None
    confidence = data["confidence"] or {}
    consensus = data["consensus"]
    return {
        "run_id": run_id,
        "run_href": "/run/{}".format(run_id),
        "confidence_level": confidence.get("level"),
        "consensus_status": consensus["status"],
        "consensus_label": consensus["status_label"],
        "adopted_label": consensus["adopted_label"],
        "tally_view": consensus["tally_view"],
    }


# -- text ---------------------------------------------------------------------


def _read_json(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def first_sentence(text):
    """Return the sentence ``text`` opens with, or a capped opening of it.

    A full-width ``。``, ``！`` or ``？`` always ends a sentence. An ASCII
    ``.``, ``!`` or ``?`` ends one only when a space, a newline or the end of
    the text follows it, so a figure like ``3.5%`` is one number and not two
    sentences — cutting a brief in half there would show a conclusion the
    reason does not hold. Text that ends no sentence is cut at
    :data:`BRIEF_LIMIT` characters with an ellipsis, and text no longer than
    that is its own opening.

    這是唯一的斷句處：頁面與 SSE 走同一份訊息 view，所以兩邊看到的概述一定
    相同，瀏覽器端不必也不該再算一次。
    """
    for index in range(len(text)):
        if _is_sentence_end(text, index):
            return text[: index + 1]
    if len(text) > BRIEF_LIMIT:
        return text[:BRIEF_LIMIT] + BRIEF_ELLIPSIS
    return text


def _is_sentence_end(text, index):
    mark = text[index]
    if mark in FULL_STOPS:
        return True
    if mark not in ASCII_STOPS:
        return False
    # A slice rather than an index: past the last character it is ``""``, which
    # is the end of the text and so is a sentence end.
    next_character = text[index + 1 : index + 2]
    return not next_character or next_character.isspace()


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _string_list(value):
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]
