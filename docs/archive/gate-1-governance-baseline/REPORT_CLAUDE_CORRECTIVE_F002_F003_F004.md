# Claude Corrective Patch — F-002, F-003, F-004

## 1. Status

```text
STATUS=READY_FOR_CODEX_SOL_REVIEW
F-002=PENDING_REVIEW
F-003=PENDING_REVIEW
F-004=PENDING_REVIEW
COMMIT_3=NOT_AUTHORIZED
```

## 2. Model

- **Model used:** Claude Opus (Claude Code Opus, `claude-opus-5`).
- **Why Opus was selected:** this patch changes canonical-state invariants, the
  terminal-report trust boundary, filesystem-backed completion evidence
  validation, and deeply immutable event payload semantics. Those concerns cross
  the Runtime, domain-model, and reporting authority boundaries at once, and the
  central design decision — which invariants belong in the canonical model
  validator versus in a shared gate that may touch the filesystem — required
  architectural reasoning rather than mechanical edits. No downgrade occurred
  during the task.

## 3. Reviewed baseline

- **Starting HEAD:** `abca5d066fff10d5d085a96b87ec6275d17aff19`
  (`docs(audit): record Terra baseline rereview`).
- **Starting Git cleanliness:** `git status --short` and
  `git status --porcelain=v1 --untracked-files=all` were both empty — tracked
  worktree clean, index clean, no untracked project files. Only ignored
  generated artifacts existed. The expected-state precondition held, so no
  `OWNER_DECISION_REQUIRED` was raised.
- **Baseline suite before changes:** 91 collected, 91 passed; Scenario 1: 31
  collected, 31 passed.
- **Policy and audit reports read:** `CLAUDE.md`, `SYSTEM.md`,
  `docs/MVP_EXECUTION_POLICY_V0.md`, `docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md`,
  `REPORT_CODEX_TERRA_BASELINE_REREVIEW.md`,
  `REPORT_CODEX_TERRA_BASELINE_REVIEW.md`,
  `REPORT_CLAUDE_BASELINE_AUDIT_CORRECTION.md`,
  `REPORT_REPOSITORY_BASELINE_RECOVERY.md`. Current source and tests were
  inspected in full before any edit.

## 4. Files changed

### `src/tomtit/runtime/state.py`

| Item | Change |
|---|---|
| `CompletionInvariantError` (new) | `ValueError` subclass raised by the shared completion gate. |
| `required_check_coverage_error()` (new) | Single definition of required-check coverage: exactly one observed result per required check, no missing checks, no duplicates, no unexpected checks, and an observed exit code inside `expected_exit_codes`. |
| `patch_evidence_error()` (new) | Single definition of patch completeness: `evidence_complete`, `diff_exists`, and no scope violations. |
| `artifact_file_error()` (new) | Single definition of "artifact exists and is a regular file". |
| `RunRecord.unresolved_findings` (new field) | `tuple[Finding, ...]`, default empty. Required so the "no unresolved blocking finding" completion condition has canonical state to validate. |
| `RunRecord.validate_runtime_snapshot` | Added the F-003 `NO_PROGRESS → attempt_count >= 2` invariant; added `attempt_count >= 1`, required-check coverage, and blocking-finding rejection to the COMPLETED branch; replaced the inline patch checks with the shared `patch_evidence_error`. |
| `validate_completed_record()` (new) | The shared completion gate. Revalidates a detached dump, asserts COMPLETED, then performs the filesystem-backed artifact proofs. |

**Why:** F-002 required that a tampered copied record cannot reach the report,
and F-003 required an invariant that holds independently of Runtime
reachability. Structural facts belong in the canonical model validator so every
`model_validate` path enforces them; the filesystem proofs cannot live there.

**Trade-offs:** the F-003 check is placed *after* the terminal
outcome/stop-reason-versus-`StopDecision` match checks, so a record that is both
mismatched and Attempt-1-`NO_PROGRESS` reports the mismatch first. This keeps
the pre-existing error contract intact. Adding `unresolved_findings` widens
`RunRecord` by one field; it stores no verifier behavior and defaults to empty.

