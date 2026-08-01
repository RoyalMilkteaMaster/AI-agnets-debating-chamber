"""The single point where seat output enters the run.

The gateway owns provenance: it allocates attempt ids, stamps ``run_id``,
``seat_id``, ``attempt_id``, ``phase``, ``created_at_utc`` and ``elapsed_ms``
from the injected clock, and validates every record against its contract before
the controller is allowed to merge it. A provider supplies content only; it can
neither forge its own identity nor bypass validation.
"""

from dataclasses import dataclass, field

from .clock import iso_utc
from .contract_validator import (
    CONTRACT_VERSION,
    ContractViolationError,
    validate_debate_turn,
    validate_evidence_card,
    validate_vote,
)


@dataclass(frozen=True)
class SeatCall:
    """Everything a provider is given for one seat in one phase."""

    run_id: str
    seat_id: str
    attempt_id: str
    phase: str
    scope: object
    prompt: object
    now_utc: object
    evidence_snapshot: tuple = ()
    debate_snapshot: tuple = ()


@dataclass
class ProviderGateway:
    """Stamps, validates and records every provider attempt."""

    provider: object
    clock: object
    run_id: str
    start_monotonic_ms: int
    attempts: list = field(default_factory=list)

    @property
    def mode(self):
        return self.provider.mode

    def attempt_id(self, seat_id):
        """First attempt of a seat; retries and replacements are a later ticket."""
        return "{}-a1".format(seat_id)

    def collect_evidence(self, seat, scope, prompt):
        call = self._call(seat, scope, prompt, "research")
        cards = []
        for index, content in enumerate(self.provider.research(call), start=1):
            content = _provider_content(
                content,
                (
                    "asset",
                    "category",
                    "statement",
                    "direction",
                    "source_url",
                    "source_origin",
                    "source_tier",
                    "published_at_utc",
                    "excerpt",
                    "credibility_note",
                ),
                "research",
            )
            stamp = self._stamp(seat.seat_id, "research")
            card = {
                "evidence_id": "{}-{:02d}".format(seat.seat_id, index),
                **stamp,
                "asset": content["asset"],
                "category": content["category"],
                "statement": content["statement"],
                "direction": content["direction"],
                "source_url": content["source_url"],
                "source_origin": content["source_origin"],
                "source_tier": content["source_tier"],
                "published_at_utc": content["published_at_utc"],
                "retrieved_at_utc": stamp["created_at_utc"],
                "excerpt": content["excerpt"],
                "credibility_note": content["credibility_note"],
            }
            cards.append(validate_evidence_card(card))
        self._record_attempt(seat.seat_id, "research", len(cards))
        return cards

    def collect_debate_turn(self, seat, scope, prompt, evidence_snapshot, round_number):
        call = self._call(seat, scope, prompt, "debate", evidence_snapshot=evidence_snapshot)
        content = _provider_content(
            self.provider.debate(call),
            (
                "stance",
                "public_reason",
                "evidence_ids",
                "responds_to",
                "stance_change_reason",
            ),
            "debate",
        )
        turn = {
            "turn_id": "{}-r{}".format(seat.seat_id, round_number),
            **self._stamp(seat.seat_id, "debate"),
            "round": round_number,
            "stance": content["stance"],
            "public_reason": content["public_reason"],
            "evidence_ids": content["evidence_ids"],
            "responds_to": content["responds_to"],
            "stance_change_reason": content["stance_change_reason"],
        }
        self._record_attempt(seat.seat_id, "debate", 1)
        return validate_debate_turn(
            turn, {card["evidence_id"] for card in evidence_snapshot}
        )

    def collect_vote(self, seat, scope, prompt, evidence_snapshot, debate_snapshot, round_number):
        call = self._call(
            seat,
            scope,
            prompt,
            "vote",
            evidence_snapshot=evidence_snapshot,
            debate_snapshot=debate_snapshot,
        )
        content = _provider_content(
            self.provider.vote(call),
            ("stance", "public_reason", "evidence_ids", "stance_change_reason"),
            "vote",
        )
        record = {
            **self._stamp(seat.seat_id, "vote"),
            "round": round_number,
            "stance": content["stance"],
            "public_reason": content["public_reason"],
            "evidence_ids": content["evidence_ids"],
            "stance_change_reason": content["stance_change_reason"],
        }
        self._record_attempt(seat.seat_id, "vote", 1)
        return validate_vote(record, {card["evidence_id"] for card in evidence_snapshot})

    def _call(self, seat, scope, prompt, phase, evidence_snapshot=(), debate_snapshot=()):
        return SeatCall(
            run_id=self.run_id,
            seat_id=seat.seat_id,
            attempt_id=self.attempt_id(seat.seat_id),
            phase=phase,
            scope=scope,
            prompt=prompt,
            now_utc=self.clock.utc_now(),
            evidence_snapshot=tuple(evidence_snapshot),
            debate_snapshot=tuple(debate_snapshot),
        )

    def _stamp(self, seat_id, phase):
        elapsed_ms = self.clock.monotonic_ms() - self.start_monotonic_ms
        return {
            "schema_version": CONTRACT_VERSION,
            "run_id": self.run_id,
            "seat_id": seat_id,
            "attempt_id": self.attempt_id(seat_id),
            "phase": phase,
            "created_at_utc": iso_utc(self.clock.utc_now()),
            "elapsed_ms": elapsed_ms,
        }

    def _record_attempt(self, seat_id, phase, record_count):
        self.attempts.append(
            {
                "attempt_id": self.attempt_id(seat_id),
                "seat_id": seat_id,
                "phase": phase,
                "provider_mode": self.mode,
                "record_count": record_count,
            }
        )


def _provider_content(content, fields, phase):
    """Keep only provider-owned fields and let the contract report omissions."""
    if not isinstance(content, dict):
        raise ContractViolationError(
            "{} provider output".format(phase), ["payload 必須為 JSON 物件"]
        )
    return {field: content.get(field) for field in fields}
