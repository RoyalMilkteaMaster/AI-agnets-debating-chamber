"""Traditional-Chinese audit view of the shared debate room (Ticket #12).

``render_debate_html`` renders the full public debate record as a seven-seat
group chat.  Its shared page tabs are exactly the pages the run bundle carries
— ``report.html`` and this audit view — with the current page marked for
assistive technology.  Every tab is a relative link to a file that ships in the
same run directory, which is the only kind of link a sealed offline bundle can
honour.

The tabs deliberately do not offer the live debate room.  That room is a served
page with a different lifetime, so a bundle can neither carry it as a file nor
reach it once archived; linking to it produced a tab that always 404'd, and
pointing the tab at the server route instead would only trade a dead link for
one that breaks whenever the server is not running.  Nothing is lost by its
absence: the live room's content *is* the debate, and this page is that debate,
transcribed verbatim.

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

No colour is decided here either: the sheet is assembled by
:func:`~hoya_market_agents.report_renderer.stylesheet` from
:mod:`~hoya_market_agents.design_tokens`, so this page and the market report are
painted from one table (Spec R-004).
"""

import html
from datetime import datetime, timedelta, timezone

from . import design_tokens
from .report_contract import is_safe_source_url
from .seats import SEAT_AVATARS, SEAT_IDS, seat_display_names, seat_identities
from .report_renderer import (
    CONSENSUS_LABELS,
    SOURCE_TIER_LABELS,
    STANCE_LABELS,
    decorative_hairline,
    resolve_stance_labels,
    stylesheet,
)

MISSING = "未提供"

# 本模組單一寫入者（launcher）每個 run 只 render 一次；標籤在 render 開頭
# 依該 run 的題型票面與資產類別解析一次，之後所有 helper 共用。
_ACTIVE_STANCE_LABELS = dict(STANCE_LABELS)
# 席位名稱依 run 的資產類別選套（ADR 0006），所以它不是模組常數：台股場的逐字稿
# 不得印出幣圈席名。權威是 roster，本模組不自己拼任何一個名字。
_ACTIVE_SEAT_LABELS = {}
_ACTIVE_SEAT_CHAT_NAMES = {}


def _activate_stance_labels(report):
    """Resolve this run's stance vocabulary before any label lookup."""
    tally = report.get("tally")
    stances = tuple(tally.keys()) if isinstance(tally, dict) and tally else tuple(
        STANCE_LABELS
    )
    provided = report.get("stance_labels")
    provided = provided if isinstance(provided, dict) else None
    _ACTIVE_STANCE_LABELS.clear()
    _ACTIVE_STANCE_LABELS.update(
        resolve_stance_labels(stances, report.get("assets") or (), provided)
    )


def _activate_seat_names(report):
    """Resolve this run's seat names from the roster before any lookup."""
    asset_class = report.get("asset_class")
    _ACTIVE_SEAT_LABELS.clear()
    _ACTIVE_SEAT_LABELS.update(seat_display_names(asset_class))
    _ACTIVE_SEAT_CHAT_NAMES.clear()
    _ACTIVE_SEAT_CHAT_NAMES.update(
        {
            seat_id: "{}（{}）".format(identity.display_name, identity.agent_number)
            for seat_id, identity in seat_identities(asset_class).items()
        }
    )


UNKNOWN_AVATAR = "❔"
UNKNOWN_SEAT = "{}（無法對應的研究席）".format(MISSING)

