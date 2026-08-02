"""Ticket T6: everything after the T+4:00 seal, driven fully offline.

No provider, no subprocess and no wall clock. The debate pool is a scripted
stand-in that answers on the same queue contract ``RealSeatRunner`` publishes,
and time only moves when the scripted runner or the sleeper moves the fake
clock — exactly the compressed-time technique ``test_competition_drill`` uses.
"""

import json
import queue
import tempfile
import unittest
from dataclasses import fields
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from tests.fakes import FIXED_START_UTC, FixedClock, ScriptedTokenSource

from hoya_market_agents.clock import iso_utc
from hoya_market_agents.contract_validator import CONTRACT_VERSION
from hoya_market_agents.debate_driver import (
    DebateDriver,
    assemble_market_report,
    assign_challenges,
    build_turns,
    challenge_message_id,
    run_after_seal,
)
from hoya_market_agents.debate_state_machine import (
    CHALLENGE_DEADLINE_MS,
    DEBATE_START_MS,
    FINAL_ROUND_END_MS,
    FINAL_ROUND_START_MS,
    FORCE_STOP_MS,
    ROUND_ONE_WINDOW_MS,
    THRESHOLD_FIVE_FROM_MS,
    stances_for,
)
from hoya_market_agents.launcher import run_launch
from hoya_market_agents.question_package import build_question_package
from hoya_market_agents.research_scheduler import research_deadlines
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.run_verifier import verify_run
from hoya_market_agents.seats import SEAT_IDS
from hoya_market_agents.system_preflight import write_ready_certificate

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
        if slug == "r1":
            return {
                "seat_id": seat_id,
                "challenges": [
                    {
                        "target_seat_id": other,
                        "public_reason": "本席以已引用的證據反駁 {} 的公開立場。".format(other),
                        "evidence_ids": ["{}-01".format(seat_id)],
                    }
                    for other in self._challenge_targets(seat_id)
                ],
                "response": {
                    "public_reason": "本席回應反方挑戰並維持原立場。",
                    "evidence_ids": ["{}-01".format(seat_id)],
                    "responds_to": list(self.responds_to.get(seat_id, ())),
                },
                "final_vote": self._vote(seat_id, self.stances[seat_id]),
            }
        stance = self.revotes.get((seat_id, slug), self.stances[seat_id])
        changed = stance != self.stances[seat_id]
        self.stances[seat_id] = stance
        return self._vote(seat_id, stance, changed=changed)

    def _challenge_targets(self, seat_id):
        """Opposing seats, or — when the room agrees — every other seat.

        A real seat writes its challenges against whoever the round 1 prompt
        names, so a unanimous room's scrutiny round has to be answerable here
        too; refusing to write one would test the double, not the driver.
        """
        opposing = [
            other
            for other in SEAT_IDS
            if self.stances.get(other) not in (None, self.stances[seat_id])
        ]
        if opposing:
            return opposing
        return [
            other
            for other in SEAT_IDS
            if other != seat_id and self.stances.get(other) is not None
        ]

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

    def drive(self, runner):
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
                    turns["r1"].schema["properties"]["final_vote"]["properties"]["stance"][
                        "enum"
                    ],
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
        self.assertTrue(votes["challenge_completed"])
        # 少數票先 relay，六票停止時異議仍留在正式紀錄裡。
        self.assertEqual(7, votes["valid_vote_count"])
        self.assertEqual("counter-evidence", votes["dissent"][0]["seat_id"])
        self.assertEqual([], [note for note in driver.notes if note["seat_id"]])
        self.assertEqual(votes, self.votes_file())

    def test_a_seat_may_cite_its_own_incoming_challenge_ids(self):
        _, incoming = assign_challenges(BULLISH_SIX)
        responds_to = {
            seat_id: [challenge_message_id(author, seat_id) for author in authors]
            for seat_id, authors in incoming.items()
        }
        # 這一席只回應排程表上的最後一則挑戰，target_seat_id 必須跟著它走。
        responds_to["counter-evidence"] = responds_to["counter-evidence"][-1:]
        runner = self.build_runner(BULLISH_SIX, responds_to=responds_to)

        driver, votes = self.drive(runner)

        responses = {
            entry["seat_id"]: entry
            for entry in driver.machine.entries
            if entry.get("kind") == "response"
        }
        self.assertEqual(7, len(responses))
        self.assertEqual(
            [challenge_message_id("social-macro", "counter-evidence")],
            responses["counter-evidence"]["responds_to"],
        )
        self.assertEqual(
            "social-macro", responses["counter-evidence"]["target_seat_id"]
        )
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

    def test_four_votes_are_adopted_only_at_the_forced_stop(self):
        stances = dict.fromkeys(SEAT_IDS[:4], "bullish")
        stances.update(
            {"news": "bearish", "social-macro": "bearish", "counter-evidence": "neutral"}
        )
        runner = self.build_runner(
            stances,
            wave_advance_ms={"opening": 30_000, "r1": 50_000, "r2": 60_000, "r3": 60_000},
        )

        _, votes = self.drive(runner)

        self.assertEqual("forced_stop_4_votes", votes["stop_reason"])
        self.assertEqual(FORCE_STOP_MS, votes["stop_elapsed_ms"])
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
        self.assertEqual(6, votes["valid_vote_count"])
        # 這一席只發布了初始立場，沒完成第一輪：有提名無有效票。
        self.assertEqual("provisional", _seat_row(votes, "news")["state"])
        self.assertIsNone(_seat_row(votes, "news")["final_stance"])
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
        self.assertIsNone(report_row["final_stance"])

        # 3) 沒有第一輪就沒有有效票；這條規則只由狀態機認定。
        self.assertNotIn("news", driver.machine.valid_votes())
        self.assertEqual("provisional", driver.machine.seats["news"].state)
        self.assertFalse(votes["challenge_completed"])

        diagnostics = json.loads(
            (self.run.path / "diagnostics" / "debate-driver.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("news", diagnostics["seats_in_public_record"])
        self.assertNotIn("news", diagnostics["seats_completing_first_round"])
        self.assertIn(
            "position_published_without_first_round",
            [note["reason"] for note in driver.notes if note["seat_id"] == "news"],
        )

    def test_a_room_that_agrees_on_one_stance_reaches_consensus(self):
        """Ticket R8: 全場一致是最強共識，跑完魔鬼代言人輪就該收工。"""
        runner = self.build_runner(
            UNANIMOUS_SEVEN,
            wave_advance_ms={"opening": 30_000, "r1": 50_000, "r2": 60_000, "r3": 60_000},
        )

        driver, votes = self.drive(runner)

        self.assertEqual("consensus_6_votes", votes["stop_reason"])
        self.assertEqual("consensus", votes["consensus_status"])
        self.assertEqual("bullish", votes["adopted_stance"])
        self.assertEqual(6, votes["valid_vote_count"])
        self.assertFalse(votes["red_no_conclusion"])
        self.assertTrue(votes["market_conclusion_allowed"])
        # 七席都公開講過話，紀錄必須留著；第六票達標時第七票已無處可投。
        self.assertEqual(["bullish"] * 7, [row["initial_stance"] for row in votes["votes"]])
        self.assertEqual(tuple(SEAT_IDS), driver.viable)
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
        self.assertEqual(6, handshake["valid_vote_count"])

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

    def test_a_room_with_a_single_published_position_has_nobody_to_pair(self):
        # 只有一席公開發言時，連輪替配對都湊不出對象：誠實退化，不得炸掉整場。
        runner = self.build_runner(
            UNANIMOUS_SEVEN,
            wave_advance_ms={"opening": 30_000, "r2": 60_000, "r3": 60_000},
            silent=[(seat_id, "opening") for seat_id in SEAT_IDS[1:]],
        )

        driver, votes = self.drive(runner)

        self.assertEqual(
            ["{}-opening".format(seat_id) for seat_id in SEAT_IDS], runner.started
        )
        self.assertEqual(
            1,
            len(
                [
                    note
                    for note in driver.notes
                    if note["reason"] == "skipped_r1_wave_no_opposing_pair"
                ]
            ),
        )
        self.assertEqual([SEAT_IDS[0]], list(driver.published))
        self.assertEqual((), driver.viable)
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
    """開場收集預算不得吃掉第一輪：吃掉了整場就沒有任何有效票。

    run 20260802T022316Z 的七席開場全部發布（315505-360080ms），第一輪七次呼叫
    卻只剩 24 秒，全部 timeout，只有最快的一席回得來，於是
    ``forced_stop_insufficient_valid_votes``、valid_vote_count 0。
    """

    def test_openings_spread_across_the_window_still_get_a_first_round(self):
        latency_ms = {
            (seat_id, "opening"): value
            for seat_id, value in REAL_OPENING_LATENCY_MS.items()
        }
        # 第一輪比開場更長：真實 run 裡 24 秒的殘餘預算讓整波全滅。
        latency_ms.update(
            {(seat_id, "r1"): 30_000 for seat_id in SEAT_IDS}
        )
        runner = self.build_latency_runner(BEARISH_FIVE_NEUTRAL_TWO, latency_ms)

        driver, votes = self.drive(runner)

        first_round = [item for item in runner.started if item.endswith("-r1")]
        challenges = [
            entry
            for entry in driver.machine.entries
            if entry.get("kind") == "challenge"
        ]
        self.assertTrue(first_round, "第一輪必須被派發")
        self.assertTrue(challenges, "第一輪必須真的產生挑戰")
        self.assertTrue(driver.viable, "至少要有一組完成第一輪的對手")
        self.assertGreaterEqual(votes["valid_vote_count"], 4)
        self.assertNotEqual(
            "forced_stop_insufficient_valid_votes", votes["stop_reason"]
        )

    def test_a_bearish_and_neutral_room_pairs_every_seat(self):
        # viable_seats 的本意只是排除「全場單一立場」；沒有 bullish 不代表
        # neutral 不能被挑戰，也不代表 neutral 不能挑戰別人。
        runner = self.build_runner(
            BEARISH_FIVE_NEUTRAL_TWO,
            wave_advance_ms={"opening": 30_000, "r1": 50_000},
        )

        driver, votes = self.drive(runner)

        self.assertEqual(
            ["{}-r1".format(seat_id) for seat_id in SEAT_IDS],
            [item for item in runner.started if item.endswith("-r1")],
        )
        self.assertEqual(tuple(SEAT_IDS), driver.viable)
        for author, targets in driver.assignment.items():
            for target in targets:
                self.assertNotEqual(
                    BEARISH_FIVE_NEUTRAL_TWO[author],
                    BEARISH_FIVE_NEUTRAL_TWO[target],
                    (author, target),
                )
        self.assertTrue(votes["challenge_completed"])
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
                self.assertNotIn(
                    "skipped_r1_wave_no_opposing_pair",
                    [note["reason"] for note in driver.notes],
                )


SCRUTINY_RULE = (
    "全場立場一致；本輪為壓力測試：請針對目標席位的證據品質提出質疑，"
    "或從快照中找出最強反證；若審視後仍維持立場，照實說明理由。"
)


class UnanimousRoomScrutinyTest(DebateDriverTestCase):
    """Ticket R8 修訂一：全場一致是最強共識，不得判成流局。

    run 20260802T043728Z-btc-eth-f8ea46 的七席開場立場完全一致，「第一輪必須挑戰
    相反立場」讓整波 r1 被跳過、零有效票，乾等到 T+10 才流局。修訂後同一個房間
    要跑完魔鬼代言人輪，七席都拿到有效票，並在最早的門檻點收工。
    """

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
        return latency_ms

    def test_a_unanimous_room_runs_a_scrutiny_round_and_reaches_consensus(self):
        runner = self.build_latency_runner(UNANIMOUS_SEVEN, self.latencies())

        driver, votes = self.drive(runner)

        self.assertEqual(
            ["{}-r1".format(seat_id) for seat_id in SEAT_IDS],
            [item for item in runner.started if item.endswith("-r1")],
        )
        self.assertEqual(tuple(SEAT_IDS), driver.viable)
        self.assertEqual("consensus_6_votes", votes["stop_reason"])
        self.assertEqual("consensus", votes["consensus_status"])
        self.assertEqual("bullish", votes["adopted_stance"])
        self.assertEqual(6, votes["tally"]["bullish"])
        self.assertTrue(votes["challenge_completed"])
        self.assertLess(votes["stop_elapsed_ms"], THRESHOLD_FIVE_FROM_MS)
        self.assertNotIn(
            "skipped_r1_wave_no_opposing_pair",
            [note["reason"] for note in driver.notes],
        )

    def test_every_scrutiny_challenge_rotates_the_roster_and_is_marked_for_audit(self):
        runner = self.build_latency_runner(UNANIMOUS_SEVEN, self.latencies())

        driver, _ = self.drive(runner)

        challenges = {
            entry["seat_id"]: entry
            for entry in driver.machine.entries
            if entry.get("kind") == "challenge"
        }
        self.assertEqual(set(SEAT_IDS), set(challenges))
        self.assertEqual(
            {"scrutiny"}, {entry["challenge_mode"] for entry in challenges.values()}
        )
        # roster 順序輪替：每席挑戰下一席，最後一席挑戰第一席。
        self.assertEqual(
            [SEAT_IDS[(index + 1) % len(SEAT_IDS)] for index in range(len(SEAT_IDS))],
            [challenges[seat_id]["target_seat_id"] for seat_id in SEAT_IDS],
        )

    def test_the_scrutiny_prompt_asks_for_a_stress_test_not_a_fake_opponent(self):
        runner = self.build_runner(
            UNANIMOUS_SEVEN, wave_advance_ms={"opening": 30_000, "r1": 50_000}
        )

        self.drive(runner)

        self.assertEqual(len(SEAT_IDS), len(runner.prompts["r1"]))
        for prompt in runner.prompts["r1"]:
            self.assertIn(SCRUTINY_RULE, prompt)
            self.assertNotIn("持相反立場", prompt)

    def test_a_mixed_room_keeps_the_opposing_wording_and_the_opposing_mode(self):
        runner = self.build_runner(
            BEARISH_FIVE_NEUTRAL_TWO,
            wave_advance_ms={"opening": 30_000, "r1": 50_000},
        )

        driver, votes = self.drive(runner)

        modes = {
            entry["challenge_mode"]
            for entry in driver.machine.entries
            if entry.get("kind") == "challenge"
        }
        self.assertEqual({"opposing"}, modes)
        for prompt in runner.prompts["r1"]:
            self.assertIn("持相反立場", prompt)
            self.assertNotIn(SCRUTINY_RULE, prompt)
        self.assertEqual(7, votes["valid_vote_count"])


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

    def test_every_seat_earns_a_valid_vote_inside_the_first_round_wall(self):
        runner = self.build_latency_runner(
            BEARISH_FIVE_NEUTRAL_TWO, self.latencies()
        )

        driver, votes = self.drive(runner)

        self.assertEqual(7, votes["valid_vote_count"])
        self.assertTrue(votes["challenge_completed"])
        self.assertEqual(tuple(SEAT_IDS), driver.viable)
        # 開場與第一輪是有效票的必經之路：這兩輪不准有任何一席掉隊。
        self.assertEqual(
            [], [note for note in driver.notes if note["turn"] in ("opening", "r1")]
        )

    def test_a_fast_seat_gets_its_first_round_call_without_waiting_for_a_slow_one(self):
        # 快席不等慢席：只要公開紀錄裡出現第二種立場，該席的 r1 立刻派出去。
        runner = self.build_latency_runner(
            BEARISH_FIVE_NEUTRAL_TWO, self.latencies()
        )

        self.drive(runner)

        slowest_opening = DEBATE_START_MS + max(REAL_OPENING_LATENCY_MS.values())
        self.assertLess(
            runner.dispatched_at_ms["counter-evidence-r1"], slowest_opening
        )
        self.assertLess(runner.dispatched_at_ms["onchain-r1"], slowest_opening)
        # 慢席自己的 r1 仍然在它交出開場之後才派發，不是提前亂猜。
        self.assertGreaterEqual(
            runner.dispatched_at_ms["social-macro-r1"],
            DEBATE_START_MS + REAL_OPENING_LATENCY_MS["social-macro"],
        )

    def test_six_votes_in_the_first_round_stop_before_the_first_round_wall(self):
        runner = self.build_latency_runner(BULLISH_SIX, self.latencies())

        _, votes = self.drive(runner)

        self.assertEqual("consensus_6_votes", votes["stop_reason"])
        self.assertEqual(6, votes["tally"]["bullish"])
        self.assertLessEqual(votes["stop_elapsed_ms"], CHALLENGE_DEADLINE_MS)

    def test_a_hundred_second_opening_honestly_misses_the_two_minute_window(self):
        # 使用者知情取捨：第一輪牆＝封存＋2:00。支援的最慢鏈是 115 秒
        # （120 秒窗 − 5 秒 relay 裕度）；100 秒開場＋60 秒挑戰＝160 秒，
        # 該席誠實成為 provisional，其他六席照常取得有效票。
        latency_ms = self.latencies()
        latency_ms[("social-macro", "opening")] = 100_000
        latency_ms[("social-macro", "r1")] = 60_000
        runner = self.build_latency_runner(BEARISH_FIVE_NEUTRAL_TWO, latency_ms)

        driver, votes = self.drive(runner)

        self.assertEqual("provisional", _seat_row(votes, "social-macro")["state"])
        self.assertEqual(6, votes["valid_vote_count"])

    def test_the_collection_budgets_stay_inside_their_own_walls(self):
        turns = build_turns(("bullish", "bearish", "neutral"))

        self.assertEqual(CHALLENGE_DEADLINE_MS - 5_000, turns["r1"].collect_until_ms)
        self.assertEqual(FINAL_ROUND_START_MS - 5_000, turns["r2"].collect_until_ms)
        self.assertEqual(FINAL_ROUND_END_MS - 5_000, turns["r3"].collect_until_ms)
        self.assertEqual(DEBATE_START_MS, turns["opening"].relay_from_ms)
        self.assertEqual(FINAL_ROUND_START_MS, turns["r3"].relay_from_ms)

    def test_a_later_seal_moves_the_first_round_wall_with_it(self):
        """相對牆：第一輪牆＝封存＋2:00；r2/r3 的絕對牆不動。"""
        turns = build_turns(
            ("asset_a_stronger", "asset_b_stronger", "no_clear_difference"),
            COMPARISON_SEAL_MS,
        )

        self.assertEqual(COMPARISON_SEAL_MS, turns["opening"].relay_from_ms)
        self.assertEqual(COMPARISON_SEAL_MS, turns["r1"].relay_from_ms)
        self.assertEqual(COMPARISON_SEAL_MS + 120_000, turns["opening"].collect_until_ms)
        self.assertEqual(
            COMPARISON_SEAL_MS + ROUND_ONE_WINDOW_MS - 5_000,
            turns["r1"].collect_until_ms,
        )
        self.assertEqual(
            COMPARISON_SEAL_MS + ROUND_ONE_WINDOW_MS + 1, turns["r2"].relay_from_ms
        )
        self.assertEqual(FINAL_ROUND_START_MS - 5_000, turns["r2"].collect_until_ms)
        self.assertEqual(FINAL_ROUND_END_MS - 5_000, turns["r3"].collect_until_ms)


class TurnWindowAuthorityTest(unittest.TestCase):
    """回合視窗只能有一份定義，而且它在 DebateStateMachine。"""

    def test_a_turn_carries_no_second_copy_of_the_round_window(self):
        turns = build_turns(("bullish", "bearish", "neutral"))

        for slug, turn in turns.items():
            names = {field.name for field in fields(turn)}
            self.assertNotIn("relay_until_ms", names, slug)


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
            runner, core_narratives=[narrative(confidence_level="green")] * 2
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
            runner, core_narratives=[narrative(confidence_level="green")] * 2
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
            self.assertEqual("green", item["draft"]["confidence"]["level"])
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
        self.assertIn("confidence_level 的客觀上限是 yellow_green", prompt)
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
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name) / "data"
        self.data_root.mkdir(parents=True)
        self.clock = FixedClock()
        self.results = None
        self.runner = None
        self.out = Stream()
        self.err = Stream()
        self._write_certificate()

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

    def _runner_factory(self, stances=BULLISH_SIX, **unused):
        def factory(*, run, data_root, results_queue, **options):
            self.results = results_queue
            self.runner = FullRunFakeRunner(
                run,
                results_queue,
                self.clock,
                stances,
                core_adapter=ScriptedCoreAdapter(self.clock, [narrative()]),
                wave_advance_ms={"opening": 30_000, "r1": 50_000},
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

    def test_a_two_asset_comparison_seals_at_four_thirty_and_still_verifies(self):
        """Ticket R7: 比較題晚 30 秒封存，仍在第一輪內拿到七張有效票。"""
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
        self.assertLessEqual(votes["stop_elapsed_ms"], CHALLENGE_DEADLINE_MS)
        self.assertGreaterEqual(votes["stop_elapsed_ms"], COMPARISON_SEAL_MS)
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


if __name__ == "__main__":
    unittest.main()
