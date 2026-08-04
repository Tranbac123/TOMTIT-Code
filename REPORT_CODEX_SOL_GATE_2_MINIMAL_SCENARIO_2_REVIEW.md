# Independent Correctness Review — Gate 2 Minimal Scenario 2

Reviewer: Codex GPT-5.6 Sol High

Review mode: `INDEPENDENT_CORRECTNESS`

Risk class: `HIGH_CANONICAL_STATE_AND_ATOMICITY`

## VERDICT

```text
REQUEST_CHANGES
```

The intended `run_scenario_2()` fake-loop path completes two attempts without
owner input, and the dedicated revision commit correctly rolls back every
injected preparation/append failure. Gate 2 is nevertheless not approvable:

1. `Runtime.transition()` publicly accepts `CHECKING -> REVISING` and emits
   `REVISION_REQUESTED` without a `DecisionEvent`, without archiving Attempt 1,
   and without advancing to Attempt 2. This bypasses the required atomic
   Runtime-owned decision-and-transition operation.
2. `_prepare_revision()` accepts and persists caller-controlled
   `policy_version`, `allowed_actions`, `binding_constraints`, and budget values
   that contradict canonical policy and the current `TaskContract`.
3. the completed-record trust boundary accepts archived Attempt 1 evidence
   belonging to another run;
4. the branch requesting Attempt 2 does not produce the policy-required
   repairable `StopDecision` and does not materialize the required one
   repairable `Finding`; and
5. `task_contract_hash()` does not include an explicit hash/contract schema
   version in its hashed representation.

Findings 1–4 are current-gate HIGH/MEDIUM correctness defects. Finding 5 is a
formally classified LOW bounded portability deviation in the pinned,
single-process fake loop.

## TARGET_SOURCE_COMMIT

```text
d597aeb78aebabe70e792c750055d3f3606e2905
```

## REVIEWED_DIFF_RANGE

```text
e12411fc1a38b220eefec51adbaf30b4c28e737c..d597aeb78aebabe70e792c750055d3f3606e2905
```

## REVIEW_REPORT_EXPECTED_PARENT

```text
1de35a4c4e33ab892afa2843c6501983c2588ee0
```

Preflight established `main`, clean tracked worktree and index, no untracked
project files, `HEAD` equal to the expected parent, `HEAD^` equal to the target
source commit, and direct ancestry from the source commit to the implementation
report commit.

## FILES_REVIEWED

Required governance and prior evidence:

- `CLAUDE.md`
- `SYSTEM.md`
- `docs/OFFICIAL_BUILD_AND_AI_CODING_PROCESS_V1.md`
- `docs/EVALUATION_CONTRACT_V1.md`
- `docs/VERIFIER_POLICY_V1.md`
- `docs/MVP_EXECUTION_POLICY_V0.md`
- `docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md`
- `docs/gate-evidence/GATE_1_SCENARIO_1.md`
- `REPORT_CLAUDE_GATE_2_MINIMAL_SCENARIO_2.md`
- `REPORT_CODEX_TERRA_GOVERNANCE_PERSISTENCE_REVIEW.md`
- `REPORT_CODEX_SOL_FINAL_CLEANUP_RETRY_REREVIEW.md`

Exact source-diff files:

- `src/tomtit/domain/evaluation.py`
- `src/tomtit/fakes.py`
- `src/tomtit/runtime/events.py`
- `src/tomtit/runtime/runtime.py`
- `src/tomtit/runtime/state.py`
- `tests/domain/test_evaluation_models.py`
- `tests/domain/test_schema_generation.py`
- `tests/scenarios/test_scenario_2.py`

The source commit changes exactly these eight files: 1,518 insertions and 21
deletions. `src/tomtit/reporting.py`, Scenario 1 tests, governance, contracts,
enums, and `runtime/__init__.py` are unchanged in the reviewed range.

## COMMANDS_AND_EXIT_CODES

