"""The seven fixed research seats: their ids, their roster, and who they are.

A seat is a logical identity, not a model process. The ids are fixed so every
evidence card, debate turn and vote stays traceable across artifacts even when
the underlying provider, the asset class or the seat's research brief changes.

Two different things live here and they answer to different files:

``SEAT_IDS`` / :func:`load_roster`
    What the run machinery dispatches. ``config/agent_roster.json`` says what
    each seat researches and where its output goes.
:func:`seat_profiles` / :func:`seat_identities`
    What a *reader* sees. Since ADR 0006 a seat has three research briefs — the
    stock set (Taiwan and US share one), the crypto set and the open set — and
    the run's asset class picks one. The roster holds all three, so this module
    holds **no seat name of its own**: it converts an asset class into a set
    (:func:`profile_set_for`, the one place that decision is made) and reads the
    names, briefs and 白話說明 back out (:func:`seat_profiles`, the one reading
    port every display path uses — offline report, audit transcript and web app
    alike).

``SEAT_AVATARS`` / ``_SEAT_BADGES``
    The presentation facts that do *not* follow the asset class: the ``Agent n``
    number, the avatar, and which provider family fills the seat.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .question import (
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_OPEN,
    ASSET_CLASS_TW_STOCK,
    ASSET_CLASS_US_STOCK,
)

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

PROFILE_SET_STOCK = "stock"
PROFILE_SET_CRYPTO = "crypto"
PROFILE_SET_OPEN = "open"
PROFILE_SETS = (PROFILE_SET_STOCK, PROFILE_SET_CRYPTO, PROFILE_SET_OPEN)

# 資產類別 → 套組的**單一判定處**（ADR 0006）。任何顯示端都不得自己寫這條規則。
PROFILE_SET_BY_ASSET_CLASS = MappingProxyType(
    {
        ASSET_CLASS_TW_STOCK: PROFILE_SET_STOCK,
        ASSET_CLASS_US_STOCK: PROFILE_SET_STOCK,
        ASSET_CLASS_CRYPTO: PROFILE_SET_CRYPTO,
        ASSET_CLASS_OPEN: PROFILE_SET_OPEN,
    }
)

_PROFILE_FIELDS = ("display_name", "focus", "blurb")


class RosterError(Exception):
    """Raised when the roster config does not describe the fixed seats."""


@dataclass(frozen=True)
class SeatProfile:
    """One seat's shown name, research brief and one-line 白話說明.

    ``focus`` and ``blurb`` are two audiences, not two copies: ``focus`` is the
    brief the seat is dispatched with and the only one that reaches a research
    prompt, while ``blurb`` is the sentence a reader gets under the seat card
    and reaches nothing else (Spec R-005).
    """

    display_name: str
    focus: str
    blurb: str


@dataclass(frozen=True)
class Seat:
    """One seat as the run machinery dispatches it.

    ``profiles`` has no default: a seat whose sets were never read is a seat with
    no name for two of the three markets, and :func:`load_roster` is the only
    thing that builds one.
    """

    seat_id: str
    focus: str
    output_dir: str
    profiles: Mapping

    def profile(self, asset_class=None):
        """This seat's name and brief for ``asset_class``'s profile set."""
        return self.profiles[profile_set_for(asset_class)]


@dataclass(frozen=True)
class SeatIdentity:
    """How one seat is shown to a reader, on every page that shows seats.

    ``display_name`` is the byline: the provider family that fills the seat,
    then the seat's name in the run's own profile set. ``provider`` is also used
    as a CSS class, so a reader can tell the three Codex seats from the three
    Claude ones without reading seven names.
    """

    seat_id: str
    display_name: str
    agent_number: str
    avatar: str
    provider: str


@dataclass(frozen=True)
class _SeatBadge:
    agent_number: str
    avatar: str
    provider: str


# 不隨資產類別改變的呈現事實。**顯示名稱刻意不在這裡**：它依 run 的資產類別
# 從 roster profiles 取得（ADR 0006）。``provider`` 是 CSS class 來源，值與
# roster 的 provider 家族對應（antigravity 席以 gemini 呈現）。
_SEAT_BADGES = MappingProxyType(
    {
        "spot-technical": _SeatBadge("Agent 1", "📈", "codex"),
        "derivatives": _SeatBadge("Agent 2", "⚙️", "codex"),
        "onchain": _SeatBadge("Agent 3", "⛓️", "claude"),
        "official-events": _SeatBadge("Agent 4", "📣", "claude"),
        "news": _SeatBadge("Agent 5", "📰", "codex"),
        "social-macro": _SeatBadge("Agent 6", "🌐", "claude"),
        "counter-evidence": _SeatBadge("Agent 7", "🔎", "gemini"),
    }
)

SEAT_AVATARS = MappingProxyType(
    {seat_id: badge.avatar for seat_id, badge in _SEAT_BADGES.items()}
)


def profile_set_for(asset_class=None):
    """Return the profile set one asset class reads.

    ``tw_stock`` and ``us_stock`` share the stock set, ``crypto`` and ``open``
    have their own. Anything else — a cross-class run, or a caller that does not
    know the class — reads the open set, which is a filled set of its own rather
    than a fallback to nothing (Spec R5).
    """
    return PROFILE_SET_BY_ASSET_CLASS.get(asset_class, PROFILE_SET_OPEN)


def seat_profiles(asset_class=None, path=None):
    """The reading port: every seat's shown name and brief for ``asset_class``.

    Nothing is frozen at import: the roster stays the single authority, an edited
    roster is picked up by the next caller, and a broken one still fails closed
    where a caller can see it. ``path`` reads that file and only that file.
    """
    set_name = profile_set_for(asset_class)
    seats = load_roster(path) if path else _shipped_roster()
    return MappingProxyType(
        {seat.seat_id: seat.profiles[set_name] for seat in seats}
    )


_SHIPPED_ROSTER = None


def _shipped_roster():
    """The shipped roster, re-read whenever the file itself changes.

    Naming one seat is one lookup, and the live debate page names a seat for
    every message it shows, so parsing the whole roster per lookup turned one
    page render into dozens of file reads. The file's own mtime and size are the
    version: edit the roster and the next lookup answers from the new one, so
    this is a cache of the current file rather than a value frozen at import.

    Two callers racing a reload is last-completion-wins, exactly as
    ``debate_rules`` documents for its own snapshot: both publish a complete,
    validated roster, so no caller can observe a half-read one.
    """
    global _SHIPPED_ROSTER
    try:
        stat = ROSTER_PATH.stat()
    except OSError:
        # Let load_roster raise its own RosterError for a roster that is not there.
        return tuple(load_roster())
    version = (stat.st_mtime_ns, stat.st_size)
    cached = _SHIPPED_ROSTER
    if cached is None or cached[0] != version:
        cached = (version, tuple(load_roster()))
        _SHIPPED_ROSTER = cached
    return cached[1]


def seat_display_names(asset_class=None, path=None):
    """``seat_id`` → the name this seat is shown under for ``asset_class``."""
    return MappingProxyType(
        {
            seat_id: profile.display_name
            for seat_id, profile in seat_profiles(asset_class, path).items()
        }
    )


def seat_identities(asset_class=None, path=None):
    """Every seat's reader-facing identity for ``asset_class``."""
    profiles = seat_profiles(asset_class, path)
    return MappingProxyType(
        {
            seat_id: SeatIdentity(
                seat_id=seat_id,
                display_name="{}・{}".format(
                    badge.provider.capitalize(), profiles[seat_id].display_name
                ),
                agent_number=badge.agent_number,
                avatar=badge.avatar,
                provider=badge.provider,
            )
            for seat_id, badge in _SEAT_BADGES.items()
        }
    )


