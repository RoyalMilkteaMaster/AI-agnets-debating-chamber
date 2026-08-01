"""Timed validation workflow for a report authored by Core.

Only Core supplies normal report prose.  Python may create a deterministic red
audit wrapper when the Core report cannot be accepted after one correction.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .contract_validator import CONTRACT_VERSION
from .report_contract import ReportContractError, validate_market_report
from .seats import SEAT_IDS

CORE_DRAFT_LIMIT_MS = 90_000
CORRECTION_WINDOW_MS = 60_000
RENDER_WINDOW_MS = 30_000
HARD_DEADLINE_MS = 13 * 60_000


@dataclass(frozen=True)
class ReportWorkflowOutcome:
    status: str
    report: dict
    errors: tuple
    corrections_used: int
    late: bool
    elapsed_ms: int
    phase_elapsed_ms: dict
    rendered: object = None


def run_report_workflow(clock, sources, core_author, renderer=None):
    """Accept one Core draft, at most one correction, then one render pass."""
    workflow_start = clock.monotonic_ms()
    phases = {"draft": 0, "correction": 0, "render": 0}

    draft_start = clock.monotonic_ms()
    try:
        draft = core_author(1, ())
    except Exception as exc:  # provider/process failures become an auditable report
        return _red_outcome(
            clock,
            workflow_start,
            sources,
            ["Core 初稿失敗：{}".format(type(exc).__name__)],
            0,
            phases,
        )
    phases["draft"] = clock.monotonic_ms() - draft_start
    if phases["draft"] > CORE_DRAFT_LIMIT_MS:
        return _red_outcome(
            clock,
            workflow_start,
            sources,
            ["Core 初稿超過 90 秒"],
            0,
            phases,
        )
    if clock.monotonic_ms() >= HARD_DEADLINE_MS:
        return _red_outcome(
            clock, workflow_start, sources, ["T+13 或之後不得宣稱成功"], 0, phases, late=True
        )

    try:
        accepted = validate_market_report(draft, sources)
        status = "accepted"
        corrections = 0
    except ReportContractError as first_error:
        correction_start = clock.monotonic_ms()
        try:
            corrected = core_author(2, tuple(first_error.problems))
        except Exception as exc:
            return _red_outcome(
                clock,
                workflow_start,
                sources,
                list(first_error.problems) + ["Core correction 失敗：{}".format(type(exc).__name__)],
                1,
                phases,
            )
        phases["correction"] = clock.monotonic_ms() - correction_start
        if phases["correction"] > CORRECTION_WINDOW_MS:
            return _red_outcome(
                clock,
                workflow_start,
                sources,
                list(first_error.problems) + ["Core correction 超過 60 秒"],
                1,
                phases,
            )
        if clock.monotonic_ms() >= HARD_DEADLINE_MS:
            return _red_outcome(
                clock,
                workflow_start,
                sources,
                list(first_error.problems) + ["T+13 或之後不得宣稱成功"],
                1,
                phases,
                late=True,
            )
        try:
            accepted = validate_market_report(corrected, sources)
            status = "corrected"
            corrections = 1
        except ReportContractError as second_error:
            return _red_outcome(
                clock,
                workflow_start,
                sources,
                list(second_error.problems),
                1,
                phases,
            )

    rendered = None
    if renderer is not None:
        render_start = clock.monotonic_ms()
        try:
            rendered = renderer(accepted)
        except Exception as exc:
            return _red_outcome(
                clock,
                workflow_start,
                sources,
                ["renderer 失敗：{}".format(type(exc).__name__)],
                corrections,
                phases,
            )
        phases["render"] = clock.monotonic_ms() - render_start
        if phases["render"] > RENDER_WINDOW_MS:
            return _red_outcome(
                clock,
                workflow_start,
                sources,
                ["renderer 超過 30 秒"],
                corrections,
                phases,
            )
    if clock.monotonic_ms() >= HARD_DEADLINE_MS:
        return _red_outcome(
            clock,
            workflow_start,
            sources,
            ["T+13 或之後不得宣稱成功"],
            corrections,
            phases,
            late=True,
        )
    return ReportWorkflowOutcome(
        status=status,
        report=accepted,
        errors=(),
        corrections_used=corrections,
        late=False,
        elapsed_ms=clock.monotonic_ms() - workflow_start,
        phase_elapsed_ms=phases,
        rendered=rendered,
    )


def build_red_audit_report(sources, errors, generated_at_utc=None):
    """Build a directionless process-failure record from official artifacts."""
    votes = sources.get("votes", {})
    evidence = sources.get("evidence", [])
    evidence_by_id = {card.get("evidence_id"): card for card in evidence}
    rows = []
    for vote in votes.get("votes", []):
        cited = vote.get("final_evidence_ids", [])
        rows.append(
            {
                "seat_id": vote.get("seat_id"),
                "initial_stance": vote.get("initial_stance"),
                "final_stance": vote.get("final_stance"),
                "stance_changed": bool(vote.get("stance_changed")),
                "initial_public_reason": vote.get("initial_public_reason") or "未取得初始票。",
                "public_reason": vote.get("final_public_reason") or "未取得有效票。",
                "stance_change_reason": vote.get("stance_change_reason"),
                "no_change_reason": None if vote.get("stance_changed") else (vote.get("final_public_reason") or "未取得有效票。"),
                "replacement_attempt_ids": list(vote.get("attempt_ids", []))[1:],
                "support_evidence_ids": [
                    item for item in cited if evidence_by_id.get(item, {}).get("direction") == "support"
                ],
                "counter_evidence_ids": [
                    item for item in cited if evidence_by_id.get(item, {}).get("direction") != "support"
                ],
            }
        )
    # A malformed source may have missing rows.  Preserve seven identities in
    # the audit without pretending those seats voted.
    existing = {row["seat_id"] for row in rows}
    for seat_id in SEAT_IDS:
        if seat_id not in existing:
            rows.append(
                {
                    "seat_id": seat_id,
                    "initial_stance": None,
                    "final_stance": None,
                    "stance_changed": False,
                    "initial_public_reason": "未取得初始票。",
                    "public_reason": "未取得有效票。",
                    "stance_change_reason": None,
                    "no_change_reason": "沒有可比較的有效票。",
                    "replacement_attempt_ids": [],
                    "support_evidence_ids": [],
                    "counter_evidence_ids": [],
                }
            )
    rows.sort(key=lambda row: SEAT_IDS.index(row["seat_id"]))
    report = {
        "schema_version": CONTRACT_VERSION,
        "run_id": votes.get("run_id", "unknown-run"),
        "generated_at_utc": generated_at_utc or "1970-01-01T00:00:00Z",
        "market_status": "報告驗證失敗",
        "period": {
            "label": "無可採用期間",
            "start_utc": "1970-01-01T00:00:00Z",
            "end_utc": "1970-01-01T00:00:00Z",
        },
        "confidence": {"level": "red", "icon": "🔴", "text": "驗證失敗，不能形成市場結論。"},
        "tally": dict(votes.get("tally", {})),
        "consensus_status": "validation_failed",
        "adopted_stance": None,
        "direction_bearing": False,
        "judgement": "驗證失敗，未形成市場結論。",
        "limitations": ["Core 報告在一次 correction 後仍未通過客觀驗證。"],
        "invalidation_conditions": ["所有驗證錯誤修正前，不得採用任何方向判斷。"],
        "process_failure": True,
        "validation_errors": [str(error) for error in errors] or ["未知驗證失敗"],
        "seats": rows,
        "evidence": [
            {
                "evidence_id": card.get("evidence_id"),
                "url": card.get("source_url"),
                "statement": card.get("statement"),
                "direction": card.get("direction"),
            }
            for card in evidence
        ],
    }
    return report


def _red_outcome(clock, workflow_start, sources, errors, corrections, phases, late=False):
    now_ms = clock.monotonic_ms()
    errors = list(errors)
    if now_ms >= HARD_DEADLINE_MS and not any("T+13" in error for error in errors):
        errors.append("T+13 或之後不得宣稱成功")
    generated = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=now_ms)
    report = build_red_audit_report(
        sources,
        errors,
        generated_at_utc=generated.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    )
    # The wrapper itself remains independently contract-checkable when the
    # official source records are sound.
    try:
        validate_market_report(report, sources)
    except ReportContractError:
        pass
    return ReportWorkflowOutcome(
        status="red_audit",
        report=report,
        errors=tuple(errors),
        corrections_used=corrections,
        late=late or now_ms >= HARD_DEADLINE_MS,
        elapsed_ms=now_ms - workflow_start,
        phase_elapsed_ms=dict(phases),
    )
