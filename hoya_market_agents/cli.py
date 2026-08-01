"""The single command that completes one run.

Usage (WSL, from the Code Root)::

    python -m hoya_market_agents run --provider-mode fake --question "分析 BTC 過去 14 日市場狀態"

Only ``fake`` is an accepted provider mode in this version, so the command can
never silently fall back to a provider that does not exist yet.
"""

import argparse
import sys
from pathlib import Path

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
    return parser


def main(argv=None, stdout=None, stderr=None):
    """Run the CLI and return its exit code."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])

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
