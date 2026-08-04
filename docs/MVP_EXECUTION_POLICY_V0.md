# MVP Execution Policy V0

**Status:** Accepted for Fake-Loop Implementation
**Version:** 0.3
**Product:** TOMTIT
**Scope:** Coding-first Coordinator MVP
**Primary interface:** CLI
**Default attempt limit:** 2
**Hard attempt limit:** 3

---

## 1. Purpose

This policy defines the smallest execution loop required to test the first TOMTIT product hypothesis:

> TOMTIT can coordinate an existing coding agent, collect objective evidence, run at most two attempts by default and three attempts under strict conditions, stop correctly, and reduce the user's coordination effort without lowering result quality.

This document defines execution invariants and authority boundaries. Machine-readable schemas and fake-scenario fixtures are separate implementation artifacts.

---

## 2. MVP Scope

The MVP supports one coding task in one Git repository at a time.

```text
User request
→ infer minimal TaskContract
→ prepare controlled workspace
→ implement
→ collect fresh evidence
→ deterministic checks
→ optional LLM review
→ revise when policy allows
→ suspend or terminate
→ generate structured report
→ append user outcome events
```

The first real integration should use an official or supported coding-agent CLI with the user's existing authenticated session or subscription.

### 2.1 Non-goals

The MVP does not include:

- dynamic model routing;
- long-term memory;
- repository graph;
- parallel agents;
- separate planner agent;
- automatic policy learning;
- automatic push, merge, release, or deployment;
- VS Code, web, or mobile interfaces;
- cloud multi-tenancy or billing;
- full ChangeGate;
- non-coding workflows.

---

## 3. Core Principles

### 3.1 TOMTIT owns execution state

Coding-agent sessions are execution details. TOMTIT owns:

- TaskContract;
- RunState;
- attempt count;
- workspace baseline;
- observed evidence;
- stop decisions;
- evaluation events;
- final outcome.

Important state must not exist only inside a Claude Code, Codex, Cursor, or other provider session.

### 3.2 Workers do not control completion

A worker may claim completion, but it cannot write canonical state or set the final outcome.

### 3.3 Evidence outranks worker claims

When claims conflict with Runtime-observed evidence, observed evidence wins.

```text
Worker claim: tests passed
Observed exit code: 1
Canonical interpretation: tests failed
```

### 3.4 Execution is bounded

- Default maximum: 2 attempts.
- Hard maximum: 3 attempts.
- Attempt 3 requires objective progress.
- Every suspended or terminated run produces a structured artifact.

### 3.5 Material ambiguity requires clarification

TOMTIT may infer low-risk defaults. It must request user input when ambiguity materially affects:

- required behavior;
- allowed scope;
- architecture;
- public interfaces;
- dependencies;
- migrations;
- destructive actions;
- deployment;
- time or cost budget.

### 3.6 Subscription-first, provider-neutral

TOMTIT should use supported integrations with coding agents the user already has.

TOMTIT must not:

- scrape provider web interfaces;
- extract login tokens;
- bypass quotas or commercial restrictions;
- rely on undocumented browser automation.

### 3.7 Safety claims must match actual capabilities

A policy instruction is not enforcement.

TOMTIT must not claim that a forbidden action was prevented unless the adapter and Runtime had enforceable control over that action. When visibility or control is incomplete, the limitation must be recorded explicitly.

### 3.8 Workspace isolation is not a security sandbox

A Git worktree isolates patches, not operating-system authority.

A worktree does not prevent a coding agent from:

- reading files outside the worktree;
- accessing user credentials;
- using the network;
- reaching Git remotes;
- changing shared Git metadata when permissions allow.

Dogfood safety controls and product claims must reflect this limitation.

---

## 4. Logical Components and Authority

These are logical boundaries inside one modular monolith.

### 4.1 User Interface

Owns:

- user request;
- clarification exchange;
- status presentation;
- report presentation;
- feedback collection.

It does not own canonical execution state.

### 4.2 Coordinator

Owns logical decisions:

- infer TaskContract;
- decide whether clarification is required;
- assign the fixed worker;
- request implementation or revision;
- choose verification mode;
- propose the next transition;
- stop or escalate according to policy.

