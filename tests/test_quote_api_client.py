"""Ticket 12: the one place a public quote API is called, and how it refuses.

**No test in this file opens a socket**, and there are two different reasons for
that rather than one. :func:`setUpModule` replaces
:func:`urllib.request.urlopen` for the length of this file with one that records
the URL and raises. The ordinary tests then drive the module through its public
functions with an *injected* opener and never go near the default at all.

The two tests in :class:`NoDefaultNetworkInATestTest` that name the guard —
:meth:`~NoDefaultNetworkInATestTest.
test_the_guard_this_module_installs_is_actually_in_place` and
:meth:`~NoDefaultNetworkInATestTest.test_the_guard_counts_what_it_stopped` —
**deliberately inject nothing**. They call with no opener, so they take the
default ``urlopen`` path on purpose, and they assert that the call was stopped
and counted. That is the point of them: a file that always injected an opener
could not tell an installed guard from a guard that was never installed or was
quietly restored. The guard is this file's own, it travels with it, and it says
nothing about how any other test module is run.

The theme running through the whole file is the ticket's fourth recurring
defect: a quote that could not be fetched, parsed or believed must never come
back as a number. Every refusal below asserts that a
:class:`QuoteUnavailableError` was raised *and* names what could not be used, so
"I do not know" cannot quietly become a confident wrong price.
"""

import ast
import codecs
import http.client
import io
import os
import shutil
import sys
import tempfile
import unittest
import urllib.request
import zoneinfo
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import hoya_market_agents
from hoya_market_agents.question import (
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_OPEN,
    ASSET_CLASS_TW_STOCK,
    ASSET_CLASS_US_STOCK,
    ASSET_CLASSES,
)
from hoya_market_agents.quote_api_client import (
    LOOKBACK_DAYS,
    MARKET_SESSIONS,
    MAX_RESPONSE_BYTES,
    QUOTE_SOURCES,
    MarketSession,
    Quote,
    QuoteUnavailableError,
    _parse_close,
    available_close_day,
    daily_close,
    is_decimal_numeral,
    is_quotable,
    is_usable_price,
    quotable_asset_classes,
)

CSV_HEADER = "Date,Open,High,Low,Close,Volume"

# What the guard says when it stops a call, and every URL it stopped. The list
# is what lets a test prove the guard was reached rather than merely installed.
GUARD_MESSAGE = "測試不得連外"
BLOCKED_URLS = []

_REAL_URLOPEN = None


def _refuse_outbound(url, *_args, **_options):
    """Stand in for ``urlopen`` and refuse, loudly, naming what was asked for."""
    BLOCKED_URLS.append(str(url))
    raise OSError("{}：{}".format(GUARD_MESSAGE, url))


def setUpModule():
    """Install the guard this file's docstring claims, for this file only."""
    global _REAL_URLOPEN
    _REAL_URLOPEN = urllib.request.urlopen
    urllib.request.urlopen = _refuse_outbound


def tearDownModule():
    """Put the real opener back, so no other module inherits this one's guard."""
    urllib.request.urlopen = _REAL_URLOPEN


def csv_body(rows, header=CSV_HEADER):
    return "\n".join([header] + list(rows)) + "\n"


class FakeResponse:
    """Just enough of what ``urlopen`` returns for the client to read it."""

    def __init__(self, payload, status=200):
        self._stream = io.BytesIO(
            payload if isinstance(payload, bytes) else payload.encode("utf-8")
        )
        self.status = status

    def read(self, size=-1):
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        return False


