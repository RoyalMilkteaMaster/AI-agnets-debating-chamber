"""One report contract, two renderings.

``build_report`` assembles the audited facts of a run into a single contract.
``render_markdown`` and ``render_html`` are pure functions of that contract, so
the two reports can never drift apart.

The renderer never writes a market conclusion. In a fake tracer run there is no
Core Agent, so the report says so plainly and shows only what the seats
themselves published: their stances, reasons, cited evidence and the arithmetic
vote tally.

**No colour is decided here.** Every value both offline pages are painted with
comes from :mod:`~hoya_market_agents.design_tokens` at render time, through
:func:`stylesheet`; a hex in this module would be a second copy of a colour that
table already owns (Spec R-004). The rule bodies below therefore name tokens and
never values, and the debate page reads the same two helpers rather than keeping
a palette of its own. A repaint reaches the pages a *new* run writes: files a
past run sealed are immutable and are not rebuilt.
"""

import html
from datetime import datetime, timedelta, timezone

from . import design_tokens
from .contract_validator import CONTRACT_VERSION, validate_report
from .question_package import (
    COMPARISON_STANCES,
    EVENT_STANCE_LABELS,
    MARKET_STANCE_LABELS,
    NO_CLEAR_DIFFERENCE_LABEL,
    OPEN_STANCE_LABELS,
)
from .report_contract import is_safe_source_url
from .seats import SEAT_DISPLAY_NAMES, SEAT_IDS, seat_display_names, seat_identities

REPORT_SCHEMA_VERSION = CONTRACT_VERSION

DIRECTION_LABELS = (
    ("support", "支持證據"),
    ("oppose", "反方證據"),
    ("neutral", "中性／限制條件證據"),
)

# 席位名稱不在本模組——它依 run 的資產類別從 roster profiles 取得（ADR 0006），
# 所以台股報告不會印出幣圈席名。這個名字只是 webapp 尚未改讀那個口的過渡出口，
# 由 Ticket 03 移除；它是同一份權威的檢視，不是第二份表。
SEAT_LABELS = SEAT_DISPLAY_NAMES

# The market ballot's own wording, kept as a name for callers that already know
# their run is a market question. Everything that renders a stance goes through
# ``stance_labels_for`` instead, because the question type is drawn live.
STANCE_LABELS = dict(MARKET_STANCE_LABELS)

# A stance string names exactly one question type's ballot, so the stances alone
# decide the vocabulary; only the comparison ballot also needs the asset names.
_FIXED_STANCE_LABELS = dict(MARKET_STANCE_LABELS)
_FIXED_STANCE_LABELS.update(EVENT_STANCE_LABELS)
_FIXED_STANCE_LABELS.update(OPEN_STANCE_LABELS)
_COMPARISON_FALLBACK_NAMES = ("前者", "後者")

CONSENSUS_LABELS = {
    "consensus": "達成共識",
    "no_consensus": "未達共識",
    "failed_insufficient_valid_votes": "有效票不足",
    "insufficient_data": "資料不足",
    "validation_failed": "驗證失敗",
    "in_progress": "進行中",
}

SOURCE_TIER_LABELS = {
    1: "第一級：官方或原始資料",
    2: "第二級：可信二手資料",
    3: "第三級：補充資訊",
}

CATEGORY_LABELS = {
    "spot-technical": "現貨價格與技術面",
    "derivatives": "衍生品與槓桿",
    "onchain": "鏈上與供給",
    "official-events": "官方事件與公告",
    "news": "新聞資訊",
    "social-macro": "社群與總體經濟",
    "counter-evidence": "反方證據與資料品質",
    "data-quality": "資料品質",
}

RAW_RECORDS = (
    ("evidence.jsonl", "證據快照"),
    ("debate.jsonl", "辯論紀錄"),
    ("votes.json", "票數紀錄"),
    ("manifest.json", "執行 manifest"),
)

# The graded 🔴／🟠／🟡／🟢／🔵 lights are the adopted stance's vote count
# (ADR 0003), which a tracer run has no real ballot for. A tracer run therefore
# reports an explicitly unassessed light rather than inventing one.
UNASSESSED_CONFIDENCE = {
    "icon": "⚪",
    "label": "未評估",
    "reason": "本次為模擬供應者追蹤執行；信心燈號門檻尚未實作，不得由示範票數推導市場信心。",
}

NO_CONCLUSION = {
    "available": False,
    "reason": (
        "本次執行未啟動核心代理人，控制程式不得自行撰寫市場結論。"
        "報告只呈現七席自己的公開立場、理由、引用證據與實際票數。"
    ),
}


def stance_labels_for(stances, assets=()):
    """Name each stance in its own ballot's Traditional Chinese wording.

    Mirrors ``question_package``'s wording, which stays the authority: the two
    approved comparison labels carry the asset names, every other approved
    ballot has fixed words, and an unknown stance keeps its raw value rather
    than losing the vote it stands for.
    """
    names = [value for value in assets if isinstance(value, str) and value.strip()]
    return {stance: _stance_wording(stance, names) for stance in stances}


def resolve_stance_labels(stances, assets=(), provided=None):
    """Prefer the ballot's recorded labels; derive them when they are incomplete."""
    stances = tuple(stances)
    if isinstance(provided, dict) and all(_is_label(provided.get(s)) for s in stances):
        return {stance: provided[stance] for stance in stances}
    return stance_labels_for(stances, assets)


