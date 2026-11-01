# TOMTIT-Code documentation

Start with the repository [`README`](../README.md). This page lists every maintained document by purpose.

## Operating rules and agent instructions

These live at the repository root, where agents and tools expect them.

| document | purpose |
|---|---|
| [`SYSTEM.md`](../SYSTEM.md) | concise operating summary of the governance documents; current project status |
| [`CLAUDE.md`](../CLAUDE.md) | Claude Code instructions: required reading, precedence, task definition, verification and gate discipline |

## Governance and process

These are the authority documents. `CLAUDE.md` names them by these exact paths, and gate evidence pins their
content by SHA256, so they stay in place and are not edited as part of documentation reorganization.

| document | version |
|---|---|
| [MVP Execution Policy](MVP_EXECUTION_POLICY_V0.md) | V0.3 |
| [Official Build and AI Coding Process](OFFICIAL_BUILD_AND_AI_CODING_PROCESS_V1.md) | V1 |
| [Evaluation Contract](EVALUATION_CONTRACT_V1.md) | V1 |
| [Verifier Routing and Review Policy](VERIFIER_POLICY_V1.md) | V1 |
| [Dogfood Experiment Protocol](DOGFOOD_EXPERIMENT_PROTOCOL_V0.md) | V0.2 |

## Product

- [Product Naming V1](PRODUCT_NAMING_V1.md): the TOMTIT-Code product name and naming rules.

## Architecture

- [Decision Evaluation V0 design](architecture/DECISION_EVALUATION_V0_DESIGN.md): the offline evaluator for `RETRY` and
  `STOP` decisions, implemented in `src/tomtit_decision_eval_v0/`.

## Development and dogfooding

- [Dogfood Evaluation Telemetry V0](DOGFOOD_EVALUATION_TELEMETRY_V0.md): the telemetry and summary contract used by
  `.tomtit/dogfood/evaluate_dogfood_run.py`.
- Running tests: see [Run tests](../README.md#run-tests) in the repository README.

## Decisions and gate evidence

Immutable gate records: approved decisions, with exact commit and artifact identities.

- [Gate 1: Scenario 1](gate-evidence/GATE_1_SCENARIO_1.md)
- [Gate 2: Minimal Scenario 2](gate-evidence/GATE_2_SCENARIO_2.md)
- [Gate 3: Claude Code CLI capability](gate-evidence/GATE_3_CLAUDE_CODE_CLI_CAPABILITY.md)
- [Freeze provenance closure](gate-evidence/FREEZE_PROVENANCE_CLOSURE.md)

The raw artifacts these records cite live in [`../evidence/`](../evidence/).

## Development records archive

[`archive/`](archive/README.md) holds the 89 point-in-time implementation reports, receipts and independent reviews
that used to sit at the repository root. They are grouped by work stream, and each one keeps its original filename.
They are provenance, not current instructions.
