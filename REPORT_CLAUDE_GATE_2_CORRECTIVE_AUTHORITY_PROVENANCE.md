# Claude Gate 2 Corrective Patch — Revision Authority and Provenance

## STATUS

```text
READY_FOR_CODEX_SOL_GATE_2_CORRECTIVE_REREVIEW
```

## STARTING_HEAD

```text
716fa6b2e8000ee5c63bd157efb4bcf18c59b06a
```

## SOURCE_COMMIT_HASH

```text
b71aca92ceb9a17544862e596cd17bd2dc794052
```

## IMPLEMENTED_DIFF_RANGE

```text
716fa6b2e8000ee5c63bd157efb4bcf18c59b06a..b71aca92ceb9a17544862e596cd17bd2dc794052
```

## REPORT_COMMIT_EXPECTED_PARENT

```text
b71aca92ceb9a17544862e596cd17bd2dc794052
```

## MODEL_USED

Claude Code Opus (`claude-opus-5`).

## MODEL_REASON

The patch closes a public canonical-transition authority bypass, adds
independent re-derivation of persisted decision metadata at a trust boundary,
binds archived evidence to run identity across three validation boundaries, and
materializes the policy-required repairable `StopDecision` and canonical
`Finding` inside the existing atomic revision commit. No downgrade occurred.

## FILES_CHANGED

Three files. `src/tomtit/domain/evaluation.py`, `src/tomtit/runtime/events.py`,
`tests/domain/test_evaluation_models.py`, and
`tests/domain/test_schema_generation.py` were in the allowed list but required
no change.

### `src/tomtit/runtime/state.py`

| Item | Change |
|---|---|
| `AttemptEvidence.run_id` (new field) | Explicit immutable run identity for archived evidence. |
| `AttemptEvidence.stop_decision`, `.findings` (new fields) | Carry the Attempt 1 branch result into the archive. |
| `AttemptEvidence.validate_attempt_binding` | Now also requires every archived check and the archived patch to belong to the archived **run**, requires each check's `snapshot_id` to be consistent with the explicit run/attempt fields, and requires `failed_check_ids` / `satisfied_criterion_ids` to reference archived observations and evaluations. |
| `attempt_evidence_from_observations()` | Takes the canonical `run_id` explicitly and rejects any observation that disagrees; accepts and stores `stop_decision` and `findings`. |
| `repairable_check_finding()` (new) | Deterministic canonical `Finding` for a failed required check: `finding_id=finding:{run}:{attempt}:{check}`, stable fingerprint, `BLOCKING` severity, `evidence_refs` containing the failed check's snapshot ID and both artifact paths. |
| `first_failed_required_check()` (new) | Deterministic selection in canonical `required_checks` order. |
| `TASK_CONTRACT_HASH_SCHEMA_VERSION` + `task_contract_hash()` | Hashes a versioned envelope `{"schema_version": "tomtit.task_contract_hash.v1", "task_contract": …}` with sorted keys and stable separators. |
| `RunRecord.validate_runtime_snapshot` | Rejects any `attempt_history` entry whose `run_id` differs from the record's. |

### `src/tomtit/runtime/runtime.py`

