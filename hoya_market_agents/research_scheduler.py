"""Research deadline state machine.

The class is deliberately not a general task scheduler. Provider adapters call
``submit_result`` or ``report_failure`` as their processes change, while
``tick`` applies the fixed competition milestones using an injected monotonic
clock. Tests advance a fake clock; production code may call ``tick`` from its
normal process loop.
"""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta

from .clock import iso_utc
from .contract_validator import CONTRACT_VERSION, validate_seat_evidence
from .recovery_state_machine import RecoveryStateMachine
from .run_store import validate_format_only_change
from .seats import SEAT_IDS

PRIMARY_ONLY_END_MS = 90_000
START_RETRY_MS = 30_000
CHECKPOINT_MS = 120_000
# 2026-08-13 起替補與 checkpoint 同刻：T+2:00 存檔後立即替補。原 T+2:35 只留給
# backup 195 秒（到 T+5:50 收件牆），實測（run 20260813T060845Z-3704-3ebeac 的
# official-events 席）不足以完成一次完整研究。
REPLACEMENT_MS = CHECKPOINT_MS
# 2026-08-11 使用者核准：一般題可搜尋到 T+5:20，之後不得再發起新搜尋，
# 必須用已取得資料交卷；T+5:50 停止收件，T+6:00 封存。
WRAP_UP_WINDOW_MS = 30_000
ACCEPT_RESULTS_UNTIL_MS = 350_000
SEAL_MS = 360_000

# 前四個里程碑四種題型共用；只有收件牆與封存隨題型移動。
# 替補不再是獨立里程碑：T+2:00 的 checkpoint 分支存檔後立即執行替補。
FIXED_MILESTONES_MS = (
    0,
    START_RETRY_MS,
    PRIMARY_ONLY_END_MS,
    CHECKPOINT_MS,
)


@dataclass(frozen=True)
class ResearchDeadlines:
    """The instants one run's research phase is measured against.

    這是全系統唯一的時刻權威：scheduler、provider timeout、辯論起點、
    verifier 與看板一律查 :func:`research_deadlines`，不得自己抄字面值。
    """

    accept_until_ms: int
    seal_ms: int

    @property
    def search_stop_ms(self):
        """Soft wall for starting searches; the remaining time is for delivery."""
        return self.accept_until_ms - WRAP_UP_WINDOW_MS

    @property
    def milestones_ms(self):
        return FIXED_MILESTONES_MS + (
            self.search_stop_ms,
            self.accept_until_ms,
            self.seal_ms,
        )


@dataclass(frozen=True)
class AdoptedResearchResult:
    """Immutable handoff from the one adopted edge to independent Opening.

    The records are the complete validated envelope payload already accepted by
    ``RunDirectory.record_attempt``.  ``adopted_evidence_sha256`` seals those
    exact JSONL bytes for the next phase; the Opening consumer recomputes it and
    fails closed if a caller mutates or truncates the handoff.
    """

    run_id: str
    seat_id: str
    attempt_id: str
    attempt_kind: str
    provider: str
    requested_model: str
    actual_provider: str
    actual_model: str
    adopted_elapsed_ms: int
    adopted_at_utc: str
    records: tuple
    adopted_evidence_sha256: str


DEFAULT_DEADLINES = ResearchDeadlines(
    accept_until_ms=ACCEPT_RESULTS_UNTIL_MS, seal_ms=SEAL_MS
)
# 兩幣比較題保留既有的額外 30 秒研究與收件時間；辯論 offset 仍錨定封存。
COMPARISON_DEADLINES = ResearchDeadlines(accept_until_ms=380_000, seal_ms=390_000)
COMPARISON_QUESTION_TYPES = frozenset({"two_asset_comparison", "comparison"})

MILESTONES_MS = DEFAULT_DEADLINES.milestones_ms

FAILURE_KINDS = ("startup_error", "provider_error", "process_error", "timeout")

# -- terminal outcomes and stable failure codes (Spec R-008) ------------------
#
# ``attempt_outcomes`` is the only writer of an attempt's終局。這裡的兩份詞彙表
# 就是它允許寫入的全部值：終局五種，failure code 是機器值而不是訊息文字，
# 舊 runner 的 failure kind 只能經 :func:`failure_code_for` 折進來。

ADOPTED = "adopted"
SUPERSEDED = "superseded"
FAILED = "failed"
CANCELLED = "cancelled"
LATE_DISCARDED = "late_discarded"

