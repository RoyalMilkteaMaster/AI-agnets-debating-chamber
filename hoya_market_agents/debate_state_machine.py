"""Shared-transcript debate and consensus state machine (Ticket #9).

Seven logical seats exchange verbatim public entries against one already sealed
evidence snapshot. Core only relays, validates, hashes and tallies: it never
rewrites a seat's content and never chooses or changes a vote. A replacement is
still the same seat, so the vote table always has exactly seven rows.

Deadlines are absolute and monotonic. The machine never sleeps; it reads an
injected clock, and the injected gateway is the only source of seat content.
"""

import hashlib
import json
from copy import deepcopy
from datetime import timedelta

from .clock import iso_utc
from .contract_validator import CONTRACT_VERSION
from .debate_rules import debate_rules
from .question_package import (
    COMPARISON_STANCES,
    EVENT_STANCES,
    MARKET_STANCES,
    OPEN_QUESTION_TYPE,
    OPEN_STANCES,
)
from .run_store import RunStoreError
from .seats import SEAT_IDS

STANCES_BY_QUESTION_TYPE = {
    "single_asset_market_state": MARKET_STANCES,
    "overall_market_state": MARKET_STANCES,
    "two_asset_comparison": COMPARISON_STANCES,
    "event_impact": EVENT_STANCES,
    OPEN_QUESTION_TYPE: OPEN_STANCES,
    # Ticket #4's contract names remain accepted until its callers migrate.
    "single_asset": MARKET_STANCES,
    "market": MARKET_STANCES,
    "comparison": COMPARISON_STANCES,
    "event": EVENT_STANCES,
}
# 每套詞彙的第三個選項都是「不表態」：中性票的加重舉證（衝突證據、不確定
# 原因、改變條件）必須四型一致，否則 open 模式的「無法決定」會變成舉證
# 負擔最低的逃生門。
NEUTRAL_STANCES = (
    MARKET_STANCES[-1],
    COMPARISON_STANCES[-1],
    EVENT_STANCES[-1],
    OPEN_STANCES[-1],
)

MESSAGE_KINDS = ("position", "challenge", "response", "final_vote")

# architecture §11.3：opening 是互不可見的盲投，七席各自獨立得出同一個結論是本
# 系統拿得到的最強共識，所以它直接停止並產報告，取代 §5.4 對全票情境「仍須一輪
# 反方挑戰」的要求。停止原因刻意不叫 consensus_<n>_votes：那一串是「辯論後的有
# 效票達到當下階梯」，和這裡的「開場票」不是同一種票。
UNANIMOUS_BLIND_PASS = "unanimous_blind_pass"


class DebateError(RuntimeError):
    """Base error; every subclass means the machine failed closed."""


class LateMessageError(DebateError):
    """A message arrived after the debate stopped or after T+10."""


class DuplicateMessageError(DebateError):
    """A message id was replayed."""


class UnknownEvidenceError(DebateError):
    """A message cited an evidence id outside the sealed snapshot."""


class EvidenceSnapshotMismatchError(DebateError):
    """A seat cited a different or tampered evidence snapshot."""


class CoreOverrideError(DebateError):
    """Core attempted to write or rewrite a seat's vote or content."""


class UnknownSeatError(DebateError):
    """A message claimed a seat outside the fixed seven."""


class UnknownAttemptError(DebateError):
    """A message carried an attempt id with no lineage for its seat."""


class ReplacementHistoryError(DebateError):
    """A replacement voted without replaying the complete public history."""


class StanceError(DebateError):
    """A stance is not one of the three enums for this question type."""


class DebateLifecycleError(DebateError):
    """A message skipped a required public debate step."""


def stances_for(question_type):
    """Return the three approved stance enums for a question type."""
    try:
        return STANCES_BY_QUESTION_TYPE[question_type]
    except KeyError as exc:
        raise DebateError("未知 question_type：{!r}".format(question_type)) from exc


def required_votes_at(elapsed_ms, rules=None):
    """Return the absolute vote threshold in force at an elapsed time."""
    return (rules or debate_rules()).required_votes_at(elapsed_ms)


def phase_at(elapsed_ms, challenge_deadline_ms=None, rules=None):
    """Name the deadline window an elapsed time falls in."""
    return (rules or debate_rules()).phase_at(elapsed_ms, challenge_deadline_ms)


