"""The single command that completes one run.

Usage (WSL, from the Code Root)::

    python -m hoya_market_agents run --provider-mode fake --question "分析 BTC 過去 14 日市場狀態"

Only ``fake`` is accepted by the analysis command. The separate ``preflight``
command can verify a real provider without silently using it for a market run.
"""

import argparse
import hashlib
import json
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .antigravity_adapter import AntigravityAdapter, AntigravityError
from .claude_adapter import run_claude_preflight
from .competition_drill import run_fake_competition_drill
from .codex_bridge import (
    CodexBridgeError,
    STATUS_NOT_READY,
    TARGET_MODEL,
    verify_codex_preflight,
)
from .fake_provider import FakeProvider
from .question import UnsupportedQuestionError
from .report_contract import ReportContractError, canonical_sha256, validate_market_report
from .report_fixtures import FIXTURE_CASES, load_fixture
from .report_renderer import render_market_html, render_market_markdown
from .report_workflow import build_red_audit_report
from .run_controller import RunController
from .run_store import RunStore, RunStoreError
from .run_verifier import RunVerificationError, verify_run
from .seats import RosterError
from .prompt_builder import load_research_snapshot
from .system_preflight import (
    REQUIRED_CHECK_IDS,
    PreflightError,
    build_preflight_manifest,
    load_frozen_roster,
    preflight_manifest_path,
    write_preflight_manifest,
)

CODE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_ROOT = CODE_ROOT.parent / "hoya-bit-market-agents_data"

PROVIDERS = {"fake": FakeProvider}

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REJECTED = 2


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m hoya_market_agents",
        description="Hoya Bit market agents controller (WSL, Python 3 standard library only).",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", help="執行一次完整分析並產生報告")
    run.add_argument(
        "--provider-mode",
        required=True,
        choices=sorted(PROVIDERS),
        help="研究席的 provider 來源；本版本只支援離線 fake。",
    )
    run.add_argument("--question", required=True, help="自然語言題目")
    run.add_argument(
        "--data-root",
        default=str(DEFAULT_DATA_ROOT),
        help="Data Root 路徑（預設為 Code Root 旁的 hoya-bit-market-agents_data）",
    )
    preflight = subcommands.add_parser(
        "preflight", help="賽前驗證真實 provider；不啟動市場研究 run"
    )
    preflight.add_argument(
        "--provider", required=True, choices=("antigravity", "claude", "system")
    )
    preflight.add_argument("--seats", required=True, type=int)
    preflight.add_argument(
        "--data-root",
        default=str(DEFAULT_DATA_ROOT),
        help="preflight session、schema、log 與 raw envelope 的 Data Root",
    )
    drill = subcommands.add_parser(
        "drill", help="執行不連網的七席 T+0 至 T+13 competition regression"
    )
    drill.add_argument("--provider-mode", required=True, choices=("fake",))
    drill.add_argument("--question", required=True)
    drill.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    preflight.add_argument("--mode", choices=("real", "fixture"), default="real")
    preflight.add_argument("--codex-run-id", help="fresh Core Task 寫入的 Codex handoff run_id")
    preflight.add_argument(
        "--codex-challenge",
        help="啟動 fresh Core Task 前產生並交給它的 URL-safe one-time nonce",
    )
    preflight.add_argument("--drill-run-id", help="已通過 verify-run 的七席演練 run_id")
    preflight.add_argument("--preflight-id", help="唯一 preflight artifact 目錄名稱")
    preflight.add_argument(
        "--fixture-failure",
        choices=("login", "model", "write", "renderer"),
        help="只限 fixture mode 的 fail-closed 回歸注入",
    )

    preflight = subcommands.add_parser(
        "verify-preflight",
        help="驗證 Core 已寫入的 provider 賽前產物；不會啟動任何 agent",
    )
    preflight.add_argument(
        "--provider",
        required=True,
        choices=("codex",),
        help="要驗證的 provider bridge；本版本只支援 codex。",
    )
    preflight.add_argument("--run-id", required=True, help="要驗證的 run_id")
    preflight.add_argument(
        "--challenge",
        required=True,
        help="建立該 fresh handoff 時使用的 preflight challenge",
    )
    preflight.add_argument(
        "--data-root",
        default=str(DEFAULT_DATA_ROOT),
        help="Data Root 路徑（預設為 Code Root 旁的 hoya-bit-market-agents_data）",
    )
    fixture = subcommands.add_parser(
        "render-fixture", help="驗證並渲染固定 Ticket #10 報告 fixture"
    )
    fixture.add_argument("--case", required=True, choices=FIXTURE_CASES)
    fixture.add_argument("--output-dir", required=True)
    verify = subcommands.add_parser("verify-run", help="唯讀驗證一個完整 run artifact bundle")
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    return parser


