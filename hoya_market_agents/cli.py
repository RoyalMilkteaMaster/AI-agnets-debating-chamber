"""The single command that completes one run.

Usage (WSL, from the Code Root)::

    python -m hoya_market_agents run --provider-mode fake --question "分析 BTC 過去 14 日市場狀態"

Only ``fake`` is accepted by the analysis command. The separate ``preflight``
command can verify a real provider without silently using it for a market run.
"""

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from .antigravity_adapter import AntigravityAdapter, AntigravityError
from .claude_adapter import run_claude_preflight
from .codex_bridge import (
    CodexBridgeError,
    STATUS_NOT_READY,
    TARGET_MODEL,
    verify_codex_preflight,
)
from .fake_provider import FakeProvider
from .question import UnsupportedQuestionError
from .run_controller import RunController
from .run_store import RunStore, RunStoreError
from .seats import RosterError

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
        "--provider", required=True, choices=("antigravity", "claude")
    )
    preflight.add_argument("--seats", required=True, type=int)
    preflight.add_argument(
        "--data-root",
        default=str(DEFAULT_DATA_ROOT),
        help="preflight session、schema、log 與 raw envelope 的 Data Root",
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
        "--data-root",
        default=str(DEFAULT_DATA_ROOT),
        help="Data Root 路徑（預設為 Code Root 旁的 hoya-bit-market-agents_data）",
    )
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


def _verify_preflight(args, out, err):
    """Read an already-written handoff artifact and report READY / NOT_READY."""
    try:
        payload = verify_codex_preflight(Path(args.data_root), args.run_id)
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
