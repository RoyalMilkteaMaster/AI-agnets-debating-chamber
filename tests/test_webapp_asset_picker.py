"""Ticket 05: the ask bar is a menu, and the menu is what the run is about.

What this file pins is the **path from the form to the launcher**, and it pins it
end to end rather than in halves: the submission becomes an argument list, and
that same argument list — not a retyped copy of it — becomes the launcher's
keyword arguments. A flag that stopped being passed anywhere along the way fails
here.

The reason the ticket exists is that the run's subject used to be inferred from
the question's prose. It is now stated: the market comes from the menu and the
targets from that market's own box, and
:func:`~hoya_market_agents.question.inspect_question` — the seam that already
accepts a caller's own answer — is handed both. So the test that matters most in
this file is the one where the wording says one market and the menu says
another, and the run is about the menu's.

The fixtures come from ``tests/test_webapp.py`` rather than being copied: a
second ``write_run`` would be a second definition of what a finished run looks
like, and the two would drift.

**Ticket 08 (Spec R-006) narrowed the menu to the markets.** Which classes the
form offers is now ``config/market_scopes.json``'s three, not the four
``question.ASSET_CLASSES`` declares — so the tests that walk the form walk the
markets, and the ones that walk ``ASSET_CLASSES`` are the ones making a
statement about the difference. The retirement stops at the form: the launcher,
the intake, the profile sets and every stored open-question run are untouched,
which ``AStoredOpenQuestionRunIsStillReadableTest`` is here to keep true.
"""

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from html import escape
from itertools import dropwhile
from pathlib import Path
from unittest import mock

# So this module can be named either way: ``discover -s tests`` puts this
# directory on the path itself, ``python3 -m unittest
# tests.test_webapp_asset_picker`` from the Code Root does not.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hoya_market_agents import prompt_builder  # noqa: E402
from hoya_market_agents.prompt_builder import market_scopes  # noqa: E402
from hoya_market_agents.question import (  # noqa: E402
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_OPEN,
    ASSET_CLASS_TW_STOCK,
    ASSET_CLASS_US_STOCK,
    ASSET_CLASSES,
    inspect_question,
)
from hoya_market_agents import run_index  # noqa: E402
from hoya_market_agents.run_index import (  # noqa: E402
    RunIndexError,
    query_runs,
    rebuild_index,
)
from hoya_market_agents.webapp import launch as launch_module  # noqa: E402
from hoya_market_agents.webapp import live, pages, views  # noqa: E402
from test_webapp import PageFixture, write_run  # noqa: E402

# A question whose *wording* is about the crypto market. Every test that submits
# it picks a different market from the menu, which is what makes the menu's
# answer the observable one.
CRYPTO_WORDED_QUESTION = "加密貨幣與 BTC 未來七天會不會漲"


class AskFixture(PageFixture):
    """One submission of the ask bar, and the child command it recorded."""

    QUESTION = "2330 未來七天會不會漲"

    def submit(
        self,
        question=None,
        asset_class=ASSET_CLASS_TW_STOCK,
        target="2330",
        **extra
    ):
        """Post the ask bar the way a browser would, field names and all."""
        fields = {
            launch_module.QUESTION_FIELD: (
                self.QUESTION if question is None else question
            )
        }
        if asset_class is not None:
            fields[launch_module.ASSET_CLASS_FIELD] = asset_class
        if asset_class and target is not None:
            fields[launch_module.target_field(asset_class)] = target
        fields.update(extra)
        return self.post("/launch", fields)

    def child_launch(self):
        """Run the recorded child command against a fake launcher, and report it.

        The argument list is the one the web app handed the spawn seam; nothing
        is rebuilt here. What comes back is what
        :func:`~hoya_market_agents.launcher.run_launch` was actually called
        with.

        **How the interpreter is told to get into the module is not asserted.**
        Whatever prefix starts it — ``-c``, ``-m``, a wrapper — the arguments
        after it are what :func:`~hoya_market_agents.webapp.launch.main` reads, so
        this walks past the prefix instead of pinning it. That the child is *this*
        interpreter is a claim worth keeping, and it is made without depending on
        where in the list it appears.
        """
        self.assertEqual(1, len(self.spawned), "no launch was started")
        args, _options = self.spawned[0]
        self.assertIn(sys.executable, args, "the child is not this interpreter")
        arguments = list(dropwhile(lambda item: not item.startswith("--"), args))
        self.assertTrue(arguments, args)
        handed = {}

        def fake_launch(question, data_root, **keywords):
            handed.update(keywords, question=question, data_root=data_root)
            return 0

        self.assertEqual(0, launch_module.main(arguments, launch=fake_launch))
        return handed


