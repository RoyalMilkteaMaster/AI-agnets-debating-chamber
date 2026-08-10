"""Page data in, HTML out. This module writes nothing.

Nearly everything here is a pure function of what
:mod:`~hoya_market_agents.webapp.views` and :mod:`~hoya_market_agents.webapp.live`
assembled. What it reads besides its arguments is two things, named here rather
than left for a reader to discover:

* **The market authority's own words**, through
  :func:`~hoya_market_agents.prompt_builder.market_scopes`, which loads
  ``config/market_scopes.json`` on first use and caches it.
  :func:`asset_class_label` and :func:`target_format_hint` read it so that a
  market's name and its spelling convention reach a page from the same file
  every prompt is built from, with no second copy here to go stale.
* **Which run has the newest report**, through
  :func:`~hoya_market_agents.webapp.views.latest_report_run`. The two report
  tabs every header carries have to point somewhere on a page that is not about
  one run, and that is a fact about the Data Root rather than about the page
  being drawn (Spec R-002).
The seat cards' 白話說明 is not a third read (Spec R-005): the sentence arrives on
the seat, beside the name it belongs with, from
:func:`~hoya_market_agents.webapp.live.seat_fields` — the same object every later
frame carries, so a redraw cannot pair one run's name with another's sentence.

Neither read makes a page need a Data Root to be drawn, which is what keeps
every page here assertable without a socket and without a run: the market file
ships with the program, and the report question answers ``None`` for an index
that is absent or will not read — the tabs are then the disabled ones they have
always been for a run with no files.

The accessibility decisions are in the markup on purpose, not in a checklist
somewhere else:

* Every page opens with a skip link into ``<main>``, so the first Tab press
  jumps the header instead of walking it.
* Every control is a real control with a ``<label for>``; every table names its
  columns with ``<th scope="col">``; every run is reached by an ``<a href>``,
  so the whole query-to-detail path is Tab and Enter.
* No element is given a positive ``tabindex``. The reading order is the tab
  order.
* :mod:`~hoya_market_agents.design_tokens` holds the one palette, the scale and
  the pairs that must meet WCAG AA; this module names them
  (:data:`PALETTE`, :data:`SCALE`, :data:`CONTRAST_REQUIREMENTS`) and owns no
  value of its own. They are data because a ratio nobody measures is a ratio
  nobody keeps: the test computes each one from those tables.

**One stylesheet, for every page.** :func:`stylesheet` is built from those
tables and nothing else, and :func:`_document` gives it to every page including
the debate room. The room used to ship an isolated sheet with tokens of its own,
which meant two font stacks, two radius scales, two greens — and only one of the
two under the contrast test. Now there is one source for a colour, one place to
change it, and one sheet that has to pass. What a rule may *not* do is name a
value: every colour, size and gap is a ``var()`` read of the tables, and
``tests/test_design_tokens.py`` asserts the set of colour literals in the
finished sheet is exactly the set the palettes hold.

**What that sheet draws** (Spec R-004): a grey-50 canvas, white cards told apart
by hairlines and space rather than by fills, pill-shaped controls, and one
frosted panel family — :func:`_frosted`'s surfaces, painted with the palette's
translucent white and blurred with the browser's own ``backdrop-filter``, no
image and nothing fetched. The four brand hues are decoration and only
decoration: they meet a reader as a hairline band along the top of every page's
header (:func:`~hoya_market_agents.report_renderer.decorative_hairline`, the same
one both offline renderers draw) and appear nowhere else, so no reader ever has
to decode a colour that says nothing.

**Only the outerwear of the frozen three changes.** The chat feed, the seat roll
and the three tallies keep their content, their position, their semantic colours
and their behaviour; what this design reaches is their type, spacing and card
style, which is the whole of what 保護區 leaves open. Neither the frost nor a
decorative hue is applied to anything inside them.

The history, detail and settings pages carry no script. Not "no external
script" — none at all, which is why the policy the server sends for them can say
``script-src 'none'`` and mean it. The settings form needs none: it is rendered
whole on the server and submitted with a plain ``POST``, and the only refusal it
shows arrives on the page that answers that ``POST``.

Only one function on this page decides anything about a rule value, and it is
not here: :func:`render_settings_page` shows the sentences
:mod:`~hoya_market_agents.webapp.settings` was given by the loader, beside the
controls that sentence named.

The live room is the one page with a script, and it is a separate file served
from this origin (:data:`LIVE_SCRIPT_PATH`) rather than an inline block, so its
policy can be ``script-src 'self'`` without ``'unsafe-inline'``. The page is
rendered whole before the script runs — every message, seat and count already in
the HTML — so the script's job is to append what arrives next, not to build the
page. Without it the room is a correct snapshot that does not update itself.

**The ask bar interacts without any script at all.** Choosing a market changes
which target box, which suggestion list and which spelling convention are shown,
and that is done by the sheet: a ``<select>``'s chosen ``<option>`` matches
``:checked`` and ``:has()`` lets the form act on it
(:func:`_asset_picker_rules`). Every market's box is in the HTML; the rules only
hide the ones not chosen, so a browser that does not understand ``:has()`` shows
all of them and the form still submits correctly. That is the whole reason the
room's script has nothing to do with asking a question.
"""

from html import escape
from urllib.parse import quote

from .. import design_tokens
from ..prompt_builder import market_scopes
from ..question import ASSET_CLASS_OPEN, ASSET_CLASSES
from ..report_contract import CONFIDENCE_ICONS, CONFIDENCE_LEVELS
from ..report_renderer import decorative_hairline
from ..run_index import (
    OUTCOME_HIT,
    OUTCOME_MISS,
    OUTCOME_PENDING,
    OUTCOME_UNREADABLE,
    OUTCOME_UNVERIFIABLE,
    OUTCOME_VERDICTS,
)
from . import launch as launch_module
from . import outcome as outcome_module
from . import pdf_export
from . import settings
from .live import STATUS_FINISHED, STATUS_WAITING
from .views import (
    DEBATE_ARTIFACT,
    REPORT_ARTIFACT,
    STATE_INDEX_MISSING,
    STATE_OK,
    latest_report_run,
)

SITE_TITLE = "Hoya Bit 市場七席"

# The pages of this site a reader browses to, shown in the top left of the
# navigation on **every** server-rendered page but the one that says the server
# has stopped. Before this there was no navigation at all: the room had tabs, the
# other pages had a stray "back to history" link, and a reader who opened the
# settings page could not get back to the debate room without editing the URL —
# which is how the settings page came to look missing.
#
# ``(path, label)`` in the order they are shown. The debate room is first
# because it is the home page and the subject of the project.
#
# **Two browsing tabs, not three.** The history query and the hit-rate page are
# one page (Spec R1), so they are one tab: a second tab pointing at the same page
# would be two names for one destination, and a tab still pointing at ``/stats``
# would send a reader through a redirect to the tab beside it. 設定 was the third
# and is no longer here; see :data:`SETTINGS_TAB`.
BROWSE_TABS = (
    ("/", "即時辯論"),
    ("/history", "歷史與命中率"),
)

# The one tab that is not a way around this site but a way to change what it
# does. It is kept out of :data:`BROWSE_TABS` because Spec R-003 asks for the
# separation itself: it is rendered in its own group beside the stop button, so
# the way around this site and the way to administer it are told apart by where
# they sit rather than by reading four labels to find the odd one out.
SETTINGS_TAB = ("/settings", "設定")

# The two tabs that are not pages of this site but files of one run: that run's
# own offline report and transcript. **Which** run is the header's question and
# not this table's — a page about one run points at that run, and a page about no
# run in particular points at the newest run that has a report (Spec R-002, and
# :func:`~.views.latest_report_run`) — and either way a file that is not there is
# a tab that says so and goes nowhere. ``(label, file name)``.
RUN_ARTIFACT_TABS = (
    ("市場報告", REPORT_ARTIFACT),
    ("完整辯論", DEBATE_ARTIFACT),
)


# The URL segment the export form submits to, and the one place it is spelled.
# :mod:`~hoya_market_agents.webapp.server` reads it from here for the same reason
# it reads :data:`LIVE_SCRIPT_PATH` from here: a form's action and the route that
# answers it are one decision, and two copies of it fail quietly — the button
# would simply 404. The label is beside it because the section heading, the button
# and the footer's sentence are one name to a reader.
EXPORT_PDF_SEGMENT = "export-pdf"
EXPORT_PDF_LABEL = "匯出 PDF"

# The URL the stop button submits to, spelled here for the same reason the export
# segment is: :mod:`~hoya_market_agents.webapp.server` reads it from the module
# that renders the form, so the button and the route that answers it cannot drift
# into two spellings. The label is beside it because the button, the page it leads
# to and its title are one name to a reader.
SHUTDOWN_PATH = "/shutdown"
SHUTDOWN_LABEL = "關閉伺服器"
SHUTDOWN_PAGE_TITLE = "伺服器已關閉"

# How the entry the user actually double-clicks is named, so the last page this
# server sends can say how to come back. It is the workspace shortcut's name
# (Spec R4), not a path: nothing on this machine's disk is this module's business.
RESTART_SHORTCUT_NAME = "開啟辯論室"


def export_pdf_path(run_id):
    """Return the URL that exports one run's PDFs."""
    return "/run/{}/{}".format(_path(run_id), EXPORT_PDF_SEGMENT)


def site_tabs(current_path, report_run=None):
    """Return this page's navigation, in the two groups Spec R-003 asks for.

    Five tabs on every page: the two pages a reader browses to, the two files of
    one run, and 設定 on its own at the end so it lands beside the stop button.
    One list for every page, so "which pages exist" is answered in one place, and
    ``current_path`` is this page's own path so its tab is marked as the current
    one.

    ``report_run`` is which run the two report tabs open, as
    ``{"run_id": …, "artifacts": {file name: on disk}}`` — this page's own run
    where it has one, and :func:`~.views.latest_report_run`'s answer where it does
    not. ``None`` is "there is nothing to open", which is shown rather than
    hidden: the tabs stay, disabled, so the site has one navigation and not two
    shapes of one.

    **Two ``<nav>`` groups, each saying which it is.** The separation is the
    requirement, and a separation a screen reader cannot hear is not one — two
    unnamed landmarks would both be announced as "navigation" with no way to tell
    the browsing tabs from the settings link.

    The markup and the stylesheet are the same on every page: ``.page-tabs`` is
    painted once in :data:`_RULES`, so the room's tab bar and the settings page's
    are the same control rather than two that happen to look alike.

    This is the site's navigation, never the offline bundle's: the bundle's is
    :mod:`report_renderer`'s and carries only the two files it actually ships.
    """
    browse = [_tab_link(path, label, current_path) for path, label in BROWSE_TABS]
    browse.extend(_run_artifact_tabs(report_run))
    settings_path, settings_label = SETTINGS_TAB
    return _tab_group("主要頁面", browse) + _tab_group(
        settings_label, [_tab_link(settings_path, settings_label, current_path)]
    )


def _tab_group(label, tabs):
    """One navigation group, named so it can be told from the other one."""
    return '<nav class="page-tabs" aria-label="{}">{}</nav>'.format(
        _e(label), "".join(tabs)
    )


def _tab_link(path, label, current_path):
    """One tab to a page of this site, marked when it is the page being read."""
    if path == current_path:
        return '<a href="{}" aria-current="page">{}</a>'.format(path, _e(label))
    return '<a href="{}">{}</a>'.format(path, _e(label))


def _run_artifact_tabs(report_run):
    """One run's two artifact tabs: a link to each file that is really there.

    ``report_run`` is the run they open and which of its files exist; ``None`` is
    a page with no run to offer, and its two tabs are the same disabled ones a
    run that has produced nothing yet gets. That is why this returns two tabs
    whatever it is given: "no report anywhere" is a state of the site a reader is
    entitled to see, and a navigation that grew and shrank by page would be two
    navigations.
    """
    run = report_run or {}
    run_id = run.get("run_id")
    artifacts = run.get("artifacts") or {}
    return [
        _artifact_tab(label, artifact, run_id, artifacts.get(artifact))
        for label, artifact in RUN_ARTIFACT_TABS
    ]


def _artifact_tab(label, artifact, run_id, available):
    """One artifact tab: a link once the file exists, and until then a tab that
    says so and goes nowhere.

    **The unavailable one is not an ``<a>``.** It used to be
    ``<a href="/" aria-disabled="true">``, which is a link that tells a screen
    reader it is disabled and then navigates anyway — the stylesheet's
    ``pointer-events:none`` stopped the mouse and nothing stopped the keyboard, so
    a reader who tabbed to "市場報告" and pressed Enter was taken off the room to
    the home page. There is no href to follow here: ``role="link"`` with
    ``aria-disabled`` is the announced-but-inert pattern, and with no ``tabindex``
    it is not in the tab order at all.
    """
    if not run_id or not available:
        return '<span role="link" aria-disabled="true">{}</span>'.format(_e(label))
    return '<a href="/run/{}/{}">{}</a>'.format(_path(run_id), artifact, _e(label))

# ``{state: (word, mark, colour token)}`` for every state one prediction can be
# in. Keyed on ``run_index``'s own vocabulary and asserted equal to it, so a
# sixth state added there fails here rather than reaching a page with no words.
#
# **The mark is not decoration.** Hit and miss are the two states a reader
# scans for, and telling them apart by green and red alone fails anyone who
# cannot separate the two — so each state carries a word and a mark, and the
# colour is the third signal rather than the only one. The three states that do
# not count towards the hit rate are deliberately not painted as failures:
# ``muted`` says "not scored", which is what they are.
OUTCOME_WORDS = {
    OUTCOME_HIT: ("命中", "✔", "success"),
    OUTCOME_MISS: ("未命中", "✘", "danger"),
    OUTCOME_UNVERIFIABLE: ("不可自動驗證", "—", "muted"),
    OUTCOME_PENDING: ("待驗證", "…", "muted"),
    OUTCOME_UNREADABLE: ("紀錄無法讀取", "⚠", "abstain"),
}

# The order the four states are shown in: the two that are scored first, then
# the two that are waiting, then the one that is broken.
OUTCOME_ORDER = (
    OUTCOME_HIT,
    OUTCOME_MISS,
    OUTCOME_PENDING,
    OUTCOME_UNVERIFIABLE,
    OUTCOME_UNREADABLE,
)

# What the hit rate divides by, spelled out on the page. A percentage whose
# denominator is not stated is a percentage of nothing in particular, and this
# one deliberately excludes two states a careless reading would include.
HIT_RATE_FORMULA = "命中 ÷（命中 + 未命中）"
HIT_RATE_NOTE = (
    "待驗證與不可自動驗證都不列入分母：還沒對答案不等於答錯，"
    "沒有價格可以對照的題目也不等於答錯。"
)
NO_HIT_RATE = "尚無可計分的預測"

# One word per light. Keyed on the contract's own tuple, and a test asserts the
# two sets are equal — a sixth light added there fails here rather than quietly
# reaching a Chinese page in English. A level the contract does not declare can
# still be stored by the index, and is shown exactly as it was recorded.
CONFIDENCE_WORDS = {
    "red": "紅燈",
    "orange": "橘燈",
    "yellow": "黃燈",
    "green": "綠燈",
    "blue": "藍燈",
}

# The one asset class that has no market, and therefore no ``market_scopes``
# entry to read a word from. This is the same split
# :data:`prompt_builder.MARKET_CLASSES` draws — ``question.ASSET_CLASSES`` minus
# this one — so a class added to the intake that *is* a market gets its word from
# the scope file, and only a genuinely market-less class is named here. A test
# walks all of ``ASSET_CLASSES`` and fails if any member reaches a page as its
# English key, so a fifth class cannot slip through untranslated.
_NON_MARKET_ASSET_LABELS = {ASSET_CLASS_OPEN: "開放題"}


