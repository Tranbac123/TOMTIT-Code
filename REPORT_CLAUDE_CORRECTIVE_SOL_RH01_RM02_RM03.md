# Claude Corrective Patch — SOL-RH01, SOL-RM02, SOL-RM03

## STATUS

```text
READY_FOR_CODEX_SOL_REREVIEW
```

## STARTING_HEAD

```text
ca3c9c31583a4a28a335c34dfdada89050acffd4
```

## SOURCE_COMMIT_HASH

```text
96ac75b810cdd10530aa72013205e40f456eeb62
```

## IMPLEMENTED_DIFF_RANGE

```text
ca3c9c31583a4a28a335c34dfdada89050acffd4..96ac75b810cdd10530aa72013205e40f456eeb62
```

## REPORT_COMMIT_EXPECTED_PARENT

```text
96ac75b810cdd10530aa72013205e40f456eeb62
```

## MODEL

Claude Code Opus (`claude-opus-5`). The patch reorders a terminal operation
across canonical state, the event journal, report staging and publication, and
the filesystem evidence gate, and adds a recovery path — reasoning that spans
several authority boundaries at once. No downgrade occurred.

## INITIAL_STATE

Branch `main`; `HEAD` = `ca3c9c31583a4a28a335c34dfdada89050acffd4`; `HEAD^` =
`ac3472fce140dfd45a2a96bd790b4fd271ba6294`. `git status --short` and
`git status --porcelain=v1 --untracked-files=all` were both empty; only the
permitted ignored generated artifacts existed. All required preconditions held,
so no `OWNER_DECISION_REQUIRED` was raised. Baseline before changes: 138
collected, 138 passed.

## FILES_CHANGED

### `src/tomtit/runtime/runtime.py`

| Item | Behavior changed |
|---|---|
| `_PreparedCompletion` (new, private frozen dataclass) | Carries the prepared record, staged report path, final report path, and both prepared events from preparation into commit. |
| `_remove_quietly()` (new, private) | Best-effort `unlink(missing_ok=True)` that swallows `OSError` so cleanup never masks the failure being recovered from. |
| `complete_run()` | Now only: legality checks, preconditions, `StopDecision` presence, then `_prepare_completion()` followed by `_commit_completion()`. |
| `_prepare_completion()` (new, private) | Performs every fallible step with no canonical or journal mutation: allocates all four timestamps, builds the candidate record **including its final `report_path`**, prepares and serializes both terminal events, builds and renders the report, stages the temporary report, and runs `validate_completed_record()` **last**. Any failure removes the staged file and re-raises. |
| `_commit_completion()` (new, private) | Takes the pre-completion record and an event-log checkpoint, assigns the prepared record, publishes the staged report, appends the prepared event batch, then removes the temporary file. On failure it restores the record and journal, removes the final report only if this operation published it, removes the staged file, and re-raises. |
| `_prepare_event()` (new, private) | Builds one `PreparedEvent` from the candidate record via the event log's `prepare()`. |

The obsolete `_replace_record(report_path=...)` post-commit update is gone;
`report_path` is present in the single canonical assignment.

### `src/tomtit/runtime/events.py`

| Item | Behavior changed |
|---|---|
| `PreparedEvent` (new, frozen slotted dataclass) | Holds a validated `EvaluationEvent` and its serialized form. |
| `prepare()` (new) | Allocates the event ID, validates, and serializes without touching history. |
| `append()` | Unchanged observable behavior; now implemented as `prepare()` plus a store and a detached reconstruction. |
| `_append_prepared_batch()` (new, private) | Extends history with already serialized strings only — no ID allocation, validation, or serialization — so a terminal pair lands whole or not at all. |
| `_checkpoint()` / `_restore_to_checkpoint()` (new, private) | Record the history length and drop only entries appended after it. Earlier committed nonterminal events are never touched. |

### `src/tomtit/reporting.py`