class MenuDecidesTheRunTest(AskFixture, unittest.TestCase):
    """The menu's answer reaches the launcher, and the wording does not."""

    def setUp(self):
        super().setUp()
        self.write_certificate()

    def test_a_submission_from_the_menu_starts_one_run(self):
        response = self.submit()

        self.assertEqual(303, response.status)
        self.assertEqual(1, len(self.spawned))

    def test_the_launcher_is_told_the_market_the_menu_says(self):
        """The headline case: the wording says crypto, the menu says 台股."""
        self.submit(
            question=CRYPTO_WORDED_QUESTION,
            asset_class=ASSET_CLASS_TW_STOCK,
            target="2330",
        )

        self.assertEqual(ASSET_CLASS_TW_STOCK, self.child_launch()["asset_class"])

    def test_the_launcher_is_told_the_target_the_menu_says(self):
        self.submit(
            question=CRYPTO_WORDED_QUESTION,
            asset_class=ASSET_CLASS_TW_STOCK,
            target="2330",
        )

        self.assertEqual(["2330"], self.child_launch()["assets"])

    def test_the_wording_still_reaches_the_run_verbatim(self):
        """FP direction: dropping the question would also pass the two above."""
        self.submit(question=CRYPTO_WORDED_QUESTION, target="2330")

        self.assertEqual(CRYPTO_WORDED_QUESTION, self.child_launch()["question"])

    def test_the_run_is_started_in_this_data_root(self):
        self.submit()

        self.assertEqual(str(self.data_root), str(self.child_launch()["data_root"]))

    def test_a_second_target_in_the_same_box_is_one_more_target(self):
        """The box takes a list because the seam does: a comparison run is two."""
        self.submit(asset_class=ASSET_CLASS_US_STOCK, target="AAPL, NVDA")

        self.assertEqual(["AAPL", "NVDA"], self.child_launch()["assets"])

    def test_a_target_reaches_the_launcher_as_the_user_typed_it(self):
        """Spelling is not this module's to decide.

        ``normalize_asset`` is the authority's, and the authority runs its shape
        check on the text *as given* before normalising — so a web app that
        upper-cased first would be handing the intake a target the user never
        wrote. What crosses the seam is what was typed; the one spelling the rest
        of the system stores is settled behind it.
        """
        self.submit(asset_class=ASSET_CLASS_US_STOCK, target="aapl")

        self.assertEqual(["aapl"], self.child_launch()["assets"])

    def test_the_child_reports_what_the_launcher_returned(self):
        """The exit code is the launch's, so the room can say how it ended."""
        self.submit()
        args, _options = self.spawned[0]
        arguments = list(dropwhile(lambda item: not item.startswith("--"), args))

        self.assertEqual(2, launch_module.main(arguments, launch=lambda *a, **k: 2))

    def test_the_launcher_is_never_left_to_read_the_wording(self):
        """``None`` would mean "read the text"; an empty list would mean "none".

        Neither is what a menu submission is, so both are wrong answers here.
        """
        self.submit()
        handed = self.child_launch()

        self.assertIsNotNone(handed["assets"])
        self.assertIsNotNone(handed["asset_class"])


class TheChildCommandReallyRunsTest(unittest.TestCase):
    """The one thing an injected launcher cannot prove: the command itself works.

    Everything else about the child is asserted in-process. What this adds is that
    the argument list handed to :class:`subprocess.Popen` — whatever shape
    :func:`~hoya_market_agents.webapp.launch.child_command` gives it — starts an
    interpreter that imports this package, parses those flags, reaches the
    launcher, and says nothing alarming on the way. The last part is not a
    detail: an invocation that made ``runpy`` warn about executing a module twice
    would put that warning at the head of every launch log, and no assertion on
    the command's spelling would notice.

    **It starts no research.** The Data Root has no READY certificate, so the
    launcher refuses at intake with its own exit code and never creates a run
    directory — which the empty directory afterwards is here to show.
    """

    def test_the_command_reaches_the_launcher_and_refuses_with_no_certificate(self):
        code_root = Path(live.__file__).resolve().parents[2]
        request = launch_module.LaunchRequest(
            "2330 未來七天會不會漲", ASSET_CLASS_TW_STOCK, ("2330",)
        )
        with tempfile.TemporaryDirectory() as data_root:
            completed = subprocess.run(
                launch_module.child_command(data_root, request),
                cwd=str(code_root),
                capture_output=True,
                text=True,
            )
            left_behind = sorted(path.name for path in Path(data_root).iterdir())

        self.assertEqual(2, completed.returncode, completed.stderr)
        self.assertIn("READY", completed.stderr)
        self.assertEqual([], left_behind)
        self.assertNotIn("RuntimeWarning", completed.stderr)

    def test_the_command_refuses_a_call_that_states_no_market(self):
        """FP direction: the flags are really parsed, not merely accepted.

        Built by taking the real command apart — the market's argument removed
        and nothing else touched — so this stays true however the command is
        spelled.
        """
        code_root = Path(live.__file__).resolve().parents[2]
        request = launch_module.LaunchRequest(
            "2330 未來七天會不會漲", ASSET_CLASS_TW_STOCK, ("2330",)
        )
        with tempfile.TemporaryDirectory() as data_root:
            completed = subprocess.run(
                [
                    argument
                    for argument in launch_module.child_command(data_root, request)
                    if launch_module.ASSET_CLASS_ARGUMENT not in argument
                ],
                cwd=str(code_root),
                capture_output=True,
                text=True,
            )

        self.assertEqual(2, completed.returncode)
        self.assertIn(launch_module.ASSET_CLASS_ARGUMENT, completed.stderr)