TERMINAL_OUTCOMES = (ADOPTED, SUPERSEDED, FAILED, CANCELLED, LATE_DISCARDED)

PROVIDER_CLI_MISSING = "provider_cli_missing"
PROVIDER_START_FAILED = "provider_start_failed"
PROVIDER_TIMEOUT = "provider_timeout"
PROVIDER_EMPTY_OUTPUT = "provider_empty_output"
PROVIDER_MALFORMED_OUTPUT = "provider_malformed_output"
PROVIDER_PROCESS_ERROR = "provider_process_error"
PROVIDER_OUTPUT_REJECTED = "provider_output_rejected"
RESEARCH_PROOF_MISSING = "research_proof_missing"
RESEARCH_RESULT_WINDOW_CLOSED = "research_result_window_closed"
PROCESS_TREE_TERMINATION_FAILED = "process_tree_termination_failed"
# 這一席已經有第一個有效結果了，同席其他還在跑的 attempt 就地收工。它與
# ``research_result_window_closed`` 必須分得開：後者是整場的收件牆到了（可能是
# 壞消息），前者是這一席自己已經答完（是好消息）。混成同一個 code，看板就再也
# 分不出「這席逾時」與「這席早就交卷了」。
RESEARCH_FIRST_VALID_ALREADY_ADOPTED = "research_first_valid_already_adopted"

FAILURE_CODES = (
    PROVIDER_CLI_MISSING,
    PROVIDER_START_FAILED,
    PROVIDER_TIMEOUT,
    PROVIDER_EMPTY_OUTPUT,
    PROVIDER_MALFORMED_OUTPUT,
    PROVIDER_PROCESS_ERROR,
    PROVIDER_OUTPUT_REJECTED,
    RESEARCH_PROOF_MISSING,
    RESEARCH_RESULT_WINDOW_CLOSED,
    RESEARCH_FIRST_VALID_ALREADY_ADOPTED,
    PROCESS_TREE_TERMINATION_FAILED,
)

# 舊 runner 詞彙 → 穩定 code。保留對照而不是改寫 adapter，是因為那些 failure
# kind 同時是 events 的事件名，既有稽核與測試都讀得到它們。
LEGACY_FAILURE_CODES = {
    "startup_error": PROVIDER_START_FAILED,
    "timeout": PROVIDER_TIMEOUT,
    "process_error": PROVIDER_PROCESS_ERROR,
    "provider_error": PROVIDER_OUTPUT_REJECTED,
}

RESEARCH_PHASE = "research"
OPENING_ACTUAL_LINEAGE_MISSING = "opening_actual_lineage_missing"


def failure_code_for(failure_kind):
    """Return the stable failure code for a runner failure kind or code."""
    if failure_kind in FAILURE_CODES:
        return failure_kind
    try:
        return LEGACY_FAILURE_CODES[failure_kind]
    except KeyError as exc:
        raise ValueError("未知 failure kind：{}".format(failure_kind)) from exc


def research_deadlines(question_type=None):
    """Return the accept-until and seal instants this question type runs on.

    未知或缺漏的題型退回最緊的預設時刻表：晚封存要有明確理由，
    而一份沒宣告題型的 run 不該因此多拿 30 秒。
    """
    if question_type in COMPARISON_QUESTION_TYPES:
        return COMPARISON_DEADLINES
    return DEFAULT_DEADLINES


class ResearchSchedulerError(RuntimeError):
    pass