The Coordinator proposes transitions. It does not write canonical state.

### 4.3 Runtime

Owns deterministic authority:

- canonical RunState;
- transition validation;
- workspace preparation;
- process supervision;
- capability validation;
- attempt/time/quota/cost enforcement;
- evidence collection;
- append-only events;
- final outcome.

Only the Runtime writes canonical execution state.

### 4.4 Worker

Performs one assignment and returns a structured WorkerResult.

The Worker must not:

- set `COMPLETED`;
- change limits or policy;
- expand scope without approval;
- push, merge, deploy, delete data, or extract credentials;
- write outside the assigned workspace.

### 4.5 Verifier

Inspects the TaskContract, patch, observed evidence, and acceptance criteria. It returns structured findings but does not modify canonical state.

### 4.6 Evaluation

Reads events and outcomes, calculates metrics, classifies failures, and proposes improvements.

Evaluation must not automatically alter:

- current RunState;
- routing;
- prompts;
- execution policy;
- success labels.

---

## 5. Minimal TaskContract

```yaml
task_id: string
goal: string

acceptance_criteria:
  - id: string
    description: string
    verification_method: deterministic | llm_verifier | owner
    required_check_ids:
      - string

allowed_scope:
  - pattern: string
    operations:
      - create | modify | delete

forbidden_actions:
  - string

required_checks:
  - id: string
    argv:
      - string
    cwd_relative: string
    timeout_seconds: integer
    expected_exit_codes:
      - integer

max_attempts: 2
hard_max_attempts: 3
time_budget_seconds: integer
cost_budget_usd: number | null
```

### 5.1 AcceptanceCriterion rules

Each acceptance criterion has one authoritative verification method:

```text
deterministic
llm_verifier
owner
```

Criterion status:

```text
SATISFIED
UNSATISFIED
UNRESOLVED
```

Rules:

- A deterministic criterion is `SATISFIED` only when its required checks provide matching evidence.
- An `llm_verifier` criterion is `SATISFIED` only when a valid LLM VerificationResult explicitly supports it and no blocking evidence contradicts it.
- An owner criterion remains `UNRESOLVED` until the owner records a decision.
- A criterion evaluated by the wrong method remains `UNRESOLVED`.
- Completion is prohibited while any required criterion is `UNRESOLVED` or `UNSATISFIED`, unless the owner explicitly waives it.

### 5.2 RequiredCheck rules

Checks are executed without a shell.

```yaml
id: unit-tests
argv:
  - python
  - -m
  - pytest
  - tests/auth
cwd_relative: .
timeout_seconds: 300
expected_exit_codes:
  - 0
```

Invariants:

- `argv` is a non-empty string array.
- `cwd_relative` is relative to the worktree root.
- Absolute paths and `..` traversal are rejected.
- Pipes, redirects, `&&`, shell expansion, and other shell syntax are unsupported in V0.
- Runtime invokes checks with `shell=False`.
- Runtime compares the observed exit code with `expected_exit_codes`.

### 5.3 ScopeRule rules

Example:

```yaml
allowed_scope:
  - pattern: src/**
    operations:
      - create
      - modify
      - delete
```

Invariants:

- Patterns are always relative to the worktree root.
- Paths are normalized to POSIX form before matching.
- Absolute paths and `..` segments are rejected.
- V0 supports `*`, `?`, and `**`.
- Wildcards match dot-prefixed files and directories under the matched root.
- Default policy is deny outside the listed scope.
- Rename is evaluated as delete of the old path plus create of the new path.
- A symlink may be changed as a path entry, but Runtime must not follow it outside the worktree for scope validation or evidence collection.
- Each operation must be authorized independently.

### 5.4 Contract invariants

- `goal` is non-empty.
- At least one acceptance criterion exists.
- Acceptance criteria and required checks have stable IDs.
- All referenced `required_check_ids` exist.
- `max_attempts` is 1 or 2.
- `hard_max_attempts` does not exceed 3.
- `max_attempts <= hard_max_attempts`.
- `time_budget_seconds > 0`.
- Default forbidden actions include:
  - no push;
  - no merge;
  - no deploy;
  - no destructive data operation;
  - no credential extraction.
