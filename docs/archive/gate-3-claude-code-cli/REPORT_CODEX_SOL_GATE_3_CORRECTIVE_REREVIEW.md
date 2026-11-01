# Final Bounded Gate 3 Corrective Rereview

## VERDICT

`APPROVE_WITH_NON_BLOCKING_FINDINGS`

G3-SOL-01 and G3-SOL-02 are resolved. The corrected adapter proves process-group
quiescence on normal and timeout paths, requires reader completion, freezes
stream evidence before the final workspace snapshot, validates explicit
executables, and normalizes supported process-start failures into immutable
results. All required offline tests and ten independent weakened-condition
controls support those conclusions. No real Claude CLI invocation was made.

## TARGET_SOURCE_COMMIT

`70a23eae1b9cd043d16484e529d1f5eb6faa045e`

## REVIEWED_DIFF_RANGE

`8f3ab4d6fd090f4656e6d37169fdd3d38348a37c..70a23eae1b9cd043d16484e529d1f5eb6faa045e`

## REVIEW_REPORT_EXPECTED_PARENT

`187e85b1cd634e5fdf803be57b8789152f1fe663`

Preflight matched exactly: branch `main`, HEAD `187e85b1...`, parent
`70a23eae...`, grandparent `8f3ab4d6...`, and a clean index/worktree.

## FILES_REVIEWED

The corrective diff changes exactly the three authorized files:

- `src/tomtit/adapters/claude_code_cli.py`
- `tests/adapters/test_claude_code_cli.py`
- `tests/scenarios/test_claude_code_cli_spike.py`

The correction report is the only change in `70a23eae..187e85b1`:
`REPORT_CLAUDE_GATE_3_PROCESS_BOUNDARY_CORRECTION.md`. The prior Sol review,
current and historical Gate 3 evidence, spike script, Runtime/domain code,
governance, and Gate 1/Gate 2 scenarios were inspected for preservation.

## COMMANDS_AND_EXIT_CODES

| Command / probe | Exit | Result |
|---|---:|---|
| `python3 -m pytest --collect-only -q` | 0 | 369 collected |
| `python3 -m pytest -p no:cacheprovider -q` | 0 | 369 passed |
| `python3 -m pytest -p no:cacheprovider tests/adapters -q` | 0 | 44 passed |
| Gate 3 scenario file | 0 | 3 passed |
| Scenario 1 file | 0 | 99 passed |
| Scenario 2 file | 0 | 137 passed |
| focused corrective `-k` selection | 0 | 19 passed |
| `python3 -m compileall -q src tests scripts` | 0 | pass |
| `ruff check .` | 0 | no findings |
| `git diff --check` | 0 | clean |
| `python3 -m mypy src` | 0 | no issues in 13 source files |
| fail-closed edge probe | 0 | all six invariants passed |
| ten isolated negative controls | 1 | 10 failed as expected |

The full-suite count is the sum printed by collection: 44 + 43 + 40 + 3 + 3
+ 99 + 137 = 369. Neither the spike script nor `claude -p` was run.

## G3_SOL_01_RESULTS

`G3-SOL-01=RESOLVED`

The prior defect was that cleanup occurred only after timeout. `_execute()`
now retains the child-created pgid and unconditionally calls
`ensure_process_group_quiescent()` after either a normal parent exit or a
timeout. Only `QUIESCENT` or positively verified `CLEANED` permits success.
Reader joins follow group cleanup, and a live reader forces
`FAILED / PROCESS_CLEANUP_FAILED`.

The exact delayed-write shape passes offline: a parent writes allowed
`result.txt`, spawns a pipe-inheriting descendant that intends to rewrite
`README.md` after three seconds, and exits 0. The adapter returns bounded with
exit 0, `timed_out=false`, `SUCCEEDED`, only `result.txt` changed, no live
reader, and the descendant absent; after four seconds `README.md` remains
unchanged. The persisted-evidence scenario repeats the delayed wait and proves
the manifest and patch still match the workspace.