class RecordingOpener:
    """An opener that answers with canned text and remembers what was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.urls = []
        self.timeouts = []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        self.timeouts.append(timeout)
        answer = self.answers.pop(0) if self.answers else ""
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)


def opener_for(*answers):
    return RecordingOpener(*answers)


# -- which classes have a source --------------------------------------------


class SourceCoverageTest(unittest.TestCase):
    """The set of quotable classes is derived from the asset-class authority.

    A hand-written list of tickers or classes is the ticket's first recurring
    defect. Nothing here enumerates a set of its own: the classes with a source
    are pinned to ``question.ASSET_CLASSES`` minus the one class that names a
    proposition rather than a market, so adding a class upstream without giving
    it a source fails here rather than silently becoming unverifiable for ever.
    """

    def test_every_asset_class_except_the_open_one_has_a_source(self):
        self.assertEqual(
            set(ASSET_CLASSES) - {ASSET_CLASS_OPEN}, set(QUOTE_SOURCES)
        )

    def test_the_open_class_has_no_source_and_is_not_quotable(self):
        self.assertNotIn(ASSET_CLASS_OPEN, QUOTE_SOURCES)
        self.assertFalse(is_quotable(ASSET_CLASS_OPEN))

    def test_the_three_market_classes_are_quotable(self):
        for asset_class in (
            ASSET_CLASS_CRYPTO,
            ASSET_CLASS_TW_STOCK,
            ASSET_CLASS_US_STOCK,
        ):
            self.assertTrue(is_quotable(asset_class), asset_class)

    def test_quotable_classes_and_the_source_table_are_the_same_set(self):
        self.assertEqual(set(QUOTE_SOURCES), set(quotable_asset_classes()))

    def test_a_class_nobody_declared_is_not_quotable(self):
        self.assertFalse(is_quotable("commodities"))
        self.assertFalse(is_quotable(None))

    def test_each_source_carries_the_class_it_is_filed_under(self):
        for asset_class, source in QUOTE_SOURCES.items():
            self.assertEqual(asset_class, source.asset_class)

    def test_no_source_is_left_without_a_label_a_page_can_show(self):
        for asset_class, source in QUOTE_SOURCES.items():
            self.assertTrue(source.label.strip(), asset_class)
            self.assertTrue(source.source_id.strip(), asset_class)

    def test_each_class_asks_for_a_different_symbol_spelling(self):
        suffixes = [source.symbol_suffix for source in QUOTE_SOURCES.values()]
        self.assertEqual(len(suffixes), len(set(suffixes)))


# -- the happy path ----------------------------------------------------------


class DailyCloseTest(unittest.TestCase):
    """One close price, and everything the outcome record has to carry."""

    def test_the_close_column_is_read_by_name_not_by_position(self):
        opener = opener_for(
            csv_body(
                ["2026-08-05,1,2,3,111.5,9"],
                header="Date,Open,High,Low,Close,Volume",
            )
        )

        quote = daily_close(
            ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=opener
        )

        self.assertIsInstance(quote, Quote)
        self.assertEqual(111.5, quote.close)

    def test_a_reordered_header_still_finds_the_close(self):
        """Position would be wrong here; the name is not."""
        opener = opener_for(
            csv_body(
                ["2026-08-05,9,222.25,1,2,3"],
                header="Date,Volume,Close,Open,High,Low",
            )
        )

        quote = daily_close(
            ASSET_CLASS_US_STOCK, "AAPL", date(2026, 8, 5), opener=opener
        )

        self.assertEqual(222.25, quote.close)

    def test_the_quote_carries_its_class_symbol_day_source_and_url(self):
        opener = opener_for(csv_body(["2026-08-05,1,2,3,50.0,9"]))

        quote = daily_close(
            ASSET_CLASS_TW_STOCK, "2330", date(2026, 8, 5), opener=opener
        )

        self.assertEqual(ASSET_CLASS_TW_STOCK, quote.asset_class)
        self.assertEqual("2330", quote.symbol)
        self.assertEqual("2026-08-05", quote.day)
        self.assertEqual(QUOTE_SOURCES[ASSET_CLASS_TW_STOCK].source_id, quote.source)
        self.assertEqual(opener.urls[0], quote.url)

    def test_the_summary_quotes_the_response_rather_than_describing_it(self):
        opener = opener_for(csv_body(["2026-08-05,1,2,3,50.0,9"]))

        quote = daily_close(
            ASSET_CLASS_TW_STOCK, "2330", date(2026, 8, 5), opener=opener
        )

        self.assertIn(CSV_HEADER, quote.summary)
        self.assertIn("2026-08-05,1,2,3,50.0,9", quote.summary)

    def test_the_summary_is_bounded_so_one_answer_cannot_fill_a_record(self):
        long_rows = ["2026-07-{:02d},1,2,3,{}.0,9".format(day, day) for day in range(1, 29)]
        opener = opener_for(csv_body(long_rows + ["2026-08-05,1,2,3,50.0,9"]))

        quote = daily_close(
            ASSET_CLASS_TW_STOCK, "2330", date(2026, 8, 5), opener=opener
        )

        self.assertLessEqual(len(quote.summary), Quote.MAX_SUMMARY_CHARS)

    def test_the_symbol_is_spelled_the_way_its_own_class_asks_for(self):
        for asset_class, symbol, expected in (
            (ASSET_CLASS_CRYPTO, "BTC", "btcusd"),
            (ASSET_CLASS_TW_STOCK, "2330", "2330.tw"),
            (ASSET_CLASS_US_STOCK, "AAPL", "aapl.us"),
        ):
            opener = opener_for(csv_body(["2026-08-05,1,2,3,7.0,9"]))
            daily_close(asset_class, symbol, date(2026, 8, 5), opener=opener)
            self.assertIn("s={}".format(expected), opener.urls[0], asset_class)

    def test_the_request_asks_for_a_window_that_ends_on_the_day_wanted(self):
        opener = opener_for(csv_body(["2026-08-05,1,2,3,7.0,9"]))

        daily_close(ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=opener)

        self.assertIn("d2=20260805", opener.urls[0])
        self.assertIn("d1=20260726", opener.urls[0])

    def test_the_window_reaches_back_exactly_the_declared_number_of_days(self):
        self.assertEqual(10, LOOKBACK_DAYS)

    def test_a_timeout_is_always_passed_to_the_opener(self):
        opener = opener_for(csv_body(["2026-08-05,1,2,3,7.0,9"]))

        daily_close(ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=opener)

        self.assertIsNotNone(opener.timeouts[0])
        self.assertGreater(opener.timeouts[0], 0)


class ClosedMarketTest(unittest.TestCase):
    """A market that was shut on the day asked about still has a last close."""

    def test_the_latest_row_on_or_before_the_day_is_the_one_used(self):
        opener = opener_for(
            csv_body(
                [
                    "2026-07-31,1,2,3,10.0,9",
                    "2026-08-03,1,2,3,20.0,9",
                    "2026-08-05,1,2,3,30.0,9",
                ]
            )
        )

        quote = daily_close(
            ASSET_CLASS_US_STOCK, "AAPL", date(2026, 8, 4), opener=opener
        )

        self.assertEqual(20.0, quote.close)
        self.assertEqual("2026-08-03", quote.priced_on)

    def test_the_day_asked_for_is_kept_beside_the_day_priced(self):
        opener = opener_for(csv_body(["2026-08-03,1,2,3,20.0,9"]))

        quote = daily_close(
            ASSET_CLASS_US_STOCK, "AAPL", date(2026, 8, 4), opener=opener
        )

        self.assertEqual("2026-08-04", quote.day)
        self.assertEqual("2026-08-03", quote.priced_on)

    def test_rows_after_the_day_wanted_are_never_used(self):
        """A price from the future is not a late answer; it is a wrong one."""
        opener = opener_for(csv_body(["2026-08-09,1,2,3,99.0,9"]))

        with self.assertRaises(QuoteUnavailableError) as raised:
            daily_close(ASSET_CLASS_US_STOCK, "AAPL", date(2026, 8, 4), opener=opener)

        self.assertIn("2026-08-04", str(raised.exception))


# -- everything that must not become a number -------------------------------


class RefusalTest(unittest.TestCase):
    """Every way this can go wrong, and the sentence it goes wrong with."""

    def refuse(self, payload, asset_class=ASSET_CLASS_CRYPTO, symbol="BTC"):
        opener = opener_for(payload)
        with self.assertRaises(QuoteUnavailableError) as raised:
            daily_close(asset_class, symbol, date(2026, 8, 5), opener=opener)
        return str(raised.exception)

    def test_a_class_with_no_source_is_refused_before_anything_is_opened(self):
        opener = opener_for(csv_body(["2026-08-05,1,2,3,7.0,9"]))

        with self.assertRaises(QuoteUnavailableError) as raised:
            daily_close(ASSET_CLASS_OPEN, "任何", date(2026, 8, 5), opener=opener)

        self.assertEqual([], opener.urls)
        self.assertIn(ASSET_CLASS_OPEN, str(raised.exception))

    def test_a_blank_symbol_is_refused_before_anything_is_opened(self):
        opener = opener_for(csv_body(["2026-08-05,1,2,3,7.0,9"]))

        with self.assertRaises(QuoteUnavailableError):
            daily_close(ASSET_CLASS_CRYPTO, "   ", date(2026, 8, 5), opener=opener)

        self.assertEqual([], opener.urls)

    def test_a_transport_failure_is_reported_rather_than_guessed_around(self):
        opener = opener_for(OSError("connection reset"))

        with self.assertRaises(QuoteUnavailableError) as raised:
            daily_close(ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=opener)

        self.assertIn("connection reset", str(raised.exception))

    def test_an_empty_body_is_refused(self):
        self.assertIn("沒有", self.refuse(""))

    def test_a_header_without_a_close_column_is_refused(self):
        message = self.refuse(csv_body(["2026-08-05,1,2,3"], header="Date,Open,High,Low"))

        self.assertIn("Close", message)

    def test_a_header_without_a_date_column_is_refused(self):
        message = self.refuse(
            csv_body(["1,2,3,7.0,9"], header="Open,High,Low,Close,Volume")
        )

        self.assertIn("Date", message)

    def test_a_body_that_is_not_the_expected_table_is_refused(self):
        message = self.refuse("<html><body>Service Unavailable</body></html>")

        self.assertTrue(message.strip())

    def test_a_header_with_no_rows_under_it_is_refused(self):
        message = self.refuse(csv_body([]))

        self.assertIn("沒有", message)

    def test_a_close_that_is_not_a_number_is_refused(self):
        message = self.refuse(csv_body(["2026-08-05,1,2,3,N/A,9"]))

        self.assertIn("N/A", message)

    def test_a_blank_close_is_refused_rather_than_read_as_zero(self):
        message = self.refuse(csv_body(["2026-08-05,1,2,3,,9"]))

        self.assertTrue(message.strip())

    def test_a_zero_close_is_refused_because_no_market_prints_one(self):
        message = self.refuse(csv_body(["2026-08-05,1,2,3,0,9"]))

        self.assertIn("0", message)

    def test_a_negative_close_is_refused(self):
        self.refuse(csv_body(["2026-08-05,1,2,3,-5,9"]))

    def test_an_infinite_close_is_refused(self):
        """``float`` reads all of these, and every one passes a ``> 0`` test.

        An infinite close compares greater than every real price, so a single
        one would settle its run's direction outright — confidently, and on a
        number no market printed. ``nan`` fails ``> 0`` already; it is listed
        because the rule that covers it is the same one, and a rule that only
        happened to cover it is a rule nobody stated.
        """
        for spelling in ("inf", "Infinity", "INF", "-inf", "-Infinity", "nan", "1e400"):
            with self.subTest(spelling):
                message = self.refuse(
                    csv_body(["2026-08-05,1,2,3,{},9".format(spelling)])
                )

                self.assertTrue(message.strip())

    def test_an_ordinary_close_is_still_accepted(self):
        """FP direction: the finiteness test must not refuse real prices."""
        for spelling, expected in (("7.0", 7.0), ("1e-8", 1e-8), ("1e308", 1e308)):
            with self.subTest(spelling):
                opener = opener_for(
                    csv_body(["2026-08-05,1,2,3,{},9".format(spelling)])
                )

                quote = daily_close(
                    ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=opener
                )

                self.assertEqual(expected, quote.close)

    def test_a_row_whose_date_will_not_parse_is_refused(self):
        message = self.refuse(csv_body(["not-a-date,1,2,3,7.0,9"]))

        self.assertIn("not-a-date", message)

    def test_a_body_that_is_not_utf8_is_refused_rather_than_mangled(self):
        opener = opener_for(b"\xff\xfe\x00\x00 not text")

        with self.assertRaises(QuoteUnavailableError):
            daily_close(ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=opener)

    def test_an_oversized_body_is_refused_rather_than_read_whole(self):
        opener = opener_for("x" * (MAX_RESPONSE_BYTES + 10))

        with self.assertRaises(QuoteUnavailableError):
            daily_close(ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=opener)

    def test_the_refusal_names_the_symbol_and_the_source(self):
        message = self.refuse(csv_body([]), asset_class=ASSET_CLASS_TW_STOCK, symbol="2330")

        self.assertIn("2330", message)
        self.assertIn(QUOTE_SOURCES[ASSET_CLASS_TW_STOCK].source_id, message)


class EveryTransportFailureIsTheOneExceptionTest(unittest.TestCase):
    """``daily_close`` 說「transport 與 answer 兩側的所有 ``Exception`` 都會變成
    ``QuoteUnavailableError``」，那就必須是。

    **這個開頭是這一輪收窄過的。** 先前寫的是「所有失敗」，而那已經不是這個模組現在
    宣稱的東西：本輪明確把**呼叫端自己的引數**留在保證外面。``asset_class`` 與
    ``day`` 是呼叫端挑的物件，模組會去問它們 ``__hash__``、``__eq__``、``__repr__``
    與 ``strftime``，遞進來的東西不友善時出去的是那個物件的例外，不是
    ``QuoteUnavailableError``；那條邊界寫在 :func:`daily_close` 的 docstring 裡，
    ``ASymbolIsACallersObjectTooTest`` 的結尾也指著同一段。這個類別測的是另一半，
    也就是宣稱真的涵蓋的那一半：opener、response、``read`` 回來的東西與它的解碼。

    先前 ``_fetch`` 攔的是一份清單：``OSError``、``urllib.error.URLError``（本身就
    是 ``OSError``）、``ValueError``。:class:`http.client.HTTPException` 直接繼承
    :class:`Exception`，清單上一個都對不上，所以連線在傳到一半被切掉時
    （:class:`http.client.IncompleteRead`）那個例外原樣穿過 ``daily_close``，打
    到一個被告知「只有一種例外要處理」的呼叫端身上。

    往清單加一項會關掉這一個，形狀原封不動——本專案一路被咬的就是這個形狀。所以
    改成用**保證**寫：那個 ``with`` 區塊整段都是別人的程式（呼叫端注入的 opener、
    response 的 context manager 與 ``read``），它會丟什麼是開放集合，而這個函式
    回什麼是封閉集合。攔 :class:`Exception`，訊息帶上型別與內容。

    :class:`BaseException` 不攔：``KeyboardInterrupt`` 與 ``SystemExit`` 是有人要
    停掉這支程式，不是「查不到價格」。最後一條測的就是那個邊界。
    """

    class CutMidBody:
        """A response that opens fine and dies inside ``read`` — Reviewer A 的路徑."""

        def __init__(self, failure):
            self.failure = failure

        def __enter__(self):
            return self

        def __exit__(self, *_exception):
            return False

        def read(self, _size=-1):
            raise self.failure

    def opener_that_dies_in_read(self, failure):
        def opener(_url, timeout=None):
            return self.CutMidBody(failure)

        return opener

    def test_a_connection_cut_mid_body_is_a_quote_failure(self):
        """被回報的那一條：``HTTPResponse.read()`` 丟 ``IncompleteRead``。"""
        opener = self.opener_that_dies_in_read(http.client.IncompleteRead(b"Date,Close"))

        with self.assertRaises(QuoteUnavailableError) as raised:
            daily_close(ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=opener)

        self.assertIn("IncompleteRead", str(raised.exception))

    def test_nothing_an_opener_or_a_response_raises_escapes_as_itself(self):
        """清單擋不住的那一整類，含**不是**傳輸層的那些。

        ``RuntimeError`` 與 ``TypeError`` 在這裡不是假想：注入的 opener 是呼叫端
        的程式，簽名寫錯就是 ``TypeError``。它們一樣只該讓這一次查價失敗。
        """
        for failure in (
            http.client.IncompleteRead(b""),
            http.client.BadStatusLine("嗯？"),
            http.client.LineTooLong("header line"),
            RuntimeError("注入的 opener 自己爆了"),
            TypeError("opener() takes 1 positional argument"),
        ):
            for how, opener in (
                ("opener", opener_for(failure)),
                ("read", self.opener_that_dies_in_read(failure)),
            ):
                with self.subTest(failure=type(failure).__name__, raised_by=how):
                    with self.assertRaises(QuoteUnavailableError) as raised:
                        daily_close(
                            ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=opener
                        )

                    self.assertIn(type(failure).__name__, str(raised.exception))

    def test_an_ordinary_answer_is_still_an_ordinary_answer(self):
        """FP 方向：攔得寬不等於把好的答案也吞掉。"""
        quote = daily_close(
            ASSET_CLASS_CRYPTO,
            "BTC",
            date(2026, 8, 5),
            opener=opener_for(csv_body(["2026-08-05,1,2,3,7.5,9"])),
        )

        self.assertEqual(7.5, quote.close)

    def test_stopping_the_program_is_not_a_price_that_could_not_be_had(self):
        """攔 ``Exception`` 的邊界：``BaseException`` 那一側不准被改寫成查價失敗。

        沒有這一條，``except Exception`` 和 ``except BaseException`` 在測試裡長得
        一模一樣，而後者會把 Ctrl-C 變成「這個 run 下次再試」。
        """
        for failure in (KeyboardInterrupt(), SystemExit(1)):
            with self.subTest(type(failure).__name__):
                with self.assertRaises(type(failure)):
                    daily_close(
                        ASSET_CLASS_CRYPTO,
                        "BTC",
                        date(2026, 8, 5),
                        opener=self.opener_that_dies_in_read(failure),
                    )


class OnlyTextEverBecomesAPriceTest(unittest.TestCase):
    """The gates a **text** price passes, and which one of them precedes ``float``.

    CSV fields and form text go through ``is_decimal_numeral`` → ``float`` →
    ``is_usable_price``, and **only the text grammar is before the conversion**.
    The injected ``Quote.close`` takes neither of those steps: it is not text, so
    it never meets the grammar, and it is already a number, so there is no
    conversion — it is checked by the total ``is_usable_price`` alone.

    ``is_usable_price(True)`` was already ``False`` — the guard was right. It just
    sat on the far side of the conversion, so what it was handed was ``1.0`` and
    ``1.0`` is a perfectly good price. A guard placed after the step that destroys
    the evidence guards nothing, and there is no way to tell from the inside that
    it is not being reached. Moving it earlier was never the fix here, because it
    is the check that wants a number; putting a *grammar* in front of it was.

    So the accepted input is written as what is allowed: this parser is handed one
    text field of a CSV answer, therefore text, therefore every other type is
    refused by type before any number exists. That is a rule, not a list, which is
    why the cases below are examples of it rather than the extent of it.
    """

    SOURCE = QUOTE_SOURCES[ASSET_CLASS_CRYPTO]

    class PretendsToBeANumber:
        """Anything at all may define ``__float__``; ``float`` will use it."""

        def __float__(self):
            return 1.0

    def refuse(self, value):
        with self.assertRaises(QuoteUnavailableError) as caught:
            _parse_close(value, self.SOURCE, "BTC")
        return str(caught.exception)

    def test_true_is_not_the_price_one(self):
        """The reported defect: ``float(True)`` is ``1.0`` and was written to disk."""
        message = self.refuse(True)

        self.assertIn("True", message)

    def test_no_type_that_float_would_have_accepted_gets_through(self):
        """Same class, other entrances. ``float`` takes all of these happily."""
        for value in (
            False,
            7,
            7.0,
            Decimal("7.0"),
            Fraction(7, 1),
            self.PretendsToBeANumber(),
        ):
            with self.subTest(type(value).__name__):
                self.assertTrue(self.refuse(value).strip())

    def test_text_that_float_reads_but_is_not_a_numeral_is_refused(self):
        """``float`` is not a numeral grammar, so it is not the gate.

        ``"1_0"`` is ten to :func:`float`, and ``"١٢٣"`` is a hundred and
        twenty-three: a plain ``\\d`` matches Unicode digits too. Neither is
        something a price table prints, and reading either as a number is a wrong
        price that looks entirely ordinary afterwards.
        """
        for spelling in ("1_0", "١٢٣", "1,234.5", "٧.٠", "0x10", " ", "７.０"):
            with self.subTest(spelling):
                self.assertTrue(self.refuse(spelling).strip())

    def test_ordinary_text_prices_still_read(self):
        """FP direction: the positive list has to contain the real answers.

        Surrounding whitespace is included because a CSV field arrives with it.
        """
        for spelling, expected in (
            ("7.0", 7.0),
            ("  7.0  ", 7.0),
            ("7", 7.0),
            (".5", 0.5),
            ("+7.0", 7.0),
            ("1e-8", 1e-8),
            ("1E3", 1000.0),
        ):
            with self.subTest(spelling):
                self.assertEqual(expected, _parse_close(spelling, self.SOURCE, "BTC"))

    def test_a_boolean_field_never_reaches_the_price_test_at_all(self):
        """Where each gate draws its line, stated so the order cannot drift.

        The first gate is about the *kind* of thing; the second is about the
        number. ``True`` fails the first, which is why the second never has to be
        right about it — and the second is kept right about it anyway, because it
        is public.
        """
        self.assertFalse(is_decimal_numeral(True))
        self.assertFalse(is_usable_price(True))
        self.assertTrue(is_decimal_numeral("1.0"))
        self.assertTrue(is_usable_price(1.0))


class APredicateThatCanRaiseIsNotAPredicateTest(unittest.TestCase):
    """兩個 predicate 對**任何**值都只回 ``True`` 或 ``False``，永不拋出。

    被回報的那一條：``is_usable_price(10**1000)`` 丟 ``OverflowError``。
    ``10**1000`` 是 ``int``，型別檢查放它過去，``math.isfinite`` 要把它轉成
    ``float`` 時炸掉。那個例外從 :func:`~hoya_market_agents.webapp.outcome.
    _priced_payload` 的價格關卡逃出去——那道關卡**不在** ``quote()`` 的 try 裡面
    ——穿過 ``sweep_due_runs``，在 cursor 寫入之前結束整輪，於是上一輪剛修好的
    starvation 原樣回來。宣告成封閉契約的函式自己丟出契約外的東西，就是這個形狀。

    **型別列舉關不掉它。** ``isinstance(value, int)`` 不是一句關於行為的話：
    ``int`` 的子類別可以覆寫 ``__gt__``、``__float__`` 與 ``__index__``，而
    ``__class__`` 可以是 property，所以連 ``isinstance`` 本身都可能丟例外。開放
    值域下的 total 不能靠「我想得到的型別都列了」——那正是本專案一路被咬的形狀
    ——只能靠把**失敗這件事**收斂成回傳值：任何 :class:`Exception` 都是「這不是
    可用的價格」「這不是數字文字」。

    :class:`BaseException` 仍然穿出去，與本模組其他地方同一條線：
    ``KeyboardInterrupt`` 與 ``SystemExit`` 是有人要停掉這支程式。
    """

    class ComparisonDetonates(int):
        """``int`` 的子類別，``>`` 會爆。``isinstance`` 完全看不出來。"""

        def __gt__(self, _other):
            raise RuntimeError("這個比較自己爆了")

    class EvenIsinstanceDetonates:
        """``__class__`` 是 property，所以型別檢查本身就是別人的程式。"""

        @property
        def __class__(self):
            raise RuntimeError("連型別檢查都爆了")

    class StripDetonates(str):
        """``str`` 的子類別，``strip`` 會爆。"""

        def strip(self, *_args):
            raise RuntimeError("這個 strip 自己爆了")

    class StripReturnsSomethingElse(str):
        """``strip`` 回了不是文字的東西，於是 ``fullmatch`` 丟 ``TypeError``。"""

        def strip(self, *_args):
            return 5

    def hostile_values(self):
        """一組真的會讓兩個 predicate 各自炸在不同地方的值。"""
        return (
            10 ** 1000,
            -(10 ** 1000),
            self.ComparisonDetonates(5),
            self.EvenIsinstanceDetonates(),
            self.StripDetonates("1"),
            self.StripReturnsSomethingElse("1"),
        )

    def test_a_number_too_large_for_float_is_refused_rather_than_raised(self):
        """被回報的那一條，逐字重現。"""
        self.assertIs(False, is_usable_price(10 ** 1000))
        self.assertIs(False, is_usable_price(-(10 ** 1000)))

    def test_nothing_at_all_makes_the_price_predicate_raise(self):
        for value in self.hostile_values():
            with self.subTest(type(value).__name__):
                self.assertIs(False, is_usable_price(value))

    def test_nothing_at_all_makes_the_numeral_predicate_raise(self):
        """同一個問題問第二次：``is_decimal_numeral`` 也宣告了封閉契約。"""
        for value in self.hostile_values():
            with self.subTest(type(value).__name__):
                self.assertIs(False, is_decimal_numeral(value))

    def test_the_answer_is_always_exactly_a_boolean(self):
        """「只回 True／False」是字面的：不是 truthy，是 ``bool``。

        ``ComparisonReturnsSomethingElse`` 是這一條的重點，也是實作裡那層
        ``bool(...)`` 存在的唯一理由：``value > 0`` 交出去的是子類別決定的東西，
        沒有那層包裝就會原樣回給呼叫端，於是呼叫端拿它去 ``if`` 的時候，跑的又是
        別人的程式。它不在 ``hostile_values`` 裡，因為它不是「會爆」的那一類。
        """

        class ComparisonReturnsSomethingElse(int):
            """``>`` 回了不是布林的東西，而且完全不丟例外。"""

            def __gt__(self, _other):
                return "yes"

        values = self.hostile_values() + (
            ComparisonReturnsSomethingElse(5),
            1.0,
            "1.0",
            True,
            None,
            object(),
        )
        for value in values:
            with self.subTest(repr(type(value).__name__)):
                self.assertIs(bool, type(is_usable_price(value)))
                self.assertIs(bool, type(is_decimal_numeral(value)))

    def test_being_total_did_not_make_them_say_yes_to_everything(self):
        """FP 方向：total 是「永不拋出」，不是「永遠 False」，更不是「永遠 True」。

        少了這一條，``return False`` 兩行就能讓上面全部變綠。
        """
        for usable in (1.0, 7, 0.00000001, 1e308):
            with self.subTest(usable=usable):
                self.assertIs(True, is_usable_price(usable))
        for refused in (0, -1.5, float("inf"), float("nan"), True, None, "7.0"):
            with self.subTest(refused=repr(refused)):
                self.assertIs(False, is_usable_price(refused))
        for numeral in ("7.0", "  7.0  ", ".5", "1e-8"):
            with self.subTest(numeral=numeral):
                self.assertIs(True, is_decimal_numeral(numeral))
        for not_numeral in ("inf", "nan", "1_0", "١٢٣", "", True, 7.0):
            with self.subTest(not_numeral=repr(not_numeral)):
                self.assertIs(False, is_decimal_numeral(not_numeral))

    def test_stopping_the_program_still_stops_the_program(self):
        """攔 ``Exception`` 的邊界，和 ``_fetch`` 那裡同一條。

        沒有這一條，``except Exception`` 與 ``except BaseException`` 在測試裡長得
        一模一樣，而後者會把 Ctrl-C 變成「這不是價格」然後繼續跑下去。
        """

        class StopsTheProgram(int):
            def __gt__(self, _other):
                raise KeyboardInterrupt

        class StopsTheProgramInStrip(str):
            def strip(self, *_args):
                raise SystemExit(1)

        with self.assertRaises(KeyboardInterrupt):
            is_usable_price(StopsTheProgram(5))
        with self.assertRaises(SystemExit):
            is_decimal_numeral(StopsTheProgramInStrip("1"))


class WhatAResponseReturnsIsAsOpenAsWhatItRaisesTest(unittest.TestCase):
    """``_fetch`` 的單一出口契約也涵蓋 ``read()`` **回**了什麼。

    上一輪那道 ``except Exception`` 只包住 ``with opener(...) as response:
    raw = response.read(...)``——也就是**取值**那一段。它把「別人的程式會丟什麼」
    這個開放集合關起來了，但沒有碰「別人的程式會回什麼」那一個：``raw`` 拿到之後
    就流進本函式自己的程式，而那段程式假設它是 ``bytes``。

    於是同一條路上有兩個出口漏在保證外面，都在 try 之外：``len(raw)`` 對沒有
    ``__len__`` 的東西丟 ``TypeError``（``read()`` 回 ``None`` 就是），而
    ``raw.decode(...)`` 對 ``str`` 丟 ``AttributeError``，那道 ``except
    UnicodeError`` 接不到。一次型別驗證同時關掉兩個，因為 ``bytes`` 同時保證了
    ``__len__`` 與 ``decode``。
    """

    class RespondsWith:
        """一個開得起來的 response，``read`` 回測試指定的任何東西。"""

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_exception):
            return False

        def read(self, _size=-1):
            return self.payload

    def opener_returning(self, payload):
        def opener(_url, timeout=None):
            return self.RespondsWith(payload)

        return opener

    def fetch_failure(self, payload):
        with self.assertRaises(QuoteUnavailableError) as raised:
            daily_close(
                ASSET_CLASS_CRYPTO,
                "BTC",
                date(2026, 8, 5),
                opener=self.opener_returning(payload),
            )
        return str(raised.exception)

    def test_a_body_that_is_not_bytes_is_a_quote_failure_not_an_attribute_error(self):
        """被回報的那一條：``read()`` 回 ``str``，``decode`` 不存在。"""
        self.assertIn("str", self.fetch_failure("not-bytes"))

    def test_a_body_with_no_length_is_the_same_one_exception(self):
        """同一條路上的第三個出口：``len(None)`` 在 decode 之前就丟了。"""
        self.assertIn("NoneType", self.fetch_failure(None))

    def test_the_refusal_names_the_type_that_came_back(self):
        """讀 log 的人要能認出是注入的 client 回錯了東西，不是市場沒開。"""
        for payload, name in ((123, "int"), ([], "list"), (object(), "object")):
            with self.subTest(name):
                self.assertIn(name, self.fetch_failure(payload))

    def test_an_ordinary_body_is_still_read(self):
        """FP 方向：型別驗證不得把真的答案擋掉。"""
        quote = daily_close(
            ASSET_CLASS_CRYPTO,
            "BTC",
            date(2026, 8, 5),
            opener=self.opener_returning(
                csv_body(["2026-08-05,1,2,3,7.5,9"]).encode("utf-8")
            ),
        )

        self.assertEqual(7.5, quote.close)


class HostileMeta(type):
    """A metaclass that refuses to say what its instances are called.

    ``type(value).__name__`` 讀的是**元類別**的屬性，所以它和 ``value`` 本身一樣是
    別人的程式。這個類別存在的唯一理由，是讓「組一句拒絕的話」這個動作本身變成一條
    逃逸路徑，好讓測試證明它不再是。
    """

    @property
    def __name__(cls):
        raise RuntimeError("這個型別連名字都問不得")


class Unnameable(metaclass=HostileMeta):
    """Not bytes, and cannot be named in the sentence that refuses it."""


class UnnameableFailure(Exception, metaclass=HostileMeta):
    """A failure from the opener that cannot be named either."""

    def __str__(self):
        raise RuntimeError("連訊息都讀不得")


class HostileStr(str):
    """Text that refuses to be formatted.

    這個類別是「總是回**精確**的 ``str``」那條規則的理由。攔掉例外還不夠：
    ``isinstance(x, str)`` 為真的東西仍然帶著自己的 ``__format__``，而那個
    ``__format__`` 執行的時機，正好是拒絕句被組出來的那一刻——也就是攔截範圍
    結束之後。放行 ``str`` 子類等於把逃逸往後挪一行，不是關掉它。
    """

    def __format__(self, _spec):
        raise RuntimeError("這個字串格式不得")


class SneakilyNamedMeta(type):
    @property
    def __name__(cls):
        return HostileStr("SneakilyNamed")


class SneakilyNamed(metaclass=SneakilyNamedMeta):
    """Not bytes, and its type's name is text that will not be formatted."""