def _stance_wording(stance, names):
    fixed = _FIXED_STANCE_LABELS.get(stance)
    if fixed is not None:
        return fixed
    if stance not in COMPARISON_STANCES:
        return str(stance)
    if stance == COMPARISON_STANCES[-1]:
        return NO_CLEAR_DIFFERENCE_LABEL
    index = COMPARISON_STANCES.index(stance)
    name = names[index] if len(names) > index else _COMPARISON_FALLBACK_NAMES[index]
    return "{}較優".format(name)


def _is_label(value):
    return isinstance(value, str) and bool(value.strip())


def build_report(
    run_id,
    question,
    assets,
    period_days,
    period_stated,
    provider_mode,
    started_at_utc,
    generated_at_utc,
    evidence,
    debate,
    votes,
    tally,
    roster,
    scope_limits,
):
    """Assemble the single report contract both renderers consume."""
    if len(votes) != len(SEAT_IDS):
        raise ValueError(
            "報告需要 {} 席的票，實際收到 {} 張。".format(len(SEAT_IDS), len(votes))
        )

    focus_by_seat = {seat.seat_id: seat.focus for seat in roster}
    turns_by_seat = {turn["seat_id"]: turn for turn in debate}

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "question": question,
        "assets": list(assets),
        "period_days": period_days,
        "period_stated": period_stated,
        "provider_mode": provider_mode,
        "started_at_utc": started_at_utc,
        "generated_at_utc": generated_at_utc,
        "confidence": dict(UNASSESSED_CONFIDENCE),
        "conclusion": dict(NO_CONCLUSION),
        "seat_count": len(votes),
        "tally": dict(tally),
        "seats": [_seat_view(vote, focus_by_seat, turns_by_seat) for vote in votes],
        "evidence": [_evidence_view(card) for card in evidence],
        "debate": [_debate_view(turn) for turn in debate],
        "scope_limits": list(scope_limits),
        "raw_records": [{"file": name, "label": label} for name, label in RAW_RECORDS],
    }
    return validate_report(report)


def render_markdown(report):
    """Render the report contract as Markdown."""
    lines = [
        "# Hoya Bit 市場研究報告",
        "",
        "> ⚠️ provider mode：{}。本報告內容為離線示範資料，不得作為市場依據。".format(
            report["provider_mode"]
        ),
        "",
        "## 摘要",
        "",
    ]
    lines += ["- {}：{}".format(term, value) for term, value in _headline_rows(report)]
    lines += ["", "## 票數", ""]
    lines += ["- {}".format(entry) for entry in _tally_entries(report)]
    lines += ["", "## 七席立場", ""]
    for seat in report["seats"]:
        lines += [
            "### {}".format(seat["seat_id"]),
            "",
            "- 專責範圍：{}".format(seat["focus"]),
            "- 最終立場：{}".format(seat["stance"]),
            "- 公開理由：{}".format(seat["public_reason"]),
            "- 引用證據：{}".format(", ".join(seat["evidence_ids"]) or "無"),
            "- 回應對象：{}".format(", ".join(seat["responds_to"]) or "無"),
            "- 改票原因：{}".format(seat["stance_change_reason"] or "未改票"),
            "",
        ]

    for direction, label in DIRECTION_LABELS:
        cards = [card for card in report["evidence"] if card["direction"] == direction]
        lines += ["## {}".format(label), ""]
        if not cards:
            lines += ["- 無", ""]
            continue
        for card in cards:
            lines.append(
                "- `{evidence_id}`（{seat_id}／來源等級 {source_tier}）{statement} "
                "原文：{excerpt} 來源：{source_url} 發布：{published_at_utc} "
                "取得：{retrieved_at_utc} 可信度：{credibility_note}".format(**card)
            )
        lines.append("")

    lines += ["## 辯論紀錄", ""]
    for turn in report["debate"]:
        lines.append(
            "- 第 {round} 輪 `{turn_id}`（{seat_id}）立場 {stance}：{public_reason} "
            "引用 {evidence} 回應 {responds}".format(
                evidence=", ".join(turn["evidence_ids"]) or "無",
                responds=", ".join(turn["responds_to"]) or "無",
                **{k: v for k, v in turn.items() if k not in ("evidence_ids", "responds_to")},
            )
        )

    lines += ["", "## 限制與失效條件", ""]
    lines += ["- {}".format(limit) for limit in report["scope_limits"]]
    lines += ["", "## 原始稽核檔案", ""]
    lines += [
        "- [{label}]({file})".format(**record) for record in report["raw_records"]
    ]
    lines.append("")
    return "\n".join(lines)


