"""The seven fixed research seats.

A seat is a logical identity, not a model process. The ids are fixed so every
evidence card, debate turn and vote stays traceable across artifacts even when
the underlying provider or attempt changes.
"""

import json
from dataclasses import dataclass
from pathlib import Path

SEAT_IDS = (
    "spot-technical",
    "derivatives",
    "onchain",
    "official-events",
    "news",
    "social-macro",
    "counter-evidence",
)

CODE_ROOT = Path(__file__).resolve().parent.parent
ROSTER_PATH = CODE_ROOT / "config" / "agent_roster.json"


class RosterError(Exception):
    """Raised when the roster config does not describe the fixed seats."""


@dataclass(frozen=True)
class Seat:
    seat_id: str
    focus: str
    output_dir: str


def load_roster(path=None):
    """Load and verify the seat roster, returning seats in approved order."""
    roster_path = Path(path) if path else ROSTER_PATH
    try:
        raw = json.loads(roster_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RosterError("找不到 roster 設定 {}。".format(roster_path)) from exc

    seats = {}
    for entry in raw.get("seats", []):
        seat = Seat(
            seat_id=entry.get("seat_id", ""),
            focus=entry.get("focus", ""),
            output_dir=entry.get("output_dir", ""),
        )
        if seat.seat_id not in SEAT_IDS:
            raise RosterError("roster 含有未核准席位 {}。".format(seat.seat_id))
        if not seat.focus or not seat.output_dir:
            raise RosterError("席位 {} 缺少 focus 或 output_dir。".format(seat.seat_id))
        seats[seat.seat_id] = seat

    missing = [seat_id for seat_id in SEAT_IDS if seat_id not in seats]
    if missing:
        raise RosterError("roster 缺少席位 {}。".format(", ".join(missing)))

    return [seats[seat_id] for seat_id in SEAT_IDS]
