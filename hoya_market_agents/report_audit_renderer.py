"""Traditional-Chinese audit view of the shared debate room (Ticket #12).

``render_debate_html`` renders the full public debate record as a seven-seat
group chat.  Its shared page tabs link to the live dashboard, market report and
this audit view, with the current page marked for assistive technology.

The renderer never authors, ranks or summarises a market judgement.  It shows
the seats' own public messages in the exact order the run recorded them, plus
the evidence cards those messages cite.  Anything the run did not record is
shown as 「未提供」 and anything structurally unusable fails closed, so the page
can never present a guess as an audited fact.

A reader of this page is a human auditor, not an operator, so each turn carries
only what a reader needs to judge the argument: a cute avatar, the seat's
Traditional-Chinese name, the elapsed offset, the stance, the verbatim public
reason, whether the stance moved and the evidence it cites.  Internal plumbing
identifiers — ``message_id``, ``attempt_id``, ``content_sha256`` and the raw
``seat_id`` — are deliberately never rendered, not even inside a hidden, data,
title or ARIA attribute.  They stay in the run artifacts, which remain the
authority for machine-level cross-referencing.

The output is one self-contained offline HTML document: inline styles only, no
scripts and no runtime network dependency.  Evidence links are the only
outbound URLs and only ``http``/``https`` ones ever become clickable.
"""

import html
from datetime import datetime, timedelta, timezone

from .report_contract import is_safe_source_url
from .live_dashboard import AGENT_PROFILES
from .report_renderer import (
    CONSENSUS_LABELS,
    SEAT_LABELS,
    SOURCE_TIER_LABELS,
    STANCE_LABELS,
)

MISSING = "未提供"

# Reuse the live room's names and avatars so the same Agent is recognisable on
# all three pages.
SEAT_AVATARS = {seat_id: profile[2] for seat_id, profile in AGENT_PROFILES.items()}
SEAT_CHAT_NAMES = {
    seat_id: "{}（{}）".format(profile[0], profile[1])
    for seat_id, profile in AGENT_PROFILES.items()
}

UNKNOWN_AVATAR = "❔"
UNKNOWN_SEAT = "{}（無法對應的研究席）".format(MISSING)

# Colour tone per seat so the chat stays readable without naming the seat id.
_SEAT_TONES = {seat_id: index + 1 for index, seat_id in enumerate(SEAT_LABELS)}

MESSAGE_KIND_LABELS = {
    "position": "初始立場",
    "challenge": "反方挑戰",
    "response": "回應挑戰",
    "final_vote": "最終投票",
}

# The heading above a turn's verbatim public reason.
REASON_HEADINGS = {
    "position": "判斷理由",
    "response": "判斷理由",
    "final_vote": "判斷理由",
    "challenge": "挑戰理由",
}
DEFAULT_REASON_HEADING = "公開理由"

DIRECTION_LABELS = {
    "support": "支持目前判斷",
    "oppose": "反對目前判斷",
    "neutral": "中性或限制條件",
}

PAGE_LIMITATIONS = (
    "本頁為公開發言與證據的稽核紀錄，內容由七席自行提出，控制程式只負責轉錄與排版。",
    "發言依本次執行記錄的原始順序逐字轉錄，未重新排序、改寫或摘要。",
    "未被本次執行記錄的欄位一律顯示「未提供」，不得由本頁推測或補寫。",
    "本頁只呈現公開內容，內部識別碼與雜湊值保留在執行紀錄檔中，不在本頁顯示。",
)


class DebateAuditError(ValueError):
    """The debate or evidence snapshot cannot be rendered without guessing."""


