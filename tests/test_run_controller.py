"""One CLI-level run: seven seats, merged artifacts, nothing overwritten."""

import json
import tempfile
import unittest
from pathlib import Path

from fakes import FixedClock, ScriptedTokenSource
from hoya_market_agents.contract_validator import (
    STANCES,
    validate_debate_turn,
    validate_evidence_card,
    validate_vote,
)
from hoya_market_agents.fake_provider import FakeProvider
from hoya_market_agents.question import UnsupportedQuestionError
from hoya_market_agents.run_controller import RunController
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.seats import SEAT_IDS

QUESTION = "分析 BTC 過去 14 日市場狀態"


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class RunControllerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.tokens = ScriptedTokenSource(["aaa111", "bbb222"])
        self.controller = RunController(
            store=RunStore(self.data_root),
            provider=FakeProvider(),
            clock=FixedClock(auto_advance_ms=250),
            token_source=self.tokens,
        )

    def execute(self, question=QUESTION):
        return self.controller.execute(question)

    def test_run_id_comes_from_the_injected_clock_and_token(self):
        result = self.execute()

        self.assertEqual("20260314T015926Z-btc-aaa111", result.run_id)
        self.assertEqual(self.data_root / "runs" / result.run_id, result.run_dir)

    def test_run_produces_the_shared_audit_artifacts(self):
        result = self.execute()

        for name in ("evidence.jsonl", "debate.jsonl", "votes.json", "manifest.json"):
            self.assertTrue((result.run_dir / name).is_file(), "缺少 {}".format(name))

    def test_every_seat_owns_its_own_directory(self):
        result = self.execute()

        for seat_id in SEAT_IDS:
            seat_dir = result.run_dir / "agents" / seat_id
            self.assertTrue((seat_dir / "research.jsonl").is_file())
            self.assertTrue((seat_dir / "debate.json").is_file())
            self.assertTrue((seat_dir / "vote.json").is_file())
            self.assertTrue((seat_dir / "prompt-research.txt").is_file())

    def test_merged_evidence_is_the_verbatim_concatenation_of_seat_files(self):
        result = self.execute()

        merged = read_jsonl(result.run_dir / "evidence.jsonl")
        per_seat = []
        for seat_id in SEAT_IDS:
            per_seat += read_jsonl(result.run_dir / "agents" / seat_id / "research.jsonl")

        self.assertEqual(per_seat, merged)

    def test_all_seven_seats_are_traceable_across_every_artifact(self):
        result = self.execute()

        evidence_seats = {card["seat_id"] for card in read_jsonl(result.run_dir / "evidence.jsonl")}
        debate_seats = {turn["seat_id"] for turn in read_jsonl(result.run_dir / "debate.jsonl")}
        votes = json.loads((result.run_dir / "votes.json").read_text(encoding="utf-8"))
        vote_seats = {record["seat_id"] for record in votes["votes"]}
        manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest_seats = {seat["seat_id"] for seat in manifest["seats"]}

        self.assertEqual(set(SEAT_IDS), evidence_seats)
        self.assertEqual(set(SEAT_IDS), debate_seats)
        self.assertEqual(set(SEAT_IDS), vote_seats)
        self.assertEqual(set(SEAT_IDS), manifest_seats)

    def test_every_record_satisfies_its_contract(self):
        result = self.execute()

        for card in read_jsonl(result.run_dir / "evidence.jsonl"):
            validate_evidence_card(card)
        for turn in read_jsonl(result.run_dir / "debate.jsonl"):
            validate_debate_turn(turn)
        votes = json.loads((result.run_dir / "votes.json").read_text(encoding="utf-8"))
        for record in votes["votes"]:
            validate_vote(record)

    def test_debate_turns_cite_evidence_that_exists_in_the_snapshot(self):
        result = self.execute()

        known = {card["evidence_id"] for card in read_jsonl(result.run_dir / "evidence.jsonl")}
        for turn in read_jsonl(result.run_dir / "debate.jsonl"):
            for evidence_id in turn["evidence_ids"]:
                self.assertIn(evidence_id, known)

    def test_votes_carry_one_vote_per_seat_and_a_tally(self):
        result = self.execute()

        votes = json.loads((result.run_dir / "votes.json").read_text(encoding="utf-8"))
        self.assertEqual(7, len(votes["votes"]))
        self.assertEqual(set(STANCES), set(votes["tally"]))
        self.assertEqual(7, sum(votes["tally"].values()))
        self.assertEqual(votes["tally"], result.tally)

    def test_records_use_the_injected_clock_and_increasing_elapsed_ms(self):
        result = self.execute()

        cards = read_jsonl(result.run_dir / "evidence.jsonl")
        elapsed = [card["elapsed_ms"] for card in cards]
        self.assertEqual(sorted(elapsed), elapsed)
        self.assertGreater(elapsed[-1], elapsed[0])
        for card in cards:
            self.assertTrue(card["created_at_utc"].startswith("2026-03-14T01:59:"))

    def test_manifest_records_provider_mode_hashes_and_scope(self):
        result = self.execute()

        manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("fake", manifest["provider_mode"])
        self.assertEqual(QUESTION, manifest["question"])
        self.assertEqual(["BTC"], manifest["assets"])
        self.assertEqual(14, manifest["period_days"])
        for name in ("evidence.jsonl", "debate.jsonl", "votes.json"):
            self.assertRegex(manifest["artifacts"][name]["sha256"], r"^[0-9a-f]{64}$")

    def test_second_run_gets_a_new_run_id_and_leaves_the_first_untouched(self):
        first = self.execute()
        first_evidence = (first.run_dir / "evidence.jsonl").read_bytes()

        second = self.execute()

        self.assertNotEqual(first.run_id, second.run_id)
        self.assertNotEqual(first.run_dir, second.run_dir)
        self.assertEqual(first_evidence, (first.run_dir / "evidence.jsonl").read_bytes())
        self.assertTrue((second.run_dir / "evidence.jsonl").is_file())

    def test_latest_pointer_follows_the_newest_run(self):
        self.execute()
        second = self.execute()

        latest = json.loads((self.data_root / "runs" / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(second.run_id, latest["run_id"])

    def test_unsupported_asset_fails_closed_without_creating_a_run(self):
        with self.assertRaises(UnsupportedQuestionError):
            self.execute("分析 DOGE 過去 14 日市場狀態")

        self.assertFalse((self.data_root / "runs").exists())

    def test_multi_asset_question_fails_closed_in_the_tracer_scope(self):
        with self.assertRaises(UnsupportedQuestionError) as caught:
            self.execute("比較 BTC 與 ETH 過去 14 日相對強弱")

        self.assertIn("單一資產", str(caught.exception))
        self.assertFalse((self.data_root / "runs").exists())


class FakeProviderTest(unittest.TestCase):
    def test_fake_provider_declares_itself_and_covers_every_seat(self):
        provider = FakeProvider()

        self.assertEqual("fake", provider.mode)
        self.assertEqual(set(SEAT_IDS), set(provider.seat_ids))

    def test_fake_provider_produces_a_minority_opinion(self):
        provider = FakeProvider()

        stances = {provider.stance_for(seat_id) for seat_id in SEAT_IDS}

        self.assertGreater(len(stances), 1, "fake run 必須保留少數意見才能測試報告揭露")


if __name__ == "__main__":
    unittest.main()
