# Claude Final Corrective Patch — SOL-RH01 / F-002 cleanup and retryability

## STATUS

```text
READY_FOR_CODEX_SOL_CORRECTIVE_REREVIEW
```

## STARTING_HEAD

```text
bfe9fc4a662c07a2de286dbb7609547b907758c4
```

## SOURCE_COMMIT_HASH

```text
368e6870c9dfb789273e62b066ab8942452e1241
```

## IMPLEMENTED_DIFF_RANGE

```text
bfe9fc4a662c07a2de286dbb7609547b907758c4..368e6870c9dfb789273e62b066ab8942452e1241
```

## REPORT_COMMIT_EXPECTED_PARENT

```text
368e6870c9dfb789273e62b066ab8942452e1241
```

## MODEL

Claude Code Opus (`claude-opus-5`). The patch changes the commit point of a
terminal operation spanning canonical state, the event journal, and write-once
filesystem publication, and must hold a rollback/retry contract across all
three. No downgrade occurred.

## INITIAL_STATE

Branch `main`; `HEAD` = `bfe9fc4a662c07a2de286dbb7609547b907758c4`; `HEAD^` =
`e85160941e5405773885d81e29a813ff009c9e50`. The only untracked project files
were the three permitted governance candidates
(`docs/OFFICIAL_BUILD_AND_AI_CODING_PROCESS_V1.md`,
`docs/EVALUATION_CONTRACT_V1.md`, `docs/VERIFIER_POLICY_V1.md`); they were not
modified, staged, or committed. Tracked worktree and index were clean. All
preconditions held, so no `OWNER_DECISION_REQUIRED` was raised.

## FILES_CHANGED

### `src/tomtit/runtime/runtime.py` — `Runtime._commit_completion()`

The commit section is reordered so that **creating the final report is the
commit point and the last fallible step**:

```text
assign completed record
→ append the prepared terminal-event batch
→ create the write-once final report        ← commit point
→ best-effort staged-file cleanup
```

The `published` flag and the final-report deletion in the rollback handler are
**removed**. Rollback now only restores the canonical record, restores the event
checkpoint, removes the staged file best-effort, and re-raises. It never deletes
a final report, because no code path can reach the handler with one created by
this operation.

*Reason:* Codex Path A required rollback to delete an already published report;
a failed deletion stranded it and blocked retry. With publication last, a
pre-publication failure has nothing to delete.

*Trade-off:* the `REPORT_GENERATED` event is now appended microseconds before
the report file exists. No externally observable state ever shows that event
without the file, because a publication failure rolls the event back. Policy
§18.2 is still satisfied: canonical state becomes terminal before the file is
written.

### `src/tomtit/reporting.py` — `publish_staged_report()`

Now performs exactly one filesystem operation, `os.link(staged, final)`, and
returns as soon as the final report exists. The `staged.unlink()` call was
**removed** from this function; staged cleanup is now the caller's separate
best-effort step.

*Reason:* Codex Path B relied on a publisher that created the final report and
then raised, so the caller could not tell the terminal result had already been
committed. `os.link` is a single atomic syscall — it either creates the name and
returns, or raises without creating it — so the return value is now an
unambiguous signal. Write-once behavior is unchanged: `os.link` raises rather
than overwriting.

`stage_terminal_report()` is unchanged apart from a docstring correction.

### `tests/scenarios/test_scenario_1.py`

Added 10 tests covering requirements 9.1–9.9 plus a direct check that the real
`_remove_quietly` swallows a removal failure. One existing test,
`test_event_batch_append_failure_recovers_after_report_publication`, was
rewritten as
`test_event_batch_append_failure_happens_before_any_report_exists`: under the
new ordering an event-append failure can no longer occur *after* publication, so
the test now asserts the publisher is never reached — which is requirement 9.5's
premise.

A shared `assert_no_contradictory_terminal_artifact()` helper encodes the
forbidden state directly:

```text
canonical CHECKING + zero terminal events + a present report.v1.json
```

## COMMANDS_AND_EXIT_CODES

| Command | Exit code | Result |
|---|---:|---|
| `git status --short` (initial) | 0 | only the three permitted governance files |
| `git status --porcelain=v1 --untracked-files=all` (initial) | 0 | same three files |
| `git status --ignored --short` (initial) | 0 | three governance files plus ignored caches |
| `git branch --show-current` | 0 | `main` |
| `git rev-parse HEAD` (initial) | 0 | `bfe9fc4a662c07a2de286dbb7609547b907758c4` |
| `git rev-parse HEAD^` (initial) | 0 | `e85160941e5405773885d81e29a813ff009c9e50` |
| `python3 -m pytest --collect-only -q` | 0 | `43 + 29 + 3 + 99 = 174` collected |
| `python3 -m pytest -q` | 0 | 174 passed (confirmed by 174 `PASSED` records under `-rA`) |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 29 passed |
| `python3 -m compileall -q src tests` | 0 | passed |
| `ruff check .` | 0 | no findings |
| `git diff --check` | 0 | no whitespace errors |
| `git diff --cached --check` (pre-commit) | 0 | no whitespace errors |
| `git diff --cached --stat` (pre-commit) | 0 | 3 files, 308 insertions, 17 deletions |
| `python3 -m mypy src tests` | 1 | `Found 83 errors in 4 files`; **0 under `src/**`** |

