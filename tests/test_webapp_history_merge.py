"""Ticket 04: the history query and the hit-rate page are one page.

What this file pins is the *merge*, not the two pages' own behaviour — the
filters belong to ``run_index``, the verdicts to ``webapp.outcome``, and both are
already pinned in ``tests/test_webapp.py``. What is new here is that one URL
shows both, that the old URL still gets a reader there, and that everything the
merged page puts on screen is Traditional Chinese.

The fixtures come from ``tests/test_webapp.py`` rather than being copied: a
second ``write_run`` would be a second definition of what a finished run looks
like, and the two would drift.
"""

import re
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from html import escape
from pathlib import Path
from unittest import mock

# So this module can be named either way: ``discover -s tests`` puts this
# directory on the path itself, ``python3 -m unittest
# tests.test_webapp_history_merge`` from the Code Root does not.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hoya_market_agents.question import (  # noqa: E402
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_TW_STOCK,
    ASSET_CLASSES,
)
from hoya_market_agents.report_contract import CONFIDENCE_LEVELS  # noqa: E402
from hoya_market_agents.report_renderer import CONSENSUS_LABELS  # noqa: E402
from hoya_market_agents.run_index import (  # noqa: E402
    OUTCOME_HIT,
    OUTCOME_MISS,
    OUTCOME_PENDING,
    OUTCOME_RECORD_NAME,
    OUTCOME_STATES,
    OUTCOME_UNREADABLE,
    OUTCOME_UNVERIFIABLE,
    outcome_summary,
    query_runs,
    rebuild_index,
)
from hoya_market_agents.run_store import RunStore, resolve_run_dir  # noqa: E402
from hoya_market_agents.seats import seat_display_names  # noqa: E402
from hoya_market_agents.webapp import outcome as outcome_module  # noqa: E402
from hoya_market_agents.webapp import pages, views  # noqa: E402
from hoya_market_agents.webapp.server import webapp_handler_class  # noqa: E402
from test_webapp import FakeQuotes, OutcomeFixture, write_run  # noqa: E402

RUN_ID = "20260801T020000Z-btc-aaaa11"


class MergedPageFixture(OutcomeFixture):
    """The merged page, with the sweep's clock and quote source in test hands.

    The page a reader opens is now the page that runs the expiry sweep, so a
    request here would otherwise read the wall clock and ask a real quote service
    for a price. Both are injected, which is what keeps these tests off the
    network and off the calendar.
    """

    def setUp(self):
        super().setUp()
        self.build_checked_handler()

    def build_checked_handler(self, now=None, quotes=None, **check_options):
        moment = self.AFTER_DUE if now is None else now
        self.check = outcome_module.OutcomeCheck(
            now=lambda: moment,
            quote=FakeQuotes() if quotes is None else quotes,
            log=self.log,
            **check_options
        )
        self.handler = webapp_handler_class(
            self.data_root,
            self.log,
            stream=self.stream,
            lock=self.lock,
            spawn=self.spawn,
            outcome_check=self.check,
        )
        return self.handler

    def merged(self, query=""):
        response = self.get("/history{}".format(query))
        self.assertEqual(200, response.status, response.body[:400])
        return response.body

    def judged(self, run_id, verdict):
        """One run whose verdict is already on disk, so a row has a result."""
        written = outcome_module.record_manual_outcome(
            self.data_root, run_id, verdict, now=self.AFTER_DUE
        )
        self.assertEqual(outcome_module.WRITTEN, written.state, written.message)
        rebuild_index(self.data_root)

    def result_rows(self, body):
        """The rows of the run list, one string each."""
        table = re.search(r'<h2 id="result-heading">.*?</table>', body, re.DOTALL)
        self.assertIsNotNone(table, "no run list on the page")
        rows = re.search(r"<tbody>(.*?)</tbody>", table.group(0), re.DOTALL)
        return re.findall(r"<tr>.*?</tr>", rows.group(1), re.DOTALL)

    def outcome_words(self, body):
        """The word in the result column of every listed run.

        The cell carries a mark and a word, so the word is its last token — which
        is also why 命中 and 未命中 are told apart here rather than by ``in``.
        """
        words = []
        for row in self.result_rows(body):
            cell = re.findall(r"<t[dh][^>]*>.*?</t[dh]>", row, re.DOTALL)[-1]
            words.append(re.sub(r"<[^>]+>", "", cell).split()[-1])
        return words