def render_debate_html(report, sources):
    """Render the debate room and evidence cards as one offline HTML page."""
    messages, evidence = _validated(report, sources)
    cards = {card["evidence_id"]: card for card in evidence}

    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-Hant">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>完整辯論室 — Hoya Bit 可稽核市場研究</title>",
        "<style>{}</style>".format(_CSS),
        "</head>",
        "<body>",
        "<main>",
        '<header class="page-header">',
        '<div><p class="eyebrow">Hoya Bit 可稽核市場研究</p><h1>完整辯論室</h1></div>',
        '<nav class="page-tabs" aria-label="主要頁面">'
        '<a href="live.html">即時辯論</a>'
        '<a href="report.html">市場報告</a>'
        '<a href="debate.html" aria-current="page">完整辯論</a></nav>',
        "</header>",
    ]
    parts += _summary_section(report, messages, evidence)
    parts += _transcript_section(messages, cards)
    parts += _evidence_section(evidence)
    parts += ['<footer class="page-footer"><h2>本頁限制</h2><ul>']
    parts += ["<li>{}</li>".format(_e(item)) for item in PAGE_LIMITATIONS]
    parts += ["</ul></footer>", "</main>", "</body>", "</html>", ""]
    return "\n".join(parts)


def evidence_anchor(evidence_id):
    """Return a safe, unique and predictable anchor id for one evidence id.

    Every character outside ``[A-Za-z0-9-]`` is encoded as ``_<hex>_``.  The
    escape character itself is encoded the same way, so two different evidence
    ids can never collide on one anchor.
    """
    if not isinstance(evidence_id, str) or not evidence_id:
        raise DebateAuditError("evidence_id 必須為非空字串才能建立 anchor。")
    encoded = []
    for character in evidence_id:
        if character == "-" or (character.isascii() and character.isalnum()):
            encoded.append(character)
        else:
            encoded.append("_{:x}_".format(ord(character)))
    return "evidence-{}".format("".join(encoded))


# -- validation -------------------------------------------------------------


def _validated(report, sources):
    """Fail closed on anything that cannot be rendered without inventing data."""
    if not isinstance(report, dict):
        raise DebateAuditError("report 必須為物件")
    if not isinstance(sources, dict):
        raise DebateAuditError("sources 必須為物件")

    debate = sources.get("debate")
    if not isinstance(debate, list):
        raise DebateAuditError("sources.debate 必須為陣列")
    messages = []
    for index, entry in enumerate(debate):
        if not isinstance(entry, dict):
            raise DebateAuditError("sources.debate[{}] 必須為物件".format(index))
        # A caller may hand over the whole debate.jsonl; only the seats' own
        # public messages belong on this page, in their recorded order.
        if entry.get("event", "seat_message") == "seat_message":
            messages.append(entry)

    evidence = sources.get("evidence")
    if not isinstance(evidence, list):
        raise DebateAuditError("sources.evidence 必須為陣列")
    seen = set()
    for index, card in enumerate(evidence):
        if not isinstance(card, dict):
            raise DebateAuditError("sources.evidence[{}] 必須為物件".format(index))
        evidence_id = card.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise DebateAuditError("sources.evidence[{}] 缺少 evidence_id".format(index))
        if evidence_id in seen:
            raise DebateAuditError("sources evidence ID 重複：{}".format(evidence_id))
        seen.add(evidence_id)
    return messages, evidence


# -- sections ---------------------------------------------------------------


def _summary_section(report, messages, evidence):
    period = report.get("period")
    rows = (
        ("共識狀態", _label(report.get("consensus_status"), CONSENSUS_LABELS, "共識狀態")),
        ("採納立場", _stance_label(report.get("adopted_stance"))),
        ("票數", _tally_text(report.get("tally"))),
        ("分析期間", _text(period.get("label") if isinstance(period, dict) else None)),
        ("公開發言則數", "{} 則".format(len(messages))),
        ("證據卡數", "{} 張".format(len(evidence))),
    )
    parts = [
        '<section class="run-summary" aria-labelledby="summary-title">',
        '<h2 id="summary-title">本場辯論摘要</h2>',
        "<dl>",
    ]
    for term, value in rows:
        parts.append("<dt>{}</dt><dd>{}</dd>".format(_e(term), _e(value)))
    parts += ["</dl>", "</section>"]
    return parts