class SneakilyWordedFailure(Exception):
    """A failure from the opener whose message will not be formatted."""

    def __str__(self):
        return HostileStr("這個訊息格式不得")


class BytesIsTheClosedSetNotItsAncestryTest(unittest.TestCase):
    """``_fetch`` 的保證要對 ``read()`` 回傳的**任何**東西成立，不只對乖的那些。

    上一輪把 ``raw`` 檢成 ``isinstance(raw, bytes)``，理由寫在 ``_fetch`` 的
    docstring 裡：「``bytes`` 同時保證了 ``__len__`` 與 ``decode``」。**那句話只對
    精確的內建** ``bytes`` **成立。** ``isinstance`` 問的是血統不是行為，而血統是開放
    集合：任何人都能繼承 ``bytes``，再把 ``__len__`` 或 ``decode`` 換成會丟例外、
    或回傳非文字的東西。於是那道型別檢查放行的，正是它宣稱已經擋掉的——和它自己
    docstring 反對的「列舉」是同一個形狀的錯，只是列舉的是祖先而不是型別名。

    ``bytearray`` 與 ``memoryview`` **本來就不是** ``bytes`` 的子類（
    ``issubclass`` 兩個都是 ``False``），所以改成精確比對並沒有多擋掉它們——它們在
    ``isinstance`` 那一版就已經被擋了。真實路徑上
    ``http.client.HTTPResponse.read(amt)`` 回的正是精確的 ``bytes``，所以這一縮並沒
    有縮掉任何真的答案。

    最後兩條測的是同一個保證的另外兩個出口，兩個都在那道 ``try`` 的**外面**：組拒絕
    句本身（``type(raw).__name__``、``str(exc)``），以及 ``opener`` 只被問了一句
    「你是不是真的」。
    """

    class RespondsWith:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_exception):
            return False

        def read(self, _size=-1):
            return self.payload

    GOOD_BODY = csv_body(["2026-08-05,1,2,3,7.5,9"]).encode("utf-8")

    def opener_returning(self, payload):
        def opener(_url, timeout=None):
            return self.RespondsWith(payload)

        return opener

    def fetch_failure(self, payload):
        with self.assertRaises(QuoteUnavailableError) as raised:
            daily_close(
                ASSET_CLASS_CRYPTO,
                "BTC",
                date(2026, 8, 5),
                opener=self.opener_returning(payload),
            )
        return str(raised.exception)

    def test_a_bytes_subclass_whose_length_cannot_be_taken_is_a_quote_failure(self):
        """``len(raw)`` 只對精確的 ``bytes`` 保證不丟。"""

        class LengthRaises(bytes):
            def __len__(self):
                raise RuntimeError("這個長度問不得")

        self.assertIn("LengthRaises", self.fetch_failure(LengthRaises(self.GOOD_BODY)))

    def test_a_bytes_subclass_whose_decode_raises_is_a_quote_failure(self):
        """``except UnicodeError`` 不是 ``RuntimeError`` 的網子。"""

        class DecodeRaises(bytes):
            def decode(self, *_args, **_options):
                raise RuntimeError("這個內容解不得")

        self.assertIn("DecodeRaises", self.fetch_failure(DecodeRaises(self.GOOD_BODY)))

    def test_a_bytes_subclass_whose_decode_returns_a_number_is_a_quote_failure(self):
        """最遠的一條：不是丟出去，是**回**了非文字，一路走到公開函式才炸。

        ``_fetch`` 回了 ``7``，``_read_close`` 對它呼叫 ``splitlines``，
        ``AttributeError`` 從 ``daily_close`` 漏出去——那是「回文字或丟
        ``QuoteUnavailableError``，沒有第三種答案」直接寫錯的證據。
        """

        class DecodeReturnsANumber(bytes):
            def decode(self, *_args, **_options):
                return 7

        self.assertIn(
            "DecodeReturnsANumber",
            self.fetch_failure(DecodeReturnsANumber(self.GOOD_BODY)),
        )

    def test_an_object_whose_class_attribute_raises_is_a_quote_failure(self):
        """``isinstance`` 對非子類會去讀 ``__class__``，那是別人的程式。"""

        class ClassRaises:
            @property
            def __class__(self):
                raise RuntimeError("這個血統問不得")

        self.assertIn("ClassRaises", self.fetch_failure(ClassRaises()))

    def test_naming_the_type_that_came_back_is_not_itself_a_way_out(self):
        """Reviewer A 那一條：拒絕句裡的 ``type(raw).__name__`` 自己會丟。

        沒有這一條，修好「檢查」之後「訊息」還是開的，而保證仍然是假的。
        """
        message = self.fetch_failure(Unnameable())

        self.assertIn("stooq-daily", message)
        self.assertIn("BTC", message)

    def test_an_exception_that_cannot_be_named_is_still_the_one_quote_failure(self):
        """同一個洞在另一個分支：opener 丟的例外連型別名與訊息都問不得。"""

        def opener(_url, timeout=None):
            raise UnnameableFailure()

        with self.assertRaises(QuoteUnavailableError) as raised:
            daily_close(ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=opener)

        self.assertIn("BTC", str(raised.exception))

    def test_a_type_name_that_is_a_hostile_str_subclass_is_not_a_way_out(self):
        """讀到了名字還不夠：那個名字自己會在 ``format`` 時丟。"""
        message = self.fetch_failure(SneakilyNamed())

        self.assertIn("stooq-daily", message)
        self.assertIn("BTC", message)

    def test_an_error_message_that_is_a_hostile_str_subclass_is_not_a_way_out(self):
        """同一條規則在例外那一支：``str(exc)`` 回的東西也可能不是乖的文字。"""

        def opener(_url, timeout=None):
            raise SneakilyWordedFailure()

        with self.assertRaises(QuoteUnavailableError) as raised:
            daily_close(ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=opener)

        self.assertIn("BTC", str(raised.exception))

    def test_an_opener_that_cannot_be_asked_whether_it_is_true_is_still_used(self):
        """``opener or urllib.request.urlopen`` 問了 ``__bool__``，而且在 try 外面。"""

        class BoolRaises:
            def __bool__(self):
                raise RuntimeError("這個真假問不得")

            def __call__(_self, _url, timeout=None):
                return self.RespondsWith(self.GOOD_BODY)

        quote = daily_close(
            ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=BoolRaises()
        )

        self.assertEqual(7.5, quote.close)

    def test_a_falsy_opener_is_still_the_seam_and_opens_no_socket(self):
        """不是敵意輸入，是這個模組自己的安全宣稱：只有沒給 opener 才會連外。

        ``opener or urllib.request.urlopen`` 把「有沒有給」問成「給的東西真不真」。
        一個完全正常、只是不為真的 opener 會被**丟掉**，然後真的 ``urlopen`` 被叫起
        來——模組 docstring 說「除非 ``daily_close`` 沒有拿到 ``opener``，這個模組不
        會開任何 socket」就地變成假的。這一條用本檔案的守衛計數器證明沒有走到那裡。
        """

        class FalsyOpener:
            def __bool__(self):
                return False

            def __call__(_self, _url, timeout=None):
                return self.RespondsWith(self.GOOD_BODY)

        blocked_before = len(BLOCKED_URLS)

        quote = daily_close(
            ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=FalsyOpener()
        )

        self.assertEqual(7.5, quote.close)
        self.assertEqual(blocked_before, len(BLOCKED_URLS))

    def test_an_ordinary_bytes_body_is_still_read(self):
        """FP 方向：精確比對不得把真的答案擋掉。

        真實路徑回的就是精確的 ``bytes``，這一條是那條路的替身。少了它，
        ``raise QuoteUnavailableError`` 一行無條件拒絕就能讓上面全部變綠。
        """
        quote = daily_close(
            ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5),
            opener=self.opener_returning(self.GOOD_BODY),
        )

        self.assertEqual(7.5, quote.close)
        self.assertEqual("2026-08-05", quote.priced_on)

    def test_a_well_behaved_bytes_subclass_is_refused_by_name_on_purpose(self):
        """精確比對是刻意的收窄，不是意外，所以它有自己的一條。

        一個乖的子類也被拒絕。真實路徑不會產生它，而「``bytes`` 保證了 ``__len__``
        與 ``decode``」這句話對子類本來就不成立——放行它就是把保證建立在別人願意守
        規矩上。訊息要點名它，讀 log 的人才知道是注入的 client 回錯了東西。
        """

        class PoliteBytesSubclass(bytes):
            pass

        self.assertIn(
            "PoliteBytesSubclass",
            self.fetch_failure(PoliteBytesSubclass(self.GOOD_BODY)),
        )


