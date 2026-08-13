#!/usr/bin/env python3
"""Ticket 07 WSL release-acceptance helpers (stdlib only, fail closed)."""

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hoya_market_agents.antigravity_adapter import AntigravityAdapter
from hoya_market_agents.claude_adapter import TerminatingRunner, run_claude_preflight
from hoya_market_agents.codex_exec_adapter import CodexExecAdapter
from hoya_market_agents.run_index import RunIndexError, query_runs
from hoya_market_agents.run_store import resolve_run_dir, run_dir_hash
from hoya_market_agents.seats import SEAT_IDS

SCHEMA_VERSION = "hoya.wsl-release-acceptance.v1"
NON_ADOPTED_TERMINAL_OUTCOMES = frozenset(
    {"superseded", "failed", "cancelled", "late_discarded"}
)
PROVIDERS = ("codex", "claude", "antigravity")
PROVIDER_COMMAND = {"codex": "codex", "claude": "claude", "antigravity": "agy"}
CANARY_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "const": "ready"}},
    "required": ["answer"],
    "additionalProperties": False,
}
CANARY_PROMPT = (
    "Perform exactly one web search for the current UTC date. Do not quote or "
    "summarize its result. Return only the schema value answer=ready."
)


class AcceptanceError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("無法讀取 JSON：{}".format(path)) from exc
    if not isinstance(value, dict):
        raise AcceptanceError("JSON 根節點必須是物件：{}".format(path))
    return value


def snapshot_data_root(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise AcceptanceError("Data Root 不存在：{}".format(root))
    records = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        record = {
            "path": relative,
            "mode": stat.S_IMODE(info.st_mode),
            "mtime_ns": info.st_mtime_ns,
            "size": info.st_size,
        }
        if stat.S_ISDIR(info.st_mode):
            record["type"] = "directory"
        elif stat.S_ISREG(info.st_mode):
            record["type"] = "file"
            record["sha256"] = sha256_bytes(path.read_bytes())
        elif stat.S_ISLNK(info.st_mode):
            record["type"] = "symlink"
            record["target"] = os.readlink(path)
        else:
            raise AcceptanceError("Data Root 含不允許的 special file：{}".format(relative))
        records.append(record)
    index_rows = []
    if (root / "runs" / "index.db").is_file():
        try:
            index_rows = query_runs(root)
        except (RunIndexError, OSError, ValueError, TypeError) as exc:
            raise AcceptanceError("無法保存 run index logical rows") from exc
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "data-root-snapshot",
        "created_at_utc": utc_now(),
        "root": str(root),
        "records": records,
        "index_rows": index_rows,
    }


def _stable_identity(record):
    """Compare content identity, not directory/append-only log timestamps."""
    kind = record.get("type")
    if kind == "directory":
        return (kind, record.get("mode"))
    if kind == "file":
        return (kind, record.get("mode"), record.get("size"), record.get("sha256"))
    if kind == "symlink":
        return (kind, record.get("mode"), record.get("target"))
    return (kind,)


def _is_current_run_path(path, run_id):
    try:
        stamp = datetime.strptime(run_id.split("-", 1)[0], "%Y%m%dT%H%M%SZ")
    except (ValueError, IndexError):
        raise AcceptanceError("run_id 必須含有效 UTC timestamp")
    # RunStore's deployment timezone is fixed Asia/Taipei (UTC+8). Avoid a
    # host tzdata dependency in this standalone acceptance tool.
    local = stamp + timedelta(hours=8)
    date_root = "runs/{}/".format(local.strftime("%Y-%m-%d"))
    relative = path[len(date_root):] if path.startswith(date_root) else None
    if relative is None:
        return path in ("runs", date_root.rstrip("/"))
    first = relative.split("/", 1)[0]
    digest = run_dir_hash(run_id)
    if first == ".{}-{}.run-claim".format(local.strftime("%H%M"), digest):
        return "/" not in relative
    return first.startswith(local.strftime("%H%M") + "-") and first.endswith("-" + digest)


