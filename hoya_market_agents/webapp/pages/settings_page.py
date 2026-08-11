"""Debate-rules settings and timeline page assembly."""

from .components import (
    SETTINGS_FOOTER, SETTINGS_NOTICES, _document, _e, _header,
    latest_report_run, settings,
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
    """Draw one row per schema-v2 vote round."""
    rows = data["timeline"]
    if not rows:
        return ""
    items = "".join(
        '<li><span class="timeline-name">{}</span>'
        '<span class="timeline-value">封存後 {} ms（{}）・門檻 {} 票</span></li>'.format(
            _e(row["label"]),
            _e(row["open_offset_ms"]),
            _e(row["clock"]),
            _e(row["threshold"]),
        )
        for row in rows
    )
    return "\n".join(
        [
            '<section class="card" aria-labelledby="settings-timeline-heading">',
            '<h2 id="settings-timeline-heading">時間軸</h2>',
            '<p class="hint">每一列是一輪；開票時刻都從證據封存那一刻起算。'
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
    if section.get("description"):
        notes.append(
            ("note-section-{}".format(anchor), "hint", _e(section["description"]))
        )
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

__all__ = ('render_settings_page', '_settings_notice', '_settings_locked_notice', '_settings_problem', '_settings_timeline', '_settings_form', '_settings_section', '_settings_title', '_settings_field', '_settings_comments')
