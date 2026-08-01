"""Fail-closed handoff between a Codex Core and three GPT research threads.

Python validates and stores metadata supplied by Core.  It deliberately has no
agent creation or model invocation code: persistent Codex threads are owned by
the Core Agent that invoked the repo-local skill.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from .prompt_builder import build_seat_prompt, load_research_snapshot
from .question_package import (
    COMPARISON_STANCES,
    EVENT_STANCES,
    MARKET_STANCES,
    QuestionPackage,
    build_question_package,
)
from .run_store import ArtifactAlreadyExistsError, RunStoreError
from .seats import CODE_ROOT, load_roster

CODEX_SEAT_IDS = ("spot-technical", "derivatives", "onchain")
CORE_ROLE = "core"
TARGET_MODEL = "gpt-5.6-sol"
STATUS_READY = "READY"
STATUS_NOT_READY = "NOT READY"
HANDOFF_PATH = "preflight/codex-handoff.json"

_PUBLIC_CONTINUATION_FIELDS = frozenset(
    {
        "claim_id",
        "evidence_ids",
        "stance",
        "public_reason",
        "responds_to",
        "stance_change_reason",
    }
)
_REQUIRED_CONTINUATION_FIELDS = _PUBLIC_CONTINUATION_FIELDS - {
    "stance_change_reason"
}
_PUBLIC_STANCES = frozenset(MARKET_STANCES + COMPARISON_STANCES + EVENT_STANCES)
_PUBLIC_CHECKPOINT_FIELDS = frozenset(
    {
        "checkpoint_id",
        "thread_id",
        "evidence_ids",
        "public_status",
        "provisional_stance",
    }
)

CONTRACT_TEXT = """Codex public handoff v1
- raw seat output is write-once UTF-8 and must be forwarded byte-for-byte
- Core must not summarize, change market meaning, choose a side, or change a vote
- debate continuation contains only claim_id, evidence_ids, stance,
  public_reason, responds_to, and stance_change_reason
- hidden chain-of-thought, scratchpads, and private reasoning are forbidden
"""

SOURCE_TIME_POLICY = """Research policy v1
- T+0:00..T+1:30: primary sources first
- after T+1:30: trusted secondary sources allowed when primary data is unavailable
- T+5:00: stop all new research
- every seat receives the same question, research snapshot, schema, and clock rules
"""


class CodexBridgeError(ValueError):
    """Base error for an invalid or unsafe Core/seat handoff."""


class PreflightNotReadyError(CodexBridgeError):
    """Raised when live Codex identity or thread capability is not proven."""

    status = STATUS_NOT_READY


def codex_seats():
    """Return the three fixed GPT seats in their approved order."""
    by_id = {seat.seat_id: seat for seat in load_roster()}
    return tuple(by_id[seat_id] for seat_id in CODEX_SEAT_IDS)


def build_codex_handoff(run_id, package, core, threads, created_at_utc):
    """Build a verified handoff from Core-observed live thread metadata."""
    if not isinstance(package, QuestionPackage):
        raise CodexBridgeError("question package 必須先由 build_question_package 驗證。")
    _require_non_empty(run_id, "run_id")
    _require_utc(created_at_utc, "created_at_utc")
    _validate_core(core)
    _validate_threads(threads)

    research = load_research_snapshot()
    package_value = package.to_dict()
    package_hash = _sha256_json(package_value)
    contract_hash = _sha256_text(CONTRACT_TEXT)
    policy_hash = _sha256_text(SOURCE_TIME_POLICY)
    seats = []
    shared_prompt = None

    for seat in codex_seats():
        prompt = build_seat_prompt(package, seat, "research")
        seat_shared_prompt = _bridge_shared_prompt(prompt.shared_section)
        if shared_prompt is None:
            shared_prompt = seat_shared_prompt
        elif seat_shared_prompt != shared_prompt:
            raise PreflightNotReadyError("三個 GPT 席收到的 shared prompt 不一致。")
        metadata = threads[seat.seat_id]
        attempt_id = "{}-codex-1".format(seat.seat_id)
        seats.append(
            {
                "seat_id": seat.seat_id,
                "role": seat.seat_id,
                "focus": seat.focus,
                "thread_id": metadata["thread_id"],
                "attempt_id": attempt_id,
                "target_model": TARGET_MODEL,
                "actual_model": metadata["actual_model"],
                "model_confirmed": metadata["model_confirmed"],
                "capability_confirmed": metadata["capability_confirmed"],
                "persistent": metadata["persistent"],
                "output_path": "agents/{}/attempts/{}".format(
                    seat.seat_id, attempt_id
                ),
                "shared_prompt_sha256": _sha256_text(seat_shared_prompt),
                "question_package_sha256": package_hash,
                "research_snapshot_sha256": research.sha256,
                "contract_text_sha256": contract_hash,
                "source_time_policy_sha256": policy_hash,
            }
        )

    shared_hash = _sha256_text(shared_prompt)
    return {
        "schema_version": "1.0.0",
        "status": STATUS_READY,
        "provider": "codex",
        "run_id": run_id,
        "created_at_utc": created_at_utc,
        "core": dict(core),
        "question_package": package_value,
        "question_package_sha256": package_hash,
        "research_snapshot": {
            "upstream_commit": research.upstream_commit,
            "git_blob_sha": research.git_blob_sha,
            "sha256": research.sha256,
        },
        "shared_prompt": shared_prompt,
        "shared_prompt_sha256": shared_hash,
        "contract_text": CONTRACT_TEXT,
        "contract_text_sha256": contract_hash,
        "source_time_policy": SOURCE_TIME_POLICY,
        "source_time_policy_sha256": policy_hash,
        "seats": seats,
    }


def write_codex_handoff(run, payload):
    """Write Core's preflight observation once; verification is separate."""
    return run.write_json(HANDOFF_PATH, payload, source="Core-observed Codex preflight")


