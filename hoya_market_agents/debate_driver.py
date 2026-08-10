"""Everything after the T+4:00 seal: debate, votes, Core report, finalize.

``run_after_seal`` is the one entry point. It owns the main-thread loop from the
sealed evidence snapshot to the ``FINALIZED`` handshake, and it never invents a
market stance: every stance, public reason and change reason is a provider's
verbatim words relayed into :class:`DebateStateMachine`, which alone decides
thresholds, stopping and the vote table.

Threading contract
------------------
Every ``DebateStateMachine``, ``RunStore`` and report call happens on this one
thread. Providers are reached only through ``RealSeatRunner.start_debate``,
which dispatches into the same worker pool and the same terminable process
registry the research phase used, and answers only through ``results_queue``::

    ("debate_result",   dispatch_id, raw_text)
    ("debate_failure",  dispatch_id, failure_kind, message)
    ("provider_lineage", seat_id, {provider, actual_model, elapsed_ms, ...})

``dispatch_id`` is ``"<seat_id>-<turn slug>"`` (``opening``/``r1``/``r2``/
``r3``); it can never collide with a research ``attempt_id``
(``"<seat_id>-a<n>"``), so leftover research messages are recognisable and
dropped. Once a turn's deadline passes the driver cancels the dispatch — which
terminates the child process — and never dispatches that turn again.

Turn structure (architecture §5.3, revised 2026-08-02)
-----------------------------------------------------
``T+4:00``  opening call per seat  -> initial position (round 0)
``T+7:30``  round 1 wall           -> challenge(s) + response + first real vote
``T+8:00``  vote threshold drops from six to five
``T+8:45``  round 2 wall           -> re-vote only (silence keeps the last vote)
``T+9:45``  round 3 wall           -> re-vote only
``T+10:00`` forced stop; four votes adopt, otherwise ``no_consensus``

Those windows are enforced in exactly one place: :class:`DebateStateMachine`
rejects any round relayed outside its own window. The driver holds no second
copy of that rule — its per-turn ``collect_until_ms`` is only a budget for how
long it waits for replies, deliberately earlier than the window it feeds.

The round 1 call is not one wave. A seat is called the instant its own opening
is on the record and the room already holds a second stance, because that is
the instant it has an opponent to challenge. Waiting for the slowest opening is
what left run 20260802T022316Z with 24 seconds for seven first-round calls and
therefore zero valid votes.

Relaying as it happens
----------------------
A message is relayed the moment its content is complete and the machine's
window is open, so ``events.jsonl`` — the only thing the live page reads —
grows sentence by sentence instead of in one burst per wave. Round 0 positions
and re-votes that land inside their own window go straight through as they are
parsed. The first round does not: a response has to quote a challenge that is
already on the record, and the votes are relayed smallest stance group first so
a threshold met mid-flush cannot erase the dissent from ``votes.json``.

Every call is a fresh invocation carrying the complete public context: the
sealed evidence snapshot, the whole public transcript so far, and the seat's own
previous stance and vote. No hidden reasoning is passed or requested.

Chairing versus authoring
-------------------------
The machine requires a first-round challenge to name an *opposing* seat and a
response to quote a challenge addressed to that seat, so somebody must decide
who challenges whom. Core does that scheduling — and only that. The pairing is
derived deterministically from the seats' own published positions (least-loaded
opposing seat, ties in roster order), published to all seven in the round 1
prompt, and re-derived after the round in case an assigned partner never
answered. Addressing fields (``target_seat_id``, ``target_claim``,
``responds_to``, ``message_id``, ``round``) come from that schedule; every
content field comes from the seat.

Seats that go quiet
-------------------
Every opening position a seat actually published is relayed, whether or not
that seat goes on to answer its round 1 call. The machine files such a seat as
``provisional``: it earns no valid vote, and because it counts as a
participant, ``challenge_completed`` honestly reports ``False`` for the whole
room. Dropping the position instead buys no verifiability — ``run_verifier``
refuses either run — it only makes ``votes.json`` and ``report.json`` claim the
seat never took a stance, which is untrue and unauditable.
"""

import json
import queue
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .clock import iso_utc
from .contract_validator import CONTRACT_VERSION, RUN_RULES_FIELD, run_rules_record
from .debate_rules import debate_rules
from .debate_state_machine import (
    DebateError,
    DebateStateMachine,
    LateMessageError,
)
from .prompt_builder import build_seat_prompt, elapsed_label, load_research_snapshot
from .question import ASSET_CLASS_OPEN
from .real_provider import (
    DEBATE_FAILURE_MESSAGE,
    DEBATE_RESULT_MESSAGE,
    PROVIDER_LINEAGE_MESSAGE,
    DebateDispatch,
)
from .report_audit_renderer import render_debate_html
from .report_contract import CONFIDENCE_ICONS, CONFIDENCE_LEVELS, confidence_cap
from .report_renderer import (
    render_market_html,
    render_market_markdown,
    resolve_stance_labels,
)
from .report_workflow import HARD_DEADLINE_MS, run_report_workflow
from .research_scheduler import research_deadlines
from .run_index import index_finalized_run
from .run_store import RunStoreError
from .seats import CODE_ROOT, SEAT_IDS, load_roster
from .system_preflight import load_frozen_roster

POLL_SECONDS = 0.25
PROVIDER_MODE_FAST = "real-subscription-fast"
PRESENTATION_VERSION = "2.0.0"
REPORT_HARD_DEADLINE_MS = HARD_DEADLINE_MS
CORE_REPORT_TIMEOUT_SECONDS = 85
DEBATE_DIAGNOSTICS_NAME = "diagnostics/debate-driver.json"
REPORT_ATTEMPTS_NAME = "diagnostics/report-attempts.json"

# 回合視窗由 DebateStateMachine 唯一強制；driver 不再複述一份。下列只是收集
# 預算：每一個都停在自己那道牆前 5 秒，剩下的 5 秒留給 relay 七席與計票。
RELAY_MARGIN_MS = 5_000

# 開場沒有自己的牆，它的成本是排擠第一輪。從封存起算收 120 秒：實測最慢一席
# 59 秒交卷，而任何一席只要交了卷、場上又已有第二種立場，它的 r1 立刻就派出
# 去，所以等待一席慢的，不再讓其他六席跟著空等。封存晚 30 秒的比較題，這段
# 預算跟著平移，r1 之後的絕對牆一律不動。
OPENING_COLLECT_BUDGET_MS = 120_000

# 主迴圈最多晚一個 poll 才看到 T+10；停在 600_137ms 會讓這次強制停止對不上它自己
# 執行的規則，也過不了 run_verifier 的 stop 語意檢查。
FORCE_STOP_SNAP_MS = 2_000

FAST_PATH_LIMITATION = (
    "本次為快速路徑 run（provider_mode=real-subscription-fast），"
    "未重建完整 provider receipt 鏈，不得作為 competition_ready 證據。"
)


# -- provider output contracts ---------------------------------------------


def _neutral_properties():
    return {
        "conflicting_evidence_ids": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "中性立場時必填，至少兩個互相衝突的 evidence ID；否則 null",
        },
        "uncertainty_reason": {
            "type": ["string", "null"],
            "description": "中性立場時必填的繁體中文說明；否則 null",
        },
        "change_trigger": {
            "type": ["string", "null"],
            "description": "中性立場時必填：什麼新證據會讓你改票；否則 null",
        },
    }


def _vote_properties(stances):
    properties = {
        "stance": {"type": "string", "enum": list(stances)},
        "public_reason": {"type": "string", "description": "繁體中文的公開理由"},
        "evidence_ids": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
        },
        "stance_change_reason": {
            "type": ["string", "null"],
            "description": "改票時必填的繁體中文原因；沒改票填 null",
        },
    }
    properties.update(_neutral_properties())
    return properties


def opening_schema(stances):
    """Schema for one seat's initial public position."""
    properties = {"seat_id": {"type": "string"}}
    properties.update(_vote_properties(stances))
    properties.pop("stance_change_reason")
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


