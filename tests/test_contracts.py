"""Ticket #4 versioned contract behavior through public validators."""

import copy
import unittest
from types import SimpleNamespace

from hoya_market_agents.contract_validator import (
    CONTRACT_VERSION,
    ContractViolationError,
    validate_agent_position,
    validate_debate_turn,
    validate_evidence_card,
    validate_question_package,
    validate_report,
    validate_run_manifest,
    validate_vote,
)
from hoya_market_agents.provider_gateway import ProviderGateway
from hoya_market_agents.run_store import deduplicate_evidence
from hoya_market_agents.seats import load_roster
from tests.fakes import FixedClock


RUN_ID = "20260801T073000Z-btc-8f3a2c"
STAMP = "2026-08-01T07:30:05Z"


def evidence_card(**overrides):
    value = {
        "schema_version": CONTRACT_VERSION,
        "evidence_id": "spot-technical-01",
        "run_id": RUN_ID,
        "seat_id": "spot-technical",
        "attempt_id": "spot-technical-a1",
        "phase": "research",
        "created_at_utc": STAMP,
        "elapsed_ms": 5000,
        "asset": "BTC",
        "category": "spot-price",
        "statement": "BTC moved.",
        "direction": "support",
        "source_url": "https://example.invalid/btc",
        "source_tier": 1,
        "published_at_utc": STAMP,
        "retrieved_at_utc": STAMP,
        "excerpt": "close 100",
        "credibility_note": "primary source",
        "source_origin": "exchange:btc-close-20260801",
    }
    value.update(overrides)
    return value


def position(**overrides):
    value = {
        "schema_version": CONTRACT_VERSION,
        "run_id": RUN_ID,
        "seat_id": "spot-technical",
        "attempt_id": "spot-technical-a1",
        "phase": "position",
        "created_at_utc": STAMP,
        "elapsed_ms": 300000,
        "round": 1,
        "stance": "bullish",
        "public_reason": "Price and volume agree.",
        "evidence_ids": ["spot-technical-01"],
        "stance_change_reason": None,
    }
    value.update(overrides)
    return value


def report_contract(**overrides):
    value = {
        "schema_version": CONTRACT_VERSION,
        "run_id": RUN_ID,
        "question": "分析 BTC",
        "assets": ["BTC"],
        "period_days": 14,
        "provider_mode": "fake",
        "started_at_utc": STAMP,
        "generated_at_utc": STAMP,
        "confidence": {"icon": "white", "label": "unknown", "reason": "fake"},
        "conclusion": {"available": False, "reason": "fake"},
        "seat_count": 1,
        "tally": {"bullish": 1, "bearish": 0, "neutral": 0},
        "seats": [
            {
                "seat_id": "spot-technical",
                "attempt_id": "spot-a1",
                "stance": "bullish",
                "public_reason": "reason",
                "evidence_ids": ["spot-technical-01"],
            }
        ],
        "evidence": [evidence_card()],
        "debate": [],
        "scope_limits": ["fake"],
        "raw_records": [],
    }
    value.update(overrides)
    return value