def compare_snapshots(before, after, run_id):
    if not run_id or Path(run_id).name != run_id:
        raise AcceptanceError("run_id 必須是精確單一名稱")
    old = {item["path"]: item for item in before.get("records", [])}
    new = {item["path"]: item for item in after.get("records", [])}
    removed = sorted(set(old) - set(new))
    changed = sorted(
        path for path in set(old) & set(new)
        if _stable_identity(old[path]) != _stable_identity(new[path])
    )
    added = sorted(set(new) - set(old))
    allowed_change = {"runs/index.db", "runs/latest.json"}
    historical_changed = [
        path for path in removed + changed
        if path.startswith("runs/")
        and path not in allowed_change
        and not _is_current_run_path(path, run_id)
    ]
    log_problems = _validate_log_changes(before, after, removed, changed, added)
    unexpected_changed = [path for path in changed if path not in allowed_change and path not in ("logs/webapp.jsonl", "logs/launch.log")]
    allowed_prefixes = (
        "runs/", "inbox/{}/".format(run_id), "logs/launch-handshakes/",
    )
    unexpected_added = [
        path for path in added
        if not path.startswith(allowed_prefixes)
        and path not in allowed_change
        and path not in ("logs/webapp.jsonl", "logs/launch.log")
        and not _is_rotated_webapp_log(path)
        and path != "inbox/{}".format(run_id)
        and path not in ("runs", "inbox", "logs", "logs/launch-handshakes")
    ]
    wrong_new_run = [
        path for path in added
        if path.startswith("runs/")
        and path not in allowed_change
        and not _is_current_run_path(path, run_id)
    ]
    special = [path for path in added if new[path].get("type") not in ("file", "directory")]
    pointer_problems = _validate_run_pointers(
        after, run_id, set(changed) | set(added)
    )
    index_problems = _validate_index_rows(before, after, run_id)
    problems = (
        removed
        + historical_changed
        + unexpected_changed
        + unexpected_added
        + wrong_new_run
        + special
        + pointer_problems
        + log_problems
        + index_problems
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "data-root-diff",
        "status": "PASS" if not problems else "FAIL",
        "run_id": run_id,
        "removed": removed,
        "changed": changed,
        "added": added,
        "problems": sorted(set(problems)),
    }


def _is_rotated_webapp_log(path):
    return re.fullmatch(r"logs/webapp-\d{4}-\d{2}-\d{2}\.jsonl", path) is not None


def _prefix_matches(root, relative, size, digest):
    try:
        path = Path(root).resolve() / relative
        with path.open("rb") as handle:
            prefix = handle.read(size)
        return len(prefix) == size and sha256_bytes(prefix) == digest
    except (OSError, TypeError, ValueError):
        return False


def _validate_log_changes(before, after, removed, changed, added):
    root = after.get("root")
    old = {item["path"]: item for item in before.get("records", [])}
    rotated = [path for path in added if _is_rotated_webapp_log(path)]
    problems = []
    for path in changed:
        if path == "logs/launch.log":
            record = old[path]
            if not _prefix_matches(root, path, record.get("size"), record.get("sha256")):
                problems.append(path)
        elif path == "logs/webapp.jsonl":
            record = old[path]
            targets = rotated or [path]
            matches = [
                target for target in targets
                if _prefix_matches(root, target, record.get("size"), record.get("sha256"))
            ]
            if len(matches) != 1:
                problems.append(path)
    for path in rotated:
        record = old.get("logs/webapp.jsonl")
        if record is None or not _prefix_matches(
            root, path, record.get("size"), record.get("sha256")
        ):
            problems.append(path)
    return problems


def _row_map(snapshot):
    rows = snapshot.get("index_rows")
    if not isinstance(rows, list):
        raise AcceptanceError("snapshot 缺少 index_rows")
    result = {}
    for row in rows:
        run_id = row.get("run_id") if isinstance(row, dict) else None
        if not isinstance(run_id, str) or not run_id or run_id in result:
            raise AcceptanceError("index_rows 含無效或重複 run_id")
        result[run_id] = row
    return result


def _validate_index_rows(before, after, run_id):
    try:
        old = _row_map(before)
        new = _row_map(after)
    except AcceptanceError:
        return ["runs/index.db:logical_rows"]
    if any(new.get(key) != row for key, row in old.items()):
        return ["runs/index.db:historical_rows"]
    additions = set(new) - set(old)
    if additions not in (set(), {run_id}):
        return ["runs/index.db:new_rows"]
    return []


