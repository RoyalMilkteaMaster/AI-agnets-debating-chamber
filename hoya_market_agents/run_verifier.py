"""Read-only verification of one immutable run bundle."""

import hashlib
import json
import re
from pathlib import Path

from .report_contract import ReportContractError, validate_market_report
from .report_renderer import render_market_html, render_market_markdown
from .seats import SEAT_IDS


REQUIRED_ARTIFACTS = (
    "manifest.json",
    "evidence.jsonl",
    "debate.jsonl",
    "votes.json",
    "report.md",
    "report.html",
)
_FORBIDDEN_HTML_DEPENDENCIES = (
    re.compile(r"<script\b", re.IGNORECASE),
    re.compile(r"<link\b", re.IGNORECASE),
    re.compile(r"\bsrc\s*=\s*[\"']https?://", re.IGNORECASE),
    re.compile(r"@import\b", re.IGNORECASE),
)


class RunVerificationError(ValueError):
    pass


def verify_run(data_root, run_id):
    """Verify paths, hashes, seven-seat lineage and offline report constraints."""
    if not isinstance(run_id, str) or Path(run_id).name != run_id or run_id in (".", ".."):
        raise RunVerificationError("run_id 不得包含路徑。")
    root = Path(data_root).resolve()
    run_dir = root / "runs" / run_id
    if not run_dir.is_dir() or not run_dir.resolve().is_relative_to(root):
        raise RunVerificationError("找不到 Data Root 內的 run：{}".format(run_id))

    manifest = _read_json(run_dir / "manifest.json")
    if manifest.get("run_id") != run_id:
        raise RunVerificationError("manifest run_id 不一致。")
    artifact_index = manifest.get("artifacts")
    if not isinstance(artifact_index, dict):
        raise RunVerificationError("manifest 缺少 artifact index。")

    digests = {}
    for name in REQUIRED_ARTIFACTS:
        path = run_dir / name
        _require_regular_file(path, run_dir)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        digests[name] = digest
        if name == "manifest.json":
            continue
        expected = artifact_index.get(name)
        expected_digest = expected.get("sha256") if isinstance(expected, dict) else None
        if expected_digest != digest:
            raise RunVerificationError("artifact hash 不符：{}".format(name))

    for name, record in artifact_index.items():
        if not isinstance(name, str) or not isinstance(record, dict) or record.get("path") != name:
            raise RunVerificationError("artifact index 路徑不一致。")
        path = run_dir / name
        _require_regular_file(path, run_dir)
        if hashlib.sha256(path.read_bytes()).hexdigest() != record.get("sha256"):
            raise RunVerificationError("artifact index hash 不符：{}".format(name))

    seats = manifest.get("seats")
    if not isinstance(seats, list) or [seat.get("seat_id") for seat in seats] != list(SEAT_IDS):
        raise RunVerificationError("manifest 必須包含固定七席且順序一致。")

    evidence = _read_jsonl(run_dir / "evidence.jsonl")
    debate = _read_jsonl(run_dir / "debate.jsonl")
    votes = _read_json(run_dir / "votes.json")
    evidence_seats = {record.get("seat_id") for record in evidence}
    debate_seats = {record.get("seat_id") for record in debate if record.get("seat_id")}
    vote_records = votes.get("votes")
    if not isinstance(vote_records, list):
        raise RunVerificationError("votes.json 缺少 votes 陣列。")
    vote_seats = {record.get("seat_id") for record in vote_records}
    expected_seats = set(SEAT_IDS)
    if (
        evidence_seats != expected_seats
        or debate_seats != expected_seats
        or vote_seats != expected_seats
        or len(vote_records) != len(SEAT_IDS)
    ):
        raise RunVerificationError("evidence/debate/votes 無法回查固定七席。")
    _verify_vote_table(votes, vote_records)

    html = (run_dir / "report.html").read_text(encoding="utf-8")
    if any(pattern.search(html) for pattern in _FORBIDDEN_HTML_DEPENDENCIES):
        raise RunVerificationError("report.html 含 script 或外部 runtime dependency。")
    if "<html" not in html.lower() or "@media print" not in html.lower():
        raise RunVerificationError("report.html 缺少離線 HTML 或列印樣式。")

    timeline = manifest.get("competition_timeline")
    if timeline is not None:
        _verify_report_lineage(run_dir, evidence, debate, votes)
        _verify_competition_timeline(run_dir, manifest, votes, timeline)

    return {
        "schema_version": "1.0.0",
        "status": "VERIFIED",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "seat_count": len(seats),
        "required_artifacts": digests,
        "provider_mode": manifest.get("provider_mode"),
        "competition_ready": manifest.get("competition_ready") is True,
        "timeline": timeline,
    }


