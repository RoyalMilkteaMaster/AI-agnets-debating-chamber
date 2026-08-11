"""Ticket T3 cold-start launcher behaviour; every test runs fully offline.

No provider, no subprocess and no wall clock is involved: the launcher's five
seams (``clock``, ``token_source``, ``runner_factory``, ``live_starter`` and
``sleeper``) are all injected, and the sleeper is what advances the fake clock
towards the T+4:00 seal.

These tests own the cold start up to the sealed snapshot, so they run
``--phase research``. Ticket T6's ``--phase full`` pipeline — debate, vote,
report and finalize — is covered end to end in ``tests/test_debate_driver.py``.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.fakes import FixedClock, ScriptedTokenSource

from hoya_market_agents.codex_exec_adapter import CODEX_MODEL, CodexExecTimeout
from hoya_market_agents.codex_inbox import write_seat_result
from hoya_market_agents.launcher import (
    DEGRADED_AFFIRMATIVE_MEANS,
    DEGRADED_NEGATIVE_MEANS,
    LIVE_URL,
    PHASE_RESEARCH,
    PROPOSITION_SCHEMA,
    PROPOSITION_TIMEOUT_SECONDS,
    default_proposition_adapter,
    ready_certificate_problem,
    run_launch,
)
from hoya_market_agents.prompt_builder import build_seat_prompt
from hoya_market_agents.question_package import build_question_package
from hoya_market_agents.real_provider import CLAUDE_SEAT_IDS, CODEX_SEAT_IDS
from hoya_market_agents.research_scheduler import SEAL_MS, research_deadlines
from hoya_market_agents.seats import CODE_ROOT, SEAT_IDS, load_roster
from hoya_market_agents.system_preflight import write_ready_certificate

QUESTION = "BTC 過去 14 日的市場狀態如何？"
OPEN_QUESTION = "若美國通過比特幣戰略儲備法案，BTC 與市場情緒會如何反應？"
TW_STOCK_QUESTION = "幫我分析 2330 未來七天會不會漲"
US_STOCK_QUESTION = "NVDA 這檔美股未來七天股價會不會漲"
UNLISTED_COIN_QUESTION = "DOGE 幣價未來七天會不會漲"
NO_ASSET_QUESTION = "聯準會九月會不會降息"
# 命題撰寫升為所有題型的主路徑，所以每一次 launch 都會呼叫它一次；測試一律注入。
DEFAULT_PROPOSITION = {
    "proposition": "本題標的在指定期間內將上漲。",
    "affirmative_means": "認為會漲。",
    "negative_means": "認為不會漲。",
}
# One hour before FixedClock's start, so the freshness advisory stays silent.
CERTIFICATE_STAMP = "2026-03-14T00:59:26Z"
STALE_CERTIFICATE_STAMP = "2026-03-13T00:59:26Z"
CARD_STAMP = "2026-03-14T01:00:00Z"
PREFLIGHT_ID = "20260314T005926Z-aaa111"
LOCAL_SEAT_IDS = CLAUDE_SEAT_IDS + ("counter-evidence",)


def evidence_card(run_id, seat_id, attempt_id, asset="BTC"):
    return {
        "schema_version": "1.0.0",
        "evidence_id": "{}-01".format(seat_id),
        "run_id": run_id,
        "seat_id": seat_id,
        "attempt_id": attempt_id,
        "phase": "research",
        "created_at_utc": CARD_STAMP,
        "elapsed_ms": 1_000,
        "asset": asset,
        "category": seat_id,
        "statement": "測試用證據陳述，僅驗證 launcher 行為。",
        "direction": "support",
        "source_url": "https://fake.invalid/{}/1".format(seat_id),
        "source_origin": "fake-source:{}".format(seat_id),
        "source_tier": 1,
        "published_at_utc": CARD_STAMP,
        "retrieved_at_utc": CARD_STAMP,
        "excerpt": "close 68,420",
        "credibility_note": "測試資料，不是真實市場證據。",
    }


def envelope_text(run_id, seat_id, attempt_id, cards=None):
    return json.dumps(
        {
            "seat_id": seat_id,
            "evidence_cards": (
                [evidence_card(run_id, seat_id, attempt_id)] if cards is None else cards
            ),
        },
        ensure_ascii=False,
    )


class FakeSeatRunner:
    """Answers seats offline: local seats via the queue, Codex seats via inbox."""

    def __init__(
        self,
        run,
        data_root,
        results_queue,
        queue_seats,
        inbox_seats=(),
        cards_for=None,
    ):
        self.run = run
        self.data_root = data_root
        self.results_queue = results_queue
        self.queue_seats = set(queue_seats)
        self.inbox_seats = set(inbox_seats)
        self.cards_for = cards_for or (lambda run_id, attempt: None)
        self.started = []
        self.shutdown_calls = []

    def start(self, attempt, checkpoint):
        self.started.append(attempt.attempt_id)
        raw = envelope_text(
            self.run.run_id,
            attempt.seat_id,
            attempt.attempt_id,
            self.cards_for(self.run.run_id, attempt),
        )
        if attempt.seat_id in self.queue_seats:
            self.results_queue.put(("result", attempt.attempt_id, raw))
        elif attempt.seat_id in self.inbox_seats:
            write_seat_result(
                self.data_root,
                self.run.run_id,
                attempt.seat_id,
                attempt.attempt_id,
                raw,
            )
        return True

    def checkpoint(self, attempt_id):
        return None

    def correct(self, attempt, raw_output, exact_error):
        return None

    def cancel(self, attempt_id):
        return None

    def terminate(self, attempt_id):
        return None

    def shutdown(self, wait=True):
        self.shutdown_calls.append(wait)


class AdvancingSleeper:
    """The only thing that moves time forward in these tests."""

    def __init__(self, clock, step_ms=30_000):
        self.clock = clock
        self.step_ms = step_ms
        self.calls = 0

    def __call__(self, seconds):
        self.calls += 1
        self.clock.advance_ms(self.step_ms)


class FakePropositionResult:
    def __init__(self, structured_output):
        self.structured_output = structured_output


class FakePropositionAdapter:
    """Stands in for ``CodexExecAdapter`` on the proposition-writing call."""

    def __init__(self, structured_output=None, error=None):
        self.structured_output = structured_output
        self.error = error
        self.calls = []

    def invoke(self, prompt, schema, work_dir, allow_search=True):
        self.calls.append(
            {
                "prompt": prompt,
                "schema": schema,
                "work_dir": Path(work_dir),
                "allow_search": allow_search,
            }
        )
        if self.error is not None:
            raise self.error
        return FakePropositionResult(self.structured_output)


class RecordingLiveStarter:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def __call__(self, data_root, run_id):
        self.calls.append((Path(data_root), run_id))
        if self.error is not None:
            raise self.error
        return None


class Stream:
    """A minimal text sink that records every emitted line in order."""

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


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name) / "data"
        self.data_root.mkdir()
        self.clock = FixedClock()
        self.sleeper = AdvancingSleeper(self.clock)
        self.live_starter = RecordingLiveStarter()
        self.out = Stream()
        self.err = Stream()
        self.runner = None
        self.factory_options = {}
        self.proposition_adapter = FakePropositionAdapter(DEFAULT_PROPOSITION)
        # 席位交回來的證據卡標的：預設五幣時代的 BTC，開放標的題各自指定。
        self.card_asset = "BTC"

    # ---------- fixtures ----------

    def write_certificate(self, generated_at_utc=CERTIFICATE_STAMP):
        manifest = {
            "schema_version": "1.0.0",
            "status": "READY",
            "provider_capabilities_ready": True,
            "generated_at_utc": generated_at_utc,
        }
        manifest_path = (
            self.data_root / "preflight" / PREFLIGHT_ID / "manifest.json"
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        write_ready_certificate(self.data_root, PREFLIGHT_ID, manifest, manifest_path)
        return manifest_path

    def certificate_path(self):
        return self.data_root / "preflight" / "latest-ready.json"

    def default_cards_for(self, run_id, attempt):
        return [
            evidence_card(run_id, attempt.seat_id, attempt.attempt_id, self.card_asset)
        ]

    def runner_factory(self, queue_seats=LOCAL_SEAT_IDS, inbox_seats=(), cards_for=None):
        cards_for = cards_for or self.default_cards_for

        def factory(*, run, data_root, results_queue, **options):
            self.factory_options = options
            self.runner = FakeSeatRunner(
                run, data_root, results_queue, queue_seats, inbox_seats, cards_for
            )
            return self.runner

        return factory

    def launch(self, question=QUESTION, **overrides):
        options = {
            "clock": self.clock,
            "token_source": ScriptedTokenSource(["abc123"]),
            "runner_factory": self.runner_factory(),
            "live_starter": self.live_starter,
            "sleeper": self.sleeper,
            "proposition_adapter": self.proposition_adapter,
            "out": self.out,
            "err": self.err,
            "phase": PHASE_RESEARCH,
        }
        options.update(overrides)
        return run_launch(question, self.data_root, **options)

    def question_record(self):
        return json.loads((self.run_dir() / "question.json").read_text(encoding="utf-8"))

    def handshake(self):
        return json.loads(self.out.lines[0])

    def sealed(self):
        return json.loads(self.out.lines[-1])

    def run_dir(self):
        return Path(self.handshake()["run_dir"])

    # ---------- happy path ----------

    def test_happy_path_emits_handshake_first_then_seals_at_five_minutes(self):
        self.write_certificate()

        code = self.launch()

        self.assertEqual(code, 0, self.err.text)
        handshake = self.handshake()
        self.assertEqual(handshake["status"], "LAUNCHED")
        self.assertEqual(handshake["run_id"], "20260314T015926Z-btc-abc123")
        self.assertEqual(handshake["live_url"], LIVE_URL)
        # ADR 0005：run 目錄按台北日期分層，名字是給人看的 HHMM-題目-hash。
        # 注入時鐘停在 2026-03-14T01:59:26Z，台北時間是同日 09:59；結尾雜湊
        # 由 `printf %s <run_id> | sha256sum | cut -c1-16` 取得。
        run_dir = Path(handshake["run_dir"])
        self.assertTrue(run_dir.is_dir())
        self.assertEqual(self.data_root / "runs" / "2026-03-14", run_dir.parent)
        self.assertTrue(run_dir.name.startswith("0959-"), run_dir.name)
        self.assertTrue(run_dir.name.endswith("-ca3de1ec8607db5e"), run_dir.name)
        self.assertEqual(
            handshake["inbox_dir"],
            str(self.data_root / "inbox" / handshake["run_id"]),
        )
        self.assertEqual(
            [seat["seat_id"] for seat in handshake["codex_seats"]],
            list(CODEX_SEAT_IDS),
        )
        self.assertEqual(
            [seat["attempt_id"] for seat in handshake["codex_seats"]],
            ["{}-a1".format(seat_id) for seat_id in CODEX_SEAT_IDS],
        )

        seal = json.loads(
            (self.run_dir() / "snapshots" / "evidence.snapshot.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(seal["elapsed_ms"], SEAL_MS)
        self.assertEqual(seal["record_count"], len(LOCAL_SEAT_IDS))
        self.assertTrue((self.run_dir() / "snapshots" / "evidence.jsonl").is_file())

        evidence_lines = (
            (self.run_dir() / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        )
        self.assertEqual(len(evidence_lines), len(LOCAL_SEAT_IDS))

        sealed = self.sealed()
        self.assertEqual(sealed["status"], "SEALED")
        self.assertEqual(sealed["run_id"], handshake["run_id"])
        self.assertEqual(sealed["evidence_snapshot_sha256"], seal["sha256"])
        self.assertEqual(sorted(sealed["adopted_seat_ids"]), sorted(LOCAL_SEAT_IDS))
        self.assertEqual(sealed["exhausted_seat_ids"], [])
        self.assertEqual(self.runner.shutdown_calls, [False])

    def test_codex_prompt_files_carry_the_byte_identical_shared_section(self):
        self.write_certificate()

        self.launch()

        package = build_question_package(QUESTION).with_proposition(
            DEFAULT_PROPOSITION["proposition"]
        )
        seats = {seat.seat_id: seat for seat in load_roster()}
        shared = build_seat_prompt(package, seats["news"], "research").shared_section
        for seat in self.handshake()["codex_seats"]:
            prompt_path = Path(seat["prompt_path"])
            self.assertTrue(prompt_path.is_file(), prompt_path)
            # Read raw bytes: universal-newline translation would hide whether
            # the shared section reached the inbox byte for byte.
            text = prompt_path.read_bytes().decode("utf-8")
            self.assertTrue(text.startswith(shared))
            self.assertIn(seat["attempt_id"], text)

    def test_handshake_declares_the_default_codex_cli_channel(self):
        self.write_certificate()

        self.launch()

        self.assertEqual("cli", self.handshake()["codex_mode"])
        self.assertEqual("cli", self.factory_options["codex_mode"])

    def test_handshake_declares_the_question_type(self):
        self.write_certificate()

        self.launch()

        self.assertEqual("single_asset_market_state", self.handshake()["question_type"])

    def test_a_two_asset_comparison_seals_thirty_seconds_later(self):
        """Ticket R7: 比較題的研究窗到 T+4:30，其他題型維持 T+4:00。"""
        self.write_certificate()

        code = self.launch(question="比較 BTC 與 ETH 過去 14 日的相對強弱")

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual("two_asset_comparison", self.handshake()["question_type"])
        seal = json.loads(
            (self.run_dir() / "snapshots" / "evidence.snapshot.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(270_000, research_deadlines("two_asset_comparison").seal_ms)
        self.assertEqual(seal["elapsed_ms"], 270_000)
        self.assertEqual(seal["record_count"], len(LOCAL_SEAT_IDS))

    # ---------- open market intake ----------

    def test_every_question_type_gets_its_proposition_written(self):
        """命題訂定是主路徑：核准題型也要拿到正方／反方詞彙。"""
        self.write_certificate()

        self.launch()

        [call] = self.proposition_adapter.calls
        self.assertFalse(call["allow_search"])
        self.assertIn(QUESTION, call["prompt"])
        self.assertIn("偏多", call["prompt"])
        self.assertIn("偏空", call["prompt"])

        question = self.question_record()
        self.assertEqual("single_asset_market_state", question["question_type"])
        self.assertEqual(DEFAULT_PROPOSITION["proposition"], question["proposition"])
        self.assertEqual(
            dict(DEFAULT_PROPOSITION, source="codex"), question["open_proposition"]
        )

    def test_a_stated_subject_beats_the_questions_own_wording(self):
        """A menu-driven caller states the run's subject; the text does not.

        「台積電回購 50000 股…」 is a standard buyback question that the text
        reader gets wrong — it names the share count 50000 as a Taiwan listing,
        and the gateway then rejects every card about 2330. Stating the subject
        removes the parser from that decision entirely, and the run binds to
        2330 end to end: slug, ``question.json`` and the evidence gateway.
        """
        self.write_certificate()
        buyback = "台積電回購 50000 股後股價會不會上漲？"
        adapter = FakePropositionAdapter(
            {
                "proposition": "2330 未來七天股價將上漲。",
                "affirmative_means": "認為 2330 未來七天會漲。",
                "negative_means": "認為 2330 未來七天不會漲。",
            }
        )

        self.card_asset = "2330"
        code = self.launch(
            question=buyback,
            proposition_adapter=adapter,
            assets=("2330",),
            asset_class="tw_stock",
        )

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual("20260314T015926Z-2330-abc123", self.handshake()["run_id"])
        question = self.question_record()
        self.assertEqual(buyback, question["question"])
        self.assertEqual(["2330"], question["assets"])
        self.assertEqual("tw_stock", question["asset_class"])

    def test_stating_no_subject_leaves_the_launch_reading_unchanged(self):
        self.write_certificate()

        code = self.launch(assets=None, asset_class=None)

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual("20260314T015926Z-btc-abc123", self.handshake()["run_id"])
        self.assertEqual(["BTC"], self.question_record()["assets"])

    def test_a_stated_subject_that_cannot_describe_a_run_is_rejected(self):
        """Intake refuses it, so no run directory is ever started.

        Each of these used to get as far as building a run id. The long one
        then failed at ``mkdir`` with a filesystem error and exit 1, after the
        run already had a name; the set produced a different name in each
        process.
        """
        self.write_certificate()

        for stated in (
            ("../etc/passwd",),
            {"NVDA", "AAPL"},
            ("NVDA", "nvda"),
            ("A" * 10000,),
            tuple("AB{:02d}".format(index) for index in range(100)),
            "2330",
            2330,
        ):
            with self.subTest(stated=type(stated).__name__):
                code = self.launch(assets=stated)

                self.assertEqual(2, code)
                self.assertIn("啟動遭拒", self.err.text)
                self.assertEqual([], sorted(p.name for p in self.runs_root().iterdir()))

    def runs_root(self):
        root = self.data_root / "runs"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_taiwan_listing_is_accepted_and_recorded_with_its_class(self):
        self.write_certificate()
        adapter = FakePropositionAdapter(
            {
                "proposition": "2330 未來七天股價將上漲。",
                "affirmative_means": "認為 2330 未來七天會漲。",
                "negative_means": "認為 2330 未來七天不會漲。",
            }
        )

        self.card_asset = "2330"
        code = self.launch(question=TW_STOCK_QUESTION, proposition_adapter=adapter)

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual(sorted(self.sealed()["adopted_seat_ids"]), sorted(LOCAL_SEAT_IDS))
        self.assertEqual("20260314T015926Z-2330-abc123", self.handshake()["run_id"])
        question = self.question_record()
        self.assertEqual(TW_STOCK_QUESTION, question["question"])
        self.assertEqual("tw_stock", question["asset_class"])
        self.assertEqual(["2330"], question["assets"])
        self.assertEqual(7, question["period_days"])
        self.assertEqual("2330 未來七天股價將上漲。", question["proposition"])
        self.assertEqual(
            {
                "proposition": "2330 未來七天股價將上漲。",
                "affirmative_means": "認為 2330 未來七天會漲。",
                "negative_means": "認為 2330 未來七天不會漲。",
                "source": "codex",
            },
            question["open_proposition"],
        )

    def test_us_listing_is_accepted_and_recorded_with_its_class(self):
        self.write_certificate()

        self.card_asset = "NVDA"
        code = self.launch(question=US_STOCK_QUESTION)

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual("20260314T015926Z-nvda-abc123", self.handshake()["run_id"])
        question = self.question_record()
        self.assertEqual("us_stock", question["asset_class"])
        self.assertEqual(["NVDA"], question["assets"])

    def test_share_class_listing_adopts_cards_spelled_the_other_way(self):
        """BRK.B／brk-b 是同一檔股票；拼法不同不該讓整場零證據。"""
        self.write_certificate()

        self.card_asset = "brk-b"
        code = self.launch(question="BRK.B 這檔美股未來七天股價會不會漲")

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual("20260314T015926Z-brk-b-abc123", self.handshake()["run_id"])
        self.assertEqual(sorted(self.sealed()["adopted_seat_ids"]), sorted(LOCAL_SEAT_IDS))
        question = self.question_record()
        self.assertEqual("us_stock", question["asset_class"])
        self.assertEqual(["BRK.B"], question["assets"])

    def test_a_bare_lower_case_legacy_coin_launches_exactly_as_before(self):
        self.write_certificate()

        code = self.launch(question="btc 會不會漲")

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual("20260314T015926Z-btc-abc123", self.handshake()["run_id"])
        question = self.question_record()
        self.assertEqual("crypto", question["asset_class"])
        self.assertEqual(["BTC"], question["assets"])

    def test_coin_outside_the_old_whitelist_is_accepted(self):
        self.write_certificate()

        self.card_asset = "DOGE"
        code = self.launch(question=UNLISTED_COIN_QUESTION)

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual("20260314T015926Z-doge-abc123", self.handshake()["run_id"])
        question = self.question_record()
        self.assertEqual("crypto", question["asset_class"])
        self.assertEqual(["DOGE"], question["assets"])

    def test_a_question_naming_no_asset_still_launches(self):
        self.write_certificate()

        self.card_asset = "美國聯邦資金利率"
        code = self.launch(question=NO_ASSET_QUESTION)

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual(sorted(self.sealed()["adopted_seat_ids"]), sorted(LOCAL_SEAT_IDS))
        self.assertEqual(
            "20260314T015926Z-overall-market-abc123", self.handshake()["run_id"]
        )
        question = self.question_record()
        self.assertEqual("open", question["asset_class"])
        self.assertEqual([], question["assets"])
        self.assertEqual("open_proposition", question["question_type"])
        self.assertIn("題目未指名特定標的", self.proposition_adapter.calls[0]["prompt"])

    def test_open_question_is_turned_into_one_votable_proposition(self):
        self.write_certificate()
        adapter = FakePropositionAdapter(
            {
                "proposition": "美國比特幣戰略儲備法案將推升 BTC 價格。",
                "affirmative_means": "認為法案會推升 BTC 價格。",
                "negative_means": "認為法案不會推升 BTC 價格。",
            }
        )

        code = self.launch(question=OPEN_QUESTION, proposition_adapter=adapter)

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual("open_proposition", self.handshake()["question_type"])
        [call] = adapter.calls
        self.assertFalse(call["allow_search"])
        self.assertIn(OPEN_QUESTION, call["prompt"])
        self.assertEqual(PROPOSITION_SCHEMA, call["schema"])

        question = json.loads(
            (self.run_dir() / "question.json").read_text(encoding="utf-8")
        )
        self.assertEqual("open_proposition", question["question_type"])
        self.assertEqual("美國比特幣戰略儲備法案將推升 BTC 價格。", question["proposition"])
        self.assertEqual(
            {
                "affirmative": "正方",
                "negative_side": "反方",
                "undecided": "無法決定",
            },
            question["stance_labels"],
        )
        self.assertEqual(
            {
                "proposition": "美國比特幣戰略儲備法案將推升 BTC 價格。",
                "affirmative_means": "認為法案會推升 BTC 價格。",
                "negative_means": "認為法案不會推升 BTC 價格。",
                "source": "codex",
            },
            question["open_proposition"],
        )

    def test_written_proposition_and_labels_reach_the_codex_prompts(self):
        self.write_certificate()
        adapter = FakePropositionAdapter(
            {
                "proposition": "美國比特幣戰略儲備法案將推升 BTC 價格。",
                "affirmative_means": "認為法案會推升 BTC 價格。",
                "negative_means": "認為法案不會推升 BTC 價格。",
            }
        )

        self.launch(question=OPEN_QUESTION, proposition_adapter=adapter)

        for seat in self.handshake()["codex_seats"]:
            text = Path(seat["prompt_path"]).read_bytes().decode("utf-8")
            self.assertIn("美國比特幣戰略儲備法案將推升 BTC 價格。", text)
            self.assertIn("無法決定", text)

    def test_failed_proposition_call_degrades_honestly_without_blocking_launch(self):
        self.write_certificate()
        adapter = FakePropositionAdapter(error=CodexExecTimeout("codex exec 超過 60 秒"))

        code = self.launch(question=OPEN_QUESTION, proposition_adapter=adapter)

        self.assertEqual(code, 0, self.err.text)
        self.assertIn("命題撰寫", self.err.text)
        question = json.loads(
            (self.run_dir() / "question.json").read_text(encoding="utf-8")
        )
        self.assertEqual(OPEN_QUESTION, question["proposition"])
        self.assertEqual(
            {
                "proposition": OPEN_QUESTION,
                "affirmative_means": DEGRADED_AFFIRMATIVE_MEANS,
                "negative_means": DEGRADED_NEGATIVE_MEANS,
                "source": "degraded",
            },
            question["open_proposition"],
        )
        self.assertEqual("open_proposition", self.handshake()["question_type"])

    def test_incomplete_proposition_output_degrades_like_a_failed_call(self):
        self.write_certificate()
        adapter = FakePropositionAdapter({"proposition": "   ", "negative_means": "反對"})

        code = self.launch(question=OPEN_QUESTION, proposition_adapter=adapter)

        self.assertEqual(code, 0, self.err.text)
        question = json.loads(
            (self.run_dir() / "question.json").read_text(encoding="utf-8")
        )
        self.assertEqual(OPEN_QUESTION, question["proposition"])
        self.assertEqual("degraded", question["open_proposition"]["source"])

    def test_default_proposition_adapter_is_a_sealed_sixty_second_codex_call(self):
        adapter = default_proposition_adapter()

        self.assertEqual(CODEX_MODEL, adapter.model)
        self.assertEqual(60, PROPOSITION_TIMEOUT_SECONDS)
        self.assertEqual(PROPOSITION_TIMEOUT_SECONDS, adapter.timeout_seconds)
        self.assertEqual(
            ["affirmative_means", "negative_means", "proposition"],
            sorted(PROPOSITION_SCHEMA["required"]),
        )

    def test_inbox_codex_mode_reaches_both_the_handshake_and_the_runner(self):
        self.write_certificate()

        code = self.launch(codex_mode="inbox")

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual("inbox", self.handshake()["codex_mode"])
        self.assertEqual("inbox", self.factory_options["codex_mode"])
        # The inbox prompts are written in either mode: cli mode audits them,
        # inbox mode needs them as the human fallback.
        for seat in self.handshake()["codex_seats"]:
            self.assertTrue(Path(seat["prompt_path"]).is_file(), seat["prompt_path"])

    def test_launch_summary_reports_certificate_seats_and_event_counts(self):
        self.write_certificate()

        self.launch()

        summary = json.loads(
            (self.run_dir() / "diagnostics" / "launch-summary.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(summary["system_preflight_id"], PREFLIGHT_ID)
        self.assertEqual(
            summary["ready_certificate"]["manifest_path"],
            "preflight/{}/manifest.json".format(PREFLIGHT_ID),
        )
        self.assertEqual(summary["sealed_elapsed_ms"], SEAL_MS)
        self.assertEqual([seat["seat_id"] for seat in summary["seats"]], list(SEAT_IDS))
        adopted = {seat["seat_id"] for seat in summary["seats"] if seat["adopted"]}
        self.assertEqual(adopted, set(LOCAL_SEAT_IDS))
        self.assertEqual(
            summary["event_counts"]["first_valid_result_adopted"], len(LOCAL_SEAT_IDS)
        )
        self.assertEqual(summary["event_counts"]["evidence_snapshot_sealed"], 1)

    def test_handshake_file_matches_the_first_stdout_line(self):
        self.write_certificate()
        handshake_file = Path(self._tmp.name) / "handshake" / "hoya-launch.json"

        code = self.launch(handshake_path=handshake_file)

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual(
            handshake_file.read_text(encoding="utf-8").strip(), self.out.lines[0]
        )

    # ---------- refusals ----------

    def test_an_unreadable_question_exits_two_without_creating_a_run(self):
        """開放標的後唯一還會被拒的，是連 run 都無法定義的題目。"""
        self.write_certificate()

        code = self.launch(question="   ")

        self.assertEqual(code, 2)
        self.assertIn("啟動遭拒", self.err.text)
        self.assertEqual(self.out.text, "")
        self.assertFalse((self.data_root / "runs").exists())

    def test_missing_ready_certificate_exits_two_without_creating_a_run(self):
        code = self.launch()

        self.assertEqual(code, 2)
        self.assertIn("找不到有效的 READY 憑證", self.err.text)
        self.assertFalse((self.data_root / "runs").exists())

    def test_manifest_hash_mismatch_exits_two_without_creating_a_run(self):
        manifest_path = self.write_certificate()
        manifest_path.write_text("{}\n", encoding="utf-8")

        code = self.launch()

        self.assertEqual(code, 2)
        self.assertIn("manifest_sha256", self.err.text)
        self.assertFalse((self.data_root / "runs").exists())

    def test_certificate_without_provider_capabilities_exits_two(self):
        target = self.certificate_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "status": "READY",
                    "system_preflight_id": PREFLIGHT_ID,
                    "manifest_path": "preflight/{}/manifest.json".format(PREFLIGHT_ID),
                    "manifest_sha256": "0" * 64,
                    "generated_at_utc": CERTIFICATE_STAMP,
                    "provider_capabilities_ready": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        code = self.launch()

        self.assertEqual(code, 2)
        self.assertIn("provider_capabilities_ready", self.err.text)
        self.assertFalse((self.data_root / "runs").exists())

    def test_stale_certificate_only_advises_and_still_launches(self):
        self.write_certificate(generated_at_utc=STALE_CERTIFICATE_STAMP)

        code = self.launch()

        self.assertEqual(code, 0, self.err.text)
        self.assertIn("已超過 12 小時", self.err.text)
        self.assertEqual(self.sealed()["status"], "SEALED")

    # ---------- degraded but honest ----------

    def test_live_server_failure_never_blocks_seat_dispatch(self):
        self.write_certificate()
        self.live_starter = RecordingLiveStarter(error=OSError("port 8765 busy"))

        code = self.launch(live_starter=self.live_starter)

        self.assertEqual(code, 0, self.err.text)
        self.assertIn("即時儀表板未能啟動", self.err.text)
        [(called_root, called_run_id)] = self.live_starter.calls
        self.assertEqual(called_root, self.data_root)
        self.assertEqual(
            called_run_id, json.loads(self.out.text.splitlines()[0])["run_id"]
        )
        for seat_id in SEAT_IDS:
            self.assertIn("{}-a1".format(seat_id), self.runner.started)
        self.assertEqual(sorted(self.sealed()["adopted_seat_ids"]), sorted(LOCAL_SEAT_IDS))

    def test_no_live_skips_the_dashboard_entirely(self):
        self.write_certificate()

        code = self.launch(no_live=True)

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual(self.live_starter.calls, [])

    def test_codex_seat_result_arriving_through_the_inbox_is_adopted(self):
        self.write_certificate()
        codex_seat = CODEX_SEAT_IDS[0]

        code = self.launch(
            runner_factory=self.runner_factory(inbox_seats=(codex_seat,)),
        )

        self.assertEqual(code, 0, self.err.text)
        adopted = json.loads(
            (self.run_dir() / "agents" / codex_seat / "adopted.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(adopted["attempt_id"], "{}-a1".format(codex_seat))
        self.assertIn(codex_seat, self.sealed()["adopted_seat_ids"])
        evidence_lines = (
            (self.run_dir() / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        )
        self.assertEqual(len(evidence_lines), len(LOCAL_SEAT_IDS) + 1)

    def test_silent_seats_are_reported_honestly_and_still_exit_zero(self):
        self.write_certificate()

        code = self.launch(runner_factory=self.runner_factory(queue_seats=()))

        self.assertEqual(code, 0, self.err.text)
        sealed = self.sealed()
        self.assertEqual(sealed["adopted_seat_ids"], [])
        self.assertEqual(sealed["evidence_record_count"], 0)
        summary = json.loads(
            (self.run_dir() / "diagnostics" / "launch-summary.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(all(seat["adopted"] is False for seat in summary["seats"]))

    def test_cards_from_another_run_are_never_adopted(self):
        self.write_certificate()

        def foreign_run_cards(run_id, attempt):
            return [
                evidence_card(
                    "20260314T015926Z-btc-other1", attempt.seat_id, attempt.attempt_id
                )
            ]

        code = self.launch(
            runner_factory=self.runner_factory(cards_for=foreign_run_cards)
        )

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual(self.sealed()["adopted_seat_ids"], [])
        self.assertEqual(self.sealed()["evidence_record_count"], 0)

    def test_cards_about_an_asset_outside_the_question_are_never_adopted(self):
        self.write_certificate()

        def foreign_asset_cards(run_id, attempt):
            return [
                evidence_card(run_id, attempt.seat_id, attempt.attempt_id, asset="XRP")
            ]

        code = self.launch(
            runner_factory=self.runner_factory(cards_for=foreign_asset_cards)
        )

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual(self.sealed()["adopted_seat_ids"], [])

    def test_an_empty_envelope_never_counts_as_an_adopted_seat(self):
        self.write_certificate()

        code = self.launch(
            runner_factory=self.runner_factory(cards_for=lambda run_id, attempt: [])
        )

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual(self.sealed()["adopted_seat_ids"], [])
        self.assertEqual(self.sealed()["evidence_record_count"], 0)

    def test_research_phase_stops_at_the_sealed_snapshot(self):
        self.write_certificate()

        code = self.launch()

        self.assertEqual(code, 0, self.err.text)
        self.assertEqual([line for line in self.out.lines], self.out.lines[:2])
        self.assertEqual(
            [json.loads(line)["status"] for line in self.out.lines],
            ["LAUNCHED", "SEALED"],
        )
        self.assertFalse((self.run_dir() / "manifest.json").exists())
        self.assertFalse((self.run_dir() / "votes.json").exists())

    def test_unknown_phase_is_refused_before_any_run_directory_exists(self):
        self.write_certificate()

        code = self.launch(phase="debate-only")

        self.assertEqual(code, 2)
        self.assertIn("phase 必須是", self.err.text)
        self.assertFalse((self.data_root / "runs").exists())

    def test_data_root_inside_code_root_exits_two_without_creating_a_run(self):
        forbidden_root = CODE_ROOT / "AI-agnets-debating-chamber_data"

        code = run_launch(
            QUESTION,
            forbidden_root,
            phase=PHASE_RESEARCH,
            clock=self.clock,
            token_source=ScriptedTokenSource(["abc123"]),
            runner_factory=self.runner_factory(),
            live_starter=self.live_starter,
            sleeper=self.sleeper,
            out=self.out,
            err=self.err,
            no_live=True,
        )

        self.assertEqual(code, 2)
        self.assertIn("Data Root", self.err.text)
        self.assertEqual(self.out.text, "")
        self.assertFalse(forbidden_root.exists())

    def test_runtime_failure_after_start_exits_one(self):
        self.write_certificate()

        def exploding_factory(**unused):
            raise RuntimeError("runner 建構失敗")

        code = self.launch(runner_factory=exploding_factory)

        self.assertEqual(code, 1)
        self.assertIn("啟動失敗", self.err.text)
        self.assertEqual(self.handshake()["status"], "LAUNCHED")

    # ---------- the dashboard launch no longer starts (Ticket 10) ----------

    def test_a_launch_with_no_hook_of_its_own_starts_no_process(self):
        """Watching is the resident web app's job; launch does not spawn one."""
        self.write_certificate()

        with mock.patch("subprocess.Popen") as popen:
            code = self.launch(live_starter=None)

        self.assertEqual(code, 0, self.err.text)
        popen.assert_not_called()
        self.assertFalse((self.data_root / "logs" / "live-server.log").exists())

    def test_a_hook_that_is_handed_in_is_still_called_once(self):
        """FP direction: the seam has to still work, or the two above prove nothing."""
        self.write_certificate()

        code = self.launch()

        self.assertEqual(code, 0, self.err.text)
        [(called_root, called_run_id)] = self.live_starter.calls
        self.assertEqual(self.data_root, called_root)
        self.assertEqual(json.loads(self.out.text.splitlines()[0])["run_id"], called_run_id)

    def test_the_handshake_names_the_page_a_reader_watches_on(self):
        self.write_certificate()

        self.launch()

        self.assertEqual(self.handshake()["live_url"], LIVE_URL)
        self.assertTrue(LIVE_URL.endswith("/live"))