def content_sha256(content):
    """Stable digest of one verbatim public entry's content."""
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def snapshot_digest(records):
    """Recompute the sealed snapshot digest exactly as the run store wrote it."""
    text = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ScriptedDebateGateway:
    """A deterministic stand-in for the seven providers.

    Each scripted message may carry ``advance_ms``; the gateway consumes it and
    moves the injected clock, so tests control deadlines without sleeping. The
    remaining fields are the seat's own content and are relayed verbatim.
    """

    mode = "scripted"

    def __init__(self, messages, clock=None):
        self._messages = [dict(message) for message in messages]
        self.clock = clock

    def bind(self, clock):
        self.clock = clock

    def messages(self):
        for message in self._messages:
            content = dict(message)
            advance_ms = content.pop("advance_ms", 0)
            if advance_ms and self.clock is not None:
                self.clock.advance_ms(advance_ms)
            yield content


class SeatRecord:
    """One logical seat's public position history across its attempts."""

    def __init__(self, seat_id):
        self.seat_id = seat_id
        self.attempt_ids = []
        self.initial = None
        self.final = None
        self.messages = []
        self.challenges = []
        self.responses = []
        self.vote_changes = []
        self.invalid_reason = None

    @property
    def state(self):
        if self.invalid_reason:
            return "invalid"
        if self.final is not None:
            return "valid"
        if self.initial is not None:
            return "provisional"
        return "missing"


