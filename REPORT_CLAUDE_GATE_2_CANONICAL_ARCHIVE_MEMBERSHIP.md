# Claude Gate 2 — Canonical Archive Membership Patch

## STATUS

```text
READY_FOR_CODEX_SOL_FINAL_GATE_2_TRUST_BOUNDARY_REVIEW
```

## STARTING_HEAD

```text
a3879b2235d88d5b43f6c37000a9609b7d4af48b
```

## SOURCE_COMMIT_HASH

```text
84bb7b0b7e332a2cd0545695ca5c16bd0cf15fe5
```

## IMPLEMENTED_DIFF_RANGE

```text
a3879b2235d88d5b43f6c37000a9609b7d4af48b..84bb7b0b7e332a2cd0545695ca5c16bd0cf15fe5
```

## REPORT_COMMIT_EXPECTED_PARENT

```text
84bb7b0b7e332a2cd0545695ca5c16bd0cf15fe5
```

## MODEL_USED

Claude Code Opus (`claude-opus-5`).

## MODEL_REASON

The patch changes completed-record validation used by the Runtime terminal-report
trust boundary. The defect was a *coordinated* forgery — archived input and
stored derived tuple faked consistently — so the fix had to move derivation onto
canonical `TaskContract` members without letting the writer and validator drift
apart.

## FILES_CHANGED

Two files, matching the allowed list exactly. `src/tomtit/runtime/runtime.py`
was **not** modified.

### `src/tomtit/runtime/state.py`

| Item | Change |
|---|---|
| `archived_check_membership_error()` (new) | Every observation must name a distinct canonical required check; also rejects duplicate check IDs and duplicate snapshot IDs. |
| `archived_criterion_membership_error()` (new) | Every archived evaluation must name a distinct canonical acceptance criterion. |
| `canonical_failed_required_check_ids()` (new) | Derives failed IDs by iterating `TaskContract.required_checks` in contract order, comparing each canonical check's exit code with its own `expected_exit_codes`. |
| `canonical_satisfied_criterion_ids()` (new) | Derives satisfied IDs by iterating `TaskContract.acceptance_criteria` in contract order. |
| `attempt_evidence_from_observations()` | Now proves check membership before deriving, uses the two canonical derivations, and accepts an optional `acceptance_criteria`. Previously it used `expected_exit_codes.get(check_id, ())`, which made an unknown check with any exit code look failed, and it sorted rather than following contract order. |
| `AttemptEvidence.validate_attempt_binding` | Additionally rejects duplicate archived criterion evaluations. |
| `revision_attempt_archive_error()` | Proves check membership, exact required-check coverage, and criterion membership **before** any derivation, then compares both derived tuples for exact equality. |
| `RunRecord.validate_runtime_snapshot` | The current-attempt derivation now also passes `acceptance_criteria`. |

The three reviewed root causes were: unknown check IDs defaulting to an empty
expected-exit-code tuple; deriving by iterating archive-controlled inputs rather
than the contract; and no contract-membership check on criterion evaluations.

### `tests/scenarios/test_scenario_2.py`

123 tests (was 103). Added the canonical-membership suite plus two- and
three-criterion contract fixtures whose canonical order deliberately differs from
alphabetical order, so ordering assertions are meaningful.

## COMMANDS_AND_EXIT_CODES

| Command | Exit code | Result |
|---|---:|---|
| `git status --short` (initial) | 0 | empty |
| `git rev-parse HEAD` (initial) | 0 | `a3879b2235d88d5b43f6c37000a9609b7d4af48b` |
| `git rev-parse HEAD^` (initial) | 0 | `d4bfe91004c7f2bf020b89aa7d92859f54742888` |
| `python3 -m pytest --collect-only -q` | 0 | `43 + 40 + 3 + 99 + 123 = 308` collected |
| `python3 -m pytest -q` | 0 | 308 passed (308 `PASSED` under `-rA`) |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed |
| `python3 -m pytest tests/scenarios/test_scenario_2.py -q` | 0 | 123 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 40 passed |
| `python3 -m pytest tests/domain/test_contracts.py -q` | 0 | 43 passed |
| `python3 -m pytest tests/domain/test_schema_generation.py -q` | 0 | 3 passed |
| `python3 -m compileall -q src tests` | 0 | passed |
| `ruff check .` | 0 | no findings |
| `git diff --check` | 0 | no whitespace errors |
| `git diff --cached --check` (pre-commit) | 0 | no whitespace errors |
| `git diff --cached --stat` (pre-commit) | 0 | 2 files, 609 insertions, 13 deletions |
| `python3 -m mypy src tests` | 1 | `Found 108 errors in 5 files`; **0 under `src/**`** |
| `python3 -m mypy src` | 0 | `Success: no issues found in 11 source files` |

