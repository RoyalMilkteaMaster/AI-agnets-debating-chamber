"""One run's summary, report, votes, evidence and transcript page assembly."""

from .components import (
    EXPORT_NOTICES, EXPORT_PDF_LABEL, RUN_DETAIL_FOOTER, _EMPTY, _document,
    _e, _evidence_card, _header, _light, _path, _tally, asset_class_label,
    export_pdf_path, pdf_export,
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

def _run_summary(data):
    consensus = data["consensus"]
    confidence = data["confidence"] or {}
    rows = [
        ("run_id", data["run_id"]),
        ("日期", data["run_date"]),
        ("資產類別", asset_class_label(data["asset_class"])),
        ("標的", "、".join(data["assets"]) or _EMPTY),
        ("題型", data["question_type_label"] or _EMPTY),
        ("共識狀態", consensus["status_label"] or _EMPTY),
        ("採納立場", consensus["adopted_label"] or _EMPTY),
        ("停止原因", consensus["stop_reason_label"] or _EMPTY),
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

__all__ = ('render_run_page', '_run_summary', '_run_report', '_run_export', '_export_sentence', '_export_notice', '_run_votes', '_seat_row', '_seat_change', '_run_evidence', '_run_transcript', '_missing_block')