class OldStatisticsUrlTest(MergedPageFixture, unittest.TestCase):
    """``GET /stats`` is a bookmark, not a page: it sends the reader to
    ``/history``."""

    def test_the_old_url_redirects_to_the_merged_page(self):
        response = self.get("/stats")

        self.assertEqual(302, response.status)
        self.assertEqual("/history", response.headers["Location"])

    def test_the_redirect_carries_no_page_of_its_own(self):
        """FP direction: a 302 that also rendered a page would be a second copy
        of the very page this ticket merged away."""
        response = self.get("/stats")

        self.assertEqual("0", response.headers["Content-Length"])
        self.assertEqual("", response.body)

    def test_where_it_sends_the_reader_shows_both_halves(self):
        self.write_market_run()

        body = self.merged()

        self.assertIn("整體命中率", body)
        self.assertIn(RUN_ID, body)

    def test_the_old_url_is_not_a_second_form_target(self):
        """The form moved with the page; answering a submission here as well
        would be the second implementation the ticket forbids."""
        self.write_market_run()

        response = self.post("/stats", {"run_id": RUN_ID, "verdict": OUTCOME_HIT})

        self.assertEqual(404, response.status)
        run_dir = resolve_run_dir(self.data_root, RUN_ID)
        self.assertFalse((run_dir / OUTCOME_RECORD_NAME).is_file())


class OnePageShowsBothTest(MergedPageFixture, unittest.TestCase):
    """The statistics above, the runs below, and the form on the same page."""

    def three_judged_runs(self):
        """One hit, one miss, one that could not be checked — and their lights."""
        for index, (verdict, level) in enumerate(
            (
                (OUTCOME_HIT, "green"),
                (OUTCOME_MISS, "green"),
                (OUTCOME_UNVERIFIABLE, "blue"),
            )
        ):
            run_id = "2026080{}T020000Z-btc-aaaa1{}".format(index + 1, index)
            self.expired_run(
                run_id,
                "BTC 未來七天會不會漲 {}".format(index),
                assets=("BTC",),
                level=level,
            )
            self.judged(run_id, verdict)

    def test_the_hit_rate_and_the_run_list_are_on_the_same_page(self):
        self.three_judged_runs()

        body = self.merged()

        self.assertIn("整體命中率", body)
        self.assertIn("50.0%", body)
        self.assertIn("查詢結果", body)
        self.assertEqual(3, len(self.listed_run_ids(body)))

    def test_the_statistics_come_above_the_list(self):
        self.three_judged_runs()

        body = self.merged()

        self.assertLess(body.index("整體命中率"), body.index("查詢結果"))

    def test_each_light_still_gets_its_own_row_of_hit_rates(self):
        self.three_judged_runs()

        body = self.merged()

        self.assertIn("各燈號命中率", body)
        self.assertIn("綠燈", body)
        self.assertIn("藍燈", body)

    def test_the_statistics_say_they_are_not_narrowed_by_the_conditions(self):
        """The card counts the whole index and the list below does not, so the
        card says so: otherwise it reads as a hit rate for the rows on screen."""
        self.three_judged_runs()

        body = self.merged("?asset_class=tw_stock")

        self.assertIn(pages.WHOLE_INDEX_NOTE, body)
        self.assertIn("50.0%", body)
        self.assertEqual([], self.listed_run_ids(body))

    def test_every_row_carries_its_own_result(self):
        self.three_judged_runs()
        write_run(self.data_root, "20260805T020000Z-btc-aaaa15", "還沒對答案的一題")
        rebuild_index(self.data_root)

        words = self.outcome_words(self.merged())

        self.assertEqual(
            sorted(["命中", "未命中", "不可自動驗證", "待驗證"]), sorted(words)
        )

    def test_a_result_the_index_cannot_read_is_named_on_the_row(self):
        run_dir = write_run(self.data_root, RUN_ID, "紀錄壞掉的一題")
        (run_dir / OUTCOME_RECORD_NAME).write_text("{ 壞掉的", encoding="utf-8")
        rebuild_index(self.data_root)

        self.assertEqual(["紀錄無法讀取"], self.outcome_words(self.merged()))

    def test_the_manual_form_is_on_the_same_page_and_posts_to_it(self):
        self.write_market_run()

        body = self.merged()

        self.assertIn("人工輸入結果", body)
        self.assertIn('<form method="post" action="/history">', body)
        for control in ("run_id", "verdict", "note", "actual_price"):
            self.assertIn('for="{}"'.format(control), body)

    def test_every_control_on_the_merged_page_is_named_by_a_label(self):
        """The page grew controls — two ``<select>`` filters and the form — and a
        control a screen reader cannot name is a control nobody can use."""
        self.write_market_run()
        body = self.merged()
        controls = re.findall(r'<(?:input|select)[^>]*\bid="([^"]+)"', body)
        labelled = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', body))

        self.assertTrue(controls)
        self.assertEqual([], [name for name in controls if name not in labelled])

    def test_the_page_admits_it_can_write_a_run_artifact(self):
        """頁尾誠實: the merged page carries the form, so it may not claim to
        only read."""
        body = self.merged()

        self.assertIn(pages.HISTORY_FOOTER, body)
        self.assertNotIn(pages.READ_ONLY_FOOTER, body)