class TheDecodeRunsSomebodyElsesCodeTooTest(unittest.TestCase):
    """``"strict"`` 不是保留字，是一個任何人都寫得到的全域登錄表的鍵。

    上一輪把這一支刻意留在保證外面，理由寫在 ``_fetch`` 的 docstring 裡：走到
    ``raw.decode`` 時 ``raw`` 已經是內建 ``bytes``，所以「那一句裡沒有別人的
    程式」。**那句話是假的。** ``bytes.decode`` 碰到不合法位元組時，並不是自己
    決定要丟什麼——它拿字串 ``"strict"`` 去 ``codecs`` 的 **error handler 登錄表**
    查一個 callable 出來呼叫，而 :func:`codecs.register_error` 是公開 API，任何
    函式庫都能把那個鍵重新綁到自己的函式上。於是那一句跑的正是別人的程式，而
    ``except UnicodeError`` 不是 ``RuntimeError`` 的網子。

    這和 ``raw`` 那一條是**同一個形狀**，差別只在登錄表放在 CPython 裡而不是放在
    參數裡：規則從來不是「別人的**物件**要包起來」，而是「別人的**程式**不能沒有
    網子就跑」。上一輪讀成了前者，所以擋掉了會被遞進來的物件，卻放行了會被查表查
    出來的函式。

    被換掉的是**錯誤處理器**而不是編碼器。``"utf-8"`` 這個編碼器名字同樣是登錄表的
    鍵、:func:`codecs.register` 同樣是公開 API。**量到的只有一行**：
    ``bytes.decode("utf-8")`` 不會去問事後註冊的 search function，因為 CPython 在
    ``PyUnicode_Decode`` 裡就把這個名字短路掉了，還沒走到登錄表。對照組是
    ``bytes.decode("cp1252")``——同一個劫持用的 search function 抓得到它——所以
    utf-8 那個零不是量壞了。

    **先前這一段把一件量到的事寫成了三件。** 它說 :func:`codecs.decode` 與
    :func:`codecs.lookup` 也一樣，理由是「快取與 fast path 都在登錄表底下」。那兩
    句是錯的：那兩個函式**會**走登錄表，只要清掉直譯器的 codec search cache、而且
    排在前面的 search function 讓開，它們就回傳被劫持的 codec。當初清掉的是
    ``encodings._cache``——上面一層的另一個快取，屬於那個被最先註冊、會搶著回答
    ``utf_8`` 的 search function。那個零量到的是**註冊順序**，不是洞不存在。

    所以這裡沒有替編碼器那一側寫測試，理由也跟著收窄成一句：這個模組跑的是
    ``bytes.decode``，不是 :func:`codecs.decode`。
    """

    BAD_BODY = b"Date,Open,High,Low,Close,Volume\n2026-08-05,1,2,3,\xff7.5,9\n"

    def setUp(self):
        """把原本的 ``"strict"`` 收好，並保證每一條測完都放回去。

        這個登錄表是行程全域的，換掉不還原會污染同一個行程裡後面每一個測試。
        """
        self.addCleanup(codecs.register_error, "strict", codecs.lookup_error("strict"))

    def refuse(self, payload):
        with self.assertRaises(QuoteUnavailableError) as raised:
            daily_close(
                ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=opener_for(payload)
            )
        return str(raised.exception)

    def test_a_replaced_strict_handler_is_still_the_one_quote_failure(self):
        """Reviewer B 的重現：登錄表被換掉，那一句就丟 ``RuntimeError``。"""

        def hostile(_exc):
            raise RuntimeError("registered strict handler escaped")

        codecs.register_error("strict", hostile)

        message = self.refuse(self.BAD_BODY)

        self.assertIn("stooq-daily", message)
        self.assertIn("BTC", message)
        self.assertIn("RuntimeError", message)

    def test_a_replaced_strict_handler_that_cannot_be_named_is_still_one(self):
        """同一條規則再往下一層：連那個例外的型別名與訊息都問不得。

        沒有這一條，把 ``except`` 放寬以後「訊息」那一側還是開的——和
        :meth:`BytesIsTheClosedSetNotItsAncestryTest.
        test_an_exception_that_cannot_be_named_is_still_the_one_quote_failure`
        是同一個理由。
        """

        def hostile(_exc):
            raise UnnameableFailure()

        codecs.register_error("strict", hostile)

        message = self.refuse(self.BAD_BODY)

        self.assertIn("stooq-daily", message)
        self.assertIn("BTC", message)

    def test_an_ordinary_undecodable_body_is_still_the_one_quote_failure(self):
        """沒有人動登錄表時的那一條：CPython 自己造的 ``UnicodeDecodeError``。

        這一支在這一輪以前**完全沒有測試**，所以把 ``except UnicodeError`` 放寬成
        ``except Exception`` 有可能連它一起弄丟而沒有人會知道。訊息現在也點出型別
        名，理由和其他分支一樣：讀 log 的人要能一眼看出是哪一種失敗。
        """
        message = self.refuse(self.BAD_BODY)

        self.assertIn("stooq-daily", message)
        self.assertIn("BTC", message)
        self.assertIn("UnicodeDecodeError", message)

    def test_an_ordinary_utf8_body_is_still_read(self):
        """FP 方向：這一輪的放寬不得把真的答案擋掉。

        少了這一條，在 ``decode`` 前面無條件 ``raise`` 一行就能讓上面全部變綠。
        """
        quote = daily_close(
            ASSET_CLASS_CRYPTO,
            "BTC",
            date(2026, 8, 5),
            opener=opener_for(csv_body(["2026-08-05,1,2,3,7.5,9"])),
        )

        self.assertEqual(7.5, quote.close)

    def test_a_multibyte_utf8_body_is_still_read(self):
        """FP 方向的另一半：真的多位元組 UTF-8 照樣解得開，不是只有 ASCII。"""
        body = csv_body(
            ["2026-08-05,1,2,3,7.5,9"], header="Date,Open,High,Low,Close,成交量"
        )

        quote = daily_close(
            ASSET_CLASS_CRYPTO,
            "BTC",
            date(2026, 8, 5),
            opener=opener_for(body.encode("utf-8")),
        )

        self.assertEqual(7.5, quote.close)

    def test_a_hijacked_strict_handler_does_not_break_a_legal_body(self):
        """組合 FP 哨兵（Reviewer B 提）：登錄表**被挾持**，body 卻是合法 UTF-8。

        「拒絕」那一側有三條釘著，「成功」那一側有兩條釘著，但**兩件事同時成立**
        這個組合先前沒有任何一條固定住。它才是這個分支真正要成立的性質：守衛只有
        在真的失敗時開火，而 ``"strict"`` 的處理器在沒有壞位元組時根本不會被叫到。

        最後那一句是哨兵自己的哨兵——如果挾持在這一輪其實沒生效，上面順利取到價格
        就什麼都沒證明。
        """

        def hostile(_exc):
            raise RuntimeError("registered strict handler escaped")

        codecs.register_error("strict", hostile)

        quote = daily_close(
            ASSET_CLASS_CRYPTO,
            "BTC",
            date(2026, 8, 5),
            opener=opener_for(csv_body(["2026-08-05,1,2,3,7.5,9"])),
        )

        self.assertEqual(7.5, quote.close)
        self.assertEqual("2026-08-05", quote.priced_on)
        with self.assertRaises(RuntimeError):
            b"\xff".decode("utf-8")


