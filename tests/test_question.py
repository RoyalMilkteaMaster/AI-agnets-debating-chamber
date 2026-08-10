"""Intake gate behaviour: every question is accepted and given an asset class.

The approved-asset whitelist is gone. What the gate owes a caller now is a
reading of the question — the targets it names, the class of market they belong
to and the analysis period — not a verdict on whether the subject is allowed.
Only an undefined run is still refused: empty text, or a period the question
states but the gate cannot parse.
"""

import collections
import re
import unicodedata
import unittest

from hoya_market_agents import question as question_module
from hoya_market_agents.question import (
    MARKET_WORDS_BY_CLASS,
    NON_ASSET_TOKENS,
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_OPEN,
    ASSET_CLASS_TW_STOCK,
    ASSET_CLASS_US_STOCK,
    ASSET_CLASSES,
    DEFAULT_PERIOD_DAYS,
    LEGACY_CRYPTO_SYMBOLS,
    OVERALL_MARKET_SLUG,
    QuestionScope,
    UnknownAssetError,
    UnsupportedQuestionError,
    analyze_question,
    asset_slug_for,
    inspect_question,
    normalize_asset,
)


class AnalyzeQuestionTest(unittest.TestCase):
    def test_upper_case_ticker_question_yields_scope(self):
        scope = analyze_question("分析 BTC 過去 14 日市場狀態")

        self.assertEqual(("BTC",), scope.assets)
        self.assertEqual(14, scope.period_days)
        self.assertEqual("btc", scope.asset_slug)
        self.assertEqual(ASSET_CLASS_CRYPTO, scope.asset_class)
        self.assertEqual("分析 BTC 過去 14 日市場狀態", scope.question)

    def test_comparison_pair_is_normalized_in_first_appearance_order(self):
        scope = analyze_question("比較 xrp 與 Btc 過去 14 日相對強弱")

        self.assertEqual(("XRP", "BTC"), scope.assets)
        self.assertEqual("xrp-btc", scope.asset_slug)

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

    def test_chinese_numeral_day_period_is_read(self):
        scope = analyze_question("幫我分析 BTC 未來七天會不會漲")

        self.assertEqual(7, scope.period_days)
        self.assertTrue(scope.period_stated)

    def test_ticker_outside_the_old_whitelist_is_accepted(self):
        scope = analyze_question("分析 DOGE 過去 14 日市場狀態")

        self.assertEqual(("DOGE",), scope.assets)
        self.assertEqual(ASSET_CLASS_OPEN, scope.asset_class)

    def test_lowercase_ticker_in_a_comparison_is_accepted(self):
        for question, assets in (
            ("比較 BTC 與 doge 過去 14 日相對強弱", ("BTC", "DOGE")),
            ("分析 eth 跟 DoGe 的市場位置", ("ETH", "DOGE")),
            ("比較 BTC、doge 過去 14 日相對強弱", ("BTC", "DOGE")),
            ("比較 BTC vs doge 過去 14 日相對強弱", ("BTC", "DOGE")),
        ):
            with self.subTest(question=question):
                self.assertEqual(assets, analyze_question(question).assets)

    def test_common_finance_acronyms_never_become_assets(self):
        scope = inspect_question("若 SEC 通過 BTC 現貨 ETF，市場會如何反應？")

        self.assertEqual(("BTC",), scope.assets)

    def test_question_naming_no_asset_still_fails_closed_for_analyze_question(self):
        with self.assertRaises(UnsupportedQuestionError):
            analyze_question("分析 過去 14 日市場狀態")

    def test_blank_question_fails_closed(self):
        with self.assertRaises(UnsupportedQuestionError):
            analyze_question("   ")

    def test_stated_but_unparseable_period_fails_closed(self):
        with self.assertRaises(UnsupportedQuestionError):
            inspect_question("BTC 過去幾週 ETF 資金？")