class AnIndexTheMergedPageCannotReadTest(MergedPageFixture, unittest.TestCase):
    """One explanation, and no hit rate invented beside it."""

    def test_the_page_explains_itself_once_instead_of_showing_zeroes(self):
        response = self.get("/history")

        self.assertEqual(200, response.status)
        self.assertIn("index-backfill", response.body)
        self.assertEqual(1, response.body.count('id="no-index-heading"'))
        self.assertNotIn("整體命中率", response.body)
        self.assertNotIn("各燈號命中率", response.body)

    def test_the_conditions_and_the_form_are_still_there_to_use(self):
        body = self.get("/history").body

        self.assertIn("查詢條件", body)
        self.assertIn("人工輸入結果", body)
        self.assertIn(pages.HISTORY_FOOTER, body)


class ARunWithNoReportTest(MergedPageFixture, unittest.TestCase):
    """Spec R1: a run that produced no report says so, in words, on the list."""

    def test_a_run_whose_report_was_never_written_says_so_in_chinese(self):
        write_run(self.data_root, RUN_ID, "沒有報告的一題", artifacts=())
        rebuild_index(self.data_root)

        body = self.merged()

        self.assertEqual([RUN_ID], self.listed_run_ids(body))
        self.assertIn(pages.NO_REPORT_STATUS, body)

    def test_a_run_that_did_produce_one_is_not_labelled_that_way(self):
        """FP direction: a page that said it of every run would prove nothing."""
        write_run(self.data_root, RUN_ID, "有報告的一題")
        rebuild_index(self.data_root)

        body = self.merged()

        self.assertEqual([RUN_ID], self.listed_run_ids(body))
        self.assertNotIn(pages.NO_REPORT_STATUS, body)
        self.assertIn(CONSENSUS_LABELS["consensus"], body)


class ManualEntryOnTheMergedPageTest(MergedPageFixture, unittest.TestCase):
    """The form moved to the merged page and behaves exactly as it did.

    What it may write is still one file for one run, once: ``outcome.json``. The
    assertions below are about the merge, so they check the page it answers on and
    that nothing else in the run directory moved; which submissions are accepted
    at all belongs to ``webapp.outcome`` and is pinned in ``tests/test_webapp.py``.
    """

    def fingerprint(self, run_dir):
        return {
            path.name: path.read_bytes()
            for path in sorted(Path(run_dir).iterdir())
            if path.is_file()
        }

    def test_a_submitted_verdict_is_written_to_the_runs_outcome_record(self):
        run_dir = self.write_market_run()

        response = self.post("/history", {"run_id": RUN_ID, "verdict": OUTCOME_MISS})

        self.assertEqual(200, response.status)
        self.assertEqual(OUTCOME_MISS, self.record(run_dir)["verdict"])

    def test_the_answer_is_the_merged_page_with_that_run_shown(self):
        self.write_market_run()

        body = self.post("/history", {"run_id": RUN_ID, "verdict": OUTCOME_MISS}).body

        self.assertIn("已記錄", body)
        self.assertIn("整體命中率", body)
        self.assertEqual(["未命中"], self.outcome_words(body))

    def test_nothing_else_in_the_run_directory_was_touched(self):
        run_dir = self.write_market_run()
        before = self.fingerprint(run_dir)

        self.post("/history", {"run_id": RUN_ID, "verdict": OUTCOME_MISS})
        after = self.fingerprint(run_dir)

        self.assertEqual([OUTCOME_RECORD_NAME], sorted(set(after) - set(before)))
        self.assertEqual(before, {name: after[name] for name in before})

    def test_a_second_submission_is_a_conflict_and_the_first_record_stands(self):
        run_dir = self.write_market_run()
        self.post("/history", {"run_id": RUN_ID, "verdict": OUTCOME_HIT})

        response = self.post("/history", {"run_id": RUN_ID, "verdict": OUTCOME_MISS})

        self.assertEqual(409, response.status)
        self.assertEqual(OUTCOME_HIT, self.record(run_dir)["verdict"])
        self.assertIn("整體命中率", response.body)


class OneMergedPageInTheNavigationTest(MergedPageFixture, unittest.TestCase):
    """驗收 9: nothing on this site points at the page that went away."""

    PAGES = ("/", "/history", "/settings", "/run/" + RUN_ID, "/does-not-exist")

    def setUp(self):
        super().setUp()
        write_run(self.data_root, RUN_ID, "BTC 未來七天會不會漲")
        rebuild_index(self.data_root)

    def test_no_page_links_to_the_retired_statistics_url(self):
        for path in self.PAGES:
            body = self.get(path).body
            self.assertNotIn('href="/stats"', body, path)
            self.assertNotIn('action="/stats"', body, path)

    def test_every_page_names_the_merged_page_in_its_navigation(self):
        for path in self.PAGES:
            nav = re.search(
                r'<nav class="page-tabs".*?</nav>', self.get(path).body, re.DOTALL
            )
            self.assertIsNotNone(nav, path)
            self.assertIn('<a href="/history"', nav.group(0), path)
            self.assertEqual(1, nav.group(0).count('href="/history"'), path)

    def test_the_merged_page_marks_its_own_tab_as_the_current_one(self):
        nav = re.search(
            r'<nav class="page-tabs".*?</nav>', self.merged(), re.DOTALL
        ).group(0)
        current = re.search(r'<a[^>]*aria-current="page"[^>]*>([^<]+)</a>', nav)

        self.assertEqual(pages.PAGE_TITLE_HISTORY, current.group(1))