### `src/tomtit/runtime/runtime.py`

| Item | Change |
|---|---|
| `transition()` | The COMPLETED branch now builds a candidate record and commits it **only after** `validate_completed_record` proves it. A failed gate leaves the canonical record untouched in `CHECKING`. |
| `_validate_transition_preconditions()` | The `CHECKING → COMPLETED` branch no longer re-implements completion checks; it keeps only the transition-legality preconditions (`StopDecision` present, `next_action=complete`). |
| `_all_required_checks_passed` / `_patch_evidence_allows_completion` (removed) | Replaced by `_completion_evidence_is_sufficient()`, which delegates to the shared predicates. |
| `_require_regular_file()` | Now delegates to `artifact_file_error()`, preserving its `InvalidTransitionError` type and message text at the pre-CHECKING boundary. |
| `_build_record()` (new) | Extracted from `_replace_record()` so a candidate record can be validated before it is committed. |

**Why:** without this split, Runtime and the report boundary each owned a copy of
completion logic and could drift. **Trade-off:** a failed completion transition
now raises `CompletionInvariantError` instead of `InvalidTransitionError`. No
existing test depended on that type at this boundary, and the change is what
makes the shared-gate behavior observable.

### `src/tomtit/reporting.py`

| Item | Change |
|---|---|
| `build_terminal_report()` | Replaced the local `RunRecord.model_validate(...)` plus state check with a single `validate_completed_record(record)` call. Every field is still read only from `validated_record`. |
| `TerminalReport.blocking_findings` | Now sourced from the validated record instead of silently defaulting. |

**Why:** the report boundary must re-establish the same completion invariants
Runtime enforced. **Trade-off:** `blocking_findings` is provably always empty at
completion (blocking findings are rejected); wiring it documents the invariant
in code rather than leaving the field disconnected.

### `src/tomtit/domain/evaluation.py`

| Item | Change |
|---|---|
| `FrozenPayload` (new) | `collections.abc.Mapping` implementation backed by a `MappingProxyType`, with pydantic core-schema, serializer, and JSON-schema hooks. |
| `_freeze_payload_value()` / `_unfreeze_payload_value()` (new) | Recursive freeze (nested mappings → `FrozenPayload`, nested sequences → tuples) and the inverse for JSON output. |
| `ImmutableEventPayload` (new) | `Annotated[Mapping[str, Any], FrozenPayload]` — the declared read-only interface. |
| `EvaluationEvent.payload` | Changed from `dict[str, Any]` to `ImmutableEventPayload`. |

**Why:** F-004 required deep immutability without exposing a mutable internal
dict. `MappingProxyType` keeps the backing dict both unreachable and unwritable.
Declaring the field as `Mapping[str, Any]` rather than `FrozenPayload` keeps
`dict` inputs valid for static analysis — `src/tomtit/runtime/events.py` is
outside this task's allowed files and would otherwise have acquired a new type
error — while `Mapping` has no `__setitem__`, so mutation is rejected statically
too. **Trade-offs:** nested lists become tuples, so a payload no longer compares
equal to a plain dict containing lists (it does compare equal after a JSON
round-trip); and `model_dump(mode="python")` now yields plain builtins rather
than the frozen value, because the serializer is declared `when_used="always"`.

### `tests/scenarios/test_scenario_1.py`

Added 22 tests: the 16 F-002 report-boundary exploits, a positive
immutable-report test, the shared-gate test, and 4 F-003 tests. Updated
`test_append_only_log_returns_detached_event_payloads` — payload mutation is now
*rejected* rather than silently detached, so it asserts the `TypeError` and
still asserts the journal is unchanged.

### `tests/domain/test_evaluation_models.py`

Added 10 F-004 tests covering rejection at each depth, anti-aliasing, canonical
list conversion, JSON output shape, round-trip, schema generation, and
non-mapping rejection.