class AssetClassTest(unittest.TestCase):
    """Four classes, read off the question's own market words."""

    def test_asset_classes_are_the_four_approved_values(self):
        self.assertEqual(
            ("crypto", "tw_stock", "us_stock", "open"),
            ASSET_CLASSES,
        )

    def test_four_digit_code_is_a_taiwan_listing(self):
        scope = inspect_question("幫我分析 2330 未來七天會不會漲")

        self.assertEqual(("2330",), scope.assets)
        self.assertEqual(ASSET_CLASS_TW_STOCK, scope.asset_class)
        self.assertEqual("2330", scope.asset_slug)
        self.assertEqual(7, scope.period_days)

    def test_taiwan_code_keeps_only_the_number_when_an_exchange_suffix_is_given(self):
        scope = inspect_question("分析 2330.TW 未來七天走勢")

        self.assertEqual(("2330",), scope.assets)
        self.assertEqual(ASSET_CLASS_TW_STOCK, scope.asset_class)

    def test_us_market_words_classify_an_english_ticker(self):
        scope = inspect_question("NVDA 這檔美股未來七天股價會不會漲")

        self.assertEqual(("NVDA",), scope.assets)
        self.assertEqual(ASSET_CLASS_US_STOCK, scope.asset_class)
        self.assertEqual("nvda", scope.asset_slug)

    def test_a_ticker_with_no_market_word_at_all_is_open(self):
        """§11.4：無法歸類就走 open，不臆測它屬於哪個市場。"""
        scope = inspect_question("NVDA 未來七天會不會漲")

        self.assertEqual(("NVDA",), scope.assets)
        self.assertEqual(ASSET_CLASS_OPEN, scope.asset_class)

    def test_institution_acronyms_never_become_assets(self):
        for question in (
            "IMF 今年會不會降息",
            "ECB 會不會升息",
            "OPEC 會不會減產",
            "FOMC 會後市場會怎麼走",
            "SEC 會不會放行",
            "CPI 會不會再升",
            "GDP 會不會下修",
            "FOMC SEC CPI GDP 對市場的影響",
        ):
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual((), scope.assets)

    def test_a_number_carrying_a_unit_is_never_a_listing_code(self):
        for question in (
            "分析 BTC 在 2026 年的市場狀態",
            "BTC 漲到 10000 元的機率",
            "BTC 漲到 10000 美元的機率",
            "成交量 50000 張代表什麼",
        ):
            with self.subTest(question=question):
                self.assertNotIn("10000", inspect_question(question).assets)
                self.assertNotIn("2026", inspect_question(question).assets)
                self.assertNotIn("50000", inspect_question(question).assets)

    def test_iso_and_slash_dates_are_never_stock_codes(self):
        for question in (
            "截至 2024-08-05，FED 是否降息？",
            "截至 2024/08/05，FED 是否降息？",
            "08-05-2024 之後市場會怎麼走？",
        ):
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual((), scope.assets)
                self.assertEqual(ASSET_CLASS_OPEN, scope.asset_class)

    def test_an_exchange_suffix_is_consumed_with_its_listing(self):
        """suffix 屬於那個標的，不能又被讀成一個獨立標的。"""
        for question, assets, slug in (
            ("2330-TW 未來七天會不會漲", ("2330",), "2330"),
            ("2330-TWO 未來七天會不會漲", ("2330",), "2330"),
            ("2330.TW 未來七天會不會漲", ("2330",), "2330"),
            ("AAPL-HK 這檔股票未來七天會不會漲", ("AAPL",), "aapl"),
            ("AAPL.HK 這檔股票未來七天會不會漲", ("AAPL",), "aapl"),
            ("BRK-B-TW 未來七天會不會漲", ("BRK.B",), "brk-b"),
            ("NVDA-US 這檔美股未來七天會不會漲", ("NVDA",), "nvda"),
        ):
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual(assets, scope.assets)
                self.assertEqual(slug, scope.asset_slug)

    def test_a_market_word_alone_does_not_name_a_share_class_ticker(self):
        """反向：沒有指認訊號時，小寫代號不寫入 assets（分類仍正確）。"""
        for question in ("brk.b 股價未來七天會不會漲", "brk-b.us 股價未來七天會不會漲"):
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual((), scope.assets)
                self.assertEqual(ASSET_CLASS_US_STOCK, scope.asset_class)

    def test_an_english_market_word_classifies_without_naming_a_target(self):
        """市場詞說得出「哪個市場」，說不出「哪個標的」——所以只做分類。"""
        for question, asset_class in (
            ("nvda stock 未來七天會不會漲", ASSET_CLASS_US_STOCK),
            ("brk-b stock 未來七天會不會漲", ASSET_CLASS_US_STOCK),
            ("ada coin 未來七天會不會漲", ASSET_CLASS_CRYPTO),
            ("ada crypto 未來七天會不會漲", ASSET_CLASS_CRYPTO),
            ("nvda shares 未來七天會不會漲", ASSET_CLASS_US_STOCK),
            ("aapl nasdaq 未來七天會不會漲", ASSET_CLASS_US_STOCK),
            ("ada token 未來七天會不會漲", ASSET_CLASS_CRYPTO),
        ):
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual((), scope.assets)
                self.assertEqual(asset_class, scope.asset_class)

    def test_an_upper_case_ticker_beside_a_market_word_is_still_named(self):
        """對照組：大寫有形狀證據，不需要靠鄰接的市場詞。"""
        for question, assets in (
            ("NVDA stock 未來七天會不會漲", ("NVDA",)),
            ("ADA coin 未來七天會不會漲", ("ADA",)),
        ):
            with self.subTest(question=question):
                self.assertEqual(assets, inspect_question(question).assets)

    def test_a_market_word_alone_is_not_promoted_into_a_target(self):
        """市場詞不能只靠中性詞（未來）就把自己升格成標的。"""
        for question in (
            "stock 未來七天會不會漲",
            "coin 未來七天會不會漲",
            "crypto 未來七天會不會漲",
            "shares 未來七天會不會漲",
        ):
            with self.subTest(question=question):
                self.assertEqual((), inspect_question(question).assets)

    def test_a_comparison_names_both_sides_explicitly(self):
        for question, assets in (
            ("比較 token 與 eth 過去七天強弱", ("TOKEN", "ETH")),
            ("比較 coin 與 nvda 未來走勢", ("COIN", "NVDA")),
            ("比較 BABYDOGE 與 ETH 過去七天強弱", ("BABYDOGE", "ETH")),
            ("比較 1INCH 與 ETH 過去七天強弱", ("1INCH", "ETH")),
            ("比較 1000SATS 與 BTC 過去七天強弱", ("1000SATS", "BTC")),
            ("比較 F 與 T 未來七天強弱", ("F", "T")),
            ("比較 1INCH 與 1000SATS 過去七天強弱", ("1INCH", "1000SATS")),
        ):
            with self.subTest(question=question):
                self.assertEqual(assets, inspect_question(question).assets)

    def test_a_pair_without_a_comparison_marker_names_nothing(self):
        """反向：沒有比較語境時，連接詞兩側只是散文。"""
        for question in ("price 與 volume 的關係如何", "apple 和 orange 哪個好吃"):
            with self.subTest(question=question):
                self.assertEqual((), inspect_question(question).assets)

    def test_an_excluded_candidate_never_steals_the_span_from_an_explicit_one(self):
        """被排除的候選不得先佔位置，否則同一個詞就補不回來了。"""
        for question, assets in (
            ("比較 crypto 與 stock 市場", ("CRYPTO", "STOCK")),
            ("比較 stock 與 crypto 市場", ("STOCK", "CRYPTO")),
            ("比較 token 與 stock 市場", ("TOKEN", "STOCK")),
            ("比較 coin 與 shares 市場", ("COIN", "SHARES")),
        ):
            with self.subTest(question=question):
                self.assertEqual(assets, inspect_question(question).assets)

    def test_a_counted_quantity_is_never_a_listing(self):
        for question in (
            "公司有 50000 股東",
            "公司共有 50000 股東",
            "公司有 50000 股東參加股東會",
            "持有 50000 股票",
            "買進 50000 股票",
            "成交量 50000 股代表什麼",
        ):
            with self.subTest(question=question):
                self.assertEqual((), inspect_question(question).assets)

    def test_a_company_topic_beside_a_code_keeps_the_code(self):
        for question in (
            "分析 2330 股災期間表現",
            "查詢 2330 股務代理",
            "2330 股份有限公司的展望",
            "2330 股數是多少",
            "2330 股東會是否配息",
            "2330 股價未來走勢",
        ):
            with self.subTest(question=question):
                self.assertEqual(("2330",), inspect_question(question).assets)

    def test_a_price_is_read_whole_so_its_currency_is_not_a_target(self):
        for question in (
            "BTC 漲到 NT$10000",
            "BTC 漲到 HK$10000",
            "BTC 漲到 CNY 10000",
            "BTC 漲到 US$10000",
            "BTC 漲到 $10000 的機率",
            "BTC 漲到 10000 USD",
        ):
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual(("BTC",), scope.assets)
                self.assertEqual(ASSET_CLASS_CRYPTO, scope.asset_class)

    def test_a_market_word_never_spans_a_demonstrative_seam(self):
        """``個股`` 是市場詞，但「這個股東會」裡的「個」屬於指示語。"""
        for question, asset_class in (
            ("policy 這個股東會議題", ASSET_CLASS_OPEN),
            ("analysis 這個股權結構", ASSET_CLASS_OPEN),
            ("analysis 這個股票投資策略", ASSET_CLASS_US_STOCK),
        ):
            with self.subTest(question=question):
                self.assertEqual(asset_class, inspect_question(question).asset_class)

    def test_a_market_word_alone_never_licenses_a_prose_word(self):
        """反向：鄰接市場詞只是名詞片語，不是指認。"""
        for question in (
            "分析 technology stock 未來走勢",
            "analyze cryptocurrency regulations",
            "評估 inflationary stock pressures",
        ):
            with self.subTest(question=question):
                self.assertEqual((), inspect_question(question).assets)

    def test_the_chinese_vocabulary_covers_the_common_ways_to_say_it(self):
        for question, asset_class in (
            ("虛擬幣未來七天會不會漲", ASSET_CLASS_CRYPTO),
            ("加密幣未來七天會不會漲", ASSET_CLASS_CRYPTO),
            ("數字貨幣市場最近怎麼樣", ASSET_CLASS_CRYPTO),
            ("數位貨幣市場最近怎麼樣", ASSET_CLASS_CRYPTO),
            ("台灣股市最近如何", ASSET_CLASS_TW_STOCK),
            ("臺灣股市最近如何", ASSET_CLASS_TW_STOCK),
            ("台灣股票最近如何", ASSET_CLASS_TW_STOCK),
            ("臺灣股票最近如何", ASSET_CLASS_TW_STOCK),
        ):
            with self.subTest(question=question):
                self.assertEqual(asset_class, inspect_question(question).asset_class)

    def test_the_more_specific_market_term_wins_across_classes(self):
        """『台灣股票』比泛用的『股票』更具體，不能被判成美股。"""
        scope = inspect_question("台灣股票最近如何")
        self.assertEqual(ASSET_CLASS_TW_STOCK, scope.asset_class)

        naming_a_target = inspect_question("DOGE 是虛擬幣，未來會不會漲")
        self.assertEqual(("DOGE",), naming_a_target.assets)
        self.assertEqual(ASSET_CLASS_CRYPTO, naming_a_target.asset_class)

    def test_specificity_only_beats_a_term_it_overlaps(self):
        """字元長度不等於語意具體度：只有蓋住同一段文字的詞才有資格壓過對方。"""
        for question in (
            "台灣股票（stock）最近如何",
            "台灣股票與 stock market 有何不同",
            "台灣股市（stock）最近如何",
        ):
            with self.subTest(question=question):
                self.assertEqual(
                    ASSET_CLASS_TW_STOCK, inspect_question(question).asset_class
                )

    def test_an_ambiguous_token_is_left_out_of_the_assets_entirely(self):
        """不可消歧時不給答案。

        ``nvda stock`` 與 ``value stock`` 在句法上完全相同，沒有詞典就分不出
        代號與形容詞。硬猜的代價不對稱：猜錯會讓 EvidenceGateway **拒絕**真正的
        研究對象，而空 assets 只是讓它退回只綁 run。所以兩者都不寫入 assets，
        分類仍由市場詞負責。``nvda stock`` 因此是刻意的保守漏認。
        """
        for question, asset_class in (
            ("value stock 未來如何", ASSET_CLASS_US_STOCK),
            ("growth stock 未來如何", ASSET_CLASS_US_STOCK),
            ("penny stock 未來如何", ASSET_CLASS_US_STOCK),
            ("meme coin 未來如何", ASSET_CLASS_CRYPTO),
            ("gas token 未來如何", ASSET_CLASS_CRYPTO),
            ("tech stock 未來如何", ASSET_CLASS_US_STOCK),
            ("bear market 未來如何", ASSET_CLASS_OPEN),
            ("price action 未來如何", ASSET_CLASS_OPEN),
            ("nvda stock 未來如何", ASSET_CLASS_US_STOCK),
        ):
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual((), scope.assets)
                self.assertEqual(asset_class, scope.asset_class)

    def test_an_english_word_ending_in_a_market_word_is_not_a_market_word(self):
        for question in (
            "livestock 價格未來七天會不會漲",
            "restock 事件的影響",
            "overstock 未來七天會不會漲",
            "feedstock 未來七天會不會漲",
            "deadstock 未來七天會不會漲",
            "woodstock 未來七天會不會漲",
            "laughingstock 未來七天會不會漲",
            "timeshares 未來七天會不會漲",
            "unshares 未來七天會不會漲",
            "betoken 未來七天會不會漲",
            "stockholder 未來七天會不會漲",
            "bespoken 未來七天會不會漲",
        ):
            with self.subTest(question=question):
                self.assertEqual(ASSET_CLASS_OPEN, inspect_question(question).asset_class)

    def test_a_suffix_collision_never_reclassifies_a_named_target(self):
        for question, assets in (
            ("DOGE livestock 價格上漲會怎樣", ("DOGE",)),
            ("SOL restock 事件的影響", ("SOL",)),
            ("BTC timeshares 市場的關係", ("BTC",)),
        ):
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual(assets, scope.assets)
                self.assertNotEqual(ASSET_CLASS_US_STOCK, scope.asset_class)

    def test_a_currency_or_policy_question_is_not_a_crypto_question(self):
        for question in (
            "台幣未來七天會不會升值",
            "人民幣未來七天會不會升值",
            "新台幣未來七天會不會升值",
            "貨幣政策是否會轉向",
            "資料加密法規會不會收緊",
        ):
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual((), scope.assets)
                self.assertEqual(ASSET_CLASS_OPEN, scope.asset_class)

    def test_complete_chinese_crypto_terms_still_classify(self):
        for question in (
            "分析過去 14 日加密市場整體狀態",
            "評估監管事件對加密市場的影響",
            "幣圈最近怎麼樣",
            "分析加密貨幣未來走勢",
            "若美國通過比特幣戰略儲備法案，BTC 與市場情緒會如何反應？",
        ):
            with self.subTest(question=question):
                self.assertEqual(ASSET_CLASS_CRYPTO, inspect_question(question).asset_class)

    def test_repeating_a_target_never_changes_the_asset_class(self):
        for once, twice in (
            ("COIN 未來七天會不會漲", "COIN 未來七天 COIN 會不會漲"),
            ("STOCK 未來七天會不會漲", "STOCK 未來七天 STOCK 會不會漲"),
            ("比較 COIN 與 NVDA 未來強弱", "比較 COIN 與 NVDA，COIN 誰較強"),
        ):
            with self.subTest(question=twice):
                first, second = inspect_question(once), inspect_question(twice)

                self.assertEqual(first.assets, second.assets)
                self.assertEqual(first.asset_class, second.asset_class)

    def test_a_market_word_never_matches_the_prefix_of_a_longer_word(self):
        """defi 不是 definite 的前綴掃描器；coin 不是 cointegration 的。"""
        for question in (
            "ada definite outlook 未來七天會不會漲",
            "apt definition 對價格的影響",
            "aapl cointegration 與利率的關係如何",
            "aapl stockholm 研討會之後會怎樣",
        ):
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual((), scope.assets)
                self.assertEqual(ASSET_CLASS_OPEN, scope.asset_class)

    def test_complete_market_terms_and_their_plurals_still_classify(self):
        for question, asset_class in (
            ("分析 cryptocurrency 市場未來走勢", ASSET_CLASS_CRYPTO),
            ("分析 cryptocurrencies 市場未來走勢", ASSET_CLASS_CRYPTO),
            ("分析 tokenomics 對價格的影響", ASSET_CLASS_CRYPTO),
            ("bitcoin 未來七天會不會漲", ASSET_CLASS_CRYPTO),
            ("nvda stocks 未來七天會不會漲", ASSET_CLASS_US_STOCK),
            ("ada coins 未來七天會不會漲", ASSET_CLASS_CRYPTO),
            ("分析 stocks 未來走勢", ASSET_CLASS_US_STOCK),
            ("分析 coins 未來走勢", ASSET_CLASS_CRYPTO),
            ("分析 tokens 未來走勢", ASSET_CLASS_CRYPTO),
        ):
            with self.subTest(question=question):
                self.assertEqual(asset_class, inspect_question(question).asset_class)

    def test_a_plural_market_word_is_not_a_target_either(self):
        for question in ("分析 stocks 未來走勢", "分析 coins 未來走勢", "分析 tokens 未來走勢"):
            with self.subTest(question=question):
                self.assertEqual((), inspect_question(question).assets)

    def test_trailing_text_outside_the_grammar_is_not_part_of_the_target(self):
        """``.USB`` 不是本文法的 suffix，所以它不屬於標的，也不會自成一個標的。"""
        scope = inspect_question("NVDA.USB 這檔美股未來七天股價會不會漲")

        self.assertEqual(("NVDA",), scope.assets)
        self.assertEqual("nvda", scope.asset_slug)

    def test_a_hyphenated_pair_is_two_targets_not_one_share_class(self):
        for question in ("btc-eth 會不會漲", "btc-eth 未來如何", "btc/eth 會不會漲"):
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual(("BTC", "ETH"), scope.assets)
                self.assertEqual(ASSET_CLASS_CRYPTO, scope.asset_class)

    def test_question_naming_no_asset_is_open(self):
        scope = inspect_question("幫我預測下週樂透號碼")

        self.assertEqual((), scope.assets)
        self.assertEqual(ASSET_CLASS_OPEN, scope.asset_class)

    def test_a_year_is_not_read_as_a_stock_code(self):
        scope = inspect_question("分析 BTC 在 2026 年的市場狀態")

        self.assertEqual(("BTC",), scope.assets)
        self.assertEqual(ASSET_CLASS_CRYPTO, scope.asset_class)

    def test_a_cashtag_names_a_target_of_any_shape(self):
        """``$`` says "ticker" outright, so no shape has to be inferred."""
        for question, assets in (
            ("$F 未來七天會不會漲", ("F",)),
            ("$1INCH 值得買嗎", ("1INCH",)),
            ("$BABYDOGE 未來如何", ("BABYDOGE",)),
            ("$BRK.B 未來如何", ("BRK.B",)),
            ("$BRK-B 未來如何", ("BRK.B",)),
            ("$NVDA 表現如何", ("NVDA",)),
            ("$BTC 未來如何", ("BTC",)),
            ("$ABCDEFGHIJKLMNOPQRST 未來如何", ("ABCDEFGHIJKLMNOPQRST",)),
        ):
            with self.subTest(question=question):
                self.assertEqual(assets, inspect_question(question).assets)


    def test_a_cashtag_ignores_casing_because_the_dollar_is_the_evidence(self):
        for question, assets in (
            ("$doge 未來如何", ("DOGE",)),
            ("$Doge 未來如何", ("DOGE",)),
            ("$dOgE 未來如何", ("DOGE",)),
            ("$brk-b 未來如何", ("BRK.B",)),
        ):
            with self.subTest(question=question):
                self.assertEqual(assets, inspect_question(question).assets)


    def test_a_bare_lower_case_word_still_names_nothing(self):
        """反向：沒有 ``$`` 就沒有證據，形狀相同的散文不能被猜成代號。"""
        for question in ("doge 未來如何", "policy 未來如何", "token 未來如何"):
            with self.subTest(question=question):
                self.assertEqual((), inspect_question(question).assets)


    def test_a_cashtag_works_on_both_sides_of_a_comparison(self):
        for question, assets in (
            ("比較 $F 與 $NVDA 過去七天強弱", ("F", "NVDA")),
            ("比較 $F 與 NVDA", ("F", "NVDA")),
            ("比較 $1INCH 與 $1000SATS 過去七天強弱", ("1INCH", "1000SATS")),
        ):
            with self.subTest(question=question):
                self.assertEqual(assets, inspect_question(question).assets)


    def test_a_dollar_followed_by_digits_alone_is_money_not_a_cashtag(self):
        """``$`` 的歧義只看它後面：有字母是代號，純數字是金額。"""
        for question in (
            "BTC 漲到 $10000 的機率",
            "BTC 從 $9000 漲到 $10000",
            "BTC 漲到 $10000USD",
            "BTC 漲到 $10000 HKD",
        ):
            with self.subTest(question=question):
                self.assertEqual(("BTC",), inspect_question(question).assets)


    def test_a_currency_prefix_never_swallows_the_ticker_in_front_of_it(self):
        for question, assets in (
            ("BTC$10000 會不會實現", ("BTC",)),
            ("ETH$5000 會不會實現", ("ETH",)),
            ("AAPL$200 會不會實現", ("AAPL",)),
        ):
            with self.subTest(question=question):
                self.assertEqual(assets, inspect_question(question).assets)


    def test_every_standard_currency_code_is_read_as_money(self):
        """The whole ISO 4217 alphabetic set, not a sample of it."""
        self.assertGreaterEqual(len(question_module._ISO_4217_CODES), 175)
        for code in question_module._ISO_4217_CODES:
            question = "BTC 漲到 {} 10000".format(code)
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual(("BTC",), scope.assets)
                self.assertEqual(ASSET_CLASS_CRYPTO, scope.asset_class)

    def test_every_unicode_currency_sign_is_read_as_money(self):
        """The sign set is Unicode's ``Sc`` category over the whole code space.

        "Whole" is the claim the name makes, so it is the claim checked here:
        six currency signs live above U+FFFF, and a sign the gate cannot see
        turns the amount beside it into a listing code.
        """
        signs = question_module._CURRENCY_SIGNS
        self.assertGreaterEqual(len(signs), 40)
        for sign in ("¥", "￥", "€", "£", "₩", "₹", "₽", "฿", "₪", "₫", "₴", "₦"):
            self.assertIn(sign, signs)
        above_the_basic_plane = [sign for sign in signs if ord(sign) > 0xFFFF]
        self.assertGreaterEqual(len(above_the_basic_plane), 6)
        for sign in signs:
            question = "BTC 漲到 {}10000 的機率".format(sign)
            with self.subTest(sign=sign):
                scope = inspect_question(question)

                self.assertEqual(("BTC",), scope.assets)
                # The end result above holds if *either* the money rule or the
                # listing reader's own lookbehind sees the sign, so it cannot
                # tell which one is doing the work. The claim this test makes
                # is about the money rule, so the money rule is asserted.
                self.assertRegex("{}10000".format(sign), question_module._MONEY_PATTERN)

    def test_a_lowercase_currency_lookalike_is_not_a_code(self):
        """Codes are spelled in upper case, which is what keeps words out.

        Adding the full standard brought in codes that are also ordinary
        English words (``ALL``, ``TOP``, ``CUP``, ``SEK``, ``PEN``). Reading
        them only as written is what stops 「top 10」 from becoming a price.
        """
        # Only codes the standard set brought in; the handful that were already
        # read case-insensitively keep that reading on purpose.
        collisions = ("ALL", "TOP", "CUP", "PEN", "SOS", "BOB", "MOP")
        self.assertFalse(
            set(collisions) & set(question_module._CASE_INSENSITIVE_CURRENCY_CODES)
        )
        for code in collisions:
            with self.subTest(code=code):
                self.assertIsNotNone(
                    question_module._MONEY_PATTERN.search("{} 10000".format(code))
                )
                self.assertIsNone(
                    question_module._MONEY_PATTERN.search("{} 10000".format(code.lower()))
                )

    def test_a_dollar_token_is_claimed_whole_whatever_it_says(self):
        """The invariant the module docstring states, checked directly.

        Every ``$`` run is classified and claimed as one token, so a reader
        that runs later can never reach inside it. The check is that the text
        the token covers is either named in full or named not at all — there is
        no spelling where a fragment of it comes back as a target.
        """
        unreadable = (
            "BRK_B", "ABC_DEF", "1_INCH", "1/INCH", "2330:TW", "2330:AAPL",
            "2330-2454", "2330-ABC", "2330.ABC", "2330/TW", "2330_TW",
            "AAPL:NASDAQ", "BTC/USD", "F_", "NVDA_US", "AAPL.NASDAQ",
            "2454:2330", "DOGE_BTC", "TSLA/AAPL", "BRK_B.US",
        )
        for body in unreadable:
            question = "${} 未來如何".format(body)
            with self.subTest(question=question):
                self.assertEqual(
                    (),
                    inspect_question(question).assets,
                    "「{}」 leaked a fragment".format(question),
                )

        readable = (
            ("F", ("F",)),
            ("1INCH", ("1INCH",)),
            ("BRK.B", ("BRK.B",)),
            ("BRK-B", ("BRK.B",)),
            ("2330.TW", ("2330",)),
            ("NVDA.US", ("NVDA",)),
        )
        for body, expected in readable:
            question = "${} 未來如何".format(body)
            with self.subTest(question=question):
                self.assertEqual(expected, inspect_question(question).assets)


    def test_a_currency_written_the_supported_ways_stays_money(self):
        for question, assets in (
            ("BTC 漲到 NT$10000", ("BTC",)),
            ("BTC 漲到 HK$10000", ("BTC",)),
            ("BTC 漲到 NT$ 10000", ("BTC",)),
            ("BTC 漲到 10000NTD", ("BTC",)),
            ("2330 漲到 NT$1000", ("2330",)),
        ):
            with self.subTest(question=question):
                self.assertEqual(assets, inspect_question(question).assets)


    def test_a_counted_quantity_may_carry_an_approximation(self):
        for question in (
            "公司有約 50000 股東",
            "公司擁有近 50000 股東",
            "公司共有超過 50000 股東",
            "公司有逾 50000 股東",
            "公司持有約 50000 股票",
            "有 50000 名股東",
            "成交 10000 張",
            "發行 10000 單位",
            "流通 50000 股",
        ):
            with self.subTest(question=question):
                self.assertEqual((), inspect_question(question).assets)


    def test_a_company_topic_beside_a_code_still_keeps_the_code(self):
        for question in ("2330 股東會", "2330 股東權益", "2330 股本形成"):
            with self.subTest(question=question):
                self.assertEqual(("2330",), inspect_question(question).assets)


    def test_a_demonstrative_no_longer_names_anything(self):
        """指示語授權整條刪除；改寫成 ``$`` 或大寫代號即可。"""
        for question in (
            "F 這檔美股未來七天股價會不會漲",
            "1INCH 這個幣未來七天會不會漲",
            "BABYDOGE 這個幣未來七天會不會漲",
            "iPhone 這個產品對股票市場的影響",
            "X 這個幣值問題",
            "X 這個個股投資術語",
            "F 這檔科技股票未來如何",
        ):
            with self.subTest(question=question):
                self.assertEqual((), inspect_question(question).assets)


    def test_a_demonstrative_no_longer_classifies_a_coin_either(self):
        self.assertEqual(ASSET_CLASS_OPEN, inspect_question("這個幣值問題").asset_class)
        self.assertEqual(ASSET_CLASS_OPEN, inspect_question("幣值問題").asset_class)
        self.assertEqual(
            ASSET_CLASS_CRYPTO, inspect_question("DOGE 幣價未來七天會不會漲").asset_class
        )


    def test_share_class_ticker_keeps_its_class_letter(self):
        for question in ("$BRK.B 未來七天會不會漲", "$BRK-B 未來七天會不會漲"):
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual(("BRK.B",), scope.assets)
                self.assertEqual("brk-b", scope.asset_slug)


    def test_crypto_words_classify_an_english_ticker(self):
        scope = inspect_question("DOGE 幣價未來七天會不會漲")

        self.assertEqual(("DOGE",), scope.assets)
        self.assertEqual(ASSET_CLASS_CRYPTO, scope.asset_class)