def asset_class_label(asset_class):
    """Return one asset class's Traditional-Chinese name, from the authorities.

    The three market classes read their word live from
    :func:`~hoya_market_agents.prompt_builder.market_scopes`, so a label edited
    in ``config/market_scopes.json`` reaches the page with no second copy here to
    fall out of date. The one non-market class is named in
    :data:`_NON_MARKET_ASSET_LABELS` because there is no market scope to read it
    from. A value outside ``question.ASSET_CLASSES`` — which the index may still
    hold, exactly as it does an undeclared confidence level — is shown as it was
    recorded, because inventing a word for a class no authority declares would be
    worse than showing what is really there. ``None`` is the empty marker.
    """
    if not asset_class:
        return _EMPTY
    if asset_class in _NON_MARKET_ASSET_LABELS:
        return _NON_MARKET_ASSET_LABELS[asset_class]
    scope = market_scopes().get(asset_class)
    if scope is not None:
        return scope.label
    return asset_class


# The id the ask bar's market menu carries, so its ``<label for>`` and the
# ``<select>`` cannot drift apart.
ASSET_CLASS_CONTROL_ID = "asset-class"


def ask_bar_markets():
    """The classes the ask bar offers, which is exactly the markets there are.

    Spec R-006 retires the open question **from the form**, and this function is
    the whole of that retirement: the menu, the target boxes and the sheet's
    show-one-box rules are all generated from what it returns, so the three of
    them cannot disagree about which classes exist on the page.

    The answer comes from :func:`~hoya_market_agents.prompt_builder.market_scopes`
    — ``config/market_scopes.json`` — rather than from ``question.ASSET_CLASSES``
    or a list written out here. A market added to that file therefore reaches the
    form with nothing to edit here, and the one class that has no market entry
    (``question.ASSET_CLASS_OPEN``) is left out by the same rule rather than by a
    special case naming it.

    **Retired from the form is not retired from the system.** ``open`` is still a
    class the intake declares, still an argument the launcher takes, still a
    profile set the roster must carry, and still the word a stored open-question
    run is shown under on the history and detail pages — which is why
    :func:`asset_class_label` and :func:`target_format_hint` still answer for it
    and why the history page's own filter still offers it.
    """
    return tuple(market_scopes())


# How a target is spelled in the one class that has no market — and therefore no
# ``symbol_resolution`` to read a convention out of. Named here for exactly the
# reason :data:`_NON_MARKET_ASSET_LABELS` is.
#
# Since Spec R-006 the ask bar no longer offers that class, so no page asks this
# question about it today; the answer stays because the class itself stays — the
# intake declares it, the launcher takes it and a stored run is still shown under
# it — and a module that answers about a class it knows is better than one that
# answers "" for a class the system really has.
_NON_MARKET_TARGET_HINTS = {
    ASSET_CLASS_OPEN: (
        "開放題沒有市場慣例可循；填你要追蹤的標的代號，"
        "系統不會為它套用任何市場的交易時段或來源優先序。"
    )
}


def target_format_hint(asset_class):
    """Return how one market spells a target, in that market's authority's words.

    The three market classes read it live from ``market_scopes()``'s
    ``symbol_resolution`` — its **first sentence**, which is where that field
    states the spelling convention before going on to the caveats a seat needs and
    a form has no room for. Taking the sentence rather than writing a short
    version here is what keeps the ask bar from holding a second, quietly stale
    copy of the convention: an edit to ``config/market_scopes.json`` reaches the
    form.

    A class no authority declares gets ``""`` — nothing to say is said by saying
    nothing, the same refusal to invent that :func:`asset_class_label` makes when
    it shows a stored class as it was recorded.
    """
    if asset_class in _NON_MARKET_TARGET_HINTS:
        return _NON_MARKET_TARGET_HINTS[asset_class]
    scope = market_scopes().get(asset_class)
    if scope is None:
        return ""
    return _first_sentence(scope.symbol_resolution)


def _first_sentence(text):
    """The text up to and including its first ideographic full stop."""
    head, mark, _rest = text.partition("。")
    return head + mark if mark else text

# -- the design tokens -------------------------------------------------------
#
# The whole site's colour, type and spacing live in
# :mod:`~hoya_market_agents.design_tokens`, which is the one authority the web
# app, the report renderer and the audit renderer all read. They are named here
# so a rule below can say ``var(--accent)`` and a test can say
# ``pages.PALETTE``, and for no other reason: this module owns no value.
#
# Every rule in :data:`_RULES` reaches the table through ``var()`` and names no
# value of its own, which is what makes "change it in one place" true rather
# than aspirational. ``tests/test_design_tokens.py`` measures the palette and
# asserts the set of colour literals in the finished sheet is exactly the set of
# values the palette holds.
#
# **One palette, not two.** Dark mode is retired (Spec R-004): there is no
# second set of values and :func:`stylesheet` emits no
# ``@media (prefers-color-scheme: dark)`` block, so the site is white whatever
# the operating system prefers.
PALETTE = design_tokens.PALETTE
SCALE = design_tokens.SCALE

# The colour a ratio is computed from, which is the palette with every glass
# surface flattened over what sits behind it. Named here because the contrast
# tests reach the table through this module.
MEASURED_COLOURS = design_tokens.MEASURED_COLOURS

BACKGROUND_TOKENS = design_tokens.BACKGROUND_TOKENS
TEXT_TOKENS = design_tokens.TEXT_TOKENS
LINE_TOKENS = design_tokens.LINE_TOKENS
DECOR_TOKENS = design_tokens.DECOR_TOKENS
TEXT_MINIMUM = design_tokens.TEXT_MINIMUM
LINE_MINIMUM = design_tokens.LINE_MINIMUM
CONTRAST_REQUIREMENTS = design_tokens.CONTRAST_REQUIREMENTS

# The colour token each ballot position *belongs to*, keyed on the class names
# :mod:`~hoya_market_agents.webapp.live` hands out. A test asserts the two sets
# line up, so a fourth position added there cannot arrive with no colour of its
# own declared, and every token named here is measured by ``ContrastTest``.
STANCE_COLOUR_TOKENS = {
    "stance-affirm": "affirm",
    "stance-oppose": "oppose",
    "stance-abstain": "abstain",
    "stance-unknown": "muted",
}

# Which of those classes a rule may actually **paint**, which is a different
# question from which colour they belong to.
#
# Spec R2 freezes the debate room's stance text: its content, position and
# semantic colour do not move, and the only things this design may change are
# type, spacing and card style. Before this design, three of the four rendered
# in the body colour and nothing else — the sheet the room shipped declared
# ``.stance-positive``, ``.stance-negative`` and ``.stance-neutral``, three class
# names :mod:`live` has never emitted, and no other page emits a stance class at
# all. So the three ballot positions had no semantic colour to keep. Merging the
# sheets would have given them one, and "the dead rule always meant to" is not an
# R2 authorisation.
#
# ``stance-unknown`` is here because it is the one the room's own sheet really
# did paint, and it still paints the same role (``muted``, one shade calibrated).
#
# The other three hues stay in the palettes and stay measured: they are the
# vocabulary whichever ticket is authorised to lift the R2 freeze will paint
# with. A test pins that no rule applies them today, so lifting the freeze is a
# decision somebody makes rather than a side effect of the next repaint.
PAINTED_STANCE_CLASSES = ("stance-unknown",)

# ``provider`` → the token its chat bubble's left stripe reads. The provider
# class comes from :mod:`~hoya_market_agents.seats`, so this is keyed on the
# roster's own families and a test asserts it covers every one of them: a fourth
# family added there fails here rather than reaching a page unmarked. Codex
# reads the accent because the room this design replaces painted the default
# stripe with its brand colour and only overrode the other two.
PROVIDER_STRIPE_TOKENS = {
    "codex": "accent",
    "claude": "provider_claude",
    "gemini": "provider_gemini",
}

# The live room's script. It is a route, not a file on disk, and not an inline
# block: that is what lets the room's policy stay ``script-src 'self'``.
LIVE_SCRIPT_PATH = "/live.js"

_EMPTY = "—"


PAGE_TITLE_HISTORY = "歷史與命中率"


def render_history_page(data):
    """Return the merged history and hit-rate page.

    One page, in the order the ticket asks for: what everything adds up to first,
    then the runs it adds up from, then the form that fills in a result no price
    could settle. Like every other page here it carries no script — the two forms
    are plain ``GET`` and ``POST`` submissions answered on the server.

    The hit-rate cards are absent, rather than empty, when the index cannot be
    read: :func:`_history_result` says so once, in ``run_index``'s own words, and
    a second card claiming zeroes above it would be a page answering a question
    it could not find out.
    """
    body = [
        _stats_write_notice(data["write"]),
        _problems(data["problems"]),
        _read_caveat(data["read_caveat"]),
        _hit_rate(data["totals"]),
        _stats_table(data["levels"]),
        _filter_form(data["submitted"]),
        _history_result(data),
        _manual_form(data),
    ]
    return _document(
        PAGE_TITLE_HISTORY,
        _header(
            PAGE_TITLE_HISTORY,
            data["data_root"],
            "/history",
            # A page about every run is a page about no run in particular, so its
            # two report tabs open the newest run that has one (Spec R-002). Not
            # the newest row it happens to be listing: the rows are whatever the
            # reader filtered for, and a tab that moved with the query would be a
            # different destination on every visit.
            report_run=latest_report_run(data["data_root"]),
        ),
        body,
        footer=HISTORY_FOOTER,
    )


def render_run_page(data, export=None, exported=()):
    """Return one run's detail page.

    ``export`` is what a submitted "匯出 PDF" did, and ``None`` when the page is
    simply being read. It is quoted rather than interpreted: whether anything was
    written is :mod:`~hoya_market_agents.webapp.pdf_export`'s answer, and the
    notice goes at the top for the same reason the history page's does — what the
    last thing you pressed did is the first thing you look for.

    ``exported`` is which of that run's PDFs are already on disk, looked up by the
    route because this module does no I/O. It decides whether the button is offered
    at all; see :func:`_run_export`.
    """
    run_id = data["run_id"]
    body = [
        _export_notice(export),
        _run_summary(data),
        _run_report(data),
        _run_export(data, exported),
        _run_votes(data),
        _run_evidence(data),
        _run_transcript(data),
    ]
    return _document(
        "run 詳情",
        _header(
            data["question"],
            "run_id：{}".format(run_id),
            None,
            # This page is about one run, so its two report tabs open that run's
            # own files — the ones :func:`~.views.run_data` has already looked
            # for. A page that showed another run's report beside this run's
            # votes would be two runs on one page.
            report_run={"run_id": run_id, "artifacts": data["artifacts"]},
        ),
        body,
        footer=RUN_DETAIL_FOOTER,
    )


def render_live_page(data, launch=None, suggestions=None):
    """Return the live chat room for one run.

    The layout is the two-column room the user asked for — the chat on the left,
    the tally and the seven seats on the right, and the rules, vote changes and
    evidence folded below — and its markup is untouched. What changed is the
    paint: the room reads :func:`stylesheet` like every other page instead of
    shipping a sheet of its own, so its type, spacing, radii and semantic hues
    are the site's, and its colours are measured by ``ContrastTest`` in both
    palettes rather than exempt from it.

    The rest of the wiring is as it was: the rule values are read live from the
    authority, the one script is an external file, the data is the reload-aware
    snapshot, and the labels come from the authorities. The page is complete
    without its script — the state at the moment it was rendered.

    ``suggestions`` is ``{asset_class: [target, ...]}`` from
    :func:`~hoya_market_agents.webapp.views.target_suggestions`; ``None`` renders
    the ask bar with every box empty, which is what a Data Root with no index yet
    looks like.
    """
    body = [
        _launch_form(launch, suggestions),
        _live_run_bar(data),
        _live_focus(data),
        _live_metrics(data),
        _live_layout(data),
        _live_secondary(data),
    ]
    return _document(
        "即時 Agent 辯論室",
        _live_header(data),
        body,
        scripts=(LIVE_SCRIPT_PATH,),
    )


# The state a run is in, in the words the room's header shows. Anything not
# named here is a run that is under way.
_LIVE_STATE_WORDS = {STATUS_WAITING: "等待新的 run", STATUS_FINISHED: "已完成"}


def _live_header(data):
    """The original ``.top`` header: eyebrow, title, question, tabs, connection."""
    question = data["question"] or "等待新的市場題目"
    state = data["state"]
    state_word = _LIVE_STATE_WORDS.get(state, "執行中")
    round_word = (
        "第 {} 輪".format(data["round"]) if data["round"] is not None else "尚未進入辯論"
    )
    return "\n".join(
        [
            '<header class="top">',
            '<div><p class="eyebrow">HOYA BIT 即時研究流程</p>',
            "<h1>即時 Agent 辯論室</h1>",
            '<p id="question">{}</p>'.format(_e(question)),
            '<p class="run-state"><span id="live-state" data-state="{}">{}</span>'
            '　<span id="live-round">{}</span>'
            '<time id="live-elapsed" data-elapsed-ms="{}" hidden>{}</time></p>'.format(
                _e(state), _e(state_word), _e(round_word),
                data["elapsed_ms"], _e(_clock(data["elapsed_ms"])),
            ),
            "</div>",
            '<div class="top-actions">',
            # The connection indicator leads the cluster rather than sitting
            # inside it: it is a state, not a control, and Spec R-003 puts 設定
            # immediately left of the stop button — which it cannot be with a
            # third thing between them.
            '<span class="connection" id="live-connection">連線中</span>',
            site_tabs("/", _live_report_run(data)),
            _stop_form(),
            "</div>",
            "</header>",
        ]
    )


def _live_report_run(data):
    """Which run the room's two report tabs open.

    The run being watched, whenever there is one: the room is a page about one
    run, and tabs that quietly opened another run's report would show a reader a
    conclusion that is not the one on screen — including while this run is still
    debating and has produced nothing yet, which is a disabled tab and not an
    invitation to read somebody else's answer.

    With no run to watch, the room is the front door before anything has been
    asked, and it offers the newest report exactly like the other pages that are
    about no run in particular (Spec R-002).
    """
    if not data["run_id"]:
        return latest_report_run(data["data_root"])
    return {
        "run_id": data["run_id"],
        # The snapshot has already looked for both files; it simply names them
        # in its own words rather than by file name.
        "artifacts": {
            REPORT_ARTIFACT: data["report_available"],
            DEBATE_ARTIFACT: data["debate_report_available"],
        },
    }


def _live_run_bar(data):
    """The original ``.run-bar``: which run, a picker for the finished ones, and
    the way back to the current run and to the history page."""
    run_id = data["run_id"]
    current = "<code>{}</code>".format(_e(run_id)) if run_id else "尚未選定"
    lines = [
        '<div class="run-bar">',
        "<span>目前 run：{}</span>".format(current),
        '<label for="run-picker">歷史 run</label>',
        '<select id="run-picker" name="run" aria-label="回看歷史 run">',
        _live_run_options(data),
        "</select>",
    ]
    if run_id:
        # Not a navigation link — the tab bar owns those. This switches the run
        # being watched back to the newest one after the picker moved it.
        lines.append('<a href="/live">回到目前 run</a>')
    lines.append("</div>")
    return "\n".join(lines)


def _live_run_options(data):
    options = data.get("run_options") or []
    head = "選擇要回看的 run…" if options else "尚無歷史 run"
    parts = ['<option value="">{}</option>'.format(_e(head))]
    for option in options:
        selected = " selected" if option.get("selected") else ""
        parts.append(
            '<option value="{}"{}>{}</option>'.format(
                _e(option["run_id"]), selected, _e(option["label"])
            )
        )
    return "".join(parts)


def _live_focus(data):
    """The original ``.focus-bar``: leading stance, the score, the light, and
    the next rule — the one line a glance is meant to land on."""
    focus = data["focus"]
    assets = "／".join(a for a in data.get("assets") or [] if a) or "市場"
    confidence = (
        _light(focus.get("confidence_level"))
        if focus.get("confidence_level")
        else "⚪ 信心尚未評估"
    )
    return "\n".join(
        [
            '<section class="focus-bar" aria-labelledby="focus-headline">',
            "<div>",
            '<p class="focus-asset" id="focus-asset">{}</p>'.format(_e(assets)),
            '<h2><span id="focus-headline">{}</span>'
            '<span class="focus-tally" id="focus-tally">{}</span></h2>'.format(
                _e(focus["headline"]), _e(focus["tally_text"])
            ),
            '<p class="focus-detail" id="focus-detail">{}</p>'.format(confidence),
            '<div id="live-outcome">{}</div>'.format(_outcome_block(data.get("outcome"))),
            "</div>",
            '<a class="focus-action" id="focus-action" href="#rules-detail">{}</a>'.format(
                _e(focus["next_label"])
            ),
            "</section>",
        ]
    )