class TheFormRefusesBeforeAnythingStartsTest(AskFixture, unittest.TestCase):
    """Every refusal is a Chinese sentence on the form and zero processes."""

    def setUp(self):
        super().setUp()
        self.write_certificate()

    def refused(self, **submission):
        response = self.submit(**submission)

        self.assertEqual(200, response.status)
        self.assertEqual([], self.spawned)
        self.assertNotIn("Traceback", response.body)
        return response.body

    def test_a_submission_with_no_market_chosen_says_to_choose_one(self):
        body = self.refused(asset_class="")

        self.assertIn("資產類別", body)

    def test_a_submission_with_no_market_field_at_all_is_refused_too(self):
        """A hand-made ``POST`` is a submission too, and fails closed."""
        self.refused(asset_class=None)

    def test_a_submission_with_an_empty_target_says_to_name_one(self):
        body = self.refused(target="   ")

        self.assertIn("標的", body)

    def test_a_blank_question_is_still_refused(self):
        body = self.refused(question="   ")

        self.assertIn("題目", body)

    def test_a_target_the_intake_refuses_is_refused_in_the_intakes_own_words(self):
        """The sentence is the authority's, quoted, not a second opinion."""
        expected = self.intake_sentence("台積電")

        body = self.refused(target="台積電")

        self.assertIn(expected, body)

    def test_a_market_no_authority_declares_is_refused_in_its_own_words(self):
        expected = self.intake_sentence("2330", asset_class="commodity")

        body = self.refused(asset_class="commodity", target="2330")

        self.assertIn(expected, body)

    def test_an_undeclared_market_is_answered_about_the_market_not_the_box(self):
        """A hand-made ``POST`` names a market nothing declares and fills no box.

        Both are wrong, and only one of them is the reader's to fix: telling them
        to type a target would be advice about the field that is not the problem.
        """
        body = self.refused(asset_class="commodity", target=None)

        self.assertIn(escape("commodity", quote=True), body)
        self.assertNotIn("請先輸入或從建議清單選擇", body)

    def intake_sentence(self, target, asset_class=ASSET_CLASS_TW_STOCK):
        """What the intake authority says about this target, asked of it.

        Escaped the way any sentence on a page is: the quotes the authority puts
        around the offending value are the reason this is compared against the
        escaped form rather than the raw one.
        """
        with self.assertRaises(Exception) as caught:
            inspect_question(
                self.QUESTION, assets=[target], asset_class=asset_class
            )
        return escape(str(caught.exception), quote=True)

    def test_a_refused_submission_is_recorded_as_a_refusal(self):
        self.refused(asset_class="")

        refusals = [r for r in self.records() if r["event"] == "launch_refused"]
        self.assertEqual(1, len(refusals))
        self.assertEqual("WARNING", refusals[0]["level"])


class LaunchRequestTest(unittest.TestCase):
    """What a submission *is*, read straight from the form's own names."""

    def read(self, **fields):
        return launch_module.read_request(
            {name: [value] for name, value in fields.items()}
        )

    def test_the_market_is_read_from_the_menus_field(self):
        request = self.read(asset_class=ASSET_CLASS_CRYPTO, asset_crypto="BTC")

        self.assertEqual(ASSET_CLASS_CRYPTO, request.asset_class)

    def test_the_targets_are_read_from_the_chosen_markets_own_box(self):
        """Another market's box is filled in and must not be read: the boxes are
        all in the page, and only the chosen one describes the run."""
        request = self.read(
            asset_class=ASSET_CLASS_CRYPTO, asset_crypto="BTC", asset_tw_stock="2330"
        )

        self.assertEqual(("BTC",), request.targets)

    def test_no_market_chosen_reads_no_target(self):
        request = self.read(asset_class="", asset_crypto="BTC")

        self.assertEqual("", request.asset_class)
        self.assertEqual((), request.targets)

    def test_a_missing_form_is_an_empty_submission_rather_than_an_error(self):
        request = launch_module.read_request({})

        self.assertEqual(("", "", ()), (request.question, request.asset_class, request.targets))

    def test_the_separators_the_hint_offers_all_split(self):
        for typed in ("AAPL, NVDA", "AAPL,NVDA", "AAPL、NVDA", "AAPL NVDA", "AAPL，NVDA"):
            request = self.read(asset_class=ASSET_CLASS_US_STOCK, asset_us_stock=typed)
            self.assertEqual(("AAPL", "NVDA"), request.targets, typed)

    def test_the_question_is_kept_as_typed_apart_from_its_edges(self):
        request = self.read(question="  2330 會漲嗎  ")

        self.assertEqual("2330 會漲嗎", request.question)


class TheWebAppNeverReadsAWordingForATargetTest(unittest.TestCase):
    """Acceptance 5, as a scan rather than as a promise.

    The intake reader is allowed here — it is where the shape of a stated target
    is decided — but only ever with both answers stated, which is the call that
    does not consult the text for either. A call that left one out would be the
    web app inferring again, and the strict reader
    (:func:`~hoya_market_agents.question.analyze_question`) may not appear at
    all.
    """

    READERS = ("inspect_question", "analyze_question", "build_question_package")

    def calls(self, source):
        """Every call to an intake reader, as ``(name, keywords)``."""
        found = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in self.READERS:
                found.append((name, {word.arg for word in node.keywords}))
        return found

    def package_calls(self):
        root = Path(live.__file__).resolve().parent
        return {
            path.name: self.calls(path.read_text(encoding="utf-8"))
            for path in sorted(root.rglob("*.py"))
        }

    def test_every_intake_call_in_the_web_app_states_both_answers(self):
        for name, calls in self.package_calls().items():
            for reader, keywords in calls:
                self.assertIn("assets", keywords, "{}: {}".format(name, reader))
                self.assertIn("asset_class", keywords, "{}: {}".format(name, reader))

    def test_the_strict_reader_is_not_reached_from_the_web_app(self):
        for name, calls in self.package_calls().items():
            self.assertNotIn(
                "analyze_question", [reader for reader, _ in calls], name
            )

    def test_the_scan_reads_the_module_that_does_state_them(self):
        """FP direction 1: a scan that found no call would pass both above."""
        found = self.package_calls()["launch.py"]

        self.assertEqual(
            [("inspect_question", {"assets", "asset_class"})],
            found,
        )

    def test_the_scan_would_catch_a_call_that_inferred_again(self):
        """FP direction 2: the scan has to be able to fail."""
        found = self.calls("scope = inspect_question(question)\n")

        self.assertEqual([("inspect_question", set())], found)


class SuggestionFixture(PageFixture):
    """Indexed runs to draw suggestions from, written the one way runs are."""

    def index(self, *runs):
        """Index one run per ``(run_id, targets, asset_class)`` triple."""
        for run_id, targets, asset_class in runs:
            write_run(
                self.data_root,
                run_id,
                "{} 未來七天會不會漲".format("／".join(targets)),
                assets=targets,
                asset_class=asset_class,
            )
        rebuild_index(self.data_root)

    def suggestions(self):
        return views.target_suggestions(self.data_root)


