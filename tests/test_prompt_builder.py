"""Every seat must receive byte-identical shared context plus its own focus."""

import unittest

from hoya_market_agents.prompt_builder import build_seat_prompt
from hoya_market_agents.question import analyze_question
from hoya_market_agents.seats import load_roster


class PromptBuilderTest(unittest.TestCase):
    def setUp(self):
        self.scope = analyze_question("分析 BTC 過去 14 日市場狀態")
        self.roster = load_roster()

    def test_all_seats_share_a_byte_identical_shared_section(self):
        shared = {
            build_seat_prompt(self.scope, seat, "research").shared_section
            for seat in self.roster
        }

        self.assertEqual(1, len(shared))

    def test_shared_section_carries_question_assets_and_period(self):
        prompt = build_seat_prompt(self.scope, self.roster[0], "research")

        self.assertIn("分析 BTC 過去 14 日市場狀態", prompt.shared_section)
        self.assertIn("BTC", prompt.shared_section)
        self.assertIn("14", prompt.shared_section)

    def test_seat_section_carries_only_that_seats_focus(self):
        spot = build_seat_prompt(self.scope, self.roster[0], "research")
        counter = build_seat_prompt(self.scope, self.roster[6], "research")

        self.assertIn("現貨價格、成交量與技術結構", spot.seat_section)
        self.assertNotIn("現貨價格、成交量與技術結構", counter.seat_section)
        self.assertIn("反方證據與資料品質檢查", counter.seat_section)

    def test_prompt_text_is_shared_section_followed_by_seat_section(self):
        prompt = build_seat_prompt(self.scope, self.roster[0], "research")

        self.assertTrue(prompt.text.startswith(prompt.shared_section))
        self.assertTrue(prompt.text.endswith(prompt.seat_section))

    def test_debate_phase_shares_the_same_evidence_snapshot_for_every_seat(self):
        snapshot = [{"evidence_id": "news-01", "statement": "示範證據"}]

        shared = {
            build_seat_prompt(self.scope, seat, "debate", evidence_snapshot=snapshot).shared_section
            for seat in self.roster
        }

        self.assertEqual(1, len(shared))
        only = shared.pop()
        self.assertIn("news-01", only)

    def test_vote_phase_shares_evidence_and_debate_snapshots(self):
        evidence = [{"evidence_id": "news-01", "statement": "示範證據"}]
        debate = [{"turn_id": "news-r1", "seat_id": "news", "public_reason": "示範理由"}]

        prompt = build_seat_prompt(
            self.scope,
            self.roster[0],
            "vote",
            evidence_snapshot=evidence,
            debate_snapshot=debate,
        )

        self.assertIn("news-01", prompt.shared_section)
        self.assertIn("news-r1", prompt.shared_section)

    def test_unknown_phase_is_rejected(self):
        with self.assertRaises(ValueError):
            build_seat_prompt(self.scope, self.roster[0], "gossip")


if __name__ == "__main__":
    unittest.main()
