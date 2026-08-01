"""Read-only local dashboard for one append-only market-agent run."""

import json
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .report_renderer import SEAT_LABELS, SOURCE_TIER_LABELS, STANCE_LABELS
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

RULES = (
    {"at_ms": 0, "label": "開始多方蒐證", "required_votes": None},
    {"at_ms": 90_000, "label": "允許可信二手來源", "required_votes": None},
    {"at_ms": 300_000, "label": "封存證據並開始辯論", "required_votes": 6},
    {"at_ms": 420_000, "label": "共識門檻降為五票", "required_votes": 5},
    {"at_ms": 600_000, "label": "停止辯論並以四票結算", "required_votes": 4},
    {"at_ms": 780_000, "label": "正式報告期限", "required_votes": None},
    {"at_ms": 900_000, "label": "人工閱讀準備結束", "required_votes": None},
)


def build_live_state(data_root, run_id=None, now_utc=None, elapsed_override_ms=None):
    """Build a public snapshot without modifying run artifacts."""
    root = Path(data_root).resolve()
    run_dir = _resolve_run_dir(root, run_id)
    if run_dir is None:
        return _waiting_state()

    question = _read_json(run_dir / "question.json") or {}
    manifest = _read_json(run_dir / "manifest.json") or {}
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

    debate = [
        _public_debate_entry(item)
        for item in visible_events
        if item.get("event") == "seat_message" and item.get("seat_id") in SEAT_IDS
    ]
    current_stances, vote_history = _vote_state(debate)
    tally = {stance: 0 for stance in ("bullish", "bearish", "neutral")}
    for stance in current_stances.values():
        if stance in tally:
            tally[stance] += 1

    completed = bool(manifest) and not replaying
    phase = _phase(elapsed_ms, completed=completed)
    research_status = _research_status(visible_events)
    seats = [
        _seat_state(seat_id, current_stances.get(seat_id), debate, research_status)
        for seat_id in SEAT_IDS
    ]
    evidence = [_public_evidence_entry(item) for item in _read_jsonl(run_dir / "evidence.jsonl")]
    if replaying and elapsed_ms < 300_000:
        evidence = []
    return {
        "schema_version": "1.0.0",
        "status": "演練重播" if replaying else ("已完成" if completed else "執行中"),
        "run_id": run_dir.name,
        "question": question.get("question") or manifest.get("question") or "題目尚未寫入",
        "elapsed_ms": elapsed_ms,
        "total_remaining_ms": max(0, 900_000 - elapsed_ms),
        "report_remaining_ms": max(0, 780_000 - elapsed_ms),
        "phase": phase,
        "tally": tally,
        "tally_labels": dict(STANCE_LABELS),
        "seats": seats,
        "debate": debate,
        "vote_history": vote_history,
        "evidence": evidence,
        "rules": list(RULES),
        "report_available": (run_dir / "report.html").is_file(),
        "debate_report_available": (run_dir / "debate.html").is_file(),
        "updated_at_utc": _iso_utc(now_utc or datetime.now(timezone.utc)),
    }


def create_live_server(data_root, run_id=None, host="127.0.0.1", port=8765):
    """Return a local threaded HTTP server; callers control its lifetime."""
    root = Path(data_root).resolve()

    class LiveHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            request = urlsplit(self.path)
            if request.path in ("/", "/live.html"):
                self._send(200, "text/html; charset=utf-8", render_live_html().encode("utf-8"))
                return
            if request.path == "/api/state":
                query = parse_qs(request.query)
                elapsed = query.get("elapsed_ms", [None])[0]
                try:
                    elapsed_override = None if elapsed is None else int(elapsed)
                except ValueError:
                    self._send_json(400, {"error": "elapsed_ms 必須為整數"})
                    return
                self._send_json(
                    200,
                    build_live_state(root, run_id, elapsed_override_ms=elapsed_override),
                )
                return
            if request.path == "/api/events":
                self._stream_events(parse_qs(request.query))
                return
            artifact_name = {
                "/report.html": "report.html",
                "/debate.html": "debate.html",
            }.get(request.path)
            if artifact_name is not None:
                selected_run = _resolve_run_dir(root, run_id)
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
                        run_id,
                        elapsed_override_ms=elapsed_override,
                    )
                    signature = _state_signature(state)
                    if signature != last_signature:
                        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
                        self.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))
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
        if not isinstance(run_id, str) or Path(run_id).name != run_id or run_id in ("", ".", ".."):
            return None
        candidate = runs_root / run_id
        return candidate if candidate.is_dir() and candidate.resolve().parent == runs_root.resolve() else None
    if not runs_root.is_dir():
        return None
    candidates = sorted((path for path in runs_root.iterdir() if path.is_dir()), key=lambda path: path.name)
    return candidates[-1] if candidates else None


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


