"""Deterministic seven-seat integration drill; never live market evidence."""

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .clock import iso_utc
from .contract_validator import CONTRACT_VERSION, validate_evidence_card
from .debate_state_machine import DebateStateMachine
from .question import UnsupportedQuestionError, analyze_question
from .report_fixtures import load_fixture
from .report_renderer import render_market_html, render_market_markdown
from .report_workflow import run_report_workflow
from .research_scheduler import ResearchScheduler
from .run_store import RunStore, new_run_id
from .seats import SEAT_IDS
from .system_preflight import load_frozen_roster


DRILL_START_UTC = datetime(2026, 8, 1, 2, 0, 0, tzinfo=timezone.utc)
LIMITATION = "不得作為真實市場或訂閱 provider READY 證據"


@dataclass(frozen=True)
class DrillResult:
    run_id: str
    run_dir: Path
    timeline: dict


class DrillClock:
    def __init__(self):
        self.elapsed = 0

    def utc_now(self):
        return DRILL_START_UTC + timedelta(milliseconds=self.elapsed)

    def monotonic_ms(self):
        return self.elapsed

    def advance_to(self, elapsed_ms):
        if elapsed_ms < self.elapsed:
            raise ValueError("drill clock 不得倒退")
        self.elapsed = elapsed_ms

    def advance(self, elapsed_ms):
        self.advance_to(self.elapsed + elapsed_ms)


class _ProcessRunner:
    def start(self, attempt, checkpoint):
        return True

    def checkpoint(self, attempt_id):
        return {"public_status": "fixture checkpoint", "attempt_id": attempt_id}

    def correct(self, attempt, raw_output, exact_error):
        return None

    def cancel(self, attempt_id):
        return None

    def terminate(self, attempt_id):
        return None


class _EvidenceGateway:
    def validate(self, attempt, raw_output):
        records = json.loads(raw_output)
        if not isinstance(records, list):
            raise ValueError("fixture evidence 必須為陣列")
        for record in records:
            validate_evidence_card(record)
            if record["seat_id"] != attempt.seat_id or record["attempt_id"] != attempt.attempt_id:
                raise ValueError("fixture evidence lineage 不一致")
        return records


class _NoRepair:
    name = "fixture-no-repair"

    def repair(self, attempt, raw_output, exact_error):
        return None