# The readings the review rounds settled on, keyed by a stable case id, as data.
# Reading them one at a time by eye is how a regression slipped through round 8,
# so they are executable here and the id manifest below is written out by hand.
#
# This is not claimed to be every input either reviewer has ever typed — it is
# every case whose reading was argued over and agreed, plus every probe the two
# reviewers cited in their reports. New arguments add rows; nothing removes one
# without failing ``test_no_settled_case_is_ever_dropped``.
CROSS_ROUND_CASES = {
    # ---- round 2: whitelist removal, five-coin regression -------------------
    "r2-btc-market-state": ("分析 BTC 過去 14 日市場狀態", ("BTC",), ASSET_CLASS_CRYPTO),
    "r2-btc-lower-bare": ("btc 會不會漲", ("BTC",), ASSET_CLASS_CRYPTO),
    "r2-xrp-btc-comparison": (
        "比較 XRP 與 BTC 過去 7 日的市場位置與風險", ("XRP", "BTC"), ASSET_CLASS_CRYPTO),
    "r2-sol-event": ("評估網路升級事件對 SOL 的影響", ("SOL",), ASSET_CLASS_CRYPTO),
    "r2-btc-price-action": ("分析 btc 過去 14 日 price action", ("BTC",), ASSET_CLASS_CRYPTO),
    "r2-price-action-btc": ("分析 price action，判斷 BTC 市場狀態", ("BTC",), ASSET_CLASS_CRYPTO),
    "r2-xrp-btc-mixed-case": (
        "比較 xrp 與 Btc 過去 14 日相對強弱", ("XRP", "BTC"), ASSET_CLASS_CRYPTO),
    "r2-eth-doge-mixed-case": ("分析 eth 跟 DoGe 的市場位置", ("ETH", "DOGE"), ASSET_CLASS_CRYPTO),
    "r2-2330-headline": ("幫我分析 2330 未來七天會不會漲", ("2330",), ASSET_CLASS_TW_STOCK),
    "r2-nvda-demonstrative": (
        "NVDA 這檔美股未來七天股價會不會漲", ("NVDA",), ASSET_CLASS_US_STOCK),
    "r2-doge-demonstrative": ("DOGE 這個幣未來七天會不會漲", ("DOGE",), ASSET_CLASS_OPEN),
    "r2-lottery-open": ("幫我預測下週樂透號碼", (), ASSET_CLASS_OPEN),
    "r2-nvda-bare-open": ("NVDA 未來七天會不會漲", ("NVDA",), ASSET_CLASS_OPEN),
    "r2-imf-not-a-target": ("IMF 今年會不會降息", (), ASSET_CLASS_OPEN),
    "r2-overall-crypto-market": ("分析過去 14 日加密市場整體狀態", (), ASSET_CLASS_CRYPTO),
    "r2-doge-single-asset": ("分析 DOGE 過去 14 日市場狀態", ("DOGE",), ASSET_CLASS_OPEN),
    # ---- round 3: dates, share classes, canonical spellings -----------------
    "r3-iso-date": ("截至 2024-08-05，FED 是否降息？", (), ASSET_CLASS_OPEN),
    "r3-slash-date": ("截至 2024/08/05，FED 是否降息？", (), ASSET_CLASS_OPEN),
    "r3-reverse-date": ("08-05-2024 之後市場會怎麼走？", (), ASSET_CLASS_OPEN),
    "r3-partial-date": ("截至 2024-08 的政策", (), ASSET_CLASS_OPEN),
    "r3-minguo-year": ("民國113年 CPI 走勢", (), ASSET_CLASS_OPEN),
    "r3-2330-tw-suffix": ("分析 2330.TW 未來七天走勢", ("2330",), ASSET_CLASS_TW_STOCK),
    "r3-brk-dot-b": ("BRK.B 這檔美股未來七天會不會漲", ("BRK.B",), ASSET_CLASS_US_STOCK),
    "r3-brk-dash-b": ("BRK-B 這檔美股未來七天會不會漲", ("BRK.B",), ASSET_CLASS_US_STOCK),
    # ---- round 4: atomic suffix consumption ---------------------------------
    "r4-2330-dash-tw": ("2330-TW 未來七天會不會漲", ("2330",), ASSET_CLASS_TW_STOCK),
    "r4-2330-dash-two": ("2330-TWO 未來七天會不會漲", ("2330",), ASSET_CLASS_TW_STOCK),
    "r4-aapl-hk": ("AAPL-HK 這檔股票未來七天會不會漲", ("AAPL",), ASSET_CLASS_US_STOCK),
    "r4-brk-b-tw": ("BRK-B-TW 未來七天會不會漲", ("BRK.B",), ASSET_CLASS_OPEN),
    "r4-2330-slash-2454": ("比較 2330/2454 未來七天強弱", ("2330", "2454"), ASSET_CLASS_TW_STOCK),
    "r4-2330-space-2454": ("2330 2454 未來七天比較", ("2330", "2454"), ASSET_CLASS_TW_STOCK),
    "r4-brk-b-vs-brk-a": ("比較 BRK-B 與 BRK-A", ("BRK.B", "BRK.A"), ASSET_CLASS_OPEN),
    "r4-btc-tw": ("BTC-TW 會不會漲", ("BTC",), ASSET_CLASS_CRYPTO),
    "r4-tsmc-us-2330-tw": ("TSMC-US 與 2330-TW 比較", ("TSMC", "2330"), ASSET_CLASS_TW_STOCK),
    "r4-btcusd": ("BTCUSD 會不會漲", ("BTCUSD",), ASSET_CLASS_OPEN),
    "r4-btc-dash-eth": ("btc-eth 會不會漲", ("BTC", "ETH"), ASSET_CLASS_CRYPTO),
    "r4-btc-slash-eth": ("btc/eth 會不會漲", ("BTC", "ETH"), ASSET_CLASS_CRYPTO),
    "r4-btc2": ("btc2 會不會漲", (), ASSET_CLASS_OPEN),
    # ---- round 5: English market words classify -----------------------------
    "r5-nvda-stock": ("nvda stock 未來七天會不會漲", (), ASSET_CLASS_US_STOCK),
    "r5-ada-coin": ("ada coin 未來七天會不會漲", (), ASSET_CLASS_CRYPTO),
    "r5-ada-crypto": ("ada crypto 未來七天會不會漲", (), ASSET_CLASS_CRYPTO),
    "r5-nvda-shares": ("nvda shares 未來七天會不會漲", (), ASSET_CLASS_US_STOCK),
    "r5-tsla-nyse": ("tsla nyse 未來七天會不會漲", (), ASSET_CLASS_US_STOCK),
    "r5-sol-defi": ("sol defi 未來七天會不會漲", ("SOL",), ASSET_CLASS_CRYPTO),
    "r5-usdc-stablecoin": ("usdc stablecoin 未來七天會不會漲", (), ASSET_CLASS_CRYPTO),
    "r5-brk-b-demonstrative": ("brk-b 這檔美股未來七天會不會漲", (), ASSET_CLASS_US_STOCK),
    "r5-nvda-demonstrative-lower": ("nvda 這檔美股未來七天會不會漲", (), ASSET_CLASS_US_STOCK),
    "r5-eth-token": ("eth token 未來七天會不會漲", ("ETH",), ASSET_CLASS_CRYPTO),
    "r5-aapl-sandp": ("aapl s&p 未來七天會不會漲", (), ASSET_CLASS_US_STOCK),
    "r5-apt-altcoin": ("apt altcoin 未來七天會不會漲", (), ASSET_CLASS_CRYPTO),
    # ---- round 6: whole-word boundaries, explicit tickers -------------------
    "r6-ada-definite": ("ada definite outlook 未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r6-apt-definition": ("apt definition 對價格的影響", (), ASSET_CLASS_OPEN),
    "r6-aapl-cointegration": ("aapl cointegration 與利率的關係如何", (), ASSET_CLASS_OPEN),
    "r6-aapl-stockholm": ("aapl stockholm 研討會之後會怎樣", (), ASSET_CLASS_OPEN),
    "r6-cryptocurrency": ("分析 cryptocurrency 市場未來走勢", (), ASSET_CLASS_CRYPTO),
    "r6-tokenomics": ("分析 tokenomics 對價格的影響", (), ASSET_CLASS_CRYPTO),
    "r6-bitcoin": ("bitcoin 未來七天會不會漲", (), ASSET_CLASS_CRYPTO),
    "r6-nvda-stocks-plural": ("nvda stocks 未來七天會不會漲", (), ASSET_CLASS_US_STOCK),
    "r6-ada-coins-plural": ("ada coins 未來七天會不會漲", (), ASSET_CLASS_CRYPTO),
    "r6-stock-alone": ("stock 未來七天會不會漲", (), ASSET_CLASS_US_STOCK),
    "r6-coin-alone": ("coin 未來七天會不會漲", (), ASSET_CLASS_CRYPTO),
    "r6-crypto-alone": ("crypto 未來七天會不會漲", (), ASSET_CLASS_CRYPTO),
    "r6-shares-alone": ("shares 未來七天會不會漲", (), ASSET_CLASS_US_STOCK),
    "r6-token-upper-demonstrative": ("TOKEN 這個幣未來會不會漲", ("TOKEN",), ASSET_CLASS_OPEN),
    "r6-coin-upper-demonstrative": ("COIN 這檔美股未來會不會漲", ("COIN",), ASSET_CLASS_US_STOCK),
    "r6-stock-upper-demonstrative": ("STOCK 這個幣未來會不會漲", ("STOCK",), ASSET_CLASS_OPEN),
    # ---- round 7: licensed identifiers, suffix collisions, Chinese terms ----
    "r7-f-us-stock": ("F 這檔美股未來七天股價會不會漲", (), ASSET_CLASS_US_STOCK),
    "r7-1inch": ("1INCH 這個幣未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-babydoge": ("BABYDOGE 這個幣未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-1000sats": ("1000SATS 這個幣未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-token-title-case": ("Token 這個幣未來會不會漲", (), ASSET_CLASS_CRYPTO),
    "r7-token-mixed-case": ("tOkEn 這個幣未來會不會漲", (), ASSET_CLASS_CRYPTO),
    "r7-token-lower-case": ("token 這個幣未來會不會漲", (), ASSET_CLASS_CRYPTO),
    "r7-coin-lower-case": ("coin 這檔美股未來七天股價會不會漲", (), ASSET_CLASS_CRYPTO),
    "r7-f-etf": ("F 這檔ETF未來如何", (), ASSET_CLASS_OPEN),
    "r7-livestock": ("livestock 價格未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-restock": ("restock 事件的影響", (), ASSET_CLASS_OPEN),
    "r7-overstock": ("overstock 未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-feedstock": ("feedstock 未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-deadstock": ("deadstock 未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-woodstock": ("woodstock 未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-laughingstock": ("laughingstock 未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-timeshares": ("timeshares 未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-unshares": ("unshares 未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-betoken": ("betoken 未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-bespoken": ("bespoken 未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-stockholder": ("stockholder 未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r7-doge-livestock": ("DOGE livestock 價格上漲會怎樣", ("DOGE",), ASSET_CLASS_OPEN),
    "r7-sol-restock": ("SOL restock 事件的影響", ("SOL",), ASSET_CLASS_CRYPTO),
    "r7-btc-timeshares": ("BTC timeshares 市場的關係", ("BTC",), ASSET_CLASS_CRYPTO),
    "r7-twd": ("台幣未來七天會不會升值", (), ASSET_CLASS_OPEN),
    "r7-cny": ("人民幣未來七天會不會升值", (), ASSET_CLASS_OPEN),
    "r7-ntd": ("新台幣未來七天會不會升值", (), ASSET_CLASS_OPEN),
    "r7-monetary-policy": ("貨幣政策是否會轉向", (), ASSET_CLASS_OPEN),
    "r7-data-encryption": ("資料加密法規會不會收緊", (), ASSET_CLASS_OPEN),
    "r7-crypto-circle": ("幣圈最近怎麼樣", (), ASSET_CLASS_CRYPTO),
    "r7-crypto-market-event": ("評估監管事件對加密市場的影響", (), ASSET_CLASS_CRYPTO),
    "r7-tsmc-competitor": ("台積電的競爭對手是誰", (), ASSET_CLASS_TW_STOCK),
    "r7-us-index": ("美股大盤如何", (), ASSET_CLASS_US_STOCK),
    "r7-coin-repeated": ("COIN 未來七天 COIN 會不會漲", ("COIN",), ASSET_CLASS_OPEN),
    "r7-stock-repeated": ("STOCK 未來七天 STOCK 會不會漲", ("STOCK",), ASSET_CLASS_OPEN),
    "r7-coin-nvda-repeated": (
        "比較 COIN 與 NVDA，COIN 誰較強", ("COIN", "NVDA"), ASSET_CLASS_OPEN),
    "r7-brk-b-us-spelling": ("BRK-B.US 這檔美股未來如何", ("BRK.B",), ASSET_CLASS_US_STOCK),
    # ---- round 8: one identifier grammar, claim after filter ----------------
    "r8-babydoge-comparison": (
        "比較 BABYDOGE 與 ETH 過去七天強弱", ("BABYDOGE", "ETH"), ASSET_CLASS_CRYPTO),
    "r8-1inch-eth-comparison": ("比較 1INCH 與 ETH 過去七天強弱", ("1INCH", "ETH"), ASSET_CLASS_CRYPTO),
    "r8-1000sats-btc-comparison": (
        "比較 1000SATS 與 BTC 過去七天強弱", ("1000SATS", "BTC"), ASSET_CLASS_CRYPTO),
    "r8-f-t-comparison": ("比較 F 與 T 未來七天強弱", ("F", "T"), ASSET_CLASS_OPEN),
    "r8-1inch-1000sats-comparison": (
        "比較 1INCH 與 1000SATS 過去七天強弱", ("1INCH", "1000SATS"), ASSET_CLASS_OPEN),
    "r8-crypto-stock-comparison": ("比較 crypto 與 stock 市場", ("CRYPTO", "STOCK"), ASSET_CLASS_OPEN),
    "r8-stock-crypto-comparison": ("比較 stock 與 crypto 市場", ("STOCK", "CRYPTO"), ASSET_CLASS_OPEN),
    "r8-token-stock-comparison": ("比較 token 與 stock 市場", ("TOKEN", "STOCK"), ASSET_CLASS_OPEN),
    "r8-coin-shares-comparison": ("比較 coin 與 shares 市場", ("COIN", "SHARES"), ASSET_CLASS_OPEN),
    "r8-token-eth-comparison": ("比較 token 與 eth 過去七天強弱", ("TOKEN", "ETH"), ASSET_CLASS_CRYPTO),
    "r8-coin-nvda-comparison": ("比較 coin 與 nvda 未來走勢", ("COIN", "NVDA"), ASSET_CLASS_OPEN),
    "r8-virtual-coin": ("虛擬幣未來七天會不會漲", (), ASSET_CLASS_CRYPTO),
    "r8-encrypted-coin": ("加密幣未來七天會不會漲", (), ASSET_CLASS_CRYPTO),
    "r8-digital-currency-simplified": ("數字貨幣市場最近怎麼樣", (), ASSET_CLASS_CRYPTO),
    "r8-digital-currency": ("數位貨幣市場最近怎麼樣", (), ASSET_CLASS_CRYPTO),
    "r8-taiwan-market": ("台灣股市最近如何", (), ASSET_CLASS_TW_STOCK),
    "r8-taiwan-market-alt": ("臺灣股市最近如何", (), ASSET_CLASS_TW_STOCK),
    "r8-taiwan-stock": ("台灣股票最近如何", (), ASSET_CLASS_TW_STOCK),
    "r8-taiwan-stock-alt": ("臺灣股票最近如何", (), ASSET_CLASS_TW_STOCK),
    "r8-doge-virtual-coin": ("doge 虛擬幣未來會不會漲", (), ASSET_CLASS_CRYPTO),
    "r8-doge-is-virtual-coin": ("DOGE 是虛擬幣，未來會不會漲", ("DOGE",), ASSET_CLASS_CRYPTO),
    "r8-technology-stock": ("分析 technology stock 未來走勢", (), ASSET_CLASS_US_STOCK),
    "r8-analyze-cryptocurrency": ("analyze cryptocurrency regulations", (), ASSET_CLASS_CRYPTO),
    "r8-inflationary-stock": ("評估 inflationary stock pressures", (), ASSET_CLASS_US_STOCK),
    "r8-unshares-verb": ("unshares 這個動詞的意思", (), ASSET_CLASS_OPEN),
    "r8-hardcoin-coinage": ("hardcoin 這個新造詞的語意", (), ASSET_CLASS_OPEN),
    "r8-price-volume-no-marker": ("price 與 volume 的關係如何", (), ASSET_CLASS_OPEN),
    "r8-long-identifier": ("ABCDEFGHIJKLM 這個幣未來會不會漲", (), ASSET_CLASS_OPEN),
    # ---- round 9: asset nouns, span-overlap specificity ---------------------
    "r9-1inch-virtual-currency": ("1INCH 這個虛擬貨幣未來會不會漲", (), ASSET_CLASS_CRYPTO),
    "r9-1inch-crypto-currency": ("1INCH 這個加密貨幣未來會不會漲", (), ASSET_CLASS_CRYPTO),
    "r9-1inch-digital-currency": ("1INCH 這個數字貨幣未來會不會漲", (), ASSET_CLASS_CRYPTO),
    "r9-f-target-noun": ("F 這個標的未來如何", (), ASSET_CLASS_OPEN),
    "r9-f-asset-noun": ("F 這項資產未來如何", (), ASSET_CLASS_OPEN),
    "r9-regulation-crypto-issue": ("regulation 這個幣圈議題的走向", (), ASSET_CLASS_CRYPTO),
    "r9-technology-stock-market-term": ("technology 這個股票市場術語", (), ASSET_CLASS_US_STOCK),
    "r9-policy-monetary": ("policy 這個貨幣政策的方向", (), ASSET_CLASS_OPEN),
    "r9-taiwan-stock-vs-stock": ("台灣股票（stock）最近如何", (), ASSET_CLASS_TW_STOCK),
    "r9-taiwan-stock-and-stock-market": (
        "台灣股票與 stock market 有何不同", (), ASSET_CLASS_TW_STOCK),
    "r9-taiwan-market-vs-stock": ("台灣股市（stock）最近如何", (), ASSET_CLASS_TW_STOCK),
    "r9-fomc": ("FOMC 會後市場會怎麼走", (), ASSET_CLASS_OPEN),
    "r9-sec": ("SEC 會不會放行", (), ASSET_CLASS_OPEN),
    "r9-cpi": ("CPI 會不會再升", (), ASSET_CLASS_OPEN),
    "r9-gdp": ("GDP 會不會下修", (), ASSET_CLASS_OPEN),
    "r9-acronym-run": ("FOMC SEC CPI GDP 對市場的影響", (), ASSET_CLASS_OPEN),
    "r9-2026-year": ("分析 BTC 在 2026 年的市場狀態", ("BTC",), ASSET_CLASS_CRYPTO),
    # ---- round 10: quantities, currencies, the head-noun rule ---------------
    "r10-dollar-price": ("BTC 漲到 $10000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    "r10-us-dollar-price": ("BTC 漲到 US$10000", ("BTC",), ASSET_CLASS_CRYPTO),
    "r10-iso-currency": ("BTC 漲到 10000 USD", ("BTC",), ASSET_CLASS_CRYPTO),
    "r10-share-quantity": ("成交量 50000 股代表什麼", (), ASSET_CLASS_OPEN),
    "r10-lot-quantity": ("成交量 50000 張代表什麼", (), ASSET_CLASS_OPEN),
    "r10-trade-count": ("今天成交 10000 筆", (), ASSET_CLASS_OPEN),
    "r10-fund-units": ("基金持有 10000 份", (), ASSET_CLASS_OPEN),
    "r10-buy-units": ("買進 10000 單位", (), ASSET_CLASS_OPEN),
    "r10-2330-share-price": ("分析 2330 股價未來走勢", ("2330",), ASSET_CLASS_TW_STOCK),
    "r10-value-stock": ("value stock 未來如何", (), ASSET_CLASS_US_STOCK),
    "r10-growth-stock": ("growth stock 未來如何", (), ASSET_CLASS_US_STOCK),
    "r10-penny-stock": ("penny stock 未來如何", (), ASSET_CLASS_US_STOCK),
    "r10-tech-stock": ("tech stock 未來如何", (), ASSET_CLASS_US_STOCK),
    "r10-meme-coin": ("meme coin 未來如何", (), ASSET_CLASS_CRYPTO),
    "r10-gas-token": ("gas token 未來如何", (), ASSET_CLASS_CRYPTO),
    "r10-bear-market": ("bear market 未來如何", (), ASSET_CLASS_OPEN),
    "r10-price-action": ("price action 未來如何", (), ASSET_CLASS_OPEN),
    "r10-nvda-stock-conservative": ("nvda stock 未來如何", (), ASSET_CLASS_US_STOCK),
    "r10-nvda-upper-stock": ("NVDA stock 未來如何", ("NVDA",), ASSET_CLASS_US_STOCK),
    "r10-1inch-worth-buying": ("1INCH 這個幣值得買嗎", (), ASSET_CLASS_OPEN),
    "r10-1inch-will-it-rise": ("1INCH 這個幣漲不漲", (), ASSET_CLASS_OPEN),
    "r10-f-etf-performance": ("F 這檔ETF表現如何", (), ASSET_CLASS_OPEN),
    "r10-f-target-trend": ("F 這個標的走勢如何", (), ASSET_CLASS_OPEN),
    "r10-1inch-particle-ya": ("1INCH 這個幣呀", (), ASSET_CLASS_OPEN),
    "r10-1inch-particle-bei": ("1INCH 這個幣唄", (), ASSET_CLASS_OPEN),
    "r10-1inch-particle-ne": ("1INCH 這個幣呢", (), ASSET_CLASS_OPEN),
    "r10-1inch-itself": ("1INCH 這個幣本身", (), ASSET_CLASS_OPEN),
    "r10-1inch-as-for": ("1INCH 這個幣而言", (), ASSET_CLASS_OPEN),
    "r10-policy-currency-of": ("policy 這個貨幣的政策方向", (), ASSET_CLASS_OPEN),
    "r10-regulation-coin-of": ("regulation 這個幣的監管議題", (), ASSET_CLASS_OPEN),
    "r10-analysis-asset-of": ("analysis 這個資產的配置議題", (), ASSET_CLASS_OPEN),
    "r10-regulation-issue-but-coin": ("regulation 這個議題但幣會漲嗎", (), ASSET_CLASS_OPEN),
    "r10-technology-term-but-stock": ("technology 這個術語但股票會漲嗎", (), ASSET_CLASS_US_STOCK),
    "r10-policy-lets-stock": ("policy 這個政策讓股票會漲嗎", (), ASSET_CLASS_US_STOCK),
    "r10-doge-this-kind-of-coin": ("DOGE 這種幣未來會不會漲", ("DOGE",), ASSET_CLASS_OPEN),
    "r10-f-that-stock": ("F 那支股票未來如何", (), ASSET_CLASS_US_STOCK),
    "r10-definitely-this-coin": ("分析 definitely 這個幣未來如何", (), ASSET_CLASS_OPEN),
    "r10-x-this-thing": ("X 這個東西未來如何", (), ASSET_CLASS_OPEN),
    "r10-modifier-tech-stock": ("F 這檔科技股票未來如何", (), ASSET_CLASS_US_STOCK),
    "r10-modifier-ai-stock": ("F 這檔AI股票未來如何", (), ASSET_CLASS_US_STOCK),
    "r10-modifier-high-dividend": ("F 這檔高股息股票未來如何", (), ASSET_CLASS_US_STOCK),
    "r10-crypto-circle-future": ("F 這個幣圈未來如何", (), ASSET_CLASS_CRYPTO),
    "r10-single-letter-comparison": ("比較 A 與 B 未來七天強弱", ("A", "B"), ASSET_CLASS_OPEN),
    "r10-modifier-semiconductor-growth": ("F 這檔半導體成長型股票未來如何", (), ASSET_CLASS_US_STOCK),
    "r10-crypto-circle-token": ("F 這個幣圈代幣未來如何", (), ASSET_CLASS_CRYPTO),
    "r10-modifier-us-listed": ("F 這檔在美上市股票如何", (), ASSET_CLASS_US_STOCK),
    "r10-modifier-popular-this-year": ("F 這檔今年熱門股票如何", (), ASSET_CLASS_US_STOCK),
    "r11-cross-clause-coin": ("regulation 這個幣會漲嗎的議題", (), ASSET_CLASS_OPEN),
    "r11-cross-clause-stock": ("analysis 這個股票會漲嗎的問題", (), ASSET_CLASS_US_STOCK),
    "r11-cross-clause-rate-cut": ("policy 這個股票會因降息上漲的說法", (), ASSET_CLASS_US_STOCK),
    "r11-cross-clause-compliance": ("regulation 這個幣是否合規的議題", (), ASSET_CLASS_OPEN),
    "r11-genitive-de-trend": ("1INCH 這個幣的走勢", (), ASSET_CLASS_OPEN),
    "r11-genitive-de-future": ("BABYDOGE 這個幣的未來", (), ASSET_CLASS_OPEN),
    "r11-genitive-zhi-trend": ("X 這個幣之走勢", (), ASSET_CLASS_OPEN),
    "r11-genitive-zhi-prose": ("policy 這個幣之監管議題", (), ASSET_CLASS_OPEN),
    "r11-genitive-yu-prose": ("policy 這個幣與股票的監管議題", (), ASSET_CLASS_US_STOCK),
    "r11-compound-coin-value": ("policy 這個幣值問題", (), ASSET_CLASS_OPEN),
    "r11-compound-coin-reform": ("policy 這個幣制改革", (), ASSET_CLASS_OPEN),
    "r11-compound-coin-choice": ("policy 這個幣別選擇", (), ASSET_CLASS_OPEN),
    "r11-compound-currency-system": ("policy 這個貨幣制度", (), ASSET_CLASS_OPEN),
    "r11-compound-money-supply": ("policy 這個貨幣供給議題", (), ASSET_CLASS_OPEN),
    "r11-compound-ticker-code": ("policy 這個股票代碼問題", (), ASSET_CLASS_US_STOCK),
    "r11-compound-trading-term": ("policy 這個股票交易術語", (), ASSET_CLASS_US_STOCK),
    "r11-compound-tokenisation": ("policy 這個代幣化議題", (), ASSET_CLASS_CRYPTO),
    "r11-compound-shareholder-meeting": ("policy 這個股東會議題", (), ASSET_CLASS_OPEN),
    "r11-compound-asset-class": ("policy 這個資產階級議題", (), ASSET_CLASS_OPEN),
    "r11-compound-asset-management": ("policy 這個資產管理問題", (), ASSET_CLASS_OPEN),
    "r11-compound-equity-structure": ("analysis 這個股權結構", (), ASSET_CLASS_OPEN),
    "r11-compound-dividend-policy": ("analysis 這個股息政策", (), ASSET_CLASS_OPEN),
    "r11-compound-payout-policy": ("analysis 這個股利政策", (), ASSET_CLASS_OPEN),
    "r11-compound-investment-strategy": ("analysis 這個股票投資策略", (), ASSET_CLASS_US_STOCK),
    "r11-compound-token-issue-rules": ("analysis 這個代幣發行規則", (), ASSET_CLASS_CRYPTO),
    "r11-compound-stockholder": ("X 這個stockholder議題", (), ASSET_CLASS_OPEN),
    "r11-compound-cointegration": ("X 這個cointegration模型", (), ASSET_CLASS_OPEN),
    "r11-compound-tokenizer": ("X 這個tokenizer模組", (), ASSET_CLASS_OPEN),
    "r11-shareholder-meeting-code": ("2330 股東會是否配息", ("2330",), ASSET_CLASS_TW_STOCK),
    "r11-payout-code": ("2330 股利會增加嗎", ("2330",), ASSET_CLASS_TW_STOCK),
    "r11-dividend-yield-code": ("2330 股息殖利率如何", ("2330",), ASSET_CLASS_TW_STOCK),
    "r11-hold-shares-quantity": ("持有 50000 股票", (), ASSET_CLASS_US_STOCK),
    "r11-buy-shares-quantity": ("買進 50000 股票", (), ASSET_CLASS_US_STOCK),
    "r12-scan-iphone-product": ("iPhone 這個產品對股票市場的影響", (), ASSET_CLASS_US_STOCK),
    "r12-scan-rating": ("A 這個評級對股票價格的影響", (), ASSET_CLASS_US_STOCK),
    "r12-scan-pronoun": ("I 這個英文代名詞對股票市場的影響", (), ASSET_CLASS_US_STOCK),
    "r12-scan-unknown": ("X 這個未知數對股票價格的影響", (), ASSET_CLASS_US_STOCK),
    "r12-scan-name": ("Xi 這個名字對股票市場的影響", (), ASSET_CLASS_US_STOCK),
    "r12-scan-test-concept": ("e2e 這個測試概念對股票市場的影響", (), ASSET_CLASS_US_STOCK),
    "r12-scan-business-model": ("b2b 這個商業模式對股票市場的影響", (), ASSET_CLASS_US_STOCK),
    "r12-scan-brand": ("eBay 這個品牌對股票市場的影響", (), ASSET_CLASS_US_STOCK),
    "r12-scan-serial": ("1INCH 這個編號對股票市場的影響", (), ASSET_CLASS_US_STOCK),
    "r12-scan-number": ("1000SATS 這個數字對股票市場的影響", (), ASSET_CLASS_US_STOCK),
    "r12-scan-shareholder-meeting": ("X 這個股東會議題", (), ASSET_CLASS_OPEN),
    "r12-quantity-shareholders": ("公司有 50000 股東", (), ASSET_CLASS_OPEN),
    "r12-quantity-shareholders-total": ("公司共有 50000 股東", (), ASSET_CLASS_OPEN),
    "r12-quantity-shareholders-attend": ("公司有 50000 股東參加股東會", (), ASSET_CLASS_OPEN),
    "r12-code-crash": ("分析 2330 股災期間表現", ("2330",), ASSET_CLASS_TW_STOCK),
    "r12-code-registrar": ("查詢 2330 股務代理", ("2330",), ASSET_CLASS_TW_STOCK),
    "r12-code-company": ("2330 股份有限公司的展望", ("2330",), ASSET_CLASS_TW_STOCK),
    "r12-code-share-count": ("2330 股數是多少", ("2330",), ASSET_CLASS_TW_STOCK),
    "r12-currency-ntd": ("BTC 漲到 NT$10000", ("BTC",), ASSET_CLASS_CRYPTO),
    "r12-currency-hkd": ("BTC 漲到 HK$10000", ("BTC",), ASSET_CLASS_CRYPTO),
    "r12-currency-cny": ("BTC 漲到 CNY 10000", ("BTC",), ASSET_CLASS_CRYPTO),
    "r12-mixed-market-comparison": (
        "比較 nvda stock 與 2330 台股", ("STOCK", "2330"), ASSET_CLASS_TW_STOCK),
    "r12-single-letter-pair": ("比較 A 與 B", ("A", "B"), ASSET_CLASS_OPEN),
    "r12-value-stock-analysis": ("分析 value stock 未來走勢", (), ASSET_CLASS_US_STOCK),
    "r12-popular-tech-stock": ("F 這檔受歡迎的科技股票未來如何", (), ASSET_CLASS_US_STOCK),
    "r13-cashtag-single-letter": ("$F 未來七天會不會漲", ("F",), ASSET_CLASS_OPEN),
    "r13-cashtag-leading-digit": ("$1INCH 值得買嗎", ("1INCH",), ASSET_CLASS_OPEN),
    "r13-cashtag-long": ("$BABYDOGE 未來如何", ("BABYDOGE",), ASSET_CLASS_OPEN),
    "r13-cashtag-share-class-dot": ("$BRK.B 未來如何", ("BRK.B",), ASSET_CLASS_OPEN),
    "r13-cashtag-share-class-dash": ("$BRK-B 未來如何", ("BRK.B",), ASSET_CLASS_OPEN),
    "r13-cashtag-upper": ("$NVDA 表現如何", ("NVDA",), ASSET_CLASS_OPEN),
    "r13-cashtag-legacy": ("$BTC 未來如何", ("BTC",), ASSET_CLASS_CRYPTO),
    "r13-cashtag-lower": ("$doge 未來如何", ("DOGE",), ASSET_CLASS_OPEN),
    "r13-cashtag-mixed-case": ("$dOgE 未來如何", ("DOGE",), ASSET_CLASS_OPEN),
    "r13-cashtag-comparison": (
        "比較 $F 與 $NVDA 過去七天強弱", ("F", "NVDA"), ASSET_CLASS_OPEN),
    "r13-cashtag-comparison-mixed": ("比較 $F 與 NVDA", ("F", "NVDA"), ASSET_CLASS_OPEN),
    "r13-dollar-digits-is-money": ("BTC 漲到 $10000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    "r13-dollar-digits-numeric-code": ("$2330 未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r13-money-not-swallowing-btc": ("BTC$10000 會不會實現", ("BTC",), ASSET_CLASS_CRYPTO),
    "r13-money-not-swallowing-eth": ("ETH$5000 會不會實現", ("ETH",), ASSET_CLASS_CRYPTO),
    "r13-money-not-swallowing-aapl": ("AAPL$200 會不會實現", ("AAPL",), ASSET_CLASS_OPEN),
    "r13-currency-aud": ("BTC 漲到 AUD 10000", ("BTC",), ASSET_CLASS_CRYPTO),
    "r13-currency-cad": ("BTC 漲到 CAD 10000", ("BTC",), ASSET_CLASS_CRYPTO),
    "r13-currency-chf": ("BTC 漲到 CHF 10000", ("BTC",), ASSET_CLASS_CRYPTO),
    "r13-currency-sgd": ("BTC 漲到 SGD 10000", ("BTC",), ASSET_CLASS_CRYPTO),
    "r13-currency-krw": ("BTC 漲到 KRW 10000", ("BTC",), ASSET_CLASS_CRYPTO),
    "r13-currency-trailing-iso": ("BTC 漲到 $10000 HKD", ("BTC",), ASSET_CLASS_CRYPTO),
    "r13-currency-spaced-prefix": ("BTC 漲到 NT$ 10000", ("BTC",), ASSET_CLASS_CRYPTO),
    "r13-currency-suffixed": ("BTC 漲到 10000NTD", ("BTC",), ASSET_CLASS_CRYPTO),
    "r13-currency-dollar-usd": ("BTC 漲到 $10000USD", ("BTC",), ASSET_CLASS_CRYPTO),
    "r13-currency-two-amounts": ("BTC 從 $9000 漲到 $10000", ("BTC",), ASSET_CLASS_CRYPTO),
    "r13-currency-code-with-listing": ("2330 漲到 NT$1000", ("2330",), ASSET_CLASS_TW_STOCK),
    "r13-quantity-about": ("公司有約 50000 股東", (), ASSET_CLASS_OPEN),
    "r13-quantity-nearly": ("公司擁有近 50000 股東", (), ASSET_CLASS_OPEN),
    "r13-quantity-more-than": ("公司共有超過 50000 股東", (), ASSET_CLASS_OPEN),
    "r13-quantity-over": ("公司有逾 50000 股東", (), ASSET_CLASS_OPEN),
    "r13-quantity-holds-about": ("公司持有約 50000 股票", (), ASSET_CLASS_US_STOCK),
    "r13-quantity-named-holders": ("有 50000 名股東", (), ASSET_CLASS_OPEN),
    "r13-quantity-lots": ("成交 10000 張", (), ASSET_CLASS_OPEN),
    "r13-quantity-units": ("發行 10000 單位", (), ASSET_CLASS_OPEN),
    "r13-quantity-shares-outstanding": ("流通 50000 股", (), ASSET_CLASS_OPEN),
    "r13-code-equity": ("2330 股東權益", ("2330",), ASSET_CLASS_TW_STOCK),
    "r13-code-capital": ("2330 股本形成", ("2330",), ASSET_CLASS_TW_STOCK),
    "r13-demonstrative-gone-f": ("F 這檔美股未來七天股價會不會漲", (), ASSET_CLASS_US_STOCK),
    "r13-demonstrative-gone-1inch": ("1INCH 這個幣未來七天會不會漲", (), ASSET_CLASS_OPEN),
    "r13-demonstrative-gone-iphone": (
        "iPhone 這個產品對股票市場的影響", (), ASSET_CLASS_US_STOCK),
    "r13-demonstrative-gone-compound": ("X 這個幣值問題", (), ASSET_CLASS_OPEN),
    "r13-demonstrative-gone-individual": ("X 這個個股投資術語", (), ASSET_CLASS_US_STOCK),
    "r13-coin-price-classifies": ("DOGE 幣價未來七天會不會漲", ("DOGE",), ASSET_CLASS_CRYPTO),
    # --- Round 14 -----------------------------------------------------------
    # X1, false-positive direction: a ``$`` token nobody can read is consumed
    # whole, so no reader may pick up the fragment its prefix happens to spell.
    "r14-dollar-unreadable-share-underscore": ("$BRK_B 未來如何", (), ASSET_CLASS_OPEN),
    "r14-dollar-unreadable-letters-underscore": ("$ABC_DEF 未來如何", (), ASSET_CLASS_OPEN),
    "r14-dollar-unreadable-digit-underscore": ("$1_INCH 未來如何", (), ASSET_CLASS_OPEN),
    "r14-dollar-unreadable-slash": ("$1/INCH 未來如何", (), ASSET_CLASS_OPEN),
    "r14-dollar-unreadable-code-colon": ("$2330:TW 未來如何", (), ASSET_CLASS_OPEN),
    "r14-dollar-unreadable-code-colon-ticker": ("$2330:AAPL 未來如何", (), ASSET_CLASS_OPEN),
    "r14-dollar-unreadable-code-pair": ("$2330-2454 未來如何", (), ASSET_CLASS_OPEN),
    "r14-dollar-unreadable-unknown-suffix": ("$2330-ABC 未來如何", (), ASSET_CLASS_OPEN),
    "r14-dollar-unreadable-unknown-dot-suffix": ("$2330.ABC 未來如何", (), ASSET_CLASS_OPEN),
    "r14-dollar-unreadable-code-slash": ("$2330/TW 未來如何", (), ASSET_CLASS_OPEN),
    "r14-dollar-unreadable-code-underscore": ("$2330_TW 未來如何", (), ASSET_CLASS_OPEN),
    # X1, false-negative direction: a legal cashtag is still read, and a known
    # exchange suffix is the evidence that the digits are a listing, not money.
    "r14-dollar-code-dot-tw": ("$2330.TW 未來七天會不會漲", ("2330",), ASSET_CLASS_TW_STOCK),
    "r14-dollar-code-dash-tw": ("$2330-TW 未來七天會不會漲", ("2330",), ASSET_CLASS_TW_STOCK),
    "r14-dollar-code-dot-two": ("$2330.TWO 未來七天會不會漲", ("2330",), ASSET_CLASS_TW_STOCK),
    "r14-dollar-code-dash-two": ("$2330-TWO 未來七天會不會漲", ("2330",), ASSET_CLASS_TW_STOCK),
    # Normalisation drops the exchange, so the code's own shape decides the
    # market — the same reading a bare ``2330.US`` has always had.
    "r14-dollar-code-dot-us": ("$2330.US 未來七天會不會漲", ("2330",), ASSET_CLASS_TW_STOCK),
    "r14-dollar-code-dot-hk": ("$2330.HK 未來七天會不會漲", ("2330",), ASSET_CLASS_TW_STOCK),
    # No separator means no suffix: the cashtag names exactly what was written.
    "r14-dollar-code-glued-letters": ("$2330TW 未來如何", ("2330TW",), ASSET_CLASS_OPEN),
    # A space means the ``$`` was never part of the word, so the ordinary
    # readers must still get to see it.
    "r14-dollar-detached-ticker": ("$ AAPL 未來如何", ("AAPL",), ASSET_CLASS_OPEN),
    "r14-dollar-trailing-comma": ("$AAPL, 未來如何", ("AAPL",), ASSET_CLASS_OPEN),
    # The documented cost of letting a suffix license the listing reading.
    "r14-dollar-amount-suffixed": ("$10000-TW 未來如何", ("10000",), ASSET_CLASS_TW_STOCK),
    "r14-dollar-spaced-amount": ("BTC 漲到 $ 10000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    # X2, false-positive direction: currency is never a target, whichever sign
    # or code it is written with.
    "r14-currency-sign-latin1-yen": ("BTC 漲到 ¥10000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    "r14-currency-sign-won": ("BTC 漲到 ₩10000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    "r14-currency-sign-rupee": ("BTC 漲到 ₹10000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    "r14-currency-sign-ruble": ("BTC 漲到 ₽10000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    "r14-currency-sign-baht": ("BTC 漲到 ฿10000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    "r14-currency-code-bdt": ("BTC 漲到 BDT 10000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    "r14-currency-code-pkr": ("BTC 漲到 PKR 10000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    "r14-currency-code-trailing-ngn": ("BTC 漲到 10000 NGN 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    "r14-currency-iso-prefixed-sign": ("BTC 漲到 CAD$10000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    # X2, false-negative direction: a prefix that is not a currency stays a
    # target. ``MX`` is not an ISO 4217 code, so ``MX$10000`` names MX.
    "r14-money-not-swallowing-non-iso-prefix": ("MX$10000 會不會實現", ("MX",), ASSET_CLASS_OPEN),
    "r14-money-not-swallowing-nvda": ("NVDA$200 會不會實現", ("NVDA",), ASSET_CLASS_OPEN),
    # X3, false-positive direction: the modifier slot is a syntactic range, so
    # degree words nobody listed are still part of the count.
    "r14-quantity-modifier-gaoda": ("公司有高達 50000 股東", (), ASSET_CLASS_OPEN),
    "r14-quantity-modifier-zuzu": ("公司有足足 50000 股東", (), ASSET_CLASS_OPEN),
    "r14-quantity-modifier-yuemo": ("公司有約莫 50000 股東", (), ASSET_CLASS_OPEN),
    "r14-quantity-modifier-dagai": ("公司有大概 50000 股東", (), ASSET_CLASS_OPEN),
    "r14-quantity-modifier-jinhu": ("公司有近乎 50000 股東", (), ASSET_CLASS_OPEN),
    "r14-quantity-modifier-buxia": ("公司有不下 50000 股東", (), ASSET_CLASS_OPEN),
    "r14-quantity-modifier-zhengzheng": ("公司有整整 12345 位股東", (), ASSET_CLASS_OPEN),
    # X3, false-negative direction: a counting verb somewhere in the sentence
    # must not cost the listing behind it.
    "r14-quantity-verb-then-listing": (
        "我持有台股想知道 2330 股價會不會漲", ("2330",), ASSET_CLASS_TW_STOCK,
    ),
    "r14-quantity-verb-then-listing-holdings": (
        "持股包含台股的 2330 值得續抱嗎", ("2330",), ASSET_CLASS_TW_STOCK,
    ),
    "r14-quantity-shareholder-equity": ("股東權益報酬率如何", (), ASSET_CLASS_OPEN),
    # --- Round 15 -----------------------------------------------------------
    # X1, false-positive direction: a Chinese IME's separator is the same
    # separator, so it must not reopen the fragment hole the ASCII form closed.
    "r15-dollar-fullwidth-underscore": ("$BRK＿B 未來如何", (), ASSET_CLASS_OPEN),
    "r15-dollar-fullwidth-underscore-letters": ("$ABC＿DEF 未來如何", (), ASSET_CLASS_OPEN),
    "r15-dollar-fullwidth-underscore-digit": ("$1＿INCH 未來如何", (), ASSET_CLASS_OPEN),
    "r15-dollar-fullwidth-dash-unknown": ("$2330－ABC 未來如何", (), ASSET_CLASS_OPEN),
    "r15-dollar-fullwidth-slash": ("$2330／TW 未來如何", (), ASSET_CLASS_OPEN),
    "r15-dollar-fullwidth-colon": ("$2330：AAPL 未來如何", (), ASSET_CLASS_OPEN),
    "r15-dollar-fullwidth-underscore-code": ("$2330＿TW 未來如何", (), ASSET_CLASS_OPEN),
    # X1, false-negative direction: the compatibility spelling of a legal token
    # still reads, including the marker and the identifier themselves.
    "r15-dollar-fullwidth-marker": ("＄BRK.B 未來如何", ("BRK.B",), ASSET_CLASS_OPEN),
    "r15-dollar-fullwidth-dot-suffix": ("$2330．TW 未來七天會不會漲", ("2330",), ASSET_CLASS_TW_STOCK),
    "r15-dollar-fullwidth-dash-suffix": ("$2330－TW 未來七天會不會漲", ("2330",), ASSET_CLASS_TW_STOCK),
    "r15-fullwidth-cashtag-ticker": ("＄ＮＶＤＡ 未來如何", ("NVDA",), ASSET_CLASS_OPEN),
    "r15-fullwidth-taiwan-code": ("２３３０ 未來七天會不會漲", ("2330",), ASSET_CLASS_TW_STOCK),
    "r15-fullwidth-comparison": ("比較 ＄Ｆ 與 NVDA", ("F", "NVDA"), ASSET_CLASS_OPEN),
    # X2, false-positive direction: confirming money wants the standard's own
    # upper case, but *excluding* a number from the listing reader asks only
    # that the word beside it be code-shaped, in any case at all.
    "r15-currency-lowercase-bdt": ("比特幣漲到 bdt 10000 會怎樣", (), ASSET_CLASS_CRYPTO),
    "r15-currency-mixedcase-bdt": ("比特幣漲到 Bdt 10000 會怎樣", (), ASSET_CLASS_CRYPTO),
    "r15-currency-lowercase-ngn": ("比特幣漲到 ngn 10000 會怎樣", (), ASSET_CLASS_CRYPTO),
    "r15-currency-uppercase-bdt": ("比特幣漲到 BDT 10000 會怎樣", (), ASSET_CLASS_CRYPTO),
    "r15-currency-lowercase-top": ("比特幣漲到 top 10000 會怎樣", (), ASSET_CLASS_CRYPTO),
    "r15-currency-lowercase-trailing": ("比特幣漲到 10000 ngn 會怎樣", (), ASSET_CLASS_CRYPTO),
    "r15-currency-supplementary-sign": ("BTC 漲到 \U0001ecb010000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    # X2, false-negative direction: with no number beside it, a code-shaped
    # ticker is still named. Only adjacency reads it as money.
    "r15-code-lookalike-top": ("分析 TOP 過去 14 日市場狀態", ("TOP",), ASSET_CLASS_OPEN),
    "r15-code-lookalike-xau": ("分析 XAU 過去 14 日市場狀態", ("XAU",), ASSET_CLASS_OPEN),
    "r15-code-lookalike-cashtag": ("$TOP 未來如何", ("TOP",), ASSET_CLASS_OPEN),
    # X3 is recorded, not fixed. These are standard buyback and capital-action
    # research questions, and the text reader gets them wrong: the count reads
    # 50000 as a Taiwan listing code and the gateway then rejects the evidence
    # the question actually wanted. Pinned here so the misreading cannot
    # quietly change shape, and so the stated-subject seam has something
    # concrete to be measured against — see StatedSubjectTest.
    "r15-buyback-misread-tsmc": (
        "台積電回購 50000 股是否有利未來股價？", ("50000",), ASSET_CLASS_TW_STOCK,
    ),
    "r15-buyback-misread-code": (
        "2330 回購 50000 股是否有利未來股價？", ("2330", "50000"), ASSET_CLASS_TW_STOCK,
    ),
    "r15-buyback-misread-nvda": (
        "分析 NVDA 回購 50000 股對未來股價的影響", ("NVDA", "50000"), ASSET_CLASS_TW_STOCK,
    ),
    # --- Round 16 -----------------------------------------------------------
    # Z1, false-positive direction: a character that merely *folds* onto ASCII
    # is not the same character, and folding it invents targets the question
    # never mentioned. Length was never what made folding safe.
    "r16-circled-digits": ("選項 ①②③④ 哪個較好？", (), ASSET_CLASS_OPEN),
    "r16-parenthesised-digits": ("⑵⑶⑶⓪ 未來如何", (), ASSET_CLASS_OPEN),
    "r16-circled-letters": ("ⒶⒷⒸ方案會成功嗎", (), ASSET_CLASS_OPEN),
    "r16-roman-numerals": ("第ⅩⅤⅠⅠ章政策對市場的影響", (), ASSET_CLASS_OPEN),
    "r16-roman-numerals-short": ("羅馬數字 ⅠⅤ 的意義？", (), ASSET_CLASS_OPEN),
    "r16-superscript-digits": ("測量值 ²³³⁰ 是否異常？", (), ASSET_CLASS_OPEN),
    "r16-superscript-code": ("⁴⁰⁹⁶ 未來如何", (), ASSET_CLASS_OPEN),
    "r16-subscript-digits": ("₂₃₃₀ 未來如何", (), ASSET_CLASS_OPEN),
    "r16-modifier-letters": ("ᵀᴼᴾ 未來如何", (), ASSET_CLASS_OPEN),
    "r16-math-bold-letters": ("𝐀𝐁𝐂 方案會成功嗎", (), ASSET_CLASS_OPEN),
    # ``\d`` matches every Unicode decimal digit, so the listing-code reader
    # took these for Taiwan codes until the digit classes were made ASCII.
    "r16-math-bold-digits": ("𝟐𝟑𝟑𝟎 未來如何", (), ASSET_CLASS_OPEN),
    "r16-arabic-indic-digits": ("٢٣٣٠ 未來如何", (), ASSET_CLASS_OPEN),
    "r16-devanagari-digits": ("२३३० 未來如何", (), ASSET_CLASS_OPEN),
    # Z1, false-negative direction: a genuine width variant is the same
    # character and still reads. The round-15 cases above must not move.
    "r16-wide-currency-sign": ("BTC 漲到 ＄10000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
    "r16-wide-currency-code": ("BTC 漲到 ＵＳＤ 10000 的機率", ("BTC",), ASSET_CLASS_CRYPTO),
}

# The ids every round settled on, written out rather than derived from the
# table above. An expectation taken from the thing it is checking can only
# ever agree with it: deleting a row would silently shrink both sides.
REQUIRED_CASE_IDS = frozenset({
    "r10-1inch-as-for",
    "r10-1inch-itself",
    "r10-1inch-particle-bei",
    "r10-1inch-particle-ne",
    "r10-1inch-particle-ya",
    "r10-1inch-will-it-rise",
    "r10-1inch-worth-buying",
    "r10-2330-share-price",
    "r10-analysis-asset-of",
    "r10-bear-market",
    "r10-buy-units",
    "r10-crypto-circle-future",
    "r10-crypto-circle-token",
    "r10-definitely-this-coin",
    "r10-doge-this-kind-of-coin",
    "r10-dollar-price",
    "r10-f-etf-performance",
    "r10-f-target-trend",
    "r10-f-that-stock",
    "r10-fund-units",
    "r10-gas-token",
    "r10-growth-stock",
    "r10-iso-currency",
    "r10-lot-quantity",
    "r10-meme-coin",
    "r10-modifier-ai-stock",
    "r10-modifier-high-dividend",
    "r10-modifier-popular-this-year",
    "r10-modifier-semiconductor-growth",
    "r10-modifier-tech-stock",
    "r10-modifier-us-listed",
    "r10-nvda-stock-conservative",
    "r10-nvda-upper-stock",
    "r10-penny-stock",
    "r10-policy-currency-of",
    "r10-policy-lets-stock",
    "r10-price-action",
    "r10-regulation-coin-of",
    "r10-regulation-issue-but-coin",
    "r10-share-quantity",
    "r10-single-letter-comparison",
    "r10-tech-stock",
    "r10-technology-term-but-stock",
    "r10-trade-count",
    "r10-us-dollar-price",
    "r10-value-stock",
    "r10-x-this-thing",
    "r11-buy-shares-quantity",
    "r11-compound-asset-class",
    "r11-compound-asset-management",
    "r11-compound-coin-choice",
    "r11-compound-coin-reform",
    "r11-compound-coin-value",
    "r11-compound-cointegration",
    "r11-compound-currency-system",
    "r11-compound-dividend-policy",
    "r11-compound-equity-structure",
    "r11-compound-investment-strategy",
    "r11-compound-money-supply",
    "r11-compound-payout-policy",
    "r11-compound-shareholder-meeting",
    "r11-compound-stockholder",
    "r11-compound-ticker-code",
    "r11-compound-token-issue-rules",
    "r11-compound-tokenisation",
    "r11-compound-tokenizer",
    "r11-compound-trading-term",
    "r11-cross-clause-coin",
    "r11-cross-clause-compliance",
    "r11-cross-clause-rate-cut",
    "r11-cross-clause-stock",
    "r11-dividend-yield-code",
    "r11-genitive-de-future",
    "r11-genitive-de-trend",
    "r11-genitive-yu-prose",
    "r11-genitive-zhi-prose",
    "r11-genitive-zhi-trend",
    "r11-hold-shares-quantity",
    "r11-payout-code",
    "r11-shareholder-meeting-code",
    "r12-code-company",
    "r12-code-crash",
    "r12-code-registrar",
    "r12-code-share-count",
    "r12-currency-cny",
    "r12-currency-hkd",
    "r12-currency-ntd",
    "r12-mixed-market-comparison",
    "r12-popular-tech-stock",
    "r12-quantity-shareholders",
    "r12-quantity-shareholders-attend",
    "r12-quantity-shareholders-total",
    "r12-scan-brand",
    "r12-scan-business-model",
    "r12-scan-iphone-product",
    "r12-scan-name",
    "r12-scan-number",
    "r12-scan-pronoun",
    "r12-scan-rating",
    "r12-scan-serial",
    "r12-scan-shareholder-meeting",
    "r12-scan-test-concept",
    "r12-scan-unknown",
    "r12-single-letter-pair",
    "r12-value-stock-analysis",
    "r13-cashtag-comparison",
    "r13-cashtag-comparison-mixed",
    "r13-cashtag-leading-digit",
    "r13-cashtag-legacy",
    "r13-cashtag-long",
    "r13-cashtag-lower",
    "r13-cashtag-mixed-case",
    "r13-cashtag-share-class-dash",
    "r13-cashtag-share-class-dot",
    "r13-cashtag-single-letter",
    "r13-cashtag-upper",
    "r13-code-capital",
    "r13-code-equity",
    "r13-coin-price-classifies",
    "r13-currency-aud",
    "r13-currency-cad",
    "r13-currency-chf",
    "r13-currency-code-with-listing",
    "r13-currency-dollar-usd",
    "r13-currency-krw",
    "r13-currency-sgd",
    "r13-currency-spaced-prefix",
    "r13-currency-suffixed",
    "r13-currency-trailing-iso",
    "r13-currency-two-amounts",
    "r13-demonstrative-gone-1inch",
    "r13-demonstrative-gone-compound",
    "r13-demonstrative-gone-f",
    "r13-demonstrative-gone-individual",
    "r13-demonstrative-gone-iphone",
    "r13-dollar-digits-is-money",
    "r13-dollar-digits-numeric-code",
    "r13-money-not-swallowing-aapl",
    "r13-money-not-swallowing-btc",
    "r13-money-not-swallowing-eth",
    "r13-quantity-about",
    "r13-quantity-holds-about",
    "r13-quantity-lots",
    "r13-quantity-more-than",
    "r13-quantity-named-holders",
    "r13-quantity-nearly",
    "r13-quantity-over",
    "r13-quantity-shares-outstanding",
    "r13-quantity-units",
    "r2-2330-headline",
    "r2-btc-lower-bare",
    "r2-btc-market-state",
    "r2-btc-price-action",
    "r2-doge-demonstrative",
    "r2-doge-single-asset",
    "r2-eth-doge-mixed-case",
    "r2-imf-not-a-target",
    "r2-lottery-open",
    "r2-nvda-bare-open",
    "r2-nvda-demonstrative",
    "r2-overall-crypto-market",
    "r2-price-action-btc",
    "r2-sol-event",
    "r2-xrp-btc-comparison",
    "r2-xrp-btc-mixed-case",
    "r3-2330-tw-suffix",
    "r3-brk-dash-b",
    "r3-brk-dot-b",
    "r3-iso-date",
    "r3-minguo-year",
    "r3-partial-date",
    "r3-reverse-date",
    "r3-slash-date",
    "r4-2330-dash-tw",
    "r4-2330-dash-two",
    "r4-2330-slash-2454",
    "r4-2330-space-2454",
    "r4-aapl-hk",
    "r4-brk-b-tw",
    "r4-brk-b-vs-brk-a",
    "r4-btc-dash-eth",
    "r4-btc-slash-eth",
    "r4-btc-tw",
    "r4-btc2",
    "r4-btcusd",
    "r4-tsmc-us-2330-tw",
    "r5-aapl-sandp",
    "r5-ada-coin",
    "r5-ada-crypto",
    "r5-apt-altcoin",
    "r5-brk-b-demonstrative",
    "r5-eth-token",
    "r5-nvda-demonstrative-lower",
    "r5-nvda-shares",
    "r5-nvda-stock",
    "r5-sol-defi",
    "r5-tsla-nyse",
    "r5-usdc-stablecoin",
    "r6-aapl-cointegration",
    "r6-aapl-stockholm",
    "r6-ada-coins-plural",
    "r6-ada-definite",
    "r6-apt-definition",
    "r6-bitcoin",
    "r6-coin-alone",
    "r6-coin-upper-demonstrative",
    "r6-crypto-alone",
    "r6-cryptocurrency",
    "r6-nvda-stocks-plural",
    "r6-shares-alone",
    "r6-stock-alone",
    "r6-stock-upper-demonstrative",
    "r6-token-upper-demonstrative",
    "r6-tokenomics",
    "r7-1000sats",
    "r7-1inch",
    "r7-babydoge",
    "r7-bespoken",
    "r7-betoken",
    "r7-brk-b-us-spelling",
    "r7-btc-timeshares",
    "r7-cny",
    "r7-coin-lower-case",
    "r7-coin-nvda-repeated",
    "r7-coin-repeated",
    "r7-crypto-circle",
    "r7-crypto-market-event",
    "r7-data-encryption",
    "r7-deadstock",
    "r7-doge-livestock",
    "r7-f-etf",
    "r7-f-us-stock",
    "r7-feedstock",
    "r7-laughingstock",
    "r7-livestock",
    "r7-monetary-policy",
    "r7-ntd",
    "r7-overstock",
    "r7-restock",
    "r7-sol-restock",
    "r7-stock-repeated",
    "r7-stockholder",
    "r7-timeshares",
    "r7-token-lower-case",
    "r7-token-mixed-case",
    "r7-token-title-case",
    "r7-tsmc-competitor",
    "r7-twd",
    "r7-unshares",
    "r7-us-index",
    "r7-woodstock",
    "r8-1000sats-btc-comparison",
    "r8-1inch-1000sats-comparison",
    "r8-1inch-eth-comparison",
    "r8-analyze-cryptocurrency",
    "r8-babydoge-comparison",
    "r8-coin-nvda-comparison",
    "r8-coin-shares-comparison",
    "r8-crypto-stock-comparison",
    "r8-digital-currency",
    "r8-digital-currency-simplified",
    "r8-doge-is-virtual-coin",
    "r8-doge-virtual-coin",
    "r8-encrypted-coin",
    "r8-f-t-comparison",
    "r8-hardcoin-coinage",
    "r8-inflationary-stock",
    "r8-long-identifier",
    "r8-price-volume-no-marker",
    "r8-stock-crypto-comparison",
    "r8-taiwan-market",
    "r8-taiwan-market-alt",
    "r8-taiwan-stock",
    "r8-taiwan-stock-alt",
    "r8-technology-stock",
    "r8-token-eth-comparison",
    "r8-token-stock-comparison",
    "r8-unshares-verb",
    "r8-virtual-coin",
    "r9-1inch-crypto-currency",
    "r9-1inch-digital-currency",
    "r9-1inch-virtual-currency",
    "r9-2026-year",
    "r9-acronym-run",
    "r9-cpi",
    "r9-f-asset-noun",
    "r9-f-target-noun",
    "r9-fomc",
    "r9-gdp",
    "r9-policy-monetary",
    "r9-regulation-crypto-issue",
    "r9-sec",
    "r9-taiwan-market-vs-stock",
    "r9-taiwan-stock-and-stock-market",
    "r9-taiwan-stock-vs-stock",
    "r9-technology-stock-market-term",
    "r14-dollar-unreadable-share-underscore",
    "r14-dollar-unreadable-letters-underscore",
    "r14-dollar-unreadable-digit-underscore",
    "r14-dollar-unreadable-slash",
    "r14-dollar-unreadable-code-colon",
    "r14-dollar-unreadable-code-colon-ticker",
    "r14-dollar-unreadable-code-pair",
    "r14-dollar-unreadable-unknown-suffix",
    "r14-dollar-unreadable-unknown-dot-suffix",
    "r14-dollar-unreadable-code-slash",
    "r14-dollar-unreadable-code-underscore",
    "r14-dollar-code-dot-tw",
    "r14-dollar-code-dash-tw",
    "r14-dollar-code-dot-two",
    "r14-dollar-code-dash-two",
    "r14-dollar-code-dot-us",
    "r14-dollar-code-dot-hk",
    "r14-dollar-code-glued-letters",
    "r14-dollar-detached-ticker",
    "r14-dollar-trailing-comma",
    "r14-dollar-amount-suffixed",
    "r14-dollar-spaced-amount",
    "r14-currency-sign-latin1-yen",
    "r14-currency-sign-won",
    "r14-currency-sign-rupee",
    "r14-currency-sign-ruble",
    "r14-currency-sign-baht",
    "r14-currency-code-bdt",
    "r14-currency-code-pkr",
    "r14-currency-code-trailing-ngn",
    "r14-currency-iso-prefixed-sign",
    "r14-money-not-swallowing-non-iso-prefix",
    "r14-money-not-swallowing-nvda",
    "r14-quantity-modifier-gaoda",
    "r14-quantity-modifier-zuzu",
    "r14-quantity-modifier-yuemo",
    "r14-quantity-modifier-dagai",
    "r14-quantity-modifier-jinhu",
    "r14-quantity-modifier-buxia",
    "r14-quantity-modifier-zhengzheng",
    "r14-quantity-verb-then-listing",
    "r14-quantity-verb-then-listing-holdings",
    "r14-quantity-shareholder-equity",
    "r15-dollar-fullwidth-underscore",
    "r15-dollar-fullwidth-underscore-letters",
    "r15-dollar-fullwidth-underscore-digit",
    "r15-dollar-fullwidth-dash-unknown",
    "r15-dollar-fullwidth-slash",
    "r15-dollar-fullwidth-colon",
    "r15-dollar-fullwidth-underscore-code",
    "r15-dollar-fullwidth-marker",
    "r15-dollar-fullwidth-dot-suffix",
    "r15-dollar-fullwidth-dash-suffix",
    "r15-fullwidth-cashtag-ticker",
    "r15-fullwidth-taiwan-code",
    "r15-fullwidth-comparison",
    "r15-currency-lowercase-bdt",
    "r15-currency-mixedcase-bdt",
    "r15-currency-lowercase-ngn",
    "r15-currency-uppercase-bdt",
    "r15-currency-lowercase-top",
    "r15-currency-lowercase-trailing",
    "r15-currency-supplementary-sign",
    "r15-code-lookalike-top",
    "r15-code-lookalike-xau",
    "r15-code-lookalike-cashtag",
    "r15-buyback-misread-tsmc",
    "r15-buyback-misread-code",
    "r15-buyback-misread-nvda",
    "r16-circled-digits",
    "r16-parenthesised-digits",
    "r16-circled-letters",
    "r16-roman-numerals",
    "r16-roman-numerals-short",
    "r16-superscript-digits",
    "r16-superscript-code",
    "r16-subscript-digits",
    "r16-modifier-letters",
    "r16-math-bold-letters",
    "r16-math-bold-digits",
    "r16-arabic-indic-digits",
    "r16-devanagari-digits",
    "r16-wide-currency-sign",
    "r16-wide-currency-code",
})

# Written out rather than derived from the matrix, for the same reason the
# manifest is: a range that follows whatever happens to be there cannot notice
# that a round's rows have gone. Every review round that settled a reading is
# listed here, and a new round adds itself.
SETTLED_ROUNDS = (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16)


class CrossRoundRegressionTest(unittest.TestCase):
    """Every reading a review round settled on, checked in one pass."""

    def test_every_settled_reading_still_holds(self):
        for case_id, (question, assets, asset_class) in sorted(CROSS_ROUND_CASES.items()):
            with self.subTest(case=case_id, question=question):
                scope = inspect_question(question)

                self.assertEqual(assets, scope.assets)
                self.assertEqual(asset_class, scope.asset_class)

    def test_no_settled_case_is_ever_dropped(self):
        """The manifest is a literal, so deleting a row here really does fail."""
        self.assertEqual(REQUIRED_CASE_IDS, frozenset(CROSS_ROUND_CASES))
        self.assertGreaterEqual(len(REQUIRED_CASE_IDS), 380)
        for round_number in SETTLED_ROUNDS:
            prefix = "r{}-".format(round_number)
            with self.subTest(round=round_number):
                self.assertTrue(
                    any(case_id.startswith(prefix) for case_id in CROSS_ROUND_CASES),
                    "第 {} 輪議定的案例全部消失了".format(round_number),
                )

class MarketWordSymmetryTest(unittest.TestCase):
    """One vocabulary, two live consumers: the classifier and the exclusion set."""

    def matcher_for(self, wanted_class):
        for asset_class, matcher in question_module._CLASS_MATCHERS:
            if asset_class == wanted_class:
                return matcher
        raise AssertionError("{} 沒有對應的分類器".format(wanted_class))

    def test_every_word_in_the_table_classifies_its_own_class(self):
        for asset_class, words in MARKET_WORDS_BY_CLASS:
            matcher = self.matcher_for(asset_class)
            for word in words:
                with self.subTest(asset_class=asset_class, word=word):
                    match = matcher.match(word)
                    self.assertTrue(
                        match and match.end() == len(word),
                        "{} 在自己的類別裡分類不到".format(word),
                    )

    def test_every_ascii_market_word_is_barred_from_implicit_promotion(self):
        for _, words in MARKET_WORDS_BY_CLASS:
            for word in words:
                if not (word.isascii() and word.isalpha()):
                    continue
                with self.subTest(word=word):
                    self.assertIn(word.upper(), NON_ASSET_TOKENS)

    def test_the_plural_the_matcher_accepts_is_barred_too(self):
        """比對允許複數 s，排除清單就必須跟著涵蓋複數，否則複數會變成幽靈標的。"""
        for _, words in MARKET_WORDS_BY_CLASS:
            for word in words:
                if not (word.isascii() and word.isalpha()):
                    continue
                with self.subTest(word=word):
                    self.assertIn(word.upper() + "S", NON_ASSET_TOKENS)

    def test_every_ascii_market_word_matches_only_a_whole_word(self):
        """詞表列的是完整詞：允許複數 s，不允許其他延伸。"""
        for asset_class, words in MARKET_WORDS_BY_CLASS:
            matcher = self.matcher_for(asset_class)
            for word in words:
                if not (word.isascii() and word.isalpha()):
                    continue
                with self.subTest(word=word):
                    self.assertTrue(matcher.match(word))
                    self.assertTrue(matcher.match(word + "s"))
                    # 右邊界
                    self.assertIsNone(matcher.search(word + "zq"))
                    self.assertIsNone(matcher.search(word + "1"))
                    # 左邊界
                    self.assertIsNone(matcher.search("zq" + word))
                    self.assertIsNone(matcher.search("1" + word))
                    self.assertIsNone(matcher.search("zq" + word + "zq"))



class LegacyCryptoCompatibilityTest(unittest.TestCase):
    """五幣時代的題目一個都不能掉：alias 只負責認得，從不負責拒收。"""

    def test_bare_lower_case_legacy_coin_is_still_recognised(self):
        for symbol in LEGACY_CRYPTO_SYMBOLS:
            question = "{} 會不會漲".format(symbol.lower())
            with self.subTest(question=question):
                scope = inspect_question(question)

                self.assertEqual((symbol,), scope.assets)
                self.assertEqual(ASSET_CLASS_CRYPTO, scope.asset_class)
                self.assertEqual(symbol.lower(), scope.asset_slug)

    def test_legacy_coin_without_a_market_word_is_still_crypto(self):
        scope = inspect_question("分析 SOL 過去 14 日市場狀態")

        self.assertEqual(("SOL",), scope.assets)
        self.assertEqual(ASSET_CLASS_CRYPTO, scope.asset_class)

    def test_the_alias_set_never_refuses_anything_outside_it(self):
        scope = inspect_question("ARB 會不會漲")

        self.assertEqual(("ARB",), scope.assets)
        self.assertEqual(ASSET_CLASS_OPEN, scope.asset_class)


def asset_spellings(bases=("BRK", "NVDA", "2330", "AAPL")):
    """Every way the same listing gets written: share class × exchange × case."""
    written = set()
    for base in bases:
        for share_class in ("", "B"):
            for share_separator in (".", "-"):
                for exchange in ("", "TW", "TWO", "US", "HK"):
                    for exchange_separator in (".", "-"):
                        text = base
                        if share_class:
                            text += share_separator + share_class
                        if exchange:
                            text += exchange_separator + exchange
                        written.add((base, share_class, text))
                        written.add((base, share_class, text.lower()))
    return sorted(written)


class NormalizeAssetTest(unittest.TestCase):
    """One canonical spelling per target, shared by the slug and the gateway."""

    def test_exchange_suffix_is_dropped(self):
        self.assertEqual("2330", normalize_asset("2330.TW"))
        self.assertEqual("2330", normalize_asset("2330.two"))
        self.assertEqual("NVDA", normalize_asset("nvda.us"))

    def test_share_class_separator_is_unified_and_kept(self):
        self.assertEqual("BRK.B", normalize_asset("BRK.B"))
        self.assertEqual("BRK.B", normalize_asset("brk-b"))

    def test_a_plain_target_is_only_upper_cased(self):
        self.assertEqual("BTC", normalize_asset("btc"))
        self.assertEqual("OVERALL-MARKET", normalize_asset("OVERALL-MARKET"))

    def test_share_class_and_exchange_suffix_combine_in_any_order(self):
        """兩種 suffix 必須各自被辨識，不能靠分隔符先後碰運氣。"""
        for spelling in ("BRK.B", "BRK-B", "BRK.B.US", "BRK-B.US", "brk-b.us", "BRK-B-US"):
            with self.subTest(spelling=spelling):
                self.assertEqual("BRK.B", normalize_asset(spelling))

    def test_every_spelling_of_one_listing_shares_one_canonical_key(self):
        for base, share_class, spelling in asset_spellings():
            expected = "{}.{}".format(base, share_class) if share_class else base
            with self.subTest(spelling=spelling):
                self.assertEqual(expected, normalize_asset(spelling))

    def test_normalization_is_idempotent(self):
        extra = ("X.TW.TW", "BRK.B.US.TW", ".TW", "TW", "OVERALL-MARKET", "", "2330")
        spellings = [item[2] for item in asset_spellings()] + list(extra)
        for spelling in spellings:
            with self.subTest(spelling=spelling):
                once = normalize_asset(spelling)

                self.assertEqual(once, normalize_asset(once))


class AssetSlugTest(unittest.TestCase):
    """The slug becomes a run id segment, so it must stay path safe."""

    def scope(self, assets):
        return QuestionScope(
            question="測試題目",
            assets=assets,
            period_days=DEFAULT_PERIOD_DAYS,
            period_stated=False,
            asset_class=ASSET_CLASS_OPEN,
        )

    def test_slug_joins_assets_in_order_and_lowercases(self):
        self.assertEqual("2330-nvda", self.scope(("2330", "NVDA")).asset_slug)

    def test_slug_falls_back_to_overall_market_without_assets(self):
        self.assertEqual(OVERALL_MARKET_SLUG, self.scope(()).asset_slug)

    def test_slug_reduces_any_other_character_to_a_single_dash(self):
        self.assertEqual("brk-b", self.scope(("BRK.B",)).asset_slug)
        self.assertEqual("a-b", self.scope(("a/b",)).asset_slug)

    def test_slug_drops_a_known_exchange_suffix(self):
        self.assertEqual("2330", asset_slug_for(("2330.TW",)))
        self.assertEqual("2330", self.scope(("2330.TW",)).asset_slug)


class OpenModeIntakeTest(unittest.TestCase):
    """The open path is the normal path now; nothing about it is a refusal."""

    QUESTION = "若 SEC 通過 BTC 現貨 ETF，市場會如何反應？"

    def test_unknown_asset_error_is_an_unsupported_question_error(self):
        self.assertTrue(issubclass(UnknownAssetError, UnsupportedQuestionError))

    def test_strict_intake_accepts_what_it_used_to_refuse(self):
        scope = inspect_question(self.QUESTION)

        self.assertEqual(("BTC",), scope.assets)
        self.assertEqual(self.QUESTION, scope.question)

    def test_allow_unknown_assets_reads_the_question_the_same_way(self):
        self.assertEqual(
            inspect_question(self.QUESTION),
            inspect_question(self.QUESTION, allow_unknown_assets=True),
        )

    def test_open_mode_never_invents_an_asset_when_none_is_named(self):
        scope = inspect_question("聯準會九月會不會降息", allow_unknown_assets=True)

        self.assertEqual((), scope.assets)
        self.assertEqual(ASSET_CLASS_OPEN, scope.asset_class)

    def test_open_mode_still_fails_closed_on_an_unparseable_period(self):
        with self.assertRaises(UnsupportedQuestionError):
            inspect_question("BTC 過去幾週 ETF 資金？", allow_unknown_assets=True)

    def test_bracketed_asset_is_recognised(self):
        scope = inspect_question("【BTC】ETF 通過後會怎樣？")

        self.assertEqual(("BTC",), scope.assets)


class WidthFoldTest(unittest.TestCase):
    """A width variant is the same character. Nothing else is folded."""

    def test_only_width_variants_are_folded(self):
        """The tag decides, and the tag is Unicode's own metadata."""
        for code_point in question_module._WIDTH_FOLD:
            tag = unicodedata.decomposition(chr(code_point)).split(" ")[0]
            with self.subTest(code_point=hex(code_point)):
                self.assertIn(tag, ("<wide>", "<narrow>"))

    def test_a_lookalike_that_is_a_different_character_is_never_folded(self):
        """Length was never what made folding safe — sameness of character was.

        Every one of these folds onto ASCII under a plain NFKC, and every one
        of them would invent a target the question never mentioned.

        The self-check is per character of the *token*, not per sentence. A
        whole-sentence ``assertNotEqual(question, NFKC(question))`` would be
        satisfied by the full-width ``？`` at the end, so a probe whose token
        had stopped exercising anything would still have looked alive.
        """
        for question, token, tag in (
            ("選項 ①②③④ 哪個較好？", "①②③④", "<circle>"),
            ("ⒶⒷⒸ方案會成功嗎", "ⒶⒷⒸ", "<circle>"),
            ("第ⅩⅤⅠⅠ章政策對市場的影響", "ⅩⅤⅠⅠ", "<compat>"),
            ("羅馬數字 ⅠⅤ 的意義？", "ⅠⅤ", "<compat>"),
            ("測量值 ²³³⁰ 是否異常？", "²³³⁰", "<super>"),
            ("⁴⁰⁹⁶ 未來如何", "⁴⁰⁹⁶", "<super>"),
            ("₂₃₃₀ 未來如何", "₂₃₃₀", "<sub>"),
            ("ᵀᴼᴾ 未來如何", "ᵀᴼᴾ", "<super>"),
            ("𝐀𝐁𝐂 方案會成功嗎", "𝐀𝐁𝐂", "<font>"),
            ("𝟐𝟑𝟑𝟎 未來如何", "𝟐𝟑𝟑𝟎", "<font>"),
        ):
            with self.subTest(question=question, tag=tag):
                self.assertIn(token, question)
                for character in token:
                    self.assertEqual(
                        tag,
                        unicodedata.decomposition(character).split(" ")[0],
                        "{!r} no longer carries {}".format(character, tag),
                    )
                    self.assertTrue(
                        unicodedata.normalize("NFKC", character).isascii(),
                        "{!r} no longer folds onto ASCII".format(character),
                    )
                    self.assertNotIn(ord(character), question_module._WIDTH_FOLD)

                self.assertEqual((), inspect_question(question).assets)

    def test_a_digit_from_another_script_is_not_a_listing_code(self):
        """The guard here is the ASCII digit class, not the fold.

        These carry no decomposition at all, so no folding rule would ever have
        touched them. What let them through was ``\\d``, which matches every
        Unicode decimal digit — so the self-check is that ``\\d`` still would.
        """
        for question, token in (
            ("٢٣٣٠ 未來如何", "٢٣٣٠"),
            ("२३३० 未來如何", "२३३०"),
            ("𝟐𝟑𝟑𝟎 未來如何", "𝟐𝟑𝟑𝟎"),
        ):
            with self.subTest(question=question):
                for character in token:
                    self.assertRegex(character, r"\d")
                    self.assertNotRegex(character, r"[0-9]")

                self.assertEqual((), inspect_question(question).assets)

    def test_folding_never_moves_an_offset(self):
        """Every claimed span indexes the question as written, so this must hold."""
        for code_point, folded in question_module._WIDTH_FOLD.items():
            self.assertEqual(1, len(folded), hex(code_point))
        sample = "".join(map(chr, question_module._WIDTH_FOLD))
        self.assertEqual(
            len(sample), len(question_module._fold_width_variants(sample))
        )

    def test_a_width_variant_reads_like_its_ascii_form(self):
        """Stated over the whole fold table, not over the characters I guessed.

        Every width variant of a character a ``$`` token can contain is
        substituted into every probe, and the reading may not change. That is
        what makes 「全形」 a non-issue rather than six characters somebody
        remembered.
        """
        token_characters = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._,:/-$"
        )
        equivalents = {}
        for code_point, folded in question_module._WIDTH_FOLD.items():
            if folded in token_characters:
                equivalents.setdefault(folded, []).append(chr(code_point))
        self.assertEqual(len(token_characters), len(equivalents))

        bodies = (
            "BRK_B", "1_INCH", "2330:AAPL", "2330-ABC", "2330/TW",
            "2330.TW", "2330-TW", "BRK.B", "1INCH", "NVDA.US", "10000",
        )
        for body in bodies:
            baseline = inspect_question("${} 未來如何".format(body))
            for index, character in enumerate(body):
                for substitute in equivalents.get(character, ()):
                    variant = body[:index] + substitute + body[index + 1:]
                    with self.subTest(body=body, variant=variant):
                        scope = inspect_question("${} 未來如何".format(variant))

                        self.assertEqual(baseline.assets, scope.assets)
                        self.assertEqual(baseline.asset_class, scope.asset_class)


class StatedSubjectTest(unittest.TestCase):
    """When the caller states the subject, the text has no say in it.

    A menu knows which row was clicked; a parser only guesses. Where the answer
    is given, guessing is not an improvement on it.
    """

    BUYBACK = "台積電回購 50000 股後股價會不會上漲？"

    def test_stated_assets_take_over_from_the_text(self):
        """The X3 misreading cannot happen on this path — that is the point."""
        self.assertEqual(("50000",), inspect_question(self.BUYBACK).assets)

        scope = inspect_question(self.BUYBACK, assets=("2330",))

        self.assertEqual(("2330",), scope.assets)
        self.assertEqual(ASSET_CLASS_TW_STOCK, scope.asset_class)

    def test_stated_assets_are_neither_extended_nor_dropped(self):
        for question, stated in (
            ("2330 回購 50000 股是否有利未來股價？", ("2330",)),
            ("分析 NVDA 回購 50000 股對未來股價的影響", ("NVDA",)),
            ("比較 $F 與 NVDA", ("AAPL",)),
            ("AI 泡沫會不會在今年破掉", ("2330", "NVDA")),
            ("BTC 未來七天會不會漲", ()),
        ):
            with self.subTest(question=question):
                self.assertEqual(stated, inspect_question(question, assets=stated).assets)

    def test_a_stated_asset_is_still_normalised(self):
        """One canonical spelling is what the rest of the system stores."""
        scope = inspect_question("任意題目", assets=("brk-b.us", "2330.TW"))

        self.assertEqual(("BRK.B", "2330"), scope.assets)

    def test_stated_asset_class_takes_over_from_the_text(self):
        """This is the answer to a bare symbol: the menu knows the market."""
        self.assertEqual(
            ASSET_CLASS_OPEN, inspect_question("分析 DOGE 過去 14 日市場狀態").asset_class
        )

        scope = inspect_question(
            "分析 DOGE 過去 14 日市場狀態", assets=("DOGE",), asset_class=ASSET_CLASS_CRYPTO
        )

        self.assertEqual(("DOGE",), scope.assets)
        self.assertEqual(ASSET_CLASS_CRYPTO, scope.asset_class)

    def test_stating_nothing_reads_exactly_as_before(self):
        """The whole settled matrix, re-read through the new parameters."""
        for case_id, (question, assets, asset_class) in CROSS_ROUND_CASES.items():
            with self.subTest(case=case_id):
                scope = inspect_question(question, assets=None, asset_class=None)

                self.assertEqual(assets, scope.assets)
                self.assertEqual(asset_class, scope.asset_class)

    def test_a_stated_subject_that_cannot_describe_a_run_fails_closed(self):
        for stated in (
            ("",), ("   ",), ("../etc/passwd",), ("a/b",), ("a\\b",), ("..",),
            ("2330 2454",), ("NVDA;rm",), (None,), (2330,),
        ):
            with self.subTest(stated=stated):
                with self.assertRaises(UnknownAssetError):
                    inspect_question("任意題目", assets=stated)

    def test_only_an_ordered_rereadable_container_is_accepted(self):
        """A set has no order, and the run id is built in the order given.

        The failure this prevents is not untidiness: with a set, the same call
        produces ``…-aapl-nvda-…`` in one process and ``…-nvda-aapl-…`` in the
        next, so one input names two different run directories.
        """
        for container in (
            {"NVDA", "AAPL", "BTC", "ETH"},
            {"NVDA": 1, "AAPL": 2},
            (asset for asset in ("NVDA", "AAPL")),
            "2330",
            2330,
            3.5,
            object(),
        ):
            with self.subTest(container=type(container).__name__):
                with self.assertRaises(UnknownAssetError):
                    inspect_question("任意題目", assets=container)

        for container in (["NVDA", "AAPL"], ("NVDA", "AAPL"), [], ()):
            with self.subTest(container=repr(container)):
                inspect_question("任意題目", assets=container)

    def test_the_same_stated_input_always_names_the_same_run(self):
        """Determinism is the whole point of the container rule.

        Looping in one process would prove nothing — a ``set`` iterates the
        same way all through a single process, and only differs between them.
        What actually makes the run id deterministic is that the order given is
        the order used, so that is what is asserted: the same targets in a
        different order are a different run, and the same order is always the
        same run.
        """
        self.assertEqual(
            "nvda-aapl-2330-brk-b",
            inspect_question("任意題目", assets=["NVDA", "AAPL", "2330", "BRK.B"]).asset_slug,
        )
        self.assertEqual(
            "aapl-nvda",
            inspect_question("任意題目", assets=["AAPL", "NVDA"]).asset_slug,
        )
        self.assertEqual(
            "nvda-aapl",
            inspect_question("任意題目", assets=["NVDA", "AAPL"]).asset_slug,
        )
        self.assertEqual(
            inspect_question("任意題目", assets=("NVDA", "AAPL")).asset_slug,
            inspect_question("任意題目", assets=["NVDA", "AAPL"]).asset_slug,
        )

    def test_a_container_subclass_cannot_change_the_order_underneath(self):
        """The order guarantee is taken from the base type, not from ``__iter__``.

        A subclass may override iteration, and then the same object hands back
        a different order on each read — which is the very thing the container
        rule exists to stop. Reading through ``list.__iter__``/``tuple.__iter__``
        and freezing the result keeps the guarantee without excluding
        subclasses that behave: a ``namedtuple`` is an ordered, re-readable
        tuple and goes through untouched.
        """
        class FlippingList(list):
            reads = 0

            def __iter__(self):
                FlippingList.reads += 1
                items = list.__iter__(self)
                return iter(list(items)[::-1] if FlippingList.reads % 2 else list(items))

        flipping = FlippingList(["NVDA", "AAPL"])
        slugs = {
            inspect_question("任意題目", assets=flipping).asset_slug for _ in range(16)
        }

        self.assertEqual({"nvda-aapl"}, slugs)
        self.assertGreater(
            len(set(tuple(iter(flipping)) for _ in range(4))),
            1,
            "the probe no longer flips, so it proves nothing",
        )

        Pair = collections.namedtuple("Pair", "first second")
        self.assertEqual(
            ("NVDA", "AAPL"),
            inspect_question("任意題目", assets=Pair("NVDA", "AAPL")).assets,
        )

    def test_an_illegal_target_is_refused_before_normalisation_can_clean_it(self):
        """Unicode upper-casing rewrites; it does not merely relabel.

        Each of these is refused as written. Validating after
        ``normalize_asset`` would have seen only the clean ASCII result and
        bound the run to a target the caller never typed.
        """
        rewritten = {
            "ß": "SS", "ı": "I", "ſ": "S", "ﬀ": "FF", "ﬁ": "FI",
            "ﬂ": "FL", "ﬃ": "FFI", "ﬄ": "FFL", "ﬅ": "ST", "ﬆ": "ST",
        }
        for written, would_become in rewritten.items():
            with self.subTest(written=written):
                self.assertEqual(
                    would_become,
                    written.upper(),
                    "{!r} no longer rewrites, so it proves nothing".format(written),
                )
                with self.assertRaises(UnknownAssetError):
                    inspect_question("任意題目", assets=(written,))

    def test_a_legal_spelling_is_still_normalised_after_the_raw_check(self):
        """The raw check must not cost the lower-case and suffix spellings."""
        for written, expected in (
            ("brk-b", ("BRK.B",)), ("BRK-B", ("BRK.B",)), ("brk.b", ("BRK.B",)),
            ("brk-b.us", ("BRK.B",)), ("BRK.B-US", ("BRK.B",)),
            ("2330", ("2330",)), ("2330.TW", ("2330",)), ("2330.tw", ("2330",)),
            ("2330-two", ("2330",)), ("nvda", ("NVDA",)), ("1inch", ("1INCH",)),
            ("f", ("F",)), ("doge", ("DOGE",)), ("aapl.us", ("AAPL",)),
        ):
            with self.subTest(written=written):
                self.assertEqual(
                    expected, inspect_question("任意題目", assets=(written,)).assets
                )

    def test_one_target_written_twice_fails_closed(self):
        """Two spellings of one target is not a two-asset comparison."""
        for stated in (("NVDA", "nvda"), ("BTC", "BTC"), ("2330", "2330.TW")):
            with self.subTest(stated=stated):
                with self.assertRaises(UnknownAssetError):
                    inspect_question("任意題目", assets=stated)

    def test_targets_that_cannot_fit_a_directory_name_fail_closed(self):
        """The limit is the filesystem's, and it is applied before the run id."""
        budget = question_module.MAX_ASSET_SLUG_BYTES
        for stated in (
            ("A" * 10000,),
            ("A" * (budget + 1),),
            tuple("AB{:02d}".format(index) for index in range(100)),
        ):
            with self.subTest(count=len(stated), width=len(stated[0])):
                with self.assertRaises(UnknownAssetError):
                    inspect_question("任意題目", assets=stated)

        scope = inspect_question("任意題目", assets=("A" * budget,))
        self.assertEqual(budget, len(scope.asset_slug.encode("utf-8")))

    def test_a_single_string_is_not_a_target_list(self):
        with self.assertRaises(UnknownAssetError):
            inspect_question("任意題目", assets="2330")

    def test_a_stated_class_that_does_not_exist_fails_closed(self):
        with self.assertRaises(UnsupportedQuestionError):
            inspect_question("任意題目", asset_class="tw-stock")


if __name__ == "__main__":
    unittest.main()
