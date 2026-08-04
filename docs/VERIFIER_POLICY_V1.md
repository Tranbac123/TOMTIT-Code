# TOMTIT Coordinator — Verifier Routing and Review Policy V1

## 0. Document governance

```yaml
document_name: TOMTIT Coordinator — Verifier Routing and Review Policy
document_version: V1
document_status: FINAL_CANDIDATE_FOR_OWNER_APPROVAL
reviewed_against:
  - TOMTIT Coordinator — Official Build and AI Coding Process V1
  - TOMTIT Coordinator — Evaluation Contract V1
```

This policy extends the approved build and evaluation contracts. It does not replace their authority, review-budget, artifact-binding, or ownership rules.

---

# 1. Purpose

The verifier exists to catch defects that deterministic checks and bounded implementer self-checks are unlikely to catch.

The verifier must not become a mandatory second implementer for every task.

Core rule:

```text
Use the cheapest verification method that is strong enough
for the task's real risk.
```

A separate AI verifier is required only when the expected reduction in correctness risk justifies its added time, cost, and coordination overhead.

---

# 2. Terminology

```text
Verification:
  the overall activity of checking task output.

Verifier:
  the role or stage that independently evaluates an exact artifact.

Reviewer:
  the concrete human or model performing the verifier role.
```

`VERIFIER_MODE=NONE` means no separate independent AI verifier. It does not mean no checks.

---

# 3. Verification layers

TOMTIT uses three layers in increasing cost order.

## 3.1 Deterministic verification

Use whenever applicable:

```text
unit tests
integration tests
build
lint
format check
type check
schema validation
contract validation
exit status
artifact existence
scope/diff checks
evidence provenance and freshness
```

Deterministic checks are the default source of truth for machine-checkable claims.

## 3.2 Mechanical verification

A lightweight reviewer verifies:

```text
exact diff and file scope
required commands and exit codes
report completeness
commit lineage
artifact identity
source-revision binding
obvious acceptance-criteria coverage
```

Default reviewer:

```text
Codex Terra or a cheaper reviewer
```

## 3.3 Independent correctness verification

A stronger independent reviewer evaluates:

```text
state and lifecycle correctness
authority boundaries
atomicity and recovery
cross-module contracts
negative cases
error propagation
side-effect safety
whether observed evidence supports the claimed outcome
```

Default reviewer:

```text
Codex Sol
```

The verifier does not modify source or tests and does not authorize the next gate.

---

# 4. Verifier modes

Every task must select exactly one mode.

```text
NONE
MECHANICAL
INDEPENDENT_CORRECTNESS
OWNER_GATED_CORRECTNESS
```

## 4.1 NONE

No separate AI verifier.

Allowed only when all skip conditions in Section 6 are satisfied.

Required controls still include:

```text
implementer self-check
relevant deterministic checks
exact diff inspection
scope verification
clean final worktree
```

## 4.2 MECHANICAL

Use when behavior risk is low but evidence, Git scope, serialization, or report correctness needs an independent check.

## 4.3 INDEPENDENT_CORRECTNESS

Use when a plausible defect could survive ordinary tests and materially affect behavior, state, or user trust.

## 4.4 OWNER_GATED_CORRECTNESS

Use when the change can cause irreversible or high-impact effects, or when unresolved MEDIUM-or-higher risk requires Owner acceptance.

Examples:

```text
data deletion
authentication or authorization
secret handling
billing or payment
auto push, merge, deploy or release
irreversible external side effects
policy exceptions
accepted unresolved correctness risk
```

---

# 5. Mandatory independent-verifier triggers

Select at least `INDEPENDENT_CORRECTNESS` when any trigger applies.

## 5.1 Canonical state and authority

```text
RunState or lifecycle transitions
canonical writer changes
DecisionEvent creation or ordering
attempt ownership
approval authority
Runtime versus Evaluation ownership
state restoration or rollback
```

## 5.2 Atomicity, persistence, and concurrency

```text
multi-record consistency
atomic commit boundaries
recovery after partial failure
persistence schema or migration
concurrency or lease behavior
idempotency of side effects
```

## 5.3 Trust boundaries and external effects

```text
permissions
authentication
authorization
secrets
network or filesystem side effects
process execution
user-data mutation
delete, deploy, publish, merge or payment behavior
```

## 5.4 Cross-boundary contracts

```text
public API or CLI contract
serialization used across components
backward compatibility
adapter protocol
Runtime-to-worker contract
terminal-report or source-revision binding
```

## 5.5 Weak or conflicting evidence

```text
worker claims conflict with observed evidence
required checks are missing or flaky
result depends on unverified assumptions
scope expanded beyond the task contract
failure recovery is not directly tested
a prior escaped defect is being corrected
```

## 5.6 New unstable capability

The first implementation of a new adapter, task class, execution path, or authority boundary requires independent correctness verification even if the diff is small.

