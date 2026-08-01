---
name: hoya-market-research
description: Run one Hoya Bit market-research analysis with the fixed Codex GPT seats (spot-technical, derivatives, onchain). Use when a fresh Codex Task must open the three persistent seat threads, distribute the byte-identical shared prompt, and collect structured evidence, debate and votes for an approved market question.
---

# Hoya Bit market research (Codex bridge)

You are **Core**. You are the only party that creates Codex subagent threads.
The Python controller in this repo never creates, starts or impersonates a
Codex agent — it builds contracts, validates records and verifies artifacts.

## Fixed seats

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
2. The question passes `question_package.build_question_package` — an
   unapproved question type is rejected before launch.
3. The Data Root is a separate directory from the Code Root.
4. The runtime enforces `allowed_tools=[]`, no filesystem access, no secret
   access, and public-structured-response-only mode for every seat. Preserve
   each runtime dispatch receipt in preflight. A prompt instruction or a helper
   assertion is not enforcement proof.
5. All three threads exist, are persistent, report an **actual model** of
   `gpt-5.6-sol`, and expose an auditable `thread_id`.
6. You wrote the handoff artifact `preflight/codex-handoff.json` into the run
   directory and `verify-preflight` reports READY.

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

## Verify

```bash
python3 -m hoya_market_agents verify-preflight --provider codex --run-id RUN_ID
# tests / non-default location:
python3 -m hoya_market_agents verify-preflight --provider codex --run-id RUN_ID --data-root /path/to/data
```

Exit `0` = READY. Exit `1` = NOT_READY; the reason is on stderr. The command
only reads an artifact you already wrote — it starts nothing.