def _transcript_section(messages, cards):
    parts = [
        '<section class="transcript" aria-labelledby="transcript-title">',
        '<h2 id="transcript-title">七席公開辯論聊天室</h2>',
        "<p>依本次執行記錄的原始順序逐則轉錄，未重新排序或摘要。</p>",
    ]
    if not messages:
        parts += ["<p>本次執行沒有記錄任何公開發言。</p>", "</section>"]
        return parts

    changes = _stance_changes(messages)
    parts.append('<ol class="chat">')
    previous_round = None
    for index, message in enumerate(messages):
        round_label = _round_label(message.get("round"))
        if round_label is not None and round_label != previous_round:
            parts.append(
                '<li class="round-mark"><span>{}</span></li>'.format(_e(round_label))
            )
            previous_round = round_label
        parts += _turn_item(index + 1, message, changes[index], cards)
    parts += ["</ol>", "</section>"]
    return parts


def _turn_item(number, message, change, cards):
    """Render one public message as a chat bubble.

    Nothing here may carry an internal identifier: the speaker, the seat a
    challenge is aimed at and every reply target are all shown by their
    Traditional-Chinese name only.
    """
    seat_id = message.get("seat_id")
    kind = message.get("kind")
    parts = [
        '<li class="turn tone-{}" id="turn-{}">'.format(_tone(seat_id), number),
        '<span class="avatar" aria-hidden="true">{}</span>'.format(_e(_avatar(seat_id))),
        '<article class="bubble">',
        '<h3 class="speaker">{}</h3>'.format(_e(_seat_name(seat_id))),
        '<p class="meta">'
        '<span class="clock">{clock}</span>'
        '<span class="stance">立場：{stance}</span>'
        "</p>".format(
            clock=_e(_elapsed_label(message.get("elapsed_ms"))),
            stance=_e(_stance_label(message.get("stance"))),
        ),
    ]
    parts += [
        '<div class="says"><h4>{}</h4><p>{}</p></div>'.format(
            _e(REASON_HEADINGS.get(kind, DEFAULT_REASON_HEADING)),
            _e(_text(message.get("public_reason"))),
        )
    ]
    parts += _change_block(change)
    parts += [
        '<p class="cites">引用證據：{}</p>'.format(
            _evidence_links(message.get("evidence_ids"), cards)
        ),
        "</article>",
        "</li>",
    ]
    return parts


def _change_block(change):
    if change["changed"]:
        return [
            '<div class="change changed"><h4>是否變更立場</h4>',
            "<p>是：{} → {}</p>".format(
                _e(_stance_label(change["previous"])), _e(_stance_label(change["current"]))
            ),
            "<p>公開變更原因：{}</p>".format(_e(change["reason"] or MISSING)),
            "</div>",
        ]
    if change["previous"] is None:
        parts = ['<p class="change">是否變更立場：否（首次公開表態）</p>']
        if change["reason"]:
            parts.append(
                '<p class="change-note">本則另記錄的公開說明：{}</p>'.format(
                    _e(change["reason"])
                )
            )
        return parts
    if change["reason"]:
        # The run recorded a public note without moving the stance; showing it
        # keeps the record complete without claiming a change happened.
        return [
            '<p class="change">是否變更立場：未變更</p>',
            '<p class="change-note">本則另記錄的公開說明：{}</p>'.format(_e(change["reason"])),
        ]
    return ['<p class="change">是否變更立場：未變更</p>']


def _stance_changes(messages):
    """Derive each turn's stance move from the seats' own recorded stances.

    The comparison uses only stances this run actually recorded earlier in the
    same transcript, so a move is reported when the record shows one and never
    otherwise.
    """
    latest = {}
    result = []
    for message in messages:
        seat_id = message.get("seat_id")
        key = seat_id if isinstance(seat_id, str) and seat_id.strip() else None
        stance = message.get("stance")
        current = stance if isinstance(stance, str) and stance in STANCE_LABELS else None
        previous = latest.get(key) if key is not None else None
        reason = message.get("stance_change_reason")
        reason = reason if isinstance(reason, str) and reason.strip() else None
        differs = previous is not None and current is not None and previous != current
        result.append(
            {
                "changed": differs,
                "previous": previous,
                "current": current,
                "reason": reason,
            }
        )
        if key is not None and current is not None:
            latest[key] = current
    return result


