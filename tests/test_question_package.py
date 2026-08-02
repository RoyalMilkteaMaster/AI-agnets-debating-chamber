"""Question Package normalizes the four approved types and opens everything else.

A live competition question is drawn on the spot, so the package may not match any
of the three demonstrated shapes. Anything naming an approved asset becomes an
open proposition instead of a refusal; only a question with no approved asset at
all still fails closed.
"""

import unittest

from hoya_market_agents.question import UnsupportedQuestionError
from hoya_market_agents.question_package import (
    OPEN_STANCES,
    build_question_package,
)


class QuestionPackageTest(unittest.TestCase):
    def test_single_asset_market_state_is_versioned_and_defaults_to_fourteen_days(self):
        package = build_question_package("分析 BTC 市場狀態")

        self.assertEqual("1.0.0", package.schema_version)
        self.assertEqual("single_asset_market_state", package.question_type)
        self.assertEqual(("BTC",), package.assets)
        self.assertEqual(14, package.period_days)
        self.assertFalse(package.period_stated)
        self.assertEqual(("bullish", "bearish", "neutral"), package.stance_options)

    def test_two_asset_comparison_preserves_named_order_and_uses_comparison_stances(self):
        package = build_question_package("比較 XRP 與 BTC 過去 7 日的市場位置與風險")

        self.assertEqual("two_asset_comparison", package.question_type)
        self.assertEqual(("XRP", "BTC"), package.assets)
        self.assertEqual(7, package.period_days)
        self.assertEqual(
            ("asset_a_stronger", "asset_b_stronger", "no_clear_difference"),
            package.stance_options,
        )

    def test_overall_market_state_allows_no_named_asset(self):
        package = build_question_package("分析過去 14 日加密市場整體狀態")

        self.assertEqual("overall_market_state", package.question_type)
        self.assertEqual((), package.assets)
        self.assertEqual("overall-market", package.asset_slug)
        self.assertEqual(("bullish", "bearish", "neutral"), package.stance_options)

    def test_event_impact_uses_event_stances(self):
        package = build_question_package("評估網路升級事件對 SOL 的影響")

        self.assertEqual("event_impact", package.question_type)
        self.assertEqual(("SOL",), package.assets)
        self.assertEqual(
            ("positive", "negative", "unclear_or_conditional"),
            package.stance_options,
        )

    def test_event_impact_can_target_the_overall_crypto_market(self):
        package = build_question_package("評估監管事件對加密市場的影響")

        self.assertEqual("event_impact", package.question_type)
        self.assertEqual((), package.assets)

    def test_question_without_any_approved_asset_fails_closed(self):
        invalid_questions = (
            "分析 DOGE 過去 14 日市場狀態",
            "幫我預測下週樂透號碼",
            "若美國通過股市新法案，標普會如何反應？",
        )

        for question in invalid_questions:
            with self.subTest(question=question):
                with self.assertRaises(UnsupportedQuestionError):
                    build_question_package(question)

    def test_lowercase_unsupported_ticker_in_market_or_event_context_fails_closed(self):
        for question in (
            "評估 doge 升級事件對加密市場的影響",
            "分析 doge 市場狀態",
            "doge 事件對加密市場的影響",
        ):
            with self.subTest(question=question):
                with self.assertRaises(UnsupportedQuestionError) as caught:
                    build_question_package(question)

                self.assertIn("DOGE", str(caught.exception))

    def test_general_english_after_analysis_verb_is_not_treated_as_a_ticker(self):
        package = build_question_package("分析 price action，判斷 BTC 市場狀態")

        self.assertEqual(("BTC",), package.assets)

    def test_comparison_assets_follow_first_appearance_without_duplicates(self):
        package = build_question_package("比較 BTC 與 ETH，BTC 相對 ETH 誰較強？")

        self.assertEqual(("BTC", "ETH"), package.assets)

    def test_chinese_week_periods_are_explicit(self):
        one_week = build_question_package("分析 BTC 過去一週市場狀態")
        two_weeks = build_question_package("分析 BTC 過去兩週市場狀態")

        self.assertEqual((7, True), (one_week.period_days, one_week.period_stated))
        self.assertEqual((14, True), (two_weeks.period_days, two_weeks.period_stated))

    def test_multi_character_chinese_week_count_uses_the_complete_token(self):
        package = build_question_package("分析 BTC 過去十二週市場狀態")

        self.assertEqual((84, True), (package.period_days, package.period_stated))

    def test_unparseable_explicit_period_hint_fails_closed(self):
        with self.assertRaises(UnsupportedQuestionError):
            build_question_package("分析 BTC 過去幾週市場狀態")


