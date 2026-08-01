"""Traditional-Chinese audit view of the shared debate room (Ticket #12).

``render_debate_html`` renders the debate end of the two-page report.  The
market page (``report.html``) links here for the full record; this page's only
navigation responsibility is the link back to 「返回市場結論」.

The renderer never authors, ranks or summarises a market judgement.  It shows
the seats' own public messages in the exact order the run recorded them, plus
the evidence cards those messages cite.  Anything the run did not record is
shown as 「未提供」 and anything structurally unusable fails closed, so the page
can never present a guess as an audited fact.

The output is one self-contained offline HTML document: inline styles only, no
scripts and no runtime network dependency.  Evidence links are the only
outbound URLs and only ``http``/``https`` ones ever become clickable.
"""

import html
from datetime import datetime, timedelta, timezone

from .report_contract import is_safe_source_url
from .report_renderer import (
    CONSENSUS_LABELS,
    SEAT_LABELS,
    SOURCE_TIER_LABELS,
    STANCE_LABELS,
)

MISSING = "未提供"

MESSAGE_KIND_LABELS = {
    "position": "初始立場",
    "challenge": "反方挑戰",
    "response": "回應挑戰",
    "final_vote": "最終投票",
}

DIRECTION_LABELS = {
    "support": "支持目前判斷",
    "oppose": "反對目前判斷",
    "neutral": "中性或限制條件",
}

# Kinds whose messages legitimately have no challenge/response target.
_UNTARGETED_KINDS = ("position", "final_vote")

PAGE_LIMITATIONS = (
    "本頁為公開發言與證據的稽核紀錄，內容由七席自行提出，控制程式只負責轉錄與排版。",
    "未被本次執行記錄的欄位一律顯示「未提供」，不得由本頁推測或補寫。",
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
        '<nav aria-label="頁面導覽">'
        '<a class="button" href="report.html">返回市場結論</a></nav>',
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
        ("執行識別碼", _text(report.get("run_id"))),
        ("分析期間", _text(period.get("label") if isinstance(period, dict) else None)),
        ("共識狀態", _label(report.get("consensus_status"), CONSENSUS_LABELS, "共識狀態")),
        ("採納立場", _stance_label(report.get("adopted_stance"))),
        ("票數", _tally_text(report.get("tally"))),
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
        '<h2 id="transcript-title">公開發言逐則紀錄</h2>',
        "<p>依本次執行記錄的原始順序呈現，未重新排序或摘要。</p>",
    ]
    if not messages:
        parts += ["<p>本次執行沒有記錄任何公開發言。</p>", "</section>"]
        return parts

    parts.append('<ol class="messages">')
    for index, message in enumerate(messages, start=1):
        parts += _message_item(index, message, cards)
    parts += ["</ol>", "</section>"]
    return parts


def _message_item(index, message, cards):
    kind = message.get("kind")
    rows = (
        ("輪次", _round_label(message.get("round"))),
        ("流程時間", _elapsed_label(message.get("elapsed_ms"))),
        ("發言時間", _time_label(message.get("created_at_utc"))),
        ("研究席", _seat_label(message.get("seat_id"))),
        ("發言類型", _label(kind, MESSAGE_KIND_LABELS, "發言類型")),
        ("立場", _stance_label(message.get("stance"))),
        ("公開理由", _text(message.get("public_reason"))),
        ("挑戰／回應對象", _target_text(message)),
        ("改票原因", _text(message.get("stance_change_reason"))),
        ("發言識別碼", _text(message.get("message_id"))),
        ("嘗試識別碼", _text(message.get("attempt_id"))),
        ("內容雜湊", _text(message.get("content_sha256"))),
    )
    parts = [
        '<li class="message" id="message-{}">'.format(index),
        '<h3>第 {} 則：{}／{}</h3>'.format(
            index,
            _e(_seat_label(message.get("seat_id"))),
            _e(_label(kind, MESSAGE_KIND_LABELS, "發言類型")),
        ),
        "<dl>",
    ]
    for term, value in rows:
        parts.append("<dt>{}</dt><dd>{}</dd>".format(_e(term), _e(value)))
    parts += [
        "<dt>引用證據</dt><dd>{}</dd>".format(
            _evidence_links(message.get("evidence_ids"), cards)
        ),
        "</dl>",
        "</li>",
    ]
    return parts