| Item | Behavior changed |
|---|---|
| `build_terminal_report_from_validated_record` → `_build_terminal_report_from_validated_record` | Renamed to private (SOL-RM03). The former public name no longer exists in the module. |
| `build_terminal_report()` | Unchanged behavior; its docstring now states it is the sole public trust boundary and no longer directs callers to the no-validation builder. |
| `STAGED_REPORT_NAME` (new) | `.report.v1.json.staged`, a private staged filename distinct from `report.v1.json`. |
| `stage_terminal_report()` (new) | Creates the report directory, writes all rendered bytes to the staged file, flushes, and closes. |
| `publish_staged_report()` (new) | `os.link(staged, final)` then unlink of the staged file. `os.link` raises rather than overwriting, preserving write-once without a read-then-write race. |

`render_terminal_report()`, `write_rendered_terminal_report()`, and
`write_terminal_report()` are unchanged.

### `tests/scenarios/test_scenario_1.py`

Added 26 tests: the SOL-RH01 failure matrix, the SOL-RM02 timing matrix, and the
SOL-RM03 boundary tests. `ArtifactDeletingClock` now targets a specific
completion clock call; `StateRecordingClock` records the state at every clock
call. One prior test,
`test_completion_does_not_revalidate_artifacts_after_canonical_commit`, was
replaced by `test_no_clock_call_occurs_after_canonical_completion`: its hook
fired on a clock call made after canonical assignment, and no such call exists
any more — the stronger property is now asserted directly.

## COMMANDS_AND_EXIT_CODES

| Command | Exit code | Result |
|---|---:|---|
| `git status --short` (initial) | 0 | empty |
| `git status --porcelain=v1 --untracked-files=all` (initial) | 0 | empty |
| `git status --ignored --short` (initial) | 0 | only permitted ignored artifacts |
| `git branch --show-current` | 0 | `main` |
| `git rev-parse HEAD` (initial) | 0 | `ca3c9c31583a4a28a335c34dfdada89050acffd4` |
| `git rev-parse HEAD^` (initial) | 0 | `ac3472fce140dfd45a2a96bd790b4fd271ba6294` |
| `python3 -m pytest --collect-only -q` | 0 | `43 + 29 + 3 + 89 = 164` collected |
| `python3 -m pytest -q` | 0 | 164 passed (confirmed by 164 `PASSED` records under `-rA`) |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 89 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 29 passed |
| `python3 -m compileall -q src tests` | 0 | passed |
| `ruff check .` | 0 | no findings |
| `git diff --check` | 0 | no whitespace errors |
| `git diff --cached --check` (pre-commit) | 0 | no whitespace errors |
| `git diff --cached --stat` (pre-commit) | 0 | 4 files, 757 insertions, 80 deletions |
| `python3 -m mypy src tests` | 1 | `Found 83 errors in 4 files (checked 15 source files)`; **0 under `src/**`** |

Total test count moved 138 → 164 (+26). The Mypy total is unchanged at 83
because SOL-RL04 is explicitly deferred and no test typing was altered.

## EVIDENCE

### Successful terminal consistency

A normal Scenario 1 run produced exactly:

```text
state=COMPLETED
outcome=COMPLETED
stop_reason=ACCEPTANCE_SATISFIED
report_path=<report_directory>/report.v1.json   (present in the committed record)
RUN_COMPLETED=1
REPORT_GENERATED=1
final report files=1
staged temporary files=0
event count=11
report built from the same validated completion snapshot=True
```

`os.link` on the published report raises `FileExistsError`, so write-once holds.

### Supported failure matrix

Every case below ended in the defined failure result — `state=CHECKING`,
`outcome=None`, `stop_reason=None`, `completed_at=None`, `report_path=None`,
`RUN_COMPLETED=0`, `REPORT_GENERATED=0`, no final report, no staged file — and
each is asserted by a named test.