def _evidence_section(evidence):
    parts = [
        '<section id="evidence-library" aria-labelledby="evidence-title">',
        '<h2 id="evidence-title">完整證據卡</h2>',
        "<p>每張證據卡預設收合，點選或按 Enter 即可展開完整內容與來源。</p>",
    ]
    if not evidence:
        parts += ["<p>本次執行沒有記錄任何證據卡。</p>", "</section>"]
        return parts

    parts.append('<div class="evidence-grid">')
    for card in evidence:
        parts.append(_evidence_card(card))
    parts += ["</div>", "</section>"]
    return parts


def _evidence_card(card):
    """Render one evidence card as a natively collapsible ``<details>``.

    No ``open`` attribute is ever emitted: the reader chooses what to expand,
    and the page still works with scripting disabled.
    """
    evidence_id = card["evidence_id"]
    rows = (
        ("提出研究席", _seat_name(card.get("seat_id"))),
        ("資料類別", _category_label(card.get("category"))),
        ("證據方向", _label(card.get("direction"), DIRECTION_LABELS, "證據方向")),
        ("來源網站", _text(card.get("source_origin"))),
        ("來源等級", _tier_label(card.get("source_tier"))),
        ("發布時間", _time_label(card.get("published_at_utc"))),
        ("取得時間", _time_label(card.get("retrieved_at_utc"))),
    )
    meta = "".join(
        "<dt>{}</dt><dd>{}</dd>".format(_e(term), _e(value)) for term, value in rows
    )
    return (
        '<details class="evidence-card" id="{anchor}">'
        "<summary>"
        '<span class="ev-id">證據卡 <code>{evidence_id}</code></span>'
        '<span class="tier">{tier}</span>'
        '<span class="hint">點選展開完整證據內容</span>'
        "</summary>"
        '<div class="evidence-body">'
        "<p><strong>證據陳述：</strong>{statement}</p>"
        '<dl class="evidence-meta">{meta}</dl>'
        "<p><strong>原文摘錄：</strong>{excerpt}</p>"
        "<p><strong>可信度說明：</strong>{credibility}</p>"
        '<p class="source">{source}</p>'
        "</div></details>"
    ).format(
        anchor=_e(evidence_anchor(evidence_id)),
        evidence_id=_e(evidence_id),
        tier=_e(_tier_label(card.get("source_tier"))),
        statement=_e(_text(card.get("statement"))),
        meta=meta,
        excerpt=_e(_text(card.get("excerpt"))),
        credibility=_e(_text(card.get("credibility_note"))),
        source=_source_html(card.get("source_url")),
    )


def _source_html(url):
    if not isinstance(url, str) or not url.strip():
        return '<span class="unsafe-url">來源網址{}</span>'.format(_e(MISSING))
    if is_safe_source_url(url):
        return '<a class="source-link" href="{}" rel="noreferrer">開啟來源網址：{}</a>'.format(
            _e(url), _e(url)
        )
    return '<span class="unsafe-url">來源網址不是 http 或 https，未建立連結：{}</span>'.format(
        _e(url)
    )


def _evidence_links(evidence_ids, cards):
    if not isinstance(evidence_ids, list) or not evidence_ids:
        return _e(MISSING)
    rendered = []
    for evidence_id in evidence_ids:
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            rendered.append('<span class="unknown">{}</span>'.format(_e(MISSING)))
        elif evidence_id in cards:
            rendered.append(
                '<a href="#{}"><code>{}</code></a>'.format(
                    _e(evidence_anchor(evidence_id)), _e(evidence_id)
                )
            )
        else:
            rendered.append(
                '<span class="unknown"><code>{}</code>（未收錄於證據快照）</span>'.format(
                    _e(evidence_id)
                )
            )
    return "、".join(rendered)


