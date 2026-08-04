# Claude Corrective Patch — SOL-H01, SOL-M02, SOL-M03, SOL-L04

## 1. Status

```text
STATUS=READY_FOR_CODEX_SOL_REREVIEW

SOL-H01=PENDING_REVIEW
SOL-M02=PENDING_REVIEW
SOL-M03=PENDING_REVIEW
SOL-L04=PENDING_REVIEW

F-002=PENDING_REVIEW
F-003=RESOLVED_REGRESSION_PASSED
F-004=PENDING_REVIEW

COMMIT_3=NOT_AUTHORIZED
```

## 2. Model

- **Model used:** Claude Opus (Claude Code Opus, `claude-opus-5`).
- **Why Opus was required:** the central change reorders a completion operation
  that spans canonical state, event emission, report construction, report
  serialization, and filesystem publication. Getting that ordering right
  required reasoning about which steps are fallible, which mutate canonical
  state, and what a partial failure leaves behind — across the Runtime, domain,
  and reporting authority boundaries at once. The work also touched a custom
  Pydantic value type's freezing semantics and the authoritative
  contract-versus-observation relationship. No downgrade occurred.

## 3. Starting state

- **Starting HEAD:** `9e7129a2951e200453570b0246385b2710ffd71f`
  (`docs(audit): record Sol review for F002 F003 F004`), parent
  `6bf4d4b5799c1d4916e8eef04689fad8024bd325`.
- **Initial Git status:** `git status --short` and
  `git status --porcelain=v1 --untracked-files=all` both empty; branch `main`;
  tracked worktree clean; index clean; no untracked project files. Only the
  permitted ignored generated artifacts existed. Preconditions matched, so no
  `OWNER_DECISION_REQUIRED` was raised.
- **Baseline before changes:** 123 collected, 123 passed.
- **Reports and policy read:** `CLAUDE.md`, `SYSTEM.md`,
  `docs/MVP_EXECUTION_POLICY_V0.md`, `docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md`,
  `REPORT_CODEX_SOL_F002_F003_F004_REVIEW.md`,
  `REPORT_CLAUDE_CORRECTIVE_F002_F003_F004.md`,
  `REPORT_CODEX_TERRA_BASELINE_REREVIEW.md`. All relevant source and tests were
  re-inspected before editing.

## 4. Files changed

### `src/tomtit/runtime/runtime.py`

| Item | Change |
|---|---|
| `run_scenario_1()` | The completion tail (transition, build report, write report, update path, emit `REPORT_GENERATED`) is replaced by a single `self.complete_run()` call. |
| `complete_run()` (new, public) | Owns the entire completion operation: legality, preconditions, preparation, then commit. |
| `transition()` | Now handles non-terminal transitions only. After legality checks it rejects a `COMPLETED` target, directing callers to `complete_run()`. |
| `_assert_transition_legal()` (new) | Extracted terminal-state, allowed-transition, and reason checks, shared by `transition()` and `complete_run()`. |
| `_append_transition_event()` (new) | Extracted transition-event emission, shared by both paths. Event type and payload shape are unchanged. |

**Why:** SOL-H01. Previously `transition()` committed `COMPLETED` and appended
`RUN_COMPLETED`, and only afterwards did `run_scenario_1()` call
`build_terminal_report()`, which re-ran the filesystem-backed gate. A failure
there left canonical `COMPLETED` plus a success event with no report.

**Trade-off:** `transition()` no longer accepts `COMPLETED`, which is a
deliberate narrowing of a public method. This is what makes "completion is one
prepared operation" enforceable rather than conventional. The rejection happens
*after* the legality checks, so `illegal transition` is still reported first for
genuinely illegal moves.

### `src/tomtit/reporting.py`

| Item | Change |
|---|---|
| `build_terminal_report()` | Remains the public trust boundary. Now delegates: `validate_completed_record(record)` then `build_terminal_report_from_validated_record(...)`. |
| `build_terminal_report_from_validated_record()` (new) | Builds the report from an already-proven record. Performs no completion validation and touches no filesystem. |
| `render_terminal_report()` (new) | Serializes a report to a string, no filesystem access. |
| `write_rendered_terminal_report()` (new) | Writes an already rendered string exactly once (`open("x")`). |
| `write_terminal_report()` | Signature unchanged; now composes render + write. |

**Why:** SOL-H01 requirement B needs a clearly named internal entry point that
does not re-run the filesystem gate, while the public boundary keeps
revalidating untrusted input. Splitting render from write lets Runtime prove
serialization succeeds *before* committing.