| # | Injected failure | Injection mechanism | Result |
|---:|---|---|---|
| 1 | `RUN_COMPLETED` event-ID generation | injected `event_id_factory` raises on event 10 | clean failure |
| 2 | `RUN_COMPLETED` event validation/serialization | injected factory returns an empty ID → `ValidationError` | clean failure |
| 3 | `REPORT_GENERATED` event-ID generation | injected factory raises on event 11 | clean failure |
| 4 | `REPORT_GENERATED` event validation/serialization | injected factory returns an empty ID on event 11 | clean failure |
| 5 | Report-directory preparation | `report_directory` is an existing regular file (Sol's reproduction) | clean failure; the occupying file is untouched |
| 6 | Temporary report creation | staged path pre-created as a directory → `IsADirectoryError` | clean failure |
| 7 | Temporary report write/flush | bounded test double writes half the bytes then raises | clean failure; **partial staged file removed** |
| 8 | Final report publication after canonical assignment | `publish_staged_report` patched to raise | recovered to clean failure |
| 9 | Prepared event-batch append after canonical assignment | `_append_prepared_batch` patched to raise | recovered to clean failure |
| 10 | Failure after the final report was published | recording publish + failing batch append | report published then removed by recovery; clean failure |
| 11 | Recovery scope | same injection, asserting the 9 earlier nonterminal events survive unchanged | earlier history intact |
| 12 | Retry after recovery | failure, `monkeypatch.undo()`, then `complete_run()` | succeeds **exactly once**: 1 report, 1 `RUN_COMPLETED`, 1 `REPORT_GENERATED`, no staged file |
| 13 | Repeated completion after success | second `complete_run()` | `InvalidTransitionError`; record identity, event count, and report bytes unchanged |
| 14 | No duplicate terminal events | parametrized over 4 injections | `RUN_COMPLETED=0` and `REPORT_GENERATED=0` in every case |

### Recovery result

Recovery restores the pre-completion record, truncates the journal to its
checkpoint, removes the final report **only when this operation published it**,
and removes the staged file. Case 10 proves the published-report removal path;
case 11 proves earlier committed nonterminal events are never disturbed.

### Retry result

After a recovered failure the run is still `CHECKING` with its `StopDecision`
intact, and `complete_run()` succeeds on the next call producing exactly one
report and one terminal event pair (test 12).

### Artifact timing cases (SOL-RM02)

| Case | Behavior |
|---|---|
| A — unavailable before completion preparation | `CompletionInvariantError`; clean failure result |
| B — unavailable during final validation | a bounded double removes a later artifact mid-walk; the gate detects it on the `stderr artifact` entry; clean failure result |
| C — unavailable after the CHECKING gate but before the final gate | parametrized over **all four** pre-commit clock calls; every one is rejected with a clean failure result |
| D — unavailable after canonical completion | **recorded, not rejected**: the run stays `COMPLETED` with its report, path, and both events |

Case C is the direct regression for SOL-RM02: previously the deletion at the
`report.generated_at` clock call slipped through because the gate ran before it.
The gate is now the final Runtime-controlled step, after all timestamp
allocation, event construction, report rendering, and staging.

### Public report-boundary result (SOL-RM03)

- `build_terminal_report_from_validated_record` is **absent** from
  `tomtit.reporting`; `_build_terminal_report_from_validated_record` exists.
- `build_terminal_report()` rejects Sol's exact bypass record — a completed
  record copied to `state=CHECKING` — with `ValidationError` from the
  reconstruction gate.
- A repository scan asserts the private name appears only in `reporting.py`
  (definition) and `runtime/runtime.py`, and that the call site is inside
  `Runtime._prepare_completion`, i.e. only after Runtime's own authoritative
  validation.
- Normal Runtime completion still produces the expected report.

### Chronology result

Preserved and asserted:

```text
completed_at <= report.generated_at <= RUN_COMPLETED.timestamp <= REPORT_GENERATED.timestamp
```

All four timestamps are allocated from the single injected Runtime clock during
preparation, in that order. Event append order remains `RUN_COMPLETED` then
`REPORT_GENERATED`, event timestamps remain sorted, and the successful Scenario
1 event sequence is unchanged at 11 events with 6 transition events.
`test_no_clock_call_occurs_after_canonical_completion` asserts no clock call
observes `COMPLETED`.

### Preserved F-003 and F-004 regression evidence

- **F-003:** all four `no_progress` tests pass — Attempt 1 direct construction
  rejected, copied-record revalidation rejected, nested `StopDecision` rejected,
  report boundary rejected, and the Attempt 2 structural positive control
  accepted. The invariant was not modified.
- **F-004 / SOL-M03:** all 14 payload and frozen-payload tests pass.
  `src/tomtit/domain/evaluation.py` was not modified.
- **SOL-M02:** the required-check `check_id`/`argv`/`cwd_relative` authority
  tests continue to pass; `required_check_coverage_error` was not modified.

### Negative-control verification

Each fix was selectively reverted in a throwaway copy of the tree (the working
tree was never modified) to prove the new tests are load-bearing:

- Removing the commit-section recovery failed 5 tests, including publication
  failure, post-publication recovery, history preservation, and retry.
- Restoring the old gate ordering (validate immediately after candidate
  construction) failed exactly the three post-gate timing cases — Sol's SOL-RM02
  window, reproduced.
- Re-adding the former public builder name failed the absence test.
- Reverting the staged-cleanup fix failed the partial-staged-write test.

## CLAIMED_FIXED_PENDING_REVIEW

```text
SOL-RH01=CLAIMED_FIXED_PENDING_REVIEW
SOL-RM02=CLAIMED_FIXED_PENDING_REVIEW
SOL-RM03=CLAIMED_FIXED_PENDING_REVIEW
F-002=CLAIMED_FIXED_PENDING_REVIEW
```

## RISKS

Bounded risks that remain. None of these is presented as a product-level
guarantee.

- **Process crash or power loss.** This patch covers in-process exceptions only.
  A crash between canonical assignment and the event-batch append, or between
  publication and journal append, leaves on-disk and in-memory state
  unreconciled. There is no crash recovery, no journal durability, and no fsync;
  `stage_terminal_report` flushes to the OS but does not fsync.
- **Cleanup failure.** `_remove_quietly` swallows `OSError` so it cannot mask
  the original exception. If removal of the staged or published report fails,
  the file survives while canonical state is correctly restored. Recovery
  therefore guarantees canonical and journal consistency, not guaranteed file
  removal.
- **Publication primitive.** `os.link` is used because it fails rather than
  overwrites. On a filesystem without hard-link support this raises, which would
  surface as a supported publication failure and a clean recovered failure
  result rather than a partial completion.
- **Post-completion artifact loss** is recorded behavior (case D), not a
  rejection.
- **Deferred, unchanged:** symlink confinement, artifact content hashing, and
  real-workspace path confinement. `Path.is_file()` still follows symlinks, and
  the gate proves existence and regular-file type, not content identity.
- `unresolved_findings` still has no producer on the fake Scenario 1 path.

## SCOPE_AND_NON_GOALS

Files changed: `src/tomtit/runtime/runtime.py`, `src/tomtit/runtime/events.py`,
`src/tomtit/reporting.py`, `tests/scenarios/test_scenario_1.py`. Confirmed **no**
changes to `CLAUDE.md`, `SYSTEM.md`, `README.md`, `.gitignore`, `pyproject.toml`,
`docs/**`, `src/tomtit/domain/**`, `src/tomtit/fakes.py`,
`src/tomtit/runtime/__init__.py`, or `tests/domain/**`. No dependency was added.

Confirmed no Scenario 2, Attempt 2, revision, `WAITING_OWNER`, Attempt 3,
`NO_PROGRESS` execution, progress engine, real adapter, subprocess runner,
database, UI, Memory, routing, graph behavior, general transaction framework, or
production crash recovery was added. `_ALLOWED_TRANSITIONS` is unchanged, so no
new state is reachable. `SOL-RL04`, `BASELINE-L01`, `AUDIT-L02`, and `F-005`
were not touched, as instructed.

## UNPROVEN_ITEMS

- Crash and power-loss consistency is **not proven** and not implemented.
- Durability of the published report is **not proven**; no fsync is performed.
- Successful removal of the staged or published file during recovery is **not
  guaranteed**; only canonical and journal restoration is asserted.
- Concurrent or parallel completion of the same run is **not proven**; the fake
  phase is single-threaded and single-run.
- Behavior on filesystems without hard-link support is **not exercised** by the
  suite; it is reasoned about, not tested.
- Artifact content identity between the CHECKING gate and completion is **not
  proven** — only existence and regular-file type.
- The exhaustiveness of the supported failure matrix is **not proven**; it covers
  the enumerated injection points, not every conceivable `OSError` origin inside
  the standard library.
- `F-002` is claimed fixed pending review; it is not independently verified here.

## NEXT_GATE

```text
CODEX_SOL_CORRECTIVE_REREVIEW
COMMIT_3_NOT_AUTHORIZED
```