def _validate_run_pointers(after, run_id, touched):
    pointer_paths = {"runs/latest.json", "runs/index.db"} & touched
    if not pointer_paths:
        return []
    root_value = after.get("root")
    if not isinstance(root_value, str) or not root_value:
        return sorted(pointer_paths)
    root = Path(root_value).resolve()
    problems = []
    if "runs/latest.json" in pointer_paths:
        try:
            latest = read_json(root / "runs" / "latest.json")
            run_dir = resolve_run_dir(root, run_id)
            recorded = Path(latest.get("run_dir", "")).resolve()
            if latest.get("run_id") != run_id or run_dir is None or recorded != run_dir.resolve():
                problems.append("runs/latest.json")
        except (AcceptanceError, OSError, ValueError, TypeError):
            problems.append("runs/latest.json")
    if "runs/index.db" in pointer_paths:
        try:
            matches = [row for row in query_runs(root) if row.get("run_id") == run_id]
            if len(matches) != 1:
                problems.append("runs/index.db")
        except (RunIndexError, OSError, ValueError, TypeError):
            problems.append("runs/index.db")
    return problems


def resolve_provider(provider, provider_bin_dir=None):
    command = PROVIDER_COMMAND[provider]
    if provider_bin_dir:
        candidate = Path(provider_bin_dir).expanduser().resolve() / command
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return _require_wsl_provider(candidate)
        raise AcceptanceError("Linux provider CLI 不存在：{}".format(candidate))
    candidate = shutil.which(command)
    if not candidate:
        local = Path.home() / ".local" / "bin" / command
        candidate = str(local) if local.is_file() and os.access(local, os.X_OK) else None
    if not candidate:
        raise AcceptanceError("WSL PATH 找不到 provider CLI：{}".format(command))
    return _require_wsl_provider(candidate)


def _require_wsl_provider(candidate):
    resolved = Path(candidate).expanduser().resolve()
    if str(resolved).startswith("/mnt/"):
        raise AcceptanceError("拒絕 Windows mount provider CLI：{}".format(resolved))
    return resolved


def resolve_git_dir(code_root):
    dot_git = Path(code_root).resolve() / ".git"
    if dot_git.is_dir():
        return dot_git.resolve()
    try:
        marker = dot_git.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise AcceptanceError("找不到 .git directory 或 worktree pointer") from exc
    if not marker.startswith("gitdir:"):
        raise AcceptanceError("無效 .git worktree pointer")
    value = marker.split(":", 1)[1].strip()
    windows = re.fullmatch(r"([A-Za-z]):[\\/](.*)", value)
    if windows:
        drive, tail = windows.groups()
        value = "/mnt/{}/{}".format(drive.lower(), tail.replace("\\", "/"))
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = dot_git.parent / candidate
    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise AcceptanceError("worktree git-dir 不存在：{}".format(resolved))
    return resolved


def process_audit():
    providers = []
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8")
            exe = (entry / "exe").resolve()
        except (OSError, UnicodeError):
            continue
        lowered = raw.lower()
        if any(token in lowered for token in ("codex exec", "claude -p", "agy --print")):
            providers.append({"pid": int(entry.name), "executable": str(exe)})
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "wsl-process-audit",
        "status": "PASS" if not providers else "FAIL",
        "providers": providers,
    }