class DebateStateMachine:
    """Relay, validate, hash and tally the shared debate room."""

    def __init__(
        self,
        run,
        clock,
        gateway,
        question_type,
        evidence_records,
        evidence_snapshot_sha256,
        seat_ids=SEAT_IDS,
        start_monotonic_ms=None,
        started_at_utc=None,
        debate_start_ms=None,
        rules=None,
    ):
        self.run = run
        self.clock = clock
        self.gateway = gateway
        self.question_type = question_type
        self.rules = rules or debate_rules()
        self.debate_start_ms = (
            self.rules.debate_start_ms if debate_start_ms is None else debate_start_ms
        )
        # 第一輪牆是相對制：以該 run 實際封存的時刻起算一整個 round_one_window。
        self.challenge_deadline_ms = self.rules.challenge_deadline_ms(
            self.debate_start_ms
        )
        self.stances = stances_for(question_type)
        self.seat_ids = tuple(seat_ids)
        self.evidence_records = list(evidence_records)
        self.evidence_snapshot_sha256 = evidence_snapshot_sha256
        actual = snapshot_digest(self.evidence_records)
        if actual != evidence_snapshot_sha256:
            raise EvidenceSnapshotMismatchError(
                "Evidence snapshot 已遭竄改：expected={} actual={}".format(
                    evidence_snapshot_sha256, actual
                )
            )
        try:
            seal = run.verify_evidence_snapshot()
        except RunStoreError as exc:
            raise EvidenceSnapshotMismatchError(
                "無法驗證 sealed Evidence snapshot：{}".format(exc)
            ) from exc
        if (
            seal.get("sha256") != evidence_snapshot_sha256
            or seal.get("record_count") != len(self.evidence_records)
            or seal.get("elapsed_ms") != self.debate_start_ms
        ):
            raise EvidenceSnapshotMismatchError(
                "傳入 Evidence snapshot 與 RunStore seal 不一致。"
            )
        self.known_evidence_ids = {
            record.get("evidence_id") for record in self.evidence_records
        }
        if start_monotonic_ms is None:
            raise DebateLifecycleError(
                "必須提供整個 Run 的 start_monotonic_ms，不能在 T+4 重設時間。"
            )
        self.start_monotonic_ms = start_monotonic_ms
        current_elapsed = max(0, clock.monotonic_ms() - self.start_monotonic_ms)
        if current_elapsed < self.debate_start_ms:
            raise DebateLifecycleError(
                "Evidence snapshot 尚未到 T+{}ms，不得啟動辯論。".format(
                    self.debate_start_ms
                )
            )
        self.started_at_utc = started_at_utc or (
            clock.utc_now() - timedelta(milliseconds=current_elapsed)
        )
        if getattr(gateway, "clock", None) is None and hasattr(gateway, "bind"):
            gateway.bind(clock)

        self.entries = []
        self.seats = {seat_id: SeatRecord(seat_id) for seat_id in self.seat_ids}
        self.public_history_sha256 = hashlib.sha256(b"").hexdigest()
        self.seen_message_ids = set()
        self.stopped = False
        self.stop_reason = None
        self.stop_elapsed_ms = None
        self.result = None
        self._append_entry(
            "debate_opened",
            {
                "question_type": question_type,
                "evidence_snapshot_sha256": evidence_snapshot_sha256,
                "evidence_record_count": len(self.evidence_records),
                "seat_ids": list(self.seat_ids),
            },
        )

    @property
    def elapsed_ms(self):
        return max(0, self.clock.monotonic_ms() - self.start_monotonic_ms)

    def run_debate(self):
        """Drive the scripted gateway until a stop rule fires, then persist."""
        if self.gateway is None:
            raise DebateLifecycleError("run_debate 需要 scripted gateway。")
        for content in self.gateway.messages():
            self.relay(content)
        if not self.stopped:
            self.tick()
        if not self.stopped:
            raise DebateLifecycleError("scripted gateway 在正式停止條件前耗盡。")
        return self.persist()

    def relay(self, content, actor="seat"):
        """Validate one verbatim seat message, record it and re-check stopping."""
        original = deepcopy(content)
        if actor != "seat":
            self._append_entry(
                "core_override_rejected",
                {
                    "actor": actor,
                    "content": original,
                    "content_sha256": content_sha256(original),
                },
            )
            raise CoreOverrideError("Core 只能原文 relay，不得建立或改寫席位內容。")
        self._evaluate_stop()
        if self.stopped:
            elapsed = self.elapsed_ms
            self._reject(original, "late_message", elapsed)
            if self.result is None:
                self.persist()
            raise LateMessageError(
                "訊息於 {}ms 抵達，deadline 已先結算；fail closed。".format(elapsed)
            )
        entry = self._accept(original)
        self._evaluate_stop()
        return deepcopy(entry)

    def tick(self):
        """Apply elapsed-time thresholds using the injected monotonic clock."""
        if not self.stopped:
            self._evaluate_stop()
        if self.stopped and self.result is None:
            self.persist()
        return self.stop_reason

    def override_vote(self, seat_id, stance, reason="core override"):
        """Core may never write a vote; this always fails closed."""
        self._append_entry(
            "core_override_rejected",
            {"seat_id": seat_id, "attempted_stance": stance, "reason": reason},
        )
        raise CoreOverrideError(
            "Core 不得代替席位 {} 決定或改寫立場。".format(seat_id)
        )

    # -- validation ---------------------------------------------------------

    def _accept(self, content):
        elapsed = self.elapsed_ms
        seat_id = content.get("seat_id")
        if seat_id not in self.seats:
            self._reject(content, "unknown_seat", elapsed)
            raise UnknownSeatError("未知 seat_id：{!r}".format(seat_id))
        seat = self.seats[seat_id]

        kind = content.get("kind")
        if kind not in MESSAGE_KINDS:
            self._reject(content, "unknown_message_kind", elapsed)
            raise DebateError("未知 message kind：{!r}".format(kind))
        if content.get("schema_version") != CONTRACT_VERSION:
            self._reject(content, "unknown_schema_version", elapsed)
            raise DebateError(
                "debate message 必須使用 schema_version {}。".format(CONTRACT_VERSION)
            )

        message_id = content.get("message_id")
        if not isinstance(message_id, str) or not message_id.strip():
            self._reject(content, "missing_message_id", elapsed)
            raise DebateError("message_id 必須為非空字串。")
        if message_id in self.seen_message_ids:
            self._reject(content, "duplicate_message_id", elapsed)
            raise DuplicateMessageError("message_id {} 重複。".format(message_id))

        if (
            self.stopped
            or elapsed >= self.rules.force_stop_ms
            or elapsed > self.rules.final_round_end_ms
        ):
            self._reject(content, "late_message", elapsed)
            raise LateMessageError(
                "訊息於 {}ms 抵達，辯論已結束；fail closed。".format(elapsed)
            )

        cited = content.get("evidence_snapshot_sha256")
        if cited != self.evidence_snapshot_sha256:
            self._reject(content, "evidence_snapshot_mismatch", elapsed)
            raise EvidenceSnapshotMismatchError(
                "席位 {} 引用的 snapshot {!r} 與已 sealed 快照不符。".format(seat_id, cited)
            )

        evidence_ids = content.get("evidence_ids")
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or any(not isinstance(item, str) or not item.strip() for item in evidence_ids)
        ):
            self._reject(content, "missing_evidence_ids", elapsed)
            raise DebateError("evidence_ids 必須為至少一個 evidence ID 的陣列。")
        unknown = [item for item in evidence_ids if item not in self.known_evidence_ids]
        if unknown:
            self._reject(content, "unknown_evidence_id", elapsed)
            raise UnknownEvidenceError(
                "引用未知 evidence ID：{}".format(", ".join(str(item) for item in unknown))
            )

        if (
            not isinstance(content.get("public_reason"), str)
            or not content["public_reason"].strip()
        ):
            self._reject(content, "missing_public_reason", elapsed)
            raise DebateError("public_reason 必須為非空字串。")

        lineage = self._validate_lineage(seat, content, elapsed)
        extra = {}

        round_number = content.get("round")
        if kind == "position":
            if type(round_number) is not int or round_number != 0 or seat.initial is not None:
                self._reject(content, "invalid_or_duplicate_initial_position", elapsed)
                raise DebateLifecycleError(
                    "initial position 必須是該席唯一的 round 0 position。"
                )
        elif (
            not isinstance(round_number, int)
            or isinstance(round_number, bool)
            or round_number not in (1, 2, 3)
        ):
            self._reject(content, "invalid_round", elapsed)
            raise DebateLifecycleError("debate round 必須為 1、2 或 3。")
        elif elapsed <= self.challenge_deadline_ms and round_number != 1:
            self._reject(content, "round_does_not_match_deadline", elapsed)
            raise DebateLifecycleError(
                "第一輪牆（{}ms）前只能提交第一輪內容。".format(
                    self.challenge_deadline_ms
                )
            )
        elif (
            self.challenge_deadline_ms < elapsed < self.rules.final_round_start_ms
            and round_number != 2
        ):
            self._reject(content, "round_does_not_match_deadline", elapsed)
            raise DebateLifecycleError(
                "第一輪牆後、最後一輪（{}ms）開始前只能提交第二輪內容。".format(
                    self.rules.final_round_start_ms
                )
            )
        elif elapsed >= self.rules.final_round_start_ms and round_number != 3:
            self._reject(content, "round_does_not_match_deadline", elapsed)
            raise DebateLifecycleError(
                "最後一輪（{}ms）開始後只能提交最後一輪內容。".format(
                    self.rules.final_round_start_ms
                )
            )

        stance = content.get("stance")
        if stance not in self.stances:
            self._reject(content, "stance_not_in_enum", elapsed)
            raise StanceError(
                "stance {!r} 不屬於 {} 的三個立場 {}。".format(
                    stance, self.question_type, "/".join(self.stances)
                )
            )
        change_reason = content.get("stance_change_reason")
        if change_reason is not None and not isinstance(change_reason, str):
            self._reject(content, "invalid_stance_change_reason", elapsed)
            raise DebateLifecycleError("stance_change_reason 必須為字串或 null。")
        if kind in ("challenge", "response"):
            current = seat.final or seat.initial
            if current is None or stance != current.get("stance") or change_reason is not None:
                self._reject(content, "intermediate_message_changed_vote", elapsed)
                raise DebateLifecycleError(
                    "challenge/response 只能重申現有立場，改票必須用 final_vote。"
                )
        if kind in ("position", "final_vote"):
            if stance in NEUTRAL_STANCES:
                conflicting = content.get("conflicting_evidence_ids")
                unknown_conflicts = (
                    []
                    if not isinstance(conflicting, list)
                    else [item for item in conflicting if item not in self.known_evidence_ids]
                )
                if (
                    not isinstance(conflicting, list)
                    or len(conflicting) < 2
                    or unknown_conflicts
                    or not isinstance(content.get("uncertainty_reason"), str)
                    or not content["uncertainty_reason"].strip()
                    or not isinstance(content.get("change_trigger"), str)
                    or not content["change_trigger"].strip()
                ):
                    self._reject(content, "incomplete_neutral_position", elapsed)
                    raise DebateLifecycleError(
                        "中性立場必須列出至少兩筆衝突證據、無法判斷原因與改票所需新證據。"
                    )
        if kind == "challenge":
            if elapsed > self.challenge_deadline_ms or seat.initial is None:
                self._reject(content, "late_or_missing_initial_challenge", elapsed)
                raise DebateLifecycleError(
                    "第一輪 challenge 必須在第一輪牆（{}ms）前且先有 position。".format(
                        self.challenge_deadline_ms
                    )
                )
            for field in ("target_seat_id", "target_claim"):
                value = content.get(field)
                if not isinstance(value, str) or not value.strip():
                    self._reject(content, "incomplete_challenge", elapsed)
                    raise DebateError("challenge 必須指名 {}。".format(field))
            if content["target_seat_id"] not in self.seats:
                self._reject(content, "unknown_seat", elapsed)
                raise UnknownSeatError(
                    "challenge 指向未知 seat_id：{!r}".format(content["target_seat_id"])
                )
            mode = self._challenge_mode(seat, content["target_seat_id"])
            if mode is None:
                self._reject(content, "challenge_not_opposing", elapsed)
                raise DebateError(
                    "第一輪挑戰必須針對相反立場的席位；只有全場立場一致時才放寬為"
                    "壓力測試。"
                )
            extra["challenge_mode"] = mode
            target = self._seat_message(content["target_claim"])
            if target is None or target["content"].get("seat_id") != content["target_seat_id"]:
                self._reject(content, "unknown_target_claim", elapsed)
                raise DebateLifecycleError("challenge 的 target_claim 必須是目標席位的公開原文。")
        elif kind == "response":
            responds_to = content.get("responds_to")
            if not isinstance(responds_to, list) or not responds_to:
                self._reject(content, "missing_challenge_response_target", elapsed)
                raise DebateLifecycleError("response 必須引用至少一個 challenge message ID。")
            challenges = [self._seat_message(message_id) for message_id in responds_to]
            if any(
                entry is None or entry["content"].get("kind") != "challenge"
                for entry in challenges
            ):
                self._reject(content, "unknown_challenge_response_target", elapsed)
                raise DebateLifecycleError("response 引用不存在或非 challenge 的公開訊息。")
            if any(
                entry["content"].get("target_seat_id") != seat.seat_id
                for entry in challenges
            ):
                self._reject(content, "challenge_addressed_to_another_seat", elapsed)
                raise DebateLifecycleError(
                    "response 只能引用 target_seat_id 指向自己的 challenge。"
                )
            target_seat_id = content.get("target_seat_id")
            if target_seat_id not in {
                entry["content"].get("seat_id") for entry in challenges
            }:
                self._reject(content, "response_target_seat_mismatch", elapsed)
                raise DebateLifecycleError(
                    "response 的 target_seat_id 必須是所引用 challenge 的作者。"
                )
        elif kind == "final_vote":
            completed_first_round = (
                seat.initial is not None
                and any(item.get("round") == 1 for item in seat.challenges)
                and any(item.get("round") == 1 for item in seat.responses)
            )
            if not completed_first_round:
                self._reject(content, "final_vote_before_required_debate", elapsed)
                raise DebateLifecycleError(
                    "final vote 前必須完成 initial position、反方 challenge 與 response。"
                )
            before = seat.final or seat.initial
            changed = before.get("stance") != content.get("stance")
            reason = content.get("stance_change_reason")
            if changed and (not isinstance(reason, str) or not reason.strip()):
                self._reject(content, "changed_vote_missing_reason", elapsed)
                raise DebateLifecycleError("改票必須保存非空 stance_change_reason。")

        return self._record(seat, content, elapsed, lineage, extra)

    def _seat_message(self, message_id):
        for entry in self.entries:
            if entry.get("event") == "seat_message" and entry.get("message_id") == message_id:
                return entry
        return None

    def opening_stances(self):
        """Return the distinct stances the published openings actually hold."""
        return {
            seat.initial["stance"]
            for seat in self.seats.values()
            if seat.initial is not None
        }

    def opening_tally(self):
        """Count the published blind openings by stance."""
        counts = {stance: 0 for stance in self.stances}
        for seat in self.seats.values():
            if seat.initial is not None:
                counts[seat.initial["stance"]] += 1
        return counts

    @property
    def debate_rounds_started(self):
        """True once any seat published something beyond its blind opening.

        判準是公開紀錄本身：只要出現過 challenge、response 或 final_vote，這一
        場就不再是「互不可見的盲投」，直過的前提也就消失了。讀 entries 而不是
        席位的 ``final``，是因為直過本身會把開場記成最終票——那不能反過來使這
        個判斷變成 True。
        """
        return any(
            entry.get("event") == "seat_message" and entry.get("kind") != "position"
            for entry in self.entries
        )

    def _challenge_mode(self, seat, target_seat_id):
        """Name the standard this first-round challenge is judged by.

        ``opposing`` is the normal room: a challenge has to name a seat holding
        a different stance. ``scrutiny`` is the devil's-advocate round a room
        whose openings are unanimous gets instead — there is no opposite to
        name, and a unanimous room is the strongest consensus available, not a
        room that failed to debate. ``None`` means the challenge meets neither
        standard and must be refused.
        """
        if self._is_opposing(seat, target_seat_id):
            return "opposing"
        if target_seat_id != seat.seat_id and len(self.opening_stances()) == 1:
            return "scrutiny"
        return None

    def _is_opposing(self, seat, target_seat_id):
        target = self.seats[target_seat_id]
        if target.seat_id == seat.seat_id:
            return False
        if seat.initial is None or target.initial is None:
            return True
        return target.initial["stance"] != seat.initial["stance"]

    def _validate_lineage(self, seat, content, elapsed):
        attempt_id = content.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            self._reject(content, "unknown_attempt", elapsed)
            raise UnknownAttemptError("attempt_id 必須為非空字串。")
        if attempt_id in seat.attempt_ids:
            return None
        if not seat.attempt_ids:
            expected = "{}-a1".format(seat.seat_id)
            if attempt_id != expected:
                self._reject(content, "unknown_attempt", elapsed)
                raise UnknownAttemptError(
                    "席位 {} 的初始 attempt 必須為 {}。".format(seat.seat_id, expected)
                )
            return {"kind": "primary", "attempt_id": attempt_id}
        expected = "{}-a{}".format(seat.seat_id, len(seat.attempt_ids) + 1)
        if attempt_id != expected:
            self._reject(content, "unknown_attempt", elapsed)
            raise UnknownAttemptError(
                "席位 {} 的下一個 attempt 必須為 {}。".format(seat.seat_id, expected)
            )
        replayed = content.get("replayed_history_sha256")
        if replayed != self.public_history_sha256:
            self._reject(content, "replacement_history_not_replayed", elapsed)
            raise ReplacementHistoryError(
                "席位 {} 的替補 {} 未重播並證明完整公開歷史。".format(seat.seat_id, attempt_id)
            )
        return {
            "kind": "replacement",
            "attempt_id": attempt_id,
            "replaced_attempt_id": seat.attempt_ids[-1],
            "public_history_sha256": replayed,
        }

    # -- recording ----------------------------------------------------------

    def _record(self, seat, content, elapsed, lineage, extra=None):
        if lineage is not None:
            seat.attempt_ids.append(lineage["attempt_id"])
            if lineage["kind"] == "replacement":
                self._append_entry(
                    "replacement_replayed_public_history",
                    {
                        "seat_id": seat.seat_id,
                        "attempt_id": lineage["attempt_id"],
                        "replaced_attempt_id": lineage["replaced_attempt_id"],
                        "replayed_history_sha256": lineage["public_history_sha256"],
                    },
                    elapsed=elapsed,
                )
        digest = content_sha256(content)
        entry = self._append_entry(
            "seat_message",
            {
                "seat_id": seat.seat_id,
                "attempt_id": content["attempt_id"],
                "kind": content["kind"],
                "message_id": content["message_id"],
                "claim_id": content["message_id"],
                "round": content["round"],
                "stance": content["stance"],
                "public_reason": content["public_reason"],
                "evidence_ids": list(content["evidence_ids"]),
                "target_seat_id": content.get("target_seat_id"),
                "responds_to": list(content.get("responds_to", [])),
                "stance_change_reason": content.get("stance_change_reason"),
                "content": content,
                "content_sha256": digest,
                **(extra or {}),
            },
            elapsed=elapsed,
        )
        self.seen_message_ids.add(content["message_id"])
        seat.messages.append(entry)

        kind = content["kind"]
        if kind == "position" and seat.initial is None:
            seat.initial = dict(content)
        elif kind == "challenge":
            seat.challenges.append(dict(content))
        elif kind == "response":
            seat.responses.append(dict(content))
        elif kind == "final_vote":
            before = seat.final or seat.initial
            change = {
                "message_id": content["message_id"],
                "before": before.get("stance"),
                "after": content.get("stance"),
                "reason": content.get("stance_change_reason"),
                "public_reason": content.get("public_reason"),
                "evidence_ids": list(content.get("evidence_ids", [])),
                "elapsed_ms": elapsed,
            }
            seat.vote_changes.append(change)
            seat.invalid_reason = None
            seat.final = dict(content)
        return entry

    def _reject(self, content, reason, elapsed):
        self._append_entry(
            "message_rejected",
            {
                "reason": reason,
                "seat_id": content.get("seat_id"),
                "attempt_id": content.get("attempt_id"),
                "message_id": content.get("message_id"),
                "content": content,
                "content_sha256": content_sha256(content),
            },
            elapsed=elapsed,
        )

    def _record_rejection(self, content, reason):
        self._reject(content, reason, self.elapsed_ms)

    def _append_entry(self, event, detail, elapsed=None):
        elapsed = self.elapsed_ms if elapsed is None else elapsed
        entry = {
            "schema_version": CONTRACT_VERSION,
            "run_id": self.run.run_id,
            "phase": "debate",
            "event": event,
            "created_at_utc": self._utc_at(elapsed),
            "elapsed_ms": elapsed,
            "deadline_phase": self.rules.phase_at(elapsed, self.challenge_deadline_ms),
            **detail,
        }
        entry["entry_sha256"] = content_sha256(entry)
        self.public_history_sha256 = hashlib.sha256(
            (self.public_history_sha256 + entry["entry_sha256"]).encode("utf-8")
        ).hexdigest()
        entry["public_history_sha256"] = self.public_history_sha256
        self.entries.append(entry)
        self.run.append_event(entry)
        return entry

    def verify_public_history(self):
        """Recompute every entry and chained history digest without trusting Core."""
        history = hashlib.sha256(b"").hexdigest()
        for entry in self.entries:
            payload = {
                key: value
                for key, value in entry.items()
                if key not in ("entry_sha256", "public_history_sha256")
            }
            digest = content_sha256(payload)
            if digest != entry.get("entry_sha256"):
                return False
            history = hashlib.sha256((history + digest).encode("utf-8")).hexdigest()
            if history != entry.get("public_history_sha256"):
                return False
        return history == self.public_history_sha256

    # -- stopping -----------------------------------------------------------

    def valid_votes(self):
        """Return the current valid vote per seat, keyed by seat id."""
        return {
            seat_id: seat.final
            for seat_id, seat in self.seats.items()
            if seat.state == "valid"
        }

    def tally(self):
        counts = {stance: 0 for stance in self.stances}
        for vote in self.valid_votes().values():
            counts[vote["stance"]] += 1
        return counts

    def _evaluate_stop(self):
        if self.stopped:
            return None
        elapsed = self.elapsed_ms
        if elapsed >= self.rules.force_stop_ms:
            return self._force_stop()
        # 直過與底下的票數階梯互斥，不必排順序：直過的前提是還沒有任何辯論訊
        # 息，因此也還沒有任何有效票，而階梯數的正是有效票。
        blind_pass_stance = self._blind_pass_stance(elapsed)
        if blind_pass_stance is not None:
            return self._blind_pass(blind_pass_stance, elapsed)
        threshold = self.rules.required_votes_at(elapsed)
        counts = self.tally()
        leader = max(counts, key=lambda stance: counts[stance])
        if counts[leader] >= threshold:
            return self._stop(
                "consensus_{}_votes".format(threshold), elapsed, leader, threshold
            )
        return None

    def _blind_pass_stance(self, elapsed):
        """Return the stance a blind 7/7 opening adopted, or ``None``.

        三個條件全部來自 ``config/debate_rules.json``，缺一不可：

        * **時點**：還在第一輪牆之內。牆一過，這一場已經是辯論場，開場票不再
          能繞過辯論直接定案。
        * **盲**：公開紀錄裡還沒有任何 challenge／response／final_vote。
        * **全席發布且達門檻**：七席的開場都已進公開紀錄，且同一立場的開場票
          數達到 ``vote_thresholds.unanimous_blind_pass``。
        """
        if elapsed > self.challenge_deadline_ms:
            return None
        if self.debate_rounds_started:
            return None
        if any(seat.initial is None for seat in self.seats.values()):
            return None
        counts = self.opening_tally()
        leader = max(counts, key=lambda stance: counts[stance])
        if counts[leader] < self.rules.unanimous_blind_pass_votes:
            return None
        return leader

    def _blind_pass(self, stance, elapsed):
        """Adopt every seat's own blind opening as its final vote, then stop.

        直過就是「開場即定案」。Core 沒有創作也沒有改寫任何內容：每一席的最終
        票就是它自己公開的那一則開場原文。因此票表、tally、報告與 verifier 全
        部沿用既有欄位，不存在第二套「直過專用」的計票路徑。
        """
        for seat in self.seats.values():
            seat.final = dict(seat.initial)
        return self._stop(
            UNANIMOUS_BLIND_PASS,
            elapsed,
            stance,
            self.rules.unanimous_blind_pass_votes,
        )

    def _force_stop(self):
        elapsed = self.elapsed_ms
        if elapsed < self.rules.force_stop_ms:
            return None
        required = self.rules.forced_stop_votes
        counts = self.tally()
        leader = max(counts, key=lambda stance: counts[stance])
        valid_count = len(self.valid_votes())
        adopted = (
            leader if valid_count >= required and counts[leader] >= required else None
        )
        if valid_count < required:
            reason = "forced_stop_insufficient_valid_votes"
        elif adopted:
            reason = "forced_stop_{}_votes".format(required)
        else:
            reason = "forced_stop_no_consensus"
        return self._stop(reason, elapsed, adopted, required)

    def _stop(self, reason, elapsed, adopted_stance, threshold):
        self.stopped = True
        self.stop_reason = reason
        self.stop_elapsed_ms = elapsed
        self._stop_threshold = threshold
        self._adopted_stance = adopted_stance
        self._append_entry(
            "debate_stopped",
            {
                "stop_reason": reason,
                "threshold_required": threshold,
                "adopted_stance": adopted_stance,
                "tally": self.tally(),
            },
            elapsed=elapsed,
        )
        return reason

    # -- persistence --------------------------------------------------------

    def vote_table(self):
        """Return the full seven-seat table, including missing/invalid seats."""
        table = []
        for seat_id in self.seat_ids:
            seat = self.seats[seat_id]
            initial = seat.initial or {}
            final = seat.final or {}
            changed = bool(initial and final and initial.get("stance") != final.get("stance"))
            table.append(
                {
                    "seat_id": seat_id,
                    "state": seat.state,
                    "attempt_ids": list(seat.attempt_ids),
                    "invalid_reason": seat.invalid_reason,
                    "initial_stance": initial.get("stance"),
                    "initial_public_reason": initial.get("public_reason"),
                    "initial_evidence_ids": list(initial.get("evidence_ids", [])),
                    "initial_neutral_context": _neutral_context(initial),
                    "final_stance": final.get("stance"),
                    "final_public_reason": final.get("public_reason"),
                    "final_evidence_ids": list(final.get("evidence_ids", [])),
                    "final_neutral_context": _neutral_context(final),
                    "stance_changed": changed,
                    "stance_change_reason": final.get("stance_change_reason"),
                    "vote_changes": list(seat.vote_changes),
                    "message_ids": [
                        entry["message_id"] for entry in seat.messages
                    ],
                    "content_sha256": [entry["content_sha256"] for entry in seat.messages],
                }
            )
        return table

    def summary(self):
        """Return votes.json content without writing it."""
        votes = self.valid_votes()
        participants = [seat for seat in self.seats.values() if seat.initial is not None]
        counts = self.tally()
        valid_count = len(votes)
        adopted = getattr(self, "_adopted_stance", None)
        threshold = getattr(self, "_stop_threshold", None)
        quorum = self.rules.forced_stop_votes
        red = self.stopped and valid_count < quorum
        if not self.stopped:
            adopted = None
            status = "in_progress"
        elif red:
            adopted = None
            status = "failed_insufficient_valid_votes"
        elif adopted is not None and counts.get(adopted, 0) >= (threshold or quorum):
            status = "consensus"
        else:
            adopted = None
            status = "no_consensus"
        return {
            "schema_version": CONTRACT_VERSION,
            "run_id": self.run.run_id,
            "phase": "vote",
            "question_type": self.question_type,
            "stances": list(self.stances),
            "evidence_snapshot_sha256": self.evidence_snapshot_sha256,
            "seat_count": len(self.seat_ids),
            "votes": self.vote_table(),
            "valid_vote_count": valid_count,
            "tally": counts,
            "consensus_status": status,
            "threshold_required": threshold,
            "adopted_stance": adopted,
            "market_conclusion_allowed": status == "consensus",
            "red_no_conclusion": red,
            "stop_reason": self.stop_reason,
            "stop_elapsed_ms": self.stop_elapsed_ms,
            "stopped_at_utc": self._utc_at(self.stop_elapsed_ms or self.elapsed_ms),
            "dissent": [
                {
                    "seat_id": seat_id,
                    "stance": vote["stance"],
                    "public_reason": vote["public_reason"],
                }
                for seat_id, vote in sorted(votes.items())
                if adopted is None or vote["stance"] != adopted
            ],
            "challenge_completed": bool(participants) and all(
                seat.challenges and seat.responses
                for seat in participants
            ),
        }

    def persist(self):
        """Write ``debate.jsonl`` and ``votes.json`` through write-once APIs."""
        if self.result is not None:
            return deepcopy(self.result)
        if not self.stopped:
            raise DebateLifecycleError("辯論尚未達停止條件，不得輸出正式票數。")
        if not self.verify_public_history():
            raise CoreOverrideError("公開辯論原文或 hash 已改變；不得輸出正式票數。")
        summary = self.summary()
        self.run.write_jsonl("debate.jsonl", self.entries, source="shared debate room")
        self.run.write_json("votes.json", summary, source="consensus tally")
        self.result = summary
        return summary

    def _utc_at(self, elapsed):
        return iso_utc(self.started_at_utc + timedelta(milliseconds=elapsed or 0))


def _neutral_context(record):
    if record.get("stance") not in NEUTRAL_STANCES:
        return None
    return {
        "conflicting_evidence_ids": list(record.get("conflicting_evidence_ids", [])),
        "uncertainty_reason": record.get("uncertainty_reason"),
        "change_trigger": record.get("change_trigger"),
    }
