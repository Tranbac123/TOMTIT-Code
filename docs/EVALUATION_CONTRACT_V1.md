# TOMTIT Coordinator — Evaluation Contract V1

## 0. Document governance

```yaml
document_name: TOMTIT Coordinator — Evaluation Contract
document_version: V1
document_status: FINAL_CANDIDATE_FOR_OWNER_APPROVAL

reviewed_against:
  - SYSTEM.md
  - MVP Execution Policy V0.3
  - Dogfood Experiment Protocol V0.2
  - TOMTIT Coordinator — Official Build and AI Coding Process V1
```

Owner approval is recorded only in external gate evidence:

```yaml
contract_status: APPROVED
approved_version: V1
approved_by: OWNER
approved_at: <timestamp>
document_sha256: <SHA-256 of exact finalized document bytes>
```

The document must not contain its own approval hash.

After approval:

- the document bytes must not change;
- later modifications create a new candidate;
- a new candidate requires a new hash and Owner approval.

If an upstream authority changes version or semantics, this contract requires a drift review before further use.

---

# 1. Evaluation purpose

TOMTIT evaluates four independent layers:

```text
1. Runtime correctness
2. Task quality
3. Decision quality
4. Product value
```

Evaluation relies only on:

```text
observed execution state
observed evidence
allowed actions
selected actions
append-only assessments
later observed outcomes
controlled evaluation experiments
```

Hidden chain of thought is not canonical evidence.

---

# 2. Ownership and authority

## 2.1 Runtime-owned canonical execution data

Runtime is the sole canonical writer for:

```text
run_state
terminal_outcome
stop_reason
attempts
evidence
execution events
decision events
terminal report
```

Evaluation must not modify canonical execution state.

## 2.2 Coordinator

Coordinator proposes structured decisions.

Coordinator must not:

- append canonical `DecisionEvent`;
- mutate canonical state;
- assign `append_sequence`;
- claim that a transition committed.

## 2.3 Evaluation-owned append-only data

The Evaluation append store owns:

```text
ReviewAssessmentEvent
OwnerAssessmentEvent
HumanInterventionEvent
DecisionAssessmentEvent
EscapedDefectEvent
```

Evaluation must not:

- create or modify `DecisionEvent`;
- alter Runtime execution state;
- replace execution evidence;
- write derived metrics into Runtime state.

## 2.4 Reviewer and Owner

Reviewer and Owner:

- assess an exact terminal artifact;
- do not change Runtime state;
- do not assign `append_sequence`;
- do not create report identity or source revision;
- cannot apply approval from an old artifact to a new artifact.

---

# 3. Run state, terminal outcome and stop reason

## 3.1 Run state

`run_state` must use the exact canonical `RunState` enum from approved execution policy and domain contracts.

`WAITING_OWNER` is a suspension state, not a terminal outcome.

## 3.2 Terminal outcome

`terminal_outcome`:

- uses the exact canonical terminal-outcome enum;
- is `null` while the run is nonterminal;
- never represents `WAITING_OWNER`.

## 3.3 Stop reason

`stop_reason` must use the exact canonical `StopReason` enum defined by:

```text
approved execution policy
approved domain contracts
current source of truth
```

This contract must not invent example enum values that may diverge from source.

Outcome and stop reason remain separate fields.

---

# 4. TerminalReport identity prerequisite

Every assessable terminal report must contain:

```text
report_id
run_id
source_revision
schema_version
```

## 4.1 Identity authority

`report_id`:

- is assigned by Runtime;
- is immutable;
- is not created by reviewer or Owner;
- cannot change after report canonicalization.

`source_revision`:

- is written by Runtime from the confirmed execution artifact;
- is immutable;
- identifies the exact source output represented by the report.

## 4.2 Exact-byte hash

```text
target_terminal_report_hash =
SHA-256 of exact serialized report.v1.json bytes
```

The exact bytes must contain:

```text
report_id
run_id
source_revision
schema_version
```

If a terminal report lacks `report_id` or `source_revision`:

```text
no ReviewAssessmentEvent or OwnerAssessmentEvent
is valid for that report
```

---

# 5. SourceRevision

Schema:

```text
SourceRevision:
  revision_type
  revision_value
```

Supported revision types are reserved in V1:

```text
GIT_COMMIT
PATCH_SHA256
WORKTREE_SNAPSHOT_SHA256
```

## 5.1 Active revision type

For current build governance:

```text
revision_type = GIT_COMMIT
revision_value = <exact source commit hash>
status = ACTIVE
```