def _live_metrics(data):
    """The four gauges: two countdowns, the phase, and the vote wall.

    The two countdowns carry ``data-countdown-from`` — the T+0 offset the clock
    is counting *down* to zero from — so the script advances them off the same
    elapsed value it advances ``#live-elapsed`` with, and neither is a second
    copy of the run's clock. The phase and the threshold are the snapshot's, in
    words; they are not counted between frames because a wall does not move on a
    tick, it moves at a milestone.
    """
    countdowns = [
        ("十五分鐘剩餘時間", data["total_remaining_ms"], "total-remaining"),
        ("報告期限剩餘時間", data["report_remaining_ms"], "report-remaining"),
    ]
    elapsed = data["elapsed_ms"]
    cells = [
        '<div class="metric"><small>{}</small>'
        '<strong id="live-{}" data-countdown-from="{}">{}</strong></div>'.format(
            _e(name), ident, remaining + elapsed, _e(_clock(remaining))
        )
        for name, remaining, ident in countdowns
    ]
    cells.append(
        '<div class="metric"><small>目前階段</small>'
        '<strong id="live-phase">{}</strong></div>'.format(_e(data["phase_label"]))
    )
    cells.append(
        '<div class="metric"><small>目前共識門檻</small>'
        '<strong id="live-threshold">{}</strong></div>'.format(
            _e(data["threshold_label"])
        )
    )
    return '<section class="metrics" aria-label="流程計時與門檻">{}</section>'.format(
        "".join(cells)
    )


def _live_layout(data):
    """The original two-column body: chat on the left, tally and seats on the
    right. The chat panel is first in the source and widest in the grid."""
    return "\n".join(
        [
            '<div class="live-layout">',
            _live_feed(data),
            "<aside>",
            _live_tally(data),
            _live_seats(data),
            "</aside>",
            "</div>",
        ]
    )


def _live_secondary(data):
    """The three folded panels below the room, in the original order."""
    return "\n".join(
        [
            '<div class="secondary-grid">',
            _live_rules(data),
            _live_vote_history(data),
            _live_evidence(data),
            "</div>",
        ]
    )


def _live_rules(data):
    """規則與時間線: the original rules panel — the milestone in force is marked
    current and the ones behind it dimmed. The values are read live from the
    authority; only the look is the original's."""
    elapsed = data["elapsed_ms"]
    rules = data["rules"]
    current = _current_rule_index(elapsed, rules)
    rows = "".join(_rule_row(rule, index, current) for index, rule in enumerate(rules))
    return _detail_panel(
        "rules-detail", "規則與時間線", '<div class="rules">{}</div>'.format(rows)
    )


def _current_rule_index(elapsed_ms, rules):
    current = -1
    for index, rule in enumerate(rules):
        if elapsed_ms >= rule["at_ms"]:
            current = index
    return current


def _rule_row(rule, index, current):
    if index == current:
        cls = "rule current"
    elif index < current:
        cls = "rule past"
    else:
        cls = "rule"
    votes = (
        "（門檻 {} 票）".format(rule["required_votes"])
        if rule["required_votes"]
        else ""
    )
    return '<div class="{}"><time>T+{}</time><span>{}{}</span></div>'.format(
        cls, _e(_clock(rule["at_ms"])), _e(rule["label"]), _e(votes)
    )


def _live_vote_history(data):
    """票數變化: every change of stance, in the order it happened — the original
    ``.history`` list, one compact row per change."""
    changes = data["changes"]
    if not changes:
        inner = "<p class=\"hint\">尚未投票。</p>"
    else:
        rows = "".join(_vote_history_row(change) for change in changes)
        inner = '<ol class="history">{}</ol>'.format(rows)
    return _detail_panel("vote-history-detail", "票數變化", inner)


def _vote_history_row(change):
    changed = change.get("before") is not None
    if changed:
        move = "{} → {}".format(
            change.get("before_label") or _EMPTY, change.get("after_label") or _EMPTY
        )
        flag = '<span class="history-flag">改票</span>'
        row_cls = "history-row changed"
    else:
        move = "首次表態：{}".format(change.get("after_label") or _EMPTY)
        flag = ""
        row_cls = "history-row"
    return (
        '<li class="{}"><time>T+{}</time>'
        '<span class="history-seat">{}</span>'
        '<span class="badge {}">{}</span>{}</li>'.format(
            row_cls,
            _e(_clock(change.get("elapsed_ms"))),
            _e(change.get("seat_label") or _EMPTY),
            _e(change.get("after_class") or "stance-unknown"),
            _e(move),
            flag,
        )
    )


def _live_evidence(data):
    """可驗證證據: the sealed evidence cards, shown the same way run detail does."""
    evidence = data["evidence"]
    if not evidence:
        inner = "<p class=\"hint\">證據將在證據快照封存後顯示。</p>"
    else:
        inner = '<ul class="evidence">{}</ul>'.format(
            "".join(_evidence_card(card) for card in evidence)
        )
    return _detail_panel("evidence-panel", "可驗證證據", inner)


def _detail_panel(panel_id, heading, inner):
    return "\n".join(
        [
            '<details class="detail-panel" id="{}">'.format(panel_id),
            "<summary>{}</summary>".format(_e(heading)),
            '<div class="detail-body">{}</div>'.format(inner),
            "</details>",
        ]
    )


def render_launch_problem_page(sentence, command=None, report_run=None):
    """Return the page for a launch this server will not start, and why.

    The same shape the history page uses when the index is missing: what
    happened, in the words of whoever refused it, and the one line that fixes it
    where there is one. No traceback reaches a reader either way.

    ``report_run`` is which run the two report tabs open, from the route that
    knows the Data Root; see :func:`site_tabs`. A refusal is a page about no run
    in particular, so it offers the newest report like every other one (Spec
    R-002) — a reader who has just been told they cannot start a run is a reader
    who may well want to read the last one.
    """
    lines = [
        '<section class="card empty" role="alert" aria-labelledby="launch-problem-heading">',
        '<h2 id="launch-problem-heading">這次沒有啟動</h2>',
        "<p>{}</p>".format(_e(sentence)),
    ]
    if command:
        lines.append("<p>在 WSL 的 Code Root 執行下面這一行，再回到本頁重試：</p>")
        lines.append("<pre><code>{}</code></pre>".format(_e(command)))
    lines.append('<p><a href="/live">回到聊天室直播</a></p>')
    lines.append("</section>")
    return _document(
        "無法啟動", _header("無法啟動", "", None, report_run=report_run), lines
    )


def render_settings_page(data):
    """Return the rule file as a form, with whatever happened to the last save.

    The page is drawn from :func:`settings.settings_data`, whose controls came
    out of the document itself — so a rule field added upstream appears here
    without this function being told about it.

    Nothing on this page is a verdict of its own. Every sentence beside a
    control is the loader's, and the control it sits beside is one the loader's
    sentence named.
    """
    body = [
        _settings_notice(data),
        _settings_problem(data),
        _settings_timeline(data),
        _settings_form(data),
        _settings_comments(data),
    ]
    return _document(
        "辯論規則設定",
        _header(
            "辯論規則設定",
            "設定檔：{}".format(data["rules_path"]),
            "/settings",
            # A page about the rules is about no run, so its two report tabs
            # open the newest run that has a report (Spec R-002).
            report_run=latest_report_run(data["data_root"]),
        ),
        body,
        footer=SETTINGS_FOOTER,
    )


# ``{state: (heading, ARIA role, CSS class)}`` for everything a save can end as
# except :data:`settings.LOCKED`, which the standing lock notice already covers.
# A test asserts this table and ``settings.STATES`` account for each other, so a
# new outcome cannot reach a reader with no words.
SETTINGS_NOTICES = {
    settings.SAVED: ("已存檔", "status", "saved"),
    settings.REFUSED: ("這次沒有存檔", "alert", "refused"),
    settings.UNREADABLE: ("讀不到設定檔", "alert", "refused"),
    settings.NOT_PUBLISHED: ("已寫入，但還沒有生效", "alert", "refused"),
    settings.NOTHING_SUBMITTED: ("這次沒有存檔", "alert", "refused"),
}


def _settings_notice(data):
    """The lock, or what became of the last submission. Never both."""
    if data["locked"]:
        return _settings_locked_notice(data)
    outcome = data["outcome"]
    if outcome is None:
        return ""
    heading, role, style = SETTINGS_NOTICES[outcome.state]
    lines = [
        '<section class="card notice {}" role="{}" '
        'aria-labelledby="settings-notice-heading">'.format(style, role),
        '<h2 id="settings-notice-heading">{}</h2>'.format(_e(heading)),
        "<p>{}</p>".format(_e(outcome.message or settings.SAVED_MESSAGE)),
    ]
    if outcome.fields:
        lines.append("<p>被指名的欄位：</p>")
        lines.append(
            "<ul>{}</ul>".format(
                "".join("<li><code>{}</code></li>".format(_e(p)) for p in outcome.fields)
            )
        )
    lines.append("</section>")
    return "\n".join(lines)


def _settings_locked_notice(data):
    return "\n".join(
        [
            '<section class="card notice locked" role="status" '
            'aria-labelledby="settings-locked-heading">',
            '<h2 id="settings-locked-heading">設定頁目前鎖定</h2>',
            "<p>{}</p>".format(_e(data["locked_message"])),
            '<p class="hint">進行中的 run：<code>{}</code></p>'.format(
                _e(data["locked_run_id"])
            ),
            '<p><a href="/live">到聊天室直播看它進行到哪裡</a></p>',
            "</section>",
        ]
    )


def _settings_problem(data):
    """Say that the file on disk is one the loader refuses, in its own words."""
    if not data["problem"]:
        return ""
    return "\n".join(
        [
            '<section class="card notice refused" role="alert" '
            'aria-labelledby="settings-problem-heading">',
            '<h2 id="settings-problem-heading">目前的設定檔不會被接受</h2>',
            "<p>{}</p>".format(_e(data["problem"])),
            '<p class="hint">下面的表單仍然顯示檔案裡的值，改好再存一次即可。</p>',
            "</section>",
        ]
    )


def _settings_timeline(data):
    """Draw each ``timeline_ms`` number as a length, and write it out as well.

    The bar is ``aria-hidden`` because it carries nothing the text beside it
    does not already say. It is not a claim about which numbers are instants and
    which are windows — see :func:`settings._timeline`.
    """
    rows = data["timeline"]
    if not rows:
        return ""
    items = "".join(
        '<li><span class="timeline-name">{}</span>'
        '<span class="timeline-bar" aria-hidden="true">'
        '<span class="timeline-fill" style="width:{}%"></span></span>'
        '<span class="timeline-value">{} ms（{}）</span></li>'.format(
            _e(row["label"]), row["percent"], _e(row["value"]), _e(row["clock"])
        )
        for row in rows
    )
    return "\n".join(
        [
            '<section class="card" aria-labelledby="settings-timeline-heading">',
            '<h2 id="settings-timeline-heading">時間軸</h2>',
            '<p class="hint">每一條的長度就是該欄位的毫秒數，起點都是 T+0；'
            "括號是換算後的 MM:SS。</p>",
            '<ul class="timeline">{}</ul>'.format(items),
            "</section>",
        ]
    )


def _settings_form(data):
    if data["document"] is None:
        return ""
    locked = data["locked"]
    lines = [
        '<form class="card" method="post" action="/settings" '
        'aria-labelledby="settings-form-heading">',
        '<h2 id="settings-form-heading">辯論規則</h2>',
        '<p class="hint">存檔會先交給載入器驗證；被拒絕時檔案不會有任何改動。'
        "存檔成功後從下一個開始的 run 生效。</p>",
    ]
    lines += [_settings_section(section, locked) for section in data["sections"]]
    lines += [
        '<div class="actions">',
        '<button class="primary" type="submit"{}>存檔</button>'.format(
            " disabled" if locked else ""
        ),
        "</div>",
        "</form>",
    ]
    return "\n".join(lines)


def _settings_section(section, locked):
    """One ``<fieldset>``: a container's name, its comment, and its controls.

    A refusal the loader aimed at the container rather than at one control — the
    last rung of the light ladder, say — is shown here and bound to the fieldset,
    so it reaches a screen reader on the way into the group instead of sitting
    beside a box it is not about.
    """
    anchor = section["path"] or "root"
    notes = []
    if section["about"]:
        notes.append(("about-{}".format(anchor), "hint", _e(section["about"])))
    if section["error"]:
        notes.append(
            (
                "error-{}".format(section["path"]),
                "field-error",
                "這一區被拒絕：{}".format(_e(section["error"])),
            )
        )
    described = (
        ' aria-describedby="{}"'.format(_e(" ".join(name for name, _, _ in notes)))
        if notes
        else ""
    )
    lines = [
        '<fieldset class="settings-group"{}>'.format(described),
        "<legend>{}</legend>".format(_settings_title(section)),
    ]
    lines += [
        '<p class="{}" id="{}">{}</p>'.format(style, _e(name), text)
        for name, style, text in notes
    ]
    lines.append('<div class="field-grid">')
    lines += [_settings_field(field, locked) for field in section["fields"]]
    lines += ["</div>", "</fieldset>"]
    return "\n".join(lines)


def _settings_title(part):
    """The name of one control or one group, with the mark when it has no 中文 one.

    The mark goes *inside* the label rather than beside it, so the reader who
    hears the label hears it too: "this box is the key the file spells, nobody
    has written a Chinese name for it yet". It is a note about the words, never
    about the value, and it changes nothing else about the control.
    """
    if not part["untranslated"]:
        return _e(part["label"])
    return '{} <span class="hint">{}</span>'.format(
        _e(part["label"]), _e(settings.UNTRANSLATED_NOTE)
    )


def _settings_field(field, locked):
    """One control, its plain sentence, its hint, and any refusal about it.

    Three kinds of prose sit under one box and each is bound with
    ``aria-describedby`` rather than left loose on the page: what the rule does
    (:data:`settings.FIELD_LABELS`, above the box because it is read before
    typing), what shape the value currently has, and — when there is one — why
    the last save was refused. "Which box is wrong" and "what is this box for"
    are the two things a screen reader cannot infer from a list of sentences.
    """
    path = field["path"]
    described = []
    if field["description"]:
        described.append("note-{}".format(path))
    described.append("hint-{}".format(path))
    if field["error"]:
        described.append("error-{}".format(path))
    attributes = [
        'id="{}"'.format(_e(path)),
        'name="{}"'.format(_e(path)),
        'type="text"',
        'value="{}"'.format(_e(field["value"])),
        'aria-describedby="{}"'.format(_e(" ".join(described))),
        'autocomplete="off"',
        'spellcheck="false"',
    ]
    if field["kind"] == settings.KIND_INTEGER:
        # A hint to the keyboard, not a gate on the value: ``type="number"``
        # would let the browser decide what may be sent, and then the loader's
        # own refusal — the only one this page shows — would never happen.
        attributes.append('inputmode="numeric"')
    if field["error"]:
        attributes.append('aria-invalid="true"')
    if locked:
        attributes.append("disabled")
    lines = [
        '<div class="field">',
        '<label for="{}">{}</label>'.format(_e(path), _settings_title(field)),
    ]
    if field["description"]:
        lines.append(
            '<p class="hint" id="note-{}">{}</p>'.format(
                _e(path), _e(field["description"])
            )
        )
    lines += [
        "<input {}>".format(" ".join(attributes)),
        '<p class="hint" id="hint-{}">{}</p>'.format(_e(path), _e(field["hint"])),
    ]
    if field["error"]:
        lines.append(
            '<p class="field-error" id="error-{}">這一欄被拒絕：{}</p>'.format(
                _e(path), _e(field["error"])
            )
        )
    lines.append("</div>")
    return "\n".join(lines)


