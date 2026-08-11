"""Ticket 01: schema-v2 debate rules and legacy manifest snapshots."""

import json
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents.contract_validator import (
    _rules_document_digest,
    load_run_rules,
    run_rules_record,
)
from hoya_market_agents.debate_rules import (
    RULES_PATH,
    DebateRulesError,
    load_debate_rules,
)


def confidence_document():
    return {
        "light_scale": [
            {"min_votes": 7, "level": "blue"},
            {"min_votes": 6, "level": "green"},
            {"min_votes": 5, "level": "yellow"},
            {"min_votes": 4, "level": "orange"},
            {"min_votes": 0, "level": "red"},
        ],
        "downgrades": {
            "few_independent_domains": {
                "levels": 1,
                "min_independent_domains": 2,
            },
            "low_trust_source": {
                "levels": 1,
                "trusted_source_tiers": [1, 2],
                "exempt_seat_ids": ["social-macro"],
            },
        },
    }


def v2_document(rounds=None, final_settle_offset_ms=360_000):
    return {
        "schema_version": 2,
        "timeline": {
            "vote_rounds": rounds
            or [
                {"open_offset_ms": 60_000, "threshold": 7},
                {"open_offset_ms": 150_000, "threshold": 6},
                {"open_offset_ms": 240_000, "threshold": 5},
                {"open_offset_ms": 330_000, "threshold": 4},
            ],
            "final_settle_offset_ms": final_settle_offset_ms,
        },
        "confidence": confidence_document(),
    }


def v1_document():
    return {
        "schema_version": 1,
        "timeline_ms": {
            "debate_start": 240_000,
            "round_one_window": 180_000,
            "reduced_threshold_from": 480_000,
            "final_round_start": 525_000,
            "final_round_end": 585_000,
            "force_stop": 600_000,
        },
        "vote_thresholds": {
            "unanimous_blind_pass": 7,
            "initial": 6,
            "reduced": 5,
            "forced_stop": 4,
        },
        "confidence": confidence_document(),
    }


class RulesFileTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "debate_rules.json"

    def load(self, document):
        self.path.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
        return load_debate_rules(self.path)

    def refuse(self, document):
        with self.assertRaises(DebateRulesError) as caught:
            self.load(document)
        return str(caught.exception)


class ShippedV2RulesTest(RulesFileTestCase):
    def test_shipped_schedule_and_confidence_match_the_approved_document(self):
        document = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        rules = load_debate_rules(RULES_PATH)

        self.assertEqual(2, document["schema_version"])
        self.assertEqual(
            [(60_000, 7), (150_000, 6), (240_000, 5), (330_000, 4)],
            [(item.open_offset_ms, item.threshold) for item in rules.vote_rounds],
        )
        self.assertEqual(360_000, rules.final_settle_offset_ms)
        self.assertEqual(
            confidence_document()["light_scale"], document["confidence"]["light_scale"]
        )
        self.assertEqual(
            confidence_document()["downgrades"],
            {
                key: value
                for key, value in document["confidence"]["downgrades"].items()
                if not key.startswith("_")
            },
        )
        self.assertIn("_about", document["confidence"])

    def test_v1_names_are_only_computed_bridges_not_v2_rule_data(self):
        rules = load_debate_rules(RULES_PATH)

        self.assertEqual(
            {"vote_rounds", "final_settle_offset_ms", "confidence"},
            set(rules.__dataclass_fields__),
        )
        self.assertNotIn("initial_votes", vars(rules))
        self.assertNotIn("force_stop_ms", vars(rules))

    def test_thresholds_and_phases_are_derived_from_the_round_array(self):
        rules = self.load(
            v2_document(
                rounds=[
                    {"open_offset_ms": 10, "threshold": 7},
                    {"open_offset_ms": 20, "threshold": 5},
                    {"open_offset_ms": 30, "threshold": 2},
                ],
                final_settle_offset_ms=40,
            )
        )
        seal = rules.debate_start_ms

        self.assertEqual(7, rules.required_votes_at(seal + 9))
        self.assertEqual(7, rules.required_votes_at(seal + 10))
        self.assertEqual(5, rules.required_votes_at(seal + 20))
        self.assertEqual(2, rules.required_votes_at(seal + 30))
        self.assertEqual("before_vote_round_1", rules.phase_at(seal + 9))
        self.assertEqual("vote_round_1", rules.phase_at(seal + 10))
        self.assertEqual("vote_round_2", rules.phase_at(seal + 20))
        self.assertEqual("vote_round_3", rules.phase_at(seal + 30))
        self.assertEqual("final_settle", rules.phase_at(seal + 40))

    def test_the_same_offsets_shift_with_each_run_s_actual_seal(self):
        rules = self.load(v2_document())

        for seal in (240_000, 270_000):
            with self.subTest(seal_ms=seal):
                self.assertEqual(
                    7, rules.required_votes_at(seal + 149_999, seal_ms=seal)
                )
                self.assertEqual(
                    6, rules.required_votes_at(seal + 150_000, seal_ms=seal)
                )
                self.assertEqual(
                    "vote_round_4", rules.phase_at(seal + 330_000, seal_ms=seal)
                )
                self.assertEqual(
                    "final_settle", rules.phase_at(seal + 360_000, seal_ms=seal)
                )