Test count moved **288 → 308 (+20)**, all in `tests/scenarios/test_scenario_2.py`.
Full-tree Mypy moved 99 → 108; all nine additional diagnostics are test-only
(deliberate invalid-input construction). Source-only Mypy is clean and no
test-only diagnostic was cleaned.

## CANONICAL_REQUIRED_CHECK_MEMBERSHIP

The canonical required-check sequence is taken directly from
`TaskContract.required_checks`, in contract order. Archived observations must
satisfy all of:

```text
every observed check_id exists in TaskContract.required_checks
every observed check_id is unique
every snapshot_id is unique
archived coverage equals the canonical required-check set
existing run/attempt/snapshot authority rules still hold
```

Membership is proven **before** failure derivation, so an unknown check ID can no
longer fall through to an empty expected-exit-code tuple. Rejected: invented
check IDs, unknown IDs with passing or failing exit codes, duplicate
observations, duplicate snapshot IDs, and missing required-check coverage.

## FAILED_CHECK_DERIVATION

`canonical_failed_required_check_ids()` iterates `TaskContract.required_checks`
in contract order, resolves each canonical check's unique archived observation,
and includes the ID only when that canonical check actually failed against its
own `expected_exit_codes`. The result can therefore contain only canonical IDs,
cannot contain duplicates, and cannot be reordered by the archive.
`archive.failed_check_ids` must equal it exactly — never a subset.

`test_failed_ids_follow_canonical_contract_order` uses a contract whose order is
`(unit-tests, lint)` and asserts the derived tuple equals contract order and
differs from alphabetical order.

## CANONICAL_CRITERION_MEMBERSHIP

The canonical criterion sequence comes from `TaskContract.acceptance_criteria`.
Archived evaluations must name only canonical criteria, once each. Unknown,
foreign and duplicate criterion IDs are rejected, and `AttemptEvidence` itself
now refuses to hold duplicate evaluations at all.

## SATISFIED_CRITERION_DERIVATION

`canonical_satisfied_criterion_ids()` iterates canonical criteria in contract
order, resolves at most one archived evaluation each, and includes the ID only
when that evaluation is `SATISFIED`. `archive.satisfied_criterion_ids` must equal
it exactly.

`test_satisfied_ids_follow_canonical_contract_order` uses a three-criterion
contract ordered `(tests-pass, z-lint-clean, a-typecheck-clean)`; Attempt 1
satisfies two of them and the derived tuple is
`("z-lint-clean", "a-typecheck-clean")`, which is asserted to differ from sorted
order. Missing evaluations remain unsatisfied; no new completion requirement was
added.

## TRUST_BOUNDARY_REPRODUCTIONS

Starting from a genuine completed Scenario 2 record, each case below is rejected
by **both** `RunRecord.model_validate(...)` and `validate_completed_record(...)`:

| Case | Result |
|---|---|
| Invented failed check + matching invented `failed_check_ids` | `REJECTED_UNKNOWN_REQUIRED_CHECK` |
| Duplicated failed observation + duplicated `failed_check_ids` | `REJECTED_DUPLICATE_REQUIRED_CHECK_OBSERVATION` |
| Non-contract satisfied criterion + matching `satisfied_criterion_ids` | `REJECTED_UNKNOWN_ACCEPTANCE_CRITERION` |
| Unknown check with a passing exit code | rejected |
| Unknown criterion with a non-satisfied status | rejected |
| Duplicate canonical criterion evaluations | rejected |
| Duplicate canonical check observations (passing check) | rejected |
| Missing required-check coverage | rejected |
| Same IDs in archive-controlled check order (observations *and* tuple reordered) | rejected |
| Same IDs in archive-controlled criterion order (evaluations *and* tuple reordered) | rejected |
| Forged satisfied tuple naming an unsatisfied criterion | rejected |