**Trade-off:** four functions where there were two. Report-construction logic
still exists exactly once; the public boundary is now a two-line composition.

### `src/tomtit/runtime/state.py`

| Item | Change |
|---|---|
| `required_check_coverage_error()` | After stable-ID matching, additionally requires `observed.argv == check.argv` and `observed.cwd_relative == check.cwd_relative` (exact canonical equality), before the exit-code check. |

**Why:** SOL-M02. A matching `check_id` must not silently redefine which command
or working directory the check names. Extending the existing shared predicate —
rather than adding a report-specific check — keeps a single definition, so
Runtime's pre-completion screen, the canonical model validator, and the report
boundary all enforce it.

**Trade-off:** observed results must now mirror the contract exactly. That is
correct for V0, where `RequiredCheck` is authoritative and Runtime copies `argv`
and `cwd_relative` from it. No shell parsing, path resolution, or subprocess
behavior was introduced.

### `src/tomtit/domain/evaluation.py`

| Item | Change |
|---|---|
| `FrozenPayload.__init__` | Assigns `_data` via `object.__setattr__`, the sole permitted moment for internal state assignment. |
| `FrozenPayload.__setattr__` (new) | Raises `AttributeError` for every attribute assignment. |
| `FrozenPayload.__delattr__` (new) | Raises `AttributeError` for every attribute deletion. |

**Why:** SOL-M03. `MappingProxyType` blocked writes *through* `_data` but not
reassignment *of* `_data`, so `event.payload._data = replacement` mutated a
validated event. This is the conventional frozen-object mechanism (the same one
frozen dataclasses use).

**Trade-off:** internal construction must use `object.__setattr__`. As the task
notes, deliberate use of `object.__setattr__` by a caller is out of contract
scope; the guard covers normal attribute operations.

### `tests/scenarios/test_scenario_1.py`

Added 10 tests (5 for SOL-H01, 5 for SOL-M02). Generalized
`ArtifactDeletingClock` to take a `when` predicate so an artifact can be removed
at a chosen point in the completion sequence. Added `runtime_with_clock` and
`event_type_count` helpers. Replaced the `no_progress_values() -> dict[str,
object]` helper with a typed `no_progress_record()` constructor (SOL-L04), and
added a narrow `# type: ignore[index]` to the deliberate read-only-Mapping
assignment (SOL-L04).

**Updated `test_runtime_clock_is_single_authority_for_terminal_chronology`.**
The assertion changed from
`completed_at <= run_completed.timestamp <= report.generated_at <= report_generated.timestamp`
to
`completed_at <= report.generated_at <= run_completed.timestamp <= report_generated.timestamp`.
This is a direct, intended consequence of SOL-H01: the report object is now
prepared while completion is still provisional, so it is stamped before the
`RUN_COMPLETED` event that records the committed transition. All four stamps
still come from the single injected Runtime clock and remain monotonic, and the
event sequence is unchanged (11 events, 6 transition events, last `to_state` is
`COMPLETED`).

### `tests/domain/test_evaluation_models.py`

Added 5 SOL-M03 tests covering top-level slot reassignment, nested slot
reassignment, arbitrary attribute assignment, attribute deletion, and frozen
internals after a JSON round trip.

## 5. SOL-H01 design

**Old ordering (defective):**

```text
transition(COMPLETED)
  → build candidate
  → validate_completed_record(candidate)          [fallible, filesystem]
  → self._record = validated                      [CANONICAL MUTATION]
  → append RUN_COMPLETED                          [HISTORY MUTATION]
run_scenario_1 continues:
  → build_terminal_report(self._record)           [fallible, filesystem AGAIN]
  → write report
  → append REPORT_GENERATED
```

A failure at the second validation left canonical `COMPLETED` and a
`RUN_COMPLETED` event with no report.

**New ordering:**

```text
complete_run()
  → _assert_transition_legal(CHECKING → COMPLETED, TERMINATION_DECIDED)
  → _validate_transition_preconditions(...)
  → StopDecision presence check
  --- preparation, no canonical mutation ---
  → candidate = _build_record(state=COMPLETED, outcome, stop_reason,
                              completion_basis, completed_at=clock())
  → validated = validate_completed_record(candidate)   [the ONLY filesystem gate]
  → report    = build_terminal_report_from_validated_record(validated,
                              generated_at=clock())    [no filesystem]
  → rendered  = render_terminal_report(report)         [no filesystem]
  --- commit point ---
  → self._record = validated
  → append RUN_COMPLETED
  → write_rendered_terminal_report(path, rendered)
  → _replace_record(report_path=...)
  → append REPORT_GENERATED
```

