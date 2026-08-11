"""Shared server-rendered HTML components and asset loaders."""

from pathlib import Path
from string import Template
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

from ... import design_tokens

from ...prompt_builder import market_scopes

from ...question import ASSET_CLASS_OPEN, ASSET_CLASSES

from ...report_contract import CONFIDENCE_ICONS, CONFIDENCE_LEVELS

from ...report_renderer import decorative_hairline

from ...run_index import (
    OUTCOME_HIT,
    OUTCOME_MISS,
    OUTCOME_PENDING,
    OUTCOME_UNREADABLE,
    OUTCOME_UNVERIFIABLE,
    OUTCOME_VERDICTS,
)

from .. import launch as launch_module

from .. import outcome as outcome_module

from .. import pdf_export

from .. import settings

from ..live import BRIEF_ELLIPSIS, STATUS_FINISHED, STATUS_WAITING

from ..views import (
    DEBATE_ARTIFACT,
    REPORT_ARTIFACT,
    STATE_INDEX_MISSING,
    STATE_OK,
    latest_report_run,
)

SITE_TITLE = "AI agnets debating chamber"

BROWSE_TABS = (
    ("/", "即時辯論"),
    ("/history", "歷史與命中率"),
)

SETTINGS_TAB = ("/settings", "設定")

RUN_ARTIFACT_TABS = (
    ("市場報告", REPORT_ARTIFACT),
    ("完整辯論", DEBATE_ARTIFACT),
)

EXPORT_PDF_SEGMENT = "export-pdf"

EXPORT_PDF_LABEL = "匯出 PDF"

SHUTDOWN_PATH = "/shutdown"

SHUTDOWN_LABEL = "關閉伺服器"

SHUTDOWN_PAGE_TITLE = "伺服器已關閉"

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

OUTCOME_WORDS = {
    OUTCOME_HIT: ("命中", "✔", "success"),
    OUTCOME_MISS: ("未命中", "✘", "danger"),
    OUTCOME_UNVERIFIABLE: ("不可自動驗證", "—", "muted"),
    OUTCOME_PENDING: ("待驗證", "…", "muted"),
    OUTCOME_UNREADABLE: ("紀錄無法讀取", "⚠", "abstain"),
}

OUTCOME_ORDER = (
    OUTCOME_HIT,
    OUTCOME_MISS,
    OUTCOME_PENDING,
    OUTCOME_UNVERIFIABLE,
    OUTCOME_UNREADABLE,
)

HIT_RATE_FORMULA = "命中 ÷（命中 + 未命中）"

HIT_RATE_NOTE = (
    "待驗證與不可自動驗證都不列入分母：還沒對答案不等於答錯，"
    "沒有價格可以對照的題目也不等於答錯。"
)

NO_HIT_RATE = "尚無可計分的預測"

CONFIDENCE_WORDS = {
    "red": "紅燈",
    "orange": "橘燈",
    "yellow": "黃燈",
    "green": "綠燈",
    "blue": "藍燈",
}

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

PALETTE = design_tokens.PALETTE

SCALE = design_tokens.SCALE

MEASURED_COLOURS = design_tokens.MEASURED_COLOURS

BACKGROUND_TOKENS = design_tokens.BACKGROUND_TOKENS

TEXT_TOKENS = design_tokens.TEXT_TOKENS

LINE_TOKENS = design_tokens.LINE_TOKENS

DECOR_TOKENS = design_tokens.DECOR_TOKENS

TEXT_MINIMUM = design_tokens.TEXT_MINIMUM

LINE_MINIMUM = design_tokens.LINE_MINIMUM

CONTRAST_REQUIREMENTS = design_tokens.CONTRAST_REQUIREMENTS

STANCE_COLOUR_TOKENS = {
    "stance-affirm": "affirm",
    "stance-oppose": "oppose",
    "stance-abstain": "abstain",
    "stance-unknown": "muted",
}

PAINTED_STANCE_CLASSES = ("stance-unknown",)

PROVIDER_STRIPE_TOKENS = {
    "codex": "accent",
    "claude": "provider_claude",
    "gemini": "provider_gemini",
}

LIVE_SCRIPT_PATH = "/live.js"

_EMPTY = "—"

PAGE_TITLE_HISTORY = "歷史與命中率"

_LIVE_STATE_WORDS = {STATUS_WAITING: "等待新的 run", STATUS_FINISHED: "已完成"}

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

