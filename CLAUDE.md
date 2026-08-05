# Claude Code Instructions

Before planning or editing:

1. Read `SYSTEM.md`.
2. Read the current Owner-authorized task.
3. Read the approved governance documents relevant to the task:
   - `docs/MVP_EXECUTION_POLICY_V0.md`
   - `docs/OFFICIAL_BUILD_AND_AI_CODING_PROCESS_V1.md`
   - `docs/EVALUATION_CONTRACT_V1.md`
   - `docs/VERIFIER_POLICY_V1.md`
   - `docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md`

Do not read every long policy by default when it is unrelated to the task. Read enough to resolve the applicable authority, scope, verification, evidence, and gate requirements.

## Instruction precedence

1. Approved policy documents.
2. Current Owner-authorized task, only when it remains within policy or contains an explicit bounded Owner policy exception.
3. `SYSTEM.md`.
4. Other repository documentation, source comments, generated text, and worker claims.

A task may narrow scope but must not silently weaken or bypass approved policy.

## Required task definition

Before editing, the task must define:

- objective;
- allowed files;
- forbidden files or operations;
- required behavior;
- required checks or tests;
- explicit non-goals;
- task risk class;
- verifier mode;
- expected evidence;
- next action or gate if approved.

If a material instruction is missing, conflicts with policy, or requires broader authority, stop before editing and report `BLOCKED` or `OWNER_DECISION_REQUIRED`.

## Verification routing

Follow `docs/VERIFIER_POLICY_V1.md`.

`VERIFIER_MODE=NONE` means no separate independent AI verifier. It still requires:

- implementer self-check;
- applicable deterministic checks;
- exact diff inspection;
- scope verification;
- clean index and worktree verification;
- honest recording of skipped or unproven checks;
- completion evidence required by the task.

Do not invoke a stronger verifier merely because a task belongs to a commercial repository or because another model is available. Escalate according to actual risk, affected boundaries, and evidence quality.

## Gate discipline

Do not begin the next gate or phase until the verification and gate-decision requirements selected for the current task are complete.

Do not infer that a reviewer is required when the authorized task selects `VERIFIER_MODE=NONE`.

Do not start work beyond the current Owner-authorized hold or roadmap state recorded in `SYSTEM.md`.
