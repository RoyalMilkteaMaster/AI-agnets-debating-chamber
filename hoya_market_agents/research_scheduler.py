"""Research deadline state machine.

The class is deliberately not a general task scheduler. Provider adapters call
``submit_result`` or ``report_failure`` as their processes change, while
``tick`` applies the fixed competition milestones using an injected monotonic
clock. Tests advance a fake clock; production code may call ``tick`` from its
normal process loop.
"""

import hashlib
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
REPLACEMENT_MS = 155_000
# 2026-08-11 使用者核准：一般題可搜尋到 T+5:20，之後不得再發起新搜尋，
# 必須用已取得資料交卷；T+5:50 停止收件，T+6:00 封存。
WRAP_UP_WINDOW_MS = 30_000
ACCEPT_RESULTS_UNTIL_MS = 350_000
SEAL_MS = 360_000

# 前五個里程碑四種題型共用；只有收件牆與封存隨題型移動。
FIXED_MILESTONES_MS = (
    0,
    START_RETRY_MS,
    PRIMARY_ONLY_END_MS,
    CHECKPOINT_MS,
    REPLACEMENT_MS,
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


DEFAULT_DEADLINES = ResearchDeadlines(
    accept_until_ms=ACCEPT_RESULTS_UNTIL_MS, seal_ms=SEAL_MS
)
# 兩幣比較題保留既有的額外 30 秒研究與收件時間；辯論 offset 仍錨定封存。
COMPARISON_DEADLINES = ResearchDeadlines(accept_until_ms=380_000, seal_ms=390_000)
COMPARISON_QUESTION_TYPES = frozenset({"two_asset_comparison", "comparison"})

MILESTONES_MS = DEFAULT_DEADLINES.milestones_ms

FAILURE_KINDS = ("startup_error", "provider_error", "process_error", "timeout")


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
    ):
        self.run = run
        self.clock = clock
        self.deadlines = deadlines or research_deadlines()
        self.gateway = gateway
        self.process_runner = process_runner
        self.format_repairer = format_repairer
        self.seat_ids = tuple(seat_ids)
        self.recovery = RecoveryStateMachine(
            self.seat_ids, primary_models, replacement_models
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

    def start(self):
        if self.started_at_utc is not None:
            raise ResearchSchedulerError("research scheduler 已啟動")
        self.started_at_utc = self.clock.utc_now()
        self.start_monotonic_ms = self.clock.monotonic_ms()
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
        """Validate and adopt a result, or retain it as diagnostic/late output."""
        self._require_started()
        self._sync_deadlines()
        attempt = self._attempt(attempt_id)
        elapsed = self.elapsed_ms
        if (
            self.seal is not None
            or not self.accepting_results
            or elapsed >= self.deadlines.accept_until_ms
        ):
            return self._record_late(attempt, raw_output, elapsed)

        validated = self._validate_with_repair(attempt, raw_output, elapsed)
        if validated is None:
            self.finished_attempt_ids.add(attempt_id)
            self._recover(attempt_id, "malformed_output", elapsed)
            return "unrepairable"

        accepted_raw, records = validated
        adopted = self.run.record_attempt(
            attempt.seat_id,
            attempt.attempt_id,
            accepted_raw,
            {"schema_version": CONTRACT_VERSION, "records": records},
        )
        self.finished_attempt_ids.add(attempt_id)
        state = self.recovery.seats[attempt.seat_id]
        if adopted and state.mark_adopted(attempt_id):
            self.adopted_records[attempt.seat_id] = list(records)
            self._event("first_valid_result_adopted", elapsed, attempt)
            return "adopted"
        self._event("valid_result_retained_as_diagnostic", elapsed, attempt)
        return "diagnostic"

    def report_failure(self, attempt_id, failure_kind, message):
        self._require_started()
        if failure_kind not in FAILURE_KINDS:
            raise ValueError("未知 failure kind：{}".format(failure_kind))
        self._sync_deadlines()
        attempt = self._attempt(attempt_id)
        elapsed = self.elapsed_ms
        if self.seal is not None or not self.accepting_results:
            self._event(
                "failure_ignored_after_cutoff",
                elapsed,
                attempt,
                failure_kind=failure_kind,
                error=str(message),
            )
            return None
        self.finished_attempt_ids.add(attempt_id)
        self._event(
            failure_kind,
            elapsed,
            attempt,
            error=str(message),
        )
        if self.seal is not None or not self.accepting_results:
            return None
        return self._recover(attempt_id, failure_kind, elapsed)

    def _milestone(self, elapsed):
        self.completed_milestones.add(elapsed)
        self._event("milestone", elapsed, detail="T+{}ms".format(elapsed))
        if elapsed == START_RETRY_MS:
            self._retry_unstarted(elapsed)
        elif elapsed == PRIMARY_ONLY_END_MS:
            self._event("trusted_secondary_sources_enabled", elapsed)
        elif elapsed == CHECKPOINT_MS:
            self._checkpoint_all(elapsed)
        elif elapsed == REPLACEMENT_MS:
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
                self._event(
                    "recovery_exhausted", elapsed, previous,
                    error="same-model retry and any configured replacement already used",
                )

    def _launch(self, attempt, at_ms):
        self.attempts[attempt.attempt_id] = attempt
        self._event(
            "attempt_launch_requested",
            at_ms,
            attempt,
            model=attempt.model,
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
            self._event("startup_error", at_ms, attempt, error=str(exc))
            self._recover(attempt.attempt_id, "startup_error", at_ms)
            return
        if started is False:
            self._event("attempt_start_pending", at_ms, attempt)
            return
        self.recovery.seats[attempt.seat_id].mark_started(attempt.attempt_id)
        self._event("attempt_started", at_ms, attempt, model=attempt.model)

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
            self._event(
                "recovery_exhausted",
                elapsed,
                self._attempt(attempt_id),
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
            self.finished_attempt_ids.add(attempt_id)

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
                "reason": "research_result_window_closed",
                "raw_output": raw_output,
            },
            source="late research diagnostic",
        )
        self._event("late_result_discarded", elapsed, attempt)
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
