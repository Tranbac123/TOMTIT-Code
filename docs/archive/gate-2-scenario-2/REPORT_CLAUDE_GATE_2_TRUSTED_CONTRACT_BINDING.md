# Claude Gate 2 — Trusted TaskContract Binding Patch

## STATUS

```text
READY_FOR_CODEX_SOL_FINAL_CONTRACT_BINDING_REVIEW
```

## STARTING_HEAD

```text
52146f9008ab55f501b2e52becc3fdf9d3c76ff8
```

## SOURCE_COMMIT_HASH

```text
4730e459b0cf226db415a08caf701768b18c8171
```

## IMPLEMENTED_DIFF_RANGE

```text
52146f9008ab55f501b2e52becc3fdf9d3c76ff8..4730e459b0cf226db415a08caf701768b18c8171
```

## REPORT_COMMIT_EXPECTED_PARENT

```text
4730e459b0cf226db415a08caf701768b18c8171
```

## MODEL_USED

Claude Code Opus (`claude-opus-5`).

## MODEL_REASON

The patch changes public completed-record validation and report publication
trust boundaries. The defect could not be closed inside the record payload at
all: every field an attacker controls was already internally consistent, so the
fix had to introduce an authority anchor outside the payload and thread it
through model validation, the completion gate, the report boundary and Runtime.

## FILES_CHANGED

### `src/tomtit/runtime/state.py`

| Item | Change |
|---|---|
| `TRUSTED_CONTRACT_HASH_CONTEXT_KEY` (new) | Validation-context key carrying the trusted hash. |
| `trusted_contract_context()` (new) | Builds the context a revised record must be validated against. |
| `RunRecord.validate_runtime_snapshot` | Now takes `info: ValidationInfo`. When `attempt_history` is non-empty it requires a trusted hash from context and compares it with `task_contract_hash(self.task_contract)`. Missing context or mismatch fails closed. Records with an empty history are untouched. |
| `validate_completed_record()` | New keyword-only `expected_task_contract_hash: str | None = None`, forwarded as validation context during reconstruction. |

### `src/tomtit/runtime/runtime.py`

| Item | Change |
|---|---|
| `Runtime.__init__` | Captures `self._canonical_task_contract_hash = task_contract_hash(task_contract)` from the **original** contract, before any record can be copied. |
| `Runtime.canonical_task_contract_hash` (new property) | Read-only exposure of that binding. |
| `_build_record()` | Now uses `RunRecord.model_validate(values, context=trusted_contract_context(self._canonical_task_contract_hash))` instead of `RunRecord(**values)`. Pydantic cannot carry context through direct construction, so this is what lets Runtime build genuine revised records while still failing closed on a swap. |
| `_prepare_completion()` | Passes the Runtime binding into `validate_completed_record`. |
| `_prepare_revision()` | Compares the proposal's `task_contract_hash` with the **Runtime binding** rather than with the record's own contract, and separately requires the record's embedded contract to still hash to that binding. |

### `src/tomtit/reporting.py`

`build_terminal_report()` gains `expected_task_contract_hash: str | None = None`
and forwards it to `validate_completed_record`. The private
already-validated builder is unchanged.

### `tests/scenarios/test_scenario_2.py`

137 tests (was 123). Added the binding suite, a `trusted_hash()` helper, and a
`coordinated_contract_swap()` forgery builder that rewrites every order-derived
archive field so the copy is internally consistent. Existing revised-record
assertions now supply the trusted binding explicitly — that is the new
fail-closed contract, not a weakening.

## COMMANDS_AND_EXIT_CODES

