"""Drive the sealed evidence snapshot through discrete ballots and reporting.

``run_after_seal`` is the single entry point.  The driver derives its opening
and between-ballot turns from the run's ``vote_rounds`` array, relays provider
words unchanged, and leaves all ballot snapshots and stop decisions to
``DebateStateMachine``.  A turn reply is optional: at each ballot wall the
state machine counts that seat's latest public stance.
"""

import json
import queue
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


def revote_schema(stances):
    """Schema for one optional between-ballot public stance update."""
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
    """Return opening plus one free-debate turn between adjacent ballots."""
    rules = rules or debate_rules()
    if debate_start_ms is None:
        debate_start_ms = rules.debate_start_ms
    first_wall_ms = debate_start_ms + rules.vote_rounds[0].open_offset_ms
    turns = {
        "opening": DebateTurn(
            slug="opening",
            round_number=0,
            label="初始立場",
            schema=opening_schema(stances),
            validator=validate_opening_shape,
            collect_until_ms=first_wall_ms - RELAY_MARGIN_MS,
            relay_from_ms=debate_start_ms,
        )
    }
    for index, (opened, next_round) in enumerate(
        zip(rules.vote_rounds, rules.vote_rounds[1:]), start=1
    ):
        slug = "r{}".format(index)
        turns[slug] = DebateTurn(
            slug=slug,
            round_number=index,
            label="第 {} 輪開票後自由辯論".format(index),
            schema=revote_schema(stances),
            validator=validate_revote_shape,
            collect_until_ms=(
                debate_start_ms + next_round.open_offset_ms - RELAY_MARGIN_MS
            ),
            relay_from_ms=debate_start_ms + opened.open_offset_ms,
        )
    return turns