class ASymbolIsACallersObjectTooTest(unittest.TestCase):
    """入口那道 ``symbol`` 檢查問的是血統，而血統是開放集合。

    ``isinstance(symbol, str)`` 為真以後，這個函式就對 ``symbol`` 呼叫
    ``strip()``、把它交給 ``symbol_for`` 的 ``str()``、再把 ``symbol.strip()`` 的
    結果寫進 :class:`Quote`。那三件事都是子類說了算，所以這道檢查放行的正是它宣稱
    已經擋掉的——和 ``raw`` 上一輪那個 ``isinstance`` 是**逐字同一個錯**，只是位置
    在函式的另一端。

    這裡改成精確比對，是把一道**已經存在**的檢查改成和這個模組別處同一個標準，
    不是新增一道政策。真實路徑上 ``symbol`` 來自 ``json.loads`` 解出來的
    ``assets``（``webapp/outcome.py`` 的 ``_assets``），只會是精確的 ``str``，所以
    這一縮沒有縮掉任何真的呼叫。

    **這一條並不宣稱把「呼叫端引數」整類關掉了**——``asset_class`` 與 ``day`` 同樣
    是呼叫端的物件，同樣跑得了別人的程式，而它們刻意沒有檢查。那條邊界寫在
    :func:`daily_close` 的 docstring 裡，是這個模組講明的**呼叫端那一側**。
    """

    GOOD_BODY = csv_body(["2026-08-05,1,2,3,7.5,9"])

    def refuse(self, symbol):
        with self.assertRaises(QuoteUnavailableError) as raised:
            daily_close(
                ASSET_CLASS_CRYPTO,
                symbol,
                date(2026, 8, 5),
                opener=opener_for(self.GOOD_BODY),
            )
        return str(raised.exception)

    def test_a_str_subclass_whose_strip_raises_is_a_quote_failure(self):
        """Reviewer A 的重現：入口那道檢查自己就呼叫了 ``strip``。"""

        class StripRaises(str):
            def strip(self, *_args):
                raise RuntimeError("這個代號 strip 不得")

        self.refuse(StripRaises("BTC"))

    def test_a_str_subclass_whose_str_raises_is_a_quote_failure(self):
        """第二個出口：``symbol_for`` 用 ``str(symbol)`` 拼服務端的寫法。"""

        class StrRaises(str):
            def __str__(self):
                raise RuntimeError("這個代號讀不得")

        self.refuse(StrRaises("BTC"))

    def test_a_str_subclass_whose_strip_returns_a_number_is_refused(self):
        """最遠的一條：不是丟出去，是**回**了非文字，然後被寫進紀錄。

        ``Quote(symbol=symbol.strip())`` 收下了一個 ``int``，於是
        ``outcome.json`` 裡那個「標的代號」欄位根本不是代號——這是「回一個
        :class:`Quote` 或丟 ``QuoteUnavailableError``」直接寫錯的證據，和
        ``decode`` 回數字那一條是同一種。
        """

        class StripReturnsANumber(str):
            def strip(self, *_args):
                return 12345

        self.refuse(StripReturnsANumber("BTC"))

    def test_a_well_behaved_str_subclass_is_refused_on_purpose(self):
        """精確比對是刻意的收窄，不是意外，所以它有自己的一條。

        真實路徑產不出 ``str`` 子類，而「``str`` 保證了 ``strip`` 回文字」這句話對
        子類本來就不成立——放行它就是把保證建立在別人願意守規矩上。
        """

        class PoliteStrSubclass(str):
            pass

        self.refuse(PoliteStrSubclass("BTC"))

    def test_an_ordinary_symbol_is_still_accepted(self):
        """FP 方向：精確比對不得把真的呼叫擋掉，前後空白照樣被修掉。"""
        quote = daily_close(
            ASSET_CLASS_CRYPTO,
            "  BTC  ",
            date(2026, 8, 5),
            opener=opener_for(self.GOOD_BODY),
        )

        self.assertEqual("BTC", quote.symbol)
        self.assertIs(str, type(quote.symbol))
        self.assertEqual(7.5, quote.close)