class EverythingOnThePageIsChineseTest(MergedPageFixture, unittest.TestCase):
    """Spec R7 and 驗收 5: no stored key reaches the reader as itself.

    The check is on what a reader sees. A ``value=""`` and a class name are how
    the browser and the query talk to each other — the filter has to send
    ``tw_stock`` back for the query to mean anything — so the text is taken with
    every tag, and therefore every attribute, removed.
    """

    STORED_KEYS = (
        "tw_stock",
        "us_stock",
        "crypto",
        "open",
        "green",
        "blue",
        "yellow",
        "orange",
        "red",
        "hit",
        "miss",
        "pending",
        "unverifiable",
        "unreadable",
        "consensus",
        "no_consensus",
    )

    def setUp(self):
        super().setUp()
        self.expired_run(RUN_ID, "BTC 未來七天會不會漲", assets=("BTC",), level="green")
        write_run(
            self.data_root,
            "20260802T020000Z-2330-bbbb22",
            "2330 未來七天會不會漲",
            assets=("2330",),
            asset_class="tw_stock",
            level="blue",
        )
        rebuild_index(self.data_root)

    def visible(self, body):
        without_style = re.sub(r"<style.*?</style>", " ", body, flags=re.DOTALL)
        return re.sub(r"<[^>]*>", " ", without_style)

    def test_no_stored_key_reaches_the_visible_text(self):
        text = self.visible(self.merged())

        for key in self.STORED_KEYS:
            self.assertIsNone(
                re.search(r"(?<![A-Za-z_]){}(?![A-Za-z_])".format(key), text), key
            )

    def test_the_scan_would_notice_one(self):
        """Discrimination: the reading above has to be able to fail."""
        text = self.visible("<p>資產類別：tw_stock</p>")

        self.assertIsNotNone(re.search(r"(?<![A-Za-z_])tw_stock(?![A-Za-z_])", text))

    def test_no_suggestion_list_puts_a_stored_key_on_screen(self):
        """A ``<datalist>`` shows its own *values*, so a stored key in one is a
        key on screen — which the tag-stripped reading above cannot see, because
        the key is an attribute. This is why the two enum filters are ``<select>``
        elements: the only suggestion list left on this page offers run ids, which
        are data rather than枚舉 (A5 exempts them by name)."""
        body = self.merged()
        lists = re.findall(r"<datalist[^>]*>.*?</datalist>", body, re.DOTALL)
        offered = [
            value for one in lists for value in re.findall(r'value="([^"]*)"', one)
        ]

        self.assertEqual(sorted(self.indexed_run_ids()), sorted(offered))
        for key in self.STORED_KEYS:
            self.assertNotIn(key, offered)

    def indexed_run_ids(self):
        """The runs the form may take, which is what the one datalist offers."""
        return [
            row["run_id"]
            for row in views.history_data(
                self.data_root, {}, outcome_check=self.check
            )["pending_runs"]
        ]

    def test_the_asset_class_filter_offers_words_and_sends_keys(self):
        body = self.merged()

        for asset_class in ASSET_CLASSES:
            self.assertIn(
                '<option value="{}">{}</option>'.format(
                    asset_class, pages.asset_class_label(asset_class)
                ),
                body,
                asset_class,
            )

    def test_the_light_filter_offers_words_and_sends_keys(self):
        body = self.merged()

        for level in CONFIDENCE_LEVELS:
            self.assertIn(
                '<option value="{}">{}</option>'.format(
                    level, pages.CONFIDENCE_WORDS[level]
                ),
                body,
                level,
            )

    def test_a_class_no_authority_declares_still_comes_back_in_the_form(self):
        """The documented handling for a value no authority names: it is shown as
        it was recorded rather than dropped, so the URL's question is the one the
        page answers."""
        body = self.merged("?asset_class=commodity")

        self.assertIn('<option value="commodity" selected>commodity</option>', body)
        self.assertEqual([], self.listed_run_ids(body))