| Command | Exit | Result |
|---|---:|---|
| `git status --short` | 0 | Empty at preflight |
| `git status --porcelain=v1 --untracked-files=all` | 0 | Empty at preflight |
| `git status --ignored --short` | 0 | Ignored `.DS_Store`, caches and bytecode only |
| `git branch --show-current` | 0 | `main` |
| `git rev-parse HEAD` | 0 | Expected report parent |
| `git rev-parse HEAD^` | 0 | Target source commit |
| `git merge-base --is-ancestor d597aeb... 1de35a4...` | 0 | Direct lineage confirmed |
| `git log --oneline --decorate -18` | 0 | Inspected |
| `git show --stat --oneline d597aeb...` | 0 | Eight expected source/test files |
| `git show --stat --oneline 1de35a4...` | 0 | Implementation report only |
| `python3 -m pytest --collect-only -q` | 0 | 219 collected |
| `python3 -m pytest -q` | 0 | 219 passed |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed |
| `python3 -m pytest tests/scenarios/test_scenario_2.py -q` | 0 | 34 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 40 passed |
| `python3 -m pytest tests/domain/test_contracts.py -q` | 0 | 43 passed |
| `python3 -m pytest tests/domain/test_schema_generation.py -q` | 0 | 3 passed |
| `python3 -m compileall -q src tests` | 0 | Passed |
| `ruff check .` | 0 | No findings |
| `git diff --check` | 0 | No whitespace errors |
| `python3 -m mypy src tests` | 1 | 96 diagnostics in five test files |
| `python3 -m mypy src` | 0 | No source diagnostics in 11 source files |
| `git diff --stat e12411f.....d597aeb...` | 0 | Eight files, 1,518 insertions, 21 deletions |
| `git diff --check e12411f.....d597aeb...` | 0 | No whitespace errors |
| isolated A–F Python fault-injection harness | 0 | All rollback/retry assertions passed |
| isolated authority/metadata reproduction harness | 0 | Reproduced all reported invalid states |
| `PYTHONPATH=src python3 -m pytest /private/tmp/tomtit-g2-review/test_gate2_negative_controls.py -q` | 1 | Expected: all ten deliberately invalid controls failed their invariant assertion |

The 96 Mypy diagnostics equal the declared baseline. They are confined to tests
and consist of accepted Pydantic list-to-tuple construction, deliberate invalid
inputs, and test introspection. No new source diagnostic was found.

## INDEPENDENT_REPRODUCTIONS

### R1 — Intended bounded Scenario 2 path

An independent `run_scenario_2()` execution produced:

```text
state=COMPLETED
outcome=COMPLETED
stop_reason=ACCEPTANCE_SATISFIED
attempt_count=2
archived Attempt 1 failed_check_ids=(unit-tests,)
current Attempt 2 unit-tests exit_code=0
persisted REVISE decisions=1
decision append_sequence=1
REVISION_REQUESTED events=1
RUN_COMPLETED events=1
REPORT_GENERATED events=1
owner/clarification events=0
```

### R2 — Public atomicity bypass (HIGH)

From canonical Attempt 1 `CHECKING`, this supported public call succeeded:

```python
runtime.transition(RunState.REVISING, TransitionReason.REVISION_APPROVED)
```

Observed invalid state:

```text
state=REVISING
attempt_count=1
attempt_history=0
persisted decisions=0
new REVISION_REQUESTED events=1
```

The call path is allowed by `_ALLOWED_TRANSITIONS`, has no matching precondition
in `_validate_transition_preconditions()`, and uses the ordinary record/event
mutation path rather than `commit_revision_decision()`.

### R3 — Canonically inconsistent proposal metadata accepted (MEDIUM)

Four separately reconstructed and schema-valid proposals were accepted and
persisted while the real contract remained `max_attempts=2`, hard max 3, time
budget 120, and policy `mvp-execution-policy-v0.3`:

```text
policy_version="attacker-policy-v999"                    ACCEPTED
allowed_actions=(REVISE, COMPLETE)                       ACCEPTED
binding_constraints=("max_attempts=999",)               ACCEPTED
budget_snapshot=(attempt=1,max=3,hard=3,time=999)        ACCEPTED
```

Every accepted proposal advanced the record to `REVISING`, Attempt 2 and
persisted the false metadata. `_prepare_revision()` checks selected action,
state, run, attempt, contract hash, real max-attempt budget and exact evidence
IDs, but does not compare these four fields with canonical Runtime values.

### R4 — Foreign-run archived evidence accepted at completion (MEDIUM)