class MarketSessionTest(unittest.TestCase):
    """Which day's close had already printed at a given instant.

    A daily bar is complete at its market's close, so at 02:00Z on a Thursday
    that Thursday's close does not exist. Asking for it anyway is how a
    prediction ends up scored against a price from after it was made.
    """

    def day_for(self, asset_class, moment):
        return available_close_day(asset_class, moment).isoformat()

    def test_crypto_before_the_end_of_the_utc_day_gets_the_day_before(self):
        self.assertEqual(
            "2026-08-05",
            self.day_for(
                ASSET_CLASS_CRYPTO, datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc)
            ),
        )

    def test_crypto_a_fraction_of_a_second_before_midnight_is_still_incomplete(self):
        """23:59:59.5Z is half a second of trading short of a finished bar.

        FN direction. ``time(23, 59, 59)`` as the boundary answered with the
        current day from 23:59:59.000000Z onwards, which is a daily bar that has
        not finished printing — the exact look-ahead the session table exists to
        remove, just a smaller slice of it than the one that was noticed first.
        """
        for microsecond in (0, 500000, 999999):
            with self.subTest(microsecond=microsecond):
                self.assertEqual(
                    "2026-08-05",
                    self.day_for(
                        ASSET_CLASS_CRYPTO,
                        datetime(
                            2026, 8, 6, 23, 59, 59, microsecond, tzinfo=timezone.utc
                        ),
                    ),
                )

    def test_crypto_gets_a_day_once_the_next_one_has_started(self):
        """The bar for a UTC day is complete at the next 00:00:00Z, exactly.

        FP direction for the same boundary: waiting for midnight must not turn
        into waiting for ever.
        """
        self.assertEqual(
            "2026-08-06",
            self.day_for(
                ASSET_CLASS_CRYPTO, datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc)
            ),
        )

    def test_a_us_instant_before_the_new_york_close_gets_the_previous_day(self):
        """17:00Z is 13:00 in New York in August: the close is three hours off."""
        self.assertEqual(
            "2026-08-05",
            self.day_for(
                ASSET_CLASS_US_STOCK, datetime(2026, 8, 6, 17, 0, tzinfo=timezone.utc)
            ),
        )

    def test_a_us_instant_after_the_new_york_close_gets_that_day(self):
        self.assertEqual(
            "2026-08-06",
            self.day_for(
                ASSET_CLASS_US_STOCK, datetime(2026, 8, 6, 21, 0, tzinfo=timezone.utc)
            ),
        )

    def test_the_new_york_close_moves_with_daylight_saving(self):
        """**One** instant, 20:30Z, on the two sides of the year.

        20:30Z is 15:30 in New York in January (EST, UTC−5) and 16:30 in July
        (EDT, UTC−4). The close is 16:00 local, so the same instant is *before*
        the close in winter — answer: the day before — and *after* it in summer
        — answer: that day. That difference is the whole of what DST does here.

        **It has to be the same instant on both sides, or nothing is being
        tested.** This test previously said 20:30Z in its docstring, said it in
        the direction opposite to the truth, and then asserted about 21:30Z and
        19:30Z — two instants an hour either side, which land on their expected
        days under a *fixed* offset just as well as under a real zone. Replacing
        ``America/New_York`` with a fixed UTC−5 left it green, which is the
        definition of a test that is not testing its own name. Under one instant
        a fixed offset must answer the same day twice and one of the two
        assertions has to fail.
        """
        for month, expected in ((1, "2026-01-14"), (7, "2026-07-15")):
            with self.subTest(month=month):
                self.assertEqual(
                    expected,
                    self.day_for(
                        ASSET_CLASS_US_STOCK,
                        datetime(2026, month, 15, 20, 30, tzinfo=timezone.utc),
                    ),
                )

    def test_a_taipei_instant_before_the_close_gets_the_previous_day(self):
        """02:00Z is 10:00 in Taipei; the exchange has not closed."""
        self.assertEqual(
            "2026-08-05",
            self.day_for(
                ASSET_CLASS_TW_STOCK, datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc)
            ),
        )

    def test_a_taipei_instant_after_the_close_gets_that_day(self):
        self.assertEqual(
            "2026-08-06",
            self.day_for(
                ASSET_CLASS_TW_STOCK, datetime(2026, 8, 6, 6, 0, tzinfo=timezone.utc)
            ),
        )

    def test_taipei_between_1330_and_1333_has_not_certainly_closed_yet(self):
        """FN direction: 13:30 is the earliest the close can print, not the latest.

        Taiwan's closing call auction may be extended to 13:33 for a security
        that triggered a volatility interruption, so between 13:30 and 13:33 the
        day's close may not exist yet. A boundary at 13:30 claimed it anyway,
        which is a price from after the instant asked about — and that is the one
        error direction ``outcome.json`` can never take back.

        05:30Z and 05:32Z are 13:30 and 13:32 in Taipei.
        """
        for hour, minute in ((5, 30), (5, 31), (5, 32)):
            with self.subTest(utc="{:02d}:{:02d}Z".format(hour, minute)):
                self.assertEqual(
                    "2026-08-05",
                    self.day_for(
                        ASSET_CLASS_TW_STOCK,
                        datetime(2026, 8, 6, hour, minute, tzinfo=timezone.utc),
                    ),
                )

    def test_taipei_at_1333_has_closed(self):
        """FP direction: the late boundary must still be a boundary.

        05:33Z is 13:33 in Taipei, the latest the close can print.
        """
        self.assertEqual(
            "2026-08-06",
            self.day_for(
                ASSET_CLASS_TW_STOCK, datetime(2026, 8, 6, 5, 33, tzinfo=timezone.utc)
            ),
        )

    def test_every_boundary_is_the_latest_the_close_can_print(self):
        """The safety property, asserted as one fact instead of three examples.

        A session whose boundary is early answers with a day whose close has not
        printed; a session whose boundary is late answers with an older, finished
        close. Only one of those two can be corrected afterwards, so every entry
        in the table is held to the late side.
        """
        self.assertEqual(
            {
                ASSET_CLASS_CRYPTO: timedelta(days=1),
                ASSET_CLASS_TW_STOCK: timedelta(hours=13, minutes=33),
                ASSET_CLASS_US_STOCK: timedelta(hours=16),
            },
            {
                asset_class: session.close_after
                for asset_class, session in MARKET_SESSIONS.items()
            },
        )

    def test_every_class_with_a_source_has_a_session(self):
        """Pinned to the source table, not kept in step by hand.

        A class that can be priced but has no session would fall back to a
        calendar day, which is the defect the table exists to remove — so the
        two sets are the same set or this fails.
        """
        self.assertEqual(set(QUOTE_SOURCES), set(MARKET_SESSIONS))

    def test_a_class_with_no_source_has_no_day_either(self):
        for asset_class in (ASSET_CLASS_OPEN, "commodities", None):
            with self.subTest(asset_class):
                with self.assertRaises(QuoteUnavailableError):
                    available_close_day(
                        asset_class, datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc)
                    )

    def test_a_session_whose_zone_is_missing_refuses_rather_than_guessing(self):
        """No fixed-offset fallback: a wrong day is a wrong price and a wrong verdict."""
        session = MarketSession(
            "crypto", "Mars/Olympus_Mons", timedelta(hours=16)
        )

        with self.assertRaises(QuoteUnavailableError) as refused:
            session.available_day(datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc))

        self.assertIn("Mars/Olympus_Mons", str(refused.exception))