# -- labels -----------------------------------------------------------------


def _text(value):
    if value is None:
        return MISSING
    text = str(value)
    return text if text.strip() else MISSING


def _label(value, mapping, noun):
    """Map an internal enum value, never inventing a meaning for a new one."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return MISSING
    try:
        if not isinstance(value, bool) and value in mapping:
            return mapping[value]
    except TypeError:
        pass
    return "{}（未知{}代碼：{}）".format(MISSING, noun, value)


def _seat_name(value):
    """Name a seat in Traditional Chinese, never exposing its internal id."""
    if isinstance(value, str) and value in SEAT_CHAT_NAMES:
        return SEAT_CHAT_NAMES[value]
    if value is None or (isinstance(value, str) and not value.strip()):
        return MISSING
    return UNKNOWN_SEAT


def _avatar(value):
    if isinstance(value, str) and value in SEAT_AVATARS:
        return SEAT_AVATARS[value]
    return UNKNOWN_AVATAR


def _tone(value):
    return _SEAT_TONES.get(value, 0) if isinstance(value, str) else 0


def _category_label(value):
    if isinstance(value, str) and value in SEAT_LABELS:
        return SEAT_LABELS[value]
    return _text(value)


def _stance_label(value):
    return _label(value, STANCE_LABELS, "立場")


def _tier_label(value):
    return _label(value, SOURCE_TIER_LABELS, "來源等級")


def _round_label(value):
    """Return the round divider text, or ``None`` when no round was recorded."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    if value == 0:
        return "初始立場"
    return "第 {} 輪".format(value)


