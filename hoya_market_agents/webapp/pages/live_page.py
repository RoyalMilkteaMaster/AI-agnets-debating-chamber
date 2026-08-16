"""Live room, launch bar and protected debate-region page assembly."""

from ..live import (
    STATUS_FINISHED, STATUS_RUNNING, TOTAL_WINDOW_MS, UNRECORDED_LABEL,
    current_rule_index, focus_state, next_milestone, phase_label, rule_timeline,
    threshold_label,
)
from .components import (
    ASSET_CLASS_CONTROL_ID, BRIEF_ELLIPSIS, DEBATE_ARTIFACT, LIVE_SCRIPT_PATH,
    REPORT_ARTIFACT, _EMPTY, _LIVE_STATE_WORDS, _clock, _document, _e,
    _evidence_card, _light, _stop_form, ask_bar_markets, asset_class_label,
    latest_report_run, launch_module, site_tabs, target_format_hint,
)

# What each run-bound surface says about a run nothing is known about yet.
#
# The room can switch which run it watches without reloading, and every surface
# here was painted for the run that was on screen when the page was drawn. The
# next run's frames rewrite most of them — the feed, the seats, the tally, the
# vote history, the rule timeline, the phase, the threshold, the focus bar, the
# sealed evidence, the round and the clock — but not before the first of those
# frames lands, and the question, the market, the confidence light and the tally
# note not at all. So the client blanks each one at the switch, and these are the
# words it blanks to. They are the page's own empty wording rather than a second
# vocabulary: the same strings the server writes when it has no question, no
# votes and no evidence to show.
WAITING_QUESTION = "等待新的市場題目"
WAITING_ROUND = "尚未進入辯論"
WAITING_ASSETS = "市場"
WAITING_CONFIDENCE = "⚪ 信心尚未評估"
WAITING_TALLY_NOTE = "尚未開始投票。"
WAITING_RULES = "規則時間線將在新的一場開始後顯示。"
WAITING_VOTES = "尚未投票。"
WAITING_EVIDENCE = "證據將在證據快照封存後顯示。"

def _fresh_run_words():
    """The reset word for every run-bound surface, read from the authorities.

    The headline, the score, the next rule, the phase and the threshold are the
    ones the page would print for a run with no votes and no elapsed time, taken
    from the same functions that print them for a real run — so "not known yet"
    can never drift into wording the room does not otherwise use. The tally and
    the seat roll blank to nothing because the next run's first frame repaints
    them outright.
    """
    timeline = rule_timeline()
    focus = focus_state([], next_milestone(0, timeline), False, None)
    return {
        "live-question": WAITING_QUESTION,
        "live-round": WAITING_ROUND,
        "focus-asset": WAITING_ASSETS,
        "focus-headline": focus["headline"],
        "focus-tally": focus["tally_text"],
        "focus-detail": WAITING_CONFIDENCE,
        "focus-action": focus["next_label"],
        "live-phase": phase_label(0, timeline, STATUS_RUNNING),
        "live-threshold": threshold_label(0),
        "live-tally": "",
        "tally-note": WAITING_TALLY_NOTE,
        "live-seats": "",
        "rules-detail-body": WAITING_RULES,
        "vote-history-detail-body": WAITING_VOTES,
        "evidence-panel-body": WAITING_EVIDENCE,
    }

def _reset(words, element_id):
    """Mark one surface as run-bound, and say what it reads when it is not."""
    return 'id="{}" data-reset="{}"'.format(element_id, _e(words[element_id]))

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

def _live_header(data):
    """The original ``.top`` header: eyebrow, title, question, tabs, connection."""
    words = _fresh_run_words()
    question = data["question"] or WAITING_QUESTION
    state = data["state"]
    state_word = _LIVE_STATE_WORDS.get(state, "執行中")
    round_word = (
        "第 {} 輪".format(data["round"]) if data["round"] is not None else WAITING_ROUND
    )
    return "\n".join(
        [
            '<header class="top">',
            '<div><p class="eyebrow">AI AGNETS DEBATING CHAMBER 即時研究流程</p>',
            "<h1>即時 Agent 辯論室</h1>",
            # Named apart from the ask bar's 題目 field: they are two different
            # things — what is being watched, and what is being asked next — and
            # one id cannot address both.
            '<p {}>{}</p>'.format(_reset(words, "live-question"), _e(question)),
            '<p class="run-state"><span id="live-state" data-state="{}">{}</span>'
            '　<span {}>{}</span>'
            '<time id="live-elapsed" data-elapsed-ms="{}" hidden>{}</time></p>'.format(
                _e(state), _e(state_word),
                _reset(words, "live-round"), _e(round_word),
                data["elapsed_ms"], _e(_clock(data["elapsed_ms"])),
            ),
            "</div>",
            '<div class="top-actions">',
            # The connection indicator leads the cluster rather than sitting
            # inside it: it is a state, not a control, and Spec R-003 puts 設定
            # immediately left of the stop button — which it cannot be with a
            # third thing between them.
            '<span class="connection" id="live-connection">連線中</span>',
            _live_site_tabs(data),
            _stop_form(),
            "</div>",
            "</header>",
        ]
    )

