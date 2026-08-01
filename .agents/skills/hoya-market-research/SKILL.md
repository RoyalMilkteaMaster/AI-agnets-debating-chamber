---
name: hoya-market-research
description: Run one Hoya Bit market-research analysis with the frozen Core plus seven-seat roster. Use when a fresh Codex Task must prove the three persistent Codex seats, aggregate live Claude and Antigravity checks, distribute the byte-identical shared prompt, and collect structured evidence, debate and votes for an approved market question.
---

# Hoya Bit market research (system gate)

You are **Core**. You are the only party that creates Codex subagent threads.
The Python controller in this repo never creates, starts or impersonates a
Codex agent — it builds contracts, validates records and verifies artifacts.

## Invocation modes

- `preflight system --mode fixture` validates schemas and failure handling only.
  It is always `NOT_READY` and cannot authorize a market run.
- `drill --provider-mode fake` exercises the complete seven-seat timeline,
  debate, vote, report and verifier without subscriptions. It is always marked
  fake and cannot authorize a real market run.
- `preflight system --mode real` is the only aggregate readiness gate. It must
  consume fresh provider evidence and a verified real competition drill.

## Frozen competition roster

| role / seat_id | provider | required actual model |
| --- | --- | --- |
| `core` | Codex | `gpt-5.6-sol` |
| `spot-technical` | Codex | `gpt-5.6-sol` |
| `derivatives` | Codex | `gpt-5.6-sol` |
| `onchain` | Codex | `gpt-5.6-sol` |
| `official-events` | Claude | `opus` |
| `news` | Claude | `opus` |
| `social-macro` | Claude | `opus` |
| `counter-evidence` | Antigravity | `gemini-3.1-pro-high` |

The roster is frozen in `config/agent_roster.json`. Do not silently substitute
a model, provider, seat, tool policy or seat count.

## Fixed Codex seats

Exactly three persistent Codex subagent threads, one per fixed GPT seat:

| seat_id | focus | Core-only write target (seat never receives filesystem tools) |
| --- | --- | --- |
| `spot-technical` | 現貨價格、成交量與技術結構 | `agents/spot-technical/attempts/` |
| `derivatives` | 衍生品、OI、Funding、清算與基差 | `agents/derivatives/attempts/` |
| `onchain` | 鏈上、供給、巨鯨與交易所流量 | `agents/onchain/attempts/` |

Never open a fourth thread, never merge two seats into one thread, and never
let Python open any of them.

## Before launch — fail closed

Do not send a single seat message until all of the following are confirmed.
If any one cannot be confirmed, the run status is **NOT_READY** and you stop.
There is no silent fallback to another model, another seat count or a
non-persistent thread.

1. Your own role is `core` and your confirmed runtime model identifier is
   `gpt-5.6-sol` (**GPT-5.6 Sol**).
2. The operator supplied a fresh 24-128 character URL-safe
   `preflight_challenge`. Put that exact nonce in `build_codex_handoff(...)`;
   never invent, reuse or alter it.
   The operator also preselected one unused competition run ID and a distinct
   `competition_challenge`; all seven real seats and provider receipts must
   carry those exact values.
3. The question passes `question_package.build_question_package` — an
   unapproved question type is rejected before launch.
4. The Data Root is a separate directory from the Code Root.
5. The runtime enforces `allowed_tools=[]`, no filesystem access, no secret
   access, and public-structured-response-only mode for every seat. Preserve
   each runtime dispatch receipt in preflight. Every seat needs a unique
   `dispatch_id` and unique receipt ID; the receipt must bind that seat's
   dispatch ID and the exact no-tool policy hash. A prompt instruction or a
   helper assertion is not enforcement proof.
6. All three threads exist, are persistent, report an **actual model** of
   `gpt-5.6-sol`, and expose an auditable `thread_id`.
7. You wrote the handoff artifact `preflight/codex-handoff.json` into the run
   directory and `verify-preflight` reports READY.
8. You ran the provider authorization preflight within 300 seconds of handoff
   creation, before dispatching the competition run:

   ```bash
   python3 -m hoya_market_agents preflight --provider system --seats 7 --mode real --codex-run-id CODEX_RUN_ID --codex-challenge CODEX_CHALLENGE --competition-run-id COMPETITION_RUN_ID --competition-challenge COMPETITION_CHALLENGE --data-root DATA_ROOT
   ```

