"""Ticket T6: everything after the T+4:00 seal, driven fully offline.

No provider, no subprocess and no wall clock. The debate pool is a scripted
stand-in that answers on the same queue contract ``RealSeatRunner`` publishes,
and time only moves when the scripted runner or the sleeper moves the fake
clock — exactly the compressed-time technique ``test_competition_drill`` uses.
"""

import hashlib
import json
import queue
import tempfile
import unittest
from dataclasses import fields, replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from tests.fakes import FIXED_START_UTC, FixedClock, ScriptedTokenSource

from hoya_market_agents.clock import iso_utc
from hoya_market_agents.contract_validator import CONTRACT_VERSION
from hoya_market_agents.debate_driver import (
    CORE_REPORT_SCHEMA,
    DebateDriver,
    assemble_market_report,
    build_turns,
    run_after_seal,
    validate_core_narrative,
)
from hoya_market_agents.debate_rules import VoteRound, debate_rules
from hoya_market_agents.debate_state_machine import content_sha256, stances_for
from hoya_market_agents.report_audit_renderer import render_debate_html
from hoya_market_agents.report_contract import (
    CONFIDENCE_ICONS,
    CONFIDENCE_LEVELS,
    confidence_cap,
)
from hoya_market_agents.report_renderer import render_market_html, render_market_markdown
from hoya_market_agents.launcher import run_launch
from hoya_market_agents.question_package import build_question_package
from hoya_market_agents.research_scheduler import research_deadlines
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.run_verifier import RunVerificationError, verify_run
from hoya_market_agents.seats import SEAT_IDS
from hoya_market_agents.system_preflight import write_ready_certificate

# 取值來源改成 config/debate_rules.json 的載入器；斷言值刻意不動。
RULES = debate_rules()
DEBATE_START_MS = RULES.debate_start_ms
ROUND_ONE_WINDOW_MS = RULES.round_one_window_ms
CHALLENGE_DEADLINE_MS = RULES.challenge_deadline_ms()
THRESHOLD_FIVE_FROM_MS = RULES.reduced_threshold_from_ms
FINAL_ROUND_START_MS = RULES.final_round_start_ms
FINAL_ROUND_END_MS = RULES.final_round_end_ms
FORCE_STOP_MS = RULES.force_stop_ms

QUESTION = "分析 BTC 過去 14 日市場狀態"
COMPARISON_QUESTION = "比較 BTC 與 ETH 過去 14 日的相對強弱"
SEAL_MS = DEBATE_START_MS
COMPARISON_SEAL_MS = 270_000
CARD_STAMP = "2026-03-14T01:00:00Z"
PREFLIGHT_ID = "20260314T005926Z-aaa111"
CERTIFICATE_STAMP = "2026-03-14T00:59:26Z"

BULLISH_SIX = dict.fromkeys(SEAT_IDS[:6], "bullish")
BULLISH_SIX["counter-evidence"] = "bearish"

# 同一個 6/1 房間，換成兩幣比較題自己的立場詞彙。
COMPARISON_SIX = dict.fromkeys(SEAT_IDS[:6], "asset_a_stronger")
COMPARISON_SIX["counter-evidence"] = "asset_b_stronger"

# run 20260802T022316Z 的真實房間：五席 bearish、兩席 neutral，沒有任何 bullish。
BEARISH_FIVE_NEUTRAL_TWO = {
    "spot-technical": "bearish",
    "derivatives": "neutral",
    "onchain": "neutral",
    "official-events": "bearish",
    "news": "bearish",
    "social-macro": "bearish",
    "counter-evidence": "bearish",
}

# 同一個 run 的實測開場延遲（events.jsonl 的 elapsed_ms 減去封存時刻）：四席在
# 20 秒內回答，三席拖到 50 秒之後，最後一席逼近 60 秒。
REAL_OPENING_LATENCY_MS = {
    "counter-evidence": 15_505,
    "onchain": 15_763,
    "spot-technical": 18_771,
    "derivatives": 21_032,
    "news": 50_557,
    "official-events": 51_065,
    "social-macro": 59_080,
}

# run 20260802T043728Z-btc-eth-f8ea46 的真實房間：七席開場立場完全一致（該場是
# asset_a_stronger；這裡在單幣題型上重現同一種房間）。
UNANIMOUS_SEVEN = dict.fromkeys(SEAT_IDS, "bullish")
UNANIMOUS_COMPARISON_SEVEN = dict.fromkeys(SEAT_IDS, "asset_a_stronger")

# Ticket 03 之後，七席開場全數同立場會先觸發盲投直過，所以魔鬼代言人輪（Ticket
# R8）的房間必須是「一致、但還缺一席開場」——那正是 §11.3 保留給 6 票以下的原
# 規則。缺的那一席用真實的 provider 超時製造，不是憑空拿掉一個席位。
SCRUTINY_SILENT_SEAT = SEAT_IDS[-1]
SCRUTINY_SPEAKING_SEATS = tuple(
    seat_id for seat_id in SEAT_IDS if seat_id != SCRUTINY_SILENT_SEAT
)

# 第一輪的實測與估計：codex/antigravity 仍在 20 秒內，claude 帶著完整公開紀錄
# 重讀一次，估 30-60 秒。
REAL_FIRST_ROUND_LATENCY_MS = {
    "spot-technical": 20_000,
    "derivatives": 22_000,
    "onchain": 18_000,
    "official-events": 55_000,
    "news": 48_000,
    "social-macro": 45_000,
    "counter-evidence": 17_000,
}

# Ticket R8 修訂二把第一輪的牆挪到 T+6:00，收集預算因此停在 T+5:55。開場最慢的
# 一席在 T+4:59 才交卷，只剩約 55 秒，所以「趕得上」的那一版把慢席壓在 45 秒
# （實測 30-60 秒區間的中段）；60 秒那一版留給知情取捨的風險案例。
FIRST_ROUND_INSIDE_THE_SIX_MINUTE_WALL_MS = {
    seat_id: min(value, 45_000)
    for seat_id, value in REAL_FIRST_ROUND_LATENCY_MS.items()
}


def evidence_card(run_id, seat_id):
    return {
        "schema_version": CONTRACT_VERSION,
        "evidence_id": "{}-01".format(seat_id),
        "run_id": run_id,
        "seat_id": seat_id,
        "attempt_id": "{}-a1".format(seat_id),
        "phase": "research",
        "created_at_utc": CARD_STAMP,
        "elapsed_ms": 1_000,
        "asset": "BTC",
        "category": seat_id,
        "statement": "本席提交的測試證據陳述，僅用於驗證流程。",
        "direction": "oppose" if seat_id == "counter-evidence" else "support",
        "source_url": "https://example.invalid/{}/1".format(seat_id),
        "source_origin": "example-source:{}".format(seat_id),
        "source_tier": 1 if SEAT_IDS.index(seat_id) < 4 else 2,
        "published_at_utc": CARD_STAMP,
        "retrieved_at_utc": CARD_STAMP,
        "excerpt": "close 68,420",
        "credibility_note": "測試資料，不是真實市場證據。",
    }


def envelope_text(run_id, seat_id, attempt_id):
    card = evidence_card(run_id, seat_id)
    card["attempt_id"] = attempt_id
    return json.dumps(
        {"seat_id": seat_id, "evidence_cards": [card]}, ensure_ascii=False
    )


NARRATIVE = {
    "market_status": "區間震盪且分歧仍在",
    "period_label": "過去 14 日",
    "judgement": "七席公開理由顯示偏多但仍有反方證據，僅能描述為震盪偏多。",
    "confidence_level": "yellow",
    "confidence_text": "多數席位同立場，但反方證據仍未被排除。",
    "limitations": ["票數代表七席代理人的共識，不代表客觀真理。"],
    "invalidation_conditions": ["若反方證據被更高等級來源證實，原描述失效。"],
}


def narrative(**overrides):
    value = dict(NARRATIVE)
    value.update(overrides)
    return value


class ScriptedCoreAdapter:
    """Core's ``codex exec`` seam: scripted narratives on a fake clock."""

    def __init__(self, clock, narratives, advance_ms=10_000):
        self.clock = clock
        self.narratives = list(narratives)
        self.advance_ms = advance_ms
        self.calls = []
        self.allow_search = []

    def invoke(self, prompt, schema, work_dir, allow_search=True):
        self.calls.append(prompt)
        self.allow_search.append(allow_search)
        self.clock.advance_ms(self.advance_ms)
        index = min(len(self.calls) - 1, len(self.narratives) - 1)
        return SimpleNamespace(structured_output=self.narratives[index])


class ScriptedDebateRunner:
    """A debate pool stand-in speaking the real queue contract.

    Each wave advances the fake clock once, on its first dispatch, so a test
    says "round one took 60 seconds" instead of counting sleeps.
    """

    def __init__(
        self,
        results_queue,
        clock,
        stances,
        *,
        wave_advance_ms=None,
        revotes=None,
        silent=(),
        failures=None,
        core_adapter=None,
        responds_to=None,
    ):
        self.results_queue = results_queue
        self.clock = clock
        self.stances = dict(stances)
        self.wave_advance_ms = wave_advance_ms or {}
        self.revotes = revotes or {}
        self.silent = set(silent)
        self.failures = failures or {}
        self.core_adapter = core_adapter
        self.responds_to = responds_to or {}
        self.started = []
        self.cancelled = []
        self.prompts = {}
        self._advanced = set()

    # -- runner protocol ----------------------------------------------------

    def start_debate(self, dispatch):
        seat_id = dispatch.seat_id
        slug = dispatch.dispatch_id[len(seat_id) + 1 :]
        self.started.append(dispatch.dispatch_id)
        self.prompts.setdefault(slug, []).append(dispatch.prompt)
        if slug not in self._advanced:
            self._advanced.add(slug)
            self.clock.advance_ms(self.wave_advance_ms.get(slug, 0))
        if (seat_id, slug) in self.silent:
            return True
        if (seat_id, slug) in self.failures:
            kind, message = self.failures[(seat_id, slug)]
            self.results_queue.put(
                ("debate_failure", dispatch.dispatch_id, kind, message)
            )
            return True
        self.results_queue.put(
            (
                "provider_lineage",
                seat_id,
                {
                    "seat_id": seat_id,
                    "dispatch_id": dispatch.dispatch_id,
                    "provider": "scripted",
                    "actual_model": "scripted-{}".format(seat_id),
                    "elapsed_ms": 1_000,
                },
            )
        )
        self.results_queue.put(
            (
                "debate_result",
                dispatch.dispatch_id,
                json.dumps(self._payload(seat_id, slug), ensure_ascii=False),
            )
        )
        return True

    def cancel(self, dispatch_id):
        self.cancelled.append(dispatch_id)

    def terminate(self, dispatch_id):
        self.cancelled.append(dispatch_id)

    def core_report_adapter(self, timeout_seconds):
        return self.core_adapter

    # -- scripted content ---------------------------------------------------

    def _payload(self, seat_id, slug):
        if slug == "opening":
            return self._vote(seat_id, self.stances[seat_id])
        stance = self.revotes.get((seat_id, slug), self.stances[seat_id])
        changed = stance != self.stances[seat_id]
        self.stances[seat_id] = stance
        return self._vote(seat_id, stance, changed=changed)

    def _vote(self, seat_id, stance, changed=False):
        return {
            "seat_id": seat_id,
            "stance": stance,
            "public_reason": "本席依據已引用的證據說明目前立場。",
            "evidence_ids": ["{}-01".format(seat_id)],
            "stance_change_reason": "反方證據改變本席判斷。" if changed else None,
            "conflicting_evidence_ids": (
                ["{}-01".format(item) for item in SEAT_IDS[:2]]
                if stance == "neutral"
                else None
            ),
            "uncertainty_reason": "正反證據互相衝突。" if stance == "neutral" else None,
            "change_trigger": "出現更高等級的一手資料。" if stance == "neutral" else None,
        }


class DrippingDebateRunner(ScriptedDebateRunner):
    """Answer one wave one seat per poll instead of all inside a single drain.

    ``ScriptedDebateRunner`` queues every reply during dispatch, so the whole
    wave lands in the driver's very first drain and says nothing about when a
    message reaches ``events.jsonl``. This one holds the wave back and releases
    exactly one reply per poll, which is what a live run looks like.
    """

    def __init__(self, *args, drip_slug="opening", **options):
        super().__init__(*args, **options)
        self.drip_slug = drip_slug
        self.held = []

    def start_debate(self, dispatch):
        seat_id = dispatch.seat_id
        slug = dispatch.dispatch_id[len(seat_id) + 1 :]
        if slug != self.drip_slug:
            return super().start_debate(dispatch)
        self.started.append(dispatch.dispatch_id)
        if slug not in self._advanced:
            self._advanced.add(slug)
            self.clock.advance_ms(self.wave_advance_ms.get(slug, 0))
        self.held.append(
            (
                "debate_result",
                dispatch.dispatch_id,
                json.dumps(self._payload(seat_id, slug), ensure_ascii=False),
            )
        )
        return True

    def release_one(self):
        if self.held:
            self.results_queue.put(self.held.pop(0))


class LatencyDebateRunner(ScriptedDebateRunner):
    """Answer every dispatch after that seat's own latency, on the fake clock.

    ``ScriptedDebateRunner`` answers inside ``start_debate``, so a whole wave is
    already in the queue before the driver's first poll and no test can see what
    a wave costs in wall time. Here a dispatch stays in flight until its latency
    has passed, and a dispatch whose latency outruns the ``timeout_seconds`` the
    driver itself handed over comes back as the provider timeout a real CLI
    reports — which is exactly how run 20260802T022316Z lost its first round.
    """

    def __init__(
        self,
        results_queue,
        clock,
        stances,
        *,
        latency_ms,
        default_latency_ms=30_000,
        **options
    ):
        super().__init__(results_queue, clock, stances, **options)
        self.latency_ms = dict(latency_ms)
        self.default_latency_ms = default_latency_ms
        self.inflight = []
        self.dispatched_at_ms = {}

    def start_debate(self, dispatch):
        seat_id = dispatch.seat_id
        slug = dispatch.dispatch_id[len(seat_id) + 1 :]
        self.started.append(dispatch.dispatch_id)
        self.dispatched_at_ms[dispatch.dispatch_id] = self.clock.monotonic_ms()
        latency_ms = self.latency_ms.get((seat_id, slug), self.default_latency_ms)
        budget_ms = int(dispatch.timeout_seconds * 1_000)
        self.inflight.append(
            SimpleNamespace(
                due_ms=self.clock.monotonic_ms() + min(latency_ms, budget_ms),
                dispatch_id=dispatch.dispatch_id,
                seat_id=seat_id,
                slug=slug,
                timed_out=latency_ms > budget_ms,
            )
        )
        return True

    def deliver_due(self):
        """Hand the driver every reply whose latency has now elapsed."""
        now = self.clock.monotonic_ms()
        for call in [item for item in self.inflight if item.due_ms <= now]:
            self.inflight.remove(call)
            self._answer(call)

    def _answer(self, call):
        if call.timed_out:
            self.results_queue.put(
                ("debate_failure", call.dispatch_id, "timeout", "provider 超時")
            )
            return
        self.results_queue.put(
            (
                "provider_lineage",
                call.seat_id,
                {
                    "seat_id": call.seat_id,
                    "dispatch_id": call.dispatch_id,
                    "provider": "scripted",
                    "actual_model": "scripted-{}".format(call.seat_id),
                    "elapsed_ms": 1_000,
                },
            )
        )
        self.results_queue.put(
            (
                "debate_result",
                call.dispatch_id,
                json.dumps(self._payload(call.seat_id, call.slug), ensure_ascii=False),
            )
        )