def _live_site_tabs(data):
    """Give the live room's two run-bound tabs stable client-side targets."""
    tabs = site_tabs("/", _live_report_run(data))
    tabs = tabs.replace(
        ">市場報告</", ' id="live-report-link">市場報告</', 1
    )
    return tabs.replace(
        ">完整辯論</", ' id="live-debate-link">完整辯論</', 1
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

    **A finished run's tabs are the completion's answer, not the directory's.**
    ``completion`` names only what this run's own manifest binds by hash, which
    is what the ``done`` frame carries and what the client writes onto these two
    tabs. Reading the files off disk instead would let a stray or rewritten
    ``debate.html`` open from the tab bar of the same page whose ``done`` frame
    refuses to name it — the initial HTML and the frame disagreeing about one
    field, which is the thing this room is not allowed to do.
    """
    if not data["run_id"]:
        return latest_report_run(data["data_root"])
    if data.get("state") == STATUS_FINISHED:
        # Finished is exactly "this run wrote a valid manifest", which is the
        # same gate the completion is read through — so an ended run that bound
        # nothing offers nothing, rather than whatever is lying in the directory.
        completion = data.get("completion") or {}
        return {
            "run_id": data["run_id"],
            "artifacts": {
                REPORT_ARTIFACT: bool(completion.get("report_href")),
                DEBATE_ARTIFACT: bool(completion.get("debate_href")),
            },
        }
    return {
        "run_id": data["run_id"],
        # Still running: there is no manifest to bind anything yet, so this is
        # the snapshot's own look at the directory — it simply names the two
        # files in its own words rather than by file name.
        "artifacts": {
            REPORT_ARTIFACT: data["report_available"],
            DEBATE_ARTIFACT: data["debate_report_available"],
        },
    }

def _live_run_bar(data):
    """The original ``.run-bar``: which run, a picker for the finished ones, and
    the way back to the current run and to the history page."""
    run_id = data["run_id"]
    current = '<code id="live-run-id">{}</code>'.format(
        _e(run_id or "尚未選定")
    )
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
    words = _fresh_run_words()
    focus = data["focus"]
    assets = "／".join(a for a in data.get("assets") or [] if a) or WAITING_ASSETS
    confidence = (
        _light(focus.get("confidence_level"))
        if focus.get("confidence_level")
        else WAITING_CONFIDENCE
    )
    return "\n".join(
        [
            '<section class="focus-bar" aria-labelledby="focus-headline">',
            "<div>",
            '<p class="focus-asset" {}>{}</p>'.format(
                _reset(words, "focus-asset"), _e(assets)
            ),
            '<h2><span {}>{}</span>'
            '<span class="focus-tally" {}>{}</span></h2>'.format(
                _reset(words, "focus-headline"), _e(focus["headline"]),
                _reset(words, "focus-tally"), _e(focus["tally_text"]),
            ),
            '<p class="focus-detail" {}>{}</p>'.format(
                _reset(words, "focus-detail"), confidence
            ),
            '<div id="live-outcome">{}</div>'.format(
                _completion_block(data.get("completion")) or _outcome_block(data.get("outcome"))
            ),
            "</div>",
            '<a class="focus-action" {} href="#rules-detail">{}</a>'.format(
                _reset(words, "focus-action"), _e(focus["next_label"])
            ),
            "</section>",
        ]
    )

def _live_metrics(data):
    """The four gauges: total countdown, debate countdown, phase, and vote wall.

    The total countdown carries ``data-countdown-from`` for the existing client
    clock seam. The debate countdown is projected from the same authoritative
    run elapsed value and becomes terminal once debate opens. The phase and the
    threshold are this render's values and are then advanced by the frames — the
    server works both out and pushes them when the clock crosses a milestone, so
    the room never runs the rule authority in the browser (architecture §4.0.1).
    """
    countdowns = [
        ("十七分鐘剩餘時間", data["total_remaining_ms"], "total-remaining"),
    ]
    elapsed = data["elapsed_ms"]
    cells = [
        '<div class="metric"><small>{}</small>'
        '<strong id="live-{}" data-countdown-from="{}" '
        'data-duration-ms="{}">{}</strong></div>'.format(
            _e(name), ident, remaining + elapsed, TOTAL_WINDOW_MS,
            _e(_clock(remaining))
        )
        for name, remaining, ident in countdowns
    ]
    debate_remaining = data.get("debate_start_remaining_ms")
    debate_text = (
        "辯論已開始" if debate_remaining is None else _countdown_clock(debate_remaining)
    )
    debate_value = "" if debate_remaining is None else str(debate_remaining)
    cells.append(
        '<div class="metric"><small>開始辯論剩餘時間</small>'
        '<strong id="live-debate-remaining" data-remaining-ms="{}">{}</strong></div>'.format(
            _e(debate_value), _e(debate_text)
        )
    )
    words = _fresh_run_words()
    cells.append(
        '<div class="metric"><small>目前階段</small>'
        '<strong {}>{}</strong></div>'.format(
            _reset(words, "live-phase"), _e(data["phase_label"])
        )
    )
    cells.append(
        '<div class="metric"><small>目前共識門檻</small>'
        '<strong {}>{}</strong></div>'.format(
            _reset(words, "live-threshold"), _e(data["threshold_label"])
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
    current = current_rule_index(elapsed, rules)
    rows = "".join(_rule_row(rule, index, current) for index, rule in enumerate(rules))
    return _detail_panel(
        "rules-detail", "規則與時間線", '<div class="rules">{}</div>'.format(rows)
    )

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
        inner = '<p class="hint">{}</p>'.format(_e(WAITING_VOTES))
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
        inner = '<p class="hint">{}</p>'.format(_e(WAITING_EVIDENCE))
    else:
        inner = '<ul class="evidence">{}</ul>'.format(
            "".join(_evidence_card(card) for card in evidence)
        )
    return _detail_panel("evidence-panel", "可驗證證據", inner)

def _detail_panel(panel_id, heading, inner):
    """One folded panel: a heading that is the page's own, and a run-bound body.

    The body is marked and the ``<details>`` around it is not. What is folded
    here — the rule timeline, the changes of stance, the sealed evidence — is
    this run's, and the switch happens before the next run's first frame can
    say anything, so blanking it is what a same-page switch starts with. All
    three are then written back by the frames (Spec 即時辯論頁三面板進行中即時
    更新): the timeline and the changes of stance ride every frame, and the
    sealed evidence arrives once, on the frame that first finds the snapshot
    sealed — sealed content never moves again, so once is all it takes. The
    summary and the fold are the room's furniture: they say the same thing
    whichever run is being watched, and a client that blanked them would take
    the panel's name off it.
    """
    words = _fresh_run_words()
    return "\n".join(
        [
            '<details class="detail-panel" id="{}">'.format(panel_id),
            "<summary>{}</summary>".format(_e(heading)),
            '<div class="detail-body" {}>{}</div>'.format(
                _reset(words, panel_id + "-body"), inner
            ),
            "</details>",
        ]
    )

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
        '<form class="run-bar ask" id="launch-form" method="post" action="/launch" '
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
        '<button id="launch-submit" type="submit"{}>送出並啟動</button>'.format(
            " disabled" if busy else ""
        ),
        '<span id="launch-status" role="status" aria-live="polite"></span>',
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
        '<span id="ask-busy">上一次由本頁啟動的程序已結束。</span>'
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


def _completion_block(completion):
    if not completion:
        return ""
    return '<p>分析完成　<a href="{}">查看市場報告</a></p>'.format(
        _e(completion["report_href"])
    )


def _countdown_clock(milliseconds):
    seconds = max(1, (int(milliseconds) + 999) // 1000)
    minutes, seconds = divmod(seconds, 60)
    return "{:02d}:{:02d}".format(minutes, seconds)

def _live_tally(data):
    """The original right-column tally panel: one cell per ballot position, in
    this ballot's own words, with a big number underneath."""
    words = _fresh_run_words()
    cells = "".join(_tally_cell(entry) for entry in data["tally"])
    note = "" if data["tally"] else WAITING_TALLY_NOTE
    return "\n".join(
        [
            '<section class="panel" aria-labelledby="live-tally-heading">',
            '<h2 id="live-tally-heading">即時票數</h2>',
            '<div class="tally" {}>{}</div>'.format(
                _reset(words, "live-tally"), cells
            ),
            '<p class="tally-note" {}>{}</p>'.format(
                _reset(words, "tally-note"), _e(note)
            ),
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
            '<div class="agents" {}>{}</div>'.format(
                _reset(_fresh_run_words(), "live-seats"),
                "".join(_agent_card(seat) for seat in data["seats"]),
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
        "</div>{blurb}{attempt}</article>"
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
        attempt=_agent_attempt(seat.get("attempt")),
    )

def _agent_attempt(attempt):
    """The seat's research lineage as one line under its card (Spec R-008).

    One seat is one card however many attempts it took, so this says which
    source was adopted and what actually answered — ``未記錄`` for a run
    recorded before those fields existed, never a guess. A caller that hands in
    a seat with no lineage at all gets nothing extra rather than an empty box.
    """
    if not attempt:
        return ""
    return (
        '<p class="agent-attempt" data-attempt-outcome="{outcome}">'
        "<span>{label}</span><small>實際模型：{model}</small></p>"
    ).format(
        outcome=_e(attempt.get("terminal_outcome") or ""),
        label=_e(attempt.get("label") or UNRECORDED_LABEL),
        model=_e(attempt.get("actual_model") or UNRECORDED_LABEL),
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
            '<div class="feed" id="live-feed" data-run-id="{}" data-cursor="{}" role="log" '
            'aria-live="polite" aria-relevant="additions" aria-label="辯論聊天室" '
            'tabindex="0">{}{}</div>'.format(
                _e(data["run_id"] or ""), _e(data["cursor"] or ""), messages, empty
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
        _reason(message["public_brief"], message["public_reason"]),
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

def _reason(brief, reason):
    """One paragraph, or a fold that opens onto the rest of the reason.

    概述等於全文時沒有東西可藏，維持原本的一段。不相等時 ``<summary>`` 顯示
    概述，body 接上全文剩下的部分，展開後讀到的仍然恰好是全文，不重複也不缺
    字。展開與收合是原生 ``<details>`` 自己的事，所以這一段沒有、也不需要任
    何 JS —— 這個頁面的 CSP 禁止 inline script。
    """
    label = "<strong>判斷／挑戰理由：</strong>"
    if brief == reason:
        return '<p class="message-reason">{}{}</p>'.format(label, _e(reason))
    displayed_brief = _e(brief)
    if brief.endswith(BRIEF_ELLIPSIS):
        displayed_brief = '{}<span class="reason-ellipsis">{}</span>'.format(
            _e(brief[: -len(BRIEF_ELLIPSIS)]), _e(BRIEF_ELLIPSIS)
        )
    return (
        '<details class="message-reason"><summary>{}{}'
        '<span class="reason-hint">顯示全文</span>'
        '<span class="reason-fold">收合</span></summary>'
        "<p>{}</p></details>"
    ).format(label, displayed_brief, _e(_reason_tail(reason, brief)))

def _reason_tail(reason, brief):
    """The part of ``reason`` a summary showing ``brief`` has not shown.

    概述是完整的第一句時，剩下的就是那一句之後的全部。概述是被字數上限截斷的
    （結尾補了 ``…``）時，那個省略號是截斷記號而不是原文，所以要從截斷處本身
    接下去。句尾字元裡沒有 ``…``，所以「結尾是省略號」只會是截斷的那一種。
    """
    if brief.endswith(BRIEF_ELLIPSIS):
        return reason[len(brief) - len(BRIEF_ELLIPSIS) :]
    return reason[len(brief) :]

__all__ = ('render_live_page', '_live_header', '_live_report_run', '_live_run_bar', '_live_run_options', '_live_focus', '_live_metrics', '_live_layout', '_live_secondary', '_live_rules', '_rule_row', '_live_vote_history', '_vote_history_row', '_live_evidence', '_detail_panel', '_launch_form', '_question_box', '_ask_note', '_asset_class_menu', '_target_box', '_disabled', '_outcome_block', '_live_tally', '_tally_cell', '_live_seats', '_agent_card', '_agent_blurb', '_live_feed', '_message', '_reason', '_reason_tail')