def _evidence_section(evidence):
    parts = [
        '<section id="evidence-library" aria-labelledby="evidence-title">',
        '<h2 id="evidence-title">完整證據卡</h2>',
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
    evidence_id = card["evidence_id"]
    rows = (
        ("提出研究席", _seat_label(card.get("seat_id"))),
        ("資料類別", _text(card.get("category"))),
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
        '<article class="evidence-card" id="{anchor}">'
        '<div class="evidence-card-head"><code>{evidence_id}</code>'
        '<span class="tier">{tier}</span></div>'
        "<p><strong>證據陳述：</strong>{statement}</p>"
        '<dl class="evidence-meta">{meta}</dl>'
        "<p><strong>原文摘錄：</strong>{excerpt}</p>"
        "<p><strong>可信度說明：</strong>{credibility}</p>"
        '<p class="source">{source}</p>'
        "</article>"
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


def _seat_label(value):
    if isinstance(value, str) and value in SEAT_LABELS:
        # Keep the internal id beside the Chinese name so the three artifacts
        # stay cross-referenceable.
        return "{}（{}）".format(SEAT_LABELS[value], value)
    return _label(value, SEAT_LABELS, "席位")


def _stance_label(value):
    return _label(value, STANCE_LABELS, "立場")


def _tier_label(value):
    return _label(value, SOURCE_TIER_LABELS, "來源等級")


def _round_label(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return MISSING
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


def _target_text(message):
    target_seat = message.get("target_seat_id")
    responds_to = message.get("responds_to")
    parts = []
    if isinstance(target_seat, str) and target_seat.strip():
        parts.append("對象席位：{}".format(_seat_label(target_seat)))
    target_claim = _target_claim(message)
    if target_claim is not None:
        parts.append("被挑戰發言：{}".format(target_claim))
    replies = (
        [item for item in responds_to if isinstance(item, str) and item.strip()]
        if isinstance(responds_to, list)
        else []
    )
    if replies:
        parts.append("回應發言：{}".format("、".join(replies)))
    if parts:
        return "；".join(parts)
    if message.get("kind") in _UNTARGETED_KINDS:
        return "不適用（此類發言沒有挑戰或回應對象）"
    return MISSING


def _target_claim(message):
    """Read the challenged wording, which debate.jsonl keeps in ``content``."""
    for holder in (message, message.get("content")):
        if not isinstance(holder, dict):
            continue
        claim = holder.get("target_claim")
        if isinstance(claim, str) and claim.strip():
            return claim
    return None


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
    "--brand:#173b70;--brand-soft:#eaf1fb;--warn:#8a3b00}"
    "*{box-sizing:border-box}body{margin:0;background:var(--wash);color:var(--ink);"
    "font-family:system-ui,'Noto Sans TC',sans-serif;line-height:1.6}"
    "main{max-width:76rem;margin:auto;padding:1.5rem}"
    ".page-header{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin:0 0 1rem}"
    ".eyebrow{margin:0;color:var(--brand);font-size:.78rem;font-weight:800;letter-spacing:.08em}"
    "h1{font-size:2rem;margin:.1rem 0}h2{margin:.2rem 0 .8rem}h3{font-size:1rem;margin:.1rem 0 .6rem}"
    ".button{display:inline-block;background:var(--brand);color:#fff;text-decoration:none;"
    "font-weight:800;padding:.7rem 1rem;border-radius:.5rem}"
    "section,.page-footer{background:var(--paper);border:1px solid var(--line);border-radius:.7rem;"
    "padding:1.1rem;margin:0 0 1rem}"
    "dl{display:grid;grid-template-columns:10rem 1fr;gap:.4rem 1rem;margin:0}"
    "dt{font-weight:750;color:var(--muted)}dd{margin:0}"
    ".messages{list-style:none;padding:0;margin:0;display:grid;gap:.8rem}"
    ".message{border:1px solid var(--line);border-left:4px solid var(--brand);"
    "border-radius:.55rem;padding:.9rem;background:#fbfcfe}"
    ".evidence-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(20rem,1fr));gap:.8rem}"
    ".evidence-card{border:1px solid var(--line);border-radius:.55rem;padding:.9rem;background:#fbfcfe}"
    ".evidence-card-head{display:flex;justify-content:space-between;gap:.5rem;align-items:center}"
    ".evidence-meta{grid-template-columns:7rem 1fr;font-size:.85rem}"
    ".tier{font-size:.75rem;font-weight:750;color:var(--brand)}"
    ".unsafe-url,.unknown{color:var(--warn);font-weight:700;overflow-wrap:anywhere}"
    "a{color:#064f9e;overflow-wrap:anywhere}code{font-weight:700}"
    "@media(max-width:60rem){.page-header{flex-direction:column;align-items:flex-start}"
    "dl,.evidence-meta{grid-template-columns:1fr}}"
    "@media print{body{background:#fff}main{max-width:none;padding:0}.button{display:none}"
    "section,.message,.evidence-card{break-inside:avoid;box-shadow:none}a{color:#000}}"
)
