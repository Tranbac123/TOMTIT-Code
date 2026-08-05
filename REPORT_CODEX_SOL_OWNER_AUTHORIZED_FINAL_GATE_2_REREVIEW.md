# Owner-Authorized Final Independent Correctness Rereview — Gate 2 Minimal Scenario 2

Reviewer: Codex GPT-5.6 Sol High
Mode: `INDEPENDENT_CORRECTNESS`

## VERDICT

```text
REQUEST_CHANGES
```

`G2-S2-04A` is resolved: exhausted Attempt 2 cannot record a contradictory
`REVISE` branch and no Attempt 3 is created. `G2-S2-04B` remains open at
MEDIUM severity. The shared archive validator rejects all 24 requested simple
tamper cases, but both public completed-record boundaries accept coordinated
forgeries when the forged stored tuples exactly match non-canonical archived
observations:

1. an invented failed check observation plus the invented failed-check ID;
2. a duplicated failed observation plus duplicated failed-check IDs; and
3. a satisfied criterion that is not in the canonical contract, when a
   two-criterion completed record still satisfies the numeric improvement check.

The defect is in the canonical derivation itself. `attempt_evidence_from_observations()`
treats an observation whose check ID is absent from `TaskContract.required_checks`
as failed because its expected-code tuple defaults to empty, and it preserves
duplicate observations. Satisfied IDs are derived from every archived criterion
evaluation without restricting IDs to the canonical contract or enforcing
uniqueness. Exact tuple equality cannot protect the trust boundary when both
the forged source observations/evaluations and the stored derived tuple agree.

Because a current MEDIUM finding remains and no further patch or rereview is
authorized, Gate 2 is not approved and requires a new Owner decision.

## TARGET_SOURCE_COMMIT

```text
eb2d87df4c2200294c016d1a1e629a46e8231c4a
```

## REVIEWED_DIFF_RANGE

```text
bf7abaf2684d5792d72e0320e47132b24cae27f5..eb2d87df4c2200294c016d1a1e629a46e8231c4a
```

## REVIEW_REPORT_EXPECTED_PARENT

```text
d4bfe91004c7f2bf020b89aa7d92859f54742888
```

Preflight proved `main`, the exact expected `HEAD` and direct parent, clean
tracked worktree and index, no untracked project files, and ancestry from the
target source commit to the implementation-report commit. Ignored caches,
bytecode and `.DS_Store` files were present and were not treated as project
changes.

## FILES_REVIEWED

Required contract and lineage material:

- `CLAUDE.md`
- `SYSTEM.md`
- `docs/OFFICIAL_BUILD_AND_AI_CODING_PROCESS_V1.md`
- `docs/EVALUATION_CONTRACT_V1.md`
- `docs/VERIFIER_POLICY_V1.md`
- `docs/MVP_EXECUTION_POLICY_V0.md`
- `docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md`
- `docs/gate-evidence/GATE_1_SCENARIO_1.md`
- `REPORT_CODEX_SOL_GATE_2_MINIMAL_SCENARIO_2_REVIEW.md`
- `REPORT_CLAUDE_GATE_2_CORRECTIVE_AUTHORITY_PROVENANCE.md`
- `REPORT_CODEX_SOL_GATE_2_CORRECTIVE_REREVIEW.md`
- `REPORT_CLAUDE_GATE_2_FINAL_CORRECTIVE_ARCHIVE_TRUST.md`

Implementation truth and relevant tests:

- `src/tomtit/runtime/runtime.py`
- `src/tomtit/runtime/state.py`
- `src/tomtit/runtime/events.py`
- `src/tomtit/reporting.py`
- `src/tomtit/domain/**`
- `src/tomtit/fakes.py`
- `tests/scenarios/test_scenario_1.py`
- `tests/scenarios/test_scenario_2.py`
- `tests/domain/**`

The corrective source diff changes exactly:

```text
src/tomtit/runtime/runtime.py
src/tomtit/runtime/state.py
tests/scenarios/test_scenario_2.py
```