class TargetSuggestionTest(SuggestionFixture, unittest.TestCase):
    """Past targets, per market, each of them once.

    Drawn from ``run_index.query_runs`` — the one path to a run listing — and
    de-duplicated here in Python. There is no SQL in this package and no new
    query in ``run_index``: the same rows the history page reads are read again
    and folded, which is why a run this Data Root does not hold cannot appear as
    a suggestion.
    """

    def test_a_past_target_is_offered_for_its_own_market(self):
        self.index(("20260801T020000Z-2330-aaaa11", ("2330",), ASSET_CLASS_TW_STOCK))

        self.assertEqual(["2330"], self.suggestions()[ASSET_CLASS_TW_STOCK])

    def test_a_target_analysed_three_times_is_offered_once(self):
        """The de-duplication, against the list it de-duplicates."""
        self.index(
            ("20260801T020000Z-2330-aaaa11", ("2330",), ASSET_CLASS_TW_STOCK),
            ("20260802T020000Z-2330-bbbb22", ("2330",), ASSET_CLASS_TW_STOCK),
            ("20260803T020000Z-2330-cccc33", ("2330",), ASSET_CLASS_TW_STOCK),
        )

        self.assertEqual(
            ["2330", "2330", "2330"], self.recorded_targets(ASSET_CLASS_TW_STOCK)
        )
        self.assertEqual(["2330"], self.suggestions()[ASSET_CLASS_TW_STOCK])

    def recorded_targets(self, asset_class):
        """Every target the index holds for one market, repeats and all.

        The independent truth the de-duplication is measured against: read
        straight from ``query_runs``, folded by nobody.
        """
        return [
            target
            for row in query_runs(self.data_root, asset_class=asset_class)
            for target in row["assets"]
        ]

    def test_a_market_never_asked_about_offers_nothing(self):
        self.index(("20260801T020000Z-2330-aaaa11", ("2330",), ASSET_CLASS_TW_STOCK))

        self.assertEqual([], self.suggestions().get(ASSET_CLASS_CRYPTO, []))

    def test_a_target_is_offered_under_the_market_it_ran_in(self):
        self.index(
            ("20260801T020000Z-2330-aaaa11", ("2330",), ASSET_CLASS_TW_STOCK),
            ("20260802T020000Z-btc-bbbb22", ("BTC",), ASSET_CLASS_CRYPTO),
        )
        suggested = self.suggestions()

        self.assertEqual(["2330"], suggested[ASSET_CLASS_TW_STOCK])
        self.assertEqual(["BTC"], suggested[ASSET_CLASS_CRYPTO])

    def test_the_most_recent_run_is_offered_first(self):
        self.index(
            ("20260801T020000Z-aapl-aaaa11", ("AAPL",), ASSET_CLASS_US_STOCK),
            ("20260803T020000Z-nvda-bbbb22", ("NVDA",), ASSET_CLASS_US_STOCK),
        )

        self.assertEqual(["NVDA", "AAPL"], self.suggestions()[ASSET_CLASS_US_STOCK])

    def test_both_targets_of_a_comparison_are_offered(self):
        self.index(
            ("20260801T020000Z-aapl-nvda-aaaa11", ("AAPL", "NVDA"), ASSET_CLASS_US_STOCK)
        )

        self.assertEqual(["AAPL", "NVDA"], self.suggestions()[ASSET_CLASS_US_STOCK])

    def test_every_target_the_index_holds_is_offered_somewhere(self):
        """FP direction: a fold that dropped rows would pass the tests above."""
        self.index(
            ("20260801T020000Z-2330-aaaa11", ("2330",), ASSET_CLASS_TW_STOCK),
            ("20260802T020000Z-btc-bbbb22", ("BTC", "ETH"), ASSET_CLASS_CRYPTO),
            ("20260803T020000Z-nvda-cccc33", ("NVDA",), ASSET_CLASS_US_STOCK),
        )
        offered = {
            target
            for targets in self.suggestions().values()
            for target in targets
        }

        self.assertEqual(
            {target for row in query_runs(self.data_root) for target in row["assets"]},
            offered,
        )

    def test_an_index_that_is_not_there_leaves_nothing_to_suggest(self):
        """A first run has no index yet, and the ask bar still has to render."""
        self.assertEqual({}, self.suggestions())
        self.assertEqual(200, self.get("/").status)

    def test_an_index_that_cannot_be_read_leaves_nothing_to_suggest(self):
        self.index(("20260801T020000Z-2330-aaaa11", ("2330",), ASSET_CLASS_TW_STOCK))

        with mock.patch.object(views, "query_runs", side_effect=RunIndexError("壞了")):
            self.assertEqual({}, self.suggestions())

    def test_a_run_that_recorded_no_market_is_not_filed_under_a_guess(self):
        write_run(
            self.data_root,
            "20260801T020000Z-2330-aaaa11",
            "2330 未來七天會不會漲",
            assets=("2330",),
            asset_class=None,
        )
        rebuild_index(self.data_root)

        self.assertEqual({}, self.suggestions())