Attempt 1 check and patch evidence in a valid completed record were rebuilt with
`run_id="foreign-run"`, retaining the correct attempt number and artifact paths.
Both of these public trust boundaries accepted it:

```text
RunRecord.model_validate(...) = ACCEPTED
validate_completed_record(...) = ACCEPTED
resulting state = COMPLETED
```

`AttemptEvidence` validates attempt number but not run identity. Completed-state
validation checks current evidence against `RunRecord.run_id`, but not archived
attempt evidence.

### R5 — Revision repairability record absent (MEDIUM)

Immediately after the accepted revision:

```text
state=REVISING
attempt_count=2
archived failed_check_ids=(unit-tests,)
decision reason=DETERMINISTIC_REPAIRABLE_CHECK_FAILURE
stop_decision=null
unresolved_findings=0
```

The reason code implies repairability, but it is not the approved
`StopDecision` contract. `MVP_EXECUTION_POLICY_V0` requires every branch that
requests another attempt to produce `next_action=REVISE`, `repairable=true`,
`owner_input_required=false`, null outcome and null stop reason. The Gate 2
sequence also requires one repairable finding; the implementation records a
failed check ID but no canonical `Finding`.

## NEGATIVE_CONTROLS

The controls ran as a standalone pytest module under `/private/tmp`; the review
tree was not modified. Each test deliberately created or asserted an invalid
state and therefore had to fail. Exit code was 1 with exactly 10 failures:

1. `test_nc01_state_attempt_transition_without_decision_is_detected` — failed
   against the real public API; observed `REVISING`, Attempt 1, zero decisions.
   This is finding R2, not merely an artificial mutation.
2. `test_nc02_decision_append_without_canonical_transition_is_detected` — a
   privately seeded decision left state `CHECKING`, Attempt 1; the paired-state
   assertion failed.
3. `test_nc03_retained_decision_after_transition_failure_is_detected` — disabling
   decision restoration while injecting a partial event append retained one
   decision; the no-decision assertion failed.
4. `test_nc04_caller_controlled_append_sequence_is_detected` — a privately
   forged persisted value retained caller sequence 77; the store-sequence
   assertion failed. The supported draft path correctly forbids this.
5. `test_nc05_attempt_2_reusing_attempt_1_evidence_is_detected` — identical
   Attempt 1/2 evidence sets failed the disjointness assertion.
6. `test_nc06_completion_without_criteria_improvement_is_detected` — the
   objective predicate returned `did not increase satisfied criteria (1 -> 1)`.
7. `test_nc07_completion_with_overlapping_evidence_ids_is_detected` — the
   objective predicate reported both reused Attempt 1 snapshot IDs.
8. `test_nc08_attempt_3_creation_is_detected` — an unsafe copied snapshot with
   `attempt_count=3` failed `attempt_count <= max_attempts` (`3 <= 2`). The
   supported second-revision API separately rejects budget exhaustion.
9. `test_nc09_copied_invalid_completed_record_is_detected` —
   `validate_completed_record()` revalidated the copy and raised for no criteria
   improvement.
10. `test_nc10_contract_hash_semantic_change_is_detected` — changing the goal
    produced a different SHA-256, so the deliberately equal-hash assertion
    failed.

Controls 2–10 simulate the forbidden condition in subprocess-local state to
show the relevant invariant assertion is load-bearing. They do not assert that
private-field writes are supported APIs. The existing suite additionally
passes focused tests for draft-owned-field rejection, stale Attempt 1
provenance, budget exhaustion, invalid state, no improvement and copied-record
revalidation. It lacks controls for R2, R3, R4 and R5.

## DECISION_AUTHORITY_RESULTS

`DecisionEventDraft` is strict and immutable, excludes identity/timestamp/
sequence, and rejects caller-supplied Runtime fields. Proposal construction is
non-mutating. The event log owns persisted decision identity and sequence, and
decision/execution streams remain separate.

Authority is nevertheless `OPEN` because the public transition API bypasses the
decision writer and because Runtime persists caller metadata without validating
it against allowed actions, policy, budget, or binding constraints. Direct
construction of `PersistedDecisionEvent` does not enter the supported stream
without private access, so that narrower concern is resolved.

## ATOMICITY_RESULTS