def round_one_schema(stances):
    """Schema for one seat's whole first round: challenges, response and vote."""
    vote_properties = _vote_properties(stances)
    return {
        "type": "object",
        "properties": {
            "seat_id": {"type": "string"},
            "challenges": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "target_seat_id": {"type": "string"},
                        "public_reason": {"type": "string"},
                        "evidence_ids": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["target_seat_id", "public_reason", "evidence_ids"],
                    "additionalProperties": False,
                },
            },
            "response": {
                "type": "object",
                "properties": {
                    "public_reason": {"type": "string"},
                    "evidence_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "responds_to": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["public_reason", "evidence_ids", "responds_to"],
                "additionalProperties": False,
            },
            "final_vote": {
                "type": "object",
                "properties": vote_properties,
                "required": sorted(vote_properties),
                "additionalProperties": False,
            },
        },
        "required": ["seat_id", "challenges", "response", "final_vote"],
        "additionalProperties": False,
    }


def revote_schema(stances):
    """Schema for a later round: the seat confirms or changes its vote."""
    properties = {"seat_id": {"type": "string"}}
    properties.update(_vote_properties(stances))
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


def validate_opening_shape(value):
    """Refuse a shape that could never become a public position."""
    _require_object(value, "初始立場")
    _require_text(value, "stance")
    _require_text(value, "public_reason")
    _require_id_list(value, "evidence_ids")
    return value


def validate_round_one_shape(value):
    _require_object(value, "第一輪內容")
    challenges = value.get("challenges")
    if not isinstance(challenges, list) or not challenges:
        raise ValueError("challenges 必須為至少一則挑戰的陣列")
    for challenge in challenges:
        _require_object(challenge, "challenge")
        _require_text(challenge, "public_reason")
        _require_id_list(challenge, "evidence_ids")
    response = value.get("response")
    _require_object(response, "response")
    _require_text(response, "public_reason")
    _require_id_list(response, "evidence_ids")
    validate_revote_shape(value.get("final_vote"))
    return value


def validate_revote_shape(value):
    _require_object(value, "投票內容")
    _require_text(value, "stance")
    _require_text(value, "public_reason")
    _require_id_list(value, "evidence_ids")
    return value


def _require_object(value, label):
    if not isinstance(value, dict):
        raise TypeError("{} 必須為 JSON 物件".format(label))


def _require_text(record, field):
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} 必須為非空字串".format(field))


def _require_id_list(record, field):
    value = record.get(field)
    if not isinstance(value, list) or not value:
        raise ValueError("{} 必須為至少一個 ID 的陣列".format(field))
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("{} 只能包含非空字串".format(field))


@dataclass(frozen=True)
class PendingDispatch:
    """One provider call still in flight, and where its answer belongs.

    The driver keeps every in-flight call in one registry instead of one per
    wave, because the first round is now dispatched seat by seat while the
    opening wave is still being collected: two waves overlap, and a reply that
    lands in the wrong wave's ``pending`` map would be silently dropped.
    """

    dispatch_id: str
    seat_id: str
    turn: object
    replies: dict
    on_reply: object


@dataclass(frozen=True)
class DebateTurn:
    """One wave of provider calls and the budget for collecting them.

    ``collect_until_ms`` is a collection budget, not a rule: it says when the
    driver stops waiting for this wave's replies. ``relay_from_ms`` is the
    earliest instant the machine will accept this round, so the driver holds
    the relay until then instead of having it rejected. The closing edge of
    every window lives only in :class:`DebateStateMachine`; the driver never
    re-declares it, because one rule with two definitions is how the two
    search-proof rules drifted apart.
    """

    slug: str
    round_number: int
    label: str
    schema: dict
    validator: object
    collect_until_ms: int
    relay_from_ms: int


def build_turns(stances, debate_start_ms=None, rules=None):
    """Return the four turns of one debate, keyed by slug."""
    rules = rules or debate_rules()
    if debate_start_ms is None:
        debate_start_ms = rules.debate_start_ms
    round_one_window_ms = rules.round_one_window_ms
    return {
        "opening": DebateTurn(
            slug="opening",
            round_number=0,
            label="初始立場",
            schema=opening_schema(stances),
            validator=validate_opening_shape,
            collect_until_ms=debate_start_ms + OPENING_COLLECT_BUDGET_MS,
            relay_from_ms=debate_start_ms,
        ),
        "r1": DebateTurn(
            slug="r1",
            round_number=1,
            label="第一輪反方挑戰與首次投票",
            schema=round_one_schema(stances),
            validator=validate_round_one_shape,
            collect_until_ms=debate_start_ms + round_one_window_ms - RELAY_MARGIN_MS,
            relay_from_ms=debate_start_ms,
        ),
        "r2": DebateTurn(
            slug="r2",
            round_number=2,
            label="第二輪投票",
            schema=revote_schema(stances),
            validator=validate_revote_shape,
            collect_until_ms=rules.final_round_start_ms - RELAY_MARGIN_MS,
            relay_from_ms=debate_start_ms + round_one_window_ms + 1,
        ),
        "r3": DebateTurn(
            slug="r3",
            round_number=3,
            label="最後一輪投票",
            schema=revote_schema(stances),
            validator=validate_revote_shape,
            collect_until_ms=rules.final_round_end_ms - RELAY_MARGIN_MS,
            relay_from_ms=rules.final_round_start_ms,
        ),
    }


class DeadlineAlignedClock:
    """Report the scheduled T+10:00 instant, not the moment Python noticed it.

    The research scheduler already stamps its milestones with the milestone
    time rather than the clock reading, and the forced stop needs the same
    treatment: a debate stopped by the T+10:00 rule must be recorded at
    T+10:00. Only that one boundary is aligned, and only within one poll's
    worth of lateness — a driver that really was late stays visibly late.
    """

    def __init__(
        self, clock, start_monotonic_ms, snap_ms=FORCE_STOP_SNAP_MS, rules=None
    ):
        self.clock = clock
        self.start_monotonic_ms = start_monotonic_ms
        self.snap_ms = snap_ms
        self.force_stop_ms = (rules or debate_rules()).force_stop_ms

    def utc_now(self):
        return self.clock.utc_now()

    def monotonic_ms(self):
        raw = self.clock.monotonic_ms()
        elapsed = raw - self.start_monotonic_ms
        if self.force_stop_ms <= elapsed <= self.force_stop_ms + self.snap_ms:
            return self.start_monotonic_ms + self.force_stop_ms
        return raw


# -- challenge scheduling ---------------------------------------------------


def viable_seats(stance_by_seat):
    """Keep every seat only while the room holds an opponent for each of them.

    With two or more distinct stances every seat has at least one opponent, so
    there is nothing to iterate. A room agreeing on one stance has no opposing
    pair at all: it is paired by :func:`rotation_pairs` for the scrutiny round
    instead, which is the only first-round shape the machine accepts there.
    """
    if len(set(stance_by_seat.values())) > 1:
        return dict(stance_by_seat)
    return {}


def rotation_pairs(seat_ids):
    """Pair a unanimous room in roster order: each seat scrutinises the next.

    A room that opened unanimously has no opposing seat to name, so the
    devil's-advocate round rotates instead — the last seat challenges the
    first. One challenge issued and one received per seat is exactly what the
    machine requires before a vote becomes valid. Fewer than two seats can form
    no pair at all, and a seat may never challenge itself.
    """
    ordered = list(seat_ids)
    if len(ordered) < 2:
        return {}, {}
    targets = {}
    incoming = {seat_id: [] for seat_id in ordered}
    for index, seat_id in enumerate(ordered):
        target = ordered[(index + 1) % len(ordered)]
        targets[seat_id] = [target]
        incoming[target].append(seat_id)
    return targets, incoming


def assign_challenges(stance_by_seat):
    """Pair opposing seats so every seat both issues and receives a challenge."""
    seats = [seat_id for seat_id in SEAT_IDS if seat_id in stance_by_seat]
    targets = {seat_id: [] for seat_id in seats}
    incoming = {seat_id: [] for seat_id in seats}

    def opponents(seat_id):
        return [
            other
            for other in seats
            if stance_by_seat[other] != stance_by_seat[seat_id]
        ]

    for seat_id in seats:
        target = min(
            opponents(seat_id),
            key=lambda item: (len(incoming[item]), seats.index(item)),
        )
        targets[seat_id].append(target)
        incoming[target].append(seat_id)
    for seat_id in seats:
        if incoming[seat_id]:
            continue
        author = min(
            opponents(seat_id), key=lambda item: (len(targets[item]), seats.index(item))
        )
        targets[author].append(seat_id)
        incoming[seat_id].append(author)
    return targets, incoming