class EveryZoneDatabaseFailureIsTheOneExceptionTest(unittest.TestCase):
    """時區資料庫是**這台機器**，不是這個模組擁有的值，而機器拒絕的方式不只一種。

    :meth:`MarketSession.zone` 先前攔的是一份清單——``ZoneInfoNotFoundError`` 與
    ``ValueError``——寫的時候把「查不到條目」讀成了唯一的失敗方式。那正是本 Task
    一路被咬的同一個形狀（開放集合上的清單），只是換到了時區這一側；而
    :func:`available_close_day` 的 docstring 早就對外宣稱「不管**怎麼**失敗都只有
    一種例外要處理」。兩位 Reviewer 都是照那句宣稱判的：**程式宣稱與行為不符**。

    下面兩條是 Reviewer A 的重現。兩條都**不是惡意輸入**，都是一台普通機器處在不
    尋常的狀態：

    * TZPATH 底下有一個 ``stat`` 得到、``open`` 不得的 TZif 檔。
      ``os.path.isfile`` 說「有」，於是路徑被回傳，接著那個 ``open`` 丟
      ``PermissionError``——失敗的是開檔，不是那道測試，所以 ``isfile`` 吞不掉它。
    * 一個對 ``tzdata`` 丟例外的 import hook。``zoneinfo._common.load_tzdata``
      只攔 ``ImportError``、``FileNotFoundError`` 與 ``UnicodeEncodeError``，別的
      原樣穿出去。

    代價是有界的：不會產生錯價，也不會寫進 ``outcome.json``（唯一呼叫端自己有
    ``except Exception``）。真正的代價是 **log 分類**——維運會把一個環境問題讀成
    這支程式的 bug。所以這裡改成攔 ``Exception``，和 :func:`_fetch` 同一個理由、
    同一個形狀，而不是往清單上再加兩個名字。
    """

    MOMENT = datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc)

    def setUp(self):
        """TZPATH、``ZoneInfo`` 快取與 ``sys.meta_path`` 三個都是行程全域的。

        換掉不還原，會污染同一個行程裡後面每一個碰到時區的測試。
        """
        self.addCleanup(zoneinfo.ZoneInfo.clear_cache)
        self.addCleanup(zoneinfo.reset_tzpath)
        self.addCleanup(setattr, sys, "meta_path", list(sys.meta_path))
        zoneinfo.ZoneInfo.clear_cache()

    def session_for(self, zone_name="UTC"):
        return MarketSession(ASSET_CLASS_CRYPTO, zone_name, timedelta(days=1))

    def an_unreadable_tzif_on_the_path(self):
        """Put a TZif file that ``stat``\\ s fine and cannot be opened on TZPATH."""
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "UTC")
        with open(path, "wb") as handle:
            handle.write(b"TZif")
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o644)
        zoneinfo.reset_tzpath([directory])
        zoneinfo.ZoneInfo.clear_cache()

    def an_import_hook_that_raises_for_tzdata(self, failure):
        """Empty the TZPATH so ``tzdata`` is reached, then poison that import."""
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        zoneinfo.reset_tzpath([empty])
        zoneinfo.ZoneInfo.clear_cache()

        class Hook:
            def find_spec(self, name, _path=None, _target=None):
                if name == "tzdata" or name.startswith("tzdata."):
                    raise failure
                return None

        sys.meta_path.insert(0, Hook())

    def test_a_tzif_file_that_cannot_be_opened_is_one_quote_failure(self):
        """Reviewer A 的重現一：``PermissionError``，清單上一個都對不上。"""
        self.an_unreadable_tzif_on_the_path()

        with self.assertRaises(QuoteUnavailableError) as refused:
            self.session_for().zone()

        self.assertIn("UTC", str(refused.exception))
        self.assertIn("PermissionError", str(refused.exception))

    def test_an_import_hook_that_raises_for_tzdata_is_one_quote_failure(self):
        """Reviewer A 的重現二：``load_tzdata`` 不是 ``RuntimeError`` 的網子。"""
        self.an_import_hook_that_raises_for_tzdata(
            RuntimeError("tzdata import hook escaped")
        )

        with self.assertRaises(QuoteUnavailableError) as refused:
            self.session_for().zone()

        self.assertIn("UTC", str(refused.exception))
        self.assertIn("RuntimeError", str(refused.exception))

    def test_available_close_day_is_the_one_exception_too(self):
        """那句宣稱寫在 :func:`available_close_day` 上，就從那個入口測它。

        ``MARKET_SESSIONS`` 的 crypto 走的正是 ``UTC``，所以上面那個讀不到的
        ``UTC`` 檔就是真實路徑會踩到的那一個。
        """
        self.an_unreadable_tzif_on_the_path()

        with self.assertRaises(QuoteUnavailableError) as refused:
            available_close_day(ASSET_CLASS_CRYPTO, self.MOMENT)

        self.assertIn("PermissionError", str(refused.exception))

    def test_a_failure_that_cannot_be_named_is_still_one_quote_failure(self):
        """訊息那一側也要關上，否則放寬 ``except`` 只是把出口往後挪一行。

        和 :meth:`BytesIsTheClosedSetNotItsAncestryTest.
        test_an_exception_that_cannot_be_named_is_still_the_one_quote_failure`
        是同一個理由：組拒絕句這個動作本身讀了別人的物件。
        """
        self.an_import_hook_that_raises_for_tzdata(UnnameableFailure())

        with self.assertRaises(QuoteUnavailableError) as refused:
            self.session_for().zone()

        self.assertIn("UTC", str(refused.exception))

    def test_an_ordinary_machine_still_builds_every_session_zone(self):
        """FP 方向：放寬 ``except`` 不得把正常機器上的正常時區也變成拒絕。

        這一條在修正前後都是綠的，它釘的不是修正而是修正**沒有**造成的事——一個
        改成無條件拒絕的 :meth:`~MarketSession.zone` 會讓上面四條照樣通過。
        """
        for asset_class, session in sorted(MARKET_SESSIONS.items()):
            with self.subTest(asset_class):
                self.assertEqual(session.zone_name, str(session.zone()))

    def test_an_ordinary_machine_still_answers_with_a_day(self):
        """FP 方向的另一半：整條路徑照樣算得出交易日。"""
        self.assertEqual(
            date(2026, 8, 5), available_close_day(ASSET_CLASS_CRYPTO, self.MOMENT)
        )