Test count moved 164 → 174 (+10). The Mypy total is unchanged at 83; SOL-RL04
is deferred and no test typing was altered.

## EVIDENCE

### Event-before-publication ordering

`_commit_completion` appends the prepared terminal-event batch **before**
calling `publish_staged_report`. Proven behaviorally by
`test_event_batch_append_failure_happens_before_any_report_exists`, which
injects an event-append failure through a recording publisher and asserts the
publisher was **never called** (`published == []`) and no report exists.

### Pre-publication rollback

| Requirement | Test | Result |
|---|---|---|
| 9.1 event append failure | `test_event_append_failure_before_publication_is_fully_retryable` | `CHECKING`, 0 terminal events, no final report, no staged file; retry succeeds exactly once |
| 9.2 partial event append | `test_partial_event_append_is_rolled_back_and_retryable` | one event lands then raises; journal restored to 9 nonterminal events; retry yields 11 events and one report |
| 9.3 final-link creation failure | `test_final_link_creation_failure_is_fully_retryable` | publisher raises without creating; `CHECKING`, 0 events, no report; retry succeeds |
| 9.9 history preservation | `test_earlier_history_is_byte_identical_after_pre_publication_rollback` | the 9 earlier serialized events are byte-identical before and after rollback, and remain the first 9 after a successful retry |

### Post-publication staged-cleanup failure

`test_staged_cleanup_failure_after_publication_keeps_the_terminal_result` (9.4)
patches cleanup to remove nothing and asserts:

```text
state=COMPLETED
outcome=COMPLETED
stop_reason=ACCEPTANCE_SATISFIED
report_path=<report_directory>/report.v1.json
RUN_COMPLETED=1
REPORT_GENERATED=1
final report present
staged file survives (the bounded, accepted cost)
repeated completion rejected with InvalidTransitionError
no contradictory CHECKING state
```

`test_staged_unlink_failure_after_publication_keeps_completion_committed`
(9.6b) models the former in-publisher unlink failing in its new position, after
the commit point, with the same committed outcome.

`test_publisher_does_not_remove_the_staged_report` (9.6a) proves structurally
that `publish_staged_report` leaves the staged file in place and that a second
call raises `FileExistsError`, so write-once holds and the publisher cannot be
ambiguous.

`test_remove_quietly_swallows_removal_failure` confirms the real helper does not
raise when removal is impossible.

### Codex reproductions 6 and 7, re-run directly

Reproduction 6 (final-report removal fails during rollback), with cleanup
patched to remove nothing:

```text
after failure: state=CHECKING events=9 RUN_COMPLETED=0 final=False staged=True
after retry  : state=COMPLETED events=11 RUN_COMPLETED=1 final=True staged=False
retry succeeded WITHOUT manual cleanup: True
```

Reproduction 7 (link succeeds, staged unlink raises):

```text
after run: state=COMPLETED events=11 RUN_COMPLETED=1 final=True staged=True
committed terminal result, not contradictory: True
```

Neither produces the forbidden `CHECKING + 0 events + report present` state, and
retry no longer raises `FileExistsError`.

### Normal success (9.7) and repeated completion (9.8)

`test_normal_success_leaves_one_report_and_no_staged_file`: exactly one final
report, **zero** staged files, one `RUN_COMPLETED`, one `REPORT_GENERATED`,
`report_path` set. `test_repeated_completion_after_success_is_rejected_without_mutation`
(pre-existing): `InvalidTransitionError`, no additional events, report bytes
unchanged.

### Retry result

Every pre-publication failure test performs an explicit retry after removing the
injection. Each retry produces exactly one final report, exactly one
`RUN_COMPLETED`, exactly one `REPORT_GENERATED`, and no staged file, with no
manual filesystem cleanup — including the case where cleanup removed nothing
during the failed attempt (9.5).

### Negative-control results

Run in throwaway copies; the tracked working tree was never modified.

| Control | Restored defect | Failing tests |
|---|---|---|
| 1 | Old publication-before-event ordering | `test_event_batch_append_failure_happens_before_any_report_exists`, `test_recovery_preserves_earlier_nonterminal_event_history`, `test_completion_retry_after_recovery_succeeds_exactly_once`, `test_event_append_failure_before_publication_is_fully_retryable`, `test_partial_event_append_is_rolled_back_and_retryable`, `test_final_report_removal_failure_path_cannot_occur`, `test_earlier_history_is_byte_identical_after_pre_publication_rollback` (7) |
| 2 | Ambiguous publisher that links then unlinks in one call | `test_staged_cleanup_failure_after_publication_keeps_the_terminal_result`, `test_publisher_does_not_remove_the_staged_report` (2) |
| 3 | Rollback to `CHECKING` after final publication (the exact old commit body) | `test_event_batch_append_failure_happens_before_any_report_exists`, `test_final_report_removal_failure_path_cannot_occur` (2) — the latter fails with the literal assertion message `canonical CHECKING with no terminal events must never leave a final report`, reproducing Codex Path A |

