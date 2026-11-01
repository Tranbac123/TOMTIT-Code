# Final Corrective Independent Correctness Rereview — Gate 2 Minimal Scenario 2

Reviewer: Codex GPT-5.6 Sol High

Review mode: `INDEPENDENT_CORRECTNESS`

Risk class: `HIGH_CANONICAL_STATE_AUTHORITY_AND_TRUST_BOUNDARY`

## VERDICT

```text
REQUEST_CHANGES
```

The corrective patch resolves G2-S2-01, G2-S2-02, G2-S2-03 and G2-S2-05.
The intended controlled Scenario 2 path remains proven, the archive/decision/
transition commit passes the full rollback matrix, and Gate 1 remains preserved.

G2-S2-04 remains `OPEN` with two independently reproduced MEDIUM defects:

1. after Attempt 2 fails at the exhausted `max_attempts=2` budget,
   `record_repairable_failure()` still writes a canonical
   `StopDecision(next_action=REVISE, repairable=true)` and one finding, even
   though canonical allowed actions are `(FAIL,)`; and
2. `RunRecord.model_validate()` and `validate_completed_record()` accept copied
   completed records whose Attempt 1 archive has its repairable `StopDecision`
   removed, its finding removed/duplicated/forged, or its formerly failed check
   changed to passing while `failed_check_ids` still claims failure.

The first defect creates a contradictory canonical branch record through a new
public Runtime method. The second means the public completion/report trust
boundary does not actually preserve and re-prove the corrective audit record.
Both are inside the corrective scope and material approved semantics. No further
automatic implementation or rereview action remains authorized.

## TARGET_SOURCE_COMMIT

```text
b71aca92ceb9a17544862e596cd17bd2dc794052
```

## REVIEWED_DIFF_RANGE

```text
716fa6b2e8000ee5c63bd157efb4bcf18c59b06a..b71aca92ceb9a17544862e596cd17bd2dc794052
```

## REVIEW_REPORT_EXPECTED_PARENT

```text
1a41d410a220106b8969fab7818d4a8de00f1671
```

Git preflight matched exactly: branch `main`; `HEAD` equal to the expected
report parent; `HEAD^` equal to the target source commit; target source is an
ancestor of the implementation report; tracked worktree, index and untracked
project-file set were clean. Only ignored `.DS_Store`, cache and bytecode files
were present.

## FILES_REVIEWED

Contract, policy, prior evidence and reports:

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
- `REPORT_CLAUDE_GATE_2_MINIMAL_SCENARIO_2.md`

The three approved governance hashes still exactly match the Gate 1 approval
bundle.

Corrective source/test diff:

- `src/tomtit/runtime/runtime.py`
- `src/tomtit/runtime/state.py`
- `tests/scenarios/test_scenario_2.py`

The range changes exactly these three files: 926 insertions and 42 deletions.
Reporting, event storage, domain models/enums/contracts, fakes, Scenario 1,
domain tests, governance and Gate evidence are unchanged.

## COMMANDS_AND_EXIT_CODES