- The resolved contract is stored before worker execution.
- User confirmation is required only for material ambiguity or risk.

---

## 6. RunState and Transitions

```text
CREATED
CONTRACT_READY
PREPARING
IMPLEMENTING
COLLECTING_EVIDENCE
CHECKING
VERIFYING
REVISING
WAITING_OWNER
COMPLETED
FAILED
BUDGET_EXHAUSTED
CANCELLED
```

Terminal states:

- `COMPLETED`
- `FAILED`
- `BUDGET_EXHAUSTED`
- `CANCELLED`

`WAITING_OWNER` is suspended, not terminal.

### 6.1 Transition invariants

- Only Runtime writes canonical state.
- Every transition records timestamp, actor, run ID, attempt number, and TransitionReason.
- A WorkerResult is required before leaving `IMPLEMENTING`.
- Fresh observed evidence is required before leaving `COLLECTING_EVIDENCE`.
- A VerificationResult is required before leaving `VERIFYING`.
- Terminal states cannot return to running states.
- Resume from `WAITING_OWNER` requires a recorded owner decision.
- Evaluation is read-only.

### 6.2 TransitionReason

TransitionReason explains movement between states and is distinct from StopReason.

```text
CONTRACT_RESOLVED
WORKSPACE_PREPARED
WORKER_ASSIGNED
WORKER_FINISHED
EVIDENCE_READY
CHECKS_FINISHED
VERIFICATION_REQUESTED
VERIFICATION_FINISHED
REVISION_APPROVED
OWNER_INPUT_REQUESTED
OWNER_RESPONSE_RECEIVED
RUN_RESUMED
TERMINATION_DECIDED
```

---

## 7. Outcomes, Verification Status, and Stop Decisions

### 7.1 Outcome

```text
COMPLETED
WAITING_OWNER
FAILED
BUDGET_EXHAUSTED
CANCELLED
```

### 7.2 VerificationStatus

```text
SELF_CHECK_ONLY
DETERMINISTICALLY_CHECKED
LLM_REVIEWED
```

`LLM_REVIEWED` does not imply statistical or organizational independence.

### 7.3 Completion rules

`COMPLETED` requires:

- every required acceptance criterion is `SATISFIED` or explicitly waived;
- no unresolved blocking finding;
- no unresolved scope violation;
- required checks completed or explicitly waived;
- a valid completion basis.

Valid completion bases:

```text
DETERMINISTIC_EVIDENCE
LLM_REVIEW
OWNER_ACCEPTED_SELF_CHECK
```

Mode A cannot autonomously produce `COMPLETED` from worker self-report. It must transition to `WAITING_OWNER` until the owner accepts the result.

After acceptance:

```yaml
outcome: COMPLETED
verification_status: SELF_CHECK_ONLY
completion_basis: OWNER_ACCEPTED_SELF_CHECK
```

`VERIFIED_WITHIN_AVAILABLE_EVIDENCE` may be used only for deterministic or LLM-reviewed completion, not self-check-only completion.

### 7.4 StopReason

```text
ACCEPTANCE_SATISFIED
USER_DECISION_REQUIRED
NO_PROGRESS
REPEATED_FINDING
SCOPE_VIOLATION
INVALID_WORKER_OUTPUT
ATTEMPT_LIMIT
TIME_LIMIT
QUOTA_LIMIT
COST_LIMIT
POLICY_BLOCKED
USER_CANCELLED
WORKER_FAILURE
VERIFICATION_FAILED
EVIDENCE_INCOMPLETE
```

### 7.5 StopDecision

Every branch that suspends, terminates, or requests another attempt must produce:

```yaml
outcome: COMPLETED | WAITING_OWNER | FAILED | BUDGET_EXHAUSTED | CANCELLED | null
stop_reason: string | null
repairable: true | false
owner_input_required: true | false
next_action: complete | wait_owner | revise | fail | cancel
```

Rules:

- `outcome` and `stop_reason` may be null only when `next_action = revise`.
- `repairable = true` is required for revision.
- `owner_input_required = true` requires `next_action = wait_owner`.
- A StopReason must map deterministically to one Outcome when the run stops.

Default mappings:

| Condition | Outcome | StopReason |
|---|---|---|
| Acceptance satisfied | COMPLETED | ACCEPTANCE_SATISFIED |
| Owner decision needed | WAITING_OWNER | USER_DECISION_REQUIRED |
| No progress after revision | FAILED | NO_PROGRESS |
| Repeated blocking finding | FAILED | REPEATED_FINDING |
| Persistent or non-repairable scope violation | FAILED | SCOPE_VIOLATION |
| Invalid worker output after one normalization retry | FAILED | INVALID_WORKER_OUTPUT |
| Attempt limit reached | FAILED | ATTEMPT_LIMIT |
| Time exhausted | BUDGET_EXHAUSTED | TIME_LIMIT |
| Provider quota exhausted | BUDGET_EXHAUSTED | QUOTA_LIMIT |
| Monetary budget exhausted | BUDGET_EXHAUSTED | COST_LIMIT |
| Policy prevents execution | FAILED | POLICY_BLOCKED |
| Worker failure is non-repairable or repeated | FAILED | WORKER_FAILURE |
| Verification failure is non-repairable | FAILED | VERIFICATION_FAILED |
| Required evidence cannot be collected | FAILED | EVIDENCE_INCOMPLETE |
| User cancelled | CANCELLED | USER_CANCELLED |

---

## 8. Attempt Policy

One attempt is:

```text
implementation or revision assignment
→ WorkerResult
→ fresh workspace snapshot
→ fresh evidence collection
→ deterministic checks
→ optional LLM review
→ next-step decision
```

Tool calls inside one worker session are not separate TOMTIT attempts.

### 8.1 Attempt rules

- Attempt 1 is allowed after contract and workspace preparation.
- Attempt 1 does not evaluate `NO_PROGRESS`, because no previous ProgressSnapshot exists.
- After Attempt 1, failure is classified as repairable, non-repairable, or owner-dependent.
- Attempt 2 is allowed only for a specific repairable failure.
- Attempt 3 is exceptional.
- After Attempt 3, the run must stop and report.
- Attempt-limit failure maps to `FAILED + ATTEMPT_LIMIT`.

### 8.2 Attempt 3 requirements

Attempt 3 is allowed only when:

1. `hard_max_attempts == 3`;
2. Attempt 2 produced objective progress relative to Attempt 1;
3. no new critical regression appeared;
4. remaining work is specific and bounded;
5. no owner decision is required;
6. the workspace patch remains inside allowed scope;
7. time, quota, and cost budgets permit execution.

---

## 9. ProgressSnapshot and Objective Progress

Each checked attempt produces:

```yaml
progress_snapshot:
  attempt_number: integer
  failed_check_ids:
    - string
  blocking_finding_fingerprints:
    - string
  scope_violations:
    - string
  satisfied_criterion_ids:
    - string
  critical_regressions:
    - string
  patch_digest: string
```

Comparison rules:

- Attempt 1 creates the baseline ProgressSnapshot.
- Objective progress is evaluated only for Attempt 2 and later.
- The evaluator compares a normalized previous snapshot with the current snapshot.

Objective progress is true only when at least one improves:

- failed checks decrease;
- blocking finding fingerprints decrease;
- scope violations decrease;
- satisfied criteria increase;
- required checks improve from missing/failed to passed;

and no new critical regression appears.

```python
progress = (
    failed_checks_decreased
    or blocking_findings_decreased
    or scope_violations_decreased
    or satisfied_criteria_increased
    or required_checks_improved
) and not new_critical_regression
```

A changed patch digest proves only that the patch changed. It does not prove progress.

---

## 10. Hard-Stop Conditions

Runtime stops or suspends when:

- the same blocking finding fingerprint appears in two consecutive attempts;
- Attempt 2 or later produces no objective progress;
- a new critical regression appears;
- scope violations persist;
- implementer and verifier disagree about an unresolved requirement;
- worker output remains invalid after one normalization retry;
- owner input is required;
- attempt, time, quota, or cost limit is reached;
- adapter capabilities are insufficient for the task;
- policy blocks execution;
- worker process repeatedly fails;
- completion evidence is missing.

Runtime must never silently continue past a hard stop.

---