class AskBarFixture(SuggestionFixture):
    """The rendered ask bar, and the pieces of it worth asserting on."""

    def ask_bar(self):
        body = self.get("/").body
        found = re.search(r'<form class="run-bar ask".*?</form>', body, re.DOTALL)
        self.assertIsNotNone(found, "no ask bar on the debate room")
        return found.group(0)

    def menu_options(self, ask_bar=None):
        """The asset-class menu as ``[(value, shown word)]``, in page order."""
        bar = ask_bar or self.ask_bar()
        menu = re.search(
            r'<select[^>]+name="{}".*?</select>'.format(
                launch_module.ASSET_CLASS_FIELD
            ),
            bar,
            re.DOTALL,
        )
        self.assertIsNotNone(menu, "no asset class menu in the ask bar")
        return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', menu.group(0))

    def target_box(self, asset_class, ask_bar=None):
        """One market's whole group: label, box, suggestion list and hint."""
        bar = ask_bar or self.ask_bar()
        found = re.search(
            r'<span class="ask-target" data-asset-class="{}">.*?</span>\s*</span>'.format(
                asset_class
            ),
            bar,
            re.DOTALL,
        )
        self.assertIsNotNone(found, "no target box for {}".format(asset_class))
        return found.group(0)

    def offered_in_page(self, asset_class, ask_bar=None):
        """The suggestions the browser would show for one market."""
        box = self.target_box(asset_class, ask_bar)
        datalist = re.search(r"<datalist[^>]*>.*?</datalist>", box, re.DOTALL)
        self.assertIsNotNone(datalist, "no suggestion list for {}".format(asset_class))
        return re.findall(r'<option value="([^"]*)"', datalist.group(0))

    def shown_hint(self, asset_class, ask_bar=None):
        box = self.target_box(asset_class, ask_bar)
        found = re.search(r'<span class="hint"[^>]*>([^<]*)</span>', box)
        self.assertIsNotNone(found, "no format hint for {}".format(asset_class))
        return found.group(1)


class TheAskBarIsAMenuTest(AskBarFixture, unittest.TestCase):
    """Acceptance 1 and 2: a market to choose, and a box that fits it."""

    def test_the_menu_offers_the_markets_rather_than_every_declared_class(self):
        """Spec R-006 split the two lists: the form's is the scope file's.

        ``ASSET_CLASSES`` is still the longer of the two, and naming the class it
        holds that the menu does not is what makes this a statement about the
        split rather than about one list.
        """
        offered = [value for value, _word in self.menu_options() if value]

        self.assertEqual(list(market_scopes()), offered)
        self.assertEqual(
            [ASSET_CLASS_OPEN],
            [asset_class for asset_class in ASSET_CLASSES if asset_class not in offered],
        )

    def test_the_menu_opens_on_nothing_chosen(self):
        """So that "I forgot to choose" is a state the form can refuse."""
        first_value, first_word = self.menu_options()[0]

        self.assertEqual("", first_value)
        self.assertIn("選擇", first_word)

    def test_every_market_is_shown_in_chinese(self):
        for value, word in self.menu_options():
            if not value:
                continue
            self.assertEqual(pages.asset_class_label(value), word)
            self.assertTrue(
                any("一" <= character <= "鿿" for character in word), value
            )

    def test_the_menus_words_are_the_scope_authoritys_own(self):
        """A label edited in ``config/market_scopes.json`` reaches the ask bar."""
        scopes = dict(market_scopes())
        scopes[ASSET_CLASS_US_STOCK] = replace(
            scopes[ASSET_CLASS_US_STOCK], label="美國股市（改）"
        )

        with mock.patch.object(prompt_builder, "_CACHED_MARKET_SCOPES", scopes):
            shown = dict(self.menu_options())

        self.assertEqual("美國股市（改）", shown[ASSET_CLASS_US_STOCK])

    def test_every_market_has_a_box_of_its_own(self):
        bar = self.ask_bar()

        for asset_class in market_scopes():
            box = self.target_box(asset_class, bar)
            self.assertIn(
                'name="{}"'.format(launch_module.target_field(asset_class)), box
            )

    def test_every_box_is_labelled_and_reachable_by_keyboard(self):
        bar = self.ask_bar()

        for asset_class in market_scopes():
            box = self.target_box(asset_class, bar)
            identifier = re.search(r'<input id="([^"]+)"', box).group(1)
            self.assertIn('<label for="{}">'.format(identifier), box)
            self.assertNotIn("tabindex", box)

    def test_no_box_is_marked_required(self):
        """A required box that the sheet has hidden is a form a browser refuses
        to submit and cannot say why: the market is what is required, and the
        server reads the box that market names."""
        bar = self.ask_bar()

        for asset_class in market_scopes():
            self.assertNotIn("required", self.target_box(asset_class, bar))

    def test_the_market_itself_is_required(self):
        self.assertIn("required", self.menu_element())

    def menu_element(self):
        found = re.search(r"<select[^>]*>", self.ask_bar())
        return found.group(0)