class ReadyCertificateProblemTest(unittest.TestCase):
    """The sentence the web app shows before spawning is the launcher's own."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name) / "data"
        self.data_root.mkdir()

    def write_certificate(self):
        manifest = {
            "schema_version": "1.0.0",
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
        return path

    def test_a_data_root_with_a_valid_certificate_has_no_problem_to_report(self):
        self.write_certificate()

        self.assertIsNone(ready_certificate_problem(self.data_root))

    def test_a_missing_certificate_is_reported_and_names_the_file(self):
        problem = ready_certificate_problem(self.data_root)

        self.assertIsNotNone(problem)
        self.assertIn("latest-ready.json", problem)

    def test_a_certificate_that_is_not_ready_is_reported(self):
        self.write_certificate()
        path = self.data_root / "preflight" / "latest-ready.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["provider_capabilities_ready"] = False
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        self.assertIn("provider_capabilities_ready", ready_certificate_problem(self.data_root))

    def test_a_manifest_edited_after_the_certificate_was_written_is_reported(self):
        manifest_path = self.write_certificate()
        manifest_path.write_text("{}", encoding="utf-8")

        self.assertIn("fail closed", ready_certificate_problem(self.data_root))

    def test_it_is_the_same_sentence_a_launch_would_have_printed(self):
        err = Stream()

        run_launch(
            QUESTION,
            self.data_root,
            clock=FixedClock(),
            out=Stream(),
            err=err,
            phase=PHASE_RESEARCH,
        )

        self.assertIn(ready_certificate_problem(self.data_root), err.text)


if __name__ == "__main__":
    unittest.main()