## 5. Shared completion validation

- **Where the shared validator lives:** `validate_completed_record()` in
  `src/tomtit/runtime/state.py`.
- **Runtime call site:** `Runtime.transition()`, in the
  `target is RunState.COMPLETED` branch — the candidate record is validated
  before it becomes `self._record` and before the `RUN_COMPLETED` event is
  appended.
- **Reporting call site:** the first statement of `build_terminal_report()` in
  `src/tomtit/reporting.py`.
- **Structural versus filesystem-backed checks:**
  - *Structural* invariants (terminal fields, `StopDecision` agreement,
    verification status and basis, required-check coverage and exit codes,
    evidence provenance, patch completeness, criteria satisfaction, blocking
    findings, the F-003 rule) live in `RunRecord.validate_runtime_snapshot`.
    The gate re-proves them by revalidating a detached
    `model_dump(mode="python")`, which is what defeats
    `model_copy(update=...)` tampering.
  - *Filesystem-backed* proofs (stdout, stderr, and patch artifacts still exist
    and are regular files) run inside the gate, against the already-revalidated
    record. They are deliberately not in the model validator: canonical model
    construction must not depend on I/O, and `_replace_record` constructs a
    record on every state write.
- **How divergence is prevented:** there is exactly one implementation of each
  completion predicate. Runtime's pre-completion decision screen
  (`_completion_evidence_is_sufficient`) calls the same
  `required_check_coverage_error` and `patch_evidence_error` functions the model
  validator calls, and both boundaries call the same gate. The gate is
  authoritative: Runtime's screen only decides *whether to attempt* completion,
  and cannot admit evidence the gate would reject.
  `test_runtime_and_report_boundary_share_one_completion_gate` asserts the
  Runtime rejection, the report rejection, and the direct gate rejection produce
  byte-identical messages for the same tampering.

## 6. F-002 evidence

Every case tampers with a genuinely COMPLETED record through
`model_copy(update=...)` (or by deleting a real artifact) and calls
`build_terminal_report`. All were rejected.

| # | Exploit | Rejected by | Message |
|---:|---|---|---|
| 1 | Check exit code changed passed → failed | model validator | `COMPLETED rejected: required check unit-tests observed exit code 1 is not an expected exit code` |
| 2 | Required check result missing | model validator | `COMPLETED rejected: observed results must cover exactly the required checks: missing=['unit-tests'], unexpected=[]` |
| 3 | Duplicate results for one required check | model validator | `COMPLETED rejected: required check unit-tests has 2 observed results, expected 1` |
| 4 | Check evidence uses a foreign `run_id` | model validator | `COMPLETED observed checks must belong to the canonical run` |
| 5 | Check evidence uses the wrong attempt number | model validator | `COMPLETED observed checks must belong to canonical attempt` |
| 6 | stdout artifact deleted | shared gate | `check unit-tests: stdout artifact must exist and be a regular file` |
| 7 | stderr artifact deleted | shared gate | `check unit-tests: stderr artifact must exist and be a regular file` |
| 8 | stdout path replaced by a directory | shared gate | `check unit-tests: stdout artifact must exist and be a regular file` |
| 9 | Patch artifact deleted | shared gate | `patch artifact must exist and be a regular file` |
| 10 | Patch artifact path replaced by a directory | shared gate | `patch artifact must exist and be a regular file` |
| 11 | `evidence_complete=false` | model validator | `COMPLETED rejected: completion requires evidence_complete patch evidence` |
| 12 | `diff_exists=false` | model validator | `COMPLETED rejected: completion requires patch evidence proving a diff exists` |
| 13 | Scope violation present | model validator | `COMPLETED rejected: completion requires patch evidence without scope violations` |
| 14 | Unresolved blocking finding | model validator | `COMPLETED requires no unresolved blocking finding` |
| 15 | Unsatisfied deterministic criterion | model validator | `COMPLETED requires all criteria satisfied` |
| 16 | Required check absent while the Worker claims it passed | model validator | `COMPLETED rejected: observed results must cover exactly the required checks: missing=['unit-tests'], unexpected=[]` |

