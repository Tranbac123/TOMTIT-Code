# Claude Gate 2 — Minimal Scenario 2 Implementation

## STATUS

```text
READY_FOR_CODEX_SOL_GATE_2_REVIEW
```

## STARTING_HEAD

```text
e12411fc1a38b220eefec51adbaf30b4c28e737c
```

## SOURCE_COMMIT_HASH

```text
d597aeb78aebabe70e792c750055d3f3606e2905
```

## IMPLEMENTED_DIFF_RANGE

```text
e12411fc1a38b220eefec51adbaf30b4c28e737c..d597aeb78aebabe70e792c750055d3f3606e2905
```

## REPORT_COMMIT_EXPECTED_PARENT

```text
d597aeb78aebabe70e792c750055d3f3606e2905
```

## MODEL_USED

Claude Code Opus (`claude-opus-5`).

## MODEL_REASON

The task introduces a Runtime-owned DecisionEvent with its own append-sequence
authority, an atomic decision-plus-transition commit boundary with rollback,
attempt progression with archived per-attempt evidence, evidence-freshness
binding across attempts, and a machine-checkable objective-improvement
invariant. These cross canonical state, authority, persistence, and the frozen
Gate 1 completion path simultaneously. No downgrade occurred.

## FILES_CHANGED

### `src/tomtit/runtime/state.py`

| Item | Change |
|---|---|
| `TransitionReason.REVISION_APPROVED` | New closed-vocabulary reason for `CHECKING → REVISING`. |
| `ObservedCheckResult.snapshot_id` / `ObservedPatchEvidence.snapshot_id` | Stable evidence identity `check:<run>:<attempt>:<check_id>` and `patch:<run>:<attempt>`. Attempt-scoped by construction, so two attempts can never share an ID. |
| `AttemptEvidence` (new) | Immutable archived evidence for one finished attempt, with `failed_check_ids` and `satisfied_criterion_ids` derived from observed results only. Validates that archived observations belong to the archived attempt. |
| `attempt_evidence_from_observations()` (new) | Derives an `AttemptEvidence` from observed checks, patch, and criterion evaluations. Worker claims are not an input. |
| `objective_improvement_error()` (new) | Single definition of objective improvement: later attempt number, no reused evidence IDs, previous attempt had a failed check, current attempt has none, and strictly more satisfied criteria. |
| `task_contract_hash()` (new) | SHA-256 over the contract's canonical JSON with sorted keys and stable separators. |
| `RunRecord.attempt_history` (new field) | Archived attempts; validated unique, strictly increasing, and strictly earlier than the current attempt. |
| `RunRecord.validate_runtime_snapshot` | The COMPLETED branch now additionally requires objective improvement **when `attempt_history` is non-empty**. A run with no earlier attempt is unaffected, so Gate 1 behavior is unchanged. |

### `src/tomtit/domain/evaluation.py`

| Item | Change |
|---|---|
| `DecisionReasonCode` (new) | Exactly one code: `DETERMINISTIC_REPAIRABLE_CHECK_FAILURE`. No taxonomy, no scoring. |
| `BudgetSnapshot` (new) | Attempt and time budget observed at proposal time. |
| `DecisionEventDraft` (new) | The Coordinator-side proposal. Carries no `append_sequence`, no `decision_id`, no `decision_timestamp`. `DomainModel` uses `extra="forbid"`, so a caller supplying any of them is rejected by validation rather than by convention. Validates action membership, uniqueness, budget/attempt agreement, and that the decision actually changes state. |
| `PersistedDecisionEvent` (new) | Subclass adding the three Runtime-owned fields. Immutable (frozen). |

New enums live here rather than in `domain/enums.py` because `enums.py` is
outside this task's allowed-file list. This is a deliberate, documented
deviation from the repository's usual enum location.

### `src/tomtit/runtime/events.py`