- **Validated candidate created:** `Runtime.complete_run()`, preparation block.
- **Terminal report object prepared:** same block, via
  `build_terminal_report_from_validated_record`.
- **Canonical state committed:** `self._record = validated`, after preparation.
- **Events appended:** `RUN_COMPLETED` immediately after the commit;
  `REPORT_GENERATED` after publication.
- **Why no fallible completion validation remains after canonical mutation:**
  the filesystem-backed gate is called exactly once, in preparation.
  `build_terminal_report_from_validated_record` performs no completion
  validation and no filesystem access, and rendering is pure. The post-commit
  steps are an in-memory assignment, event appends, and one file write.
  `transition()` can no longer reach `COMPLETED` at all, so there is no second
  path that could commit completion without preparation.

**Residual risk (documented, not fixed).** The single remaining post-commit
fallible operation is the report file write itself. `write_rendered_terminal_report`
uses `open("x")`, so an OS-level failure (full disk, permissions, or a
concurrently created file) after the commit point would leave canonical
`COMPLETED` and `RUN_COMPLETED` with no `report.v1.json` and no
`REPORT_GENERATED`. This is a filesystem-durability property, not a completion-
validation ordering defect, and closing it would require the transaction or
workspace machinery this task explicitly forbids. `_assert_scenario_1_preflight`
already rejects a pre-existing `report.v1.json` before `PREPARING`, which
removes the most likely cause. No cross-filesystem ACID behavior is claimed.

## 6. SOL-M02 evidence

All cases were rejected by **both** `validate_completed_record()` and
`build_terminal_report()`.

| Case | Result |
|---|---|
| `argv` mismatch (`("totally","different","command")`) | REJECTED — `COMPLETED rejected: required check unit-tests observed argv ['totally', 'different', 'command'] does not match the contract argv ['python', '-m', 'pytest', 'tests/unit']` |
| `cwd_relative` mismatch (`"different-dir"`) | REJECTED — `... observed cwd_relative 'different-dir' does not match the contract cwd_relative '.'` |
| Both mismatched | REJECTED — `argv` reported first (deterministic order) |
| Matching positive control | ACCEPTED — report emits `argv` and `cwd_relative` equal to the contract |
| Worker claims `unit-tests` succeeded while `argv` contradicts the contract | REJECTED — the claim does not participate; `test_worker_claim_cannot_excuse_contradicting_check_metadata` |

Sol's exact reproduction (`model_copy` with both fields changed) is now rejected
at both boundaries. Because the rule lives in the shared predicate, Runtime's
own pre-completion screen also refuses to attempt completion on such evidence.

## 7. SOL-M03 evidence

- **Frozen-object mechanism:** guarded `__setattr__` / `__delattr__` raising
  `AttributeError`, with `object.__setattr__` used once during `__init__` — the
  same convention frozen dataclasses use. No third-party immutable-collection
  package was added.
- **Top-level slot reassignment:** `event.payload._data = MappingProxyType(...)`
  → `AttributeError: FrozenPayload is immutable; cannot assign attribute '_data'`.
  Payload and serialized output remain the original values.
- **Nested slot reassignment:** `event.payload["nested"]._data = ...` →
  `AttributeError`; nested value unchanged.
- **Arbitrary attribute assignment:** `event.payload.injected = "value"` →
  `AttributeError`, at both top level and nested. (Note: `__slots__` already
  rejected this case before the patch; slot *reassignment* was the actual
  defect.)
- **Attribute deletion:** `del event.payload._data` → `AttributeError`.
- **JSON output:** `model_dump(mode="json")` still emits plain dicts and lists.
- **Round trip:** `model_dump_json` → `model_validate_json` restores a
  `FrozenPayload` with tuple sequences, equal to the original, and the restored
  payload also rejects `_data` reassignment.
- **Schema:** `EvaluationEvent.model_json_schema()` still succeeds;
  `tests/domain/test_schema_generation.py` passes unmodified.
- **Event journal:** append ordering, serialization, detached reads, and the
  11-event Scenario 1 sequence all still pass.

## 8. SOL-L04 evidence

- **Diagnostics removed:** all 55 diagnostics originating from the four flagged
  locations — 1 × `Unsupported target for indexed assignment ("Mapping[str, Any]")`
  at the payload-mutation test, and 54 × `incompatible type "**dict[str, object]"`
  from the three `RunRecord(**no_progress_values(...))` expansions. Verified by
  grep: both patterns now return zero matches.
  - The indexed assignment now carries `# type: ignore[index]` with an
    explanatory comment.
  - `no_progress_values() -> dict[str, object]` was replaced by
    `no_progress_record(*, attempt_count: int) -> RunRecord`, which constructs
    the record with typed keywords instead of dict expansion.