class FiltersSurviveTheMergeTest(MergedPageFixture, unittest.TestCase):
    """驗收 8: the conditions behave as they did, and old URLs still work."""

    def setUp(self):
        super().setUp()
        write_run(
            self.data_root, RUN_ID, "BTC 未來七天會不會漲",
            assets=("BTC",), asset_class=ASSET_CLASS_CRYPTO, level="green",
        )
        write_run(
            self.data_root, "20260802T020000Z-2330-bbbb22", "2330 未來七天會不會漲",
            assets=("2330",), asset_class="tw_stock", level="blue",
        )
        rebuild_index(self.data_root)

    def test_a_url_from_before_the_merge_still_narrows_the_list(self):
        body = self.merged("?asset_class=crypto&confidence=green")

        self.assertEqual([RUN_ID], self.listed_run_ids(body))

    def test_a_keyword_from_before_the_merge_still_works(self):
        body = self.merged("?keyword=2330")

        self.assertEqual(["20260802T020000Z-2330-bbbb22"], self.listed_run_ids(body))

    def test_clearing_the_conditions_is_still_one_click_to_the_whole_list(self):
        clear = re.search(
            r'<a class="secondary" href="([^"]*)">清除條件</a>', self.merged()
        )

        self.assertEqual("/history", clear.group(1))
        self.assertEqual(2, len(self.listed_run_ids(self.merged())))

    def test_a_cap_the_query_refuses_is_still_reported_and_not_used(self):
        body = self.merged("?limit=-1")

        self.assertIn("-1", self.complaints(body))
        self.assertEqual(2, len(self.listed_run_ids(body)))

    def test_the_conditions_do_not_hide_the_form_or_the_statistics(self):
        body = self.merged("?asset_class=crypto")

        self.assertIn("整體命中率", body)
        self.assertIn("人工輸入結果", body)


class SeatNamesAgreeAcrossThePagesTest(MergedPageFixture, unittest.TestCase):
    """裁定五: one seat of one run has one name, whichever page shows it.

    The detail page read a module-level view of the open set whatever the question
    was, while the room reads the roster's port for the run's own asset class
    (ADR 0006). So a 台股 run was named twice, differently, depending on which page
    a reader opened. The expected values come from
    :mod:`hoya_market_agents.seats` rather than being spelled out, because the
    roster is the authority and a name edited there must not need a test edited
    too.
    """

    SEAT_ROW = re.compile(
        r'<th scope="row">([^<]*)<span class="hint">([^<]*)</span></th>'
    )
    SEAT_CARD = re.compile(
        r'data-seat-id="([^"]+)".*?<small>[^｜]*｜([^<]*)</small>', re.DOTALL
    )

    def a_run_of(self, asset_class, run_id=RUN_ID, question="2330 未來七天會不會漲"):
        write_run(
            self.data_root, run_id, question, assets=("2330",),
            asset_class=asset_class,
        )
        rebuild_index(self.data_root)
        return run_id

    def detail_names(self, run_id=RUN_ID):
        body = self.get("/run/{}".format(run_id)).body
        return {seat_id: label for label, seat_id in self.SEAT_ROW.findall(body)}

    def room_names(self, run_id=RUN_ID):
        body = self.get("/?run={}".format(run_id)).body
        return dict(self.SEAT_CARD.findall(body))

    def test_a_stock_runs_detail_page_names_its_seats_from_the_stock_set(self):
        self.a_run_of(ASSET_CLASS_TW_STOCK)

        self.assertEqual(
            dict(seat_display_names(ASSET_CLASS_TW_STOCK)), self.detail_names()
        )

    def test_the_detail_page_and_the_debate_room_name_them_identically(self):
        self.a_run_of(ASSET_CLASS_TW_STOCK)

        detail = self.detail_names()
        room = self.room_names()

        self.assertEqual(7, len(detail))
        self.assertEqual(room, detail)

    def test_no_name_from_another_set_reaches_the_detail_page(self):
        """FP direction: the two assertions above would both pass on a page that
        printed every set."""
        stock = set(seat_display_names(ASSET_CLASS_TW_STOCK).values())
        crypto = set(seat_display_names(ASSET_CLASS_CRYPTO).values())
        self.assertTrue(crypto - stock, "the roster no longer discriminates")
        self.a_run_of(ASSET_CLASS_TW_STOCK)

        body = self.get("/run/{}".format(RUN_ID)).body

        for name in crypto - stock:
            self.assertNotIn(escape(name), body, name)

    def test_a_crypto_runs_detail_page_reads_the_crypto_set(self):
        self.a_run_of(ASSET_CLASS_CRYPTO, question="BTC 未來七天會不會漲")

        self.assertEqual(
            dict(seat_display_names(ASSET_CLASS_CRYPTO)), self.detail_names()
        )

    def test_a_run_recorded_before_the_field_existed_reads_the_open_set(self):
        """A run whose ``question.json`` never carried an asset class is the one
        case the port has to fall back for, and it falls back to a filled set."""
        self.a_run_of(None, question="這一題")

        self.assertEqual(dict(seat_display_names(None)), self.detail_names())


