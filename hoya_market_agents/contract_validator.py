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
CONTRACT_VERSION = "1.0.0"

DIRECTIONS = ("support", "oppose", "neutral")
STANCES = ("bullish", "bearish", "neutral")
SOURCE_TIERS = (1, 2, 3)
QUESTION_TYPES = ("single_asset", "comparison", "market", "event")

_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class ContractViolationError(ValueError):
    """Raised when a record does not satisfy its approved contract."""

    def __init__(self, kind, problems):
        self.kind = kind
        self.problems = list(problems)
        super().__init__("{} 不符合 contract：{}".format(kind, "；".join(self.problems)))


def validate_evidence_card(card):
    """Validate one evidence card and return it unchanged."""
    problems = _version_problems(card) + _audit_problems(card)
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


def validate_agent_position(record, known_evidence_ids=None):
    """Validate an agent position and its evidence references."""
    problems = _version_problems(record)
    problems += _position_problems(record, expected_phase="position")
    problems += _evidence_reference_problems(record, known_evidence_ids)
    return _result("agent position", record, problems)


def validate_debate_turn(turn, known_evidence_ids=None):
    """Validate one shared-transcript debate turn and return it unchanged."""
    problems = _version_problems(turn)
    problems += _position_problems(turn, expected_phase="debate")
    problems += _evidence_reference_problems(turn, known_evidence_ids)
    problems += _non_empty_string_problem(turn, "turn_id")
    if not isinstance(turn.get("responds_to"), list):
        problems.append("responds_to 必須為陣列")
    return _result("debate turn", turn, problems)


def validate_vote(record, known_evidence_ids=None):
    """Validate one seat vote and return it unchanged."""
    problems = _version_problems(record)
    problems += _position_problems(record, expected_phase="vote")
    problems += _evidence_reference_problems(record, known_evidence_ids)
    return _result("vote", record, problems)


def validate_question_package(package):
    """Validate the Core-created question passed to the seven seats."""
    problems = _version_problems(package)
    problems += _non_empty_string_problem(package, "run_id")
    problems += _non_empty_string_problem(package, "question")
    problems += _enum_problem(package, "question_type", QUESTION_TYPES)
    problems += _utc_timestamp_problem(package, "created_at_utc")
    problems += _elapsed_problem(package)
    if package.get("phase") != "question":
        problems.append("phase 必須為 question，實際為 {!r}".format(package.get("phase")))
    problems += _assets_problems(package)
    problems += _positive_integer_problem(package, "period_days")
    return _result("question package", package, problems)


def validate_run_manifest(manifest):
    """Validate one run manifest, including its artifact hash index."""
    problems = _version_problems(manifest)
    for field in ("run_id", "provider_mode", "question"):
        problems += _non_empty_string_problem(manifest, field)
    for field in ("started_at_utc", "completed_at_utc"):
        problems += _utc_timestamp_problem(manifest, field)
    problems += _elapsed_problem(manifest)
    problems += _assets_problems(manifest)
    problems += _positive_integer_problem(manifest, "period_days")

    seats = manifest.get("seats")
    if not isinstance(seats, list):
        problems.append("seats 必須為陣列")
    else:
        for index, seat in enumerate(seats):
            if not isinstance(seat, dict):
                problems.append("seats[{}] 必須為物件".format(index))
                continue
            seat_id = seat.get("seat_id")
            if seat_id not in SEAT_IDS:
                problems.append("seats[{}].seat_id {!r} 不是七席之一".format(index, seat_id))
            attempts = seat.get("attempt_ids")
            if not isinstance(attempts, list) or any(
                not isinstance(item, str) or not item.strip() for item in attempts
            ):
                problems.append("seats[{}].attempt_ids 必須為字串陣列".format(index))

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        problems.append("artifacts 必須為物件")
    else:
        for name, entry in artifacts.items():
            prefix = "artifacts[{!r}]".format(name)
            if not isinstance(name, str) or not name or not isinstance(entry, dict):
                problems.append("{} 必須以非空檔名對應物件".format(prefix))
                continue
            if entry.get("path") != name:
                problems.append("{}.path 必須等於 artifact 名稱".format(prefix))
            digest = entry.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                problems.append("{}.sha256 必須為 64 位小寫十六進位".format(prefix))
            if not isinstance(entry.get("source"), str) or not entry["source"].strip():
                problems.append("{}.source 必須為非空字串".format(prefix))
    return _result("run manifest", manifest, problems)