def verify_codex_preflight(data_root, run_id):
    """Verify an existing Core-written artifact without creating any agent."""
    _require_safe_segment(run_id, "run_id")
    path = _validated_data_root(data_root) / "runs" / run_id / HANDOFF_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise PreflightNotReadyError("找不到有效 Codex preflight artifact。") from exc
    try:
        package = build_question_package(payload["question_package"]["question"])
        rebuilt = build_codex_handoff(
            run_id=run_id,
            package=package,
            core=payload["core"],
            threads={
                seat["seat_id"]: {
                    key: seat[key]
                    for key in (
                        "thread_id",
                        "actual_model",
                        "model_confirmed",
                        "capability_confirmed",
                        "persistent",
                    )
                }
                for seat in payload["seats"]
            },
            created_at_utc=payload["created_at_utc"],
        )
    except (KeyError, TypeError, CodexBridgeError) as exc:
        if isinstance(exc, PreflightNotReadyError):
            raise
        raise PreflightNotReadyError("Codex preflight contract 不完整：{}".format(exc)) from exc

    if payload != rebuilt:
        raise PreflightNotReadyError("Codex preflight artifact 與可驗證內容不一致。")
    return payload


def validate_continuation_message(message, previous_stance=None):
    """Accept only public, auditable debate fields and return unchanged data."""
    if not isinstance(message, dict):
        raise CodexBridgeError("public continuation 必須為 JSON object。")
    extra = set(message) - _PUBLIC_CONTINUATION_FIELDS
    missing = _REQUIRED_CONTINUATION_FIELDS - set(message)
    if extra:
        raise CodexBridgeError("禁止 continuation 欄位：{}".format(", ".join(sorted(extra))))
    if missing:
        raise CodexBridgeError("缺少 continuation 欄位：{}".format(", ".join(sorted(missing))))
    for field in ("claim_id", "public_reason"):
        _require_non_empty(message.get(field), field)
    for field in ("evidence_ids", "responds_to"):
        values = message.get(field)
        if not isinstance(values, list) or not values or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise CodexBridgeError("{} 必須為非空字串陣列。".format(field))
    if message.get("stance") not in _PUBLIC_STANCES:
        raise CodexBridgeError("未知 stance：{!r}".format(message.get("stance")))
    change_reason = message.get("stance_change_reason")
    if change_reason is not None and not isinstance(change_reason, str):
        raise CodexBridgeError("stance_change_reason 必須為字串或 null。")
    if previous_stance is not None and previous_stance != message["stance"]:
        _require_non_empty(change_reason, "stance_change_reason")
    return message