## G3_SOL_02_RESULTS

`G3-SOL-02=RESOLVED`

`run()` now sends every explicit override through
`discover_claude_executable(explicit_path)`. Nonexistent paths, directories,
and non-executable files return `UNAVAILABLE / EXECUTABLE_UNAVAILABLE`, with
null exit, `timed_out=false`, and no `Popen` call. The guarded `Popen` boundary
independently maps `FileNotFoundError`, `PermissionError`, and other `OSError`
subclasses into result values.

## PROCESS_GROUP_QUIESCENCE_RESULTS

`PROCESS_GROUP_QUIESCENCE=RESOLVED`

The cleanup sequence is positive absence probe, SIGTERM, bounded absence wait,
SIGKILL when needed, and a second bounded absence verification. Indeterminate
probe state, failed signal delivery, or a group persisting after SIGKILL all
return `PROCESS_GROUP_CLEANUP_FAILED`. A probe independently confirmed both
signals are attempted for persistence. Pgid values at or below one and
TOMTIT's own process group are refused. The direct parent's genuine exit code
is preserved, and descendant cleanup after normal exit does not set
`timed_out`.

## READER_THREAD_RESULTS

`READER_THREAD_COMPLETION=RESOLVED`

Both pipe readers are joined only after the process group is quiescent. Their
`is_alive()` state is checked; a simulated live reader returned
`FAILED / PROCESS_CLEANUP_FAILED`. A normal pipe-holder reproduction leaves no
adapter reader thread alive and returns without consuming the reader-join
timeout. Stream bytes, digests, and truncation flags are copied into a frozen
execution outcome after the joins.

## FINAL_SNAPSHOT_RESULTS

`FINAL_SNAPSHOT_ORDERING=RESOLVED`

The structural sequence is:

```text
before snapshot
child new session/process group
parent exit or timeout
group cleanup and verified quiescence
reader completion
stream evidence freeze
final snapshot
changed-path/status/result derivation
```

The instrumented regression observed exactly
`[snapshot, cleanup, snapshot]`. No successful result can be formed while
cleanup is unproven or a reader remains alive.

## DELAYED_WRITE_RESULTS

The original delayed descendant attack is prevented: the descendant is gone
before return and `README.md` remains `before` after its intended write window.
The early-write counterpart remains visible: when the descendant changes
`README.md` before cleanup, the file remains modified, changed/unexpected paths
include `README.md`, and status is `SCOPE_VIOLATION`, never success. Cleanup
does not hide or revert an observed violation.

## EXECUTABLE_VALIDATION_RESULTS

`EXPLICIT_EXECUTABLE_VALIDATION=RESOLVED`

All three invalid override shapes return a frozen non-success result and never
reach `Popen`. Negative control 7 asserts the former bypass and fails because
the validation guard is load-bearing.

## PROCESS_START_NORMALIZATION_RESULTS

`PROCESS_START_NORMALIZATION=RESOLVED`

The independently exercised mappings are:

| Raised by `Popen` | Result |
|---|---|
| `FileNotFoundError` | `UNAVAILABLE / EXECUTABLE_UNAVAILABLE` |
| `PermissionError` | `UNAVAILABLE / EXECUTABLE_NOT_EXECUTABLE` |
| generic `OSError`, including `BlockingIOError` | `FAILED / PROCESS_START_FAILED` |

Every start-failure result has ordered timestamps, nonnegative duration,
populated before/after workspace hashes and path tuples, empty stream excerpts,
correct empty SHA-256 values, null exit, `timed_out=false`, and frozen fields.
Error text uses exception class and errno, not secret-bearing `str(exc)`.

## TOTAL_RESULT_BOUNDARY_RESULTS

`TOTAL_RESULT_NORMALIZATION=RESOLVED`

