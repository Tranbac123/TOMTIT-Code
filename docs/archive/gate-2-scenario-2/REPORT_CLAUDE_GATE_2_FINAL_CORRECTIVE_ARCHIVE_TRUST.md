# Claude Gate 2 Final Corrective Patch — Archive Trust and Budget Authority

## STATUS

```text
READY_FOR_CODEX_SOL_OWNER_AUTHORIZED_FINAL_GATE_2_REREVIEW
```

## STARTING_HEAD

```text
bf7abaf2684d5792d72e0320e47132b24cae27f5
```

## SOURCE_COMMIT_HASH

```text
eb2d87df4c2200294c016d1a1e629a46e8231c4a
```

## IMPLEMENTED_DIFF_RANGE

```text
bf7abaf2684d5792d72e0320e47132b24cae27f5..eb2d87df4c2200294c016d1a1e629a46e8231c4a
```

## REPORT_COMMIT_EXPECTED_PARENT

```text
eb2d87df4c2200294c016d1a1e629a46e8231c4a
```

## MODEL_USED

Claude Code Opus (`claude-opus-5`).

## MODEL_REASON

The patch changes Runtime decision authority (which branch result may be written
at all) and the completed-record validation used by the Runtime terminal-report
trust boundary. Both are canonical-state authority surfaces, and the archive
validator had to be placed so a single definition serves model validation,
completed-state validation and the reporting boundary's full reconstruction.

## FILES_CHANGED

Three files, matching the allowed list exactly.

### `src/tomtit/runtime/state.py`

| Item | Change |
|---|---|
| `acceptance_criterion_for_check()` (new) | The first acceptance criterion requiring a check, in contract order. Extracted so Runtime and the archive validator share one derivation. |
| `canonical_repairable_branch_finding()` (new) | Single derivation of the one repairable finding a failed attempt must carry, from contract + run + attempt + archived observations. |
| `repairable_stop_decision_error()` (new) | Single predicate for the policy-required repairable branch: `next_action=REVISE`, `repairable=true`, `owner_input_required=false`, null outcome, null stop reason. |
| `revision_attempt_archive_error()` (new) | **The shared archive validator.** Re-proves identity, observation binding, exact `failed_check_ids` and `satisfied_criterion_ids` derivation, the StopDecision, and the single canonical Finding. Returns a reason string or `None`. |
| `RunRecord.validate_runtime_snapshot` | Replaces the previous parent-run-only check with a call to `revision_attempt_archive_error()` for **every** archived attempt, at every construction and reconstruction. Adds the bounded Gate 2 shape (exactly one archive, Attempt 1 archived, Attempt 2 current) for a revised **completed** record only. |

### `src/tomtit/runtime/runtime.py`

| Item | Change |
|---|---|
| `record_repairable_failure()` | Now derives canonical revision metadata **before writing anything** and refuses when `REVISE` is not in canonical allowed actions, when `attempt_count >= max_attempts`, or when `attempt_count >= hard_max_attempts`. |
| `_canonical_revision_metadata()` | `budget_remains` now uses `min(max_attempts, hard_max_attempts)`. Because `TaskContract` already enforces `max <= hard_max`, this is behaviour-preserving and makes the hard limit explicit. |
| `_canonical_repairable_finding()` | Now delegates to the shared `canonical_repairable_branch_finding()`, so Runtime and the trust boundary cannot disagree about what the finding should be. |
| `_prepare_revision()` | The five inline StopDecision checks are replaced by the shared `repairable_stop_decision_error()` predicate. |

### `tests/scenarios/test_scenario_2.py`

103 tests (was 71). Added the budget-authority suite and the archive
trust-boundary suite; updated three existing expectations that the corrections
legitimately changed (see below).

## COMMANDS_AND_EXIT_CODES

| Command | Exit code | Result |
|---|---:|---|
| `git status --short` (initial) | 0 | empty |
| `git rev-parse HEAD` (initial) | 0 | `bf7abaf2684d5792d72e0320e47132b24cae27f5` |
| `git rev-parse HEAD^` (initial) | 0 | `1a41d410a220106b8969fab7818d4a8de00f1671` |
| `python3 -m pytest --collect-only -q` | 0 | `43 + 40 + 3 + 99 + 103 = 288` collected |
| `python3 -m pytest -q` | 0 | 288 passed (288 `PASSED` under `-rA`) |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed |
| `python3 -m pytest tests/scenarios/test_scenario_2.py -q` | 0 | 103 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 40 passed |
| `python3 -m pytest tests/domain/test_contracts.py -q` | 0 | 43 passed |
| `python3 -m pytest tests/domain/test_schema_generation.py -q` | 0 | 3 passed |
| `python3 -m compileall -q src tests` | 0 | passed |
| `ruff check .` | 0 | no findings |
| `git diff --check` | 0 | no whitespace errors |
| `git diff --cached --check` (pre-commit) | 0 | no whitespace errors |
| `git diff --cached --stat` (pre-commit) | 0 | 3 files, 608 insertions, 43 deletions |
| `python3 -m mypy src tests` | 1 | `Found 99 errors in 5 files`; **0 under `src/**`** |
| `python3 -m mypy src` | 0 | `Success: no issues found in 11 source files` |

