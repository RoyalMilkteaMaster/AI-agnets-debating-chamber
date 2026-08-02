"""The file inbox that carries prompts to Codex seats and results back.

The three GPT seats live inside a Codex Task, not inside this process, so the
only channel between them and the controller is Data Root. Every exchange goes
through ``<Data Root>/inbox/<run_id>/``:

``prompts/<seat_id>.txt``
    one write-once prompt per seat, written by the controller
``requests/<attempt_id>.json``
    the dispatch record a seat picks up
``results/<seat_id>.<attempt_id>.json``
    one write-once raw seat response, written by ``submit-seat``

The inbox deliberately sits outside ``runs/`` so ``RunDirectory`` stays the
single writer of the audited run bundle: an outside agent can drop a file here
without ever touching a sealed run artifact. Promoting an inbox result into the
run bundle stays the controller's job.
"""

import argparse
import json
import sys
from pathlib import Path

from .clock import SystemClock, iso_utc
from .codex_bridge import CODEX_SEAT_IDS
from .seats import CODE_ROOT

SCHEMA_VERSION = "1.0.0"
INBOX_DIRECTORY = "inbox"
CHANNELS = ("prompts", "requests", "results")
DEFAULT_DATA_ROOT = CODE_ROOT.parent / "hoya-bit-market-agents_data"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REJECTED = 2

_RESULT_FIELDS = (
    "schema_version",
    "run_id",
    "seat_id",
    "attempt_id",
    "submitted_at_utc",
    "raw_output",
)


class InboxError(Exception):
    """Raised when an inbox path, seat or write-once rule is violated."""


def inbox_root(data_root, run_id):
    """Return ``<Data Root>/inbox/<run_id>/`` for a validated run id."""
    _require_safe_segment(run_id, "run_id")
    return Path(data_root) / INBOX_DIRECTORY / run_id


def ensure_inbox(data_root, run_id):
    """Create the prompt, request and result channels and return the root."""
    root = inbox_root(data_root, run_id)
    try:
        for channel in CHANNELS:
            (root / channel).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InboxError("無法建立 inbox 目錄 {}：{}".format(root, exc)) from exc
    return root


def write_seat_prompt(data_root, run_id, seat_id, text):
    """Write one seat's prompt exactly once and return its path."""
    _require_safe_segment(seat_id, "seat_id")
    if not isinstance(text, str):
        raise InboxError("prompt 必須為文字。")
    target = ensure_inbox(data_root, run_id) / "prompts" / "{}.txt".format(seat_id)
    _write_once(target, text, "prompt")
    return target


def write_seat_result(
    data_root,
    run_id,
    seat_id,
    attempt_id,
    raw_text,
    submitted_at_utc=None,
):
    """Store one Codex seat's raw response exactly once and return its path."""
    if seat_id not in CODEX_SEAT_IDS:
        raise InboxError("不是固定 GPT 席：{}".format(seat_id))
    _require_safe_segment(attempt_id, "attempt_id")
    if not isinstance(raw_text, str):
        raise InboxError("raw output 必須為文字。")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": _validated_run_id(run_id),
        "seat_id": seat_id,
        "attempt_id": attempt_id,
        "submitted_at_utc": submitted_at_utc or iso_utc(SystemClock().utc_now()),
        "raw_output": raw_text,
    }
    target = (
        ensure_inbox(data_root, run_id)
        / "results"
        / "{}.{}.json".format(seat_id, attempt_id)
    )
    _write_once(
        target,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        "seat result",
    )
    return target


def poll_results(data_root, run_id, seen):
    """Return newly arrived ``(seat_id, attempt_id, raw_output)`` results.

    ``seen`` is a mutable set of ``(seat_id, attempt_id)`` keys owned by the
    caller and updated in place, so repeated polling never replays a result.
    A missing inbox and a half-written or malformed file are both normal during
    a live run: they are skipped instead of ending the run.
    """
    results_dir = inbox_root(data_root, run_id) / "results"
    try:
        paths = sorted(results_dir.glob("*.json"))
    except OSError:
        return []

    arrived = []
    for path in paths:
        record = _read_result(path)
        if record is None:
            continue
        seat_id, attempt_id, raw_output = record
        if (seat_id, attempt_id) in seen:
            continue
        seen.add((seat_id, attempt_id))
        arrived.append(record)
    return arrived


def run_submit_seat(argv, stdin, stdout, stderr):
    """Run the ``submit-seat`` subcommand and return its exit code."""
    parser = _submit_seat_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgumentError as exc:
        print("提交失敗：{}".format(exc), file=stderr)
        return EXIT_REJECTED

    raw_text = stdin.read()
    if not raw_text.strip():
        print("提交失敗：stdin 沒有任何 raw 輸出。", file=stderr)
        return EXIT_REJECTED

    try:
        target = write_seat_result(
            Path(args.data_root),
            args.run_id,
            args.seat_id,
            args.attempt_id,
            raw_text,
        )
    except InboxError as exc:
        print("提交失敗：{}".format(exc), file=stderr)
        return EXIT_REJECTED

    print("已提交：{}".format(target), file=stdout)
    return EXIT_OK


class _ArgumentError(Exception):
    """Raised instead of argparse's SystemExit so the CLI owns its streams."""


class _SubmitSeatParser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgumentError(message)


def _submit_seat_parser():
    parser = _SubmitSeatParser(
        prog="python -m hoya_market_agents submit-seat",
        description="把一個固定 GPT 席的 raw 輸出寫入 run inbox（write-once）。",
        add_help=False,
    )
    parser.add_argument("--run-id", required=True, help="目標 run_id")
    parser.add_argument(
        "--seat-id",
        required=True,
        choices=CODEX_SEAT_IDS,
        help="提交的固定 GPT 席位",
    )
    parser.add_argument("--attempt-id", required=True, help="該席位的 attempt_id")
    parser.add_argument(
        "--data-root",
        default=str(DEFAULT_DATA_ROOT),
        help="Data Root 路徑（預設為 Code Root 旁的 hoya-bit-market-agents_data）",
    )
    return parser


def _read_result(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or any(
        field not in payload for field in _RESULT_FIELDS
    ):
        return None
    seat_id = payload["seat_id"]
    attempt_id = payload["attempt_id"]
    raw_output = payload["raw_output"]
    if not all(isinstance(value, str) for value in (seat_id, attempt_id, raw_output)):
        return None
    return (seat_id, attempt_id, raw_output)


def _write_once(target, content, label):
    try:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except FileExistsError as exc:
        raise InboxError("{} 已存在，拒絕覆寫：{}".format(label, target)) from exc
    except OSError as exc:
        raise InboxError("無法寫入 {}：{}".format(target, exc)) from exc


def _validated_run_id(run_id):
    _require_safe_segment(run_id, "run_id")
    return run_id


def _require_safe_segment(value, label):
    if not isinstance(value, str) or not value.strip():
        raise InboxError("{} 必須為非空字串。".format(label))
    if Path(value).name != value or value in (".", "..") or "/" in value or "\\" in value:
        raise InboxError("{} 不得包含路徑。".format(label))


def main(argv=None):
    """Allow ``python -m hoya_market_agents.codex_inbox`` during a live run."""
    return run_submit_seat(
        argv if argv is not None else sys.argv[1:], sys.stdin, sys.stdout, sys.stderr
    )


if __name__ == "__main__":
    raise SystemExit(main())