def validate_report(report):
    """Validate the shared report contract and all evidence references."""
    problems = _version_problems(report)
    for field in ("run_id", "question", "provider_mode"):
        problems += _non_empty_string_problem(report, field)
    for field in ("started_at_utc", "generated_at_utc"):
        problems += _utc_timestamp_problem(report, field)
    problems += _assets_problems(report)
    problems += _positive_integer_problem(report, "period_days")
    if not isinstance(report.get("confidence"), dict):
        problems.append("confidence 必須為物件")
    if not isinstance(report.get("conclusion"), dict):
        problems.append("conclusion 必須為物件")
    if not isinstance(report.get("tally"), dict):
        problems.append("tally 必須為物件")
    for field in ("scope_limits", "raw_records", "debate", "seats", "evidence"):
        if not isinstance(report.get(field), list):
            problems.append("{} 必須為陣列".format(field))

    evidence = report.get("evidence")
    known = set()
    if isinstance(evidence, list):
        for index, card in enumerate(evidence):
            evidence_id = card.get("evidence_id") if isinstance(card, dict) else None
            if not isinstance(evidence_id, str) or not evidence_id:
                problems.append("evidence[{}].evidence_id 必須為非空字串".format(index))
            elif evidence_id in known:
                problems.append("evidence_id {} 重複".format(evidence_id))
            else:
                known.add(evidence_id)

    for collection in ("seats", "debate"):
        records = report.get(collection)
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                problems.append("{}[{}] 必須為物件".format(collection, index))
                continue
            evidence_ids = record.get("evidence_ids")
            if not isinstance(evidence_ids, list):
                problems.append("{}[{}].evidence_ids 必須為陣列".format(collection, index))
                continue
            unknown = [item for item in evidence_ids if item not in known]
            if unknown:
                problems.append(
                    "{}[{}] 引用未知 evidence ID：{}".format(
                        collection, index, ", ".join(str(item) for item in unknown)
                    )
                )
    return _result("report", report, problems)


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


def _version_problems(record):
    return _enum_problem(record, "schema_version", (CONTRACT_VERSION,))


def _evidence_reference_problems(record, known_evidence_ids):
    if known_evidence_ids is None:
        return []
    evidence_ids = record.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        return []
    known = set(known_evidence_ids)
    unknown = [item for item in evidence_ids if item not in known]
    if not unknown:
        return []
    return ["引用未知 evidence ID：{}".format(", ".join(str(item) for item in unknown))]


def _assets_problems(record):
    assets = record.get("assets")
    if not isinstance(assets, list) or not assets:
        return ["assets 必須為至少一個資產的陣列"]
    invalid = [asset for asset in assets if asset not in SUPPORTED_ASSETS]
    if invalid:
        return ["assets 包含未支援資產：{}".format(", ".join(str(item) for item in invalid))]
    return []


def _positive_integer_problem(record, field):
    value = record.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return ["{} 必須為 >= 1 的整數，實際為 {!r}".format(field, value)]
    return []


def _elapsed_problem(record):
    elapsed = record.get("elapsed_ms")
    if "elapsed_ms" not in record:
        return ["缺少必要欄位 elapsed_ms"]
    if not isinstance(elapsed, int) or isinstance(elapsed, bool) or elapsed < 0:
        return ["elapsed_ms 必須為 >= 0 的整數，實際為 {!r}".format(elapsed)]
    return []


def _audit_problems(record):
    """Check the audit fields every formal record must carry."""
    problems = []
    for field in ("run_id", "seat_id", "attempt_id", "phase"):
        problems += _non_empty_string_problem(record, field)
    if "seat_id" in record and record.get("seat_id") not in SEAT_IDS:
        problems.append("seat_id {!r} 不是七席之一".format(record.get("seat_id")))
    problems += _utc_timestamp_problem(record, "created_at_utc")
    problems += _elapsed_problem(record)
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
