# Final Independent Correctness Review — Gate 2 Trust Boundary

Reviewer: Codex GPT-5.6 Sol High
Mode: `INDEPENDENT_CORRECTNESS`

## VERDICT

```text
REQUEST_CHANGES
```

The canonical membership correction closes the three prior coordinated archive
forgeries and correctly derives failed-check and satisfied-criterion IDs from
canonical contract members in contract order. The Runtime writer is safe by
construction, and all prior Gate 2 and Gate 1 preservation checks pass.

A current MEDIUM trust-boundary defect nevertheless remains under the review's
explicit contract-order rule. Starting with a genuine completed two-check
Scenario 2 record, a caller can replace the embedded `TaskContract` with the
same contract whose required checks are reordered, then rewrite only the
order-derived `failed_check_ids` and canonical Finding. Both
`RunRecord.model_validate(...)` and `validate_completed_record(...)` accept the
copy. The original revision DecisionEvent remains bound to the original
contract hash and rejects that old hash if used after the swap, but neither
completed-record boundary receives or retains that binding. It therefore treats
the caller-selected replacement contract as canonical.

The task requires this current public-boundary acceptance to be classified
MEDIUM. Consequently `G2-S2-04B` and `G2-S2-04` remain open, Gate 2 is not
approved, and a new Owner decision is required.

## TARGET_SOURCE_COMMIT

```text
84bb7b0b7e332a2cd0545695ca5c16bd0cf15fe5
```

## REVIEWED_DIFF_RANGE

```text
a3879b2235d88d5b43f6c37000a9609b7d4af48b..84bb7b0b7e332a2cd0545695ca5c16bd0cf15fe5
```

## REVIEW_REPORT_EXPECTED_PARENT

```text
a625ca755ef2eeb3683cbaab3508f6ec71ceb603
```

Preflight proved `main`, exact expected HEAD and parent, clean tracked worktree
and index, no untracked project files, and direct ancestry from the target
source commit to the implementation-report commit. Ignored caches, bytecode and
`.DS_Store` files were present and are not project changes.

## FILES_REVIEWED

Contract and lineage truth:

- `CLAUDE.md`
- `SYSTEM.md`
- `docs/OFFICIAL_BUILD_AND_AI_CODING_PROCESS_V1.md`
- `docs/EVALUATION_CONTRACT_V1.md`
- `docs/VERIFIER_POLICY_V1.md`
- `docs/MVP_EXECUTION_POLICY_V0.md`
- `docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md`
- `docs/gate-evidence/GATE_1_SCENARIO_1.md`
- `REPORT_CODEX_SOL_OWNER_AUTHORIZED_FINAL_GATE_2_REREVIEW.md`
- `REPORT_CLAUDE_GATE_2_CANONICAL_ARCHIVE_MEMBERSHIP.md`
- `REPORT_CLAUDE_GATE_2_FINAL_CORRECTIVE_ARCHIVE_TRUST.md`

Implementation truth and tests:

- `src/tomtit/runtime/state.py`
- `src/tomtit/runtime/runtime.py`
- `src/tomtit/runtime/events.py`
- `src/tomtit/reporting.py`
- `src/tomtit/domain/**`
- `src/tomtit/fakes.py`
- `tests/scenarios/test_scenario_1.py`
- `tests/scenarios/test_scenario_2.py`
- `tests/domain/**`

The corrective source commit changes exactly:

```text
src/tomtit/runtime/state.py
tests/scenarios/test_scenario_2.py
```

The range contains 609 insertions and 13 deletions. Every path required
unchanged by the authorization is unchanged.

## COMMANDS_AND_EXIT_CODES

| Command | Exit | Result |
|---|---:|---|
| Required Git preflight command set | 0 each | Exact lineage and clean project state |
| `python3 -m pytest --collect-only -q` | 0 | 308 collected |
| `python3 -m pytest -q` | 0 | 308 passed |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed |
| `python3 -m pytest tests/scenarios/test_scenario_2.py -q` | 0 | 123 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 40 passed |
| `python3 -m pytest tests/domain/test_contracts.py -q` | 0 | 43 passed |
| `python3 -m pytest tests/domain/test_schema_generation.py -q` | 0 | 3 passed |
| `python3 -m compileall -q src tests` | 0 | Passed |
| `ruff check .` | 0 | No findings |
| `git diff --check` | 0 | No whitespace errors |
| `python3 -m mypy src tests` | 1 | 108 diagnostics in five test files |
| `python3 -m mypy src` | 0 | No issues in 11 source files |
| reviewed-range `git diff --stat` | 0 | Two authorized files; 609 insertions, 13 deletions |
| reviewed-range `git diff --check` | 0 | No whitespace errors |
| focused membership/order/preservation selection | 0 | 27 passed |
| focused StopDecision/Finding selection | 0 | 32 passed |
| `/private/tmp/tomtit_gate2_final_trust_probe.py` | 0 | Three prior forgeries rejected; reordered-contract copy accepted; stale decision hash rejected |
| `/private/tmp/tomtit_gate2_atomic_probe.py` | 0 | Seven rollback/retry injections passed |
| isolated 16-control pytest module | 1 | Expected: 16 failed restored-invariant assertions |