## 11. Verification Modes

### 11.1 Mode A — Guided self-check

```text
Worker
→ structured self-report
→ WAITING_OWNER
→ owner accepts or rejects
```

- `verification_status = SELF_CHECK_ONLY`
- Experimental baseline only.
- No autonomous canonical completion.
- Change Policy is a soft guardrail.

### 11.2 Mode B — Deterministic checks

```text
Worker
→ Runtime collects diff/build/test evidence
→ deterministic verification
→ policy decision
```

Minimum checks:

- changed files and operation types;
- allowed scope;
- required `argv` checks and exit codes;
- diff existence;
- evidence completeness;
- forbidden operations when observable.

- `verification_status = DETERMINISTICALLY_CHECKED`

### 11.3 Mode C — Deterministic checks plus LLM review

```text
Worker
→ deterministic evidence
→ LLM verifier
→ policy decision
```

- `verification_status = LLM_REVIEWED`
- Mode C is not assumed to be better than Mode B.
- It is retained only for task classes where data shows positive marginal value.

---

## 12. Change Policy

Expected location:

```text
.tomtit/CHANGE_POLICY.md
```

The file instructs workers to:

- restate goal and expected files;
- prefer the smallest correct change;
- avoid unrelated files;
- avoid unapproved public API or dependency changes;
- inspect final diff;
- run and report checks honestly;
- report risks and skipped checks;
- never push, merge, deploy, delete data, or extract credentials.

`CHANGE_POLICY.md` is a guided self-check. It is not command enforcement and is not a full ChangeGate.

---

## 13. WorkerAdapter and Capabilities

```python
class WorkerAdapter(Protocol):
    def capabilities(self) -> WorkerCapabilities:
        ...

    def execute(self, request: WorkerRequest) -> WorkerResult:
        ...
```

### 13.1 WorkerCapabilities

```yaml
non_interactive_invocation: true | false
structured_output: true | false
command_visibility: full | partial | none
command_control: enforceable | advisory | unavailable
session_resume: true | false
workspace_isolation: worktree | temporary_clone | sandbox | none
filesystem_isolation: sandboxed | none
network_isolation: disabled | restricted | none
```

### 13.2 Capability policy

An adapter is `LIMITED` when any of the following applies:

- command visibility is not `full`;
- command control is not `enforceable`;
- workspace isolation is `none`;
- filesystem isolation is `none`;
- network isolation is `none`.

LIMITED adapters:

- may be used only for local dogfood;
- must use a disposable worktree or temporary clone;
- must not receive production credentials or sensitive repositories;
- must report limitations in the suspension or terminal report;
- cannot support claims that forbidden operations were prevented.

Runtime must fail closed when a task requires a capability the adapter does not provide.

### 13.3 ProcessRunner minimum controls

```text
shell=False
fixed working directory
timeout
process-group termination
environment allowlist
stdout/stderr capture
exit-code capture
output-size limit
```

These controls supervise the outer CLI process. They do not imply visibility into hidden inner commands.

### 13.4 WorkerRequest

```yaml
task_contract:
attempt_number:
assignment_type: implement | revise | verify
blocking_findings:
workspace_path:
change_policy_version:
output_schema_version:
time_limit_seconds:
```

### 13.5 WorkerResult

```yaml
status: completed | blocked | needs_input | failed
summary: string
changed_files:
  - string
commands_executed:
  - string
claimed_checks:
  - string
scope_concerns:
  - string
remaining_risks:
  - string
questions:
  - string
session_reference: string | null
raw_output_path: string
```

Worker claims are recorded but never treated as observed evidence.

---

## 14. VerificationResult, Finding, and Verifier Identity

```yaml
verification_result:
  status: pass | fail | needs_owner

  verifier_identity:
    provider: string
    model: string
    session_reference: string | null
    relationship_to_implementer:
      same_model_new_session |
      different_model |
      different_provider

  findings:
    - finding_id: string
      fingerprint: string
      severity: blocking | warning | info
      category: string
      file: string | null
      line: integer | null
      message: string
      evidence_refs:
        - string
      acceptance_criterion_id: string | null

  missing_evidence:
    - string

  remaining_risks:
    - string
```