def run_fake_competition_drill(*, data_root, question, token):
    """Exercise scheduler, debate and report workflow with one fake clock."""
    scope = analyze_question(question)
    if len(scope.assets) != 1 or scope.assets[0] != "BTC":
        raise UnsupportedQuestionError("competition drill 只接受 BTC 單幣合法題目。")
    roster = load_frozen_roster()
    clock = DrillClock()
    run_id = new_run_id(clock.utc_now(), scope.asset_slug, token)
    store = RunStore(Path(data_root))
    run = store.create_run(run_id, SEAT_IDS)
    run.write_json(
        "question.json",
        {
            "schema_version": CONTRACT_VERSION,
            "run_id": run_id,
            "phase": "question",
            "created_at_utc": iso_utc(clock.utc_now()),
            "elapsed_ms": 0,
            "question": question,
            "question_type": "single_asset_market_state",
            "assets": ["BTC"],
            "period_days": scope.period_days,
        },
    )

    models = {seat["seat_id"]: seat["target_model"] for seat in roster["seats"]}
    scheduler = ResearchScheduler(
        run=run,
        clock=clock,
        gateway=_EvidenceGateway(),
        process_runner=_ProcessRunner(),
        format_repairer=_NoRepair(),
        primary_models=models,
        replacement_models=models,
    )
    scheduler.start()
    completion_ms = {}
    for index, seat_id in enumerate(SEAT_IDS, start=1):
        clock.advance_to(index * 1_000)
        attempt = scheduler.recovery.seats[seat_id].attempts[0]
        raw = json.dumps(
            [_evidence(run_id, seat_id, attempt.attempt_id, clock)],
            ensure_ascii=False,
        )
        if scheduler.submit_result(attempt.attempt_id, raw) != "adopted":
            raise RuntimeError("fixture seat 未被採用：{}".format(seat_id))
        completion_ms[seat_id] = clock.monotonic_ms()

    for milestone in (30_000, 90_000, 150_000, 210_000, 285_000, 300_000):
        clock.advance_to(milestone)
        scheduler.tick()
    evidence = [
        card
        for seat_id in SEAT_IDS
        for card in scheduler.adopted_records[seat_id]
    ]
    run.write_jsonl("evidence.jsonl", evidence, source="sealed fake drill evidence")

    debate = DebateStateMachine(
        run=run,
        clock=clock,
        gateway=None,
        question_type="single_asset_market_state",
        evidence_records=evidence,
        evidence_snapshot_sha256=scheduler.seal["sha256"],
        start_monotonic_ms=0,
        started_at_utc=DRILL_START_UTC,
    )
    stances = ["bullish"] * 6 + ["bearish"]
    _complete_first_round(debate, stances, scheduler.seal["sha256"])
    # Persist minority first; the sixth bullish vote then stops at 6/1 with all
    # seven seats valid instead of stopping before the dissenter can vote.
    for index in (6, 0, 1, 2, 3, 4, 5):
        debate.relay(
            _message(
                SEAT_IDS[index],
                "final_vote",
                "final",
                stances[index],
                scheduler.seal["sha256"],
            )
        )
    votes = debate.persist()

    sources = {
        "evidence": evidence,
        "debate": [
            entry for entry in debate.entries if entry.get("event") == "seat_message"
        ],
        "votes": votes,
    }

    def core_author(attempt, errors):
        clock.advance(30_000)
        return _core_report(run_id, sources, clock)

    def renderer(report):
        clock.advance(10_000)
        return {
            "markdown": render_market_markdown(report),
            "html": render_market_html(report),
        }

    outcome = run_report_workflow(
        clock,
        sources,
        core_author,
        renderer,
        run_start_monotonic_ms=0,
    )
    if outcome.status not in ("accepted", "corrected") or outcome.late:
        raise RuntimeError("fake Core report 未通過：{}".format(outcome.errors))
    run.write_json("report.json", outcome.report, source="fake Core authored report")
    run.write_text("report.md", outcome.rendered["markdown"], source="validated report")
    run.write_text("report.html", outcome.rendered["html"], source="validated report")

    timeline = {
        "all_seats_dispatched_at_ms": 0,
        "seat_completion_ms": completion_ms,
        "research_accept_until_ms": 285_000,
        "evidence_snapshot_sealed_at_ms": 300_000,
        "evidence_snapshot_sha256": scheduler.seal["sha256"],
        "debate_stop_at_ms": votes["stop_elapsed_ms"],
        "debate_stop_reason": votes["stop_reason"],
        "report_completed_at_ms": clock.monotonic_ms(),
        "report_hard_deadline_ms": 780_000,
    }
    manifest = {
        "schema_version": CONTRACT_VERSION,
        "run_id": run_id,
        "provider_mode": "fake-competition-drill",
        "competition_ready": False,
        "question": question,
        "assets": ["BTC"],
        "period_days": scope.period_days,
        "started_at_utc": iso_utc(DRILL_START_UTC),
        "completed_at_utc": iso_utc(clock.utc_now()),
        "elapsed_ms": clock.monotonic_ms(),
        "code_root": str(Path(__file__).resolve().parent.parent),
        "data_root": str(store.data_root),
        "run_dir": str(run.path),
        "seats": [
            {
                **seat,
                "actual_model": "fixture:{}".format(seat["target_model"]),
                "completion_ms": completion_ms[seat["seat_id"]],
            }
            for seat in roster["seats"]
        ],
        "tally": votes["tally"],
        "competition_timeline": timeline,
        "artifacts": run.artifact_index(),
        "limitations": [LIMITATION],
    }
    run.write_json("manifest.json", manifest, source="fake competition drill manifest")
    store.point_latest_at(run)
    return DrillResult(run_id, run.path, timeline)


def _evidence(run_id, seat_id, attempt_id, clock):
    index = SEAT_IDS.index(seat_id)
    retrieved = iso_utc(clock.utc_now())
    return {
        "schema_version": CONTRACT_VERSION,
        "evidence_id": "{}-01".format(seat_id),
        "run_id": run_id,
        "seat_id": seat_id,
        "attempt_id": attempt_id,
        "phase": "research",
        "created_at_utc": retrieved,
        "elapsed_ms": clock.monotonic_ms(),
        "asset": "BTC",
        "category": seat_id,
        "statement": "{} 的固定 fake drill 證據。".format(seat_id),
        "direction": "oppose" if seat_id == "counter-evidence" else "support",
        "source_url": "https://fake.invalid/{}/1".format(seat_id),
        "source_origin": "fake-source:{}".format(seat_id),
        "source_tier": 1 if index < 4 else 2,
        "published_at_utc": iso_utc(DRILL_START_UTC - timedelta(hours=1)),
        "retrieved_at_utc": retrieved,
        "excerpt": "fixture numeric value {}".format(index + 1),
        "credibility_note": "deterministic fixture; not real market evidence",
    }


