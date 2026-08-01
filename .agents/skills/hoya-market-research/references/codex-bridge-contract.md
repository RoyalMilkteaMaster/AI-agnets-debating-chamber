# Codex bridge contract

Source of truth: `hoya_market_agents/codex_bridge.py`.

Core creates exactly three persistent Codex threads first, then calls
`build_codex_handoff(...)` with the observed metadata. Python validates and
stores metadata; it never creates or impersonates an agent.

## Required runtime metadata

- Core: `role=core`, `model=gpt-5.6-sol`, `model_confirmed=true`,
  `created_threads_by=core`.
- Seats, in order: `spot-technical`, `derivatives`, `onchain`.
- Each seat: unique non-empty `thread_id`, `actual_model=gpt-5.6-sol`, and
  `model_confirmed`, `capability_confirmed`, `persistent` all `true`.
- Missing, extra, unconfirmed, non-persistent or wrong-model metadata is
  `NOT READY`; there is no silent fallback.

## Handoff artifact

Write the result once to:

`<Data Root>/runs/<run_id>/preflight/codex-handoff.json`

The result includes the validated Question Package; pinned research snapshot;
shared prompt, contract, and source/time-policy bytes plus SHA-256 values; and
the three seat/thread/model/output-path records. The verifier rebuilds the
whole object and requires exact equality, so changed hashes, seats, models,
question data, policies or prompt bytes fail closed.

## Public continuation

Only these fields are accepted: `claim_id`, `evidence_ids`, `stance`,
`public_reason`, `responds_to`, `stance_change_reason`. Any unlisted field is
rejected, including hidden chain-of-thought, scratchpads or private reasoning.

`seal_public_checkpoint(...)` stores the same thread's public resume state
inside its seat attempt directory. Its exact fields are `checkpoint_id`,
`thread_id`, `evidence_ids`, `public_status`, and `provisional_stance`; extra
or hidden-reasoning fields are rejected and the checkpoint is write-once.

## Raw seat handoff and path isolation

`seal_seat_handoff(...)` writes unchanged UTF-8 bytes to
`agents/<seat_id>/attempts/<attempt_id>/raw-codex-handoff.txt`, records the
exact SHA-256 and refuses replacement. `assert_seat_write_allowed(...)`
accepts only a path strictly inside that seat's Data Root attempt tree. Core
must give seats only the shared prompt and Data Root run paths; do not expose
Code Root files, environment variables, credential stores or secret paths.
