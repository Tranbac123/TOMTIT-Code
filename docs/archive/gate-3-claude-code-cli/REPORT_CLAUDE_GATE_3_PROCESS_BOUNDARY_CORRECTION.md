# Claude Gate 3 — Process Boundary Correction

## STATUS

```text
READY_FOR_CODEX_SOL_GATE_3_CORRECTIVE_REREVIEW
```

Both authorized findings are corrected in the adapter's process and result
boundary. No real or billable Claude Code CLI invocation was performed; the
committed real-success evidence is byte-for-byte unchanged.

## STARTING_HEAD

```text
8f3ab4d6fd090f4656e6d37169fdd3d38348a37c
```

## SOURCE_COMMIT_HASH

```text
70a23eae1b9cd043d16484e529d1f5eb6faa045e
```

## IMPLEMENTED_DIFF_RANGE

```text
8f3ab4d6fd090f4656e6d37169fdd3d38348a37c..70a23eae1b9cd043d16484e529d1f5eb6faa045e
```

## REPORT_COMMIT_EXPECTED_PARENT

```text
70a23eae1b9cd043d16484e529d1f5eb6faa045e
```

## MODEL_USED

Claude Code Opus.

## MODEL_REASON

Assigned for this corrective patch. The work is a concurrency and process
lifecycle correction on a trust boundary, where the failure mode is a race
that deterministic tests had already missed once.

## FILES_CHANGED

Three authorized files, all modified; no file created, deleted, or renamed:

| File | Change |
|---|---:|
| `src/tomtit/adapters/claude_code_cli.py` | +296 / −51 |
| `tests/adapters/test_claude_code_cli.py` | +709 |
| `tests/scenarios/test_claude_code_cli_spike.py` | +82 |

`src/tomtit/adapters/__init__.py` was **not** modified: every symbol added by
this patch is module-internal, so no existing public export needed
re-exporting. No dependency was added. `scripts/run_claude_code_cli_spike.py`,
`evidence/gate3/**`, all prior reports, Runtime, domain, governance, and both
earlier scenario test files are untouched.

## G3_SOL_01_CORRECTION

The defect was structural: `_execute()` cleaned the process group **only**
inside `if timed_out:`. A parent that exited normally was never checked for
survivors, and the two `join(timeout=5)` calls were never inspected, so
`run()` could return `SUCCEEDED` while a descendant and both reader threads
were still live.

The correction makes group quiescence a property of *every* return path
rather than of one branch:

- `ensure_process_group_quiescent(pgid)` is now called unconditionally after
  the poll loop, for normal exit and timeout alike.
- Reader joins moved *after* that call, because the readers cannot reach EOF
  until the group holding the pipe write-ends is gone. Joining first was
  precisely what let them survive.
- `readers_alive` is computed from an explicit `is_alive()` check on both
  readers and can veto success.
- Stream evidence is copied into an immutable `_ExecutionOutcome` only after
  the joins, so nothing live can mutate what the result reports.

Independent reproduction of Sol's exact fixture against the patched adapter:

```text
status=SUCCEEDED success=True exit=0 elapsed=0.66s
changed=('child.pid', 'result.txt') unexpected=()
README at return: 'before\n'
live non-main threads: []
descendant 86889 is gone  <-- correct
README 4s after return: 'before\n'
```

Sol measured `elapsed=10.51s`, two live readers, and a live descendant that
then mutated `README.md`. The delayed write no longer happens at all, because
the descendant is terminated before the final snapshot is taken.

## G3_SOL_02_CORRECTION

`run()` previously did `executable_path or discover_claude_executable()`,
which let a caller-supplied path skip validation entirely and reach
`subprocess.Popen`, raising `FileNotFoundError` out of a method contracted to
return only `ClaudeCodeCLIResult`.

Two independent guards now close this, deliberately as defense-in-depth:

1. **Validation boundary.** `run()` calls
   `discover_claude_executable(executable_path)`, so the explicit override
   passes through the same check as discovery: resolved if relative, then
   `is_file()` and `os.access(..., X_OK)`, with `OSError` absorbed. A rejected
   override returns `UNAVAILABLE` / `EXECUTABLE_UNAVAILABLE` without ever
   constructing a subprocess.