class DeadlineAlignedClock:
    """Report the scheduled T+10:00 instant, not the moment Python noticed it.

    The research scheduler already stamps its milestones with the milestone
    time rather than the clock reading, and the forced stop needs the same
    treatment: a debate stopped by the T+10:00 rule must be recorded at
    T+10:00. Only that one boundary is aligned, and only within one poll's
    worth of lateness — a driver that really was late stays visibly late.
    """

    def __init__(self, clock, start_monotonic_ms, force_stop_ms, snap_ms=FORCE_STOP_SNAP_MS):
        self.clock = clock
        self.start_monotonic_ms = start_monotonic_ms
        self.snap_ms = snap_ms
        self.force_stop_ms = force_stop_ms

    def utc_now(self):
        return self.clock.utc_now()

    def monotonic_ms(self):
        raw = self.clock.monotonic_ms()
        elapsed = raw - self.start_monotonic_ms
        if self.force_stop_ms <= elapsed <= self.force_stop_ms + self.snap_ms:
            return self.start_monotonic_ms + self.force_stop_ms
        return raw


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
        self.deadlines = research_deadlines(package.question_type)
        self.final_settle_ms = (
            self.deadlines.seal_ms + self.rules.final_settle_offset_ms
        )
        self.clock = DeadlineAlignedClock(
            clock, start_monotonic_ms, self.final_settle_ms
        )
        self.runner = runner
        self.results_queue = results_queue
        self.package = package
        self.evidence_records = list(evidence_records)
        self.sleeper = sleeper
        self.err = err
        # 這一場的封存時刻由題型決定，唯一權威是 research_deadlines；
        # 狀態機再拿它跟該 run 真正的 seal 對帳，對不上就 fail closed。
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
        self.seats = {seat.seat_id: seat for seat in load_roster()}
        self.research_snapshot = load_research_snapshot()
        self.positions = {}
        self.pending = {}
        self.published = ()
        self.completed_turns = {}
        self.lineage = {}
        self.notes = []
        self.dispatched = []

    # -- public API ---------------------------------------------------------

    def run_debate(self):
        """Return the persisted ``votes.json`` content for this run."""
        self._opening_wave()
        for turn in tuple(self.turns.values())[1:]:
            self._wait_until(turn.relay_from_ms)
            if self.machine.stopped:
                break
            self._free_debate(turn)
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

    def _free_debate(self, turn):
        seats = [seat_id for seat_id in self.published if self.machine.seats[seat_id].initial]
        if not seats:
            self._note(None, turn.slug, "no_public_position_to_debate")
            return
        replies = {}

        def publish(seat_id, payload):
            if self.machine.stopped:
                self._note(seat_id, turn.slug, "arrived_after_stop")
                return
            if self._relay(self._vote_message(seat_id, payload, turn.round_number)):
                self.completed_turns.setdefault(turn.slug, []).append(seat_id)

        dispatch_ids = self._dispatch(
            seats,
            turn,
            lambda seat_id: self._free_debate_prompt(seat_id, turn),
            replies,
            publish,
        )
        self._await(dispatch_ids, turn)

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
        """Publish an opening immediately; it is already eligible at a wall."""
        if self._relay(self._position_message(seat_id)) is not None:
            self.published += (seat_id,)

    def _relay(self, content):
        """Return the recorded entry, or None when the machine refused it."""
        try:
            return self.machine.relay(content)
        except LateMessageError:
            self._note(content["seat_id"], content["kind"], "arrived_after_stop")
        except (DebateError, RunStoreError, ValueError) as exc:
            self._note(content["seat_id"], content["kind"], "rejected:{}".format(exc))
        return None

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
        # The loop condition becomes false exactly on the wall.  Evaluate that
        # wall before the caller can dispatch the following turn.
        if not self.machine.stopped:
            self.machine.tick()

    def _settle(self):
        self._wait_until(self.final_settle_ms)
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
                "seats_in_public_record": list(self.published),
                "completed_free_debate_turns": {
                    slug: list(seat_ids)
                    for slug, seat_ids in self.completed_turns.items()
                },
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

    PERSUASION_RULE = (
        "- 用證據說服對方，也重新檢查自己的判斷；不盲從，也不死守。"
        "被說服時必須改票並寫出改票原因，目標是達到下一輪門檻（{votes} 票）。"
    )

    def _opening_prompt(self, seat_id):
        turn = self.turns["opening"]
        lines = self._proposition_lines() + [
            "## 本回合：{}（round 0，{}）".format(
                turn.label, elapsed_label(self.deadlines.seal_ms)
            ),
            "- 讀完上方僅屬於本席的 sealed 證據視圖後，公開你這一席的初始立場。",
            "- 輸出欄位：seat_id、stance、public_reason、evidence_ids、"
            "conflicting_evidence_ids、uncertainty_reason、change_trigger。",
            "- 第一輪開票會採用開票當下這一席最新公開立場；本回合後不必另交票才有效。",
            self.CONCLUSION_FIRST_RULE,
        ]
        return self._prompt(seat_id, lines, (), private_evidence=True)

    def _persuasion_rule(self, turn):
        """State the purpose against the next ballot wall's threshold."""
        counted_at_ms = turn.collect_until_ms + RELAY_MARGIN_MS
        return self.PERSUASION_RULE.format(
            votes=self.rules.required_votes_at(
                counted_at_ms, seal_ms=self.deadlines.seal_ms
            )
        )

    def _free_debate_prompt(self, seat_id, turn):
        record = self.machine.seats[seat_id]
        stance = (record.final or record.initial or {}).get("stance")
        lines = self._proposition_lines() + [
            "## 本回合：{}（round {}）".format(turn.label, turn.round_number),
            "- 你目前的公開立場是 {}。".format(self._stance_text(stance)),
            "- 讀完完整證據快照與公開辯論原文後，自由回應最值得處理的證據或觀點。",
            "- 維持立場時 stance_change_reason 填 null；改票必須寫非空的繁體中文原因。",
            "- 輸出這一席最新的公開立場；沒有回覆時，上一張公開立場仍會留到下個開票牆。",
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

    def _prompt(self, seat_id, turn_lines, transcript, *, private_evidence=False):
        visible_evidence = (
            [
                record
                for record in self.evidence_records
                if record.get("seat_id") == seat_id
            ]
            if private_evidence
            else self.evidence_records
        )
        prompt = build_seat_prompt(
            self.package,
            self.seats[seat_id],
            "debate",
            evidence_snapshot=() if private_evidence else visible_evidence,
            debate_snapshot=transcript,
            research_snapshot=self.research_snapshot,
            evidence_view=visible_evidence if private_evidence else (),
        )
        return prompt.text + "\n".join(
            turn_lines + self._shared_rules(seat_id, visible_evidence)
        )

    def _shared_rules(self, seat_id, visible_evidence):
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
                "、".join(record["evidence_id"] for record in visible_evidence)
                or "（無）"
            ),
            "- 中性立場必須同時提供至少兩筆衝突證據 ID、無法判斷原因與改票條件；"
            "非中性時這三個欄位填 null。",
            "- {} 之後禁止再上網搜尋或建立任何 agent；只能依據上方快照與辯論原文。".format(
                elapsed_label(self.deadlines.seal_ms)
            ),
            "- 只交換可稽核的公開理由、證據與反駁，不交換也不索取思考過程。",
            "",
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
            "# AI agnets debating chamber Core：撰寫本次 run 的市場報告初稿",
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
