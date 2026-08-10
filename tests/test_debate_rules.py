"""Ticket 02: config/debate_rules.json is the only debate rule authority."""

import ast
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from hoya_market_agents.seats import SEAT_IDS, load_roster
from hoya_market_agents.debate_rules import (
    RULES_PATH,
    DebateRulesError,
    debate_rules,
    load_debate_rules,
    reload_debate_rules,
)


class ShippedRulesTest(unittest.TestCase):
    """預設設定檔＝2026-08-02 核准時間表的等價搬移，行為零變化。"""

    def test_the_shipped_file_carries_the_approved_schedule(self):
        rules = debate_rules()

        self.assertEqual(240_000, rules.debate_start_ms)
        self.assertEqual(180_000, rules.round_one_window_ms)
        self.assertEqual(420_000, rules.challenge_deadline_ms())
        self.assertEqual(480_000, rules.reduced_threshold_from_ms)
        self.assertEqual(525_000, rules.final_round_start_ms)
        self.assertEqual(585_000, rules.final_round_end_ms)
        self.assertEqual(600_000, rules.force_stop_ms)

    def test_the_shipped_file_carries_the_approved_vote_ladder(self):
        rules = debate_rules()

        self.assertEqual(6, rules.initial_votes)
        self.assertEqual(5, rules.reduced_votes)
        self.assertEqual(4, rules.forced_stop_votes)

    def test_the_shipped_file_carries_the_blind_pass_threshold(self):
        # Ticket 03：7/7 盲投直過的門檻是設定值，不是程式裡的 len(SEAT_IDS)。
        rules = debate_rules()

        self.assertEqual(7, rules.unanimous_blind_pass_votes)

    def test_the_shipped_file_carries_the_adr_0003_light_scale(self):
        # Ticket 04：7藍／6綠／5黃／4橘／<4紅，五級一個都不能少。
        scale = debate_rules().confidence.light_scale

        self.assertEqual(
            [(step.min_votes, step.level) for step in scale],
            [(7, "blue"), (6, "green"), (5, "yellow"), (4, "orange"), (0, "red")],
        )

    def test_the_shipped_file_carries_the_two_adr_0003_downgrades(self):
        by_name = {rule.rule: rule for rule in debate_rules().confidence.downgrades}

        self.assertEqual(
            {"few_independent_domains", "low_trust_source"}, set(by_name)
        )
        self.assertEqual(1, by_name["few_independent_domains"].levels)
        self.assertEqual(2, by_name["few_independent_domains"].min_independent_domains)
        self.assertEqual((), by_name["few_independent_domains"].exempt_seat_ids)
        self.assertEqual(1, by_name["low_trust_source"].levels)
        self.assertEqual((1, 2), by_name["low_trust_source"].trusted_source_tiers)
        self.assertEqual(("social-macro",), by_name["low_trust_source"].exempt_seat_ids)

    def test_the_exempt_seat_is_the_one_the_roster_calls_social_macro(self):
        # 輿情席以 roster 的席位 ID 為準，設定檔不得自己發明一個字串。
        exempt = {
            seat_id
            for rule in debate_rules().confidence.downgrades
            for seat_id in rule.exempt_seat_ids
        }

        self.assertTrue(exempt)
        self.assertTrue(exempt.issubset({seat.seat_id for seat in load_roster()}))

    def test_the_authority_is_the_repository_config_file(self):
        self.assertEqual("debate_rules.json", RULES_PATH.name)
        self.assertEqual("config", RULES_PATH.parent.name)
        self.assertEqual(debate_rules(), load_debate_rules(RULES_PATH))


def valid_document():
    """A minimal legal document tests mutate one field at a time."""
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
        "confidence": {"light_scale": [], "downgrades": {}},
    }


def filled_confidence():
    """ADR 0003 的燈號新制，Ticket 04 會填的就是這一份。"""
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


