"""Fail-closed aggregate readiness manifest for the competition roster."""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from .seats import ROSTER_PATH, SEAT_IDS


REQUIRED_CHECK_IDS = (
    "cli_versions",
    "provider_login",
    "target_actual_models",
    "provider_runtime_attestation",
    "search",
    "research_snapshot",
    "code_data_boundary",
    "path_translation",
    "data_root_write",
    "disk_space",
    "renderer",
    "clock",
    "roster",
    "codex_runtime_receipts",
    "seven_seat_timeline",
    "report_deadline",
)
RUN_SCOPED_CHECK_IDS = frozenset(("search", "seven_seat_timeline", "report_deadline"))
NON_BLOCKING_CHECK_IDS = frozenset(("provider_runtime_attestation",))
PROVIDER_COUNTS = {"claude": 3, "codex": 3, "antigravity": 1}
EXPECTED_SEATS = {
    "spot-technical": ("codex", "gpt-5.6-sol", ["web_search"], ["research"]),
    "derivatives": ("codex", "gpt-5.6-sol", ["web_search"], ["research"]),
    "onchain": ("codex", "gpt-5.6-sol", ["web_search"], ["research"]),
    "official-events": ("claude", "opus", ["WebSearch", "WebFetch"], ["research"]),
    "news": ("claude", "opus", ["WebSearch", "WebFetch"], ["research"]),
    "social-macro": ("claude", "opus", ["WebSearch", "WebFetch"], ["research"]),
    "counter-evidence": (
        "antigravity",
        "gemini-3.1-pro-high",
        ["search_web"],
        ["research"],
    ),
}
COMPETITION_AUTHORIZATION_NAME = "competition-authorization.json"
READY_CERTIFICATE_NAME = "latest-ready.json"


class PreflightError(ValueError):
    pass


