# Final Independent Gate 2 Trusted TaskContract Binding Review

## VERDICT

`APPROVE_WITH_NON_BLOCKING_FINDINGS`

The original Runtime-owned TaskContract binding is preserved independently of the mutable record, is required at revised-record and completion boundaries, and is used by the Runtime-owned report-publication path. The coordinated reordered-contract substitution is rejected without state, stream, or artifact side effects. No current BLOCKER, HIGH, or MEDIUM defect was reproduced inside the bounded Gate 2 in-memory Runtime/report threat model.

## TARGET_SOURCE_COMMIT

`4730e459b0cf226db415a08caf701768b18c8171`

## REVIEWED_DIFF_RANGE

`52146f9008ab55f501b2e52becc3fdf9d3c76ff8..4730e459b0cf226db415a08caf701768b18c8171`

## REVIEW_REPORT_EXPECTED_PARENT

`f446a59d56ef2438331523b4c2d8684f75da1786`

Preflight matched the required lineage: branch `main`, HEAD at the expected implementation-report commit, HEAD parent at the target source commit, target source commit an ancestor of HEAD, and no tracked, indexed, or untracked project changes. Ignored entries were limited to `.DS_Store`, caches, and bytecode.

## FILES_REVIEWED

Required authority and policy material:

- `CLAUDE.md`
- `SYSTEM.md`
- `docs/OFFICIAL_BUILD_AND_AI_CODING_PROCESS_V1.md`
- `docs/EVALUATION_CONTRACT_V1.md`
- `docs/VERIFIER_POLICY_V1.md`
- `docs/MVP_EXECUTION_POLICY_V0.md`
- `docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md`
- `docs/gate-evidence/GATE_1_SCENARIO_1.md`
- `REPORT_CODEX_SOL_FINAL_GATE_2_TRUST_BOUNDARY_REVIEW.md`
- `REPORT_CLAUDE_GATE_2_TRUSTED_CONTRACT_BINDING.md`
- `REPORT_CLAUDE_GATE_2_CANONICAL_ARCHIVE_MEMBERSHIP.md`

Corrective source and test diff:

- `src/tomtit/runtime/state.py`
- `src/tomtit/runtime/runtime.py`
- `src/tomtit/reporting.py`
- `tests/scenarios/test_scenario_2.py`

Production call sites for `RunRecord(`, `RunRecord.model_validate(`, `_build_record(`, and `_replace_record(` were inspected directly. The corrective commit changes exactly the four files above (450 insertions, 26 deletions). `src/tomtit/runtime/events.py`, `src/tomtit/domain/**`, `src/tomtit/fakes.py`, Scenario 1 tests, domain tests, docs, `CLAUDE.md`, and `SYSTEM.md` are unchanged in the source commit.

## COMMANDS_AND_EXIT_CODES

| Command | Exit | Result |
|---|---:|---|
| `python3 -m pytest --collect-only -q` | 0 | 322 collected |
| `python3 -m pytest -q` | 0 | 322 passed |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed |
| `python3 -m pytest tests/scenarios/test_scenario_2.py -q` | 0 | 137 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 40 passed |
| `python3 -m pytest tests/domain/test_contracts.py -q` | 0 | 43 passed |
| `python3 -m pytest tests/domain/test_schema_generation.py -q` | 0 | 3 passed |
| `python3 -m compileall -q src tests` | 0 | pass |
| `ruff check .` | 0 | no findings |
| `git diff --check` | 0 | clean |
| `python3 -m mypy src tests` | 1 | expected 108 test-only diagnostics in 5 test files |
| `python3 -m mypy src` | 0 | no issues in 11 source files |
| `git diff --stat 52146f9..4730e45` | 0 | four expected files; 450 insertions, 26 deletions |
| `git diff --check 52146f9..4730e45` | 0 | clean |
| focused Gate 2 binding/archive/budget regression selection | 0 | 28 passed |
| independent contract-binding probe | 0 | all expected accept/reject and preservation assertions passed |
| focused atomic rollback/retry probe | 0 | all 7 failure points rolled back and retried correctly |
| 12 isolated weakened-condition negative controls | 1 | 12 failed as required |

The full-tree Mypy result exactly matches the documented baseline. Source Mypy remains clean; this is a deferred LOW test-typing issue, not a Gate 2 correctness failure.

## INDEPENDENT_REPRODUCTIONS

