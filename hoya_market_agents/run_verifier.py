"""Read-only verification of one immutable run bundle."""

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from .report_contract import ReportContractError, validate_market_report
from .report_renderer import render_market_html, render_market_markdown
from .seats import SEAT_IDS
from .system_preflight import (
    REQUIRED_CHECK_IDS,
    RUN_SCOPED_CHECK_IDS,
    load_frozen_roster,
)


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
_REAL_RECEIPT_FIELDS = {
    "schema_version",
    "receipt_id",
    "system_preflight_id",
    "run_id",
    "competition_challenge",
    "seat_id",
    "attempt_id",
    "provider",
    "target_model",
    "actual_model",
    "dispatch",
    "completion",
    "search_receipt_path",
    "search_receipt_sha256",
    "raw_transcript_path",
    "raw_transcript_sha256",
    "output_path",
    "output_sha256",
}
_PHASE_RECEIPT_FIELDS = {"receipt_id", "at_utc", "elapsed_ms"}
_SEARCH_RECEIPT_FIELDS = {
    "schema_version",
    "receipt_id",
    "run_id",
    "seat_id",
    "attempt_id",
    "provider",
    "competition_challenge",
    "tool",
    "succeeded",
    "completed_at_utc",
    "elapsed_ms",
}
_SEARCH_TOOLS = {
    "codex": "web_search",
    "claude": "WebSearch",
    "antigravity": "search_web",
}
_SHIPPED_FAKE_MARKERS = (
    "fake-competition-drill",
    "fake.invalid",
    "fake-source:",
    "fixture:",
    "fake drill",
    "deterministic fixture",
    "不得作為真實市場或訂閱 provider ready 證據",
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

    provider_mode = manifest.get("provider_mode")
    competition_ready = manifest.get("competition_ready") is True
    declares_real = provider_mode == "real-subscription" or competition_ready
    if declares_real and not (provider_mode == "real-subscription" and competition_ready):
        raise RunVerificationError("real provider mode 與 competition_ready 必須同時成立。")

    timeline = manifest.get("competition_timeline")
    if declares_real and not isinstance(timeline, dict):
        raise RunVerificationError("real competition run 缺少完整 timeline。")
    if timeline is not None:
        _verify_report_lineage(run_dir, evidence, debate, votes)
        _verify_competition_timeline(run_dir, manifest, votes, timeline)
    if declares_real:
        authorization = _verify_real_provider_lineage(root, manifest)
        receipt_payloads = _verify_real_run_receipts(
            run_dir,
            manifest,
            artifact_index,
            timeline,
            authorization,
        )
        _reject_shipped_fake_markers(
            manifest,
            evidence,
            debate,
            _read_json(run_dir / "report.json"),
            receipt_payloads,
        )

    return {
        "schema_version": "1.0.0",
        "status": "VERIFIED",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "seat_count": len(seats),
        "required_artifacts": digests,
        "provider_mode": provider_mode,
        "competition_ready": competition_ready,
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


def _verify_real_provider_lineage(data_root, manifest):
    try:
        roster = load_frozen_roster()
    except ValueError as exc:
        raise RunVerificationError("frozen roster 無法驗證。") from exc
    seats = manifest["seats"]
    expected_by_id = {seat["seat_id"]: seat for seat in roster["seats"]}
    for seat in seats:
        expected = expected_by_id[seat["seat_id"]]
        if (
            seat.get("provider") != expected["provider"]
            or seat.get("target_model") != expected["target_model"]
            or not _actual_model_matches(expected, seat.get("actual_model"))
        ):
            raise RunVerificationError(
                "real run 席位 {} 的 provider/target/actual model 不符。".format(
                    seat["seat_id"]
                )
            )

    lineage = manifest.get("provider_preflight_lineage")
    required = {"system_preflight_id", "manifest_path", "sha256"}
    if not isinstance(lineage, dict) or set(lineage) != required:
        raise RunVerificationError("real run 缺少可稽核 provider preflight lineage。")
    preflight_id = lineage["system_preflight_id"]
    if (
        not isinstance(preflight_id, str)
        or not preflight_id
        or preflight_id in (".", "..")
        or Path(preflight_id).name != preflight_id
    ):
        raise RunVerificationError("provider preflight ID 不安全。")
    expected_relative = "preflight/{}/manifest.json".format(preflight_id)
    if lineage.get("manifest_path") != expected_relative:
        raise RunVerificationError("provider preflight manifest path 不一致。")
    path = data_root / expected_relative
    _require_regular_file(path, data_root)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != lineage.get("sha256"):
        raise RunVerificationError("provider preflight manifest hash 不一致。")
    preflight = _read_json(path)
    if preflight.get("mode") != "real" or preflight.get("provider_capabilities_ready") is not True:
        raise RunVerificationError("provider preflight 未證明真實七席能力。")
    checks = preflight.get("checks")
    if (
        not isinstance(checks, list)
        or [item.get("check_id") for item in checks if isinstance(item, dict)]
        != list(REQUIRED_CHECK_IDS)
    ):
        raise RunVerificationError("provider preflight checks 不完整或順序不符。")
    by_check = {item["check_id"]: item for item in checks}
    failed = [check_id for check_id in REQUIRED_CHECK_IDS if by_check[check_id].get("ok") is not True]
    if preflight.get("blockers") != failed:
        raise RunVerificationError("provider preflight blockers 與 checks 不一致。")
    expected_ready = not failed
    if (
        preflight.get("ready") is not expected_ready
        or preflight.get("status") != ("READY" if expected_ready else "NOT_READY")
    ):
        raise RunVerificationError("provider preflight READY 狀態與 blockers 不一致。")
    failed_provider_checks = set(failed) - RUN_SCOPED_CHECK_IDS
    if failed_provider_checks:
        raise RunVerificationError("provider preflight 仍有 capability blocker。")
    preflight_generated = _parse_utc(preflight.get("generated_at_utc"), "provider preflight time")
    run_started = _parse_utc(manifest.get("started_at_utc"), "run start time")
    if preflight_generated > run_started:
        raise RunVerificationError("provider preflight 不得晚於 competition run。")
    matrix_by_seat = _verify_provider_matrix(preflight.get("provider_matrix"), roster)
    for seat in seats:
        if matrix_by_seat[seat["seat_id"]].get("actual_model") != seat.get("actual_model"):
            raise RunVerificationError(
                "real run 席位 {} actual model 與 preflight 不一致。".format(
                    seat["seat_id"]
                )
            )
    return _verify_competition_authorization(
        data_root,
        manifest,
        preflight_id,
        preflight,
        preflight_generated,
        run_started,
    )


def _verify_competition_authorization(
    data_root,
    run_manifest,
    preflight_id,
    preflight,
    preflight_generated,
    run_started,
):
    lineage = preflight.get("competition_authorization")
    required = {
        "status",
        "authorized_run_id",
        "competition_challenge_sha256",
        "path",
        "sha256",
    }
    if not isinstance(lineage, dict) or set(lineage) != required:
        raise RunVerificationError("provider preflight 缺少 competition authorization lineage。")
    run_id = run_manifest["run_id"]
    expected_path = "preflight/{}/competition-authorization.json".format(preflight_id)
    if (
        lineage.get("status") != "AUTHORIZED"
        or lineage.get("authorized_run_id") != run_id
        or lineage.get("path") != expected_path
    ):
        raise RunVerificationError("competition authorization 未綁定本次 run。")
    path = data_root / expected_path
    _require_regular_file(path, data_root)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != lineage.get("sha256"):
        raise RunVerificationError("competition authorization hash 不一致。")
    authorization = _read_json(path)
    expected_fields = {
        "schema_version",
        "status",
        "system_preflight_id",
        "authorized_run_id",
        "competition_challenge",
        "issued_at_utc",
    }
    if not isinstance(authorization, dict) or set(authorization) != expected_fields:
        raise RunVerificationError("competition authorization contract 不完整。")
    challenge = authorization.get("competition_challenge")
    if (
        authorization.get("schema_version") != "1.0.0"
        or authorization.get("status") != "AUTHORIZED"
        or authorization.get("system_preflight_id") != preflight_id
        or authorization.get("authorized_run_id") != run_id
        or not _valid_challenge(challenge)
        or hashlib.sha256(challenge.encode("utf-8")).hexdigest()
        != lineage.get("competition_challenge_sha256")
    ):
        raise RunVerificationError("competition authorization identity/challenge 不符。")
    issued = _parse_utc(authorization.get("issued_at_utc"), "authorization issued time")
    if issued != preflight_generated or issued > run_started:
        raise RunVerificationError("competition authorization 時間與 preflight/run 不一致。")

    matching_authorizations = 0
    preflight_root = data_root / "preflight"
    for candidate in preflight_root.glob("*/competition-authorization.json"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            candidate_value = _read_json(candidate)
        except RunVerificationError:
            continue
        if candidate_value.get("authorized_run_id") == run_id:
            matching_authorizations += 1
    if matching_authorizations != 1:
        raise RunVerificationError("competition run_id 必須只有一份 preflight authorization。")
    return {
        "system_preflight_id": preflight_id,
        "run_id": run_id,
        "competition_challenge": challenge,
    }


def _verify_provider_matrix(matrix, roster):
    if not isinstance(matrix, list) or len(matrix) != 8:
        raise RunVerificationError("provider preflight matrix 必須包含 Core 加七席。")
    by_seat = {}
    for row in matrix:
        if not isinstance(row, dict) or not isinstance(row.get("seat_id"), str):
            raise RunVerificationError("provider matrix row 無效。")
        if row["seat_id"] in by_seat:
            raise RunVerificationError("provider matrix 席位重複。")
        by_seat[row["seat_id"]] = row
    core = by_seat.pop("core", None)
    if (
        core is None
        or core.get("provider") != "codex"
        or core.get("target_model") != "gpt-5.6-sol"
        or core.get("actual_model") != "gpt-5.6-sol"
    ):
        raise RunVerificationError("provider matrix Core model 不符。")
    expected_seats = {seat["seat_id"]: seat for seat in roster["seats"]}
    if set(by_seat) != set(expected_seats):
        raise RunVerificationError("provider matrix 未完整保留固定七席。")
    for seat_id, expected in expected_seats.items():
        row = by_seat[seat_id]
        if (
            row.get("provider") != expected["provider"]
            or row.get("target_model") != expected["target_model"]
            or not _actual_model_matches(expected, row.get("actual_model"))
        ):
            raise RunVerificationError("provider matrix {} model 不符。".format(seat_id))
    return by_seat


def _verify_real_run_receipts(run_dir, manifest, artifact_index, timeline, authorization):
    lineage = manifest.get("provider_receipts")
    if (
        not isinstance(lineage, list)
        or len(lineage) != len(SEAT_IDS)
        or [item.get("seat_id") for item in lineage if isinstance(item, dict)]
        != list(SEAT_IDS)
    ):
        raise RunVerificationError("real run 必須提供固定七席 provider receipt lineage。")
    seats = {seat["seat_id"]: seat for seat in manifest["seats"]}
    started = _parse_utc(manifest.get("started_at_utc"), "run start time")
    unique_ids = {
        name: set()
        for name in ("attempt", "receipt", "dispatch", "completion", "search")
    }
    unique_paths = set()
    payloads = []

    for item in lineage:
        if set(item) != {"seat_id", "path", "sha256"}:
            raise RunVerificationError("provider receipt lineage 欄位不符。")
        seat_id = item["seat_id"]
        expected_receipt_path = "provider-receipts/{}.json".format(seat_id)
        if item.get("path") != expected_receipt_path:
            raise RunVerificationError("{} provider receipt path 不一致。".format(seat_id))
        receipt_path = run_dir / expected_receipt_path
        _verify_indexed_artifact(
            receipt_path,
            run_dir,
            artifact_index,
            expected_receipt_path,
            item.get("sha256"),
        )
        receipt = _read_json(receipt_path)
        if set(receipt) != _REAL_RECEIPT_FIELDS or receipt.get("schema_version") != "1.0.0":
            raise RunVerificationError("{} provider receipt contract 不完整。".format(seat_id))
        seat = seats[seat_id]
        attempt_id = seat.get("attempt_id")
        if not _safe_segment(attempt_id):
            raise RunVerificationError("{} real run 缺少安全 attempt_id。".format(seat_id))
        _add_unique(unique_ids["attempt"], attempt_id, "real attempt_id")
        expected_values = {
            "system_preflight_id": authorization["system_preflight_id"],
            "run_id": authorization["run_id"],
            "competition_challenge": authorization["competition_challenge"],
            "seat_id": seat_id,
            "attempt_id": attempt_id,
            "provider": seat["provider"],
            "target_model": seat["target_model"],
            "actual_model": seat["actual_model"],
        }
        if any(receipt.get(field) != value for field, value in expected_values.items()):
            raise RunVerificationError("{} provider receipt 未綁定 run/seat/model/challenge。".format(seat_id))
        _add_unique(unique_ids["receipt"], receipt.get("receipt_id"), "provider receipt_id")
        _verify_phase_receipt(
            receipt.get("dispatch"),
            unique_ids["dispatch"],
            started,
            expected_elapsed=0,
            label="{} dispatch".format(seat_id),
        )
        completion_elapsed = timeline["seat_completion_ms"][seat_id]
        _verify_phase_receipt(
            receipt.get("completion"),
            unique_ids["completion"],
            started,
            expected_elapsed=completion_elapsed,
            label="{} completion".format(seat_id),
        )

        attempt_root = "agents/{}/attempts/{}/".format(seat_id, attempt_id)
        search = _verify_search_receipt(
            run_dir,
            artifact_index,
            receipt,
            expected_values,
            started,
            completion_elapsed,
            unique_ids["search"],
            attempt_root,
            unique_paths,
        )
        raw_text = _verify_receipt_payload(
            run_dir,
            artifact_index,
            receipt,
            "raw_transcript_path",
            "raw_transcript_sha256",
            attempt_root,
            unique_paths,
        )
        output_text = _verify_receipt_payload(
            run_dir,
            artifact_index,
            receipt,
            "output_path",
            "output_sha256",
            attempt_root,
            unique_paths,
        )
        payloads.extend((receipt, search, raw_text, output_text))
    return payloads


def _verify_phase_receipt(value, unique_ids, started, expected_elapsed, label):
    if not isinstance(value, dict) or set(value) != _PHASE_RECEIPT_FIELDS:
        raise RunVerificationError("{} receipt contract 不完整。".format(label))
    _add_unique(unique_ids, value.get("receipt_id"), "{} receipt_id".format(label))
    elapsed = value.get("elapsed_ms")
    if type(elapsed) is not int or elapsed != expected_elapsed:
        raise RunVerificationError("{} elapsed 與正式 timeline 不一致。".format(label))
    observed = _parse_utc(value.get("at_utc"), "{} time".format(label))
    if observed != started + timedelta(milliseconds=elapsed):
        raise RunVerificationError("{} timestamp 與 monotonic elapsed 不一致。".format(label))


def _verify_search_receipt(
    run_dir,
    artifact_index,
    receipt,
    expected_values,
    started,
    completion_elapsed,
    unique_ids,
    attempt_root,
    unique_paths,
):
    path_value = receipt.get("search_receipt_path")
    _require_attempt_artifact_path(path_value, attempt_root, "search receipt")
    if path_value in unique_paths:
        raise RunVerificationError("real receipt payload path 重複。")
    unique_paths.add(path_value)
    path = run_dir / path_value
    _verify_indexed_artifact(
        path,
        run_dir,
        artifact_index,
        path_value,
        receipt.get("search_receipt_sha256"),
    )
    search = _read_json(path)
    if set(search) != _SEARCH_RECEIPT_FIELDS or search.get("schema_version") != "1.0.0":
        raise RunVerificationError("search receipt contract 不完整。")
    for field in (
        "run_id",
        "seat_id",
        "attempt_id",
        "provider",
        "competition_challenge",
    ):
        if search.get(field) != expected_values[field]:
            raise RunVerificationError("search receipt 未綁定 {}。".format(field))
    _add_unique(unique_ids, search.get("receipt_id"), "search receipt_id")
    elapsed = search.get("elapsed_ms")
    if (
        search.get("succeeded") is not True
        or search.get("tool") != _SEARCH_TOOLS[expected_values["provider"]]
        or type(elapsed) is not int
        or not 0 <= elapsed <= completion_elapsed
    ):
        raise RunVerificationError("search receipt 未證明該席在截止前完成搜尋。")
    observed = _parse_utc(search.get("completed_at_utc"), "search completion time")
    if observed != started + timedelta(milliseconds=elapsed):
        raise RunVerificationError("search receipt timestamp 與 elapsed 不一致。")
    return search


def _verify_receipt_payload(
    run_dir,
    artifact_index,
    receipt,
    path_field,
    hash_field,
    attempt_root,
    unique_paths,
):
    path_value = receipt.get(path_field)
    _require_attempt_artifact_path(path_value, attempt_root, path_field)
    if path_value in unique_paths:
        raise RunVerificationError("real receipt payload path 重複。")
    unique_paths.add(path_value)
    path = run_dir / path_value
    _verify_indexed_artifact(
        path,
        run_dir,
        artifact_index,
        path_value,
        receipt.get(hash_field),
    )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise RunVerificationError("real receipt payload 必須為 UTF-8 公開內容。") from exc
    if not text.strip():
        raise RunVerificationError("real receipt payload 不得為空。")
    return text


def _require_attempt_artifact_path(path_value, attempt_root, label):
    if not isinstance(path_value, str) or "\\" in path_value:
        raise RunVerificationError("{} 不在該席 attempt 目錄。".format(label))
    path = PurePosixPath(path_value)
    root = PurePosixPath(attempt_root)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) <= len(root.parts)
        or path.parts[: len(root.parts)] != root.parts
    ):
        raise RunVerificationError("{} 不在該席 attempt 目錄。".format(label))


def _verify_indexed_artifact(path, run_dir, artifact_index, relative, expected_sha):
    _require_regular_file(path, run_dir)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    indexed = artifact_index.get(relative)
    if (
        not isinstance(indexed, dict)
        or indexed.get("path") != relative
        or indexed.get("sha256") != digest
        or expected_sha != digest
    ):
        raise RunVerificationError("run receipt artifact hash/index 不一致：{}".format(relative))


def _add_unique(values, value, label):
    if not isinstance(value, str) or not value or value in values:
        raise RunVerificationError("{} 缺少或重複。".format(label))
    values.add(value)


def _reject_shipped_fake_markers(*values):
    if any(_contains_fake_marker(value) for value in values):
        raise RunVerificationError("real competition bundle 含 shipped fake/fixture marker。")


def _contains_fake_marker(value):
    if isinstance(value, dict):
        return any(
            _contains_fake_marker(key) or _contains_fake_marker(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_fake_marker(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in _SHIPPED_FAKE_MARKERS)
    return False


def _valid_challenge(value):
    return (
        isinstance(value, str)
        and 24 <= len(value) <= 128
        and all(
            character.isascii() and (character.isalnum() or character in "-_")
            for character in value
        )
    )


def _safe_segment(value):
    return (
        isinstance(value, str)
        and bool(value)
        and value not in (".", "..")
        and Path(value).name == value
        and "/" not in value
        and "\\" not in value
    )


def _actual_model_matches(expected, actual_model):
    if not isinstance(actual_model, str):
        return False
    if expected["provider"] == "claude":
        lowered = actual_model.lower()
        return lowered == "opus" or lowered.startswith("claude-opus-")
    return actual_model == expected["target_model"]


def _parse_utc(value, label):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RunVerificationError("{} 必須為 UTC ISO-8601。".format(label))
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RunVerificationError("{} 必須為有效 UTC ISO-8601。".format(label)) from exc


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