Discovery unavailable, invalid overrides, all four requested start exceptions,
timeout, cleanup failure, and live-reader failure each return
`ClaudeCodeCLIResult`. Reader `OSError`/`ValueError`, process-group
`ProcessLookupError`/`PermissionError`/`OSError`, and bounded timeout expiry are
handled inside the process boundary. No supported process failure escaped the
reviewed sweeps; unrelated programming defects remain outside this contract.

## OFFLINE_TEST_RESULTS

All expected counts match: 369 total, 44 adapter, 3 Gate 3 scenario, 99
Scenario 1, and 137 Scenario 2. The independent 19-test corrective selection
covered explicit validation, start normalization and immutability, total
failure results, normal descendants, pipe holders, delayed and early writes,
SIGKILL escalation, cleanup ambiguity/failure, live readers, final ordering,
and frozen output/evidence.

## NEGATIVE_CONTROLS

The harness existed only at
`/private/tmp/test_tomtit_gate3_corrective_negative_controls.py`. Each test
asserted the restored invalid behavior against the current guard; all ten
failed, as required.

| # | Restored invalid condition | Exact failing control | Load-bearing guard |
|---:|---|---|---|
| 1 | normal exit skips cleanup | `test_nc01_normal_exit_skips_group_cleanup` | unconditional group cleanup |
| 2 | final snapshot precedes cleanup | `test_nc02_final_snapshot_before_cleanup` | `_execute` completes cleanup before returning |
| 3 | live reader permits success | `test_nc03_live_reader_allows_success` | reader-alive status veto |
| 4 | ambiguous group means quiescent | `test_nc04_ambiguous_group_treated_as_quiescent` | three-valued fail-closed probe |
| 5 | SIGKILL fallback absent | `test_nc05_sigkill_fallback_removed` | TERM-to-KILL escalation |
| 6 | delayed README write lands after success | `test_nc06_delayed_write_after_success` | pre-snapshot descendant removal |
| 7 | explicit invalid path reaches `Popen` | `test_nc07_explicit_path_bypasses_validation` | explicit-path validation boundary |
| 8 | `FileNotFoundError` escapes | `test_nc08_10_popen_exception_escapes[exception0]` | start normalization |
| 9 | `PermissionError` escapes | `test_nc08_10_popen_exception_escapes[exception1]` | start normalization |
| 10 | generic `OSError` escapes | `test_nc08_10_popen_exception_escapes[exception2]` | start normalization |

Result: `10 failed in 3.67s`, expected exit 1. No tracked file or real service
was touched.

## REAL_EVIDENCE_PRESERVATION

The successful evidence remains byte-for-byte unchanged from
`ecfa981bdedf25f31e46bf870aee6fa5be3fc31c`, and its report remains at
`9846ff1771ec4607ec342411d25def662fcf95e4`. The manifest records
`SUCCEEDED`, `success=true`, exit 0, no timeout, only `result.txt`, and no
unexpected path. Its nonempty patch changes only `result.txt` to exactly
`TOMTIT Claude Code CLI adapter smoke test passed`.

The attempt-1 failed manifest/empty patch and the spike script remain unchanged
from the original Gate 3 source lineage.

```text
REAL_CLI_INVOCATION=PRESERVED
REAL_CLI_RESULT_FILE=PRESERVED
REAL_CLI_SCOPE_CONTROL=PRESERVED
REAL_CLI_EVIDENCE=PRESERVED
HISTORICAL_FAILURE_PRESERVED=PASS
```

## AUTHORITY_AND_PRIOR_GATE_PRESERVATION

The adapter imports no Runtime or domain authority, writes no canonical state,
appends no event or `DecisionEvent`, and owns no retry or escalation decision.
One `run()` performs exactly one `Popen`; the corresponding regression passes.
The corrective range does not change Runtime/domain or either prior scenario.

```text
RUNTIME_AUTHORITY_PRESERVED=RESOLVED
GATE_1_PRESERVATION=PRESERVED
GATE_2_PRESERVATION=PRESERVED
```