Test count moved **256 → 288 (+32)**, all in `tests/scenarios/test_scenario_2.py`.
Full-tree Mypy moved 97 → 99; both new diagnostics are test-only (deliberate
private-attribute writes staging invalid canonical records). Source-only Mypy is
clean and no test-only diagnostic was cleaned up.

**Three existing tests were repointed**, each because a correction made the
rejection earlier or renamed the message — none because behaviour weakened:

- `drive_to_exhausted_budget` no longer calls `record_repairable_failure()` at
  Attempt 2; that call is now exactly what G2-S2-04A forbids.
- `test_revision_is_rejected_without_a_recorded_repairable_branch` and
  `test_completed_record_rejects_foreign_run_attempt_history` match the shared
  predicates' messages.
- `test_completed_record_rejects_a_revision_without_improvement` now asserts the
  forged `satisfied_criterion_ids` is caught by derivation re-computation (a
  strictly earlier and stronger rejection than the improvement predicate) and
  additionally asserts `validate_completed_record()` refuses it.

## EXHAUSTED_BUDGET_AUTHORITY_EVIDENCE

Driving Attempt 1 failure → revision → Attempt 2 failure leaves
`state=CHECKING, attempt_count=2, max_attempts=2`. At that point:

```text
_canonical_revision_metadata().allowed_actions == (FAIL,)
NextAction.REVISE not in allowed_actions
```

`record_repairable_failure()` now raises `RevisionNotAllowed` naming the
canonical allowed actions, and the record is untouched:

```text
record identity unchanged (is and ==)
stop_decision = None
unresolved_findings = ()
attempt_count = 2
attempt_history length = 1        (the genuine Attempt 1 archive)
events tuple unchanged
decision stream unchanged (still exactly 1)
REVISION_REQUESTED events = 1
```

No `REVISE` StopDecision can exist at that point, and a forged REVISE proposal is
still refused at the commit boundary with `attempt budget exhausted`. The FAIL
terminal branch was deliberately **not** implemented; refusing the invalid
recording is the authorized scope.

Preserved authority behaviour: rejection outside `CHECKING`, rejection when all
required checks pass, no caller-supplied decision or finding, deterministic
first-failed-check selection, value-idempotence on repeated valid Attempt 1
recording (equal StopDecision and equal single finding, no extra event or
decision), and no state transition, attempt increment, DecisionEvent or
`REVISION_REQUESTED` event from the writer itself.

## ARCHIVE_TRUST_BOUNDARY_EVIDENCE

`revision_attempt_archive_error()` is the single definition, called from
`RunRecord.validate_runtime_snapshot`. Because both `RunRecord.model_validate()`
and `validate_completed_record()` (which reconstructs from a detached dump) go
through that validator, the same semantics apply at the model boundary, the
completion gate, and the reporting boundary's existing full reconstruction — with
no change to `reporting.py`.

Every case below was rejected at **both** `RunRecord.model_validate()` and
`validate_completed_record()`, starting from a genuine completed Scenario 2
record:

| # | Copied archive | Rejected |
|---:|---|---|
| 1 | `stop_decision = null` | yes |
| 2 | `next_action = FAIL` | yes |
| 3 | `repairable = false` | yes |
| 4 | `owner_input_required = true` | yes |
| 5 | non-null outcome | yes |
| 6 | non-null stop reason | yes |
| 7 | `findings = ()` | yes |
| 8 | two findings | yes |
| 9 | forged finding identity | yes |
| 10 | foreign-run finding id | yes |
| 11 | wrong-attempt finding id | yes |
| 12 | finding not referencing the failed snapshot | yes |
| 13 | finding bound to a different check (fingerprint) | yes |
| 14 | archived failed check flipped to `exit_code=0` | yes |
| 15 | forged `failed_check_ids` | yes |
| 16 | genuinely failed check missing from `failed_check_ids` | yes |
| 17 | forged `satisfied_criterion_ids` | yes |
| 18 | archive belonging to another run | yes |