| Item | Change |
|---|---|
| `PreparedDecision` (new) | A validated, already serialized decision not yet persisted. |
| `prepare_decision()` (new) | Assigns `decision_id`, `decision_timestamp`, and `append_sequence`, then validates and serializes. Sequence is `len(stream) + 1`, assigned only here. |
| `_append_prepared_decision()` (new) | Persists one prepared decision. No validation, no serialization. |
| `_decision_checkpoint()` / `_restore_decisions_to_checkpoint()` (new) | Rollback support for the decision stream. |
| `decisions` property (new) | Detached reads reconstructed from stored bytes. |

The decision stream is separate from the execution-event journal; their
sequences are never compared.

### `src/tomtit/runtime/runtime.py`

| Item | Change |
|---|---|
| `_ALLOWED_TRANSITIONS` | Adds `CHECKING → REVISING` and `REVISING → IMPLEMENTING`. No other edge added. |
| `_EXPECTED_TRANSITION_REASONS` / `_TRANSITION_EVENT_TYPES` | Adds the two edges and maps `REVISING` to the existing `EvaluationEventType.REVISION_REQUESTED`. |
| `RevisionNotAllowed` (new) | Raised when a REVISE proposal cannot be accepted. |
| `build_revision_proposal()` (new) | Builds a `DecisionEventDraft` from current observed evidence. Appends nothing, mutates nothing. |
| `commit_revision_decision()` (new) | Validates the proposal, then commits decision and transition together. |
| `_prepare_revision()` (new) | Reconstructs the untrusted draft through full validation, then checks state, run, attempt, contract hash, attempt budget, at-least-one-failed-check, and exact evidence-set equality. Builds the candidate record, the prepared decision, and the prepared transition event. Nothing canonical is touched. |
| `_commit_revision()` (new) | Assigns the record, appends the decision, appends the transition event; on any failure restores the record and both stream checkpoints and re-raises. |
| `_execute_attempt()` (new) | Extracted attempt body used by both scenarios; always collects evidence for the **canonical** attempt number. |
| `_attempt_satisfies_completion()` / `_decide_completion()` (new) | Extracted from `run_scenario_1`; behavior identical. |
| `run_scenario_2()` (new) | The bounded flow: Attempt 1 failure → proposal → atomic commit → Attempt 2 → improvement check → frozen completion path. |
| `_validate_transition_preconditions` | Adds `REVISING → IMPLEMENTING` preconditions: attempt advanced, budget not exceeded, previous attempt archived, and no stale evidence carried forward. |

`run_scenario_1`, `complete_run`, `_prepare_completion`, and
`_commit_completion` are behaviorally unchanged.

### `src/tomtit/fakes.py`

`FakeCheckRunner.attempt_outcomes: dict[tuple[int, str], FakeCheckOutcome]`
allows a check to fail on Attempt 1 and pass on Attempt 2. Consulted before the
existing per-check `outcomes`, so existing behavior is unchanged when unset.

### Tests

`tests/scenarios/test_scenario_2.py` (new, 34 tests) covers 14.1–14.9.
`tests/domain/test_evaluation_models.py` (+11) and
`tests/domain/test_schema_generation.py` cover the new models.

`src/tomtit/reporting.py` and `tests/scenarios/test_scenario_1.py` were **not**
modified; `src/tomtit/runtime/__init__.py` needed no change because tests import
the new types from their defining modules.

## COMMANDS_AND_EXIT_CODES