def _verify_competition_timeline(run_dir, manifest, votes, timeline):
    if not isinstance(timeline, dict):
        raise RunVerificationError("competition_timeline 必須為 object。")
    if timeline.get("all_seats_dispatched_at_ms") != 0:
        raise RunVerificationError("七席必須在 T+0 同時 dispatch。")
    completions = timeline.get("seat_completion_ms")
    if not isinstance(completions, dict) or set(completions) != set(SEAT_IDS):
        raise RunVerificationError("competition timeline 必須包含七席 completion。")
    if any(
        type(value) is not int or value < 0 or value > 285_000
        for value in completions.values()
    ):
        raise RunVerificationError("研究席未在 T+4:45 前完成有效 contract。")
    if timeline.get("evidence_snapshot_sealed_at_ms") != 300_000:
        raise RunVerificationError("Evidence snapshot 必須在 T+5 seal。")
    snapshot = run_dir / "snapshots" / "evidence.jsonl"
    _require_regular_file(snapshot, run_dir)
    snapshot_sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if timeline.get("evidence_snapshot_sha256") != snapshot_sha:
        raise RunVerificationError("T+5 snapshot hash 不一致。")
    if snapshot.read_bytes() != (run_dir / "evidence.jsonl").read_bytes():
        raise RunVerificationError("正式 evidence.jsonl 與 sealed snapshot 不一致。")

    stop_reason = timeline.get("debate_stop_reason")
    if stop_reason not in {
        "consensus_6_votes",
        "consensus_5_votes",
        "forced_stop_4_votes",
        "forced_stop_no_consensus",
        "forced_stop_insufficient_valid_votes",
    }:
        raise RunVerificationError("未知辯論停止原因。")
    stop_ms = timeline.get("debate_stop_at_ms")
    if type(stop_ms) is not int or not 300_000 <= stop_ms <= 600_000:
        raise RunVerificationError("辯論停止時間超出 T+5 至 T+10。")
    if votes.get("stop_reason") != stop_reason or votes.get("stop_elapsed_ms") != stop_ms:
        raise RunVerificationError("timeline 與 votes 停止紀錄不一致。")
    if manifest.get("tally") != votes.get("tally"):
        raise RunVerificationError("manifest 與 votes 票數不一致。")
    _verify_stop_semantics(votes, stop_reason, stop_ms)

    report_ms = timeline.get("report_completed_at_ms")
    if type(report_ms) is not int or report_ms >= 780_000:
        raise RunVerificationError("報告未在 T+13 前完成。")
    if report_ms - stop_ms > 180_000:
        raise RunVerificationError("Core 報告超過共識後三分鐘。")
    if timeline.get("report_hard_deadline_ms") != 780_000:
        raise RunVerificationError("report hard deadline 不是 T+13。")
    if manifest.get("elapsed_ms") != report_ms:
        raise RunVerificationError("manifest elapsed_ms 與 report timeline 不一致。")