2. **Start normalization.** The `Popen` call is wrapped, mapping
   `FileNotFoundError` → `UNAVAILABLE` / `EXECUTABLE_UNAVAILABLE`,
   `PermissionError` → `UNAVAILABLE` / `EXECUTABLE_NOT_EXECUTABLE`, and any
   other `OSError` → `FAILED` / `PROCESS_START_FAILED`.

The existing `CLI_NOT_FOUND` category is preserved for the `shutil.which`
path, so the distinction between "no CLI installed" and "the override you gave
me is not usable" survives in the evidence.

## PROCESS_GROUP_QUIESCENCE

`ensure_process_group_quiescent(pgid, *, grace_seconds=None)` answers the
three required states via `_ProcessGroupCleanup`:

```text
PROCESS_GROUP_QUIESCENT       group was already empty on first probe
PROCESS_GROUP_CLEANED         survivors existed and were terminated and verified gone
PROCESS_GROUP_CLEANUP_FAILED  quiescence could not be positively proven
```

Mechanism and guarantees:

- `_process_group_alive(pgid)` probes with `os.killpg(pgid, 0)` and returns a
  three-valued answer: `False` on `ProcessLookupError` (absent), `None` on
  `PermissionError` or other `OSError` (indeterminate), `True` otherwise. A
  POSIX group exists exactly while it has a member, so this directly answers
  "can anything from this invocation still run?".
- Escalation is SIGTERM → bounded grace → SIGKILL, with absence re-verified
  after each step. There is no sleep loop without a monotonic deadline; every
  wait is bounded by `_PROCESS_CLEANUP_GRACE_SECONDS` (5.0s default).
- Every ambiguous outcome fails closed as `FAILED`: an indeterminate probe, a
  group that outlives SIGKILL, a failed signal delivery, or a non-POSIX
  platform where group verification is unavailable.
- `pgid <= 1` and `pgid == os.getpgrp()` are refused outright, so TOMTIT can
  never signal its own group. Only the pgid produced by this adapter's own
  `start_new_session=True` child is ever used; no process table is scanned and
  no pgid is derived from worker stdout.
- `ProcessLookupError`, `PermissionError`, and `OSError` are each handled
  explicitly, and `ProcessLookupError` is caught before `OSError` so the
  "already gone" case is never misread as a failure.

## NORMAL_EXIT_DESCENDANT_CLEANUP

After the direct parent exits normally, the poll loop's `proc.poll()` has
already reaped it, so its zombie cannot keep the group artificially alive for
the probe. Cleanup then runs unconditionally and, per the required semantics:

- the parent's real exit code is preserved and returned unchanged;
- `timed_out` stays `False` — needing to clean up descendants is not a
  timeout;
- `CLEANED` is a normal, success-compatible outcome: a run is not downgraded
  merely because survivors had to be terminated;
- only `FAILED` blocks success.

On the timeout path the group is signalled first and the parent is then reaped
with a bounded `wait()`, before the same unconditional quiescence check runs.

## READER_THREAD_COMPLETION

Ordering is now: quiesce the group → join both readers (bounded by
`_READER_JOIN_TIMEOUT_SECONDS`) → inspect `is_alive()` on both → freeze
stream evidence. If either reader is still alive after that, the result is
`FAILED` / `PROCESS_CLEANUP_FAILED`; daemon readers are never silently
abandoned.

Buffers are copied with `bytes(reader.buffer)` into the frozen
`_ExecutionOutcome` after the joins, so even in the failure case a straggling
thread cannot alter what the returned result reports. `stdout_sha256` still
covers the full stream including any truncated tail.

## FINAL_SNAPSHOT_ORDERING

The required sequence is now structural rather than incidental: `_execute()`
does not return until cleanup and reader joins are done, and `run()` takes the
`after_hashes` snapshot only from that return value. The ordering is asserted
directly by `test_final_snapshot_occurs_only_after_cleanup`, which records
call order and requires exactly:

```text
["snapshot", "cleanup", "snapshot"]
```

## DELAYED_WRITE_REPRODUCTION

`test_delayed_descendant_write_is_prevented_before_final_snapshot` builds the
authorized fixture: the parent writes the allowed `result.txt`, spawns a
descendant in its own group that sleeps 3s before writing the unexpected
`README.md`, and exits 0 immediately. Asserted: descendant proven gone,
`changed_paths == ("result.txt",)`, `unexpected_changed_paths == ()`, no live
reader, and — after sleeping past the intended write window — `README.md` is
still the original content.