def main(argv=None, stdout=None, stderr=None):
    """Run the CLI and return its exit code."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])

    if args.command == "preflight":
        return _preflight(args, out, err)
    if args.command == "verify-preflight":
        return _verify_preflight(args, out, err)
    if args.command == "render-fixture":
        return _render_fixture(args, out, err)
    if args.command == "verify-run":
        return _verify_run(args, out, err)
    if args.command == "drill":
        return _drill(args, out, err)

    data_root = Path(args.data_root)
    controller = RunController(
        store=RunStore(data_root),
        provider=PROVIDERS[args.provider_mode](),
    )

    try:
        result = controller.execute(args.question)
    except UnsupportedQuestionError as exc:
        print("題目未通過範圍檢查：{}".format(exc), file=err)
        return EXIT_REJECTED
    except (RunStoreError, RosterError) as exc:
        print("執行中止：{}".format(exc), file=err)
        return EXIT_FAILED

    _report_result(result, out)
    return EXIT_OK


def _preflight(args, out, err):
    if args.provider == "system":
        return _system_preflight(args, out, err)
    if args.provider == "claude":
        report = run_claude_preflight(
            seats=args.seats,
            code_root=CODE_ROOT,
            data_root=Path(args.data_root),
        )
        print(json.dumps(report, ensure_ascii=False, indent=2), file=out)
        return EXIT_OK if report["ready"] else EXIT_FAILED

    if args.seats != 1:
        print("NOT READY：antigravity preflight 此票只支援 --seats 1", file=err)
        return EXIT_FAILED
    data_root = Path(args.data_root).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    attempt_dir = (
        data_root
        / "preflight"
        / "antigravity"
        / "{}-{}".format(stamp, secrets.token_hex(3))
    )
    adapter = AntigravityAdapter(data_root=data_root)
    try:
        result = adapter.preflight(attempt_dir)
    except AntigravityError as exc:
        print("NOT READY：{}".format(exc), file=err)
        return EXIT_FAILED
    summary = {
        "status": "READY",
        "provider": "antigravity",
        "seats": 1,
        "cli_path": str(adapter.cli_path),
        "version": result.version,
        "requested_model": result.requested_model,
        "actual_model": result.actual_model,
        "effort": result.effort,
        "search_available": result.search_available,
        "search_smoke_succeeded": result.search_succeeded,
        "duration_seconds": result.duration_seconds,
        "usage": result.usage,
        "structured_contract_valid": result.structured_output == {"answer": "ready"},
        "schema_path": str(result.schema_path),
        "schema_sha256": result.schema_sha256,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=out)
    return EXIT_OK


def _system_preflight(args, out, err):
    if args.seats != 7:
        print("NOT READY：system preflight 必須恰好 --seats 7", file=err)
        return EXIT_FAILED
    if args.fixture_failure and args.mode != "fixture":
        print("NOT READY：--fixture-failure 只能搭配 --mode fixture", file=err)
        return EXIT_FAILED

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    preflight_id = args.preflight_id or datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ-{}".format(secrets.token_hex(3))
    )
    try:
        manifest_path = preflight_manifest_path(Path(args.data_root), preflight_id)
    except PreflightError as exc:
        print("NOT READY：{}".format(exc), file=err)
        return EXIT_FAILED
    provider_matrix = []
    checks = _fixture_system_checks()
    if args.mode == "real":
        checks, provider_matrix = _real_system_checks(args, preflight_id)
    elif args.fixture_failure:
        failure_checks = {
            "login": "provider_login",
            "model": "target_actual_models",
            "write": "data_root_write",
            "renderer": "renderer",
        }
        item = next(
            check for check in checks
            if check["check_id"] == failure_checks[args.fixture_failure]
        )
        item.update(ok=False, actual="broken fixture", evidence="deliberate failure fixture")

    try:
        manifest = build_preflight_manifest(
            checks=checks,
            mode=args.mode,
            generated_at_utc=generated,
            code_root=CODE_ROOT,
            data_root=Path(args.data_root),
        )
        manifest["manifest_path"] = str(manifest_path)
        manifest["provider_matrix"] = provider_matrix
        write_preflight_manifest(Path(args.data_root), preflight_id, manifest)
    except (OSError, PreflightError) as exc:
        print("NOT READY：{}".format(exc), file=err)
        return EXIT_FAILED
    print(json.dumps(manifest, ensure_ascii=False, indent=2), file=out)
    return EXIT_OK if manifest["ready"] else EXIT_FAILED


def _fixture_system_checks():
    return [
        {
            "check_id": check_id,
            "ok": True,
            "target": "required",
            "actual": "fixture_pass",
            "evidence": "deterministic offline fixture",
        }
        for check_id in REQUIRED_CHECK_IDS
    ]


def _real_system_checks(args, preflight_id):
    checks = [
        {
            "check_id": check_id,
            "ok": False,
            "target": "required",
            "actual": "not_observed",
            "evidence": "live evidence not observed",
        }
        for check_id in REQUIRED_CHECK_IDS
    ]
    data_root = Path(args.data_root).resolve()
    artifact_root = data_root / "preflight" / preflight_id
    _apply_local_observations(checks, artifact_root)

    codex = None
    if args.codex_run_id and args.codex_challenge:
        try:
            codex = verify_codex_preflight(
                data_root,
                args.codex_run_id,
                expected_challenge=args.codex_challenge,
                binding_id=preflight_id,
            )
        except CodexBridgeError as exc:
            _set_check(
                checks,
                "codex_runtime_receipts",
                False,
                "rejected",
                "Codex handoff rejected: {}".format(exc),
            )
        else:
            _set_check(
                checks,
                "codex_runtime_receipts",
                True,
                "3 verified receipts",
                "verified fresh Core handoff {}".format(args.codex_run_id),
            )
    else:
        missing = "--codex-run-id and --codex-challenge"
        _set_check(
            checks,
            "codex_runtime_receipts",
            False,
            "not_observed",
            "fresh Codex Task did not supply {}".format(missing),
        )

    provider_matrix = _codex_matrix(codex)
    claude = None
    antigravity = None
    # Fail fast before subscription calls when Core identity/receipts are absent.
    if codex is not None:
        claude_root = artifact_root / "providers" / "claude"
        claude = run_claude_preflight(
            seats=3,
            code_root=CODE_ROOT,
            data_root=claude_root,
        )
        provider_matrix.extend(_claude_matrix(claude))
        antigravity_root = artifact_root / "providers" / "antigravity"
        try:
            result = AntigravityAdapter(data_root=antigravity_root).preflight(
                antigravity_root / "attempt-1"
            )
        except AntigravityError as exc:
            antigravity = {"ready": False, "reason": str(exc)}
        else:
            antigravity = {
                "ready": True,
                "version": result.version,
                "requested_model": result.requested_model,
                "actual_model": result.actual_model,
                "search_succeeded": result.search_succeeded,
                "duration_seconds": result.duration_seconds,
            }
        provider_matrix.extend(_antigravity_matrix(antigravity))

    login_ok = bool(
        codex
        and claude
        and claude.get("ready") is True
        and claude.get("auth") == "claude.ai Max"
        and antigravity
        and antigravity.get("ready") is True
    )
    _set_check(
        checks,
        "provider_login",
        login_ok,
        "all observed" if login_ok else "not_observed",
        "fresh Codex runtime + Claude Max + Google OAuth capability smoke",
    )

    model_ok = bool(
        codex
        and claude
        and len(claude.get("seats", ())) == 3
        and all(_is_claude_opus_model(seat.get("actual_model")) for seat in claude["seats"])
        and antigravity
        and antigravity.get("actual_model") == "gemini-3.1-pro-high"
    )
    _set_check(
        checks,
        "target_actual_models",
        model_ok,
        "7 matched" if model_ok else "not_observed",
        "Core/GPT gpt-5.6-sol; Claude actual contains opus; Gemini exact high model",
    )

    # #8 intentionally requires an enforced no-tool receipt. Current Codex
    # runtime metadata therefore cannot also prove three independent searches.
    # Do not turn Claude/Gemini search success into a seven-seat claim.
    search_evidence = (
        "Codex seats have verified no-tool policy; per-seat search receipts are "
        "not observable without changing the #8 trust boundary"
    )
    _set_check(checks, "search", False, "3 Codex searches not observed", search_evidence)

    boundary_ok = bool(
        codex
        and claude
        and claude.get("isolation", {}).get("code_root_write_tools") is False
        and antigravity
        and antigravity.get("ready") is True
    )
    _set_check(
        checks,
        "code_data_boundary",
        boundary_ok,
        "enforced" if boundary_ok else "not_observed",
        "Codex runtime receipts, Claude Web-only tools, Antigravity sandbox attempt dir",
    )

    if args.drill_run_id:
        _apply_drill_observation(checks, data_root, args.drill_run_id)
    return checks, provider_matrix


def _apply_local_observations(checks, artifact_root):
    versions = {}
    commands = {
        "codex": ["/home/leslie/.local/bin/codex", "--version"],
        "claude": ["/home/leslie/.local/bin/claude", "--version"],
        "antigravity": ["/home/leslie/.local/bin/agy", "--version"],
    }
    for provider, command in commands.items():
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            versions[provider] = None
        else:
            versions[provider] = completed.stdout.strip().splitlines()[0] if completed.returncode == 0 and completed.stdout.strip() else None
    version_ok = all(versions.values())
    _set_check(
        checks,
        "cli_versions",
        version_ok,
        "observed" if version_ok else "missing",
        "; ".join("{}={}".format(key, value or "unavailable") for key, value in versions.items()),
    )

    try:
        snapshot = load_research_snapshot()
    except (OSError, UnicodeError, ValueError) as exc:
        _set_check(checks, "research_snapshot", False, "invalid", str(exc))
    else:
        _set_check(
            checks,
            "research_snapshot",
            True,
            snapshot.sha256,
            "commit={} blob={}".format(snapshot.upstream_commit, snapshot.git_blob_sha),
        )

    code_root = CODE_ROOT.resolve()
    data_root = artifact_root.parents[1].resolve()
    separated = data_root != code_root and not data_root.is_relative_to(code_root)
    _set_check(
        checks,
        "code_data_boundary",
        separated,
        "separate" if separated else "unsafe",
        "code_root={} data_root={}".format(code_root, data_root),
    )

    try:
        windows_path = subprocess.run(
            ["wslpath", "-w", str(code_root)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        roundtrip = subprocess.run(
            ["wslpath", "-u", windows_path.stdout.strip()],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        translation_ok = (
            windows_path.returncode == 0
            and bool(windows_path.stdout.strip())
            and roundtrip.returncode == 0
            and Path(roundtrip.stdout.strip()).resolve() == code_root
        )
    except (OSError, subprocess.TimeoutExpired):
        translation_ok = False
        windows_path = None
    _set_check(
        checks,
        "path_translation",
        translation_ok,
        "roundtrip ok" if translation_ok else "unavailable",
        "wslpath -w/-u Code Root roundtrip"
        + ("; windows_path={}".format(windows_path.stdout.strip()) if translation_ok else ""),
    )

    try:
        artifact_root.mkdir(parents=True, exist_ok=False)
        probe = artifact_root / "write-probe.json"
        with probe.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump({"write": "ok"}, handle)
            handle.write("\n")
    except OSError as exc:
        _set_check(checks, "data_root_write", False, "failed", str(exc))
    else:
        _set_check(checks, "data_root_write", True, "write-once ok", str(probe))

    try:
        usage = shutil.disk_usage(data_root)
        disk_ok = usage.free >= 10 * 1024 * 1024
    except OSError as exc:
        _set_check(checks, "disk_space", False, "unavailable", str(exc))
    else:
        _set_check(
            checks,
            "disk_space",
            disk_ok,
            "{} bytes free".format(usage.free),
            "minimum=10485760 bytes",
        )

    try:
        fixture = load_fixture("consensus-6-1")
        report = validate_market_report(fixture["report"], fixture["sources"])
        markdown = render_market_markdown(report)
        html = render_market_html(report)
        renderer_ok = bool(markdown and "@media print" in html and "<script" not in html.lower() and "<link" not in html.lower())
    except Exception as exc:
        _set_check(checks, "renderer", False, "failed", type(exc).__name__)
    else:
        _set_check(
            checks,
            "renderer",
            renderer_ok,
            "offline render ok" if renderer_ok else "invalid render",
            "md_sha256={} html_sha256={}".format(
                hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                hashlib.sha256(html.encode("utf-8")).hexdigest(),
            ),
        )

    first = time.monotonic_ns()
    second = time.monotonic_ns()
    utc = datetime.now(timezone.utc)
    clock_ok = second >= first and utc.utcoffset() == timezone.utc.utcoffset(utc)
    _set_check(
        checks,
        "clock",
        clock_ok,
        "monotonic+UTC" if clock_ok else "invalid",
        "monotonic_delta_ns={}".format(second - first),
    )
    try:
        roster = load_frozen_roster()
    except PreflightError as exc:
        _set_check(checks, "roster", False, "invalid", str(exc))
    else:
        _set_check(
            checks,
            "roster",
            True,
            roster["roster_version"],
            "Core + 3 Codex + 3 Claude + 1 Antigravity",
        )


def _set_check(checks, check_id, ok, actual, evidence):
    next(item for item in checks if item["check_id"] == check_id).update(
        ok=bool(ok), actual=str(actual), evidence=str(evidence)
    )


def _codex_matrix(payload):
    if payload is None:
        return []
    rows = [
        {
            "role": "core",
            "provider": "codex",
            "seat_id": "core",
            "target_model": "gpt-5.6-sol",
            "actual_model": payload["core"]["model"],
            "identity": "fresh Core Task",
        }
    ]
    rows += [
        {
            "role": "research-seat",
            "provider": "codex",
            "seat_id": seat["seat_id"],
            "target_model": "gpt-5.6-sol",
            "actual_model": seat["actual_model"],
            "identity": _masked_id(seat["thread_id"]),
        }
        for seat in payload["seats"]
    ]
    return rows


def _claude_matrix(report):
    if not report:
        return []
    return [
        {
            "role": "research-seat",
            "provider": "claude",
            "seat_id": seat.get("seat_id"),
            "target_model": "opus",
            "actual_model": seat.get("actual_model"),
            "identity": _masked_id(seat.get("session_id")),
            "search_succeeded": seat.get("web_search_requests", 0) >= 1,
            "elapsed_ms": seat.get("elapsed_ms"),
        }
        for seat in report.get("seats", [])
    ]


def _antigravity_matrix(report):
    if not report:
        return []
    return [
        {
            "role": "research-seat",
            "provider": "antigravity",
            "seat_id": "counter-evidence",
            "target_model": "gemini-3.1-pro-high",
            "actual_model": report.get("actual_model"),
            "identity": "Google OAuth [REDACTED]",
            "search_succeeded": report.get("search_succeeded") is True,
            "duration_seconds": report.get("duration_seconds"),
        }
    ]


def _masked_id(value):
    value = str(value or "")
    return "{}…{}".format(value[:8], value[-4:]) if len(value) > 12 else "[REDACTED]"


def _is_claude_opus_model(value):
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return lowered == "opus" or lowered.startswith("claude-opus-")


def _apply_drill_observation(checks, data_root, run_id):
    try:
        summary = verify_run(data_root, run_id)
    except RunVerificationError as exc:
        evidence = "drill rejected: {}".format(exc)
        for check_id in ("seven_seat_timeline", "report_deadline"):
            next(item for item in checks if item["check_id"] == check_id)["evidence"] = evidence
        return
    if (
        summary.get("provider_mode") != "real-subscription"
        or not summary.get("competition_ready")
        or not isinstance(summary.get("timeline"), dict)
    ):
        evidence = "verified {} is not live competition evidence".format(
            summary.get("provider_mode") or "unknown provider mode"
        )
        for check_id in ("seven_seat_timeline", "report_deadline"):
            next(item for item in checks if item["check_id"] == check_id).update(
                ok=False,
                actual="not_observed",
                evidence=evidence,
            )
        return
    for check_id in ("seven_seat_timeline", "report_deadline"):
        next(item for item in checks if item["check_id"] == check_id).update(
            ok=True,
            actual="observed",
            evidence="verify-run {} status {}".format(run_id, summary["status"]),
        )


def _verify_run(args, out, err):
    try:
        summary = verify_run(Path(args.data_root), args.run_id)
    except RunVerificationError as exc:
        print("NOT VERIFIED：{}".format(exc), file=err)
        return EXIT_FAILED
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=out)
    return EXIT_OK


def _drill(args, out, err):
    try:
        result = run_fake_competition_drill(
            data_root=Path(args.data_root),
            question=args.question,
            token=secrets.token_hex(3),
        )
        verification = verify_run(Path(args.data_root), result.run_id)
    except (UnsupportedQuestionError, RunStoreError, RunVerificationError, ValueError) as exc:
        print("DRILL FAILED：{}".format(exc), file=err)
        return EXIT_FAILED
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "run_dir": str(result.run_dir),
                "timeline": result.timeline,
                "verification": verification,
            },
            ensure_ascii=False,
            indent=2,
        ),
        file=out,
    )
    return EXIT_OK


def _verify_preflight(args, out, err):
    """Read an already-written handoff artifact and report READY / NOT_READY."""
    try:
        payload = verify_codex_preflight(
            Path(args.data_root),
            args.run_id,
            expected_challenge=args.challenge,
        )
    except CodexBridgeError as exc:
        print("{}：{}".format(STATUS_NOT_READY, exc), file=err)
        return EXIT_FAILED

    lines = [
        "Provider：{}".format(args.provider),
        "Run ID：{}".format(payload["run_id"]),
        "狀態：{}".format(payload["status"]),
        "Core 模型：{}".format(payload["core"]["model"]),
        "目標模型：{}".format(TARGET_MODEL),
        "共享 prompt SHA-256：{}".format(payload["shared_prompt_sha256"]),
        "固定 GPT 席位：",
    ]
    lines += [
        "  - {}：thread={} model={}".format(
            seat["seat_id"], seat["thread_id"], seat["actual_model"]
        )
        for seat in payload["seats"]
    ]
    print("\n".join(lines), file=out)
    return EXIT_OK


def _report_result(result, out):
    tally = "／".join("{}：{}".format(s, n) for s, n in result.tally.items())
    lines = [
        "Run ID：{}".format(result.run_id),
        "Data Root：{}".format(result.data_root),
        "Run 目錄：{}".format(result.run_dir),
        "報告（Markdown）：{}".format(result.run_dir / "report.md"),
        "報告（HTML）：{}".format(result.run_dir / "report.html"),
        "票數：{}".format(tally),
        "七席立場：",
    ]
    lines += ["  - {}：{}".format(seat_id, stance) for seat_id, stance in result.seat_stances.items()]
    print("\n".join(lines), file=out)


def _render_fixture(args, out, err):
    fixture = load_fixture(args.case)
    status = "accepted"
    exit_code = EXIT_OK
    try:
        report = validate_market_report(fixture["report"], fixture["sources"])
    except ReportContractError as exc:
        report = build_red_audit_report(
            fixture["sources"],
            exc.problems,
            generated_at_utc=fixture["report"].get("generated_at_utc"),
        )
        validate_market_report(report, fixture["sources"])
        status = "red_audit"
        exit_code = EXIT_FAILED

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered = {
        "report.json": json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        "report.md": render_market_markdown(report),
        "report.html": render_market_html(report),
    }
    for name, content in rendered.items():
        (output_dir / name).write_text(content, encoding="utf-8")
    audit = {
        "case": args.case,
        "status": status,
        "hash_lineage": {
            "sources": canonical_sha256(fixture["sources"]),
            **{
                name: hashlib.sha256(content.encode("utf-8")).hexdigest()
                for name, content in rendered.items()
            },
        },
    }
    (output_dir / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "{}: {} -> {}".format(status.upper(), args.case, output_dir),
        file=out if exit_code == EXIT_OK else err,
    )
    return exit_code