def _message(seat_id, kind, suffix, stance, snapshot_sha, **overrides):
    value = {
        "schema_version": CONTRACT_VERSION,
        "message_id": "{}-{}".format(seat_id, suffix),
        "seat_id": seat_id,
        "attempt_id": "{}-a1".format(seat_id),
        "kind": kind,
        "round": 1,
        "evidence_snapshot_sha256": snapshot_sha,
        "evidence_ids": ["{}-01".format(seat_id)],
        "public_reason": "{} 的公開 fake drill 理由。".format(seat_id),
        "stance": stance,
        "stance_change_reason": None,
    }
    value.update(overrides)
    return value


def _complete_first_round(machine, stances, snapshot_sha):
    for seat_id, stance in zip(SEAT_IDS, stances):
        machine.relay(_message(seat_id, "position", "position", stance, snapshot_sha, round=0))

    incoming = {seat_id: [] for seat_id in SEAT_IDS}
    challenges = []
    for index, seat_id in enumerate(SEAT_IDS):
        target_index = next(
            other for other in range(7)
            if other != index and stances[other] != stances[index]
        )
        target = SEAT_IDS[target_index]
        challenge = _message(
            seat_id,
            "challenge",
            "challenge-to-{}".format(target),
            stances[index],
            snapshot_sha,
            target_seat_id=target,
            target_claim="{}-position".format(target),
        )
        challenges.append(challenge)
        incoming[target].append(challenge)
    for target_index, target in enumerate(SEAT_IDS):
        if incoming[target]:
            continue
        author_index = next(
            other for other in range(7)
            if other != target_index and stances[other] != stances[target_index]
        )
        author = SEAT_IDS[author_index]
        challenge = _message(
            author,
            "challenge",
            "extra-challenge-to-{}".format(target),
            stances[author_index],
            snapshot_sha,
            target_seat_id=target,
            target_claim="{}-position".format(target),
        )
        challenges.append(challenge)
        incoming[target].append(challenge)
    for challenge in challenges:
        machine.relay(challenge)
    for index, seat_id in enumerate(SEAT_IDS):
        challenge = incoming[seat_id][0]
        machine.relay(
            _message(
                seat_id,
                "response",
                "response",
                stances[index],
                snapshot_sha,
                responds_to=[challenge["message_id"]],
                target_seat_id=challenge["seat_id"],
            )
        )


def _core_report(run_id, sources, clock):
    report = deepcopy(load_fixture("consensus-6-1")["report"])
    report.update(
        run_id=run_id,
        generated_at_utc=iso_utc(clock.utc_now()),
        tally=dict(sources["votes"]["tally"]),
        consensus_status=sources["votes"]["consensus_status"],
        adopted_stance=sources["votes"]["adopted_stance"],
        limitations=[
            "本資料為固定 fake drill，不代表真實市場。",
            "票數代表 Agent 共識，不代表客觀真理。",
        ],
    )
    report["period"] = {
        "label": "過去 14 日",
        "start_utc": iso_utc(DRILL_START_UTC - timedelta(days=14)),
        "end_utc": iso_utc(DRILL_START_UTC),
    }
    by_evidence = {card["evidence_id"]: card for card in sources["evidence"]}
    report["seats"] = []
    for vote in sources["votes"]["votes"]:
        cited = vote["final_evidence_ids"]
        report["seats"].append(
            {
                "seat_id": vote["seat_id"],
                "initial_stance": vote["initial_stance"],
                "final_stance": vote["final_stance"],
                "stance_changed": vote["stance_changed"],
                "initial_public_reason": vote["initial_public_reason"],
                "public_reason": vote["final_public_reason"],
                "stance_change_reason": vote["stance_change_reason"],
                "no_change_reason": vote["final_public_reason"],
                "replacement_attempt_ids": vote["attempt_ids"][1:],
                "support_evidence_ids": [
                    item for item in cited if by_evidence[item]["direction"] == "support"
                ],
                "counter_evidence_ids": [
                    item for item in cited if by_evidence[item]["direction"] != "support"
                ],
            }
        )
    report["evidence"] = [
        {
            "evidence_id": card["evidence_id"],
            "url": card["source_url"],
            "statement": card["statement"],
            "direction": card["direction"],
        }
        for card in sources["evidence"]
    ]
    return report
