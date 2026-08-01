# Fresh-task preflight checklist (live Codex smoke)

Run this in a **fresh Codex Task**, from the Code Root. It is a live check; no
unit test can substitute for it, and no unit test in this repo has performed it.

1. **Discovery** — confirm the fresh task lists the skill `hoya-market-research`
   from `.agents/skills/hoya-market-research/SKILL.md`. If it is not listed,
   stop: the bridge is NOT_READY.
2. **Fresh challenge** — require the operator-provided 24-128 character
   URL-safe nonce. Pass it unchanged as `preflight_challenge` when building the
   handoff. A missing, altered, older-than-300-second or already-bound handoff
   is `NOT_READY`.
   Also require one preselected, unused competition run ID and a distinct
   competition challenge before provider smoke begins.
3. **Core identity** — confirm your role is `core` and your runtime model is
   confirmed as `gpt-5.6-sol`. Record what you actually observed, not what was
   requested. Unconfirmed → NOT_READY, no fallback.
4. **Question** — build the Question Package for the approved question; an
   unsupported question type stops the run before any thread is opened.
5. **Data Root** — confirm the Data Root is a directory separate from the Code
   Root, and that the run directory exists.
6. **Dispatch policy** — use runtime-enforced `allowed_tools=[]`, no filesystem
   or secret access, and public-structured-response-only mode. Record the
   runtime dispatch ID and receipt. Require unique dispatch and receipt IDs per
   seat, and require every receipt to bind its dispatch ID and exact policy
   hash. If the runtime cannot enforce or expose this, stop: prompt text and
   Python helper claims do not prove isolation.
7. **Threads** — create exactly three persistent Codex subagent threads, one
   per fixed seat. For each, record `seat_id`, `thread_id` and the **actual**
   model reported by the thread. Any thread that cannot confirm `gpt-5.6-sol` or
   persistence → NOT_READY; do not proceed with two seats.
8. **Handoff** — write `preflight/codex-handoff.json` (shape in
   `codex-bridge-contract.md`) once into the run directory.
9. **Verify** —

   ```bash
   python3 -m hoya_market_agents verify-preflight --provider codex --run-id RUN_ID --challenge CODEX_CHALLENGE --data-root DATA_ROOT
   ```

   Exit `0` and `狀態：READY` on stdout means the artifact is consistent.
   Exit `1` prints the NOT_READY reason on stderr.
10. **Provider bridge checkpoint** — do not send the market question yet. A
   Codex bridge READY result is only one input to system readiness.
11. **Provider authorization** — before dispatching the competition run, run:

    ```bash
    python3 -m hoya_market_agents preflight --provider system --seats 7 --mode real --codex-run-id CODEX_RUN_ID --codex-challenge CODEX_CHALLENGE --competition-run-id COMPETITION_RUN_ID --competition-challenge COMPETITION_CHALLENGE --data-root DATA_ROOT
    ```

    Current subscription CLIs lack independently verifiable provider/runtime
    attestation, so require the exact `provider_runtime_attestation` blocker,
    `provider_capabilities_ready=false`, and no competition authorization.
    Local JSON, hashes and local signatures prove integrity, not provider
    authenticity. Stop before real dispatch.
12. **Search receipt gate** — require live receipts for three independent GPT
    search executions in the authorized run. The current no-tool Codex handoff
    does not prove this. Do not substitute a prompt claim, fixture, fake
    receipt, different model or fewer seats.
13. **Live drill and launch** — require a verifier-passing drill marked
    `provider_mode=real-subscription` and `competition_ready=true`. A fake drill
    tests orchestration only. Dispatch only the exact preauthorized run ID and
    challenge. Final competition readiness comes from `verify-run`, not a
    second preflight that would create circular lineage.
    Every real seat must also return its run-scoped dispatch/completion receipt,
    provider search receipt, public transcript hash and structured output hash;
    all receipt paths/hashes must be present in the run artifact index. Receipt
    attempts must match adopted evidence/debate/vote lineage and parsed
    structured outputs must canonically equal the formal evidence records.

Known limitation: steps 1, 3, 6 and 7 depend on live Codex runtime capability and
are the parts unit tests cannot prove. Record the observed `thread_id` and
actual model values in the handoff so the result is auditable afterwards.