def validate_public_checkpoint(checkpoint):
    """Accept only a small public checkpoint that can resume the same thread."""
    if not isinstance(checkpoint, dict):
        raise CodexBridgeError("public checkpoint 必須為 JSON object。")
    extra = set(checkpoint) - _PUBLIC_CHECKPOINT_FIELDS
    missing = _PUBLIC_CHECKPOINT_FIELDS - set(checkpoint)
    if extra:
        raise CodexBridgeError("禁止 checkpoint 欄位：{}".format(", ".join(sorted(extra))))
    if missing:
        raise CodexBridgeError("缺少 checkpoint 欄位：{}".format(", ".join(sorted(missing))))
    for field in ("checkpoint_id", "thread_id", "public_status"):
        _require_non_empty(checkpoint.get(field), field)
    evidence_ids = checkpoint.get("evidence_ids")
    if not isinstance(evidence_ids, list) or any(
        not isinstance(value, str) or not value.strip() for value in evidence_ids
    ):
        raise CodexBridgeError("evidence_ids 必須為字串陣列。")
    stance = checkpoint.get("provisional_stance")
    if stance is not None and stance not in _PUBLIC_STANCES:
        raise CodexBridgeError("未知 provisional_stance：{!r}".format(stance))
    return checkpoint


def seal_public_checkpoint(run, seat_id, attempt_id, checkpoint):
    """Write one public resume checkpoint inside its seat attempt directory."""
    if seat_id not in CODEX_SEAT_IDS:
        raise CodexBridgeError("不是固定 GPT 席：{}".format(seat_id))
    _require_safe_segment(attempt_id, "attempt_id")
    validate_public_checkpoint(checkpoint)
    name = "agents/{}/attempts/{}/public-checkpoint.json".format(
        seat_id, attempt_id
    )
    target = assert_seat_write_allowed(
        run.path / name, run.path.parent.parent, run.run_id, seat_id
    )
    try:
        run.write_json(name, checkpoint, source="public Codex thread checkpoint")
    except (ArtifactAlreadyExistsError, RunStoreError) as exc:
        raise CodexBridgeError("public checkpoint 已 sealed 或路徑不安全。") from exc
    relative = target.relative_to(run.path).as_posix()
    return {"path": relative, "sha256": run.artifact_hashes[relative]}


def seat_output_dir(data_root, run_id, seat_id):
    """Return a fixed seat's attempt root under Data Root."""
    _require_safe_segment(run_id, "run_id")
    if seat_id not in CODEX_SEAT_IDS:
        raise CodexBridgeError("不是固定 GPT 席：{}".format(seat_id))
    return _validated_data_root(data_root) / "runs" / run_id / "agents" / seat_id / "attempts"


def assert_seat_write_allowed(target, data_root, run_id, seat_id):
    """Return a resolved path only when it is inside this seat's attempt root."""
    allowed = seat_output_dir(data_root, run_id, seat_id).resolve()
    resolved = Path(target).resolve()
    try:
        relative = resolved.relative_to(allowed)
    except ValueError as exc:
        raise CodexBridgeError("seat 只能寫入自己的 Data Root attempt 目錄。") from exc
    if not relative.parts:
        raise CodexBridgeError("seat 必須指定自己 attempt 目錄內的檔案。")
    return resolved


