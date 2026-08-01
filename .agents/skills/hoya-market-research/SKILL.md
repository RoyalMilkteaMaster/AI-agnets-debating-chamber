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

| seat_id | focus | own output path (Data Root, relative to the run dir) |
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
4. All three threads exist, are persistent, report an **actual model** of
   `gpt-5.6-sol`, and expose an auditable `thread_id`.
5. You wrote the handoff artifact `preflight/codex-handoff.json` into the run
   directory and `verify-preflight` reports READY.

See `references/codex-bridge-contract.md` for the exact artifact shape and
`references/preflight-checklist.md` for the fresh-task checklist.

Use the Codex collaboration runtime to create three isolated subagents with an
explicit `model: gpt-5.6-sol`, one fixed `seat_id` per persistent thread. The
bootstrap message contains only the seat identity and asks it to wait; do not
send the market question until all live metadata passes preflight. Reuse the
same thread with the runtime's follow-up operation for research and public
debate. If the runtime does not expose actual model or persistent thread
identity, record `NOT READY` and stop.

## What every seat receives

Byte-identical for all three seats:

- the versioned Question Package,
- the pinned research snapshot **and** its hash,
- the schema / contract text,
- the source and time policy,
- the shared prompt bytes (`shared_prompt_sha256` must be the same value for
  all three seat entries).

Only `role`, `focus` and the seat's own output path differ.

## During the run

- Continuation messages to a persistent thread carry only public fields:
  `claim_id`, `evidence_ids`, `stance`, `public_reason`, `responds_to` and
  `stance_change_reason`. Never request, accept or store hidden
  chain-of-thought, reasoning traces or scratchpads.
- Preserve each seat's raw structured handoff bytes and their SHA-256 exactly.
  You may not summarise, reorder or otherwise change market meaning or votes.
- Keep resume checkpoints public and write-once. They may contain only a
  checkpoint ID, thread ID, evidence IDs, public status and provisional stance.
- A seat may not write the Code Root, may not read secrets, and may only write
  its own Data Root attempt directory.

## Verify

```bash
python3 -m hoya_market_agents verify-preflight --provider codex --run-id RUN_ID
# tests / non-default location:
python3 -m hoya_market_agents verify-preflight --provider codex --run-id RUN_ID --data-root /path/to/data
```

Exit `0` = READY. Exit `1` = NOT_READY; the reason is on stderr. The command
only reads an artifact you already wrote — it starts nothing.
