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

STANCE_LABELS = {
    "bullish": "偏多",
    "bearish": "偏空",
    "neutral": "方向不明",
}

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
        "- 票數：{}".format(_market_tally(report)),
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
            "- 初始立場：{}".format(_stance_label(seat["initial_stance"])),
            "- 最終立場：{}".format(_stance_label(seat["final_stance"])),
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
        '<nav aria-label="報告頁面"><a class="button" href="debate.html">查看完整辯論與證據</a></nav></header>',
        '<section class="decision" aria-labelledby="decision-title">',
        '<h2 id="decision-title">判斷摘要</h2>',
        "<dl>",
        "<dt>市場狀態</dt><dd>{}</dd>".format(_e(report["market_status"])),
        "<dt>分析期間</dt><dd>{}</dd>".format(_e(report["period"]["label"])),
        '<dt>信心</dt><dd class="confidence {}" aria-label="信心 {} {}">{} {}</dd>'.format(
            _e(confidence["level"]),
            _e(confidence["icon"]),
            _e(confidence["text"]),
            _e(confidence["icon"]),
            _e(confidence["text"]),
        ),
        "<dt>票數</dt><dd>{}</dd>".format(_e(_market_tally(report))),
        "<dt>共識狀態</dt><dd>{}</dd>".format(_e(_consensus_label(report["consensus_status"]))),
        "<dt>判斷</dt><dd>{}</dd>".format(_e(report["judgement"])),
        "</dl>",
        "<h3>失效條件</h3>",
        "<ul>",
    ]
    parts += ["<li>{}</li>".format(_e(item)) for item in report["invalidation_conditions"]]
    parts += ["</ul>", "</section>", "<!--first-screen-end-->"]

    parts += ["<section><h2>限制</h2><ul>"]
    parts += ["<li>{}</li>".format(_e(item)) for item in report["limitations"]]
    parts += ["</ul></section>"]
    if report["validation_errors"]:
        parts += ["<section><h2>驗證錯誤</h2><ul>"]
        parts += ["<li>{}</li>".format(_e(item)) for item in report["validation_errors"]]
        parts += ["</ul></section>"]

    parts += [
        "<section>",
        '<div class="section-heading"><div><p class="eyebrow">七個獨立研究角度</p>',
        "<h2>各席判斷與可驗證依據</h2></div>",
        '<a class="text-link" href="debate.html">前往完整辯論室 →</a></div>',
        "<table>",
        "<caption>每席初始與最終立場、公開理由、證據來源與替補紀錄</caption>",
        "<thead><tr>",
        '<th scope="col">研究席</th><th scope="col">立場演變</th>',
        '<th scope="col">公開判斷理由</th><th scope="col">可驗證證據</th><th scope="col">流程紀錄</th>',
        "</tr></thead><tbody>",
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
        parts += [
            "<tr>",
            '<th scope="row"><span class="seat-name">{}</span><code>{}</code></th>'.format(
                _e(_seat_label(seat["seat_id"])), _e(seat["seat_id"])
            ),
            '<td><span class="stance initial">初始：{}</span><span class="arrow" aria-hidden="true">→</span>'
            '<span class="stance final">最終：{}</span></td>'.format(
                _e(_stance_label(seat["initial_stance"])),
                _e(_stance_label(seat["final_stance"])),
            ),
            '<td><p><strong>初始判斷：</strong>{}</p><p><strong>最終判斷：</strong>{}</p></td>'.format(
                _e(seat["initial_public_reason"]), _e(seat["public_reason"])
            ),
            '<td><ul class="seat-evidence">{}</ul></td>'.format(evidence_html),
            '<td><strong>{}</strong><br>替補紀錄：{}</td>'.format(
                _e(change_text), _e(lineage)
            ),
            "</tr>",
        ]
    parts += ["</tbody></table></section>"]

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
            '<article class="evidence-card" id="evidence-{}">'
            '<div class="evidence-card-head"><code>{}</code><span class="tier">{}</span></div>'
            '<h3>{}</h3><dl class="evidence-meta">'
            '<dt>研究席</dt><dd>{}</dd><dt>資料分類</dt><dd>{}</dd>'
            '<dt>證據方向</dt><dd>{}</dd><dt>來源網站</dt><dd>{}</dd>'
            '<dt>發布時間</dt><dd>{}</dd><dt>取得時間</dt><dd>{}</dd></dl>'
            '<p><strong>原文或關鍵數值：</strong>{}</p>'
            '<p><strong>可信度與限制：</strong>{}</p>{}</article>'.format(
                _e(evidence_id),
                _e(evidence_id),
                _e(_source_tier_label(card.get("source_tier"))),
                _e(card.get("statement") or "未提供證據摘要"),
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
    "--brand:#173b70;--brand-soft:#eaf1fb;--positive:#17633b;--negative:#9a2f2f}"
    "*{box-sizing:border-box}body{margin:0;background:var(--wash);color:var(--ink);"
    "font-family:system-ui,'Noto Sans TC',sans-serif;line-height:1.6}main{max-width:86rem;margin:auto;padding:1.5rem}"
    ".page-header,.section-heading{display:flex;justify-content:space-between;align-items:center;gap:1rem}"
    ".page-header{margin:0 0 1rem}.eyebrow{margin:0;color:var(--brand);font-size:.78rem;font-weight:800;"
    "letter-spacing:.08em;text-transform:uppercase}h1{font-size:2rem;margin:.1rem 0}h2{margin:.2rem 0 .8rem}"
    ".button{display:inline-block;background:var(--brand);color:#fff;text-decoration:none;font-weight:800;"
    "padding:.7rem 1rem;border-radius:.5rem}.text-link{color:var(--brand);font-weight:700;text-decoration:none}"
    ".decision,section{background:var(--paper);border:1px solid var(--line);border-radius:.7rem;"
    "padding:1.1rem;margin:0 0 1rem;box-shadow:0 3px 14px rgba(23,32,51,.05)}"
    "dl{display:grid;grid-template-columns:9rem 1fr;gap:.45rem 1rem}dt{font-weight:750}dd{margin:0}"
    ".confidence{font-weight:800}.red{color:#8f1414}.orange{color:#8a3b00}.yellow{color:#6b5700}"
    ".yellow_green{color:#315b10}.green{color:#12602e}table{border-collapse:separate;border-spacing:0;width:100%;"
    "font-size:.94rem}th,td{border-right:1px solid var(--line);border-bottom:1px solid var(--line);"
    "padding:.7rem;text-align:left;vertical-align:top}thead th{background:var(--brand-soft);border-top:1px solid var(--line)}"
    "tr>*:first-child{border-left:1px solid var(--line)}caption{font-weight:700;text-align:left;margin-bottom:.6rem}"
    ".seat-name{display:block;font-weight:850}.seat-name+code{font-size:.72rem;color:var(--muted)}"
    ".stance{display:block;font-weight:750}.arrow{display:block;color:var(--muted);margin:.15rem 0}.final{color:var(--brand)}"
    ".seat-evidence{list-style:none;padding:0;margin:0}.seat-evidence li{padding:.45rem 0;border-bottom:1px dashed var(--line)}"
    ".seat-evidence li:last-child{border:0}.evidence-summary{display:block;margin-top:.2rem}.meta{display:block;color:var(--muted);"
    "font-size:.78rem}.evidence-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(19rem,1fr));gap:.8rem}"
    ".evidence-card{border:1px solid var(--line);border-radius:.55rem;padding:.9rem;background:#fbfcfe}"
    ".evidence-card-head{display:flex;justify-content:space-between;gap:.5rem;align-items:center}.tier{font-size:.75rem;"
    "font-weight:750;color:var(--brand)}.evidence-card h3{font-size:1rem}.evidence-meta{grid-template-columns:5.5rem 1fr;"
    "font-size:.83rem}.source-link{display:inline-block;margin-top:.3rem}a{color:#064f9e;overflow-wrap:anywhere}code{font-weight:700}"
    "@media(max-width:60rem){.page-header,.section-heading{align-items:flex-start;flex-direction:column}"
    "dl{grid-template-columns:1fr}table{display:block;overflow-x:auto}}"
    "@media print{body{background:#fff}main{max-width:none;padding:0}.button,.text-link{display:none}"
    "section{break-inside:auto;border-color:#777;box-shadow:none}.evidence-card{break-inside:avoid}a{color:#000}}"
)


def _market_tally(report):
    return "／".join(
        "{}：{}".format(_stance_label(stance), count)
        for stance, count in report["tally"].items()
    )


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


def _stance_label(value):
    if value is None or value == "":
        return "未取得有效立場"
    return STANCE_LABELS.get(value, str(value))


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