Diff size alone never determines risk.

---

# 6. Conditions for skipping a separate verifier

`VERIFIER_MODE=NONE` is allowed only when every condition is true:

```text
1. The task does not touch canonical state, authority, persistence,
   external side effects, security, or cross-boundary contracts.

2. The change is bounded to one well-understood area.

3. Acceptance criteria are objective and machine-checkable.

4. Relevant deterministic checks exist and pass.

5. The diff is easy to inspect and contains no unexplained expansion.

6. Failure would be local, reversible, and low impact.

7. The task is not the first example of a new capability or task class.

8. There is no conflicting evidence or unresolved MEDIUM-or-higher risk.
```

If any condition is false, route to at least `MECHANICAL`.

---

# 7. Task examples

| Task type | Default verifier mode | Notes |
|---|---|---|
| Typo, wording, comment, README correction | `NONE` | Inspect diff; no behavior change |
| Formatting or import cleanup with no semantic change | `NONE` | Run formatter/lint/tests relevant to touched files |
| Isolated CSS or text adjustment | `NONE` or `MECHANICAL` | Use build and visual evidence when user-visible |
| Add a normal unit test for unchanged behavior | `NONE` | Use `MECHANICAL` if the test defines safety or acceptance policy |
| Small pure-function bug fix with a reproduced failing test | `NONE` or `MECHANICAL` | No state, side effects, or contract changes |
| Add a small component or bounded endpoint field | `MECHANICAL` | Escalate if API compatibility or authorization changes |
| Serialization or schema change | `INDEPENDENT_CORRECTNESS` | Contract and backward-compatibility risk |
| First Claude Code CLI adapter implementation | `INDEPENDENT_CORRECTNESS` | New external boundary and evidence collection |
| Later mechanical adapter patch with reproduced behavior | `MECHANICAL` | Escalate if invocation, cancellation, session, or provenance changes |
| State machine, attempt lifecycle, stop logic | `INDEPENDENT_CORRECTNESS` | Canonical behavior |
| DecisionEvent plus transition atomicity | `INDEPENDENT_CORRECTNESS` | Runtime authority and consistency |
| Database migration, concurrency, lease, recovery | `INDEPENDENT_CORRECTNESS` | Persistence and partial-failure risk |
| Authentication, permissions, secrets, deletion, deploy | `OWNER_GATED_CORRECTNESS` | High-impact trust boundary |
| Governance docs with only wording cleanup | `MECHANICAL` | Deep review not required |
| Governance semantics changing authority or gate rules | `INDEPENDENT_CORRECTNESS` | Policy affects future execution |

---

# 8. Coordinator routing algorithm

The Coordinator must make verifier selection before implementation begins and may escalate after observing the diff or evidence.

```text
1. Run mandatory-trigger check.

2. If high-impact or irreversible trigger exists:
     OWNER_GATED_CORRECTNESS

3. Else if canonical-state, authority, atomicity, persistence,
   cross-boundary, new-capability, or weak-evidence trigger exists:
     INDEPENDENT_CORRECTNESS

4. Else if all skip conditions are satisfied:
     NONE

5. Else:
     MECHANICAL
```

Post-implementation escalation is required when:

```text
diff exceeds authorized scope
required checks are missing or flaky
worker claims conflict with evidence
unexpected files or contracts changed
recovery path changed
new external behavior appears
```

The Coordinator must not downgrade a mandatory trigger because the implementer reports high confidence.

---

# 9. Required verifier-decision record

Every task prompt or run record must contain:

```text
TASK_RISK_CLASS:
VERIFIER_MODE:
VERIFIER_REQUIRED:
VERIFIER_REASON:
MANDATORY_TRIGGERS:
DETERMINISTIC_CHECKS:
ESCALATION_TRIGGERS:
TARGET_SOURCE_REVISION:
REVIEW_BUDGET_STATUS:
```

Example:

```text
TASK_RISK_CLASS: LOW
VERIFIER_MODE: NONE
VERIFIER_REQUIRED: false
VERIFIER_REASON: Isolated wording change with no runtime behavior.
MANDATORY_TRIGGERS: none
DETERMINISTIC_CHECKS: markdown lint, exact diff inspection
ESCALATION_TRIGGERS: unexpected code/config change
```

---

# 10. Verifier input contract

An independent verifier receives only bounded, auditable inputs:

```text
task contract
acceptance criteria
non-goals
exact source revision or diff range
implementation report
required check outputs and exit codes
observed evidence
worker claims
current review-budget status
previous authorized findings, when applicable
```

The verifier must not review an ambiguous moving worktree when an exact revision can be provided.

---

# 11. Verifier output contract

The verifier returns:

```text
VERDICT
TARGET_SOURCE_REVISION
REVIEWED_DIFF_RANGE
CHECKS_RERUN
INDEPENDENT_REPRODUCTIONS
FINDINGS
EVIDENCE_REFS
SCOPE_STATUS
UNPROVEN_ITEMS
NEXT_GATE_RECOMMENDATION
```