The early-write variant
(`test_early_descendant_unexpected_write_is_scope_violation`) has the
descendant write `README.md` before it can be terminated. Asserted:
`SCOPE_VIOLATION`, `success=False`, `unexpected_changed_paths ==
("README.md",)`, and the file still contains the unexpected content — the
adapter reports, it never silently reverts.

The same guarantee is proven at the evidence layer in
`tests/scenarios/test_claude_code_cli_spike.py`, where the persisted manifest
and patch are re-verified against the real workspace after the delay window.

## EXECUTABLE_VALIDATION

`discover_claude_executable` is the single validation boundary. An explicit
override is resolved if relative, then required to be an existing, regular,
executable file. Nonexistent path, directory path, and non-executable file
each return `UNAVAILABLE` / `EXECUTABLE_UNAVAILABLE` with `exit_code=None`,
`timed_out=False`, and no exception. `test_nc07` proves the guard is
load-bearing by asserting that an unvalidated path never *reaches* `Popen` at
all — asserting only on the returned result is insufficient, because the
start-normalization guard independently catches the same failure.

## PROCESS_START_NORMALIZATION

`Popen` construction is wrapped and produces a `_start_failure_outcome` whose
result satisfies every required property: valid ordered timestamps,
non-negative duration, retained `workspace_before_hash`, a safely captured
`workspace_after_hash`, reported changed/unexpected paths, explicitly empty
stdout/stderr with the correct empty-string SHA-256, and a frozen result.

Error messages are deliberately built from `exc.__class__.__name__` and
`exc.errno` only, never `str(exc)`, so no environment-derived text can reach
persisted evidence. This is asserted by
`test_start_failure_result_is_immutable_and_leaks_no_environment`, which
plants a secret inside the raised exception's message and requires it absent
from `error_message`.

## TOTAL_RESULT_BOUNDARY

`run()` now returns `ClaudeCodeCLIResult` for every supported request and
override. `test_no_executable_or_start_exception_escapes_run` sweeps three
rejected override shapes and four raised start exceptions
(`FileNotFoundError`, `PermissionError`, `OSError`, `BlockingIOError`) and
requires an instance of the result contract every time. Timeout, cleanup,
and reader failures are likewise normalized rather than raised.

Unexpected programmer errors unrelated to process execution are deliberately
still allowed to propagate, per the task's explicit carve-out.

## OFFLINE_TESTS

Adapter tests grew from 23 to **44**; the Gate 3 offline scenario file grew
from 2 to **3**. All are deterministic and offline; none contacts the real
Claude service. Every test that creates a descendant either proves it dead or
force-kills it in a `finally` block, and a post-run `ps` sweep confirmed zero
leaked processes.

| # | Required case | Test |
|---:|---|---|
| 1 | Invalid explicit path → UNAVAILABLE | `test_invalid_explicit_path_returns_unavailable_result` |
| 2 | Directory override → UNAVAILABLE | `test_explicit_path_that_is_a_directory_returns_unavailable` |
| 3 | Non-executable file → UNAVAILABLE | `test_explicit_non_executable_file_returns_unavailable` |
| 4 | Popen `FileNotFoundError` normalized | `test_popen_file_not_found_is_normalized` |
| 5 | Popen `PermissionError` normalized | `test_popen_permission_error_is_normalized` |
| 6 | Generic `OSError` → FAILED | `test_generic_popen_oserror_returns_failed` |
| 7 | No start exception escapes | `test_no_executable_or_start_exception_escapes_run` |
| 8 | Normal exit kills descendant | `test_normal_exit_with_surviving_descendant_kills_descendant` |
| 9 | Pipe-holding descendant → readers end | `test_normal_exit_with_pipe_holding_descendant_terminates_readers` |
| 10 | Delayed write prevented | `test_delayed_descendant_write_is_prevented_before_final_snapshot` |
| 11 | Early write → SCOPE_VIOLATION | `test_early_descendant_unexpected_write_is_scope_violation` |
| 12 | SIGTERM-resistant → SIGKILL | `test_sigterm_resistant_descendant_receives_sigkill` |
| 13 | Cleanup failure fails closed | `test_cleanup_failure_returns_process_cleanup_failed` |
| 14 | Live reader fails closed | `test_live_reader_returns_process_cleanup_failed` |
| 15 | Snapshot only after cleanup | `test_final_snapshot_occurs_only_after_cleanup` |
| 16 | No buffer mutation after return | `test_no_output_buffer_mutation_after_run_returns` |
| 17 | Timeout descendant preserved | `test_no_child_process_survives_a_timeout` (pre-existing) |
| 18 | Successful fake worker preserved | `test_success_with_expected_file_change` (pre-existing) |
| 19 | Real evidence unchanged | `test_committed_real_evidence_files_are_unchanged` |
| 20 | Exactly one Popen per run | `test_exactly_one_popen_per_run` |