The 108 full-tree Mypy diagnostics exactly match the documented test-only
baseline. No source diagnostic exists, so this remains deferred LOW.

## INDEPENDENT_REPRODUCTIONS

The direct trust-boundary probe produced:

```text
A_invented_failed:
  RunRecord.model_validate=REJECTED
  validate_completed_record=REJECTED

B_duplicate_failed:
  RunRecord.model_validate=REJECTED
  validate_completed_record=REJECTED

C_noncontract_satisfied:
  RunRecord.model_validate=REJECTED
  validate_completed_record=REJECTED

D_caller_reordered_contract:
  RunRecord.model_validate=ACCEPTED
  validate_completed_record=ACCEPTED

E_revision_decision_old_hash:
  commit_revision_decision=REJECTED:RevisionNotAllowed
```

Additional focused reproductions rejected at both completed-record boundaries:
invented passing check; duplicate passing check; duplicate snapshot identity;
missing canonical required check; extra unknown check with full canonical
coverage; archive-controlled check observation and failed-ID ordering; unknown
unsatisfied criterion; duplicate satisfied/unsatisfied evaluation; foreign
criterion mixed with canonical criteria; archive-controlled criterion and
satisfied-ID ordering; and unsatisfied criterion listed as satisfied.

A genuine revised record, a genuine multi-check/multi-criterion revised record,
and a Gate 1 unrevised record remained accepted.

## NEGATIVE_CONTROLS

The isolated `/private/tmp` pytest module simulated every requested restored
invalid condition through copied/private state. It exited 1 with these exact
failing invariant tests:

1. `test_nc01_unknown_check_defaults_to_empty_expected_codes`
2. `test_nc02_unknown_check_enters_failed_derivation`
3. `test_nc03_duplicate_check_observations_accepted`
4. `test_nc04_duplicate_snapshot_ids_accepted`
5. `test_nc05_missing_required_check_coverage_accepted`
6. `test_nc06_failed_ids_derive_in_archive_order`
7. `test_nc07_unknown_criterion_ids_accepted`
8. `test_nc08_duplicate_criterion_evaluations_accepted`
9. `test_nc09_satisfied_ids_derive_in_archive_order`
10. `test_nc10_bypass_runrecord_archive_validation`
11. `test_nc11_bypass_validate_completed_reconstruction`
12. `test_nc12_writer_noncontract_criterion_without_strict_validation`
13. `test_nc13_exhausted_budget_revise_recording_reenabled`
14. `test_nc14_public_checking_to_revising_reenabled`
15. `test_nc15_canonical_metadata_comparison_removed`
16. `test_nc16_contract_hash_schema_version_removed`

Each test restores the prohibited condition and fails the invariant that the
current guard enforces. Controls 1–6 prove contract-membership, uniqueness,
coverage and canonical check-order rules are load-bearing. Controls 7–9 do the
same for criteria. Controls 10–12 demonstrate why both reconstruction layers
and the strict archive validator are required even though the current writer is
safe by construction. Controls 13–16 preserve the earlier authority and hash
guards. The tracked tree was not modified.

The separate caller-reordered-contract reproduction is a current-tree defect,
not a negative control.

## REQUIRED_CHECK_MEMBERSHIP_RESULTS

`archived_check_membership_error()` runs before derivation and requires every
observation to name a canonical required check, with unique check and snapshot
identities. `revision_attempt_archive_error()` then requires exact coverage of
the canonical required-check set. Unknown checks never reach a default empty
expected-code tuple.

Invented passing/failing checks, duplicate passing/failing checks, duplicate
snapshot identity, missing coverage and extra unknown observations all reject
at both completed-record boundaries.

```text
CANONICAL_REQUIRED_CHECK_MEMBERSHIP=RESOLVED
```

## FAILED_CHECK_DERIVATION_RESULTS

`canonical_failed_required_check_ids()` iterates
`TaskContract.required_checks`, resolves at most one observation per canonical
ID after uniqueness validation, and compares against canonical expected exit
codes. It cannot emit unknown or duplicate IDs. Exact tuple comparison rejects
passing checks stored as failed, omitted failures, invented IDs, alphabetical
substitution, stored-tuple reordering, and coordinated observation/tuple
reordering. The order-sensitive contract was `unit-tests, lint`, deliberately
different from alphabetical order.

```text
CANONICAL_FAILED_CHECK_DERIVATION=RESOLVED
```

## CRITERION_MEMBERSHIP_RESULTS