class TheMenuIsTheScopeFilesThreeMarketsTest(AskBarFixture, unittest.TestCase):
    """Ticket 08 / Spec R-006: the ask bar offers the markets, and only those.

    The retired class is retired **from the form only**. ``open`` is still an
    asset class the intake declares, still a launcher argument, still a profile
    set in the roster and still the word a stored open-question run is shown
    under — what changed is that nobody can start a new one from this page.

    Which markets the form offers is therefore no longer ``ASSET_CLASSES``: it is
    what ``config/market_scopes.json`` declares, read live. The tests below
    compare the page against that file rather than against a list retyped here,
    and one of them takes a market away from the authority to show the page
    really follows it.
    """

    def scope_file_markets(self):
        """The markets the config file declares, read straight from the file.

        Independent of ``prompt_builder``: this is the acceptance condition's own
        authority ("與 ``config/market_scopes.json`` 的三市場一致"), so it is read
        the way a reader would read it rather than through the loader the page
        happens to use.
        """
        code_root = Path(live.__file__).resolve().parents[2]
        document = json.loads(
            (code_root / "config" / "market_scopes.json").read_text(encoding="utf-8")
        )
        return set(document["scopes"])

    def offered(self, ask_bar=None):
        return [value for value, _word in self.menu_options(ask_bar) if value]

    def test_the_menu_offers_exactly_the_markets_the_config_declares(self):
        self.assertEqual(self.scope_file_markets(), set(self.offered()))

    def test_the_three_markets_are_the_ones_named_in_the_acceptance_condition(self):
        """FP direction: a config read that came back empty would pass the above."""
        self.assertEqual(
            {ASSET_CLASS_CRYPTO, ASSET_CLASS_TW_STOCK, ASSET_CLASS_US_STOCK},
            self.scope_file_markets(),
        )

    def test_the_retired_class_is_not_an_option(self):
        self.assertNotIn(ASSET_CLASS_OPEN, self.offered())

    def test_the_retired_classs_word_is_nowhere_in_the_ask_bar(self):
        """Not on the menu, and not as a box's label either: it is gone from the
        form, not merely moved down it."""
        bar = self.ask_bar()

        self.assertNotIn(pages.asset_class_label(ASSET_CLASS_OPEN), bar)
        self.assertNotIn(launch_module.target_field(ASSET_CLASS_OPEN), bar)

    def test_the_menu_is_in_the_authoritys_own_order(self):
        self.assertEqual(list(market_scopes()), self.offered())

    def test_a_market_the_authority_stops_declaring_leaves_the_form(self):
        """The one test that a hard-coded list of options cannot pass.

        A market removed from the authority has to leave the menu *and* take its
        box with it — which is what says the options are generated from the scope
        file rather than written out beside it.
        """
        thinner = {
            asset_class: scope
            for asset_class, scope in market_scopes().items()
            if asset_class != ASSET_CLASS_CRYPTO
        }

        with mock.patch.object(prompt_builder, "_CACHED_MARKET_SCOPES", thinner):
            bar = self.ask_bar()

        self.assertEqual([ASSET_CLASS_TW_STOCK, ASSET_CLASS_US_STOCK], self.offered(bar))
        self.assertNotIn(launch_module.target_field(ASSET_CLASS_CRYPTO), bar)


# One target per market, spelled the way that market spells one. Keyed on the
# market so a fourth market added to the scope file fails the walk below with a
# missing key rather than quietly going untested.
TARGET_PER_MARKET = {
    ASSET_CLASS_CRYPTO: "BTC",
    ASSET_CLASS_TW_STOCK: "2330",
    ASSET_CLASS_US_STOCK: "AAPL",
}


class EveryMarketOnTheMenuStartsARunTest(AskFixture, unittest.TestCase):
    """The ticket's manual step 2, as a test: all three options really launch.

    A launch holds the room's lock until the child ends, so three submissions are
    three fixtures — ``setUp`` is called again between them for a fresh Data Root,
    lock and spawn list, which is cheaper and more honest than reaching in to
    unlock the one that is held.
    """

    def test_each_offered_market_starts_a_run_in_that_market(self):
        for asset_class in market_scopes():
            with self.subTest(asset_class=asset_class):
                self.setUp()
                self.write_certificate()

                response = self.submit(
                    asset_class=asset_class, target=TARGET_PER_MARKET[asset_class]
                )

                self.assertEqual(303, response.status)
                handed = self.child_launch()
                self.assertEqual(asset_class, handed["asset_class"])
                self.assertEqual([TARGET_PER_MARKET[asset_class]], handed["assets"])


class AStoredOpenQuestionRunIsStillReadableTest(SuggestionFixture, unittest.TestCase):
    """Ticket 08 の other half: taking the option away takes no run away.

    These pass before the menu changes as well as after — that is the point.
    They are what says the retirement stopped at the form: the history page still
    lists an open-question run under its own word, the detail page still opens,
    and the report it wrote still renders.
    """

    RUN_ID = "20260801T020000Z-overall-market-aaaa11"
    QUESTION = "利率政策未來七天會怎麼走"

    def setUp(self):
        super().setUp()
        write_run(
            self.data_root,
            self.RUN_ID,
            self.QUESTION,
            assets=("OVERALL-MARKET",),
            asset_class=ASSET_CLASS_OPEN,
        )
        rebuild_index(self.data_root)

    def test_the_history_page_still_lists_it_under_its_own_word(self):
        body = self.get("/history?asset_class={}".format(ASSET_CLASS_OPEN)).body

        self.assertIn(self.RUN_ID, body)
        self.assertIn(pages.asset_class_label(ASSET_CLASS_OPEN), body)

    def test_its_detail_page_still_opens_and_still_names_its_class(self):
        response = self.get("/run/{}".format(self.RUN_ID))

        self.assertEqual(200, response.status)
        self.assertIn(pages.asset_class_label(ASSET_CLASS_OPEN), response.body)
        self.assertNotIn("Traceback", response.body)

    def test_the_report_it_wrote_still_opens(self):
        response = self.get("/run/{}/report.html".format(self.RUN_ID))

        self.assertEqual(200, response.status)

    def test_the_history_filter_still_offers_the_class_to_filter_by(self):
        """The filter is the reader's way *back* to those runs, so it keeps the
        option the ask bar gives up."""
        body = self.get("/history").body

        self.assertIn(
            '<option value="{}">{}</option>'.format(
                ASSET_CLASS_OPEN, pages.asset_class_label(ASSET_CLASS_OPEN)
            ),
            body,
        )