def seat_identity(seat_id, asset_class=None):
    """Return one seat's identity, or ``None`` when the id is not one of seven.

    ``None`` rather than a made-up placeholder: a caller rendering a seat this
    build has never heard of has to decide for itself what to show, and the
    callers make different choices about that.
    """
    if seat_id not in _SEAT_BADGES:
        return None
    return seat_identities(asset_class)[seat_id]


class _RosterView(Mapping):
    """A read-through mapping over the seven seats, resolved from the roster.

    The web app still reads seat labels and identities as module-level mappings;
    it moves to the asset-class-aware port in Ticket 03. Until then these names
    stay importable, but as a view over the roster rather than a second copy of
    it: no seat name is frozen at import, and a roster that cannot be read fails
    closed at use with :class:`RosterError`.

    Only the seven fixed ids are keys, so ``get`` on anything else returns the
    caller's own default exactly as the frozen dictionaries did.
    """

    def __init__(self, resolve):
        self._resolve = resolve

    def __getitem__(self, seat_id):
        if seat_id not in SEAT_IDS:
            raise KeyError(seat_id)
        return self._resolve()[seat_id]

    def __iter__(self):
        return iter(SEAT_IDS)

    def __len__(self):
        return len(SEAT_IDS)


#: Transitional open-set views for callers that have not moved to the port yet.
SEAT_IDENTITIES = _RosterView(seat_identities)
SEAT_DISPLAY_NAMES = _RosterView(seat_display_names)