## 5.2 Reserved revision types

```text
PATCH_SHA256
WORKTREE_SNAPSHOT_SHA256
```

have status:

```text
RESERVED_NOT_ACTIVE
```

They must not be used for valid assessment binding until an approved canonicalization contract exists.

## 5.3 Canonicalization prerequisite before real-adapter activation

Before activating `PATCH_SHA256` or `WORKTREE_SNAPSHOT_SHA256`, an approved contract must define:

```text
newline normalization
file ordering
path normalization
inclusion rules
exclusion rules
binary-file handling
metadata inclusion or exclusion
exact serialization format
hash algorithm application
```

Until then:

- uncommitted adapter output cannot receive an assessment that claims canonical `PATCH_SHA256` or `WORKTREE_SNAPSHOT_SHA256` provenance;
- the adapter must mark source-revision capability as limited;
- the system must not silently invent local canonicalization.

Assessment validation must always prove:

```text
assessment.target_source_revision
==
terminal_report.source_revision
```

---

# 6. Decision proposal and canonical commit

Required flow:

```text
Coordinator proposes a structured decision
→ Runtime validates the proposal
→ Runtime prepares DecisionEvent and state transition
→ Runtime commits both within one bounded canonical operation
```

Runtime validates against:

```text
current state
allowed actions
policy constraints
attempt state
budget state
binding execution constraints
```

Atomic invariant:

```text
SUCCESS:
immutable DecisionEvent exists
AND canonical state transition is applied

FAILURE:
DecisionEvent does not exist
AND canonical state transition is not applied
```

The system must not retain:

```text
DecisionEvent committed
but canonical transition failed
```

A distributed transaction is not required.

The requirement is a bounded Runtime-owned operation:

```text
prepare
→ validate
→ commit both
→ commit neither on failure
```

---

# 7. DecisionEvent draft and persisted event

Callers must not supply `append_sequence`.

Semantic distinction:

```text
DecisionEventDraft:
  no append_sequence

PersistedDecisionEvent:
  append_sequence assigned by Runtime append store
```

V1 does not require two separate Pydantic classes, but:

- proposal APIs must not accept `append_sequence`;
- validators must reject caller-supplied sequence values;
- only the owning append store creates the persisted value.

Persisted fields:

```text
decision_id
decision_schema_version
policy_version
task_contract_hash
run_id
attempt_number
state_before
evidence_snapshot_ids
allowed_actions
selected_action
reason_code
binding_constraints
budget_snapshot
decision_timestamp
state_after
append_sequence
```

The persisted event is immutable.

Canonical task-contract hash:

```text
SHA-256 of canonical JSON
with stable key ordering
and explicit schema version
```

Canonical representation requires:

- deterministic key order;
- deterministic scalar encoding;
- explicit schema version;
- no dependence on non-semantic whitespace.

---

# 8. append_sequence authority

`append_sequence` is assigned only by the append store that owns its stream.

It must be:

```text
strictly increasing within its stream
unique within its stream
immutable after append
```

It must not be supplied or overridden by:

```text
Coordinator
Evaluation
reviewer
Owner
external caller
```

Streams are separate:

```text
runtime_execution_stream
evaluation_assessment_stream
```

Sequence values must never be compared across streams.

The latest-assessment rule compares only events with:

```text
same target artifact
same source revision
same authority level
same assessment stream
```

Timestamp is for display and audit, not primary ordering authority.

---

# 9. ReviewAssessmentEvent

```text
ReviewAssessmentEvent:
  assessment_id
  assessment_schema_version
  run_id
  target_terminal_report_id
  target_terminal_report_hash
  target_source_revision
  review_outcome
  reviewer
  timestamp
  append_sequence
  evidence_refs
```

Allowed review outcomes:

```text
REVIEWER_APPROVED
REVIEWER_APPROVED_WITH_NON_BLOCKING_FINDINGS
REVIEWER_REQUESTED_CHANGES
REVIEWER_BLOCKED
```

An assessment is valid only when:

```text
run_id matches
target_terminal_report_id matches report_id
target_terminal_report_hash matches exact report bytes
target_source_revision matches report source_revision
source revision type is active
```

Assessment of an old report or source revision cannot apply to a new output.

---

# 10. OwnerAssessmentEvent

```text
OwnerAssessmentEvent:
  assessment_id
  assessment_schema_version
  run_id
  target_terminal_report_id
  target_terminal_report_hash
  target_source_revision
  owner_outcome
  timestamp
  append_sequence
  evidence_refs
```