class TheFormatHintFollowsTheMarketTest(AskBarFixture, unittest.TestCase):
    """Acceptance 2: each market's box says how that market spells a code."""

    def test_every_declared_market_has_a_chinese_hint(self):
        for asset_class in ASSET_CLASSES:
            hint = pages.target_format_hint(asset_class)
            self.assertTrue(hint, asset_class)
            self.assertTrue(
                any("一" <= character <= "鿿" for character in hint), asset_class
            )

    def test_a_market_hint_is_the_scope_files_own_first_sentence(self):
        """Not a copy written on the page: the convention has an authority and
        ``symbol_resolution`` is it. The first sentence is where that field states
        the spelling, before the caveats a seat needs and a form does not."""
        for asset_class, scope in market_scopes().items():
            expected = scope.symbol_resolution.split("。")[0] + "。"
            self.assertEqual(expected, pages.target_format_hint(asset_class))

    def test_the_hint_on_the_page_is_that_sentence(self):
        bar = self.ask_bar()

        for asset_class in market_scopes():
            self.assertEqual(
                escape(pages.target_format_hint(asset_class), quote=True),
                self.shown_hint(asset_class, bar),
            )

    def test_a_convention_edited_in_the_config_reaches_the_ask_bar(self):
        scopes = dict(market_scopes())
        scopes[ASSET_CLASS_TW_STOCK] = replace(
            scopes[ASSET_CLASS_TW_STOCK], symbol_resolution="改成四位數字。後面還有字。"
        )

        with mock.patch.object(prompt_builder, "_CACHED_MARKET_SCOPES", scopes):
            self.assertEqual("改成四位數字。", self.shown_hint(ASSET_CLASS_TW_STOCK))

    def test_the_market_with_no_scope_is_named_here_because_none_describes_it(self):
        """The class the ask bar stopped offering still has an answer of its own.

        Spec R-006 took it off the form, not out of the system: it is still what
        the intake declares and what the launcher takes, so this module still
        knows how its targets are spelled rather than answering "" about a class
        that really exists.
        """
        self.assertNotIn(ASSET_CLASS_OPEN, market_scopes())
        self.assertTrue(pages.target_format_hint(ASSET_CLASS_OPEN))

    def test_the_hint_is_tied_to_the_box_for_a_screen_reader(self):
        for asset_class in market_scopes():
            box = self.target_box(asset_class)
            described = re.search(r'aria-describedby="([^"]+)"', box).group(1)
            self.assertIn('<span class="hint" id="{}">'.format(described), box)


class OneBoxAtATimeWithNoScriptTest(unittest.TestCase):
    """Acceptance 9 and 2 together: the switch is the browser's own.

    A ``<select>``'s chosen ``<option>`` matches ``:checked`` and ``:has()`` lets
    the form act on it, so which box and which hint are shown follows the menu
    with no script on the page. The rules are generated from
    ``pages.ask_bar_markets()`` — the same list the menu and the boxes come from,
    so a rule cannot be written for an option the menu does not offer, which is
    what Spec R-006's retired class would otherwise have become.

    They only ever **hide** the boxes that were not chosen, which is the safe
    direction: a browser without ``:has()`` shows every box and the form still
    submits, because the server reads the box the chosen market names rather than
    the one that happens to be visible.
    """

    def setUp(self):
        self.sheet = pages.stylesheet()

    def rules_for(self, asset_class):
        return re.findall(
            r'[^{}]*option\[value="{}"\]:checked[^{{]*\{{[^}}]*\}}'.format(
                "{}", asset_class
            ),
            self.sheet,
        )

    def test_every_market_the_form_offers_owns_a_rule(self):
        for asset_class in market_scopes():
            self.assertTrue(self.rules_for(asset_class), asset_class)

    def test_the_class_the_form_no_longer_offers_owns_no_rule(self):
        """A rule selecting an option the menu does not have would be a second,
        staler copy of the list of markets — exactly what generating them from
        one list prevents."""
        self.assertEqual([], self.rules_for(ASSET_CLASS_OPEN))

    def test_a_chosen_market_hides_the_other_markets_boxes(self):
        for asset_class in market_scopes():
            rules = " ".join(self.rules_for(asset_class))
            self.assertIn(
                ':not([data-asset-class="{}"])'.format(asset_class), rules
            )
            self.assertIn("display:none", rules)

    def test_nothing_chosen_hides_every_box_and_shows_the_prompt(self):
        self.assertIn('.ask:has(option[value=""]:checked) .ask-target{display:none;}', self.sheet)
        self.assertIn(".ask-prompt{display:none;}", self.sheet)

    def test_a_market_the_intake_does_not_declare_owns_no_rule(self):
        """FP direction: the scan has to be able to come back empty."""
        self.assertEqual([], self.rules_for("commodity"))


class TheRoomBringsEverythingWithItTest(AskBarFixture, unittest.TestCase):
    """Acceptance 9: no inline script, no second origin, nothing to fetch."""

    def test_the_ask_bar_carries_no_script_and_no_handler(self):
        bar = self.ask_bar()

        self.assertNotIn("<script", bar)
        self.assertIsNone(re.search(r"\son[a-z]+=", bar), bar)
        self.assertNotIn("javascript:", bar)

    def test_every_script_on_the_room_is_a_file_from_this_origin(self):
        body = self.get("/").body

        for tag in re.findall(r"<script[^>]*>(.*?)</script>", body, re.DOTALL):
            self.assertEqual("", tag.strip())
        for source in re.findall(r"<script[^>]+src=\"([^\"]+)\"", body):
            self.assertEqual(pages.LIVE_SCRIPT_PATH, source)

    def test_the_room_asks_the_network_for_nothing(self):
        body = self.get("/").body

        for reference in re.findall(r"(?:src|href)=\"([^\"]*)\"", body):
            self.assertFalse(reference.startswith("http"), reference)
            self.assertFalse(reference.startswith("//"), reference)