Additionally: an archived attempt not preceding the current attempt is rejected,
the bounded Gate 2 shape rejects a two-archive completed record, and the archive
is re-proven at `REVISING` as well as at `COMPLETED`. The genuine completed
record is still accepted by both boundaries with its archive unchanged, and a
Gate 1 unrevised record (`attempt_history=()`, `attempt_count=1`) remains valid
at both boundaries.

## FAILED_CHECK_DERIVATION_EVIDENCE

`failed_check_ids` and `satisfied_criterion_ids` are recomputed from the archived
observations, the archived criterion evaluations and the canonical
`TaskContract.required_checks`, then compared with **exact tuple equality** —
never subset. Consequences proven by test:

- A check with `exit_code=0` cannot remain listed as failed (case 14).
- A genuinely failed check cannot be omitted (case 16).
- Duplicated or invented IDs are refused (case 15).
- A forged satisfied-criteria claim is refused before the objective-improvement
  predicate is consulted (case 17).
- An archived revision source attempt with no derived failure is refused
  (`a revision source attempt must have at least one failed required check`).

The derivation reuses `attempt_evidence_from_observations()`, so the archive
validator and the Runtime writer cannot drift.

## STOP_DECISION_AND_FINDING_REVALIDATION

The archived `StopDecision` is re-proven field-by-field through
`repairable_stop_decision_error()` — the same predicate `_prepare_revision()`
uses — requiring exactly `next_action=REVISE`, `repairable=true`,
`owner_input_required=false`, `outcome=null`, `stop_reason=null`.

The archived `Finding` is **re-derived**, not trusted: from the record's
`run_id`, the archive's `attempt_number`, canonical required-check order, the
first archived failed required check, and that check's archived observed
evidence. Exactly one finding is required and it must equal the derivation, so
identity, run, attempt, severity, category, fingerprint, acceptance criterion and
evidence references are all covered by one comparison. Seven distinct forgeries
are individually asserted (cases 9–13 plus severity and acceptance-criterion
tampering).

## ATOMICITY_AND_ROLLBACK_EVIDENCE

The archive is validated as part of candidate construction, so an invalid archive
aborts the revision before anything is committed.
`test_archive_validation_failure_rolls_the_whole_revision_back` injects a broken
archive into `_build_record` and asserts:

```text
state = CHECKING
attempt_count = 1
attempt_history = ()
decisions = ()
REVISION_REQUESTED events = 0
Attempt 1 stop_decision unchanged
Attempt 1 unresolved_findings unchanged
```

then retries and commits exactly once: one archive, one DecisionEvent at
`append_sequence=1`, one `REVISION_REQUESTED` event, Attempt 2. The pre-existing
injections at decision preparation, candidate transition preparation, decision
append, transition-event append and partial transition-event append all still
pass, and earlier nonterminal history stays byte-identical.

## NEGATIVE_CONTROLS

Run in isolated temporary copies; the tracked tree was never modified.

| # | Restored defect | Invalid state accepted | Path | Failing tests |
|---:|---|---|---|---|
| 1 | Remove attempt-budget check from `record_repairable_failure()` | REVISE branch written while canonical actions are `(FAIL,)` | public API | 2 — `test_record_repairable_failure_is_refused_at_the_exhausted_budget`, `test_no_revise_stop_decision_can_exist_at_the_exhausted_budget` |
| 2 | Allow missing archived StopDecision | completed record with no branch decision | copied trust-boundary input | 3 — `test_archived_stop_decision_is_reproven[None]`, both `test_archived_stop_decision_rejects_terminal_fields` |
| 3–5 | Allow missing / duplicate / forged Finding | archive with 0, 2 or forged findings | copied trust-boundary input | 11 — both `test_archived_finding_count_is_reproven`, all seven `test_archived_finding_is_reproven_field_by_field`, plus two adjacent |
| 6–7 | Stop re-deriving failed checks | forged `failed_check_ids` accepted | copied trust-boundary input | 1 — `test_forged_failed_check_ids_are_rejected` |
| 8 | Subset instead of exact failed-check equality | extra/duplicated IDs accepted | copied trust-boundary input | 1 — `test_forged_failed_check_ids_are_rejected` |
| 9 | Stop re-deriving satisfied criteria | forged improvement claim accepted | copied trust-boundary input | 1 — `test_completed_record_rejects_a_revision_without_improvement` |
| 10 | Bypass completed-record archive validation | every archive forgery accepted | copied trust-boundary input | 20 — the whole archive suite |
| 11 | Remove archive/decision rollback | partial archive/decision/transition commit | private injection | 4 — `test_decision_append_failure_commits_nothing`, `test_transition_event_failure_commits_no_decision`, `test_retry_after_failed_revision_commits_exactly_once`, `test_archive_decision_and_transition_roll_back_together` |
| 12 | Re-enable Attempt 3 (drop budget gates) | REVISE permitted past `max_attempts` | public API | 4 — `test_second_revision_is_rejected_when_the_budget_is_exhausted`, `test_canonical_allowed_actions_drop_revise_at_the_budget_limit`, `test_record_repairable_failure_is_refused_at_the_exhausted_budget`, `test_no_revise_stop_decision_can_exist_at_the_exhausted_budget` |