Allowed outcomes:

```text
OWNER_ACCEPTED
OWNER_REJECTED
```

Do not create:

```text
OWNER_UNREVIEWED
```

Derived status:

```text
owner_review_status = UNREVIEWED
```

when no valid Owner assessment exists for the exact artifact and source revision.

---

# 11. Effective-assessment rules

Assessment applies only to the exact target:

```text
run_id
terminal report ID
terminal report SHA-256
source revision type
source revision value
```

Authority precedence:

```text
valid OwnerAssessmentEvent
>
valid ReviewAssessmentEvent proxy
```

Reviewer proxy is used only when no valid Owner assessment exists for the same exact target.

When multiple assessments share the same authority and target:

```text
effective assessment =
assessment with greatest append_sequence
inside evaluation_assessment_stream
```

Append-only history retains all prior assessments.

Assessment of an old artifact or source revision never applies to a newer output.

---

# 12. Product-success predicate

```text
PRODUCT_SUCCESS =
  terminal_outcome == COMPLETED
  AND effective_acceptance_result in {
    OWNER_ACCEPTED,
    REVIEWER_APPROVED,
    REVIEWER_APPROVED_WITH_NON_BLOCKING_FINDINGS
  }
  AND required_checks_passed == true
  AND criteria_satisfied == true
  AND blocking_regression_count == 0
  AND unresolved_scope_violation_count == 0
  AND required_evidence_complete == true
```

Override:

```text
effective_acceptance_result == OWNER_REJECTED
→ PRODUCT_SUCCESS = false
```

A valid Owner assessment overrides reviewer proxy for the same artifact.

Runtime `COMPLETED` alone is not product success.

---

# 13. Acceptance basis

Every experiment must predeclare:

```text
primary_acceptance_basis:
  OWNER
  or
  INDEPENDENT_REVIEW_PROXY
```

Alternatively, report metrics separately for both bases.

Always report:

```text
owner_acceptance_rate
reviewer_proxy_acceptance_rate
```

A combined exploratory number must disclose:

```text
owner-accepted task count
reviewer-proxy-accepted task count
percentage composition
```

---

# 14. DecisionAssessmentEvent

```text
DecisionAssessmentEvent:
  assessment_id
  assessment_schema_version
  decision_id
  later_observed_outcome
  review_label
  assessment_method
  reviewer_or_experiment_id
  evidence_refs
  timestamp
  append_sequence
```

Review labels:

```text
CORRECT
REASONABLE
INCORRECT
NOT_ENOUGH_EVIDENCE
```

Do not use:

```text
NOT_REVIEWED
```

Absence of a valid `DecisionAssessmentEvent` means the decision has not been reviewed.

Assessment methods:

```text
HUMAN_REVIEW
COUNTERFACTUAL_REPLAY
ALTERNATIVE_ACTION_EXPERIMENT
```

Assessment never modifies the original `DecisionEvent`.

---

# 15. Decision-quality limitations

The following cannot be inferred from one normal run:

```text
stop_precision
premature_stop_rate
owner_escalation_precision
unnecessary_owner_escalation_rate
revision_coverage
```

They require:

- human review;
- counterfactual replay;
- or an alternative-action experiment in an isolated evaluation workspace.

Do not run counterfactual actions on the production repository.

Example:

```text
selected_action = STOP
later_outcome = FAILED
```

does not by itself prove that the decision was incorrect.

---

# 16. HumanInterventionEvent

```text
HumanInterventionEvent:
  intervention_id
  intervention_schema_version
  run_id
  category
  requested_at
  responded_at
  active_seconds
  measurement_method
  required_by_decision_id
  evidence_refs
  append_sequence
```

Categories:

```text
CLARIFICATION
SCOPE_CHANGE
ACCEPTANCE_CHANGE
OPTION_SELECTION
MANUAL_CODE_EDIT
MANUAL_AGENT_RESTART
ERROR_RECOVERY
RESULT_REJECTION
```

Measurement methods:

```text
UI_MEASURED
USER_SELF_REPORTED
ESTIMATED
UNAVAILABLE
```

Do not count as intervention:

- initial task submission;
- reading the report;
- protocol-mandated final approval;
- post-task feedback;
- the full period during which TOMTIT waits for the Owner.

Separate:

```text
owner_wait_elapsed_seconds
active_human_intervention_seconds
```

Wall-clock wait is not active human effort.

---

# 17. Review effort

Separate:

```text
review_elapsed_seconds
active_review_seconds
blocking_findings_count
```

Optional fields:

```text
review_comments_count
manual_files_inspected
manual_commands_run
```

Time spent waiting for a reviewer is not active review effort.

---

# 18. Per-run facts

```text
run_state
terminal_outcome
stop_reason
partial_terminal_state_detected
invalid_transition_count
evidence_provenance_failure_count
report_generation_failure_count
recovery_count
trace_complete
required_checks_passed
criteria_satisfied
scope_violation_count
unresolved_scope_violation_count
blocking_regression_count
required_evidence_complete
attempt_count
revision_count
total_elapsed_seconds
worker_duration_seconds
verification_duration_seconds
human_intervention_count
active_human_intervention_seconds
owner_wait_elapsed_seconds
review_elapsed_seconds
active_review_seconds
```

These are run-level facts, not aggregate rates.

---

# 19. Per-decision facts

```text
selected_action
reason_code
allowed_actions
evidence_snapshot_ids
policy_version
budget_snapshot
state_before
state_after
later_observed_outcome
review_label
assessment_method
```

Later outcome and assessment fields are joined from `DecisionAssessmentEvent`, not written back into `DecisionEvent`.

---

# 20. Aggregate metric contract

Every aggregate metric must declare:

```text
metric_name
numerator
denominator
eligible_population
evaluation_window
missing_data_treatment
result
```

Allowed missing-data treatments:

```text
EXCLUDED
INCLUDED_AS_FAILURE
INCLUDED_AS_ZERO
REPORTED_SEPARATELY
```

Missing data must not be silently treated as zero or success.

When the denominator is zero:

```text
metric = UNDEFINED
```

---

# 21. Intervention metrics

## 21.1 Owner-accepted tasks

```text
human_interventions_per_owner_accepted_task =
sum(
  human_intervention_count
  for owner-basis product-success runs
)
/
count(owner-basis product-success runs)
```

Numerator and denominator use the same population.

## 21.2 Reviewer-proxy-accepted tasks

```text
human_interventions_per_reviewer_proxy_accepted_task =
sum(
  human_intervention_count
  for reviewer-proxy product-success runs
)
/
count(reviewer-proxy product-success runs)
```

Numerator and denominator use the same population.

## 21.3 Started tasks

```text
human_interventions_per_started_task =
sum(
  human_intervention_count
  for all eligible started runs
)
/
count(all eligible started runs)
```

This metric includes accepted and failed runs.

## 21.4 Zero-intervention rate

Report separately:

```text
zero_intervention_owner_accepted_task_rate
zero_intervention_reviewer_proxy_accepted_task_rate
```

---

# 22. Revision metrics

Report together:

```text
revision_success_rate
revision_waste_rate
revision_coverage
```

Revision-success numerator:

```text
eligible revisions producing objective progress
or successful completion
```

Denominator:

```text
all eligible revision decisions
```

A revision is waste when it produces none of:

- fewer failed checks;
- more satisfied criteria;
- fewer blocking findings;
- successful completion.

Revision coverage:

```text
reviewed repairable cases selected for revision
/
all reviewed repairable cases
```

Coverage requires reviewer labels or replay.

---

# 23. Escaped defects

`EscapedDefectEvent` is emitted only when a defect is found.

Schema:

```text
EscapedDefectEvent:
  event_id
  run_id
  severity
  detected_at
  evidence_refs
  append_sequence
```

Do not emit events for:

```text
NONE_OBSERVED
WINDOW_NOT_COMPLETE
```

Derived status:

## WINDOW_NOT_COMPLETE

```text
The evaluation window has not ended.
```

## FOUND

```text
At least one EscapedDefectEvent exists inside the window.
```

## NONE_OBSERVED

```text
The full evaluation window ended and no EscapedDefectEvent
exists inside the window.
```

Evaluation window:

```text
escaped_defect_window_days = 7
```

The window begins at:

- effective Owner acceptance timestamp; or
- effective reviewer-proxy approval timestamp.

Only runs with a completed seven-day window enter the denominator of `escaped_defect_rate`.

`WINDOW_NOT_COMPLETE` is censored and reported separately.

---

# 24. Subscription cost

Record separately:

```text
observed_cost_usd
estimated_api_equivalent_cost_usd
subscription_usage_available
model_execution_duration_seconds
```

When actual per-task cost is unavailable:

```text
observed_cost_usd = UNAVAILABLE
```

Do not present an API-equivalent estimate as observed cost.

---

# 25. Controlled baseline experiment