def load_roster(path=None):
    """Load and verify the seat roster, returning seats in approved order.

    Every refusal is a :class:`RosterError` naming what is wrong, including the
    ones that are the file's shape rather than its content: a roster that is not
    JSON, or is a list, or lists a seat twice, is a deployment mistake an operator
    has to be able to read, not a ``JSONDecodeError`` from inside a run.

    The file is read exactly once, and :func:`roster_seats` verifies *that* text
    — see its own note on why a caller that already holds the document must not
    hand over a path instead.
    """
    roster_path = Path(path) if path else ROSTER_PATH
    return roster_seats(_roster_document(roster_path), roster_path)


def roster_seats(document, source=ROSTER_PATH):
    """Verify one already-parsed roster document; return its seats in order.

    Callers that have read the roster for their own reasons — the competition
    preflight reads it for the provider policy — verify **the document they are
    going to use** by passing it here. Re-opening the path instead would verify a
    second read of the file: the two can differ, and then the checked bytes are
    not the answered ones. ``source`` only names the file in refusals.
    """
    if not isinstance(document, dict):
        raise RosterError("roster 設定 {} 最外層必須是物件。".format(source))
    if not isinstance(document.get("seats"), list):
        raise RosterError("roster 設定 {} 的 seats 必須是陣列。".format(source))

    seats = {}
    for index, entry in enumerate(document["seats"]):
        seat = _seat(index, entry)
        if seat.seat_id in seats:
            raise RosterError("roster 重複列出席位 {}。".format(seat.seat_id))
        seats[seat.seat_id] = seat

    missing = [seat_id for seat_id in SEAT_IDS if seat_id not in seats]
    if missing:
        raise RosterError("roster 缺少席位 {}。".format(", ".join(missing)))

    return [seats[seat_id] for seat_id in SEAT_IDS]


def _roster_document(roster_path):
    """The roster file read once and parsed, or a refusal naming the file.

    Shape is not judged here: that is :func:`roster_seats`'s job, so the same
    verification runs whether the document arrived from this file or from a
    caller that had already read it.
    """
    try:
        text = roster_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RosterError("找不到 roster 設定 {}。".format(roster_path)) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise RosterError(
            "roster 設定 {} 無法讀取：{}。".format(roster_path, exc)
        ) from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RosterError(
            "roster 設定 {} 不是合法 JSON：{}。".format(roster_path, exc)
        ) from exc
    return document


def _seat(index, entry):
    """One roster entry as a :class:`Seat`, or a refusal naming the entry."""
    if not isinstance(entry, dict):
        raise RosterError("roster seats[{}] 必須是物件。".format(index))
    seat_id = entry.get("seat_id")
    if seat_id not in SEAT_IDS:
        raise RosterError("roster 含有未核准席位 {!r}。".format(seat_id))
    return Seat(
        seat_id=seat_id,
        focus=_required_text(seat_id, entry, "focus"),
        output_dir=_required_text(seat_id, entry, "output_dir"),
        profiles=_profiles(seat_id, entry.get("profiles")),
    )


def _required_text(seat_id, entry, field):
    """A seat-level field a run relies on, refused unless it is real text.

    ``focus`` reaches a seat's prompt and ``output_dir`` reaches a path, so a
    truthy non-string (a list, a number) is not "filled in" — it is a value that
    renders as its own repr in front of a model or in a directory name.
    """
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RosterError("席位 {} 缺少 {}。".format(seat_id, field))
    return value


def _profiles(seat_id, raw):
    """Read one seat's three profile sets, refusing anything incomplete.

    A missing set or a blank field is named down to the field, because the only
    other way to render that seat would be to invent a name for it, a brief for
    it, or the sentence that tells a reader what it does. The fields are read by
    name from :data:`_PROFILE_FIELDS`, so the order the file happens to use is
    not part of the contract.
    """
    if not isinstance(raw, dict):
        raise RosterError("席位 {} 缺少 profiles 套組設定。".format(seat_id))
    profiles = {}
    for set_name in PROFILE_SETS:
        entry = raw.get(set_name)
        if not isinstance(entry, dict):
            raise RosterError("席位 {} 缺少 {} 套組設定。".format(seat_id, set_name))
        for field in _PROFILE_FIELDS:
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise RosterError(
                    "席位 {} 的 {} 套組缺少 {}。".format(seat_id, set_name, field)
                )
        profiles[set_name] = SeatProfile(
            **{field: entry[field] for field in _PROFILE_FIELDS}
        )
    return MappingProxyType(profiles)