An independent temporary probe constructed a genuine completed Scenario 2 record with two required checks and two acceptance criteria, then made a coordinated forged copy that reordered both collections and recomputed `failed_check_ids`, `satisfied_criterion_ids`, and the canonical Finding consistently.

With the original Runtime hash, model validation, completed-record validation, and report building each rejected the forged record with `ValidationError`. The live Runtime completion path also rejected it. The record identity and value, canonical binding, execution stream, and decision stream remained unchanged; `RUN_COMPLETED=0`, `REPORT_GENERATED=0`, and no report existed.

The genuine revised record passed all three boundaries. Unrevised Gate 1 model, completion, and report paths passed without a binding. Explicitly changing the low-level authority to the replacement hash allowed generic model, completion, and report validation, as permitted by the specified threat-model distinction.

## NEGATIVE_CONTROLS

All controls were created only in `/private/tmp`; the tracked review tree was not changed. Each test restores or simulates a weakened condition and is intentionally expected to fail against the current guard.

| Control | Restored invalid condition / path | Exact failing test | Load-bearing guard demonstrated |
|---:|---|---|---|
| 1 | Revised model accepts missing context; public model API | `test_nc01_revised_model_accepts_missing_context` | revised snapshots fail closed without trusted context |
| 2 | Completion derives authority from embedded contract; public completion API | `test_nc02_completed_derives_hash_from_embedded_contract` | completion does not infer authority from caller-controlled payload |
| 3 | Runtime binding follows the current record; copied/private simulation | `test_nc03_runtime_hash_recomputed_from_current_record` | binding is captured independently at Runtime creation |
| 4 | `_build_record` trusts candidate hash; private canonical-construction simulation on a revised record | `test_nc04_build_record_validates_against_candidate_hash` | `_build_record` supplies the Runtime-captured hash |
| 5 | Revised completion omits binding; public completion API | `test_nc05_runtime_completion_omits_binding` | revised completion requires explicit external authority |
| 6 | Publication trusts a record-selected replacement authority; public low-level API simulation | `test_nc06_report_publication_uses_record_hash` | generic explicit authority can validate relative to itself, but the substitution remains observable and Runtime never selects it |
| 7 | Decision hash is not compared with Runtime binding; supported Runtime decision path | `test_nc07_decision_hash_not_compared_with_runtime_binding` | `_prepare_revision` rejects the inconsistent proposal before persistence |
| 8 | Coordinated reordered-contract rewrite accepted; public model API | `test_nc08_reordered_contract_rewrite_accepted` | external original hash detects the internally consistent replacement |
| 9 | Canonical membership validation bypassed; unsafe copied-model simulation | `test_nc09_membership_validation_bypassed` | invented membership is observable; canonical validation tests prove public boundaries reject it |
| 10 | Public CHECKING-to-REVISING transition re-enabled; unsafe private-record simulation | `test_nc10_public_checking_to_revising_reenabled` | bypassed private mutation exposes the forbidden state; supported public transition remains unavailable |
| 11 | Exhausted-budget REVISE recording re-enabled; unsafe private-record simulation | `test_nc11_exhausted_budget_revise_recording_reenabled` | bypassed mutation exposes the forbidden decision; supported recording path rejects and preserves streams |
| 12 | Contract-hash schema version removed; copied hash simulation | `test_nc12_contract_hash_schema_version_removed` | the production hash is domain-separated/versioned |

Result: `12 failed in 0.52s`, which is the expected negative-control outcome. Controls 9-11 deliberately show that unsafe private copying can manufacture invalid objects; the supported validated paths and focused public-path tests make those restored mutations unreachable.

## RUNTIME_CANONICAL_BINDING_RESULTS

`Runtime.__init__` computes `task_contract_hash(task_contract)` before constructing the canonical record and stores it in `_canonical_task_contract_hash`. The read-only `canonical_task_contract_hash` property has no setter. The value is not recomputed from `RunRecord.task_contract`, and remained stable through Attempt 1, revision, Attempt 2, completion, record replacement attempts, and report generation.

`_build_record` validates with `trusted_contract_context(self._canonical_task_contract_hash)`. `_prepare_revision` separately compares both the proposal hash and embedded record-contract hash to the captured binding before persistence. Replacing the embedded record contract did not replace the Runtime binding.

`RUNTIME_VALIDATION_AUTHORITY=ORIGINAL_RUN_BINDING`

## RUNRECORD_CONTEXT_VALIDATION_RESULTS