Compare:

```text
Direct Claude Code
vs.
TOMTIT + Claude Code
```

Every paired experiment must use:

- identical agent-visible task request;
- identical hidden evaluation contract;
- the same repository starting commit;
- the same model;
- the same tool permissions;
- the same test suite;
- the same time budget;
- the same evaluator contract;
- the same acceptance criteria;
- independent clean worktrees;
- no cross-run artifact visibility.

Execution order must be randomized or alternated:

```text
Pair A:
Direct first
TOMTIT second

Pair B:
TOMTIT first
Direct second
```

Where practical, the reviewer evaluates diffs without knowing which arm produced them.

The second run must not see the first run’s:

- patch;
- logs;
- reports;
- reviewer findings;
- generated artifacts.

---

# 26. Evaluation by gate

## Scenario 2

Required evidence:

```text
Attempt 1 fails
→ Coordinator proposes REVISE
→ Runtime atomically commits DecisionEvent + transition
→ reason_code is valid
→ Attempt 2 evidence is fresh
→ objective improvement occurs
→ terminal_outcome = COMPLETED
→ human_intervention_count = 0
```

This does not prove product value.

## Claude Code CLI capability spike

Measure:

```text
invocation_success
working_directory_control
exit_status_available
stdout_capture
stderr_capture
structured_output_available
session_resume_available
command_visibility
timeout_behavior
cancellation_behavior
contract_mismatch_count
```

Before `PATCH_SHA256` is activated, source-revision capability for uncommitted output remains limited.

## Adapter 3–5 tasks

Use only to:

- prove integration feasibility;
- find output-parsing problems;
- find session mismatch;
- find contract mismatch;
- test subscription-first workflow.

Do not use this sample to claim product effectiveness.

## Dogfood 15–30 tasks

Use as exploratory evidence for:

- intervention trend;
- acceptance trend;
- adapter versus contract versus decision-policy failures;
- objective revision progress;
- active review-effort reduction.

Do not use it to:

- claim statistical significance;
- train routing;
- publish precise broad-market improvement percentages;
- generalize to every task class.

---

# 27. Minimum Evaluation V1 dataset

## Runtime-owned

```text
run_state
terminal_outcome
stop_reason
attempts
evidence
execution events
decision events
terminal report
```

## Evaluation-owned append-only

```text
ReviewAssessmentEvent
OwnerAssessmentEvent
HumanInterventionEvent
DecisionAssessmentEvent
EscapedDefectEvent
```

## Derived only

```text
effective Owner assessment
effective reviewer assessment
effective acceptance result
owner review status
product success
acceptance metrics
intervention metrics
revision metrics
escaped-defect status and rate
product-comparison metrics
```

Derived values must not be written into canonical Runtime state.

---

# 28. Scope before dogfood

Do not expand Evaluation V1 before dogfood with:

- unnecessary event types;
- a learned evaluator;
- an automatic counterfactual engine;
- routing evaluation;
- statistical-significance claims;
- a generalized experiment platform;
- production analytics infrastructure.

Before dogfood, implement only what the current gate requires.

Scenario 2 minimally requires:

- Runtime-owned immutable `DecisionEvent`;
- atomic decision-and-transition commit;
- reason code;
- evidence snapshot references;
- fresh Attempt 2 evidence;
- objective improvement proof.

---

# 29. Core invariants

```text
WAITING_OWNER is a state, not an outcome.

Runtime owns execution truth.

Coordinator proposes; Runtime validates and commits.

DecisionEvent and state transition share one bounded commit boundary.

Evaluation appends assessments and cannot rewrite execution truth.

append_sequence is assigned only by the owning append store.

Sequence values are never compared across different streams.

Terminal reports have Runtime-assigned immutable identity and source revision.

Assessments bind to exact report bytes and an active exact source revision.

Owner assessment overrides reviewer proxy for the same target.

Product success requires completed execution plus explicit accepted quality.

Accepted-task metrics use accepted runs in both numerator and denominator.

Escaped-defect absence is derived, not emitted as an event.

Missing data is never silently treated as success.

Documents are not modified after their approved SHA-256 is recorded.
```

## Document status

```text
TOMTIT_COORDINATOR_EVALUATION_CONTRACT_V1=
FINAL_CANDIDATE_FOR_OWNER_APPROVAL
```

Owner approval is recorded only in external gate evidence.

The document bytes must not be modified after their SHA-256 is approved.

Any later modification creates a new document candidate requiring a new hash and new Owner approval.