| Command | Exit code | Result |
|---|---:|---|
| `git status --short` (initial) | 0 | empty |
| `git rev-parse HEAD` (initial) | 0 | `e12411fc1a38b220eefec51adbaf30b4c28e737c` |
| `git rev-parse HEAD^` (initial) | 0 | `4ae5fe35e332a77a31d76b1f2ea936e5478b9ba4` |
| `python3 -m pytest --collect-only -q` | 0 | `43 + 40 + 3 + 99 + 34 = 219` collected |
| `python3 -m pytest -q` | 0 | 219 passed (219 `PASSED` records under `-rA`) |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed |
| `python3 -m pytest tests/scenarios/test_scenario_2.py -q` | 0 | 34 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 40 passed |
| `python3 -m pytest tests/domain/test_contracts.py -q` | 0 | 43 passed |
| `python3 -m pytest tests/domain/test_schema_generation.py -q` | 0 | 3 passed |
| `python3 -m compileall -q src tests` | 0 | passed |
| `ruff check .` | 0 | no findings |
| `git diff --check` | 0 | no whitespace errors |
| `git diff --cached --check` (pre-commit) | 0 | no whitespace errors |
| `git diff --cached --stat` (pre-commit) | 0 | 8 files, 1518 insertions, 21 deletions |
| `python3 -m mypy src tests` | 1 | `Found 96 errors in 5 files`; **0 under `src/**`** |

Totals moved 174 → 219 collected and passing (+45). Mypy moved 83 → 96, entirely
in test files and entirely in the repository's accepted list-versus-tuple and
deliberate-invalid-input style; `src/**` remains at zero. SOL-RL04 was not
touched.

## PRODUCT_EVIDENCE

```text
The Runtime can perform one bounded revision from deterministic
failure to successful completion without owner input in the
controlled fake loop.
```

Observed from `run_scenario_2()`:

```text
state=COMPLETED
outcome=COMPLETED
stop_reason=ACCEPTANCE_SATISFIED
attempt_count=2
attempt_history=[attempt 1: failed_check_ids=('unit-tests',)]
attempt 2 observed exit_code=0, criterion tests-pass=SATISFIED
persisted REVISE decisions=1 (append_sequence=1)
REVISION_REQUESTED events=1
RUN_COMPLETED=1, REPORT_GENERATED=1, report.v1.json present
owner/clarification events=0
```

Event order is deterministic and timestamps are monotonic:

```text
TASK_SUBMITTED, CONTRACT_INFERRED, RUN_STARTED, WORKSPACE_PREPARED,
WORKER_ASSIGNED, WORKER_STARTED, WORKER_FINISHED, EVIDENCE_COLLECTED,
CHECK_FAILED, REVISION_REQUESTED, WORKER_ASSIGNED, WORKER_STARTED,
WORKER_FINISHED, EVIDENCE_COLLECTED, CHECK_PASSED, RUN_COMPLETED,
REPORT_GENERATED
```

This proves the controlled fake loop only. It does not prove any reduction in
real-world user coordination.

## DECISION_AUTHORITY_EVIDENCE

- `build_revision_proposal()` returns a `DecisionEventDraft` and leaves the
  record object identity and the full event tuple unchanged
  (`test_building_a_proposal_mutates_nothing`).
- The draft type has no `append_sequence`, `decision_id`, or
  `decision_timestamp` field at all
  (`test_proposal_carries_no_runtime_owned_identity_or_sequence`).
- Supplying any of those three to the draft raises `ValidationError` via
  `extra="forbid"` (parametrized, in both the scenario and domain suites).
- Only `InMemoryEventLog.prepare_decision` assigns `append_sequence`, and only
  `Runtime._commit_revision` persists it
  (`test_only_runtime_assigns_append_sequence`).
- Worker output never reaches the decision: the proposal's evidence set is
  rebuilt from observed results and must match exactly.

## ATOMICITY_EVIDENCE

| Injection | Result | Test |
|---|---|---|
| Decision append raises | no decision, no transition, no attempt increment, Attempt 1 canonical | `test_decision_append_failure_commits_nothing` |
| Transition-event append raises | no decision persisted, no transition | `test_transition_event_failure_commits_no_decision` |
| Transition preparation (`_build_record`) raises | nothing prepared, nothing committed | `test_transition_preparation_failure_commits_nothing` |
| Any of the above | earlier nonterminal event history byte-identical | `test_earlier_history_survives_a_failed_revision` |
| Retry after failure | exactly one decision, `append_sequence == 1`, exactly one `REVISION_REQUESTED` | `test_retry_after_failed_revision_commits_exactly_once` |