class TheWebAppHoldsNoSqlTest(unittest.TestCase):
    """零 SQL: the web app asks ``run_index``, it never writes a statement.

    A scan rather than a rule somebody remembers — the same shape
    ``tests/test_webapp.py`` uses to keep the quote client out of the research
    pipeline.
    """

    STATEMENT = re.compile(
        r"\b(SELECT|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM"
        r"|CREATE\s+TABLE|FROM\s+runs|sqlite3)\b"
    )

    def modules(self):
        return sorted(Path(pages.__file__).parent.glob("*.py"))

    def test_no_module_in_the_web_app_holds_a_sql_statement(self):
        self.assertTrue(self.modules())
        for module in self.modules():
            found = self.STATEMENT.search(module.read_text(encoding="utf-8"))
            self.assertIsNone(found, "{}: {}".format(module.name, found))

    def test_the_scan_would_notice_one(self):
        """Discrimination: the pattern has to be able to fail."""
        self.assertIsNotNone(
            self.STATEMENT.search('rows = conn.execute("SELECT run_id FROM runs")')
        )


class RenderedMergedPageTest(MergedPageFixture, unittest.TestCase):
    """The substitute for the screenshot the ticket asks for.

    No browser exists in this environment, so the page is rendered, written to a
    file a reviewer can open, and its load-bearing parts are asserted here instead
    of eyeballed.
    """

    def test_the_kept_page_holds_both_halves_and_says_it_writes(self):
        for index, (verdict, level) in enumerate(
            (
                (OUTCOME_HIT, "green"),
                (OUTCOME_HIT, "green"),
                (OUTCOME_MISS, "green"),
                (OUTCOME_UNVERIFIABLE, "blue"),
            )
        ):
            run_id = "2026080{}T020000Z-btc-aaaa1{}".format(index + 1, index)
            self.expired_run(
                run_id, "BTC 未來七天會不會漲 {}".format(index), assets=("BTC",),
                level=level,
            )
            self.judged(run_id, verdict)
        write_run(
            self.data_root, "20260806T020000Z-2330-eeee55", "2330 沒有產出報告的一題",
            assets=("2330",), asset_class="tw_stock", artifacts=(),
        )
        rebuild_index(self.data_root)

        body = self.merged()
        kept = Path(tempfile.gettempdir()) / "t04-history-merge.html"
        kept.write_text(body, encoding="utf-8")

        self.assertTrue(body.startswith("<!doctype html>"))
        self.assertIn(
            "<title>{}・{}</title>".format(pages.PAGE_TITLE_HISTORY, pages.SITE_TITLE),
            body,
        )
        self.assertIn("66.7%", body)
        self.assertIn("各燈號命中率", body)
        self.assertIn("查詢條件", body)
        self.assertIn("人工輸入結果", body)
        self.assertIn(pages.NO_REPORT_STATUS, body)
        self.assertIn(pages.HISTORY_FOOTER, body)
        self.assertEqual(5, len(self.listed_run_ids(body)))


