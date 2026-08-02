"""Intake gate behaviour: only approved assets may start a run."""

import unittest

from hoya_market_agents.question import (
    DEFAULT_PERIOD_DAYS,
    SUPPORTED_ASSETS,
    UnknownAssetError,
    UnsupportedQuestionError,
    analyze_question,
    inspect_question,
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
            "比較 BTC、doge 過去 14 日相對強弱",
            "比較 BTC,doge 過去 14 日相對強弱",
            "比較 BTC vs doge 過去 14 日相對強弱",
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


class OpenModeIntakeTest(unittest.TestCase):
    """The open-proposition path may carry unknown tokens; scope stays approved."""

    QUESTION = "若 SEC 通過 BTC 現貨 ETF，市場會如何反應？"

    def test_unknown_asset_error_is_an_unsupported_question_error(self):
        self.assertTrue(issubclass(UnknownAssetError, UnsupportedQuestionError))

    def test_strict_intake_still_rejects_unknown_upper_case_tokens(self):
        with self.assertRaises(UnknownAssetError):
            inspect_question(self.QUESTION)

    def test_open_mode_keeps_unknown_tokens_out_of_the_scope_assets(self):
        scope = inspect_question(self.QUESTION, allow_unknown_assets=True)

        self.assertEqual(("BTC",), scope.assets)
        self.assertEqual(self.QUESTION, scope.question)

    def test_open_mode_never_invents_an_asset_when_none_is_approved(self):
        scope = inspect_question("若 SEC 通過 DOGE 現貨 ETF？", allow_unknown_assets=True)

        self.assertEqual((), scope.assets)

    def test_open_mode_still_fails_closed_on_an_unparseable_period(self):
        with self.assertRaises(UnsupportedQuestionError):
            inspect_question("BTC 過去幾週 ETF 資金？", allow_unknown_assets=True)

    def test_bracketed_asset_is_recognised_in_open_mode(self):
        scope = inspect_question("【BTC】ETF 通過後會怎樣？", allow_unknown_assets=True)

        self.assertEqual(("BTC",), scope.assets)


if __name__ == "__main__":
    unittest.main()
