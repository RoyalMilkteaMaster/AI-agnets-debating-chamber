"""One report contract, two renderings.

``build_report`` assembles the audited facts of a run into a single contract.
``render_markdown`` and ``render_html`` are pure functions of that contract, so
the two reports can never drift apart.

The renderer never writes a market conclusion. In a fake tracer run there is no
Core Agent, so the report says so plainly and shows only what the seats
themselves published: their stances, reasons, cited evidence and the arithmetic
vote tally.
"""

import html
from datetime import datetime, timedelta, timezone

from .contract_validator import CONTRACT_VERSION, validate_report
from .question_package import (
    COMPARISON_STANCES,
    EVENT_STANCE_LABELS,
    MARKET_STANCE_LABELS,
    NO_CLEAR_DIFFERENCE_LABEL,
    OPEN_STANCE_LABELS,
)
from .report_contract import is_safe_source_url
from .seats import SEAT_IDS

REPORT_SCHEMA_VERSION = CONTRACT_VERSION

DIRECTION_LABELS = (
    ("support", "支持證據"),
    ("oppose", "反方證據"),
    ("neutral", "中性／限制條件證據"),
)

SEAT_LABELS = {
    "spot-technical": "現貨技術席",
    "derivatives": "衍生品席",
    "onchain": "鏈上資料席",
    "official-events": "官方事件席",
    "news": "新聞資訊席",
    "social-macro": "社群與總體經濟席",
    "counter-evidence": "反方證據席",
}

REPORT_AGENT_PROFILES = {
    "spot-technical": ("Codex・圖表偵探", "📈"),
    "derivatives": ("Codex・槓桿雷達", "⚙️"),
    "onchain": ("Codex・鏈上獵人", "⛓️"),
    "official-events": ("Claude・官方哨兵", "📣"),
    "news": ("Claude・新聞探員", "📰"),
    "social-macro": ("Claude・社群觀察員", "🌐"),
    "counter-evidence": ("Gemini・反證稽核員", "🔎"),
}

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

# The graded 🔴／🟠／🟡／🟡🟢／🟢 lights are bounded by the absolute 6/5/4 vote
# thresholds, which this ticket does not implement. A tracer run therefore
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
        "<style>{}</style>".format(_CSS),
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


