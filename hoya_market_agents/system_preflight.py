"""Fail-closed aggregate readiness manifest for the competition roster."""

import json
from pathlib import Path

from .seats import ROSTER_PATH, SEAT_IDS


REQUIRED_CHECK_IDS = (
    "cli_versions",
    "provider_login",
    "target_actual_models",
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
PROVIDER_COUNTS = {"claude": 3, "codex": 3, "antigravity": 1}
EXPECTED_SEATS = {
    "spot-technical": ("codex", "gpt-5.6-sol", []),
    "derivatives": ("codex", "gpt-5.6-sol", []),
    "onchain": ("codex", "gpt-5.6-sol", []),
    "official-events": ("claude", "opus", ["WebSearch", "WebFetch"]),
    "news": ("claude", "opus", ["WebSearch", "WebFetch"]),
    "social-macro": ("claude", "opus", ["WebSearch", "WebFetch"]),
    "counter-evidence": ("antigravity", "gemini-3.1-pro-high", ["search_web"]),
}


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
        required = {"seat_id", "focus", "output_dir", "provider", "target_model", "allowed_tools"}
        if not required <= set(seat):
            raise PreflightError("席位 {} 缺少 provider/model/tool 欄位。".format(seat.get("seat_id")))
        if not isinstance(seat["allowed_tools"], list):
            raise PreflightError("席位 {} allowed_tools 必須為陣列。".format(seat["seat_id"]))
        expected = EXPECTED_SEATS[seat["seat_id"]]
        actual = (seat["provider"], seat["target_model"], seat["allowed_tools"])
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
            blockers.append(check_id)
    blockers.extend(sorted(duplicates))
    unknown = sorted(set(by_id) - set(REQUIRED_CHECK_IDS))
    if unknown:
        blockers.extend("unknown_check:{}".format(check_id) for check_id in unknown)

    simulation_status = "PASS" if not blockers else "FAIL"
    provider_blockers = [
        blocker for blocker in blockers
        if blocker not in ("seven_seat_timeline", "report_deadline")
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
