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
from pathlib import Path

RUN_ID_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_TOKEN_BYTES = 3


class RunStoreError(Exception):
    """Base error for run store violations."""


class RunAlreadyExistsError(RunStoreError):
    """Raised when a run id would reuse an existing run directory."""


class ArtifactAlreadyExistsError(RunStoreError):
    """Raised when an already-written artifact would be overwritten."""


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

    def seat_dir(self, seat_id):
        return self.path / "agents" / seat_id

    def write_json(self, name, payload):
        return self.write_text(name, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def write_jsonl(self, name, records):
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
        return self.write_text(name, text)

    def write_text(self, name, text):
        target = self.path / name
        if target.exists():
            raise ArtifactAlreadyExistsError(
                "{} 已存在於 {}；run artifacts 不得覆寫。".format(name, self.path)
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.artifact_hashes[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return target


class RunStore:
    """Creates and guards run directories under a Data Root."""

    def __init__(self, data_root):
        self.data_root = Path(data_root)

    @property
    def runs_root(self):
        return self.data_root / "runs"

    def create_run(self, run_id, seat_ids):
        run_path = self.runs_root / run_id
        try:
            run_path.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise RunAlreadyExistsError(
                "run 目錄 {} 已存在；不得覆寫既有執行紀錄。".format(run_path)
            ) from exc
        for seat_id in seat_ids:
            (run_path / "agents" / seat_id).mkdir(parents=True, exist_ok=False)
        return RunDirectory(run_id, run_path)

    def point_latest_at(self, run):
        payload = {
            "run_id": run.run_id,
            "run_dir": str(run.path),
            "report_md": str(run.path / "report.md"),
            "report_html": str(run.path / "report.html"),
        }
        target = self.runs_root / "latest.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp, target)
        return target
