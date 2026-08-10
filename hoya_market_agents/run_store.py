"""Immutable, file-based run store (ADR 0002, ADR 0005).

Every run owns a fresh ``<Data Root>/runs/<Taipei date>/<HHMM-slug-hash>/``
directory. Shared artifacts are written exactly once by this single writer and
hashed, so a later run can never overwrite an earlier report, evidence set or
debate record. ``runs/latest.json`` is the only mutable file; it is a
convenience pointer, not an audit record.

The directory name is a label for a human browsing ``runs/`` — the date and
the question are what someone looking for last Tuesday's analysis actually
remembers. The identity of a run is still its ``run_id``, which carries a UTC
timestamp and is what every artifact records; :func:`resolve_run_dir` is how a
caller holding only a run id gets back to its directory.

The two are tied together by the ``hash`` the name ends in, which is a digest
of the **whole** run id. That is what makes the tie exact: a run id differing
by one second, or naming a different asset, hashes somewhere else and cannot
land on this run's directory. The label in the middle is free text and takes
no part in identity — which is also why occupying a run id is a separate,
atomic step (see :meth:`RunStore.create_run`) rather than something the
directory name could enforce on its own.
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path

from .question import MAX_ASSET_SLUG_BYTES

RUN_ID_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_TOKEN_BYTES = 3

# The date folder is the user's local date: someone looking for "yesterday's
# run" means yesterday where they are. Uniqueness is not this value's job —
# that stays with the UTC timestamp inside the run id.
#
# A fixed +08:00 rather than ``zoneinfo``: Taiwan has observed no daylight
# saving since 1980, and ``zoneinfo`` on Windows needs the external ``tzdata``
# distribution this project does not take. ``report_renderer``,
# ``report_audit_renderer`` and ``live_dashboard`` already render Taipei times
# from the same offset.
RUN_DIR_TIME_ZONE = timezone(timedelta(hours=8), "Asia/Taipei")
RUN_DIR_DATE_FORMAT = "%Y-%m-%d"
RUN_DIR_TIME_FORMAT = "%H%M"

# The directory name ends in a digest of the whole run id, not in the run id's
# own random token. Two reasons, and both are about what the name has to
# guarantee rather than about how it looks:
#
#   * A token says nothing about the rest of the id. Two run ids a second
#     apart, or naming two different assets, carry the same token whenever the
#     token is reused, and a name ending in it could not tell them apart — so
#     a lookup by run id could land on someone else's run. A digest of the
#     whole id cannot: change any part of the id and the digest moves.
#   * A token comes from the caller. A digest is generated here, so the tail
#     of the name is entirely this module's own. What the caller wrote reaches
#     only the label in the middle, and only after normalisation and the
#     allowlist in ``run_dir_slug`` — a question about ``私A`` really does put
#     ``私a`` in the name.
#
# 64 bits: two distinct run ids inside one Taipei minute would have to collide
# on all of them, and a collision is a refusal (see ``create_run``), never an
# overwrite.
RUN_DIR_HASH_LENGTH = 16

# Bytes of randomness written inside each claim so that one acquisition can be
# told from another. An inode number cannot do that job on its own: it is
# reused as soon as the file holding it goes away.
_CLAIM_NONCE_BYTES = 16

# Tokens are the random tail of a run id. ``default_token`` makes lower-case
# hex; the bound is the 32 characters ``question.MAX_ASSET_SLUG_BYTES``
# already budgets for, so enforcing it here is enforcing the arithmetic that
# module did rather than inventing a second limit. A separator cannot appear
# because the token is what follows the last one.
_RUN_ID_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9]{1,32}$")

# ``question.MAX_ASSET_SLUG_BYTES`` is NAME_MAX minus what a run id spends on
# its 16-byte UTC timestamp, its 32-byte token budget and two separators. A
# run directory name spends the same two separators, a 4-byte ``HHMM`` in
# place of the timestamp and a 16-byte digest in place of the token, so its
# budget is that same derivation plus what those two swaps give back. One
# derivation, not two — which is also why every asset slug intake accepts
# still fits in a directory name.
MAX_RUN_DIR_SLUG_BYTES = (
    MAX_ASSET_SLUG_BYTES
    + (len("20260314T015926Z") - len("HHMM"))
    + (32 - RUN_DIR_HASH_LENGTH)
)

_RUN_ID_STAMP_PATTERN = re.compile(r"^(\d{8}T\d{6}Z)-(.*)$")
_SLUG_SEPARATOR_RUNS = re.compile(r"-+")


class RunStoreError(Exception):
    """Base error for run store violations."""


class RunAlreadyExistsError(RunStoreError):
    """Raised when a run id would reuse an existing run directory."""


class ArtifactAlreadyExistsError(RunStoreError):
    """Raised when an already-written artifact would be overwritten."""


class ArtifactTamperedError(RunStoreError):
    """Raised when an artifact no longer matches its recorded digest."""


class SnapshotSealedError(RunStoreError):
    """Raised when code attempts to replace the sealed evidence snapshot."""


class FormatRepairSemanticChangeError(RunStoreError):
    """Raised when a format-only repair changes decoded content."""


def validate_format_only_change(before_text, after_text):
    """Return whether two JSON texts differ only in tolerated formatting."""
    try:
        before_value = _load_json_with_trailing_comma_repair(before_text)
        after_value = json.loads(after_text)
    except (TypeError, json.JSONDecodeError):
        return False
    return _typed_json(before_value) == _typed_json(after_value)


def default_token():
    """Return the short random component of a run id."""
    return secrets.token_hex(_TOKEN_BYTES)


def new_run_id(started_at_utc, asset_slug, token=None):
    """Build a run id of ``<UTC start>-<asset slug>-<short token>``."""
    stamp = started_at_utc.strftime(RUN_ID_TIMESTAMP_FORMAT)
    return "{}-{}-{}".format(stamp, asset_slug, token or default_token())


def run_dir_slug(text, max_bytes=MAX_RUN_DIR_SLUG_BYTES):
    """Reduce ``text`` to the label part of one directory name.

    The rule is one line: a character stays if it is alphanumeric, or if it is
    a combining mark on something that stayed; everything else becomes a
    separator. It is stated that way, rather than as a list of what to remove,
    because the list of characters a question might contain is not something
    anyone can finish writing down — but what a *name* may contain is.

    Everything Win32 refuses inside a path component falls outside that rule
    and therefore cannot survive: the reserved punctuation ``<>:"/\\|?*``, the
    control range, the separators themselves, and the trailing dot or space
    Windows silently drops. None of those is a letter, a digit or a mark.
    (Reserved device names are a rule about whole names, not characters; see
    :func:`run_dir_parts`, which is where a name is assembled.)

    The marks are why the rule is not simply ``isalnum``. Devanagari writes its
    vowels as marks — ``बिटकॉइन`` is seven code points of which three are marks
    — and Arabic writes its diacritics the same way. Dropping them does not
    shorten the label, it shreds it: ``बिटकॉइन`` came out as ``ब-टक-इन``, which
    is no longer the word anyone asked about, and a folder nobody can browse by
    question is the one thing this label exists to avoid. A mark is only kept
    where it has a base to attach to, so a stray one after punctuation is still
    a separator.

    The result is case-folded and capped at ``max_bytes`` UTF-8 bytes, cut on a
    character boundary. It is empty when the text holds nothing to keep.
    """
    folded = unicodedata.normalize("NFKC", text or "").casefold()
    # Case folding can leave the string denormalised.
    folded = unicodedata.normalize("NFKC", folded)
    kept = []
    attached = False
    for character in folded:
        if character.isalnum():
            kept.append(character)
            attached = True
        elif attached and unicodedata.category(character).startswith("M"):
            kept.append(character)
        else:
            kept.append("-")
            attached = False
    slug = _SLUG_SEPARATOR_RUNS.sub("-", "".join(kept)).strip("-")
    encoded = slug.encode("utf-8")
    if len(encoded) > max_bytes:
        slug = encoded[:max_bytes].decode("utf-8", "ignore").strip("-")
    return slug


def run_dir_hash(run_id):
    """Return the digest of a whole run id that ends its directory name."""
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:RUN_DIR_HASH_LENGTH]


def run_dir_parts(run_id, question=None):
    """Return ``(date folder, directory name)`` for one run.

    The date folder is the run's Taipei date and the name opens with its
    Taipei ``HHMM``, so a day's runs sort chronologically inside the folder
    that a user would look in for them. Between the time and the digest sits
    the label: the question when one is given, and the run id's own slug
    otherwise — a question is not always available at the point a run
    directory is created.

    The time and the digest are computed here; the label is the only part the
    caller's own words reach, and they reach it only after normalisation and
    the allowlist in :func:`run_dir_slug` — so a question about ``私A`` really
    does put ``私a`` in the name, and a question's punctuation, controls and
    separators really do not. The run id's token is not in the name at all;
    it is spent on the digest.

    That is what settles the DOS device names: a name always begins with four
    digits and a separator and always ends with sixteen hex characters, so the
    part Windows compares against ``CON``, ``PRN``, ``AUX``, ``NUL``,
    ``COM1``–``COM9`` and ``LPT1``–``LPT9`` is never one of them, whatever the
    question or the token said. Nothing needs to be filtered out for that to
    hold, and ``CONSENSUS`` keeps its name. A malformed run id is refused
    here, before anything is created.
    """
    parsed = _split_run_id(run_id)
    if parsed is None:
        raise RunStoreError(
            "run_id {!r} 不是 {}-slug-token 形狀（UTC 時戳＋1–32 個英數字的"
            "token）；無法決定日期分層目錄，fail closed。".format(
                run_id, RUN_ID_TIMESTAMP_FORMAT
            )
        )
    started_at_utc, id_slug, _ = parsed
    local = started_at_utc.astimezone(RUN_DIR_TIME_ZONE)
    slug = run_dir_slug(question) if question else run_dir_slug(id_slug)
    fields = [local.strftime(RUN_DIR_TIME_FORMAT)]
    if slug:
        fields.append(slug)
    fields.append(run_dir_hash(run_id))
    return local.strftime(RUN_DIR_DATE_FORMAT), "-".join(fields)


def resolve_run_dir(data_root, run_id):
    """Return the directory holding ``run_id``'s run, or ``None``.

    A directory name carries the question, which a caller holding only a run
    id does not have, so the run is found by the parts of the name the run id
    does fix: its Taipei date and time, and the digest of the whole id. That
    digest is what makes the answer this run's and no other's — a run id one
    second later, or naming another asset, hashes elsewhere and matches
    nothing here.

    ``None`` comes back for a run id that is not well formed, and for one that
    finds no directory.
    """
    parsed = _split_run_id(run_id)
    if parsed is None:
        return None
    started_at_utc, _, _ = parsed
    local = started_at_utc.astimezone(RUN_DIR_TIME_ZONE)
    date_dir = Path(data_root) / "runs" / local.strftime(RUN_DIR_DATE_FORMAT)
    prefix = local.strftime(RUN_DIR_TIME_FORMAT) + "-"
    suffix = "-" + run_dir_hash(run_id)
    try:
        entries = sorted(date_dir.iterdir())
    except OSError:
        return None
    matches = [
        entry
        for entry in entries
        if entry.name.startswith(prefix)
        and entry.name.endswith(suffix)
        and entry.is_dir()
    ]
    return matches[0] if len(matches) == 1 else None


def _run_claim_path(runs_root, run_id):
    """Return the file that occupies ``run_id`` for good, once it is taken.

    It lives beside the run directories of its own day rather than in a table
    of its own, so deleting a day deletes its claims with it, and it is named
    so that it can never be read as a run: the leading dot keeps it clear of
    the ``HHMM-`` prefix every run directory name starts with, and it is a
    file, so the two readers that walk a date folder — this module's resolver
    and the dashboard's listing — skip it on ``is_dir`` alone.
    """
    started_at_utc, _, _ = _split_run_id(run_id)
    local = started_at_utc.astimezone(RUN_DIR_TIME_ZONE)
    return runs_root / local.strftime(RUN_DIR_DATE_FORMAT) / ".{}-{}.run-claim".format(
        local.strftime(RUN_DIR_TIME_FORMAT), run_dir_hash(run_id)
    )


class _RunIdAlreadyTaken(Exception):
    """The claim was already someone's, so this call owns nothing on disk.

    Carries no owner. Who holds the claim is wording for the error message and
    is read where the message is built; putting it here would mean a file read
    standing between the failure and the conclusion drawn from it.
    """


class _RunDirectoryAlreadyThere(Exception):
    """The directory predates this call, so this call must not remove it."""


def _taken_message(claim, run_id):
    """Explain a refusal. Reading the owner is for wording, never for control.

    Whether this call owns anything was settled the moment the claim could not
    be created; that answer does not depend on this file being readable, and
    nothing here may be allowed to change it. So the read lives out here in
    the message, where an interruption ends the call and touches nothing,
    rather than inside the decision.
    """
    owner = _run_claim_owner(claim)
    if owner == run_id:
        return "run {} 已被建立過；不得覆寫既有執行紀錄。".format(run_id)
    return (
        "run {} 的目錄雜湊與同一分鐘的既有 run {} 相同；"
        "換一個 token 重試。".format(run_id, owner or "（無法讀取）")
    )


def _take_run_id(claim, run_id, taken):
    """Take ``run_id`` exclusively, recording the take in ``taken``.

    The claim is written somewhere else first and then *linked* into place.
    ``link`` is atomic and refuses an existing name, so it decides the winner
    the way ``O_EXCL`` would — but it also carries the content in with it, and
    that is the point. Creating an empty file and filling it afterwards leaves
    a moment where the claim exists and names nobody, and a claim like that is
    worse than no claim at all: it occupies a run id for good and cannot say
    on whose behalf. Linking a finished file means that moment does not exist
    to be interrupted in.

    ``taken`` is filled in **before** the link, with the two things that will
    later identify this call's own work: the inode of the file about to be
    linked, and a nonce written inside it. Recording them early is deliberate.
    Neither of them is permission by itself — permission is the claim on disk
    *carrying* them, and the only thing in the world that can put them there is
    this call's own ``link`` returning. So the permission comes into existence
    with the successful link, in the same instant, with no statement afterwards
    for an interrupt to arrive in front of. Every earlier attempt at this put
    permission in a statement that ran later, and each one, in its own way,
    freed a claim belonging to a finished run.

    Two identifiers rather than one, because neither survives alone. An inode
    number is not a lasting name: unlink the scratch file and the filesystem
    is free to hand the same number to the next file it allocates, so a caller
    that failed long ago can find its old number sitting on a claim a *later*
    caller linked — measured happening on the very first reallocation. And
    contents cannot tell two callers apart at all, since both write the same
    run id. A nonce can: it is fresh per acquisition, so no other caller's
    claim carries it, however the numbers fall.

    Tidying the scratch file away must not change any of that, so its errors
    stay in here. At worst it leaves behind a name nothing enumerates; it may
    not overwrite the conclusion ``link`` already reached.

    One gap is known and accepted: an interrupt inside ``mkstemp`` itself, in
    the moment between the file existing and this frame learning its name,
    leaves a scratch file and its descriptor behind. Nothing can be released
    that was never taken and nothing can be found that is not a run, so the
    cost is a stray ``.claim-`` file until the process ends. Closing it would
    take a create whose name is known before the call, which is the thing
    ``mkstemp`` exists not to do.
    """
    nonce = secrets.token_hex(_CLAIM_NONCE_BYTES)
    descriptor, scratch = tempfile.mkstemp(prefix=".claim-", dir=str(claim.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_claim_bytes(run_id, nonce))
            stream.flush()
            os.fsync(stream.fileno())
        taken.append((os.stat(scratch).st_ino, nonce))
        try:
            os.link(scratch, claim)
        except FileExistsError as exc:
            raise _RunIdAlreadyTaken() from exc
    finally:
        _discard_quietly(scratch)


def _claim_bytes(run_id, nonce):
    """A claim's contents: who holds the id, and which acquisition took it."""
    return "{}\n{}\n".format(run_id, nonce).encode("utf-8")