def _elapsed_label(value):
    """Render the recorded monotonic offset as ``T+MM:SS``."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return MISSING
    seconds = value // 1000
    return "T+{:02d}:{:02d}".format(seconds // 60, seconds % 60)


def _time_label(value):
    if not isinstance(value, str) or not value.strip():
        return MISSING
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    taipei = parsed.astimezone(timezone(timedelta(hours=8)))
    return "{}（台北時間；原始時間 {}）".format(
        taipei.strftime("%Y/%m/%d %H:%M:%S"), value
    )


def _tally_text(tally):
    if not isinstance(tally, dict) or not tally:
        return MISSING
    return "／".join(
        "{}：{}".format(_stance_label(stance), _text(count))
        for stance, count in tally.items()
    )


def _e(value):
    return html.escape("" if value is None else str(value), quote=True)


_CSS = (
    ":root{--ink:#172033;--muted:#5d687b;--line:#d8dee9;--paper:#fff;--wash:#f3f6fb;"
    "--brand:#173b70;--brand-soft:#eaf1fb;--warn:#8a3b00;--tone:#173b70}"
    "*{box-sizing:border-box}body{margin:0;background:var(--wash);color:var(--ink);"
    "font-family:system-ui,'Noto Sans TC',sans-serif;line-height:1.6}"
    "main{max-width:76rem;margin:auto;padding:1.5rem}"
    ".page-header{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin:0 0 1rem}"
    ".eyebrow{margin:0;color:var(--brand);font-size:.78rem;font-weight:800;letter-spacing:.08em}"
    "h1{font-size:2rem;margin:.1rem 0}h2{margin:.2rem 0 .8rem}"
    "h3{font-size:1rem;margin:0}h4{font-size:.8rem;margin:.55rem 0 .1rem;color:var(--muted)}"
    ".page-tabs{display:flex;gap:.25rem;padding:.3rem;background:var(--brand-soft);border:1px solid var(--line);"
    "border-radius:.65rem}.page-tabs a{color:var(--brand);text-decoration:none;font-weight:800;"
    "padding:.55rem .75rem;border-radius:.4rem;white-space:nowrap}.page-tabs a[aria-current=page]{"
    "background:var(--brand);color:#fff}"
    "a:focus-visible,summary:focus-visible{outline:3px solid #f0b429;outline-offset:2px}"
    "section,.page-footer{background:var(--paper);border:1px solid var(--line);border-radius:.7rem;"
    "padding:1.1rem;margin:0 0 1rem}"
    "dl{display:grid;grid-template-columns:10rem 1fr;gap:.4rem 1rem;margin:0}"
    "dt{font-weight:750;color:var(--muted)}dd{margin:0}"
    ".chat{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:.7rem}"
    ".round-mark{display:flex;align-items:center;gap:.6rem;color:var(--muted);font-size:.8rem;"
    "font-weight:800;margin:.35rem 0}"
    ".round-mark span{background:var(--brand-soft);border-radius:1rem;padding:.15rem .7rem}"
    ".turn{display:flex;gap:.6rem;align-items:flex-start}"
    ".avatar{flex:none;width:2.6rem;height:2.6rem;border-radius:50%;display:grid;place-items:center;"
    "font-size:1.4rem;background:var(--brand-soft);border:1px solid var(--line)}"
    ".bubble{flex:1;min-width:0;background:#fbfcfe;border:1px solid var(--line);"
    "border-left:4px solid var(--tone);border-radius:.25rem .8rem .8rem .8rem;padding:.8rem .9rem}"
    ".tone-1{--tone:#1f6f4a}.tone-2{--tone:#8a5a13}.tone-3{--tone:#2f5ea8}.tone-4{--tone:#7357b4}"
    ".tone-5{--tone:#0f7488}.tone-6{--tone:#a3486b}.tone-7{--tone:#8a3b00}.tone-0{--tone:#5d687b}"
    ".speaker{font-size:1.02rem;font-weight:850;color:var(--tone)}"
    ".meta{display:flex;flex-wrap:wrap;gap:.4rem .7rem;margin:.2rem 0 .1rem;font-size:.82rem;color:var(--muted)}"
    ".meta .clock{font-variant-numeric:tabular-nums}"
    ".meta .stance{font-weight:800;color:var(--ink)}"
    ".says p,.change,.change-note,.cites{margin:.15rem 0}"
    ".says p{white-space:pre-wrap}"
    ".change{font-size:.88rem}.change.changed{border:1px dashed var(--tone);border-radius:.45rem;"
    "padding:.4rem .6rem;margin:.5rem 0;background:#fff}"
    ".change-note{font-size:.85rem;color:var(--muted)}"
    ".cites{font-size:.85rem;margin-top:.5rem}"
    ".evidence-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(20rem,1fr));gap:.8rem;"
    "align-items:start}"
    ".evidence-card{border:1px solid var(--line);border-radius:.55rem;background:#fbfcfe}"
    ".evidence-card summary{cursor:pointer;padding:.75rem .9rem;display:flex;flex-wrap:wrap;gap:.35rem .6rem;"
    "align-items:center;font-weight:750;border-radius:.55rem}"
    ".evidence-card .hint{color:var(--muted);font-size:.78rem;font-weight:600}"
    ".evidence-body{padding:0 .9rem .9rem}"
    ".evidence-meta{grid-template-columns:7rem 1fr;font-size:.85rem}"
    ".tier{font-size:.75rem;font-weight:750;color:var(--brand)}"
    ".unsafe-url,.unknown{color:var(--warn);font-weight:700;overflow-wrap:anywhere}"
    "a{color:#064f9e;overflow-wrap:anywhere}code{font-weight:700}"
    "@media(max-width:60rem){.page-header{flex-direction:column;align-items:flex-start}"
    ".page-tabs{width:100%}.page-tabs a{flex:1;text-align:center}dl,.evidence-meta{grid-template-columns:1fr}}"
    "@media print{body{background:#fff}main{max-width:none;padding:0}.page-tabs{display:none}"
    ".evidence-card .hint{display:none}"
    "section,.turn,.evidence-card{break-inside:avoid;box-shadow:none}a{color:#000}}"
)