class StepSleeper:
    """The only thing that moves time forward between waves."""

    def __init__(self, clock, step_ms=1_000):
        self.clock = clock
        self.step_ms = step_ms
        self.calls = 0

    def __call__(self, seconds):
        self.calls += 1
        self.clock.advance_ms(self.step_ms)


class DripSleeper(StepSleeper):
    """Photograph ``events.jsonl`` at every poll, then release the next reply."""

    def __init__(self, clock, run, step_ms=1_000):
        super().__init__(clock, step_ms)
        self.run = run
        self.runner = None
        self.snapshots = []

    def __call__(self, seconds):
        self.snapshots.append(position_seats(self.run))
        if self.runner is not None:
            self.runner.release_one()
        super().__call__(seconds)


class LatencySleeper(StepSleeper):
    """Move the fake clock one poll, then hand over every reply now due."""

    def __init__(self, clock, step_ms=1_000):
        super().__init__(clock, step_ms)
        self.runner = None

    def __call__(self, seconds):
        super().__call__(seconds)
        if self.runner is not None:
            self.runner.deliver_due()


def position_seats(run):
    """Return the seats whose opening position is already in ``events.jsonl``."""
    path = run.path / "events.jsonl"
    if not path.is_file():
        return []
    return [
        entry["seat_id"]
        for entry in (
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        )
        if entry.get("event") == "seat_message" and entry.get("kind") == "position"
    ]


class Stream:
    def __init__(self):
        self.chunks = []

    def write(self, text):
        self.chunks.append(text)
        return len(text)

    def flush(self):
        return None

    @property
    def text(self):
        return "".join(self.chunks)

    @property
    def lines(self):
        return [line for line in self.text.splitlines() if line.strip()]


class DebateDriverTestCase(unittest.TestCase):
    """A run already sealed at T+4:00, ready for the debate to start."""

    def setUp(self):
        self.build_sealed_run(QUESTION)

    def build_sealed_run(self, question):
        """Seal one run at the instant this question type's research窗 closes."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name) / "data"
        self.data_root.mkdir(parents=True)
        self.clock = FixedClock()
        self.sleeper = StepSleeper(self.clock)
        self.results = queue.Queue()
        self.err = Stream()
        self.package = build_question_package(question)
        self.seal_ms = research_deadlines(self.package.question_type).seal_ms
        self.store = RunStore(self.data_root)
        self.run_id = "20260314T015926Z-btc-abc123"
        self.run = self.store.create_run(self.run_id, SEAT_IDS)
        self.evidence = [evidence_card(self.run_id, seat_id) for seat_id in SEAT_IDS]
        self.run.write_jsonl("evidence.jsonl", self.evidence, source="sealed evidence")
        self.seal = self.run.seal_evidence_snapshot(
            self.evidence,
            iso_utc(FIXED_START_UTC + timedelta(milliseconds=self.seal_ms)),
            self.seal_ms,
        )
        self.certificate = {
            "system_preflight_id": PREFLIGHT_ID,
            "manifest_path": "preflight/{}/manifest.json".format(PREFLIGHT_ID),
            "manifest_sha256": "0" * 64,
            "generated_at_utc": CERTIFICATE_STAMP,
        }
        self.research_events = [
            {
                "event": "first_valid_result_adopted",
                "seat_id": seat_id,
                "elapsed_ms": 1_000 * (index + 1),
            }
            for index, seat_id in enumerate(SEAT_IDS)
        ]
        self.clock.advance_ms(self.seal_ms)

    def build_runner(self, stances, **options):
        options.setdefault("core_adapter", ScriptedCoreAdapter(self.clock, [narrative()]))
        return ScriptedDebateRunner(self.results, self.clock, stances, **options)

    def build_latency_runner(self, stances, latency_ms, **options):
        """A pool that answers each dispatch after that seat's own latency."""
        options.setdefault(
            "core_adapter", ScriptedCoreAdapter(self.clock, [narrative()])
        )
        self.sleeper = LatencySleeper(self.clock)
        runner = LatencyDebateRunner(
            self.results, self.clock, stances, latency_ms=latency_ms, **options
        )
        self.sleeper.runner = runner
        return runner

    def drive(self, runner, rules=None):
        driver = DebateDriver(
            run=self.run,
            clock=self.clock,
            runner=runner,
            results_queue=self.results,
            package=self.package,
            evidence_records=self.evidence,
            snapshot_sha256=self.seal["sha256"],
            start_monotonic_ms=0,
            started_at_utc=FIXED_START_UTC,
            sleeper=self.sleeper,
            err=self.err,
            rules=rules,
        )
        return driver, driver.run_debate()

    def finish(self, runner, core_narratives=None):
        if core_narratives is not None:
            runner.core_adapter = ScriptedCoreAdapter(self.clock, core_narratives)
        return run_after_seal(
            run=self.run,
            store=self.store,
            clock=self.clock,
            runner=runner,
            results_queue=self.results,
            package=self.package,
            certificate=self.certificate,
            evidence_records=self.evidence,
            seal=self.seal,
            research_events=self.research_events,
            started_at_utc=FIXED_START_UTC,
            start_monotonic_ms=0,
            sleeper=self.sleeper,
            err=self.err,
        )

    def votes_file(self):
        return json.loads((self.run.path / "votes.json").read_text(encoding="utf-8"))


CONCLUSION_FIRST_RULE = (
    "public_reason 的第一句必須是 30-60 字的核心結論（先立場與理由），之後才展開論據。"
)

# Ticket 03 範圍第 3 條的原文語意，逐字寫在測試裡，不從實作 import 回來。
PERSUASION_GOAL = "用證據說服對方"


QUESTION_BY_TYPE = {
    "single_asset_market_state": "分析 BTC 過去 14 日市場狀態",
    "two_asset_comparison": "比較 BTC 與 ETH 過去 14 日的相對強弱",
    "event_impact": "分析監管事件對 BTC 的影響",
    "open_proposition": "分析 BTC 是否值得長期持有",
}

STANCE_RULE_BY_TYPE = {
    "single_asset_market_state": "- stance 只能是 bullish（偏多）、bearish（偏空）、neutral（方向不明）。",
    "two_asset_comparison": (
        "- stance 只能是 asset_a_stronger（BTC較優）、asset_b_stronger（ETH較優）、"
        "no_clear_difference（無明顯差異）。"
    ),
    "event_impact": (
        "- stance 只能是 positive（利多）、negative（利空）、"
        "unclear_or_conditional（不明或有條件）。"
    ),
    "open_proposition": (
        "- stance 只能是 affirmative（正方）、negative_side（反方）、undecided（無法決定）。"
    ),
}


class StanceVocabularyTest(DebateDriverTestCase):
    """Ticket T12a: schemas and prompts speak the drawn question type's words."""

    def _consensus_stances(self, stances):
        room = dict.fromkeys(SEAT_IDS[:6], stances[0])
        room["counter-evidence"] = stances[1]
        return room

    def _run_type(self, question_type):
        # 每一種題型都要在自己的封存時刻上重新開一場，比較題晚 30 秒。
        self.build_sealed_run(QUESTION_BY_TYPE[question_type])
        self.assertEqual(question_type, self.package.question_type)
        runner = self.build_runner(
            self._consensus_stances(stances_for(question_type)),
            wave_advance_ms={"opening": 30_000, "r1": 50_000},
        )
        return self.drive(runner) + (runner,)

    def test_every_schema_enum_comes_from_the_state_machine_mapping(self):
        for question_type in QUESTION_BY_TYPE:
            with self.subTest(question_type=question_type):
                package = build_question_package(QUESTION_BY_TYPE[question_type])
                stances = list(stances_for(question_type))
                turns = build_turns(stances)

                self.assertEqual(stances, list(package.stance_options))
                self.assertEqual(
                    stances, turns["opening"].schema["properties"]["stance"]["enum"]
                )
                self.assertEqual(
                    stances,
                    turns["r1"].schema["properties"]["stance"]["enum"],
                )
                self.assertEqual(
                    stances, turns["r2"].schema["properties"]["stance"]["enum"]
                )

    def test_every_prompt_lists_the_options_with_their_chinese_labels(self):
        for question_type, expected in STANCE_RULE_BY_TYPE.items():
            with self.subTest(question_type=question_type):
                # Each question type needs its own sealed run to debate in.
                _, votes, runner = self._run_type(question_type)

                self.assertEqual("consensus", votes["consensus_status"])
                self.assertTrue(runner.prompts)
                for slug, prompts in runner.prompts.items():
                    for prompt in prompts:
                        self.assertIn(expected, prompt, slug)
                        if question_type != "single_asset_market_state":
                            self.assertNotIn("bullish", prompt, slug)
                            self.assertNotIn("偏多", prompt, slug)

    def test_a_written_proposition_opens_the_opening_and_challenge_prompts(self):
        self.package = build_question_package(
            QUESTION_BY_TYPE["open_proposition"]
        ).with_proposition("BTC 在未來一年仍值得長期持有。")
        runner = self.build_runner(
            self._consensus_stances(stances_for("open_proposition")),
            wave_advance_ms={"opening": 30_000, "r1": 50_000},
        )

        self.drive(runner)

        block = "## 本場命題：BTC 在未來一年仍值得長期持有。（正方=正方，反方=反方）"
        for slug in ("opening", "r1"):
            for prompt in runner.prompts[slug]:
                self.assertIn(block, prompt, slug)
                self.assertLess(prompt.index(block), prompt.index("## 本回合"), slug)

    def test_a_question_without_a_proposition_shows_no_proposition_block(self):
        _, _, runner = self._run_type("single_asset_market_state")

        for prompts in runner.prompts.values():
            for prompt in prompts:
                self.assertNotIn("## 本場命題", prompt)


