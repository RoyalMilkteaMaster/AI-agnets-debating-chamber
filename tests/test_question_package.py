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


if __name__ == "__main__":
    unittest.main()