| Item | Change |
|---|---|
| `_RUNTIME_COMMITTED_STATES` (new) | Maps `COMPLETED` and `REVISING` to their refusal messages: entering either carries its own canonical record. |
| `transition()` | Checks the edge first, then refuses any protected target **before** the reason check, so `CHECKING → REVISING` is refused for every supplied `TransitionReason`. |
| `_assert_transition_edge()` / `_assert_transition_reason()` (new) | Split out of `_assert_transition_legal()`, which now composes them. `complete_run()` keeps using the composed form, so its behavior is unchanged. |
| `_RevisionMetadata` (new) | Frozen carrier for canonically derived decision metadata. |
| `_canonical_revision_metadata()` (new) | Single derivation of `policy_version`, `allowed_actions`, `binding_constraints`, and `budget_snapshot` from the record and contract. `allowed_actions` is `(REVISE, FAIL)` only while attempt budget remains, otherwise `(FAIL,)`. |
| `build_revision_proposal()` | Now sources every metadata field from that derivation. |
| `_prepare_revision()` | Independently re-derives the metadata and requires exact equality on all four fields; then requires the recorded repairable `StopDecision` (exact field values) and exactly one `Finding` equal to the canonical derivation. |
| `record_repairable_failure()` (new, public) | Runtime writes the policy-required `StopDecision(next_action=REVISE, repairable=True, owner_input_required=False, outcome=None, stop_reason=None)` and the one canonical `Finding`. Refused outside `CHECKING` and when no required check failed. |
| `_canonical_repairable_finding()` (new) | Derives the finding, including the acceptance criterion that references the failed check. |
| `_prepare_revision()` archive | The Attempt 1 archive now carries the `StopDecision` and `Finding`; the candidate clears the current record's `stop_decision` and `unresolved_findings` because they belong to Attempt 1. |
| `run_scenario_2()` | Calls `record_repairable_failure()` before proposing. |

### `tests/scenarios/test_scenario_2.py`

34 → 71 tests. Existing helpers updated (`drive_to_first_failure` now records the
repairable branch, mirroring `run_scenario_2`). The two budget tests were
strengthened: with the budget exhausted, canonical policy no longer permits
`REVISE`, so the proposal cannot even name it, and a hand-forged REVISE draft is
still refused at the commit boundary.

## COMMANDS_AND_EXIT_CODES

| Command | Exit code | Result |
|---|---:|---|
| `git status --short` (initial) | 0 | empty |
| `git rev-parse HEAD` (initial) | 0 | `716fa6b2e8000ee5c63bd157efb4bcf18c59b06a` |
| `git rev-parse HEAD^` (initial) | 0 | `1de35a4c4e33ab892afa2843c6501983c2588ee0` |
| `python3 -m pytest --collect-only -q` | 0 | `43 + 40 + 3 + 99 + 71 = 256` collected |
| `python3 -m pytest -q` | 0 | 256 passed (256 `PASSED` under `-rA`) |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed |
| `python3 -m pytest tests/scenarios/test_scenario_2.py -q` | 0 | 71 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 40 passed |
| `python3 -m pytest tests/domain/test_contracts.py -q` | 0 | 43 passed |
| `python3 -m pytest tests/domain/test_schema_generation.py -q` | 0 | 3 passed |
| `python3 -m compileall -q src tests` | 0 | passed |
| `ruff check .` | 0 | no findings |
| `git diff --check` | 0 | no whitespace errors |
| `git diff --cached --check` (pre-commit) | 0 | no whitespace errors |
| `git diff --cached --stat` (pre-commit) | 0 | 3 files, 926 insertions, 42 deletions |
| `python3 -m mypy src tests` | 1 | `Found 97 errors in 5 files`; **0 under `src/**`** |
| `python3 -m mypy src` | 0 | `Success: no issues found in 11 source files` |

Test baseline moved 219 → 256 (+37), all in `tests/scenarios/test_scenario_2.py`.
Full-tree Mypy moved 96 → 97; the one additional diagnostic is test-only (a
deliberate private-attribute write used to stage an invalid canonical record).
Source-only Mypy is clean, and no test-only diagnostics were cleaned up.

## PUBLIC_TRANSITION_AUTHORITY_EVIDENCE

The exact reviewed bypass now raises `InvalidTransitionError` naming
`commit_revision_decision`, for **every** supplied reason
(`REVISION_APPROVED`, `EVIDENCE_READY`, `TERMINATION_DECIDED` — parametrized in
`test_public_transition_cannot_enter_revising`). After the refusal:

```text
record identity unchanged (is and ==)
state=CHECKING
attempt_count=1
attempt_history=()
events tuple unchanged
persisted decisions=0
REVISION_REQUESTED events=0
```