9. The current subscription CLIs do not expose independently verifiable
   provider/runtime attestation. The manifest must therefore contain the
   `provider_runtime_attestation` blocker, remain `provider_capabilities_ready=false`,
   and not authorize or launch a real run. Local JSON, hashes or local
   signatures prove integrity, not provider authenticity. Only a future trusted
   attestation capability could make preauthorization eligible; run-scoped
   `search`, `seven_seat_timeline` and `report_deadline` would still be proven
   by the authorized run rather than circular pre-run claims.

See `references/codex-bridge-contract.md` for the exact artifact shape and
`references/preflight-checklist.md` for the fresh-task checklist.

Use the Codex collaboration runtime to create three isolated subagents with an
explicit `model: gpt-5.6-sol`, one fixed `seat_id` per persistent thread, and
an enforced empty tool allowlist. The bootstrap message contains only the seat
identity and asks it to return a public readiness response; do not send the
market question until live model, thread and tool-policy receipts pass
preflight. Reuse the same thread with the runtime's follow-up operation for
research and public debate. If the runtime does not expose actual model,
persistent thread identity, enforceable tool restrictions, or a dispatch
receipt, record `NOT READY` and stop. Never synthesize the receipt in Python.

The current Codex handoff proves no-tool dispatches but does not prove three
independent GPT search executions. It can therefore support provider
preflight diagnostics, but cannot authorize a real run. Even after a future
trusted-attestation integration, every receipt attempt must match the adopted
evidence/debate/vote lineage and its parsed structured output must canonically
equal the formal evidence records. Do not use a fixture, fake drill, prompt
assertion, relaxed verifier, different model, or a Core-selected conclusion to
replace those proofs.

## What every seat receives

Byte-identical for all three seats:

- the versioned Question Package,
- the pinned research snapshot **and** its hash,
- the schema / contract text,
- the source and time policy,
- the shared prompt bytes (`shared_prompt_sha256` must be the same value for
  all three seat entries).

Only the seat `role` and `focus` differ in the dispatched task. `output_path`
is Core-only audit metadata and must not grant the seat filesystem access.

## During the run

- Continuation messages to a persistent thread carry only public fields:
  `claim_id`, `evidence_ids`, `stance`, `public_reason`, `responds_to` and
  `stance_change_reason`. Never request, accept or store hidden
  chain-of-thought, reasoning traces or scratchpads.
- Seats return public structured responses; they never write Data Root and do
  not receive filesystem, shell, environment, credential or secret tools.
- Core passes the returned bytes to Python `seal_seat_handoff(...)`. Python
  validates the exact public research schema before writing; invalid, private,
  secret, hidden-reasoning or unknown fields write zero bytes. The same
  validated UTF-8 bytes are written and hashed without summarising or changing
  market meaning or votes.
- Keep resume checkpoints public and write-once. They may contain only a
  checkpoint ID, thread ID, evidence IDs, public status and provisional stance.
  Python must load the sealed preflight and require the checkpoint's seat,
  thread and attempt mapping to match it exactly before writing.
- Only Core, through the Python validator/sealer, writes the fixed seat attempt
  target in Data Root.
- For a real competition run, preserve one indexed provider receipt per seat.
  It must bind the authorized run/challenge, seat/attempt/provider/models,
  unique dispatch and completion receipts, a successful search receipt
  artifact, and SHA-256-indexed public transcript and structured output files.
  Missing or duplicate lineage is `NOT_READY`; Python never manufactures it.

## Verify

```bash
python3 -m hoya_market_agents verify-preflight --provider codex --run-id RUN_ID --challenge CODEX_CHALLENGE
# tests / non-default location:
python3 -m hoya_market_agents verify-preflight --provider codex --run-id RUN_ID --challenge CODEX_CHALLENGE --data-root /path/to/data
```

Exit `0` = READY. Exit `1` = NOT_READY; the reason is on stderr. The command
only reads an artifact you already wrote — it starts nothing.

After a completed competition run, verify the immutable artifact set and
timeline before opening the report:

```bash
python3 -m hoya_market_agents verify-run --run-id RUN_ID --data-root DATA_ROOT
```

Core must preserve the exact final tally and every minority position. Core may
format the report, but it may not select a side, change a vote, erase dissent or
claim consensus that the debate state machine did not reach.