### 14.1 Finding fingerprint

The fingerprint must be stable across wording changes.

It should be derived from normalized structural fields such as:

```text
category
+ normalized file path
+ acceptance criterion ID
+ normalized affected symbol or location
```

Raw verifier messages must not be used alone to detect repeated findings.

### 14.2 Independence claims

`LLM_REVIEWED` is the default status for Mode C.

`INDEPENDENTLY_REVIEWED` must not be used unless a later policy defines and verifies independence requirements. A second session of the same model is recorded as `same_model_new_session`, not assumed independent.

---

## 15. Workspace Baseline, Isolation, and Attempt Snapshots

### 15.1 Git requirement

MVP V0 supports Git repositories only. Non-Git repositories are rejected.

### 15.2 Patch-isolation rules

1. Every run uses an isolated Git worktree or temporary clone.
2. Runtime records the base commit and pre-run status.
3. A dirty source repository is not modified. Runtime either:
   - rejects the run; or
   - creates the workspace from a selected clean commit while recording pre-existing user changes as excluded.
4. Each attempt records before/after patch snapshots.
5. Diff attribution is calculated from the run's base commit and attempt snapshots.
6. Scope violations are never silently retained.
7. Revision continues on the previous patch only when the patch remains inside allowed scope and is considered repairable.
8. When a run stops, the workspace is retained for review by default and is never merged automatically.
9. Cleanup is explicit and recorded.
10. Workers must be instructed and configured to use the assigned workspace.

### 15.3 Dogfood environment controls

Before a real adapter is used:

- use non-sensitive repository fixtures;
- do not provide production credentials;
- use an environment allowlist;
- remove, override, or disable Git remotes in the disposable workspace where practical;
- record `filesystem_isolation`;
- record `network_isolation`;
- state explicitly when neither is enforced.

A worktree alone must not be described as a security sandbox.

For higher-risk adapters or repositories, use a temporary clone or a real sandbox rather than a shared worktree.

### 15.4 Scope violation handling

A scope violation causes:

- deterministic failure;
- a structured finding;
- either a bounded revision request to remove the violation or immediate stop, depending on risk;
- no automatic merge or acceptance while the violation remains.

---

## 16. Observed Evidence

```yaml
repository:
  base_revision:
  source_pre_run_status:
  workspace_path:
  workspace_isolation:
  filesystem_isolation:
  network_isolation:
  git_remote_state:

attempt:
  number:
  before_patch_digest:
  after_patch_digest:
  before_patch_artifact:
  after_patch_artifact:

patch:
  changed_files:
    - path:
      operation: create | modify | delete
  diff_lines:
  artifact_path:

checks:
  - check_id:
    argv:
      - string
    cwd_relative:
    exit_code:
    duration_seconds:
    stdout_path:
    stderr_path:

scope:
  violations:

process:
  worker:
  adapter_version:
  provider_cli_version:
  start_time:
  end_time:
  exit_code:

capabilities:
  command_visibility:
  command_control:
  workspace_isolation:
  filesystem_isolation:
  network_isolation:
  adapter_limited:

policy:
  change_policy_version:
  change_policy_digest:
```

Raw source code is not uploaded by default.

---

## 17. Evaluation and Outcome Events

Events are append-only and versioned.

```yaml
event_id:
schema_version:
timestamp:
event_type:
task_id:
run_id:
attempt_number:
actor:
payload:
```

Minimum event types:

```text
TASK_SUBMITTED
CONTRACT_INFERRED
CLARIFICATION_REQUESTED
CLARIFICATION_RECEIVED
RUN_STARTED
WORKSPACE_PREPARED
WORKER_ASSIGNED
WORKER_STARTED
WORKER_FINISHED
EVIDENCE_COLLECTED
CHECK_PASSED
CHECK_FAILED
VERIFICATION_STARTED
VERIFICATION_FINISHED
REVISION_REQUESTED
WAITING_OWNER
OWNER_DECISION_RECORDED
RUN_RESUMED
POLICY_BLOCKED
RUN_COMPLETED
RUN_FAILED
RUN_CANCELLED
BUDGET_EXHAUSTED
SUSPENSION_REPORT_GENERATED
REPORT_GENERATED
RESULT_ACCEPTED
RESULT_EDITED
RESULT_REJECTED
ESCAPED_DEFECT_REPORTED
ESCAPED_DEFECT_CLEARED
OUTCOME_SNAPSHOT_GENERATED
```

