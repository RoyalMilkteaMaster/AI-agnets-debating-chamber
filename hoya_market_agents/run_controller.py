"""Straight-through orchestration of one run.

The controller sequences research, debate and voting, keeps each seat writing
only into its own directory, and merges the shared artifacts as the single
writer. It never judges market direction: the tally it computes is arithmetic
over the seats' own votes.

This fake tracer path stays straight-through. Ticket #5 provides the separate
research deadline state machine for the later real-provider adapters; the
absolute 6/5/4 debate thresholds remain a later ticket.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .clock import SystemClock, iso_utc
from .contract_validator import (
    CONTRACT_VERSION,
    RUN_RULES_FIELD,
    STANCES,
    run_rules_record,
    validate_question_package,
    validate_run_manifest,
    validate_seat_evidence,
)
from .debate_rules import debate_rules
from .prompt_builder import build_seat_prompt, load_research_snapshot
from .provider_gateway import ProviderGateway
from .question import UnsupportedQuestionError, analyze_question
from .report_renderer import build_report, render_html, render_markdown
from .report_audit_renderer import render_debate_html
from .run_store import default_token, new_run_id
from .seats import SEAT_IDS, load_roster

SCHEMA_VERSION = CONTRACT_VERSION
DEBATE_ROUND = 1

SCOPE_LIMITS = (
    "本次執行使用 fake provider，內容為離線示範資料，不得作為市場依據。",
    "本 fake tracer path 尚未接上 Ticket #5 的研究時間狀態機。",
    "本 fake tracer path 不模擬重試、替補或 Format Repair Agent。",
    "尚未實作絕對 6／5／4 共識門檻與信心燈號；votes.json 只記錄實際票數。",
    "尚未實作兩幣比較與事件影響的投票詞彙；只支援單一資產／整體立場。",
)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    run_dir: Path
    data_root: Path
    artifacts: dict
    tally: dict
    seat_stances: dict


class RunController:
    """Drives one run from question to rendered report."""

    def __init__(self, store, provider, clock=None, roster=None, token_source=None):
        self.store = store
        self.provider = provider
        self.clock = clock or SystemClock()
        self.roster = roster or load_roster()
        self.token_source = token_source or default_token

    def execute(self, question):
        scope = self._approve(question)
        started_at_utc = self.clock.utc_now()
        start_monotonic_ms = self.clock.monotonic_ms()
        # 規則在 run 開始的這一刻定下來，整趟就用這一份，manifest 也記這一份。
        # 在 _write_manifest 才現讀的話，途中有人 reload 就會寫下一份這場 run 從
        # 來沒有遵守過的規則——事後驗證會照那份去判，比不記錄還糟。
        rules = debate_rules()

        run_id = new_run_id(started_at_utc, scope.asset_slug, self.token_source())
        run = self.store.create_run(run_id, SEAT_IDS)
        question_package = validate_question_package(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "phase": "question",
                "created_at_utc": iso_utc(started_at_utc),
                "elapsed_ms": 0,
                "question": scope.question,
                "question_type": "single_asset",
                "assets": list(scope.assets),
                "period_days": scope.period_days,
            }
        )
        run.write_json("question.json", question_package, source="approved question package")
        gateway = ProviderGateway(
            provider=self.provider,
            clock=self.clock,
            run_id=run_id,
            start_monotonic_ms=start_monotonic_ms,
        )

        evidence = self._research(run, gateway, scope)
        debate = self._debate(run, gateway, scope, evidence)
        votes, tally = self._vote(run, gateway, scope, evidence, debate)
        self._report(run, scope, evidence, debate, votes, tally, started_at_utc)

        self._write_manifest(
            run=run,
            gateway=gateway,
            scope=scope,
            votes=votes,
            tally=tally,
            started_at_utc=started_at_utc,
            start_monotonic_ms=start_monotonic_ms,
            rules=rules,
        )
        self.store.point_latest_at(run)

        return RunResult(
            run_id=run_id,
            run_dir=run.path,
            data_root=self.store.data_root,
            artifacts={name: run.path / name for name in run.artifact_hashes},
            tally=tally,
            seat_stances={record["seat_id"]: record["stance"] for record in votes},
        )

    def _approve(self, question):
        scope = analyze_question(question)
        if len(scope.assets) != 1:
            raise UnsupportedQuestionError(
                "本版本只支援單一資產題目，題目指名 {}；fail closed。".format(
                    "、".join(scope.assets)
                )
            )
        return scope

    def _research(self, run, gateway, scope):
        evidence = []
        for seat in self.roster:
            prompt = build_seat_prompt(scope, seat, "research")
            run.write_text(self._seat_file(seat, "prompt-research.txt"), prompt.text)
            cards = gateway.collect_evidence(seat, scope, prompt)
            validate_seat_evidence(seat.seat_id, cards)
            run.record_attempt(
                seat.seat_id,
                gateway.attempt_id(seat.seat_id),
                json.dumps(cards, ensure_ascii=False),
                {"schema_version": SCHEMA_VERSION, "records": cards},
            )
            run.write_jsonl(self._seat_file(seat, "research.jsonl"), cards)
            evidence += cards
        run.write_jsonl("evidence.jsonl", evidence)
        return evidence

    def _debate(self, run, gateway, scope, evidence):
        turns = []
        for seat in self.roster:
            prompt = build_seat_prompt(scope, seat, "debate", evidence_snapshot=evidence)
            run.write_text(self._seat_file(seat, "prompt-debate.txt"), prompt.text)
            turn = gateway.collect_debate_turn(seat, scope, prompt, evidence, DEBATE_ROUND)
            run.write_json(self._seat_file(seat, "debate.json"), turn)
            turns.append(turn)
        run.write_jsonl("debate.jsonl", turns)
        return turns

    def _vote(self, run, gateway, scope, evidence, debate):
        votes = []
        for seat in self.roster:
            prompt = build_seat_prompt(
                scope, seat, "vote", evidence_snapshot=evidence, debate_snapshot=debate
            )
            run.write_text(self._seat_file(seat, "prompt-vote.txt"), prompt.text)
            record = gateway.collect_vote(seat, scope, prompt, evidence, debate, DEBATE_ROUND)
            run.write_json(self._seat_file(seat, "vote.json"), record)
            votes.append(record)

        tally = {stance: 0 for stance in STANCES}
        for record in votes:
            tally[record["stance"]] += 1

        run.write_json(
            "votes.json",
            {
                "run_id": run.run_id,
                "schema_version": SCHEMA_VERSION,
                "round": DEBATE_ROUND,
                "seat_count": len(votes),
                "tally": tally,
                "votes": votes,
                "scope_limits": list(SCOPE_LIMITS),
            },
        )
        return votes, tally

    def _report(self, run, scope, evidence, debate, votes, tally, started_at_utc):
        report = build_report(
            run_id=run.run_id,
            question=scope.question,
            assets=scope.assets,
            period_days=scope.period_days,
            period_stated=scope.period_stated,
            provider_mode=self.provider.mode,
            started_at_utc=iso_utc(started_at_utc),
            generated_at_utc=iso_utc(self.clock.utc_now()),
            evidence=evidence,
            debate=debate,
            votes=votes,
            tally=tally,
            roster=self.roster,
            scope_limits=SCOPE_LIMITS,
        )
        run.write_json("report.json", report)
        run.write_text("report.md", render_markdown(report))
        run.write_text("report.html", render_html(report))
        run.write_text(
            "debate.html",
            render_debate_html(
                report,
                {"evidence": evidence, "debate": debate, "votes": {"votes": votes}},
            ),
        )
        return report

    def _write_manifest(
        self, run, gateway, scope, votes, tally, started_at_utc, start_monotonic_ms, rules
    ):
        completed_at_utc = self.clock.utc_now()
        stances = {record["seat_id"]: record["stance"] for record in votes}
        research_snapshot = load_research_snapshot()

        manifest = {
            "run_id": run.run_id,
            "schema_version": SCHEMA_VERSION,
            "provider_mode": gateway.mode,
            "question": scope.question,
            "assets": list(scope.assets),
            "period_days": scope.period_days,
            "period_stated": scope.period_stated,
            "research_snapshot": {
                "upstream_commit": research_snapshot.upstream_commit,
                "git_blob_sha": research_snapshot.git_blob_sha,
                "local_sha256": research_snapshot.sha256,
            },
            "started_at_utc": iso_utc(started_at_utc),
            "completed_at_utc": iso_utc(completed_at_utc),
            "elapsed_ms": self.clock.monotonic_ms() - start_monotonic_ms,
            "code_root": str(Path(__file__).resolve().parent.parent),
            "data_root": str(self.store.data_root),
            "run_dir": str(run.path),
            "seats": [
                _seat_manifest_entry(seat, gateway.attempts, stances[seat.seat_id])
                for seat in self.roster
            ],
            "tally": tally,
            "artifacts": run.artifact_index(),
            "scope_limits": list(SCOPE_LIMITS),
            RUN_RULES_FIELD: run_rules_record(rules),
        }
        validate_run_manifest(manifest)
        run.write_json("manifest.json", manifest, source="final run manifest")

    @staticmethod
    def _seat_file(seat, name):
        return "agents/{}/{}".format(seat.output_dir, name)


def _seat_manifest_entry(seat, attempts, stance):
    own = [attempt for attempt in attempts if attempt["seat_id"] == seat.seat_id]
    research = [attempt for attempt in own if attempt["phase"] == "research"]
    return {
        "seat_id": seat.seat_id,
        "focus": seat.focus,
        "output_dir": "agents/{}".format(seat.output_dir),
        "attempt_ids": sorted({attempt["attempt_id"] for attempt in own}),
        "evidence_count": sum(attempt["record_count"] for attempt in research),
        "stance": stance,
    }