class OneVersionOfTheIndexPerPageTest(MergedPageFixture, unittest.TestCase):
    """The page's three index reads are one version of the index, or say so.

    The index is a file with writers outside this request: the launch child indexes
    a finishing run, any visit to this page records what expired, a submission
    records one by hand — and the server is a ``ThreadingHTTPServer``, so two of
    those can be in flight at once. Three reads straddling one commit is a page
    whose card counts a verdict its own row does not show.
    """

    # Enough waiting runs for the worst case this class drives: three attempts,
    # two index reads each, two commits per read.
    WAITING_RUNS = 12

    def setUp(self):
        super().setUp()
        self.waiting = []
        for index in range(self.WAITING_RUNS):
            run_id = "20260801T02{:02d}00Z-btc-cccc{:02d}".format(index, index)
            self.write_market_run(
                run_id=run_id, question="第 {} 題 BTC 會不會漲".format(index)
            )
            self.waiting.append(run_id)
        rebuild_index(self.data_root)

    def writer_between_reads(self, times, per_read=1):
        """A real writer committing to the index between this page's reads.

        Each commit records a verdict for one more waiting run: a real
        ``outcome.json`` and a real index upsert, not a touched file. ``times``
        bounds how many land in total and ``per_read`` how many land together, so
        one test can have a single interleaving, another one on every read, and
        another more than one inside a single read window.
        """
        real = views.query_runs
        self.reads = 0
        self.writes = []
        allowed = min(times, len(self.waiting))

        def query_runs(*args, **options):
            rows = real(*args, **options)
            self.reads += 1
            for _ in range(per_read):
                if len(self.writes) >= allowed:
                    break
                run_id = self.waiting[len(self.writes)]
                self.writes.append(run_id)
                written = outcome_module.record_manual_outcome(
                    self.data_root, run_id, OUTCOME_HIT, now=self.AFTER_DUE
                )
                self.assertEqual(
                    outcome_module.WRITTEN, written.state, written.message
                )
            return rows

        return query_runs

    def card_counts(self, body):
        """The counts the hit-rate card shows, keyed by the word above them."""
        card = re.search(r'<dl class="stat-row">(.*?)</dl>', body, re.DOTALL).group(1)
        return {
            word: int(count)
            for word, count in re.findall(
                r"<dt>([^<]*)<span[^>]*>[^<]*</span></dt><dd[^>]*>(\d+)</dd>", card
            )
        }

    def caveat(self):
        return views.MIXED_READ_CAVEAT.format(views.INDEX_READ_ATTEMPTS)

    def test_a_commit_landing_between_two_reads_is_not_drawn_as_a_mixed_page(self):
        with mock.patch.object(views, "query_runs", self.writer_between_reads(1)):
            body = self.merged()

        counts = self.card_counts(body)
        words = self.outcome_words(body)
        self.assertEqual(1, len(self.writes))
        self.assertEqual(counts["命中"], words.count("命中"), (counts, words))
        self.assertEqual(counts["待驗證"], words.count("待驗證"), (counts, words))
        self.assertEqual(len(words), sum(counts.values()))
        self.assertNotIn(self.caveat(), body)

    def test_a_quiet_index_is_read_once_rather_than_over_and_over(self):
        """FP direction: a page that always re-read would pass the test above and
        pay for it on every visit.

        Three reads, and the number is the point: the page's own window asks two
        of them — the rows, and the runs a verdict may be entered for — and asks
        them **once**, which is what an index nobody is writing has to cost. The
        third is the header's, which since Spec R-002 asks which run the report
        tabs open (:func:`~hoya_market_agents.webapp.views.latest_report_run`).
        That one is deliberately outside the window: it is a different question,
        and a commit landing beside it can only change which run the tabs open,
        never make the card disagree with the rows below it.
        """
        with mock.patch.object(views, "query_runs", self.writer_between_reads(0)):
            body = self.merged()

        self.assertEqual(3, self.reads)
        self.assertNotIn(self.caveat(), body)

    def test_an_index_written_on_every_read_is_still_shown_and_says_so(self):
        """A Data Root being rewritten continuously is a page worth showing with a
        caveat, not an error page claiming the index cannot be read."""
        with mock.patch.object(
            views, "query_runs", self.writer_between_reads(len(self.waiting))
        ):
            response = self.get("/history")

        self.assertEqual(200, response.status)
        self.assertIn(self.caveat(), response.body)
        self.assertIn("查詢結果", response.body)
        self.assertIn("整體命中率", response.body)

    def test_more_than_one_commit_can_land_inside_one_read_window(self):
        """The gap is not one write, so nothing may say it is.

        Two commits land together on every read, which is what the stamp cannot
        tell apart from one: it knows the index changed, never how many times. The
        card and the list therefore end up more than one run apart, and the page
        still has to be a page.
        """
        with mock.patch.object(
            views,
            "query_runs",
            self.writer_between_reads(self.WAITING_RUNS, per_read=2),
        ):
            response = self.get("/history")

        counts = self.card_counts(response.body)
        words = self.outcome_words(response.body)
        gap = counts["命中"] - words.count("命中")
        self.assertEqual(200, response.status)
        self.assertGreater(len(self.writes), self.reads, self.writes)
        self.assertGreater(gap, 1, (counts, words))
        self.assertIn(self.caveat(), response.body)
        self.assertIn("查詢結果", response.body)

    def test_the_caveat_bounds_no_gap_and_promises_no_clean_retry(self):
        """What the version stamp actually knows is "this changed", so a sentence
        naming a size, or promising the next read is quieter, says more than the
        page can support. These two phrasings did both."""
        caveat = self.caveat()

        for over_claim in ("相差一次寫入", "即可取得一致"):
            self.assertNotIn(over_claim, caveat, over_claim)


class TheListAndTheTotalsReadTheColumnAlikeTest(
    MergedPageFixture, unittest.TestCase
):
    """One reading of the ``outcome`` column, proved rather than shared.

    ``run_index.outcome_summary`` counts by its own reading of that column and the
    list names each row by :func:`views.row_view`. The rule cannot be *shared* as
    one function without adding one to ``run_index``, whose public interface this
    ticket may not change, so the two are pinned equal instead — over every state a
    stored record can be in, on real rows.
    """

    def setUp(self):
        super().setUp()
        for index, verdict in enumerate(
            (OUTCOME_HIT, OUTCOME_MISS, OUTCOME_UNVERIFIABLE)
        ):
            run_id = "2026080{}T020000Z-btc-bbbb2{}".format(index + 1, index)
            self.expired_run(run_id, "第 {} 題".format(index), assets=("BTC",))
            self.judged(run_id, verdict)
        broken = write_run(self.data_root, RUN_ID, "紀錄壞掉的一題")
        (broken / OUTCOME_RECORD_NAME).write_text("{ 壞掉的", encoding="utf-8")
        write_run(self.data_root, "20260807T020000Z-btc-ffff66", "還沒對答案的一題")
        rebuild_index(self.data_root)

    def test_every_stored_state_is_counted_where_its_own_row_says_it_is(self):
        runs_root = RunStore(self.data_root).runs_root
        totals = outcome_summary(self.data_root)["totals"]

        counted = Counter(
            views.row_view(row, runs_root)["outcome_state"]
            for row in query_runs(self.data_root)
        )

        for state in OUTCOME_STATES:
            self.assertEqual(totals[state], counted[state], state)
        # Not vacuous: every state a stored record can be in is one of these rows.
        self.assertEqual({state: 1 for state in OUTCOME_STATES}, dict(counted))