class VersionedContractTest(unittest.TestCase):
    def test_every_contract_rejects_a_missing_or_unknown_schema_version(self):
        validators_and_values = (
            (validate_evidence_card, evidence_card()),
            (validate_agent_position, position()),
            (validate_vote, position(phase="vote")),
            (
                validate_debate_turn,
                position(phase="debate", turn_id="spot-r1", responds_to=[]),
            ),
        )
        for validator, value in validators_and_values:
            with self.subTest(validator=validator.__name__):
                missing = dict(value)
                missing.pop("schema_version")
                with self.assertRaises(ContractViolationError) as caught:
                    validator(missing)
                self.assertIn("schema_version", str(caught.exception))

                with self.assertRaises(ContractViolationError):
                    validator({**value, "schema_version": "99.0"})

    def test_question_package_checks_types_and_supported_assets(self):
        package = {
            "schema_version": CONTRACT_VERSION,
            "run_id": RUN_ID,
            "phase": "question",
            "created_at_utc": STAMP,
            "elapsed_ms": 0,
            "question": "分析 BTC 過去 14 日市場狀態",
            "question_type": "single_asset",
            "assets": ["BTC"],
            "period_days": 14,
        }
        self.assertIs(package, validate_question_package(package))

        with self.assertRaises(ContractViolationError) as caught:
            validate_question_package({**package, "assets": ["DOGE"], "period_days": "14"})
        self.assertIn("assets", str(caught.exception))
        self.assertIn("period_days", str(caught.exception))

    def test_bool_never_satisfies_an_integer_contract(self):
        cases = (
            (validate_evidence_card, evidence_card(source_tier=True), "source_tier"),
            (validate_evidence_card, evidence_card(elapsed_ms=True), "elapsed_ms"),
            (validate_agent_position, position(round=True), "round"),
        )
        for validator, value, field in cases:
            with self.subTest(field=field):
                with self.assertRaises(ContractViolationError) as caught:
                    validator(value)
                self.assertIn(field, str(caught.exception))

    def test_evidence_requires_canonical_source_origin(self):
        card = evidence_card()
        card.pop("source_origin")

        with self.assertRaises(ContractViolationError) as caught:
            validate_evidence_card(card)

        self.assertIn("source_origin", str(caught.exception))

    def test_position_vote_and_debate_reject_unknown_evidence_ids(self):
        known = {"spot-technical-01"}
        agent_position = position()
        vote = position(phase="vote")
        self.assertIs(agent_position, validate_agent_position(agent_position, known))
        self.assertIs(vote, validate_vote(vote, known))
        debate = position(
            phase="debate", turn_id="spot-r1", responds_to=[], evidence_ids=["unknown"]
        )
        with self.assertRaises(ContractViolationError) as caught:
            validate_debate_turn(debate, known)
        self.assertIn("unknown", str(caught.exception))

    def test_manifest_checks_hash_index_shape(self):
        manifest = {
            "schema_version": CONTRACT_VERSION,
            "run_id": RUN_ID,
            "provider_mode": "fake",
            "question": "分析 BTC 過去 14 日市場狀態",
            "assets": ["BTC"],
            "period_days": 14,
            "started_at_utc": STAMP,
            "completed_at_utc": STAMP,
            "elapsed_ms": 100,
            "seats": [{"seat_id": "spot-technical", "attempt_ids": ["spot-a1"]}],
            "artifacts": {
                "evidence.jsonl": {
                    "path": "evidence.jsonl",
                    "sha256": "a" * 64,
                    "source": "validated seat attempts",
                }
            },
        }
        self.assertIs(manifest, validate_run_manifest(manifest))
        with self.assertRaises(ContractViolationError) as caught:
            validate_run_manifest(
                {**manifest, "artifacts": {"evidence.jsonl": {"sha256": "bad"}}}
            )
        self.assertIn("artifacts", str(caught.exception))

    def test_report_rejects_unknown_evidence_reference(self):
        report = report_contract()
        report["seats"][0]["evidence_ids"] = ["unknown"]
        with self.assertRaises(ContractViolationError) as caught:
            validate_report(report)
        self.assertIn("unknown", str(caught.exception))

    def test_report_nested_seat_fields_are_fail_closed(self):
        cases = (
            ("attempt_id", None, "attempt_id"),
            ("stance", "maybe", "stance"),
            ("evidence_ids", "spot-technical-01", "evidence_ids"),
            ("evidence_ids", ["unknown"], "unknown"),
        )
        for field, replacement, expected in cases:
            with self.subTest(field=field, replacement=replacement):
                report = copy.deepcopy(report_contract())
                if replacement is None:
                    report["seats"][0].pop(field)
                else:
                    report["seats"][0][field] = replacement
                with self.assertRaises(ContractViolationError) as caught:
                    validate_report(report)
                self.assertIn(expected, str(caught.exception))


class ProviderContractBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.seat = load_roster()[0]
        self.scope = SimpleNamespace(assets=("BTC",), period_days=14)

    def gateway(self, provider):
        return ProviderGateway(
            provider=provider,
            clock=FixedClock(),
            run_id=RUN_ID,
            start_monotonic_ms=0,
        )

    def test_missing_provider_field_becomes_contract_violation_not_key_error(self):
        class MissingFieldProvider:
            mode = "test"

            def research(self, call):
                return [{"asset": "BTC"}]

        with self.assertRaises(ContractViolationError) as caught:
            self.gateway(MissingFieldProvider()).collect_evidence(
                self.seat, self.scope, prompt=None
            )

        self.assertIn("source_origin", str(caught.exception))

    def test_gateway_preserves_origin_for_cross_url_syndication_dedupe(self):
        class SyndicatedProvider:
            mode = "test"

            def research(self, call):
                base = {
                    "asset": "BTC",
                    "category": "news",
                    "statement": "same release",
                    "direction": "support",
                    "source_tier": 2,
                    "published_at_utc": STAMP,
                    "excerpt": "same release",
                    "credibility_note": "syndicated",
                    "source_origin": "press-release:abc",
                }
                return [
                    {**base, "source_url": "https://wire.invalid/story"},
                    {**base, "source_url": "https://publisher.invalid/repost"},
                ]

        cards = self.gateway(SyndicatedProvider()).collect_evidence(
            self.seat, self.scope, prompt=None
        )
        unique, duplicates = deduplicate_evidence(cards)

        self.assertEqual("press-release:abc", cards[0]["source_origin"])
        self.assertEqual([cards[0]], unique)
        self.assertEqual(
            [{"evidence_id": cards[1]["evidence_id"], "duplicate_of": cards[0]["evidence_id"]}],
            duplicates,
        )


if __name__ == "__main__":
    unittest.main()