def load_frozen_roster(path=None):
    """Load the exact competition mapping; extra flexibility is unsafe here."""
    roster_path = Path(path or ROSTER_PATH)
    try:
        roster = json.loads(roster_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise PreflightError("找不到有效 frozen roster：{}".format(roster_path)) from exc

    core = roster.get("core")
    seats = roster.get("seats")
    if not isinstance(core, dict) or core != {
        "role": "core",
        "provider": "codex",
        "target_model": "gpt-5.6-sol",
    }:
        raise PreflightError("Core 必須固定為 gpt-5.6-sol。")
    if not isinstance(seats, list) or len(seats) != 7:
        raise PreflightError("frozen roster 必須恰好包含七席。")
    if [seat.get("seat_id") for seat in seats] != list(SEAT_IDS):
        raise PreflightError("frozen roster seat 順序或身分不符。")

    for seat in seats:
        required = {
            "seat_id",
            "focus",
            "output_dir",
            "provider",
            "target_model",
            "allowed_tools",
            "required_skills",
        }
        if not required <= set(seat):
            raise PreflightError("席位 {} 缺少 provider/model/tool 欄位。".format(seat.get("seat_id")))
        if not isinstance(seat["allowed_tools"], list):
            raise PreflightError("席位 {} allowed_tools 必須為陣列。".format(seat["seat_id"]))
        if not isinstance(seat["required_skills"], list):
            raise PreflightError("席位 {} required_skills 必須為陣列。".format(seat["seat_id"]))
        expected = EXPECTED_SEATS[seat["seat_id"]]
        actual = (
            seat["provider"],
            seat["target_model"],
            seat["allowed_tools"],
            seat["required_skills"],
        )
        if actual != expected:
            raise PreflightError("席位 {} provider/model/tool policy 漂移。".format(seat["seat_id"]))

    actual_counts = {
        provider: sum(seat["provider"] == provider for seat in seats)
        for provider in PROVIDER_COUNTS
    }
    if actual_counts != PROVIDER_COUNTS:
        raise PreflightError("provider 組成必須固定為 Claude 3、Codex 3、Antigravity 1。")
    if any(
        seat["target_model"] != "gpt-5.6-sol"
        for seat in seats
        if seat["provider"] == "codex"
    ):
        raise PreflightError("三個 Codex 席必須固定為 gpt-5.6-sol。")
    if any(
        seat["target_model"] != "opus"
        for seat in seats
        if seat["provider"] == "claude"
    ):
        raise PreflightError("三個 Claude 席必須固定為 opus。")
    gemini = [seat for seat in seats if seat["provider"] == "antigravity"]
    if gemini[0]["target_model"] != "gemini-3.1-pro-high":
        raise PreflightError("Gemini 席必須固定為 gemini-3.1-pro-high。")
    return roster


def build_preflight_manifest(*, checks, mode, generated_at_utc, code_root, data_root):
    """Normalize objective checks and refuse READY for incomplete or fixture evidence."""
    if mode not in ("real", "fixture"):
        raise PreflightError("preflight mode 必須為 real 或 fixture。")
    if not isinstance(checks, (list, tuple)):
        raise PreflightError("checks 必須為陣列。")

    by_id = {}
    duplicates = set()
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("check_id"), str):
            raise PreflightError("每個 preflight check 必須有 check_id。")
        check_id = check["check_id"]
        if check_id in by_id:
            duplicates.add(check_id)
        by_id[check_id] = dict(check)

    normalized = []
    blockers = []
    advisories = []
    for check_id in REQUIRED_CHECK_IDS:
        check = by_id.get(check_id)
        if check is None:
            blockers.append(check_id)
            normalized.append(
                {
                    "check_id": check_id,
                    "ok": False,
                    "target": "required",
                    "actual": "not_observed",
                    "evidence": "required check missing",
                }
            )
            continue
        item = {
            "check_id": check_id,
            "ok": check.get("ok") is True,
            "target": str(check.get("target", "")),
            "actual": str(check.get("actual", "")),
            "evidence": str(check.get("evidence", "")),
        }
        normalized.append(item)
        if not item["ok"]:
            if check_id in NON_BLOCKING_CHECK_IDS:
                advisories.append(check_id)
            else:
                blockers.append(check_id)
    blockers.extend(sorted(duplicates))
    unknown = sorted(set(by_id) - set(REQUIRED_CHECK_IDS))
    if unknown:
        blockers.extend("unknown_check:{}".format(check_id) for check_id in unknown)

    simulation_status = "PASS" if not blockers else "FAIL"
    provider_blockers = [
        blocker for blocker in blockers
        if blocker not in RUN_SCOPED_CHECK_IDS
    ]
    provider_capabilities_ready = mode == "real" and not provider_blockers
    if mode == "fixture":
        blockers.append("fixture_mode_is_not_live_evidence")
    ready = mode == "real" and not blockers
    return {
        "schema_version": "1.0.0",
        "status": "READY" if ready else "NOT_READY",
        "ready": ready,
        "mode": mode,
        "simulation_status": simulation_status if mode == "fixture" else None,
        "provider_capabilities_ready": provider_capabilities_ready,
        "advisories": advisories,
        "generated_at_utc": generated_at_utc,
        "code_root": str(Path(code_root)),
        "data_root": str(Path(data_root)),
        "roster": load_frozen_roster(),
        "checks": normalized,
        "blockers": blockers,
    }


def preflight_manifest_path(data_root, preflight_id):
    if (
        not isinstance(preflight_id, str)
        or not preflight_id
        or preflight_id in (".", "..")
        or Path(preflight_id).name != preflight_id
    ):
        raise PreflightError("preflight_id 不得包含路徑。")
    resolved_data_root = Path(data_root).resolve()
    preflight_root = resolved_data_root / "preflight"
    if preflight_root.resolve().parent != resolved_data_root:
        raise PreflightError("preflight root 解析後逃出 Data Root。")
    target_directory = preflight_root / preflight_id
    if target_directory.resolve().parent != preflight_root.resolve():
        raise PreflightError("preflight target 必須位於 Data Root 的直接子目錄。")
    return target_directory / "manifest.json"