def _read_claim(claim):
    """Return ``(run id, nonce)`` from a claim, with ``None`` for what is missing."""
    try:
        lines = claim.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None, None
    owner = lines[0].strip() if lines else ""
    nonce = lines[1].strip() if len(lines) > 1 else ""
    return owner or None, nonce or None


def _discard_quietly(scratch):
    """Remove a scratch file without letting its failure speak for the call."""
    try:
        os.unlink(scratch)
    except OSError:
        pass


def _remove_started_directory(run_path):
    """Take back a run directory this call began and could not finish.

    It holds no artifacts — the first writer runs long after — but it does
    carry the digest, so leaving it behind would leave a directory that
    answers this run id's lookup and contains nothing, which is worse than any
    amount of clutter. Whether the directory was ever created is not asked:
    ``rmtree`` on a path that was never made is a ``FileNotFoundError`` and
    nothing else.

    Failing is not reported. Whether the directory is gone is not something
    the caller should be told and then have to remember — it is something the
    disk still knows at the moment it matters, and :func:`_release_run_id`
    asks it there.
    """
    try:
        shutil.rmtree(run_path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _release_run_id(claim, run_id, taken, started_directory):
    """Hand back a run id, and only ever the one this call took.

    This is the only place in the module that unlinks a claim. Permission is
    one thing: the file standing at ``claim`` is the file this call linked
    there. ``taken`` says which one that would be — an inode and a nonce — and
    the disk says what is actually there. Both must agree, and only this
    call's own link could ever have made them.

    Both are needed. An inode number is reusable, so an old failed call can
    find its number on a claim a later caller linked; a nonce is fresh per
    acquisition, so it cannot appear on anyone else's claim. Contents alone
    identify nothing, since two callers of the same run id write the same run
    id — granting on that was a bug, twice.

    A failure before the link therefore finds no agreement: ``taken`` is
    empty, or the claim is absent, or it is somebody else's file. A failure
    after the link finds agreement at once, with no window in between, because
    the link is what created it.

    ``started_directory`` is a **veto and never a grant**. If this call's
    half-built directory is still on disk, the id stays occupied whatever else
    is true — released, it would let a retry build a second directory
    answering the same lookup. Asking the disk here, rather than remembering
    an earlier answer, is what keeps that guarantee out of reach of an
    interrupt: there is no moment where the code has decided to keep the id
    and has not yet acted on it. It is passed as ``None`` when the directory
    at that path is known not to be this call's.

    Beyond that, this reads the disk as a single writer's workspace: Data Root
    has one writer, and a second process swapping a path for another file
    between the check and the unlink is outside what any check here can see.
    """
    if not taken:
        return
    if started_directory is not None and started_directory.exists():
        return
    inode, nonce = taken[0]
    try:
        if claim.stat().st_ino != inode:
            return
    except OSError:
        return
    owner, written = _read_claim(claim)
    if owner != run_id or written != nonce:
        return
    claim.unlink(missing_ok=True)


def _run_claim_owner(claim):
    """Return the run id a claim file records, or ``None`` if unreadable."""
    return _read_claim(claim)[0]


def _split_run_id(run_id):
    """Return ``(UTC start, slug, token)``, or ``None`` if this is not a run id."""
    if not isinstance(run_id, str):
        return None
    matched = _RUN_ID_STAMP_PATTERN.match(run_id)
    if matched is None:
        return None
    try:
        started = datetime.strptime(matched.group(1), RUN_ID_TIMESTAMP_FORMAT)
    except ValueError:
        return None
    slug, _, token = matched.group(2).rpartition("-")
    if not _RUN_ID_TOKEN_PATTERN.match(token):
        return None
    return started.replace(tzinfo=timezone.utc), slug, token


class RunDirectory:
    """A single run's write-once directory."""

    def __init__(self, run_id, path, data_root):
        self.run_id = run_id
        self.path = path
        self.data_root = Path(data_root)
        self.artifact_hashes = {}
        self.artifact_sources = {}
        self._lock = threading.Lock()

    def seat_dir(self, seat_id):
        return self.path / "agents" / _safe_segment(seat_id, "seat_id")

    def write_json(self, name, payload, source="run controller"):
        return self.write_text(
            name,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            source=source,
        )

    def write_jsonl(self, name, records, source="run controller"):
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
        return self.write_text(name, text, source=source)

    def write_text(self, name, text, source="run controller"):
        relative = _safe_relative(name)
        target = self.path / relative
        try:
            _atomic_write_once(target, text.encode("utf-8"))
        except FileExistsError as exc:
            raise ArtifactAlreadyExistsError(
                "{} 已存在於 {}；run artifacts 不得覆寫。".format(relative, self.path)
            ) from exc
        self._record_artifact(relative, text.encode("utf-8"), source)
        return target

    def record_attempt(self, seat_id, attempt_id, raw_text, validated_payload):
        """Atomically commit one valid attempt and adopt only the first success."""
        seat_id = _safe_segment(seat_id, "seat_id")
        attempt_id = _safe_segment(attempt_id, "attempt_id")
        seat = self.seat_dir(seat_id)
        if not seat.is_dir():
            raise RunStoreError("未知 seat_id：{}".format(seat_id))
        attempts = seat / "attempts"
        target = attempts / attempt_id
        temporary = Path(tempfile.mkdtemp(prefix=".attempt-", dir=str(attempts)))
        raw_bytes = raw_text.encode("utf-8")
        validated_bytes = (
            json.dumps(validated_payload, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        try:
            (temporary / "raw.txt").write_bytes(raw_bytes)
            (temporary / "validated.json").write_bytes(validated_bytes)
            os.rename(temporary, target)
        except FileExistsError as exc:
            _remove_empty_attempt_temp(temporary)
            raise ArtifactAlreadyExistsError(
                "attempt {} 已存在；不得覆寫。".format(attempt_id)
            ) from exc
        except Exception:
            _remove_empty_attempt_temp(temporary)
            raise

        raw_name = target.relative_to(self.path).as_posix() + "/raw.txt"
        validated_name = target.relative_to(self.path).as_posix() + "/validated.json"
        self._record_artifact(raw_name, raw_bytes, "raw provider output")
        self._record_artifact(validated_name, validated_bytes, "validated attempt output")

        adopted_name = "agents/{}/adopted.json".format(seat_id)
        try:
            self.write_json(
                adopted_name,
                {
                    "run_id": self.run_id,
                    "seat_id": seat_id,
                    "attempt_id": attempt_id,
                    "validated_path": validated_name,
                },
                source="first valid attempt selection",
            )
            return True
        except ArtifactAlreadyExistsError:
            self.write_json(
                "diagnostics/attempts/{}.json".format(attempt_id),
                {
                    "run_id": self.run_id,
                    "seat_id": seat_id,
                    "attempt_id": attempt_id,
                    "reason": "not_adopted_first_valid_already_selected",
                    "validated_path": validated_name,
                },
                source="non-adopted valid attempt",
            )
            return False

    def seal_evidence_snapshot(self, records, sealed_at_utc, elapsed_ms):
        """Write and seal the T+4 evidence snapshot exactly once."""
        snapshot_name = "snapshots/evidence.jsonl"
        text = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        try:
            self.write_text(snapshot_name, text, source="validated evidence merge at T+4")
            metadata = {
                "run_id": self.run_id,
                "path": snapshot_name,
                "sha256": digest,
                "sealed_at_utc": sealed_at_utc,
                "elapsed_ms": elapsed_ms,
                "record_count": len(records),
            }
            self.write_json(
                "snapshots/evidence.snapshot.json",
                metadata,
                source="evidence snapshot seal",
            )
            return metadata
        except ArtifactAlreadyExistsError as exc:
            raise SnapshotSealedError("Evidence snapshot 已 sealed，不得覆寫。") from exc

    def verify_evidence_snapshot(self):
        """Return seal metadata after verifying snapshot content."""
        seal_path = self.path / "snapshots" / "evidence.snapshot.json"
        if not seal_path.is_file():
            raise SnapshotSealedError("找不到 Evidence snapshot seal。")
        metadata = json.loads(seal_path.read_text(encoding="utf-8"))
        self._verify_digest(metadata["path"], metadata["sha256"])
        return metadata

    def record_format_repair(
        self,
        repair_id,
        seat_id,
        source_attempt_id,
        repair_attempt_id,
        before_text,
        after_text,
        reason,
        operator,
    ):
        """Record a narrow JSON format repair without changing decoded types."""
        repair_id = _safe_segment(repair_id, "repair_id")
        seat_id = _safe_segment(seat_id, "seat_id")
        source_attempt_id = _safe_segment(source_attempt_id, "source_attempt_id")
        repair_attempt_id = _safe_segment(repair_attempt_id, "repair_attempt_id")
        if not isinstance(reason, str) or not reason.strip():
            raise RunStoreError("Format Repair 的 reason 不得為空。")
        if not isinstance(operator, str) or not operator.strip():
            raise RunStoreError("Format Repair 的 reason 與 operator 不得為空。")
        source_name = "agents/{}/attempts/{}/raw.txt".format(seat_id, source_attempt_id)
        source_path = self.path / source_name
        if not source_path.is_file():
            raise RunStoreError(
                "Format Repair 找不到 source attempt {}。".format(source_attempt_id)
            )
        source_bytes = source_path.read_bytes()
        if not isinstance(before_text, str) or source_bytes != before_text.encode("utf-8"):
            raise FormatRepairSemanticChangeError(
                "before_text 與 source attempt raw output 不一致；fail closed。"
            )
        if not validate_format_only_change(before_text, after_text):
            raise FormatRepairSemanticChangeError("Format Repair 改變市場語意；fail closed。")

        record_name = "diagnostics/format-repairs/{}.json".format(repair_id)
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        payload = {
            "run_id": self.run_id,
            "repair_id": repair_id,
            "seat_id": seat_id,
            "reason": reason,
            "operator": operator,
            "before": {
                "text": before_text,
                "path": source_name,
                "sha256": source_sha256,
            },
            "after": {
                "text": after_text,
                "path": record_name,
                "json_pointer": "/after/text",
                "sha256": hashlib.sha256(after_text.encode("utf-8")).hexdigest(),
            },
            "lineage": {
                "source_attempt_id": source_attempt_id,
                "source_path": source_name,
                "source_sha256": source_sha256,
                "repair_attempt_id": repair_attempt_id,
            },
        }
        return self.write_json(
            record_name,
            payload,
            source="format repair lineage",
        )

    def artifact_index(self):
        """Return the manifest-ready hash and source index."""
        with self._lock:
            return {
                name: {
                    "path": name,
                    "sha256": digest,
                    "source": self.artifact_sources[name],
                }
                for name, digest in sorted(self.artifact_hashes.items())
            }

    def verify_artifacts(self, artifact_index):
        """Fail closed if any indexed artifact was changed or removed."""
        for name, entry in artifact_index.items():
            if entry.get("path") != name:
                raise ArtifactTamperedError("artifact index path 不一致：{}".format(name))
            self._verify_digest(name, entry.get("sha256"))
        return True

    def append_event(self, event):
        """Append one complete JSON event line without rewriting earlier events."""
        line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        target = self.path / "events.jsonl"
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(target, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, line)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            content = target.read_bytes()
            self.artifact_hashes["events.jsonl"] = hashlib.sha256(content).hexdigest()
            self.artifact_sources["events.jsonl"] = "append-only run events"
        return target

    def _record_artifact(self, name, content, source):
        with self._lock:
            self.artifact_hashes[str(name)] = hashlib.sha256(content).hexdigest()
            self.artifact_sources[str(name)] = source

    def _verify_digest(self, name, expected):
        relative = _safe_relative(name)
        target = self.path / relative
        if not target.is_file():
            raise ArtifactTamperedError("artifact 遺失：{}".format(relative))
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            raise ArtifactTamperedError(
                "artifact hash 不符：{} expected={} actual={}".format(
                    relative, expected, actual
                )
            )


class RunStore:
    """Creates and guards run directories under a Data Root."""

    def __init__(self, data_root):
        self.data_root = Path(data_root)

    @property
    def runs_root(self):
        return self.data_root / "runs"

    def create_run(self, run_id, seat_ids, question=None):
        """Make one run's directory under its Taipei date, or fail closed.

        ``question`` only names the directory. Two runs of the same question
        are still two runs, and a second run under an id that already has a
        directory is refused however differently it is worded.

        Occupying the run id is its own step, and it has to be, because the
        directory name cannot do the job. Two callers racing on one id write
        two different questions, so they build two different names, and
        ``mkdir`` sees no collision at all: both succeed, and afterwards each
        of the two directories matches the other's lookup, so the id resolves
        to neither. Reading first and creating second has the same hole with a
        wider window. So the id is claimed by linking one finished file into a
        name that only the id decides (see :func:`_take_run_id`) — the kernel
        picks the winner, and there is no interval between the check and the
        claim for a second caller to fit inside.

        The claim outlives the call. It is what makes a run id single-use for
        good rather than only while two callers happen to overlap. It is
        released only when this call leaves no run directory behind, and the
        half-built directory goes with it — a corpse under the same digest
        would be a second answer to this id's lookup, which is the very thing
        the claim exists to prevent. If the corpse cannot be removed the id
        stays occupied instead.

        **Taking the id and building the directory are two separate pieces of
        cleaning up, and they are nested rather than sequential.** Which one
        may run has to follow from something already true when the failure
        happened, not from how far the code got or from which private
        exception it managed to construct — building an exception can be
        interrupted too, and an interrupted classification that fell through
        to "clean everything" once deleted a finished run.

        So the outer region owns the claim and the inner region owns the
        directory, and the inner one is entered only after :func:`_take_run_id`
        has *returned*. Anything that goes wrong while taking the id, up to
        and including an interrupt in the middle of it, reaches only the outer
        region, which touches nothing but the claim. Nothing outside the inner
        region can remove a directory, and nothing enters the inner region
        without owning the claim outright.

        **And the outer region may only touch the claim it took.** Not the
        claim that happens to be there, not the claim whose contents name this
        run id, not "the claim, because no directory of mine turned up" —
        those all describe something a *different* run of this same id would
        satisfy just as well, and each of them, tried in turn, freed a claim
        that belonged to a finished run.

        The permission is the claim on disk carrying the nonce
        :func:`_take_run_id` minted for this acquisition. **It is put into
        ``taken`` before the link, deliberately** — do not "fix" that by
        moving it after, which is its own way of losing a claim to an
        interrupt. What makes the permission real is not the list but the
        claim actually carrying that nonce, and the only thing that can put it
        there is this call's own link.

        One thing can *veto* the release without ever granting it: this call's
        half-built directory still standing on disk. That is asked of the
        filesystem at the moment of release rather than remembered from the
        attempt to remove it, so there is no instant where the decision to
        keep the id exists only as something still to be done.

        A refusal can also mean two different run ids whose digests collided.
        The claim records its owner, so the error says which of the two it is
        instead of guessing — but only as wording. Reading it decides nothing.
        Callers that must have an id, like the launch reservation loop, retry
        with a fresh token.
        """
        run_id = _safe_segment(run_id, "run_id")
        seat_ids = tuple(_safe_segment(item, "seat_id") for item in seat_ids)
        if len(seat_ids) != len(set(seat_ids)):
            raise RunStoreError("seat_ids 不得重複。")
        # 這一行會先驗完整個 run_id；在它之後才會有任何目錄被建立。
        date_dir, run_dir_name = run_dir_parts(run_id, question)
        claim = _run_claim_path(self.runs_root, run_id)
        run_path = self.runs_root / date_dir / run_dir_name
        claim.parent.mkdir(parents=True, exist_ok=True)
        # `_take_run_id` 會在 link 之前把「候選 inode ＋ nonce」放進來；授權不是
        # 這個清單本身，而是磁碟上的 claim 真的帶著這兩樣東西——只有這次呼叫的
        # link 做得到。空清單＝什麼都沒取得，後面任何清理路徑都碰不到 claim。
        taken = []
        try:
            _take_run_id(claim, run_id, taken)
            # --- 以下才是「目錄」的收拾範圍；claim 到手才會進來 ---
            try:
                try:
                    run_path.mkdir(exist_ok=False)
                except FileExistsError as exc:
                    raise _RunDirectoryAlreadyThere() from exc
                for seat_id in seat_ids:
                    (run_path / "agents" / seat_id / "attempts").mkdir(
                        parents=True, exist_ok=False
                    )
                for name in ("snapshots", "reports", "late", "diagnostics"):
                    (run_path / name).mkdir(exist_ok=False)
            except _RunDirectoryAlreadyThere:
                # 目錄比這次呼叫還早存在，不是這次建的：交給外層只還 claim。
                raise
            except BaseException:
                _remove_started_directory(run_path)
                raise
        except _RunIdAlreadyTaken as already:
            # claim 本來就是別人的。磁碟上沒有一樣東西屬於這次呼叫。
            raise RunAlreadyExistsError(_taken_message(claim, run_id)) from already
        except _RunDirectoryAlreadyThere as blocked:
            # 目錄不是這次建的，所以它的存在不該擋住 run id 的歸還。
            _release_run_id(claim, run_id, taken, None)
            raise RunAlreadyExistsError(
                "run 目錄 {} 已存在；不得覆寫既有執行紀錄。".format(run_path)
            ) from blocked
        except BaseException:
            _release_run_id(claim, run_id, taken, run_path)
            raise
        return RunDirectory(run_id, run_path, self.data_root)

    def point_latest_at(self, run):
        payload = {
            "run_id": run.run_id,
            "run_dir": str(run.path),
            "report_md": str(run.path / "report.md"),
            "report_html": str(run.path / "report.html"),
            "debate_html": str(run.path / "debate.html"),
        }
        target = self.runs_root / "latest.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp, target)
        return target


def deduplicate_evidence(cards):
    """Keep the first card per declared origin, falling back to canonical URL."""
    unique = []
    duplicates = []
    first_by_origin = {}
    for card in cards:
        origin = card.get("source_origin")
        key = ("origin", origin.strip().casefold()) if isinstance(origin, str) and origin.strip() else (
            "url",
            _canonical_url(card.get("source_url", "")),
        )
        if key in first_by_origin:
            duplicates.append(
                {
                    "evidence_id": card.get("evidence_id"),
                    "duplicate_of": first_by_origin[key],
                }
            )
            continue
        first_by_origin[key] = card.get("evidence_id")
        unique.append(card)
    return unique, duplicates


def _canonical_url(value):
    if not isinstance(value, str) or not value.strip():
        return ""
    parts = urlsplit(value.strip())
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), parts.query, "")
    )


