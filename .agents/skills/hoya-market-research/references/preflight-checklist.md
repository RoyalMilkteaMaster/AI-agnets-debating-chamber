# Fresh-task preflight checklist (live Codex smoke)

Run this in a **fresh Codex Task**, from the Code Root. It is a live check; no
unit test can substitute for it, and no unit test in this repo has performed it.

1. **Discovery** — confirm the fresh task lists the skill `hoya-market-research`
   from `.agents/skills/hoya-market-research/SKILL.md`. If it is not listed,
   stop: the bridge is NOT_READY.
2. **Core identity** — confirm your role is `core` and your runtime model is
   confirmed as `gpt-5.6-sol`. Record what you actually observed, not what was
   requested. Unconfirmed → NOT_READY, no fallback.
3. **Question** — build the Question Package for the approved question; an
   unsupported question type stops the run before any thread is opened.
4. **Data Root** — confirm the Data Root is a directory separate from the Code
   Root, and that the run directory exists.
5. **Dispatch policy** — use runtime-enforced `allowed_tools=[]`, no filesystem
   or secret access, and public-structured-response-only mode. Record the
   runtime dispatch ID and receipt. If the runtime cannot enforce or expose
   this, stop: prompt text and Python helper claims do not prove isolation.
6. **Threads** — create exactly three persistent Codex subagent threads, one
   per fixed seat. For each, record `seat_id`, `thread_id` and the **actual**
   model reported by the thread. Any thread that cannot confirm `gpt-5.6-sol` or
   persistence → NOT_READY; do not proceed with two seats.
7. **Handoff** — write `preflight/codex-handoff.json` (shape in
   `codex-bridge-contract.md`) once into the run directory.
8. **Verify** —

   ```bash
   python3 -m hoya_market_agents verify-preflight --provider codex --run-id RUN_ID --data-root DATA_ROOT
   ```

   Exit `0` and `狀態：READY` on stdout means the artifact is consistent.
   Exit `1` prints the NOT_READY reason on stderr.
9. **Launch** — only after step 8 passes, send the byte-identical shared prompt
   to all three threads, differing only in role, focus and own output path.

Known limitation: steps 1, 2, 5 and 6 depend on live Codex runtime capability and
are the parts unit tests cannot prove. Record the observed `thread_id` and
actual model values in the handoff so the result is auditable afterwards.