Allowed verdicts:

```text
APPROVE
APPROVE_WITH_NON_BLOCKING_FINDINGS
REQUEST_CHANGES
BLOCKED
```

Every BLOCKER, HIGH, or MEDIUM finding must include:

```text
severity
reproduction or concrete evidence
correctness impact
affected scope
why deterministic checks did not already prove safety
```

LOW, style, cleanup, or theoretical findings do not block progression.

The verifier assesses the artifact. It does not mutate canonical state, implement fixes, or authorize the next gate.

---

# 12. Review budget and stop rules

Default per verified gate or task:

```text
1 independent verifier pass
1 bounded corrective patch
1 corrective rereview
```

Stop verification when:

```text
acceptance criteria are satisfied
deterministic checks pass
no reproduced BLOCKER/HIGH/MEDIUM remains
scope is valid
evidence is complete
```

Do not:

```text
run a second verifier only to obtain another opinion
reverify an unchanged artifact
open a patch for LOW findings
search indefinitely for theoretical edge cases
replace deterministic tests with repeated LLM review
continue beyond budget without Owner authorization
```

---

# 13. Build-governance profile

For building TOMTIT itself:

## 13.1 Verify at gate boundaries, not after every micro-patch

Independent verification is normally required for:

```text
gate-closing implementation
canonical state or authority changes
atomicity and recovery changes
first real adapter integration
corrections for reproduced MEDIUM-or-higher findings
```

Separate independent verification is normally skipped for:

```text
doc wording
bounded test additions
mechanical report fixes
formatting
small local implementation corrections already covered by the same gate review
```

A gate review may cover several bounded implementation commits when the prompt declares one exact reviewed diff range and the lineage remains auditable.

## 13.2 Model routing

```text
MECHANICAL:
  Codex Terra or cheaper reviewer

INDEPENDENT_CORRECTNESS:
  Codex Sol

OWNER_GATED_CORRECTNESS:
  Codex Sol verdict plus explicit Owner gate decision
```

---

# 14. TOMTIT product-runtime profile

For user coding tasks executed through TOMTIT:

## 14.1 Always-on controls

Every task receives:

```text
scope/diff validation
required deterministic checks
evidence freshness and provenance validation
worker-claim versus observed-evidence comparison
attempt and budget enforcement
```

## 14.2 Conditional AI verifier

Run an independent AI verifier only when routing selects:

```text
MECHANICAL
INDEPENDENT_CORRECTNESS
OWNER_GATED_CORRECTNESS
```

The verifier verdict becomes observed evidence for Coordinator decision-making. It does not directly mutate Runtime state.

## 14.3 New task-class warmup and sampling

Recommended dogfood defaults:

```text
New task class or new adapter path:
  verify the first 3 eligible tasks.

After 3 consecutive accepted tasks with no scope violation,
MEDIUM-or-higher reviewer finding, or escaped defect:
  reduce stable low-risk classes to 20% random mechanical audit.

After any scope violation, escaped defect, or MEDIUM-or-higher finding:
  return that task class to 100% verification for the next 5 tasks.
```

Sampling applies only to low-risk stable task classes. Mandatory-trigger tasks are always verified.

These defaults may be adjusted after dogfood evidence; they are not learned routing.

---

# 15. Metrics for verifier value

Measure whether the verifier earns its cost:

```text
verifier_invocation_rate
blocking_finding_rate
finding_reproduction_rate
verifier-caused_revision_success_rate
escaped_defect_rate_with_verifier
escaped_defect_rate_without_verifier
active_review_seconds
added_elapsed_time
human_intervention_delta
```

A verifier policy is over-verifying when:

```text
invocation rate is high
but reproduced blocking-finding rate is persistently low,
quality does not improve,
and review time materially increases.
```

A verifier policy is under-verifying when:

```text
escaped defects or scope violations cluster in unverified task classes,
or users repeatedly reject outputs that deterministic checks accepted.
```

Do not infer causality from a small sample. Use dogfood results as exploratory evidence.

---

# 16. Anti-patterns

Do not use a verifier to:

```text
compensate for a vague TaskContract
repeat the implementer's self-review
review only prose claims instead of the exact artifact
approve based on model confidence
request aesthetic refactors outside acceptance criteria
block on non-reproduced theoretical concerns
run multiple frontier models on every small task
hide missing deterministic tests
turn every user task into a governance gate
```

---

# 17. Final routing principle

```text
Deterministic checks first.

Independent AI verification only when risk or evidence justifies it.

Deep verification for state, authority, atomicity, trust boundaries,
and new external integrations.

Skip separate verification for bounded, reversible, objectively checked work.

Sample stable low-risk task classes instead of verifying everything forever.
```