def render_html(report):
    """Render the report contract as one self-contained, offline HTML file."""
    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-Hant">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>{} — Hoya Bit 市場研究報告</title>".format(_e(report["run_id"])),
        "<style>{}</style>".format(stylesheet(_CSS)),
        "</head>",
        "<body>",
        '<p class="banner">⚠️ provider mode：{}。本報告內容為離線示範資料，不得作為市場依據。</p>'.format(
            _e(report["provider_mode"])
        ),
        "<h1>Hoya Bit 市場研究報告</h1>",
        '<p><a class="button" href="debate.html">查看完整辯論與證據</a></p>',
        '<section class="headline">',
        "<dl>",
    ]
    for term, value in _headline_rows(report):
        parts += ["<dt>{}</dt>".format(_e(term)), "<dd>{}</dd>".format(_e(value))]
    parts += ["</dl>", "</section>"]

    parts += ["<h2>票數</h2>", "<ul>"]
    parts += ["<li>{}</li>".format(_e(entry)) for entry in _tally_entries(report)]
    parts += ["</ul>"]

    parts += ["<h2>七席立場</h2>"]
    for seat in report["seats"]:
        parts += [
            "<article>",
            "<h3>{}</h3>".format(_e(seat["seat_id"])),
            "<ul>",
            "<li>專責範圍：{}</li>".format(_e(seat["focus"])),
            "<li>最終立場：{}</li>".format(_e(seat["stance"])),
            "<li>公開理由：{}</li>".format(_e(seat["public_reason"])),
            "<li>引用證據：{}</li>".format(_e(", ".join(seat["evidence_ids"]) or "無")),
            "<li>回應對象：{}</li>".format(_e(", ".join(seat["responds_to"]) or "無")),
            "<li>改票原因：{}</li>".format(_e(seat["stance_change_reason"] or "未改票")),
            "</ul>",
            "</article>",
        ]

    for direction, label in DIRECTION_LABELS:
        cards = [card for card in report["evidence"] if card["direction"] == direction]
        parts += ["<h2>{}</h2>".format(_e(label))]
        if not cards:
            parts += ["<p>無</p>"]
            continue
        parts += ["<ul>"]
        for card in cards:
            parts.append(
                "<li><code>{evidence_id}</code>（{seat_id}／來源等級 {source_tier}）{statement}"
                "<br>原文：{excerpt}"
                '<br>來源：<a href="{source_url}">{source_url}</a>'
                "<br>發布：{published_at_utc}｜取得：{retrieved_at_utc}"
                "<br>可信度：{credibility_note}</li>".format(
                    **{key: _e(value) for key, value in card.items()}
                )
            )
        parts += ["</ul>"]

    parts += ["<h2>辯論紀錄</h2>", "<ul>"]
    for turn in report["debate"]:
        parts.append(
            "<li>第 {round} 輪 <code>{turn_id}</code>（{seat_id}）立場 {stance}："
            "{public_reason}<br>引用 {evidence}｜回應 {responds}</li>".format(
                evidence=_e(", ".join(turn["evidence_ids"]) or "無"),
                responds=_e(", ".join(turn["responds_to"]) or "無"),
                **{
                    key: _e(value)
                    for key, value in turn.items()
                    if key not in ("evidence_ids", "responds_to")
                },
            )
        )
    parts += ["</ul>"]

    parts += ["<h2>限制與失效條件</h2>", "<ul>"]
    parts += ["<li>{}</li>".format(_e(limit)) for limit in report["scope_limits"]]
    parts += ["</ul>"]

    parts += ["<h2>原始稽核檔案</h2>", "<ul>"]
    parts += [
        '<li><a href="{file}">{label}</a></li>'.format(
            file=_e(record["file"]), label=_e(record["label"])
        )
        for record in report["raw_records"]
    ]
    parts += ["</ul>", "</body>", "</html>", ""]
    return "\n".join(parts)


# -- styling ----------------------------------------------------------------
#
# Two rules govern everything below and in :mod:`~.report_audit_renderer`:
#
# * **A colour is always a token.** ``var(--x)`` and nothing else, so the sheet
#   holds no value that :mod:`~hoya_market_agents.design_tokens` does not own.
# * **A length is a token when it is rhythm** — padding, gaps, margins, radii,
#   type — and stays a literal when it is one component's own dimension (a
#   reading measure, a grid column's minimum, a hairline's width). A scale that
#   also holds one panel's width is not a scale.


def stylesheet(rules):
    """Return one offline page's stylesheet: the token table, then the rules.

    The table is read here at render time rather than baked into the rule text,
    so a colour changed in :mod:`~hoya_market_agents.design_tokens` reaches the
    pages the next run writes and nothing else has to be edited. Both offline
    renderers call this, which is what "the CSS is taken from design_tokens"
    means when it is checked rather than claimed.

    One ``:root`` and no media query: dark mode is retired (Spec R-004), so
    there is a single palette and the page is white whatever the operating
    system prefers.
    """
    return (
        ":root{"
        + _custom_properties(design_tokens.PALETTE)
        + _custom_properties(design_tokens.SCALE)
        + "}"
        + rules
    )


def _custom_properties(values):
    return "".join(
        "--{}:{};".format(name.replace("_", "-"), value)
        for name, value in sorted(values.items())
    )