Each control's failing test is the assertion that the restored defect must
violate, which is what makes the correction load-bearing rather than incidental.

**One honest nuance on controls 6–8.** Removing or weakening the failed-check
equality check causes exactly one test to fail, not three. The flipped-to-passing
case (14) and the missing-ID case (16) are additionally caught by two adjacent
guards — the "revision source attempt must have at least one failed required
check" rule and the objective-improvement predicate — so those two tests still
pass under the weakened validator. That is defense in depth, not a coverage gap:
`test_forged_failed_check_ids_are_rejected` isolates the equality rule itself,
and control 10 shows the whole suite collapses when archive validation is removed
entirely.

## CLAIMED_FIXED_PENDING_REVIEW

```text
G2-S2-04A=CLAIMED_FIXED_PENDING_REVIEW
G2-S2-04B=CLAIMED_FIXED_PENDING_REVIEW

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

- **The FAIL terminal branch does not exist.** At an exhausted budget with a
  failed Attempt 2 the run now correctly refuses to record REVISE, but it also
  has no canonical terminal result and simply stays in `CHECKING`. This is the
  authorized scope, and it is a known incompleteness rather than a resolved
  state.
- **Archive validation runs on every `RunRecord` construction.** With
  `attempt_history` non-empty this recomputes derivations and rebuilds the
  canonical finding on each `_replace_record`. Correct and cheap at Gate 2 scale;
  it would need attention if attempt history ever grew.
- **Finding equality remains exact.** A future verifier that legitimately
  enriches findings would need this relaxed deliberately at both the writer and
  the archive validator.
- **The bounded Gate 2 shape is asserted for revised completed records.** Any
  future multi-revision gate must revisit that rule; it is intentionally not
  generalized.
- **Archive binding is identity and derivation, not content integrity.** It
  proves the archive is internally consistent with canonical truth, not that the
  referenced artifact bytes are unchanged.
- **Deferred and untouched:** `DecisionReasonCode` location, test-only Mypy
  diagnostics, persistence, crash recovery, concurrency, Attempt 3,
  `WAITING_OWNER`, real adapters, product metrics.

## SCOPE_AND_NON_GOALS

Changed exactly: `src/tomtit/runtime/runtime.py`, `src/tomtit/runtime/state.py`,
`tests/scenarios/test_scenario_2.py`. Confirmed unchanged:
`src/tomtit/reporting.py`, `src/tomtit/runtime/events.py`,
`src/tomtit/domain/**`, `src/tomtit/fakes.py`, `src/tomtit/runtime/__init__.py`,
`tests/scenarios/test_scenario_1.py`, `tests/domain/**`, `docs/**`, `CLAUDE.md`,
`SYSTEM.md`, and every existing audit report. No dependency was added.

Not implemented: the FAIL terminal branch, Attempt 3, `WAITING_OWNER`,
`NO_PROGRESS` engine, multiple-finding workflow, finding ranking, general policy
engine, persistent storage, crash recovery, concurrency, real adapter,
subprocess runner, product CLI, assessment store, routing, Memory, graph
orchestration. No enum was relocated, no test-only Mypy diagnostic was cleaned,
and Gate 1 was not modified. `_ALLOWED_TRANSITIONS` is unchanged.

## UNPROVEN_ITEMS

- Real-world coordination value remains **not proven**; this is a fake loop.
- The terminal behaviour of an exhausted-budget failed run is **not implemented**
  and therefore **not proven**.
- Persistence, crash recovery and concurrency remain **not proven** and out of
  scope.
- Exhaustiveness of the archive forgery surface is **not proven**; the patch
  closes the reviewed reproductions and the enumerated variants, not every
  conceivable tampering.
- Archived artifact **content** integrity is **not proven** — only identity and
  derivation consistency.
- `task_contract_hash` stability across Pydantic versions is **not proven**.
- Whether these corrections satisfy Gate 2 is **claimed, not self-verified**;
  that is the reviewer's determination.

## NEXT_GATE

```text
CODEX_SOL_OWNER_AUTHORIZED_FINAL_GATE_2_REREVIEW
NO_ADDITIONAL_IMPLEMENTATION_PATCH_AUTHORIZED
NO_NEXT_GATE_AUTHORIZED
```