`append_sequence` is unique and strictly increasing
(`test_append_sequence_is_unique_and_increasing`).

## ATTEMPT_AND_EVIDENCE_FRESHNESS

- Attempt 2 evidence IDs are disjoint from Attempt 1's: `check:run-s2:1:...` and
  `patch:run-s2:1` versus `check:run-s2:2:...` and `patch:run-s2:2`.
- Attempt 1 evidence is archived in `attempt_history`, stays frozen, and still
  records `exit_code == 1` after the run completes
  (`test_attempt_1_evidence_stays_immutable_and_auditable`).
- The revision commit clears `observed_checks`, `observed_patch`,
  `criterion_evaluations`, and `worker_result`, and `REVISING → IMPLEMENTING`
  refuses to proceed if any stale evidence remains.
- A runner that stamps Attempt 1 provenance onto Attempt 2 results is rejected
  at `COLLECTING_EVIDENCE → CHECKING`
  (`test_stale_attempt_1_evidence_cannot_be_presented_as_attempt_2_evidence`).
- Proposals are rejected for a foreign `run_id`, the wrong attempt number, a
  wrong contract hash, a wrong `state_before`/`state_after`, missing evidence
  IDs, and Attempt 2 evidence used to justify the Attempt 1 decision.

## OBJECTIVE_IMPROVEMENT_EVIDENCE

Machine-checkable from observed evidence only:

```text
attempt_1_failed_required_check_count = 1  (>= 1)
attempt_2_failed_required_check_count = 0
attempt_2_satisfied_criteria_count (1) > attempt_1_satisfied_criteria_count (0)
evidence ID overlap = {}
```

Enforced in two places that share one definition
(`objective_improvement_error`): the Scenario 2 driver before completion, and
the canonical `RunRecord` COMPLETED invariant — so the report boundary, which
revalidates, enforces it too. A completed record whose archived attempt claims
the same satisfied criteria is rejected with `did not increase satisfied
criteria` (`test_completed_record_rejects_a_revision_without_improvement`); a
record reusing evidence across attempts is also rejected. Attempt 2 still
failing yields `CompletionConditionsNotMet` with no report
(`test_completion_is_rejected_when_attempt_2_still_fails`).

## NEGATIVE_CONTROLS

Run in throwaway copies; the tracked working tree was never modified.

| Control | Restored defect | Failing tests |
|---|---|---|
| 1 | State transition without DecisionEvent append | 6, incl. `test_scenario_2_revises_once_and_completes`, `test_decision_append_failure_commits_nothing`, `test_retry_after_failed_revision_commits_exactly_once` |
| 2 | DecisionEvent append without state transition | 12, incl. the happy path, `test_only_runtime_assigns_append_sequence`, `test_attempt_three_is_never_created` |
| 3 | Caller-controlled `append_sequence` on the draft | 10+, incl. `test_caller_supplied_runtime_owned_fields_are_rejected[append_sequence]`, `test_proposal_carries_no_runtime_owned_identity_or_sequence` |
| 4 | Attempt 2 reusing Attempt 1 evidence | 9, incl. `test_attempt_1_evidence_stays_immutable_and_auditable`, the happy path |
| 5 | Completion without objective improvement (both guards removed) | `test_completed_record_rejects_a_revision_without_improvement` |
| 6 | Creation of Attempt 3 | `test_second_revision_is_rejected_when_the_budget_is_exhausted`, `test_attempt_three_is_never_created` |

## CLAIMED_FIXED_PENDING_REVIEW