def build_competition_authorization(
    *,
    preflight_id,
    run_id,
    competition_challenge,
    issued_at_utc,
):
    _require_safe_segment(preflight_id, "preflight_id")
    _require_safe_segment(run_id, "competition run_id")
    _require_challenge(competition_challenge)
    _require_utc(issued_at_utc, "authorization issued_at_utc")
    return {
        "schema_version": "1.0.0",
        "status": "AUTHORIZED",
        "system_preflight_id": preflight_id,
        "authorized_run_id": run_id,
        "competition_challenge": competition_challenge,
        "issued_at_utc": issued_at_utc,
    }


def write_competition_authorization(data_root, preflight_id, authorization):
    manifest_path = preflight_manifest_path(data_root, preflight_id)
    target = manifest_path.parent / COMPETITION_AUTHORIZATION_NAME
    content = json.dumps(authorization, ensure_ascii=False, indent=2) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    if preflight_manifest_path(data_root, preflight_id).parent != target.parent:
        raise PreflightError("competition authorization target 逃出 preflight 目錄。")
    try:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except FileExistsError as exc:
        raise PreflightError("competition authorization 已簽發，不得覆寫。") from exc
    return {
        "status": "AUTHORIZED",
        "authorized_run_id": authorization["authorized_run_id"],
        "competition_challenge_sha256": hashlib.sha256(
            authorization["competition_challenge"].encode("utf-8")
        ).hexdigest(),
        "path": "preflight/{}/{}".format(
            preflight_id, COMPETITION_AUTHORIZATION_NAME
        ),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def write_preflight_manifest(data_root, preflight_id, manifest):
    target = preflight_manifest_path(data_root, preflight_id)
    target_directory = target.parent
    target_directory.mkdir(parents=True, exist_ok=True)
    if preflight_manifest_path(data_root, preflight_id) != target:
        raise PreflightError("preflight target 解析後逃出 Data Root。")
    content = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    return target


def write_ready_certificate(data_root, preflight_id, manifest, manifest_path):
    """Publish the single pointer a live run consults; capabilities gate the write."""
    if manifest.get("provider_capabilities_ready") is not True:
        return None
    _require_safe_segment(preflight_id, "preflight_id")
    generated_at_utc = manifest.get("generated_at_utc")
    _require_utc(generated_at_utc, "manifest generated_at_utc")

    certificate = {
        "schema_version": "1.0.0",
        "status": "READY",
        "system_preflight_id": preflight_id,
        "manifest_path": "preflight/{}/manifest.json".format(preflight_id),
        "manifest_sha256": hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest(),
        "generated_at_utc": generated_at_utc,
        "provider_capabilities_ready": True,
        "provider_matrix_summary": _summarize_provider_matrix(
            manifest.get("provider_matrix")
        ),
    }
    target = Path(data_root) / "preflight" / READY_CERTIFICATE_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(certificate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, target)
    return target


def _summarize_provider_matrix(matrix):
    rows = matrix if isinstance(matrix, list) else []
    rows_by_provider = {}
    research_seat_ids = []
    for row in rows:
        provider = str(row.get("provider", "unknown"))
        rows_by_provider[provider] = rows_by_provider.get(provider, 0) + 1
        if row.get("role") == "research-seat":
            research_seat_ids.append(str(row.get("seat_id", "")))
    return {
        "row_count": len(rows),
        "rows_by_provider": dict(sorted(rows_by_provider.items())),
        "research_seat_ids": sorted(research_seat_ids),
    }


def _require_safe_segment(value, label):
    if (
        not isinstance(value, str)
        or not value
        or value in (".", "..")
        or Path(value).name != value
        or "/" in value
        or "\\" in value
    ):
        raise PreflightError("{} 不得包含路徑。".format(label))


def _require_challenge(value):
    if (
        not isinstance(value, str)
        or not 24 <= len(value) <= 128
        or any(
            not (character.isascii() and (character.isalnum() or character in "-_"))
            for character in value
        )
    ):
        raise PreflightError("competition challenge 必須為 24 至 128 字元 URL-safe nonce。")


def _require_utc(value, label):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PreflightError("{} 必須為 UTC ISO-8601。".format(label))
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PreflightError("{} 必須為有效 UTC ISO-8601。".format(label)) from exc