Three further defensive tests were added beyond the required list:
`test_indeterminate_process_group_probe_fails_closed`,
`test_process_group_cleanup_never_targets_own_group`, and
`test_start_failure_result_is_immutable_and_leaks_no_environment`.

One test fixture needed a correction during development, worth recording: the
SIGTERM-resistant test initially raced Python interpreter startup, so SIGTERM
sometimes landed before `SIG_IGN` was installed and the descendant died to the
default action, making the test pass for the wrong reason. It now uses an
explicit readiness handshake. The adapter was not changed for this.

## NEGATIVE_CONTROLS

Ten controls, each restoring exactly one invalid condition, stored only in
`/private/tmp` and applied by runtime monkeypatching. Result: **10 failed**,
the expected outcome. No tracked file was modified; `git status --porcelain`
showed only the three authorized files throughout.

| # | Restored invalid condition | Offline path / failing control | Load-bearing guard |
|---:|---|---|---|
| 1 | Normal-exit branch skips group cleanup | `test_nc01_normal_exit_skips_group_cleanup` | unconditional `ensure_process_group_quiescent` |
| 2 | Final snapshot before cleanup | `test_nc02_final_snapshot_before_cleanup` | `_execute` returns only post-cleanup |
| 3 | Reader alive but success allowed | `test_nc03_live_reader_allows_success` | `readers_alive` veto |
| 4 | Ambiguous group treated as quiescent | `test_nc04_ambiguous_group_treated_as_quiescent` | three-valued probe fails closed |
| 5 | SIGKILL fallback removed | `test_nc05_sigkill_fallback_removed` | SIGTERM→SIGKILL escalation |
| 6 | Delayed write after success return | `test_nc06_delayed_write_after_success` | pre-snapshot group termination |
| 7 | Explicit path bypasses validation | `test_nc07_explicit_path_bypasses_validation` | `discover_claude_executable` boundary |
| 8 | `FileNotFoundError` escapes run() | `test_nc08_10_popen_exception_escapes[exc0]` | Popen normalization |
| 9 | `PermissionError` escapes run() | `test_nc08_10_popen_exception_escapes[exc1]` | Popen normalization |
| 10 | Popen `OSError` escapes run() | `test_nc08_10_popen_exception_escapes[exc2]` | Popen normalization |

Control 7 required a second iteration and the reason is worth surfacing: the
first version asserted only on the returned result and **passed**, because the
Popen-normalization guard independently catches an unvalidated path. It was
rewritten to assert that `Popen` is never reached. This is evidence that the
two G3-SOL-02 guards are genuinely independent layers, not one guard counted
twice.

## COMMANDS_AND_EXIT_CODES

| Command | Exit | Result |
|---|---:|---|
| `python3 -m pytest --collect-only -q` | 0 | 369 collected |
| `python3 -m pytest -q` | 0 | 369 passed |
| `python3 -m pytest tests/adapters -q` | 0 | 44 passed |
| `python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q` | 0 | 3 passed |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed |
| `python3 -m pytest tests/scenarios/test_scenario_2.py -q` | 0 | 137 passed |
| focused new regressions (`-k` selection) | 0 | 19 passed, 25 deselected |
| `python3 -m compileall -q src tests scripts` | 0 | pass |
| `ruff check .` | 0 | no findings |
| `git diff --check` | 0 | clean |
| `python3 -m mypy src` | 0 | no issues in 13 source files |
| negative controls (10) | 1 | 10 failed, as expected |

Total moved from 347 to **369** (+22: 21 adapter, 1 scenario).

`python3 scripts/run_claude_code_cli_spike.py` was **not** run. No `claude -p`
or any other real CLI invocation was made.

## PRESERVATION_RESULTS

```text
Scenario 1 = 99 passed, unchanged.
Scenario 2 = 137 passed, unchanged.
```

