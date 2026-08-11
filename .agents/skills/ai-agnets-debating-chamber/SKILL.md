---
name: ai-agnets-debating-chamber
description: Fast-path launch for one AI agnets debating chamber market-research run. Use when the user pastes an approved market question and says to start analysis. Core immediately runs the single launch command, which dispatches all seven seats itself, chairs the debate and the vote after the T+4:00 snapshot, writes the report and finalizes the run. Core reads the three handshakes and watches the live dashboard. No plan confirmation, no pre-game steps.
---

# AI agnets debating chamber market research (fast path)

You are **Core**. When the user pastes the question and says to start, do not
confirm a plan — immediately run this single command (WSL; single-quote the
question to avoid shell expansion):

```bash
nohup python3 -m hoya_market_agents launch --question '<題目>' \
  --data-root <DATA_ROOT> --handshake-file /tmp/hoya-launch.json \
  >/tmp/hoya-launch.log 2>&1 &
```

`launch` does everything itself: validates the question, checks the
`preflight/latest-ready.json` credential, creates the run, dispatches **all
seven seats** in parallel — 3 Claude, 1 Antigravity and the 3 Codex seats
through headless `codex exec` — starts the live server in the background (its
failure never blocks), and immediately emits the LAUNCHED handshake (stdout
first line, same content as the handshake file). It then keeps going by itself:
the T+4:00 evidence seal (`SEALED`), the seven-seat debate and vote, your
report and the manifest (`FINALIZED`). Never run `prepare-launch`,
`verify-preflight`, provider preflight or `drill` here — those are pre-game
steps and are never on the cold-start path.

## Sole precondition

The only gate before dispatching seats: `<DATA_ROOT>/preflight/latest-ready.json`
exists with `provider_capabilities_ready: true`, produced by the one-time
pre-game `preflight --provider system --seats 7 --mode real`. If the credential
is missing, `launch` exits 2 and explains; Core stops and reports — never
bypass it. Pre-game checklist: `references/preflight-checklist.md`.

## After the handshake

Read the handshake JSON fields `run_id`, `run_dir`, `inbox_dir`, `live_url`,
`codex_mode`, `codex_seats[{seat_id, attempt_id, prompt_path}]`. With the
default `codex_mode: "cli"` there is **nothing left to dispatch**: watch
`live_url` and wait. Do not open Codex threads and do not call `submit-seat` —
a manual reply would duplicate a seat that `launch` already dispatched.

`launch` prints exactly three JSON lines and then exits 0:

| line | when | means |
| --- | --- | --- |
| `LAUNCHED` | T+0:00 | run created, seven seats away, live server up |
| `SEALED` | T+4:00 | evidence snapshot sealed; debate starts |
| `FINALIZED` | ≤ T+13:00 | votes, report and manifest written |

`FINALIZED` carries `consensus_status`, `adopted_stance`, `tally`,
`stop_reason`, `report_status` and `report_html`. Report that line to the user
and open `report_html`. `report_status: "red_audit"` is an honest outcome, not a
crash: the report failed objective validation twice, so the run published the
red 「報告驗證失敗」 version with its exact reasons — never rewrite it.

**Chairing is scheduling, not authoring.** `launch` decides who challenges whom
(derived from the seats' own published positions) and in what order messages are
relayed. Every stance, public reason, evidence ID and change reason is the
seat's verbatim output. Core never votes, never rewrites a seat and never
removes a minority opinion.

`--phase research` stops the command at `SEALED`. Use it only when the debate
will be chaired by hand; the fast path never needs it.

## Fallback: `--codex-mode inbox`

Only when the `codex exec` channel is unusable (CLI missing, logged out, or the
handshake reports `codex_mode: "inbox"`), add `--codex-mode inbox` to the launch
command. Then `launch` writes the three prompts and requests but dispatches no
Codex seat, and Core drives them by hand: create 3 persistent Codex subagent
threads (model `gpt-5.6-sol`, only `web_search` allowed). The first message of
each thread is the complete content of that seat's `prompt_path` file — it
already contains the question. No readiness-only bootstrap round. When a seat
replies, pipe the raw reply verbatim through stdin:

```bash
python3 -m hoya_market_agents submit-seat --run-id <run_id> \
  --seat-id <seat> --attempt-id <attempt> --data-root <DATA_ROOT>
```

## Frozen roster and Core rules

| seats | provider | model |
| --- | --- | --- |
| core, spot-technical, derivatives, news | Codex | `gpt-5.6-sol` |
| onchain, official-events, social-macro | Claude | `opus` |
| counter-evidence | Antigravity | `gemini-3.1-pro-high` |

- Roster 凍結於 `config/agent_roster.json`；不得替換模型、席位或席數。
- 所有公開分析、辯論與報告使用繁體中文；evidence ID、URL、資產代號與 contract enum 維持原文。
- Core 不得改票、不得改寫或刪除少數意見、不得代席位投票。
- T+4:00 證據快照產生後，`launch` 依 architecture §5.3 的固定時間關卡主持辯論
  （2026-08-02 使用者核准修訂）：開場逐席即時發布，某席開場已發且全場已有
  兩種以上立場就立刻派發該席第一輪（快席不等慢席）、T+7:30 前完成第一輪反方
  挑戰與首次投票（達 6 票即停）、T+8:00 門檻降為 5 票、T+8:45 與 T+9:45 前各
  一輪改票機會、T+10:00 強制停止（4 票採用，否則未達共識）。
- 未完成第一輪反方挑戰的席位不產生有效票；全室立場一致時沒有反方可挑戰，
  該次辯論會誠實停在「有效票不足」，不得為了好看而補票。
