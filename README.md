# TOMTIT Coordinator

TOMTIT Coordinator is a coding-first coordinator designed to supervise an existing coding agent through bounded execution, observed evidence, proportional verification, explicit revision limits, and auditable stop decisions.

The product goal is not to replace the coding model. It is to preserve or improve accepted result quality while reducing the user's coordination and review effort.

## Current status

The repository has completed three bounded build gates:

- **Gate 1 — Scenario 1:** atomic completion or rollback is approved and frozen.
- **Gate 2 — Scenario 2:** one bounded deterministic revise-then-complete flow is approved and frozen.
- **Gate 3 — Claude Code CLI capability:** correctness and persisted evidence have been independently reviewed and approved; Gate 3 is mechanically verified and closed. The capability is proven only within the bounded Gate 3 scope.

Development is stopped for Owner-led manual dogfooding. No later implementation gate is authorized.

```text
DEVELOPMENT_STATUS=STOPPED_FOR_OWNER_MANUAL_DOGFOOD
NO_NEXT_GATE_AUTHORIZED=true
```

No later implementation gate is currently authorized.

## Implemented and verified capabilities

Within the currently approved scope, the repository contains:

- machine-readable domain contracts and immutable execution records;
- Runtime-owned canonical state and bounded transition rules;
- Scenario 1 completion/report consistency with rollback on failure;
- Scenario 2 fresh-evidence revision from Attempt 1 to Attempt 2;
- deterministic evidence, scope, and check handling used by the approved scenarios;
- a bounded `ClaudeCodeCLIAdapter` capability;
- isolated-workspace invocation, bounded stdout/stderr and exit-code capture;
- process-group cleanup and final-snapshot ordering controls;
- explicit executable validation and normalized supported process-start failures;
- preserved real Claude Code CLI evidence for one successful bounded sample.

The Gate 3 capability does not yet prove production or multi-task reliability.

## Run tests

```bash
python -m pytest
```

Use the exact environment and additional commands required by the current Owner-authorized task. Do not invent substitute checks when a required command is unavailable; record the limitation instead.

## Governance

Primary policy and process documents:

- `docs/MVP_EXECUTION_POLICY_V0.md` — V0.3
- `docs/OFFICIAL_BUILD_AND_AI_CODING_PROCESS_V1.md` — V1
- `docs/EVALUATION_CONTRACT_V1.md` — V1
- `docs/VERIFIER_POLICY_V1.md` — V1
- `docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md` — V0.2

Bootstrap and operating summaries:

- `SYSTEM.md`
- `CLAUDE.md`

Gate decisions and exact artifact identities belong in `docs/gate-evidence/` and the corresponding implementation and review reports. README summaries do not replace those records.

## Verification model

TOMTIT uses proportional verification:

1. deterministic checks whenever claims are machine-checkable;
2. mechanical review for scope, Git, report, serialization, or evidence risks;
3. independent correctness review for state, authority, atomicity, recovery, trust boundaries, and new external integrations;
4. Owner-gated review for irreversible or high-impact risk.

Low-risk, bounded tasks may use `VERIFIER_MODE=NONE`, but still require deterministic checks, exact diff and scope inspection, clean-worktree verification, and completion evidence.

## Current limitations

The current evidence does not prove:

- production reliability across Claude Code CLI versions, prompts, accounts, or many tasks;
- persistent integration of the adapter into the complete Runtime/Coordinator loop;
- operating-system sandbox security;
- non-POSIX process supervision parity;
- product `WAITING_OWNER` and resume behavior;
- Attempt 3 product behavior;
- a user-facing product CLI;
- dynamic model routing;
- additional providers or worker adapters;
- Memory, graph execution, parallel agents, cloud operation, billing, or deployment automation.

These remain outside the current authorized scope.

## Manual dogfood hold

The next activity is Owner-led manual dogfood using the approved Gate 3 evidence and instructions. Results should be recorded as observed evidence and used to decide whether a later implementation gate is justified.

Do not start the next product gate automatically.
