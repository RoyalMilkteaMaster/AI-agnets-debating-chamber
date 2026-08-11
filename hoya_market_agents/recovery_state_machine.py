"""Per-seat attempt lineage for research retries and replacements."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ResearchAttempt:
    attempt_id: str
    seat_id: str
    model: str
    kind: str
    original_attempt_id: str
    parent_attempt_id: str | None = None
    reason: str | None = None
    checkpoint: object = None


@dataclass
class SeatRecoveryState:
    seat_id: str
    primary_model: str
    replacement_model: str | None
    attempts: list = field(default_factory=list)
    started_attempt_ids: set = field(default_factory=set)
    adopted_attempt_id: str | None = None
    checkpoint: object = None

    @property
    def same_model_retry_used(self):
        return any(item.kind == "same_model_retry" for item in self.attempts)

    @property
    def cross_model_replacement_used(self):
        return any(item.kind == "cross_model_replacement" for item in self.attempts)

    def primary(self):
        if self.attempts:
            raise ValueError("primary attempt 已建立")
        return self._new(self.primary_model, "primary", None, None)

    def recover(self, failed_attempt_id, reason):
        if self.adopted_attempt_id:
            return None
        if not self.same_model_retry_used:
            return self._new(
                self.primary_model, "same_model_retry", failed_attempt_id, reason
            )
        if not self.cross_model_replacement_used:
            if not self.replacement_model:
                return None
            if self.replacement_model == self.primary_model:
                raise ValueError("cross-model replacement 必須使用不同模型")
            return self._new(
                self.replacement_model,
                "cross_model_replacement",
                failed_attempt_id,
                reason,
            )
        return None

    def mark_started(self, attempt_id):
        self._find(attempt_id)
        self.started_attempt_ids.add(attempt_id)

    def mark_adopted(self, attempt_id):
        self._find(attempt_id)
        if self.adopted_attempt_id is None:
            self.adopted_attempt_id = attempt_id
            return True
        return False

    def save_checkpoint(self, checkpoint):
        self.checkpoint = checkpoint

    def _new(self, model, kind, parent_attempt_id, reason):
        attempt_id = "{}-a{}".format(self.seat_id, len(self.attempts) + 1)
        original_attempt_id = self.attempts[0].attempt_id if self.attempts else attempt_id
        attempt = ResearchAttempt(
            attempt_id=attempt_id,
            seat_id=self.seat_id,
            model=model,
            kind=kind,
            original_attempt_id=original_attempt_id,
            parent_attempt_id=parent_attempt_id,
            reason=reason,
            checkpoint=self.checkpoint,
        )
        self.attempts.append(attempt)
        return attempt

    def _find(self, attempt_id):
        for attempt in self.attempts:
            if attempt.attempt_id == attempt_id:
                return attempt
        raise KeyError("未知 attempt_id：{}".format(attempt_id))


class RecoveryStateMachine:
    """Creates a primary, one same-model retry and an optional replacement."""

    def __init__(self, seat_ids, primary_models, replacement_models):
        self.seats = {}
        for seat_id in seat_ids:
            self.seats[seat_id] = SeatRecoveryState(
                seat_id=seat_id,
                primary_model=primary_models[seat_id],
                replacement_model=replacement_models[seat_id],
            )

    def start_all(self):
        return [state.primary() for state in self.seats.values()]

    def recover(self, attempt_id, reason):
        state = self.for_attempt(attempt_id)
        return state.recover(attempt_id, reason)

    def for_attempt(self, attempt_id):
        for state in self.seats.values():
            if any(item.attempt_id == attempt_id for item in state.attempts):
                return state
        raise KeyError("未知 attempt_id：{}".format(attempt_id))