All 23 pre-existing adapter tests and both pre-existing Gate 3 scenario tests
pass unmodified, covering non-zero exit classification, timeout
classification, scope-violation priority, missing-expected-change failure,
stdout/stderr truncation, full-stream hashing, secret redaction, environment
allowlisting, argv separation, no shell execution, request/result
immutability, absence of Runtime/domain imports, and no adapter retry.

`git status --porcelain -- evidence/gate3` is empty: the real-success manifest
and patch, and the historical blocked-attempt evidence, are byte-for-byte
unchanged. This is additionally enforced from inside the suite by
`test_committed_real_evidence_files_are_unchanged`.

## RISKS

- **PID reuse remains theoretically possible.** Cleanup is keyed on the pgid,
  which equals the reaped leader's pid. Between the leader's reaping and the
  probe, the kernel could in principle recycle that pid as a new group leader,
  and cleanup would then target an unrelated group. The window is very small
  and POSIX keeps a group alive while any member exists, but this is not
  eliminated. Closing it fully needs `pidfd` (Linux-only), which is outside
  both this patch's scope and the macOS target environment.
- **Status priority is a judgment call.** `PROCESS_CLEANUP_FAILED` is checked
  *before* the scope-violation branch, on the reasoning that if quiescence
  cannot be proven then the snapshot behind every other check may be stale, so
  no snapshot-derived verdict should be presented as reliable. Observed
  `changed_paths` and `unexpected_changed_paths` are still populated on the
  result, so this ordering hides no data — only the trust label differs. A
  reviewer who prefers scope-violation to outrank cleanup failure would order
  these two differently; the existing scope-violation-over-nonzero-exit
  priority is unchanged.
- **Non-POSIX fails closed by design.** On a non-POSIX platform every run
  would now return `PROCESS_CLEANUP_FAILED`, because group quiescence cannot
  be verified there. This is the specified behavior for a macOS/POSIX gate,
  but it means the adapter is effectively POSIX-only until a platform-specific
  supervisor is written.
- **Cleanup adds bounded latency in the worst case.** A stubborn group can
  cost up to two grace periods (10s default) plus reader joins before `run()`
  returns. Normal runs are unaffected — the reproduction returned in 0.66s.
- **Still a spike.** One corrected sample and offline fixtures do not
  establish production reliability, and real-world adapter stability remains
  unproven.

## SCOPE_AND_NON_GOALS

Only the two authorized findings were addressed. This patch does not touch
Runtime, domain, reporting, fakes, governance, the spike script, any evidence
file, or any prior report; does not add a dependency; does not build a general
cross-platform process supervisor; and does not implement any phase-gated
feature (retry policy, escalation, routing, persistence, sandboxing,
concurrency, Memory, or multi-provider support). The adapter still owns no
canonical state, imports no Runtime or domain module, and performs exactly one
`Popen` per `run()`.

## UNPROVEN_ITEMS

```text
G3-SOL-01=CLAIMED_FIXED_PENDING_REVIEW
G3-SOL-02=CLAIMED_FIXED_PENDING_REVIEW

PROCESS_CLEANUP=CLAIMED_RESOLVED_PENDING_REVIEW
SCOPE_CHANGE_DETECTION=CLAIMED_RESOLVED_PENDING_REVIEW
TOTAL_RESULT_NORMALIZATION=CLAIMED_RESOLVED_PENDING_REVIEW

REAL_CLI_INVOCATION=PRESERVED_PENDING_REVIEW
REAL_CLI_RESULT_FILE=PRESERVED_PENDING_REVIEW
REAL_CLI_EVIDENCE=PRESERVED_PENDING_REVIEW

RUNTIME_AUTHORITY_PRESERVED=PENDING_REVIEW
GATE_1=PRESERVED_PENDING_REVIEW
GATE_2=PRESERVED_PENDING_REVIEW

NO_REAL_CLI_INVOCATION_PERFORMED=true
NO_NEXT_GATE_AUTHORIZED=true
POST_GATE_ACTION=STOP_FOR_OWNER_MANUAL_DOGFOOD
```

Additionally not proven by this patch: behavior under PID reuse; behavior on
non-POSIX platforms; real-world adapter stability across CLI versions,
prompts, and account states; and production reliability, persistent
integration, recovery, or sandbox security.

## NEXT_ACTION

```text
READY_FOR_CODEX_SOL_GATE_3_CORRECTIVE_REREVIEW
NO_NEXT_GATE_AUTHORIZED
POST_GATE_ACTION=STOP_FOR_OWNER_MANUAL_DOGFOOD
```