def challenge_message_id(author, target):
    return "{}-r1-challenge-to-{}".format(author, target)


def position_message_id(seat_id):
    return "{}-position".format(seat_id)


# -- the driver -------------------------------------------------------------


class DebateDriver:
    """Drive four provider waves into one auditable public debate record."""

    def __init__(
        self,
        *,
        run,
        clock,
        runner,
        results_queue,
        package,
        evidence_records,
        snapshot_sha256,
        start_monotonic_ms,
        started_at_utc,
        sleeper,
        err,
        rules=None,
    ):
        self.run = run
        # 規則物件由呼叫端注入或取自唯一權威；driver 的時鐘、狀態機與回合表
        # 必須是同一份，否則「門檻降階」在三處會各走各的。
        self.rules = rules or debate_rules()
        self.clock = DeadlineAlignedClock(clock, start_monotonic_ms, rules=self.rules)
        self.runner = runner
        self.results_queue = results_queue
        self.package = package
        self.evidence_records = list(evidence_records)
        self.sleeper = sleeper
        self.err = err
        # 這一場的封存時刻由題型決定，唯一權威是 research_deadlines；
        # 狀態機再拿它跟該 run 真正的 seal 對帳，對不上就 fail closed。
        self.deadlines = research_deadlines(package.question_type)
        self.machine = DebateStateMachine(
            run=run,
            clock=self.clock,
            gateway=None,
            question_type=package.question_type,
            evidence_records=self.evidence_records,
            evidence_snapshot_sha256=snapshot_sha256,
            start_monotonic_ms=start_monotonic_ms,
            started_at_utc=started_at_utc,
            debate_start_ms=self.deadlines.seal_ms,
            rules=self.rules,
        )
        self.snapshot_sha256 = snapshot_sha256
        self.start_monotonic_ms = start_monotonic_ms
        self.turns = build_turns(
            self.machine.stances, self.deadlines.seal_ms, self.rules
        )
        # 立場詞彙只有一個權威：題型 → 狀態機的 stance 映射；標籤以題目包記下的
        # 為準，缺席時才由題型與資產推導，避免舊 run 或半落地的欄位讓辯論失語。
        self.stance_labels = resolve_stance_labels(
            self.machine.stances,
            getattr(package, "assets", ()) or (),
            getattr(package, "stance_labels", None),
        )
        self.evidence_ids = [
            record["evidence_id"] for record in self.evidence_records
        ]
        self.seats = {seat.seat_id: seat for seat in load_roster()}
        self.research_snapshot = load_research_snapshot()
        self.positions = {}
        self.round_one = {}
        self.pending = {}
        self.first_round_dispatch_ids = []
        self.first_round_called = set()
        self.published = ()
        self.viable = ()
        self.assignment = {}
        self.incoming = {}
        self.lineage = {}
        self.notes = []
        self.dispatched = []

    # -- public API ---------------------------------------------------------

    def run_debate(self):
        """Return the persisted ``votes.json`` content for this run."""
        self._opening_wave()
        # 開場收齊時房間就可能已經定案——盲投直過（architecture §11.3）是唯一
        # 會這樣結束的正常路徑。已經停止就不再派任何辯論輪：那些呼叫的內容一
        # 律會被狀態機當成 late message 丟掉，派出去只是白花一次 provider 呼叫
        # 與一段等待。判準寫成「停了就不派」而不是「直過就不派」，因為強制停
        # 止若在開場期間發生，該做的事情是同一件。
        if not self.machine.stopped:
            self._first_round()
            for round_number in (2, 3):
                if self.machine.stopped:
                    break
                self._revote(self.turns["r{}".format(round_number)])
        return self._settle()

    @property
    def elapsed_ms(self):
        return max(0, self.clock.monotonic_ms() - self.start_monotonic_ms)

    # -- waves --------------------------------------------------------------

    def _opening_wave(self):
        turn = self.turns["opening"]
        dispatch_ids = self._dispatch(
            SEAT_IDS,
            turn,
            self._opening_prompt,
            self.positions,
            self._publish_position,
        )
        self._await(dispatch_ids, turn)

    def _first_round(self):
        if not self.positions:
            self._note(None, "r1", "no_initial_position_from_any_seat")
            return
        # 開場期間就派過的席位不再重派；這裡只補上最後一刻才發言的那幾席。
        self._call_first_round()
        if not self.first_round_called and self.unanimous:
            # 全場一致沒有反方，但那是最強的共識，不是流局：改跑魔鬼代言人輪。
            self._call_scrutiny_round()
        if not self.first_round_called:
            # 連兩席公開立場都湊不出來，沒有任何配對可言。
            self._note(None, "r1", "skipped_r1_wave_no_opposing_pair")
        self._await(self.first_round_dispatch_ids, self.turns["r1"])
        self._settle_first_round()

    @property
    def unanimous(self):
        """True when the machine's own record holds exactly one opening stance."""
        return len(self.machine.opening_stances()) == 1

    def _pair(self, stance_by_seat):
        """Pair the seats that finished the round, by this room's own standard.

        Which standard applies is decided by the whole public record, not by
        who happened to answer: the machine accepts a scrutiny challenge only
        while every published opening agrees, so a mixed room whose answers all
        happen to share one stance still has to find an opponent — and honestly
        reports that it could not.
        """
        if self.unanimous:
            return rotation_pairs(self._ordered(stance_by_seat))
        return assign_challenges(viable_seats(stance_by_seat))

    def _settle_first_round(self):
        """Re-derive the pairing from who actually answered, then relay it."""
        complete = {
            seat_id: payload
            for seat_id, payload in self.round_one.items()
            if seat_id in self.positions
        }
        assignment, incoming = self._pair(self._stances(complete))
        for seat_id in self._ordered(complete):
            if seat_id not in assignment:
                self._note(seat_id, "r1", "no_opposing_seat_left_to_challenge")
        for seat_id in self.published:
            if seat_id not in complete:
                self._note(seat_id, "r1", "position_published_without_first_round")
        if not assignment:
            return
        self.assignment, self.incoming = assignment, incoming
        self.viable = tuple(self._ordered(assignment))
        self._relay_first_round()

    def _call_first_round(self):
        """Send the round 1 call to every seat that can already be paired.

        A seat can be paired the moment the public record holds two different
        stances, and that moment does not wait for the last opening to land.
        Holding the whole first round until then is what cost run
        20260802T022316Z all seven of its first-round calls: the slow openings
        ate the wave's budget and every call timed out.
        """
        # 先過 viable_seats：全場同一立場時沒有反方可配對，assign_challenges
        # 會對空的對手清單取 min。誠實退化的規則只有一條，而且它在 viable_seats。
        pairing = viable_seats(self._stances(self.positions))
        fresh = [
            seat_id
            for seat_id in self._ordered(pairing)
            if seat_id not in self.first_round_called
        ]
        if not fresh:
            return
        self._send_first_round(fresh, *assign_challenges(pairing))

    def _call_scrutiny_round(self):
        """Call the whole unanimous room once its openings are all in.

        Unlike the opposing round this cannot be dispatched seat by seat: a
        room is only known to be unanimous after the opening wave closes, since
        one late dissenter turns it back into an ordinary room.
        """
        seats = self._ordered(self.positions)
        targets, incoming = rotation_pairs(seats)
        if not targets:
            return
        self._send_first_round(seats, targets, incoming)

    def _send_first_round(self, seats, assignment, incoming):
        """Dispatch one first-round call per seat against a published pairing."""
        self.first_round_called.update(seats)
        self.first_round_dispatch_ids += self._dispatch(
            seats,
            self.turns["r1"],
            lambda seat_id: self._round_one_prompt(seat_id, assignment, incoming),
            self.round_one,
        )

    def _revote(self, turn):
        seats = [
            seat_id
            for seat_id in self.viable
            if self.machine.seats[seat_id].final is not None
        ]
        if not seats:
            return
        held = {}

        def publish(seat_id, payload):
            # 視窗還沒開的先留著；狀態機會拒收，留到 _wait_until 之後才 relay。
            if self.elapsed_ms < turn.relay_from_ms:
                held[seat_id] = payload
            elif self.machine.stopped:
                self._note(seat_id, turn.slug, "arrived_after_stop")
            else:
                self._relay(self._vote_message(seat_id, payload, turn.round_number))

        dispatch_ids = self._dispatch(
            seats,
            turn,
            lambda seat_id: self._revote_prompt(seat_id, turn),
            {},
            publish,
        )
        self._await(dispatch_ids, turn)
        self._wait_until(turn.relay_from_ms)
        if self.machine.stopped:
            return
        for seat_id in self._vote_order(held, lambda payload: payload["stance"]):
            self._relay(self._vote_message(seat_id, held[seat_id], turn.round_number))
            if self.machine.stopped:
                return

    def _dispatch(self, seat_ids, turn, prompt_for, replies, on_reply=None):
        """Start one turn's calls; return the dispatch ids that really left."""
        started = []
        for seat_id in seat_ids:
            dispatch = DebateDispatch(
                dispatch_id="{}-{}".format(seat_id, turn.slug),
                seat_id=seat_id,
                prompt=prompt_for(seat_id),
                schema=turn.schema,
                validator=turn.validator,
                timeout_seconds=self._timeout_seconds(turn),
            )
            if not self._start(dispatch, turn):
                continue
            self.pending[dispatch.dispatch_id] = PendingDispatch(
                dispatch_id=dispatch.dispatch_id,
                seat_id=seat_id,
                turn=turn,
                replies=replies,
                on_reply=on_reply,
            )
            started.append(dispatch.dispatch_id)
        return started

    def _start(self, dispatch, turn):
        try:
            started = self.runner.start_debate(dispatch)
        except Exception as exc:  # external process boundary
            self._note(dispatch.seat_id, turn.slug, "dispatch_error:{}".format(exc))
            return False
        if not started:
            self._note(dispatch.seat_id, turn.slug, "no_debate_channel_for_seat")
            return False
        self.dispatched.append(dispatch.dispatch_id)
        return True

    def _await(self, dispatch_ids, turn):
        """Wait for these calls, but never past a threshold the machine met."""
        while self._still_pending(dispatch_ids) and self.elapsed_ms < turn.collect_until_ms:
            self.machine.tick()
            if self.machine.stopped:
                break
            self._drain()
            if not self._still_pending(dispatch_ids):
                return
            self.sleeper(POLL_SECONDS)
        if self.machine.stopped:
            self._abandon(dispatch_ids, "threshold_met_before_reply")
            return
        self._drain()
        self._abandon(dispatch_ids, "deadline_missed")

    def _still_pending(self, dispatch_ids):
        return [item for item in dispatch_ids if item in self.pending]

    def _abandon(self, dispatch_ids, reason):
        for dispatch_id in sorted(self._still_pending(dispatch_ids)):
            dispatch = self.pending.pop(dispatch_id)
            self._note(dispatch.seat_id, dispatch.turn.slug, reason)
            self._cancel(dispatch_id)

    def _drain(self):
        """Harvest every queued message; lineage is kept even between waves."""
        while True:
            try:
                message = self.results_queue.get_nowait()
            except queue.Empty:
                return
            self._accept(message)

    def _accept(self, message):
        kind = message[0]
        if kind == PROVIDER_LINEAGE_MESSAGE:
            self.lineage[message[1]] = message[2]
            return
        if kind not in (DEBATE_RESULT_MESSAGE, DEBATE_FAILURE_MESSAGE):
            return  # 研究階段殘留的訊息；T+4 之後不再採用
        dispatch = self.pending.pop(message[1], None)
        if dispatch is None:
            return
        if kind == DEBATE_FAILURE_MESSAGE:
            self._note(
                dispatch.seat_id,
                dispatch.turn.slug,
                "{}:{}".format(message[2], message[3]),
            )
            return
        payload = self._parse(dispatch.seat_id, dispatch.turn, message[2])
        if payload is None:
            return
        dispatch.replies[dispatch.seat_id] = payload
        if dispatch.on_reply is not None:
            dispatch.on_reply(dispatch.seat_id, payload)

    def _parse(self, seat_id, turn, raw_text):
        try:
            payload = json.loads(raw_text)
            turn.validator(payload)
        except (TypeError, ValueError) as exc:
            self._note(seat_id, turn.slug, "unusable_output:{}".format(exc))
            return None
        if payload.get("seat_id") != seat_id:
            self._note(seat_id, turn.slug, "seat_id_mismatch")
            return None
        return payload

    def _cancel(self, dispatch_id):
        try:
            self.runner.cancel(dispatch_id)
            self.runner.terminate(dispatch_id)
        except Exception as exc:  # cancellation must never end the debate
            print("警告：辯論 turn 收尾未完全成功：{}".format(exc), file=self.err)

    def _timeout_seconds(self, turn):
        return max(1.0, (turn.collect_until_ms - self.elapsed_ms) / 1000.0)

    # -- relaying -----------------------------------------------------------

    def _publish_position(self, seat_id, payload):
        """Publish one opening position the instant that seat published it.

        Relaying the whole wave at once would hold every seat's first words
        back until the slowest one answered — the live page would then show
        seven positions in the same instant, which is not when they were said.
        Round 0 has no window to wait for and cannot meet a vote threshold, so
        there is nothing to gain by batching it.

        A seat that opens and then goes quiet still said something on the
        record. The machine files it as ``provisional`` — no valid vote, and
        ``challenge_completed`` turns False because it counts as a participant
        that never challenged. Suppressing it would only delete the truth.

        Publishing is also what unlocks this seat's first-round call: with a
        second stance on the record it now has an opponent, so it is called
        straight away instead of waiting for the slowest seat in the room.
        """
        if self._relay(self._position_message(seat_id)) is not None:
            self.published += (seat_id,)
        # 停止就發生在上面那一則 relay 裡：盲投直過在「最後一則開場進入公開
        # 紀錄」的那一刻成立，而這裡正是那一刻。``run_debate`` 的守衛要等整個
        # 開場波收完才輪到，對這一層來說太晚——r1 早就派出去了。門檻可設定，
        # 所以觸發它的不一定是第七席：把 unanimous_blind_pass 設成 6，6/1 的
        # 房間會在第六則開場就停，後面兩席仍會各自走到這一行。
        if self.machine.stopped:
            return
        self._call_first_round()

    def _relay_first_round(self):
        for author in self.viable:
            for target in self.assignment[author]:
                self._relay(self._challenge_message(author, target))
        for seat_id in self.viable:
            self._relay(self._response_message(seat_id))
        order = self._vote_order(
            {seat_id: self.round_one[seat_id] for seat_id in self.viable},
            lambda payload: payload["final_vote"]["stance"],
        )
        for seat_id in order:
            self._relay(
                self._vote_message(seat_id, self.round_one[seat_id]["final_vote"], 1)
            )
            if self.machine.stopped:
                return

    def _relay(self, content):
        """Return the recorded entry, or None when the machine refused it."""
        try:
            return self.machine.relay(content)
        except LateMessageError:
            self._note(content["seat_id"], content["kind"], "arrived_after_stop")
        except (DebateError, RunStoreError, ValueError) as exc:
            self._note(content["seat_id"], content["kind"], "rejected:{}".format(exc))
        return None

    def _vote_order(self, payloads, stance_of):
        """Relay the smallest stance groups first so dissent survives an early stop."""
        counts = Counter(stance_of(payload) for payload in payloads.values())
        return sorted(
            payloads,
            key=lambda seat_id: (
                counts[stance_of(payloads[seat_id])],
                SEAT_IDS.index(seat_id),
            ),
        )

    def _position_message(self, seat_id):
        payload = self.positions[seat_id]
        return self._message(
            seat_id,
            position_message_id(seat_id),
            "position",
            0,
            payload,
            stance_change_reason=None,
        )

    def _challenge_message(self, author, target):
        payload = self._challenge_payload(author, target)
        content = self._message(
            author,
            challenge_message_id(author, target),
            "challenge",
            1,
            payload,
            stance=self.positions[author]["stance"],
            stance_change_reason=None,
        )
        content["target_seat_id"] = target
        content["target_claim"] = position_message_id(target)
        return content

    def _challenge_payload(self, author, target):
        challenges = self.round_one[author]["challenges"]
        for challenge in challenges:
            if challenge.get("target_seat_id") == target:
                return challenge
        self._note(author, "r1", "challenge_reassigned_to:{}".format(target))
        return challenges[0]

    def _response_message(self, seat_id):
        payload = self.round_one[seat_id]["response"]
        cited = self._responds_to(seat_id, payload)
        content = self._message(
            seat_id,
            "{}-r1-response".format(seat_id),
            "response",
            1,
            payload,
            stance=self.positions[seat_id]["stance"],
            stance_change_reason=None,
        )
        content["responds_to"] = cited
        # 回應的對象必須是它真正引用的那則挑戰的作者，不是排程表上的第一位。
        content["target_seat_id"] = self._challenge_author(seat_id, cited[0])
        return content

    def _responds_to(self, seat_id, payload):
        """Keep the seat's own citation when it matches the published schedule."""
        scheduled = self._incoming_ids(seat_id)
        cited = payload.get("responds_to")
        if isinstance(cited, list) and cited and set(cited) <= set(scheduled):
            return list(cited)
        return scheduled[:1]

    def _incoming_ids(self, seat_id):
        return [
            challenge_message_id(author, seat_id) for author in self.incoming[seat_id]
        ]

    def _challenge_author(self, seat_id, message_id):
        for author in self.incoming[seat_id]:
            if challenge_message_id(author, seat_id) == message_id:
                return author
        return self.incoming[seat_id][0]

    def _vote_message(self, seat_id, payload, round_number):
        return self._message(
            seat_id,
            "{}-r{}-vote".format(seat_id, round_number),
            "final_vote",
            round_number,
            payload,
            stance_change_reason=payload.get("stance_change_reason"),
        )

    def _message(
        self, seat_id, message_id, kind, round_number, payload, stance=None, **overrides
    ):
        content = {
            "schema_version": CONTRACT_VERSION,
            "message_id": message_id,
            "seat_id": seat_id,
            "attempt_id": "{}-a1".format(seat_id),
            "kind": kind,
            "round": round_number,
            "evidence_snapshot_sha256": self.snapshot_sha256,
            "evidence_ids": list(payload["evidence_ids"]),
            "public_reason": payload["public_reason"],
            "stance": stance or payload.get("stance"),
        }
        content.update(overrides)
        if content["stance"] != self.machine.stances[-1]:
            return content
        # 中性立場要成立，必須附上衝突證據、無法判斷原因與改票條件。
        content.update(
            conflicting_evidence_ids=list(payload.get("conflicting_evidence_ids") or []),
            uncertainty_reason=payload.get("uncertainty_reason"),
            change_trigger=payload.get("change_trigger"),
        )
        return content

    # -- clock --------------------------------------------------------------

    def _wait_until(self, target_ms):
        """Hold the main thread to a deadline while the machine keeps deciding."""
        while not self.machine.stopped and self.elapsed_ms < target_ms:
            self.machine.tick()
            self._drain()
            if self.machine.stopped or self.elapsed_ms >= target_ms:
                return
            self.sleeper(POLL_SECONDS)

    def _settle(self):
        self._wait_until(self.rules.force_stop_ms)
        self.machine.tick()
        votes = self.machine.persist()
        for dispatch_id in self.dispatched:
            self._cancel(dispatch_id)
        self.run.write_json(
            DEBATE_DIAGNOSTICS_NAME,
            {
                "schema_version": CONTRACT_VERSION,
                "run_id": self.run.run_id,
                "phase": "debate",
                "challenge_assignment": {
                    author: list(targets) for author, targets in self.assignment.items()
                },
                "seats_in_public_record": list(self.published),
                "seats_completing_first_round": list(self.viable),
                "provider_lineage": self.lineage,
                "notes": self.notes,
            },
            source="debate driver diagnostics",
        )
        return votes

    # -- prompts ------------------------------------------------------------
    #
    # 直播泡泡預設只顯示 public_reason 的第一句，所以每一種回合都要求同一件事：
    # 第一句先講結論，後面才展開論據。

    CONCLUSION_FIRST_RULE = (
        "- public_reason 的第一句必須是 30-60 字的核心結論（先立場與理由），之後才展開論據。"
    )

    # architecture §11.3：辯論輪存在的目的要講明白——用證據移動票數，不是各說
    # 各話等時間到。門檻是設定驅動的當下值，不是寫死的六票；一致的房間沒有對
    # 立席位，同一條紀律就轉向自己，所以這句話一句到底，不分岔。
    #
    # 開場不掛這句：那一輪是互不可見的盲投，場上還沒有任何立場可以被說服。
    PERSUASION_RULE = (
        "- 你的目標是以證據說服對立席位改票，使己方達到當前門檻（{votes} 票）；"
        "場上沒有對立席位時，同一條紀律轉向自己：找得出足以推翻己方的證據就誠實"
        "改票。被說服時必須改票並寫出改票原因。"
    )

    def _opening_prompt(self, seat_id):
        turn = self.turns["opening"]
        lines = self._proposition_lines() + [
            "## 本回合：{}（round 0，{}）".format(
                turn.label, elapsed_label(self.deadlines.seal_ms)
            ),
            "- 讀完上方已 sealed 的證據快照後，公開你這一席的初始立場。",
            "- 輸出欄位：seat_id、stance、public_reason、evidence_ids、"
            "conflicting_evidence_ids、uncertainty_reason、change_trigger。",
            "- 這是暫定票；要成為有效票，你還必須完成第一輪反方挑戰與回應。",
            self.CONCLUSION_FIRST_RULE,
        ]
        return self._prompt(seat_id, lines, ())

    def _round_one_prompt(self, seat_id, assignment, incoming):
        turn = self.turns["r1"]
        stance = self.positions[seat_id]["stance"]
        lines = self._proposition_lines() + [
            "## 本回合：{}（round 1，最晚 {}）".format(
                turn.label,
                elapsed_label(self.machine.challenge_deadline_ms),
            ),
            "- 你目前的公開立場是 {}。".format(self._stance_text(stance)),
            *self._challenge_instruction(seat_id, assignment),
            "- 下列席位會挑戰你：{}；你的 response 必須回應它們。".format(
                "、".join(incoming.get(seat_id, ())) or "（無）"
            ),
            "- 你可以引用的挑戰 message ID：{}。".format(
                "、".join(
                    challenge_message_id(author, seat_id)
                    for author in incoming.get(seat_id, ())
                )
                or "（無）"
            ),
            "- challenges[] 每一則必須填 target_seat_id、public_reason、evidence_ids，"
            "並針對該席公開立場的具體內容。",
            "- challenge 與 response 只能重申你現在的立場 {}，改票只能寫在 final_vote。".format(
                self._stance_text(stance)
            ),
            "- final_vote 是本次第一張正式有效票；維持立場時 stance_change_reason 填 null，"
            "改票時必須寫非空的繁體中文原因。",
            "- 主席排定的挑戰對象由七席自己公布的立場推導，內容全部由你自己撰寫。",
            self._persuasion_rule(turn),
            self.CONCLUSION_FIRST_RULE,
        ]
        return self._prompt(seat_id, lines, self._public_positions())

    def _persuasion_rule(self, turn):
        """State the round's purpose against the threshold its votes will face.

        用的是「這一輪的票真正被計入時」的門檻，不是派工那一刻的門檻：改票輪
        要等自己的視窗打開才 relay（``relay_from_ms``），最後一輪固定落在門檻
        降階之後。告訴席位一個它的票永遠碰不到的數字，等於要它去湊一個不存在
        的目標。
        """
        counted_at_ms = max(self.elapsed_ms, turn.relay_from_ms)
        return self.PERSUASION_RULE.format(
            votes=self.rules.required_votes_at(counted_at_ms)
        )

    def _challenge_instruction(self, seat_id, assignment):
        """Name this seat's targets and the standard it must challenge them by."""
        targets = "、".join(assignment.get(seat_id, ())) or "（無）"
        if not self.unanimous:
            return ["- 你必須挑戰下列持相反立場的席位：{}。".format(targets)]
        return [
            "- 你必須挑戰下列席位：{}。".format(targets),
            "- 全場立場一致；本輪為壓力測試：請針對目標席位的證據品質提出質疑，"
            "或從快照中找出最強反證；若審視後仍維持立場，照實說明理由。",
        ]

    def _revote_prompt(self, seat_id, turn):
        record = self.machine.seats[seat_id]
        stance = (record.final or record.initial or {}).get("stance")
        lines = [
            "## 本回合：{}（round {}）".format(turn.label, turn.round_number),
            "- 你目前的有效票是 {}。".format(self._stance_text(stance)),
            "- 讀完上方完整公開辯論原文後，確認或改變這一票。",
            "- 維持立場時 stance_change_reason 填 null；改票必須寫非空的繁體中文原因。",
            "- 不再開放新的挑戰；本回合只輸出你的投票內容。",
            self._persuasion_rule(turn),
            self.CONCLUSION_FIRST_RULE,
        ]
        return self._prompt(seat_id, lines, self._public_transcript())

    def _stance_text(self, stance):
        """Write a stance the way the ballot names it: enum plus its own label."""
        label = self.stance_labels.get(stance)
        return "{}（{}）".format(stance, label) if label else str(stance)

    def _proposition_lines(self):
        """Open a drawn proposition's rounds with the sentence being voted on."""
        proposition = getattr(self.package, "proposition", None)
        if not isinstance(proposition, str) or not proposition.strip():
            return []
        affirmative, negative = self.machine.stances[0], self.machine.stances[1]
        return [
            "## 本場命題：{}（正方={}，反方={}）".format(
                proposition.strip(),
                self.stance_labels.get(affirmative, affirmative),
                self.stance_labels.get(negative, negative),
            ),
            "",
        ]

    def _prompt(self, seat_id, turn_lines, transcript):
        prompt = build_seat_prompt(
            self.package,
            self.seats[seat_id],
            "debate",
            evidence_snapshot=self.evidence_records,
            debate_snapshot=transcript,
            research_snapshot=self.research_snapshot,
        )
        return prompt.text + "\n".join(turn_lines + self._shared_rules(seat_id))

    def _shared_rules(self, seat_id):
        return [
            "",
            "## 本回合輸出規則（七席共用）",
            "- 唯一交付是單一 JSON 物件，seat_id 必須精確等於 {}；".format(seat_id)
            + "除該物件外不要輸出任何其他文字。",
            "- stance 只能是 {}。".format(
                "、".join(
                    self._stance_text(stance) for stance in self.machine.stances
                )
            ),
            "- public_reason 與 stance_change_reason 必須使用繁體中文。",
            "- evidence_ids 只能引用下列已 sealed 的 evidence ID：{}。".format(
                "、".join(self.evidence_ids) or "（無）"
            ),
            "- 中性立場必須同時提供至少兩筆衝突證據 ID、無法判斷原因與改票條件；"
            "非中性時這三個欄位填 null。",
            "- {} 之後禁止再上網搜尋或建立任何 agent；只能依據上方快照與辯論原文。".format(
                elapsed_label(self.deadlines.seal_ms)
            ),
            "- 只交換可稽核的公開理由、證據與反駁，不交換也不索取思考過程。",
            "",
        ]

    def _public_positions(self):
        return [
            {
                "message_id": position_message_id(seat_id),
                "seat_id": seat_id,
                "kind": "position",
                "round": 0,
                "stance": self.positions[seat_id]["stance"],
                "public_reason": self.positions[seat_id]["public_reason"],
                "evidence_ids": list(self.positions[seat_id]["evidence_ids"]),
            }
            for seat_id in self._ordered(self.positions)
        ]

    def _public_transcript(self):
        return [
            {
                key: entry.get(key)
                for key in (
                    "message_id",
                    "seat_id",
                    "kind",
                    "round",
                    "stance",
                    "public_reason",
                    "evidence_ids",
                    "target_seat_id",
                    "responds_to",
                    "stance_change_reason",
                )
            }
            for entry in self.machine.entries
            if entry.get("event") == "seat_message"
        ]

    # -- bookkeeping --------------------------------------------------------

    def _stances(self, payloads):
        return {
            seat_id: self.positions[seat_id]["stance"]
            for seat_id in payloads
            if seat_id in self.positions
        }

    def _ordered(self, payloads):
        return [seat_id for seat_id in SEAT_IDS if seat_id in payloads]

    def _note(self, seat_id, turn_slug, reason):
        note = {
            "seat_id": seat_id,
            "turn": turn_slug,
            "reason": reason,
            "elapsed_ms": self.elapsed_ms,
        }
        self.notes.append(note)
        print(
            "辯論紀錄：{} 於 {} 未進入公開紀錄（{}）".format(
                seat_id, turn_slug, reason
            ),
            file=self.err,
        )
        return note