Positive control: `test_untampered_completed_record_still_builds_immutable_report`
proves a normal Scenario 1 record still produces the expected report and that
the report is immutable (field assignment raises `ValidationError`; its tuple
collections have no `append`).

**Negative-control verification.** To prove these tests are not vacuous, the
patch was selectively reverted in a throwaway copy of the tree (the working tree
was never modified):

- Reverting only the report boundary to revalidation-without-gate failed exactly
  cases 6–10 plus the shared-gate test.
- Reverting only the new structural invariants failed exactly cases 1, 2, 3, 14,
  16 and all three F-003 rejection tests.
- Reverting `payload` to a plain `dict` failed 6 of the F-004 tests plus the
  event-journal detachment test.

## 7. F-003 evidence

| Path | Test | Result |
|---|---|---|
| Direct construction | `test_direct_construction_rejects_attempt_one_no_progress` | `RunRecord(attempt_count=1, state=FAILED, outcome=FAILED, stop_reason=NO_PROGRESS)` raises `NO_PROGRESS requires attempt_count >= 2 because Attempt 1 has no previous ProgressSnapshot to compare against`. |
| Revalidation of a copied record | `test_revalidation_rejects_copied_attempt_one_no_progress` | A valid `attempt_count=2` `NO_PROGRESS` record copied to `attempt_count=1` is rejected on `RunRecord.model_validate`. |
| Report boundary | `test_report_boundary_rejects_copied_attempt_one_no_progress` | An Attempt-1 COMPLETED record unsafely copied to `FAILED`/`NO_PROGRESS` is rejected by `build_terminal_report`. |
| Positive structural control | `test_no_progress_is_structurally_allowed_from_attempt_two` | `NO_PROGRESS` with `attempt_count=2` constructs successfully. |

The invariant also covers `stop_decision.stop_reason`, not just the canonical
`stop_reason` field, so a `CHECKING` record carrying an Attempt-1 `NO_PROGRESS`
decision is rejected before it can be committed.

Scenario 4 no-progress execution, `ProgressSnapshot` comparison, and progress
evaluation remain **unimplemented and out of scope**.

## 8. F-004 evidence

| Concern | Result |
|---|---|
| Immutable representation | `payload` validates to `FrozenPayload`; nested mappings become `FrozenPayload`, nested sequences become tuples. Top-level and nested item assignment raise `TypeError`; nested list `append` raises `AttributeError`. |
| No mutable internals exposed | The backing dict is wrapped in `MappingProxyType` and stored in a `__slots__` attribute; `payload._data["x"] = 1` raises `TypeError`. No property returns a mutable dict. |
| Input normalization | `dict` inputs remain accepted; list values become tuples; non-mapping payloads are rejected with `ValidationError`; non-string keys are rejected. |
| Anti-aliasing | Mutating the original input dict or its nested list after event creation does not change the event (`test_mutating_original_payload_dict_does_not_reach_the_event`, `test_mutating_original_nested_list_does_not_reach_the_event`). Note the top-level case already held via pydantic's copy; the nested case was the real defect and is the one that fails without this patch. |
| JSON output | `model_dump(mode="json")["payload"]` emits plain dicts and lists. |
| Round-trip | `model_dump_json` → `model_validate_json` preserves content and returns an immutable `FrozenPayload` whose nested list is again a tuple; the restored event compares equal to the original. |
| JSON Schema | `EvaluationEvent.model_json_schema()` still succeeds with `type=object`, `additionalProperties=False` at model level, and `type=object` for `payload`. `tests/domain/test_schema_generation.py` continues to pass unmodified. |
| Event journal deep detachment | `test_append_only_log_returns_detached_event_payloads` now asserts that mutation of a detached snapshot is rejected **and** that the journal's own value is unchanged. |