The dedicated `_prepare_revision()` / `_commit_revision()` boundary passed all
independent injections:

| Injection | Failure result | Retry result |
|---|---|---|
| A. decision preparation | Exact rollback to `CHECKING`, Attempt 1; no decision/event | One decision, sequence 1, one revision event, Attempt 2 |
| B. canonical record preparation | Exact rollback | Same single-commit result |
| C. decision append | Exact rollback | Same single-commit result |
| D. transition-event append after decision | Both streams and record restored | Same single-commit result |
| E. partial transition-event append then raise | Partial event, decision and record all restored to checkpoints | Same single-commit result |
| F. retry after every failure | No duplicate persisted value | Exactly one decision and transition |

Earlier event history and Attempt 1 evidence remained byte-for-byte/equivalent
at every failed boundary. However, the system-wide atomic invariant is `OPEN`
because R2 reaches the same canonical `REVISING` transition outside this sound
commit boundary.

## APPEND_SEQUENCE_RESULTS

The supported proposal cannot supply or override `append_sequence`.
`InMemoryEventLog.prepare_decision()` assigns `len(decision_stream)+1`; persisted
values are immutable and detached. Rollback removes an uncommitted sequence,
and all five retries persisted exactly sequence 1 with no duplicate. Decision
and execution stream positions are never compared.

`len(stream)+1` is valid for this bounded, single-process, append-only in-memory
scope. Persistence and concurrency are correctly outside Gate 2.

## ATTEMPT_PROGRESSION_RESULTS

The intended path archives immutable Attempt 1 evidence, increments exactly to
Attempt 2, clears current evidence, and rejects a second revision at the
`max_attempts=2` budget. Failed dedicated commits do not increment; retries do
not skip; the current implementation does not create Attempt 3 through the
supported revision API.

Status remains `OPEN`: R2 creates an internally inconsistent `REVISING` state
that is still Attempt 1 with no history. This violates canonical attempt
progression even though the following `REVISING -> IMPLEMENTING` precondition
then prevents execution.

## EVIDENCE_FRESHNESS_RESULTS

Current-attempt observations are deterministically identified as
`check:{run}:{attempt}:{check}` and `patch:{run}:{attempt}`. The supported loop
clears stale evidence, collects fresh Attempt 2 observations, rejects stale
attempt provenance, requires the exact proposal evidence set, and keeps
Attempt 1 evidence immutable and disjoint.

Status is `OPEN` because R4 proves archived evidence from another run is
accepted by both completed-record trust boundaries. The IDs are provenance
identifiers, not content hashes; collision resistance beyond the bounded run /
attempt / check namespace is not claimed or required here.

## OBJECTIVE_IMPROVEMENT_RESULTS

The predicate uses observed evidence and enforces all four required conditions:
previous failed checks nonempty, current failed checks empty, satisfied criteria
strictly increase, and evidence sets are disjoint. It is called before Scenario
2 completion and again during completed `RunRecord` validation. Attempt 2 still
failing, no prior failure, no criteria increase, overlap, and copied-record
bypass all fail closed.

Objective improvement is `RESOLVED`. R4 is separately an evidence run-binding
defect: the numerical/disjoint predicate itself still operates correctly.

## TASK_CONTRACT_HASH_RESULTS

`task_contract_hash()` hashes compact UTF-8 JSON with sorted object keys,
deterministic Pydantic scalar serialization, stable separators and no runtime
object identity. Equivalent validated contracts in the current model hash
equally; semantic goal change produces a different hash.

It does **not** include an explicit contract/hash schema version in the hashed
representation. This directly misses an approved Evaluation Contract clause.
Current Gate 2 decision binding remains unambiguous because both proposal and
validation use the same pinned function and in-process `TaskContract`; it is
therefore `BOUNDED_DEVIATION` / LOW, not the cause of `REQUEST_CHANGES`.

## ZERO_OWNER_INTERVENTION_RESULTS

The intended fake loop never enters `WAITING_OWNER`, emits zero owner or
clarification event types, and completes with
`stop_decision.owner_input_required=false`. This bounded claim is `RESOLVED`.
No claim is made about real user effort.

## GATE_1_PRESERVATION_RESULTS

