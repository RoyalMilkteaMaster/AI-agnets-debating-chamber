"""History, outcome statistics and manual-result page assembly."""

from .components import (
    ANY_VALUE, ASSET_CLASSES, CONFIDENCE_LEVELS, CONFIDENCE_WORDS,
    HISTORY_FOOTER, HIT_RATE_FORMULA, HIT_RATE_NOTE, MANUAL_LIST_LIMIT,
    NO_HIT_RATE, NO_REPORT_STATUS, OUTCOME_ORDER, OUTCOME_VERDICTS,
    OUTCOME_WORDS, PAGE_TITLE_HISTORY, STATE_INDEX_MISSING, STATE_OK,
    UNRECORDED_CONSENSUS_STATUS, WHOLE_INDEX_NOTE, _EMPTY, _document, _e,
    _header, _light, _path, _problems, _tally, asset_class_label,
    latest_report_run, outcome_module,
)

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

__all__ = ('render_history_page', '_read_caveat', '_hit_rate', '_stats_table', '_stats_row', '_stats_write_notice', '_manual_form', '_filter_form', '_field', '_choice_field', '_datalist', '_history_result', '_index_unavailable', '_history_table', '_history_row', '_run_status', '_outcome_cell')
