"""The seven fixed seats and the explicit record contracts they must satisfy."""

import unittest

from hoya_market_agents.contract_validator import (
    ContractViolationError,
    MAX_EVIDENCE_CARDS_PER_SEAT,
    validate_debate_turn,
    validate_evidence_card,
    validate_seat_evidence,
    validate_vote,
)
from hoya_market_agents.seats import SEAT_IDS, load_roster


def evidence_card(**overrides):
    card = {
        "evidence_id": "spot-technical-01",
        "run_id": "20260801T073000Z-btc-8f3a2c",
        "seat_id": "spot-technical",
        "attempt_id": "spot-technical-a1",
        "phase": "research",
        "created_at_utc": "2026-08-01T07:30:05Z",
        "elapsed_ms": 5000,
        "asset": "BTC",
        "category": "spot-price",
        "statement": "現貨收盤價較 14 日前上升 4.2%。",
        "direction": "support",
        "source_url": "https://fake.invalid/spot/btc",
        "source_tier": 1,
        "published_at_utc": "2026-08-01T07:00:00Z",
        "retrieved_at_utc": "2026-08-01T07:30:05Z",
        "excerpt": "close 68,420 / 14d ago 65,660",
        "credibility_note": "fake provider 產生的示範資料，不得作為市場依據。",
    }
    card.update(overrides)
    return card


def debate_turn(**overrides):
    turn = {
        "turn_id": "spot-technical-r1",
        "run_id": "20260801T073000Z-btc-8f3a2c",
        "seat_id": "spot-technical",
        "attempt_id": "spot-technical-a1",
        "phase": "debate",
        "created_at_utc": "2026-08-01T07:35:00Z",
        "elapsed_ms": 300000,
        "round": 1,
        "stance": "bullish",
        "public_reason": "價格結構與量能一致偏多。",
        "evidence_ids": ["spot-technical-01"],
        "responds_to": ["counter-evidence-r1"],
        "stance_change_reason": None,
    }
    turn.update(overrides)
    return turn


def vote(**overrides):
    record = {
        "run_id": "20260801T073000Z-btc-8f3a2c",
        "seat_id": "spot-technical",
        "attempt_id": "spot-technical-a1",
        "phase": "vote",
        "created_at_utc": "2026-08-01T07:36:00Z",
        "elapsed_ms": 360000,
        "round": 1,
        "stance": "bullish",
        "public_reason": "價格結構與量能一致偏多。",
        "evidence_ids": ["spot-technical-01"],
        "stance_change_reason": None,
    }
    record.update(overrides)
    return record


class SeatRosterTest(unittest.TestCase):
    def test_seven_fixed_seats_in_approved_order(self):
        self.assertEqual(
            (
                "spot-technical",
                "derivatives",
                "onchain",
                "official-events",
                "news",
                "social-macro",
                "counter-evidence",
            ),
            SEAT_IDS,
        )

    def test_roster_config_describes_every_seat(self):
        roster = load_roster()

        self.assertEqual(list(SEAT_IDS), [seat.seat_id for seat in roster])
        for seat in roster:
            self.assertTrue(seat.focus, "seat {} 缺少研究範圍".format(seat.seat_id))
            self.assertEqual(seat.seat_id, seat.output_dir)


class EvidenceCardContractTest(unittest.TestCase):
    def test_valid_card_passes(self):
        validate_evidence_card(evidence_card())

    def test_missing_audit_field_is_rejected(self):
        for field in ("run_id", "seat_id", "attempt_id", "phase", "created_at_utc", "elapsed_ms"):
            card = evidence_card()
            del card[field]
            with self.assertRaises(ContractViolationError) as caught:
                validate_evidence_card(card)
            self.assertIn(field, str(caught.exception))

    def test_unknown_seat_id_is_rejected(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_evidence_card(evidence_card(seat_id="rogue-seat"))
        self.assertIn("seat_id", str(caught.exception))

    def test_direction_enum_is_enforced(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_evidence_card(evidence_card(direction="probably-up"))
        self.assertIn("direction", str(caught.exception))

    def test_source_tier_outside_one_to_three_is_rejected(self):
        with self.assertRaises(ContractViolationError):
            validate_evidence_card(evidence_card(source_tier=4))

    def test_unsupported_asset_is_rejected(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_evidence_card(evidence_card(asset="DOGE"))
        self.assertIn("asset", str(caught.exception))

    def test_non_utc_timestamp_is_rejected(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_evidence_card(evidence_card(created_at_utc="2026-08-01 07:30:05+08:00"))
        self.assertIn("created_at_utc", str(caught.exception))

    def test_negative_elapsed_ms_is_rejected(self):
        with self.assertRaises(ContractViolationError):
            validate_evidence_card(evidence_card(elapsed_ms=-1))

    def test_all_violations_are_reported_together(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_evidence_card(evidence_card(direction="sideways", source_tier=9))
        message = str(caught.exception)
        self.assertIn("direction", message)
        self.assertIn("source_tier", message)


class SeatEvidenceCapTest(unittest.TestCase):
    def test_cap_is_eight_cards_per_seat(self):
        self.assertEqual(8, MAX_EVIDENCE_CARDS_PER_SEAT)

    def test_seat_may_submit_up_to_the_cap(self):
        cards = [evidence_card(evidence_id="spot-technical-{:02d}".format(i)) for i in range(8)]
        validate_seat_evidence("spot-technical", cards)

    def test_seat_exceeding_the_cap_is_rejected(self):
        cards = [evidence_card(evidence_id="spot-technical-{:02d}".format(i)) for i in range(9)]
        with self.assertRaises(ContractViolationError):
            validate_seat_evidence("spot-technical", cards)

    def test_duplicate_evidence_ids_are_rejected(self):
        cards = [evidence_card(), evidence_card()]
        with self.assertRaises(ContractViolationError) as caught:
            validate_seat_evidence("spot-technical", cards)
        self.assertIn("evidence_id", str(caught.exception))

    def test_card_belonging_to_another_seat_is_rejected(self):
        with self.assertRaises(ContractViolationError):
            validate_seat_evidence("news", [evidence_card()])


class PositionContractTest(unittest.TestCase):
    def test_valid_debate_turn_passes(self):
        validate_debate_turn(debate_turn())

    def test_debate_turn_requires_at_least_one_evidence_id(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_debate_turn(debate_turn(evidence_ids=[]))
        self.assertIn("evidence_ids", str(caught.exception))

    def test_debate_round_must_be_positive(self):
        with self.assertRaises(ContractViolationError):
            validate_debate_turn(debate_turn(round=0))

    def test_valid_vote_passes(self):
        validate_vote(vote())

    def test_vote_stance_enum_is_enforced(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_vote(vote(stance="maybe"))
        self.assertIn("stance", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