# Colour tone per seat so the chat stays readable without naming the seat id.
_SEAT_TONES = {seat_id: index + 1 for index, seat_id in enumerate(SEAT_IDS)}

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
    # 標籤解析必須在 fail-closed 驗證之後：結構不可用的輸入要走原本的
    # 拒絕路徑，不能先在這裡炸出 AttributeError。
    _activate_stance_labels(report)
    _activate_seat_names(report)
    cards = {card["evidence_id"]: card for card in evidence}

    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-Hant">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>完整辯論室 — Hoya Bit 可稽核市場研究</title>",
        "<style>{}</style>".format(stylesheet(_CSS)),
        "</head>",
        "<body>",
        "<main>",
        '<header class="page-header">',
        '<div><p class="eyebrow">Hoya Bit 可稽核市場研究</p><h1>完整辯論室</h1></div>',
        # 只列這份 bundle 真的帶著的頁面；理由見模組 docstring。
        '<nav class="page-tabs" aria-label="主要頁面">'
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
    consensus = _label(report.get("consensus_status"), CONSENSUS_LABELS, "共識狀態")
    stance = _stance_label(report.get("adopted_stance"))
    rows = (
        ("分析期間", _text(period.get("label") if isinstance(period, dict) else None)),
        ("公開發言則數", "{} 則".format(len(messages))),
        ("證據卡數", "{} 張".format(len(evidence))),
    )
    parts = [
        '<section class="run-summary" aria-labelledby="summary-title">',
        '<div class="summary-main"><div><p class="eyebrow">本場辯論摘要</p>',
        '<h2 id="summary-title">{}｜採納立場：{}</h2>'.format(
            _e(consensus), _e(stance)
        ),
        '<p class="summary-tally">{}</p></div>'.format(_e(_tally_text(report.get("tally")))),
        '<a class="primary-action" href="report.html">回到市場報告</a></div>',
        '<dl class="summary-meta">',
        '<dt>共識狀態</dt><dd>{}</dd>'.format(_e(consensus)),
        '<dt>採納立場</dt><dd>{}</dd>'.format(_e(stance)),
        '<dt>票數</dt><dd>{}</dd>'.format(_e(_tally_text(report.get("tally")))),
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
        # 立場在寫入時已由狀態機驗證過；稽核頁只如實轉述，不再用詞彙表過濾，
        # 否則非市場題型的合法立場會被誤判成 None、改票偵測整段失效。
        current = stance if isinstance(stance, str) and stance.strip() else None
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
    if isinstance(value, str) and value in _ACTIVE_SEAT_CHAT_NAMES:
        return _ACTIVE_SEAT_CHAT_NAMES[value]
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
    if isinstance(value, str) and value in _ACTIVE_SEAT_LABELS:
        return _ACTIVE_SEAT_LABELS[value]
    return _text(value)


def _stance_label(value):
    return _label(value, _ACTIVE_STANCE_LABELS, "立場")


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


def _tone_rules():
    """One stripe colour per seat, cycled over the four decorative hues.

    The stripe is what lets a reader see at a glance that two bubbles are the
    same speaker; it has never been the only signal, because every bubble is
    signed with the seat's name in words. So seven seats over four hues — two
    seats sharing a stripe — loses nothing a reader was relying on, and it keeps
    the page inside the decorative vocabulary R-004 approved instead of inventing
    three more colours of its own.

    Generated from :data:`~hoya_market_agents.design_tokens.DECOR_TOKENS` and
    :data:`~hoya_market_agents.seats.SEAT_IDS`, so an eighth seat gets a stripe
    and a fifth hue joins the rotation without either being written out here.
    """
    hues = design_tokens.DECOR_TOKENS
    return "".join(
        ".tone-{}{{--tone:var(--{});}}".format(
            number, hues[(number - 1) % len(hues)].replace("_", "-")
        )
        for number in range(1, len(SEAT_IDS) + 1)
    )


# The debate room's rules. Same reading as the market report: white cards on the
# grey canvas, the frosted panel for the one summary at the top, hairlines and
# space instead of fills.
#
# **A decorative hue is a stripe, never a word.** ``DECOR_INK`` guarantees that
# text placed *on* one of the four fills can be read; a brand yellow used as text
# on white is 1.7:1 and is not AA at any size. So ``--tone`` reaches a border and
# an avatar's ring, and the speaker's name is plain ``--text``.
_CSS = """
*{box-sizing:border-box;}
body{margin:0;background:var(--page);color:var(--text);font-family:var(--font-sans);
 font-size:var(--size-md);line-height:var(--line-base);}
main{max-width:var(--shell);margin:auto;padding:var(--space-6);}
h1,h2,h3,h4{line-height:var(--line-tight);}
h1{font-size:var(--size-xl);margin:var(--space-1) 0;}
h2{margin:var(--space-1) 0 var(--space-4);font-size:var(--size-lg);}
h3{margin:0;font-size:var(--size-md);}
h4{margin:var(--space-3) 0 var(--space-1);color:var(--muted);font-size:var(--size-xs);}
a{color:var(--link);overflow-wrap:anywhere;}
code{font-family:var(--font-mono);font-size:var(--size-sm);}
a:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:2px;}
.page-header{display:flex;justify-content:space-between;align-items:center;
 gap:var(--space-5);margin:0 0 var(--space-5);}
.eyebrow{margin:0;color:var(--muted);font-size:var(--size-2xs);font-weight:700;
 letter-spacing:.08em;text-transform:uppercase;}
.page-tabs{display:flex;gap:var(--space-1);padding:var(--space-1);
 background-color:var(--glass-surface);border:1px solid var(--border);
 border-radius:var(--radius-pill);
 -webkit-backdrop-filter:blur(var(--glass-blur));backdrop-filter:blur(var(--glass-blur));}
.page-tabs a{color:var(--link);text-decoration:none;font-weight:700;
 padding:var(--space-3) var(--space-4);border-radius:var(--radius-pill);white-space:nowrap;}
.page-tabs a[aria-current=page]{background:var(--accent);color:var(--accent-text);}
section,.page-footer{background:var(--surface);border:1px solid var(--border);
 border-radius:var(--radius-lg);padding:var(--space-6);margin:0 0 var(--space-5);}
.run-summary{padding-top:var(--space-7);background-color:var(--glass-surface);
 -webkit-backdrop-filter:blur(var(--glass-blur));backdrop-filter:blur(var(--glass-blur));}
.run-summary .eyebrow{color:var(--accent);}
.summary-main{display:flex;align-items:center;justify-content:space-between;
 gap:var(--space-6);}
.summary-main h2{margin:var(--space-1) 0;font-size:var(--size-xl);}
.summary-tally{margin:var(--space-1) 0;color:var(--muted);font-weight:700;}
.primary-action{flex:none;background:var(--accent);color:var(--accent-text);
 font-weight:700;text-decoration:none;padding:var(--space-4) var(--space-5);
 border-radius:var(--radius-md);}
.summary-meta{grid-template-columns:7rem 1fr;max-width:36rem;
 margin-top:var(--space-5);padding-top:var(--space-4);
 border-top:1px solid var(--border);}
dl{display:grid;grid-template-columns:10rem 1fr;gap:var(--space-2) var(--space-5);margin:0;}
dt{font-weight:700;color:var(--muted);}
dd{margin:0;}
.chat{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;
 gap:var(--space-4);}
.round-mark{display:flex;align-items:center;gap:var(--space-3);color:var(--muted);
 font-size:var(--size-xs);font-weight:700;margin:var(--space-2) 0;}
.round-mark span{background:var(--page);border:1px solid var(--border);
 border-radius:var(--radius-pill);padding:var(--space-1) var(--space-4);}
.turn{display:flex;gap:var(--space-3);align-items:flex-start;--tone:var(--border);}
.avatar{flex:none;width:2.6rem;height:2.6rem;border-radius:50%;display:grid;
 place-items:center;font-size:var(--size-lg);background:var(--page);
 border:1px solid var(--tone);}
.bubble{flex:1;min-width:0;background:var(--surface);border:1px solid var(--border);
 border-left:4px solid var(--tone);padding:var(--space-4) var(--space-5);
 border-radius:var(--radius-sm) var(--radius-lg) var(--radius-lg) var(--radius-lg);}
.speaker{color:var(--text);font-size:var(--size-md);font-weight:700;}
.meta{display:flex;flex-wrap:wrap;gap:var(--space-1) var(--space-4);
 margin:var(--space-1) 0;font-size:var(--size-xs);color:var(--muted);}
.meta .clock{font-variant-numeric:tabular-nums;}
.meta .stance{font-weight:700;color:var(--text);}
.says p,.change,.change-note,.cites{margin:var(--space-1) 0;}
.says p{white-space:pre-wrap;}
.change{font-size:var(--size-sm);}
.change.changed{border:1px dashed var(--tone);border-radius:var(--radius-sm);
 padding:var(--space-2) var(--space-3);margin:var(--space-3) 0;background:var(--page);}
.change-note{font-size:var(--size-sm);color:var(--muted);}
.cites{font-size:var(--size-sm);margin-top:var(--space-3);}
.evidence-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(20rem,1fr));
 gap:var(--space-4);align-items:start;}
.evidence-card{background:var(--surface);border:1px solid var(--border);
 border-radius:var(--radius-md);}
.evidence-card summary{cursor:pointer;padding:var(--space-4) var(--space-5);
 display:flex;flex-wrap:wrap;gap:var(--space-2) var(--space-3);align-items:center;
 font-weight:700;border-radius:var(--radius-md);}
.evidence-card .hint{color:var(--muted);font-size:var(--size-2xs);font-weight:400;}
.evidence-body{padding:0 var(--space-5) var(--space-5);}
.evidence-meta{grid-template-columns:7rem 1fr;font-size:var(--size-sm);}
.tier{color:var(--accent);font-size:var(--size-2xs);font-weight:700;}
.unsafe-url,.unknown{color:var(--danger);font-weight:700;overflow-wrap:anywhere;}
@media(max-width:60rem){.page-header,.summary-main{flex-direction:column;
 align-items:flex-start;}
 .page-tabs{width:100%;}
 .page-tabs a{flex:1;text-align:center;}
 dl,.evidence-meta{grid-template-columns:1fr;}}
@media print{body{background:var(--surface);}
 main{max-width:none;padding:0;}
 .page-tabs,.primary-action{display:none;}
 .evidence-card .hint{display:none;}
 section,.turn,.evidence-card{break-inside:avoid;}
 a{color:var(--text);}}
""" + decorative_hairline(".run-summary") + _tone_rules()
