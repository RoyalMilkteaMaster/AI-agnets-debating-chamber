"""Ticket #4 versioned contract behavior through public validators."""

import unittest

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
        report = {
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
                    "evidence_ids": ["unknown"],
                }
            ],
            "evidence": [evidence_card()],
            "debate": [],
            "scope_limits": ["fake"],
            "raw_records": [],
        }
        with self.assertRaises(ContractViolationError) as caught:
            validate_report(report)
        self.assertIn("unknown", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