`archived_criterion_membership_error()` runs before satisfied-ID derivation.
Every evaluation ID must belong to `TaskContract.acceptance_criteria`, and each
ID is unique. `AttemptEvidence` independently rejects duplicate evaluations.
Unknown satisfied/unsatisfied criteria, duplicates, and foreign criteria mixed
with canonical criteria reject. Missing evaluations remain permitted and are
treated as not satisfied, as authorized.

```text
CANONICAL_CRITERION_MEMBERSHIP=RESOLVED
```

## SATISFIED_CRITERION_DERIVATION_RESULTS

`canonical_satisfied_criterion_ids()` iterates canonical acceptance criteria in
contract order and includes an ID only when its unique evaluation is
`SATISFIED`. Exact tuple comparison rejects unknown/duplicate/forged IDs,
unsatisfied IDs, archive-controlled ordering and alphabetical ordering. The
order-sensitive satisfied tuple was `z-lint-clean, a-typecheck-clean`, not its
alphabetical ordering.

```text
CANONICAL_SATISFIED_CRITERION_DERIVATION=RESOLVED
```

## WRITER_PATH_RESULTS

Every production call was inspected. `_execute_attempt()` obtains checks only
by iterating `task_contract.required_checks`; the evidence transition validates
provenance, uniqueness and exact coverage. `_evaluate_deterministic_criteria()`
iterates `task_contract.acceptance_criteria` and never accepts externally
supplied criterion IDs. `_current_attempt_evidence()` omits the optional
`acceptance_criteria` argument, but receives only those Runtime-built unique
evaluations in canonical order. `_prepare_revision()` constructs the archive
and calls `_build_record()`, which constructs a fully validated `RunRecord`;
strict archive membership is therefore re-proven before the record becomes
canonical.

No supported writer path can source, duplicate or preserve a non-contract
criterion into a canonical archive.

```text
WRITER_CRITERION_MEMBERSHIP=SAFE_BY_CONSTRUCTION
```

## CONTRACT_ORDER_BINDING_RESULTS

Within a supported live Runtime operation, the same immutable `TaskContract`
instance is retained in every `_build_record()` update; proposal creation and
commit independently hash that contract, and a draft bound to the original
contract is rejected after an unsafe contract swap.

The public completed-record boundaries do not retain or accept an independent
canonical contract hash, DecisionEvent, run-registry reference or equivalent
anchor. Their only contract truth is the `task_contract` embedded in the
untrusted record. The independent reproduction replaced it with a reordered
contract, rewrote the order-derived archive tuple and Finding, and was accepted
at both boundaries. This is the exact MEDIUM condition defined by the review
authorization, not merely a future portability limitation.

```text
CONTRACT_ORDER_BINDING=OPEN
```

## ARCHIVE_TRUST_BOUNDARY_RESULTS

Canonical membership, uniqueness, coverage, derivation order, StopDecision and
Finding semantics are all re-proven through the shared validator. The three
prior coordinated forgeries are closed. However, the validator proves those
facts relative to a caller-replaceable embedded contract rather than a retained
canonical contract binding. The completed-record trust boundary therefore
remains open at MEDIUM severity.

```text
G2-S2-04B=OPEN
G2-S2-04=OPEN
```

## STOP_DECISION_AND_FINDING_RESULTS

No regression was found. The shared StopDecision predicate requires exactly
REVISE, repairable true, owner input false and null terminal fields. Exactly one
Finding is re-derived from the first failed required check in canonical contract
order and compared as a complete immutable value. Missing/wrong decisions;
missing/multiple/forged findings; foreign-run or wrong-attempt identities;
missing failed snapshot; wrong severity, category/message, fingerprint or
acceptance criterion all reject.

## ATOMICITY_AND_ROLLBACK_RESULTS

Seven independent injections covered archive validation, archive construction,
DecisionEvent preparation, candidate transition preparation, decision append,
transition-event append and partial transition append. Every failure restored
the exact record identity/value and exact serialized event/decision streams,
preserved Attempt 1 StopDecision/Finding and earlier bytes, and created no
archive, decision, event or attempt increment. Every retry committed exactly
one archive, one sequence-1 DecisionEvent, one `REVISION_REQUESTED` event and
Attempt 2, with no duplicate transition.

```text
DECISION_EVENT_ATOMICITY=RESOLVED
```

## PRESERVATION_RESULTS

- G2-S2-01: public `CHECKING -> REVISING` remains protected.
- G2-S2-02: canonical decision metadata remains independently re-derived.
- G2-S2-03: foreign-run archives remain rejected.
- G2-S2-04A: exhausted Attempt 2 exposes only `(FAIL,)`; repairable recording
  rejects before any write; no Finding, REVISE decision or Attempt 3 appears.