SETTINGS_NOTICES = {
    settings.SAVED: ("已存檔", "status", "saved"),
    settings.REFUSED: ("這次沒有存檔", "alert", "refused"),
    settings.UNREADABLE: ("讀不到設定檔", "alert", "refused"),
    settings.NOT_PUBLISHED: ("已寫入，但還沒有生效", "alert", "refused"),
    settings.NOTHING_SUBMITTED: ("這次沒有存檔", "alert", "refused"),
}

WHOLE_INDEX_NOTE = "統計涵蓋索引中的全部 run，不受下方查詢條件影響。"

MANUAL_LIST_LIMIT = 20

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

READ_ONLY_FOOTER = "本頁只讀取 run artifact，不會修改任何一份紀錄。"

SETTINGS_FOOTER = (
    "本頁會寫入辯論規則設定檔；run artifact 一律只讀，不會修改任何一份紀錄。"
)

HISTORY_FOOTER = (
    "本頁會為分析期間已到期的 run 新增 outcome.json；"
    "既有的 run artifact 一律只讀，不會修改任何一份紀錄。"
)

RUN_DETAIL_FOOTER = (
    "本頁的「{}」只會在這個 run 的資料夾新增 {}，已經有就拒絕、不覆寫；"
    "既有的 run artifact 一律只讀，不會修改任何一份紀錄。".format(
        EXPORT_PDF_LABEL, "與".join(pdf_export.EXPORT_TARGETS)
    )
)

SHUTDOWN_FOOTER = (
    "伺服器停止時會在 webapp.jsonl 寫下 server_stop；"
    "run artifact 一律只讀，不會修改任何一份紀錄。"
)


def _evidence_card(card):
    """Render the evidence-card shape shared by run detail and the live room."""
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

def _document(title, header, sections, scripts=(), footer=READ_ONLY_FOOTER):
    """Wrap one page.

    There is no ``style`` argument to override: every page carries the same
    :func:`stylesheet`. The room used to pass its own here, which is how the
    site came to have two of everything.
    """
    section_html = _template(
        _page_template(title),
        sections="\n".join(section for section in sections if section),
    )
    return _template(
        "document.html",
        title="{}・{}".format(_e(title), _e(SITE_TITLE)),
        scripts="\n".join(
            '<script src="{}" defer></script>'.format(source) for source in scripts
        ),
        header=header,
        sections=section_html,
        footer=_e(footer),
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

NO_REPORT_STATUS = "未產出報告"

UNRECORDED_CONSENSUS_STATUS = "未記錄共識狀態"

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

EXPORT_NOTICES = {
    pdf_export.EXPORTED: ("已匯出 PDF", "status", "saved"),
    pdf_export.ALREADY_EXPORTED: ("已經匯出過了", "alert", "refused"),
    pdf_export.IN_PROGRESS: ("正在匯出中", "status", "notice locked"),
    pdf_export.SOURCE_MISSING: ("還不能匯出", "alert", "refused"),
    pdf_export.CONVERSION_FAILED: ("這次沒有匯出", "alert", "refused"),
}

def _clock(elapsed_ms):
    """``MM:SS`` from milliseconds, which is how a run's turns are stamped."""
    seconds = max(0, int(elapsed_ms or 0) // 1000)
    return "{:02d}:{:02d}".format(seconds // 60, seconds % 60)

def _e(value):
    return escape("" if value is None else str(value), quote=True)

def _path(value):
    """Percent-encode one path segment, leaving no separator behind."""
    return quote(str(value), safe="")

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

WEBAPP_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = WEBAPP_ROOT / "templates"
STATIC_DIR = WEBAPP_ROOT / "static"
STATIC_SITE_CSS = STATIC_DIR / "site.css"
STATIC_LIVE_JS = STATIC_DIR / "live.js"


def _template(name, **values):
    template = Template((TEMPLATES_DIR / name).read_text(encoding="utf-8"))
    return template.substitute(values)


def _page_template(title):
    return {
        PAGE_TITLE_HISTORY: "history.html",
        "run 詳情": "run.html",
        "即時 Agent 辯論室": "live.html",
        "辯論規則設定": "settings.html",
    }.get(title, "page.html")


def stylesheet():
    return "".join(
        [
            design_tokens.root_rule(),
            STATIC_SITE_CSS.read_text(encoding="utf-8"),
            _asset_picker_rules(),
            _semantic_rules(),
        ]
    )


def live_script():
    return STATIC_LIVE_JS.read_text(encoding="utf-8")


LIVE_SCRIPT = live_script()