`test_revising_remains_reachable_only_through_the_decision_commit` shows the
refusal followed by a successful `commit_revision_decision()` that produces
exactly one decision at `append_sequence=1`, one `REVISION_REQUESTED` event, and
Attempt 2. `test_completed_remains_protected_from_the_public_transition_api`
confirms the Gate 1 `COMPLETED` protection is unchanged. Because the protected
check runs after the edge check, a genuinely illegal edge is still reported as
`illegal transition` — the Gate 1 test asserting that still passes.

## CANONICAL_DECISION_METADATA_EVIDENCE

`_canonical_revision_metadata()` is the single derivation used by both the
proposal builder and, independently, the commit boundary. All four reviewed
forgeries are refused, with the record, attempt, history, decision stream and
event history unchanged:

| Forged field | Result |
|---|---|
| `policy_version="attacker-policy-v999"` | rejected — `does not match the canonical policy` |
| `allowed_actions=(REVISE, COMPLETE)` | rejected — `do not match canonical` |
| `binding_constraints=("max_attempts=999",)` | rejected — `do not match canonical` |
| `budget_snapshot` with wrong attempt / max / hard max / time budget | rejected (parametrized over all four fields) |

`test_a_valid_proposal_still_commits_after_metadata_rejections` shows a genuine
proposal still commits exactly once afterwards, persisting
`policy_version="mvp-execution-policy-v0.3"`,
`allowed_actions=(revise, fail)`,
`binding_constraints=("max_attempts=2", "deterministic_evidence_required")`,
and a budget snapshot of `max=2, hard=3, time=120`.

## ARCHIVED_RUN_PROVENANCE_EVIDENCE

`AttemptEvidence.run_id` is an explicit field, and validation uses model fields
rather than parsing snapshot strings (the snapshot-ID consistency check is an
additional cross-check, not the mechanism).

| Boundary | Foreign-run archive |
|---|---|
| `AttemptEvidence(...)` with foreign-run checks | rejected — `belong to the archived run` |
| `AttemptEvidence(...)` with foreign-run patch | rejected — `patch must belong to the archived run` |
| `RunRecord.model_validate(...)` | rejected — `attempt history must belong to the canonical run` |
| `validate_completed_record(...)` | rejected — same |
| `attempt_evidence_from_observations(run_id=…)` | rejected — `belong to the canonical run` |

The genuine archive is still accepted and returned unchanged by
`validate_completed_record`, and `test_gate_1_empty_history_completion_is_unaffected`
confirms the empty-history Gate 1 path is untouched.

## STOP_DECISION_AND_FINDING_EVIDENCE

After the Attempt 1 deterministic failure:

```text
stop_decision.next_action=revise
stop_decision.repairable=True
stop_decision.owner_input_required=False
stop_decision.outcome=None
stop_decision.stop_reason=None

unresolved_findings = exactly 1
finding.severity=BLOCKING
finding.finding_id=finding:run-s2:1:unit-tests
finding.acceptance_criterion_id=tests-pass
finding.evidence_refs contains check:run-s2:1:unit-tests
```

After a successful revision, the Attempt 1 archive preserves the `StopDecision`,
the one `Finding`, and the failed observed evidence
(`failed_check_ids=("unit-tests",)`), while the current record carries only
Attempt 2's completion decision and no unresolved findings. The finding is
derived from observed evidence; Worker claims are not an input.

Rejections (each leaving no decision, no archive, no attempt increment):

| Case | Result |
|---|---|
| No recorded branch result | `REVISE requires a recorded repairable StopDecision` |
| `next_action=FAIL` | `next_action=revise` |
| `repairable=False` | `repairable=true` |
| `owner_input_required=True` | `owner_input_required=false` |
| non-null outcome / stop reason | `must not set outcome or stop reason` |
| 0 findings / 2 findings | `exactly one repairable finding` |
| foreign-run finding id | `canonical repairable finding` |
| wrong-attempt finding id | `canonical repairable finding` |
| finding not referencing the failed evidence | `canonical repairable finding` |
| non-blocking severity | `canonical repairable finding` |