For non-empty `attempt_history`, `RunRecord.validate_runtime_snapshot` requires `expected_task_contract_hash` from Pydantic validation context and compares it with `task_contract_hash(record.task_contract)`:

- missing context: rejected;
- mismatched original context: rejected;
- matching original Runtime context: accepted for genuine records;
- replacement hash: accepted only as explicitly caller-designated generic authority;
- empty-history Gate 1 records: accepted without context.

The expected value is not sourced from any record field. Direct construction/revalidation of a revised record without context fails closed, an intentional nonblocking API behavior.

## COMPLETED_RECORD_BINDING_RESULTS

`validate_completed_record` creates trusted context only from its explicit `expected_task_contract_hash` argument and forwards that context into detached `RunRecord.model_validate`. It never derives the authority from the record under validation.

For revised records, missing and mismatched bindings reject; the original Runtime binding accepts a genuine completion. Gate 1 unrevised completion remains valid without a binding.

## REPORT_PUBLICATION_BINDING_RESULTS

Runtime completion constructs the candidate through `_build_record`, which has already validated it against the original Runtime binding, then uses the private already-validated report builder and revalidates completion with the same captured binding before commit. Report staging is removed on failure.

The live reordered-contract attack generated neither `RUN_COMPLETED` nor `REPORT_GENERATED`, published no report, and left the canonical record, binding, and both streams unchanged. Genuine revised and Gate 1 reports still publish.

The public `build_terminal_report` accepts an explicitly caller-designated optional authority. Missing authority fails closed for revised records; a caller supplying a replacement hash validates relative to its own authority. Runtime does not expose or use such caller substitution on its completion path.

## DECISION_EVENT_HASH_CONSISTENCY_RESULTS

The reproduced genuine flow established:

`Runtime canonical hash == persisted revision DecisionEvent.task_contract_hash == expected revised-completion hash`.

A proposal hash differing from the Runtime binding and an embedded record contract differing from the Runtime binding both reject in `_prepare_revision`. No DecisionEvent or transition persists after the failed check. The decision hash for the genuine flow equals the original binding.

## REORDERED_CONTRACT_ATTACK_RESULTS

`REJECTED` at every required original-authority boundary:

- `RunRecord.model_validate(forged_payload, context=trusted_contract_context(original_hash))`;
- `validate_completed_record(forged_record, expected_task_contract_hash=original_hash)`;
- `build_terminal_report(forged_record, expected_task_contract_hash=original_hash)`;
- supported live Runtime completion/publication.

The attack remained internally self-consistent relative to its replacement contract, demonstrating that rejection is caused by the external original-run binding rather than only archive derivation checks.

## GENERIC_AUTHORITY_BOUNDARY_RESULTS

`GENERIC_VALIDATION_AUTHORITY=CALLER_DESIGNATED_BY_API`

`RUNTIME_VALIDATION_AUTHORITY=ORIGINAL_RUN_BINDING`

The generic low-level APIs intentionally validate relative to the authority explicitly supplied by their caller. Supplying `task_contract_hash(replacement_contract)` accepted the coordinated replacement at generic model, completion, and report boundaries. This is not a Gate 2 defect because the supported Runtime and Runtime-owned publication path always supply the independently captured original hash.

## ARCHIVE_TRUST_PRESERVATION_RESULTS

Canonical required-check membership, uniqueness, coverage, ordering-based derivation, failed-check derivation, criterion membership, uniqueness, satisfied-criterion derivation, StopDecision validation, canonical Finding re-derivation, foreign-run rejection, attempt provenance, and the bounded one-archive Scenario 2 shape remain enforced.

The earlier coordinated invented failed check, duplicated failed observation, and non-contract satisfied criterion forgeries reject at both `RunRecord.model_validate` and `validate_completed_record` when supplied the original Runtime binding. The focused regression selection passed all 28 selected tests.

At exhausted `max_attempts=2`, canonical allowed actions remain `(FAIL,)`; repairable-failure/REVISE recording rejects, produces no new Finding or Attempt 3, and preserves record and streams. The absent FAIL terminal implementation remains explicitly out of scope.

## ATOMICITY_AND_ROLLBACK_RESULTS

The independent focused probe injected all seven required failures:

- archive semantic validation;
- archive construction;
- DecisionEvent preparation;
- candidate-transition preparation;
- decision append;
- transition-event append;
- partial transition-event append.

Every failed operation restored the exact record and both streams: no archive, persisted decision, revision event, or attempt increment was retained, and the Attempt 1 StopDecision/Finding remained. Every retry produced exactly one archive, one DecisionEvent with `append_sequence=1`, one `REVISION_REQUESTED`, Attempt 2, and no duplicate transition.