Raw tool traces are artifacts, not required analytics events.

TaskOutcome is a derived snapshot generated from append-only events. Existing outcome history is not overwritten.

---

## 18. Suspension Report, Terminal Report, and Outcome Snapshot

### 18.1 Suspension report

When entering `WAITING_OWNER`, Runtime writes an immutable:

```text
suspension.v1.json
```

It includes:

- current state and reason;
- unresolved decision;
- evidence collected so far;
- worker claims;
- verification status;
- remaining risks;
- options presented to the owner.

A suspension report is not a final report.

After `OWNER_DECISION_RECORDED`, the run may resume. The suspension artifact remains unchanged.

### 18.2 Terminal report

Runtime writes:

```text
report.v1.json
```

only after entering a terminal state:

- `COMPLETED`
- `FAILED`
- `BUDGET_EXHAUSTED`
- `CANCELLED`

It includes:

```yaml
outcome:
stop_reason:
verification_status:
completion_basis:

goal:
interpreted_scope:
acceptance_criteria:

attempts:
elapsed_seconds:

implementer:
verifier:
verification_mode:
adapter_capability_limitations:

worker_claims:
observed_evidence:
verifier_assessment:

changed_files:
diff_summary:
checks_executed:
check_results:
scope_violations:

blocking_findings:
remaining_risks:
missing_evidence:
recommended_next_action:
```

`report.v1.json` is immutable.

### 18.3 TaskOutcome snapshot

User feedback and escaped-defect updates are appended as events.

A derived snapshot may be generated as:

```text
task_outcome.v1.json
```

```yaml
task_id:
run_id:

execution_outcome:
stop_reason:
verification_status:

user_result: accepted | edited | rejected | unknown
accepted_without_manual_edit: true | false | null

human_intervention_seconds: integer | null
human_intervention_source:
  explicit_user_report | interaction_timestamps | unavailable

clarification_count:
user_override_count:

scope_valid:
required_checks_passed:
evidence_complete:

agent_execution_rounds:
user_reprompts:
user_clarifications:
manual_commands:
elapsed_seconds:

total_cost_usd: number | null
cost_source:
  provider_reported | estimated | unavailable

failure_category:
escaped_defect_status: unknown | none_reported | reported | cleared
feedback_reason:
snapshot_generated_at:
```

The default escaped-defect status immediately after terminal execution is `unknown`.

---

## 19. Failure Taxonomy

```text
REQUIREMENT_MISUNDERSTOOD
INSUFFICIENT_CLARIFICATION
SCOPE_VIOLATION
WRONG_ASSIGNMENT
CONTEXT_MISSING
IMPLEMENTATION_DEFECT
CHECK_INADEQUATE
VERIFIER_FALSE_POSITIVE
VERIFIER_MISSED_DEFECT
RETRY_LOOP
TOOL_FAILURE
WORKER_OUTPUT_INVALID
USER_CANCELLED
COST_TOO_HIGH
TIME_TOO_HIGH
POLICY_BLOCKED
UNKNOWN
```

---

## 20. Mandatory Fake Scenarios

### Scenario 1 — Deterministic completion

```text
Attempt 1
→ worker succeeds
→ deterministic checks pass
→ COMPLETED
→ ACCEPTANCE_SATISFIED
```

### Scenario 2 — Revise then complete

```text
Attempt 1
→ blocking finding
→ Attempt 2 improves
→ checks pass
→ COMPLETED
```

### Scenario 3 — Waiting owner

```text
Attempt 1
→ material unresolved decision
→ WAITING_OWNER
→ suspension.v1.json
→ owner decision event required before resume
→ terminal report only after terminal state
```

### Scenario 4 — No progress

```text
Attempt 1 fails and creates baseline ProgressSnapshot
→ Attempt 2 shows no objective improvement
→ FAILED
→ NO_PROGRESS
```

### Scenario 5 — Attempt limit