def _settings_comments(data):
    """Show the comments the file carries; this page cannot edit them."""
    if not data["comments"]:
        return ""
    items = "".join(
        "<div><dt><code>{}</code></dt><dd>{}</dd></div>".format(_e(path), _e(text))
        for path, text in data["comments"]
    )
    return "\n".join(
        [
            '<section class="card" aria-labelledby="settings-about-heading">',
            '<h2 id="settings-about-heading">設定檔內的說明</h2>',
            '<p class="hint">這些是 JSON 裡以底線開頭的註解欄位，'
            "本頁只顯示，不修改。</p>",
            '<dl class="summary">{}</dl>'.format(items),
            "</section>",
        ]
    )


def _read_caveat(sentence):
    """Say that the statistics and the list may not be the same read, when they may.

    Above the statistics because that is the pair it is about, and a ``status``
    rather than an ``alert``: nothing is wrong and nothing was refused — the index
    was being written while this page read it. It sits beside the write notice
    rather than among the query problems, whose heading is about conditions that
    were not applied.

    Neither the heading nor the sentence names how far apart the two halves are:
    what :func:`~.views._one_version_read` detects is that the index changed, not
    how many times it changed.
    """
    if not sentence:
        return ""
    return (
        '<section class="card" role="status" aria-labelledby="read-caveat-heading">'
        '<h2 id="read-caveat-heading">統計與列表可能來自不同的索引版本</h2>'
        "<p>{}</p></section>".format(_e(sentence))
    )


# What the hit-rate card counts, said on the card. The list below it is filtered
# and the card is not: the card is the whole index, always, because its numbers
# come from the one authority that counts them
# (``run_index.outcome_summary``) rather than from the rows on screen. A card that
# did not say so would read as a hit rate for the runs a reader can see.
WHOLE_INDEX_NOTE = "統計涵蓋索引中的全部 run，不受下方查詢條件影響。"


def _hit_rate(totals):
    """The one number, its denominator, and the counts it was made from.

    ``None`` is the index this page could not read, and the answer to it is no
    card at all: :func:`_history_result` states that once, and a card of zeroes
    beside it would be an answer where there is none.
    """
    if totals is None:
        return ""
    rate = totals["hit_rate"]
    shown = NO_HIT_RATE if rate is None else "{:.1f}%".format(rate * 100)
    cells = "".join(
        '<div class="stat"><dt>{}<span class="mark" aria-hidden="true"> {}</span></dt>'
        '<dd class="outcome-{}">{}</dd></div>'.format(
            _e(OUTCOME_WORDS[state][0]),
            _e(OUTCOME_WORDS[state][1]),
            OUTCOME_WORDS[state][2],
            totals[state],
        )
        for state in OUTCOME_ORDER
    )
    return "\n".join(
        [
            '<section class="card" aria-labelledby="rate-heading">',
            '<h2 id="rate-heading">整體命中率</h2>',
            '<p class="hit-rate">{}</p>'.format(_e(shown)),
            "<p>命中率的算法是 {}，共 {} 筆可計分、{} 筆預測。</p>".format(
                _e(HIT_RATE_FORMULA), totals["scored"], totals["total"]
            ),
            "<p>{}</p>".format(_e(HIT_RATE_NOTE)),
            '<p class="hint">{}</p>'.format(_e(WHOLE_INDEX_NOTE)),
            '<dl class="stat-row">{}</dl>'.format(cells),
            "</section>",
        ]
    )


def _stats_table(levels):
    """One row per light, or nothing at all when there is no light to report."""
    if not levels:
        return ""
    columns = ("燈號", "命中率", "命中", "未命中", "待驗證", "不可自動驗證", "紀錄無法讀取", "合計")
    head = "".join('<th scope="col">{}</th>'.format(_e(name)) for name in columns)
    body = "".join(_stats_row(level) for level in levels)
    caption = "各燈號的命中率，分母同樣是 {}。".format(HIT_RATE_FORMULA)
    return "\n".join(
        [
            '<section class="card" aria-labelledby="levels-heading">',
            '<h2 id="levels-heading">各燈號命中率</h2>',
            '<div class="table-scroll">',
            "<table>",
            "<caption>{}</caption>".format(_e(caption)),
            "<thead><tr>{}</tr></thead>".format(head),
            "<tbody>{}</tbody>".format(body),
            "</table>",
            "</div>",
            "</section>",
        ]
    )


def _stats_row(level):
    rate = level["hit_rate"]
    cells = [
        "<th scope=\"row\">{}</th>".format(
            _light(level["level"]) if level["level"] else _e("未記錄燈號")
        ),
        "<td>{}</td>".format(
            _e(NO_HIT_RATE if rate is None else "{:.1f}%".format(rate * 100))
        ),
    ]
    cells += [
        '<td class="outcome-{}">{} {}</td>'.format(
            OUTCOME_WORDS[state][2], _e(OUTCOME_WORDS[state][1]), level[state]
        )
        for state in OUTCOME_ORDER
    ]
    cells.append("<td>{}</td>".format(level["total"]))
    return "<tr>{}</tr>".format("".join(cells))


def _stats_write_notice(write):
    """Quote what the last manual submission did, in the words it came back with.

    Nothing is decided here: the state and the sentence both come from
    :mod:`~hoya_market_agents.webapp.outcome`, which is the only thing that
    knows whether a record was written, refused, or found unreadable.
    """
    if write is None:
        return ""
    saved = write.state == outcome_module.WRITTEN
    return (
        '<section class="card {}" role="{}" aria-labelledby="write-heading">'
        '<h2 id="write-heading">{}</h2><p>{}</p></section>'.format(
            "saved" if saved else "refused",
            "status" if saved else "alert",
            _e("已記錄" if saved else "這次沒有記錄"),
            _e(write.message),
        )
    )


# How many waiting runs the list under the form spells out. The datalist beside
# the input carries every one of them, so nothing is unreachable; this only
# bounds how much of the page one visit spends on it — and when it bites, the
# caption below says so rather than letting a reader believe they saw them all.
MANUAL_LIST_LIMIT = 20


def _manual_form(data):
    """The fallback: enter a result by hand when no price could settle it.

    The runs offered are the ones a write would accept — finished, and past
    their deadline. A run still under way or still inside its period is not
    listed, because ``outcome.json`` is written once: a verdict entered before
    the prediction has happened is one nobody can correct afterwards.
    """
    options = "".join(
        '<option value="{}">{}</option>'.format(_e(verdict), _e(OUTCOME_WORDS[verdict][0]))
        for verdict in OUTCOME_VERDICTS
    )
    pending = data["pending_runs"]
    listed = _datalist(
        "pending-runs", ["{}".format(run["run_id"]) for run in pending]
    )
    rows = "".join(
        "<li><code>{}</code>｜{}｜{}</li>".format(
            _e(run["run_id"]), _e(run["run_date"] or _EMPTY), _e(run["question"])
        )
        for run in pending[:MANUAL_LIST_LIMIT]
    )
    shown = min(len(pending), MANUAL_LIST_LIMIT)
    caption = (
        "共 {} 個，以下列出 {} 個；其餘可在上面的欄位直接輸入 run_id。".format(
            len(pending), shown
        )
        if len(pending) > shown
        else "共 {} 個。".format(len(pending))
    )
    return "\n".join(
        [
            '<section class="card" aria-labelledby="manual-heading">',
            '<h2 id="manual-heading">人工輸入結果</h2>',
            "<p>報價服務取不到價、或這一題本來就沒有可對照的價格時，"
            "可以在這裡自己填。每個 run 只能填一次，填過就不會被覆寫；"
            "因此只有「已完成而且分析期間已經到期」的 run 才收，還沒到期的填了就改不回來。</p>",
            '<form method="post" action="/history">',
            '<div class="field"><label for="run_id">run_id</label>'
            '<input id="run_id" name="run_id" list="pending-runs" required></div>',
            listed,
            '<div class="field"><label for="verdict">結果</label>'
            '<select id="verdict" name="verdict">{}</select></div>'.format(options),
            '<div class="field"><label for="actual_price">實際價格（可留空）</label>'
            '<input id="actual_price" name="actual_price" inputmode="decimal"></div>',
            '<div class="field"><label for="note">備註（可留空）</label>'
            '<input id="note" name="note"></div>',
            # The same class every other submit button on the site wears. It had
            # none, which under the old sheet was invisible and under this one is
            # a browser's default grey button in the middle of a page that has
            # none: 全站一致 is the requirement, and a control that opts out of
            # the design system is the one place a reader notices it.
            '<button class="primary" type="submit">記錄結果</button>',
            "</form>",
            "<h3>已到期、還沒有結果的 run</h3>",
            "<p class=\"hint\">{}</p>".format(_e(caption)),
            "<ul>{}</ul>".format(
                rows or "<li class=\"empty\">目前沒有等待對答案的 run。</li>"
            ),
            "</section>",
        ]
    )


def render_not_found_page(what, report_run=None):
    """Return the page for a URL that names nothing this server has.

    ``report_run`` is which run the two report tabs open, from the route that
    knows the Data Root; see :func:`site_tabs`. A page for a URL that named
    nothing is a page about no run, so it offers the newest report like the rest
    of the site (Spec R-002): a mistyped URL is exactly when a reader needs the
    way onwards to be one click.

    It defaults to ``None`` — the two tabs disabled — because this page is also
    what the request boundary sends when a page failed to render at all, and
    that reply must not depend on reading anything else.
    """
    body = [
        '<section class="card" aria-labelledby="missing-heading">',
        '<h2 id="missing-heading">找不到這個頁面</h2>',
        "<p>{}</p>".format(_e(what)),
        '<p><a href="/history">回到{}</a></p>'.format(_e(PAGE_TITLE_HISTORY)),
        "</section>",
    ]
    return _document(
        "找不到頁面", _header("找不到頁面", "", None, report_run=report_run), body
    )


def render_shutdown_page():
    """Return the last page this server sends: the answer to the stop button.

    It is drawn before the server stops rather than after — that is the whole
    point of the endpoint's ordering — so it is written as what is about to be
    true of *this reply*, and it does not claim anything it cannot know. The one
    thing it can say for certain is where the way back is, which is why the
    shortcut is named.

    **It carries neither the stop button nor the site navigation**, and both
    absences are the same honesty: by the time it is on screen there is nothing
    left to stop and no link on it that would reach anything.
    """
    body = [
        '<section class="card" aria-labelledby="closed-heading">',
        '<h2 id="closed-heading">這是這個伺服器送出的最後一頁</h2>',
        "<p>送出這一頁之後它就停止監聽，這個分頁裡的連結都不會再有反應。</p>",
        "<p>要再打開辯論室，用工作區的「{}」捷徑。</p>".format(
            _e(RESTART_SHORTCUT_NAME)
        ),
        "</section>",
    ]
    return _document(
        SHUTDOWN_PAGE_TITLE,
        _closed_header(SHUTDOWN_PAGE_TITLE),
        body,
        footer=SHUTDOWN_FOOTER,
    )


# -- page furniture ---------------------------------------------------------


# What the foot of a page says it did. The default is true of every page that
# only reads; the settings page writes one file and says so instead, because a
# page that writes while claiming it does not is worse than no footer at all.
READ_ONLY_FOOTER = "本頁只讀取 run artifact，不會修改任何一份紀錄。"
SETTINGS_FOOTER = (
    "本頁會寫入辯論規則設定檔；run artifact 一律只讀，不會修改任何一份紀錄。"
)
# The history and hit-rate page is the one page that writes a run artifact, and
# it says so. It creates ``outcome.json`` for a run whose analysis period has run
# out, once, and changes nothing that was already written. It carries the manual
# entry form as well, so this is the footer whether the write came from the sweep
# or from a submission.
HISTORY_FOOTER = (
    "本頁會為分析期間已到期的 run 新增 outcome.json；"
    "既有的 run artifact 一律只讀，不會修改任何一份紀錄。"
)
# The run detail page carries the export button, so it is a page that can write —
# and it says which files and under what condition. Reading it writes nothing;
# what writes is pressing the button, and the sentence is worded as that rather
# than as "this page writes", which would be false of every visit that only reads.
RUN_DETAIL_FOOTER = (
    "本頁的「{}」只會在這個 run 的資料夾新增 {}，已經有就拒絕、不覆寫；"
    "既有的 run artifact 一律只讀，不會修改任何一份紀錄。".format(
        EXPORT_PDF_LABEL, "與".join(pdf_export.EXPORT_TARGETS)
    )
)
# The one page whose own arrival changes something outside the browser, so the
# read-only sentence would be beside the point here: what it says instead is what
# stopping does write, which is one line in the web app's own log.
SHUTDOWN_FOOTER = (
    "伺服器停止時會在 webapp.jsonl 寫下 server_stop；"
    "run artifact 一律只讀，不會修改任何一份紀錄。"
)


def _document(title, header, sections, scripts=(), footer=READ_ONLY_FOOTER):
    """Wrap one page.

    There is no ``style`` argument to override: every page carries the same
    :func:`stylesheet`. The room used to pass its own here, which is how the
    site came to have two of everything.
    """
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-Hant">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>{}・{}</title>".format(_e(title), _e(SITE_TITLE)),
            "<style>{}</style>".format(stylesheet()),
            "\n".join(
                '<script src="{}" defer></script>'.format(source) for source in scripts
            ),
            "</head>",
            "<body>",
            '<a class="skip-link" href="#main">跳到主要內容</a>',
            header,
            '<main id="main" tabindex="-1">',
            "\n".join(section for section in sections if section),
            "</main>",
            '<footer class="site-footer"><p>{}</p></footer>'.format(_e(footer)),
            "</body>",
            "</html>",
            "",
        ]
    )


def _header(heading, note, current_path, report_run=None):
    """The card-system pages' header: title, note, navigation, and the stop button.

    ``current_path`` is this page's own path, so its tab is the current one.
    Every page gets the same navigation — there is no page you can open and not
    be able to leave — and every page gets the same stop button, because "收工"
    is not something one page happens to be able to do (Spec R4).

    ``report_run`` is which run the two report tabs open; see :func:`site_tabs`.
    It is left out by the two pages assembled from a sentence rather than from a
    Data Root — the launch refusal and the not-found page — which therefore offer
    the two tabs disabled, the same as a site with no report at all.

    The navigation and the button sit in the same ``.top-actions`` group the room's
    header uses, so the top right corner of this site is one control cluster
    described in one rule rather than two that happen to line up. 設定 is the last
    tab in that cluster, which is what puts it immediately left of the button
    (Spec R-003).
    """
    return "\n".join(
        [
            '<header class="site-header">',
            '<div class="header-top">',
            _header_title(heading, note),
            '<div class="top-actions">',
            site_tabs(current_path, report_run),
            _stop_form(),
            "</div>",
            "</div>",
            "</header>",
        ]
    )


def _closed_header(heading):
    """The one header with no navigation and no stop button.

    :func:`render_shutdown_page` is the only page that gets it, and the reason is
    in that function: every link this site could offer is about to stop answering.
    """
    return "\n".join(
        [
            '<header class="site-header">',
            '<div class="header-top">',
            _header_title(heading, ""),
            "</div>",
            "</header>",
        ]
    )


def _header_title(heading, note):
    """The left-hand side of a header: what this page is, and its one caption."""
    lines = ["<div>", "<h1>{}</h1>".format(_e(heading))]
    if note:
        lines.append('<p class="site-note">{}</p>'.format(_e(note)))
    lines.append("</div>")
    return "\n".join(lines)


def _stop_form():
    """The stop button, as a form rather than a link or a script.

    A ``POST`` because stopping is not something a URL should do when it is merely
    opened — the endpoint answers ``GET`` with a 404 saying so — and a plain form
    because this site carries no inline script and this button is not the reason
    to start.
    """
    return "\n".join(
        [
            '<form class="stop-form" method="post" action="{}">'.format(SHUTDOWN_PATH),
            '<button type="submit">{}</button>'.format(_e(SHUTDOWN_LABEL)),
            "</form>",
        ]
    )


def _problems(problems):
    if not problems:
        return ""
    items = "".join("<li>{}</li>".format(_e(problem)) for problem in problems)
    return (
        '<section class="card problems" role="alert" aria-labelledby="problems-heading">'
        '<h2 id="problems-heading">這些條件沒有套用</h2>'
        "<ul>{}</ul></section>".format(items)
    )