The genuine completed revised record remains accepted at both boundaries with
its archive unchanged, and a Gate 1 unrevised record (`attempt_history=()`,
`attempt_count=1`) remains accepted at both boundaries. A multi-criterion
revision also completes and revalidates.

All previously closed archive rules still reject: missing/wrong StopDecision
fields, zero/duplicate/forged/foreign-run/wrong-attempt findings, a finding not
bound to the failed snapshot, passing evidence stored as failed, foreign-run
archive, non-preceding archive, and a two-archive completed record. The canonical
finding still derives from the first failed required check in canonical contract
order.

## ATOMICITY_PRESERVATION

No revision-commit behaviour was modified; `runtime.py` is untouched. The focused
atomicity tests all still pass (11 tests in the rollback/retry/append-sequence
subset), covering archive-validation failure, decision-append failure,
transition-event failure, partial event append, and retry committing exactly one
archive, one decision at `append_sequence=1`, and one `REVISION_REQUESTED` event
with no duplicate transition.

## NEGATIVE_CONTROLS

Run in isolated temporary copies; the tracked tree was never modified. Every
patch asserted its pattern matched before running.

| # | Restored defect | Invalid state accepted | Path | Failing tests |
|---:|---|---|---|---|
| 1–2 | Unknown checks default to empty expected codes and enter failure derivation | invented check treated as a genuine failure | copied trust-boundary input | 2 — `test_failed_ids_follow_canonical_contract_order`, `test_two_criterion_revision_completes_and_revalidates` |
| 3–4 | **Both** duplicate guards removed | duplicated passing observation accepted | copied trust-boundary input | 1 — `test_duplicate_passing_check_observation_is_rejected` |
| 5 | Missing required-check coverage accepted | archive omitting a required check | copied trust-boundary input | 1 — `test_missing_required_check_coverage_is_rejected` |
| 6 | Failed IDs derived in archive order | reordered observations + tuple accepted | copied trust-boundary input | 1 — `test_archive_controlled_check_ordering_is_rejected` |
| 7 | Unknown criterion IDs included | non-contract criterion accepted | copied trust-boundary input | 1 — `test_unknown_archived_criterion_evaluation_is_rejected` |
| 8 | Duplicate criterion evaluations preserved | duplicated evaluation accepted | copied trust-boundary input | 1 — `test_duplicate_archived_criterion_evaluation_is_rejected` |
| 9 | Satisfied IDs derived in archive order | reordered evaluations + tuple accepted | copied trust-boundary input | 1 — `test_archive_controlled_criterion_ordering_is_rejected` |
| 10 | `RunRecord` archive validation bypassed | every archive forgery accepted | copied trust-boundary input | 32 |
| 11 | `validate_completed_record` reconstruction bypassed | every forgery accepted at the completion gate | copied trust-boundary input | 37 |
| 12 | Exhausted-budget REVISE recording re-enabled (`runtime.py` patched in the copy) | contradictory REVISE branch written | public API | 2 — `test_record_repairable_failure_is_refused_at_the_exhausted_budget`, `test_no_revise_stop_decision_can_exist_at_the_exhausted_budget` |

**Two findings from running these controls, reported rather than hidden.**

First, controls 3 and 4 individually produced **zero** failures, because the
duplicate-check-ID guard and the duplicate-snapshot-ID guard each catch the
other's case: `snapshot_id` is a pure function of `check_id` once run and attempt
are already validated equal. Neither can be isolated alone. I added
`test_duplicate_passing_check_observation_is_rejected` — duplicating a *passing*
observation leaves both coverage and the derived tuple unchanged — which isolates
the pair, and control 3+4 with **both** guards removed now fails exactly that
test. The snapshot rule is retained because §7 requires it, but it is
structurally implied rather than independently reachable.