Scenario 1 source and tests are unchanged in the reviewed range. All 99 focused
tests pass, including completion, rollback, report publication, chronology and
write-once behavior. The independent single-attempt run completes with empty
attempt history and zero decision/revision records. `GATE_1_PRESERVATION` is
`PRESERVED`.

## FINDING_STATUS

```text
SCENARIO_2=PROVEN
DECISION_AUTHORITY=OPEN
DECISION_EVENT_ATOMICITY=OPEN
APPEND_SEQUENCE_AUTHORITY=RESOLVED
ATTEMPT_PROGRESSION=OPEN
EVIDENCE_FRESHNESS=OPEN
OBJECTIVE_IMPROVEMENT=RESOLVED
ZERO_OWNER_INTERVENTION=RESOLVED
GATE_1_PRESERVATION=PRESERVED
TASK_CONTRACT_HASH=BOUNDED_DEVIATION
DECISION_REASON_CODE_LOCATION=DEFERRED_LOW
TEST_ONLY_MYPY=DEFERRED_LOW
PERSISTENCE_AND_CRASH_RECOVERY=OUT_OF_SCOPE
CONCURRENCY=OUT_OF_SCOPE
REAL_WORLD_COORDINATION_VALUE=NOT_PROVEN
```

`SCENARIO_2=PROVEN` denotes only the narrow product statement: the intended
controlled fake-loop method can execute failure, one revision, fresh success
and completion. It does not override the open correctness findings.

## PRODUCT_EVIDENCE_ASSESSMENT

```text
The Runtime can perform one bounded revision from deterministic
failure to successful completion without owner input in the
controlled fake loop.

PROVEN
```

The exact intended run was independently reproduced. Gate approval still fails
because other supported/public and trust-boundary paths violate the canonical
contract.

## RISKS

| ID | Severity | Risk |
|---|---|---|
| G2-S2-01 | HIGH | Public transition bypass produces `REVISING` plus transition event with no decision and invalid Attempt 1 lifecycle |
| G2-S2-02 | MEDIUM | Persisted decision metadata can falsely state policy, allowed actions, binding constraints and budget |
| G2-S2-03 | MEDIUM | A copied completed record can canonically retain archived evidence from a foreign run |
| G2-S2-04 | MEDIUM | Revision branch omits the required repairable `StopDecision` and canonical repairable finding |
| G2-S2-05 | LOW | Contract hash lacks explicit schema version; pinned fake-loop binding remains deterministic |
| G2-S2-06 | LOW | `DecisionReasonCode` location deviates from repository convention without a concrete compatibility defect |

The bounded corrective patch should close only G2-S2-01 through G2-S2-04 and,
if safely adjacent, G2-S2-05. It should not expand the attempt engine or add
persistence/concurrency infrastructure.

## SCOPE_AND_NON_GOALS

Reviewed: fake-loop product evidence, decision authority and schema, bounded
atomic commit, decision-stream sequencing, exactly-two-attempt behavior,
evidence freshness, objective improvement, zero owner intervention, and Gate 1
preservation.

Not requested or reviewed as implementation requirements: Attempt 3,
`WAITING_OWNER`, `NO_PROGRESS` execution, real adapters, subprocess execution,
database persistence, crash recovery, concurrency/leases, assessment storage,
analytics, routing, Memory, graph orchestration or self-improvement.

## UNPROVEN_ITEMS

- Real-world reduction in human coordination or intervention.
- Real Claude Code CLI invocation, cancellation, sessions and provenance.
- Durability across process crash and recovery.
- Concurrent decision appends or multi-writer sequence allocation.
- Content-addressed evidence identity.
- Generalized repeated revision or Attempt 3 behavior.
- Full human-intervention event modeling.

## REVIEW_BUDGET_CONSEQUENCE

This was the first independent Gate 2 review. `REQUEST_CHANGES` consumes that
review and authorizes exactly the remaining default budget:

```text
1 bounded corrective patch
1 corrective rereview
```

It does not authorize a redesign, a second opinion verifier, Gate 2 evidence,
or the next gate.

## NEXT_GATE_RECOMMENDATION

```text
GATE_2_STATUS=NOT_APPROVED
ONE_BOUNDED_CORRECTIVE_PATCH_AVAILABLE
NO_NEXT_GATE_AUTHORIZED
```
