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

from .contract_validator import CONTRACT_VERSION, validate_report
from .seats import SEAT_IDS

REPORT_SCHEMA_VERSION = CONTRACT_VERSION

DIRECTION_LABELS = (
    ("support", "支持證據"),
    ("oppose", "反方證據"),
    ("neutral", "中性／限制條件證據"),
)

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
    "reason": "本次為 fake provider tracer 執行；信心燈號門檻尚未實作，不得由示範票數推導市場信心。",
}

NO_CONCLUSION = {
    "available": False,
    "reason": (
        "本次執行未啟動 Core Agent，控制程式不得自行撰寫市場結論。"
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
    "article{border-left:4px solid #c8d2dc;padding-left:.9rem;margin-bottom:.9rem}"
    "code{background:#eef2f6;padding:.1rem .3rem;border-radius:.2rem}"
    "a{color:#0b4f9e}"
    "@media print{body{max-width:none;padding:0}.banner{border-width:1px}}"
)


def _headline_rows(report):
    """The first-screen facts, shared verbatim by both renderings."""
    return (
        ("Run ID", report["run_id"]),
        ("題目", report["question"]),
        ("分析資產", "、".join(report["assets"])),
        (
            "分析期間",
            "過去 {} 日（{}）".format(
                report["period_days"], "題目指定" if report["period_stated"] else "預設"
            ),
        ),
        ("開始時間（UTC）", report["started_at_utc"]),
        ("產生時間（UTC）", report["generated_at_utc"]),
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


# --- Ticket #10: renderings of one validated, Core-authored report ----------

NO_DIRECTION_TEXT = "無方向（未達共識、有效票不足或資料不足時不得製造方向）"

_MARKET_CSS = (
    "body{font-family:system-ui,'Noto Sans TC',sans-serif;margin:0 auto;max-width:52rem;"
    "padding:1.5rem;line-height:1.6;color:#16202a;background:#ffffff}"
    ".first-screen{border:2px solid #16202a;border-radius:.5rem;padding:1rem}"
    ".confidence{font-weight:700}"
    "dl{margin:0;display:grid;grid-template-columns:12rem 1fr;gap:.35rem 1rem}"
    "dt{font-weight:700}dd{margin:0}"
    "h1{font-size:1.6rem}h2{font-size:1.2rem;border-bottom:1px solid #c8d2dc;padding-bottom:.3rem}"
    "table{border-collapse:collapse;width:100%}"
    "caption{text-align:left;font-weight:700;padding:.4rem 0}"
    "th,td{border:1px solid #c8d2dc;padding:.4rem;text-align:left;vertical-align:top}"
    "th{background:#eef2f6}"
    "code{background:#eef2f6;padding:.1rem .3rem;border-radius:.2rem}"
    "a{color:#0b4f9e}"
    "@media print{body{max-width:none;padding:0;font-size:11pt}"
    ".first-screen{border-width:1px;break-inside:avoid}"
    "table{break-inside:auto}thead{display:table-header-group}}"
)


def market_tally_entries(report):
    """The full tally, rendered identically for Markdown and HTML."""
    return ["{}：{}".format(stance, count) for stance, count in report["tally"].items()]


def market_direction_text(report):
    """State the adopted direction, or state plainly that there is none."""
    if report.get("adopted_stance") is None:
        return NO_DIRECTION_TEXT
    return report["adopted_stance"]


def _market_headline(report):
    period = report["period"]
    return (
        ("市場狀態", report["market_status"]),
        (
            "分析期間",
            "{}（{} ~ {}）".format(period["label"], period["start_utc"], period["end_utc"]),
        ),
        (
            "信心",
            "{} {}｜{}".format(
                report["confidence"]["icon"],
                report["confidence"]["level"],
                report["confidence"]["text"],
            ),
        ),
        ("票數", "／".join(market_tally_entries(report))),
        ("共識狀態", report["consensus_status"]),
        ("市場方向", market_direction_text(report)),
        ("判讀", report["judgement"]),
    )


def render_market_markdown(report):
    """Render one validated report as Markdown."""
    lines = ["# Hoya Bit 市場研究報告", "", "## 第一屏摘要", ""]
    lines += ["- {}：{}".format(term, value) for term, value in _market_headline(report)]
    lines += ["", "### 失效條件", ""]
    lines += ["- {}".format(item) for item in report["invalidation_conditions"]]
    lines += ["", "## 限制", ""]
    lines += ["- {}".format(item) for item in report["limitations"]]

    lines += ["", "## 七席立場", ""]
    for row in report["seats"]:
        lines += [
            "### {}".format(row["seat_id"]),
            "",
            "- 初始立場：{}".format(row["initial_stance"]),
            "- 最終立場：{}".format(row["final_stance"]),
            "- 是否改票：{}".format("改票" if row["stance_changed"] else "未改票"),
            "- 改票／未改票原因：{}".format(
                row["stance_change_reason"] if row["stance_changed"] else row["no_change_reason"]
            ),
            "- 初始公開理由：{}".format(row["initial_public_reason"]),
            "- 最終公開理由：{}".format(row["public_reason"]),
            "- 替補 attempt 系譜：{}".format(", ".join(row["replacement_attempt_ids"]) or "無替補"),
            "- 支持證據：{}".format(", ".join(row["support_evidence_ids"]) or "無"),
            "- 反方證據：{}".format(", ".join(row["counter_evidence_ids"]) or "無"),
            "",
        ]

    lines += ["## 證據清單", ""]
    for card in report["evidence"]:
        lines.append(
            "- `{}`（{}）{} 來源：{}".format(
                card["evidence_id"], card["direction"], card["statement"], card["url"]
            )
        )
    if not report["evidence"]:
        lines.append("- 無可追溯證據")

    lines += ["", "## 稽核", ""]
    lines.append("- 流程失敗：{}".format("是" if report.get("process_failure") else "否"))
    if report.get("validation_errors"):
        lines += ["- 驗證錯誤：", ""]
        lines += ["  - {}".format(error) for error in report["validation_errors"]]
    lines.append("")
    return "\n".join(lines)


def render_market_html(report):
    """Render one validated report as a single offline, printable HTML file."""
    confidence = report["confidence"]
    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-Hant">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Hoya Bit 市場研究報告</title>",
        "<style>{}</style>".format(_MARKET_CSS),
        "</head>",
        "<body>",
        "<h1>Hoya Bit 市場研究報告</h1>",
        '<section class="first-screen" aria-label="報告摘要">',
        "<dl>",
    ]
    for term, value in _market_headline(report):
        css = ' class="confidence"' if term == "信心" else ""
        aria = (
            ' aria-label="信心 {}"'.format(_e(confidence["level"])) if term == "信心" else ""
        )
        parts += [
            "<dt>{}</dt>".format(_e(term)),
            "<dd{}{}>{}</dd>".format(css, aria, _e(value)),
        ]
    parts += ["</dl>", "<h2>失效條件</h2>", "<ul>"]
    parts += ["<li>{}</li>".format(_e(item)) for item in report["invalidation_conditions"]]
    parts += ["</ul>", "</section>", "<!--first-screen-end-->"]

    parts += ["<h2>限制</h2>", "<ul>"]
    parts += ["<li>{}</li>".format(_e(item)) for item in report["limitations"]]
    parts += ["</ul>"]

    parts += [
        "<h2>七席立場</h2>",
        "<table>",
        "<caption>七席初始與最終立場、改票紀錄、替補系譜與引用證據</caption>",
        "<thead><tr>",
    ]
    headers = (
        "席位",
        "初始立場",
        "最終立場",
        "是否改票",
        "改票／未改票原因",
        "初始公開理由",
        "最終公開理由",
        "替補 attempt 系譜",
        "支持證據",
        "反方證據",
    )
    parts += ['<th scope="col">{}</th>'.format(_e(header)) for header in headers]
    parts += ["</tr></thead>", "<tbody>"]
    for row in report["seats"]:
        cells = (
            row["initial_stance"],
            row["final_stance"],
            "改票" if row["stance_changed"] else "未改票",
            row["stance_change_reason"] if row["stance_changed"] else row["no_change_reason"],
            row["initial_public_reason"],
            row["public_reason"],
            ", ".join(row["replacement_attempt_ids"]) or "無替補",
            ", ".join(row["support_evidence_ids"]) or "無",
            ", ".join(row["counter_evidence_ids"]) or "無",
        )
        parts.append(
            '<tr><th scope="row">{}</th>{}</tr>'.format(
                _e(row["seat_id"]),
                "".join("<td>{}</td>".format(_e(cell)) for cell in cells),
            )
        )
    parts += ["</tbody>", "</table>"]

    parts += ["<h2>證據清單</h2>", "<ul>"]
    for card in report["evidence"]:
        parts.append(
            '<li><code>{}</code>（{}）{} 來源：<a href="{}">{}</a></li>'.format(
                _e(card["evidence_id"]),
                _e(card["direction"]),
                _e(card["statement"]),
                _e(card["url"]),
                _e(card["url"]),
            )
        )
    if not report["evidence"]:
        parts.append("<li>無可追溯證據</li>")
    parts += ["</ul>"]

    parts += [
        "<h2>稽核</h2>",
        "<ul>",
        "<li>流程失敗：{}</li>".format("是" if report.get("process_failure") else "否"),
    ]
    for error in report.get("validation_errors") or []:
        parts.append("<li>驗證錯誤：{}</li>".format(_e(error)))
    parts += ["</ul>", "</body>", "</html>", ""]
    return "\n".join(parts)


def render_market_markdown(report):
    """Render one already-validated, Core-authored report as Markdown."""
    lines = [
        "# Hoya Bit 市場判斷報告",
        "",
        "## 判斷摘要",
        "",
        "- 市場狀態：{}".format(report["market_status"]),
        "- 分析期間：{}".format(report["period"]["label"]),
        "- 信心：{} {}（{}）".format(
            report["confidence"]["icon"],
            report["confidence"]["text"],
            report["confidence"]["level"],
        ),
        "- 票數：{}".format(_market_tally(report)),
        "- 共識狀態：{}".format(report["consensus_status"]),
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
            "### {}".format(seat["seat_id"]),
            "",
            "- 初始立場：{}".format(_display(seat["initial_stance"])),
            "- 最終立場：{}".format(_display(seat["final_stance"])),
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
        lines.append(
            "- `{}` [{}]({})｜{}｜{}".format(
                card["evidence_id"], card["url"], card["url"], card["direction"], card["statement"]
            )
        )
    lines.append("")
    return "\n".join(lines)


def render_market_html(report):
    """Render one validated report as a self-contained, accessible HTML file."""
    confidence = report["confidence"]
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
        "<h1>Hoya Bit 市場判斷報告</h1>",
        '<section class="decision" aria-labelledby="decision-title">',
        '<h2 id="decision-title">判斷摘要</h2>',
        "<dl>",
        "<dt>市場狀態</dt><dd>{}</dd>".format(_e(report["market_status"])),
        "<dt>分析期間</dt><dd>{}</dd>".format(_e(report["period"]["label"])),
        '<dt>信心</dt><dd class="confidence {}" aria-label="信心 {} {}">{} {}（{}）</dd>'.format(
            _e(confidence["level"]),
            _e(confidence["icon"]),
            _e(confidence["text"]),
            _e(confidence["icon"]),
            _e(confidence["text"]),
            _e(confidence["level"]),
        ),
        "<dt>票數</dt><dd>{}</dd>".format(_e(_market_tally(report))),
        "<dt>共識狀態</dt><dd>{}</dd>".format(_e(report["consensus_status"])),
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
        "<h2>七席完整紀錄</h2>",
        "<table>",
        "<caption>每席初始與最終立場、理由、改票及替補 lineage</caption>",
        "<thead><tr>",
        '<th scope="col">席位</th><th scope="col">初始</th><th scope="col">最終</th>',
        '<th scope="col">改票</th><th scope="col">公開理由</th><th scope="col">來源</th>',
        "</tr></thead><tbody>",
    ]
    for seat in report["seats"]:
        change_text = (
            "是：{}".format(_display(seat["stance_change_reason"]))
            if seat["stance_changed"]
            else "否：{}".format(_display(seat["no_change_reason"]))
        )
        lineage = ", ".join(seat["replacement_attempt_ids"]) or "無替補"
        evidence = "支持 {}；反方 {}".format(
            ", ".join(seat["support_evidence_ids"]) or "無",
            ", ".join(seat["counter_evidence_ids"]) or "無",
        )
        parts += [
            "<tr>",
            '<th scope="row">{}</th>'.format(_e(seat["seat_id"])),
            "<td>{}<br>{}</td>".format(
                _e(_display(seat["initial_stance"])), _e(seat["initial_public_reason"])
            ),
            "<td>{}</td>".format(_e(_display(seat["final_stance"]))),
            "<td>{}</td>".format(_e(change_text)),
            "<td>{}</td>".format(_e(seat["public_reason"])),
            "<td>{}<br>{}</td>".format(_e(lineage), _e(evidence)),
            "</tr>",
        ]
    parts += ["</tbody></table></section>"]

    parts += ['<section><h2>證據與來源</h2><ul class="evidence">']
    for card in report["evidence"]:
        parts.append(
            '<li><code>{}</code> <a href="{}">{}</a>｜{}｜{}</li>'.format(
                _e(card["evidence_id"]),
                _e(card["url"]),
                _e(card["url"]),
                _e(card["direction"]),
                _e(card["statement"]),
            )
        )
    parts += ["</ul></section>", "</main>", "</body>", "</html>", ""]
    return "\n".join(parts)


_MARKET_CSS = (
    "*{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:#18212b;"
    "font-family:system-ui,sans-serif;line-height:1.55}main{max-width:72rem;margin:auto;padding:1.25rem}"
    "h1{font-size:1.8rem}.decision,section{background:#fff;border:1px solid #cbd5df;"
    "border-radius:.55rem;padding:1rem;margin:0 0 1rem}dl{display:grid;grid-template-columns:9rem 1fr;gap:.4rem 1rem}"
    "dt{font-weight:700}dd{margin:0}.confidence{font-weight:800}.red{color:#8f1414}.orange{color:#8a3b00}"
    ".yellow{color:#6b5700}.yellow_green{color:#315b10}.green{color:#12602e}table{border-collapse:collapse;width:100%}"
    "th,td{border:1px solid #aeb9c4;padding:.5rem;text-align:left;vertical-align:top}caption{font-weight:700;"
    "text-align:left;margin-bottom:.5rem}a{color:#064f9e;overflow-wrap:anywhere}code{font-weight:700}"
    "@media(max-width:48rem){dl{grid-template-columns:1fr}table{display:block;overflow-x:auto}}"
    "@media print{body{background:#fff}main{max-width:none;padding:0}section{break-inside:avoid;border-color:#777}a{color:#000}}"
)


def _market_tally(report):
    return "／".join("{}：{}".format(stance, count) for stance, count in report["tally"].items())


def _display(value):
    return "無" if value is None or value == "" else str(value)