ANY_VALUE = "（不限）"


def _filter_form(submitted):
    fields = [
        _field("date_from", "日期起", submitted, kind="date", hint="以台北日期分層"),
        _field("date_to", "日期迄", submitted, kind="date", hint="含當天"),
        _choice_field(
            "asset_class",
            "資產類別",
            submitted,
            [(value, asset_class_label(value)) for value in ASSET_CLASSES],
            hint="選單為目前設定檔宣告的類別；其他值仍可用網址參數查詢",
        ),
        _choice_field(
            "confidence",
            "燈號",
            submitted,
            [(level, CONFIDENCE_WORDS[level]) for level in CONFIDENCE_LEVELS],
            hint="選單為報告契約宣告的級別；其他值仍可用網址參數查詢",
        ),
        _field("keyword", "題目關鍵字", submitted, kind="search", hint="字面子字串比對"),
        _field("limit", "筆數上限", submitted, kind="number", hint="0 表示不列出任何一筆"),
    ]
    return "\n".join(
        [
            '<form class="card filters" method="get" action="/history" '
            'aria-labelledby="filters-heading">',
            '<h2 id="filters-heading">查詢條件</h2>',
            '<div class="field-grid">',
            "\n".join(fields),
            "</div>",
            '<div class="actions">',
            '<button class="primary" type="submit">查詢</button>',
            '<a class="secondary" href="/history">清除條件</a>',
            "</div>",
            "</form>",
        ]
    )


def _field(name, label, submitted, kind="text", hint=None):
    """One typed-in filter. The ones whose values an authority declares are
    :func:`_choice_field`'s, so nothing here suggests a stored key."""
    attributes = [
        'id="{}"'.format(name),
        'name="{}"'.format(name),
        'type="{}"'.format(kind),
        'value="{}"'.format(_e(submitted.get(name, ""))),
    ]
    if kind == "number":
        attributes.append('min="0"')
    if hint:
        attributes.append('aria-describedby="{}-hint"'.format(name))
    lines = [
        '<div class="field">',
        '<label for="{}">{}</label>'.format(name, _e(label)),
        "<input {}>".format(" ".join(attributes)),
    ]
    if hint:
        lines.append(
            '<p class="hint" id="{}-hint">{}</p>'.format(name, _e(hint))
        )
    lines.append("</div>")
    return "\n".join(lines)


def _choice_field(name, label, submitted, options, hint=None):
    """One filter whose values are an authority's, shown in this site's language.

    A ``<select>`` rather than a text box beside a ``<datalist>``: the suggestions
    a datalist shows *are* its values, so ``tw_stock`` and ``green`` were on
    screen in a page that is supposed to be Traditional Chinese throughout
    (Spec R7). Here the word is what a reader picks and the stored key is what the
    query gets, which is the same round trip the URL always had.

    A submitted value no authority declares — one typed into the address bar, or a
    class the index holds and this build does not know — is added as the selected
    option under its own name. Dropping it would silently answer a different
    question than the one the URL asked, and inventing a word for it would be
    worse; this is the same decision :func:`asset_class_label` and :func:`_light`
    make about a value they cannot name.
    """
    chosen = submitted.get(name, "")
    known = [value for value, _word in options]
    listed = list(options)
    if chosen and chosen not in known:
        listed.append((chosen, chosen))
    lines = [
        '<div class="field">',
        '<label for="{}">{}</label>'.format(name, _e(label)),
        '<select id="{}" name="{}"{}>'.format(
            name, name, ' aria-describedby="{}-hint"'.format(name) if hint else ""
        ),
        '<option value=""{}>{}</option>'.format(
            "" if chosen else " selected", _e(ANY_VALUE)
        ),
    ]
    lines += [
        '<option value="{}"{}>{}</option>'.format(
            _e(value), " selected" if value == chosen else "", _e(word)
        )
        for value, word in listed
    ]
    lines.append("</select>")
    if hint:
        lines.append('<p class="hint" id="{}-hint">{}</p>'.format(name, _e(hint)))
    lines.append("</div>")
    return "\n".join(lines)


def _datalist(identifier, values):
    options = "".join('<option value="{}"></option>'.format(_e(v)) for v in values)
    return '<datalist id="{}">{}</datalist>'.format(identifier, options)


# -- history result ---------------------------------------------------------


def _history_result(data):
    state = data["state"]
    if state != STATE_OK:
        return _index_unavailable(state, data)
    if not data["rows"]:
        return (
            '<section class="card empty" aria-labelledby="empty-heading">'
            '<h2 id="empty-heading">沒有符合條件的 run</h2>'
            "<p>放寬或清除上面的條件再查一次。</p></section>"
        )
    return _history_table(data["rows"], data["capped_at"])


def _index_unavailable(state, data):
    heading = (
        "尚未建立查詢索引" if state == STATE_INDEX_MISSING else "查詢索引無法讀取"
    )
    lead = (
        "這個 Data Root 還沒有可查詢的索引，所以沒有歷史可以列出。"
        if state == STATE_INDEX_MISSING
        else "索引檔存在但讀不出來。它是可重建的衍生資料，重建一次即可。"
    )
    command = "python3 -m hoya_market_agents index-backfill --data-root {}".format(
        data["data_root"]
    )
    return "\n".join(
        [
            '<section class="card empty" aria-labelledby="no-index-heading">',
            '<h2 id="no-index-heading">{}</h2>'.format(_e(heading)),
            "<p>{}</p>".format(_e(lead)),
            '<p class="hint">{}</p>'.format(_e(data["reason"] or "")),
            "<p>在 WSL 的 Code Root 執行下面這一行，再重新整理本頁：</p>",
            "<pre><code>{}</code></pre>".format(_e(command)),
            "</section>",
        ]
    )


# What a run that never produced a report is shown as, and what a run that
# recorded no consensus status is. Both are states rather than data, so they are
# words here and not a dash: a reader of an empty cell cannot tell "this run has
# no report" from "this page did not fill the cell in" (Spec R1, R7).
NO_REPORT_STATUS = "未產出報告"
UNRECORDED_CONSENSUS_STATUS = "未記錄共識狀態"


def _history_table(rows, capped_at=None):
    columns = ("日期", "題目", "標的", "狀態", "燈號", "採納立場", "票數", "命中結果")
    head = "".join('<th scope="col">{}</th>'.format(_e(name)) for name in columns)
    body = "".join(_history_row(row) for row in rows)
    caption = "共 {} 筆，依日期由新到舊。".format(len(rows))
    if capped_at is not None:
        caption += "已達本次筆數上限 {}，可能還有更多；調高「筆數上限」再查一次。".format(
            capped_at
        )
    return "\n".join(
        [
            '<section class="card" aria-labelledby="result-heading">',
            '<h2 id="result-heading">查詢結果</h2>',
            '<div class="table-scroll">',
            "<table>",
            "<caption>{}</caption>".format(_e(caption)),
            "<thead><tr>{}</tr></thead>".format(head),
            "<tbody>{}</tbody>".format(body),
            "</table>",
            "</div>",
            "</section>",
        ]
    )


def _history_row(row):
    run_id = row.get("run_id") or ""
    question = row.get("question") or run_id
    cells = [
        '<td class="nowrap">{}</td>'.format(_e(row.get("run_date") or _EMPTY)),
        '<td><a href="/run/{}">{}</a></td>'.format(_path(run_id), _e(question)),
        "<td>{}</td>".format(_e("、".join(row.get("assets") or []) or _EMPTY)),
        "<td>{}</td>".format(_e(_run_status(row))),
        "<td>{}</td>".format(_light(row.get("confidence_level"))),
        "<td>{}</td>".format(_e(row.get("adopted_label") or _EMPTY)),
        "<td>{}</td>".format(_tally(row.get("tally_view"))),
        _outcome_cell(row.get("outcome_state")),
    ]
    return "<tr>{}</tr>".format("".join(cells))


def _run_status(row):
    """What one run ended as, in one cell that is never blank.

    A run whose report was never written is the state Spec R1 asks for by name,
    and it is read from the file rather than inferred from an empty column. Every
    other run is shown under the consensus status it recorded — including a status
    no authority declares, which is carried through as recorded rather than
    replaced by a word nothing supports.
    """
    if not row.get("report_available"):
        return NO_REPORT_STATUS
    return row.get("consensus_label") or UNRECORDED_CONSENSUS_STATUS


def _outcome_cell(state):
    """One run's verdict: a mark, a word, and colour as the third signal.

    The same three-signal rule the hit-rate card follows, for the same reason —
    green and red alone are not a difference every reader can see.
    """
    word, mark, token = OUTCOME_WORDS[state]
    return (
        '<td class="outcome-{}">'
        '<span class="mark" aria-hidden="true">{}</span> {}</td>'.format(
            token, _e(mark), _e(word)
        )
    )


def _light(level):
    if not level:
        return _e(_EMPTY)
    icon = CONFIDENCE_ICONS.get(level, "")
    word = CONFIDENCE_WORDS.get(level, level)
    return '<span class="light">{}{}</span>'.format(
        "{} ".format(icon) if icon else "", _e(word)
    )


def _tally(tally_view):
    if not tally_view:
        return _e(_EMPTY)
    return _e(
        "・".join(
            "{} {}".format(entry["label"], entry["count"]) for entry in tally_view
        )
    )


# -- run detail -------------------------------------------------------------


def _run_summary(data):
    consensus = data["consensus"]
    confidence = data["confidence"] or {}
    rows = [
        ("run_id", data["run_id"]),
        ("日期", data["run_date"]),
        ("資產類別", asset_class_label(data["asset_class"])),
        ("標的", "、".join(data["assets"]) or _EMPTY),
        ("題型", data["question_type"] or _EMPTY),
        ("共識狀態", consensus["status_label"] or _EMPTY),
        ("採納立場", consensus["adopted_label"] or _EMPTY),
        ("停止原因", consensus["stop_reason"] or _EMPTY),
    ]
    items = "".join(
        "<div><dt>{}</dt><dd>{}</dd></div>".format(_e(name), _e(value))
        for name, value in rows
    )
    light = _light(confidence.get("level"))
    tally = _tally(consensus["tally_view"])
    return "\n".join(
        [
            '<section class="card" aria-labelledby="summary-heading">',
            '<h2 id="summary-heading">這一場的結論</h2>',
            '<p class="verdict">燈號 {}　票數 {}</p>'.format(light, tally),
            "<dl class=\"summary\">{}</dl>".format(items),
            "</section>",
        ]
    )


def _run_report(data):
    if not data["artifacts"].get("report.html"):
        # ``artifacts`` is a plain "is this file there", so its absence really
        # is the reason and there is nothing else to report.
        return _missing_block("report-heading", "正式報告", "尚未產生 report.html")
    source = "/run/{}/report.html".format(_path(data["run_id"]))
    return "\n".join(
        [
            '<section class="card" aria-labelledby="report-heading">',
            '<h2 id="report-heading">正式報告</h2>',
            '<p><a href="{}">在新分頁開啟完整報告</a></p>'.format(source),
            '<iframe class="report-frame" src="{}" title="這一場的正式報告 report.html" '
            'loading="lazy"></iframe>'.format(source),
            "</section>",
        ]
    )


def _run_export(data, exported=()):
    """The one control on this page, in whichever of its three states applies.

    **The button is shown disabled rather than hidden**, so a reader who came
    looking for it learns why it cannot be pressed instead of wondering whether
    this build has it. Two things can disable it, and both are facts about this
    run's directory: the pages to print are not there yet, or the PDFs already
    are — :mod:`~hoya_market_agents.webapp.pdf_export` refuses to overwrite its
    own output, so offering the button in that state would be offering a press
    that is answered with a refusal. Which names matter in either case is read
    from that module rather than copied here.

    ``exported`` is handed in because this module does no I/O: the route looks and
    this draws. That is the same seam the endpoint's refusal reads, so the disabled
    button and the refusal are one decision.

    A disabled ``<button>`` is inert in the way the room's disabled artifact tabs
    had to be made inert by hand: it is out of the tab order and submits nothing
    on Enter, natively. ``aria-disabled`` is carried alongside so the two disabled
    states on this site announce themselves the same way.
    """
    missing = [
        name for name in pdf_export.EXPORT_SOURCES if not data["artifacts"].get(name)
    ]
    refuses = bool(missing or exported)
    return "\n".join(
        [
            '<section class="card" aria-labelledby="export-heading">',
            '<h2 id="export-heading">{}</h2>'.format(_e(EXPORT_PDF_LABEL)),
            "<p>{}</p>".format(_e(_export_sentence(missing, exported))),
            '<form method="post" action="{}">'.format(export_pdf_path(data["run_id"])),
            '<div class="actions">',
            '<button class="primary" type="submit"{}>{}</button>'.format(
                ' disabled aria-disabled="true"' if refuses else "",
                _e(EXPORT_PDF_LABEL),
            ),
            "</div>",
            "</form>",
            "</section>",
        ]
    )


def _export_sentence(missing, exported):
    """What pressing the button will do, or why it will not be pressed.

    The order matches the endpoint's: a run that already has its PDFs is done,
    whether or not the pages that produced them are still there.
    """
    if exported:
        return (
            "這個 run 已經有 {} 了。這裡不覆寫任何既有檔案，所以現在不能匯出；"
            "要重做請先自己把舊的 PDF 移走或刪掉。".format("與".join(exported))
        )
    if missing:
        return "這個 run 還沒有 {}，沒有可以轉檔的來源，所以現在不能匯出。".format(
            "與".join(missing)
        )
    return (
        "把這個 run 現成的 {} 轉成 {}，存進這個 run 的資料夾；"
        "既有的檔案一個都不會改。".format(
            "與".join(pdf_export.EXPORT_SOURCES), "與".join(pdf_export.EXPORT_TARGETS)
        )
    )


# ``{state: (heading, ARIA role, CSS class)}`` for every state an export can be
# answered with — that is, every state except ``RUN_MISSING``, which is a 404 and
# never reaches this page. One heading per state rather than "worked / did not":
# "已經匯出過了" and "這次沒有匯出" are different answers, and a heading that
# covered both would be the page's own version of a message that does not match
# the directory. A test asserts this table and ``pdf_export.STATES`` account for
# each other, so a sixth state cannot reach a reader under another one's words.
# ``IN_PROGRESS`` wears the settings page's lock styling rather than a refusal's:
# nothing is wrong and nothing was refused on its merits — something else is
# happening and this reader is being told to look again shortly.
EXPORT_NOTICES = {
    pdf_export.EXPORTED: ("已匯出 PDF", "status", "saved"),
    pdf_export.ALREADY_EXPORTED: ("已經匯出過了", "alert", "refused"),
    pdf_export.IN_PROGRESS: ("正在匯出中", "status", "notice locked"),
    pdf_export.SOURCE_MISSING: ("還不能匯出", "alert", "refused"),
    pdf_export.CONVERSION_FAILED: ("這次沒有匯出", "alert", "refused"),
}


def _export_notice(export):
    """Quote what the last export did, in the words it came back with.

    Nothing is decided here — not even whether it worked:
    :mod:`~hoya_market_agents.webapp.pdf_export` is the only thing that knows what
    is on disk, and this shows its sentence under the heading its state carries.
    Everything but a success is ``role="alert"`` because it is the answer to
    something the reader just pressed and it did not happen.
    """
    if export is None:
        return ""
    heading, role, style = EXPORT_NOTICES[export.state]
    return (
        '<section class="card {}" role="{}" aria-labelledby="export-notice-heading">'
        '<h2 id="export-notice-heading">{}</h2><p>{}</p></section>'.format(
            style, role, _e(heading), _e(export.message)
        )
    )