### Preserved findings

| Finding | Evidence |
|---|---|
| SOL-RM02 | 16 artifact-timing tests pass; the evidence gate is still the last preparation step and no clock call follows it |
| SOL-RM03 | 7 report-boundary tests pass; the former public builder name is still absent and the private helper still has only Runtime's validated call site |
| SOL-M02 | 5 check-metadata authority tests pass; `required_check_coverage_error` untouched |
| SOL-M03 / F-004 | 14 payload/frozen tests pass; `src/tomtit/domain/evaluation.py` untouched |
| F-003 | 4 `NO_PROGRESS` tests pass; `src/tomtit/runtime/state.py` untouched |
| Chronology and event order | `completed_at <= report.generated_at <= RUN_COMPLETED.timestamp <= REPORT_GENERATED.timestamp` still asserted; event sequence still 11 events with `RUN_COMPLETED` before `REPORT_GENERATED` |
| Write-once | `os.link` still refuses to overwrite; `test_terminal_report_is_write_once` still passes |

## CLAIMED_FIXED_PENDING_REVIEW

```text
SOL-RH01=CLAIMED_FIXED_PENDING_REVIEW
F-002=CLAIMED_FIXED_PENDING_REVIEW

SOL-RM02=PRESERVED_PENDING_REVIEW
SOL-RM03=PRESERVED_PENDING_REVIEW
SOL-M02=PRESERVED_PENDING_REVIEW
SOL-M03=PRESERVED_PENDING_REVIEW
F-003=PRESERVED_PENDING_REVIEW
F-004=PRESERVED_PENDING_REVIEW
```

## RISKS

- **Staged file may survive a cleanup failure.** This is the accepted bounded
  cost. The staged name is private and fixed, and preparation overwrites it with
  `open("w")`, so a stale staged file does not block a later run. It is never
  the terminal report.
- **Persistent filesystem failures.** If the report directory becomes
  permanently unwritable, staging fails during preparation and completion fails
  cleanly, but no run can complete until the environment is repaired.
- **`os.link` dependency.** Publication requires hard-link support in the report
  directory. On a filesystem without it, `os.link` raises before creating
  anything, which is a clean pre-publication failure — but this is reasoned
  about, not exercised by the suite.
- **Report file durability.** No `fsync` is performed. The report bytes are
  flushed to the OS during staging; power loss before the OS flushes may lose
  them.
- **Process crash or power loss** remains outside the bounded in-process model:
  a crash between the event append and the link can leave canonical in-memory
  state that no longer exists and a report that does not, or vice versa. There
  is no crash recovery and no journal durability.
- **Deferred and untouched:** `SOL-RL04`, `BASELINE-L01`, `AUDIT-L02`, `F-005`,
  symlink confinement, artifact content hashing, real-workspace path
  confinement.

## SCOPE_AND_NON_GOALS

Files changed: `src/tomtit/runtime/runtime.py`, `src/tomtit/reporting.py`,
`tests/scenarios/test_scenario_1.py`. Confirmed **no** changes to
`src/tomtit/runtime/events.py`, `src/tomtit/runtime/state.py`,
`src/tomtit/domain/**`, `src/tomtit/fakes.py`, `src/tomtit/runtime/__init__.py`,
`tests/domain/**`, `docs/**`, `CLAUDE.md`, `SYSTEM.md`, `README.md`,
`pyproject.toml`, `.gitignore`, or any existing audit report. The three
governance candidates remain untracked, unmodified, and unstaged. No dependency
was added.

No general transaction system, database, crash-recovery mechanism, Scenario 2,
Attempt 2, revision, `WAITING_OWNER`, Attempt 3, progress engine, real adapter,
subprocess runner, UI, Memory, routing, or graph behavior was added.
`_ALLOWED_TRANSITIONS` is unchanged. No gate-evidence file was created;
`docs/gate-evidence/GATE_1_SCENARIO_1.md` is out of scope for this task.

## UNPROVEN_ITEMS

- Crash and power-loss consistency is **not proven** and not implemented.
- Durability of the published report is **not proven**; no `fsync` is performed.
- Behavior on filesystems without hard-link support is **not exercised** by the
  suite.
- Concurrent or parallel completion of the same run is **not proven**.
- Removal of the staged file is **not guaranteed**; only canonical state, the
  event journal, and the absence of a final report before the commit point are
  guaranteed on rollback.
- The exhaustiveness of the supported failure matrix is **not proven**; it covers
  the enumerated injection points, not every possible `OSError` origin.
- Whether the corrected behavior fully satisfies the Gate 1 product evidence is
  **claimed, not independently verified**; that is the reviewer's determination.

## NEXT_GATE

```text
CODEX_SOL_CORRECTIVE_REREVIEW
NO_ADDITIONAL_IMPLEMENTATION_CYCLE_AUTHORIZED
```