class TheSuggestionsReachTheBoxTest(AskBarFixture, unittest.TestCase):
    """Acceptance 3, seen from the page rather than from the fold."""

    def test_a_past_target_is_offered_in_its_markets_box(self):
        self.index(("20260801T020000Z-2330-aaaa11", ("2330",), ASSET_CLASS_TW_STOCK))

        self.assertEqual(["2330"], self.offered_in_page(ASSET_CLASS_TW_STOCK))

    def test_a_target_analysed_twice_is_offered_once(self):
        self.index(
            ("20260801T020000Z-btc-aaaa11", ("BTC",), ASSET_CLASS_CRYPTO),
            ("20260802T020000Z-btc-bbbb22", ("BTC",), ASSET_CLASS_CRYPTO),
        )

        self.assertEqual(["BTC"], self.offered_in_page(ASSET_CLASS_CRYPTO))

    def test_a_market_is_not_offered_another_markets_targets(self):
        self.index(("20260801T020000Z-btc-aaaa11", ("BTC",), ASSET_CLASS_CRYPTO))

        self.assertEqual([], self.offered_in_page(ASSET_CLASS_TW_STOCK))

    def test_the_box_is_wired_to_its_own_suggestion_list(self):
        self.index(("20260801T020000Z-btc-aaaa11", ("BTC",), ASSET_CLASS_CRYPTO))
        box = self.target_box(ASSET_CLASS_CRYPTO)

        listed = re.search(r'<input[^>]+list="([^"]+)"', box).group(1)
        self.assertIn('<datalist id="{}">'.format(listed), box)

    def test_a_room_with_no_history_still_renders_every_box(self):
        bar = self.ask_bar()

        for asset_class in market_scopes():
            self.assertEqual([], self.offered_in_page(asset_class, bar))


class TheAskBarShowsNoEnglishDataValueTest(AskBarFixture, unittest.TestCase):
    """Acceptance 8 and Spec A5: enum keys are machine values, not words.

    The keys are still in the markup — an ``<option>`` has to submit something and
    a rule has to select something — and that is exactly the split Spec A5 draws:
    what may not appear in English is what the reader *sees*. So this reads the
    text between the tags, which is what a person reads.
    """

    def visible_text(self):
        stripped = re.sub(r"<[^>]+>", " ", self.ask_bar())
        return re.sub(r"\s+", " ", stripped)

    def test_no_asset_class_key_is_shown_as_a_word(self):
        shown = self.visible_text()

        for asset_class in ASSET_CLASSES:
            self.assertNotIn(asset_class, shown)

    def test_the_words_that_are_shown_are_the_authoritys(self):
        """FP direction: an ask bar with no words at all would pass the above."""
        shown = self.visible_text()

        for asset_class in market_scopes():
            self.assertIn(pages.asset_class_label(asset_class), shown)

    def test_a_target_code_is_left_alone_because_it_is_data(self):
        """Spec A5 names 標的代號 as outside the rule; the suggestions are codes."""
        self.index(("20260801T020000Z-btc-aaaa11", ("BTC",), ASSET_CLASS_CRYPTO))

        self.assertEqual(["BTC"], self.offered_in_page(ASSET_CLASS_CRYPTO))


class TheAskBarWhileARunIsGoingTest(AskBarFixture, unittest.TestCase):
    """The busy state covers the new controls too, or it covers nothing."""

    def test_a_running_launch_disables_the_menu_and_every_box(self):
        self.write_certificate()
        self.post(
            "/launch",
            {
                launch_module.QUESTION_FIELD: "2330 未來七天會不會漲",
                launch_module.ASSET_CLASS_FIELD: ASSET_CLASS_TW_STOCK,
                launch_module.target_field(ASSET_CLASS_TW_STOCK): "2330",
            },
        )
        bar = self.ask_bar()

        self.assertIn("disabled", re.search(r"<select[^>]*>", bar).group(0))
        for asset_class in market_scopes():
            self.assertIn("disabled", self.target_box(asset_class, bar))


class TheWebAppHoldsNoSqlTest(unittest.TestCase):
    """Spec 產品限制: webapp 零 SQL；唯一取數路徑是 ``run_index.query_runs``.

    Ticket 05 is the ticket that could have broken this — "the suggestions are a
    ``SELECT DISTINCT``" is the obvious way to write it — so the boundary is
    asserted here as a scan rather than left to a reviewer's grep.
    """

    KEYWORDS = ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE", "sqlite3")

    def package_files(self):
        root = Path(live.__file__).resolve().parent
        return sorted(path for path in root.rglob("*.py"))

    def statements(self, text):
        return [word for word in self.KEYWORDS if word in text]

    def test_no_module_in_the_web_app_writes_a_statement(self):
        for path in self.package_files():
            self.assertEqual(
                [], self.statements(path.read_text(encoding="utf-8")), path.name
            )

    def test_the_scan_reads_the_modules_it_claims_to(self):
        """FP direction 1: a scan over an empty file list also never fails."""
        names = {path.name for path in self.package_files()}

        self.assertIn("views.py", names)
        self.assertIn("launch.py", names)
        self.assertIn("components.py", names)
        self.assertIn("live_page.py", names)
        self.assertGreater(len(names), 5)

    def test_the_scan_would_catch_a_query_written_by_hand(self):
        """FP direction 2: the scan has to be able to fail."""
        self.assertEqual(
            ["SELECT "],
            self.statements('rows = "SELECT DISTINCT asset FROM runs"'),
        )

    def test_the_authority_that_may_hold_sql_still_does(self):
        """FP direction 3: the needle has to be one something really spells."""
        source = Path(run_index.__file__).read_text(encoding="utf-8")

        self.assertIn("SELECT ", source)


class TheBusySentenceNamesAPageThatExistsTest(unittest.TestCase):
    """Coordinator ruling 6, carried over from Ticket 04: the page was renamed."""

    def test_the_busy_sentence_points_at_the_merged_page(self):
        self.assertIn("歷史與命中率", launch_module.BUSY_MESSAGE)

    def test_the_busy_sentence_no_longer_names_the_retired_page(self):
        self.assertNotIn("歷史查詢", launch_module.BUSY_MESSAGE)