class ResearchScheduler:
    """Coordinate seven logical seats until the immutable evidence snapshot."""

    def __init__(
        self,
        run,
        clock,
        gateway,
        process_runner,
        format_repairer,
        primary_models,
        replacement_models,
        seat_ids=SEAT_IDS,
        deadlines=None,
        seat_providers=None,
        backup_candidates=None,
        on_adopted=None,
    ):
        self.run = run
        self.clock = clock
        self.deadlines = deadlines or research_deadlines()
        self.gateway = gateway
        self.process_runner = process_runner
        self.format_repairer = format_repairer
        self.seat_ids = tuple(seat_ids)
        self.seat_providers = dict(seat_providers or {})
        self.recovery = RecoveryStateMachine(
            self.seat_ids,
            primary_models,
            replacement_models,
            seat_providers=self.seat_providers,
            backup_candidates=backup_candidates,
        )
        self.started_at_utc = None
        self.start_monotonic_ms = None
        self.events = []
        self.attempts = {}
        self.finished_attempt_ids = set()
        self.adopted_records = {}
        self.completed_milestones = set()
        self.accepting_results = False
        self.seal = None
        self._late_counts = {}
        #: attempt_id → 這個 attempt 的唯一 lineage／終局紀錄。除了本類別的
        #: ``_settle`` 與 ``record_lineage``，沒有第二個寫入者。
        self.attempt_outcomes = {}
        self.exhausted_seat_ids = set()
        self.on_adopted = on_adopted

    @property
    def source_policy(self):
        elapsed = self.elapsed_ms
        if elapsed >= self.deadlines.seal_ms:
            return "search_closed"
        if elapsed >= self.deadlines.search_stop_ms:
            return "wrap_up_only"
        if elapsed >= PRIMARY_ONLY_END_MS:
            return "trusted_secondary_allowed"
        return "primary_only"

    @property
    def elapsed_ms(self):
        if self.start_monotonic_ms is None:
            return 0
        return max(0, self.clock.monotonic_ms() - self.start_monotonic_ms)

    def start(self, started_at_utc=None, start_monotonic_ms=None):
        """Start research on this run's one absolute coordinate.

        The caller may hand in the coordinate it already gave the Early Opening
        driver. Reading the clock twice — once per consumer — makes every tick
        between the two reads permanent drift, so an Opening dispatched at the
        adopted edge would be measured against a different origin than the
        deadline that adopted it.
        """
        if self.started_at_utc is not None:
            raise ResearchSchedulerError("research scheduler 已啟動")
        self.started_at_utc = (
            self.clock.utc_now() if started_at_utc is None else started_at_utc
        )
        self.start_monotonic_ms = (
            self.clock.monotonic_ms()
            if start_monotonic_ms is None
            else start_monotonic_ms
        )
        self.accepting_results = True
        self._milestone(0)
        for attempt in self.recovery.start_all():
            self._launch(attempt, at_ms=0)
        return self.events

    def tick(self):
        self._require_started()
        return self._sync_deadlines()

    def _sync_deadlines(self):
        now = self.elapsed_ms
        if now >= self.deadlines.accept_until_ms:
            self.accepting_results = False
        for milestone in self.deadlines.milestones_ms:
            if milestone <= now and milestone not in self.completed_milestones:
                self._milestone(milestone)
        return self.events

    def submit_result(self, attempt_id, raw_output):
        """Validate and adopt a result, or retain it as diagnostic/late output.

        The finished/terminal guard runs first, before a single byte is
        validated: an attempt that has already timed out, been cancelled, failed
        or been superseded has spent its one terminal outcome, so whatever
        arrives afterwards is diagnostic — it can neither be adopted nor rewrite
        the outcome that is already recorded.
        """
        self._require_started()
        self._sync_deadlines()
        attempt = self._attempt(attempt_id)
        elapsed = self.elapsed_ms
        window_closed = (
            self.seal is not None
            or not self.accepting_results
            or elapsed >= self.deadlines.accept_until_ms
        )
        if self._is_finished(attempt_id):
            if window_closed:
                return self._record_late(attempt, raw_output, elapsed)
            return self._retain_diagnostic(attempt, raw_output, elapsed)
        if window_closed:
            return self._record_late(attempt, raw_output, elapsed)

        validated = self._validate_with_repair(attempt, raw_output, elapsed)
        if validated is None:
            self.finished_attempt_ids.add(attempt_id)
            self._settle(attempt, FAILED, failure_code=PROVIDER_MALFORMED_OUTPUT,
                         failure_message="unrepairable research output", elapsed=elapsed)
            self._recover(attempt_id, "malformed_output", elapsed)
            return "unrepairable"

        accepted_raw, records = validated
        state = self.recovery.seats[attempt.seat_id]
        adoptable = state.adopted_attempt_id is None
        adopted = self.run.record_attempt(
            attempt.seat_id,
            attempt.attempt_id,
            accepted_raw,
            {"schema_version": CONTRACT_VERSION, "records": records},
            adoptable=adoptable,
        )
        self.finished_attempt_ids.add(attempt_id)
        if adopted and state.mark_adopted(attempt_id):
            self.adopted_records[attempt.seat_id] = list(records)
            self._settle(attempt, ADOPTED, elapsed=elapsed)
            self._event("first_valid_result_adopted", elapsed, attempt)
            self._notify_adopted(attempt, records, elapsed)
            # 就在這裡收工，不是等收件牆。這中間的空窗如果留著，同席還在跑的
            # attempt 交出來的結果仍然可能被採用，first-valid-wins 就名存實亡；
            # 那個進程也會繼續燒訂閱到牆為止。
            self._seal_losing_attempts(state, attempt_id, elapsed)
            return "adopted"
        self._settle(attempt, SUPERSEDED, elapsed=elapsed)
        self._event("valid_result_retained_as_diagnostic", elapsed, attempt)
        return "diagnostic"

    def _notify_adopted(self, attempt, records, elapsed):
        """Notify the next phase once; observer failure cannot undo research."""
        if self.on_adopted is None:
            return None
        lineage = self._outcome_record(attempt)
        missing_lineage_fields = sorted(
            field
            for field in ("actual_provider", "actual_model")
            if not isinstance(lineage.get(field), str) or not lineage[field].strip()
        )
        if missing_lineage_fields:
            # The requested lane is not proof of what actually answered.  Keep
            # the adopted research result, but do not invent provenance for an
            # independent Opening invocation.
            self._event(
                "adopted_result_observer_failed",
                elapsed,
                attempt,
                observer_phase="opening",
                failure_code=OPENING_ACTUAL_LINEAGE_MISSING,
                missing_lineage_fields=missing_lineage_fields,
            )
            return None
        frozen_records = tuple(deepcopy(records))
        handoff = AdoptedResearchResult(
            run_id=self.run.run_id,
            seat_id=attempt.seat_id,
            attempt_id=attempt.attempt_id,
            attempt_kind=attempt.kind,
            provider=lineage["provider"],
            requested_model=lineage["requested_model"],
            actual_provider=lineage["actual_provider"],
            actual_model=lineage["actual_model"],
            adopted_elapsed_ms=elapsed,
            adopted_at_utc=self._utc_at(elapsed),
            records=frozen_records,
            adopted_evidence_sha256=_records_sha256(frozen_records),
        )
        try:
            return self.on_adopted(handoff)
        except Exception as exc:  # Opening is independent from research terminal state
            self._event(
                "adopted_result_observer_failed",
                elapsed,
                attempt,
                observer_error_type=type(exc).__name__,
            )
            return None

    def report_failure(self, attempt_id, failure_kind, message):
        """Record one attempt's failure, once, and start its recovery.

        ``failure_kind`` may be a runner's own vocabulary or an already-stable
        failure code; :func:`failure_code_for` is the only translation. A second
        report for the same attempt is diagnostic noise: it is audited and
        dropped, because the first one already spent the terminal outcome.
        """
        self._require_started()
        failure_code = failure_code_for(failure_kind)
        self._sync_deadlines()
        attempt = self._attempt(attempt_id)
        elapsed = self.elapsed_ms
        if self.seal is not None or not self.accepting_results:
            self._event(
                "failure_ignored_after_cutoff",
                elapsed,
                attempt,
                failure_kind=failure_kind,
                failure_code=failure_code,
                error=str(message),
            )
            return None
        if self._is_finished(attempt_id):
            self._event(
                "failure_ignored_after_terminal_outcome",
                elapsed,
                attempt,
                failure_kind=failure_kind,
                failure_code=failure_code,
                terminal_outcome=self._terminal_outcome(attempt_id),
                error=str(message),
            )
            return None
        self.finished_attempt_ids.add(attempt_id)
        self._settle(
            attempt,
            FAILED,
            failure_code=failure_code,
            failure_message=str(message),
            elapsed=elapsed,
        )
        self._event(
            failure_kind,
            elapsed,
            attempt,
            failure_code=failure_code,
            error=str(message),
        )
        return self._recover(attempt_id, failure_kind, elapsed)

    def record_lineage(self, attempt_id, provider=None, actual_model=None):
        """Record which provider and model actually answered; never a verdict.

        Provenance stays true even for an attempt that was cancelled or whose
        answer arrived too late, so this is deliberately the one lineage write
        that does not touch ``terminal_outcome`` or ``failure_code``.
        """
        self._require_started()
        attempt = self._attempt(attempt_id)
        record = self._outcome_record(attempt)
        if provider is not None:
            record["actual_provider"] = provider
        if actual_model is not None:
            record["actual_model"] = actual_model
        self._event(
            "attempt_lineage_recorded",
            self.elapsed_ms,
            attempt,
            actual_provider=record["actual_provider"],
            actual_model=record["actual_model"],
        )
        return record

    def attempt_summary(self):
        """One additive row per seat: its attempts, their lineage and terminals.

        The same projection the launch summary artifact and the Live seat cards
        read, so there is no second, disagreeing account of what a seat did.
        """
        return [
            {
                "seat_id": seat_id,
                "provider": self.seat_providers.get(seat_id),
                "adopted": state.adopted_attempt_id is not None,
                "adopted_attempt_id": state.adopted_attempt_id,
                "exhausted": (
                    state.adopted_attempt_id is None
                    and seat_id in self.exhausted_seat_ids
                ),
                "attempt_ids": [item.attempt_id for item in state.attempts],
                "attempts": [
                    dict(self._outcome_record(item)) for item in state.attempts
                ],
            }
            for seat_id, state in (
                (seat_id, self.recovery.seats[seat_id]) for seat_id in self.seat_ids
            )
        ]

    # -- the one terminal-outcome writer --------------------------------------

    def _outcome_record(self, attempt):
        """Return this attempt's lineage record, creating it on first sight."""
        record = self.attempt_outcomes.get(attempt.attempt_id)
        if record is not None:
            return record
        record = {
            "run_id": self.run.run_id,
            "seat_id": attempt.seat_id,
            "attempt_id": attempt.attempt_id,
            "phase": RESEARCH_PHASE,
            "attempt_kind": attempt.kind,
            "provider": self._provider_of(attempt),
            "requested_model": attempt.model,
            "actual_provider": None,
            "actual_model": None,
            "requested_at_utc": None,
            "requested_elapsed_ms": None,
            "started": False,
            "started_at_utc": None,
            "terminal_outcome": None,
            "failure_code": None,
            "failure_message": None,
            "adopted": False,
        }
        self.attempt_outcomes[attempt.attempt_id] = record
        return record

    def _settle(self, attempt, terminal_outcome, failure_code=None,
                failure_message=None, elapsed=None):
        """Write this attempt's one terminal outcome, or keep the existing one."""
        if terminal_outcome not in TERMINAL_OUTCOMES:
            raise ValueError("未知 terminal outcome：{}".format(terminal_outcome))
        record = self._outcome_record(attempt)
        if record["terminal_outcome"] is not None:
            return record
        elapsed = self.elapsed_ms if elapsed is None else elapsed
        record["terminal_outcome"] = terminal_outcome
        record["failure_code"] = failure_code
        record["failure_message"] = failure_message
        record["adopted"] = terminal_outcome == ADOPTED
        record["settled_at_utc"] = self._utc_at(elapsed)
        self._event(
            "attempt_outcome_recorded",
            elapsed,
            attempt,
            attempt_kind=record["attempt_kind"],
            provider=record["provider"],
            requested_model=record["requested_model"],
            actual_provider=record["actual_provider"],
            actual_model=record["actual_model"],
            terminal_outcome=terminal_outcome,
            failure_code=failure_code,
            failure_message=failure_message,
            adopted=record["adopted"],
        )
        return record

    def _seal_losing_attempts(self, state, winner_attempt_id, elapsed):
        """Close and reclaim this seat's other live attempts, now that one won.

        *Every* other non-terminal attempt is sealed, pending ones included. A
        ``start`` that reported ``False`` means the dispatch was requested and
        has not begun — not that it never will: the provider can still hand a
        process over afterwards. Skipping those was the hole this closes, and
        stopping them is not an invented cancellation, because
        ``ProcessRegistry.terminate`` poisons the attempt key. Cancel-then-
        register is the exact order that path already exists to handle, so a
        generation that appears late is reclaimed the moment it registers
        instead of running unattended until the receiving wall.

        The outcome is ``cancelled`` rather than ``superseded``: this attempt is
        being stopped before it delivered anything, which is exactly what
        ``cancelled`` means everywhere else here. ``superseded`` is reserved for
        an attempt that did produce a valid result and lost the race for
        adoption — a distinction a reader of one seat's history needs, because
        only one of the two says the provider actually answered.

        Reclaim goes through the injected ``process_runner`` — the same
        cancel/terminate path the cutoff sweep uses — so process-group ownership
        stays where it already lives.
        """
        for attempt in state.attempts:
            attempt_id = attempt.attempt_id
            if attempt_id == winner_attempt_id:
                continue
            if self._is_finished(attempt_id):
                continue
            self.finished_attempt_ids.add(attempt_id)
            self._settle(
                attempt,
                CANCELLED,
                failure_code=RESEARCH_FIRST_VALID_ALREADY_ADOPTED,
                failure_message="本席已採用 {}".format(winner_attempt_id),
                elapsed=elapsed,
            )
            self._stop_attempt(attempt, elapsed)

    def _stop_attempt(self, attempt, elapsed):
        """Cancel and terminate one attempt's process; every outcome is audited."""
        attempt_id = attempt.attempt_id
        try:
            self.process_runner.cancel(attempt_id)
            self._event("process_cancelled", elapsed, attempt)
        except Exception as exc:  # cancellation failure must remain visible
            self._event("process_cancel_error", elapsed, attempt, error=str(exc))
        try:
            self.process_runner.terminate(attempt_id)
            self._event("process_terminated", elapsed, attempt)
        except Exception as exc:
            self._event("process_terminate_error", elapsed, attempt, error=str(exc))

    def _is_finished(self, attempt_id):
        return (
            self._terminal_outcome(attempt_id) is not None
            or attempt_id in self.finished_attempt_ids
        )

    def _terminal_outcome(self, attempt_id):
        record = self.attempt_outcomes.get(attempt_id)
        return record["terminal_outcome"] if record else None

    def _provider_of(self, attempt):
        return attempt.provider or self.seat_providers.get(attempt.seat_id)

    def _retain_diagnostic(self, attempt, raw_output, elapsed):
        """Keep a valid-looking answer that arrived after its attempt was over."""
        try:
            self.run.record_attempt(
                attempt.seat_id,
                attempt.attempt_id,
                raw_output,
                {
                    "schema_version": CONTRACT_VERSION,
                    "validated": False,
                    "reason": "attempt_already_terminal",
                    "terminal_outcome": self._terminal_outcome(attempt.attempt_id),
                    "records": [],
                },
                adoptable=False,
            )
        except Exception as exc:  # artifact boundary; the outcome is unaffected
            self._event(
                "diagnostic_result_not_recorded", elapsed, attempt, error=str(exc)
            )
        self._event(
            "late_result_retained_as_diagnostic",
            elapsed,
            attempt,
            terminal_outcome=self._terminal_outcome(attempt.attempt_id),
        )
        return "diagnostic"

    def _milestone(self, elapsed):
        self.completed_milestones.add(elapsed)
        self._event("milestone", elapsed, detail="T+{}ms".format(elapsed))
        if elapsed == START_RETRY_MS:
            self._retry_unstarted(elapsed)
        elif elapsed == PRIMARY_ONLY_END_MS:
            self._event("trusted_secondary_sources_enabled", elapsed)
        elif elapsed == CHECKPOINT_MS:
            # 順序是契約：先存 checkpoint，替補才拿得到它接續研究。
            self._checkpoint_all(elapsed)
            self._replace_missing(elapsed)
        elif elapsed == self.deadlines.search_stop_ms:
            self._event("research_wrap_up_started", elapsed)
        elif elapsed == self.deadlines.accept_until_ms:
            self.accepting_results = False
            self._event("research_result_window_closed", elapsed)
            self._cancel_running(elapsed)
        elif elapsed == self.deadlines.seal_ms:
            self.accepting_results = False
            self._event("search_hard_stopped", elapsed)
            records = [
                card
                for seat_id in self.seat_ids
                for card in self.adopted_records.get(seat_id, ())
            ]
            self.seal = self.run.seal_evidence_snapshot(
                records, self._utc_at(elapsed), elapsed
            )
            self._event(
                "evidence_snapshot_sealed",
                elapsed,
                detail=self.seal["sha256"],
                record_count=len(records),
            )

    def _retry_unstarted(self, elapsed):
        if not self.accepting_results:
            return
        for state in self.recovery.seats.values():
            if state.adopted_attempt_id or state.started_attempt_ids:
                continue
            previous = state.attempts[-1]
            next_attempt = state.recover(
                previous.attempt_id, "not_started_at_{}ms".format(START_RETRY_MS)
            )
            if next_attempt:
                self._launch(next_attempt, elapsed)

    def _checkpoint_all(self, elapsed):
        for state in self.recovery.seats.values():
            checkpoint = None
            source_attempt_id = None
            for attempt in reversed(state.attempts):
                if attempt.attempt_id not in state.started_attempt_ids:
                    continue
                source_attempt_id = attempt.attempt_id
                try:
                    checkpoint = self.process_runner.checkpoint(attempt.attempt_id)
                except Exception as exc:  # external process boundary
                    self._event(
                        "checkpoint_error", elapsed, attempt, error=str(exc)
                    )
                break
            state.save_checkpoint(checkpoint)
            payload = {
                "run_id": self.run.run_id,
                "seat_id": state.seat_id,
                "attempt_id": source_attempt_id,
                "phase": "research",
                "created_at_utc": self._utc_at(elapsed),
                "elapsed_ms": elapsed,
                "checkpoint": checkpoint,
            }
            self.run.write_json(
                "agents/{}/checkpoint-{}.json".format(state.seat_id, CHECKPOINT_MS),
                payload,
                source="T+2:00 public research checkpoint",
            )
            self._event(
                "checkpoint_saved",
                elapsed,
                self.attempts.get(source_attempt_id),
                seat_id=state.seat_id,
                checkpoint_available=checkpoint is not None,
            )

    def _replace_missing(self, elapsed):
        if not self.accepting_results:
            return
        for state in self.recovery.seats.values():
            if state.adopted_attempt_id:
                continue
            previous = state.attempts[-1]
            next_attempt = state.recover(
                previous.attempt_id, "no_valid_result_at_{}ms".format(REPLACEMENT_MS)
            )
            if next_attempt:
                self._launch(next_attempt, elapsed)
            else:
                self.exhausted_seat_ids.add(state.seat_id)
                self._event(
                    "recovery_exhausted", elapsed, previous,
                    error="same-model retry and any configured replacement already used",
                )

    def _launch(self, attempt, at_ms):
        self.attempts[attempt.attempt_id] = attempt
        record = self._outcome_record(attempt)
        record["requested_at_utc"] = self._utc_at(at_ms)
        record["requested_elapsed_ms"] = at_ms
        self._event(
            "attempt_launch_requested",
            at_ms,
            attempt,
            model=attempt.model,
            provider=record["provider"],
            requested_model=record["requested_model"],
            attempt_kind=attempt.kind,
            parent_attempt_id=attempt.parent_attempt_id,
            original_attempt_id=attempt.original_attempt_id,
            reason=attempt.reason,
            checkpoint=attempt.checkpoint,
        )
        try:
            started = self.process_runner.start(attempt, attempt.checkpoint)
        except Exception as exc:  # external process boundary
            self.finished_attempt_ids.add(attempt.attempt_id)
            self._settle(
                attempt,
                FAILED,
                failure_code=PROVIDER_START_FAILED,
                failure_message=str(exc),
                elapsed=at_ms,
            )
            self._event("startup_error", at_ms, attempt,
                        failure_code=PROVIDER_START_FAILED, error=str(exc))
            self._recover(attempt.attempt_id, "startup_error", at_ms)
            return
        if started is False:
            self._event("attempt_start_pending", at_ms, attempt)
            return
        self.recovery.seats[attempt.seat_id].mark_started(attempt.attempt_id)
        record["started"] = True
        record["started_at_utc"] = self._utc_at(at_ms)
        self._event(
            "attempt_started",
            at_ms,
            attempt,
            model=attempt.model,
            provider=record["provider"],
            requested_model=record["requested_model"],
            attempt_kind=attempt.kind,
        )

    def _recover(self, attempt_id, reason, elapsed):
        if not self.accepting_results or self.seal is not None:
            self._event(
                "recovery_ignored_after_cutoff",
                elapsed,
                self._attempt(attempt_id),
                error=reason,
            )
            return None
        next_attempt = self.recovery.recover(attempt_id, reason)
        if next_attempt is None:
            failed = self._attempt(attempt_id)
            self.exhausted_seat_ids.add(failed.seat_id)
            self._event(
                "recovery_exhausted",
                elapsed,
                failed,
                error=reason,
            )
            return None
        self._launch(next_attempt, elapsed)
        return next_attempt

    def _validate_with_repair(self, attempt, raw_output, elapsed):
        raw = raw_output
        try:
            return raw, self._validate_submission(attempt, raw)
        except Exception as exc:  # contract boundary; exact error is audited
            error = str(exc)
            self._event("malformed_output", elapsed, attempt, error=error)

        try:
            corrected = self.process_runner.correct(attempt, raw, error)
        except Exception as exc:  # external process boundary
            corrected = None
            self._event(
                "original_format_correction_error",
                elapsed,
                attempt,
                error=str(exc),
            )
        else:
            self._event(
                "original_format_correction_requested",
                elapsed,
                attempt,
                error=error,
            )
        if corrected is not None:
            try:
                records = self._validate_submission(attempt, corrected)
                self._event("original_format_correction_valid", elapsed, attempt)
                return corrected, records
            except Exception as exc:
                raw, error = corrected, str(exc)
                self._event(
                    "original_format_correction_invalid",
                    elapsed,
                    attempt,
                    error=error,
                )

        try:
            repaired = self.format_repairer.repair(attempt, raw, error)
        except Exception as exc:  # non-voting repair boundary
            repaired = None
            self._event("format_repair_error", elapsed, attempt, error=str(exc))
        if repaired is None:
            self._event("format_repair_unavailable", elapsed, attempt, error=error)
            return None
        if not validate_format_only_change(raw, repaired):
            self._event(
                "format_repair_rejected_semantic_change", elapsed, attempt
            )
            return None
        try:
            records = self._validate_submission(attempt, repaired)
        except Exception as exc:
            self._event("format_repair_invalid", elapsed, attempt, error=str(exc))
            return None

        self.run.write_json(
            "diagnostics/format-repairs/{}.json".format(attempt.attempt_id),
            {
                "run_id": self.run.run_id,
                "seat_id": attempt.seat_id,
                "attempt_id": attempt.attempt_id,
                "phase": "format_repair",
                "created_at_utc": self._utc_at(elapsed),
                "elapsed_ms": elapsed,
                "operator": getattr(self.format_repairer, "name", "format-repair"),
                "error": error,
                "before_sha256": _sha256(raw),
                "after_sha256": _sha256(repaired),
                "before": raw,
                "after": repaired,
            },
            source="non-voting format repair audit",
        )
        self._event("format_repair_valid", elapsed, attempt)
        return repaired, records

    def _validate_submission(self, attempt, raw_output):
        records = self.gateway.validate(attempt, raw_output)
        validate_seat_evidence(attempt.seat_id, records)
        return records

    def _cancel_running(self, elapsed):
        for attempt_id, attempt in self.attempts.items():
            if attempt_id in self.finished_attempt_ids:
                continue
            self._stop_attempt(attempt, elapsed)
            self.finished_attempt_ids.add(attempt_id)
            self._settle(
                attempt,
                CANCELLED,
                failure_code=RESEARCH_RESULT_WINDOW_CLOSED,
                failure_message="收件窗於 T+{}ms 關閉".format(elapsed),
                elapsed=elapsed,
            )

    def _record_late(self, attempt, raw_output, elapsed):
        count = self._late_counts.get(attempt.attempt_id, 0) + 1
        self._late_counts[attempt.attempt_id] = count
        self.run.write_json(
            "late/{}-{}.json".format(attempt.attempt_id, count),
            {
                "run_id": self.run.run_id,
                "seat_id": attempt.seat_id,
                "attempt_id": attempt.attempt_id,
                "phase": "research",
                "created_at_utc": self._utc_at(elapsed),
                "elapsed_ms": elapsed,
                "reason": RESEARCH_RESULT_WINDOW_CLOSED,
                "raw_output": raw_output,
            },
            source="late research diagnostic",
        )
        # 已有終局的 attempt 不改寫：晚到的內容只是診斷，取代不了 cancelled
        # 或 failed 這種更早、更具體的事實。
        self._settle(
            attempt,
            LATE_DISCARDED,
            failure_code=RESEARCH_RESULT_WINDOW_CLOSED,
            failure_message="研究結果收件窗已關閉",
            elapsed=elapsed,
        )
        self._event(
            "late_result_discarded",
            elapsed,
            attempt,
            terminal_outcome=self._terminal_outcome(attempt.attempt_id),
        )
        return "late"

    def _event(self, event, elapsed, attempt=None, seat_id=None, **details):
        record = {
            "schema_version": CONTRACT_VERSION,
            "run_id": self.run.run_id,
            "seat_id": attempt.seat_id if attempt else seat_id,
            "attempt_id": attempt.attempt_id if attempt else None,
            "phase": "research",
            "created_at_utc": self._utc_at(elapsed),
            "elapsed_ms": elapsed,
            "event": event,
            **details,
        }
        self.events.append(record)
        self.run.append_event(record)
        return record

    def _utc_at(self, elapsed):
        return iso_utc(self.started_at_utc + timedelta(milliseconds=elapsed))

    def _attempt(self, attempt_id):
        try:
            return self.attempts[attempt_id]
        except KeyError as exc:
            raise ResearchSchedulerError("未知 attempt_id：{}".format(attempt_id)) from exc

    def _require_started(self):
        if self.started_at_utc is None:
            raise ResearchSchedulerError("research scheduler 尚未啟動")


def _sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _records_sha256(records):
    text = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