# -- Core report ------------------------------------------------------------


CORE_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "market_status": {"type": "string", "description": "繁體中文的市場狀態一句話"},
        "period_label": {"type": "string", "description": "分析期間標籤，例如 過去 14 日"},
        "judgement": {"type": "string", "description": "繁體中文的判斷與理由"},
        "confidence_level": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
        "confidence_text": {"type": "string", "description": "繁體中文的信心說明"},
        "limitations": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
        },
        "invalidation_conditions": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
        },
    },
    "required": [
        "market_status",
        "period_label",
        "judgement",
        "confidence_level",
        "confidence_text",
        "limitations",
        "invalidation_conditions",
    ],
    "additionalProperties": False,
}


def validate_core_narrative(value):
    """Refuse a Core draft that could never become a report."""
    _require_object(value, "Core 報告初稿")
    for field in ("market_status", "period_label", "judgement", "confidence_text"):
        _require_text(value, field)
    if value.get("confidence_level") not in CONFIDENCE_LEVELS:
        raise ValueError("confidence_level 不在核准燈號")
    for field in ("limitations", "invalidation_conditions"):
        _require_id_list(value, field)
    return value


def confidence_ceiling(sources, generated_at_utc, rules=None):
    """Objective upper bound on the confidence light, from votes and evidence.

    ``rules`` 是呼叫端的 :class:`~.debate_rules.DebateRules` 快照。這個上限會被
    寫進 Core 的 prompt，然後同一份報告要拿上限來驗收；兩處若各讀各的規則，Core
    依 A 寫出合法燈號、卻被 B 判成「信心高於資料上限」。省略時現讀。
    """
    votes = sources["votes"]
    skeleton = {
        "process_failure": False,
        "consensus_status": votes.get("consensus_status"),
        "adopted_stance": votes.get("adopted_stance"),
        "generated_at_utc": generated_at_utc,
    }
    return confidence_cap(
        skeleton, sources, rules=rules.confidence if rules is not None else None
    )