## 9. Commands and exit codes

| Command | Exit code | Result |
|---|---:|---|
| `git status --short` (initial) | 0 | empty — clean |
| `git status --porcelain=v1 --untracked-files=all` (initial) | 0 | empty — clean |
| `git rev-parse HEAD` (initial) | 0 | `abca5d066fff10d5d085a96b87ec6275d17aff19` |
| `git log --oneline --decorate -8` | 0 | 5-commit baseline graph |
| `python3 -m pytest --collect-only -q` | 0 | `43 + 24 + 3 + 53 = 123` collected |
| `python3 -m pytest -q` | 0 | 123 passed (confirmed by 123 `PASSED` records under `-rA`) |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 53 collected, 53 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 24 collected, 24 passed |
| `python3 -m compileall -q src tests` | 0 | passed |
| `ruff check .` | 0 | no findings |
| `git diff --check` | 0 | no whitespace errors |
| `git diff --cached --check` (pre-Commit E) | 0 | no whitespace errors |
| `git diff --cached --stat` (pre-Commit E) | 0 | 6 files, 786 insertions, 53 deletions |
| `python3 -m mypy src tests` (informational only) | 1 | 136 errors in 4 files — **all in test files**, 0 in `src/`. Baseline was 81 errors in 4 test files, also 0 in `src/`. The increase comes from the newly added tests reusing the same list-literal-versus-tuple call style as the pre-existing tests. Not fixed, per scope. |

Test counts moved from 91 → 123 collected and passed (+32).

## 10. Scope

Confirmed **no** changes to:

```text
docs/**
CLAUDE.md
SYSTEM.md
README.md
.gitignore
pyproject.toml
```

Also unchanged: `src/tomtit/domain/contracts.py`, `src/tomtit/domain/enums.py`,
`src/tomtit/fakes.py`, `src/tomtit/runtime/events.py`,
`src/tomtit/runtime/__init__.py`, `src/tomtit/domain/__init__.py`,
`tests/domain/test_contracts.py`, `tests/domain/test_schema_generation.py`, and
every previously committed audit report.

Commit E changed exactly the six authorized files.

Confirmed **no** Attempt 2, revision, `WAITING_OWNER`, `NO_PROGRESS` execution
logic, Attempt 3, progress comparison, real workspace, subprocess execution,
worker adapter, LLM verifier, database, CLI product UI, Memory, model routing,
or graph behavior was added. `_ALLOWED_TRANSITIONS` is unchanged, so no new
state is reachable. `SYSTEM.md` trailing whitespace was not touched.

No dependency was added; `pydantic_core` ships with the existing `pydantic`
dependency.

## 11. Remaining risks

Still open and explicitly out of scope for this task:

```text
BASELINE-L01
AUDIT-L02
F-005
```

Accepted deferred risks, recorded honestly:

- **Symlink confinement** — artifact validation calls `Path.is_file()`, which
  follows symlinks. A symlink pointing at a regular file outside the workspace
  would pass. Not addressed here by explicit instruction.
- **Artifact hashing** — the gate proves an artifact *exists and is a regular
  file*, not that its contents are the bytes Runtime observed. An artifact
  replaced with different content between CHECKING and report generation would
  pass.
- **Real workspace path confinement** — there is no workspace root, so artifact
  paths are unconstrained absolute paths from the fake check runner. Path
  confinement arrives with the real workspace phase.
- `unresolved_findings` is canonical state with no producer yet; only the fake
  deterministic path exists, so it is always empty in Runtime-reachable records.
  Its validation is exercised by adversarial tests only.
- `FrozenPayload.__hash__` assumes payload values are hashable; a payload
  carrying an unhashable custom object would raise on `hash()`, matching the
  pre-existing behavior of an unhashable `dict` payload.

## 12. Final gate

```text
READY_FOR_CODEX_SOL_REVIEW
COMMIT_3_NOT_AUTHORIZED
```
