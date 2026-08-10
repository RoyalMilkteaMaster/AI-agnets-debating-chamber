"""Question Package normalizes the four approved types and opens everything else.

A live question is drawn on the spot, so the package may not match any of the
demonstrated shapes — and its target may be a stock, a coin nobody listed, or
nothing tradable at all. Anything unmatched becomes an open proposition. Only a
question that cannot describe a run at all is still refused.
"""

import unittest

from hoya_market_agents.question import (
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_OPEN,
    ASSET_CLASS_TW_STOCK,
    ASSET_CLASS_US_STOCK,
    UnsupportedQuestionError,
)
from hoya_market_agents.question_package import (
    OPEN_QUESTION_TYPE,
    OPEN_STANCES,
    UnknownAssetError,
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

    def test_question_naming_no_tradable_asset_opens_instead_of_failing(self):
        open_questions = (
            "幫我預測下週樂透號碼",
            "若美國通過股市新法案，標普會如何反應？",
            "聯準會九月會不會降息",
        )

        for question in open_questions:
            with self.subTest(question=question):
                package = build_question_package(question)

                self.assertEqual(OPEN_QUESTION_TYPE, package.question_type)
                self.assertEqual((), package.assets)
                self.assertEqual(OPEN_STANCES, package.stance_options)

    def test_ticker_outside_the_old_whitelist_reaches_its_own_question_type(self):
        package = build_question_package("分析 DOGE 過去 14 日市場狀態")

        self.assertEqual("single_asset_market_state", package.question_type)
        self.assertEqual(("DOGE",), package.assets)
        # 題目沒寫市場，就不替它猜一個；寫了「幣」才是 crypto。
        self.assertEqual(ASSET_CLASS_OPEN, package.asset_class)
        self.assertEqual(
            ASSET_CLASS_CRYPTO,
            build_question_package("分析 DOGE 幣價過去 14 日市場狀態").asset_class,
        )

    def test_a_lowercase_ticker_needs_a_naming_signal_to_reach_the_assets(self):
        """沒有指認訊號的小寫代號不寫入 assets——猜錯會綁死錯的研究對象。"""
        for question, question_type in (
            ("評估 doge 升級事件對加密市場的影響", "event_impact"),
            ("doge 事件對加密市場的影響", "event_impact"),
        ):
            with self.subTest(question=question):
                package = build_question_package(question)

                self.assertEqual(question_type, package.question_type)
                self.assertEqual((), package.assets)

    def test_a_ticker_whose_shape_is_not_prose_does_reach_the_assets(self):
        package = build_question_package("分析 DOGE 幣價未來的市場狀態")
        self.assertEqual(("DOGE",), package.assets)

        compared = build_question_package("比較 doge 與 eth 過去 14 日相對強弱")
        self.assertEqual(("DOGE", "ETH"), compared.assets)

    def test_blank_question_is_the_only_shape_left_that_fails_closed(self):
        with self.assertRaises(UnsupportedQuestionError):
            build_question_package("   ")

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
            ("請告訴我 BTC 是什麼", ("BTC",)),
            ("分析 BTC 與 ETH 市場狀態", ("BTC", "ETH")),
        ):
            with self.subTest(question=question):
                package = build_question_package(question)

                self.assertEqual("open_proposition", package.question_type)
                self.assertEqual(assets, package.assets)

    def test_a_comparison_naming_an_unlisted_coin_is_a_comparison(self):
        package = build_question_package("比較 BTC 與 doge 過去 14 日市場位置")

        self.assertEqual("two_asset_comparison", package.question_type)
        self.assertEqual(("BTC", "DOGE"), package.assets)

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