`record_repairable_failure()` itself is refused outside `CHECKING` and when no
required check failed.

## ATOMICITY_AND_ROLLBACK_EVIDENCE

The archive, the `StopDecision`, the `Finding`, the attempt increment, the
`DecisionEvent`, and the `REVISION_REQUESTED` transition are one candidate and
one rollback unit. `test_archive_decision_and_transition_roll_back_together`
injects a decision-append failure and asserts:

```text
attempt_history=()          (no archive without a decision)
decisions=()                (no decision without an archive)
attempt_count=1
stop_decision unchanged     (Attempt 1 branch result preserved)
unresolved_findings unchanged
REVISION_REQUESTED events=0
```

then retries and commits exactly once, with the archive carrying the same
`StopDecision` and `Finding` objects. The pre-existing injections at decision
preparation, candidate transition preparation, decision append, transition-event
append, and partial transition-event append all still pass, and earlier
nonterminal history stays byte-identical.

## TASK_CONTRACT_HASH_RESULT

```text
G2-S2-05=CLAIMED_FIXED_PENDING_REVIEW
```

`task_contract_hash()` now hashes a versioned envelope carrying
`schema_version="tomtit.task_contract_hash.v1"`. Tests assert the hash equals an
independently computed envelope digest, differs from the previous unversioned
digest, is stable across equal contracts, and changes when the contract goal
changes. No general canonicalization framework was introduced.

## NEGATIVE_CONTROLS

Run in isolated temporary copies; the tracked tree was never modified.

| # | Restored invalid state | Failing tests |
|---:|---|---|
| 1 | Public `CHECKING → REVISING` re-enabled | 4 — `test_public_transition_cannot_enter_revising[REVISION_APPROVED|EVIDENCE_READY|TERMINATION_DECIDED]`, `test_revising_remains_reachable_only_through_the_decision_commit` |
| 2 | `policy_version` comparison removed | 2 — `test_false_decision_metadata_is_rejected[policy_version]`, `test_a_valid_proposal_still_commits_after_metadata_rejections` |
| 3 | Extra allowed action accepted | 1 — `test_false_decision_metadata_is_rejected[allowed_actions]` |
| 4 | False binding constraint accepted | 2 — `test_false_decision_metadata_is_rejected[binding_constraints]`, `test_a_valid_proposal_still_commits_after_metadata_rejections` |
| 5 | False budget snapshot accepted | 3 — `test_false_budget_snapshot_is_rejected[max_attempts|hard_max_attempts|time_budget_seconds]` |
| 6 | Foreign-run archived evidence accepted | 5 — all four provenance-boundary tests plus `test_evidence_helper_rejects_foreign_run_observations` |
| 7 | Missing StopDecision accepted | 5 — `test_revision_is_rejected_without_a_recorded_repairable_branch`, three `test_invalid_stop_decision_rejects_revision` cases, `test_terminal_stop_decision_fields_reject_revision` |
| 8 | Missing canonical Finding accepted | 6 — both `test_wrong_finding_count_rejects_revision` cases and all four `test_non_canonical_finding_rejects_revision` cases |
| 9 | Finding not bound to failed evidence accepted | 4 — all four `test_non_canonical_finding_rejects_revision` cases |
| 10 | Partial archive/decision/transition commit (rollback removed) | 4 — `test_decision_append_failure_commits_nothing`, `test_transition_event_failure_commits_no_decision`, `test_retry_after_failed_revision_commits_exactly_once`, `test_archive_decision_and_transition_roll_back_together` |
| 11 | Explicit hash schema version removed | 1 — `test_contract_hash_includes_an_explicit_schema_version` |

## CLAIMED_FIXED_PENDING_REVIEW

