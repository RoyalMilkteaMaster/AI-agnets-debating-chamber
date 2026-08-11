"""Timed validation workflow for a report authored by Core.

Only Core supplies normal report prose.  Python may create a deterministic red
audit wrapper when the Core report cannot be accepted after one correction.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .contract_validator import CONTRACT_VERSION
from .question import ASSET_CLASS_OPEN
from .report_contract import ReportContractError, validate_market_report
from .seats import SEAT_IDS

CORE_DRAFT_LIMIT_MS = 90_000
CORRECTION_WINDOW_MS = 60_000
RENDER_WINDOW_MS = 30_000
HARD_DEADLINE_MS = 15 * 60_000


@dataclass(frozen=True)
class ReportWorkflowOutcome:
    """One workflow result.

    ``audit_contract_problems`` 只在紅字稽核路徑上有意義：它是那份自動組出來的
    稽核報告**自己**沒通過契約時的原因，空 tuple 代表通過。這個欄位存在的理由是
    可觀察性——原本那段自驗證把成功結果丟掉、把 ``ReportContractError`` 吞掉，於
    是「它有沒有跑、跑出什麼」在外面完全看不出來，連整段刪掉都沒有任何測試會
    紅。稽核報告連自己的契約都過不了，是操作者該知道的事。

    **目前的可見範圍只到這個物件。** ``finalized_handshake`` 的鍵沒有跟著加，所
    以直接讀 handshake 的操作者看不到它——要讓它一路浮到操作者面前，得動 run
    artifact 的資料格式，那會撞到 Ticket 07「同 token 重跑逐檔一致」的契約，屬後
    續資料格式票。Python 呼叫端（``run_core_report``／測試）現在就讀得到。
    """

    status: str
    report: dict
    errors: tuple
    corrections_used: int
    late: bool
    elapsed_ms: int
    phase_elapsed_ms: dict
    rendered: object = None
    audit_contract_problems: tuple = ()
    red_branch: str = ""


def _confidence_of(rules):
    """The block report validation needs, out of a whole-rules snapshot.

    ``rules`` 在本模組一律指一份 :class:`~.debate_rules.DebateRules` 快照（與
    ``run_verifier``、``debate_driver`` 同義）；``report_contract`` 那一側的
    ``rules`` 指的是 ``ConfidenceRules``。轉換就只發生在這一個地方。
    """
    return rules.confidence if rules is not None else None


def run_report_workflow(
    clock,
    sources,
    core_author,
    renderer=None,
    run_start_monotonic_ms=None,
    rules=None,
    asset_class=ASSET_CLASS_OPEN,
):
    """Accept one Core draft, at most one correction, then one render pass.

    ``rules`` 是呼叫端的規則快照。撰稿當下被告知的信心上限，與事後拿來驗收它的
    上限，必須來自**同一份**設定：分開讀的話，Core 依規則 A 寫出合法的燈號，卻
    被規則 B 判成「信心高於資料上限」而錯拒——一份完全合法的報告因此變成
    red_audit，而且輸出裡沒有任何欄位記得是哪一份規則做的判斷。

    這一份快照貫穿初稿驗證、correction 驗證與 red-audit 自驗證三處。省略時每處
    現讀，行為與過去相同。

    ``asset_class`` 是這場 run 的市場，來自它的 question package。紅字稽核路徑
    自己組一份報告契約發佈出去，那份骨架同樣要說出市場：離線 renderer 依它選席位
    套組（ADR 0006），欄位缺席就會落到 open 套，讓台股 run 的驗證失敗報告印出幣圈
    席名。省略時＝呼叫端沒有指名市場，與其他讀取端一樣得到 open 套。
    """
    workflow_start = clock.monotonic_ms()
    run_start = workflow_start if run_start_monotonic_ms is None else run_start_monotonic_ms
    if run_start > workflow_start:
        raise ValueError("run_start_monotonic_ms 不得晚於目前 monotonic time")
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
            run_start_monotonic_ms=run_start,
            rules=rules,
            branch="draft-exception",
            asset_class=asset_class,
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
            run_start_monotonic_ms=run_start,
            rules=rules,
            branch="draft-over-limit",
            asset_class=asset_class,
        )
    if clock.monotonic_ms() - run_start >= HARD_DEADLINE_MS:
        return _red_outcome(
            clock, workflow_start, sources, ["T+15 或之後不得宣稱成功"], 0, phases,
            late=True, run_start_monotonic_ms=run_start, rules=rules,
            branch="t15-after-draft",
            asset_class=asset_class,
        )

    try:
        accepted = validate_market_report(draft, sources, rules=_confidence_of(rules))
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
                run_start_monotonic_ms=run_start,
                rules=rules,
                branch="correction-exception",
                asset_class=asset_class,
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
                run_start_monotonic_ms=run_start,
                rules=rules,
                branch="correction-over-limit",
                asset_class=asset_class,
            )
        if clock.monotonic_ms() - run_start >= HARD_DEADLINE_MS:
            return _red_outcome(
                clock,
                workflow_start,
                sources,
                list(first_error.problems) + ["T+15 或之後不得宣稱成功"],
                1,
                phases,
                late=True,
                run_start_monotonic_ms=run_start,
                rules=rules,
                branch="t15-after-correction",
                asset_class=asset_class,
            )
        try:
            accepted = validate_market_report(corrected, sources, rules=_confidence_of(rules))
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
                run_start_monotonic_ms=run_start,
                rules=rules,
                branch="second-validation-failure",
                asset_class=asset_class,
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
                run_start_monotonic_ms=run_start,
                rules=rules,
                branch="renderer-exception",
                asset_class=asset_class,
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
                run_start_monotonic_ms=run_start,
                rules=rules,
                branch="renderer-over-limit",
                asset_class=asset_class,
            )
    if clock.monotonic_ms() - run_start >= HARD_DEADLINE_MS:
        return _red_outcome(
            clock,
            workflow_start,
            sources,
            ["T+15 或之後不得宣稱成功"],
            corrections,
            phases,
            late=True,
            run_start_monotonic_ms=run_start,
            rules=rules,
            branch="t15-after-render",
            asset_class=asset_class,
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


def build_red_audit_report(
    sources, errors, generated_at_utc=None, asset_class=ASSET_CLASS_OPEN
):
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
        "asset_class": asset_class,
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


def _red_outcome(
    clock,
    workflow_start,
    sources,
    errors,
    corrections,
    phases,
    late=False,
    run_start_monotonic_ms=None,
    rules=None,
    branch="",
    asset_class=ASSET_CLASS_OPEN,
):
    """``branch`` names which of the ten red call sites produced this outcome.

    三個 T+15 呼叫點的錯誤訊息一字不差，所以光看 ``errors`` 分不出流程走到哪一
    個——停掉其中一個，流程會掉到下一個，訊息相同，測試照樣綠。identity 讓「這
    個情境到達的是這一個呼叫點」變成可斷言的事實。
    """
    now_ms = clock.monotonic_ms()
    run_start = workflow_start if run_start_monotonic_ms is None else run_start_monotonic_ms
    run_elapsed_ms = now_ms - run_start
    errors = list(errors)
    if run_elapsed_ms >= HARD_DEADLINE_MS and not any("T+15" in error for error in errors):
        errors.append("T+15 或之後不得宣稱成功")
    generated = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=now_ms)
    report = build_red_audit_report(
        sources,
        errors,
        generated_at_utc=generated.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        asset_class=asset_class,
    )
    # The wrapper itself remains independently contract-checkable when the
    # official source records are sound. 結果必須留下痕跡：吞掉之後這段檢查就
    # 只是一個沒有人看得到的動作，刪掉它也不會有任何測試變紅。
    try:
        validate_market_report(report, sources, rules=_confidence_of(rules))
        audit_problems = ()
    except ReportContractError as audit_failure:
        audit_problems = tuple(audit_failure.problems)
    return ReportWorkflowOutcome(
        status="red_audit",
        report=report,
        errors=tuple(errors),
        corrections_used=corrections,
        late=late or run_elapsed_ms >= HARD_DEADLINE_MS,
        elapsed_ms=now_ms - workflow_start,
        phase_elapsed_ms=dict(phases),
        audit_contract_problems=audit_problems,
        red_branch=branch,
    )