def _phase(elapsed_ms, completed=False):
    if completed:
        return {"key": "completed", "label": "執行完成", "required_votes": None, "next_rule_label": "無", "next_rule_in_ms": 0}
    if elapsed_ms < 300_000:
        key, label, required = "research", "多方蒐證", None
    elif elapsed_ms < 420_000:
        key, label, required = "first_debate", "第一輪辯論", 6
    elif elapsed_ms < 600_000:
        key, label, required = "extended_debate", "深入辯論", 5
    elif elapsed_ms < 780_000:
        key, label, required = "reporting", "結算與產生報告", 4
    elif elapsed_ms < 900_000:
        key, label, required = "human_review", "人工閱讀與準備", None
    else:
        key, label, required = "expired", "十五分鐘流程結束", None
    upcoming = next((rule for rule in RULES if rule["at_ms"] > elapsed_ms), None)
    return {
        "key": key,
        "label": label,
        "required_votes": required,
        "next_rule_label": upcoming["label"] if upcoming else "無",
        "next_rule_in_ms": max(0, upcoming["at_ms"] - elapsed_ms) if upcoming else 0,
    }


def _public_debate_entry(item):
    return {
        "message_id": item.get("message_id"),
        "seat_id": item.get("seat_id"),
        "agent_name": AGENT_PROFILES[item["seat_id"]][0],
        "avatar": AGENT_PROFILES[item["seat_id"]][2],
        "kind": item.get("kind"),
        "round": item.get("round"),
        "stance": item.get("stance"),
        "stance_label": STANCE_LABELS.get(item.get("stance"), "未表態"),
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


def _vote_state(debate):
    current = {}
    history = []
    for message in debate:
        stance = message.get("stance")
        if stance not in STANCE_LABELS:
            continue
        seat_id = message["seat_id"]
        before = current.get(seat_id)
        if before == stance:
            continue
        current[seat_id] = stance
        history.append(
            {
                "seat_id": seat_id,
                "agent_name": message["agent_name"],
                "before": before,
                "after": stance,
                "before_label": STANCE_LABELS.get(before, "尚未投票"),
                "after_label": STANCE_LABELS[stance],
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


def _seat_state(seat_id, stance, debate, research_status):
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
        "stance_label": STANCE_LABELS.get(stance, "尚未投票"),
        "status": status_by_kind.get(latest.get("kind") if latest else None, research_status.get(seat_id, "等待派工")),
        "last_reason": latest["public_reason"] if latest else "尚未發布公開理由",
        "message_count": len(messages),
    }


def _waiting_state():
    return {
        "schema_version": "1.0.0",
        "status": "等待執行",
        "run_id": None,
        "question": "等待新的市場題目",
        "elapsed_ms": 0,
        "total_remaining_ms": 900_000,
        "report_remaining_ms": 780_000,
        "phase": _phase(0),
        "tally": {"bullish": 0, "bearish": 0, "neutral": 0},
        "tally_labels": dict(STANCE_LABELS),
        "seats": [_seat_state(seat_id, None, [], {}) for seat_id in SEAT_IDS],
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
:root{--ink:#172033;--muted:#667085;--line:#dbe2ec;--paper:#fff;--wash:#f2f5fa;--brand:#173b70;--bull:#177245;--bear:#a33333;--neutral:#856600}
*{box-sizing:border-box}body{margin:0;background:var(--wash);color:var(--ink);font-family:system-ui,'Noto Sans TC',sans-serif;line-height:1.5}
main{max-width:96rem;margin:auto;padding:1rem}.top{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem}.eyebrow{color:var(--brand);font-weight:850;font-size:.78rem;letter-spacing:.08em;margin:0}h1{margin:.15rem 0}.top-actions{display:flex;align-items:center;gap:.55rem;flex-wrap:wrap;justify-content:flex-end}.connection{background:#e9f8ef;color:#17633b;padding:.35rem .65rem;border-radius:2rem;font-weight:750}.deliverables{display:flex;gap:.45rem}.deliverables a{background:var(--brand);color:#fff;text-decoration:none;padding:.35rem .65rem;border-radius:.4rem;font-weight:750}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.75rem;margin:1rem 0}.panel,.metric{background:var(--paper);border:1px solid var(--line);border-radius:.75rem;box-shadow:0 3px 14px rgba(23,32,51,.05)}.metric{padding:.85rem}.metric small{display:block;color:var(--muted)}.metric strong{display:block;font-size:1.45rem;margin-top:.15rem}
.layout{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(20rem,.8fr);gap:1rem}.panel{padding:1rem;margin-bottom:1rem}.panel h2{margin:.1rem 0 .8rem}.rules{display:flex;overflow:auto;gap:.5rem;padding-bottom:.25rem}.rule{min-width:10rem;border-left:4px solid var(--line);padding:.5rem;background:#fafbfd}.rule.active{border-color:var(--brand);background:#edf3fc}.rule time{font-weight:800;display:block}
.tally{display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem}.tally div{padding:.7rem;border-radius:.5rem;background:#f7f9fc;text-align:center}.tally strong{font-size:1.7rem;display:block}.bullish{color:var(--bull)}.bearish{color:var(--bear)}.neutral{color:var(--neutral)}
.agents{display:grid;grid-template-columns:repeat(auto-fit,minmax(14rem,1fr));gap:.65rem}.agent{border:1px solid var(--line);border-radius:.65rem;padding:.7rem;background:#fbfcfe}.agent-head{display:flex;gap:.6rem;align-items:center}.avatar{width:2.7rem;height:2.7rem;border-radius:50%;display:grid;place-items:center;font-size:1.35rem;background:#eaf1fb}.agent h3{font-size:.95rem;margin:0}.agent small{color:var(--muted)}.agent .stance{font-weight:850;margin:.5rem 0 .2rem}.agent p{font-size:.84rem;margin:.25rem 0}.agent .status{display:inline-block;background:#edf3fc;color:var(--brand);font-size:.75rem;padding:.2rem .45rem;border-radius:1rem}
.feed{display:flex;flex-direction:column;gap:.65rem;max-height:42rem;overflow:auto;padding-right:.25rem}.message{border-left:4px solid var(--line);background:#fbfcfe;padding:.7rem;border-radius:.4rem}.message.challenge{border-color:var(--bear)}.message.response{border-color:var(--bull)}.message-head{display:flex;justify-content:space-between;gap:.5rem}.message p{margin:.35rem 0}.chips{display:flex;flex-wrap:wrap;gap:.3rem}.chip{border:0;background:#eaf1fb;color:var(--brand);padding:.2rem .45rem;border-radius:1rem;font:inherit;font-size:.76rem;cursor:pointer}
.history{list-style:none;padding:0}.history li{padding:.5rem 0;border-bottom:1px solid var(--line)}.evidence-card{border:1px solid var(--line);border-radius:.5rem;padding:.65rem;margin:.5rem 0;background:#fbfcfe}.evidence-card p{margin:.25rem 0}.evidence-card a{color:var(--brand)}
@media(max-width:70rem){.metrics{grid-template-columns:repeat(2,1fr)}.layout{grid-template-columns:1fr}}@media(max-width:38rem){.top{flex-direction:column}.metrics{grid-template-columns:1fr}.tally{grid-template-columns:1fr}}
</style></head><body><main>
<header class="top"><div><p class="eyebrow">HOYA BIT 即時研究流程</p><h1>即時 Agent 辯論室</h1><p id="question">等待新的市場題目</p></div><div class="top-actions"><nav class="deliverables" id="deliverables" aria-label="最終交付物" hidden><a id="report-link" href="/report.html">市場判斷報告</a><a id="debate-link" href="/debate.html">完整辯論紀錄</a></nav><span class="connection" id="connection">連線中</span></div></header>
<section class="metrics"><div class="metric"><small>十五分鐘剩餘時間</small><strong id="total-time">15:00</strong></div><div class="metric"><small>報告期限剩餘時間</small><strong id="report-time">13:00</strong></div><div class="metric"><small>目前階段</small><strong id="phase">等待執行</strong></div><div class="metric"><small>目前共識門檻</small><strong id="threshold">尚未投票</strong></div></section>
<section class="panel"><h2>規則與時間線</h2><div class="rules" id="rules"></div></section>
<div class="layout"><div><section class="panel"><h2>即時票數</h2><div class="tally"><div class="bullish">偏多<strong id="bullish">0</strong></div><div class="bearish">偏空<strong id="bearish">0</strong></div><div class="neutral">方向不明<strong id="neutral">0</strong></div></div></section>
<section class="panel"><h2>七席研究 Agent</h2><div class="agents" id="agents"></div></section><section class="panel"><h2>公開辯論直播</h2><div class="feed" id="feed"><p>尚未開始辯論。</p></div></section></div>
<aside><section class="panel"><h2>票數變化</h2><ol class="history" id="history"><li>尚未投票。</li></ol></section><section class="panel"><h2>可驗證證據</h2><div id="evidence"><p>證據將在 T+5 封存後顯示。</p></div></section></aside></div>
</main><script>
const byId=id=>document.getElementById(id);const node=(tag,cls,text)=>{const value=document.createElement(tag);if(cls)value.className=cls;if(text!==undefined)value.textContent=text;return value};
const formatMs=value=>{const seconds=Math.max(0,Math.floor(value/1000));return String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0')};
const kindLabel={position:'初始立場',challenge:'提出質疑',response:'回應反方',final_vote:'最終投票'};const params=new URLSearchParams(location.search);const replay=params.get('replay')==='1';const replaySpeed=Math.max(1,Math.min(200,Number(params.get('speed')||20)));const viewSignatures={rules:'',agents:'',debate:'',history:'',evidence:''};let latestState=null;let receivedAt=0;
function clear(id){const target=byId(id);while(target.firstChild)target.removeChild(target.firstChild);return target}
function renderRules(state){const root=clear('rules');state.rules.forEach(rule=>{const card=node('div','rule'+(state.elapsed_ms>=rule.at_ms?' active':''));card.append(node('time','',formatMs(rule.at_ms)));card.append(node('span','',rule.label));root.append(card)})}
function renderAgents(state){const root=clear('agents');state.seats.forEach(agent=>{const card=node('article','agent '+agent.provider_class);const head=node('div','agent-head');head.append(node('div','avatar',agent.avatar));const names=node('div');names.append(node('h3','',agent.agent_name));names.append(node('small','',agent.agent_number+'｜'+agent.seat_label));head.append(names);card.append(head);card.append(node('p','stance '+(agent.stance||''),agent.stance_label));card.append(node('span','status',agent.status));card.append(node('p','',agent.last_reason));root.append(card)})}
function evidenceButton(id){const button=node('button','chip',id);button.type='button';button.addEventListener('click',()=>{const target=document.querySelector('[data-evidence="'+CSS.escape(id)+'"]');if(target)target.scrollIntoView({behavior:'smooth',block:'center'})});return button}
function renderFeed(state){const root=clear('feed');if(!state.debate.length){root.append(node('p','','尚未開始辯論。'));return}state.debate.forEach(item=>{const card=node('article','message '+item.kind);const head=node('div','message-head');head.append(node('strong','',item.avatar+' '+item.agent_name));head.append(node('time','', 'T+'+formatMs(item.elapsed_ms)+'｜'+(kindLabel[item.kind]||'公開發言')));card.append(head);card.append(node('p','stance '+(item.stance||''),item.stance_label));card.append(node('p','',item.public_reason));if(item.target_seat_label)card.append(node('p','', '質疑對象：'+item.target_seat_label));const chips=node('div','chips');item.evidence_ids.forEach(id=>chips.append(evidenceButton(id)));card.append(chips);root.append(card)});root.scrollTop=root.scrollHeight}
function renderHistory(state){const root=clear('history');if(!state.vote_history.length){root.append(node('li','','尚未投票。'));return}state.vote_history.forEach(item=>{const row=node('li');row.append(node('strong','',item.agent_name+'｜T+'+formatMs(item.elapsed_ms)));row.append(node('p','',item.before_label+' → '+item.after_label));row.append(node('small','',item.reason));root.append(row)})}
function safeUrl(value){try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:null}catch{return null}}
function renderEvidence(state){const root=clear('evidence');if(!state.evidence.length){root.append(node('p','','證據將在 T+5 封存後顯示。'));return}state.evidence.forEach(item=>{const card=node('article','evidence-card');card.dataset.evidence=item.evidence_id||'';card.append(node('strong','',item.evidence_id||'證據識別碼未提供'));card.append(node('p','',item.statement||'摘要未提供'));card.append(node('p','', '原文或數值：'+(item.excerpt||'未提供')));card.append(node('small','', (item.source_tier_label||'來源等級未提供')+'｜'+(item.published_at_label||'發布時間未提供')));const href=safeUrl(item.source_url);if(href){const link=node('a','source-link','開啟原始來源');link.href=href;link.target='_blank';link.rel='noopener noreferrer';card.append(link)}root.append(card)})}
function renderChanged(key,signature,callback){if(viewSignatures[key]===signature)return;viewSignatures[key]=signature;callback()}
function render(state){latestState=state;receivedAt=Date.now();byId('question').textContent=state.question;byId('phase').textContent=state.phase.label;byId('threshold').textContent=state.phase.required_votes?state.phase.required_votes+' 票':(state.elapsed_ms>=900000?'流程已結束':'尚未投票');['bullish','bearish','neutral'].forEach(key=>byId(key).textContent=state.tally[key]);renderChanged('rules',state.rules.filter(rule=>state.elapsed_ms>=rule.at_ms).length,()=>renderRules(state));renderChanged('agents',JSON.stringify(state.seats),()=>renderAgents(state));renderChanged('debate',state.debate.map(item=>item.message_id).join('|'),()=>renderFeed(state));renderChanged('history',JSON.stringify(state.vote_history),()=>renderHistory(state));renderChanged('evidence',state.evidence.map(item=>item.evidence_id).join('|'),()=>renderEvidence(state));byId('deliverables').hidden=!(state.report_available||state.debate_report_available);byId('report-link').hidden=!state.report_available;byId('debate-link').hidden=!state.debate_report_available;byId('connection').textContent=(replay?'重播中｜':'')+state.status}
function updateClock(){if(latestState){const advances=latestState.status==='執行中'||replay;const speed=replay?replaySpeed:1;const elapsed=Math.min(900000,latestState.elapsed_ms+(advances?(Date.now()-receivedAt)*speed:0));byId('total-time').textContent=formatMs(900000-elapsed);byId('report-time').textContent=formatMs(780000-elapsed)}requestAnimationFrame(updateClock)}
const streamQuery=replay?'?replay=1&speed='+encodeURIComponent(replaySpeed):'';const eventStream=new EventSource('/api/events'+streamQuery);eventStream.onopen=()=>{byId('connection').textContent=replay?'重播已連線':'即時連線'};eventStream.onmessage=event=>{try{render(JSON.parse(event.data))}catch(error){byId('connection').textContent='狀態格式錯誤'}};eventStream.onerror=()=>{byId('connection').textContent='連線中斷，正在重連';byId('connection').style.background='#fdecec';byId('connection').style.color='#9a2f2f'};requestAnimationFrame(updateClock);
</script></body></html>'''