Second, controls 6 and 9 initially produced zero failures because my first
ordering tests permuted only the stored tuple, which contract-order derivation
rejects for a different reason. I replaced them with tests that permute the
archived **inputs and** the stored tuple together — the only shape an
archive-order derivation would accept — and both controls now fail exactly the
corresponding test.

Membership (controls 1–2) and coverage (control 5) are jointly load-bearing:
adding any unknown ID also breaks exact coverage, so the unknown-check tests are
defended twice. Control 1–2 still fails the ordering and multi-criterion tests,
which is what proves contract-iteration derivation itself is load-bearing.

## CLAIMED_FIXED_PENDING_REVIEW

```text
G2-S2-04B=CLAIMED_FIXED_PENDING_REVIEW
G2-S2-04=PENDING_FINAL_REVIEW

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

- **The writer path is exempted from criterion membership.**
  `attempt_evidence_from_observations()` takes `acceptance_criteria` optionally,
  and Runtime's writer call site does not pass it, because Runtime builds its
  evaluations by iterating the contract itself. The resulting record is still
  re-proven against the full contract during `RunRecord` validation, so the
  writer can only produce a strict subset of what the validator accepts — the
  exemption the brief permits. It would become a real gap only if some future
  writer built evaluations from a non-contract source.
- **The snapshot-uniqueness rule is structurally implied**, not independently
  reachable, as recorded above.
- **Coverage requires exact required-check equality for an archived revision
  attempt.** That is correct for Gate 2, where every attempt runs every required
  check; a future gate with conditional checks would need this revisited.
- **Ordering rules bind the archive to contract order.** Reordering
  `TaskContract.required_checks` or `acceptance_criteria` between writing and
  validating a record would invalidate an otherwise genuine archive. The contract
  hash already binds the decision to a contract version, but the archive itself
  carries no contract-version field.
- **Derivation proves consistency, not artifact content.** Nothing here hashes
  stdout/stderr or patch bytes.
- **Deferred and untouched:** FAIL terminal branch, `DecisionReasonCode`
  location, test-only Mypy diagnostics, persistence, crash recovery, concurrency,
  Attempt 3, `WAITING_OWNER`, real adapters, product metrics.

## SCOPE_AND_NON_GOALS

Changed exactly: `src/tomtit/runtime/state.py`,
`tests/scenarios/test_scenario_2.py`. Confirmed unchanged:
`src/tomtit/runtime/runtime.py`, `src/tomtit/reporting.py`,
`src/tomtit/runtime/events.py`, `src/tomtit/domain/**`, `src/tomtit/fakes.py`,
`src/tomtit/runtime/__init__.py`, `tests/scenarios/test_scenario_1.py`,
`tests/domain/**`, `docs/**`, `CLAUDE.md`, `SYSTEM.md`, and every existing audit
report. No dependency was added.

Not implemented: FAIL terminal branch, Attempt 3, `WAITING_OWNER`, `NO_PROGRESS`,
multiple-finding workflow, finding ranking, general policy engine, artifact
content hashing, persistent storage, crash recovery, concurrency, real adapter,
subprocess runner, product CLI, routing, Memory, graph orchestration. Gate 1 was
not modified and no test-only Mypy diagnostic was cleaned.

## UNPROVEN_ITEMS

- Real-world coordination value remains **not proven**; this is a fake loop.
- Exhaustiveness of the archive forgery surface is **not proven**; this patch
  closes the three reviewed reproductions and the enumerated variants.
- Artifact **content** integrity is **not proven** — only membership, derivation
  and identity consistency.
- Behaviour under a contract whose check or criterion ordering changes between
  write and validation is **not proven**.
- Persistence, crash recovery and concurrency remain **not proven** and out of
  scope.
- The terminal behaviour of an exhausted-budget failed run remains **not
  implemented** and therefore **not proven**.
- Whether these corrections satisfy Gate 2 is **claimed, not self-verified**;
  that is the reviewer's determination.

## NEXT_GATE

```text
CODEX_SOL_FINAL_GATE_2_TRUST_BOUNDARY_REVIEW
NO_ADDITIONAL_IMPLEMENTATION_PATCH_AUTHORIZED
NO_NEXT_GATE_AUTHORIZED
```