```text
SCENARIO_2=CLAIMED_IMPLEMENTED_PENDING_REVIEW
DECISION_EVENT_ATOMICITY=CLAIMED_IMPLEMENTED_PENDING_REVIEW
ATTEMPT_2_EVIDENCE_FRESHNESS=CLAIMED_IMPLEMENTED_PENDING_REVIEW
OBJECTIVE_IMPROVEMENT=CLAIMED_IMPLEMENTED_PENDING_REVIEW
ZERO_OWNER_INTERVENTION=CLAIMED_IMPLEMENTED_PENDING_REVIEW
```

## RISKS

- **New enums outside `domain/enums.py`.** `DecisionReasonCode` lives in
  `domain/evaluation.py` because `enums.py` is outside the allowed-file list.
  This diverges from the repository's convention and may warrant relocation in a
  later authorized task.
- **Zero owner intervention is asserted, not modelled.** There is no
  `HumanInterventionEvent` and no counter field. The scenario asserts the
  absence of owner/clarification events and `owner_input_required is False`, per
  the task's allowance for a bounded assertion.
- **Decision-stream sequence is per-log, in-memory.** It is unique and strictly
  increasing within one `InMemoryEventLog` instance only. There is no
  persistence, no crash recovery, and no cross-process ordering.
- **Atomicity is bounded and in-process**, matching Gate 1. A process crash
  between the decision append and the transition-event append is outside the
  supported model.
- **`task_contract_hash` depends on Pydantic's JSON encoding** of the contract.
  It is stable within a pinned Pydantic version but is not a cross-version or
  cross-language canonicalization.
- **Deferred and untouched:** `SOL-RL04`, `BASELINE-L01`, `AUDIT-L02`, `F-005`,
  symlink confinement, artifact hashing, workspace path confinement, and the
  Evaluation V1 report-identity fields (`report_id`, `source_revision`), which
  would require the frozen `reporting.py`.

## SCOPE_AND_NON_GOALS

Changed exactly: `src/tomtit/domain/evaluation.py`, `src/tomtit/runtime/events.py`,
`src/tomtit/runtime/runtime.py`, `src/tomtit/runtime/state.py`,
`src/tomtit/fakes.py`, `tests/domain/test_evaluation_models.py`,
`tests/domain/test_schema_generation.py`, and the new
`tests/scenarios/test_scenario_2.py`.

Confirmed **unchanged**: `src/tomtit/reporting.py`,
`tests/scenarios/test_scenario_1.py`, `docs/**` (including
`docs/gate-evidence/GATE_1_SCENARIO_1.md`), every existing audit report,
`CLAUDE.md`, `SYSTEM.md`, `README.md`, `pyproject.toml`, `.gitignore`,
`src/tomtit/domain/contracts.py`, `src/tomtit/domain/enums.py`, and
`src/tomtit/runtime/__init__.py`. The Gate 1 completion commit point, report
publication, rollback behavior, and terminal chronology are untouched. No
dependency was added.

Not implemented: Attempt 3, `WAITING_OWNER`, `NO_PROGRESS` execution, repeated
revision, repeated-finding engine, progress scoring, LLM coordinator, model
routing, real adapter, subprocess execution, product CLI, database persistence,
concurrency or leases, assessment-event store, product metrics, memory, graph
orchestration, self-improvement. `_ALLOWED_TRANSITIONS` gained exactly two
edges.

## UNPROVEN_ITEMS

- Real-world coordination reduction is **not proven**; this is a fake loop.
- Crash, power-loss, and durability behavior for the decision stream is **not
  proven**.
- Concurrent or parallel revision of the same run is **not proven**.
- `task_contract_hash` stability across Pydantic versions is **not proven**.
- Exhaustiveness of the atomicity failure matrix is **not proven**; it covers the
  enumerated injection points.
- Repeated revision beyond one bounded cycle is **not implemented** and therefore
  not proven safe.
- Whether Gate 2 product evidence is satisfied is **claimed, not
  self-verified**; that is the reviewer's determination.

## NEXT_GATE

```text
CODEX_SOL_GATE_2_REVIEW
NO_NEXT_GATE_AUTHORIZED
```
