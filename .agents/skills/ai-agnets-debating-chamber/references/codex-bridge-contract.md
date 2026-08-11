# Codex bridge contract

> **Legacy 稽核路徑**：本 contract 描述 legacy 稽核路徑
> （`prepare-launch`／handoff／`verify-preflight`），不在比賽冷啟動路徑上；
> 快速路徑見 `SKILL.md`。

Source of truth: `hoya_market_agents/codex_bridge.py`.

Core creates exactly three persistent Codex threads first, then calls
`build_codex_handoff(...)` with the observed metadata. Python validates and
stores metadata; it never creates or impersonates an agent.

After the user supplies only the market question, Core runs `prepare-launch`.
That command validates the question and generates a fresh URL-safe challenge,
an unused competition run ID and a distinct competition challenge. Core must
copy those values exactly into the handoff and authorization flow; it must not
ask the user to generate or relay them. Verification rejects the wrong nonce,
a handoff older than 300 seconds, a handoff too far in the future, or a handoff
already bound to another system preflight. The binding artifact is write-once.
The generated competition run ID is claimed by an atomic, hashed write-once
reservation in `preflight/launch-reservations/`; an existing reservation or run
directory makes Core generate another ID.

## Required runtime metadata

- Core: `role=core`, `model=gpt-5.6-sol`, `model_confirmed=true`,
  `created_threads_by=core`. New handoffs also record
  `model_confirmation_source=runtime|operator_ui`; use `operator_ui` only when
  the operator explicitly confirms that the UI shows the target model. Legacy
  four-field handoffs remain valid and imply `runtime`.
- Seats, in order: `spot-technical`, `derivatives`, `news`.
- Each seat: unique non-empty `thread_id`, `actual_model=gpt-5.6-sol`, and
  `model_confirmed`, `capability_confirmed`, `persistent` all `true`.
- Each seat dispatch: unique `dispatch_id`, exact web-search-only `tool_policy`,
  `tool_policy_confirmed=true`, and a runtime-provided policy receipt with
  unique `receipt_id`. The receipt's `dispatch_id` and `tool_policy_sha256`
  must bind it to that seat's dispatch and exact policy. Python records and
  hashes the receipt but never creates it.
- Missing, extra, unconfirmed, non-persistent or wrong-model metadata is
  `NOT READY`; there is no silent fallback.

## Handoff artifact

Write the result once to `preflight/codex-handoff.json` **inside that run's own
directory**.

Do not spell the directory out by hand. Since ADR 0005 a run lives under its
Taipei date, in a folder named for the question, and ends in a digest of the
whole run id — `<Data Root>/runs/<YYYY-MM-DD>/<HHMM-題目slug-hash>/` — so a
path assembled from the run id alone points at nothing. Ask for it:

```bash
# WSL, from Code Root
python3 -c 'import sys; from hoya_market_agents.run_store import resolve_run_dir; \
print(resolve_run_dir(sys.argv[1], sys.argv[2]) or "")' "$DATA_ROOT" "$CODEX_RUN_ID"
```

An empty answer means there is no such run under that Data Root; that is
`NOT READY`, not an invitation to create the path.

The result includes the validated Question Package; pinned research snapshot;
shared prompt, preflight challenge, contract, and source/time-policy bytes plus SHA-256 values; and
the three seat/thread/model/dispatch-policy/output-path records. The verifier rebuilds the
whole object and requires exact equality, so changed hashes, seats, models,
question data, policies or prompt bytes fail closed.

`verify-preflight` is read-only and requires the expected challenge. The real
system preflight additionally binds the verified handoff once; repeating the
same handoff under another preflight ID is rejected as replay.

## Public continuation

Only these fields are accepted: `claim_id`, `evidence_ids`, `stance`,
`public_reason`, `responds_to`, `stance_change_reason`. Any unlisted field is
rejected, including hidden chain-of-thought, scratchpads or private reasoning.

`seal_public_checkpoint(...)` stores the same thread's public resume state
inside its seat attempt directory. Its exact fields are `checkpoint_id`,
`thread_id`, `evidence_ids`, `public_status`, and `provisional_stance`; extra
or hidden-reasoning fields are rejected. Before writing, it reloads the sealed
preflight and requires exact seat-to-thread and seat-to-attempt equality;
wrong-seat, wrong-thread, wrong challenge or missing-preflight input writes
nothing.

## Raw seat handoff and path isolation

`seal_seat_handoff(...)` first parses a unique-key JSON research envelope and
requires an exact allowlist for both the envelope and every EvidenceCard.
Private, hidden-reasoning, secret, API-key or unknown fields are rejected
before any directory or file is created. It then writes the same validated
UTF-8 bytes to
`agents/<seat_id>/attempts/<attempt_id>/raw-codex-handoff.txt`, records the
exact SHA-256 and refuses replacement. Seats receive no filesystem tools;
Core alone calls the Python validator/sealer and owns the Data Root write.