class FailClosedV2RulesTest(RulesFileTestCase):
    def test_a_v1_config_is_rejected_as_a_version_mismatch(self):
        message = self.refuse(v1_document())

        self.assertIn("schema_version", message)
        self.assertIn("2", message)
        self.assertIn("1", message)

    def test_zero_rounds_are_rejected(self):
        document = v2_document()
        document["timeline"]["vote_rounds"] = []

        self.assertIn("vote_rounds", self.refuse(document))

    def test_offsets_must_be_strictly_increasing(self):
        document = v2_document()
        document["timeline"]["vote_rounds"][1]["open_offset_ms"] = 60_000

        message = self.refuse(document)
        self.assertIn("vote_rounds[1].open_offset_ms", message)
        self.assertIn("嚴格遞增", message)

    def test_thresholds_must_be_strictly_decreasing(self):
        document = v2_document()
        document["timeline"]["vote_rounds"][1]["threshold"] = 7

        message = self.refuse(document)
        self.assertIn("vote_rounds[1].threshold", message)
        self.assertIn("嚴格遞減", message)

    def test_final_settle_must_be_after_the_last_round(self):
        document = v2_document(final_settle_offset_ms=330_000)

        message = self.refuse(document)
        self.assertIn("final_settle_offset_ms", message)
        self.assertIn("vote_rounds[3].open_offset_ms", message)

    def test_unknown_keys_are_rejected_at_every_new_level(self):
        mutations = (
            lambda document: document.update({"vote_thresholds": {}}),
            lambda document: document["timeline"].update({"force_stop": 1}),
            lambda document: document["timeline"]["vote_rounds"][0].update(
                {"round": 1}
            ),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                document = v2_document()
                mutate(document)
                self.assertIn("未知欄位", self.refuse(document))


class ManifestRulesCompatibilityTest(unittest.TestCase):
    def test_new_snapshot_is_v2_and_round_trips(self):
        rules = load_debate_rules(RULES_PATH)
        record = run_rules_record(rules)

        self.assertEqual(2, record["document"]["schema_version"])
        self.assertEqual(rules, load_run_rules({"debate_rules": record}))

    def test_a_v1_manifest_snapshot_still_loads_with_its_original_semantics(self):
        document = v1_document()
        record = {
            "sha256": _rules_document_digest(document),
            "document": document,
        }

        rules = load_run_rules({"debate_rules": record})

        self.assertEqual(6, rules.required_votes_at(479_999))
        self.assertEqual(5, rules.required_votes_at(480_000))
        self.assertEqual(4, rules.required_votes_at(600_000))
        self.assertEqual("final_round", rules.phase_at(525_000))


if __name__ == "__main__":
    unittest.main()