| Command | Exit code | Result |
|---|---:|---|
| `git status --short` (initial) | 0 | empty |
| `git rev-parse HEAD` (initial) | 0 | `52146f9008ab55f501b2e52becc3fdf9d3c76ff8` |
| `git rev-parse HEAD^` (initial) | 0 | `a625ca755ef2eeb3683cbaab3508f6ec71ceb603` |
| `python3 -m pytest --collect-only -q` | 0 | `43 + 40 + 3 + 99 + 137 = 322` collected |
| `python3 -m pytest -q` | 0 | 322 passed |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed |
| `python3 -m pytest tests/scenarios/test_scenario_2.py -q` | 0 | 137 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 40 passed |
| `python3 -m pytest tests/domain/test_contracts.py -q` | 0 | 43 passed |
| `python3 -m pytest tests/domain/test_schema_generation.py -q` | 0 | 3 passed |
| `python3 -m compileall -q src tests` | 0 | passed |
| `ruff check .` | 0 | no findings |
| `git diff --check` | 0 | no whitespace errors |
| `git diff --cached --check` (pre-commit) | 0 | no whitespace errors |
| `git diff --cached --stat` (pre-commit) | 0 | 4 files, 450 insertions, 26 deletions |
| `python3 -m mypy src tests` | 1 | `Found 108 errors in 5 files`; **0 under `src/**`** |
| `python3 -m mypy src` | 0 | `Success: no issues found in 11 source files` |

Test count moved **308 → 322 (+14)**, all in `tests/scenarios/test_scenario_2.py`.
Full-tree Mypy is unchanged at 108, all test-only; source-only Mypy is clean.

## RUNTIME_CANONICAL_CONTRACT_BINDING

`Runtime.__init__` computes the binding from the original `TaskContract`
argument — not from `self._record` — and stores it privately. It is never
recomputed. `test_runtime_canonical_binding_is_immutable_across_transitions`
drives Attempt 1 failure → revision → Attempt 2 → completion and asserts the
value is identical at every step.

## RUNRECORD_VALIDATION_CONTEXT

A record with a non-empty `attempt_history` requires
`context={"expected_task_contract_hash": ...}`:

| Case | Result |
|---|---|
| No context | `REJECTED_MISSING_TRUSTED_BINDING` — *"a revised run record must be validated against a trusted expected_task_contract_hash"* |
| Mismatched hash | rejected — *"the embedded TaskContract does not match the canonical contract this run is bound to"* |
| Genuine record + Runtime binding | accepted |

Because Pydantic cannot pass context through `Model(**values)`, direct
construction of a revised record has no context and therefore fails closed by
default. Runtime is the only producer that supplies one, via `_build_record`.

## COMPLETED_RECORD_BINDING

`validate_completed_record(record, *, expected_task_contract_hash=None)`
forwards the value as context. Revised records require it; unrevised Gate 1
records behave exactly as before. The expected hash is never derived from
`record.task_contract`, a record-provided field, the report payload, or any
archive field.

## REPORT_PUBLICATION_BINDING

Runtime's completion path passes its own binding into the completion gate before
the report is prepared, so a swapped-contract record cannot reach publication.
The public `build_terminal_report()` requires the caller to supply the binding
for a revised record: without it the call is refused, and with the original
binding a reordered-contract forgery is refused.
`test_report_boundary_accepts_the_genuine_record_with_its_binding` confirms the
genuine path still produces a report.

## DECISION_EVENT_HASH_CONSISTENCY

Verified equal in `test_persisted_decision_hash_equals_the_runtime_binding`:

```text
Runtime canonical binding
== PersistedDecisionEvent.task_contract_hash
== expected hash used by revised completion validation
```

`_prepare_revision` now compares the proposal against the Runtime binding, and
independently requires the record's embedded contract to hash to it, so a
mid-run contract swap fails closed before any decision is persisted
(`test_revision_proposal_is_rejected_when_the_record_contract_was_swapped`).

## REORDERED_CONTRACT_REPRODUCTION

`coordinated_contract_swap()` reproduces the reviewed attack exactly: reversed
`required_checks` and `acceptance_criteria`, with `failed_check_ids`,
`satisfied_criterion_ids` and the canonical `Finding` all recomputed against the
replacement so the copy is internally consistent. Judged against the original
binding:

```text
RunRecord.model_validate(forged, context={"expected_task_contract_hash": original})  REJECTED
validate_completed_record(forged, expected_task_contract_hash=original)              REJECTED
build_terminal_report(forged, expected_task_contract_hash=original)                  REJECTED
build_terminal_report(forged)  (no binding)                                          REJECTED
```

