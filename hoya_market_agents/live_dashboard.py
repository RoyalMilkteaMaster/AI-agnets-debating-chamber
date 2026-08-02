"""Read-only local dashboard for one append-only market-agent run."""

import json
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .debate_state_machine import (
    DEBATE_START_MS,
    FORCE_STOP_MS,
    THRESHOLD_FIVE_FROM_MS,
    DebateError,
    stances_for,
)
from .question_package import MARKET_STANCES
from .report_renderer import SEAT_LABELS, SOURCE_TIER_LABELS, resolve_stance_labels
from .research_scheduler import PRIMARY_ONLY_END_MS, research_deadlines
from .seats import SEAT_IDS


AGENT_PROFILES = {
    "spot-technical": ("Codex・圖表偵探", "Agent 1", "📈", "codex"),
    "derivatives": ("Codex・槓桿雷達", "Agent 2", "⚙️", "codex"),
    "onchain": ("Codex・鏈上獵人", "Agent 3", "⛓️", "codex"),
    "official-events": ("Claude・官方哨兵", "Agent 4", "📣", "claude"),
    "news": ("Claude・新聞探員", "Agent 5", "📰", "claude"),
    "social-macro": ("Claude・社群觀察員", "Agent 6", "🌐", "claude"),
    "counter-evidence": ("Gemini・反證稽核員", "Agent 7", "🔎", "gemini"),
}

# One colour per ballot position, so a page written for 偏多／偏空／方向不明 still
# reads correctly when the drawn question type ships different words. Anything
# outside the three approved positions gets the neutral treatment instead of an
# undefined class.
STANCE_CLASSES = ("stance-positive", "stance-negative", "stance-neutral")
UNKNOWN_STANCE_CLASS = "stance-unknown"

def rules_for(debate_start_ms=DEBATE_START_MS):
    """The public timeline; only the seal milestone moves with the question type."""
    return (
        {"at_ms": 0, "label": "開始多方蒐證", "required_votes": None},
        {"at_ms": PRIMARY_ONLY_END_MS, "label": "允許可信二手來源", "required_votes": None},
        {"at_ms": debate_start_ms, "label": "封存證據並開始辯論", "required_votes": 6},
        {"at_ms": THRESHOLD_FIVE_FROM_MS, "label": "共識門檻降為五票", "required_votes": 5},
        {"at_ms": FORCE_STOP_MS, "label": "停止辯論並以四票結算", "required_votes": 4},
        {"at_ms": 780_000, "label": "正式報告期限", "required_votes": None},
        {"at_ms": 900_000, "label": "人工閱讀準備結束", "required_votes": None},
    )


RULES = rules_for()


def build_live_state(data_root, run_id=None, now_utc=None, elapsed_override_ms=None):
    """Build a public snapshot without modifying run artifacts."""
    root = Path(data_root).resolve()
    run_dir = _resolve_run_dir(root, run_id)
    if run_dir is None:
        return _waiting_state()

    question = _read_json(run_dir / "question.json") or {}
    manifest = _read_json(run_dir / "manifest.json") or {}
    report = _read_json(run_dir / "report.json") or {}
    events = _read_jsonl(run_dir / "events.jsonl")
    if not events:
        events = _read_jsonl(run_dir / "debate.jsonl")
    replaying = elapsed_override_ms is not None
    if replaying:
        elapsed_ms = max(0, int(elapsed_override_ms))
        visible_events = [
            item for item in events if _integer(item.get("elapsed_ms"), 0) <= elapsed_ms
        ]
    else:
        visible_events = events
        elapsed_ms = _live_elapsed_ms(question, manifest, events, now_utc)

    # 這一場的封存時刻由題型決定；規則時間線與階段判斷都跟著它走。
    debate_start_ms = research_deadlines(_question_type(question)).seal_ms
    rules = rules_for(debate_start_ms)
    stance_options, stance_labels = _ballot(question)
    debate = [
        _public_debate_entry(item, seq, stance_labels)
        for seq, item in enumerate(
            item
            for item in visible_events
            if item.get("event") == "seat_message" and item.get("seat_id") in SEAT_IDS
        )
    ]
    current_stances, vote_history = _vote_state(debate, stance_labels)
    tally = {stance: 0 for stance in stance_options}
    for stance in current_stances.values():
        if stance in tally:
            tally[stance] += 1

    completed = bool(manifest) and not replaying
    phase = _phase(elapsed_ms, completed=completed, rules=rules)
    report_available = (run_dir / "report.html").is_file()
    research_status = _research_status(visible_events)
    seats = [
        _seat_state(
            seat_id, current_stances.get(seat_id), debate, research_status, stance_labels
        )
        for seat_id in SEAT_IDS
    ]
    evidence = [_public_evidence_entry(item) for item in _read_jsonl(run_dir / "evidence.jsonl")]
    if replaying and elapsed_ms < debate_start_ms:
        evidence = []
    return {
        "schema_version": "1.0.0",
        "status": "演練重播" if replaying else ("已完成" if completed else "執行中"),
        "run_id": run_dir.name,
        "question": question.get("question") or manifest.get("question") or "題目尚未寫入",
        "asset_label": _asset_label(question, report),
        "elapsed_ms": elapsed_ms,
        "total_remaining_ms": max(0, 900_000 - elapsed_ms),
        "report_remaining_ms": max(0, 780_000 - elapsed_ms),
        "phase": phase,
        "tally": tally,
        "tally_labels": dict(stance_labels),
        "stance_options": list(stance_options),
        "stance_labels": dict(stance_labels),
        "stance_classes": _stance_classes(stance_options),
        "focus": _focus_state(
            tally, phase, report, report_available, stance_options, stance_labels
        ),
        "seats": seats,
        "debate": debate,
        "vote_history": vote_history,
        "evidence": evidence,
        "rules": list(rules),
        "report_available": report_available,
        "debate_report_available": (run_dir / "debate.html").is_file(),
        "updated_at_utc": _iso_utc(now_utc or datetime.now(timezone.utc)),
    }