def seal_seat_handoff(run, seat_id, attempt_id, raw_text):
    """Store one seat's raw UTF-8 response exactly once and return its digest."""
    if seat_id not in CODEX_SEAT_IDS:
        raise CodexBridgeError("不是固定 GPT 席：{}".format(seat_id))
    _require_safe_segment(attempt_id, "attempt_id")
    if not isinstance(raw_text, str):
        raise CodexBridgeError("raw handoff 必須為文字。")
    name = "agents/{}/attempts/{}/raw-codex-handoff.txt".format(
        seat_id, attempt_id
    )
    target = assert_seat_write_allowed(run.path / name, run.path.parent.parent, run.run_id, seat_id)
    try:
        run.write_text(name, raw_text, source="verbatim Codex seat handoff")
    except (ArtifactAlreadyExistsError, RunStoreError) as exc:
        raise CodexBridgeError("raw handoff 已 sealed 或路徑不安全。") from exc
    raw_bytes = raw_text.encode("utf-8")
    return {
        "run_id": run.run_id,
        "seat_id": seat_id,
        "attempt_id": attempt_id,
        "path": target.relative_to(run.path).as_posix(),
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "bytes": len(raw_bytes),
    }


def _validate_core(core):
    if not isinstance(core, dict):
        raise PreflightNotReadyError("無法確認 Core metadata。")
    if core.get("role") != CORE_ROLE:
        raise PreflightNotReadyError("Core role 未確認。")
    if core.get("model") != TARGET_MODEL or core.get("model_confirmed") is not True:
        raise PreflightNotReadyError("Core GPT-5.6 Sol runtime model 未確認。")
    if core.get("created_threads_by") != "core":
        raise PreflightNotReadyError("三個 Codex threads 必須由 Core 建立。")


def _validate_threads(threads):
    if not isinstance(threads, dict) or set(threads) != set(CODEX_SEAT_IDS):
        raise PreflightNotReadyError("必須恰好提供三個固定 GPT seat/thread。")
    thread_ids = []
    for seat_id in CODEX_SEAT_IDS:
        metadata = threads[seat_id]
        if not isinstance(metadata, dict):
            raise PreflightNotReadyError("{} thread metadata 無效。".format(seat_id))
        if not isinstance(metadata.get("thread_id"), str) or not metadata["thread_id"].strip():
            raise PreflightNotReadyError("{} thread_id 未確認。".format(seat_id))
        thread_ids.append(metadata["thread_id"])
        if metadata.get("actual_model") != TARGET_MODEL:
            raise PreflightNotReadyError("{} actual model 不是 GPT-5.6 Sol。".format(seat_id))
        for field in ("model_confirmed", "capability_confirmed", "persistent"):
            if metadata.get(field) is not True:
                raise PreflightNotReadyError("{} 的 {} 未確認。".format(seat_id, field))
    if len(set(thread_ids)) != len(CODEX_SEAT_IDS):
        raise PreflightNotReadyError("三個 GPT 席必須使用不同 persistent threads。")


def _sha256_json(value):
    return _sha256_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _bridge_shared_prompt(prompt_shared_section):
    return (
        prompt_shared_section
        + "\n## Codex bridge public contract\n"
        + CONTRACT_TEXT
        + "\n## Codex source/time policy\n"
        + SOURCE_TIME_POLICY
    )


def _sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_non_empty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise CodexBridgeError("{} 必須為非空字串。".format(label))


def _require_safe_segment(value, label):
    _require_non_empty(value, label)
    if Path(value).name != value or value in (".", "..") or "/" in value or "\\" in value:
        raise CodexBridgeError("{} 不得包含路徑。".format(label))


def _require_utc(value, label):
    _require_non_empty(value, label)
    if not value.endswith("Z"):
        raise CodexBridgeError("{} 必須為 UTC ISO-8601 並以 Z 結尾。".format(label))
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CodexBridgeError("{} 必須為有效 UTC ISO-8601。".format(label)) from exc


def _validated_data_root(data_root):
    root = Path(data_root).resolve()
    code_root = CODE_ROOT.resolve()
    if root == code_root or root.is_relative_to(code_root):
        raise CodexBridgeError("Data Root 必須與 Code Root 分離。")
    return root