def run_canary(provider, scratch_root, provider_bin_dir=None):
    scratch_root = Path(scratch_root).resolve()
    scratch_root.mkdir(parents=True, exist_ok=False)
    executable = resolve_provider(provider, provider_bin_dir)
    runner = TerminatingRunner()
    started = time.monotonic()
    result = None
    status = "FAIL"
    safe = {
        "schema_version": SCHEMA_VERSION,
        "kind": "provider-canary",
        "provider": provider,
        "executable_sha256": sha256_bytes(executable.read_bytes()),
        "started_at_utc": utc_now(),
    }
    try:
        if provider == "codex":
            result = CodexExecAdapter(cli_path=executable, runner=runner, timeout_seconds=120).invoke(
                CANARY_PROMPT, CANARY_SCHEMA, scratch_root / "attempt", allow_search=True
            )
            safe.update({
                "model": "gpt-5.6-sol",
                "structured_output_sha256": sha256_bytes(json.dumps(result.structured_output, sort_keys=True).encode()),
                "search_invocations": result.search_invocations,
                "search_parse_status": result.search_parse_status,
                "malformed_event_count": result.malformed_event_count,
                "search_activity_count": result.search_activity_count,
            })
            status = "PASS" if result.structured_output == {"answer": "ready"} and result.search_invocations == 1 else "FAIL"
        elif provider == "claude":
            report = run_claude_preflight(
                seats=3, cli_path=executable, code_root=ROOT,
                data_root=scratch_root, runner=runner,
            )
            safe.update({
                "ready": report.get("ready") if isinstance(report, dict) else getattr(report, "ready", False),
                "report_sha256": sha256_bytes(json.dumps(report, default=str, sort_keys=True).encode()),
            })
            status = "PASS" if safe["ready"] else "FAIL"
        else:
            result = AntigravityAdapter(
                cli_path=executable, code_root=ROOT, data_root=scratch_root,
                runner=runner.run_process, timeout_seconds=120,
            ).preflight(scratch_root / "attempt")
            safe.update({
                "ready": result.ready,
                "version": result.version,
                "requested_model": result.requested_model,
                "actual_model": result.actual_model,
                "schema_sha256": result.schema_sha256,
                "search_succeeded": result.search_succeeded,
            })
            status = "PASS" if result.ready and result.search_succeeded else "FAIL"
    except Exception as exc:
        safe["failure_type"] = type(exc).__name__
        status = "FAIL"
    safe["status"] = status
    safe["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    safe["finished_at_utc"] = utc_now()
    shutil.rmtree(scratch_root, ignore_errors=True)
    if status != "PASS":
        raise AcceptanceError(json.dumps(safe, ensure_ascii=False, sort_keys=True))
    return safe


def http_json(url, data=None, timeout=10):
    body = urlencode(data).encode() if data is not None else None
    request = Request(url, data=body, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    except (URLError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("HTTP JSON 失敗：{}".format(url)) from exc


def poll_launch(base_url, token, timeout_seconds=60):
    if not token:
        raise AcceptanceError("launch token 不得為空")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status, payload = http_json(
            base_url.rstrip("/") + "/launch/status?token=" + token, timeout=5
        )
        if status == 404 or payload.get("status") == "unknown":
            raise AcceptanceError("unknown launch token")
        if payload.get("status") == "launched":
            run_id = payload.get("run_id")
            if not isinstance(run_id, str) or Path(run_id).name != run_id:
                raise AcceptanceError("launch status 未回精確 run_id")
            return {"status": "launched", "run_id": run_id}
        if payload.get("status") == "failed":
            raise AcceptanceError("launch failed")
        time.sleep(0.25)
    raise AcceptanceError("launch status timeout")


def jsonl(path):
    try:
        return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("JSONL 無法讀取：{}".format(path)) from exc


def reconcile_run(data_root, run_id):
    run_dir = resolve_run_dir(Path(data_root), run_id)
    if run_dir is None:
        raise AcceptanceError("找不到 exact run")
    manifest = read_json(run_dir / "manifest.json")
    votes = read_json(run_dir / "votes.json")
    summary = read_json(run_dir / "diagnostics" / "launch-summary.json")
    events = jsonl(run_dir / "events.jsonl")
    evidence = jsonl(run_dir / "evidence.jsonl")
    debate = jsonl(run_dir / "debate.jsonl")
    vote_rows = votes.get("votes") if isinstance(votes.get("votes"), list) else []
    vote_seats = [row.get("seat_id") for row in vote_rows]
    summary_rows = summary.get("seats") if isinstance(summary.get("seats"), list) else []
    adopted = {row.get("seat_id"): row.get("adopted_attempt_id") for row in summary_rows}
    terminal = [event for event in events if event.get("event") == "attempt_outcome_recorded"]
    opening_results = [event for event in events if event.get("event") == "opening_result_received"]
    opening_lineage = [event for event in events if event.get("event") == "opening_invocation_lineage_recorded"]
    artifact_problems = []
    for name, record in (manifest.get("artifacts") or {}).items():
        path = run_dir / name
        if not path.is_file() or sha256_bytes(path.read_bytes()) != record.get("sha256"):
            artifact_problems.append(name)
    problems = []
    if (
        manifest.get("provider_mode") != "real-subscription-fast"
        or manifest.get("competition_ready") is not False
        or not isinstance(manifest.get("competition_timeline"), dict)
    ):
        problems.append("run_contract_not_release_fast")
    if vote_seats != list(SEAT_IDS) or votes.get("valid_vote_count") != 7:
        problems.append("votes_not_7_of_7")
    if any(not row.get("final_stance") or row.get("state") != "valid" for row in vote_rows):
        problems.append("invalid_final_vote")
    if len(summary_rows) != 7 or set(adopted) != set(SEAT_IDS) or any(not value for value in adopted.values()):
        problems.append("adoption_lineage_incomplete")
    manifest_rows = manifest.get("seats") if isinstance(manifest.get("seats"), list) else []
    manifest_by_seat = _exact_seat_map(manifest_rows)
    votes_by_seat = _exact_seat_map(vote_rows)
    summary_by_seat = _exact_seat_map(summary_rows)
    if None in (manifest_by_seat, votes_by_seat, summary_by_seat):
        problems.append("seat_rows_not_exact")
    if {row.get("seat_id") for row in evidence} != set(SEAT_IDS):
        problems.append("evidence_not_7_seats")
    if {row.get("seat_id") for row in debate if row.get("seat_id")} != set(SEAT_IDS):
        problems.append("debate_not_7_seats")
    if not terminal or not opening_results or not opening_lineage:
        problems.append("terminal_or_opening_events_missing")
    if None not in (manifest_by_seat, votes_by_seat, summary_by_seat):
        for seat_id in SEAT_IDS:
            summary_row = summary_by_seat[seat_id]
            vote_attempts = votes_by_seat[seat_id].get("attempt_ids")
            manifest_attempts = manifest_by_seat[seat_id].get("attempt_ids")
            adopted_id = summary_row.get("adopted_attempt_id")
            if not _lineage_is_bound(vote_attempts, manifest_attempts, summary_row):
                problems.append("attempt_lineage_cross_bound")
                break
            evidence_attempts = {row.get("attempt_id") for row in evidence if row.get("seat_id") == seat_id}
            if evidence_attempts != {adopted_id}:
                problems.append("evidence_attempt_cross_bound")
                break
            try:
                pointer = read_json(run_dir / "agents" / seat_id / "adopted.json")
            except AcceptanceError:
                problems.append("adopted_pointer_missing")
                break
            if pointer != {
                "run_id": run_id,
                "seat_id": seat_id,
                "attempt_id": adopted_id,
                "validated_path": "agents/{}/attempts/{}/validated.json".format(seat_id, adopted_id),
            }:
                problems.append("adopted_pointer_cross_bound")
                break
            for event_rows, label in ((opening_results, "opening_result_cross_bound"), (opening_lineage, "opening_lineage_cross_bound")):
                matches = [row for row in event_rows if row.get("seat_id") == seat_id and row.get("research_attempt_id") == adopted_id]
                if len(matches) != 1:
                    problems.append(label)
                    break
            positions = [row for row in debate if row.get("seat_id") == seat_id and row.get("kind") == "position"]
            if len(positions) != 1 or _position_research_attempt(positions[0]) != adopted_id:
                problems.append("opening_public_lineage_cross_bound")
                break
        expected_outcomes = {
            (seat_id, row.get("attempt_id"), row.get("terminal_outcome"))
            for seat_id, summary_row in summary_by_seat.items()
            for row in summary_row.get("attempts", ())
        }
        actual_outcomes = {
            (row.get("seat_id"), row.get("attempt_id"), row.get("terminal_outcome"))
            for row in terminal
        }
        if expected_outcomes != actual_outcomes or len(terminal) != len(expected_outcomes):
            problems.append("terminal_outcomes_cross_bound")
    if artifact_problems:
        problems.append("artifact_hash_mismatch")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "run-reconciliation",
        "status": "PASS" if not problems else "FAIL",
        "run_id": run_id,
        "vote_seats": vote_seats,
        "valid_vote_count": votes.get("valid_vote_count"),
        "adopted_attempts": adopted,
        "terminal_outcome_events": len(terminal),
        "opening_events": len(opening_results),
        "artifact_problems": artifact_problems,
        "problems": problems,
    }


def _position_research_attempt(entry):
    """A debate entry carries its research lineage inside the sealed content."""
    content = entry.get("content")
    if not isinstance(content, dict):
        return None
    return content.get("research_attempt_id")


def _attempt_id_list(value):
    """A lineage list is a non-empty list of non-empty attempt id strings."""
    if not isinstance(value, list) or not value:
        return None
    if any(not isinstance(item, str) or not item for item in value):
        return None
    return value


def _summary_attempt_ids(summary_row):
    """Return the seat's research attempt ids only when the summary agrees itself.

    ``launch-summary`` states the same lineage twice: the ``attempt_ids`` list and
    the ``attempts`` records. They must agree in order, be unique and complete.
    """
    attempts = summary_row.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return None
    if any(not isinstance(row, dict) for row in attempts):
        return None
    attempt_ids = _attempt_id_list([row.get("attempt_id") for row in attempts])
    if attempt_ids is None or len(set(attempt_ids)) != len(attempt_ids):
        return None
    if summary_row.get("attempt_ids") != attempt_ids:
        return None
    return attempt_ids


def _lineage_is_bound(vote_attempts, manifest_attempts, summary_row):
    """Check each lineage inside its own namespace; never across namespaces.

    ``launch-summary`` holds the research lineage: every attempt of the seat,
    primary plus backup, cancelled ones included. ``votes.json`` /
    ``manifest.json`` hold the debate speech lineage, which legitimately starts
    at ``seat-a1`` even when the adopted research attempt is the backup. Comparing
    ids across the two would reject a valid backup-adopted run, so the adoption is
    bound instead by the research artifacts themselves: evidence rows, the adopted
    pointer, the opening terminal events and the sealed opening content.
    """
    if _attempt_id_list(vote_attempts) is None or vote_attempts != manifest_attempts:
        return False
    if summary_row.get("adopted") is not True:
        return False
    attempt_ids = _summary_attempt_ids(summary_row)
    if attempt_ids is None:
        return False
    outcomes = [row.get("terminal_outcome") for row in summary_row["attempts"]]
    if outcomes.count("adopted") != 1:
        return False
    if attempt_ids[outcomes.index("adopted")] != summary_row.get("adopted_attempt_id"):
        return False
    return all(
        outcome in NON_ADOPTED_TERMINAL_OUTCOMES
        for outcome in outcomes
        if outcome != "adopted"
    )


def _exact_seat_map(rows):
    result = {}
    for row in rows:
        seat_id = row.get("seat_id") if isinstance(row, dict) else None
        if seat_id not in SEAT_IDS or seat_id in result:
            return None
        result[seat_id] = row
    return result if set(result) == set(SEAT_IDS) else None


def parser():
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot-data-root")
    snap.add_argument("--data-root", required=True)
    snap.add_argument("--output", required=True)
    compare = commands.add_parser("compare-data-root")
    compare.add_argument("--before", required=True)
    compare.add_argument("--after", required=True)
    compare.add_argument("--run-id", required=True)
    compare.add_argument("--output", required=True)
    audit = commands.add_parser("audit-processes")
    audit.add_argument("--output", required=True)
    canary = commands.add_parser("canary")
    canary.add_argument("provider", choices=PROVIDERS)
    canary.add_argument("--scratch-root", required=True)
    canary.add_argument("--output", required=True)
    canary.add_argument("--provider-bin-dir")
    canary.add_argument("--execute-provider", action="store_true")
    poll = commands.add_parser("poll-launch")
    poll.add_argument("--base-url", default="http://127.0.0.1:8765")
    poll.add_argument("--token", required=True)
    poll.add_argument("--output", required=True)
    poll.add_argument("--timeout", type=float, default=60)
    reconcile = commands.add_parser("reconcile-run")
    reconcile.add_argument("--data-root", required=True)
    reconcile.add_argument("--run-id", required=True)
    reconcile.add_argument("--output", required=True)
    git_dir = commands.add_parser("resolve-git-dir")
    git_dir.add_argument("--code-root", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "snapshot-data-root":
            result = snapshot_data_root(args.data_root)
        elif args.command == "compare-data-root":
            result = compare_snapshots(read_json(args.before), read_json(args.after), args.run_id)
        elif args.command == "audit-processes":
            result = process_audit()
        elif args.command == "canary":
            if not args.execute_provider:
                raise AcceptanceError("真 provider canary 必須明確提供 --execute-provider")
            result = run_canary(args.provider, args.scratch_root, args.provider_bin_dir)
        elif args.command == "poll-launch":
            result = poll_launch(args.base_url, args.token, args.timeout)
        elif args.command == "reconcile-run":
            result = reconcile_run(args.data_root, args.run_id)
        else:
            print(str(resolve_git_dir(args.code_root)))
            return 0
        write_json(args.output, result)
        print(json.dumps({"status": result.get("status", "PASS"), "output": args.output}, ensure_ascii=False))
        return 0 if result.get("status") != "FAIL" else 1
    except AcceptanceError as exc:
        print("ACCEPTANCE_FAILED: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