| Command | Exit | Result |
|---|---:|---|
| `git status --short` | 0 | Empty at preflight |
| `git status --porcelain=v1 --untracked-files=all` | 0 | Empty at preflight |
| `git status --ignored --short` | 0 | Ignored files only |
| `git branch --show-current` | 0 | `main` |
| `git rev-parse HEAD` | 0 | `1a41d410a220106b8969fab7818d4a8de00f1671` |
| `git rev-parse HEAD^` | 0 | `b71aca92ceb9a17544862e596cd17bd2dc794052` |
| `git merge-base --is-ancestor b71aca9... 1a41d41...` | 0 | Ancestry confirmed |
| `git log --oneline --decorate -18` | 0 | Inspected |
| `git show --stat --oneline b71aca9...` | 0 | Three corrective files |
| `git show --stat --oneline 1a41d41...` | 0 | Corrective implementation report only |
| `python3 -m pytest --collect-only -q` | 0 | 256 collected |
| `python3 -m pytest -q` | 0 | 256 passed |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed |
| `python3 -m pytest tests/scenarios/test_scenario_2.py -q` | 0 | 71 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 40 passed |
| `python3 -m pytest tests/domain/test_contracts.py -q` | 0 | 43 passed |
| `python3 -m pytest tests/domain/test_schema_generation.py -q` | 0 | 3 passed |
| `python3 -m compileall -q src tests` | 0 | Passed |
| `ruff check .` | 0 | No findings |
| `git diff --check` | 0 | No whitespace errors |
| `python3 -m mypy src tests` | 1 | Expected 97 diagnostics in five test files |
| `python3 -m mypy src` | 0 | No issues in 11 source files |
| `git diff --stat 716fa6b...b71aca9` | 0 | Three files, 926 insertions, 42 deletions |
| `git diff --check 716fa6b...b71aca9` | 0 | No whitespace errors |
| independent authority/metadata/product/hash harness | 0 | All positive and rejection assertions observed |
| independent eight-point atomic fault-injection harness | 0 | Exact rollback and one successful retry at every point |
| independent completed-archive trust-boundary harness | 0 | Reproduced five accepted invalid completed archives |
| isolated 16-control pytest module | 1 | Expected: all 16 deliberately invalid controls failed |
| isolated exhausted-budget branch control | 1 | Unexpected correction gap: `DID NOT RAISE` |

The full-tree Mypy result matches the documented corrective baseline. The one
diagnostic added since the first review is test-only; source remains clean.

## INDEPENDENT_REPRODUCTIONS

### Intended product path

An independent `run_scenario_2()` execution produced:

```text
state=COMPLETED
attempt_count=2
persisted REVISE decisions=1
decision append_sequence=1
REVISION_REQUESTED events=1
Attempt 1 failed_check_ids=(unit-tests,)
Attempt 1 archived StopDecision.next_action=REVISE
Attempt 1 archived StopDecision.repairable=true
Attempt 1 archived findings=1
Attempt 2 failed checks=0
owner/clarification events=0
```

### Residual R1 — exhausted-budget contradictory branch record

The independent run used an always-failing check runner:

```text
Attempt 1 fails
→ valid repairable branch and revision
→ Attempt 2 starts
→ Attempt 2 fails
→ state=CHECKING, attempt_count=2, max_attempts=2
```

At this point `_canonical_revision_metadata().allowed_actions` was `(FAIL,)`.
Calling the new public `record_repairable_failure()` nevertheless returned
success and wrote:

```text
stop_decision.next_action=REVISE
stop_decision.repairable=true
stop_decision.owner_input_required=false
unresolved_findings=1
```

`build_revision_proposal()` then failed schema validation because the selected
`REVISE` action was not in canonical allowed actions. The contradictory
canonical branch record remained. This does not create Attempt 3, but violates
Runtime decision authority and the rule that Attempt 2 is permitted only within
the bounded budget.

### Residual R2 — completed archive audit fields are not trust-boundary invariants

Starting from a genuine completed Scenario 2 record, five separately copied
Attempt 1 archives were passed through both `RunRecord.model_validate()` and
`validate_completed_record()`. Every case was accepted as `COMPLETED`:

```text
archive.stop_decision = null                              ACCEPTED
archive.findings = ()                                    ACCEPTED
archive.findings = two findings                          ACCEPTED
archive finding_id = foreign-run identity                ACCEPTED
archived observed failed check exit_code changed to 0,
  while failed_check_ids still names it                  ACCEPTED
```

`AttemptEvidence` currently checks run/attempt provenance and that derived ID
sets are subsets of observation/evaluation IDs. It does not prove the archived
StopDecision, exact canonical finding, finding/evidence binding, or that
`failed_check_ids` are actually derived from required-check exit codes.
`RunRecord` adds only parent-run binding before applying objective improvement.
Thus unsafe copied data survives the exact public completion/report boundary
that is documented as defeating `model_copy(update=...)` tampering.

## NEGATIVE_CONTROLS

Controls ran from an isolated module under `/private/tmp`, with pytest-provided
temporary artifact directories. No tracked source or test was modified. The
first 16 controls intentionally asserted the restored invalid behavior and all
failed, exit 1:

1. `test_nc01_public_checking_to_revising_bypass` — supported public path;
   `InvalidTransitionError` proves the bypass correction is load-bearing.
2. `test_nc02_record_failure_outside_checking` — supported public path;
   `InvalidTransitionError` proves state gating.
3. `test_nc03_duplicate_repairable_findings` — supported repeated call;
   observed one deterministic finding, not two, proving value-idempotence.
4. `test_nc04_missing_policy_comparison` — supported untrusted draft;
   `RevisionNotAllowed` proves policy equality is load-bearing.
5. `test_nc05_extra_allowed_action` — supported untrusted draft;
   `RevisionNotAllowed` proves exact allowed-action equality.
6. `test_nc06_false_binding_constraints` — supported untrusted draft;
   `RevisionNotAllowed` proves canonical binding comparison.
7. `test_nc07_false_budget` — supported untrusted draft;
   `RevisionNotAllowed` proves canonical budget comparison.
8. `test_nc08_foreign_run_archive` — copied trust-boundary input;
   `ValidationError` proves parent-run archive binding.
9. `test_nc09_missing_archived_stop_decision` — copied trust-boundary input;
   **failed with `DID NOT RAISE`**, reproducing residual R2.
10. `test_nc10_missing_archived_finding` — copied trust-boundary input;
    **failed with `DID NOT RAISE`**, reproducing residual R2.
11. `test_nc11_archived_finding_not_bound_to_failed_evidence` — copied
    trust-boundary input; **failed with `DID NOT RAISE`**, reproducing R2.
12. `test_nc12_archive_without_decision` — private mutation simulation; the
    invariant assertion detected the seeded archive-only state.
13. `test_nc13_decision_retained_without_archive_transition` — private mutation
    simulation; the invariant assertion detected the seeded decision-only state.
14. `test_nc14_completion_without_objective_improvement` — copied trust-boundary
    input; `ValidationError` proves the objective guard remains load-bearing.
15. `test_nc15_attempt_three_creation` — unsafe copied-state simulation; the
    bounded-attempt assertion detected `3 > 2`; supported revision commit also
    rejects exhausted budget.
16. `test_nc16_remove_hash_schema_version` — independent old-envelope digest;
    inequality proves the explicit version is load-bearing.

An additional supported public-path control,
`test_nc17_repairable_revision_record_at_exhausted_budget`, expected
`RevisionNotAllowed` but failed with `DID NOT RAISE`, reproducing residual R1.

For controls 1–8, 14 and 16, the failed invalid assertion establishes that the
correction is active. Controls 9–11 and 17 instead identify missing rejection
at current public trust/authority boundaries. Controls 12, 13 and 15 are
explicit private/unsafe simulations, not claims about supported mutation APIs.

## PUBLIC_TRANSITION_AUTHORITY_RESULTS

G2-S2-01 is `RESOLVED`.

From Attempt 1 `CHECKING`, every member of `TransitionReason` was independently
supplied to `transition(REVISING, reason)`. Every call raised
`InvalidTransitionError`; record value, attempt, history, execution events and
decision stream remained exactly unchanged. The dedicated commit then produced
one persisted decision, one revision event, one archive and Attempt 2.

`_RUNTIME_COMMITTED_STATES`, edge-first validation, and the protected-target
refusal correctly preserve both the new `REVISING` authority and the frozen
`COMPLETED` authority.

## REPAIRABLE_FAILURE_AUTHORITY_RESULTS

`record_repairable_failure()` correctly:

- rejects calls outside `CHECKING`;
- rejects a `CHECKING` attempt whose required checks pass;
- derives its output without caller-supplied decision or finding;
- leaves state, attempt, decision stream and revision-event count unchanged;
- selects the first failure in canonical required-check order; and
- is value-idempotent on repeated valid calls: one equal StopDecision and one
  equal finding, with no additional event or decision.

Authority remains `OPEN` because residual R1 shows it does not incorporate
canonical attempt-budget policy. It can write `next_action=REVISE` when
`REVISE` is not a canonical allowed action, leaving contradictory Runtime state.