_CSS = (
    "body{font-family:system-ui,'Noto Sans TC',sans-serif;margin:0 auto;max-width:52rem;"
    "padding:1.5rem;line-height:1.6;color:#16202a;background:#ffffff}"
    ".banner{border:2px solid #b34700;background:#fff4e5;color:#7a3000;"
    "padding:.75rem 1rem;border-radius:.5rem;font-weight:700}"
    ".headline{border:1px solid #c8d2dc;border-radius:.5rem;padding:1rem;background:#f6f9fc}"
    "dl{margin:0;display:grid;grid-template-columns:10rem 1fr;gap:.35rem 1rem}"
    "dt{font-weight:700}dd{margin:0}"
    "h1{font-size:1.6rem}h2{font-size:1.2rem;border-bottom:1px solid #c8d2dc;padding-bottom:.3rem}"
    "h3{font-size:1rem;margin-bottom:.3rem}"
    ".button{display:inline-block;background:#173b70;color:#fff;text-decoration:none;font-weight:800;padding:.6rem .9rem;border-radius:.45rem}"
    "article{border-left:4px solid #c8d2dc;padding-left:.9rem;margin-bottom:.9rem}"
    "code{background:#eef2f6;padding:.1rem .3rem;border-radius:.2rem}"
    "a{color:#0b4f9e}"
    "@media print{body{max-width:none;padding:0}.banner{border-width:1px}}"
)


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
            "### {}（`{}`）".format(_seat_label(seat["seat_id"]), seat["seat_id"]),
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
    evidence_by_id = _market_evidence_index(report, sources)
    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-Hant">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Hoya Bit 市場判斷報告</title>",
        "<style>{}</style>".format(_MARKET_CSS),
        "</head>",
        "<body>",
        "<main>",
        '<header class="page-header"><div><p class="eyebrow">Hoya Bit 可稽核市場研究</p>',
        "<h1>市場判斷報告</h1></div>",
        '<nav class="page-tabs" aria-label="主要頁面">'
        '<a href="live.html">即時辯論</a>'
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
        agent_name, avatar = REPORT_AGENT_PROFILES.get(
            seat["seat_id"], (_seat_label(seat["seat_id"]), "❔")
        )
        parts += [
            '<article class="seat-card">',
            '<header class="seat-head"><span class="seat-avatar" aria-hidden="true">{}</span>'
            '<div><h3>{}</h3><p>{}｜<code>{}</code></p></div></header>'.format(
                _e(avatar),
                _e(agent_name),
                _e(_seat_label(seat["seat_id"])),
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
                _e(_seat_label(card.get("seat_id"))),
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


_MARKET_CSS = (
    ":root{--ink:#172033;--muted:#5d687b;--line:#d8dee9;--paper:#fff;--wash:#f3f6fb;"
    "--brand:#173b70;--brand-2:#2d5f9d;--brand-soft:#eaf1fb;--positive:#17633b;--negative:#9a2f2f}"
    "*{box-sizing:border-box}body{margin:0;background:var(--wash);color:var(--ink);"
    "font-family:system-ui,'Noto Sans TC',sans-serif;line-height:1.6}main{max-width:90rem;margin:auto;padding:1.5rem}"
    ".page-header,.section-heading{display:flex;justify-content:space-between;align-items:center;gap:1rem}"
    ".page-header{margin:0 0 1rem}.eyebrow{margin:0;color:var(--brand);font-size:.78rem;font-weight:800;"
    "letter-spacing:.08em;text-transform:uppercase}h1{font-size:2rem;margin:.1rem 0}h2{margin:.2rem 0 .8rem}"
    ".page-tabs{display:flex;gap:.25rem;padding:.3rem;background:var(--brand-soft);border:1px solid var(--line);"
    "border-radius:.65rem}.page-tabs a{color:var(--brand);text-decoration:none;font-weight:800;"
    "padding:.55rem .75rem;border-radius:.4rem;white-space:nowrap}.page-tabs a[aria-current=page]{"
    "background:var(--brand);color:#fff}.page-tabs a:focus-visible,.primary-action:focus-visible,summary:focus-visible{outline:3px solid #f0b429;outline-offset:2px}"
    ".text-link{color:var(--brand);font-weight:700;text-decoration:none}.primary-action{flex:none;background:#fff;color:var(--brand);"
    "font-weight:850;text-decoration:none;padding:.75rem 1rem;border-radius:.55rem}"
    "section{background:var(--paper);border:1px solid var(--line);border-radius:.75rem;padding:1.1rem;margin:0 0 1rem;"
    "box-shadow:0 3px 14px rgba(23,32,51,.05)}.decision{padding:1.35rem;background:linear-gradient(120deg,var(--brand),var(--brand-2));color:#fff}"
    ".decision .eyebrow{color:#cbdcf2}.decision-main{display:flex;justify-content:space-between;align-items:center;gap:1.5rem}"
    ".decision-scope{margin:.2rem 0;opacity:.82;font-weight:750}.market-status{font-size:clamp(1.8rem,3vw,2.75rem);line-height:1.22;margin:.2rem 0 .45rem}"
    ".judgement{font-size:1.08rem;margin:0;max-width:58rem}.decision-facts{display:flex;gap:.65rem;flex-wrap:wrap;margin-top:1rem}"
    ".decision-facts span{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.22);border-radius:2rem;padding:.35rem .7rem}"
    "dl{display:grid;grid-template-columns:9rem 1fr;gap:.45rem 1rem}dt{font-weight:750}dd{margin:0}"
    ".confidence{font-weight:800}.red{color:#8f1414}.orange{color:#8a3b00}.yellow{color:#6b5700}"
    ".yellow_green{color:#315b10}.green{color:#12602e}.decision .confidence{color:#fff}"
    ".evidence-columns{display:grid;grid-template-columns:1fr 1fr;gap:1rem}.evidence-side{border:1px solid var(--line);border-radius:.65rem;padding:.9rem}"
    ".evidence-side h3{margin:.1rem 0 .6rem}.evidence-side ol{margin:0;padding-left:1.35rem}.evidence-side li{padding:.45rem .2rem}"
    ".evidence-side.support{border-top:4px solid var(--positive);background:#f6fbf8}.evidence-side.oppose{border-top:4px solid var(--negative);background:#fff8f8}"
    ".comparison-meta{display:block;color:var(--muted);font-size:.78rem}.risk-section{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem}"
    ".risk-section h2{font-size:1.15rem}.section-intro{color:var(--muted);margin-top:-.45rem}"
    ".seat-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.85rem}.seat-card{border:1px solid var(--line);border-radius:.7rem;padding:.9rem;background:#fbfcfe}"
    ".seat-head{display:flex;align-items:center;gap:.65rem}.seat-avatar{width:2.7rem;height:2.7rem;display:grid;place-items:center;border-radius:50%;background:var(--brand-soft);font-size:1.35rem}"
    ".seat-head h3{margin:0;font-size:1rem}.seat-head p{margin:0;color:var(--muted);font-size:.76rem}.stance-line{display:flex;gap:.5rem;align-items:center;margin:.7rem 0;padding:.45rem .6rem;border-radius:.45rem;background:var(--brand-soft)}"
    ".stance-line strong{color:var(--brand)}.seat-reason h4{margin:.5rem 0 .1rem;color:var(--muted);font-size:.78rem}.seat-reason p,.seat-process{margin:.15rem 0 .6rem}.seat-process{font-size:.84rem;color:var(--muted)}"
    ".seat-sources{border-top:1px solid var(--line);padding-top:.55rem}.seat-sources summary{cursor:pointer;color:var(--brand);font-weight:800}"
    ".seat-evidence{list-style:none;padding:0;margin:0}.seat-evidence li{padding:.45rem 0;border-bottom:1px dashed var(--line)}"
    ".seat-evidence li:last-child{border:0}.evidence-summary{display:block;margin-top:.2rem}.meta{display:block;color:var(--muted);"
    "font-size:.78rem}.evidence-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(19rem,1fr));gap:.8rem}"
    ".evidence-card{border:1px solid var(--line);border-radius:.55rem;background:#fbfcfe;align-self:start}.evidence-card summary{cursor:pointer;padding:.8rem;display:flex;justify-content:space-between;gap:.6rem}"
    ".evidence-card summary span:first-child{display:flex;flex-direction:column}.evidence-card summary strong{font-size:.9rem}.evidence-body{padding:0 .8rem .8rem}.tier{font-size:.75rem;"
    "font-weight:750;color:var(--brand)}.evidence-meta{grid-template-columns:5.5rem 1fr;"
    "font-size:.83rem}.source-link{display:inline-block;margin-top:.3rem}a{color:#064f9e;overflow-wrap:anywhere}code{font-weight:700}"
    "@media(max-width:60rem){.page-header,.section-heading{align-items:flex-start;flex-direction:column}"
    ".page-tabs{width:100%}.page-tabs a{flex:1;text-align:center}dl{grid-template-columns:1fr}.decision-main{align-items:flex-start;flex-direction:column}"
    ".evidence-columns,.risk-section,.seat-grid{grid-template-columns:1fr}}"
    "@media print{body{background:#fff}main{max-width:none;padding:0}.page-tabs,.text-link{display:none}"
    ".decision{background:#fff;color:#000}.decision .eyebrow,.decision .confidence{color:#000}.primary-action{display:none}"
    "section{break-inside:auto;border-color:#777;box-shadow:none}.evidence-card,.seat-card{break-inside:avoid}a{color:#000}}"
)


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


def _seat_label(value):
    if value is None or value == "":
        return "未提供"
    return SEAT_LABELS.get(value, str(value))


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