- **Final Mypy count:** `Found 83 errors in 4 files (checked 15 source files)`,
  exit code `1`. Previous count was 136.
- **`src/**` diagnostics:** **zero**, unchanged.
- **No unrelated cleanup:** the remaining 83 are the repository's accepted
  list-input-versus-tuple test style (60 in `tests/domain/test_contracts.py`, 10
  in `tests/domain/test_evaluation_models.py`, 2 in
  `tests/domain/test_schema_generation.py`, 13 in
  `tests/scenarios/test_scenario_1.py`). None were rewritten.

## 9. Commands and exit codes

| Command | Exit code | Result |
|---|---:|---|
| `git status --short` (initial) | 0 | empty — clean |
| `git status --porcelain=v1 --untracked-files=all` (initial) | 0 | empty |
| `git status --ignored --short` (initial) | 0 | only permitted ignored artifacts |
| `git branch --show-current` | 0 | `main` |
| `git rev-parse HEAD` (initial) | 0 | `9e7129a2951e200453570b0246385b2710ffd71f` |
| `python3 -m pytest --collect-only -q` | 0 | `43 + 29 + 3 + 63 = 138` collected |
| `python3 -m pytest -q` | 0 | 138 passed (confirmed by 138 `PASSED` records under `-rA`) |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 63 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 29 passed |
| `python3 -m compileall -q src tests` | 0 | passed |
| `ruff check .` | 0 | no findings |
| `git diff --check` | 0 | no whitespace errors |
| `git diff --cached --check` (pre-Commit G) | 0 | no whitespace errors |
| `git diff --cached --stat` (pre-Commit G) | 0 | 6 files, 434 insertions, 72 deletions |
| `python3 -m mypy src tests` (informational) | 1 | 83 errors in 4 files; 0 under `src/**` |

Collection moved from 123 → 138 (+15: 10 in Scenario 1, 5 in evaluation models).

**Negative-control verification.** Each fix was selectively reverted in a
throwaway copy of the tree (the working tree was never modified), to prove the
new tests are load-bearing:

- Reverting `runtime.py` + `reporting.py` to the pre-patch versions failed
  `test_completion_does_not_revalidate_artifacts_after_canonical_commit`
  (reproducing Sol's exact defect — `CompletionInvariantError: check unit-tests:
  stdout artifact must exist and be a regular file` raised after commit),
  `test_completion_is_not_reachable_through_the_generic_transition`, and the
  chronology test.
- Reverting only the SOL-M02 metadata equality failed all four SOL-M02
  rejection tests.
- Reverting only the SOL-M03 `__setattr__`/`__delattr__` guards failed the two
  slot-reassignment tests, the deletion test, and the round-trip freeze test.

## 10. Scope and non-goals

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
every previously committed audit report. Commit G changed exactly the six
authorized files.

Confirmed **no** Attempt 2, revision, `WAITING_OWNER`, Attempt 3, progress
engine or progress comparison, `NO_PROGRESS` execution logic, real workspace,
subprocess runner, additional worker adapter, LLM verifier, database, UI,
Memory, model routing, or graph behavior was added. `_ALLOWED_TRANSITIONS` is
unchanged, so no new state is reachable; the only reachable terminal state
remains Scenario 1 `COMPLETED`. No dependency was added.

**F-003 regression preserved.** All five F-003 behaviors still pass: Attempt 1
direct-construction rejection, copied-record revalidation rejection, nested
`StopDecision` rejection, report-boundary rejection, and the Attempt 2 positive
structural control. The only change was replacing the untyped construction
helper with a typed one (SOL-L04); the invariant itself is untouched.

## 11. Remaining open findings

Still open and out of scope for this task:

```text
BASELINE-L01
AUDIT-L02
F-005
```

Deferred risks, recorded without fixing:

- **Symlink confinement** — artifact validation uses `Path.is_file()`, which
  follows symlinks.
- **Artifact hashing** — the gate proves an artifact exists and is a regular
  file, not that its contents are the bytes Runtime observed.
- **Real workspace path confinement** — artifact paths remain unconstrained
  absolute paths from the fake check runner.
- **Post-commit report write** — see the residual risk documented in §5.
- `unresolved_findings` still has no producer on the fake Scenario 1 path; it is
  exercised by adversarial tests only.

## 12. Final gate

```text
READY_FOR_CODEX_SOL_REREVIEW
COMMIT_3_NOT_AUTHORIZED
```