```text
G2-S2-01=CLAIMED_FIXED_PENDING_REVIEW
G2-S2-02=CLAIMED_FIXED_PENDING_REVIEW
G2-S2-03=CLAIMED_FIXED_PENDING_REVIEW
G2-S2-04=CLAIMED_FIXED_PENDING_REVIEW
G2-S2-05=CLAIMED_FIXED_PENDING_REVIEW

SCENARIO_2=PRESERVED_PENDING_REVIEW
APPEND_SEQUENCE_AUTHORITY=PRESERVED_PENDING_REVIEW
OBJECTIVE_IMPROVEMENT=PRESERVED_PENDING_REVIEW
ZERO_OWNER_INTERVENTION=PRESERVED_PENDING_REVIEW
GATE_1=PRESERVED_PENDING_REVIEW
```

## RISKS

- **`record_repairable_failure()` is a new public Runtime method.** It writes
  canonical state, which is Runtime authority, and takes no caller input, but it
  widens the public surface. A caller can invoke it only from `CHECKING` and only
  when a required check actually failed.
- **Finding equality is exact.** The commit boundary requires the recorded
  finding to equal the canonical derivation exactly. This is strong but rigid: a
  future verifier that legitimately enriches findings would need this rule
  relaxed deliberately.
- **`allowed_actions` is derived for this branch only.** It encodes the failed
  deterministic-check branch, not a general policy engine. Other branches will
  need their own derivation when they exist.
- **Archived run binding is identity-only.** It proves the archive names this
  run; it is not a content hash and does not prove artifact bytes are unchanged.
- **Hash envelope is local and Pydantic-dependent.** The schema version makes a
  future canonicalization change detectable, but the inner encoding still depends
  on the pinned Pydantic version.
- **Deferred and untouched:** `DecisionReasonCode` location, test-only Mypy
  diagnostics, persistence, crash recovery, concurrency, Attempt 3,
  `WAITING_OWNER`, real adapters, product metrics.

## SCOPE_AND_NON_GOALS

Changed exactly: `src/tomtit/runtime/runtime.py`, `src/tomtit/runtime/state.py`,
`tests/scenarios/test_scenario_2.py`. Confirmed unchanged:
`src/tomtit/reporting.py`, `src/tomtit/fakes.py`,
`src/tomtit/domain/contracts.py`, `src/tomtit/domain/enums.py`,
`src/tomtit/domain/evaluation.py`, `src/tomtit/runtime/events.py`,
`src/tomtit/runtime/__init__.py`, `tests/scenarios/test_scenario_1.py`,
`tests/domain/**`, `docs/**`, `CLAUDE.md`, `SYSTEM.md`, and every existing audit
report. No dependency was added.

Not implemented: Attempt 3, `WAITING_OWNER`, `NO_PROGRESS` engine, multiple
findings, finding ranking, general policy engine, general canonicalization
framework, persistent decision store, crash recovery, concurrency, real adapter,
subprocess runner, product CLI, assessment-event store, routing, Memory, graph
orchestration. `_ALLOWED_TRANSITIONS` is unchanged. `DecisionReasonCode` was not
relocated and no test-only Mypy diagnostic was cleaned.

## UNPROVEN_ITEMS

- Real-world coordination value remains **not proven**; this is a fake loop.
- Persistence, crash recovery, and concurrency for the decision stream and the
  archive remain **not proven** and out of scope.
- `task_contract_hash` stability across Pydantic versions is **not proven**.
- Exhaustiveness of the authority and provenance attack surface is **not
  proven**; the patch closes the four reviewed findings and the enumerated
  variants, not every conceivable forgery.
- Archived artifact **content** integrity is not proven — only run/attempt
  identity binding.
- Whether these corrections satisfy Gate 2 is **claimed, not self-verified**;
  that is the reviewer's determination.

## NEXT_GATE

```text
CODEX_SOL_GATE_2_CORRECTIVE_REREVIEW
NO_ADDITIONAL_IMPLEMENTATION_PATCH_AUTHORIZED
NO_NEXT_GATE_AUTHORIZED
```
