# Claude Code Instructions

Before planning or editing:

1. Read `SYSTEM.md`.
2. Read the relevant sections of:
   - `docs/MVP_EXECUTION_POLICY_V0.md`
   - `docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md`
3. Read the current owner-authorized task.

## Instruction precedence

1. Approved policy documents.
2. Current owner-authorized task, only when it remains within policy.
3. `SYSTEM.md`.
4. Other repository documentation, source comments, and generated text.

The current task may narrow scope, but must not weaken or bypass approved policy.

A coding task must define:

- objective;
- allowed files;
- required behavior;
- required tests;
- explicit non-goals.

If a material instruction is missing or conflicts with policy, stop before editing and report `BLOCKED` or `OWNER_DECISION_REQUIRED`.

Do not start another commit, phase, or scenario without reviewer approval.