**On §14.3 (forged replacement hash).** A caller who supplies the replacement
contract's own hash as the trusted input will validate relative to that
authority — that is what "explicitly supplied authority" means, and the tests do
not claim otherwise. What matters is that the Runtime and report paths never
select such a hash: `test_runtime_completion_cannot_be_bound_to_a_swapped_contract`
performs the swap on a live Runtime and shows the supported completion path
refuses it, leaving the canonical record unchanged, no `RUN_COMPLETED`, no
`REPORT_GENERATED`, no report file, and the Runtime binding untouched. The suite
therefore distinguishes generic validation-against-supplied-authority from
Runtime validation-against-run-authority.

## GATE_1_PRESERVATION

99 Scenario 1 tests pass; no Gate 1 source or test was modified. An unrevised
completed record still validates, still passes the completion gate, and still
builds a report with **no** binding argument
(`test_gate_1_unrevised_record_needs_no_contract_binding`).

## ATOMICITY_PRESERVATION

No revision-commit ordering changed. The focused rollback/retry subset passes (7
tests), and the canonical-membership forgery subset passes (14 tests). All three
earlier coordinated forgeries — invented check, duplicated check, non-contract
satisfied criterion — remain rejected, along with membership, uniqueness,
coverage, derivation ordering, StopDecision, Finding, foreign-run and bounded
one-archive rules.

## NEGATIVE_CONTROLS

Isolated temporary copies only; the tracked tree was never modified. Each patch
asserted its pattern matched.

| # | Restored condition | Path | Failing tests |
|---:|---|---|---|
| 1 | Revised validation falls back to the embedded contract instead of requiring context | copied trust-boundary input | 3 — missing-binding (model), missing-binding (completion), report-boundary rejection |
| 2 | Completed validation derives the expected hash from the embedded contract | copied trust-boundary input | 2 — missing-binding (completion), report-boundary rejection |
| 3 | Runtime completion uses the record-provided hash | public path | **0** — see note below |
| 4 | Runtime completion omits the expected hash entirely | public path | 66 — the entire revised suite fails closed |
| 5 | Runtime canonical hash recomputed from the current replaceable record | public path | 1 — `test_runtime_completion_cannot_be_bound_to_a_swapped_contract` |
| 6 | DecisionEvent hash not compared with the Runtime binding | public path | 2 — proposal hash binding, swapped-contract proposal |
| 7 | Embedded-hash comparison removed (reordered contract accepted) | copied trust-boundary input | 5 — including the reordered-contract reproduction and the live-Runtime anti-swap |
| 8 | Canonical membership validation bypassed | copied trust-boundary input | 1 — coverage |
| 9 | Public `CHECKING → REVISING` protection removed | public path | 4 |
| 10 | Exhausted-budget REVISE recording restored | public path | 2 |

**Control 3 produced zero failures, and the reason is worth stating.** With the
completion gate using the candidate's own hash, the swap is still refused —
`_build_record` already validates every constructed record against the Runtime
binding, so a swapped revised record cannot be built at all. I verified this
directly rather than assuming: under control 3 the swap still raises *"the
embedded TaskContract does not match the canonical contract this run is bound
to"*, from `_build_record`. The internal completion gate's hash argument is
therefore defense in depth behind `_build_record`, not the sole guard. The
equivalent guard at the **public** boundary is genuinely load-bearing and is
covered by control 2. Control 4 confirms that removing the binding from the
Runtime path entirely collapses 66 tests.

## CLAIMED_FIXED_PENDING_REVIEW