def _run_votes(data):
    if not data["seats"]:
        return _missing_block(
            "votes-heading",
            "七席投票與改票",
            data["notes"].get("votes.json") or "votes.json 沒有任何席位紀錄",
        )
    columns = ("席位", "第一輪立場", "最終立場", "改票", "最終理由")
    head = "".join('<th scope="col">{}</th>'.format(_e(name)) for name in columns)
    body = "".join(_seat_row(seat) for seat in data["seats"])
    return "\n".join(
        [
            '<section class="card" aria-labelledby="votes-heading">',
            '<h2 id="votes-heading">七席投票與改票</h2>',
            '<div class="table-scroll">',
            "<table>",
            "<caption>共 {} 席，立場以該場選項的用語呈現。</caption>".format(
                len(data["seats"])
            ),
            "<thead><tr>{}</tr></thead>".format(head),
            "<tbody>{}</tbody>".format(body),
            "</table>",
            "</div>",
            "</section>",
        ]
    )


def _seat_row(seat):
    cells = [
        '<th scope="row">{}<span class="hint">{}</span></th>'.format(
            _e(seat["seat_label"] or ""), _e(seat["seat_id"] or "")
        ),
        "<td>{}</td>".format(_e(seat["initial_stance_label"] or _EMPTY)),
        "<td>{}</td>".format(_e(seat["final_stance_label"] or _EMPTY)),
        "<td>{}</td>".format(_seat_change(seat)),
        "<td>{}</td>".format(_e(seat["final_public_reason"] or _EMPTY)),
    ]
    return "<tr>{}</tr>".format("".join(cells))


def _seat_change(seat):
    if not seat["changes"] and not seat["stance_changed"]:
        return '<span class="unchanged">維持原立場</span>'
    lines = []
    for change in seat["changes"]:
        lines.append(
            "<li>{} → {}{}</li>".format(
                _e(change["before_label"] or change["before"] or _EMPTY),
                _e(change["after_label"] or change["after"] or _EMPTY),
                "：{}".format(_e(change["reason"])) if change["reason"] else "",
            )
        )
    if not lines:
        lines.append("<li>{}</li>".format(_e(seat["stance_change_reason"] or "已改票")))
    return '<ul class="changes">{}</ul>'.format("".join(lines))


def _run_evidence(data):
    note = data["notes"].get("evidence.jsonl")
    if not data["evidence"]:
        return _missing_block(
            "evidence-heading",
            "證據卡",
            note or "evidence.jsonl 沒有任何證據卡",
        )
    cards = "".join(_evidence_card(card) for card in data["evidence"])
    lines = [
        '<section class="card" aria-labelledby="evidence-heading">',
        '<h2 id="evidence-heading">證據卡</h2>',
    ]
    if note:
        lines.append('<p class="hint">{}</p>'.format(_e(note)))
    lines.append('<ul class="evidence">{}</ul>'.format(cards))
    lines.append("</section>")
    return "\n".join(lines)


def _evidence_card(card):
    url = card.get("source_url") or ""
    tier = card.get("source_tier")
    return "".join(
        [
            "<li>",
            '<p class="evidence-id">{}<span class="hint">{}</span></p>'.format(
                _e(card.get("evidence_id") or _EMPTY), _e(card.get("seat_id") or "")
            ),
            "<p>{}</p>".format(_e(card.get("statement") or _EMPTY)),
            "<blockquote>{}</blockquote>".format(_e(card.get("excerpt") or _EMPTY)),
            '<p class="hint">來源等級 {}・{}</p>'.format(
                _e(_EMPTY if tier is None else tier),
                _e(card.get("source_origin") or _EMPTY),
            ),
            '<p class="source"><code>{}</code></p>'.format(_e(url)),
            "</li>",
        ]
    )


def _run_transcript(data):
    if not data["artifacts"].get("debate.html"):
        return _missing_block(
            "transcript-heading", "辯論逐字稿", "尚未產生 debate.html"
        )
    return "\n".join(
        [
            '<section class="card" aria-labelledby="transcript-heading">',
            '<h2 id="transcript-heading">辯論逐字稿</h2>',
            '<p><a href="/run/{}/debate.html">開啟這一場的公開辯論全文</a></p>'.format(
                _path(data["run_id"])
            ),
            "</section>",
        ]
    )


def _missing_block(heading_id, heading, reason):
    """Say which record is not there and in what way, never just that it is not.

    The caller supplies the reason because only the caller knows which of the
    two silences it hit: a file that was never written, and a file that was
    written and holds nothing.
    """
    return "\n".join(
        [
            '<section class="card empty" aria-labelledby="{}">'.format(heading_id),
            '<h2 id="{}">{}</h2>'.format(heading_id, _e(heading)),
            "<p>{}，因此這一區沒有內容可以顯示。</p>".format(_e(reason)),
            "</section>",
        ]
    )


# -- live room ---------------------------------------------------------------


def _launch_form(launch, suggestions=None):
    """The one control on this site that starts something.

    It lives on the debate room, so it wears the room's own ``.run-bar`` — the
    original page's horizontal bar — rather than the card system's ``.card``,
    which the room's stylesheet does not define and must not import.

    **It is a menu now, and that is the point of Ticket 05.** A market is chosen
    from a ``<select>`` whose words come from the same authority every other page
    reads (:func:`asset_class_label`), and the target is typed into that market's
    own box, beside that market's own spelling convention
    (:func:`target_format_hint`) and its own list of targets this Data Root has
    analysed before. Nothing here reads the question's prose: what the run is
    about is stated, not inferred.

    **Which markets it offers is :func:`ask_bar_markets`'s answer** — the scope
    file's — for the menu, the boxes and the sheet's rules alike, so Spec R-006's
    retired 開放題 is absent from all three at once while the history page, the
    launcher and the roster keep it.

    Every market's box is in the page and the sheet shows one at a time
    (:func:`_asset_picker_rules`), which is what makes the switch instant and
    script-free. The boxes are named per market — ``asset_tw_stock`` and friends,
    spelled by :func:`~hoya_market_agents.webapp.launch.target_field` — so the
    server reads the box the *chosen* market names and never has to ask which one
    a browser happened to show.
    """
    launch = launch or {}
    busy = bool(launch.get("running"))
    suggestions = suggestions or {}
    lines = [
        '<form class="run-bar ask" method="post" action="/launch" '
        'aria-labelledby="ask-heading">',
        '<span id="ask-heading">提問並啟動七席</span>',
        '<label for="question">題目</label>',
        _question_box(busy),
        '<label for="{}">資產類別</label>'.format(ASSET_CLASS_CONTROL_ID),
        _asset_class_menu(busy),
        '<span class="ask-prompt hint">選定資產類別後，這裡會出現該類別的'
        "標的輸入框、格式提示與過往標的建議。</span>",
    ]
    lines += [
        _target_box(asset_class, suggestions.get(asset_class) or (), busy)
        for asset_class in ask_bar_markets()
    ]
    lines += [
        '<button type="submit"{}>送出並啟動</button>'.format(
            " disabled" if busy else ""
        ),
        _ask_note(launch, busy),
        '<span id="question-hint">送出後會在背景啟動一次完整的七席研究，'
        "本頁只負責啟動與觀看。同一類別要分析多個標的時，以逗號分隔。</span>",
        "</form>",
    ]
    return "\n".join(line for line in lines if line)


def _question_box(busy):
    """The free-text question: quoted into the run, and deciding nothing.

    It is still the first field because it is still what the run is *about* in
    words — the seven seats read it, the report shows it. What it no longer does
    is name the target, which is why it sits beside a menu now instead of alone.
    """
    return "<input {}>".format(
        " ".join(
            [
                'id="question"',
                'name="{}"'.format(launch_module.QUESTION_FIELD),
                'type="text"',
                'value=""',
                'aria-describedby="question-hint"',
                'autocomplete="off"',
            ]
            + _disabled(busy)
        )
    )


def _ask_note(launch, busy):
    """What the bar says about the launch this server started, if it started one."""
    if busy:
        return (
            '<span id="ask-busy">目前有一個由本頁啟動的 run 還在進行，'
            "結束後才能再送出下一題。</span>"
        )
    if not launch.get("started"):
        return ""
    return (
        '<span id="ask-busy">上一次由本頁啟動的程序已結束'
        "（結束碼 {}）。</span>".format(_e(launch.get("returncode")))
    )


def _asset_class_menu(busy):
    """The market menu: the markets there are, in the site's words.

    Which markets those are is :func:`ask_bar_markets`'s answer, so the menu is
    the scope file's list and nothing else — Spec R-006's 開放題 is absent because
    no market scope describes it, not because a line here removes it.

    It opens on nothing chosen, because "I forgot to choose" has to be a state
    the form can refuse — there is no market that is a safe default, and defaulting
    to one would start a run in a market the reader never picked.
    """
    options = ['<option value="" selected>請選擇資產類別</option>'] + [
        '<option value="{}">{}</option>'.format(
            _e(asset_class), _e(asset_class_label(asset_class))
        )
        for asset_class in ask_bar_markets()
    ]
    return '<select id="{}" name="{}" required{}>{}</select>'.format(
        ASSET_CLASS_CONTROL_ID,
        launch_module.ASSET_CLASS_FIELD,
        " disabled" if busy else "",
        "".join(options),
    )


def _target_box(asset_class, offered, busy):
    """One market's target box: label, input, suggestion list and hint.

    The box is **not** marked ``required``. A required control the sheet has
    hidden is a form the browser refuses to submit while pointing at something
    the reader cannot see; what is required is the market, and the box that
    market names is checked on the server, where the sentence can be a sentence.
    """
    control = _e("asset-{}".format(asset_class))
    listed = _e("asset-list-{}".format(asset_class))
    described = _e("asset-hint-{}".format(asset_class))
    label = asset_class_label(asset_class)
    return "".join(
        [
            '<span class="ask-target" data-asset-class="{}">'.format(_e(asset_class)),
            '<label for="{}">{}標的</label>'.format(control, _e(label)),
            "<input {}>".format(
                " ".join(
                    [
                        'id="{}"'.format(control),
                        'name="{}"'.format(launch_module.target_field(asset_class)),
                        'type="text"',
                        'value=""',
                        'list="{}"'.format(listed),
                        'aria-describedby="{}"'.format(described),
                        'autocomplete="off"',
                    ]
                    + _disabled(busy)
                )
            ),
            '<datalist id="{}">{}</datalist>'.format(
                listed,
                "".join(
                    '<option value="{}"></option>'.format(_e(target))
                    for target in offered
                ),
            ),
            '<span class="hint" id="{}">{}</span>'.format(
                described, _e(target_format_hint(asset_class))
            ),
            "</span>",
        ]
    )


def _disabled(busy):
    return ["disabled"] if busy else []


def _outcome_block(outcome):
    if not outcome:
        return ""
    return (
        '<p class="focus-detail">燈號 {}　{}　採納立場 {}</p>'
        '<p><a href="{}">開啟這一場的 run 詳情</a></p>'.format(
            _light(outcome.get("confidence_level")),
            _e(outcome.get("consensus_label") or _EMPTY),
            _e(outcome.get("adopted_label") or _EMPTY),
            _e(outcome.get("run_href") or "/"),
        )
    )


def _live_tally(data):
    """The original right-column tally panel: one cell per ballot position, in
    this ballot's own words, with a big number underneath."""
    cells = "".join(_tally_cell(entry) for entry in data["tally"])
    note = "" if data["tally"] else "尚未開始投票。"
    return "\n".join(
        [
            '<section class="panel" aria-labelledby="live-tally-heading">',
            '<h2 id="live-tally-heading">即時票數</h2>',
            '<div class="tally" id="live-tally">{}</div>'.format(cells),
            '<p class="tally-note" id="tally-note">{}</p>'.format(_e(note)),
            "</section>",
        ]
    )


def _tally_cell(entry):
    return '<div class="{}"><span class="tally-label">{}</span><strong>{}</strong></div>'.format(
        _e(entry["class"]), _e(entry["label"]), _e(entry["count"])
    )


def _live_seats(data):
    """The original ``.agents`` roll: the seven seats, each with its identity,
    its stance and what it is doing now — and, under each, the one line saying
    what that seat looks at (Spec R-005).

    The sentence arrives on the seat itself (``seat_blurb``), beside the name it
    belongs with, exactly as every later frame delivers it. This module reads no
    roster of its own for it: whatever set named the seat also explains it, and
    a redraw cannot pair one run's name with another run's sentence.
    """
    return "\n".join(
        [
            '<section class="panel" aria-labelledby="live-seats-heading">',
            '<h2 id="live-seats-heading">七席研究 Agent</h2>',
            '<div class="agents" id="live-seats">{}</div>'.format(
                "".join(_agent_card(seat) for seat in data["seats"])
            ),
            "</section>",
        ]
    )


def _agent_card(seat):
    return (
        '<article class="agent {provider}" data-seat-id="{seat_id}">'
        '<div class="agent-head">'
        '<span class="avatar" aria-hidden="true">{avatar}</span>'
        "<div><h3>{name}</h3><small>{number}｜{label}</small></div>"
        '<p class="stance {stance_class}">{stance_label}</p>'
        '<span class="status">{status}</span>'
        "</div>{blurb}</article>"
    ).format(
        provider=_e(seat["provider"]),
        seat_id=_e(seat["seat_id"]),
        avatar=_e(seat["avatar"]),
        name=_e(seat["agent_name"]),
        number=_e(seat["agent_number"]),
        label=_e(seat["seat_label"]),
        stance_class=_e(seat["stance_class"]),
        stance_label=_e(seat["stance_label"]),
        status=_e(seat["status"]),
        blurb=_agent_blurb(seat["seat_blurb"]),
    )


def _agent_blurb(blurb):
    """The seat's 白話說明 as a line under its card, or nothing at all."""
    if not blurb:
        return ""
    return '<p class="agent-blurb">{}</p>'.format(_e(blurb))


def _live_feed(data):
    """The original left-column chat panel: a flat, append-only list of public
    messages. The chat is first in the source and widest in the two-column grid,
    so it is the left column the user asked to keep."""
    messages = "".join(_message(message) for message in data["messages"])
    empty = "" if data["messages"] else '<p class="feed-empty" id="feed-empty">尚未開始辯論。</p>'
    return "\n".join(
        [
            '<section class="panel chat-panel" aria-labelledby="live-feed-heading">',
            '<p class="eyebrow">現在正在發生</p>',
            '<h2 id="live-feed-heading">公開辯論直播</h2>',
            '<div class="feed" id="live-feed" data-cursor="{}" role="log" '
            'aria-live="polite" aria-relevant="additions" aria-label="辯論聊天室" '
            'tabindex="0">{}{}</div>'.format(
                _e(data["cursor"] or ""), messages, empty
            ),
            '<button class="feed-jump" id="feed-jump" type="button" hidden>有新發言 ↓</button>',
            "</section>",
        ]
    )


def _message(message):
    parts = [
        '<article class="message {}" data-seq="{}">'.format(
            _e(message["provider"]), _e(message["seq"])
        ),
        '<div class="message-head"><div class="speaker">'
        '<span class="speaker-avatar" aria-hidden="true">{}</span>'
        "<div><strong>{}</strong><small>{}｜{}</small></div></div>".format(
            _e(message["avatar"]),
            _e(message["agent_name"]),
            _e(message["agent_number"]),
            _e(message["seat_label"]),
        ),
        '<div class="message-meta"><span class="badge {}">{}</span>'
        "<time>T+{}</time></div></div>".format(
            _e(message["stance_class"]),
            _e(message["stance_label"]),
            _e(_clock(message["elapsed_ms"])),
        ),
        '<p class="message-reason"><strong>判斷／挑戰理由：</strong>{}</p>'.format(
            _e(message["public_reason"])
        ),
    ]
    if message["change_label"]:
        parts.append(
            '<p class="stance-change{}"><strong>是否變更立場：</strong>{}</p>'.format(
                " changed" if message["changed"] else "", _e(message["change_label"])
            )
        )
    if message["evidence_ids"]:
        parts.append(
            '<p class="message-evidence"><strong>引用證據：</strong>{}</p>'.format(
                _e("、".join(message["evidence_ids"]))
            )
        )
    parts.append("</article>")
    return "".join(parts)