## CANONICAL_DECISION_METADATA_RESULTS

G2-S2-02 is `RESOLVED`.

`build_revision_proposal()` derives policy, allowed actions, constraints and
budget solely from Runtime record/contract constants. `_prepare_revision()`
independently re-derives and compares all four for exact equality. Independent
forgeries of policy version, extra action, constraints, attempt, max, hard max
and time budget were all rejected with exact record and stream preservation.
A valid proposal committed once afterwards at sequence 1.

At exhausted budget canonical allowed actions are `(FAIL,)`; a forged REVISE
draft is rejected. Residual R1 is in the separate branch-record writer, before
proposal/commit metadata validation, and does not reopen G2-S2-02 itself.

## ARCHIVED_RUN_PROVENANCE_RESULTS

G2-S2-03 is `RESOLVED`.

`AttemptEvidence.run_id`, observation run/attempt validation, parent
`RunRecord.run_id` validation and helper validation now reject foreign checks,
patches, explicit archive IDs and copied completed records. Snapshot identity is
deterministically derived from explicit model fields. Gate 1's empty history
remains valid. Cryptographic artifact content identity is not required here.

## STOP_DECISION_AND_FINDING_RESULTS

G2-S2-04 is `OPEN`.

The intended Attempt 1 path now creates the exact required repairable
`StopDecision` and one deterministic BLOCKING finding derived from the first
failed required check in contract order. Commit-time validation correctly
rejects missing/invalid decisions; zero/multiple/foreign/wrong-attempt findings;
wrong evidence references; and incorrect severity/category/criterion through
exact canonical equality. A successful revision archives the genuine branch
result and clears it from the current Attempt 2 record.

That correction is incomplete at two required boundaries:

- the public branch writer can produce a contradictory REVISE decision at
  exhausted budget (R1); and
- completed Runtime/report validation does not preserve or validate the
  archived decision/finding/failure derivation (R2).

These are current MEDIUM correctness defects, not LOW rigidity or API-width
limitations.

## ATOMICITY_AND_ROLLBACK_RESULTS

The dedicated bounded commit is `RESOLVED`.

Eight independent injections were run:

1. repairable branch-record preparation;
2. finding derivation;
3. attempt archive construction;
4. DecisionEvent preparation;
5. candidate transition preparation;
6. decision append;
7. transition-event append; and
8. partial transition-event append followed by raise.

Every failure restored the exact prior record and both serialized stream lists.
For commit-stage failures the Attempt 1 StopDecision and finding remained in
the current branch, with no archive, decision, revision event or increment.
Every retry produced exactly one archive, one decision at `append_sequence=1`,
one `REVISION_REQUESTED` and Attempt 2. No duplicate survived.

Residual R1 does not transition or create an archive/decision, and residual R2
is a completed-record trust-boundary validation gap; neither demonstrates a
partial `_commit_revision()` operation.

## TASK_CONTRACT_HASH_RESULTS

G2-S2-05 is `RESOLVED`.

The exact hashed representation is the required envelope:

```json
{
  "schema_version": "tomtit.task_contract_hash.v1",
  "task_contract": { "...": "..." }
}
```

Independent checks confirmed stable sorted keys and separators, equal-contract
equality, semantic-change inequality, schema-version-change inequality and
inequality with the former unversioned digest. The inner contract encoding
remains pinned-Pydantic-dependent, an allowed nonblocking limitation.

## PRESERVATION_RESULTS

- Scenario 2 intended product path: `PROVEN`.
- Append-sequence authority: `RESOLVED`; caller fields remain forbidden and all
  rollback retries persist exactly sequence 1.
- Attempt progression: supported path remains bounded to Attempt 2; no Attempt
  3 is created.
- Evidence freshness: Attempt 1/2 IDs are disjoint, Attempt 1 evidence remains
  immutable, and Attempt 2 stale provenance fails closed.
- Objective improvement: `RESOLVED`; current failures are zero, satisfied
  criteria increase, overlap is rejected, and copied no-improvement completion
  still fails.