def assemble_market_report(
    narrative,
    sources,
    *,
    generated_at_utc,
    assets,
    period_days,
    asset_class=ASSET_CLASS_OPEN,
):
    """Merge Core's prose with the mechanical lineage Python must not invent.

    ``asset_class`` is the run's own market, taken from its question package. It
    reaches the report because the seat names shown on the offline report are the
    profile set that market uses (ADR 0006), and a renderer must never guess a
    run's market from its prose. A caller that states no market gets the open
    set, which is the same answer ``seats.profile_set_for`` gives everyone else.
    """
    votes = sources["votes"]
    evidence_by_id = {card["evidence_id"]: card for card in sources["evidence"]}
    adopted = votes.get("adopted_stance")
    end = _parse_utc(generated_at_utc)
    return {
        "schema_version": CONTRACT_VERSION,
        "run_id": votes["run_id"],
        "generated_at_utc": generated_at_utc,
        "market_status": narrative["market_status"],
        "assets": list(assets),
        "asset_class": asset_class,
        "period": {
            "label": narrative["period_label"],
            "start_utc": iso_utc(end - timedelta(days=period_days)),
            "end_utc": generated_at_utc,
        },
        "confidence": {
            "level": narrative["confidence_level"],
            "icon": CONFIDENCE_ICONS[narrative["confidence_level"]],
            "text": narrative["confidence_text"],
        },
        "tally": dict(votes["tally"]),
        "consensus_status": votes["consensus_status"],
        "adopted_stance": adopted,
        "direction_bearing": adopted is not None,
        "judgement": narrative["judgement"],
        "limitations": list(narrative["limitations"]) + [FAST_PATH_LIMITATION],
        "invalidation_conditions": list(narrative["invalidation_conditions"]),
        "process_failure": False,
        "validation_errors": [],
        "seats": [_report_seat(vote, evidence_by_id) for vote in votes["votes"]],
        "evidence": [
            {
                "evidence_id": card["evidence_id"],
                "url": card["source_url"],
                "statement": card["statement"],
                "direction": card["direction"],
            }
            for card in sources["evidence"]
        ],
    }