```text
Attempt 1 fails
→ Attempt 2 improves
→ Attempt 3 allowed
→ still fails
→ FAILED
→ ATTEMPT_LIMIT
```

Additional required tests before a real adapter:

- invalid worker output;
- repeated finding fingerprint;
- persistent scope violation;
- time limit;
- user cancellation;
- LIMITED adapter rejected for an incompatible task;
- Mode A waits for owner acceptance;
- deterministic criterion remains unresolved without its required check;
- owner criterion remains unresolved without owner decision;
- scope rename is evaluated as delete plus create.

---

## 21. Build Order

1. Accept this policy.
2. Create machine-readable schemas:
   - TaskContract;
   - RequiredCheck;
   - ScopeRule;
   - AcceptanceCriterion;
   - WorkerCapabilities;
   - WorkerResult;
   - Finding;
   - VerifierIdentity;
   - VerificationResult;
   - StopDecision;
   - ProgressSnapshot;
   - EvaluationEvent;
   - TaskOutcome.
3. Implement fake worker and fake verifier.
4. Implement controlled WorkspaceManager.
5. Implement ScopeChecker and `argv`-based CheckRunner.
6. Implement attempt controller, ProgressEvaluator, and hard stops.
7. Implement append-only events.
8. Generate suspension report, terminal report, and derived outcome snapshot.
9. Pass all mandatory fake scenarios.
10. Add one official CLI worker adapter.
11. Run the dogfood protocol.
12. Change policy only after a comparable cohort.

---

## 22. Definition of Done — Fake Loop

The fake loop is complete only when:

- all mandatory scenarios pass deterministically;
- workers cannot write canonical state or set completion;
- attempts never exceed the hard limit;
- Attempt 1 never produces `NO_PROGRESS`;
- Attempt 3 requires objective progress;
- finding repetition uses fingerprints, not raw messages;
- acceptance criteria use the required verification method;
- checks use `argv` with `shell=False`;
- scope semantics are root-relative and default-deny;
- each attempt has before/after workspace snapshots;
- every transition emits a versioned event;
- resume requires an owner decision event;
- `WAITING_OWNER` creates a suspension report, not a terminal report;
- every terminal run has normalized Outcome and StopReason;
- `report.v1.json` is immutable;
- TaskOutcome is derived from append-only events;
- evaluation does not parse free-form logs for canonical fields;
- terminal states cannot resume;
- the same fixture reproduces the same outcome.

---

## 23. Dogfood Experiment Requirement

Before pilot, TOMTIT must be compared against direct-agent usage under `DOGFOOD_EXPERIMENT_PROTOCOL_V0.md`.

The protocol must separate:

- the identical agent-visible request used in both arms;
- the hidden evaluation contract used only for scoring;
- TOMTIT's inferred TaskContract.

Do not change both worker and orchestration workflow in the same comparison.

---

## 24. Definition of Done — Dogfood

The MVP is ready for a small pilot only when:

- 100% of dogfood runs produce complete traces and reports;
- no run exceeds attempt policy;
- safety claims accurately reflect adapter capabilities;
- no automatic push, merge, deploy, or destructive action occurs;
- acceptance is not lower than the direct-agent baseline;
- human intervention is not higher than baseline;
- failures are attributable to contract, worker, verification, runtime, or policy;
- contract inference accuracy is measured;
- user can accept, mark edited, or reject;
- local evaluation data can be viewed, exported, and deleted;
- raw source upload is disabled by default.

---

## 25. Policy Change Rules

Policy is not changed automatically from one task.

A change proposal requires:

- at least 10 comparable tasks, preferably 15–30;
- repeated evidence;
- direct-agent baseline comparison;
- owner review.

```yaml
policy_version:
change:
reason:
supporting_run_ids:
expected_effect:
approved_by:
timestamp:
```

---

## 26. Final MVP Decision Rule

Expand TOMTIT only when evidence shows:

- result quality is not lower than direct-agent usage;
- user intervention decreases;
- bounded attempts prevent wasteful loops;
- observed evidence improves completion decisions;
- reports explain failures well enough to guide improvements.

Otherwise, improve the execution workflow before adding routing, memory, graph, additional interfaces, or broader domains.
