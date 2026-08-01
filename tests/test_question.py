"""Intake gate behaviour: only approved assets may start a run."""

import unittest

from hoya_market_agents.question import (
    DEFAULT_PERIOD_DAYS,
    SUPPORTED_ASSETS,
    UnsupportedQuestionError,
    analyze_question,
)


class AnalyzeQuestionTest(unittest.TestCase):
    def test_supported_asset_question_yields_scope(self):
        scope = analyze_question("分析 BTC 過去 14 日市場狀態")

        self.assertEqual(("BTC",), scope.assets)
        self.assertEqual(14, scope.period_days)
        self.assertEqual("btc", scope.asset_slug)
        self.assertEqual("分析 BTC 過去 14 日市場狀態", scope.question)

    def test_supported_asset_matching_is_case_insensitive_and_normalized(self):
        scope = analyze_question("比較 xrp 與 Btc 過去 14 日相對強弱")

        self.assertEqual(("BTC", "XRP"), scope.assets)
        self.assertEqual("btc-xrp", scope.asset_slug)

    def test_lowercase_english_description_is_not_treated_as_an_asset(self):
        scope = analyze_question("分析 btc 過去 14 日 price action")

        self.assertEqual(("BTC",), scope.assets)

    def test_period_defaults_to_fourteen_days_when_unstated(self):
        scope = analyze_question("分析 ETH 市場狀態")

        self.assertEqual(DEFAULT_PERIOD_DAYS, scope.period_days)
        self.assertFalse(scope.period_stated)

    def test_stated_period_overrides_default(self):
        scope = analyze_question("分析 SOL 過去 7 天市場狀態")

        self.assertEqual(7, scope.period_days)
        self.assertTrue(scope.period_stated)

    def test_two_asset_comparison_keeps_canonical_order(self):
        scope = analyze_question("比較 XRP 與 BTC 過去 14 日相對強弱")

        self.assertEqual(("BTC", "XRP"), scope.assets)
        self.assertEqual("btc-xrp", scope.asset_slug)

    def test_unsupported_asset_fails_closed(self):
        with self.assertRaises(UnsupportedQuestionError) as caught:
            analyze_question("分析 DOGE 過去 14 日市場狀態")

        self.assertIn("DOGE", str(caught.exception))

    def test_unsupported_asset_mixed_with_supported_asset_fails_closed(self):
        with self.assertRaises(UnsupportedQuestionError) as caught:
            analyze_question("比較 BTC 與 DOGE 過去 14 日相對強弱")

        self.assertIn("DOGE", str(caught.exception))

    def test_lowercase_unsupported_asset_in_comparison_fails_closed(self):
        for question in (
            "比較 BTC 與 doge 過去 14 日相對強弱",
            "分析 eth 跟 DoGe 的市場位置",
        ):
            with self.subTest(question=question):
                with self.assertRaises(UnsupportedQuestionError) as caught:
                    analyze_question(question)

                self.assertIn("DOGE", str(caught.exception))

    def test_question_without_any_asset_fails_closed(self):
        with self.assertRaises(UnsupportedQuestionError):
            analyze_question("分析 過去 14 日市場狀態")

    def test_blank_question_fails_closed(self):
        with self.assertRaises(UnsupportedQuestionError):
            analyze_question("   ")

    def test_approved_asset_set_matches_requirements(self):
        self.assertEqual(("BTC", "ETH", "SOL", "BNB", "XRP"), SUPPORTED_ASSETS)


if __name__ == "__main__":
    unittest.main()