It is 608 insertions and 43 deletions. Every path required unchanged by the
review authorization is unchanged in the reviewed range.

## COMMANDS_AND_EXIT_CODES

| Command | Exit | Result |
|---|---:|---|
| Git preflight command set | 0 each | Exact branch, lineage and clean project state |
| `python3 -m pytest --collect-only -q` | 0 | 288 collected |
| `python3 -m pytest -q` | 0 | 288 passed |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed |
| `python3 -m pytest tests/scenarios/test_scenario_2.py -q` | 0 | 103 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 40 passed |
| `python3 -m pytest tests/domain/test_contracts.py -q` | 0 | 43 passed |
| `python3 -m pytest tests/domain/test_schema_generation.py -q` | 0 | 3 passed |
| `python3 -m compileall -q src tests` | 0 | Passed |
| `ruff check .` | 0 | No findings |
| `git diff --check` | 0 | No whitespace errors |
| `python3 -m mypy src tests` | 1 | 99 diagnostics in five test files |
| `python3 -m mypy src` | 0 | No issues in 11 source files |
| reviewed-range `git diff --stat` | 0 | Three authorized files, 608 insertions, 43 deletions |
| reviewed-range `git diff --check` | 0 | No whitespace errors |
| `/private/tmp/tomtit_gate2_required_archive_cases.py` | 0 | Requested 24 cases rejected at both boundaries; genuine accepted |
| `/private/tmp/tomtit_gate2_archive_probe.py` | 0 | Three coordinated forgeries accepted at both boundaries |
| `/private/tmp/tomtit_gate2_atomic_probe.py` | 0 | Seven rollback/retry injections passed |
| isolated 17-control pytest module | 1 | Expected negative-control run: 17 failed invariant assertions |

The 99 full-tree Mypy diagnostics match the documented test-only baseline and
are nonblocking. No source diagnostic exists.

## INDEPENDENT_REPRODUCTIONS

### Exhausted budget

Attempt 1 failed, a valid revision started Attempt 2, and Attempt 2 failed.
Canonical state was `CHECKING`, `attempt_count=2`, `max_attempts=2`, and
canonical allowed actions were exactly `(FAIL,)`. Calling
`record_repairable_failure()` raised `RevisionNotAllowed` before any write:
record identity/value, current branch fields, Attempt 1 archive, execution
events and decision stream were unchanged. The one earlier decision and one
earlier `REVISION_REQUESTED` event remained; no Attempt 3 appeared. A forged
REVISE proposal was separately rejected at commit time.

### Requested copied completed records

Starting from a genuine completed Scenario 2 record, both
`RunRecord.model_validate(...)` and `validate_completed_record(...)` rejected
each of the 24 requested cases: missing or malformed StopDecision fields; zero,
duplicate or field-forged findings; passing evidence retained as failed;
forged, omitted or duplicated failed IDs; forged satisfied IDs; foreign archive;
nonpreceding attempt; and two archives. The genuine record was accepted.

### Coordinated archive forgeries

Both boundaries returned `ACCEPTED` for all three:

```text
invented_required_check       model=ACCEPTED completion=ACCEPTED
duplicate_failed_check        model=ACCEPTED completion=ACCEPTED
foreign_satisfied_criterion   model=ACCEPTED completion=ACCEPTED
```

The first adds a same-run/same-attempt observation named `invented-check`, with
exit 1, and stores `failed_check_ids=(invented-check, unit-tests)`. The second
duplicates the genuine failed observation and stores the duplicated ID tuple.
The third uses a canonical two-criterion current attempt, adds an archived
satisfied evaluation whose ID is absent from the contract, and stores that ID;
objective improvement still passes numerically from one claimed archived
criterion to two current canonical criteria.

### Genuine and Gate 1 records

The genuine revised record was accepted by both boundaries. The 99 Scenario 1
tests passed, including report reconstruction, and the focused Gate 2 test for
an unrevised completed record with empty attempt history passed.

## NEGATIVE_CONTROLS

The isolated pytest module under `/private/tmp` simulated all 17 requested
restored invalid states using public paths or deliberately unsafe copied/private
state. It exited 1 with the following 17 failing invariant tests:

1. `test_nc01_remove_exhausted_budget_check`
2. `test_nc02_allow_missing_archived_stop_decision`
3. `test_nc03_allow_wrong_stop_decision_fields`
4. `test_nc04_allow_missing_finding`
5. `test_nc05_allow_duplicate_findings`
6. `test_nc06_allow_forged_finding_identity`
7. `test_nc07_allow_finding_without_failed_snapshot`
8. `test_nc08_stop_rederiving_failed_checks`
9. `test_nc09_use_subset_failed_check_equality`
10. `test_nc10_stop_rederiving_satisfied_criteria`
11. `test_nc11_bypass_runrecord_archive_validation`
12. `test_nc12_bypass_completed_record_reconstruction`
13. `test_nc13_remove_archive_rollback`
14. `test_nc14_reenable_public_checking_to_revising`
15. `test_nc15_accept_false_canonical_decision_metadata`
16. `test_nc16_reenable_attempt_three`
17. `test_nc17_remove_contract_hash_schema_version`

Controls 1–9 and 11–17 show the prohibited state each guard is intended to
exclude and why that guard is load-bearing. Control 10 is additionally a direct
current-tree reproduction: the sophisticated forged satisfied criterion is
accepted by `validate_completed_record()`, so its invariant assertion fails for
a present defect, not merely a restored one. The tracked review tree was never
modified by these controls.

## EXHAUSTED_BUDGET_AUTHORITY_RESULTS

`record_repairable_failure()` re-derives canonical revision metadata, requires
`REVISE` in canonical allowed actions, and checks both configured and hard
attempt limits before `_replace_record()`. Attempt 1 valid behavior remains:
one deterministic canonical finding, the exact repairable StopDecision,
value-idempotent repeated calls, and no decision, event, transition or attempt
change. Commit-time proposal validation independently refuses exhausted budget.

```text
G2-S2-04A=RESOLVED
```

## ARCHIVE_TRUST_BOUNDARY_RESULTS

Identity binding is correct for archive run/attempt, archived checks and patch,
and preceding-attempt order. The bounded one-archive completed shape is also
enforced. The trust boundary is nevertheless incomplete because it validates
stored derivations against a helper that accepts non-contract and duplicate
source observations/evaluations. Coordinated archive content can therefore be
internally consistent with the helper but inconsistent with canonical contract
truth.

```text
G2-S2-04B=OPEN
```

## FAILED_CHECK_DERIVATION_RESULTS

Simple forged, missing and duplicate stored tuples are rejected when the
observations remain genuine. Exact equality is present. However:

- unexpected check IDs are not rejected before derivation;
- a non-contract check uses an empty expected-exit-code tuple and is classified
  as failed;
- duplicate observed checks produce duplicate derived IDs; and
- no uniqueness/exact required-check coverage rule is applied to an archive.

Thus invented and duplicated failed IDs are accepted when their forged
observations are changed in concert. Required failed-check derivation is not
fully canonical.

Satisfied IDs are likewise filtered only by `status=SATISFIED`, not by canonical
criterion membership or uniqueness. The coordinated non-contract criterion
reproduction is accepted. Required satisfied-criterion derivation is not fully
canonical.

## STOP_DECISION_AND_FINDING_RESULTS

The shared StopDecision predicate requires exactly REVISE, repairable true,
owner input false, and null outcome/reason. The finding is re-derived from
canonical required-check order and compared as a complete immutable value,
covering identity, severity, category/message, fingerprint, acceptance
criterion and evidence references. All requested StopDecision/Finding field
forgeries reject at both boundaries.

This portion of `G2-S2-04B` is sound. It does not cure the separate failed-check
and satisfied-criterion derivation gaps.

## ATOMICITY_AND_ROLLBACK_RESULTS

Seven independent injections covered archive semantic validation, archive
construction, DecisionEvent preparation, candidate transition preparation,
decision append, transition-event append, and partial transition-event append.
Every failure restored the exact record identity/value and exact serialized
decision/execution streams, retained the current Attempt 1 StopDecision/Finding,
and left earlier event bytes unchanged. Every retry committed exactly one
archive, decision sequence 1, one revision event and Attempt 2, with no duplicate
transition.