class DebateRoundTest(DebateDriverTestCase):
    def test_every_turn_prompt_demands_a_conclusion_as_the_first_sentence(self):
        """Ticket #T11: the live bubble shows only the first sentence."""
        runner = self.build_runner(BEARISH_FIVE_NEUTRAL_TWO)

        self.drive(runner)

        self.assertIn("opening", runner.prompts)
        self.assertIn("r1", runner.prompts)
        revote_slugs = [slug for slug in runner.prompts if slug not in ("opening", "r1")]
        self.assertTrue(revote_slugs, "本情境應該要進入改票回合")
        for slug, prompts in runner.prompts.items():
            for prompt in prompts:
                self.assertIn(CONCLUSION_FIRST_RULE, prompt, slug)

    def test_six_votes_in_the_first_round_stop_the_debate_immediately(self):
        runner = self.build_runner(
            BULLISH_SIX, wave_advance_ms={"opening": 30_000, "r1": 50_000}
        )

        driver, votes = self.drive(runner)

        self.assertEqual("consensus_6_votes", votes["stop_reason"])
        self.assertEqual("consensus", votes["consensus_status"])
        self.assertEqual("bullish", votes["adopted_stance"])
        self.assertEqual(6, votes["tally"]["bullish"])
        self.assertLess(votes["stop_elapsed_ms"], THRESHOLD_FIVE_FROM_MS)
        self.assertFalse(votes["challenge_completed"])
        self.assertEqual(7, votes["valid_vote_count"])
        self.assertEqual("counter-evidence", votes["dissent"][0]["seat_id"])
        self.assertEqual([], [note for note in driver.notes if note["seat_id"]])
        self.assertEqual(votes, self.votes_file())

    def test_a_free_turn_has_no_assigned_target_metadata(self):
        runner = self.build_runner(BULLISH_SIX)

        driver, votes = self.drive(runner)

        free_messages = [
            entry
            for entry in driver.machine.entries
            if entry.get("kind") == "final_vote"
        ]
        self.assertTrue(free_messages)
        for entry in free_messages:
            self.assertIsNone(entry["target_seat_id"])
            self.assertEqual([], entry["responds_to"])
        self.assertEqual("consensus", votes["consensus_status"])

    def test_five_votes_after_t7_stop_the_debate_at_the_lower_threshold(self):
        stances = dict.fromkeys(SEAT_IDS[:5], "bullish")
        stances.update({"social-macro": "bearish", "counter-evidence": "bearish"})
        runner = self.build_runner(
            stances, wave_advance_ms={"opening": 30_000, "r1": 50_000, "r2": 50_000}
        )

        _, votes = self.drive(runner)

        self.assertEqual("consensus_5_votes", votes["stop_reason"])
        self.assertEqual("bullish", votes["adopted_stance"])
        self.assertEqual(5, votes["threshold_required"])
        self.assertGreaterEqual(votes["stop_elapsed_ms"], THRESHOLD_FIVE_FROM_MS)
        self.assertLess(votes["stop_elapsed_ms"], FORCE_STOP_MS)

    def test_four_votes_are_adopted_at_the_fourth_ballot(self):
        stances = dict.fromkeys(SEAT_IDS[:4], "bullish")
        stances.update(
            {"news": "bearish", "social-macro": "bearish", "counter-evidence": "neutral"}
        )
        runner = self.build_runner(
            stances,
            wave_advance_ms={"opening": 30_000, "r1": 50_000, "r2": 60_000, "r3": 60_000},
        )

        _, votes = self.drive(runner)

        self.assertEqual("consensus_4_votes", votes["stop_reason"])
        self.assertEqual(self.seal_ms + 330_000, votes["stop_elapsed_ms"])
        self.assertEqual(4, votes["threshold_required"])
        self.assertEqual("bullish", votes["adopted_stance"])
        self.assertEqual(7, votes["valid_vote_count"])

    def test_a_three_two_two_split_ends_without_consensus(self):
        stances = dict.fromkeys(SEAT_IDS[:3], "bullish")
        stances.update(
            {
                "official-events": "bearish",
                "news": "bearish",
                "social-macro": "neutral",
                "counter-evidence": "neutral",
            }
        )
        runner = self.build_runner(
            stances,
            wave_advance_ms={"opening": 30_000, "r1": 50_000, "r2": 60_000, "r3": 60_000},
        )

        _, votes = self.drive(runner)

        self.assertEqual("forced_stop_no_consensus", votes["stop_reason"])
        self.assertEqual("no_consensus", votes["consensus_status"])
        self.assertIsNone(votes["adopted_stance"])
        self.assertFalse(votes["market_conclusion_allowed"])
        self.assertEqual({"bullish": 3, "bearish": 2, "neutral": 2}, votes["tally"])

    def test_a_late_poll_still_records_the_forced_stop_at_the_t10_rule(self):
        # 主迴圈只能每個 poll 才看到 T+10；停在 600_500ms 會讓這次強制停止
        # 對不上它自己執行的規則。
        stances = dict.fromkeys(SEAT_IDS[:3], "bullish")
        stances.update(
            {
                "official-events": "bearish",
                "news": "bearish",
                "social-macro": "neutral",
                "counter-evidence": "neutral",
            }
        )
        runner = self.build_runner(
            stances,
            wave_advance_ms={"opening": 30_000, "r1": 50_000, "r2": 60_000, "r3": 60_500},
        )

        _, votes = self.drive(runner)

        self.assertEqual(FORCE_STOP_MS, votes["stop_elapsed_ms"])
        self.assertEqual("forced_stop_no_consensus", votes["stop_reason"])

    def test_one_silent_seat_never_blocks_the_debate_from_finishing(self):
        runner = self.build_runner(
            BULLISH_SIX,
            wave_advance_ms={"opening": 30_000, "r1": 50_000, "r2": 60_000, "r3": 60_000},
            silent=(("news", "r1"),),
        )

        driver, votes = self.drive(runner)

        self.assertEqual("consensus", votes["consensus_status"])
        self.assertEqual(7, votes["valid_vote_count"])
        self.assertEqual("valid", _seat_row(votes, "news")["state"])
        self.assertEqual("bullish", _seat_row(votes, "news")["final_stance"])
        self.assertFalse(votes["challenge_completed"])
        self.assertIn("news-r1", runner.cancelled)
        self.assertIn(
            "deadline_missed",
            [note["reason"] for note in driver.notes if note["seat_id"] == "news"],
        )

    def test_a_published_opening_survives_a_missed_first_round(self):
        runner = self.build_runner(
            BULLISH_SIX,
            wave_advance_ms={"opening": 30_000, "r1": 50_000, "r2": 60_000, "r3": 60_000},
            silent=(("news", "r1"),),
        )

        driver, votes = self.drive(runner)

        # 1) 該席的原始 payload 如實出現在公開紀錄裡。
        published = [
            json.loads(line)
            for line in (self.run.path / "debate.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        position = next(
            entry
            for entry in published
            if entry.get("seat_id") == "news" and entry.get("kind") == "position"
        )
        self.assertEqual(0, position["round"])
        self.assertEqual("bullish", position["stance"])
        self.assertEqual("本席依據已引用的證據說明目前立場。", position["public_reason"])
        self.assertEqual(["news-01"], position["evidence_ids"])
        self.assertEqual(
            ["news-position"], [entry["message_id"] for entry in published
                                if entry.get("seat_id") == "news"]
        )

        # 2) 席位列說出真相，不再寫「未取得初始票」。
        row = _seat_row(votes, "news")
        self.assertEqual("bullish", row["initial_stance"])
        self.assertEqual("本席依據已引用的證據說明目前立場。", row["initial_public_reason"])
        report = assemble_market_report(
            narrative(),
            {"votes": votes, "evidence": self.evidence, "debate": published},
            generated_at_utc=CARD_STAMP,
            assets=self.package.assets,
            period_days=self.package.period_days,
        )
        report_row = next(
            seat for seat in report["seats"] if seat["seat_id"] == "news"
        )
        self.assertEqual("bullish", report_row["initial_stance"])
        self.assertNotEqual("未取得初始票。", report_row["initial_public_reason"])
        self.assertEqual("bullish", report_row["final_stance"])

        # 3) 沒有自由辯論回覆時，opening 仍是開票牆上的最新公開立場。
        self.assertIn("news", driver.machine.valid_votes())
        self.assertEqual("provisional", driver.machine.seats["news"].state)
        self.assertFalse(votes["challenge_completed"])

        diagnostics = json.loads(
            (self.run.path / "diagnostics" / "debate-driver.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("news", diagnostics["seats_in_public_record"])
        self.assertNotIn("news", diagnostics["completed_free_debate_turns"]["r1"])
        self.assertIn(
            "deadline_missed",
            [note["reason"] for note in driver.notes if note["seat_id"] == "news"],
        )

    def test_a_room_that_agrees_on_one_stance_reaches_consensus(self):
        """Ticket R8 → Ticket 03：全場一致仍是最強共識，現在直接盲投直過收工。"""
        runner = self.build_runner(
            UNANIMOUS_SEVEN,
            wave_advance_ms={"opening": 30_000, "r1": 50_000, "r2": 60_000, "r3": 60_000},
        )

        driver, votes = self.drive(runner)

        self.assertEqual("unanimous_blind_pass", votes["stop_reason"])
        self.assertEqual("consensus", votes["consensus_status"])
        self.assertEqual("bullish", votes["adopted_stance"])
        self.assertEqual(7, votes["valid_vote_count"])
        self.assertFalse(votes["red_no_conclusion"])
        self.assertTrue(votes["market_conclusion_allowed"])
        # 七席都公開講過話，紀錄必須留著；開場原文就是每一席的最終票。
        self.assertEqual(["bullish"] * 7, [row["initial_stance"] for row in votes["votes"]])
        self.assertEqual(["bullish"] * 7, [row["final_stance"] for row in votes["votes"]])
        self.assertEqual({}, driver.completed_turns)
        self.assertEqual(list(SEAT_IDS), list(driver.published))
        self.assertEqual(votes, self.votes_file())

    def test_a_room_that_agrees_on_one_stance_still_finalizes(self):
        runner = self.build_runner(
            UNANIMOUS_SEVEN,
            wave_advance_ms={"opening": 30_000, "r1": 50_000, "r2": 60_000, "r3": 60_000},
        )

        handshake = self.finish(runner)

        self.assertEqual("FINALIZED", handshake["status"])
        self.assertEqual("consensus", handshake["consensus_status"])
        self.assertEqual("bullish", handshake["adopted_stance"])
        self.assertEqual("unanimous_blind_pass", handshake["stop_reason"])
        self.assertEqual(7, handshake["valid_vote_count"])
        self.assertEqual("accepted", handshake["report_status"])

    def test_each_reply_reaches_events_before_the_next_one_arrives(self):
        # 直播頁只讀 events.jsonl：一則講完就要看得到，不能整波結束才一次倒出來。
        self.sleeper = DripSleeper(self.clock, self.run)
        runner = DrippingDebateRunner(
            self.results,
            self.clock,
            BULLISH_SIX,
            wave_advance_ms={"r1": 50_000},
            core_adapter=ScriptedCoreAdapter(self.clock, [narrative()]),
        )
        self.sleeper.runner = runner

        driver, votes = self.drive(runner)

        # 第 n 次輪詢釋出第 n 則回覆，所以每一次輪詢看到的都是前 n-1 則。
        self.assertEqual([], self.sleeper.snapshots[0])
        self.assertEqual([SEAT_IDS[0]], self.sleeper.snapshots[1])
        self.assertEqual(list(SEAT_IDS[:2]), self.sleeper.snapshots[2])
        self.assertEqual(list(SEAT_IDS), position_seats(self.run))
        self.assertEqual(list(SEAT_IDS), list(driver.published))
        self.assertEqual("consensus", votes["consensus_status"])

    def test_a_room_with_a_single_published_position_still_finishes(self):
        runner = self.build_runner(
            UNANIMOUS_SEVEN,
            wave_advance_ms={"opening": 30_000, "r2": 60_000, "r3": 60_000},
            silent=[(seat_id, "opening") for seat_id in SEAT_IDS[1:]],
        )

        driver, votes = self.drive(runner)

        expected = ["{}-opening".format(seat_id) for seat_id in SEAT_IDS]
        expected += ["{}-r{}".format(SEAT_IDS[0], index) for index in (1, 2, 3)]
        self.assertEqual(expected, runner.started)
        self.assertEqual([SEAT_IDS[0]], list(driver.published))
        self.assertEqual(
            {"r1": [SEAT_IDS[0]], "r2": [SEAT_IDS[0]], "r3": [SEAT_IDS[0]]},
            driver.completed_turns,
        )
        self.assertEqual("failed_insufficient_valid_votes", votes["consensus_status"])

    def test_a_failed_dispatch_is_recorded_instead_of_being_swallowed(self):
        runner = self.build_runner(
            BULLISH_SIX,
            failures={("news", "opening"): ("timeout", "claude 超時")},
        )

        driver, votes = self.drive(runner)

        self.assertEqual("missing", _seat_row(votes, "news")["state"])
        self.assertIn(
            "timeout:claude 超時",
            [note["reason"] for note in driver.notes if note["seat_id"] == "news"],
        )
        diagnostics = json.loads(
            (self.run.path / "diagnostics" / "debate-driver.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("news", diagnostics["seats_in_public_record"])
        self.assertEqual(
            "scripted-spot-technical",
            diagnostics["provider_lineage"]["spot-technical"]["actual_model"],
        )


class FirstRoundBudgetTest(DebateDriverTestCase):
    """Each free turn keeps five seconds for relay and wall-time tallying."""

    def test_openings_spread_across_the_window_still_get_a_first_round(self):
        latency_ms = {
            (seat_id, "opening"): value
            for seat_id, value in REAL_OPENING_LATENCY_MS.items()
        }
        latency_ms.update(
            {(seat_id, "r1"): 30_000 for seat_id in SEAT_IDS}
        )
        runner = self.build_latency_runner(BEARISH_FIVE_NEUTRAL_TWO, latency_ms)

        driver, votes = self.drive(runner)

        first_round = [item for item in runner.started if item.endswith("-r1")]
        free_votes = [
            entry
            for entry in driver.machine.entries
            if entry.get("kind") == "final_vote" and entry.get("round") == 1
        ]
        self.assertTrue(first_round, "第一輪未過後必須派發自由辯論")
        self.assertTrue(free_votes, "自由辯論回覆必須原文進公開紀錄")
        self.assertGreaterEqual(votes["valid_vote_count"], 4)
        self.assertNotEqual(
            "forced_stop_insufficient_valid_votes", votes["stop_reason"]
        )

    def test_a_bearish_and_neutral_room_calls_every_published_seat(self):
        runner = self.build_runner(
            BEARISH_FIVE_NEUTRAL_TWO,
            wave_advance_ms={"opening": 30_000, "r1": 50_000},
        )

        driver, votes = self.drive(runner)

        self.assertEqual(
            ["{}-r1".format(seat_id) for seat_id in SEAT_IDS],
            [item for item in runner.started if item.endswith("-r1")],
        )
        self.assertEqual(list(SEAT_IDS), driver.completed_turns["r1"])
        self.assertFalse(
            any(
                entry.get("kind") in ("challenge", "response")
                for entry in driver.machine.entries
            )
        )
        self.assertEqual(7, votes["valid_vote_count"])

    def test_two_stances_in_the_room_always_dispatch_the_first_round(self):
        # 只要公開紀錄裡出現兩種以上立場，第一輪一定派發；跳過只保留給真一致。
        rooms = (
            BEARISH_FIVE_NEUTRAL_TWO,
            BULLISH_SIX,
            dict(dict.fromkeys(SEAT_IDS, "neutral"), **{"news": "bearish"}),
        )
        for stances in rooms:
            with self.subTest(stances=sorted(set(stances.values()))):
                self.setUp()
                runner = self.build_runner(
                    stances, wave_advance_ms={"opening": 30_000, "r1": 50_000}
                )

                driver, _ = self.drive(runner)

                self.assertEqual(
                    ["{}-r1".format(seat_id) for seat_id in driver.published],
                    [item for item in runner.started if item.endswith("-r1")],
                )
                for prompt in runner.prompts["r1"]:
                    self.assertIn(PERSUASION_GOAL, prompt)


class UnanimousRoomScrutinyTest(DebateDriverTestCase):
    """Six matching openings still use the same free-debate path."""

    def latencies(self):
        """實測開場延遲，配上落在第一輪牆之內的第一輪回覆。"""
        latency_ms = {
            (seat_id, "opening"): value
            for seat_id, value in REAL_OPENING_LATENCY_MS.items()
        }
        latency_ms.update(
            {
                (seat_id, "r1"): min(value, 50_000)
                for seat_id, value in REAL_FIRST_ROUND_LATENCY_MS.items()
            }
        )
        # 這一席的開場超出 driver 自己交出去的預算，provider 回超時，房間因此
        # 停在六席一致，直過不成立。
        latency_ms[(SCRUTINY_SILENT_SEAT, "opening")] = 10_000_000
        return latency_ms

    def test_matching_openings_run_free_debate_and_reach_their_first_threshold(self):
        runner = self.build_latency_runner(UNANIMOUS_SEVEN, self.latencies())

        driver, votes = self.drive(runner)

        self.assertEqual(
            set(driver.published),
            {
                item[: -len("-r1")]
                for item in runner.started
                if item.endswith("-r1")
            },
        )
        self.assertEqual(set(driver.published), set(driver.completed_turns["r1"]))
        self.assertEqual("consensus_5_votes", votes["stop_reason"])
        self.assertEqual("consensus", votes["consensus_status"])
        self.assertEqual("bullish", votes["adopted_stance"])
        self.assertEqual(5, votes["tally"]["bullish"])
        self.assertFalse(votes["challenge_completed"])
        self.assertEqual(self.seal_ms + 240_000, votes["stop_elapsed_ms"])

    def test_free_debate_creates_no_challenge_audit_records(self):
        runner = self.build_latency_runner(UNANIMOUS_SEVEN, self.latencies())

        driver, _ = self.drive(runner)

        self.assertFalse(
            any(
                entry.get("kind") in ("challenge", "response")
                for entry in driver.machine.entries
            )
        )

    def test_matching_room_prompt_uses_free_persuasion_wording(self):
        runner = self.build_runner(
            UNANIMOUS_SEVEN,
            wave_advance_ms={"opening": 30_000, "r1": 50_000},
            silent=[(SCRUTINY_SILENT_SEAT, "opening")],
        )

        self.drive(runner)

        self.assertEqual(len(SCRUTINY_SPEAKING_SEATS), len(runner.prompts["r1"]))
        for prompt in runner.prompts["r1"]:
            for wording in (PERSUASION_GOAL, "不盲從", "不死守"):
                self.assertIn(wording, prompt)
            self.assertNotIn("你必須挑戰", prompt)

    def test_mixed_room_uses_the_same_free_persuasion_wording(self):
        runner = self.build_runner(
            BEARISH_FIVE_NEUTRAL_TWO,
            wave_advance_ms={"opening": 30_000, "r1": 50_000},
        )

        driver, votes = self.drive(runner)

        for prompt in runner.prompts["r1"]:
            self.assertIn(PERSUASION_GOAL, prompt)
            self.assertNotIn("主席排定", prompt)
        self.assertFalse(
            any(
                entry.get("kind") in ("challenge", "response")
                for entry in driver.machine.entries
            )
        )
        self.assertEqual(7, votes["valid_vote_count"])


class PersuasionPromptTest(DebateDriverTestCase):
    """Ticket 03：辯論輪的 prompt 必須說明白目的是用證據把票拉過來。

    句子取自 Ticket 原文與 architecture §11.3「prompt 層強化『說服對方拉票』語
    意」，不是從實作抄回來的。
    """

    def test_every_debate_round_prompt_states_the_persuasion_goal(self):
        room = dict.fromkeys(SEAT_IDS[:3], "bullish")
        room.update(dict.fromkeys(SEAT_IDS[3:5], "bearish"))
        room.update(dict.fromkeys(SEAT_IDS[5:], "neutral"))
        runner = self.build_runner(room)

        self.drive(runner)

        debate_slugs = sorted(slug for slug in runner.prompts if slug != "opening")
        self.assertEqual(["r1", "r2", "r3"], debate_slugs)
        for slug in debate_slugs:
            for prompt in runner.prompts[slug]:
                self.assertIn(PERSUASION_GOAL, prompt, slug)

    def test_the_blind_opening_prompt_carries_no_persuasion_goal(self):
        # 開場是互不可見的盲投：這一刻場上還沒有任何立場可以被說服。
        room = dict.fromkeys(SEAT_IDS[:3], "bullish")
        room.update(dict.fromkeys(SEAT_IDS[3:5], "bearish"))
        room.update(dict.fromkeys(SEAT_IDS[5:], "neutral"))
        runner = self.build_runner(room)

        self.drive(runner)

        self.assertEqual(len(SEAT_IDS), len(runner.prompts["opening"]))
        for prompt in runner.prompts["opening"]:
            self.assertNotIn(PERSUASION_GOAL, prompt)

    def test_the_goal_names_the_threshold_this_round_s_votes_will_face(self):
        """Each turn names the threshold at its next discrete ballot."""
        room = dict.fromkeys(SEAT_IDS[:3], "bullish")
        room.update(dict.fromkeys(SEAT_IDS[3:5], "bearish"))
        room.update(dict.fromkeys(SEAT_IDS[5:], "neutral"))
        runner = self.build_runner(room)

        self.drive(runner)

        expected = {
            "r1": RULES.vote_rounds[1].threshold,
            "r2": RULES.vote_rounds[2].threshold,
            "r3": RULES.vote_rounds[3].threshold,
        }
        self.assertEqual([6, 5, 4], list(expected.values()))
        for slug, votes in expected.items():
            self.assertIn(slug, runner.prompts)
            for prompt in runner.prompts[slug]:
                self.assertIn(
                    "目標是達到下一輪門檻（{} 票）".format(votes), prompt, slug
                )

    def test_a_late_revote_wave_names_the_threshold_it_is_dispatched_under(self):
        """A slow wave cannot change the threshold of its next fixed wall."""
        runner = self.build_runner(
            BEARISH_FIVE_NEUTRAL_TWO,
            wave_advance_ms={"opening": 30_000, "r1": 50_000, "r2": 170_000},
        )

        self.drive(runner)

        for prompt in runner.prompts["r2"]:
            self.assertIn("目標是達到下一輪門檻（5 票）", prompt)

    def test_a_unanimous_scrutiny_round_still_states_the_same_goal(self):
        # 全場一致時沒有對立席位，但那一輪仍然是為了移動票數而存在。
        runner = self.build_runner(
            UNANIMOUS_SEVEN,
            wave_advance_ms={"opening": 30_000, "r1": 50_000},
            silent=[(SCRUTINY_SILENT_SEAT, "opening")],
        )

        self.drive(runner)

        self.assertEqual(len(SCRUTINY_SPEAKING_SEATS), len(runner.prompts["r1"]))
        for prompt in runner.prompts["r1"]:
            self.assertIn(PERSUASION_GOAL, prompt)


class UnanimousBlindPassDriverTest(DebateDriverTestCase):
    """Ticket 03：七席盲投同立場 → 不派任何辯論輪，直接進報告流程。

    architecture §11.3：opening 是互不可見的盲投，七席各自獨立得出同一結論即
    直接停止。魔鬼代言人輪只在「還有席位沒發言」的一致房間才跑得到（見
    UnanimousRoomScrutinyTest）。
    """

    def test_a_unanimous_blind_opening_skips_every_debate_round(self):
        runner = self.build_runner(
            UNANIMOUS_SEVEN, wave_advance_ms={"opening": 30_000}
        )

        driver, votes = self.drive(runner)

        # 只派過開場那一波；r1/r2/r3 一次都沒有離開過這台機器。
        self.assertEqual(
            ["{}-opening".format(seat_id) for seat_id in SEAT_IDS], runner.started
        )
        self.assertEqual("unanimous_blind_pass", votes["stop_reason"])
        self.assertEqual("consensus", votes["consensus_status"])
        self.assertEqual("bullish", votes["adopted_stance"])
        self.assertEqual(7, votes["valid_vote_count"])
        self.assertEqual(7, votes["threshold_required"])
        self.assertEqual(votes, self.votes_file())

    def test_the_public_record_holds_seven_openings_and_nothing_else(self):
        runner = self.build_runner(
            UNANIMOUS_SEVEN, wave_advance_ms={"opening": 30_000}
        )

        driver, _ = self.drive(runner)

        seat_messages = [
            entry
            for entry in driver.machine.entries
            if entry.get("event") == "seat_message"
        ]
        self.assertEqual({"position"}, {entry["kind"] for entry in seat_messages})
        self.assertEqual(len(SEAT_IDS), len(seat_messages))
        self.assertEqual(list(SEAT_IDS), list(driver.published))
        self.assertEqual({}, driver.completed_turns)

    def test_the_run_stops_at_the_last_opening_not_at_the_forced_stop(self):
        # 直過的價值就是「最快被辨識」：時間不能拖到 T+10 才結算。
        runner = self.build_runner(
            UNANIMOUS_SEVEN, wave_advance_ms={"opening": 30_000}
        )

        _, votes = self.drive(runner)

        self.assertEqual(SEAL_MS + 30_000, votes["stop_elapsed_ms"])
        self.assertLess(votes["stop_elapsed_ms"], CHALLENGE_DEADLINE_MS)

    def test_a_configured_six_vote_first_round_is_not_a_blind_pass(self):
        rounds = list(RULES.vote_rounds)
        rounds[0] = VoteRound(rounds[0].open_offset_ms, 6)
        rules = replace(RULES, vote_rounds=tuple(rounds))
        runner = self.build_runner(
            BULLISH_SIX, wave_advance_ms={"opening": 30_000}
        )

        driver, votes = self.drive(runner, rules=rules)

        self.assertEqual(
            ["{}-opening".format(seat_id) for seat_id in SEAT_IDS], runner.started
        )
        self.assertEqual([], [item for item in runner.started if item.endswith("-r1")])
        self.assertEqual("consensus_6_votes", votes["stop_reason"])
        self.assertEqual(6, votes["threshold_required"])
        self.assertEqual("bullish", votes["adopted_stance"])
        self.assertEqual(7, votes["valid_vote_count"])
        self.assertEqual({"bullish": 6, "bearish": 1, "neutral": 0}, votes["tally"])
        # 反方那一席仍是有效票，也仍留在異議名單裡。
        self.assertEqual(
            ["counter-evidence"], [item["seat_id"] for item in votes["dissent"]]
        )
        self.assertEqual({}, driver.completed_turns)

    def test_a_six_one_blind_opening_still_runs_the_whole_debate(self):
        """驗收條件二：6/1 盲投照常進辯論，行為與現制一致。"""
        runner = self.build_runner(
            BULLISH_SIX, wave_advance_ms={"opening": 30_000, "r1": 50_000}
        )

        driver, votes = self.drive(runner)

        self.assertEqual(
            ["{}-r1".format(seat_id) for seat_id in SEAT_IDS],
            [item for item in runner.started if item.endswith("-r1")],
        )
        self.assertEqual("consensus_6_votes", votes["stop_reason"])
        self.assertEqual(list(SEAT_IDS), driver.completed_turns["r1"])
        self.assertFalse(
            any(entry.get("kind") in ("challenge", "response") for entry in driver.machine.entries)
        )


class RevisedScheduleTimingTest(DebateDriverTestCase):
    """核心驗收：2026-08-02 核准的時間表，配上實測的 provider 延遲。

    開場 codex/antigravity 15-20 秒、claude 50-60 秒，第一輪回覆 30-60 秒。
    整場只有一個問題值得問：七席有沒有都拿到有效票，而且快席有沒有被慢席拖住。
    """

    def latencies(self, first_round_ms=None):
        latency_ms = {
            (seat_id, "opening"): value
            for seat_id, value in REAL_OPENING_LATENCY_MS.items()
        }
        first_round_ms = first_round_ms or REAL_FIRST_ROUND_LATENCY_MS
        latency_ms.update(
            {(seat_id, "r1"): value for seat_id, value in first_round_ms.items()}
        )
        return latency_ms

    def test_openings_inside_the_first_wall_become_valid_without_a_turn_reply(self):
        runner = self.build_latency_runner(
            BEARISH_FIVE_NEUTRAL_TWO, self.latencies()
        )

        driver, votes = self.drive(runner)

        self.assertEqual(6, votes["valid_vote_count"])
        self.assertFalse(votes["challenge_completed"])
        self.assertEqual("missing", _seat_row(votes, "social-macro")["state"])
        reasons = [
            note["reason"]
            for note in driver.notes
            if note["seat_id"] == "social-macro"
        ]
        self.assertTrue(
            any("timeout" in reason or reason == "deadline_missed" for reason in reasons)
        )

    def test_free_debate_starts_only_after_the_first_ballot_wall(self):
        runner = self.build_latency_runner(
            BEARISH_FIVE_NEUTRAL_TWO, self.latencies()
        )

        self.drive(runner)

        first_wall = self.seal_ms + RULES.vote_rounds[0].open_offset_ms
        for dispatch_id, dispatched_at in runner.dispatched_at_ms.items():
            if dispatch_id.endswith("-r1"):
                self.assertGreaterEqual(dispatched_at, first_wall)

    def test_six_votes_stop_at_the_second_ballot_wall(self):
        latency_ms = self.latencies()
        latency_ms[("social-macro", "opening")] = 50_000
        runner = self.build_latency_runner(BULLISH_SIX, latency_ms)

        _, votes = self.drive(runner)

        self.assertEqual("consensus_6_votes", votes["stop_reason"])
        self.assertEqual(6, votes["tally"]["bullish"])
        self.assertEqual(self.seal_ms + 150_000, votes["stop_elapsed_ms"])

    def test_an_opening_past_the_first_wall_is_excluded(self):
        latency_ms = self.latencies()
        latency_ms[("social-macro", "opening")] = 100_000
        latency_ms[("social-macro", "r1")] = 60_000
        runner = self.build_latency_runner(BEARISH_FIVE_NEUTRAL_TWO, latency_ms)

        driver, votes = self.drive(runner)

        self.assertEqual("missing", _seat_row(votes, "social-macro")["state"])
        self.assertEqual(6, votes["valid_vote_count"])

    def test_a_late_opening_never_unlocks_a_later_turn(self):
        latency_ms = self.latencies()
        latency_ms[("social-macro", "opening")] = 100_000
        latency_ms[("social-macro", "r1")] = 120_000
        runner = self.build_latency_runner(BEARISH_FIVE_NEUTRAL_TWO, latency_ms)

        driver, votes = self.drive(runner)

        self.assertEqual("missing", _seat_row(votes, "social-macro")["state"])
        self.assertEqual(6, votes["valid_vote_count"])
        self.assertNotIn("social-macro-r1", runner.started)

    def test_the_collection_budgets_stay_inside_their_own_walls(self):
        turns = build_turns(("bullish", "bearish", "neutral"))

        self.assertEqual(
            DEBATE_START_MS + 60_000 - 5_000, turns["opening"].collect_until_ms
        )
        self.assertEqual(DEBATE_START_MS + 150_000 - 5_000, turns["r1"].collect_until_ms)
        self.assertEqual(DEBATE_START_MS + 240_000 - 5_000, turns["r2"].collect_until_ms)
        self.assertEqual(DEBATE_START_MS + 330_000 - 5_000, turns["r3"].collect_until_ms)
        self.assertEqual(DEBATE_START_MS, turns["opening"].relay_from_ms)
        self.assertEqual(DEBATE_START_MS + 60_000, turns["r1"].relay_from_ms)
        self.assertEqual(DEBATE_START_MS + 240_000, turns["r3"].relay_from_ms)

    def test_a_later_seal_moves_the_first_round_wall_with_it(self):
        """Every ballot and turn moves with a later comparison seal."""
        turns = build_turns(
            ("asset_a_stronger", "asset_b_stronger", "no_clear_difference"),
            COMPARISON_SEAL_MS,
        )

        self.assertEqual(COMPARISON_SEAL_MS, turns["opening"].relay_from_ms)
        self.assertEqual(COMPARISON_SEAL_MS + 60_000, turns["r1"].relay_from_ms)
        self.assertEqual(COMPARISON_SEAL_MS + 55_000, turns["opening"].collect_until_ms)
        self.assertEqual(
            COMPARISON_SEAL_MS + 145_000,
            turns["r1"].collect_until_ms,
        )
        self.assertEqual(COMPARISON_SEAL_MS + 150_000, turns["r2"].relay_from_ms)
        self.assertEqual(COMPARISON_SEAL_MS + 235_000, turns["r2"].collect_until_ms)
        self.assertEqual(COMPARISON_SEAL_MS + 325_000, turns["r3"].collect_until_ms)


class TurnWindowAuthorityTest(unittest.TestCase):
    """回合視窗只能有一份定義，而且它在 DebateStateMachine。"""

    def test_a_turn_carries_no_second_copy_of_the_round_window(self):
        turns = build_turns(("bullish", "bearish", "neutral"))

        for slug, turn in turns.items():
            names = {field.name for field in fields(turn)}
            self.assertNotIn("relay_until_ms", names, slug)


class DriverFreeDebateTurnsTest(DebateDriverTestCase):
    """Ticket 03: round-array turns replace assigned challenge bundles."""

    def test_build_turns_follows_any_round_array_and_each_next_wall(self):
        rules = replace(
            RULES,
            vote_rounds=(
                VoteRound(10_000, 3),
                VoteRound(20_000, 2),
                VoteRound(30_000, 1),
            ),
            final_settle_offset_ms=35_000,
        )

        turns = build_turns(("yes", "no", "undecided"), 40_000, rules)

        self.assertEqual(["opening", "r1", "r2"], list(turns))
        self.assertEqual(
            (45_000, 40_000),
            (turns["opening"].collect_until_ms, turns["opening"].relay_from_ms),
        )
        self.assertEqual(
            (55_000, 50_000),
            (turns["r1"].collect_until_ms, turns["r1"].relay_from_ms),
        )
        self.assertEqual(
            (65_000, 60_000),
            (turns["r2"].collect_until_ms, turns["r2"].relay_from_ms),
        )
        for turn in turns.values():
            self.assertNotIn("challenges", turn.schema["properties"])
            self.assertNotIn("response", turn.schema["properties"])
            self.assertNotIn("final_vote", turn.schema["properties"])

    def test_failed_first_ballot_dispatches_free_debate_and_records_vote_change(self):
        stances = dict.fromkeys(SEAT_IDS[:5], "bullish")
        stances.update(dict.fromkeys(SEAT_IDS[5:], "bearish"))
        changed_seat = SEAT_IDS[5]
        runner = self.build_runner(
            stances,
            revotes={(changed_seat, "r1"): "bullish"},
        )

        _, votes = self.drive(runner)

        self.assertEqual("consensus_6_votes", votes["stop_reason"])
        self.assertEqual(self.seal_ms + 150_000, votes["stop_elapsed_ms"])
        entries = [
            json.loads(line)
            for line in (self.run.path / "debate.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        seat_messages = [
            entry for entry in entries if entry.get("event") == "seat_message"
        ]
        self.assertFalse(
            any(entry.get("kind") in ("challenge", "response") for entry in seat_messages)
        )
        changed = next(row for row in votes["votes"] if row["seat_id"] == changed_seat)
        self.assertEqual("反方證據改變本席判斷。", changed["stance_change_reason"])
        self.assertEqual(
            "反方證據改變本席判斷。", changed["vote_changes"][-1]["reason"]
        )
        free_prompt = runner.prompts["r1"][0]
        for wording in ("用證據說服對方", "不盲從", "不死守"):
            self.assertIn(wording, free_prompt)
        for retired in ("你必須挑戰", "主席排定", "魔鬼代言人"):
            self.assertNotIn(retired, free_prompt)
        diagnostics = json.loads(
            (self.run.path / "diagnostics/debate-driver.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("challenge_assignment", diagnostics)


class EvidenceVisibilityGateTest(DebateDriverTestCase):
    """Ticket 04: prompt visibility changes only after the first ballot fails."""

    @staticmethod
    def _evidence_block(prompt):
        start = prompt.index("## 共享證據快照")
        end = prompt.index("## 共享辯論快照", start)
        return prompt[start:end]

    def test_opening_prompt_contains_only_its_own_evidence_id_and_content(self):
        runner = self.build_runner(BULLISH_SIX)

        self.drive(runner)

        self.assertEqual(len(SEAT_IDS), len(runner.prompts["opening"]))
        for seat_id, prompt in zip(SEAT_IDS, runner.prompts["opening"]):
            self.assertIn("{}-01".format(seat_id), prompt)
            self.assertIn(
                "https://example.invalid/{}/1".format(seat_id), prompt
            )
            for other_seat_id in SEAT_IDS:
                if other_seat_id == seat_id:
                    continue
                self.assertNotIn("{}-01".format(other_seat_id), prompt)
                self.assertNotIn(
                    "https://example.invalid/{}/1".format(other_seat_id), prompt
                )

    def test_every_free_turn_restores_the_full_snapshot_and_public_history(self):
        room = dict.fromkeys(SEAT_IDS[:3], "bullish")
        room.update(dict.fromkeys(SEAT_IDS[3:5], "bearish"))
        room.update(dict.fromkeys(SEAT_IDS[5:], "neutral"))
        runner = self.build_runner(room)

        self.drive(runner)

        prior_message_ids = ["{}-position".format(seat_id) for seat_id in SEAT_IDS]
        for round_number, slug in enumerate(("r1", "r2", "r3"), start=1):
            prompts = runner.prompts[slug]
            self.assertEqual(1, len({self._evidence_block(prompt) for prompt in prompts}))
            for prompt in prompts:
                for seat_id in SEAT_IDS:
                    self.assertIn("{}-01".format(seat_id), prompt)
                for message_id in prior_message_ids:
                    self.assertIn(message_id, prompt)
            prior_message_ids.extend(
                "{}-r{}-vote".format(seat_id, round_number)
                for seat_id in SEAT_IDS
            )

    def test_unanimous_blind_pass_never_exposes_another_seats_evidence(self):
        runner = self.build_runner(UNANIMOUS_SEVEN)

        _, votes = self.drive(runner)

        self.assertEqual("unanimous_blind_pass", votes["stop_reason"])
        self.assertEqual({"opening"}, set(runner.prompts))
        for seat_id, prompt in zip(SEAT_IDS, runner.prompts["opening"]):
            self.assertIn("{}-01".format(seat_id), prompt)
            for other_seat_id in SEAT_IDS:
                if other_seat_id != seat_id:
                    self.assertNotIn("{}-01".format(other_seat_id), prompt)

    def test_visibility_gate_does_not_change_the_sealed_artifact_contract(self):
        evidence_path = self.run.path / "evidence.jsonl"
        before_bytes = evidence_path.read_bytes()
        before_entry = dict(self.run.artifact_index()["evidence.jsonl"])
        expected_fields = {
            "schema_version",
            "evidence_id",
            "run_id",
            "seat_id",
            "attempt_id",
            "phase",
            "created_at_utc",
            "elapsed_ms",
            "asset",
            "category",
            "statement",
            "direction",
            "source_url",
            "source_origin",
            "source_tier",
            "published_at_utc",
            "retrieved_at_utc",
            "excerpt",
            "credibility_note",
        }
        runner = self.build_runner(BULLISH_SIX)

        self.finish(runner)

        manifest = json.loads(
            (self.run.path / "manifest.json").read_text(encoding="utf-8")
        )
        records = [
            json.loads(line)
            for line in evidence_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(before_bytes, evidence_path.read_bytes())
        self.assertEqual(before_entry, manifest["artifacts"]["evidence.jsonl"])
        self.assertTrue(records)
        self.assertTrue(all(set(record) == expected_fields for record in records))


class CoreNarrativeLightSetTest(unittest.TestCase):
    """Core 的輸出 schema 與撰稿驗證必須用 report_contract 那一份燈號集合。"""

    def test_the_output_schema_offers_exactly_the_approved_lights(self):
        self.assertEqual(
            list(CONFIDENCE_LEVELS),
            CORE_REPORT_SCHEMA["properties"]["confidence_level"]["enum"],
        )

    def test_every_approved_light_is_accepted_by_the_narrative_check(self):
        for level in CONFIDENCE_LEVELS:
            with self.subTest(level=level):
                self.assertIsNotNone(
                    validate_core_narrative(narrative(confidence_level=level))
                )

    def test_a_light_outside_the_approved_set_is_refused(self):
        for level in ("yellow_green", "grene", "", None):
            with self.subTest(level=repr(level)):
                with self.assertRaises(ValueError):
                    validate_core_narrative(narrative(confidence_level=level))


class ReportAndFinalizeTest(DebateDriverTestCase):
    def test_finalize_writes_a_verifiable_fast_path_manifest(self):
        runner = self.build_runner(
            BULLISH_SIX, wave_advance_ms={"opening": 30_000, "r1": 50_000}
        )

        handshake = self.finish(runner)

        manifest = json.loads(
            (self.run.path / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual("FINALIZED", handshake["status"])
        self.assertEqual("accepted", handshake["report_status"])
        self.assertEqual(str(self.run.path / "report.html"), handshake["report_html"])
        self.assertEqual("consensus", handshake["consensus_status"])
        self.assertEqual("real-subscription-fast", manifest["provider_mode"])
        self.assertFalse(manifest["competition_ready"])
        timeline = manifest["competition_timeline"]
        self.assertEqual(0, timeline["all_seats_dispatched_at_ms"])
        self.assertEqual(SEAL_MS, timeline["evidence_snapshot_sealed_at_ms"])
        self.assertEqual(7, len(timeline["seat_completion_ms"]))
        self.assertLess(timeline["report_completed_at_ms"], 780_000)
        self.assertEqual(manifest["elapsed_ms"], timeline["report_completed_at_ms"])
        self.assertEqual(
            PREFLIGHT_ID,
            manifest["provider_lineage_fast"]["ready_certificate"]["system_preflight_id"],
        )
        self.assertEqual(
            ["scripted-{}".format(seat_id) for seat_id in SEAT_IDS],
            [
                seat["actual_model"]
                for seat in manifest["provider_lineage_fast"]["seats"]
            ],
        )
        latest = json.loads(
            (self.data_root / "runs" / "latest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(self.run_id, latest["run_id"])

    def test_an_unfixable_core_report_publishes_the_red_audit_version(self):
        runner = self.build_runner(
            BULLISH_SIX, wave_advance_ms={"opening": 30_000, "r1": 50_000}
        )

        handshake = self.finish(
            runner, core_narratives=[narrative(confidence_level="blue")] * 2
        )

        report = json.loads((self.run.path / "report.json").read_text(encoding="utf-8"))
        self.assertEqual("red_audit", handshake["report_status"])
        self.assertTrue(report["process_failure"])
        self.assertEqual("validation_failed", report["consensus_status"])
        self.assertIsNone(report["adopted_stance"])
        self.assertFalse(report["direction_bearing"])
        self.assertEqual("red", report["confidence"]["level"])
        self.assertTrue(report["validation_errors"])
        for name in ("report.md", "report.html", "debate.html"):
            self.assertTrue((self.run.path / name).is_file(), name)
        self.assertEqual(2, len(runner.core_adapter.calls))
        self.assertIn("上一稿的精確驗證錯誤", runner.core_adapter.calls[1])

    def test_every_rejected_core_draft_is_kept_verbatim_next_to_its_problems(self):
        # 紅牌只留錯誤訊息無從稽核：被拒的原稿本身必須留檔。
        runner = self.build_runner(
            BULLISH_SIX, wave_advance_ms={"opening": 30_000, "r1": 50_000}
        )

        handshake = self.finish(
            runner, core_narratives=[narrative(confidence_level="blue")] * 2
        )

        attempts = json.loads(
            (self.run.path / "diagnostics" / "report-attempts.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("red_audit", handshake["report_status"])
        self.assertEqual([1, 2], [item["attempt"] for item in attempts])
        for item in attempts:
            self.assertTrue(item["problems"], item)
            self.assertTrue(item["submitted_at_utc"].endswith("Z"), item)
            self.assertEqual("blue", item["draft"]["confidence"]["level"])
            self.assertEqual(NARRATIVE["judgement"], item["draft"]["judgement"])

    def test_an_accepted_core_draft_leaves_no_rejected_draft_behind(self):
        runner = self.build_runner(
            BULLISH_SIX, wave_advance_ms={"opening": 30_000, "r1": 50_000}
        )

        self.finish(runner)

        self.assertFalse(
            (self.run.path / "diagnostics" / "report-attempts.json").exists()
        )

    def test_the_core_prompt_carries_the_official_tally_and_confidence_ceiling(self):
        runner = self.build_runner(
            BULLISH_SIX, wave_advance_ms={"opening": 30_000, "r1": 50_000}
        )

        self.finish(runner)

        prompt = runner.core_adapter.calls[0]
        self.assertIn('"consensus_status": "consensus"', prompt)
        self.assertIn("confidence_level 的客觀上限是 green", prompt)
        self.assertIn("counter-evidence", prompt)

    def test_core_writes_the_report_without_any_search_capability(self):
        # 報告在 T+4 封存之後產生，只能依正式 artifacts，不得再上網。
        runner = self.build_runner(
            BULLISH_SIX, wave_advance_ms={"opening": 30_000, "r1": 50_000}
        )

        self.finish(runner)

        self.assertEqual([False], runner.core_adapter.allow_search)


class FullLaunchTest(unittest.TestCase):
    """One command from an approved question to a verifiable run bundle."""

    def setUp(self):
        self._reset_run_root()

    def _reset_run_root(self):
        """A fresh, certificated data root — one test may launch several runs."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._tmp = tmp
        self.data_root = Path(tmp.name) / "data"
        self.data_root.mkdir(parents=True)
        self.clock = FixedClock()
        self.results = None
        self.runner = None
        self.out = Stream()
        self.err = Stream()
        self._write_certificate()

    @staticmethod
    def _room(bullish, bearish, neutral=0):
        """Seven seats' opening stances by bloc size."""
        assert bullish + bearish + neutral == len(SEAT_IDS)
        stances = ("bullish",) * bullish + ("bearish",) * bearish + ("neutral",) * neutral
        return dict(zip(SEAT_IDS, stances))

    def _write_certificate(self):
        manifest = {
            "schema_version": CONTRACT_VERSION,
            "status": "READY",
            "provider_capabilities_ready": True,
            "generated_at_utc": CERTIFICATE_STAMP,
        }
        path = self.data_root / "preflight" / PREFLIGHT_ID / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        write_ready_certificate(self.data_root, PREFLIGHT_ID, manifest, path)

    def _runner_factory(self, stances=BULLISH_SIX, core_narratives=None, **runner_options):
        runner_options.setdefault("wave_advance_ms", {"opening": 30_000, "r1": 50_000})
        narratives = list(core_narratives) if core_narratives else [narrative()]

        def factory(*, run, data_root, results_queue, **options):
            self.results = results_queue
            self.runner = FullRunFakeRunner(
                run,
                results_queue,
                self.clock,
                stances,
                core_adapter=ScriptedCoreAdapter(self.clock, narratives),
                **runner_options,
            )
            return self.runner

        return factory

    def test_full_phase_produces_a_run_that_verify_run_accepts(self):
        code = run_launch(
            QUESTION,
            self.data_root,
            clock=self.clock,
            token_source=ScriptedTokenSource(["abc123"]),
            runner_factory=self._runner_factory(),
            sleeper=StepSleeper(self.clock, step_ms=30_000),
            out=self.out,
            err=self.err,
            no_live=True,
        )

        self.assertEqual(0, code, self.err.text)
        statuses = [json.loads(line)["status"] for line in self.out.lines]
        self.assertEqual(["LAUNCHED", "SEALED", "FINALIZED"], statuses)
        finalized = json.loads(self.out.lines[-1])
        self.assertEqual("consensus", finalized["consensus_status"])
        self.assertEqual(6, finalized["tally"]["bullish"])
        verification = verify_run(self.data_root, finalized["run_id"])
        self.assertEqual("VERIFIED", verification["status"])
        self.assertEqual("real-subscription-fast", verification["provider_mode"])
        self.assertFalse(verification["competition_ready"])
        run_dir = Path(finalized["run_dir"])
        self.assertTrue((run_dir / "report.html").is_file())
        self.assertTrue((run_dir / "debate.html").is_file())
        events = [
            json.loads(line)
            for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        # 直播頁只讀 events.jsonl；辯論原文與票數必須自動出現在裡面。
        seat_messages = [item for item in events if item.get("event") == "seat_message"]
        self.assertEqual(
            set(SEAT_IDS), {item["seat_id"] for item in seat_messages}
        )
        self.assertTrue(
            any(item["kind"] == "final_vote" for item in seat_messages)
        )

    def test_a_unanimous_blind_pass_run_verifies_with_an_empty_debate(self):
        """Ticket 03 驗收條件一：七席盲投全同立場的完整 run bundle。

        無任何辯論訊息（只有七則開場）、``votes.json`` 記 ``unanimous_blind_pass``、
        報告三頁照常產出、``verify-run`` PASS。
        """
        code = run_launch(
            QUESTION,
            self.data_root,
            clock=self.clock,
            token_source=ScriptedTokenSource(["abc123"]),
            runner_factory=self._runner_factory(stances=UNANIMOUS_SEVEN),
            sleeper=StepSleeper(self.clock, step_ms=30_000),
            out=self.out,
            err=self.err,
            no_live=True,
        )

        self.assertEqual(0, code, self.err.text)
        finalized = json.loads(self.out.lines[-1])
        run_dir = Path(finalized["run_dir"])
        votes = json.loads((run_dir / "votes.json").read_text(encoding="utf-8"))
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual("unanimous_blind_pass", votes["stop_reason"])
        self.assertEqual(
            "unanimous_blind_pass", manifest["competition_timeline"]["debate_stop_reason"]
        )
        self.assertEqual("consensus", votes["consensus_status"])
        self.assertEqual("bullish", votes["adopted_stance"])
        self.assertEqual(7, votes["valid_vote_count"])
        self.assertEqual(7, votes["threshold_required"])
        self.assertEqual(7, votes["tally"]["bullish"])
        self.assertEqual([], votes["dissent"])
        self.assertFalse(votes["challenge_completed"])
        self.assertLessEqual(votes["stop_elapsed_ms"], CHALLENGE_DEADLINE_MS)

        # 「無任何辯論訊息」＝公開紀錄只有七則開場，沒有挑戰、回應或改票。
        debate = [
            json.loads(line)
            for line in (run_dir / "debate.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        seat_messages = [
            entry for entry in debate if entry.get("event") == "seat_message"
        ]
        self.assertEqual({"position"}, {entry["kind"] for entry in seat_messages})
        self.assertEqual(len(SEAT_IDS), len(seat_messages))
        self.assertEqual(set(SEAT_IDS), {entry["seat_id"] for entry in seat_messages})

        # 報告正常產出。
        for name in ("report.json", "report.md", "report.html", "debate.html"):
            self.assertTrue((run_dir / name).is_file(), name)
        self.assertEqual("accepted", finalized["report_status"])

        verification = verify_run(self.data_root, finalized["run_id"])
        self.assertEqual("VERIFIED", verification["status"])

    def test_a_debated_run_cannot_relabel_itself_as_a_blind_pass(self):
        """改一個停止原因字串就想冒充直過：公開紀錄裡的挑戰會揭穿它。"""
        code = run_launch(
            QUESTION,
            self.data_root,
            clock=self.clock,
            token_source=ScriptedTokenSource(["abc123"]),
            runner_factory=self._runner_factory(),
            sleeper=StepSleeper(self.clock, step_ms=30_000),
            out=self.out,
            err=self.err,
            no_live=True,
        )

        self.assertEqual(0, code, self.err.text)
        finalized = json.loads(self.out.lines[-1])
        run_dir = Path(finalized["run_dir"])
        self.assertEqual("VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"])

        votes_path = run_dir / "votes.json"
        votes = json.loads(votes_path.read_text(encoding="utf-8"))
        self.assertEqual("consensus_6_votes", votes["stop_reason"])
        votes.update(
            stop_reason="unanimous_blind_pass",
            threshold_required=RULES.unanimous_blind_pass_votes,
            challenge_completed=False,
        )
        payload = json.dumps(votes, ensure_ascii=False, indent=2) + "\n"
        votes_path.write_text(payload, encoding="utf-8")

        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["competition_timeline"]["debate_stop_reason"] = "unanimous_blind_pass"
        manifest["artifacts"]["votes.json"]["sha256"] = hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(RunVerificationError, "七則開場"):
            verify_run(self.data_root, finalized["run_id"])

    def _launch(self, stances, step_ms=30_000, **runner_options):
        # step_ms 是輪詢粒度：所有席位都秒回時看不出差別，但只要有一席不回，
        # 粗粒度的輪詢會一步跨過第一輪牆，讓整場退化成「沒有人投得到票」。
        code = run_launch(
            QUESTION,
            self.data_root,
            clock=self.clock,
            token_source=ScriptedTokenSource(["abc123"]),
            runner_factory=self._runner_factory(stances=stances, **runner_options),
            sleeper=StepSleeper(self.clock, step_ms=step_ms),
            out=self.out,
            err=self.err,
            no_live=True,
        )
        self.assertEqual(0, code, self.err.text)
        return json.loads(self.out.lines[-1])

    def _reindex(self, run_dir, name, text):
        """Rewrite one artifact and repair the manifest index, as a forger would."""
        (run_dir / name).write_text(text, encoding="utf-8")
        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][name]["sha256"] = hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _debate_entries(self, run_dir):
        return [
            json.loads(line)
            for line in (run_dir / "debate.jsonl").read_text(encoding="utf-8").splitlines()
        ]

    def _reforge_debate(self, run_dir, entries):
        """Republish a tampered public record as a fully self-consistent bundle.

        偽造者會做完整套：重算每一則的 ``entry_sha256``、整條 public history
        雜湊鏈、由新的公開紀錄重新渲染三頁報告，最後修好 manifest 的 artifact
        index。這樣做出來的 bundle 每一項既有檢查都過得去，只有把公開紀錄與票
        表對起來的檢查才擋得住。
        """
        history = hashlib.sha256(b"").hexdigest()
        for entry in entries:
            payload = {
                key: value
                for key, value in entry.items()
                if key not in ("entry_sha256", "public_history_sha256")
            }
            entry["entry_sha256"] = content_sha256(payload)
            history = hashlib.sha256(
                (history + entry["entry_sha256"]).encode("utf-8")
            ).hexdigest()
            entry["public_history_sha256"] = history
        self._reindex(
            run_dir,
            "debate.jsonl",
            "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
        )
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        sources = {
            "evidence": [
                json.loads(line)
                for line in (run_dir / "evidence.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ],
            "debate": [entry for entry in entries if entry.get("seat_id")],
            "votes": json.loads((run_dir / "votes.json").read_text(encoding="utf-8")),
        }
        self._reindex(run_dir, "report.md", render_market_markdown(report))
        self._reindex(run_dir, "report.html", render_market_html(report, sources))
        self._reindex(run_dir, "debate.html", render_debate_html(report, sources))

    def test_a_forged_blind_pass_bundle_is_refused(self):
        """兩位 Reviewer 第 1 輪 [重要] ①：完全自洽的偽造 bundle 也必須被拒。

        由合法 7/7 run 把一席的開場改成反方，重算該則的 ``content_sha256``、
        整條 entry／public history 雜湊鏈、重新渲染三頁報告、修好 manifest 的
        artifact index——票表則維持七票一致。這份 bundle 每一項既有檢查都過得
        去（第 1 輪實測 VERIFIED），只有把公開紀錄與票表對起來才擋得住。
        """
        finalized = self._launch(UNANIMOUS_SEVEN)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

        entries = self._debate_entries(run_dir)
        target = next(
            entry
            for entry in entries
            if entry.get("event") == "seat_message"
            and entry.get("seat_id") == "counter-evidence"
        )
        target["stance"] = "bearish"
        target["content"]["stance"] = "bearish"
        target["content_sha256"] = content_sha256(target["content"])
        self._reforge_debate(run_dir, entries)

        votes = json.loads((run_dir / "votes.json").read_text(encoding="utf-8"))
        self.assertEqual(7, votes["tally"]["bullish"])
        self.assertEqual(
            {"bullish": 6, "bearish": 1},
            _opening_tally(entries),
            "偽造後的公開紀錄應為 6/1",
        )

        with self.assertRaisesRegex(
            RunVerificationError, "counter-evidence.*initial_stance"
        ):
            verify_run(self.data_root, finalized["run_id"])

    def _sources_on_disk(self, run_dir, votes=None):
        """The three official artifacts a renderer reads, as the bundle has them."""
        if votes is None:
            votes = json.loads((run_dir / "votes.json").read_text(encoding="utf-8"))
        return {
            "evidence": [
                json.loads(line)
                for line in (run_dir / "evidence.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ],
            "debate": [
                entry
                for entry in self._debate_entries(run_dir)
                if entry.get("seat_id")
            ],
            "votes": votes,
        }

    def _forge_report(self, run_dir, mutate):
        """Rewrite report.json and republish the three views it feeds.

        偽造者會把三頁都重新渲染、雜湊也修好，所以「報告是不是由 report.json
        產生」這道檢查照樣過得去；只有讀得懂燈號的檢查才擋得住。
        """
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        mutate(report)
        self._reindex(
            run_dir,
            "report.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        sources = self._sources_on_disk(run_dir)
        self._reindex(run_dir, "report.md", render_market_markdown(report))
        self._reindex(run_dir, "report.html", render_market_html(report, sources))
        self._reindex(run_dir, "debate.html", render_debate_html(report, sources))
        return report

    def _forge_votes(self, run_dir, mutate, sync_tally=False):
        """Rewrite votes.json and republish everything downstream of it."""
        votes_path = run_dir / "votes.json"
        votes = json.loads(votes_path.read_text(encoding="utf-8"))
        mutate(votes)
        self._reindex(
            run_dir, "votes.json", json.dumps(votes, ensure_ascii=False, indent=2) + "\n"
        )
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        if sync_tally:
            report["tally"] = dict(votes["tally"])
            self._reindex(
                run_dir,
                "report.json",
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            )
        sources = self._sources_on_disk(run_dir, votes)
        self._reindex(run_dir, "report.md", render_market_markdown(report))
        self._reindex(run_dir, "report.html", render_market_html(report, sources))
        self._reindex(run_dir, "debate.html", render_debate_html(report, sources))
        if sync_tally:
            manifest_path = run_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["tally"] = dict(votes["tally"])
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return votes

    def test_a_bundle_may_not_publish_a_light_outside_the_approved_set(self):
        """燈號級別集合一致性：驗證器與 report_contract 必須讀同一份 enum。

        報告 schema 的 enum、renderer 的樣式與 ``run_verifier`` 全部由
        ``report_contract.CONFIDENCE_LEVELS`` 供應，所以退場的 ``yellow_green``
        與根本不存在的 ``grene`` 都必須在驗證階段被擋下來——即使三頁都重新渲
        染過、雜湊也修好。
        """
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

        for level in ("yellow_green", "grene"):
            with self.subTest(level=level):
                self._forge_report(
                    run_dir,
                    lambda report, level=level: report["confidence"].update(level=level),
                )

                with self.assertRaisesRegex(RunVerificationError, "核准燈號"):
                    verify_run(self.data_root, finalized["run_id"])

    def test_a_bundle_may_not_publish_a_light_above_its_vote_count(self):
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

        self._forge_report(
            run_dir,
            lambda report: report["confidence"].update(
                level="blue", icon=CONFIDENCE_ICONS["blue"]
            ),
        )

        with self.assertRaisesRegex(RunVerificationError, "高於資料上限"):
            verify_run(self.data_root, finalized["run_id"])

    def test_a_bundle_that_understates_its_own_light_is_still_verified(self):
        # 上限是上限，不是規定值：Core 自行下修必須照樣通過。
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])

        self._forge_report(
            run_dir,
            lambda report: report["confidence"].update(
                level="yellow", icon=CONFIDENCE_ICONS["yellow"]
            ),
        )

        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

    # 每一級各自的票面：藍綠黃橘是共識停止，紅是未達共識停止。
    #
    # 落在強停牆上的兩室（4/3 與 3/3/1）要用比較細的輪詢粒度：30 秒一步會一腳
    # 跨過 T+10 停在 620000ms，而 run_verifier 要求停止時間不得超出封存至 T+10。
    # 那是測試替身的輪詢假象，不是產品行為——10 秒一步就精確停在 600000ms。
    LIGHT_BALLOTS = (
        ("blue", (7, 0, 0), 30_000),
        ("green", (6, 1, 0), 30_000),
        ("yellow", (5, 2, 0), 30_000),
        ("orange", (4, 3, 0), 10_000),
        ("red", (3, 3, 1), 10_000),
    )

    def test_every_approved_light_is_reachable_by_a_real_bundle(self):
        """驗收條件 5 的反向保護：任何漏掉某一級的 allowlist 都要有測試轉紅。

        只驗「未知值被拒」擋不住「合法值被誤拒」——例如日後某處新增一份只排除
        ``orange`` 的第二 allowlist。這裡逐級產生**對應票數的真實 bundle**、以
        該級發布、再送進 ``verify_run``；五級全部走一次，漏掉任何一級都會紅。
        """
        self.assertEqual(
            set(CONFIDENCE_LEVELS), {level for level, _, _ in self.LIGHT_BALLOTS}
        )
        for level, blocs, step_ms in self.LIGHT_BALLOTS:
            with self.subTest(level=level):
                self._reset_run_root()
                finalized = self._launch(
                    self._room(*blocs),
                    step_ms=step_ms,
                    core_narratives=[narrative(confidence_level=level)],
                )
                run_dir = Path(finalized["run_dir"])
                report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
                html = (run_dir / "report.html").read_text(encoding="utf-8")

                self.assertEqual("accepted", finalized["report_status"], self.err.text)
                self.assertEqual(level, report["confidence"]["level"])
                self.assertEqual(CONFIDENCE_ICONS[level], report["confidence"]["icon"])
                self.assertEqual(
                    level, confidence_cap(report, self._sources_on_disk(run_dir))
                )
                self.assertIn('class="confidence {}"'.format(level), html)
                self.assertIn(
                    "<strong>{}</strong>".format(CONFIDENCE_ICONS[level]), html
                )
                self.assertEqual(
                    "VERIFIED",
                    verify_run(self.data_root, finalized["run_id"])["status"],
                )

    def test_a_real_no_consensus_bundle_publishes_the_red_light(self):
        """S1：合法的 3/3/1 未達共識——七張有效票，最大集團 3——是紅燈。

        ADR 0003 決策 1 只給「最終採納立場」的票數一個燈號；未達共識時沒有採
        納立場，可數的採納票數是 0。
        """
        finalized = self._launch(
            self._room(3, 3, 1),
            step_ms=10_000,
            core_narratives=[narrative(confidence_level="red")],
        )
        run_dir = Path(finalized["run_dir"])
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        votes = json.loads((run_dir / "votes.json").read_text(encoding="utf-8"))

        self.assertEqual("forced_stop_no_consensus", votes["stop_reason"])
        self.assertEqual("no_consensus", report["consensus_status"])
        self.assertIsNone(report["adopted_stance"])
        self.assertEqual(7, votes["valid_vote_count"])
        self.assertEqual({"bullish": 3, "bearish": 3, "neutral": 1}, report["tally"])
        self.assertEqual("accepted", finalized["report_status"], self.err.text)
        self.assertEqual("red", report["confidence"]["level"])
        self.assertEqual(
            "red", confidence_cap(report, self._sources_on_disk(run_dir))
        )
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

    def test_a_real_no_consensus_bundle_may_not_publish_orange(self):
        # 漏擋方向：orange 正是修正前那個未達共識上限，必須被拒到紅牌。
        finalized = self._launch(
            self._room(3, 3, 1),
            step_ms=10_000,
            core_narratives=[narrative(confidence_level="orange")] * 2,
        )
        run_dir = Path(finalized["run_dir"])
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        attempts = json.loads(
            (run_dir / "diagnostics" / "report-attempts.json").read_text(encoding="utf-8")
        )

        self.assertEqual("red_audit", finalized["report_status"])
        self.assertEqual("red", report["confidence"]["level"])
        self.assertTrue(
            any(
                "信心 orange 高於資料上限 red" in problem
                for item in attempts
                for problem in item["problems"]
            ),
            attempts,
        )

    def test_the_objective_ceiling_of_a_six_vote_bundle_is_green(self):
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))

        self.assertEqual(6, report["tally"]["bullish"])
        self.assertEqual("green", confidence_cap(report, self._sources_on_disk(run_dir)))

    def test_a_seven_vote_bundle_may_publish_the_blue_light(self):
        # 燈號的最終選擇權在 Core，Python 只給上限；這裡證明七票時 blue 是可
        # 達到的，而且真的渲染成藍燈、通過完整 bundle 驗證。
        finalized = self._launch(UNANIMOUS_SEVEN)
        run_dir = Path(finalized["run_dir"])
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(7, report["tally"]["bullish"])
        self.assertEqual("blue", confidence_cap(report, self._sources_on_disk(run_dir)))

        published = self._forge_report(
            run_dir,
            lambda report: report["confidence"].update(
                level="blue", icon=CONFIDENCE_ICONS["blue"]
            ),
        )

        html = (run_dir / "report.html").read_text(encoding="utf-8")
        self.assertIn('class="confidence blue"', html)
        self.assertIn("<strong>🔵</strong>", html)
        self.assertIn(published["confidence"]["text"], html)
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

    def test_a_v2_bundle_has_no_assigned_challenge_lineage(self):
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )
        votes = json.loads((run_dir / "votes.json").read_text(encoding="utf-8"))
        self.assertFalse(votes["challenge_completed"])
        self.assertFalse(
            any(
                entry.get("kind") in ("challenge", "response")
                for entry in self._debate_entries(run_dir)
            )
        )

    def test_a_blind_pass_bundle_also_has_no_challenge_lineage(self):
        finalized = self._launch(UNANIMOUS_SEVEN)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )
        votes = json.loads((run_dir / "votes.json").read_text(encoding="utf-8"))
        self.assertFalse(votes["challenge_completed"])
        self.assertFalse(
            any(
                entry.get("kind") in ("challenge", "response", "final_vote")
                for entry in self._debate_entries(run_dir)
            )
        )

    def test_a_bundle_may_not_invent_an_extra_tally_column(self):
        """Reviewer B 第 2 輪 [建議]：多一個永遠是 0 的立場，重算照樣相等。

        votes／report／manifest 三份 tally 同步加上同一個捏造欄位、三頁重渲
        染、index 修好——所有「重算後相等」的檢查都還是相等，只有把欄位釘回
        該場自己的立場 enum 才擋得住。
        """
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

        votes = self._forge_votes(
            run_dir,
            lambda votes: votes["tally"].update(fabricated=0),
            sync_tally=True,
        )
        self.assertNotIn("fabricated", votes["stances"])

        with self.assertRaisesRegex(RunVerificationError, "立場 enum"):
            verify_run(self.data_root, finalized["run_id"])

    def test_an_opening_remains_valid_when_its_free_turn_times_out(self):
        finalized = self._launch(BULLISH_SIX, silent=(("news", "r1"),))
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )
        votes = json.loads((run_dir / "votes.json").read_text(encoding="utf-8"))
        self.assertEqual("valid", _seat_row(votes, "news")["state"])
        self.assertEqual("bullish", _seat_row(votes, "news")["final_stance"])
        self.assertFalse(
            any(
                entry.get("seat_id") == "news" and entry.get("kind") == "final_vote"
                for entry in self._debate_entries(run_dir)
            )
        )

    def test_a_blind_pass_bundle_may_not_invent_a_replacement_attempt(self):
        """兩位 Reviewer 第 2 輪 [重要] ①：完整 bundle 追加幽靈 attempt。

        votes 的 ``attempt_ids`` 追加一個公開紀錄裡不存在的 attempt，並同步
        report 的 ``replacement_attempt_ids``、重渲染三頁、修好 index——只驗
        「有包含」的檢查完全抓不到。
        """
        finalized = self._launch(UNANIMOUS_SEVEN)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

        phantom = "spot-technical-a2-phantom"
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        for row in report["seats"]:
            if row["seat_id"] == "spot-technical":
                row["replacement_attempt_ids"] = [phantom]
        self._reindex(
            run_dir,
            "report.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )

        def add_phantom(votes):
            for row in votes["votes"]:
                if row["seat_id"] == "spot-technical":
                    row["attempt_ids"] = row["attempt_ids"] + [phantom]

        votes = self._forge_votes(run_dir, add_phantom)
        self.assertEqual(
            ["spot-technical-a1", phantom],
            _seat_row(votes, "spot-technical")["attempt_ids"],
        )

        # 第 4 輪起 attempt lineage 由 ``_verify_attempt_lineage`` 單一權威把
        # 關（直過與普通辯論同一條規則），所以訊息由它發出。
        with self.assertRaisesRegex(
            RunVerificationError, "spot-technical.*attempt lineage"
        ):
            verify_run(self.data_root, finalized["run_id"])

    def test_a_blind_pass_bundle_may_not_erase_its_attempt_ids(self):
        """第 4 輪 R1：兩邊同時是 ``None`` 時 ``[None] == [None]`` 不得成立。

        狀態機對空的或非字串 attempt 一律丟 ``UnknownAttemptError``，所以這是
        一份狀態機不可能產生的 bundle。
        """
        finalized = self._launch(UNANIMOUS_SEVEN)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

        entries = self._debate_entries(run_dir)
        for entry in entries:
            if entry.get("seat_id") == "spot-technical":
                entry.pop("attempt_id", None)
                entry["content"].pop("attempt_id", None)
                entry["content_sha256"] = content_sha256(entry["content"])
        self._reforge_debate(run_dir, entries)

        def blank_attempts(votes):
            for row in votes["votes"]:
                if row["seat_id"] == "spot-technical":
                    row["attempt_ids"] = [None]

        self._forge_votes(run_dir, blank_attempts)

        with self.assertRaisesRegex(
            RunVerificationError, "spot-technical.*非空字串 attempt_id"
        ):
            verify_run(self.data_root, finalized["run_id"])

    def test_a_debated_bundle_may_not_invent_a_replacement_attempt(self):
        """第 4 輪 R2：普通路徑也必須驗 attempt 真的在公開紀錄裡出現過。

        我第 3 輪主張「不能限制成單一 attempt」——那半句對；但「所以完全不
        驗」不成立，兩位 Reviewer 都用這個形狀打穿了。
        """
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

        phantom = "spot-technical-a2-phantom"
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        for row in report["seats"]:
            if row["seat_id"] == "spot-technical":
                row["replacement_attempt_ids"] = [phantom]
        self._reindex(
            run_dir,
            "report.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )

        def add_phantom(votes):
            for row in votes["votes"]:
                if row["seat_id"] == "spot-technical":
                    row["attempt_ids"] = row["attempt_ids"] + [phantom]

        self._forge_votes(run_dir, add_phantom)

        with self.assertRaisesRegex(
            RunVerificationError, "spot-technical.*attempt lineage"
        ):
            verify_run(self.data_root, finalized["run_id"])

    def _promote_to_second_attempt(self, run_dir, seat_id, attempt_id, replay=None):
        """Re-attribute a seat's final vote to a later attempt, as a forger would."""
        entries = self._debate_entries(run_dir)
        vote = next(
            entry
            for entry in entries
            if entry.get("seat_id") == seat_id and entry.get("kind") == "final_vote"
        )
        vote["attempt_id"] = attempt_id
        vote["content"]["attempt_id"] = attempt_id
        vote["content_sha256"] = content_sha256(vote["content"])
        if replay is not None:
            entries.insert(entries.index(vote), replay)
        self._reforge_debate(run_dir, entries)
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        for row in report["seats"]:
            if row["seat_id"] == seat_id:
                row["replacement_attempt_ids"] = [attempt_id]
        self._reindex(
            run_dir,
            "report.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        self._forge_votes(
            run_dir,
            lambda votes: [
                row.update(attempt_ids=["{}-a1".format(seat_id), attempt_id])
                for row in votes["votes"]
                if row["seat_id"] == seat_id
            ],
        )

    def test_a_new_attempt_without_its_replay_event_is_refused(self):
        """第 5 輪 T1①：有 a2 訊息、沒有 replacement replay event。"""
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

        self._promote_to_second_attempt(
            run_dir, "spot-technical", "spot-technical-a2"
        )

        with self.assertRaisesRegex(RunVerificationError, "未一一對應"):
            verify_run(self.data_root, finalized["run_id"])

    def test_an_orphan_replay_event_is_refused(self):
        """第 5 輪 T1②：孤立的 replay event，沒有任何新 attempt 訊息。

        用 Reviewer A 的形狀——事件冒用既有的 a1、``votes`` 維持 ``[a1]``——
        這樣它繞得過 ``report_contract`` 的 attempt 回查，真正擋下它的是本票新
        增的「事件必須與替補 attempt 一一對應」。
        """
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

        entries = self._debate_entries(run_dir)
        entries.insert(
            len(entries) - 1,
            {
                "schema_version": CONTRACT_VERSION,
                "run_id": finalized["run_id"],
                "phase": "debate",
                "event": "replacement_replayed_public_history",
                "seat_id": "spot-technical",
                "attempt_id": "spot-technical-a1",
                "replaced_attempt_id": "spot-technical-a1",
                "replayed_history_sha256": "0" * 64,
                "elapsed_ms": 320_000,
                "created_at_utc": "2026-03-14T02:04:20.000Z",
                "deadline_phase": "first_round",
            },
        )
        self._reforge_debate(run_dir, entries)

        with self.assertRaisesRegex(RunVerificationError, "未一一對應"):
            verify_run(self.data_root, finalized["run_id"])

    def test_an_attempt_sequence_that_skips_a_number_is_refused(self):
        """第 5 輪 T2：a1 之後直接跳到 a3，序列不是 canonical 的。"""
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])

        self._promote_to_second_attempt(
            run_dir, "spot-technical", "spot-technical-a3"
        )

        with self.assertRaisesRegex(
            RunVerificationError, "第 2 個 attempt 應為 'spot-technical-a2'"
        ):
            verify_run(self.data_root, finalized["run_id"])

    def test_a_negative_timestamp_is_refused(self):
        """第 5 輪 T3：整席時間戳改成負值，相對順序維持不變。"""
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

        entries = self._debate_entries(run_dir)
        stamps = [-4, -3, -2, -1]
        index = 0
        for entry in entries:
            if entry.get("seat_id") == "spot-technical" and entry.get("kind"):
                entry["elapsed_ms"] = stamps[min(index, len(stamps) - 1)]
                index += 1
        self._reforge_debate(run_dir, entries)

        order = [
            entry["elapsed_ms"]
            for entry in self._debate_entries(run_dir)
            if entry.get("seat_id") == "spot-technical" and entry.get("kind")
        ]
        self.assertEqual(sorted(order), order, "相對順序必須維持不變")

        # 第 6 輪起負值先被無條件的底線擋下（不需要 timeline）；範圍檢查是有
        # timeline 時才追加的第二層。
        with self.assertRaisesRegex(RunVerificationError, "非負整數毫秒"):
            verify_run(self.data_root, finalized["run_id"])

    def test_a_near_miss_attempt_name_is_refused(self):
        """第 6 輪 V1：拿掉命名體系豁免後，改一個字元也不再放行。"""
        for attempt_id in ("spot-technical-b2", "spot-technical-A2", "spot-technical-a1x"):
            with self.subTest(attempt_id=attempt_id):
                self.setUp()
                finalized = self._launch(BULLISH_SIX)
                run_dir = Path(finalized["run_dir"])
                self._promote_to_second_attempt(run_dir, "spot-technical", attempt_id)

                with self.assertRaisesRegex(
                    RunVerificationError, "第 2 個 attempt 應為 'spot-technical-a2'"
                ):
                    verify_run(self.data_root, finalized["run_id"])

    def test_an_entry_wedged_between_the_replay_event_and_its_message_is_refused(self):
        """第 6 輪 V2：事件與新 attempt 首則訊息之間插入別席發言。"""
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

        entries = self._debate_entries(run_dir)
        vote = next(
            entry
            for entry in entries
            if entry.get("seat_id") == "spot-technical"
            and entry.get("kind") == "final_vote"
        )
        vote["attempt_id"] = "spot-technical-a2"
        vote["content"]["attempt_id"] = "spot-technical-a2"
        vote["content_sha256"] = content_sha256(vote["content"])
        position = entries.index(vote)
        wedged = next(
            entry
            for entry in entries
            if entry.get("seat_id") == "counter-evidence"
            and entry.get("kind") == "final_vote"
        )
        event = {
            "schema_version": CONTRACT_VERSION,
            "run_id": finalized["run_id"],
            "phase": "debate",
            "event": "replacement_replayed_public_history",
            "seat_id": "spot-technical",
            "attempt_id": "spot-technical-a2",
            "replaced_attempt_id": "spot-technical-a1",
            "elapsed_ms": vote["elapsed_ms"],
            "created_at_utc": vote["created_at_utc"],
            "deadline_phase": vote["deadline_phase"],
        }
        entries.insert(position, dict(wedged))
        entries.insert(position, event)
        # 重算雜湊鏈後把 replay 事件引用的公開歷史補成正確值。
        self._reforge_debate(run_dir, entries)
        entries = self._debate_entries(run_dir)
        index = next(
            i
            for i, entry in enumerate(entries)
            if entry.get("event") == "replacement_replayed_public_history"
        )
        entries[index]["replayed_history_sha256"] = entries[index - 1][
            "public_history_sha256"
        ]
        self._reforge_debate(run_dir, entries)

        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        for row in report["seats"]:
            if row["seat_id"] == "spot-technical":
                row["replacement_attempt_ids"] = ["spot-technical-a2"]
        self._reindex(
            run_dir,
            "report.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        self._forge_votes(
            run_dir,
            lambda votes: [
                row.update(attempt_ids=["spot-technical-a1", "spot-technical-a2"])
                for row in votes["votes"]
                if row["seat_id"] == "spot-technical"
            ],
        )

        with self.assertRaisesRegex(
            RunVerificationError, "未緊鄰它自己的第一則訊息"
        ):
            verify_run(self.data_root, finalized["run_id"])

    def test_a_negative_timestamp_is_refused_even_without_a_timeline(self):
        """第 6 輪 V3：時間戳底線不得只在有 competition_timeline 時生效。"""
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])

        entries = self._debate_entries(run_dir)
        for entry in entries:
            if entry.get("seat_id") == "spot-technical" and entry.get("kind"):
                entry["elapsed_ms"] = -1
                break
        self._reforge_debate(run_dir, entries)

        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.pop("competition_timeline")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(RunVerificationError, "非負整數毫秒"):
            verify_run(self.data_root, finalized["run_id"])

    def test_a_first_free_turn_vote_is_stamped_after_its_ballot_wall(self):
        """The driver itself publishes no free-turn vote before round one opens."""
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

        entries = self._debate_entries(run_dir)
        position = next(
            entry
            for entry in entries
            if entry.get("seat_id") == "spot-technical"
            and entry.get("kind") == "position"
        )
        vote = next(
            entry
            for entry in entries
            if entry.get("seat_id") == "spot-technical"
            and entry.get("kind") == "final_vote"
        )
        self.assertGreater(vote["elapsed_ms"], position["elapsed_ms"])
        self.assertGreaterEqual(vote["elapsed_ms"], SEAL_MS + 60_000)

    def test_a_free_vote_is_recorded_after_its_opening(self):
        finalized = self._launch(BULLISH_SIX)
        run_dir = Path(finalized["run_dir"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

        order = [
            (entry["kind"], entry["elapsed_ms"])
            for entry in self._debate_entries(run_dir)
            if entry.get("seat_id") == "spot-technical"
        ]
        self.assertEqual("final_vote", order[1][0], order)
        self.assertGreater(order[1][1], order[0][1])

    def test_a_seat_that_missed_its_first_round_does_not_void_the_consensus(self):
        """Reviewer B 第 1 輪 [重要] ③：一席掉隊的合法 run 必須 PASS。

        §5.4 只要求**每張有效票**完成第一輪，不要求所有開場參與者皆完成。
        ``challenge_completed`` 是房間層級旗標，那一席掉隊時它就是 False——
        把它當成共識的必要條件會誤殺這個形狀（實測比賽 run 就長這樣）。
        """
        stances = dict.fromkeys(SEAT_IDS[:5], "bullish")
        stances.update({"social-macro": "bearish", "counter-evidence": "bearish"})

        finalized = self._launch(
            stances,
            step_ms=1_000,
            silent=[("social-macro", "r1")],
            wave_advance_ms={"opening": 30_000, "r1": 50_000, "r2": 50_000},
        )
        run_dir = Path(finalized["run_dir"])
        votes = json.loads((run_dir / "votes.json").read_text(encoding="utf-8"))
        row = {item["seat_id"]: item for item in votes["votes"]}["social-macro"]

        self.assertEqual("accepted", finalized["report_status"])
        self.assertEqual("consensus_{}_votes".format(RULES.reduced_votes), votes["stop_reason"])
        self.assertEqual(RULES.reduced_votes, votes["threshold_required"])
        self.assertEqual(len(SEAT_IDS), votes["valid_vote_count"])
        self.assertEqual("consensus", votes["consensus_status"])
        self.assertEqual("bullish", votes["adopted_stance"])
        self.assertEqual("valid", row["state"])
        self.assertEqual("bearish", row["final_stance"])
        self.assertEqual("bearish", row["initial_stance"])
        # 房間旗標誠實地是 False，但共識仍然成立。
        self.assertFalse(votes["challenge_completed"])
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"]
        )

    def test_a_two_asset_comparison_seals_at_four_thirty_and_still_verifies(self):
        """Comparison ballots and final settle all move with the later seal."""
        code = run_launch(
            COMPARISON_QUESTION,
            self.data_root,
            clock=self.clock,
            token_source=ScriptedTokenSource(["abc123"]),
            runner_factory=self._runner_factory(stances=COMPARISON_SIX),
            sleeper=StepSleeper(self.clock, step_ms=30_000),
            out=self.out,
            err=self.err,
            no_live=True,
        )

        self.assertEqual(0, code, self.err.text)
        finalized = json.loads(self.out.lines[-1])
        run_dir = Path(finalized["run_dir"])
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        timeline = manifest["competition_timeline"]
        votes = json.loads((run_dir / "votes.json").read_text(encoding="utf-8"))

        self.assertEqual("two_asset_comparison", manifest["question_type"])
        self.assertEqual(COMPARISON_SEAL_MS, timeline["evidence_snapshot_sealed_at_ms"])
        self.assertEqual(260_000, timeline["research_accept_until_ms"])
        self.assertEqual("consensus_6_votes", votes["stop_reason"])
        self.assertEqual(7, votes["valid_vote_count"])
        self.assertEqual(6, votes["tally"]["asset_a_stronger"])
        self.assertEqual(COMPARISON_SEAL_MS + 150_000, votes["stop_elapsed_ms"])
        self.assertEqual("VERIFIED", verify_run(self.data_root, finalized["run_id"])["status"])


class FullRunFakeRunner(ScriptedDebateRunner):
    """Answers the research phase too, so one launch exercises both halves."""

    def __init__(self, run, results_queue, clock, stances, **options):
        super().__init__(results_queue, clock, stances, **options)
        self.run = run

    def start(self, attempt, checkpoint):
        self.results_queue.put(
            (
                "result",
                attempt.attempt_id,
                envelope_text(self.run.run_id, attempt.seat_id, attempt.attempt_id),
            )
        )
        return True

    def checkpoint(self, attempt_id):
        return None

    def correct(self, attempt, raw_output, exact_error):
        return None

    def shutdown(self, wait=True):
        return None


def _seat_row(votes, seat_id):
    return next(row for row in votes["votes"] if row["seat_id"] == seat_id)


def _opening_tally(entries):
    """Count the openings the public record actually holds, by stance."""
    counts = {}
    for entry in entries:
        if entry.get("event") == "seat_message" and entry.get("kind") == "position":
            counts[entry["stance"]] = counts.get(entry["stance"], 0) + 1
    return counts


class AfterSealRulesSnapshotTest(DebateDriverTestCase):
    """Ticket 11 D1：封存後的整段流程共用一份規則快照。

    ``run_after_seal`` 原本讀三次規則權威：driver 建構、Core prompt 的信心上限、
    以及報告驗證。撰稿當下被告知一個上限、交稿後被另一份規則驗收，於是一份完全
    合法的報告變成 ``red_audit``，錯誤訊息還是「信心 X 高於資料上限 Y」——而輸出
    裡沒有任何欄位記得那兩個數字來自不同的設定。

    這一組全部走 ``ScriptedDebateRunner`` 與 ``ScriptedCoreAdapter``，**不發出任
    何 codex 呼叫**。第 2 輪我宣稱這條路徑「需要真實 codex 所以量不到」，那是一
    個沒驗證過的假設。
    """

    def all_bullish(self):
        return self.build_runner({seat: "bullish" for seat in SEAT_IDS})

    def stricter(self):
        """Same ladder, plus a downgrade that always fires: blue → orange.

        七席全票在任何合法階梯上都是 blue（blue 的 min_votes 上限就是席位數），
        所以要讓上限低於稿件宣稱的 yellow，只能靠降級。
        """
        from hoya_market_agents.debate_rules import DowngradeRule

        shipped = debate_rules()
        return replace(
            shipped,
            confidence=replace(
                shipped.confidence,
                downgrades=(
                    DowngradeRule(
                        rule="few_independent_domains",
                        levels=3,
                        min_independent_domains=99,
                    ),
                ),
            ),
        )

    def answering(self, *rulesets):
        reads = []

        def next_answer():
            reads.append(None)
            return rulesets[min(len(reads) - 1, len(rulesets) - 1)]

        return next_answer, reads

    def patched(self, authority):
        """Swap the authority in every module the after-seal flow reads it from."""
        from unittest import mock

        from hoya_market_agents import debate_driver, report_contract, report_workflow

        return [
            mock.patch.object(module, "debate_rules", authority)
            for module in (debate_driver, report_contract, report_workflow)
            if hasattr(module, "debate_rules")
        ]

    def finish_with(self, authority):
        runner = self.all_bullish()
        patches = self.patched(authority)
        for patch in patches:
            patch.start()
        try:
            return self.finish(runner)
        finally:
            for patch in patches:
                patch.stop()

    def test_the_shipped_rules_accept_the_ordinary_run(self):
        """FP 方向：整組改動不得讓正常路徑改變行為。"""
        handshake = self.finish(self.all_bullish())

        self.assertEqual("accepted", handshake["report_status"])
        self.assertEqual([], handshake["report_errors"])

    def test_a_reload_after_the_prompt_does_not_reject_a_legal_report(self):
        """核心回歸：撰稿後規則變嚴，報告仍必須照舊被接受。"""
        authority, reads = self.answering(debate_rules(), self.stricter())

        handshake = self.finish_with(authority)

        self.assertEqual(1, len(reads), "第二次讀取＝一個 reload 插得進來的窗口")
        self.assertEqual("accepted", handshake["report_status"])
        self.assertEqual([], handshake["report_errors"])

    def test_a_report_above_the_ceiling_is_still_rejected(self):
        """FP 方向：單一快照 ≠ 不驗證。

        整段流程都用同一份嚴格規則時，稿件宣稱的 yellow 高於 orange 上限，必須
        照樣被擋下來——證明上一條的 accepted 是因為只讀一次，不是因為檢查沒跑。
        """
        strict = self.stricter()

        handshake = self.finish_with(lambda: strict)

        self.assertEqual("red_audit", handshake["report_status"])
        self.assertIn("信心 yellow 高於資料上限 orange", handshake["report_errors"])

    def test_the_whole_after_seal_flow_reads_the_authority_once(self):
        from tests.test_debate_rules import count_authority_reads

        runner = self.all_bullish()

        self.assertEqual(1, count_authority_reads(lambda: self.finish(runner)))

    def test_the_red_audit_path_also_reads_the_authority_once(self):
        """紅字稽核也在同一份快照上。

        ``_red_outcome`` 會把自己組出來的稽核報告再驗一次；那次驗證的結果被丟
        棄，所以它讀哪一份規則**不影響輸出**——正因如此，只驗成功路徑的計數測試
        完全蓋不到它。這條測試就是為了蓋住那個洞：Core 直接爆掉，流程走紅字稽
        核，讀取次數仍然必須是 1。
        """
        from tests.test_debate_rules import count_authority_reads

        class ExplodingCoreAdapter:
            def __init__(self, clock):
                self.clock = clock
                self.calls = []

            def invoke(self, prompt, schema, work_dir, allow_search=True):
                self.calls.append(prompt)
                self.clock.advance_ms(1_000)
                raise RuntimeError("core down")

        runner = self.build_runner(
            {seat: "bullish" for seat in SEAT_IDS},
            core_adapter=ExplodingCoreAdapter(self.clock),
        )
        handshake = {}

        def finish():
            handshake.update(self.finish(runner))

        reads = count_authority_reads(finish)

        self.assertEqual("red_audit", handshake["report_status"])
        self.assertEqual(1, reads)

    def test_the_prompt_ceiling_and_the_verdict_come_from_one_ruleset(self):
        """上限只會被寫進 prompt 一次，而驗收用的就是同一份。

        兩份設定並存時，prompt 裡會出現一個上限、驗證會用另一個；這裡直接檢查
        Core 收到的 prompt 只提到一個上限，而且報告被接受。
        """
        authority, reads = self.answering(debate_rules(), self.stricter())
        runner = self.all_bullish()
        patches = self.patched(authority)
        for patch in patches:
            patch.start()
        try:
            handshake = self.finish(runner)
        finally:
            for patch in patches:
                patch.stop()

        ceilings = [
            line
            for prompt in runner.core_adapter.calls
            for line in prompt.splitlines()
            if "客觀上限是" in line
        ]
        self.assertEqual(["- confidence_level 的客觀上限是 blue；不得高於它。"], ceilings)
        self.assertEqual("accepted", handshake["report_status"])
        self.assertEqual(1, len(reads))


if __name__ == "__main__":
    unittest.main()