def create_live_server(data_root, run_id=None, host="127.0.0.1", port=8765):
    """Return a local threaded HTTP server; callers control its lifetime."""
    root = Path(data_root).resolve()

    class LiveHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            request = urlsplit(self.path)
            query = parse_qs(request.query)
            if request.path in ("/", "/live.html"):
                self._send(200, "text/html; charset=utf-8", render_live_html().encode("utf-8"))
                return
            if request.path == "/api/runs":
                self._send_json(200, {"runs": list_runs(root)})
                return
            if request.path == "/api/state":
                elapsed = query.get("elapsed_ms", [None])[0]
                try:
                    elapsed_override = None if elapsed is None else int(elapsed)
                except ValueError:
                    self._send_json(400, {"error": "elapsed_ms 必須為整數"})
                    return
                self._send_json(
                    200,
                    build_live_state(
                        root,
                        self._selected_run_id(query),
                        elapsed_override_ms=elapsed_override,
                    ),
                )
                return
            if request.path == "/api/events":
                self._stream_events(query)
                return
            artifact_name = {
                "/report.html": "report.html",
                "/debate.html": "debate.html",
            }.get(request.path)
            if artifact_name is not None:
                selected_run = _resolve_run_dir(root, self._selected_run_id(query))
                artifact_path = selected_run / artifact_name if selected_run else None
                try:
                    body = artifact_path.read_bytes() if artifact_path else None
                except OSError:
                    body = None
                if body is None:
                    self._send_json(404, {"error": "報告尚未產生"})
                    return
                self._send(200, "text/html; charset=utf-8", body)
                return
            self._send_json(404, {"error": "找不到頁面"})

        def _selected_run_id(self, query):
            """Query parameter wins, then the server pin, then the newest run."""
            requested = query.get("run", [""])[0]
            return requested or run_id

        def _send_json(self, status, payload):
            self._send(
                status,
                "application/json; charset=utf-8",
                (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"),
            )

        def _stream_events(self, query):
            replaying = query.get("replay", ["0"])[0] == "1"
            try:
                replay_speed = max(1.0, min(200.0, float(query.get("speed", ["20"])[0])))
            except ValueError:
                self._send_json(400, {"error": "speed 必須為數字"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            selected_run_id = self._selected_run_id(query)
            started = time.monotonic()
            last_signature = None
            last_heartbeat = started
            try:
                while True:
                    now = time.monotonic()
                    elapsed_override = (
                        int((now - started) * 1000 * replay_speed) if replaying else None
                    )
                    state = build_live_state(
                        root,
                        selected_run_id,
                        elapsed_override_ms=elapsed_override,
                    )
                    signature = _state_signature(state)
                    if signature != last_signature:
                        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
                        # Every push carries the whole snapshot and the client
                        # dedupes on debate seq, so a fast reconnect can neither
                        # duplicate nor lose a message.
                        self.wfile.write(
                            ("data: " + payload + "\nretry: 2000\n\n").encode("utf-8")
                        )
                        self.wfile.flush()
                        last_signature = signature
                        last_heartbeat = now
                    elif now - last_heartbeat >= 15:
                        self.wfile.write(": 保持連線\n\n".encode("utf-8"))
                        self.wfile.flush()
                        last_heartbeat = now
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def _send(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline' 'self'; script-src 'unsafe-inline' 'self'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer((host, port), LiveHandler)
    server.daemon_threads = True
    return server


def render_live_html():
    """Return the self-contained dashboard shell; public data uses textContent."""
    return _LIVE_HTML


def _resolve_run_dir(data_root, run_id):
    runs_root = data_root / "runs"
    if run_id is not None:
        return _named_run_dir(runs_root, run_id)
    newest = _run_dirs_newest_first(runs_root)
    if newest:
        return newest[0]
    return _named_run_dir(runs_root, _latest_pointer_run_id(runs_root))


def _named_run_dir(runs_root, run_id):
    if not isinstance(run_id, str) or Path(run_id).name != run_id or run_id in ("", ".", ".."):
        return None
    candidate = runs_root / run_id
    if not candidate.is_dir():
        return None
    return candidate if candidate.resolve().parent == runs_root.resolve() else None


def _run_dirs_newest_first(runs_root):
    """Run ids start with a UTC timestamp, so reverse name order is time order."""
    if not runs_root.is_dir():
        return []
    try:
        entries = [path for path in runs_root.iterdir() if path.is_dir()]
    except OSError:
        return []
    return sorted(entries, key=lambda path: path.name, reverse=True)


def _latest_pointer_run_id(runs_root):
    pointer = _read_json(runs_root / "latest.json") or {}
    run_id = pointer.get("run_id") if isinstance(pointer, dict) else None
    return run_id if isinstance(run_id, str) else None


def list_runs(data_root):
    """Return one cheap summary per run directory, newest first."""
    runs_root = Path(data_root).resolve() / "runs"
    summaries = []
    for run_dir in _run_dirs_newest_first(runs_root):
        summary = _run_summary(run_dir)
        if summary is not None:
            summaries.append(summary)
    return summaries


def _run_summary(run_dir):
    try:
        question = _read_json(run_dir / "question.json") or {}
        return {
            "run_id": run_dir.name,
            "question": _run_summary_label(question),
            "sealed": (run_dir / "snapshots" / "evidence.snapshot.json").is_file(),
            "has_report": (run_dir / "report.html").is_file(),
            "event_count": _count_lines(run_dir / "events.jsonl"),
        }
    except OSError:
        return None


def _run_summary_label(question):
    if not isinstance(question, dict):
        return None
    text = question.get("question")
    if isinstance(text, str) and text.strip():
        return text.strip()
    assets = question.get("assets")
    clean = [value.strip() for value in (assets or []) if isinstance(value, str) and value.strip()]
    return "／".join(clean) if clean else None


def _count_lines(path):
    total = 0
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                total += chunk.count(b"\n")
    except OSError:
        return 0
    return total


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _read_jsonl(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _live_elapsed_ms(question, manifest, events, now_utc):
    if isinstance(manifest.get("elapsed_ms"), int):
        return max(0, manifest["elapsed_ms"])
    latest = max((_integer(item.get("elapsed_ms"), 0) for item in events), default=0)
    start_text = question.get("created_at_utc")
    if not start_text and events:
        first = min(events, key=lambda item: _integer(item.get("elapsed_ms"), 0))
        created = _parse_utc(first.get("created_at_utc"))
        if created is not None:
            start = created - timedelta(milliseconds=_integer(first.get("elapsed_ms"), 0))
        else:
            start = None
    else:
        start = _parse_utc(start_text)
    if start is None:
        return latest
    now = now_utc or datetime.now(timezone.utc)
    wall_elapsed = int((now - start).total_seconds() * 1000)
    return max(latest, max(0, wall_elapsed))


def _phase(elapsed_ms, completed=False, rules=RULES):
    if completed:
        return {
            "key": "completed",
            "label": "執行完成",
            "required_votes": None,
            "threshold_label": "已結算",
            "next_rule_label": "流程已完成",
            "next_rule_in_ms": 0,
        }
    debate_start_ms = _seal_milestone_ms(rules)
    if elapsed_ms < debate_start_ms:
        key, label, required = "research", "多方蒐證", None
    elif elapsed_ms < THRESHOLD_FIVE_FROM_MS:
        key, label, required = "first_debate", "第一輪辯論", 6
    elif elapsed_ms < FORCE_STOP_MS:
        key, label, required = "extended_debate", "深入辯論", 5
    elif elapsed_ms < 780_000:
        key, label, required = "reporting", "結算與產生報告", 4
    elif elapsed_ms < 900_000:
        key, label, required = "human_review", "人工閱讀與準備", None
    else:
        key, label, required = "expired", "十五分鐘流程結束", None
    upcoming = next((rule for rule in rules if rule["at_ms"] > elapsed_ms), None)
    return {
        "key": key,
        "label": label,
        "required_votes": required,
        "threshold_label": (
            "{} 票".format(required)
            if required is not None
            else ("流程已結束" if key == "expired" else "尚未進入投票")
        ),
        "next_rule_label": upcoming["label"] if upcoming else "無",
        "next_rule_in_ms": max(0, upcoming["at_ms"] - elapsed_ms) if upcoming else 0,
    }


def _seal_milestone_ms(rules):
    """The seal milestone is the one rule that moves; read it back from 時間線。"""
    return next(rule["at_ms"] for rule in rules if rule["required_votes"] == 6)


def _question_type(question):
    return question.get("question_type") if isinstance(question, dict) else None


def _ballot(question):
    """Read this run's stance options and wording off ``question.json``.

    The state machine's mapping stays the authority for which stances exist;
    the run's own recorded labels win over derived ones, and a question written
    before the type was recorded still reads as a market question.
    """
    question = question if isinstance(question, dict) else {}
    options = _stance_options(question.get("question_type"))
    labels = resolve_stance_labels(
        options, question.get("assets") or (), question.get("stance_labels")
    )
    return options, labels


def _stance_options(question_type):
    try:
        return tuple(stances_for(question_type))
    except DebateError:
        return MARKET_STANCES


def _stance_classes(stance_options):
    return {
        stance: STANCE_CLASSES[index] if index < len(STANCE_CLASSES) else UNKNOWN_STANCE_CLASS
        for index, stance in enumerate(stance_options)
    }


def _asset_label(question, report):
    assets = report.get("assets") if isinstance(report, dict) else None
    if not isinstance(assets, list):
        assets = question.get("assets") if isinstance(question, dict) else None
    clean = [value for value in (assets or []) if isinstance(value, str) and value.strip()]
    return "／".join(clean) if clean else "市場"


def _focus_state(tally, phase, report, report_available, stance_options, stance_labels):
    highest = max(tally.values(), default=0)
    leaders = [
        stance for stance in stance_options if tally.get(stance) == highest and highest > 0
    ]
    adopted = report.get("adopted_stance") if isinstance(report, dict) else None
    consensus = report.get("consensus_status") if isinstance(report, dict) else None
    if consensus == "consensus" and adopted in stance_labels:
        headline = "已達共識：{}".format(stance_labels[adopted])
    elif len(leaders) == 1:
        headline = "目前{}領先".format(stance_labels[leaders[0]])
    else:
        headline = "尚未形成單一領先"

    confidence = report.get("confidence") if isinstance(report, dict) else None
    confidence = confidence if isinstance(confidence, dict) else {}
    tally_text = "｜".join(
        "{} {}".format(stance_labels[stance], tally.get(stance, 0))
        for stance in stance_options
    )
    if report_available:
        action_label = "查看市場報告"
        action_href = "report.html"
    else:
        action_label = "下一規則：{}".format(phase["next_rule_label"])
        action_href = "#rules-detail"
    return {
        "headline": headline,
        "tally_text": tally_text,
        "market_status": report.get("market_status") if isinstance(report, dict) else None,
        "confidence_icon": confidence.get("icon") or "⚪",
        "confidence_text": confidence.get("text") or "尚未評估",
        "action_label": action_label,
        "action_href": action_href,
    }


def _public_debate_entry(item, seq, stance_labels):
    """`seq` is the chat-room cursor: append-only logs give a stable position."""
    profile = AGENT_PROFILES[item["seat_id"]]
    return {
        "seq": seq,
        "message_id": item.get("message_id"),
        "seat_id": item.get("seat_id"),
        "seat_label": SEAT_LABELS[item["seat_id"]],
        "agent_name": profile[0],
        "agent_number": profile[1],
        "avatar": profile[2],
        "provider_class": profile[3],
        "kind": item.get("kind"),
        "round": item.get("round"),
        "stance": item.get("stance"),
        "stance_label": stance_labels.get(item.get("stance"), "未表態"),
        "public_reason": item.get("public_reason") or "未提供公開理由",
        "evidence_ids": list(item.get("evidence_ids") or []),
        "target_seat_id": item.get("target_seat_id"),
        "target_seat_label": SEAT_LABELS.get(item.get("target_seat_id")),
        "responds_to": list(item.get("responds_to") or []),
        "stance_change_reason": item.get("stance_change_reason"),
        "elapsed_ms": _integer(item.get("elapsed_ms"), 0),
    }


def _public_evidence_entry(item):
    result = dict(item)
    result["source_tier_label"] = SOURCE_TIER_LABELS.get(
        item.get("source_tier"), "來源等級未提供"
    )
    result["published_at_label"] = _time_label(item.get("published_at_utc"))
    return result


def _vote_state(debate, stance_labels):
    current = {}
    history = []
    for message in debate:
        stance = message.get("stance")
        if stance not in stance_labels:
            message["stance_changed"] = None
            message["stance_change_label"] = "未提供"
            continue
        seat_id = message["seat_id"]
        before = current.get(seat_id)
        changed = before is not None and before != stance
        message["stance_changed"] = changed
        if before is None:
            message["stance_change_label"] = "否（首次表態）"
        elif changed:
            message["stance_change_label"] = "是：{} → {}".format(
                stance_labels[before], stance_labels[stance]
            )
        else:
            message["stance_change_label"] = "否"
        if before == stance:
            continue
        current[seat_id] = stance
        history.append(
            {
                "seat_id": seat_id,
                "agent_name": message["agent_name"],
                "before": before,
                "after": stance,
                "before_label": stance_labels.get(before, "尚未投票"),
                "after_label": stance_labels[stance],
                "reason": message.get("stance_change_reason") or message["public_reason"],
                "elapsed_ms": message["elapsed_ms"],
            }
        )
    return current, history


def _research_status(events):
    result = {}
    labels = {
        "attempt_started": "正在蒐證",
        "first_valid_result_adopted": "研究資料已採用",
        "valid_result_retained_as_diagnostic": "保留診斷資料",
        "malformed_output": "格式修復中",
        "process_cancelled": "執行逾時",
    }
    for event in events:
        seat_id = event.get("seat_id")
        if seat_id in SEAT_IDS and event.get("event") in labels:
            result[seat_id] = labels[event["event"]]
    return result


def _seat_state(seat_id, stance, debate, research_status, stance_labels):
    profile = AGENT_PROFILES[seat_id]
    messages = [item for item in debate if item["seat_id"] == seat_id]
    latest = messages[-1] if messages else None
    status_by_kind = {
        "position": "已提出初始立場",
        "challenge": "正在提出質疑",
        "response": "已回應反方",
        "final_vote": "已完成投票",
    }
    return {
        "seat_id": seat_id,
        "seat_label": SEAT_LABELS[seat_id],
        "agent_name": profile[0],
        "agent_number": profile[1],
        "avatar": profile[2],
        "provider_class": profile[3],
        "stance": stance,
        "stance_label": stance_labels.get(stance, "尚未投票"),
        "status": status_by_kind.get(latest.get("kind") if latest else None, research_status.get(seat_id, "等待派工")),
        "last_reason": latest["public_reason"] if latest else "尚未發布公開理由",
        "message_count": len(messages),
    }


def _waiting_state():
    phase = _phase(0)
    stance_options, stance_labels = _ballot({})
    tally = {stance: 0 for stance in stance_options}
    return {
        "schema_version": "1.0.0",
        "status": "等待執行",
        "run_id": None,
        "question": "等待新的市場題目",
        "asset_label": "市場",
        "elapsed_ms": 0,
        "total_remaining_ms": 900_000,
        "report_remaining_ms": 780_000,
        "phase": phase,
        "tally": tally,
        "tally_labels": dict(stance_labels),
        "stance_options": list(stance_options),
        "stance_labels": dict(stance_labels),
        "stance_classes": _stance_classes(stance_options),
        "focus": _focus_state(tally, phase, {}, False, stance_options, stance_labels),
        "seats": [
            _seat_state(seat_id, None, [], {}, stance_labels) for seat_id in SEAT_IDS
        ],
        "debate": [],
        "vote_history": [],
        "evidence": [],
        "rules": list(RULES),
        "report_available": False,
        "debate_report_available": False,
        "updated_at_utc": _iso_utc(datetime.now(timezone.utc)),
    }


def _parse_utc(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _integer(value, default):
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _iso_utc(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _time_label(value):
    if not isinstance(value, str) or not value:
        return "發布時間未提供"
    parsed = _parse_utc(value)
    if parsed is None:
        return value
    taipei = parsed.astimezone(timezone(timedelta(hours=8)))
    return taipei.strftime("%Y/%m/%d %H:%M（台北時間）")


def _state_signature(state):
    stable = dict(state)
    for key in (
        "elapsed_ms",
        "total_remaining_ms",
        "report_remaining_ms",
        "updated_at_utc",
    ):
        stable.pop(key, None)
    return json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_LIVE_HTML = r'''<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hoya Bit 即時 Agent 辯論室</title><style>
:root{--ink:#172033;--muted:#667085;--line:#dbe2ec;--paper:#fff;--wash:#f2f5fa;--brand:#173b70;--brand-2:#2d5f9d;--bull:#177245;--bear:#a33333;--neutral:#856600}
*{box-sizing:border-box}body{margin:0;background:var(--wash);color:var(--ink);font-family:system-ui,'Noto Sans TC',sans-serif;line-height:1.5}
main{max-width:96rem;margin:auto;padding:1rem 1.25rem 2rem}.top{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem}.eyebrow{color:var(--brand);font-weight:850;font-size:.78rem;letter-spacing:.08em;margin:0}h1{margin:.15rem 0}.top p:last-child{margin:.25rem 0;color:var(--muted)}.top-actions{display:flex;align-items:center;gap:.55rem;flex-wrap:wrap;justify-content:flex-end}.connection{background:#e9f8ef;color:#17633b;padding:.35rem .65rem;border-radius:2rem;font-weight:750}.page-tabs{display:flex;gap:.25rem;padding:.3rem;background:#eaf1fb;border:1px solid var(--line);border-radius:.65rem}.page-tabs a{color:var(--brand);text-decoration:none;padding:.55rem .75rem;border-radius:.4rem;font-weight:800;white-space:nowrap}.page-tabs a[aria-current=page]{background:var(--brand);color:#fff}.page-tabs a[aria-disabled=true]{color:var(--muted);opacity:.55;cursor:not-allowed}.page-tabs a:focus-visible,.focus-action:focus-visible,summary:focus-visible{outline:3px solid #f0b429;outline-offset:2px}
.run-bar{display:flex;align-items:center;gap:.65rem;flex-wrap:wrap;margin:.75rem 0 0;padding:.5rem .8rem;background:var(--paper);border:1px solid var(--line);border-radius:.65rem;font-size:.85rem;color:var(--muted)}.run-bar code{font-weight:800;color:var(--brand);font-size:.85rem}.run-bar select{font:inherit;max-width:26rem;padding:.28rem .4rem;border:1px solid var(--line);border-radius:.4rem;background:#fbfcfe;color:var(--ink)}.run-bar a{color:var(--brand);font-weight:800}.run-bar select:focus-visible,.run-bar a:focus-visible{outline:3px solid #f0b429;outline-offset:2px}.tally-note{margin:.6rem 0 0;color:var(--muted);font-size:.85rem}
.focus-bar{display:flex;align-items:center;justify-content:space-between;gap:1.5rem;margin:1rem 0;padding:1.2rem 1.35rem;border-radius:.9rem;background:linear-gradient(120deg,var(--brand),var(--brand-2));color:#fff;box-shadow:0 8px 24px rgba(23,59,112,.18)}.focus-bar p{margin:.15rem 0}.focus-asset{font-weight:850;letter-spacing:.08em;opacity:.78}.focus-bar h2{font-size:1.65rem;margin:.1rem 0}.focus-tally{font-size:1rem;font-weight:650;opacity:.9;margin-left:.45rem}.focus-detail{opacity:.9}.focus-action{flex:none;background:#fff;color:var(--brand);font-weight:850;text-decoration:none;padding:.75rem 1rem;border-radius:.55rem}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.65rem;margin:0 0 1rem}.panel,.metric,.detail-panel{background:var(--paper);border:1px solid var(--line);border-radius:.75rem;box-shadow:0 3px 14px rgba(23,32,51,.05)}.metric{padding:.65rem .8rem}.metric small{display:block;color:var(--muted)}.metric strong{display:block;font-size:1.08rem;margin-top:.1rem;font-variant-numeric:tabular-nums}
.live-layout{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(20rem,.72fr);gap:1rem;align-items:start}.panel{padding:1rem;margin-bottom:1rem}.panel h2{margin:.1rem 0 .8rem}.chat-panel{min-height:38rem;position:relative}.secondary-grid{display:grid;grid-template-columns:1.1fr .8fr 1.2fr;gap:1rem;margin-top:0}.detail-panel{padding:0;overflow:hidden}.detail-panel summary{cursor:pointer;padding:.9rem 1rem;font-weight:850;color:var(--brand);background:#fbfcfe}.detail-body{padding:0 1rem 1rem}.rules{display:flex;flex-direction:column;gap:.3rem}.rule{display:flex;align-items:baseline;gap:.6rem;border-left:4px solid var(--line);padding:.35rem .6rem;background:#fafbfd;font-size:.9rem}.rule time{font-weight:800;font-variant-numeric:tabular-nums;min-width:4.2rem}.rule.past{opacity:.45}.rule.current{border-color:var(--brand);background:#edf3fc;font-weight:800}
.tally{display:grid;grid-template-columns:repeat(auto-fit,minmax(5.5rem,1fr));gap:.5rem}.tally div{padding:.7rem;border-radius:.5rem;background:#f7f9fc;text-align:center}.tally strong{font-size:1.7rem;display:block}.stance-positive{color:var(--bull)}.stance-negative{color:var(--bear)}.stance-neutral{color:var(--neutral)}.stance-unknown{color:var(--muted)}
.agents{display:flex;flex-direction:column;gap:.45rem}.agent{border:1px solid var(--line);border-radius:.6rem;padding:.55rem .65rem;background:#fbfcfe}.agent-head{display:grid;grid-template-columns:2.25rem 1fr auto;gap:.55rem;align-items:center}.avatar{width:2.25rem;height:2.25rem;border-radius:50%;display:grid;place-items:center;font-size:1.15rem;background:#eaf1fb}.agent h3{font-size:.9rem;margin:0}.agent small{color:var(--muted);font-size:.72rem}.agent .stance{font-size:.82rem;font-weight:850;margin:0}.agent .status{grid-column:2/4;justify-self:start;background:#edf3fc;color:var(--brand);font-size:.72rem;padding:.16rem .42rem;border-radius:1rem}
.feed{display:flex;flex-direction:column;gap:.8rem;max-height:48rem;overflow:auto;padding-right:.25rem}.message{width:min(52rem,96%);border:1px solid var(--line);border-left:4px solid var(--brand);background:#fbfcfe;padding:.8rem .9rem;border-radius:.25rem .85rem .85rem .85rem;box-shadow:0 2px 8px rgba(23,32,51,.05)}.message.claude{border-left-color:#b7602a}.message.gemini{border-left-color:#7357b4}.message-head{display:flex;justify-content:space-between;align-items:center;gap:.75rem}.speaker{display:flex;align-items:center;gap:.45rem}.speaker-avatar{width:2rem;height:2rem;border-radius:50%;display:grid;place-items:center;background:#eaf1fb}.message time{color:var(--muted);font-size:.8rem;white-space:nowrap}.message p{margin:.45rem 0}.message-reason strong,.stance-change strong{color:var(--muted)}.stance-change{font-size:.86rem}.stance-change.changed{color:var(--bear);font-weight:750}
.feed:focus-visible,.feed-jump:focus-visible{outline:3px solid #f0b429;outline-offset:2px}.feed-empty{color:var(--muted)}.speaker small{display:block;color:var(--muted);font-size:.72rem}.message-meta{display:flex;align-items:center;gap:.5rem;flex:none}.badge{font-size:.76rem;font-weight:850;padding:.14rem .5rem;border-radius:1rem;background:#eef1f6;color:var(--muted)}.badge.stance-positive{background:#e7f5ec}.badge.stance-negative{background:#fbeaea}.badge.stance-neutral{background:#fbf3dd}.badge.stance-unknown{background:#eef1f6}.message-evidence{font-size:.76rem;color:var(--muted);margin:.35rem 0 0}.message{animation:bubble-in .18s ease-out}@keyframes bubble-in{from{opacity:0;transform:translateY(.5rem)}to{opacity:1;transform:none}}
.feed-jump{position:absolute;left:50%;bottom:1.1rem;transform:translateX(-50%);border:1px solid var(--line);background:var(--brand);color:#fff;font:inherit;font-weight:800;padding:.4rem .85rem;border-radius:1.5rem;cursor:pointer;box-shadow:0 4px 14px rgba(23,32,51,.18)}
@media(prefers-reduced-motion:reduce){.message{animation:none}}
.history{list-style:none;padding:0;margin:0}.history li{padding:.35rem 0;border-bottom:1px solid var(--line)}.history-row{display:flex;align-items:center;gap:.5rem;font-size:.88rem}.history-row time{color:var(--muted);font-variant-numeric:tabular-nums;min-width:4.2rem}.history-seat{flex:1;min-width:0}.history-row.changed{background:#fff6e6;border-left:3px solid #b8781f;padding-left:.4rem}.history-flag{font-size:.74rem;font-weight:850;color:#8a5a12;background:#fbeccd;padding:.1rem .4rem;border-radius:1rem}.history-more{border-bottom:none}.history-toggle,.reason-toggle{font:inherit;font-size:.8rem;font-weight:800;color:var(--brand);background:none;border:none;padding:.1rem .2rem;cursor:pointer;text-decoration:underline}.history-toggle:focus-visible,.reason-toggle:focus-visible,.evidence-chip:focus-visible{outline:3px solid #f0b429;outline-offset:2px}.evidence-chip{display:inline-block;font-size:.74rem;color:var(--brand);background:#eef3fb;border:1px solid var(--line);border-radius:1rem;padding:.1rem .5rem;margin:.15rem .25rem 0 0;text-decoration:none}.evidence-chip.missing{color:var(--muted);background:#f4f5f7}.evidence-card{border:1px solid var(--line);border-radius:.5rem;padding:.65rem;margin:.5rem 0;background:#fbfcfe}.evidence-card p{margin:.25rem 0}.evidence-card a{color:var(--brand)}
@media(max-width:70rem){.metrics{grid-template-columns:repeat(2,1fr)}.live-layout,.secondary-grid{grid-template-columns:1fr}.focus-bar{align-items:flex-start}.focus-tally{display:block;margin-left:0}}@media(max-width:38rem){.top,.focus-bar{flex-direction:column}.top-actions,.page-tabs{width:100%}.page-tabs a{flex:1;text-align:center;padding:.5rem .3rem}.metrics{grid-template-columns:1fr}.tally{grid-template-columns:1fr}.focus-action{width:100%;text-align:center}}
</style></head><body><main>
<header class="top"><div><p class="eyebrow">HOYA BIT 即時研究流程</p><h1>即時 Agent 辯論室</h1><p id="question">等待新的市場題目</p></div><div class="top-actions"><nav class="page-tabs" aria-label="主要頁面"><a href="live.html" aria-current="page">即時辯論</a><a id="report-link" href="report.html" aria-disabled="true">市場報告</a><a id="debate-link" href="debate.html" aria-disabled="true">完整辯論</a></nav><span class="connection" id="connection">連線中</span></div></header>
<div class="run-bar"><span>目前 run：<code id="run-id">尚未選定</code></span><label for="run-picker">歷史 run</label><select id="run-picker"><option value="">載入中…</option></select><a id="run-back" href="/" hidden>回到目前 run</a></div>
<section class="focus-bar" aria-labelledby="focus-title"><div><p class="focus-asset" id="focus-asset">市場</p><h2 id="focus-title"><span id="focus-headline">尚未形成單一領先</span><span class="focus-tally" id="focus-tally">票數尚未開始累計</span></h2><p class="focus-detail" id="focus-detail">⚪ 信心尚未評估</p></div><a class="focus-action" id="focus-action" href="#rules-detail">查看下一規則</a></section>
<section class="metrics"><div class="metric"><small>十五分鐘剩餘時間</small><strong id="total-time">15:00</strong></div><div class="metric"><small>報告期限剩餘時間</small><strong id="report-time">13:00</strong></div><div class="metric"><small>目前階段</small><strong id="phase">等待執行</strong></div><div class="metric"><small>目前共識門檻</small><strong id="threshold">尚未進入投票</strong></div></section>
<div class="live-layout"><section class="panel chat-panel"><p class="eyebrow">現在正在發生</p><h2>公開辯論直播</h2><div class="feed" id="feed" role="log" aria-live="polite" aria-relevant="additions" aria-label="辯論聊天室" tabindex="0"><p class="feed-empty" id="feed-empty">尚未開始辯論。</p></div><button class="feed-jump" id="feed-jump" type="button" hidden>有新發言 ↓</button></section><aside><section class="panel"><h2>即時票數</h2><div class="tally" id="tally"></div><p class="tally-note" id="tally-note"></p></section><section class="panel"><h2>七席研究 Agent</h2><div class="agents" id="agents"></div></section></aside></div>
<div class="secondary-grid"><details class="detail-panel" id="rules-detail"><summary>規則與時間線</summary><div class="detail-body"><div class="rules" id="rules"></div></div></details><details class="detail-panel"><summary>票數變化</summary><div class="detail-body"><ol class="history" id="history"><li>尚未投票。</li></ol></div></details><details class="detail-panel"><summary>可驗證證據</summary><div class="detail-body" id="evidence"><p>證據將在證據快照封存後顯示。</p></div></details></div>
</main><script>
const byId=id=>document.getElementById(id);const node=(tag,cls,text)=>{const value=document.createElement(tag);if(cls)value.className=cls;if(text!==undefined)value.textContent=text;return value};
const formatMs=value=>{const seconds=Math.max(0,Math.floor(value/1000));return String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0')};
const params=new URLSearchParams(location.search);const replay=params.get('replay')==='1';const replaySpeed=Math.max(1,Math.min(200,Number(params.get('speed')||20)));const runParam=params.get('run')||'';const viewSignatures={rules:'',tally:'',agents:'',history:'',evidence:''};let latestState=null;let receivedAt=0;let evidenceById={};let stanceClasses={};
const stanceClass=stance=>stanceClasses[stance]||'stance-unknown';
const isFinished=state=>state.status==='已完成';
const CHAT_STAGGER_MS=180;const chat={runId:false,seq:-1,primed:false,queue:[],timer:null};
const feedPinnedToLatest=root=>root.scrollHeight-root.scrollTop-root.clientHeight<64;
function clear(id){const target=byId(id);while(target.firstChild)target.removeChild(target.firstChild);return target}
const currentRuleIndex=state=>state.rules.reduce((found,rule,index)=>state.elapsed_ms>=rule.at_ms?index:found,-1);
function ruleClass(index,current){if(index===current)return 'rule current';return index<current?'rule past':'rule'}
const ruleLabel=rule=>rule.required_votes?rule.label+'（門檻 '+rule.required_votes+'）':rule.label;
function renderRules(state){const root=clear('rules');const current=currentRuleIndex(state);state.rules.forEach((rule,index)=>{const row=node('div',ruleClass(index,current));row.append(node('time','', 'T+'+formatMs(rule.at_ms)));row.append(node('span','',ruleLabel(rule)));root.append(row)})}
function renderTally(state){const root=clear('tally');state.stance_options.forEach(stance=>{const cell=node('div',stanceClass(stance),state.stance_labels[stance]||stance);cell.append(node('strong','',String(state.tally[stance]||0)));root.append(cell)})}
function renderAgents(state){const root=clear('agents');state.seats.forEach(agent=>{const card=node('article','agent '+agent.provider_class);const head=node('div','agent-head');head.append(node('div','avatar',agent.avatar));const names=node('div');names.append(node('h3','',agent.agent_name));names.append(node('small','',agent.agent_number+'｜'+agent.seat_label));head.append(names);head.append(node('p','stance '+stanceClass(agent.stance),agent.stance_label));head.append(node('span','status',agent.status));card.append(head);root.append(card)})}
const FULL_STOPS='。！？';const ASCII_STOPS='.!?';const BRIEF_LIMIT=60;const CHIP_LIMIT=12;
function isSentenceEnd(text,index){const mark=text[index];if(FULL_STOPS.includes(mark))return true;if(!ASCII_STOPS.includes(mark))return false;const next=text[index+1];return next===undefined||next===' '||next==='\n'}
function firstSentence(text){for(let index=0;index<text.length;index+=1){if(isSentenceEnd(text,index))return text.slice(0,index+1)}return text.length>BRIEF_LIMIT?text.slice(0,BRIEF_LIMIT)+'…':text}
const clip=(text,limit)=>text.length>limit?text.slice(0,limit)+'…':text;
function reasonBlock(fullText){const brief=firstSentence(fullText);const reason=node('p','message-reason');reason.append(node('strong','', '判斷／挑戰理由：'));const body=node('span','reason-text',brief);reason.append(body);if(brief===fullText)return reason;const toggle=node('button','reason-toggle','顯示全文');toggle.type='button';toggle.setAttribute('aria-expanded','false');toggle.onclick=()=>{const open=toggle.getAttribute('aria-expanded')==='true'?false:true;body.textContent=open?fullText:brief;toggle.textContent=open?'收合':'顯示全文';toggle.setAttribute('aria-expanded',String(open))};reason.append(document.createTextNode(' '));reason.append(toggle);return reason}
function evidenceChip(evidenceId){const card=evidenceById[evidenceId];if(!card)return node('span','evidence-chip missing',evidenceId);const label=(card.source_origin||'來源未提供')+'｜'+clip(card.statement||'摘要未提供',CHIP_LIMIT);const href=safeUrl(card.source_url);if(!href)return node('span','evidence-chip',label);const link=node('a','evidence-chip',label);link.href=href;link.target='_blank';link.rel='noopener noreferrer';link.title=card.statement||evidenceId;return link}
function evidenceBlock(evidenceIds){const row=node('p','message-evidence');row.append(node('strong','', '引用證據：'));evidenceIds.forEach(evidenceId=>{row.append(document.createTextNode(' '));row.append(evidenceChip(evidenceId))});return row}
function chatBubble(item){const card=node('article','message '+item.provider_class);card.dataset.seq=String(item.seq);const head=node('div','message-head');const speaker=node('div','speaker');speaker.append(node('span','speaker-avatar',item.avatar));const names=node('div');names.append(node('strong','',item.agent_name));names.append(node('small','',item.agent_number+'｜'+item.seat_label));speaker.append(names);head.append(speaker);const meta=node('div','message-meta');meta.append(node('span','badge '+stanceClass(item.stance),item.stance_label));meta.append(node('time','', 'T+'+formatMs(item.elapsed_ms)));head.append(meta);card.append(head);card.append(reasonBlock(item.public_reason));const change=node('p','stance-change '+(item.stance_changed?'changed':''));change.append(node('strong','', '是否變更立場：'));change.append(document.createTextNode(item.stance_change_label));if(item.stance_changed&&item.stance_change_reason)change.append(document.createTextNode('｜'+item.stance_change_reason));card.append(change);if(item.evidence_ids.length)card.append(evidenceBlock(item.evidence_ids));return card}
function appendBubble(item){const root=byId('feed');const pinned=feedPinnedToLatest(root);root.append(chatBubble(item));byId('feed-empty').hidden=true;if(pinned){root.scrollTo({top:root.scrollHeight,behavior:'smooth'});byId('feed-jump').hidden=true}else byId('feed-jump').hidden=false}
function appendBatch(items){if(!items.length)return;const root=byId('feed');const pinned=feedPinnedToLatest(root);items.forEach(item=>root.append(chatBubble(item)));byId('feed-empty').hidden=true;if(pinned){root.scrollTo({top:root.scrollHeight,behavior:'instant'});byId('feed-jump').hidden=true}else byId('feed-jump').hidden=false}
function drainChat(){if(chat.timer||!chat.queue.length)return;appendBubble(chat.queue.shift());chat.timer=setTimeout(()=>{chat.timer=null;drainChat()},CHAT_STAGGER_MS)}
function resetChat(runId){chat.runId=runId;chat.seq=-1;chat.primed=false;chat.queue.length=0;if(chat.timer){clearTimeout(chat.timer);chat.timer=null}byId('feed').querySelectorAll('.message').forEach(card=>card.remove());byId('feed-jump').hidden=true}
function syncChat(state){if(chat.runId!==state.run_id)resetChat(state.run_id);const fresh=state.debate.filter(item=>item.seq>chat.seq);if(fresh.length){chat.seq=fresh[fresh.length-1].seq;if(chat.primed)fresh.forEach(item=>chat.queue.push(item));else appendBatch(fresh)}chat.primed=true;const empty=byId('feed-empty');empty.textContent=isFinished(state)?'本 run 未進行辯論，沒有可回看的發言。':'尚未開始辯論。';empty.hidden=chat.seq>=0;drainChat()}
const HISTORY_PREVIEW=8;let historyExpanded=false;
const seatLabelsOf=state=>Object.fromEntries(state.seats.map(seat=>[seat.seat_id,seat.seat_label]));
function historyRow(item,seatLabels){const changed=Boolean(item.before);const row=node('li','history-row'+(changed?' changed':''));row.append(node('time','', 'T+'+formatMs(item.elapsed_ms)));row.append(node('span','history-seat',seatLabels[item.seat_id]||item.agent_name));row.append(node('span','badge '+stanceClass(item.after),item.after_label));if(changed)row.append(node('span','history-flag','改票'));return row}
function historyToggle(total){const holder=node('li','history-more');const button=node('button','history-toggle',historyExpanded?'只顯示最近 '+HISTORY_PREVIEW+' 筆':'顯示全部（'+total+' 筆）');button.type='button';button.setAttribute('aria-expanded',String(historyExpanded));button.onclick=()=>{historyExpanded=!historyExpanded;renderHistory(latestState)};holder.append(button);return holder}
function renderHistory(state){const root=clear('history');if(!state.vote_history.length){root.append(node('li','',isFinished(state)?'本 run 未進行投票。':'尚未投票。'));return}const seatLabels=seatLabelsOf(state);const hidden=Math.max(0,state.vote_history.length-HISTORY_PREVIEW);const visible=historyExpanded?state.vote_history:state.vote_history.slice(hidden);visible.forEach(item=>root.append(historyRow(item,seatLabels)));if(hidden)root.append(historyToggle(state.vote_history.length))}
function safeUrl(value){try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:null}catch{return null}}
function renderEvidence(state){const root=clear('evidence');if(!state.evidence.length){root.append(node('p','','證據將在證據快照封存後顯示。'));return}state.evidence.forEach(item=>{const card=node('article','evidence-card');card.dataset.evidence=item.evidence_id||'';card.append(node('strong','',item.evidence_id||'證據識別碼未提供'));card.append(node('p','',item.statement||'摘要未提供'));card.append(node('p','', '原文或數值：'+(item.excerpt||'未提供')));card.append(node('small','', (item.source_tier_label||'來源等級未提供')+'｜'+(item.published_at_label||'發布時間未提供')));const href=safeUrl(item.source_url);if(href){const link=node('a','source-link','開啟原始來源');link.href=href;link.target='_blank';link.rel='noopener noreferrer';card.append(link)}root.append(card)})}
function renderChanged(key,signature,callback){if(viewSignatures[key]===signature)return;viewSignatures[key]=signature;callback()}
function setPageAvailability(id,available){const link=byId(id);link.setAttribute('aria-disabled',String(!available));link.onclick=available?null:event=>event.preventDefault()}
function renderFocus(state){byId('focus-asset').textContent=state.asset_label;byId('focus-headline').textContent=state.focus.headline;byId('focus-tally').textContent=state.focus.tally_text;const detail=[state.focus.market_status,state.focus.confidence_icon+' '+state.focus.confidence_text].filter(Boolean).join('｜');byId('focus-detail').textContent=detail;const action=byId('focus-action');action.textContent=state.focus.action_label;action.href=state.focus.action_href.startsWith('#')?state.focus.action_href:state.focus.action_href+runQuery;action.onclick=state.report_available?null:()=>{byId('rules-detail').open=true}}
function renderRunBar(state){byId('run-id').textContent=state.run_id||'尚無 run';const total=Object.values(state.tally).reduce((sum,count)=>sum+(count||0),0);byId('tally-note').textContent=total?'':(isFinished(state)?'本 run 未進行投票，票數維持 0。':'投票尚未開始，票數維持 0。')}
function renderRunOptions(runs){const picker=byId('run-picker');while(picker.firstChild)picker.removeChild(picker.firstChild);const head=node('option','',runs.length?'選擇要回看的 run…':'尚無歷史 run');head.value='';picker.append(head);runs.forEach(item=>{const marks=[item.question||'未記錄題目',item.sealed?'已封存證據':'未封存',item.event_count+' 則事件'];if(item.has_report)marks.push('有報告');const option=node('option','',item.run_id+'｜'+marks.join('｜'));option.value=item.run_id;if(item.run_id===runParam)option.selected=true;picker.append(option)});picker.disabled=!runs.length}
function loadRuns(){fetch('/api/runs',{headers:{'Accept':'application/json'}}).then(response=>response.json()).then(payload=>renderRunOptions(payload.runs||[])).catch(()=>renderRunOptions([]))}
byId('run-picker').onchange=event=>{const value=event.target.value;if(value)location.search='?run='+encodeURIComponent(value)};
byId('feed-jump').onclick=()=>{const root=byId('feed');root.scrollTo({top:root.scrollHeight,behavior:'smooth'});byId('feed-jump').hidden=true};
byId('feed').onscroll=()=>{if(feedPinnedToLatest(byId('feed')))byId('feed-jump').hidden=true};
const runQuery=runParam?'?run='+encodeURIComponent(runParam):'';
byId('run-back').hidden=!runParam;if(runParam){byId('report-link').href='report.html'+runQuery;byId('debate-link').href='debate.html'+runQuery}loadRuns();
function render(state){latestState=state;receivedAt=Date.now();evidenceById=Object.fromEntries(state.evidence.map(card=>[card.evidence_id,card]));stanceClasses=state.stance_classes||{};renderRunBar(state);byId('question').textContent=state.question;byId('phase').textContent=state.phase.label;byId('threshold').textContent=state.phase.threshold_label;renderFocus(state);renderChanged('tally',JSON.stringify([state.stance_options,state.stance_labels,state.tally]),()=>renderTally(state));renderChanged('rules',state.rules.filter(rule=>state.elapsed_ms>=rule.at_ms).length,()=>renderRules(state));renderChanged('agents',JSON.stringify(state.seats),()=>renderAgents(state));syncChat(state);renderChanged('history',JSON.stringify(state.vote_history),()=>renderHistory(state));renderChanged('evidence',state.evidence.map(item=>item.evidence_id).join('|'),()=>renderEvidence(state));setPageAvailability('report-link',state.report_available);setPageAvailability('debate-link',state.debate_report_available);byId('connection').textContent=(replay?'重播中｜':'')+state.status}
function updateClock(){if(latestState){const advances=latestState.status==='執行中'||replay;const speed=replay?replaySpeed:1;const elapsed=Math.min(900000,latestState.elapsed_ms+(advances?(Date.now()-receivedAt)*speed:0));byId('total-time').textContent=formatMs(900000-elapsed);byId('report-time').textContent=formatMs(780000-elapsed)}requestAnimationFrame(updateClock)}
const streamParams=new URLSearchParams();if(replay){streamParams.set('replay','1');streamParams.set('speed',String(replaySpeed))}if(runParam)streamParams.set('run',runParam);const streamQuery=streamParams.toString();const eventStream=new EventSource('/api/events'+(streamQuery?'?'+streamQuery:''));eventStream.onopen=()=>{byId('connection').textContent=replay?'重播已連線':'即時連線'};eventStream.onmessage=event=>{try{render(JSON.parse(event.data))}catch(error){byId('connection').textContent='狀態格式錯誤'}};eventStream.onerror=()=>{byId('connection').textContent='連線中斷，正在重連';byId('connection').style.background='#fdecec';byId('connection').style.color='#9a2f2f'};requestAnimationFrame(updateClock);
</script></body></html>'''