class RulesVariantTestCase(unittest.TestCase):
    """Every fail-closed case loads a variant file from a temporary directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "debate_rules.json"

    def load(self, document):
        self.path.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
        return load_debate_rules(self.path)

    def refuse(self, document):
        with self.assertRaises(DebateRulesError) as caught:
            self.load(document)
        return str(caught.exception)


class MissingFieldTest(RulesVariantTestCase):
    """欄位缺漏必須拒絕啟動，而且錯誤訊息要指名是哪一個欄位。"""

    def test_a_legal_document_loads(self):
        rules = self.load(valid_document())

        self.assertEqual(600_000, rules.force_stop_ms)
        self.assertEqual(4, rules.forced_stop_votes)

    def test_comment_keys_are_allowed_beside_the_real_fields(self):
        document = valid_document()
        document["timeline_ms"]["_about"] = "JSON 沒有註解，底線鍵當註解用。"

        self.assertEqual(600_000, self.load(document).force_stop_ms)

    def test_a_missing_section_is_named(self):
        document = valid_document()
        del document["vote_thresholds"]

        self.assertIn("vote_thresholds", self.refuse(document))

    def test_a_missing_deadline_is_named(self):
        document = valid_document()
        del document["timeline_ms"]["force_stop"]

        self.assertIn("timeline_ms.force_stop", self.refuse(document))

    def test_a_missing_vote_step_is_named(self):
        document = valid_document()
        del document["vote_thresholds"]["reduced"]

        self.assertIn("vote_thresholds.reduced", self.refuse(document))

    def test_the_reserved_confidence_slots_must_be_present(self):
        document = valid_document()
        del document["confidence"]["light_scale"]

        self.assertIn("confidence.light_scale", self.refuse(document))

    def test_an_unknown_field_is_named_instead_of_silently_ignored(self):
        document = valid_document()
        document["timeline_ms"]["forceStop"] = 600_000

        message = self.refuse(document)
        self.assertIn("forceStop", message)

    def test_an_unknown_top_level_field_is_named_too(self):
        # 最外層與 section 套用同一條規則，否則改錯鍵名的人以為自己改到了。
        document = valid_document()
        document["time_line_ms"] = {"force_stop": 1}

        self.assertIn("time_line_ms", self.refuse(document))

    def test_a_missing_schema_version_is_named(self):
        document = valid_document()
        del document["schema_version"]

        self.assertIn("schema_version", self.refuse(document))

    def test_an_unsupported_schema_version_is_refused(self):
        document = valid_document()
        document["schema_version"] = 99

        self.assertIn("schema_version", self.refuse(document))

    def test_a_json_true_cannot_impersonate_schema_version_one(self):
        # Python 的 bool 是 int 的子型別，True == 1；只比值的話 true 會過關。
        document = valid_document()
        document["schema_version"] = True

        self.assertIn("schema_version", self.refuse(document))

    def test_a_float_cannot_impersonate_schema_version_one(self):
        document = valid_document()
        document["schema_version"] = 1.0

        self.assertIn("schema_version", self.refuse(document))

    def test_a_section_that_is_not_an_object_is_refused(self):
        # 斷言必須指名「形狀」錯誤：只檢查訊息含 vote_thresholds 的話，缺欄位
        # 的訊息也會誤綠，型別校驗被拿掉時測試不會紅。
        document = valid_document()
        document["vote_thresholds"] = [6, 5, 4]

        message = self.refuse(document)
        self.assertIn("vote_thresholds", message)
        self.assertIn("必須是 object", message)

    def test_a_document_that_is_not_an_object_is_refused(self):
        self.path.write_text("[1, 2, 3]", encoding="utf-8")

        with self.assertRaises(DebateRulesError) as caught:
            load_debate_rules(self.path)

        self.assertIn("最外層", str(caught.exception))

    def test_an_unreadable_file_names_the_path(self):
        missing = Path(self._tmp.name) / "absent.json"

        with self.assertRaises(DebateRulesError) as caught:
            load_debate_rules(missing)

        self.assertIn("absent.json", str(caught.exception))

    def test_malformed_json_names_the_path(self):
        self.path.write_text("{ not json", encoding="utf-8")

        with self.assertRaises(DebateRulesError) as caught:
            load_debate_rules(self.path)

        self.assertIn("debate_rules.json", str(caught.exception))


class TimelineOrderTest(RulesVariantTestCase):
    """時間非遞增必須拒絕啟動，並指名互相矛盾的那一對欄位。"""

    def test_a_final_round_that_ends_before_it_starts_is_refused(self):
        document = valid_document()
        document["timeline_ms"]["final_round_end"] = 500_000

        message = self.refuse(document)
        self.assertIn("timeline_ms.final_round_end", message)
        self.assertIn("timeline_ms.final_round_start", message)

    def test_a_forced_stop_before_the_final_round_is_refused(self):
        document = valid_document()
        document["timeline_ms"]["force_stop"] = 500_000

        message = self.refuse(document)
        self.assertIn("timeline_ms.force_stop", message)
        self.assertIn("timeline_ms.final_round_end", message)

    def test_a_threshold_drop_before_the_debate_starts_is_refused(self):
        document = valid_document()
        document["timeline_ms"]["reduced_threshold_from"] = 100_000

        message = self.refuse(document)
        self.assertIn("timeline_ms.reduced_threshold_from", message)
        self.assertIn("timeline_ms.debate_start", message)

    def test_two_walls_at_the_same_instant_are_refused(self):
        document = valid_document()
        document["timeline_ms"]["final_round_start"] = 585_000

        self.assertIn("timeline_ms.final_round_start", self.refuse(document))

    def test_a_zero_length_first_round_window_is_refused(self):
        document = valid_document()
        document["timeline_ms"]["round_one_window"] = 0

        self.assertIn("timeline_ms.round_one_window", self.refuse(document))

    def test_a_negative_deadline_is_refused(self):
        document = valid_document()
        document["timeline_ms"]["debate_start"] = -1

        self.assertIn("timeline_ms.debate_start", self.refuse(document))

    def test_a_deadline_that_is_not_a_whole_millisecond_is_refused(self):
        document = valid_document()
        document["timeline_ms"]["force_stop"] = 600_000.5

        self.assertIn("timeline_ms.force_stop", self.refuse(document))


class VoteLadderTest(RulesVariantTestCase):
    """票數非法必須拒絕啟動，並指名該欄位。"""

    def test_a_zero_vote_threshold_is_refused(self):
        document = valid_document()
        document["vote_thresholds"]["forced_stop"] = 0

        self.assertIn("vote_thresholds.forced_stop", self.refuse(document))

    def test_a_threshold_larger_than_the_seven_seats_is_refused(self):
        document = valid_document()
        document["vote_thresholds"]["initial"] = 8

        self.assertIn("vote_thresholds.initial", self.refuse(document))

    def test_a_boolean_is_not_a_vote_count(self):
        # 刻意選階梯最底層：True 等於 1，階梯校驗（6>5>1）照樣過得去，所以這
        # 個案例只可能被型別校驗擋下來，拿掉型別校驗它就會紅。
        document = valid_document()
        document["vote_thresholds"]["forced_stop"] = True

        message = self.refuse(document)
        self.assertIn("vote_thresholds.forced_stop", message)
        self.assertIn("整數票數", message)

    def test_a_ladder_that_rises_over_time_is_refused(self):
        document = valid_document()
        document["vote_thresholds"]["reduced"] = 7

        message = self.refuse(document)
        self.assertIn("vote_thresholds.reduced", message)
        self.assertIn("vote_thresholds.initial", message)

    def test_two_equal_steps_are_refused(self):
        # consensus_<n>_votes 這個停止原因必須只有一種讀法。
        document = valid_document()
        document["vote_thresholds"]["forced_stop"] = 5

        self.assertIn("vote_thresholds.forced_stop", self.refuse(document))

    def test_an_illegal_blind_pass_threshold_is_named(self):
        # 直過門檻不在遞減階梯上，所以它只剩票數校驗這一道；三種形狀都要擋。
        for value in (0, len(SEAT_IDS) + 1, True):
            with self.subTest(unanimous_blind_pass=value):
                document = valid_document()
                document["vote_thresholds"]["unanimous_blind_pass"] = value

                message = self.refuse(document)
                self.assertIn("vote_thresholds.unanimous_blind_pass", message)
                self.assertIn("整數票數", message)

    def test_a_missing_blind_pass_threshold_is_named(self):
        document = valid_document()
        del document["vote_thresholds"]["unanimous_blind_pass"]

        self.assertIn(
            "vote_thresholds.unanimous_blind_pass", self.refuse(document)
        )


class RulesDriveBehaviourTest(RulesVariantTestCase):
    """規則是設定驅動的：改設定檔，時間軸與門檻跟著改。"""

    def moved_rules(self):
        document = valid_document()
        document["timeline_ms"]["reduced_threshold_from"] = 450_000
        document["vote_thresholds"]["reduced"] = 5
        return self.load(document)

    def test_moving_the_threshold_drop_moves_the_vote_requirement(self):
        rules = self.moved_rules()

        self.assertEqual(6, rules.required_votes_at(449_999))
        self.assertEqual(5, rules.required_votes_at(450_000))
        self.assertEqual(4, rules.required_votes_at(600_000))

    def test_moving_the_threshold_drop_moves_the_named_phase(self):
        rules = self.moved_rules()

        self.assertEqual("first_round_closed", rules.phase_at(449_999))
        self.assertEqual("five_vote_threshold", rules.phase_at(450_000))

    def test_the_first_round_wall_follows_the_run_s_own_seal(self):
        rules = self.load(valid_document())

        self.assertEqual(420_000, rules.challenge_deadline_ms())
        self.assertEqual(450_000, rules.challenge_deadline_ms(270_000))

    def test_the_state_machine_helpers_read_the_injected_rules(self):
        from hoya_market_agents.debate_state_machine import phase_at, required_votes_at

        rules = self.moved_rules()

        self.assertEqual(6, required_votes_at(449_999, rules=rules))
        self.assertEqual(5, required_votes_at(450_000, rules=rules))
        self.assertEqual("five_vote_threshold", phase_at(450_000, rules=rules))


class ConfidencePlaceholderTest(RulesVariantTestCase):
    """燈號區塊的結構與型別校驗。

    Ticket 02 只交結構、值留空；Ticket 04 依 ADR 0003 填入五級映射與兩條降級。
    出貨值的斷言在 :class:`ShippedRulesTest`，這裡守的是「空的仍然合法、任意
    形狀怎麼被拒」——空區塊必須繼續被接受，因為載入器不是燈號詞彙的權威。
    """

    def test_an_empty_block_is_accepted(self):
        confidence = self.load(valid_document()).confidence

        self.assertFalse(confidence.configured)

    def test_the_adr_0003_scale_loads_best_first(self):
        document = valid_document()
        document["confidence"] = filled_confidence()

        confidence = self.load(document).confidence

        self.assertTrue(confidence.configured)
        self.assertEqual(
            ["blue", "green", "yellow", "orange", "red"],
            [step.level for step in confidence.light_scale],
        )
        self.assertEqual(
            [7, 6, 5, 4, 0], [step.min_votes for step in confidence.light_scale]
        )

    def test_the_adr_0003_downgrades_keep_their_own_parameters(self):
        document = valid_document()
        document["confidence"] = filled_confidence()

        by_rule = {rule.rule: rule for rule in self.load(document).confidence.downgrades}

        self.assertEqual(2, by_rule["few_independent_domains"].min_independent_domains)
        self.assertEqual((), by_rule["few_independent_domains"].exempt_seat_ids)
        self.assertEqual((1, 2), by_rule["low_trust_source"].trusted_source_tiers)
        # 輿情席職責就是蒐集三手輿情，對它套用來源等級降級屬於誤殺。
        self.assertEqual(
            ("social-macro",), by_rule["low_trust_source"].exempt_seat_ids
        )

    def test_a_scale_that_is_not_an_array_is_refused(self):
        document = valid_document()
        document["confidence"]["light_scale"] = {}

        self.assertIn("confidence.light_scale", self.refuse(document))

    def test_downgrades_that_are_not_an_object_are_refused(self):
        document = valid_document()
        document["confidence"]["downgrades"] = []

        self.assertIn("confidence.downgrades", self.refuse(document))

    def test_a_scale_rung_that_is_not_an_object_is_refused(self):
        document = valid_document()
        document["confidence"]["light_scale"] = ["blue"]

        message = self.refuse(document)
        self.assertIn("confidence.light_scale[0]", message)
        self.assertIn("必須是 object", message)

    def test_a_downgrade_that_is_not_an_object_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["low_trust_source"] = ["levels"]

        message = self.refuse(document)
        self.assertIn("confidence.downgrades.low_trust_source", message)
        self.assertIn("必須是 object", message)

    def test_an_exemption_list_that_is_not_an_array_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["low_trust_source"][
            "exempt_seat_ids"
        ] = "social-macro"

        message = self.refuse(document)
        self.assertIn("exempt_seat_ids", message)
        self.assertIn("必須是 array", message)

    def test_a_scale_rung_missing_its_level_is_named(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        del document["confidence"]["light_scale"][0]["level"]

        self.assertIn("confidence.light_scale[0].level", self.refuse(document))

    def test_a_scale_rung_with_an_unknown_key_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["light_scale"][0]["colour"] = "blue"

        self.assertIn("colour", self.refuse(document))

    def test_a_vote_count_beyond_the_seven_seats_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["light_scale"][0]["min_votes"] = 8

        self.assertIn("confidence.light_scale[0].min_votes", self.refuse(document))

    def test_a_json_false_is_not_a_vote_count_on_the_scale(self):
        # 末級的 min_votes 必須是 0，而 False == 0，所以連「全函式」那道檢查也
        # 攔不住它；只有精確型別比對擋得下來。
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["light_scale"][-1]["min_votes"] = False

        message = self.refuse(document)
        self.assertIn("confidence.light_scale[4].min_votes", message)
        self.assertIn("整數", message)

    def test_a_negative_vote_count_on_the_scale_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["light_scale"][0]["min_votes"] = -1

        self.assertIn("confidence.light_scale[0].min_votes", self.refuse(document))

    def test_a_light_level_that_is_not_a_string_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["light_scale"][0]["level"] = 5

        message = self.refuse(document)
        self.assertIn("confidence.light_scale[0].level", message)
        self.assertIn("非空字串", message)

    def test_a_scale_that_is_not_ordered_best_first_is_refused(self):
        # 順序就是「降一級」的定義；亂序的階梯沒有可降的方向。
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["light_scale"][1]["min_votes"] = 7

        self.assertIn("confidence.light_scale[1].min_votes", self.refuse(document))

    def test_a_duplicated_light_level_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["light_scale"][1]["level"] = "blue"

        self.assertIn("confidence.light_scale[1].level", self.refuse(document))

    def test_a_scale_that_does_not_reach_zero_is_refused(self):
        # 票數低於最後一級就沒有燈號可用；燈號映射必須是全函式。
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["light_scale"][-1]["min_votes"] = 3

        self.assertIn("confidence.light_scale", self.refuse(document))

    def test_a_blank_light_level_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["light_scale"][0]["level"] = "   "

        self.assertIn("confidence.light_scale[0].level", self.refuse(document))

    def test_an_unknown_downgrade_rule_is_refused(self):
        # 斷言必須指名「未知」：只檢查訊息含 stale_evidence 的話，把它加進
        # allowlist 之後那條規則自己的「缺欄位」訊息也含這個名字，測試會誤綠。
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["stale_evidence"] = {"levels": 1}

        message = self.refuse(document)
        self.assertIn("stale_evidence", message)
        self.assertIn("含未知欄位", message)

    def test_a_downgrade_missing_its_parameter_is_named(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        del document["confidence"]["downgrades"]["low_trust_source"][
            "trusted_source_tiers"
        ]

        self.assertIn(
            "confidence.downgrades.low_trust_source.trusted_source_tiers",
            self.refuse(document),
        )

    def test_a_zero_level_downgrade_is_refused(self):
        # 降 0 級的降級規則是雜訊，不是規則。
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["few_independent_domains"]["levels"] = 0

        self.assertIn(
            "confidence.downgrades.few_independent_domains.levels",
            self.refuse(document),
        )

    def test_a_downgrade_deeper_than_the_seven_seats_is_refused(self):
        # 降級格數的合理上限就是席位數；再大只可能是打錯（例如把 10% 寫成 10）。
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["few_independent_domains"]["levels"] = 8

        self.assertIn(
            "confidence.downgrades.few_independent_domains.levels",
            self.refuse(document),
        )

    def test_a_json_true_is_not_a_downgrade_level(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["few_independent_domains"]["levels"] = True

        message = self.refuse(document)
        self.assertIn("confidence.downgrades.few_independent_domains.levels", message)
        self.assertIn("整數", message)

    def test_a_non_positive_domain_threshold_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["few_independent_domains"][
            "min_independent_domains"
        ] = 0

        self.assertIn("min_independent_domains", self.refuse(document))

    def test_a_json_true_is_not_a_domain_threshold(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["few_independent_domains"][
            "min_independent_domains"
        ] = True

        message = self.refuse(document)
        self.assertIn("min_independent_domains", message)
        self.assertIn("正整數", message)

    def test_an_empty_trusted_tier_list_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["low_trust_source"][
            "trusted_source_tiers"
        ] = []

        self.assertIn("trusted_source_tiers", self.refuse(document))

    def test_a_trusted_tier_list_that_is_not_an_array_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["low_trust_source"][
            "trusted_source_tiers"
        ] = 1

        self.assertIn("trusted_source_tiers", self.refuse(document))

    def test_a_json_true_hiding_inside_the_trusted_tier_list_is_refused(self):
        # 逐元素檢查：True == 1，混在 [1, true] 裡看起來就像合法的 tier 1。
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["low_trust_source"][
            "trusted_source_tiers"
        ] = [1, True]

        self.assertIn("trusted_source_tiers", self.refuse(document))

    def test_a_non_positive_tier_in_the_trusted_list_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["low_trust_source"][
            "trusted_source_tiers"
        ] = [0]

        self.assertIn("trusted_source_tiers", self.refuse(document))

    def test_an_exemption_for_a_seat_that_does_not_exist_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["low_trust_source"]["exempt_seat_ids"] = [
            "social_macro"
        ]

        self.assertIn("social_macro", self.refuse(document))

    def test_an_unknown_key_inside_a_downgrade_is_refused(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["low_trust_source"]["window_days"] = 30

        self.assertIn("window_days", self.refuse(document))


class LegalBoundaryTest(RulesVariantTestCase):
    """合法的邊界值必須被接受——只鎖「非法值被拒」會留下一整類退化沒人守。

    每一條有上下界的校驗，端點本身都是合法的。把 ``<=`` 收緊成 ``<`` 會讓
    系統開始拒絕它自己文件寫明允許的設定，而這種退化在只有拒絕案例的測試套
    件裡是全綠的。下面每個案例都對應一個「把界收緊」的 mutant。
    """

    def test_a_debate_that_starts_at_zero_is_accepted(self):
        # 時間下界是 0，不是 1：T+0 起跑的設定合法。
        document = valid_document()
        document["timeline_ms"]["debate_start"] = 0

        self.assertEqual(0, self.load(document).debate_start_ms)

    def test_a_one_millisecond_first_round_window_is_accepted(self):
        document = valid_document()
        document["timeline_ms"]["round_one_window"] = 1

        rules = self.load(document)
        self.assertEqual(1, rules.round_one_window_ms)
        self.assertEqual(240_001, rules.challenge_deadline_ms())

    def test_walls_one_millisecond_apart_are_accepted(self):
        # 「嚴格遞增」的下限就是差 1ms；差 1ms 仍然是遞增。
        document = valid_document()
        document["timeline_ms"].update(
            debate_start=240_000,
            reduced_threshold_from=240_001,
            final_round_start=240_002,
            final_round_end=240_003,
            force_stop=240_004,
        )

        rules = self.load(document)
        self.assertEqual(240_001, rules.reduced_threshold_from_ms)
        self.assertEqual(240_004, rules.force_stop_ms)

    def test_the_widest_legal_vote_ladder_is_accepted(self):
        # 兩個端點一次驗完：上界＝席位數 7，下界＝1；順便驗 6/5/4 以外的階梯。
        document = valid_document()
        document["vote_thresholds"] = {
            "unanimous_blind_pass": 7,
            "initial": 7,
            "reduced": 4,
            "forced_stop": 1,
        }

        rules = self.load(document)
        self.assertEqual(7, rules.initial_votes)
        self.assertEqual(1, rules.forced_stop_votes)
        self.assertEqual(7, rules.required_votes_at(0))
        self.assertEqual(1, rules.required_votes_at(600_000))

    def test_both_ends_of_the_blind_pass_threshold_are_accepted(self):
        # 直過門檻不在遞減階梯上，所以它自己的兩個端點要單獨驗：1 與席位數 7。
        for value in (1, len(SEAT_IDS)):
            with self.subTest(unanimous_blind_pass=value):
                document = valid_document()
                document["vote_thresholds"]["unanimous_blind_pass"] = value

                self.assertEqual(value, self.load(document).unanimous_blind_pass_votes)

    def test_a_blind_pass_threshold_below_the_debate_ladder_is_accepted(self):
        # 直過門檻與 6/5/4 階梯量的是兩種票（開場票 vs 有效票），刻意不互相
        # 約束。設得比 initial 低是合法設定，載入器不得越權替使用者否決。
        document = valid_document()
        document["vote_thresholds"]["unanimous_blind_pass"] = 4

        rules = self.load(document)
        self.assertEqual(4, rules.unanimous_blind_pass_votes)
        self.assertEqual(6, rules.initial_votes)

    def test_the_smallest_legal_light_scale_is_accepted(self):
        # 一級的階梯就是全函式：所有票數都對到同一個燈號。
        document = valid_document()
        document["confidence"]["light_scale"] = [{"min_votes": 0, "level": "red"}]

        confidence = self.load(document).confidence
        self.assertTrue(confidence.configured)
        self.assertEqual(("red",), tuple(step.level for step in confidence.light_scale))

    def test_a_downgrade_of_seven_levels_is_accepted(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["few_independent_domains"]["levels"] = 7

        by_rule = {rule.rule: rule for rule in self.load(document).confidence.downgrades}
        self.assertEqual(7, by_rule["few_independent_domains"].levels)

    def test_a_single_independent_domain_threshold_is_accepted(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["few_independent_domains"][
            "min_independent_domains"
        ] = 1

        by_rule = {rule.rule: rule for rule in self.load(document).confidence.downgrades}
        self.assertEqual(1, by_rule["few_independent_domains"].min_independent_domains)

    def test_a_single_trusted_tier_is_accepted(self):
        # 「至少一個」的下限就是一個。
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["low_trust_source"][
            "trusted_source_tiers"
        ] = [1]

        by_rule = {rule.rule: rule for rule in self.load(document).confidence.downgrades}
        self.assertEqual((1,), by_rule["low_trust_source"].trusted_source_tiers)

    def test_configuring_only_one_downgrade_rule_is_accepted(self):
        # 兩條降級各自可選；只設一條不該被當成缺漏。
        document = valid_document()
        document["confidence"] = filled_confidence()
        del document["confidence"]["downgrades"]["few_independent_domains"]

        downgrades = self.load(document).confidence.downgrades
        self.assertEqual(("low_trust_source",), tuple(r.rule for r in downgrades))


class SetMembershipTest(RulesVariantTestCase):
    """集合／allowlist 的成員邊界——和數值界是兩種不同的東西。

    數值界是一條有序軸，只會往內或往外平移；集合可以任意增刪個別成員，所以
    兩個方向要用兩種不同的測試：

    * **內縮**（合法成員被錯誤拒絕）→ 對每個成員各一個「接受」案例。
    * **外擴**（非成員被錯誤接受）→ 對非成員的「拒絕」案例只能擋掉被點名的
      那個值；要真正擋住任意新成員，唯一辦法是把集合本身釘住（inventory）。
      下面標示 inventory 的案例就是為此存在，不是為了測實作細節。
    """

    def test_every_seat_on_the_roster_can_be_exempted(self):
        # 內縮防線：allowlist 少掉任何一席，這裡就會紅。
        for seat_id in SEAT_IDS:
            with self.subTest(seat_id=seat_id):
                document = valid_document()
                document["confidence"] = filled_confidence()
                document["confidence"]["downgrades"]["low_trust_source"][
                    "exempt_seat_ids"
                ] = [seat_id]

                by_rule = {
                    rule.rule: rule
                    for rule in self.load(document).confidence.downgrades
                }
                self.assertEqual(
                    (seat_id,), by_rule["low_trust_source"].exempt_seat_ids
                )

    def test_all_seven_seats_can_be_exempted_at_once(self):
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["low_trust_source"][
            "exempt_seat_ids"
        ] = list(SEAT_IDS)

        by_rule = {rule.rule: rule for rule in self.load(document).confidence.downgrades}
        self.assertEqual(SEAT_IDS, by_rule["low_trust_source"].exempt_seat_ids)

    def test_the_domain_rule_has_no_exemption_knob_at_all(self):
        """豁免是「這席的卡片不受這條規則判定」，只有逐卡規則說得通。

        ``few_independent_domains`` 數的是**集合基數**：把某席的卡片豁免掉會讓
        網域集合變小，於是降級**更容易**觸發——一個叫做「豁免」的欄位做出相反
        的事。與其驗證它、改名它，不如讓這個狀態無法表達。
        """
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["few_independent_domains"][
            "exempt_seat_ids"
        ] = ["social-macro"]

        message = self.refuse(document)
        self.assertIn("confidence.downgrades.few_independent_domains", message)
        self.assertIn("exempt_seat_ids", message)

    def test_an_empty_exemption_list_is_refused_on_the_domain_rule_too(self):
        # 空清單同樣不合法：欄位存在本身就是誤導，不是值的問題。
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["few_independent_domains"][
            "exempt_seat_ids"
        ] = []

        self.assertIn("exempt_seat_ids", self.refuse(document))

    def test_the_source_tier_rule_keeps_its_exemption(self):
        # 誤擋方向：收緊降級①不得波及降級②的豁免。
        document = valid_document()
        document["confidence"] = filled_confidence()

        by_rule = {rule.rule: rule for rule in self.load(document).confidence.downgrades}

        self.assertEqual(("social-macro",), by_rule["low_trust_source"].exempt_seat_ids)
        self.assertEqual((), by_rule["few_independent_domains"].exempt_seat_ids)

    def test_a_seat_that_was_never_on_the_roster_is_refused(self):
        # 與既有的 social_macro（錯用底線）不同形狀：完全虛構的席位。
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["downgrades"]["low_trust_source"]["exempt_seat_ids"] = [
            "ghost-seat"
        ]

        message = self.refuse(document)
        self.assertIn("ghost-seat", message)
        self.assertIn("含未知席位", message)

    def test_the_exemption_allowlist_is_the_frozen_seven_seat_roster(self):
        """inventory：豁免判準必須就是 seats 的七席名冊，不得自成一份。"""
        from hoya_market_agents import debate_rules as module
        from hoya_market_agents.seats import SEAT_IDS as roster

        self.assertEqual(roster, module.SEAT_IDS)
        self.assertEqual(7, len(module.SEAT_IDS))

    def test_every_allowed_top_level_key_is_also_a_required_one(self):
        """inventory：最外層 allowlist 恰好等於載入器真的會讀的鍵。

        allowlist 外擴（多允許一個沒人讀的鍵）不會讓任何「拒絕」案例變紅，
        因為那個鍵是新的、沒有測試點過它的名字。唯一擋得住的方式是把集合與
        「一份最小合法文件的鍵」對齊。
        """
        from hoya_market_agents.debate_rules import _TOP_LEVEL_FIELDS

        for name in _TOP_LEVEL_FIELDS:
            with self.subTest(field=name):
                document = valid_document()
                del document[name]
                self.assertIn(name, self.refuse(document))
        self.assertEqual(set(_TOP_LEVEL_FIELDS), set(valid_document()))

    def test_every_allowed_vote_threshold_key_is_also_a_required_one(self):
        """inventory：票數 allowlist 恰好等於載入器真的會讀的鍵。

        和最外層同一個理由：多允許一個沒人讀的票數鍵不會讓任何「拒絕」案例
        變紅，只有把集合與一份最小合法文件對齊才擋得住。
        """
        from hoya_market_agents.debate_rules import _VOTE_FIELDS

        for name in _VOTE_FIELDS:
            with self.subTest(field=name):
                document = valid_document()
                del document["vote_thresholds"][name]
                self.assertIn(
                    "vote_thresholds.{}".format(name), self.refuse(document)
                )
        self.assertEqual(
            set(_VOTE_FIELDS), set(valid_document()["vote_thresholds"])
        )

    def test_only_the_debate_ladder_is_bound_by_the_decreasing_rule(self):
        """inventory：遞減檢查的成員恰好是三階梯，直過門檻不在其中。"""
        from hoya_market_agents.debate_rules import (
            _VOTE_FIELDS,
            _VOTE_LADDER_FIELDS,
        )

        self.assertEqual(("initial", "reduced", "forced_stop"), _VOTE_LADDER_FIELDS)
        self.assertEqual(
            ("unanimous_blind_pass",) + _VOTE_LADDER_FIELDS, _VOTE_FIELDS
        )

    def test_the_downgrade_rule_names_are_exactly_the_two_from_adr_0003(self):
        """inventory：認得的降級規則只有 ADR 0003 那兩條。"""
        from hoya_market_agents.debate_rules import _DOWNGRADE_FIELDS

        self.assertEqual(
            {"few_independent_domains", "low_trust_source"},
            set(_DOWNGRADE_FIELDS),
        )

    def test_only_the_per_card_rule_owns_an_exemption_field(self):
        """inventory：豁免欄位只能掛在逐卡判定的規則上。"""
        from hoya_market_agents.debate_rules import _DOWNGRADE_FIELDS

        self.assertEqual(
            {"low_trust_source"},
            {
                name
                for name, fields in _DOWNGRADE_FIELDS.items()
                if "exempt_seat_ids" in fields
            },
        )

    def test_the_next_schema_version_is_not_silently_accepted(self):
        # 支援版本是一個單元素集合；相鄰成員（2）必須被拒，否則「外擴成 {1,2}」
        # 這種退化沒人擋。
        for version in (0, 2):
            with self.subTest(version=version):
                document = valid_document()
                document["schema_version"] = version

                self.assertIn("schema_version", self.refuse(document))


class LateFirstRoundWallTest(RulesVariantTestCase):
    """牆落在門檻降低之後是合法設定，行為必須是定義好的、不是意外。

    第一輪牆是相對的（該 run 封存時刻＋窗），比較題晚 30 秒封存，所以牆的落點
    載入時根本算不出來。載入器只驗窗為正；重疊時的語意由 phase_at 定義。
    """

    COMPARISON_SEAL_MS = 270_000

    def late_wall_rules(self):
        document = valid_document()
        document["timeline_ms"]["round_one_window"] = 220_000
        return self.load(document)

    def test_a_window_whose_wall_passes_the_threshold_drop_still_loads(self):
        rules = self.late_wall_rules()

        wall = rules.challenge_deadline_ms(self.COMPARISON_SEAL_MS)
        self.assertEqual(490_000, wall)
        self.assertGreater(wall, rules.reduced_threshold_from_ms)

    def test_the_later_absolute_wall_wins_when_the_two_overlap(self):
        rules = self.late_wall_rules()
        wall = rules.challenge_deadline_ms(self.COMPARISON_SEAL_MS)

        self.assertEqual("first_round", rules.phase_at(479_999, wall))
        self.assertEqual("five_vote_threshold", rules.phase_at(480_000, wall))
        self.assertEqual("five_vote_threshold", rules.phase_at(wall, wall))

    def test_the_vote_threshold_still_drops_on_its_own_schedule(self):
        # 牆晚到不代表門檻跟著晚：票數階梯只看絕對時刻。
        rules = self.late_wall_rules()

        self.assertEqual(6, rules.required_votes_at(479_999))
        self.assertEqual(5, rules.required_votes_at(480_000))


class SingleSourceTest(unittest.TestCase):
    """被取代的模組常數必須刪除，不得殘留第二來源。"""

    def test_the_state_machine_no_longer_exports_the_moved_constants(self):
        from hoya_market_agents import debate_state_machine

        for name in (
            "DEBATE_START_MS",
            "ROUND_ONE_WINDOW_MS",
            "CHALLENGE_DEADLINE_MS",
            "THRESHOLD_FIVE_FROM_MS",
            "FINAL_ROUND_START_MS",
            "FINAL_ROUND_END_MS",
            "FORCE_STOP_MS",
        ):
            self.assertFalse(
                hasattr(debate_state_machine, name),
                "{} 應該只存在於 config/debate_rules.json".format(name),
            )


PACKAGE = "hoya_market_agents"


def count_authority_reads(operation):
    """Run ``operation`` once; return how many times it read the rules authority.

    盤點二的量測工具，給每個模組的測試共用。

    為什麼不用靜態計數：``rules or debate_rules()`` 這種寫法只有在呼叫端沒傳
    快照時才真的讀，所以數原始碼裡的呼叫點會高估。這裡直接量執行時的次數。

    為什麼要掃 ``sys.modules`` 而不是只換定義它的那個模組：消費端寫的是
    ``from .debate_rules import debate_rules``，各自持有同一個函式物件的參照，
    只換定義處一個也攔不到。這裡用**物件識別**去找持有者，所以連 import 時改名
    的模組也照樣算得到——不依賴任何命名慣例。

    **只計入發起執行緒的讀取。** 換掉的是全模組共用的名字，所以背景執行緒（例如
    看板測試留下的 request thread）的讀取本來會被算進當前 operation，讓計數變成
    「大部分時候是對的」。本工具是承重的——第 2 輪的 ``dashboard_keeps_a_module_
    level_rules`` 就是靠它殺掉的——所以它不能偶爾多數幾次。
    已知限制：operation 若把讀取搬到自己 spawn 的執行緒，這裡會**低估**；目前所
    有入口都是同步的，真的要那樣做時得改成顯式傳遞 token。

    **還原是按物件識別再掃一次**，不是還原「呼叫前記下來的那一份清單」：操作途
    中才被 import 進來的 consumer 也會持有 ``counting``，只還原舊清單會把一個計
    數用的 closure 永久留在模組樹上，污染之後每一個測試。
    """
    from hoya_market_agents import debate_rules as rules_module

    original = rules_module.debate_rules
    owner = threading.get_ident()
    reads = []

    def counting():
        if threading.get_ident() == owner:
            reads.append(None)
        return original()

    def holders(wanted):
        for module in list(sys.modules.values()):
            if not getattr(module, "__name__", "").startswith(PACKAGE):
                continue
            for attribute, value in list(vars(module).items()):
                if value is wanted:
                    yield module, attribute

    for module, attribute in list(holders(original)):
        setattr(module, attribute, counting)
    try:
        operation()
    finally:
        for module, attribute in list(holders(counting)):
            setattr(module, attribute, original)
    return len(reads)


# 盤點一：import 期間不得留下會過期的設定。
#
# 這句話由三個機制合起來查（①讀設定檔、②存著快照、③呼叫權威），**沒有哪一個是
# 另外兩個的超集**。不要把其中任何一個講成整個不變式——第 9 輪的檔頭寫成「不得
# 有人讀那份設定檔」，那句話一旦成立，②就沒有存在的必要了，而②確實有。
#
# 演進紀錄，因為每一步都是被打破之後才學到的——而且前三步都只是這句話的**代理指
# 標**，每一步更接近，但都還是代理：
#   第 2-3 輪  手刻 AST 分析器近似「import 時會執行什麼」。那個問題的答案是整個
#              Python 語言，每一輪都被普通語法繞過。
#   第 4 輪    改成插樁 + 真的 import。方向對，但偵測靠 monkeypatch（重新綁定名
#              字就能推翻），結果靠 ``stdout or "[]"``（把「我沒有答案」翻譯成
#              「答案是乾淨」）。七種沉默失敗。
#   第 5 輪    profiler + sentinel 信封。但 ``observed_calls`` 只證明探針**曾**
#              生效，不是**全程**生效：暫時關掉 profiler、fork、開子行程、先印
#              一個假信封遮蔽真的——六種又繞過去。
#   第 6 輪    ``sys.addaudithook``（**裝上就無法移除**，這是 CPython 的設計保
#              證）盯住整段 import 期間；信封改走獨立檔案。
#   第 7 輪    偵測從「進入權威**函式**」放寬到「進入權威**檔案**」——公開的
#              ``load_debate_rules`` 不再是漏洞。但複製一份載入器、或先建好快照
#              再轉交，仍然繞得過。
#   第 8 輪    直接守設定檔本身。設定檔的合法讀取者是 ``load_debate_rules``，
#              而它只在 lazy 路徑上被呼叫，import 期本來就不該跑到——所以「import
#              期間有人開那個檔」這一項不再是代理，它就是缺陷本身。**但它只是三
#              個機制之一**：不讀檔也拿得到快照的形狀由②負責。
#   第 9 輪    ①改比檔案身分（st_dev, st_ino）而不是路徑字串；②的 fixture 改用
#              dataclass，隔離才真的成立。
#   第 10 輪   ①的相對路徑保守攔截限定在相對路徑——絕對路徑用 ``os.stat`` 就能
#              決定性判定，沒有 fail-closed 的理由。
#
# 三個機制，涵蓋面不同，缺一不可：
#   ①「有沒有人讀設定檔」：``open`` audit event 比對**檔案身分**（st_dev,
#     st_ino），import 期間任何模組（**包含權威模組自己**）開到同一個 inode 就
#     fail-closed；另有一條只對**相對路徑**的同名保守攔截（見下）。抓的是複製載
#     入器、以及權威模組自己在 module body 先載入的情形。
#   ②「有沒有人存著快照」：import 完成後掃全 package 的**直接模組層屬性**，其中
#     「型別定義在權威檔裡」的實例算作預先建好的快照。抓的是不讀檔也拿得到物件的
#     情形——權威模組自己預先建好、或別的模組自己建一個再轉交。（**不要寫成「權
#     威模組不匯出快照就沒有東西可以被轉交」**：中間模組可以自己建構一個，測試
#     ``test_a_snapshot_held_by_a_middle_module_is_reported_not_cleared`` 用的就
#     是那個形狀。）判準的範圍見下方「機制②的判準」。
#   ③「有沒有人呼叫權威」：profiler 監看進入權威檔的 frame。抓的是未來某個入口
#     回傳已快取物件、根本不讀檔的情形。
#   另有一個獨立於執行時稽核的靜態守門（見 :class:`PrivateAuthorityStateTest`），
#   抓「碰了私有名字」——那條路完全不進入權威檔的任何函式，①②③都看不到。
#
# 威脅模型（第 8 輪重新校對；每一項附正確理由）：
#   **這個工具防的是「不小心把設定值凍結在 import 時」，不是對抗刻意規避。**
#   官方文件也明說 audit hook 不是 sandbox，所以下面不做安全邊界宣稱。
#
#   已修：
#     - 讀設定檔取得設定值（機制①）：用檔案身分（st_dev, st_ino）比對，所以
#       hardlink／symlink／大小寫別名都算同一個檔案。
#     - 預先建好、可被轉交的快照（機制②）：**判準有明確範圍，見下方「機制②的
#       判準」**。
#     - 呼叫權威檔裡的函式（機制③）：看的是 frame 落在哪個檔案，不是函式名，所
#       以未來新增的入口**只要定義在權威檔裡**就一併涵蓋；搬到別的檔案就不是③的
#       範圍了。
#     - worker 執行緒裡的探針被關掉：白名單放行 ``threading`` 傳播之前，額外要求
#       它傳播的**就是我們這一個 hook**。
#     - ``os.system``／``_thread.start_new_thread``：兩者都有標準 audit event，
#       直接監看。（第 6 輪的報告說 ``_thread.start_new_thread`` 沒有 event，
#       **那句話是錯的**，實測 Python 3.12.3 會發出。）
#
#   機制②的判準（這是「我防什麼」的一部分，不是剩餘風險）：
#     只掃**直接的模組層屬性**，且只認**型別定義在權威檔**的實例。因此看不到：
#       - 回傳 ``dict`` 或 stdlib 型別的設定（沒有權威型別可認）；
#       - 藏在 list／dict／class attribute 裡的實例（不是直接的模組層屬性）。
#     目前四種設定型別（``DebateRules``／``ConfidenceRules``／``LightStep``／
#     ``DowngradeRule``）都定義在權威檔，判準成立——那是**現況**，不是保證，所以
#     由 :class:`GuardedConfigurationTypesTest` 釘住；改動時它會先紅。
#
#   已知不涵蓋（宣告排除，理由如下）：
#     - **把設定值硬寫在消費端程式碼裡**（``INITIAL_VOTES = 6``）：那不是快照，
#       是資料的複本。沒有任何執行時事件會發生——沒有開檔、沒有進入權威檔、沒有
#       實例——所以任何執行時工具都看不到它。**只能靠 code review。**
#     - C extension 直接讀模組狀態（讀**記憶體**）：不經過任何 Python frame，
#       profiler 看不到。
#     - 透過 ctypes、C extension 或其他 native API 直接開啟／讀取設定檔（讀**檔
#       案**）：不一定會產生 CPython 的 ``open`` audit event。本工具只涵蓋會發出
#       該 audit event 的檔案 I/O。這與上一項是不同的形狀，兩個都不涵蓋。
#     - 直接讀 ``_CACHED_RULES``：不進入權威檔的任何函式，由靜態守門負責。
#     - 目標模組若能寫入信封檔（路徑在 ``sys.argv`` 裡）就能偽造結果：屬刻意規避。
#     - 用 ``compile(..., threading.__file__, "exec")`` 冒充呼叫端檔名：同上。附
#       帶說明，加上「傳播的必須是我們這一個 hook」之後，它也不再只靠檔名就過。
#     - ``os.startfile``：Windows 專屬，本執行環境沒有這個屬性；跨平台移植時要
#       一併納入 WATCHED。
#   已涵蓋但有取捨（不是排除項，列在這裡是因為它的判準與①的其餘部分不同）：
#     - 透過 **dir_fd** 的相對開檔：``open`` audit event 不帶 dir_fd，身分比對解
#       不到正確基準——那是真的無法判定，所以**只對相對路徑**做同名保守攔截，標成
#       ``basename:`` 以便和身分比對分辨。絕對路徑不套用：``os.stat`` 對它可以決
#       定性地證明是不是同一個 inode，「可以判定而且答案是否」沒有 fail-closed 的
#       正當理由（第 9 輪對絕對路徑也攔，那是誤報）。
#       殘餘代價：import 期間開一個**相對路徑**的同名無關檔案會誤報；目前 package
#       內沒有第二個同名檔，且訊息會直接印出路徑，判斷成本低。
#
# ── docstring 規則（M1，第 12 輪立）─────────────────────────────────────────
#   1. helper／局部函式的 docstring **只描述這個函式對它的輸入做了什麼**，不描述
#      整個工具涵蓋什麼。
#   2. **涵蓋範圍的陳述只能出現在上面那份威脅模型**。別處要提就指向它，不要複述
#      ——複述就會漂移：這一票「宣稱超過實作」已**反覆出現**，每一次都是某個
#      helper 各自寫了一份自己的涵蓋說明。（刻意不寫精確次數：那個數字每輪都會
#      過期，維護一個會過期的數字本身就是下一個漂移點。）
#   3. 局部函式若有自己的限制（例如 ``references_in`` 只看靜態 ``Name``／
#      ``Attribute``／``ImportFrom``），**寫出那個限制本身**，不要寫成「涵蓋每一
#      種」。
#   4. **這條規則也套用在 ``_IMPORT_AUDIT_SOURCE`` 內嵌的那份程式**。它是字串，
#      外層的 AST 盤點看不到它——所以檢查時要先把它 ``format`` 出來再
#      ``ast.parse``，否則「全檔已收斂」只是「外層已收斂」。第 12 輪就是這樣漏
#      掉內嵌 ``hook()`` 那份分工說明的（那是第三份複本）。
#   改動這個檔案之後，把改到的 docstring 逐段重讀一次——這一類缺陷 grep 抓不到。


AUDIT_PROTOCOL = 7
_AUDIT_BEGIN = "<<<HOYA-IMPORT-AUDIT "
_AUDIT_END = " HOYA-IMPORT-AUDIT>>>"
def _is_exact_int(value):
    """``type(x) is int``：``bool`` 是 ``int`` 的子類，``True`` 不是版本號。"""
    return type(value) is int


def _is_count(value):
    """非負整數。

    次數不可能是負的，而 ``-1`` 會**通過** ``if not payload[...]`` 那道 guard
    ——型別對、語意不可能、結果是回報乾淨。所以被當成 guard 的整數欄位要驗到
    語意域，不只是型別。
    """
    return type(value) is int and value >= 0


def _is_bool(value):
    return type(value) is bool


def _is_text(value):
    return isinstance(value, str)


def _is_text_list(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_stack_list(value):
    """``reads`` 是 [呼叫堆疊]，每一格是 ``{file, line, name}``。"""
    if not isinstance(value, list):
        return False
    for stack in value:
        if not isinstance(stack, list) or not stack:
            return False
        for frame in stack:
            if not isinstance(frame, dict):
                return False
            if not _is_text(frame.get("file")) or not _is_text(frame.get("name")):
                return False
            if not _is_exact_int(frame.get("line")):
                return False
    return True


# 欄位 → 判準。信封存在的理由就是「子行程可能是壞的」，所以鍵齊了還不夠：一個把
# complete 寫成 "yes"、observed_calls 寫成 "one" 的子行程，正是這一層該擋下來的
# ——真值字串會讓後面每一道 guard 都變成恆真。
#
# 判準要多嚴，由**這個欄位的值有沒有參與判斷**決定，不是逐個猜：
#
#   guard 欄位（值會決定通過與否）——判準必須讓**整個值域都是有意義的**：
#     protocol／target／status  相等比對：不等就拒，任何值都安全，只需型別。
#     complete／probe_intact    ``type(x) is bool``：值域就是 {True, False}。
#     integrity_violations      list[str] 的真假值：空＝過、非空＝拒，全域有意義。
#     config_opens／snapshot_exports／threads_alive   同上。
#     observed_calls            **整數的真假值**：0＝拒、正數＝過，但**負數是真值
#                               會過**，而負的呼叫次數不可能存在——值域裡有一段
#                               既不合理也不會被拒的區間，所以它要 :func:`_is_count`。
#
#   資料欄位（值不參與任何判斷）——只需型別，怪值不會造成 fail-open：
#     detail                    只出現在錯誤訊息裡。
#     reads                     轉成 Finding 回傳；奇怪的行號只會產生奇怪的
#                               finding，不會讓結果變乾淨。
_AUDIT_FIELD_TYPES = {
    "protocol": _is_exact_int,
    "target": _is_text,
    "status": _is_text,
    "detail": _is_text,
    "reads": _is_stack_list,
    "observed_calls": _is_count,
    "probe_intact": _is_bool,
    "integrity_violations": _is_text_list,
    "config_opens": _is_text_list,
    "snapshot_exports": _is_text_list,
    "threads_alive": _is_text_list,
    "complete": _is_bool,
}
_AUDIT_REQUIRED_KEYS = frozenset(_AUDIT_FIELD_TYPES)


class ImportAuditError(AssertionError):
    """The audit could not produce a trustworthy answer.

    刻意不是「乾淨」的同義詞：**信封上每一道檢查**失敗時都走到這裡，而不是變成
    綠燈。已知涵蓋的失敗形態見 :class:`ImportAuditFailsClosedTest` 與
    :class:`AuditEnvelopeTest`——那是一份**已經被找到**的清單，不是「所有可能的
    壞法」的窮舉。
    """


_IMPORT_AUDIT_SOURCE = '''\
import json
import os
import sys
import threading

PROTOCOL = {protocol}
BEGIN = {begin!r}
END = {end!r}
# 承重的項目每一個都有具名端到端測試（見 ImportAuditFailsClosedTest）；
# 冗餘的三個保留下來當縱深防禦，理由逐條寫在下面。
WATCHED = frozenset(
    {{
        # -- 承重，各有具名測試 --------------------------------------------
        "sys.setprofile",            # 暫時關掉探針再讀
        "os.fork",                   # 讀取跑到子行程去
        "os.forkpty",                # 同上，另一個 fork 入口
        "os.posix_spawn",            # 不經過 fork 的子行程
        "os.system",                 # shell 出去讀，結果寫檔交回
        "subprocess.Popen",          # 同上，Python 介面
        "_thread.start_new_thread",  # 繞過 threading.Thread 的原始執行緒
        # -- 冗餘，保留當縱深防禦 ------------------------------------------
        # sys.settrace：偵測是 profile-based，trace hook 動不動都不影響判定。
        "sys.settrace",
        # os.exec*：行程映像被換掉，信封永遠寫不出來，缺信封那道守衛本來就
        # fail-closed（test_a_module_that_execs_is_reported_not_cleared 釘住）。
        "os.exec",
        # os.spawn*：POSIX 上會先觸發 os.fork，那一項已經承重。
        "os.spawn",
    }}
)

search_path, authority_file, config_file, target, envelope_path, disabled = sys.argv[1:7]
disabled = set(filter(None, disabled.split(",")))
authority_names = {{authority_file, os.path.realpath(authority_file)}}
threading_names = {{threading.__file__, os.path.realpath(threading.__file__)}}
config_basename = os.path.basename(config_file)
try:
    config_stat = os.stat(config_file)
except OSError:
    config_stat = None
package_root = os.path.realpath(os.path.dirname(authority_file))
sys.path.insert(0, search_path)

reads = []
observed = [0]
violations = []
config_opens = []
watching = [False]
reentrant = [False]


def hook(frame, event, arg):
    """Record a call whose frame is in the authority file and whose caller is not.

    輸入是 profiler 的 ``call`` 事件；符合條件時把整條呼叫堆疊追加到 ``reads``。

    兩種不記：``co_name == "<module>"``（那是權威檔自己被 import，不是有人讀
    它），以及呼叫端也在權威檔裡（內部互相呼叫，只記最外層那一次進入）。

    這一格是機制③；三個機制的分工與整體涵蓋範圍見**外層檔頭的威脅模型**，這裡
    不複述——第 12 輪立的規則第 4 條就是為了這一份內嵌程式。
    """
    if event != "call":
        return
    observed[0] += 1
    if "frames" in disabled:
        return
    code = frame.f_code
    if code.co_filename not in authority_names:
        return
    if code.co_name == "<module>":
        return
    back = frame.f_back
    if back is not None and back.f_code.co_filename in authority_names:
        return
    stack = []
    current = frame
    while current is not None:
        stack.append(
            {{
                "file": current.f_code.co_filename,
                "line": current.f_lineno,
                "name": current.f_code.co_name,
            }}
        )
        current = current.f_back
    reads.append(stack)


def propagating_our_hook():
    """True when ``threading`` is handing our own hook to a new thread.

    只看呼叫端是不是 ``threading.py`` 不夠：模組可以先
    ``threading.setprofile(None)`` 再開執行緒，那時呼叫端**真的**是
    ``threading.py``，但它傳播的已經不是我們的 hook，worker 裡的讀取就變隱形。
    所以放行前要求 ``_profile_hook`` 仍然是我們這一個。
    """
    # 深度 2：frame 0 是這個 helper、frame 1 是 audit()、frame 2 才是觸發事件
    # 的那一格。第 7 輪把檢查從 audit() 內聯搬進 helper 時漏掉這一格，於是合法
    # 的 threading.Thread 全被判成違規。
    try:
        caller = sys._getframe(2)
    except ValueError:
        return False
    if caller is None or caller.f_code.co_filename not in threading_names:
        return False
    return getattr(threading, "_profile_hook", None) is hook


def audit(event, args):
    # 裝上就移不掉，所以整段 import 期間的完整性是它保證的，不是結束時補驗的。
    if not watching[0] or reentrant[0]:
        return
    if event == "open":
        # 機制①：import 期間不得有人讀那份設定檔。
        #
        # 比的是**檔案身分**（st_dev, st_ino），不是路徑字串：大小寫別名、
        # hardlink、symlink、任何「同一個檔案不同寫法」都是同一個 inode。第 8 輪
        # 比字串，DrvFs 上的 config/DEBATE_RULES.JSON 就穿過去了。
        #
        # 次要規則，**只對相對路徑**：``open`` 這個 audit event 不帶 dir_fd，所以
        # ``os.open(name, dir_fd=...)`` 的相對路徑解析不到正確基準——那是真的無法
        # 判定，因此同名的相對開檔保守攔截，標成 ``basename:`` 以便分辨。
        # 絕對路徑不套用：``os.stat`` 對它可以決定性地證明是不是同一個 inode，
        # 「可以判定而且答案是否」沒有 fail-closed 的正當理由。
        if "config" in disabled:
            return
        reentrant[0] = True
        try:
            name = args[0] if args else None
            if isinstance(name, (str, bytes, os.PathLike)):
                text = os.fsdecode(name)
                matched = False
                if config_stat is not None:
                    try:
                        matched = os.path.samestat(os.stat(text), config_stat)
                    except OSError:
                        matched = False
                if matched:
                    config_opens.append("identity:" + os.path.basename(text))
                elif (
                    not os.path.isabs(text)
                    and os.path.basename(text) == config_basename
                ):
                    config_opens.append("basename:" + text)
        except Exception:
            pass
        finally:
            reentrant[0] = False
        return
    if event not in WATCHED:
        return
    if event in ("sys.setprofile", "sys.settrace", "_thread.start_new_thread"):
        if propagating_our_hook():
            return
    violations.append(event)


threading.setprofile(hook)
sys.setprofile(hook)
watching[0] = True
sys.addaudithook(audit)

status = "ok"
detail = ""
try:
    __import__(target)
except BaseException as exc:            # SystemExit included on purpose
    status = "import-failed"
    detail = "{{}}: {{}}".format(type(exc).__name__, exc)

for thread in list(threading.enumerate()):
    if thread is not threading.main_thread():
        thread.join(timeout=10)
alive = [
    thread.name
    for thread in threading.enumerate()
    if thread is not threading.main_thread() and thread.is_alive()
]

# 機制②：掃全 package 的**直接模組層屬性**，其中「型別定義在權威檔裡」的實例
# 算作預先建好的快照——不論是權威模組自己建的，還是別的模組自己建一個再轉交。
# （不要寫成「權威模組不匯出快照就沒有東西可以被轉交」：中間模組可以自己建構。）
snapshot_exports = []
for module_name, module in [] if "snapshot" in disabled else list(sys.modules.items()):
    module_file = getattr(module, "__file__", None)
    if not module_file:
        continue
    try:
        if not os.path.realpath(module_file).startswith(package_root):
            continue
        for attribute, value in list(vars(module).items()):
            if attribute.startswith("__"):
                continue
            owner = sys.modules.get(getattr(type(value), "__module__", ""), None)
            owner_file = getattr(owner, "__file__", None)
            if owner_file and os.path.realpath(owner_file) in authority_names:
                snapshot_exports.append("{{}}.{{}}".format(module_name, attribute))
    except Exception:
        continue

intact = sys.getprofile() is hook and getattr(threading, "_profile_hook", None) is hook
watching[0] = False
sys.setprofile(None)
threading.setprofile(None)

with open(envelope_path, "w", encoding="utf-8") as handle:
    handle.write(
        BEGIN
        + json.dumps(
            {{
                "protocol": PROTOCOL,
                "target": target,
                "status": status,
                "detail": detail,
                "reads": reads,
                "observed_calls": observed[0],
                "probe_intact": intact,
                "integrity_violations": sorted(set(violations)),
                "config_opens": sorted(set(config_opens)),
                "snapshot_exports": sorted(set(snapshot_exports)),
                "threads_alive": alive,
                "complete": True,
            }}
        )
        + END
    )
'''.format(protocol=AUDIT_PROTOCOL, begin=_AUDIT_BEGIN, end=_AUDIT_END)


Finding = collections.namedtuple(
    "Finding", "owner_file owner_line reader_file reader_line reader_name"
)


def _finding_from(stack, roots):
    """Split one call stack into the freezing owner and the actual reader.

    ``reader`` 是最內層落在 package 內的那一格——真正呼叫權威的地方。
    ``owner`` 是最內層、``co_name`` 為 ``<module>`` 的那一格——真正把值綁在模組
    層的地方。``A: FROZEN = B.helper()`` 兩者不同：報 ``B.helper`` 會讓下一個維
    護者跑去改 helper，但要改的是 A。取不到 module frame 時（例如讀取發生在執行
    緒裡）owner 退化成 reader，並照實呈現。

    ``stack[0]`` 是權威函式自己那一格（profiler 就是在它的 call 事件上觸發的），
    對「誰讀了它」沒有資訊，先丟掉。
    """
    stack = stack[1:] or stack
    inside = [
        frame
        for frame in stack
        if any(os.path.realpath(frame["file"]).startswith(str(root)) for root in roots)
    ]
    if not inside:
        inside = [stack[0]]
    reader = inside[0]
    owner = next((frame for frame in inside if frame["name"] == "<module>"), reader)
    return Finding(
        os.path.basename(owner["file"]),
        owner["line"],
        os.path.basename(reader["file"]),
        reader["line"],
        reader["name"],
    )


def _payload_from(text, target, where):
    """Parse exactly one envelope out of ``text``, or raise.

    檢查四件事，每一件不過就丟 :class:`ImportAuditError`，不回傳預設值：
    sentinel 恰好一組、body 是合法 JSON、鍵集合與 :data:`_AUDIT_REQUIRED_KEYS`
    相等、每個欄位的型別符合 :data:`_AUDIT_FIELD_TYPES`。
    """
    if text.count(_AUDIT_BEGIN) != 1 or text.count(_AUDIT_END) != 1:
        raise ImportAuditError(
            "{}：{} 裡的 completion sentinel 出現 {} 次，恰好要一次".format(
                target, where, text.count(_AUDIT_BEGIN)
            )
        )
    body = text[text.index(_AUDIT_BEGIN) + len(_AUDIT_BEGIN) : text.index(_AUDIT_END)]
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise ImportAuditError("{}：信封不是合法 JSON".format(target)) from exc
    if not isinstance(payload, dict):
        # 先判型別再算缺鍵：``set([[]])`` 會 unhashable、``set(True)`` 不可迭代，
        # 組錯誤訊息的時候自己炸掉，丟出來的就不是 ImportAuditError 了。
        raise ImportAuditError(
            "{}：信封最外層必須是 object，收到 {}".format(
                target, type(payload).__name__
            )
        )
    if set(payload) != _AUDIT_REQUIRED_KEYS:
        raise ImportAuditError(
            "{}：信封 schema 不符，缺 {}、多 {}".format(
                target,
                sorted(_AUDIT_REQUIRED_KEYS - set(payload)),
                sorted(set(payload) - _AUDIT_REQUIRED_KEYS),
            )
        )
    wrong = sorted(
        name for name, ok in _AUDIT_FIELD_TYPES.items() if not ok(payload[name])
    )
    if wrong:
        raise ImportAuditError(
            "{}：信封欄位型別不符 {}——鍵齊了不代表值可信，"
            "真值字串會讓後面每一道檢查都變成恆真".format(target, wrong)
        )
    return payload


def _envelope(finished, target, envelope_text):
    """Run the known envelope guards over one parsed payload.

    依序檢查：stdout 上沒有偽造 sentinel、信封存在、協定版本、目標相符、完成旗
    標、import 狀態、完整性違規、設定檔開啟、快照匯出、殘留執行緒、探針曾攔到
    呼叫、子行程回傳碼。任何一項不過就丟 :class:`ImportAuditError`；沒有任何一
    條路徑會退化成空 list。

    這些是**這個函式跑的 guard**——工具整體涵蓋什麼、不涵蓋什麼，見檔頭的威脅
    模型。
    """
    if _AUDIT_BEGIN in finished.stdout:
        raise ImportAuditError(
            "{}：目標的 stdout 出現了 sentinel——信封只能由稽核器經獨立檔案產生，"
            "stdout 上的一律視為偽造".format(target)
        )
    if not envelope_text:
        raise ImportAuditError(
            "{}：稽核子行程沒有寫出信封（rc={}）。空輸出或提前退出一律當成失敗，"
            "不是乾淨。stderr：{}".format(
                target, finished.returncode, finished.stderr[-800:]
            )
        )
    payload = _payload_from(envelope_text, target, "信封檔")
    if payload["protocol"] != AUDIT_PROTOCOL:
        raise ImportAuditError(
            "{}：稽核協定版本不符（{!r}）".format(target, payload["protocol"])
        )
    if payload["target"] != target:
        raise ImportAuditError(
            "{}：信封宣稱的目標是 {!r}，不是這一次要掃的模組".format(
                target, payload["target"]
            )
        )
    if not payload["complete"]:
        raise ImportAuditError("{}：稽核信封沒有標記完成".format(target))
    if payload["status"] != "ok":
        raise ImportAuditError(
            "{}：import 沒有正常完成——{}".format(target, payload["detail"])
        )
    if payload["integrity_violations"]:
        raise ImportAuditError(
            "{}：import 期間動了探針或離開了本行程 {}，結果不可信".format(
                target, payload["integrity_violations"]
            )
        )
    if payload["config_opens"]:
        raise ImportAuditError(
            "{}：import 期間開啟了規則設定檔 {}——設定值被凍結在 import 當下，"
            "reload 之後不會更新".format(target, payload["config_opens"])
        )
    if payload["snapshot_exports"]:
        raise ImportAuditError(
            "{}：模組層存著權威型別的實例 {}——那是一份預先建好的快照，"
            "reload 之後不會更新，而且可以被別的模組轉交出去".format(
                target, payload["snapshot_exports"]
            )
        )
    if not payload["probe_intact"]:
        raise ImportAuditError(
            "{}：探針在 import 期間被動過（profiler 已不在原位），結果不可信".format(
                target
            )
        )
    if payload["threads_alive"]:
        raise ImportAuditError(
            "{}：import 結束時仍有執行緒活著 {}，它們還可能讀權威".format(
                target, payload["threads_alive"]
            )
        )
    if not payload["observed_calls"]:
        raise ImportAuditError(
            "{}：profiler 一次呼叫都沒攔到，代表探針根本沒生效".format(target)
        )
    if finished.returncode != 0:
        raise ImportAuditError(
            "{}：稽核子行程回傳 {}".format(target, finished.returncode)
        )
    return payload["reads"]


def import_time_authority_reads(target, *, search_path, roots, authority_file,
                                config_file, disable=()):
    """Import ``target`` in a clean subprocess; return its import-time reads.

    回傳 ``[Finding]``。空 list 的意思是「信封上每一道檢查都過了，而且沒有偵測
    到讀取」——信封的任何一道檢查沒過都會丟 :class:`ImportAuditError`，不會退化
    成空 list。檢查涵蓋的失敗形態是一份已知清單（見
    :class:`AuditEnvelopeTest`），不是「所有可能的壞法」的窮舉。

    每個模組一個全新子行程：同一個行程裡掃的話，第二個模組會因為相依已在
    ``sys.modules`` 而不再執行 import 期程式碼，整批漏報。

    信封走獨立檔案而不是 stdout：目標模組印出來的東西不該和協定輸出混在同一條
    通道上——先印一個合法的乾淨信封就能把真的遮蔽掉。
    """
    handle, envelope_path = tempfile.mkstemp(prefix="b1-envelope-", suffix=".json")
    os.close(handle)
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        try:
            finished = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    _IMPORT_AUDIT_SOURCE,
                    str(search_path),
                    str(authority_file),
                    str(config_file),
                    target,
                    envelope_path,
                    ",".join(disable),
                ],
                capture_output=True,
                text=True,
                env=environment,
                timeout=180,
            )
        except subprocess.TimeoutExpired as exc:
            raise ImportAuditError("{}：稽核子行程逾時".format(target)) from exc
        with open(envelope_path, encoding="utf-8") as envelope_file:
            envelope_text = envelope_file.read()
        roots = tuple(os.path.realpath(str(root)) for root in roots)
        return [
            _finding_from(stack, roots)
            for stack in _envelope(finished, target, envelope_text)
        ]
    finally:
        os.unlink(envelope_path)


def package_root():
    from hoya_market_agents import debate_rules as module

    return Path(module.__file__).parent


def discover_modules(package, root):
    """Module names for every ``.py`` **source** file under ``root``.

    遞迴，含子套件與每一層 ``__init__``。只看原始碼檔：沒有 ``.py`` 的
    sourceless ``.pyc`` 仍然 import 得起來，但這裡看不到它。

    第 4 輪用 ``root.glob("*.py")``，看不到子套件；而 coverage 測試拿同一個 glob
    當預期值，於是自我印證。這裡遞迴，鑑別力測試則用另一種走法（``os.walk``）當
    預期值，不再用同一個函式驗自己。
    """
    root = Path(root)
    names = []
    for path in sorted(root.rglob("*.py")):
        parts = list(path.relative_to(root).parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][: -len(".py")]
        names.append(".".join([package] + parts))
    return names


def audit_package_imports(
    package="hoya_market_agents",
    root=None,
    search_path=None,
    authority_file=None,
    config_file=None,
    disable=(),
):
    """``{module: [Finding]}`` — 三個機制在 import 期間偵測到的結果。

    對 :func:`discover_modules` 列出的每個模組各開一個乾淨子行程；回傳有 finding
    的那些。涵蓋範圍與已知不涵蓋的形狀見檔頭的威脅模型。

    這是**唯一**的公開入口，而且吃參數：合成 package 的鑑別力測試走的就是這一條
    路徑。第 4 輪那一版是 hard-code 真 package，測試繞過它直接呼叫下層，所以把它
    整個掏空成 ``return {}`` 也沒有人發現。
    """
    root = package_root() if root is None else Path(root)
    search_path = str(root.parent) if search_path is None else str(search_path)
    if authority_file is None:
        authority_file = root / "debate_rules.py"
    if config_file is None:
        config_file = root.parent / "config" / "debate_rules.json"
    results = {}
    for name in discover_modules(package, root):
        findings = import_time_authority_reads(
            name,
            search_path=search_path,
            roots=(root,),
            authority_file=authority_file,
            config_file=config_file,
            disable=disable,
        )
        if findings:
            results[name] = findings
    return results


class ImportTimeAuthorityReadTest(unittest.TestCase):
    """盤點一：import 一個模組不得留下會過期的設定。

    這一條斷言的是**三個機制合起來**的結果，三者各查一件事：
      ① import 期間沒有人開那份設定檔；
      ② import 完成後沒有模組層存著權威型別的實例；
      ③ import 期間沒有人進入權威檔裡的函式。

    **沒有哪一個是另外兩個的超集。** ②存在的理由正是「有些快照不讀設定檔」——
    權威模組預先建好、再經中間模組轉交的那一種，①和③都看不到。所以這裡不寫
    「凍結值一定會讀，這條涵蓋它」之類的話：那句話是錯的，而且它一旦成立，②就
    沒有存在的必要了。

    每一個機制的涵蓋範圍與已知限制，見檔頭的威脅模型；判準是執行時的事實，不是
    對 Python 語法的近似。
    """

    def test_no_shipped_module_reads_the_authority_while_being_imported(self):
        self.assertEqual({}, audit_package_imports())

    def test_the_sweep_covers_the_package_itself_and_the_authority_module(self):
        """守門要有權威邊界，而且邊界不能靠同一個函式自我印證。

        第 4 輪的 coverage 測試用 ``glob("*.py")`` 當預期值，而被驗的函式也用同
        一個 glob——自己驗自己。這裡改用 ``os.walk`` 另外走一遍當 oracle，並明確
        釘住兩個第 4 輪其實沒被真的掃到的目標：package 自己（``__init__``）與權
        威模組本身。
        """
        root = package_root()
        names = discover_modules("hoya_market_agents", root)
        expected = set()
        for current, _directories, files in os.walk(root):
            for name in files:
                if not name.endswith(".py"):
                    continue
                relative = Path(current, name).relative_to(root)
                parts = list(relative.parts)
                if parts[-1] == "__init__.py":
                    parts = parts[:-1]
                else:
                    parts[-1] = parts[-1][: -len(".py")]
                expected.add(".".join(["hoya_market_agents"] + parts))

        self.assertEqual(expected, set(names))
        self.assertIn("hoya_market_agents", names)
        self.assertIn("hoya_market_agents.debate_rules", names)

    def test_the_authority_module_is_really_executed_while_being_audited(self):
        """權威模組自己被掃時，body 必須真的有跑到。

        第 4 輪的插樁要先 import 權威模組才裝得上，所以輪到掃它的時候
        ``sys.modules`` 已經有它了，body 不再執行——它在名單裡，卻從來沒有被真的
        掃過。profiler 在任何 import 之前就掛上，這個洞才關得掉；探針沒生效時
        ``observed_calls`` 會是 0，信封檢查就會擋下來。
        """
        root = package_root()

        findings = import_time_authority_reads(
            "hoya_market_agents.debate_rules",
            search_path=str(root.parent),
            roots=(root,),
            authority_file=root / "debate_rules.py",
            config_file=root.parent / "config" / "debate_rules.json",
        )

        self.assertEqual([], findings)


class ImportAuditDiscriminationTest(unittest.TestCase):
    """新工具對前兩輪 Reviewer 找到的**每一種**形狀都要判對。

    漏報方向（真的在 import 當下讀了）與誤報方向（沒讀）各自列表，逐一驗證。
    """

    SETTINGS = '{"value": 1}\n'
    # 合成的權威模組刻意鏡射真實結構：``load()`` 是唯一會碰設定檔的路徑，而它只
    # 在 lazy 路徑上被呼叫；``Snapshot`` 是「權威型別」，模組層存著它的實例就是
    # 一份預先建好的快照。
    AUTHORITY = (
        "import json\n"
        "from pathlib import Path\n"
        "\n"
        "SETTINGS_PATH = Path(__file__).parent / 'settings.json'\n"
        "_STATE = None\n"
        "\n"
        # frozen dataclass，和真實產品的 DebateRules／ConfidenceRules／LightStep／
        # DowngradeRule 同一種形狀。重點在於 dataclass 產生的 __init__ 其
        # co_filename 是 "<string>"，不在權威檔裡——所以建構一個實例**不會**產生
        # 權威檔的 frame，機制②因此可以被單獨驗證。第 8 輪用手寫 class，建構子
        # 就在權威檔裡，③會先抓到，我那句「沒有進入權威檔的任何函式」是錯的。
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class Snapshot:\n"
        "    value: int\n"
        "\n"
        "def reload():\n"
        "    global _STATE\n"
        "    _STATE = 1\n"
        "    return _STATE\n"
        "\n"
        "def read():\n"
        "    return _STATE\n"
        "\n"
        "def load():\n"
        "    return Snapshot(json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))['value'])\n"
    )
    HEADER = (
        "from typing import TYPE_CHECKING\n"
        "from .authority import read\n"
        "\n"
        "def derive():\n"
        "    return (read(),)\n"
        "\n"
    )

    FREEZING = {
        "plain-assignment": "MYSTERY_BOX = derive()\n",
        "conditional": "if True:\n    FROZEN = derive()\n",
        "else-branch": "if False:\n    pass\nelse:\n    FROZEN = derive()\n",
        "while": "_n = 1\nwhile _n:\n    FROZEN = derive()\n    _n = 0\n",
        "for": "for _ in (1,):\n    FROZEN = derive()\n",
        "for-else": "for _ in ():\n    pass\nelse:\n    FROZEN = derive()\n",
        "with": "import contextlib\nwith contextlib.suppress(Exception):\n    FROZEN = derive()\n",
        "try-body": "try:\n    FROZEN = derive()\nexcept Exception:\n    pass\n",
        "try-handler": "try:\n    raise ValueError\nexcept Exception:\n    FROZEN = derive()\n",
        "try-else": "try:\n    pass\nexcept Exception:\n    pass\nelse:\n    FROZEN = derive()\n",
        "try-finally": "try:\n    pass\nfinally:\n    FROZEN = derive()\n",
        "match": "match 1:\n    case 1:\n        FROZEN = derive()\n",
        "class-body": "class Holder:\n    FROZEN = derive()\n",
        "class-body-if": "class Holder:\n    if True:\n        FROZEN = derive()\n",
        "tuple-unpack": "LEFT, RIGHT = derive() + derive()\n",
        "list-unpack": "[A, B] = derive() + derive()\n",
        "starred-unpack": "HEAD, *TAIL = derive() + derive()\n",
        "nested-unpack": "OUTER, (L, R) = derive(), derive() + derive()\n",
        "walrus-expression": "FROZEN = (_w := derive())\n",
        "walrus-in-if": "if (_w := derive()):\n    FROZEN = _w\n",
        "decorator-argument": (
            "def tag(value):\n"
            "    def wrap(fn):\n"
            "        fn.tag = value\n"
            "        return fn\n"
            "    return wrap\n"
            "@tag(derive())\n"
            "def decorated():\n"
            "    return 1\n"
        ),
        "decorator-direct": (
            "def take(fn):\n"
            "    return fn\n"
            "def outer():\n"
            "    read()\n"
            "    return take\n"
            "@outer()\n"
            "def decorated():\n"
            "    return 1\n"
        ),
        "default-argument": "def use(table=derive()):\n    return table\n",
        "keyword-only-default": "def use(*, table=derive()):\n    return table\n",
        "class-base": (
            "def base_of():\n"
            "    read()\n"
            "    return object\n"
            "class Holder(base_of()):\n"
            "    pass\n"
        ),
        "metaclass-keyword": (
            "def meta_of():\n"
            "    read()\n"
            "    return type\n"
            "class Holder(metaclass=meta_of()):\n"
            "    pass\n"
        ),
        "annotation": "FROZEN: derive() = 1\n",
        "annotation-only": "FROZEN: derive()\n",
        "class-annotation": "class Holder:\n    FROZEN: derive()\n",
        "aug-assign": "FROZEN = ()\nFROZEN += derive()\n",
        "subscript-mutation": "BOX = {}\nBOX['rules'] = derive()\n",
        "lambda-alias-then-call": "maker = lambda: derive()\nFROZEN = maker()\n",
        "immediately-invoked-lambda": "FROZEN = (lambda: derive())()\n",
        "comprehension": "FROZEN = [derive() for _ in range(1)]\n",
        "consumed-generator": "FROZEN = tuple(derive() for _ in range(1))\n",
        "module-level-call-in-fstring": "FROZEN = f'{derive()}'\n",
    }

    DEFERRED = {
        "type-checking-branch": "if TYPE_CHECKING:\n    FROZEN = derive()\n",
        "unconsumed-generator": "LAZY = (derive() for _ in range(1))\n",
        "lambda-not-called": "LAZY = lambda: derive()\n",
        "lambda-alias-not-called": "maker = lambda: derive()\nALIAS = maker\n",
        "nested-def": "def factory():\n    def inner():\n        return read()\n    return inner\n",
        "reference-without-call": "ALIAS = derive\n",
        "method-body": "class Holder:\n    def read_now(self):\n        return derive()\n",
        "module-level-global": "_LOCAL = None\ndef set_it():\n    global _LOCAL\n    _LOCAL = 1\n",
        "call-every-time": "def use():\n    return derive()\n",
        "false-branch": "if False:\n    FROZEN = derive()\n",
        "decorator-not-invoked": (
            "def take(fn):\n"
            "    return fn\n"
            "@take\n"
            "def decorated():\n"
            "    return derive()\n"
        ),
        "default-lambda": "def use(getter=lambda: derive()):\n    return getter\n",
    }

    @classmethod
    def setUpClass(cls):
        """Build **one** synthetic package holding every shape, and sweep it once.

        第 5 輪每個形狀各建一個 package、各走一次正式入口，於是 48 個形狀就開了
        約 144 個子行程（實測全體 219 個）。形狀彼此獨立，放在同一個 package 裡
        每個仍然各自一個乾淨子行程——隔離性沒有變，只是不再重複掃 ``__init__``
        與 ``authority``。
        """
        cls._directory = Path(tempfile.mkdtemp(prefix="b1-audit-matrix-"))
        root = cls._directory / "auditee"
        root.mkdir()
        (root / "__init__.py").write_text("", encoding="utf-8")
        (root / "authority.py").write_text(cls.AUTHORITY, encoding="utf-8")
        (root / "settings.json").write_text(cls.SETTINGS, encoding="utf-8")
        cls._names = {}
        for index, (label, consumer) in enumerate(
            list(cls.FREEZING.items()) + list(cls.DEFERRED.items())
        ):
            module = "shape{:03d}".format(index)
            (root / (module + ".py")).write_text(
                cls.HEADER + consumer, encoding="utf-8"
            )
            cls._names[label] = "auditee." + module
        cls._results = audit_package_imports(
            package="auditee",
            root=root,
            search_path=cls._directory,
            authority_file=root / "authority.py",
            config_file=root / "settings.json",
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._directory, ignore_errors=True)

    def findings_for(self, label):
        return self._results.get(self._names[label], [])

    def build(self, modules, package="auditee", authority=None):
        """Write a synthetic package and return the arguments the entry point takes."""
        directory = Path(tempfile.mkdtemp(prefix="b1-audit-"))
        self.addCleanup(shutil.rmtree, directory, True)
        root = directory / package
        root.mkdir()
        (root / "__init__.py").write_text("", encoding="utf-8")
        (root / "authority.py").write_text(
            authority or self.AUTHORITY, encoding="utf-8"
        )
        (root / "settings.json").write_text(self.SETTINGS, encoding="utf-8")
        for name, source in modules.items():
            path = root.joinpath(*name.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        return {
            "package": package,
            "root": root,
            "search_path": directory,
            "authority_file": root / "authority.py",
            "config_file": root / "settings.json",
        }

    def sweep(self, modules, package="auditee", authority=None, disable=()):
        """Run the **public** entry point over a synthetic package.

        走正式入口是重點：第 4 輪的矩陣直接呼叫下層，所以把
        ``audit_package_imports`` 掏空成 ``return {}`` 也沒有人發現。
        """
        return audit_package_imports(
            disable=disable, **self.build(modules, package, authority)
        )

    def audit(self, consumer):
        """One ad-hoc shape, swept through the public entry point."""
        return self.sweep({"consumer.py": self.HEADER + consumer}).get(
            "auditee.consumer", []
        )

    def test_every_shape_that_reads_at_import_is_reported(self):
        for label in self.FREEZING:
            with self.subTest(shape=label):
                self.assertTrue(
                    self.findings_for(label),
                    "{} 在 import 當下讀了權威，卻沒有被報出來".format(label),
                )

    def test_every_deferred_shape_is_not_reported(self):
        for label in self.DEFERRED:
            with self.subTest(shape=label):
                self.assertEqual(
                    [],
                    self.findings_for(label),
                    "{} 在 import 當下沒有讀權威，不該被報".format(label),
                )

    def test_a_finding_names_the_file_and_the_line(self):
        findings = self.audit("MYSTERY_BOX = derive()\n")

        self.assertEqual(1, len(findings))
        finding = findings[0]
        self.assertEqual("consumer.py", finding.owner_file)
        self.assertGreater(finding.owner_line, 0)
        self.assertEqual("consumer.py", finding.reader_file)
        self.assertEqual("derive", finding.reader_name)

    def test_a_finding_separates_the_freezing_owner_from_the_reader(self):
        """F3：報 callee 會讓下一個人跑去改錯地方。

        ``A: FROZEN = B.helper()`` 真正把值綁在模組層的是 A，實際呼叫權威的是
        ``B.helper``。兩個都要報：owner 指出該改哪裡，reader 指出讀是在哪裡發生
        的。
        """
        findings = self.sweep(
            {
                "middle.py": "from .authority import read\ndef helper():\n    return read()\n",
                "consumer.py": "from .middle import helper\nFROZEN = helper()\n",
            }
        )["auditee.consumer"]

        self.assertEqual(1, len(findings))
        finding = findings[0]
        self.assertEqual("consumer.py", finding.owner_file)
        self.assertEqual(2, finding.owner_line)
        self.assertEqual("middle.py", finding.reader_file)
        self.assertEqual("helper", finding.reader_name)

    def test_a_module_that_freezes_during_its_own_import_owns_the_finding(self):
        """A import B、B 自己在 import 期凍結時，owner 是 B 不是 A。"""
        findings = self.sweep(
            {
                "middle.py": "from .authority import read\nFROZEN = read()\n",
                "consumer.py": "from .middle import FROZEN\n",
            }
        )

        self.assertEqual(
            ("middle.py", 2, "middle.py", "<module>"),
            (
                findings["auditee.middle"][0].owner_file,
                findings["auditee.middle"][0].owner_line,
                findings["auditee.middle"][0].reader_file,
                findings["auditee.middle"][0].reader_name,
            ),
        )


class ImportAuditFailsClosedTest(unittest.TestCase):
    """稽核器壞掉的**已知**方式，每一種都要變成看得見的錯誤，不是綠燈。

    兩位 Reviewer 在第 4 輪各自找到七種讓它靜靜回報乾淨的方法，之後幾輪又陸續
    找到更多。它們是同一件事：``finished.stdout or "[]"`` 把「我沒有答案」翻譯
    成「答案是乾淨」。這一組逐一證明**清單上的每一種**現在都會丟
    :class:`ImportAuditError`，或者被如實報成 finding。

    這是一份**被找到過的清單**，不是「所有可能的壞法」——每多一輪 Review 就多
    幾條。新形狀出現時該做的是加進來，不是把這句話寫成全稱。
    """

    AUTHORITY = ImportAuditDiscriminationTest.AUTHORITY
    SETTINGS = ImportAuditDiscriminationTest.SETTINGS
    HEADER = ImportAuditDiscriminationTest.HEADER
    build = ImportAuditDiscriminationTest.build
    sweep = ImportAuditDiscriminationTest.sweep

    def test_a_module_that_kills_the_probe_is_reported_not_cleared(self):
        """插樁被清掉 → probe tampering，不是乾淨。"""
        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": "import sys\nsys.setprofile(None)\n"})

        self.assertIn("探針", str(caught.exception))

    def test_a_module_that_exits_zero_is_reported_not_cleared(self):
        """``SystemExit(0)`` → import 沒有正常完成，不是乾淨。"""
        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": "raise SystemExit(0)\n"})

        self.assertIn("import 沒有正常完成", str(caught.exception))

    def test_a_module_that_hard_exits_is_reported_not_cleared(self):
        """``os._exit(0)`` 連 atexit 都不跑 → 沒有 sentinel，不是乾淨。"""
        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": "import os\nos._exit(0)\n"})

        self.assertIn("沒有寫出信封", str(caught.exception))

    def test_a_module_that_leaves_a_thread_running_is_reported_not_cleared(self):
        """import 結束時還在跑的執行緒 → fail-closed，因為它還可能讀權威。"""
        source = (
            "import threading, time\n"
            "def spin():\n"
            "    time.sleep(30)\n"
            "threading.Thread(target=spin, daemon=True).start()\n"
        )

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("仍有執行緒活著", str(caught.exception))

    def test_a_read_from_a_late_joined_thread_is_still_counted(self):
        """import 返回後才由執行緒讀 → join 之後仍然算得到。"""
        source = (
            "import threading\n"
            "from .authority import read\n"
            "worker = threading.Thread(target=read)\n"
            "worker.start()\n"
        )

        findings = self.sweep({"consumer.py": source})

        self.assertIn("auditee.consumer", findings)

    def test_reloading_the_authority_cannot_shake_off_the_probe(self):
        """``importlib.reload`` 換掉的是名字綁定，騙不過直譯器層的 profiler。"""
        source = (
            "import importlib\n"
            "from . import authority\n"
            "importlib.reload(authority)\n"
            "FROZEN = authority.read()\n"
        )

        findings = self.sweep({"consumer.py": source})

        self.assertIn("auditee.consumer", findings)

    def test_restoring_the_original_function_cannot_shake_off_the_probe(self):
        """目標模組把原函式塞回 globals，一樣騙不過 profiler。"""
        source = (
            "from . import authority\n"
            "original = authority.read.__wrapped__ if hasattr(authority.read, '__wrapped__') else authority.read\n"
            "authority.read = original\n"
            "FROZEN = authority.read()\n"
        )

        findings = self.sweep({"consumer.py": source})

        self.assertIn("auditee.consumer", findings)

    def test_a_nested_subpackage_is_enumerated_and_swept(self):
        """子套件不再隱形；``__init__`` 也算一個模組。"""
        modules = {
            "nested/__init__.py": "",
            "nested/hidden.py": "from ..authority import read\nFROZEN = read()\n",
        }
        arguments = self.build(modules)
        names = discover_modules(arguments["package"], arguments["root"])

        self.assertIn("auditee.nested", names)
        self.assertIn("auditee.nested.hidden", names)
        self.assertIn("auditee.nested.hidden", audit_package_imports(**arguments))

    def test_a_clean_synthetic_package_really_comes_back_clean(self):
        """FP 方向：fail-closed 不得退化成「什麼都報錯」。"""
        modules = {"consumer.py": "from .authority import read\ndef use():\n    return read()\n"}

        self.assertEqual({}, self.sweep(modules))

    def test_a_module_that_temporarily_disables_the_probe_is_reported_not_cleared(self):
        """暫時關掉 profiler 讀完再裝回去——結束時的完整性檢查看不到，audit hook 看得到。

        這是第 5 輪 ``observed_calls`` 的盲點：它證明的是探針**曾**生效，不是
        **全程**生效。
        """
        source = (
            "import sys\n"
            "from .authority import read\n"
            "saved = sys.getprofile()\n"
            "sys.setprofile(None)\n"
            "FROZEN = read()\n"
            "sys.setprofile(saved)\n"
        )

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("sys.setprofile", str(caught.exception))

    def test_a_module_that_clears_the_thread_profile_hook_is_reported_not_cleared(self):
        """``threading.setprofile`` 沒有標準 audit event——這一格只有結束時的完整性檢查守得住。

        這裡講的是**探針完整性那兩個 guard 之間**的分工；工具整體涵蓋什麼、不涵蓋
        什麼見檔頭的威脅模型，這裡不複述。audit hook 盯 ``sys.setprofile``／fork／子行程（整段期
        間、移不掉），``probe_intact`` 盯結束時探針是否還在原位。第 6 輪的
        mutation 抓到我原本沒有任何端到端測試會走到 ``probe_intact`` 那一格——
        audit hook 把它遮住了，於是那道檢查形同沒人驗。
        """
        source = "import threading\nthreading.setprofile(None)\n"

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("探針", str(caught.exception))

    def test_a_module_that_forks_is_reported_not_cleared(self):
        source = "import os\npid = os.fork()\nif pid == 0:\n    os._exit(0)\nos.waitpid(pid, 0)\n"

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("os.fork", str(caught.exception))

    def test_a_module_that_spawns_a_subprocess_is_reported_not_cleared(self):
        """讀取離開本行程就離開了 profiler 的視野。"""
        source = (
            "import subprocess, sys\n"
            "subprocess.run([sys.executable, '-c', 'pass'], check=False)\n"
        )

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("subprocess.Popen", str(caught.exception))

    def test_the_audit_hook_still_fires_after_an_attempt_to_remove_it(self):
        """audit hook 的賣點就是裝上就移不掉——這一條把那個保證釘住。

        目標模組先把 ``sys.addaudithook`` 與 ``sys.audit`` 換掉、再暫時關閉
        profiler 去讀。已經裝好的 hook 握在直譯器內部，換名字動不到它。
        """
        source = (
            "import sys\n"
            "from .authority import read\n"
            "sys.addaudithook = lambda *a, **k: None\n"
            "try:\n"
            "    sys.audit = lambda *a, **k: None\n"
            "except Exception:\n"
            "    pass\n"
            "saved = sys.getprofile()\n"
            "sys.setprofile(None)\n"
            "FROZEN = read()\n"
            "sys.setprofile(saved)\n"
        )

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("sys.setprofile", str(caught.exception))

    def test_a_forged_envelope_on_stdout_cannot_mask_a_real_read(self):
        """先印一個乾淨信封再讀——協定走獨立檔案，遮蔽不了。"""
        source = (
            "from .authority import read\n"
            "print('<<<HOYA-IMPORT-AUDIT {\"protocol\": 4} HOYA-IMPORT-AUDIT>>>')\n"
            "FROZEN = read()\n"
        )

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("偽造", str(caught.exception))

    def test_a_forged_envelope_then_hard_exit_is_still_refused(self):
        source = (
            "print('<<<HOYA-IMPORT-AUDIT {\"protocol\": 4} HOYA-IMPORT-AUDIT>>>')\n"
            "import os\n"
            "os._exit(0)\n"
        )

        with self.assertRaises(ImportAuditError):
            self.sweep({"consumer.py": source})

    def test_a_worker_thread_with_the_probe_switched_off_is_reported_not_cleared(self):
        """H1：Reviewer 的原始重現——第 6 輪我宣稱已修，其實還在。

        ``threading.setprofile(None)`` 之後開執行緒去讀：worker 裡那個
        ``sys.setprofile`` 的呼叫端**真的是** ``threading.py``，光看檔名的白名單
        會放行；結束前又恢復，所以完整性檢查也過。放行前額外要求「傳播的就是我
        們這一個 hook」才擋得住。
        """
        source = (
            "import threading\n"
            "from .authority import read\n"
            "box = []\n"
            "saved = threading._profile_hook\n"
            "threading.setprofile(None)\n"
            "t = threading.Thread(target=lambda: box.append(read()))\n"
            "t.start(); t.join()\n"
            "threading.setprofile(saved)\n"
            "FROZEN = box[0]\n"
        )

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("_thread.start_new_thread", str(caught.exception))

    def test_a_legitimate_worker_thread_is_not_flagged(self):
        """FP 方向：白名單收緊不得把正常的 threading.Thread 判成違規。"""
        source = (
            "import threading\n"
            "def quiet():\n"
            "    return 1\n"
            "t = threading.Thread(target=quiet)\n"
            "t.start(); t.join()\n"
        )

        self.assertEqual({}, self.sweep({"consumer.py": source}))

    def test_a_module_that_execs_is_reported_not_cleared(self):
        """``os.exec*`` 是清單裡的冗餘項——這一條把「冗餘」的理由變成事實。

        行程映像被換掉之後信封永遠寫不出來，所以就算沒有 audit event，缺信封那
        道守衛也會 fail-closed。
        """
        source = "import os, sys\nos.execv(sys.executable, [sys.executable, '-c', 'pass'])\n"

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("沒有寫出信封", str(caught.exception))

    def test_a_module_that_shells_out_is_reported_not_cleared(self):
        """H2：``os.system`` 有標準 audit event，子行程的讀取離開了 profiler 視野。"""
        source = "import os\nos.system('true')\n"

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("os.system", str(caught.exception))

    def test_a_module_that_forkptys_is_reported_not_cleared(self):
        """H2：``os.forkpty`` 原本在清單裡卻沒有端到端測試。"""
        source = (
            "import os\n"
            "pid, fd = os.forkpty()\n"
            "if pid == 0:\n"
            "    os._exit(0)\n"
            "os.waitpid(pid, 0)\n"
        )

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("os.forkpty", str(caught.exception))

    def test_a_module_that_posix_spawns_is_reported_not_cleared(self):
        """H2：``os.posix_spawn`` 原本在清單裡卻沒有端到端測試。"""
        source = (
            "import os, sys\n"
            "pid = os.posix_spawn(sys.executable, [sys.executable, '-c', 'pass'], os.environ)\n"
            "os.waitpid(pid, 0)\n"
        )

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("os.posix_spawn", str(caught.exception))

    def test_a_raw_thread_that_reads_is_reported_not_cleared(self):
        """H4：``_thread.start_new_thread`` **有**標準 audit event，直接監看。

        第 6 輪報告說它沒有——那句話是錯的，實測 Python 3.12.3 會發出。
        """
        source = (
            "import _thread, time\n"
            "from .authority import read\n"
            "box = []\n"
            "_thread.start_new_thread(lambda: box.append(read()), ())\n"
            "time.sleep(0.3)\n"
        )

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("_thread.start_new_thread", str(caught.exception))

    def test_any_entry_into_the_authority_file_is_a_read(self):
        """機制③：不看函式名，看有沒有進入權威檔。

        真實系統的 ``load_debate_rules(path)`` 是公開 API。列舉函式名的話，它、
        ``reload_debate_rules``、以及下一個人新增的入口都會漏掉。

        這裡刻意選兩個**不碰設定檔**的入口（``read``／``reload``），好讓這條測試
        單獨證明③——會碰設定檔的 ``load`` 由機制①攔下（見
        ``test_a_copied_loader_that_reads_the_config_is_reported_not_cleared``），
        那是另一條防線。
        """
        for entry in ("read", "reload"):
            with self.subTest(entry=entry):
                source = "from .authority import {0}\nFROZEN = {0}()\n".format(entry)

                findings = self.sweep({"consumer.py": source})

                self.assertIn("auditee.consumer", findings)
                self.assertEqual(
                    "consumer.py", findings["auditee.consumer"][0].owner_file
                )

    def test_the_authority_file_calling_itself_is_not_a_read(self):
        """FP 方向：權威檔內部彼此呼叫、以及它自己被 import，都不算。"""
        source = "def use():\n    return 1\n"

        self.assertEqual({}, self.sweep({"consumer.py": source}))

    def test_a_copied_loader_that_reads_the_config_is_reported_not_cleared(self):
        """**機制①的局部不變式**是「import 期間不得有人讀那份設定檔」。

        這是①自己的契約，**不是整個工具的不變式**——整體契約是三個機制合起來的
        結果，見檔頭。不讀設定檔也拿得到快照的形狀由②負責。

        在①的範圍內：監看權威函式、監看權威檔案都只是這句話的代理指標，複製一
        份載入器就繞過去了；監看開檔則直接對上它——值是從那個檔案來的，就要開
        它，而且比的是檔案身分不是路徑字串，所以別名寫法也算同一個檔案。
        """
        source = (
            "import json\n"
            "from pathlib import Path\n"
            "SETTINGS = Path(__file__).parent / 'settings.json'\n"
            "FROZEN = json.loads(SETTINGS.read_text(encoding='utf-8'))['value']\n"
        )

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("開啟了規則設定檔", str(caught.exception))

    def test_an_authority_that_exports_a_snapshot_is_reported_not_cleared(self):
        """機制②：權威模組自己在 module body 先建好一份快照。

        這是②要抓的兩種來源之一；另一種是別的模組自己建一個再轉交，見
        ``test_a_snapshot_held_by_a_middle_module_is_reported_not_cleared``。
        """
        # 刻意用 Snapshot(1) 而不是 load()：load() 會開設定檔，機制①先攔下來，
        # 這條就證明不了②。不碰檔案地把實例留在模組層，只有②看得到。
        greedy = self.AUTHORITY + "DEFAULT = Snapshot(1)\n"

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": "from .authority import DEFAULT\n"}, authority=greedy)

        self.assertIn("預先建好的快照", str(caught.exception))

    SNAPSHOT_VIA_MIDDLE = {
        "middle.py": "from .authority import Snapshot\nSNAP = Snapshot(1)\n",
        "consumer.py": "from .middle import SNAP\nFROZEN = SNAP\n",
    }

    def test_a_snapshot_held_by_a_middle_module_is_reported_not_cleared(self):
        """機制②：從已持有快照的其他模組取得物件。"""
        with self.assertRaises(ImportAuditError) as caught:
            self.sweep(self.SNAPSHOT_VIA_MIDDLE)

        self.assertIn("auditee.middle.SNAP", str(caught.exception))

    def test_only_mechanism_two_catches_the_middle_module_snapshot(self):
        """②單獨承重的**決定性**證據：停用②之後結果必須是 ``{}``。

        第 8 輪我用「有被抓到」當作隔離證明，兩位 Reviewer 各自停用②，發現它其
        實是被③抓到的——手寫 class 的建構子就在權威檔裡。斷言「停用之後乾淨」
        才是隔離，斷言「有被抓到」不是。
        """
        self.assertEqual(
            {}, self.sweep(self.SNAPSHOT_VIA_MIDDLE, disable=("snapshot",))
        )

    def test_the_other_two_mechanisms_are_each_load_bearing_too(self):
        """同一把尺量另外兩個機制：各自停用之後，只有它抓得到的形狀會變乾淨。"""
        copied_loader = {
            "consumer.py": (
                "import json\n"
                "from pathlib import Path\n"
                "SETTINGS = Path(__file__).parent / 'settings.json'\n"
                "FROZEN = json.loads(SETTINGS.read_text(encoding='utf-8'))['value']\n"
            )
        }
        entering = {"consumer.py": "from .authority import read\nFROZEN = read()\n"}

        self.assertEqual({}, self.sweep(copied_loader, disable=("config",)))
        self.assertEqual({}, self.sweep(entering, disable=("frames",)))

    def test_a_lazy_consumer_that_never_reads_at_import_is_clean(self):
        """FP 方向：合法的 lazy 讀取不是 import 期，不得被誤判。

        真實系統的 ``debate_rules()`` 就是這個形狀：函式定義好，第一次被**呼叫**
        時才載入。import 的時候什麼都沒發生。
        """
        modules = {
            "consumer.py": (
                "from .authority import load\n"
                "def settings():\n"
                "    return load()\n"
            )
        }

        self.assertEqual({}, self.sweep(modules))

    def test_the_authority_module_on_its_own_is_clean(self):
        """FP 方向：權威模組正常被 import 不得被誤判。

        它的 module body 只建路徑、定義類別與函式，不開設定檔、不建快照。
        """
        self.assertEqual({}, self.sweep({}))

    def alias_consumer(self, name):
        return {
            "consumer.py": (
                "import json\n"
                "from pathlib import Path\n"
                "ALIAS = Path(__file__).parent / {!r}\n"
                "FROZEN = json.loads(ALIAS.read_text(encoding='utf-8'))['value']\n"
            ).format(name)
        }

    def test_a_hardlinked_alias_of_the_config_is_still_caught(self):
        """J1：比的是檔案身分，不是路徑字串。

        hardlink 是同一個 inode 的另一個名字——字串完全不同，``samestat`` 一樣認
        得出來。
        """
        arguments = self.build(self.alias_consumer("linked.json"))
        os.link(arguments["config_file"], arguments["root"] / "linked.json")

        with self.assertRaises(ImportAuditError) as caught:
            audit_package_imports(**arguments)

        self.assertIn("開啟了規則設定檔", str(caught.exception))
        self.assertIn("identity:", str(caught.exception))

    def test_a_symlinked_alias_of_the_config_is_still_caught(self):
        arguments = self.build(self.alias_consumer("linked.json"))
        (arguments["root"] / "linked.json").symlink_to(arguments["config_file"])

        with self.assertRaises(ImportAuditError) as caught:
            audit_package_imports(**arguments)

        self.assertIn("identity:", str(caught.exception))

    def test_a_case_alias_of_the_config_is_still_caught(self):
        """Reviewer 在 DrvFs 上用大小寫別名穿透三個機制——身分比對關掉它。

        ext4 之類的大小寫敏感檔案系統上，``SETTINGS.JSON`` 是**另一個檔案**，這
        個形狀根本不存在，所以那裡跳過。判準本身不分平台：同一個 inode 就是同一
        個檔案。
        """
        arguments = self.build(self.alias_consumer("SETTINGS.JSON"))
        alias = arguments["root"] / "SETTINGS.JSON"
        if not alias.exists():
            self.skipTest("此檔案系統大小寫敏感，別名指向不存在的檔案")

        with self.assertRaises(ImportAuditError) as caught:
            audit_package_imports(**arguments)

        self.assertIn("開啟了規則設定檔", str(caught.exception))

    def test_a_relative_open_through_a_directory_fd_is_caught_conservatively(self):
        """J1：``open`` audit event **不帶 dir_fd**，所以相對路徑解析不到正確基準。

        身分比對在這一種上會失敗（``os.stat("settings.json")`` 解到的是 CWD 底
        下的東西）。所以同名的相對開檔保守攔截，並標成 ``basename:`` 以便分辨誤
        報——這一項的取捨寫在威脅模型裡。
        """
        source = (
            "import os\n"
            "directory = os.open(os.path.dirname(__file__), os.O_RDONLY)\n"
            "fd = os.open('settings.json', os.O_RDONLY, dir_fd=directory)\n"
            "FROZEN = os.read(fd, 64)\n"
            "os.close(fd)\n"
            "os.close(directory)\n"
        )

        with self.assertRaises(ImportAuditError) as caught:
            self.sweep({"consumer.py": source})

        self.assertIn("basename:", str(caught.exception))

    def test_an_absolute_same_named_file_is_not_mistaken_for_the_config(self):
        """K1 的 FP 方向：絕對路徑的同名檔案是**可以判定**的，判定為否就該乾淨。

        保守攔截只對相對路徑有意義——``open`` audit event 不帶 dir_fd，那才是真
        的解不到基準。絕對路徑用 ``os.stat`` 就能決定性地證明它是不同的 inode，
        對它 fail-closed 沒有正當理由。
        """
        directory = Path(tempfile.mkdtemp(prefix="b1-lookalike-"))
        self.addCleanup(shutil.rmtree, directory, True)
        lookalike = directory / "settings.json"
        lookalike.write_text('{"value": 999}\n', encoding="utf-8")
        source = (
            "import json\n"
            "from pathlib import Path\n"
            "OTHER = Path({!r})\n"
            "VALUE = json.loads(OTHER.read_text(encoding='utf-8'))['value']\n"
        ).format(str(lookalike))

        arguments = self.build({"consumer.py": source})
        self.assertNotEqual(
            os.stat(lookalike).st_ino, os.stat(arguments["config_file"]).st_ino
        )

        self.assertEqual({}, audit_package_imports(**arguments))

    def test_an_unrelated_file_is_not_mistaken_for_the_config(self):
        """FP 方向：保守攔截只針對**同名**的開檔，其他檔案不得被誤判。"""
        modules = {
            "consumer.py": (
                "from pathlib import Path\n"
                "OTHER = Path(__file__).parent / 'notes.txt'\n"
                "OTHER.write_text('hello', encoding='utf-8')\n"
                "TEXT = OTHER.read_text(encoding='utf-8')\n"
            )
        }

        self.assertEqual({}, self.sweep(modules))


class AuditEnvelopeTest(unittest.TestCase):
    """信封上的每一道檢查各自要有殺得掉它的測試。

    第 5 輪的 mutation 抓到：把協定版本檢查改成 ``if False:`` 之後全套照樣綠——
    那道檢查存在，卻沒有任何東西證明它在把關。「檢查寫了但沒人驗」跟「沉默的失
    敗」是同一類，所以這裡逐道拆開驗。
    """

    def envelope(self, payload=None, envelope_text=None, stdout="", returncode=0):
        if envelope_text is None:
            body = dict(
                {
                    "protocol": AUDIT_PROTOCOL,
                    "target": "t",
                    "status": "ok",
                    "detail": "",
                    "reads": [],
                    "observed_calls": 7,
                    "probe_intact": True,
                    "integrity_violations": [],
                    "config_opens": [],
                    "snapshot_exports": [],
                    "threads_alive": [],
                    "complete": True,
                },
                **(payload or {})
            )
            envelope_text = _AUDIT_BEGIN + json.dumps(body) + _AUDIT_END
        finished = types.SimpleNamespace(
            stdout=stdout, stderr="", returncode=returncode
        )
        return _envelope(finished, "t", envelope_text)

    def test_a_well_formed_envelope_is_accepted(self):
        """FP 方向：正常信封不得被擋。"""
        self.assertEqual([], self.envelope())

    def test_a_missing_envelope_is_refused(self):
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope(envelope_text="")

        self.assertIn("沒有寫出信封", str(caught.exception))

    def test_a_malformed_envelope_is_refused(self):
        """H5：壞 JSON 那道守衛原本沒有測試，也沒有 expected killer。

        它不會 fail-open（只會漏出原始 ``JSONDecodeError``），但「不會 fail-open」
        跟「有人驗過」是兩件事。
        """
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope(envelope_text=_AUDIT_BEGIN + "{not json" + _AUDIT_END)

        self.assertIn("不是合法 JSON", str(caught.exception))

    def test_a_sentinel_on_stdout_is_refused_as_forgery(self):
        """目標印出來的東西不得冒充協定輸出。"""
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope(stdout=_AUDIT_BEGIN + "{}" + _AUDIT_END)

        self.assertIn("偽造", str(caught.exception))

    def test_two_envelopes_are_refused(self):
        """恰好一個：多出來的一個代表有人在寫協定通道。"""
        good = _AUDIT_BEGIN + json.dumps({"protocol": AUDIT_PROTOCOL}) + _AUDIT_END

        with self.assertRaises(ImportAuditError) as caught:
            self.envelope(envelope_text=good + good)

        self.assertIn("恰好要一次", str(caught.exception))

    def test_an_envelope_with_extra_or_missing_keys_is_refused(self):
        for mutation in ({"extra": 1}, {"drop": "reads"}):
            with self.subTest(mutation=mutation):
                body = {
                    "protocol": AUDIT_PROTOCOL,
                    "target": "t",
                    "status": "ok",
                    "detail": "",
                    "reads": [],
                    "observed_calls": 7,
                    "probe_intact": True,
                    "integrity_violations": [],
                    "config_opens": [],
                    "snapshot_exports": [],
                    "threads_alive": [],
                    "complete": True,
                }
                if "drop" in mutation:
                    body.pop(mutation["drop"])
                else:
                    body.update(mutation)
                text = _AUDIT_BEGIN + json.dumps(body) + _AUDIT_END
                with self.assertRaises(ImportAuditError) as caught:
                    self.envelope(envelope_text=text)

                self.assertIn("schema 不符", str(caught.exception))

    def test_an_envelope_for_another_target_is_refused(self):
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope({"target": "somebody.else"})

        self.assertIn("信封宣稱的目標", str(caught.exception))

    def test_a_wrong_protocol_version_is_refused(self):
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope({"protocol": AUDIT_PROTOCOL + 1})

        self.assertIn("協定版本", str(caught.exception))

    def test_an_incomplete_envelope_is_refused(self):
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope({"complete": False})

        self.assertIn("完成", str(caught.exception))

    def test_a_failed_import_is_refused(self):
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope({"status": "import-failed", "detail": "SystemExit: 0"})

        self.assertIn("import 沒有正常完成", str(caught.exception))

    def test_an_integrity_violation_is_refused(self):
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope({"integrity_violations": ["os.fork"]})

        self.assertIn("動了探針或離開了本行程", str(caught.exception))

    def test_a_config_open_is_refused(self):
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope({"config_opens": ["debate_rules.json"]})

        self.assertIn("開啟了規則設定檔", str(caught.exception))

    def test_a_snapshot_export_is_refused(self):
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope({"snapshot_exports": ["pkg.mod.FROZEN"]})

        self.assertIn("預先建好的快照", str(caught.exception))

    def test_a_tampered_probe_is_refused(self):
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope({"probe_intact": False})

        self.assertIn("探針", str(caught.exception))

    def test_lingering_threads_are_refused(self):
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope({"threads_alive": ["worker"]})

        self.assertIn("仍有執行緒活著", str(caught.exception))

    def test_a_probe_that_saw_nothing_is_refused(self):
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope({"observed_calls": 0})

        self.assertIn("探針根本沒生效", str(caught.exception))

    def test_a_non_zero_exit_is_refused(self):
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope(returncode=1)

        self.assertIn("回傳", str(caught.exception))

    def test_a_truthy_string_does_not_pass_as_a_boolean(self):
        """M2：鍵齊了不代表值可信。

        Reviewer B 的重現——``complete="yes"``／``probe_intact="yes"`` 都是真
        值，會讓「完成了嗎」「探針有生效嗎」那幾道 guard 恆真，最後回傳乾淨。
        信封存在的理由就是「子行程可能是壞的」，寫錯型別的子行程正是它該擋的。
        """
        for field in ("complete", "probe_intact"):
            with self.subTest(field=field):
                with self.assertRaises(ImportAuditError) as caught:
                    self.envelope({field: "yes"})

                self.assertIn("欄位型別不符", str(caught.exception))
                self.assertIn(field, str(caught.exception))

    def test_a_counter_that_is_not_an_integer_is_refused(self):
        for value in ("one", 1.0, True, None):
            with self.subTest(value=value):
                with self.assertRaises(ImportAuditError) as caught:
                    self.envelope({"observed_calls": value})

                self.assertIn("observed_calls", str(caught.exception))

    def test_a_list_field_that_is_not_a_list_of_strings_is_refused(self):
        for field in (
            "integrity_violations",
            "config_opens",
            "snapshot_exports",
            "threads_alive",
        ):
            for value in ("not-a-list", [1], [None]):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ImportAuditError) as caught:
                        self.envelope({field: value})

                    self.assertIn(field, str(caught.exception))

    def test_a_malformed_reads_payload_is_refused(self):
        """``reads`` 的形狀壞掉會讓 ``_finding_from`` 拿到垃圾，也要在這裡擋。"""
        for value in (
            "not-a-list",
            [[]],
            [[{"file": "a.py", "line": "x", "name": "<module>"}]],
            [[{"file": "a.py", "name": "<module>"}]],
            [["not-a-frame"]],
        ):
            with self.subTest(value=value):
                with self.assertRaises(ImportAuditError) as caught:
                    self.envelope({"reads": value})

                self.assertIn("reads", str(caught.exception))

    def test_a_text_field_that_is_not_text_is_refused(self):
        for field in ("target", "status", "detail"):
            with self.subTest(field=field):
                with self.assertRaises(ImportAuditError):
                    self.envelope({field: 1})

    def test_every_field_is_type_checked(self):
        """把型別表當清單走一遍：沒有欄位漏掉型別驗證。"""
        wrong = {
            "protocol": "7",
            "target": 1,
            "status": 1,
            "detail": 1,
            "reads": "x",
            "observed_calls": "one",
            "probe_intact": "yes",
            "integrity_violations": "x",
            "config_opens": "x",
            "snapshot_exports": "x",
            "threads_alive": "x",
            "complete": "yes",
        }
        self.assertEqual(set(wrong), set(_AUDIT_FIELD_TYPES))
        for field, value in wrong.items():
            with self.subTest(field=field):
                with self.assertRaises(ImportAuditError):
                    self.envelope({field: value})

    def test_a_well_typed_envelope_with_real_content_is_accepted(self):
        """FP 方向：型別正確的非空信封不得被擋。"""
        stack = [{"file": "consumer.py", "line": 2, "name": "<module>"}]

        findings = self.envelope({"reads": [stack]})

        self.assertEqual(1, len(findings))

    def test_a_negative_counter_is_refused(self):
        """N1：型別對、語意不可能、而且會 fail-open。

        ``-1`` 通過 ``type(x) is int``，然後 ``if not payload["observed_calls"]``
        對它是 false——「探針根本沒生效」那道 guard 不會觸發，結果回報乾淨。
        被當成 guard 的整數欄位要驗到語意域，不只是型別。
        """
        for value in (-1, -999):
            with self.subTest(value=value):
                with self.assertRaises(ImportAuditError) as caught:
                    self.envelope({"observed_calls": value})

                self.assertIn("observed_calls", str(caught.exception))

    def test_a_zero_counter_is_still_refused_by_its_own_guard(self):
        """FP 方向：0 仍然要走「探針沒生效」那道 guard，不是被型別檢查吃掉。"""
        with self.assertRaises(ImportAuditError) as caught:
            self.envelope({"observed_calls": 0})

        self.assertIn("探針根本沒生效", str(caught.exception))

    def test_a_positive_counter_passes(self):
        """FP 方向：正常次數不得被擋。"""
        self.assertEqual([], self.envelope({"observed_calls": 1}))

    def test_a_non_object_envelope_is_refused_without_leaking_a_type_error(self):
        """N3：非 dict 的信封要丟 ImportAuditError，不是組錯誤訊息時自己炸掉。

        ``set([[]])`` 是 unhashable、``set(True)`` 不可迭代——先判型別再算缺鍵。
        """
        for body in ("[[]]", "[{}]", "true", "12", '"text"', "null"):
            with self.subTest(body=body):
                text = _AUDIT_BEGIN + body + _AUDIT_END
                with self.assertRaises(ImportAuditError) as caught:
                    self.envelope(envelope_text=text)

                self.assertIn("最外層必須是 object", str(caught.exception))

    def test_no_guard_turns_a_bad_envelope_into_an_empty_result(self):
        """下列清單中的每一種壞信封都不得回到「乾淨」那條路。

        清單就是緊接著列出的那幾個 payload——它是已經被找到的形狀，不是窮舉。
        """
        broken = (
            {"protocol": AUDIT_PROTOCOL + 1},
            {"target": "somebody.else"},
            {"complete": False},
            {"status": "import-failed"},
            {"integrity_violations": ["sys.setprofile"]},
            {"config_opens": ["debate_rules.json"]},
            {"snapshot_exports": ["pkg.mod.FROZEN"]},
            {"probe_intact": False},
            {"threads_alive": ["worker"]},
            {"observed_calls": 0},
            {"observed_calls": -1},
        )
        for payload in broken:
            with self.subTest(payload=payload):
                with self.assertRaises(ImportAuditError):
                    self.envelope(payload)


class PrivateAuthorityStateTest(unittest.TestCase):
    """Package-wide **私有名字保留規則**：權威模組的私有名字，別人不得出現。

    刻意講成「名字保留」而不是「精確的權威狀態引用分析」——它就是一條保留字規
    則，照實這樣講：
      - 會誤報（FP）：不相關模組裡剛好同名的 **local 變數**也會被指出來。這是保
        留規則的預期行為，換個名字就好。
      - 不會抓到（已宣告）：``getattr(rules, "_CACHED_RULES")`` 這種動態存取。

    **這一條查的是「模組原始碼裡有沒有出現那些名字」，如此而已。** 它與執行時稽
    核的分工寫在檔頭的威脅模型，那裡是唯一的真相來源——這裡不複述，複述就會漂
    移（第 12 輪這段就漂成了「執行時稽核＝機制③」，漏掉①②）。

    存在的理由：``from .debate_rules import _CACHED_RULES`` 這條路完全不進入權威
    檔的任何函式、也不開設定檔，所以執行時稽核看不到它。第 1 輪的
    ``ReloadIsTheOnlyWriterTest`` 守「誰寫」，沒有守「誰讀」；Reviewer A 在第 5
    輪做出了直接讀私有狀態而稽核回報乾淨的證據，所以補這一條。

    要守的名字是**推導**出來的，不是寫死的清單：``debate_rules.py`` 裡被
    ``global`` 重新綁定的模組層名字就是它的私有可變狀態。
    """

    def authority_file(self):
        from hoya_market_agents import debate_rules as module

        return module.__file__

    def is_guarded(self, path):
        """Everything except the authority module itself.

        排除的是**那一個檔案的解析後路徑**，不是「任何叫 debate_rules.py 的
        檔案」——後者會讓子套件裡的同名檔案白白拿到豁免。
        """
        return Path(path).resolve() != Path(self.authority_file()).resolve()

    def private_state_names(self):
        from hoya_market_agents import debate_rules as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        return {
            name
            for node in ast.walk(tree)
            if isinstance(node, ast.Global)
            for name in node.names
        }

    def references_in(self, path, names):
        """Static ``Name``／``Attribute``／``ImportFrom`` references to ``names``.

        只看這三種靜態節點：``getattr(rules, "_CACHED_RULES")`` 這種動態存取抓
        不到（本 class 的 docstring 已宣告）。
        """
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in names:
                found.add(node.id)
            elif isinstance(node, ast.Attribute) and node.attr in names:
                found.add(node.attr)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in names:
                        found.add(alias.name)
        return found

    def test_the_authority_owns_names_worth_guarding(self):
        """先證明推導出來的集合非空，否則下面那條是空話。"""
        self.assertEqual({"_CACHED_RULES"}, self.private_state_names())

    def test_no_other_module_touches_the_authority_private_state(self):
        names = self.private_state_names()
        root = package_root()
        offenders = {}
        for path in sorted(root.rglob("*.py")):
            if not self.is_guarded(path):
                continue
            hits = self.references_in(path, names)
            if hits:
                offenders[str(path.relative_to(root))] = sorted(hits)

        self.assertEqual({}, offenders)

    def test_a_same_named_file_in_a_subpackage_is_not_excluded(self):
        """H6：排除的對象要是**那一個檔案**，不是任何叫同名的檔案。

        用 basename 排除的話，``sub/debate_rules.py`` 會一起被跳過——它不是權威
        模組，卻拿到了權威模組的豁免。
        """
        root = package_root()
        authority = Path(self.authority_file()).resolve()
        impostor = root / "sub" / "debate_rules.py"

        self.assertNotEqual(authority, impostor.resolve())
        self.assertTrue(self.is_guarded(impostor))
        self.assertFalse(self.is_guarded(authority))

    def test_the_scan_would_catch_a_module_that_did_touch_it(self):
        """鑑別力：這條掃描要真的抓得到，否則上面那條證明不了什麼。"""
        directory = Path(tempfile.mkdtemp(prefix="b1-private-"))
        self.addCleanup(shutil.rmtree, directory, True)
        offender = directory / "sneaky.py"
        offender.write_text(
            "from .debate_rules import _CACHED_RULES\n"
            "FROZEN = _CACHED_RULES\n",
            encoding="utf-8",
        )

        self.assertEqual(
            {"_CACHED_RULES"}, self.references_in(offender, {"_CACHED_RULES"})
        )

    def test_the_scan_ignores_a_module_that_only_uses_the_public_function(self):
        """FP 方向：正常消費端不得被誤判。"""
        directory = Path(tempfile.mkdtemp(prefix="b1-private-"))
        self.addCleanup(shutil.rmtree, directory, True)
        clean = directory / "fine.py"
        clean.write_text(
            "from .debate_rules import debate_rules\n"
            "def use():\n    return debate_rules().initial_votes\n",
            encoding="utf-8",
        )

        self.assertEqual(set(), self.references_in(clean, {"_CACHED_RULES"}))


class SingleAuthorityReadInventoryTest(unittest.TestCase):
    """盤點二：一次操作只讀一次規則權威。

    量測方法見 :func:`count_authority_reads`。這裡驗的是本模組構得出來的入口；
    ``verify_run`` 與 ``build_live_state`` 各自在自己的測試檔用同一個工具驗。
    """

    def fixture(self):
        from hoya_market_agents.report_fixtures import load_fixture

        return load_fixture("consensus-6-1")

    def test_one_confidence_cap_reads_the_authority_once(self):
        from hoya_market_agents.report_contract import confidence_cap

        fixture = self.fixture()

        self.assertEqual(
            1,
            count_authority_reads(
                lambda: confidence_cap(fixture["report"], fixture["sources"])
            ),
        )

    def test_one_report_validation_reads_the_authority_once(self):
        from hoya_market_agents.report_contract import validate_market_report

        fixture = self.fixture()

        self.assertEqual(
            1,
            count_authority_reads(
                lambda: validate_market_report(fixture["report"], fixture["sources"])
            ),
        )

    def test_a_supplied_snapshot_means_no_read_at_all(self):
        from hoya_market_agents.report_contract import confidence_cap

        fixture = self.fixture()
        snapshot = debate_rules().confidence

        self.assertEqual(
            0,
            count_authority_reads(
                lambda: confidence_cap(
                    fixture["report"], fixture["sources"], rules=snapshot
                )
            ),
        )

    def test_the_counter_sees_reads_made_through_a_renamed_import(self):
        """鑑別力：工具用物件識別找持有者，所以改過名字的參照也算得到。"""
        from hoya_market_agents import report_contract

        alias = report_contract.debate_rules
        try:
            report_contract.renamed_authority = alias

            def read_twice():
                report_contract.renamed_authority()
                report_contract.renamed_authority()

            self.assertEqual(2, count_authority_reads(read_twice))
        finally:
            del report_contract.renamed_authority

    def test_an_unrelated_background_thread_is_not_counted(self):
        """計數必須 operation-local。

        換掉的是全模組共用的名字，所以背景執行緒（看板測試留下的 request
        thread 就是這樣）的讀取本來會被算進當前 operation。這個工具是承重的
        ——``dashboard_keeps_a_module_level_rules`` 就是靠它殺掉的——所以它不能
        只是「大部分時候會對」。
        """
        from hoya_market_agents import report_contract

        stop = threading.Event()
        started = threading.Event()

        def background():
            started.set()
            while not stop.is_set():
                report_contract.debate_rules()

        noise = threading.Thread(target=background, daemon=True)

        def operation():
            noise.start()
            started.wait(timeout=5)
            report_contract.debate_rules()
            # 讓背景執行緒確實讀到很多次，否則這條測試證明不了什麼
            for _ in range(200):
                report_contract.debate_rules
            stop.set()
            noise.join(timeout=5)

        try:
            self.assertEqual(1, count_authority_reads(operation))
        finally:
            stop.set()
            noise.join(timeout=5)

    def test_a_holder_imported_during_the_operation_is_restored(self):
        """操作途中才 import 進來的 consumer 也持有計數器，必須一起還原。

        只還原「呼叫前記下來的那份清單」的話，會有一個計數用的 closure 永久留
        在模組樹上：它會污染之後每一個測試，而且下一次盤點反而漏數那個持有者。
        """
        from hoya_market_agents import debate_rules as rules_module
        from hoya_market_agents import report_contract

        authority = rules_module.debate_rules
        late = types.ModuleType("hoya_market_agents.late_consumer")

        def operation():
            # 模擬「操作進行中才被載入的消費端」：它抓到的是當下的那個名字。
            late.debate_rules = report_contract.debate_rules
            sys.modules[late.__name__] = late
            late.debate_rules()

        try:
            self.assertEqual(1, count_authority_reads(operation))
            self.assertIs(authority, late.debate_rules)
            self.assertIs(authority, report_contract.debate_rules)
        finally:
            sys.modules.pop(late.__name__, None)


class GuardedConfigurationTypesTest(unittest.TestCase):
    """機制②依賴的前提：設定型別仍由權威檔定義。"""

    def test_the_configuration_types_are_still_defined_in_the_authority_file(self):
        """機制②的判準是「型別定義在權威檔」——這一條把那個前提釘住。

        若哪天有人把設定改成回傳 ``dict``、或把這些型別搬到別的模組，②就看不到
        那種快照了。與其讓它靜靜失效，不如在這裡先紅。
        """
        from hoya_market_agents import debate_rules as module

        for name in ("DebateRules", "ConfidenceRules", "LightStep", "DowngradeRule"):
            with self.subTest(type=name):
                configuration_type = getattr(module, name)
                self.assertEqual(
                    "hoya_market_agents.debate_rules", configuration_type.__module__
                )
                self.assertTrue(hasattr(configuration_type, "__dataclass_fields__"))

    def test_the_published_rules_are_an_instance_of_a_guarded_type(self):
        """FP 方向：上面那條要真的對應到權威回傳的東西，不是四個沒人用的名字。"""
        self.assertEqual("DebateRules", type(debate_rules()).__name__)
        self.assertEqual(
            "hoya_market_agents.debate_rules", type(debate_rules()).__module__
        )


class ReloadTestCase(RulesVariantTestCase):
    """Every reload test puts the repository config back, whatever it published.

    快取是行程層級的全域，所以任何換過規則的測試都必須換回來，否則污染的是同
    一次 ``unittest discover`` 裡後面的每一個測試。清理是 LIFO：這裡登記的還原
    會在暫存目錄被刪掉之前跑完。
    """

    def setUp(self):
        super().setUp()
        debate_rules()
        self.addCleanup(reload_debate_rules)

    def publish(self, document):
        """Write a variant, prove the loader accepts it, then publish it."""
        self.load(document)
        return reload_debate_rules(self.path)

    def lower_ladder(self):
        """A legal variant whose every published number differs from shipped."""
        document = valid_document()
        document["vote_thresholds"]["initial"] = 5
        document["vote_thresholds"]["reduced"] = 4
        document["vote_thresholds"]["forced_stop"] = 3
        document["timeline_ms"]["force_stop"] = 660_000
        return document


class ReloadPublishesTest(ReloadTestCase):
    """Ticket 11 B1：設定頁存檔後，既有消費端必須改看新規則。"""

    def test_a_reload_replaces_every_published_number(self):
        self.publish(self.lower_ladder())

        rules = debate_rules()
        self.assertEqual(5, rules.initial_votes)
        self.assertEqual(4, rules.reduced_votes)
        self.assertEqual(3, rules.forced_stop_votes)
        self.assertEqual(660_000, rules.force_stop_ms)

    def test_the_reload_returns_exactly_the_object_it_published(self):
        published = self.publish(self.lower_ladder())

        self.assertIs(published, debate_rules())

    def test_loading_a_file_without_publishing_it_changes_nothing(self):
        """FP 方向：``load_debate_rules`` 仍是純函式，讀一份檔案不得偷偷生效。"""
        before = debate_rules()
        self.load(self.lower_ladder())

        self.assertIs(before, debate_rules())

    def test_reloading_without_a_path_returns_to_the_repository_config(self):
        self.publish(self.lower_ladder())

        reload_debate_rules()

        self.assertEqual(load_debate_rules(RULES_PATH), debate_rules())

    def test_reloading_an_unchanged_file_changes_no_value(self):
        """FP 方向：reload 本身不得是一個會改值的操作。"""
        before = debate_rules()

        self.assertEqual(before, reload_debate_rules())

    def test_a_first_query_publishes_the_repository_config(self):
        """冷啟動路徑仍然存在：沒人 reload 過時，第一次查詢就載入出貨設定。"""
        from hoya_market_agents import debate_rules as module

        with mock.patch.object(module, "_CACHED_RULES", None):
            self.assertEqual(load_debate_rules(RULES_PATH), module.debate_rules())


class ReloadIsFailClosedTest(ReloadTestCase):
    """壞設定檔不得把既有規則清掉，也不得讓系統進入無設定狀態。

    設定頁按下存檔後看到錯誤訊息、系統繼續用舊規則跑，遠好過系統半殘：所以每
    一個案例都用 ``assertIs`` 釘住「還是**同一個物件**」——重新載入出來的等值
    物件會讓 ``assertEqual`` 通過，卻代表全域真的被動過。
    """

    def refuse(self, document):
        """Publish attempt must be refused, and the published object untouched."""
        published = debate_rules()
        self.path.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaises(DebateRulesError) as caught:
            reload_debate_rules(self.path)
        self.assertIs(published, debate_rules())
        return str(caught.exception)

    def test_a_file_that_is_not_json_keeps_the_old_rules(self):
        published = debate_rules()
        self.path.write_text("{ 這不是 JSON", encoding="utf-8")

        with self.assertRaises(DebateRulesError) as caught:
            reload_debate_rules(self.path)

        self.assertIn("不是合法 JSON", str(caught.exception))
        self.assertIs(published, debate_rules())

    def test_a_missing_field_keeps_the_old_rules(self):
        document = valid_document()
        del document["vote_thresholds"]["initial"]

        self.assertIn("vote_thresholds.initial", self.refuse(document))

    def test_an_out_of_range_value_keeps_the_old_rules(self):
        document = valid_document()
        document["vote_thresholds"]["initial"] = 99

        self.assertIn("vote_thresholds.initial", self.refuse(document))

    def test_an_out_of_order_timeline_keeps_the_old_rules(self):
        document = valid_document()
        document["timeline_ms"]["final_round_end"] = 1

        self.assertIn("嚴格遞增", self.refuse(document))

    def test_an_unknown_key_keeps_the_old_rules(self):
        document = valid_document()
        document["timeline_ms"]["force_stopp"] = 600_000

        self.assertIn("force_stopp", self.refuse(document))

    def test_an_unreadable_path_keeps_the_old_rules(self):
        published = debate_rules()

        with self.assertRaises(DebateRulesError):
            reload_debate_rules(self.path.parent / "does-not-exist.json")

        self.assertIs(published, debate_rules())

    def test_an_interrupt_midway_through_loading_keeps_the_old_rules(self):
        """中斷落在驗證途中：全域完全沒被碰過，不是「換到一半」。

        ``KeyboardInterrupt`` 不是 ``DebateRulesError`` 也不是 ``Exception``，
        所以它同時證明保護不是靠 ``except`` 攔下來的——是靠「切換排在載入之
        後」這個順序本身。
        """
        from hoya_market_agents import debate_rules as module

        published = debate_rules()

        def interrupted(path):
            raise KeyboardInterrupt("中斷落在載入途中")

        with mock.patch.object(module, "load_debate_rules", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                module.reload_debate_rules(self.path)

        self.assertIs(published, debate_rules())

    def test_a_legal_variant_is_still_published(self):
        """FP 方向：fail-closed 不得退化成「什麼都不換」。"""
        document = valid_document()
        document["timeline_ms"]["force_stop"] = 660_000

        self.assertEqual(660_000, self.publish(document).force_stop_ms)


class ReloadIsTheOnlyWriterTest(unittest.TestCase):
    """``_CACHED_RULES`` 只能有一個寫入者，而且切換只能是一次賦值。

    Ticket 11 B1 要求 webapp 不得直接改私有變數；守不守得住，取決於模組自己有
    沒有第二個寫入點。多一個寫入點就多一條沒被驗證過的切換路徑，「原子」也就
    不再是一句審查得了的話。第二次賦值同理：驗證與切換之間只要隔著可被搶先的
    語句，就會出現「已經決定要換、但還沒換成」的窗口。

    **守備範圍只到 ``debate_rules.py`` 這一個檔案的原始碼。** 別的模組從外面寫
    ``m._CACHED_RULES = ...``、或 ``globals()[...]``／``exec`` 這類動態寫入，這
    裡都攔不到——package 層級的守門屬於 Ticket 11，見下方那一條測試。
    """

    def writes_per_function(self, source, name):
        """``{function name: how many times it binds `name`}``, functions only."""
        counts = {}
        for function in ast.walk(ast.parse(source)):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = [t for t in node.targets if isinstance(t, ast.Name)]
                elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                    targets = [node.target] if isinstance(node.target, ast.Name) else []
                for target in targets:
                    if target.id == name:
                        counts[function.name] = counts.get(function.name, 0) + 1
        return counts

    def rules_module_writes(self):
        from hoya_market_agents import debate_rules as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        return self.writes_per_function(source, "_CACHED_RULES")

    def test_only_the_reload_function_writes_the_rules_cache(self):
        self.assertEqual({"reload_debate_rules"}, set(self.rules_module_writes()))

    def test_the_swap_is_a_single_assignment(self):
        self.assertEqual(
            1,
            self.rules_module_writes().get("reload_debate_rules"),
            "兩次賦值＝驗證與切換之間有一個可被搶先的窗口",
        )

    def test_only_this_module_is_covered_by_the_two_tests_above(self):
        """守備範圍聲明：這兩條只看 ``debate_rules.py`` 的原始碼。

        它們**擋不住**下列三種寫法，不要以為擋得住：
        1. 別的模組做 ``from hoya_market_agents import debate_rules as m;
           m._CACHED_RULES = ...``（package 層級的守門屬於 Ticket 11）；
        2. ``globals()["_CACHED_RULES"] = ...`` 這種動態寫入；
        3. ``exec``／``setattr`` 產生的寫入。
        目前原始碼沒有任何一種，但那是事實，不是這兩條測試的保證。
        """
        from hoya_market_agents import debate_rules as module

        self.assertTrue(module.__file__.endswith("debate_rules.py"))

    def test_the_counter_reports_a_second_writer_and_a_second_write(self):
        """helper 本身要分辨得出「兩個寫入者」與「寫兩次」，否則上面兩條是空話。"""
        source = (
            "_CACHED_RULES = None\n"
            "def publish(rules):\n"
            "    global _CACHED_RULES\n"
            "    _CACHED_RULES = None\n"
            "    _CACHED_RULES = rules\n"
            "def sneak(rules):\n"
            "    global _CACHED_RULES\n"
            "    _CACHED_RULES = rules\n"
        )

        self.assertEqual(
            {"publish": 2, "sneak": 1}, self.writes_per_function(source, "_CACHED_RULES")
        )
