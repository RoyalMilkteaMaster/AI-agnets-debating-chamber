"""Immutable, file-based run store (ADR 0002).

Every run owns a fresh ``<Data Root>/runs/<run_id>/`` directory. Shared
artifacts are written exactly once by this single writer and hashed, so a later
run can never overwrite an earlier report, evidence set or debate record.
``runs/latest.json`` is the only mutable file; it is a convenience pointer, not
an audit record.
"""

import hashlib
import json
import os
import secrets
import tempfile
import threading
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path

RUN_ID_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_TOKEN_BYTES = 3


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


class RunDirectory:
    """A single run's write-once directory."""

    def __init__(self, run_id, path):
        self.run_id = run_id
        self.path = path
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
        """Write and seal the T+5 evidence snapshot exactly once."""
        snapshot_name = "snapshots/evidence.jsonl"
        text = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        try:
            self.write_text(snapshot_name, text, source="validated evidence merge at T+5")
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

    def create_run(self, run_id, seat_ids):
        run_id = _safe_segment(run_id, "run_id")
        seat_ids = tuple(_safe_segment(item, "seat_id") for item in seat_ids)
        if len(seat_ids) != len(set(seat_ids)):
            raise RunStoreError("seat_ids 不得重複。")
        run_path = self.runs_root / run_id
        try:
            run_path.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise RunAlreadyExistsError(
                "run 目錄 {} 已存在；不得覆寫既有執行紀錄。".format(run_path)
            ) from exc
        for seat_id in seat_ids:
            (run_path / "agents" / seat_id / "attempts").mkdir(
                parents=True, exist_ok=False
            )
        for name in ("snapshots", "reports", "late", "diagnostics"):
            (run_path / name).mkdir(exist_ok=False)
        return RunDirectory(run_id, run_path)

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