- G2-S2-05: task-contract hash retains its explicit schema envelope.
- Append sequence remains Runtime/store-owned and retry sequence is 1.
- Attempt 2 evidence remains fresh and disjoint.
- Objective improvement and copied-record reconstruction remain enforced.
- Controlled Scenario 2 has zero owner-intervention events.
- Scenario 1 source/tests are unchanged; all 99 tests pass.
- Gate 1 empty-history completion and report reconstruction remain valid.

The missing exhausted FAIL terminal branch remains out of scope and nonblocking.

## FINDING_STATUS

```text
G2-S2-04B=OPEN
G2-S2-04=OPEN

G2-S2-04A=RESOLVED
G2-S2-01=RESOLVED
G2-S2-02=RESOLVED
G2-S2-03=RESOLVED
G2-S2-05=RESOLVED

SCENARIO_2=PROVEN
DECISION_AUTHORITY=RESOLVED
DECISION_EVENT_ATOMICITY=RESOLVED
APPEND_SEQUENCE_AUTHORITY=RESOLVED
ATTEMPT_PROGRESSION=RESOLVED
EVIDENCE_FRESHNESS=RESOLVED
OBJECTIVE_IMPROVEMENT=RESOLVED
ZERO_OWNER_INTERVENTION=RESOLVED
GATE_1_PRESERVATION=PRESERVED

WRITER_CRITERION_MEMBERSHIP=SAFE_BY_CONSTRUCTION
CONTRACT_ORDER_BINDING=OPEN

EXHAUSTED_FAIL_TERMINAL_BRANCH=NOT_IMPLEMENTED_OUT_OF_SCOPE
DECISION_REASON_CODE_LOCATION=DEFERRED_LOW
TEST_ONLY_MYPY=DEFERRED_LOW
PERSISTENCE_AND_CRASH_RECOVERY=OUT_OF_SCOPE
CONCURRENCY=OUT_OF_SCOPE
REAL_WORLD_COORDINATION_VALUE=NOT_PROVEN
```

## PRODUCT_EVIDENCE_ASSESSMENT

```text
The Runtime can perform one bounded revision from deterministic
failure to successful completion without owner input in the
controlled fake loop.

PROVEN
```

The happy path remains independently supported by the complete and focused
suites. This product statement does not override the open MEDIUM completed-
record trust-boundary defect.

## RISKS

| ID | Severity | Risk |
|---|---|---|
| G2-S2-04B / contract binding | MEDIUM | Both completed-record boundaries accept a caller-selected reordered TaskContract when order-derived archive fields are rewritten consistently; the original decision hash is not available at those boundaries |
| Known limitation | LOW | Exhausted failed Attempt 2 remains in `CHECKING`; FAIL terminal branch is absent |
| Known limitation | LOW | Writer helper criterion membership is optional, though the only production writer is safe by construction and strict archive validation follows |
| Known limitation | LOW | Duplicate snapshot identity is structurally implied by duplicate check identity after provenance validation |
| Known limitation | LOW | Exact Finding equality is rigid; archive derivation does not hash artifact contents |

## SCOPE_AND_NON_GOALS

Reviewed only canonical check/criterion membership, uniqueness and order,
original coordinated forgeries, writer-path safety, contract-order binding,
archive trust, prior Gate 2 findings and Gate 1 preservation. No source, test,
governance, implementation report, prior review report, configuration or Gate
evidence was changed. No implementation or review task was created.

The FAIL terminal branch, Attempt 3, `WAITING_OWNER`, `NO_PROGRESS`, finding
ranking, general policy engine, artifact content hashing, persistence, crash
recovery, concurrency, adapters, subprocesses, product CLI, routing, Memory,
graphs, analytics and self-improvement remain outside this review.

## UNPROVEN_ITEMS

- Real-world coordination or intervention reduction.
- Real Claude Code CLI execution and provenance.
- Persistence, crash recovery, durability and concurrency.
- Archived artifact byte/content integrity.
- Exhausted failed Attempt 2 terminal behavior.
- General contract-version migration and historical contract registry behavior.
- General Attempt 3 or multiple-revision behavior.

## REVIEW_BUDGET_CONSEQUENCE

This final trust-boundary review is consumed. No additional implementation
patch or rereview is authorized. Because the verdict is `REQUEST_CHANGES`:

```text
GATE_2_STATUS=NOT_APPROVED
OWNER_DECISION_REQUIRED
NO_AUTOMATIC_CORRECTIVE_CYCLE
NO_NEXT_GATE_AUTHORIZED
```

This report does not authorize another implementation, another review, Gate 2
evidence or the next gate.

## NEXT_GATE_RECOMMENDATION

```text
GATE_2_STATUS=NOT_APPROVED
OWNER_DECISION_REQUIRED
NO_AUTOMATIC_CORRECTIVE_CYCLE
NO_NEXT_GATE_AUTHORIZED
```
