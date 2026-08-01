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