- Zero owner intervention: `RESOLVED`; `WAITING_OWNER` is never entered and
  owner/clarification events remain zero.
- Gate 1: `PRESERVED`; source/tests are unchanged and all 99 tests pass,
  including frozen completion, rollback, publication and write-once behavior.

The contradictory exhausted-budget branch record does not create Attempt 3,
so attempt progression remains resolved, while decision authority remains open.

## FINDING_STATUS

```text
G2-S2-01=RESOLVED
G2-S2-02=RESOLVED
G2-S2-03=RESOLVED
G2-S2-04=OPEN
G2-S2-05=RESOLVED

SCENARIO_2=PROVEN
DECISION_AUTHORITY=OPEN
DECISION_EVENT_ATOMICITY=RESOLVED
APPEND_SEQUENCE_AUTHORITY=RESOLVED
ATTEMPT_PROGRESSION=RESOLVED
EVIDENCE_FRESHNESS=RESOLVED
OBJECTIVE_IMPROVEMENT=RESOLVED
ZERO_OWNER_INTERVENTION=RESOLVED
GATE_1_PRESERVATION=PRESERVED

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

This narrow existential product statement is independently established. Gate
approval additionally requires no open HIGH/MEDIUM correctness finding, and
G2-S2-04 remains open.

## RISKS

| ID | Severity | Result |
|---|---|---|
| G2-S2-01 | HIGH | RESOLVED — generic transition cannot enter `REVISING` |
| G2-S2-02 | MEDIUM | RESOLVED — proposal metadata is canonically re-derived |
| G2-S2-03 | MEDIUM | RESOLVED — archived evidence is parent-run bound |
| G2-S2-04A | MEDIUM | OPEN — exhausted-budget public branch writer records contradictory REVISE decision |
| G2-S2-04B | MEDIUM | OPEN — completed trust boundary accepts missing/forged archive decision and finding |
| G2-S2-05 | LOW | RESOLVED — explicit hash schema envelope added |

Nonblocking limitations: public surface width when used inside valid budget;
rigid exact finding equality; branch-specific allowed-action derivation;
Pydantic-dependent inner hash encoding; identity-only rather than content-hash
archive binding; and test-only Mypy diagnostics.

## SCOPE_AND_NON_GOALS

Reviewed only the five corrective findings, protected transition authority,
repairable branch authority, canonical decision metadata, archive provenance,
StopDecision/Finding audit preservation, atomic archive/decision/transition,
contract-hash versioning, and prior Gate 2/Gate 1 preservation.

Not reviewed as implementation requirements: Attempt 3 implementation,
`WAITING_OWNER`, `NO_PROGRESS`, ranking/general finding engine, general policy
engine, real adapters, subprocess execution, persistence, crash recovery,
concurrency/leases, assessment storage, routing, Memory, graphs, analytics or
self-improvement.

## UNPROVEN_ITEMS

- Real-world coordination or intervention reduction.
- Real Claude Code CLI execution and provenance.
- Persistence, process-crash recovery and power-loss durability.
- Concurrent/multi-writer revision and sequence allocation.
- Archived artifact content integrity beyond run/attempt identity.
- Cross-Pydantic-version inner contract canonicalization stability.
- General repeated revision or Attempt 3 behavior.

## REVIEW_BUDGET_CONSEQUENCE

The first independent review, bounded corrective patch and this final
corrective rereview are all consumed. No additional implementation patch or
rereview is authorized by the default budget.

Because this verdict is `REQUEST_CHANGES`:

```text
GATE_2_STATUS=NOT_APPROVED
OWNER_DECISION_REQUIRED
NO_AUTOMATIC_CORRECTIVE_CYCLE
```

This review does not authorize an implementation prompt, another verifier,
Gate 2 evidence, or the next gate.

## NEXT_GATE_RECOMMENDATION

```text
GATE_2_STATUS=NOT_APPROVED
OWNER_DECISION_REQUIRED
NO_AUTOMATIC_CORRECTIVE_CYCLE
NO_NEXT_GATE_AUTHORIZED
```