def _clock(elapsed_ms):
    """``MM:SS`` from milliseconds, which is how a run's turns are stamped."""
    seconds = max(0, int(elapsed_ms or 0) // 1000)
    return "{:02d}:{:02d}".format(seconds // 60, seconds % 60)


# -- text -------------------------------------------------------------------


def _e(value):
    return escape("" if value is None else str(value), quote=True)


def _path(value):
    """Percent-encode one path segment, leaving no separator behind."""
    return quote(str(value), safe="")


def stylesheet():
    """Return the site's one stylesheet, built from the token tables.

    Every page gets this, the debate room included. It used to get its own
    isolated sheet with its own tokens, which is how the site came to have two
    of everything — two font stacks, two radii, two greens — and only one of them
    under :data:`CONTRAST_REQUIREMENTS`. One sheet means one place to change a
    colour and one place that has to pass the contrast test.

    One ``:root`` and no media query: dark mode is retired (Spec R-004), so the
    sheet carries a single palette and the page is white whatever the operating
    system prefers.
    """
    return "".join(
        [
            ":root{{{}{}}}".format(_tokens(PALETTE), _tokens(SCALE)),
            _RULES,
            _asset_picker_rules(),
            _semantic_rules(),
        ]
    )


def _tokens(values):
    return "".join(
        "--{}:{};".format(name.replace("_", "-"), value)
        for name, value in sorted(values.items())
    )


def _asset_picker_rules():
    """The rules that show one market's target box at a time.

    Generated from :func:`ask_bar_markets` for the same reason
    :func:`_semantic_rules` is generated: which markets the form offers is that
    function's answer, and writing the rules out would be a second copy of it
    that a fourth market would not update — and, since Spec R-006, a copy that
    would still be selecting an option the menu no longer has.

    **The switch is the browser's own.** A ``<select>``'s chosen ``<option>``
    matches ``:checked``, and ``:has()`` lets the form react to it, so the box,
    the suggestion list and the spelling convention follow the menu with no
    script on the page — which is what lets the room keep ``script-src 'self'``
    for one file that has nothing to do with this form.

    **Only ever hiding, never showing.** Every rule below removes the boxes that
    were *not* chosen; none of them is what makes the chosen one appear. So a
    browser that does not understand ``:has()`` shows all of them, which is
    cluttered and still correct: the server reads the box the chosen market
    names, not the one that happens to be visible. The failure direction is a
    form that looks busy, never a form with no way to type a target.
    """
    hidden = "".join(
        '.ask:has(option[value="{0}"]:checked) '
        '.ask-target:not([data-asset-class="{0}"]){{display:none;}}'.format(asset_class)
        for asset_class in ask_bar_markets()
    )
    return (
        '.ask:has(option[value=""]:checked) .ask-target{display:none;}'
        + hidden
        + '.ask:not(:has(option[value=""]:checked)) .ask-prompt{display:none;}'
    )


def _frosted(selector):
    """Return the rule that makes ``selector`` one of the site's frosted panels.

    Spec R-004 asks for 半透明毛玻璃, and this is the whole of it: the palette's
    translucent white as the fill, the browser's own ``backdrop-filter`` as the
    blur, and the ``-webkit-`` spelling beside it because a surface that frosts
    in one browser and not another is two designs. There is no image, no
    ``url()`` and nothing fetched — the same recipe both offline renderers use,
    so the site and the report a run writes are made of one material.

    ``background-color`` rather than the ``background`` shorthand, so the
    hairline the header also carries (a ``background-image``) is not reset by it.

    **What may be given to this is decided by where it sits, not by what it is.**
    :data:`~hoya_market_agents.design_tokens.GLASS` measures the frosted fill as
    its composite *over the canvas*, and the contrast test holds every word to
    that composite — so a frosted panel nested inside a white card, or inside
    another frosted one, would be read against a backdrop nobody measured and its
    AA number would be about a colour the page does not paint. Every selector
    passed below is a page's own header or a direct child of ``<main>``, and a
    test walks the rendered pages to say so rather than trusting this note.
    """
    return (
        selector
        + "{background-color:var(--glass-surface);"
        + "-webkit-backdrop-filter:blur(var(--glass-blur));"
        + "backdrop-filter:blur(var(--glass-blur));}"
    )


def _semantic_rules():
    """The rules that are generated from a table rather than written out.

    A stance's colour, an outcome's colour and a provider's stripe are all
    decided by data that lives somewhere else — the ballot's classes, the
    index's verdicts, the roster's provider families — so writing them out here
    would be a second copy of that data, and a fourth of anything would arrive
    with no colour at all. They come last in the sheet so that a painted class
    wins over the neutral fill of whatever it is sitting on.

    Only :data:`PAINTED_STANCE_CLASSES` gets a stance rule: the other three are
    frozen by Spec R2 and are documented there.
    """
    stances = "".join(
        ".{}{{color:var(--{});}}".format(name, STANCE_COLOUR_TOKENS[name])
        for name in sorted(PAINTED_STANCE_CLASSES)
    )
    outcomes = "".join(
        ".outcome-{0}{{color:var(--{0});}}".format(token)
        for token in sorted({token for _word, _mark, token in OUTCOME_WORDS.values()})
    )
    stripes = "".join(
        ".message.{}{{border-left-color:var(--{});}}".format(
            provider, token.replace("_", "-")
        )
        for provider, token in sorted(PROVIDER_STRIPE_TOKENS.items())
    )
    return stances + outcomes + stripes


# The site's rules, in one sheet. Reading order: the shell every page shares,
# then the surfaces, then the controls, then the debate room's own layout, then
# the pages that are tables and forms, then the two breakpoints. Nothing here
# names a colour, a size or a gap: they are all ``var()`` reads of the tables
# above, which is checked rather than trusted.
#
# **The design, in three sentences** (Spec R-004). White cards on a grey-50
# canvas, told apart by a hairline and by space rather than by a fill or a
# shadow, with the space one step wider than the density this site used to have
# because 大量留白 is the requirement and not a taste. Anything a finger or a
# pointer lands on is a pill — the tab bar, the buttons, the badges — which is
# the one shape that makes a control read as a control at this weight of line.
# The frost and the four hues are appended below rather than written here: both
# are generated, and generating them is what keeps this sheet, the market report
# and the tracer report one material instead of three that resemble each other.
_RULES = """
*{box-sizing:border-box;}
body{margin:0;background:var(--page);color:var(--text);font-family:var(--font-sans);
 font-size:var(--size-md);line-height:var(--line-base);}
h1,h2,h3{line-height:var(--line-tight);}
h1{margin:0 0 var(--space-3);font-size:var(--size-2xl);font-weight:500;
 letter-spacing:-.01em;}
a{color:var(--link);}
a:hover{text-decoration:none;}
code{font-family:var(--font-mono);font-size:var(--size-sm);}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px;
 border-radius:var(--radius-sm);}
.skip-link{position:absolute;left:-999px;top:0;background:var(--accent);
 color:var(--accent-text);padding:var(--space-4) var(--space-5);z-index:10;
 border-radius:0 0 var(--radius-md) 0;}
.skip-link:focus{left:0;}
.site-header,.top{border-bottom:1px solid var(--border);
 padding:var(--space-7) clamp(var(--space-5),4vw,var(--space-7)) var(--space-6);}
.header-top,.top{display:flex;justify-content:space-between;align-items:flex-start;
 gap:var(--space-6);flex-wrap:wrap;}
.site-note,.top p:last-child{margin:var(--space-1) 0;color:var(--muted);
 font-size:var(--size-xs);word-break:break-all;}
.eyebrow{margin:0;color:var(--accent);font-weight:700;font-size:var(--size-2xs);
 letter-spacing:.08em;}
.top-actions{display:flex;align-items:center;gap:var(--space-4);flex-wrap:wrap;
 justify-content:flex-end;}
.connection{background:var(--surface);border:1px solid var(--border);
 color:var(--success);padding:var(--space-2) var(--space-4);
 border-radius:var(--radius-pill);font-weight:700;font-size:var(--size-xs);}
.stop-form{margin:0;}
.stop-form button{font:inherit;font-weight:700;font-size:var(--size-sm);
 padding:var(--space-3) var(--space-5);border:1px solid var(--border);
 border-radius:var(--radius-pill);background:var(--surface);color:var(--link);
 cursor:pointer;white-space:nowrap;}
main{display:block;max-width:var(--shell);margin:0 auto;
 padding:var(--space-7) clamp(var(--space-5),4vw,var(--space-7)) var(--space-7);}
main:focus{outline:none;}
.site-footer{max-width:var(--shell);margin:0 auto;
 padding:0 clamp(var(--space-5),4vw,var(--space-7)) var(--space-7);
 color:var(--muted);font-size:var(--size-xs);}
.page-tabs{display:flex;gap:var(--space-1);padding:var(--space-1);
 border:1px solid var(--border);border-radius:var(--radius-pill);flex-wrap:wrap;
 background:var(--surface);}
.page-tabs a,.page-tabs [role=link]{color:var(--link);text-decoration:none;
 font-size:var(--size-sm);padding:var(--space-3) var(--space-5);
 border-radius:var(--radius-pill);font-weight:700;white-space:nowrap;}
.page-tabs a[aria-current=page]{background:var(--accent);color:var(--accent-text);}
.page-tabs [aria-disabled=true]{color:var(--muted);opacity:var(--dim);}
.card,.panel,.metric,.detail-panel,fieldset.settings-group{background:var(--surface);
 border:1px solid var(--border);border-radius:var(--radius-lg);}
.card{padding:var(--space-6);margin:0 0 var(--space-6);}
.panel{padding:var(--space-6);margin:0 0 var(--space-6);}
.metric{padding:var(--space-5);}
.detail-panel{padding:0;overflow:hidden;}
.card h2,.panel h2{margin:0 0 var(--space-5);font-size:var(--size-lg);
 font-weight:600;}
.field-grid{display:grid;gap:var(--space-6);
 grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));}
.field{display:flex;flex-direction:column;gap:var(--space-2);}
.field label{font-weight:600;font-size:var(--size-sm);}
.field input,.run-bar select,.run-bar input{font:inherit;font-size:var(--size-sm);
 padding:var(--space-3) var(--space-4);border:1px solid var(--border);
 border-radius:var(--radius-md);background:var(--surface);color:var(--text);
 min-width:0;}
.hint{margin:0;color:var(--muted);font-size:var(--size-xs);}
.actions{display:flex;flex-wrap:wrap;gap:var(--space-4);align-items:center;
 margin-top:var(--space-6);}
button.primary,.focus-action,.feed-jump,.run-bar button{font:inherit;font-weight:700;
 border:1px solid var(--accent);border-radius:var(--radius-pill);
 background:var(--accent);color:var(--accent-text);cursor:pointer;
 text-decoration:none;}
button.primary{padding:var(--space-3) var(--space-6);}
a.secondary{padding:var(--space-3) var(--space-5);border:1px solid var(--border);
 border-radius:var(--radius-pill);text-decoration:none;}
button.primary[disabled]{opacity:var(--dim);cursor:not-allowed;}
.field input[disabled]{opacity:var(--dim);}
.problems{border-color:var(--accent);}
.problems ul,.notice ul{margin:var(--space-3) 0 0;padding-left:var(--space-6);}
.empty p{margin:var(--space-3) 0;}
pre{overflow-x:auto;background:var(--page);border:1px solid var(--border);
 border-radius:var(--radius-md);padding:var(--space-5);margin:var(--space-4) 0 0;}
.table-scroll{overflow-x:auto;}
table{border-collapse:collapse;width:100%;font-size:var(--size-sm);}
caption{text-align:left;color:var(--muted);font-size:var(--size-xs);
 padding-bottom:var(--space-4);}
th,td{text-align:left;vertical-align:top;padding:var(--space-4);
 border-bottom:1px solid var(--border);}
thead th{font-size:var(--size-xs);letter-spacing:.04em;color:var(--muted);
 border-bottom:2px solid var(--border);white-space:nowrap;}
tbody tr:last-child th,tbody tr:last-child td{border-bottom:none;}
th[scope=row]{font-weight:600;white-space:nowrap;}
th[scope=row] .hint{display:block;font-weight:400;}
.nowrap{white-space:nowrap;font-variant-numeric:tabular-nums;}
.light{white-space:nowrap;}
.unchanged{color:var(--muted);}
.changes{margin:0;padding-left:var(--space-5);}
.verdict{margin:0 0 var(--space-6);font-size:var(--size-lg);font-weight:600;}
dl.summary{display:grid;gap:var(--space-5) var(--space-6);margin:0;
 grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));}
dl.summary dt{color:var(--muted);font-size:var(--size-xs);}
dl.summary dd{margin:var(--space-1) 0 0;word-break:break-all;}
.report-frame{width:100%;height:min(75vh,46rem);border:1px solid var(--border);
 border-radius:var(--radius-lg);background:var(--surface);}
ul.evidence{list-style:none;margin:0;padding:0;display:grid;gap:var(--space-5);
 grid-template-columns:repeat(auto-fit,minmax(19rem,1fr));}
ul.evidence>li{border:1px solid var(--border);border-radius:var(--radius-md);
 padding:var(--space-5);background:var(--page);}
.evidence-id{margin:0 0 var(--space-3);font-weight:600;}
.evidence-id .hint{display:inline;margin-left:var(--space-3);}
blockquote{margin:var(--space-3) 0;padding-left:var(--space-4);
 border-left:3px solid var(--border);color:var(--muted);}
.source code{word-break:break-all;}
.run-bar{display:flex;align-items:center;gap:var(--space-4);flex-wrap:wrap;
 margin:var(--space-6) 0 0;padding:var(--space-4) var(--space-6);
 border:1px solid var(--border);
 border-radius:var(--radius-lg);font-size:var(--size-sm);color:var(--muted);}
.run-bar code{font-weight:700;color:var(--accent);}
.run-bar select{max-width:26rem;}
.run-bar input{flex:1;min-width:12rem;}
.run-bar a{color:var(--link);font-weight:700;}
.run-bar button{padding:var(--space-2) var(--space-5);}
.ask{align-items:baseline;}
.ask-target{display:flex;align-items:baseline;gap:var(--space-3);flex-wrap:wrap;
 flex:1;min-width:14rem;}
.ask-target .hint{flex-basis:100%;}
.ask-target input[disabled],.ask select[disabled]{opacity:var(--dim);}
.tally-note{margin:var(--space-4) 0 0;color:var(--muted);font-size:var(--size-sm);}
.focus-bar{display:flex;align-items:center;justify-content:space-between;
 gap:var(--space-6);margin:var(--space-6) 0;
 padding:var(--space-6) var(--space-7);
 border:1px solid var(--border);border-left:.35rem solid var(--accent);
 border-radius:var(--radius-lg);}
.focus-bar p{margin:var(--space-1) 0;}
.focus-asset{color:var(--muted);font-weight:700;letter-spacing:.08em;
 font-size:var(--size-xs);}
.focus-bar h2{margin:var(--space-1) 0;font-size:var(--size-xl);}
.focus-tally{margin-left:var(--space-2);color:var(--muted);font-size:var(--size-md);
 font-weight:600;font-variant-numeric:tabular-nums;}
.focus-detail{color:var(--muted);}
.focus-action{flex:none;padding:var(--space-4) var(--space-6);}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));
 gap:var(--space-4);margin:0 0 var(--space-6);}
.metric small{display:block;color:var(--muted);font-size:var(--size-xs);}
.metric strong{display:block;margin-top:var(--space-2);font-size:var(--size-lg);
 font-family:var(--font-mono);font-variant-numeric:tabular-nums;}
.live-layout{display:grid;gap:var(--space-6);align-items:start;
 grid-template-columns:minmax(0,1.7fr) minmax(20rem,.72fr);}
.chat-panel{min-height:38rem;position:relative;}
.secondary-grid{display:grid;grid-template-columns:1.1fr .8fr 1.2fr;
 gap:var(--space-6);}
.detail-panel summary{cursor:pointer;padding:var(--space-5) var(--space-6);
 font-weight:700;color:var(--accent);}
.detail-panel[open] summary{border-bottom:1px solid var(--border);}
.detail-body{padding:var(--space-6);}
.rules{display:flex;flex-direction:column;gap:var(--space-2);}
.rule{display:flex;align-items:baseline;gap:var(--space-4);
 border-left:4px solid var(--border);padding:var(--space-3) var(--space-4);
 background:var(--page);font-size:var(--size-sm);
 border-radius:0 var(--radius-sm) var(--radius-sm) 0;}
.rule time{min-width:4.2rem;font-weight:700;font-family:var(--font-mono);
 font-variant-numeric:tabular-nums;}
.rule.past{opacity:var(--dim);}
.rule.current{border-color:var(--accent);background:var(--surface);font-weight:700;}
.tally{display:grid;gap:var(--space-4);
 grid-template-columns:repeat(auto-fit,minmax(5.5rem,1fr));}
.tally div{padding:var(--space-5);border-radius:var(--radius-md);
 background:var(--page);text-align:center;}
.tally-label{font-size:var(--size-xs);}
.tally strong{display:block;font-size:var(--size-2xl);font-family:var(--font-mono);
 font-variant-numeric:tabular-nums;}
.agents{display:flex;flex-direction:column;gap:var(--space-3);}
.agent{border:1px solid var(--border);border-radius:var(--radius-md);
 padding:var(--space-4) var(--space-5);background:var(--page);}
.agent-head{display:grid;grid-template-columns:2.25rem 1fr auto;gap:var(--space-3);
 align-items:center;}
.avatar,.speaker-avatar{display:grid;place-items:center;
 border-radius:var(--radius-pill);background:var(--surface);
 border:1px solid var(--border);}
.avatar{width:2.25rem;height:2.25rem;font-size:var(--size-lg);}
.speaker-avatar{width:2rem;height:2rem;}
.agent h3{margin:0;font-size:var(--size-sm);}
.agent small,.speaker small{display:block;color:var(--muted);
 font-size:var(--size-2xs);}
.agent-blurb{margin:var(--space-2) 0 0;color:var(--muted);
 font-size:var(--size-2xs);line-height:1.6;}
.agent .stance{margin:0;font-size:var(--size-xs);font-weight:700;}
.agent .status{grid-column:2/4;justify-self:start;background:var(--surface);
 color:var(--accent);font-size:var(--size-2xs);
 padding:var(--space-1) var(--space-3);border-radius:var(--radius-pill);}
.feed{display:flex;flex-direction:column;gap:var(--space-5);max-height:48rem;
 overflow:auto;padding-right:var(--space-2);}
.feed-empty{color:var(--muted);}
.message{width:min(52rem,96%);border:1px solid var(--border);
 border-left:4px solid var(--accent);background:var(--page);
 padding:var(--space-5);
 border-radius:var(--radius-sm) var(--radius-lg) var(--radius-lg) var(--radius-lg);
 animation:bubble-in var(--motion-fast) ease-out;}
.message-head{display:flex;justify-content:space-between;align-items:center;
 gap:var(--space-4);}
.speaker{display:flex;align-items:center;gap:var(--space-3);}
.message time,.history-row time{color:var(--muted);font-size:var(--size-xs);
 white-space:nowrap;font-family:var(--font-mono);font-variant-numeric:tabular-nums;}
.message p{margin:var(--space-3) 0;}
.message-meta{display:flex;align-items:center;gap:var(--space-3);flex:none;}
.message-reason strong,.stance-change strong{color:var(--muted);}
.stance-change{font-size:var(--size-sm);}
.stance-change.changed{color:var(--oppose);font-weight:700;}
.badge{font-size:var(--size-2xs);font-weight:700;
 padding:var(--space-1) var(--space-3);border-radius:var(--radius-pill);
 background:var(--surface);border:1px solid var(--border);color:var(--muted);}
.message-evidence{margin:var(--space-2) 0 0;color:var(--muted);
 font-size:var(--size-2xs);}
@keyframes bubble-in{from{opacity:0;transform:translateY(.5rem);}
 to{opacity:1;transform:none;}}
@media (prefers-reduced-motion:reduce){.message{animation:none;}}
.feed-jump{position:absolute;left:50%;bottom:var(--space-5);
 transform:translateX(-50%);padding:var(--space-3) var(--space-5);
 border-radius:var(--radius-pill);}
.history{list-style:none;margin:0;padding:0;}
.history li{padding:var(--space-3) 0;border-bottom:1px solid var(--border);}
.history-row{display:flex;align-items:center;gap:var(--space-3);
 font-size:var(--size-sm);}
.history-row time{min-width:4.2rem;}
.history-seat{flex:1;min-width:0;}
.history-row.changed{background:var(--page);border-left:3px solid var(--abstain);
 padding-left:var(--space-3);}
.history-flag{font-size:var(--size-2xs);font-weight:700;color:var(--abstain);
 background:var(--surface);border:1px solid var(--border);
 padding:var(--space-1) var(--space-3);border-radius:var(--radius-pill);}
.notice.saved,.card.saved{border-color:var(--success);}
.notice.refused,.card.refused{border-color:var(--danger);}
.notice.locked{border-color:var(--border);}
.notice.saved h2,.card.saved h2{color:var(--success);}
.notice.refused h2,.card.refused h2{color:var(--danger);}
.field-error{margin:0;color:var(--danger);font-size:var(--size-xs);font-weight:600;}
.field input[aria-invalid="true"]{border-color:var(--danger);border-width:2px;}
.hit-rate{margin:var(--space-2) 0 var(--space-5);font-size:var(--size-2xl);
 font-weight:700;font-family:var(--font-mono);font-variant-numeric:tabular-nums;}
.stat-row{display:flex;flex-wrap:wrap;gap:var(--space-5) var(--space-7);
 margin:var(--space-5) 0 0;}
.stat-row .stat{display:grid;gap:var(--space-1);}
.stat-row dt{font-size:var(--size-sm);color:var(--muted);}
.stat-row dd{margin:0;font-size:var(--size-lg);font-weight:700;
 font-family:var(--font-mono);font-variant-numeric:tabular-nums;}
fieldset.settings-group{padding:var(--space-5) var(--space-6) var(--space-6);
 margin:0 0 var(--space-6);min-width:0;}
fieldset.settings-group legend{padding:0 var(--space-3);font-weight:700;
 font-size:var(--size-sm);font-family:var(--font-mono);}
ul.timeline{list-style:none;margin:0;padding:0;display:grid;gap:var(--space-4);}
ul.timeline>li{display:grid;gap:var(--space-1) var(--space-4);align-items:center;
 grid-template-columns:minmax(9rem,auto) 1fr minmax(9rem,auto);}
.timeline-name{font-family:var(--font-mono);font-size:var(--size-sm);}
.timeline-bar{display:block;height:.75rem;border:1px solid var(--border);
 border-radius:var(--radius-pill);background:var(--page);overflow:hidden;}
.timeline-fill{display:block;height:100%;background:var(--accent);}
.timeline-value{font-family:var(--font-mono);font-variant-numeric:tabular-nums;
 font-size:var(--size-sm);color:var(--muted);}
@media (max-width:70rem){.metrics{grid-template-columns:repeat(2,1fr);}
 .live-layout,.secondary-grid{grid-template-columns:1fr;}
 .focus-bar{align-items:flex-start;}
 .focus-tally{display:block;margin-left:0;}}
@media (max-width:38rem){.top,.header-top,.focus-bar{flex-direction:column;}
 .top-actions,.page-tabs{width:100%;}
 .page-tabs a,.page-tabs [role=link]{flex:1;text-align:center;
  padding:var(--space-3) var(--space-1);}
 .metrics,.tally{grid-template-columns:1fr;}
 .focus-action{width:100%;text-align:center;}
 ul.timeline>li{grid-template-columns:1fr;}}
""" + _frosted(".site-header,.top,.focus-bar,.run-bar") + decorative_hairline(
    # The one band of 紅藍綠黃 a page carries, on the one piece of furniture all
    # six kinds of page share — the shutdown page, which has no navigation at
    # all, included. Both offline renderers draw the same band from the same
    # function, so 點綴 is one decision for the whole project rather than three
    # sheets that resemble each other.
    ".site-header,.top"
)


# The live room's script. It appends what the stream sends and ticks the clock;
# it never builds the page, which the server already did. Everything that comes
# from a run is written with ``textContent`` — no markup is ever assembled from
# run data on the client, which is why a public reason holding ``<script>`` is
# text here exactly as it is text on the server-rendered page.
LIVE_SCRIPT = r"""
(function () {
  "use strict";
  var picker = document.getElementById("run-picker");
  if (picker) {
    picker.addEventListener("change", function () {
      if (picker.value) { location.search = "?run=" + encodeURIComponent(picker.value); }
    });
  }
  var feed = document.getElementById("live-feed");
  if (!feed || typeof EventSource === "undefined") { return; }
  var connection = document.getElementById("live-connection");
  var stateBox = document.getElementById("live-state");
  var roundBox = document.getElementById("live-round");
  var elapsedBox = document.getElementById("live-elapsed");
  var tallyBox = document.getElementById("live-tally");
  var seatBox = document.getElementById("live-seats");
  var outcomeBox = document.getElementById("live-outcome");
  var totalRemaining = document.getElementById("live-total-remaining");
  var reportRemaining = document.getElementById("live-report-remaining");
  var focusTally = document.querySelector(".focus-bar .focus-tally");
  var jump = document.getElementById("feed-jump");

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }
  function clock(ms) {
    var seconds = Math.max(0, Math.floor((ms || 0) / 1000));
    var minutes = Math.floor(seconds / 60);
    return ("0" + minutes).slice(-2) + ":" + ("0" + (seconds % 60)).slice(-2);
  }
  function clear(node) {
    while (node && node.firstChild) { node.removeChild(node.firstChild); }
  }
  function message(item) {
    var card = el("article", "message " + item.provider);
    card.dataset.seq = String(item.seq);
    var head = el("div", "message-head");
    var speaker = el("div", "speaker");
    var avatar = el("span", "speaker-avatar", item.avatar);
    avatar.setAttribute("aria-hidden", "true");
    speaker.append(avatar);
    var names = el("div");
    names.append(el("strong", "", item.agent_name));
    names.append(el("small", "", item.agent_number + "｜" + item.seat_label));
    speaker.append(names);
    head.append(speaker);
    var meta = el("div", "message-meta");
    meta.append(el("span", "badge " + item.stance_class, item.stance_label));
    meta.append(el("time", "", "T+" + clock(item.elapsed_ms)));
    head.append(meta);
    card.append(head);
    var reason = el("p", "message-reason");
    reason.append(el("strong", "", "判斷／挑戰理由："));
    reason.append(document.createTextNode(item.public_reason));
    card.append(reason);
    if (item.change_label) {
      var change = el("p", "stance-change" + (item.changed ? " changed" : ""));
      change.append(el("strong", "", "是否變更立場："));
      change.append(document.createTextNode(item.change_label));
      card.append(change);
    }
    if (item.evidence_ids && item.evidence_ids.length) {
      var ev = el("p", "message-evidence");
      ev.append(el("strong", "", "引用證據："));
      ev.append(document.createTextNode(item.evidence_ids.join("、")));
      card.append(ev);
    }
    return card;
  }
  function pinned() {
    return feed.scrollHeight - feed.scrollTop - feed.clientHeight < 64;
  }
  function appendMessages(messages) {
    if (!messages.length) { return; }
    var empty = document.getElementById("feed-empty");
    if (empty) { empty.remove(); }
    var wasPinned = pinned();
    messages.forEach(function (item) { feed.append(message(item)); });
    if (wasPinned) {
      feed.scrollTop = feed.scrollHeight;
      if (jump) { jump.hidden = true; }
    } else if (jump) {
      jump.hidden = false;
    }
  }
  function drawTally(entries) {
    if (!tallyBox || !entries) { return; }
    clear(tallyBox);
    entries.forEach(function (entry) {
      var cell = el("div", entry["class"]);
      cell.append(el("span", "tally-label", entry.label));
      cell.append(el("strong", "", entry.count));
      tallyBox.append(cell);
    });
    syncFocus(entries);
  }
  function syncFocus(entries) {
    if (!focusTally || !entries.length) { return; }
    focusTally.textContent = entries.map(function (entry) {
      return entry.label + " " + entry.count;
    }).join("｜");
  }
  function tick(box) {
    if (!box) { return; }
    var from = Number(box.dataset.countdownFrom || 0);
    var elapsed = Number(elapsedBox && elapsedBox.dataset.elapsedMs || 0);
    box.textContent = clock(Math.max(0, from - elapsed));
  }
  function drawSeats(seats) {
    if (!seatBox || !seats) { return; }
    clear(seatBox);
    seats.forEach(function (seat) {
      var card = el("article", "agent " + seat.provider);
      card.dataset.seatId = seat.seat_id;
      var headline = el("div", "agent-head");
      var avatar = el("span", "avatar", seat.avatar);
      avatar.setAttribute("aria-hidden", "true");
      headline.append(avatar);
      var names = el("div");
      names.append(el("h3", "", seat.agent_name));
      names.append(el("small", "", seat.agent_number + "｜" + seat.seat_label));
      headline.append(names);
      headline.append(el("p", "stance " + seat.stance_class, seat.stance_label));
      headline.append(el("span", "status", seat.status));
      card.append(headline);
      // 說明跟名稱同在這一個 seat 物件裡，所以重畫永遠是整組換，配不出
      // 「這一趟的名字＋上一趟的說明」。
      if (seat.seat_blurb) { card.append(el("p", "agent-blurb", seat.seat_blurb)); }
      seatBox.append(card);
    });
  }
  function drawOutcome(outcome) {
    if (!outcomeBox || !outcome) { return; }
    clear(outcomeBox);
    var line = el("p", "focus-detail");
    line.append(el("span", "", "燈號 " + (outcome.confidence_level || "—")));
    line.append(el("span", "", "　" + (outcome.consensus_label || "—")));
    outcomeBox.append(line);
    var link = document.createElement("a");
    link.href = outcome.run_href;
    link.textContent = "開啟這一場的 run 詳情";
    var holder = el("p");
    holder.append(link);
    outcomeBox.append(holder);
  }
  function apply(payload, replace) {
    if (replace) {
      clear(feed);
      feed.append(el("p", "feed-empty", "尚未開始辯論。"));
      document.querySelector(".feed-empty").id = "feed-empty";
    }
    appendMessages(payload.messages || []);
    drawTally(payload.tally);
    drawSeats(payload.seats);
    if (payload.cursor) { feed.dataset.cursor = payload.cursor; }
    if (roundBox && payload.round !== undefined && payload.round !== null) {
      roundBox.textContent = "第 " + payload.round + " 輪";
    }
    if (elapsedBox && payload.elapsed_ms !== undefined) {
      elapsedBox.dataset.elapsedMs = String(payload.elapsed_ms);
      elapsedBox.textContent = clock(payload.elapsed_ms);
    }
  }

  if (jump) {
    jump.addEventListener("click", function () {
      feed.scrollTop = feed.scrollHeight;
      jump.hidden = true;
    });
    feed.addEventListener("scroll", function () {
      if (pinned()) { jump.hidden = true; }
    });
  }

  var url = "/live/events";
  if (feed.dataset.cursor) {
    url += "?after=" + encodeURIComponent(feed.dataset.cursor);
  }
  var stream = new EventSource(url);
  stream.addEventListener("open", function () {
    if (connection) { connection.textContent = "直播連線中"; }
  });
  stream.addEventListener("snapshot", function (event) {
    apply(JSON.parse(event.data), true);
  });
  stream.addEventListener("append", function (event) {
    apply(JSON.parse(event.data), false);
  });
  stream.addEventListener("done", function (event) {
    var payload = JSON.parse(event.data);
    apply(payload, false);
    drawOutcome(payload.outcome);
    if (stateBox) { stateBox.textContent = "已完成"; stateBox.dataset.state = "finished"; }
    stream.close();
  });
  stream.addEventListener("error", function () {
    if (connection) { connection.textContent = "連線中斷，正在重連"; }
  });

  window.setInterval(function () {
    if (!elapsedBox || !stateBox || stateBox.dataset.state !== "running") { return; }
    var next = Number(elapsedBox.dataset.elapsedMs || 0) + 1000;
    elapsedBox.dataset.elapsedMs = String(next);
    elapsedBox.textContent = clock(next);
    tick(totalRemaining);
    tick(reportRemaining);
  }, 1000);
})();
"""