## FINDING_STATUS

```text
G3-SOL-01=RESOLVED
G3-SOL-02=RESOLVED

CLAUDE_CODE_CLI_ADAPTER=RESOLVED

PROCESS_TIMEOUT_HANDLING=RESOLVED
PROCESS_CLEANUP=RESOLVED
PROCESS_GROUP_QUIESCENCE=RESOLVED
READER_THREAD_COMPLETION=RESOLVED
FINAL_SNAPSHOT_ORDERING=RESOLVED
SCOPE_CHANGE_DETECTION=RESOLVED

EXPLICIT_EXECUTABLE_VALIDATION=RESOLVED
PROCESS_START_NORMALIZATION=RESOLVED
TOTAL_RESULT_NORMALIZATION=RESOLVED

REAL_CLI_INVOCATION=PROVEN
REAL_CLI_RESULT_FILE=PROVEN
REAL_CLI_SCOPE_CONTROL=PROVEN
REAL_CLI_EVIDENCE=PROVEN

SECRET_REDACTION=RESOLVED
RUNTIME_AUTHORITY_PRESERVED=RESOLVED

HISTORICAL_FAILURE_PRESERVED=PASS
GATE_1_PRESERVATION=PRESERVED
GATE_2_PRESERVATION=PRESERVED

CLI_INSTALLATION_VERSION_METADATA_MISMATCH=OBSERVED_NONBLOCKING
REAL_WORLD_ADAPTER_STABILITY=NOT_PROVEN
```

## GATE_STATEMENT_ASSESSMENT

`PROVEN`

For this bounded Gate 3 capability, TOMTIT can invoke one real Claude Code CLI
worker in an isolated workspace, prevent its descendants from mutating after
result finalization, capture bounded immutable result/workspace evidence,
detect unexpected paths, normalize supported execution failures, and preserve
Runtime authority. This does not prove production or multi-task reliability.

## RISKS

Only LOW or explicitly out-of-scope limitations remain:

- theoretical PID/PGID reuse between leader reap and group probe;
- POSIX-only verified group cleanup and bounded worst-case cleanup latency;
- `PROCESS_CLEANUP_FAILED` priority over a snapshot-derived scope status;
- one successful real sample, not stability across versions/prompts/accounts;
- absolute local paths and the recorded CLI installation/self-report mismatch;
- no persistent Runtime integration, production sandbox, multi-provider layer,
  or cost-control system.

None is a current survivor, post-return write, reader leak, unnormalized
supported process failure, credential leak, Runtime mutation, or prior-gate
regression.

## SCOPE_AND_NON_GOALS

This rereview is limited to G3-SOL-01, G3-SOL-02, their process/result boundary,
the authorized corrective diff, preservation of existing evidence, and prior
gate authority. It performed no implementation/evidence edit, no real CLI
invocation, no persistent integration, and no next-gate work.

## UNPROVEN_ITEMS

- Production reliability and statistical stability across CLI releases,
  prompts, accounts, and multiple tasks.
- Non-POSIX process supervision and complete elimination of PID/PGID reuse.
- Persistent recovery/integration, OS sandbox security, cost control, routing,
  and multi-provider behavior.

## REVIEW_BUDGET_CONSEQUENCE

No further Gate 3 corrective patch is warranted for the two authorized
findings. The bounded Gate 3 capability is approved; the next authorized act
is evidence persistence, followed by owner manual dogfooding and a stop. This
rereview does not authorize another gate.

## NEXT_ACTION

```text
GATE_3_STATUS=APPROVED
CLAUDE_CODE_CLI_CAPABILITY=PROVEN
NEXT_ACTION=PERSIST_GATE_3_EVIDENCE
POST_PERSISTENCE_ACTION=STOP_FOR_OWNER_MANUAL_DOGFOOD
NO_NEXT_GATE_AUTHORIZED
```