def decorative_hairline(selector):
    """Return the rule that lays the four brand hues along ``selector``'s top edge.

    R-004 asks for 紅藍綠黃 as decoration and for them to say nothing, so the one
    place they appear is a hairline above a panel: no text sits on it, no reader
    has to decode it, and a browser that cannot paint the gradient simply shows
    the panel without it. The stops are generated from
    :data:`~hoya_market_agents.design_tokens.DECOR_TOKENS`, so a fifth decorative
    hue widens the strip instead of being silently left out of it.
    """
    hues = design_tokens.DECOR_TOKENS
    stops = []
    for index, token in enumerate(hues):
        colour = "var(--{})".format(token.replace("_", "-"))
        stops.append("{} {}%".format(colour, index * 100 // len(hues)))
        stops.append("{} {}%".format(colour, (index + 1) * 100 // len(hues)))
    return (
        selector
        + "{background-image:linear-gradient(90deg,"
        + ",".join(stops)
        + ");background-repeat:no-repeat;background-size:100% 4px;"
        + "background-position:top;}"
    )


# The tracer report's rules. It is one plain document rather than a card layout,
# so the frosted panel is the headline block and the four hues sit above it.
_CSS = """
*{box-sizing:border-box;}
body{margin:0 auto;max-width:52rem;padding:var(--space-6);background:var(--page);
 color:var(--text);font-family:var(--font-sans);font-size:var(--size-md);
 line-height:var(--line-base);}
h1{font-size:var(--size-xl);line-height:var(--line-tight);}
h2{font-size:var(--size-lg);line-height:var(--line-tight);padding-bottom:var(--space-2);
 border-bottom:1px solid var(--border);}
h3{font-size:var(--size-md);line-height:var(--line-tight);margin-bottom:var(--space-2);}
a{color:var(--link);overflow-wrap:anywhere;}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px;}
code{font-family:var(--font-mono);font-size:var(--size-sm);background:var(--surface);
 padding:var(--space-1) var(--space-2);border-radius:var(--radius-sm);}
.banner{border:1px solid var(--abstain);background:var(--surface);color:var(--abstain);
 padding:var(--space-4) var(--space-5);border-radius:var(--radius-md);font-weight:700;}
.button{display:inline-block;background:var(--accent);color:var(--accent-text);
 text-decoration:none;font-weight:700;padding:var(--space-4) var(--space-5);
 border-radius:var(--radius-md);}
.headline{border:1px solid var(--border);border-radius:var(--radius-lg);
 padding:var(--space-6);background-color:var(--glass-surface);
 -webkit-backdrop-filter:blur(var(--glass-blur));backdrop-filter:blur(var(--glass-blur));}
dl{margin:0;display:grid;grid-template-columns:10rem 1fr;gap:var(--space-2) var(--space-5);}
dt{font-weight:700;color:var(--muted);}
dd{margin:0;}
article{background:var(--surface);border:1px solid var(--border);
 border-left:4px solid var(--border);border-radius:var(--radius-md);
 padding:var(--space-3) var(--space-5);margin-bottom:var(--space-5);}
@media print{body{max-width:none;padding:0;background:var(--surface);}
 a{color:var(--text);}}
""" + decorative_hairline(".headline")


def _headline_rows(report):
    """The first-screen facts, shared verbatim by both renderings."""
    return (
        ("執行識別碼", report["run_id"]),
        ("題目", report["question"]),
        ("分析資產", "、".join(report["assets"])),
        (
            "分析期間",
            "過去 {} 日（{}）".format(
                report["period_days"], "題目指定" if report["period_stated"] else "預設"
            ),
        ),
        ("開始時間", report["started_at_utc"]),
        ("產生時間", report["generated_at_utc"]),
        (
            "信心",
            "{} {}｜{}".format(
                report["confidence"]["icon"],
                report["confidence"]["label"],
                report["confidence"]["reason"],
            ),
        ),
        ("市場結論", report["conclusion"]["reason"]),
        ("票數", "／".join(_tally_entries(report))),
    )


def _tally_entries(report):
    return ["{}：{}".format(stance, count) for stance, count in report["tally"].items()]


def _seat_view(vote, focus_by_seat, turns_by_seat):
    turn = turns_by_seat.get(vote["seat_id"], {})
    return {
        "seat_id": vote["seat_id"],
        "focus": focus_by_seat.get(vote["seat_id"], ""),
        "attempt_id": vote["attempt_id"],
        "stance": vote["stance"],
        "public_reason": vote["public_reason"],
        "evidence_ids": list(vote["evidence_ids"]),
        "responds_to": list(turn.get("responds_to", [])),
        "last_round": turn.get("round", vote["round"]),
        "stance_change_reason": vote["stance_change_reason"],
    }


def _evidence_view(card):
    return {
        key: card[key]
        for key in (
            "evidence_id",
            "seat_id",
            "asset",
            "category",
            "statement",
            "direction",
            "source_url",
            "source_origin",
            "source_tier",
            "published_at_utc",
            "retrieved_at_utc",
            "excerpt",
            "credibility_note",
        )
    }


def _debate_view(turn):
    return {
        key: turn[key]
        for key in (
            "turn_id",
            "seat_id",
            "round",
            "stance",
            "public_reason",
            "evidence_ids",
            "responds_to",
            "stance_change_reason",
        )
    }


def _e(value):
    return html.escape("" if value is None else str(value), quote=True)


def render_market_markdown(report):
    """Render one already-validated, Core-authored report as Markdown."""
    labels = _report_stance_labels(report)
    seat_labels = _report_seat_labels(report)
    lines = [
        "# Hoya Bit 市場判斷報告",
        "",
        "## 判斷摘要",
        "",
        "- 市場狀態：{}".format(report["market_status"]),
        "- 分析期間：{}".format(report["period"]["label"]),
        "- 信心：{} {}".format(
            report["confidence"]["icon"],
            report["confidence"]["text"],
        ),
        "- 票數：{}".format(_market_tally(report, labels)),
        "- 共識狀態：{}".format(_consensus_label(report["consensus_status"])),
        "- 判斷：{}".format(report["judgement"]),
        "",
        "### 失效條件",
        "",
    ]
    lines += ["- {}".format(item) for item in report["invalidation_conditions"]]
    lines += ["", "## 限制", ""]
    lines += ["- {}".format(item) for item in report["limitations"]]
    if report["validation_errors"]:
        lines += ["", "## 驗證錯誤", ""]
        lines += ["- {}".format(item) for item in report["validation_errors"]]

    lines += ["", "## 七席完整紀錄", ""]
    for seat in report["seats"]:
        lines += [
            "### {}（`{}`）".format(
                _seat_label(seat["seat_id"], seat_labels), seat["seat_id"]
            ),
            "",
            "- 初始立場：{}".format(_stance_label(seat["initial_stance"], labels)),
            "- 最終立場：{}".format(_stance_label(seat["final_stance"], labels)),
            "- 是否改票：{}".format("是" if seat["stance_changed"] else "否"),
            "- 初始理由：{}".format(seat["initial_public_reason"]),
            "- 最終理由：{}".format(seat["public_reason"]),
            "- 改票原因：{}".format(_display(seat["stance_change_reason"])),
            "- 未改票原因：{}".format(_display(seat["no_change_reason"])),
            "- 替補 attempts：{}".format(", ".join(seat["replacement_attempt_ids"]) or "無"),
            "- 支持 evidence：{}".format(", ".join(seat["support_evidence_ids"]) or "無"),
            "- 反方 evidence：{}".format(", ".join(seat["counter_evidence_ids"]) or "無"),
            "",
        ]

    lines += ["## 證據與來源", ""]
    for card in report["evidence"]:
        source = (
            "[{}]({})".format(card["url"], card["url"])
            if is_safe_source_url(card["url"])
            else "不安全來源網址（未建立連結）：{}".format(card["url"])
        )
        lines.append(
            "- `{}` {}｜{}｜{}".format(
                card["evidence_id"], source, _direction_label(card["direction"]), card["statement"]
            )
        )
    lines.append("")
    return "\n".join(lines)


def render_market_html(report, sources=None):
    """Render one validated report as a self-contained, accessible HTML file."""
    confidence = report["confidence"]
    labels = _report_stance_labels(report)
    seat_labels = _report_seat_labels(report)
    identities = _report_seat_identities(report)
    evidence_by_id = _market_evidence_index(report, sources)
    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-Hant">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Hoya Bit 市場判斷報告</title>",
        "<style>{}</style>".format(stylesheet(_MARKET_CSS)),
        "</head>",
        "<body>",
        "<main>",
        '<header class="page-header"><div><p class="eyebrow">Hoya Bit 可稽核市場研究</p>',
        "<h1>市場判斷報告</h1></div>",
        # 導覽只列這份 bundle 真的帶著的頁面。曾經有第三個 tab 指向
        # ``live.html``，但那個檔案從來不在 run 目錄裡：舊的直播頁走的是伺服器
        # URL，Ticket 10 退役 live_dashboard 之後連那條脈絡都沒有了，於是每一份
        # 離線報告都帶著一個永遠 404 的連結出貨。改成 ``/live`` 不算修好——那只
        # 是把死連結換成「沒開伺服器就壞掉」的連結，而離線 bundle 的契約正是它
        # 自己就能看。封存後也不缺那一頁：辯論逐字稿就是 debate.html。
        '<nav class="page-tabs" aria-label="主要頁面">'
        '<a href="report.html" aria-current="page">市場報告</a>'
        '<a href="debate.html">完整辯論</a></nav></header>',
        '<section class="decision" aria-labelledby="decision-title">',
        '<div class="decision-main"><div><p class="eyebrow">本次結論</p>',
        '<p class="decision-scope">{}｜{}</p>'.format(
            _e("／".join(report.get("assets") or []) or "市場"),
            _e(report["period"]["label"]),
        ),
        '<h2 class="market-status" id="decision-title">{}</h2>'.format(
            _e(report["market_status"])
        ),
        '<p class="judgement">{}</p></div>'.format(_e(report["judgement"])),
        '<a class="primary-action" href="debate.html">查看七席如何形成判斷</a></div>',
        '<div class="decision-facts">',
        '<span class="confidence {}" aria-label="信心 {} {}"><strong>{}</strong> {}</span>'.format(
            _e(confidence["level"]),
            _e(confidence["icon"]),
            _e(confidence["text"]),
            _e(confidence["icon"]),
            _e(confidence["text"]),
        ),
        '<span><strong>票數</strong> {}</span>'.format(_e(_market_tally(report, labels))),
        '<span><strong>共識</strong> {}</span>'.format(
            _e(_consensus_label(report["consensus_status"]))
        ),
        "</div>",
        "</section>",
        '<section class="evidence-compare" aria-labelledby="evidence-compare-title">',
        '<div class="section-heading"><div><p class="eyebrow">先看正反證據</p>',
        '<h2 id="evidence-compare-title">支持與反方證據</h2></div>',
        '<a class="text-link" href="#evidence-library">查看完整來源 →</a></div>',
        '<div class="evidence-columns">',
        '<article class="evidence-side support"><h3>支持證據</h3><ol>',
    ]
    support_cards = [card for card in evidence_by_id.values() if card.get("direction") == "support"]
    oppose_cards = [card for card in evidence_by_id.values() if card.get("direction") == "oppose"]
    parts += _comparison_items(support_cards, "本次沒有記錄支持證據。")
    parts += ["</ol></article>", '<article class="evidence-side oppose"><h3>反方證據</h3><ol>']
    parts += _comparison_items(oppose_cards, "本次沒有記錄反方證據。")
    parts += ["</ol></article></div></section>"]

    parts += ['<section class="risk-section"><div><h2>失效條件</h2><ul>']
    parts += ["<li>{}</li>".format(_e(item)) for item in report["invalidation_conditions"]]
    parts += ["</ul></div><div><h2>限制</h2><ul>"]
    parts += ["<li>{}</li>".format(_e(item)) for item in report["limitations"]]
    parts += ["</ul></div></section>", "<!--first-screen-end-->"]

    if report["validation_errors"]:
        parts += ["<section><h2>驗證錯誤</h2><ul>"]
        parts += ["<li>{}</li>".format(_e(item)) for item in report["validation_errors"]]
        parts += ["</ul></section>"]

    parts += [
        "<section>",
        '<div class="section-heading"><div><p class="eyebrow">七個獨立研究角度</p>',
        "<h2>各席判斷與可驗證依據</h2></div>",
        '<a class="text-link" href="debate.html">前往完整辯論室 →</a></div>',
        '<p class="section-intro">先讀每席最終理由；需要查證時，再展開其引用來源。</p>',
        '<div class="seat-grid">',
    ]
    for seat in report["seats"]:
        change_text = (
            "是：{}".format(_display(seat["stance_change_reason"]))
            if seat["stance_changed"]
            else "否：{}".format(_display(seat["no_change_reason"]))
        )
        lineage = ", ".join(seat["replacement_attempt_ids"]) or "無替補"
        cited_ids = seat["support_evidence_ids"] + seat["counter_evidence_ids"]
        evidence_items = []
        for evidence_id in cited_ids:
            card = evidence_by_id.get(evidence_id, {})
            evidence_items.append(_seat_evidence_html(evidence_id, card))
        evidence_html = "".join(evidence_items) or "<li>沒有可驗證證據</li>"
        identity = identities.get(seat["seat_id"])
        agent_name = (
            identity.display_name
            if identity
            else _seat_label(seat["seat_id"], seat_labels)
        )
        avatar = identity.avatar if identity else "❔"
        parts += [
            '<article class="seat-card">',
            '<header class="seat-head"><span class="seat-avatar" aria-hidden="true">{}</span>'
            '<div><h3>{}</h3><p>{}｜<code>{}</code></p></div></header>'.format(
                _e(avatar),
                _e(agent_name),
                _e(_seat_label(seat["seat_id"], seat_labels)),
                _e(seat["seat_id"]),
            ),
            '<p class="stance-line"><span>初始 {}</span><span aria-hidden="true">→</span>'
            '<strong>最終 {}</strong></p>'.format(
                _e(_stance_label(seat["initial_stance"], labels)),
                _e(_stance_label(seat["final_stance"], labels)),
            ),
            '<div class="seat-reason"><h4>最終判斷理由</h4><p>{}</p></div>'.format(
                _e(seat["public_reason"])
            ),
            '<p class="seat-process"><strong>是否改票：</strong>{}<br>'
            '<strong>替補紀錄：</strong>{}</p>'.format(_e(change_text), _e(lineage)),
            '<details class="seat-sources"><summary>查看 {} 項可驗證依據</summary>'
            '<ul class="seat-evidence">{}</ul>'
            '<p><strong>初始判斷：</strong>{}</p></details>'.format(
                len(cited_ids), evidence_html, _e(seat["initial_public_reason"])
            ),
            "</article>",
        ]
    parts += ["</div></section>"]

    parts += [
        '<section id="evidence-library"><div class="section-heading"><div>',
        '<p class="eyebrow">證據資料庫</p><h2>完整證據與來源</h2></div>',
        '<a class="text-link" href="debate.html">查看證據如何被辯論 →</a></div>',
        '<div class="evidence-grid">',
    ]
    for evidence_id, card in evidence_by_id.items():
        url = card.get("source_url") or card.get("url") or ""
        source = (
            '<a class="source-link" href="{}">開啟原始來源</a>'.format(_e(url))
            if is_safe_source_url(url)
            else '<span class="unsafe-url">來源網址無法安全開啟：{}</span>'.format(
                _e(url or "未提供")
            )
        )
        parts.append(
            '<details class="evidence-card" id="evidence-{}"><summary>'
            '<span><code>{}</code><strong>{}</strong></span><span class="tier">{}</span></summary>'
            '<div class="evidence-body"><dl class="evidence-meta">'
            '<dt>研究席</dt><dd>{}</dd><dt>資料分類</dt><dd>{}</dd>'
            '<dt>證據方向</dt><dd>{}</dd><dt>來源網站</dt><dd>{}</dd>'
            '<dt>發布時間</dt><dd>{}</dd><dt>取得時間</dt><dd>{}</dd></dl>'
            '<p><strong>原文或關鍵數值：</strong>{}</p>'
            '<p><strong>可信度與限制：</strong>{}</p>{}</div></details>'.format(
                _e(evidence_id),
                _e(evidence_id),
                _e(card.get("statement") or "未提供證據摘要"),
                _e(_source_tier_label(card.get("source_tier"))),
                _e(_seat_label(card.get("seat_id"), seat_labels)),
                _e(_category_label(card.get("category"))),
                _e(_direction_label(card.get("direction"))),
                _e(card.get("source_origin") or "未提供"),
                _time_html(card.get("published_at_utc")),
                _time_html(card.get("retrieved_at_utc")),
                _e(card.get("excerpt") or "未提供"),
                _e(card.get("credibility_note") or "未提供"),
                source,
            )
        )
    parts += ["</div></section>", "</main>", "</body>", "</html>", ""]
    return "\n".join(parts)


# The market report's rules. The conclusion panel is the page's one frosted
# surface and carries the four-hue hairline; everything else is white cards on
# the grey canvas, told apart by hairlines and space rather than by fills.
#
# **The five confidence lights hold no rule here.** Their colour is the
# authority's own icon beside the word (``report_contract.CONFIDENCE_ICONS``),
# which is why ``design_tokens`` deliberately carries no hex for them: a
# ``.green{color:...}`` in this sheet would be a second copy of a colour that
# already has an owner. The level's class stays on the element for assistive
# technology and for anything downstream that reads it; what is gone is the
# paint, not the light.
_MARKET_CSS = """
*{box-sizing:border-box;}
body{margin:0;background:var(--page);color:var(--text);font-family:var(--font-sans);
 font-size:var(--size-md);line-height:var(--line-base);}
main{max-width:var(--shell);margin:auto;padding:var(--space-6);}
h1,h2,h3,h4{line-height:var(--line-tight);}
h1{font-size:var(--size-xl);margin:var(--space-1) 0;}
h2{margin:var(--space-1) 0 var(--space-4);font-size:var(--size-lg);}
a{color:var(--link);overflow-wrap:anywhere;}
code{font-family:var(--font-mono);font-size:var(--size-sm);}
.page-header,.section-heading{display:flex;justify-content:space-between;
 align-items:center;gap:var(--space-5);}
.page-header{margin:0 0 var(--space-5);}
.eyebrow{margin:0;color:var(--muted);font-size:var(--size-2xs);font-weight:700;
 letter-spacing:.08em;text-transform:uppercase;}
.page-tabs{display:flex;gap:var(--space-1);padding:var(--space-1);
 background-color:var(--glass-surface);border:1px solid var(--border);
 border-radius:var(--radius-pill);
 -webkit-backdrop-filter:blur(var(--glass-blur));backdrop-filter:blur(var(--glass-blur));}
.page-tabs a{color:var(--link);text-decoration:none;font-weight:700;
 padding:var(--space-3) var(--space-4);border-radius:var(--radius-pill);white-space:nowrap;}
.page-tabs a[aria-current=page]{background:var(--accent);color:var(--accent-text);}
a:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:2px;}
.text-link{color:var(--link);font-weight:700;text-decoration:none;}
.primary-action{flex:none;background:var(--accent);color:var(--accent-text);
 font-weight:700;text-decoration:none;padding:var(--space-4) var(--space-5);
 border-radius:var(--radius-md);}
section{background:var(--surface);border:1px solid var(--border);
 border-radius:var(--radius-lg);padding:var(--space-6);margin:0 0 var(--space-5);}
.decision{padding-top:var(--space-7);background-color:var(--glass-surface);
 -webkit-backdrop-filter:blur(var(--glass-blur));backdrop-filter:blur(var(--glass-blur));}
.decision .eyebrow{color:var(--accent);}
.decision-main{display:flex;justify-content:space-between;align-items:center;
 gap:var(--space-6);}
.decision-scope{margin:var(--space-1) 0;color:var(--muted);font-weight:700;}
.market-status{margin:var(--space-1) 0 var(--space-3);font-size:var(--size-2xl);}
.judgement{margin:0;max-width:58rem;font-size:var(--size-lg);}
.decision-facts{display:flex;gap:var(--space-3);flex-wrap:wrap;margin-top:var(--space-5);}
.decision-facts span{background:var(--surface);border:1px solid var(--border);
 border-radius:var(--radius-pill);padding:var(--space-2) var(--space-4);}
.confidence{font-weight:700;}
dl{display:grid;grid-template-columns:9rem 1fr;gap:var(--space-2) var(--space-5);}
dt{font-weight:700;color:var(--muted);}
dd{margin:0;}
.evidence-columns{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-5);}
.evidence-side{background:var(--page);border:1px solid var(--border);
 border-radius:var(--radius-md);padding:var(--space-5);}
.evidence-side h3{margin:0 0 var(--space-4);font-size:var(--size-md);}
.evidence-side ol{margin:0;padding-left:var(--space-6);}
.evidence-side li{padding:var(--space-2) var(--space-1);}
.evidence-side.support{border-top:4px solid var(--affirm);}
.evidence-side.oppose{border-top:4px solid var(--oppose);}
.comparison-meta{display:block;color:var(--muted);font-size:var(--size-2xs);}
.risk-section{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-6);}
.risk-section h2{font-size:var(--size-lg);}
.section-intro{margin-top:0;color:var(--muted);}
.seat-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--space-5);}
.seat-card{background:var(--surface);border:1px solid var(--border);
 border-radius:var(--radius-md);padding:var(--space-5);}
.seat-head{display:flex;align-items:center;gap:var(--space-4);}
.seat-avatar{width:2.7rem;height:2.7rem;display:grid;place-items:center;
 border-radius:50%;background:var(--page);border:1px solid var(--border);
 font-size:var(--size-lg);}
.seat-head h3{margin:0;font-size:var(--size-md);}
.seat-head p{margin:0;color:var(--muted);font-size:var(--size-xs);}
.stance-line{display:flex;gap:var(--space-3);align-items:center;
 margin:var(--space-4) 0;padding:var(--space-2) var(--space-4);
 background:var(--page);border-radius:var(--radius-sm);}
.stance-line strong{color:var(--text);}
.seat-reason h4{margin:var(--space-3) 0 var(--space-1);color:var(--muted);
 font-size:var(--size-2xs);}
.seat-reason p,.seat-process{margin:var(--space-1) 0 var(--space-4);}
.seat-process{color:var(--muted);font-size:var(--size-sm);}
.seat-sources{border-top:1px solid var(--border);padding-top:var(--space-3);}
.seat-sources summary{cursor:pointer;color:var(--link);font-weight:700;}
.seat-evidence{list-style:none;padding:0;margin:0;}
.seat-evidence li{padding:var(--space-2) 0;border-bottom:1px dashed var(--border);}
.seat-evidence li:last-child{border:0;}
.evidence-summary{display:block;margin-top:var(--space-1);}
.meta{display:block;color:var(--muted);font-size:var(--size-2xs);}
.evidence-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(19rem,1fr));
 gap:var(--space-4);}
.evidence-card{background:var(--surface);border:1px solid var(--border);
 border-radius:var(--radius-md);align-self:start;}
.evidence-card summary{cursor:pointer;padding:var(--space-4);display:flex;
 justify-content:space-between;gap:var(--space-3);}
.evidence-card summary span:first-child{display:flex;flex-direction:column;}
.evidence-card summary strong{font-size:var(--size-sm);}
.evidence-body{padding:0 var(--space-4) var(--space-4);}
.tier{color:var(--accent);font-size:var(--size-2xs);font-weight:700;}
.evidence-meta{grid-template-columns:5.5rem 1fr;font-size:var(--size-sm);}
.source-link{display:inline-block;margin-top:var(--space-1);}
.unsafe-url{color:var(--danger);font-weight:700;overflow-wrap:anywhere;}
@media(max-width:60rem){.page-header,.section-heading{align-items:flex-start;
 flex-direction:column;}
 .page-tabs{width:100%;}
 .page-tabs a{flex:1;text-align:center;}
 dl{grid-template-columns:1fr;}
 .decision-main{align-items:flex-start;flex-direction:column;}
 .evidence-columns,.risk-section,.seat-grid{grid-template-columns:1fr;}}
@media print{body{background:var(--surface);}
 main{max-width:none;padding:0;}
 .page-tabs,.text-link,.primary-action{display:none;}
 section{break-inside:auto;}
 .evidence-card,.seat-card{break-inside:avoid;}
 a{color:var(--text);}}
""" + decorative_hairline(".decision")


def _market_tally(report, labels):
    return "／".join(
        "{}：{}".format(_stance_label(stance, labels), count)
        for stance, count in report["tally"].items()
    )


def _report_stance_labels(report):
    """Read this run's ballot off the report itself: tally, seats and adoption."""
    tally = report.get("tally")
    stances = list(tally) if isinstance(tally, dict) else []
    seats = report.get("seats") if isinstance(report.get("seats"), list) else []
    candidates = [report.get("adopted_stance")]
    candidates += [
        seat.get(field)
        for seat in seats
        if isinstance(seat, dict)
        for field in ("initial_stance", "final_stance")
    ]
    for stance in candidates:
        if isinstance(stance, str) and stance and stance not in stances:
            stances.append(stance)
    return stance_labels_for(stances, report.get("assets") or ())


def _report_seat_labels(report):
    """The seat names this run is shown under, read off the report's own market.

    The asset class picks the profile set (``seats.profile_set_for``); a report
    that does not name one reads the open set, exactly as any other caller
    without an asset class does. This renderer spells no seat name of its own.
    """
    return seat_display_names(report.get("asset_class"))


def _report_seat_identities(report):
    """The same seats' bylines — provider family plus the name above."""
    return seat_identities(report.get("asset_class"))


def _comparison_items(cards, empty_text):
    if not cards:
        return ['<li class="empty">{}</li>'.format(_e(empty_text))]
    return [
        '<li><strong>{}</strong><span class="comparison-meta">{}｜{}</span>'
        '<a href="#evidence-{}">查看原始來源與可信度</a></li>'.format(
            _e(card.get("statement") or "證據摘要未提供"),
            _e(_source_tier_label(card.get("source_tier"))),
            _e(card.get("source_origin") or "來源網站未提供"),
            _e(card.get("evidence_id")),
        )
        for card in cards
    ]


def _market_evidence_index(report, sources):
    if isinstance(sources, dict) and isinstance(sources.get("evidence"), list):
        return {
            card.get("evidence_id"): dict(card)
            for card in sources["evidence"]
            if isinstance(card, dict) and card.get("evidence_id")
        }
    return {
        card.get("evidence_id"): {
            "evidence_id": card.get("evidence_id"),
            "source_url": card.get("url"),
            "statement": card.get("statement"),
            "direction": card.get("direction"),
        }
        for card in report.get("evidence", [])
        if isinstance(card, dict) and card.get("evidence_id")
    }


def _seat_evidence_html(evidence_id, card):
    return (
        '<li><a href="#evidence-{}"><code>{}</code></a>'
        '<span class="evidence-summary">{}</span><span class="meta">{}｜{}｜發布 {}</span></li>'
    ).format(
        _e(evidence_id),
        _e(evidence_id),
        _e(card.get("statement") or "證據摘要未提供"),
        _e(_source_tier_label(card.get("source_tier"))),
        _e(card.get("source_origin") or "來源網站未提供"),
        _time_html(card.get("published_at_utc")),
    )


def _seat_label(value, labels):
    if value is None or value == "":
        return "未提供"
    return labels.get(value, str(value))


def _stance_label(value, labels):
    if value is None or value == "":
        return "未取得有效立場"
    return labels.get(value, str(value))


def _consensus_label(value):
    return CONSENSUS_LABELS.get(value, str(value))


def _source_tier_label(value):
    return SOURCE_TIER_LABELS.get(value, "來源等級未提供")


def _category_label(value):
    if value is None or value == "":
        return "未提供"
    return CATEGORY_LABELS.get(value, str(value))


def _direction_label(value):
    return {
        "support": "支持目前判斷",
        "oppose": "反對目前判斷",
        "neutral": "中性或限制條件",
    }.get(value, "未提供")


def _time_html(value):
    if not isinstance(value, str) or not value:
        return "未提供"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _e(value)
    taipei = parsed.astimezone(timezone(timedelta(hours=8)))
    label = taipei.strftime("%Y/%m/%d %H:%M")
    return '<time datetime="{}">{}（台北時間）</time>'.format(_e(value), _e(label))


def _display(value):
    return "無" if value is None or value == "" else str(value)