```text
G2-S2-04B=CLAIMED_FIXED_PENDING_REVIEW
G2-S2-04=PENDING_FINAL_REVIEW
CONTRACT_ORDER_BINDING=CLAIMED_RESOLVED_PENDING_REVIEW

G2-S2-04A=PRESERVED_PENDING_REVIEW
G2-S2-01=PRESERVED_PENDING_REVIEW
G2-S2-02=PRESERVED_PENDING_REVIEW
G2-S2-03=PRESERVED_PENDING_REVIEW
G2-S2-05=PRESERVED_PENDING_REVIEW

SCENARIO_2=PRESERVED_PENDING_REVIEW
DECISION_EVENT_ATOMICITY=PRESERVED_PENDING_REVIEW
APPEND_SEQUENCE_AUTHORITY=PRESERVED_PENDING_REVIEW
ATTEMPT_PROGRESSION=PRESERVED_PENDING_REVIEW
EVIDENCE_FRESHNESS=PRESERVED_PENDING_REVIEW
OBJECTIVE_IMPROVEMENT=PRESERVED_PENDING_REVIEW
ZERO_OWNER_INTERVENTION=PRESERVED_PENDING_REVIEW
GATE_1=PRESERVED_PENDING_REVIEW
```

## RISKS

- **The binding is only as trustworthy as the caller supplying it.** For an
  external caller, `expected_task_contract_hash` is caller-designated authority
  by construction. The security property this patch establishes is that Runtime
  and the report path always supply the run's own captured hash and never a
  value taken from the record. A caller who chooses to trust a forged contract
  is validating against a different authority, which the tests state explicitly
  rather than hide.
- **`_build_record` now runs `model_validate` instead of direct construction**
  on every canonical write. Behaviour is equivalent for Gate 2 volumes, but it
  is a hotter path than `RunRecord(**values)`.
- **Direct `RunRecord(...)` construction of a revised record is now impossible**
  without going through `model_validate` with context. That is deliberate
  fail-closed behaviour, but it is a real API change for any future caller.
- **`build_terminal_report`'s new parameter defaults to `None`**, so an existing
  caller that forgets it gets a clear rejection for revised records rather than
  a silent bypass — but it is a signature change on a public function.
- **The binding is in-memory and per-Runtime-instance.** There is no persistence,
  no registry and no signature; a new Runtime for the same run would recompute
  from whatever contract it was given.
- **Deferred and untouched:** FAIL terminal branch, `DecisionReasonCode`
  location, test-only Mypy diagnostics, persistence, crash recovery,
  concurrency, Attempt 3, `WAITING_OWNER`, real adapters, product metrics.

## SCOPE_AND_NON_GOALS

Changed exactly: `src/tomtit/runtime/state.py`, `src/tomtit/runtime/runtime.py`,
`src/tomtit/reporting.py`, `tests/scenarios/test_scenario_2.py`. Confirmed
unchanged: `src/tomtit/runtime/events.py`, `src/tomtit/domain/**`,
`src/tomtit/fakes.py`, `tests/scenarios/test_scenario_1.py`, `tests/domain/**`,
`docs/**`, `CLAUDE.md`, `SYSTEM.md`, and every existing audit report. No
dependency was added.

Not implemented: contract registry, contract migration, persistent run store,
signatures or cryptographic authentication, artifact content hashing, FAIL
terminal branch, Attempt 3, `WAITING_OWNER`, `NO_PROGRESS`, real adapter,
subprocess runner, product CLI, concurrency, crash recovery, routing, Memory,
graph orchestration. Gate 1 source and tests were not modified and no test-only
Mypy diagnostic was cleaned.

## UNPROVEN_ITEMS

- Real-world coordination value remains **not proven**; this is a fake loop.
- The binding is **not cryptographically authenticated**; it is a hash comparison
  against an in-process value, which is sufficient for the bounded in-memory Gate
  2 threat model and nothing more.
- Behaviour across process restarts or multiple Runtime instances for one run is
  **not proven** and out of scope.
- Exhaustiveness of the contract-substitution surface is **not proven**; this
  closes the reviewed reordering attack and the enumerated variants.
- Artifact **content** integrity remains **not proven**.
- The exhausted-budget terminal branch remains **not implemented**.
- Whether these corrections satisfy Gate 2 is **claimed, not self-verified**;
  that is the reviewer's determination.

## NEXT_GATE

```text
CODEX_SOL_FINAL_CONTRACT_BINDING_REVIEW
NO_ADDITIONAL_IMPLEMENTATION_PATCH_AUTHORIZED
NO_NEXT_GATE_AUTHORIZED
```