def _safe_segment(value, label):
    if not isinstance(value, str) or not value or value in (".", ".."):
        raise RunStoreError("{} 必須為安全的非空路徑片段。".format(label))
    if Path(value).name != value or "/" in value or "\\" in value:
        raise RunStoreError("{} 不得包含路徑。".format(label))
    return value


def _safe_relative(value):
    path = Path(value)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise RunStoreError("artifact 名稱必須位於 run 目錄內：{}".format(value))
    return path.as_posix()


def _atomic_write_once(target, content):
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".write-", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_name, target)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _remove_empty_attempt_temp(path):
    for child in path.iterdir():
        child.unlink()
    path.rmdir()


def _load_json_with_trailing_comma_repair(text):
    """Decode JSON after removing only commas directly before ``}`` or ``]``."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = _remove_trailing_commas(text)
        if repaired == text:
            raise
        return json.loads(repaired)


def _remove_trailing_commas(text):
    output = []
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            output.append(character)
            continue
        if character == ",":
            following = index + 1
            while following < len(text) and text[following].isspace():
                following += 1
            if following < len(text) and text[following] in "}]":
                continue
        output.append(character)
    return "".join(output)


def _typed_json(value):
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", value)
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, list):
        return ("list", tuple(_typed_json(item) for item in value))
    return (
        "object",
        tuple(sorted((key, _typed_json(item)) for key, item in value.items())),
    )