```text
DECISION_EVENT_ATOMICITY=RESOLVED
```

## PRESERVATION_RESULTS

- `G2-S2-01`: public `CHECKING -> REVISING` remains protected.
- `G2-S2-02`: canonical policy, allowed actions, constraints and budget are
  independently re-derived; false proposal metadata rejects.
- `G2-S2-03`: foreign-run archive identity rejects.
- `G2-S2-05`: contract hash retains its explicit schema-version envelope.
- Append sequence remains store-owned and retry persists sequence 1.
- Attempt 2 evidence remains fresh and disjoint; no Attempt 3 is created.
- Copied completions without objective improvement remain rejected.
- Controlled Scenario 2 emits no owner-intervention events.
- Scenario 1 source/tests are unchanged and all 99 tests pass.
- Gate 1 unrevised completion/report reconstruction remains valid.
- `reporting.py` remains unchanged; its reconstruction reaches
  `validate_completed_record()` and the `RunRecord` boundary.

The new G2-S2-04B reproduction is an archive semantic-derivation defect. It does
not regress those narrower resolved findings.

## FINDING_STATUS

```text
G2-S2-04A=RESOLVED
G2-S2-04B=OPEN
G2-S2-04=OPEN

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

The happy path and zero-owner-intervention evidence pass. Product evidence does
not override the open MEDIUM completed-archive trust defect, so it is
insufficient for Gate approval.

## RISKS

| ID | Severity | Risk |
|---|---|---|
| G2-S2-04B | MEDIUM | Completed revised records accept coordinated invented/duplicate failed-check evidence and a non-contract satisfied criterion because the shared derivation is not restricted to canonical contract membership and uniqueness |
| Known limitation | LOW | Exhausted failed Attempt 2 remains in `CHECKING`; FAIL terminal branch is not implemented |
| Known limitation | LOW | Exact Finding equality is rigid and archive validation recomputes on every RunRecord construction |
| Known limitation | LOW | Archive identity/derivation binding is not artifact content hashing |

## SCOPE_AND_NON_GOALS

Reviewed only exhausted-budget decision authority, archived completed-record
trust, revision atomicity, preservation of previously resolved Gate 2 findings,
and Gate 1 preservation. No source, test, governance, configuration, prior
report or gate-evidence file was changed. No implementation prompt or corrective
cycle was created.

The FAIL terminal branch, Attempt 3 implementation, `WAITING_OWNER`,
`NO_PROGRESS`, multiple-finding/ranking engines, real adapters, subprocesses,
persistence, crash recovery, concurrency, leases, assessment store, routing,
Memory, graphs, analytics and self-improvement remain outside this review.

## UNPROVEN_ITEMS

- Real-world coordination or intervention reduction.
- Real Claude Code CLI invocation and provenance.
- Persistence, crash recovery, power-loss durability and concurrency.
- Archived artifact byte/content integrity.
- Exhausted failed Attempt 2 terminal behavior.
- General multi-revision or Attempt 3 behavior.
- Exhaustiveness against every conceivable coordinated archive forgery.

## REVIEW_BUDGET_CONSEQUENCE

The Owner-authorized additional bounded corrective patch and this final
rereview are consumed. No additional implementation patch or rereview is
authorized. Because the verdict is `REQUEST_CHANGES`:

```text
GATE_2_STATUS=NOT_APPROVED
OWNER_DECISION_REQUIRED
NO_AUTOMATIC_CORRECTIVE_CYCLE
NO_NEXT_GATE_AUTHORIZED
```
This report does not authorize another task, another verifier, Gate 2 evidence,
or the next gate.

## NEXT_GATE_RECOMMENDATION

```text
GATE_2_STATUS=NOT_APPROVED
OWNER_DECISION_REQUIRED
NO_AUTOMATIC_CORRECTIVE_CYCLE
NO_NEXT_GATE_AUTHORIZED
```
