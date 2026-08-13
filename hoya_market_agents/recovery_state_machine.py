"""Per-seat attempt lineage for research retries and replacements.

A seat is a fixed identity; an attempt is one dispatch made on its behalf. Since
Ticket 03 a seat configured with a :class:`ProviderCandidate` recovers by
handing its work to **another provider once** — the same-provider, same-model
retry buys nothing when the provider itself is what failed. The older
model-only policy (one same-model retry, then an optional cross-model
replacement) is still what a state built without a candidate does, so a caller
that has no provider column keeps the behaviour it was written against.

Either way the seat's own ``seat_id``, ``focus`` and roster provider never move:
a backup is attempt lineage, not a different seat.
"""

from dataclasses import dataclass, field

BACKUP_KIND = "backup"


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
    #: 這次 attempt 實際要派給哪個 provider 家族；舊呼叫端沒有 provider 欄時為 ``None``。
    provider: str | None = None


@dataclass(frozen=True)
class ProviderCandidate:
    """One approved fallback: a provider family and that family's fixed model."""

    provider: str
    model: str


@dataclass
class SeatRecoveryState:
    seat_id: str
    primary_model: str
    replacement_model: str | None
    attempts: list = field(default_factory=list)
    started_attempt_ids: set = field(default_factory=set)
    adopted_attempt_id: str | None = None
    checkpoint: object = None
    #: 這一席 roster 上的 primary provider（Ticket 03 起由呼叫端提供）。
    provider: str | None = None
    #: 唯一核准的 backup 候選；設定後就是這一席的整個 recovery 政策。
    backup: ProviderCandidate | None = None

    @property
    def same_model_retry_used(self):
        return any(item.kind == "same_model_retry" for item in self.attempts)

    @property
    def cross_model_replacement_used(self):
        return any(item.kind == "cross_model_replacement" for item in self.attempts)

    @property
    def backup_used(self):
        return any(item.kind == BACKUP_KIND for item in self.attempts)

    def primary(self):
        if self.attempts:
            raise ValueError("primary attempt 已建立")
        return self._new(self.primary_model, "primary", None, None, self.provider)

    def recover(self, failed_attempt_id, reason):
        if self.adopted_attempt_id:
            return None
        if self.backup is not None:
            return self._backup(failed_attempt_id, reason)
        if not self.same_model_retry_used:
            return self._new(
                self.primary_model,
                "same_model_retry",
                failed_attempt_id,
                reason,
                self.provider,
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
                self.provider,
            )
        return None

    def _backup(self, failed_attempt_id, reason):
        """The one other-provider attempt this seat is allowed, and only one."""
        if self.backup_used:
            return None
        if self.backup.provider == self.provider:
            raise ValueError("backup 必須使用與 primary 不同的 Provider")
        return self._new(
            self.backup.model,
            BACKUP_KIND,
            failed_attempt_id,
            reason,
            self.backup.provider,
        )

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

    def _new(self, model, kind, parent_attempt_id, reason, provider=None):
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
            provider=provider,
        )
        self.attempts.append(attempt)
        return attempt

    def _find(self, attempt_id):
        for attempt in self.attempts:
            if attempt.attempt_id == attempt_id:
                return attempt
        raise KeyError("未知 attempt_id：{}".format(attempt_id))


class RecoveryStateMachine:
    """One primary per seat, then whichever recovery policy the seats were given.

    Supplying ``backup_candidates`` selects Ticket 03's policy: one attempt on
    another provider, and no second one. Leaving it out keeps the older
    model-only policy, so a caller with no provider column is unaffected.
    """

    def __init__(
        self,
        seat_ids,
        primary_models,
        replacement_models,
        seat_providers=None,
        backup_candidates=None,
    ):
        seat_providers = seat_providers or {}
        backup_candidates = backup_candidates or {}
        self.seats = {}
        for seat_id in seat_ids:
            self.seats[seat_id] = SeatRecoveryState(
                seat_id=seat_id,
                primary_model=primary_models[seat_id],
                replacement_model=replacement_models[seat_id],
                provider=seat_providers.get(seat_id),
                backup=backup_candidates.get(seat_id),
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