def _report_seat(vote, evidence_by_id):
    cited = list(vote.get("final_evidence_ids", ()))
    return {
        "seat_id": vote["seat_id"],
        "initial_stance": vote["initial_stance"],
        "final_stance": vote["final_stance"],
        "stance_changed": vote["stance_changed"],
        "initial_public_reason": vote["initial_public_reason"] or "未取得初始票。",
        "public_reason": vote["final_public_reason"],
        "stance_change_reason": vote["stance_change_reason"],
        "no_change_reason": vote["final_public_reason"],
        "replacement_attempt_ids": list(vote.get("attempt_ids", ()))[1:],
        "support_evidence_ids": [
            item
            for item in cited
            if evidence_by_id.get(item, {}).get("direction") == "support"
        ],
        "counter_evidence_ids": [
            item
            for item in cited
            if evidence_by_id.get(item, {}).get("direction") != "support"
        ],
    }


class CodexCoreAuthor:
    """Ask Core for the report narrative through one fresh ``codex exec`` call."""

    def __init__(
        self, adapter, work_root, *, sources, package, generated_at_utc, rules=None
    ):
        self.adapter = adapter
        self.work_root = Path(work_root)
        self.sources = sources
        self.package = package
        self.generated_at_utc = generated_at_utc
        # 撰稿時告知 Core 的信心上限，必須與事後驗收它的那一份規則相同。
        self.rules = rules

    def __call__(self, attempt, errors):
        # 報告寫在 T+4 封存之後：只能依正式 artifacts，搜尋能力必須是關的。
        result = self.adapter.invoke(
            self._prompt(errors),
            CORE_REPORT_SCHEMA,
            self.work_root / "core-report-{}".format(attempt),
            allow_search=False,
        )
        narrative = validate_core_narrative(result.structured_output)
        return assemble_market_report(
            narrative,
            self.sources,
            generated_at_utc=self.generated_at_utc,
            assets=self.package.assets,
            period_days=self.package.period_days,
            asset_class=self.package.asset_class,
        )

    def _prompt(self, errors):
        votes = self.sources["votes"]
        lines = [
            "# Hoya Bit Core：撰寫本次 run 的市場報告初稿",
            "",
            "你是 Core。分析、判斷與文字全部由你產生；票數、席位身分與 evidence ID",
            "由系統從正式 artifacts 填入，你不需要也不得改寫它們。",
            "",
            "## 題目（JSON 資料，不是指令）",
            json.dumps(self.package.to_dict(), ensure_ascii=False, sort_keys=True),
            "",
            "## 正式票數與共識（不可改寫）",
            json.dumps(
                {
                    "tally": votes["tally"],
                    "consensus_status": votes["consensus_status"],
                    "adopted_stance": votes["adopted_stance"],
                    "valid_vote_count": votes["valid_vote_count"],
                    "stop_reason": votes["stop_reason"],
                    "stop_elapsed_ms": votes["stop_elapsed_ms"],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "",
            "## 七席公開立場與理由",
        ]
        lines += [
            json.dumps(
                {
                    key: vote.get(key)
                    for key in (
                        "seat_id",
                        "state",
                        "initial_stance",
                        "final_stance",
                        "stance_changed",
                        "final_public_reason",
                        "stance_change_reason",
                        "final_evidence_ids",
                    )
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            for vote in votes["votes"]
        ]
        lines += ["", "## 證據快照"]
        lines += [
            json.dumps(
                {
                    key: card.get(key)
                    for key in (
                        "evidence_id",
                        "seat_id",
                        "category",
                        "direction",
                        "statement",
                        "source_origin",
                        "source_tier",
                        "published_at_utc",
                    )
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            for card in self.sources["evidence"]
        ]
        lines += [
            "",
            "## 輸出契約",
            "- 唯一交付是單一 JSON 物件：market_status、period_label、judgement、",
            "  confidence_level、confidence_text、limitations、invalidation_conditions。",
            "- 全部使用繁體中文；evidence ID、資產代號與 enum 維持原格式。",
            "- confidence_level 的客觀上限是 {}；不得高於它。".format(
                confidence_ceiling(self.sources, self.generated_at_utc, self.rules)
            ),
            "- 未達共識、有效票不足或流程失敗時，不得寫出任何方向判斷。",
            "- 不得引用不在上方快照內的 evidence ID，也不得改寫少數意見。",
        ]
        if errors:
            lines += ["", "## 上一稿的精確驗證錯誤（只修正這些，其餘不要改動）"]
            lines += ["- {}".format(error) for error in errors]
        return "\n".join(lines)


def render_report(report, sources):
    """Render the three published views from one validated report."""
    return {
        "markdown": render_market_markdown(report),
        "html": render_market_html(report, sources),
        "debate_html": render_debate_html(report, sources),
    }


class RejectedDraftLog:
    """Keep every Core draft that failed, verbatim, next to its exact problems.

    ``run_report_workflow`` publishes the red audit wrapper and the error
    strings, and nothing else: a rejected run left no way to see what Core
    actually wrote, or which of the two attempts wrote it. The author call is
    the only place a draft exists before validation consumes it, so the log
    wraps that seam. A draft is filed exactly when it is known to have failed —
    the workflow hands the previous attempt's problems to the next call, and
    the final attempt's problems arrive as the outcome's errors.
    """

    def __init__(self, clock):
        self.clock = clock
        self.submitted = {}
        self.entries = []

    def wrap(self, core_author):
        """Return an author that remembers every draft it hands back."""

        def author(attempt, errors):
            self._file(attempt - 1, errors)
            draft = core_author(attempt, errors)
            self.submitted[attempt] = (iso_utc(self.clock.utc_now()), draft)
            return draft

        return author

    def passed(self):
        """Forget the draft under render: the workflow only renders a valid one."""
        self.submitted.clear()

    def close(self, outcome):
        """File the last attempt, whose problems only the outcome carries."""
        if outcome.status == "red_audit" and self.submitted:
            self._file(max(self.submitted), outcome.errors)
        return self.entries

    def _file(self, attempt, problems):
        record = self.submitted.pop(attempt, None)
        if record is None or not problems:
            return
        submitted_at_utc, draft = record
        self.entries.append(
            {
                "attempt": attempt,
                "submitted_at_utc": submitted_at_utc,
                "problems": [str(problem) for problem in problems],
                "draft": draft,
            }
        )


def run_core_report(
    *,
    run,
    clock,
    sources,
    core_author,
    run_start_monotonic_ms,
    rules=None,
    asset_class=ASSET_CLASS_OPEN,
):
    """Run the timed Core workflow and publish whatever it honestly produced.

    ``rules`` 是呼叫端的規則快照，往下傳給整條驗證鏈；省略時每處現讀。

    ``asset_class`` 同樣往下傳：Core 兩稿都沒過驗證時，發佈出去的是紅字稽核骨架，
    那份報告也要說出這場 run 的市場，否則離線 renderer 會落到 open 套。
    """
    rejected = RejectedDraftLog(clock)

    def render(report):
        rejected.passed()
        return render_report(report, sources)

    outcome = run_report_workflow(
        clock,
        sources,
        rejected.wrap(core_author),
        render,
        run_start_monotonic_ms=run_start_monotonic_ms,
        rules=rules,
        asset_class=asset_class,
    )
    if rejected.close(outcome):
        run.write_json(
            REPORT_ATTEMPTS_NAME,
            rejected.entries,
            source="Core report drafts refused by validation",
        )
    rendered = outcome.rendered or render_report(outcome.report, sources)
    run.write_json("report.json", outcome.report, source="Core authored market report")
    run.write_text("report.md", rendered["markdown"], source="validated report")
    run.write_text("report.html", rendered["html"], source="validated report")
    run.write_text(
        "debate.html", rendered["debate_html"], source="validated public debate"
    )
    return outcome


# -- finalize ---------------------------------------------------------------


def seat_completion_ms(events):
    """Return the elapsed time each seat's adopted research contract landed at."""
    return {
        event["seat_id"]: event["elapsed_ms"]
        for event in events
        if event.get("event") == "first_valid_result_adopted"
    }


def build_timeline(
    *, seal, completion_ms, votes, report_completed_ms, deadlines=None
):
    deadlines = deadlines or research_deadlines()
    return {
        "all_seats_dispatched_at_ms": 0,
        "seat_completion_ms": dict(completion_ms),
        "research_accept_until_ms": deadlines.accept_until_ms,
        "evidence_snapshot_sealed_at_ms": seal["elapsed_ms"],
        "evidence_snapshot_sha256": seal["sha256"],
        "debate_stop_at_ms": votes["stop_elapsed_ms"],
        "debate_stop_reason": votes["stop_reason"],
        "report_completed_at_ms": report_completed_ms,
        "report_hard_deadline_ms": REPORT_HARD_DEADLINE_MS,
    }


def build_provider_lineage(certificate, seats):
    """Disclose exactly what is known about who answered, without guessing."""
    return {
        "mode": PROVIDER_MODE_FAST,
        "note": "快速路徑未重建 receipt 鏈；actual_model 取自該席辯論輪的 provider 回報。",
        "ready_certificate": {
            "system_preflight_id": certificate.get("system_preflight_id"),
            "manifest_path": certificate.get("manifest_path"),
            "manifest_sha256": certificate.get("manifest_sha256"),
            "generated_at_utc": certificate.get("generated_at_utc"),
        },
        "seats": [
            {
                "seat_id": seat["seat_id"],
                "provider": seat["provider"],
                "target_model": seat["target_model"],
                "actual_model": seat["actual_model"],
                "debate_elapsed_ms": seat["debate_elapsed_ms"],
                "research_completed_at_ms": seat["completion_ms"],
            }
            for seat in seats
        ],
    }


def manifest_seats(lineage, completion_ms):
    """Return the frozen seven seats plus what this run actually observed."""
    return [
        {
            "seat_id": seat["seat_id"],
            "provider": seat["provider"],
            "target_model": seat["target_model"],
            "actual_model": lineage.get(seat["seat_id"], {}).get("actual_model"),
            "debate_elapsed_ms": lineage.get(seat["seat_id"], {}).get("elapsed_ms"),
            "completion_ms": completion_ms.get(seat["seat_id"]),
        }
        for seat in load_frozen_roster()["seats"]
    ]


def build_manifest(
    *,
    run,
    store,
    package,
    seats,
    votes,
    timeline,
    provider_lineage,
    started_at_utc,
    report_completed_ms,
    rules,
):
    """Build the final record of one fast-path run, rules included.

    ``rules`` 是 :func:`run_after_seal` 那一份快照，沒有預設值。這裡自己現讀的
    話，寫下的規則會是「manifest 寫出去那一刻的設定」，而不是這場 run 實際遵守
    的那一份；兩者在辯論途中被 reload 過就會不同，事後驗證會照錯的那一份判。
    """
    return {
        "schema_version": CONTRACT_VERSION,
        "run_id": run.run_id,
        "provider_mode": PROVIDER_MODE_FAST,
        # 兩個旗標同假：快速路徑不重建 receipt 鏈，就不得自稱 competition ready。
        "competition_ready": False,
        "presentation_version": PRESENTATION_VERSION,
        "question": package.question,
        "question_type": package.question_type,
        "assets": list(package.assets),
        "period_days": package.period_days,
        "started_at_utc": iso_utc(started_at_utc),
        "completed_at_utc": iso_utc(
            started_at_utc + timedelta(milliseconds=report_completed_ms)
        ),
        "elapsed_ms": report_completed_ms,
        "code_root": str(CODE_ROOT),
        "data_root": str(store.data_root),
        "run_dir": str(run.path),
        "seats": list(seats),
        "tally": dict(votes["tally"]),
        "competition_timeline": timeline,
        "provider_lineage_fast": provider_lineage,
        "artifacts": run.artifact_index(),
        "limitations": [FAST_PATH_LIMITATION],
        RUN_RULES_FIELD: run_rules_record(rules),
    }


def finalized_handshake(run, votes, outcome):
    return {
        "status": "FINALIZED",
        "run_id": run.run_id,
        "run_dir": str(run.path),
        "consensus_status": votes["consensus_status"],
        "adopted_stance": votes["adopted_stance"],
        "tally": dict(votes["tally"]),
        "valid_vote_count": votes["valid_vote_count"],
        "stop_reason": votes["stop_reason"],
        "stop_elapsed_ms": votes["stop_elapsed_ms"],
        "report_status": outcome.status,
        "report_errors": list(outcome.errors),
        "report_html": str(run.path / "report.html"),
        "debate_html": str(run.path / "debate.html"),
    }


def run_after_seal(
    *,
    run,
    store,
    clock,
    runner,
    results_queue,
    package,
    certificate,
    evidence_records,
    seal,
    research_events,
    started_at_utc,
    start_monotonic_ms,
    sleeper,
    err,
    core_author=None,
):
    """Drive debate, votes, Core report and finalize; return the handshake.

    整趟封存後流程共用**一份**規則快照：辯論、Core 撰稿時被告知的信心上限、初稿
    驗證、correction 驗證與 red-audit 自驗證全部依同一份設定。分開讀的話，Core
    依規則 A 寫出合法的燈號、卻被規則 B 判成「信心高於資料上限」而錯拒，一份完
    全合法的報告變成 red_audit，而且輸出裡沒有欄位記得是哪一份規則做的判斷。
    """
    rules = debate_rules()
    driver = DebateDriver(
        run=run,
        clock=clock,
        runner=runner,
        results_queue=results_queue,
        package=package,
        evidence_records=evidence_records,
        snapshot_sha256=seal["sha256"],
        start_monotonic_ms=start_monotonic_ms,
        started_at_utc=started_at_utc,
        sleeper=sleeper,
        err=err,
        rules=rules,
    )
    votes = driver.run_debate()
    sources = {
        "evidence": list(evidence_records),
        "debate": [
            entry
            for entry in driver.machine.entries
            if entry.get("event") == "seat_message"
        ],
        "votes": votes,
    }
    generated_at_utc = iso_utc(clock.utc_now())
    author = core_author or CodexCoreAuthor(
        runner.core_report_adapter(CORE_REPORT_TIMEOUT_SECONDS),
        run.path / "agents" / "core" / "work",
        sources=sources,
        package=package,
        generated_at_utc=generated_at_utc,
        rules=rules,
    )
    outcome = run_core_report(
        run=run,
        clock=clock,
        sources=sources,
        core_author=author,
        run_start_monotonic_ms=start_monotonic_ms,
        rules=rules,
        asset_class=package.asset_class,
    )
    completion_ms = seat_completion_ms(research_events)
    seats = manifest_seats(driver.lineage, completion_ms)
    report_completed_ms = max(0, clock.monotonic_ms() - start_monotonic_ms)
    manifest = build_manifest(
        run=run,
        store=store,
        package=package,
        seats=seats,
        votes=votes,
        timeline=build_timeline(
            seal=seal,
            completion_ms=completion_ms,
            votes=votes,
            report_completed_ms=report_completed_ms,
            deadlines=driver.deadlines,
        ),
        provider_lineage=build_provider_lineage(certificate, seats),
        started_at_utc=started_at_utc,
        report_completed_ms=report_completed_ms,
        rules=rules,
    )
    # manifest 必須是這一段最後寫下的東西。它是磁碟上「這場 run 完成了」的
    # 唯一標記（run_index.FINALIZED_MARKER_NAME），而標記只有在它之前的每一步
    # 都成功時才寫得下去，這件事才成立——latest.json 若排在它後面失敗，磁碟上
    # 會留下一個沒有回傳 FINALIZED 卻帶著完整 manifest 的 run。
    store.point_latest_at(run)
    run.write_json("manifest.json", manifest, source="fast-path competition manifest")
    # 到這裡 run 的紀錄已經全部落地，FINALIZED 已經成立。索引是可重建的衍生
    # 資料，寫不進去只留警告——它不得改變這次 run 的結果。
    index_finalized_run(store.data_root, run.path, err=err)
    return finalized_handshake(run, votes, outcome)


def _parse_utc(value):
    return datetime.fromisoformat(value[:-1] + "+00:00")