## GATE_1_PRESERVATION_RESULTS

Scenario 1 source and tests are unchanged. All 99 Scenario 1 tests pass. Unrevised completed records with empty history require no contract-binding context, and independent Gate 1 model, completion-validation, and report-publication probes all accepted the genuine record.

`GATE_1_PRESERVATION=PRESERVED`

## FINDING_STATUS

```text
G2-S2-04B=RESOLVED
G2-S2-04=RESOLVED
CONTRACT_ORDER_BINDING=RESOLVED

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

GENERIC_VALIDATION_AUTHORITY=CALLER_DESIGNATED_BY_API
RUNTIME_VALIDATION_AUTHORITY=ORIGINAL_RUN_BINDING

EXHAUSTED_FAIL_TERMINAL_BRANCH=NOT_IMPLEMENTED_OUT_OF_SCOPE
PERSISTENT_CONTRACT_REGISTRY=OUT_OF_SCOPE
CROSS_PROCESS_BINDING=OUT_OF_SCOPE
CRYPTOGRAPHIC_AUTHENTICATION=OUT_OF_SCOPE
ARTIFACT_CONTENT_INTEGRITY=OUT_OF_SCOPE
TEST_ONLY_MYPY=DEFERRED_LOW
REAL_WORLD_COORDINATION_VALUE=NOT_PROVEN
```

## PRODUCT_EVIDENCE_ASSESSMENT

`PROVEN` in the controlled fake loop:

> The Runtime can perform one bounded revision from deterministic failure to successful completion without owner input in the controlled fake loop.

The validated path is Attempt 1 deterministic failure, repairable StopDecision plus canonical Finding, Runtime-owned REVISE decision, atomic archive/DecisionEvent/transition, fresh Attempt 2 evidence, objective improvement, COMPLETED, report publication, and zero owner intervention.

This does not prove real-world coordination reduction.

## RISKS

Only LOW or explicitly out-of-scope limitations remain:

- generic low-level validation authority is caller-designated by API;
- the Runtime binding is in-memory and per Runtime instance;
- there is no persistent registry, restart/cross-process reconstruction, or cryptographic authentication;
- direct revised `RunRecord(...)` construction fails closed;
- public `build_terminal_report` has an optional binding parameter and revised calls without it fail closed;
- the FAIL terminal branch is absent;
- artifact contents are not hashed;
- 108 documented test-only Mypy diagnostics remain, while production source is clean.

None is a current MEDIUM-or-higher defect under the authorized bounded threat model.

## SCOPE_AND_NON_GOALS

This review is limited to Runtime-owned contract binding, revised validation context, completed-record and Runtime publication binding, DecisionEvent hash consistency, reordered-contract substitution, preservation of existing Gate 2 findings, and Gate 1 preservation.

No persistence, registry, migration, cross-process recovery, signature/authentication, artifact-content hashing, FAIL terminal, Attempt 3, WAITING_OWNER, NO_PROGRESS, real adapter, subprocess runner, product CLI, concurrency, crash recovery, routing, Memory, graph orchestration, analytics, or self-improvement claim was reviewed or required.

## UNPROVEN_ITEMS

- Real-world coordination value or reduction.
- Security outside the bounded in-memory Runtime instance.
- Persistent/restart/cross-process contract identity.
- Cryptographic provenance or external-caller authentication.
- Artifact content integrity.
- Production adapter, subprocess, CLI, concurrency, and crash-recovery behavior.

## REVIEW_BUDGET_CONSEQUENCE

The reviewed substitution defect is closed, the bounded Gate 2 evidence is proven, and no current BLOCKER, HIGH, or MEDIUM finding remains. The review budget is complete. No further Gate 2 correctness review or corrective patch is warranted for the listed LOW/out-of-scope limitations.

Approval authorizes the designated evidence-persistence step only. This review does not create or modify `docs/gate-evidence/GATE_2_SCENARIO_2.md`.

## NEXT_GATE_RECOMMENDATION

```text
GATE_2_STATUS=APPROVED
SCENARIO_2=FROZEN
NEXT_ACTION=PERSIST_GATE_2_EVIDENCE
NEXT_GATE_AFTER_PERSISTENCE=CLAUDE_CODE_CLI_CAPABILITY_SPIKE
NO_FURTHER_GATE_2_REVIEW
```