def _verify_vote_table(votes, vote_records):
    tally = votes.get("tally")
    if not isinstance(tally, dict) or not tally:
        raise RunVerificationError("votes tally 必須為非空 object。")
    if any(type(count) is not int or count < 0 for count in tally.values()):
        raise RunVerificationError("votes tally 含無效票數。")
    recomputed = {stance: 0 for stance in tally}
    modern = "valid_vote_count" in votes
    valid_count = 0
    for record in vote_records:
        if not isinstance(record, dict):
            raise RunVerificationError("votes 含無效席位紀錄。")
        if modern and record.get("state") != "valid":
            continue
        stance = record.get("final_stance") if modern else record.get("stance")
        if stance not in recomputed:
            raise RunVerificationError("有效票立場不在 tally。")
        recomputed[stance] += 1
        valid_count += 1
    count_matches = votes.get("valid_vote_count") == valid_count if modern else True
    if not count_matches or tally != recomputed:
        raise RunVerificationError("votes tally 與逐席有效票不一致。")


def _verify_stop_semantics(votes, stop_reason, stop_ms):
    tally = votes["tally"]
    leader_count = max(tally.values())
    adopted = votes.get("adopted_stance")
    adopted_count = tally.get(adopted, 0)
    threshold = votes.get("threshold_required")
    status = votes.get("consensus_status")
    valid_count = votes.get("valid_vote_count")
    challenge_completed = votes.get("challenge_completed") is True

    if stop_reason == "consensus_6_votes":
        valid = stop_ms < 420_000 and threshold == 6 and adopted_count >= 6
    elif stop_reason == "consensus_5_votes":
        valid = 420_000 <= stop_ms < 600_000 and threshold == 5 and adopted_count >= 5
    elif stop_reason == "forced_stop_4_votes":
        valid = stop_ms == 600_000 and threshold == 4 and adopted_count >= 4
    elif stop_reason == "forced_stop_no_consensus":
        valid = (
            stop_ms == 600_000
            and threshold == 4
            and valid_count >= 4
            and leader_count < 4
            and adopted is None
            and status == "no_consensus"
        )
        if not valid:
            raise RunVerificationError("T+10 無共識停止語意與票數不一致。")
        return
    else:
        valid = (
            stop_ms == 600_000
            and threshold == 4
            and valid_count < 4
            and adopted is None
            and status == "failed_insufficient_valid_votes"
        )
        if not valid:
            raise RunVerificationError("T+10 有效票不足停止語意不一致。")
        return
    if not (valid and status == "consensus" and challenge_completed):
        raise RunVerificationError("辯論門檻、停止時間或採用立場與票數不一致。")


def _verify_report_lineage(run_dir, evidence, debate, votes):
    report_path = run_dir / "report.json"
    _require_regular_file(report_path, run_dir)
    report = _read_json(report_path)
    try:
        validate_market_report(
            report,
            {
                "evidence": evidence,
                "debate": [entry for entry in debate if entry.get("seat_id")],
                "votes": votes,
            },
        )
    except ReportContractError as exc:
        raise RunVerificationError("report.json 無法回查正式 artifacts：{}".format(exc)) from exc
    expected_markdown = render_market_markdown(report).encode("utf-8")
    expected_html = render_market_html(report).encode("utf-8")
    if (run_dir / "report.md").read_bytes() != expected_markdown:
        raise RunVerificationError("report.md 不是由正式 report.json 產生。")
    if (run_dir / "report.html").read_bytes() != expected_html:
        raise RunVerificationError("report.html 不是由正式 report.json 產生。")


def _require_regular_file(path, run_dir):
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(run_dir.resolve()):
        raise RunVerificationError("缺少或不安全的 artifact：{}".format(path.name))


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunVerificationError("無法解析 {}。".format(path.name)) from exc
    if not isinstance(value, dict):
        raise RunVerificationError("{} 必須為 JSON object。".format(path.name))
    return value


def _read_jsonl(path):
    try:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError) as exc:
        raise RunVerificationError("無法解析 {}。".format(path.name)) from exc
    if any(not isinstance(record, dict) for record in records):
        raise RunVerificationError("{} 必須是一行一個 JSON object。".format(path.name))
    return records