class OpenMarketIntakeTest(unittest.TestCase):
    """Any market may start a run, and the package says which one it is."""

    def test_taiwan_listing_is_packaged_with_its_class(self):
        package = build_question_package("幫我分析 2330 未來七天會不會漲")

        self.assertEqual(("2330",), package.assets)
        self.assertEqual(ASSET_CLASS_TW_STOCK, package.asset_class)
        self.assertEqual("2330", package.asset_slug)
        self.assertEqual(7, package.period_days)
        self.assertEqual(OPEN_QUESTION_TYPE, package.question_type)
        self.assertEqual(OPEN_STANCES, package.stance_options)

    def test_us_listing_is_packaged_with_its_class(self):
        package = build_question_package("NVDA 這檔美股未來七天股價會不會漲")

        self.assertEqual(("NVDA",), package.assets)
        self.assertEqual(ASSET_CLASS_US_STOCK, package.asset_class)
        self.assertEqual("nvda", package.asset_slug)

    def test_share_class_listing_keeps_its_class_letter_through_the_package(self):
        package = build_question_package("BRK.B 這檔美股未來七天股價會不會漲")

        self.assertEqual(("BRK.B",), package.assets)
        self.assertEqual(ASSET_CLASS_US_STOCK, package.asset_class)
        self.assertEqual("brk-b", package.asset_slug)

    def test_open_proposition_without_a_target_keeps_the_overall_market_slug(self):
        package = build_question_package("聯準會九月會不會降息")

        self.assertEqual(ASSET_CLASS_OPEN, package.asset_class)
        self.assertEqual("overall-market", package.asset_slug)

    def test_asset_class_survives_the_serialised_package(self):
        package = build_question_package("幫我分析 2330 未來七天會不會漲")

        self.assertEqual(ASSET_CLASS_TW_STOCK, package.to_dict()["asset_class"])

    def test_unknown_asset_error_stays_importable_for_existing_callers(self):
        self.assertTrue(issubclass(UnknownAssetError, UnsupportedQuestionError))

    def test_a_stated_subject_reaches_the_package(self):
        """The seam a menu-driven caller uses, checked at the package boundary."""
        buyback = "台積電回購 50000 股後股價會不會上漲？"
        self.assertEqual(("50000",), build_question_package(buyback).assets)

        package = build_question_package(
            buyback, assets=("2330",), asset_class=ASSET_CLASS_TW_STOCK
        )

        self.assertEqual(("2330",), package.assets)
        self.assertEqual(ASSET_CLASS_TW_STOCK, package.asset_class)
        self.assertEqual(("2330",), tuple(package.to_dict()["assets"]))

    def test_a_stated_class_answers_the_bare_symbol_case(self):
        question = "分析 DOGE 過去 14 日市場狀態"
        self.assertEqual(ASSET_CLASS_OPEN, build_question_package(question).asset_class)

        package = build_question_package(
            question, assets=("DOGE",), asset_class=ASSET_CLASS_CRYPTO
        )

        self.assertEqual(("DOGE",), package.assets)
        self.assertEqual(ASSET_CLASS_CRYPTO, package.asset_class)

    def test_stating_nothing_leaves_the_package_unchanged(self):
        for question in (
            "幫我分析 2330 未來七天會不會漲",
            "DOGE 幣價未來七天會不會漲",
            "AI 泡沫會不會在今年破掉",
            "比較 BTC 與 ETH 未來七天",
        ):
            with self.subTest(question=question):
                self.assertEqual(
                    build_question_package(question).to_dict(),
                    build_question_package(
                        question, assets=None, asset_class=None
                    ).to_dict(),
                )

    def test_a_stated_subject_that_cannot_describe_a_run_fails_closed(self):
        with self.assertRaises(UnknownAssetError):
            build_question_package("任意題目", assets=("../etc/passwd",))
        with self.assertRaises(UnsupportedQuestionError):
            build_question_package("任意題目", asset_class="tw-stock")
        with self.assertRaises(UnknownAssetError):
            build_question_package("任意題目", assets={"NVDA", "AAPL"})
        with self.assertRaises(UnknownAssetError):
            build_question_package("任意題目", assets=("NVDA", "nvda"))
        with self.assertRaises(UnknownAssetError):
            build_question_package("任意題目", assets=("A" * 10000,))

    def test_both_spellings_of_one_question_get_the_same_type(self):
        """Targets and question type must be read off the same view of the text.

        Before the width-folded reading was carried on the scope, the intake
        gate folded and the type dispatcher did not, so ``ＢＴＣ ｖｓ ＥＴＨ``
        found both targets and was still filed as an open proposition.
        """
        for ascii_spelling, wide_spelling in (
            ("BTC vs ETH 過去七天", "ＢＴＣ ｖｓ ＥＴＨ 過去七天"),
            ("分析 BTC market state", "分析 ＢＴＣ ｍａｒｋｅｔ ｓｔａｔｅ"),
            ("BTC 過去 14 日的市場狀態如何？", "ＢＴＣ 過去 14 日的市場狀態如何？"),
            ("比較 BTC 與 ETH 未來七天", "比較 ＢＴＣ 與 ＥＴＨ 未來七天"),
        ):
            with self.subTest(question=wide_spelling):
                plain = build_question_package(ascii_spelling)
                wide = build_question_package(wide_spelling)

                self.assertEqual(plain.question_type, wide.question_type)
                self.assertEqual(plain.assets, wide.assets)
                self.assertEqual(plain.stance_options, wide.stance_options)

    def test_the_package_still_quotes_the_question_verbatim(self):
        """Prompts and artifacts must show the user's own text, not a folding."""
        wide = "ＢＴＣ ｖｓ ＥＴＨ 過去七天"

        package = build_question_package(wide)

        self.assertEqual(wide, package.question)
        self.assertEqual(wide, package.to_dict()["question"])


if __name__ == "__main__":
    unittest.main()