class NoSecondFunctionLevelCallSiteToTheSeamTest(unittest.TestCase):
    """釘住的是「``outcome.py`` 的**函式層**沒有第二個接縫呼叫點」，不是「唯一呼叫端」。

    模組 docstring 說「唯一呼叫端是 ``_priced_payload``」。**這個類別證不到那麼
    多。** 它靠的是 :meth:`seam_callers` 那支掃描器，而掃描器只走同步 ``def``、只
    比對被呼叫者的名字；掃不到與會誤算的寫法列在它自己的 docstring 裡，那些是它
    的**限制**。這裡量到的是掃描器看得見的那一層，能宣稱的也就是那一層。

    那一層仍然值得釘，因為它正是最可能垮的一層。「唯一呼叫端」那句話是**邊界那一
    段的地基**：``asset_class`` 與 ``day`` 刻意沒有檢查，理由是真實路徑上它們來自
    ``json.loads`` 解出來的紀錄與 :func:`available_close_day` 的回傳，所以不會有敵
    意物件。地基垮了，上面那一整段就跟著垮——而未來的自己要弄垮它，最順手的方式
    就是在 ``outcome.py`` 裡多寫一個平凡的同步 ``def``。

    ``tests/test_webapp.py`` 的 allowlist 掃的是**誰 import 了這個模組**，不是**誰
    呼叫它**：在同一個 ``webapp/outcome.py`` 裡多加一個呼叫點，那份 allowlist 一個
    字都不會變。威脅模型不是外部攻擊者（本機單人程式沒有），是**未來的自己**。

    ``daily_close`` 在 ``outcome.py`` 裡從來不是被直接呼叫的——它是 ``quote`` 這個
    接縫的預設值（``quote or daily_close``）。所以這裡釘的是**接縫的呼叫點**，不是
    那個名字；釘名字會掃到零個呼叫然後高高興興地通過。
    """

    OUTCOME = Path(hoya_market_agents.__file__).parent / "webapp" / "outcome.py"

    def seam_callers(self, source):
        """回傳 ``source`` 裡**同步 ``def``** 這一層、內部呼叫了名為 ``quote`` 者的函式名。

        這支掃描器是刻意做窄的。以下是它**掃不到**與**會誤算**的東西——寫在這裡
        是為了界定用到它的測試宣稱到哪裡為止，不是「日後可以再補強」的清單：

        * 它只認 :class:`ast.FunctionDef`，也就是**同步** ``def``。呼叫點只要不在
          任何同步 ``def`` 的子樹裡就看不見：module 層的呼叫、頂層 ``async def``
          裡的呼叫（那是 :class:`ast.AsyncFunctionDef`，另一個型別）、module 層的
          lambda 與 module 層的生成式，全部掃出空集合。（反過來，巢狀在某個同步
          ``def`` 裡的 ``async def``、lambda 與生成式**看得見**，但會記在外層那個
          同步 ``def`` 的名字底下。）
        * 它只比對被呼叫者的名字（``ast.Name.id`` 或 ``ast.Attribute.attr``），不
          追型別也不追來源。任何 ``某物.quote()`` 都會被算成呼叫這個接縫。
        * 巢狀 ``def`` 裡的呼叫會同時記在內層與外層兩個名字底下。

        所以由它得出的「沒有第二個呼叫點」，只在上述涵蓋範圍內成立。
        """
        callers = set()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                func = inner.func
                if isinstance(func, ast.Name):
                    name = func.id
                else:
                    name = getattr(func, "attr", None)
                if name == "quote":
                    callers.add(node.name)
        return callers

    def test_no_function_level_call_site_for_the_seam_besides_priced_payload(self):
        """在 :meth:`seam_callers` 看得見的那一層，呼叫接縫的只有 ``_priced_payload``。

        「那一層」就是同步 ``def``，範圍與誤算都以 :meth:`seam_callers` 的 docstring
        為準。具體地說，這一條**不涵蓋**這些呼叫點：寫在 module 層的、寫在頂層
        ``async def`` 裡的、寫在 module 層 lambda 裡的——``outcome.py`` 哪天長出這
        三種其中一種，這一條照樣是綠的。反方向它也會誤算：任何 ``某物.quote()``
        都會被當成呼叫這個接縫而讓這一條變紅。
        """
        source = self.OUTCOME.read_text(encoding="utf-8")

        self.assertEqual({"_priced_payload"}, self.seam_callers(source))

    def test_daily_close_is_never_called_by_name_in_outcome(self):
        """它是預設值，不是呼叫。這一條是上一條為什麼掃接縫而不掃名字的理由。"""
        source = self.OUTCOME.read_text(encoding="utf-8")
        called = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "daily_close"
        ]

        self.assertEqual([], called)

    def test_the_scan_would_see_a_second_call_site(self):
        """FN 方向：掃不到東西的掃描器也會通過，所以要證明它掃得到。"""
        source = (
            "def _priced_payload(quote):\n"
            "    return quote(1, 2, 3)\n"
            "\n"
            "def _somewhere_new(quote):\n"
            "    return quote(4, 5, 6)\n"
        )

        self.assertEqual(
            {"_priced_payload", "_somewhere_new"}, self.seam_callers(source)
        )

    def test_the_scan_does_not_count_a_seam_merely_passed_along(self):
        """FP 方向：把接縫**傳下去**不是呼叫它，否則 ``quote=self.quote`` 會假紅。"""
        source = (
            "class Sweep:\n"
            "    def run(self):\n"
            "        return _priced_payload(quote=self.quote)\n"
        )

        self.assertEqual(set(), self.seam_callers(source))


class NoDefaultNetworkInATestTest(unittest.TestCase):
    """The opener is a seam, and the tests never fall back to the real one."""

    def test_the_opener_is_the_only_way_out_and_it_is_replaceable(self):
        opener = opener_for(csv_body(["2026-08-05,1,2,3,7.0,9"]))

        daily_close(ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5), opener=opener)

        self.assertEqual(1, len(opener.urls))

    def test_every_url_this_module_builds_is_https(self):
        for asset_class in QUOTE_SOURCES:
            opener = opener_for(csv_body(["2026-08-05,1,2,3,7.0,9"]))
            daily_close(asset_class, "AAA", date(2026, 8, 5), opener=opener)
            self.assertTrue(opener.urls[0].startswith("https://"), asset_class)

    def test_the_guard_this_module_installs_is_actually_in_place(self):
        """The docstring's claim, checked rather than asserted in prose.

        ``setUpModule`` replaces :func:`urllib.request.urlopen` for the length
        of this file. If it were not installed — or were quietly restored — a
        call with no opener would reach the real network instead of failing
        here, and every "no test opens a socket" sentence above would be a
        claim nobody was keeping.
        """
        self.assertIs(urllib.request.urlopen, _refuse_outbound)

        with self.assertRaises(QuoteUnavailableError) as refused:
            daily_close(ASSET_CLASS_CRYPTO, "BTC", date(2026, 8, 5))

        self.assertIn(GUARD_MESSAGE, str(refused.exception))

    def test_the_guard_counts_what_it_stopped(self):
        """FP direction: a guard that was never reached would prove nothing."""
        before = len(BLOCKED_URLS)

        with self.assertRaises(QuoteUnavailableError):
            daily_close(ASSET_CLASS_TW_STOCK, "2330", date(2026, 8, 5))

        self.assertEqual(before + 1, len(BLOCKED_URLS))
        self.assertIn("2330", BLOCKED_URLS[-1])


if __name__ == "__main__":
    unittest.main()