class TheRowCapIsBoundedTest(MergedPageFixture, unittest.TestCase):
    """A cap a user types is not a cap this page will draw.

    Every listed row costs one look at the filesystem (its report), so a cap
    nobody bounds is a page a URL can make arbitrarily expensive.
    """

    def test_a_cap_above_the_bound_is_brought_down_and_said_on_the_page(self):
        body = self.merged("?limit=100000")

        self.assertIn(str(views.MAX_ROW_LIMIT), self.complaints(body))

    def test_the_number_that_reaches_the_query_is_the_bound(self):
        filters, problems = views.parse_filters({"limit": ["100000"]})

        self.assertEqual(views.MAX_ROW_LIMIT, filters["limit"])
        self.assertEqual(1, len(problems))

    def test_the_bound_itself_is_not_a_complaint(self):
        filters, problems = views.parse_filters(
            {"limit": [str(views.MAX_ROW_LIMIT)]}
        )

        self.assertEqual(views.MAX_ROW_LIMIT, filters["limit"])
        self.assertEqual([], problems)

    def test_a_cap_a_reader_would_actually_type_is_untouched(self):
        filters, problems = views.parse_filters({"limit": ["7"]})

        self.assertEqual(7, filters["limit"])
        self.assertEqual([], problems)


class RowShapingTest(unittest.TestCase):
    """The merged page's shaping seam, fed one row at a time.

    :func:`views.row_view` takes what ``query_runs`` hands back and returns what
    the list shows, so the words a reader sees can be asserted without an index,
    a Data Root or a socket.
    """

    def row(self, **overrides):
        row = {
            "run_id": RUN_ID,
            "run_date": "2026-08-01",
            "question": "BTC 未來七天會不會漲",
            "slug": "btc",
            "asset_class": ASSET_CLASS_CRYPTO,
            "assets": ["BTC"],
            "question_type": "market_direction",
            "confidence_level": "green",
            "adopted_stance": "bullish",
            "tally": {"bullish": 6, "bearish": 1},
            "consensus_status": "consensus",
            "report_path": "2026-08-01/0200-btc-aaaa11/report.html",
            "outcome": OUTCOME_HIT,
        }
        row.update(overrides)
        return row

    def view(self, runs_root=None, **overrides):
        return views.row_view(
            self.row(**overrides), Path("/nowhere") if runs_root is None else runs_root
        )

    def test_a_recorded_verdict_is_the_state_the_row_is_in(self):
        self.assertEqual(OUTCOME_HIT, self.view()["outcome_state"])

    def test_nothing_recorded_reads_as_waiting_rather_than_as_a_verdict(self):
        self.assertEqual(OUTCOME_PENDING, self.view(outcome=None)["outcome_state"])

    def test_a_verdict_outside_the_closed_set_reads_as_an_unreadable_record(self):
        self.assertEqual(
            OUTCOME_UNREADABLE, self.view(outcome="probably")["outcome_state"]
        )

    def test_the_consensus_status_is_named_in_the_authoritys_words(self):
        self.assertEqual(
            CONSENSUS_LABELS["no_consensus"],
            self.view(consensus_status="no_consensus")["consensus_label"],
        )

    def test_a_report_that_is_not_on_disk_is_not_reported_as_available(self):
        self.assertFalse(self.view()["report_available"])

    def test_a_row_naming_no_report_at_all_is_not_asked_for_one(self):
        self.assertFalse(self.view(report_path=None)["report_available"])

    def test_a_report_that_is_there_is_found(self):
        root = Path(tempfile.mkdtemp(prefix="t04-rows-"))
        self.addCleanup(shutil.rmtree, root, True)
        report = root / "2026-08-01" / "0200-btc-aaaa11" / "report.html"
        report.parent.mkdir(parents=True)
        report.write_text("<!doctype html>", encoding="utf-8")

        self.assertTrue(self.view(runs_root=root)["report_available"])


if __name__ == "__main__":
    unittest.main()
