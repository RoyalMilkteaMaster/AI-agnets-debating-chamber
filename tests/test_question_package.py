"""Question Package is the fail-closed boundary for the four approved question types."""

import unittest

from hoya_market_agents.question import UnsupportedQuestionError
from hoya_market_agents.question_package import build_question_package


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

    def test_unsupported_asset_and_question_type_fail_closed(self):
        invalid_questions = (
            "分析 DOGE 過去 14 日市場狀態",
            "比較 BTC 與 doge 過去 14 日市場位置",
            "請告訴我 BTC 是什麼",
            "分析 BTC 與 ETH 市場狀態",
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


if __name__ == "__main__":
    unittest.main()
