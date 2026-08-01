"""Explicit record contracts for evidence cards, debate turns and votes.

The Python standard library has no general JSON Schema validator, so the
approved contracts are enforced here as explicit field, type and enum checks.
Every violation in a record is reported at once, so a provider can repair its
output in a single pass instead of one field at a time.

The validator only checks objectively verifiable structure. It never inspects
or overrides a seat's market direction.
"""

import re

from .question import SUPPORTED_ASSETS
from .seats import SEAT_IDS

MAX_EVIDENCE_CARDS_PER_SEAT = 8

DIRECTIONS = ("support", "oppose", "neutral")
STANCES = ("bullish", "bearish", "neutral")
SOURCE_TIERS = (1, 2, 3)

_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class ContractViolationError(ValueError):
    """Raised when a record does not satisfy its approved contract."""

    def __init__(self, kind, problems):
        self.kind = kind
        self.problems = list(problems)
        super().__init__("{} 不符合 contract：{}".format(kind, "；".join(self.problems)))


def validate_evidence_card(card):
    """Validate one evidence card and return it unchanged."""
    problems = _audit_problems(card)
    problems += _enum_problem(card, "asset", SUPPORTED_ASSETS)
    problems += _enum_problem(card, "direction", DIRECTIONS)
    problems += _enum_problem(card, "source_tier", SOURCE_TIERS)
    for field in (
        "evidence_id",
        "category",
        "statement",
        "source_url",
        "excerpt",
        "credibility_note",
    ):
        problems += _non_empty_string_problem(card, field)
    for field in ("published_at_utc", "retrieved_at_utc"):
        problems += _utc_timestamp_problem(card, field)
    if "phase" in card and card["phase"] != "research":
        problems.append("phase 必須為 research，實際為 {!r}".format(card["phase"]))
    return _result("evidence card", card, problems)


def validate_seat_evidence(seat_id, cards):
    """Validate a seat's whole evidence submission and return it unchanged."""
    problems = []
    if len(cards) > MAX_EVIDENCE_CARDS_PER_SEAT:
        problems.append(
            "席位 {} 提交 {} 張證據卡，超過上限 {}".format(
                seat_id, len(cards), MAX_EVIDENCE_CARDS_PER_SEAT
            )
        )
    seen = set()
    for card in cards:
        validate_evidence_card(card)
        if card.get("seat_id") != seat_id:
            problems.append(
                "證據卡 {} 的 seat_id 為 {!r}，不屬於席位 {}".format(
                    card.get("evidence_id"), card.get("seat_id"), seat_id
                )
            )
        evidence_id = card.get("evidence_id")
        if evidence_id in seen:
            problems.append("evidence_id {} 在同一席位重複".format(evidence_id))
        seen.add(evidence_id)
    return _result("seat evidence", cards, problems)


def validate_debate_turn(turn):
    """Validate one shared-transcript debate turn and return it unchanged."""
    problems = _position_problems(turn, expected_phase="debate")
    problems += _non_empty_string_problem(turn, "turn_id")
    if not isinstance(turn.get("responds_to"), list):
        problems.append("responds_to 必須為陣列")
    return _result("debate turn", turn, problems)


def validate_vote(record):
    """Validate one seat vote and return it unchanged."""
    return _result("vote", record, _position_problems(record, expected_phase="vote"))


def _position_problems(record, expected_phase):
    problems = _audit_problems(record)
    problems += _enum_problem(record, "stance", STANCES)
    problems += _non_empty_string_problem(record, "public_reason")
    if record.get("phase") != expected_phase:
        problems.append(
            "phase 必須為 {}，實際為 {!r}".format(expected_phase, record.get("phase"))
        )
    round_number = record.get("round")
    if not isinstance(round_number, int) or isinstance(round_number, bool) or round_number < 1:
        problems.append("round 必須為 >= 1 的整數，實際為 {!r}".format(round_number))
    evidence_ids = record.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not evidence_ids:
        problems.append("evidence_ids 必須為至少一個 evidence ID 的陣列")
    elif any(not isinstance(item, str) or not item.strip() for item in evidence_ids):
        problems.append("evidence_ids 只能包含非空字串")
    change_reason = record.get("stance_change_reason", None)
    if change_reason is not None and not isinstance(change_reason, str):
        problems.append("stance_change_reason 必須為字串或 null")
    return problems


def _audit_problems(record):
    """Check the audit fields every formal record must carry."""
    problems = []
    for field in ("run_id", "seat_id", "attempt_id", "phase"):
        problems += _non_empty_string_problem(record, field)
    if "seat_id" in record and record.get("seat_id") not in SEAT_IDS:
        problems.append("seat_id {!r} 不是七席之一".format(record.get("seat_id")))
    problems += _utc_timestamp_problem(record, "created_at_utc")
    elapsed = record.get("elapsed_ms")
    if "elapsed_ms" not in record:
        problems.append("缺少必要欄位 elapsed_ms")
    elif not isinstance(elapsed, int) or isinstance(elapsed, bool) or elapsed < 0:
        problems.append("elapsed_ms 必須為 >= 0 的整數，實際為 {!r}".format(elapsed))
    return problems


def _non_empty_string_problem(record, field):
    if field not in record:
        return ["缺少必要欄位 {}".format(field)]
    value = record[field]
    if not isinstance(value, str) or not value.strip():
        return ["{} 必須為非空字串，實際為 {!r}".format(field, value)]
    return []


def _enum_problem(record, field, allowed):
    if field not in record:
        return ["缺少必要欄位 {}".format(field)]
    if record[field] not in allowed:
        return [
            "{} 必須為 {} 之一，實際為 {!r}".format(
                field, "/".join(str(item) for item in allowed), record[field]
            )
        ]
    return []


def _utc_timestamp_problem(record, field):
    if field not in record:
        return ["缺少必要欄位 {}".format(field)]
    value = record[field]
    if not isinstance(value, str) or not _UTC_TIMESTAMP.match(value):
        return ["{} 必須為 UTC ISO-8601 並以 Z 結尾，實際為 {!r}".format(field, value)]
    return []


def _result(kind, value, problems):
    if problems:
        raise ContractViolationError(kind, problems)
    return value