class StanceLabelTest(unittest.TestCase):
    """Every question type carries the Traditional Chinese ballot wording."""

    def test_market_labels_cover_the_three_market_stances(self):
        package = build_question_package("分析 BTC 市場狀態")

        self.assertEqual(
            {"bullish": "偏多", "bearish": "偏空", "neutral": "方向不明"},
            package.stance_labels,
        )

    def test_comparison_labels_name_the_actual_assets_in_order(self):
        package = build_question_package("比較 XRP 與 BTC 過去 7 日的市場位置與風險")

        self.assertEqual(
            {
                "asset_a_stronger": "XRP較優",
                "asset_b_stronger": "BTC較優",
                "no_clear_difference": "無明顯差異",
            },
            package.stance_labels,
        )

    def test_event_labels_cover_the_three_event_stances(self):
        package = build_question_package("評估網路升級事件對 SOL 的影響")

        self.assertEqual(
            {
                "positive": "利多",
                "negative": "利空",
                "unclear_or_conditional": "不明或有條件",
            },
            package.stance_labels,
        )

    def test_open_labels_cover_the_three_proposition_stances(self):
        package = build_question_package(OpenPropositionTest.QUESTION)

        self.assertEqual(
            {
                "affirmative": "正方",
                "negative_side": "反方",
                "undecided": "無法決定",
            },
            package.stance_labels,
        )

    def test_open_negative_key_never_collides_with_the_event_vocabulary(self):
        self.assertNotIn("negative", OPEN_STANCES)

    def test_labels_survive_the_serialised_package(self):
        package = build_question_package("分析 BTC 市場狀態")

        self.assertEqual(package.stance_labels, package.to_dict()["stance_labels"])
        self.assertIsNone(package.to_dict()["proposition"])


class OpenPropositionTest(unittest.TestCase):
    """A drawn question that matches no approved shape still becomes votable."""

    QUESTION = "若美國通過比特幣戰略儲備法案，BTC 與市場情緒會如何反應？"

    def test_free_form_question_naming_an_approved_asset_opens_a_proposition(self):
        package = build_question_package(self.QUESTION)

        self.assertEqual("open_proposition", package.question_type)
        self.assertEqual(("BTC",), package.assets)
        self.assertEqual(OPEN_STANCES, package.stance_options)
        self.assertEqual(
            ("affirmative", "negative_side", "undecided"), package.stance_options
        )
        self.assertIsNone(package.proposition)

    def test_unknown_upper_case_token_rides_along_into_open_mode(self):
        package = build_question_package("SEC 對 BTC 現貨 ETF 的態度會怎麼走？")

        self.assertEqual("open_proposition", package.question_type)
        self.assertEqual(("BTC",), package.assets)

    def test_bracketed_asset_counts_as_an_approved_asset(self):
        package = build_question_package("【BTC】接下來會發生什麼？")

        self.assertEqual("open_proposition", package.question_type)
        self.assertEqual(("BTC",), package.assets)

    def test_shapes_that_used_to_be_refused_now_open_instead(self):
        for question, assets in (
            ("比較 BTC 與 doge 過去 14 日市場位置", ("BTC",)),
            ("請告訴我 BTC 是什麼", ("BTC",)),
            ("分析 BTC 與 ETH 市場狀態", ("BTC", "ETH")),
        ):
            with self.subTest(question=question):
                package = build_question_package(question)

                self.assertEqual("open_proposition", package.question_type)
                self.assertEqual(assets, package.assets)

    def test_written_proposition_replaces_only_the_proposition_field(self):
        package = build_question_package(self.QUESTION).with_proposition(
            "美國比特幣戰略儲備法案將推升 BTC 價格。"
        )

        self.assertEqual("美國比特幣戰略儲備法案將推升 BTC 價格。", package.proposition)
        self.assertEqual(self.QUESTION, package.question)
        self.assertEqual("open_proposition", package.question_type)
        self.assertEqual(
            "美國比特幣戰略儲備法案將推升 BTC 價格。",
            package.to_dict()["proposition"],
        )


if __name__ == "__main__":
    unittest.main()
