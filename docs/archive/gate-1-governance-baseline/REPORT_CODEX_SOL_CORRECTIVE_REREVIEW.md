# Codex Sol Corrective Rereview

## 1. Verdict

```text
VERDICT=REQUEST_CHANGES

SOL-H01=OPEN
SOL-M02=RESOLVED
SOL-M03=RESOLVED
SOL-L04=OPEN_LOW

F-002=OPEN
F-003=RESOLVED
F-004=RESOLVED
```

Commit G removes the original post-commit filesystem revalidation, enforces
authoritative check metadata, and conventionally freezes `FrozenPayload`.
However, completion still has policy-breaking partial states after canonical
assignment, the artifact gate is not the last Runtime-controlled step before
that assignment, and a publicly named report helper can build and publish a
terminal report from an unchecked copied record. One high and two medium
findings remain, so the approval gate is not met.

## 2. Model and review scope

Model used: Codex Sol.

Sol was appropriate because this rereview covers canonical Runtime authority,
event-journal failure behavior, report preparation versus publication,
filesystem-backed proof timing, authoritative check metadata, custom Pydantic
value immutability, and terminal chronology. This was an independent source and
behavior review; no implementation or test file was modified.

## 3. Reviewed commits

```text
implementation=83b72b88faf28c8ae138c4114780454fad04da93
implementation_report=ac3472fce140dfd45a2a96bd790b4fd271ba6294
implementation_parent=9e7129a2951e200453570b0246385b2710ffd71f
reviewed_target_head=ac3472fce140dfd45a2a96bd790b4fd271ba6294
```

The review-report commit is the commit containing this file and is recorded in
the final handoff; its hash cannot be embedded in its own preimage without
rewriting history.

## 4. Initial Git state

All commands below ran before this report was created.

| Command | Exit code | Exact output |
|---|---:|---|
| `git status --short` | 0 | `<empty>` |
| `git status --porcelain=v1 --untracked-files=all` | 0 | `<empty>` |
| `git branch --show-current` | 0 | `main` |
| `git rev-parse HEAD` | 0 | `ac3472fce140dfd45a2a96bd790b4fd271ba6294` |
| `git rev-parse HEAD^` | 0 | `83b72b88faf28c8ae138c4114780454fad04da93` |
| `git rev-parse HEAD^^` | 0 | `9e7129a2951e200453570b0246385b2710ffd71f` |

`git status --ignored --short` exited `0`:

```text
!! .mypy_cache/
!! .pytest_cache/
!! .ruff_cache/
!! src/.DS_Store
!! src/tomtit/.DS_Store
!! src/tomtit/__pycache__/
!! src/tomtit/domain/__pycache__/
!! src/tomtit/runtime/__pycache__/
!! tests/domain/__pycache__/
!! tests/scenarios/__pycache__/
```

`git log --oneline --decorate -12` exited `0`:

```text
ac3472f (HEAD -> main) docs(audit): record Sol correctness follow-up
83b72b8 fix(runtime): complete Sol correctness follow-up
9e7129a docs(audit): record Sol review for F002 F003 F004
6bf4d4b docs(audit): record F002 F003 F004 corrective patch
c0aa507 fix(runtime): enforce completion and event invariants
abca5d0 docs(audit): record Terra baseline rereview
0927926 docs(audit): correct recovered baseline test evidence
239f1e9 docs(audit): record Terra baseline review
eb5bb39 docs(audit): record recovered baseline evidence
1e87e99 chore(repo): establish recovered reviewed baseline
```

The initial branch and graph matched the brief. The tracked worktree and index
were clean, there were no untracked project files, and only permitted ignored
generated artifacts existed.

## 5. Commit-scope verification

The required `git show --format=fuller --stat` and `--name-status` commands for
both commits exited `0`.

Commit G modifies exactly:

```text
M src/tomtit/domain/evaluation.py
M src/tomtit/reporting.py
M src/tomtit/runtime/runtime.py
M src/tomtit/runtime/state.py
M tests/domain/test_evaluation_models.py
M tests/scenarios/test_scenario_1.py
```

Its stat is `6 files changed, 434 insertions(+), 72 deletions(-)`.

Commit H adds exactly:

```text
A REPORT_CLAUDE_CORRECTIVE_SOL_H01_M02_M03.md
```

Its stat is `1 file changed, 379 insertions(+)`. No policy, governance,
configuration, README, or unrelated implementation file changed.

The diff adds no reachable Attempt 2, revision, `WAITING_OWNER`, Attempt 3,
progress comparison, `NO_PROGRESS` execution, real workspace management,
subprocess execution, worker adapter, LLM verification, database, CLI UI,
Memory, model-routing, or graph behavior. `_ALLOWED_TRANSITIONS` is unchanged.

## 6. Completion entry-point review

`Runtime.complete_run()` is the sole repository Runtime path that assigns a
completed `RunRecord`. Repository search found only two assignments to
`self._record`: initialization and the validated assignment inside
`complete_run()`; `_replace_record()` reconstructs ordinary snapshots but has
no independent completed-state path.

`run_scenario_1()` delegates its terminal tail to `complete_run()`. Generic
`transition(COMPLETED, TERMINATION_DECIDED)` first verifies that the move is
otherwise legal, then rejects it with `InvalidTransitionError` directing the
caller to `complete_run()`. Completion legality, expected reason, terminal-state
guard, `StopDecision`, and `next_action=complete` preconditions remain enforced.

`complete_run()` is a public class method but that is justified as the one
Runtime-owned completion operation. It is not re-exported separately. A second
call after success and a direct terminal transition both reject without adding
events or overwriting the report.

## 7. Preparation before canonical commit

The intended preparation sequence is:

```text
candidate RunRecord construction
→ validate_completed_record(candidate)
→ build_terminal_report_from_validated_record(...)
→ render_terminal_report(...)
→ canonical assignment
```

Independent injected failures in candidate construction, report-model
construction, and serialization each left exactly:

```text
state=CHECKING
outcome=None
RUN_COMPLETED=0
report file absent
report_path=None
REPORT_GENERATED=0
```

Missing artifacts before the gate and removal during the gate likewise raised
`CompletionInvariantError` with the same clean nonterminal result. The original
SOL-H01 defect—running a second filesystem completion gate after canonical
assignment—is removed.

Preparation is not fully correct because the filesystem gate is followed by a
clock callback, report construction, and serialization before canonical
assignment. An artifact removed by the injected Runtime clock at
`report.generated_at` therefore disappears after validation but before commit,
yet completion succeeds. See `SOL-RM02`.

## 8. Post-commit fallibility analysis

The implementation report's statement that only the report file write remains
fallible is incorrect. After `self._record = validated`, the following steps
remain:

| Operation | Fallible work | Reproduced partial state |
|---|---|---|
| Append `RUN_COMPLETED` | Runtime clock; event-ID factory; `EvaluationEvent` construction, JSON serialization, journal append, JSON reconstruction | `COMPLETED`; no `RUN_COMPLETED`; no report; no path; no `REPORT_GENERATED` |
| Publish rendered report | parent-directory creation; exclusive open; write; close | `COMPLETED`; one `RUN_COMPLETED`; report absent or potentially partial; no path; no `REPORT_GENERATED` |
| Update `report_path` | canonical `RunRecord` reconstruction | `COMPLETED`; one `RUN_COMPLETED`; report exists; path unset; no `REPORT_GENERATED` |
| Append `REPORT_GENERATED` | Runtime clock and the same event construction/serialization pipeline | `COMPLETED`; one `RUN_COMPLETED`; report and path exist; no `REPORT_GENERATED` |

The first two are practical without modifying production source. A supported
`event_id_factory` that raises on event 10 leaves canonical `COMPLETED` with no
completion event or report. Setting `report_directory` to an existing regular
file passes preflight and later raises `FileExistsError` from `mkdir`, leaving
canonical `COMPLETED` and `RUN_COMPLETED` without a report. The latter exact
result was:

```text
state=COMPLETED
outcome=COMPLETED
RUN_COMPLETED=1
report_exists=False
report_path=None
REPORT_GENERATED=0
```

Injected failures at the path update and final event append confirm the other
partial-state shapes. See `SOL-RH01`.

## 9. Report-publication risk decision

The residual report-publication risk is not permitted by the accepted V0
policy. Policy section 3.4 requires every suspended or terminated run to
produce a structured artifact. Section 18.2 requires Runtime to write immutable
`report.v1.json` only after entering a terminal state. The dogfood gate also
requires every TOMTIT run to have a complete trace and terminal report. System
rules require every transition to emit an event and reports to be write-once.

The current report accurately describes the `COMPLETED + RUN_COMPLETED` without
report-file shape for one failure, but incorrectly limits post-commit
fallibility to the file write and classifies the policy violation as an accepted
durability limitation. V0 does not grant that exception.

A database or general transaction framework is unnecessary. A bounded design
can prepare and serialize terminal events and stage a complete report in a
temporary file before the commit point, then use a narrowly defined publication
and recovery order so a normal clock, event, rename, or write failure cannot
leave a canonical terminal run without its required event and report. The exact
implementation remains for a separately authorized corrective task.

## 10. Artifact timing analysis

Independent timing results:

```text
unavailable_before_validation:
  CompletionInvariantError; CHECKING; no completion event or report

unavailable_during_validation:
  CompletionInvariantError; CHECKING; no completion event or report

unavailable_after_validation_before_assignment:
  SUCCESS; COMPLETED; artifact absent; one RUN_COMPLETED; report and path present;
  one REPORT_GENERATED

unavailable_after_assignment:
  SUCCESS; COMPLETED; artifact absent; one RUN_COMPLETED; report and path present;
  one REPORT_GENERATED
```

The policy requires required evidence artifacts to exist before completion.
The authoritative filesystem proof should therefore be the final
Runtime-controlled check immediately before canonical assignment. Continued
existence after the completion commit is not required by that wording and is
not treated as a new durability, hashing, symlink, or path-confinement finding.

Current ordering calls the Runtime clock and performs report preparation after
the proof but before completion, providing a deterministic internal window in
which the requirement becomes false before assignment. A small reordering or
final in-preparation validation can close that window without post-commit
revalidation; see `SOL-RM02`.

## 11. Chronology and clock-authority decision

The successful chronology is:

```text
completed_at
<= report.generated_at
<= RUN_COMPLETED.timestamp
<= REPORT_GENERATED.timestamp
```

All timestamps come from the injected Runtime clock and are monotonic. The
event sequence remains `... CHECK_PASSED, RUN_COMPLETED, REPORT_GENERATED`.

The policy does not define an ordering for `report.generated_at` relative to
`RUN_COMPLETED.timestamp`; it requires only that the report file be written
after entering a terminal state. In this design, `report.generated_at`
accurately marks in-memory report preparation/serialization, while the later
`REPORT_GENERATED` event marks successful publication. Those moments are not
represented as identical. The new chronology is internally coherent and no
chronology finding is raised.

Timestamp allocation can remain in preparation while the filesystem gate is
moved to the end of preparation, preserving this policy-compatible order.

## 12. Validated-report helper assessment

`build_terminal_report_from_validated_record()` is not re-exported by a package
`__init__`, and the only repository caller passes the result of
`validate_completed_record()`. Nevertheless, it is a publicly named function in
`tomtit.reporting`, its public boundary docstring directs callers to use it,
and its `RunRecord` type carries no proof that validation occurred.

The practical bypass is deterministic: change a valid completed record to
`state=CHECKING` via unchecked `model_copy(update=...)`, pass it directly to the
helper, and it returns a `TerminalReport(outcome=COMPLETED)`. That report can be
published through the public write-once writer. See `SOL-RM03`.

No wrapper type is required solely for aesthetics. Making the no-validation
builder private and keeping `build_terminal_report()` as the sole public trust
boundary is sufficient for the current repository architecture.

## 13. SOL-M02 — RequiredCheck metadata authority

Independent tests through both `validate_completed_record()` and
`build_terminal_report()` produced:

```text
different argv: rejected by both
different cwd_relative: rejected by both
both different: rejected by both
missing result despite Worker success claim: rejected by both
exact argv/cwd_relative positive match: accepted by both
```

Comparison uses exact canonical tuple/string equality after matching by stable
check ID. The shared `required_check_coverage_error()` owns the rule, so
Runtime's preliminary evidence screen and the canonical/report boundary use the
same implementation. Report construction introduces no duplicate metadata
rule, and successful output matches the contract.

`SOL-M02` is resolved.

## 14. SOL-M03 — FrozenPayload immutability

Independent probes confirmed rejection of:

- top-level and nested `_data` reassignment;
- arbitrary attribute addition;
- `_data` deletion;
- top-level and nested item assignment;
- nested sequence mutation;
- `_data` reassignment after JSON round trip.

Mutating the original top-level dictionary, nested dictionary, and nested list
does not affect the event. JSON-mode dump returns dicts/lists;
`model_dump_json()`, `model_validate_json()`, schema generation, and event-
journal append/read all succeed; restored values are frozen.

`object.__setattr__` is used only in `FrozenPayload.__init__`. Direct deliberate
calls to that Python internal remain out of scope, as instructed.

`SOL-M03` and F-004 are resolved.

## 15. SOL-L04 and Mypy

`python3 -m mypy src tests` exited `1` with:

```text
Found 83 errors in 4 files (checked 15 source files)
```

There are zero diagnostics under `src/**`. The four locations from the original
Sol finding are removed: the deliberate mapping assignment now has a narrow
ignore, and the `dict[str, object]` expansion was replaced by a typed helper. No
broad unrelated typing cleanup occurred.

However, Commit G adds two new avoidable `attr-defined` errors at
`tests/domain/test_evaluation_models.py:500` and `:532`; their existing ignores
cover `union-attr`, not the emitted code. The Claude report's statement that all
remaining diagnostics are the accepted list-versus-tuple style is therefore
incorrect. This is a bounded low finding, `SOL-RL04`.

## 16. F-003 regression

The independent F-003 harness exited `0`:

```text
Attempt 1 direct NO_PROGRESS construction: rejected
copied Attempt 2 changed to Attempt 1: rejected on reconstruction
Attempt 1 nested StopDecision.NO_PROGRESS: rejected
report boundary with invalid Attempt 1 NO_PROGRESS: rejected
Attempt 2 structural positive control: accepted
```

No `NO_PROGRESS` execution, Attempt 2 execution path, progress comparison, or
Attempt 3 eligibility was added. F-003 remains resolved.

## 17. Successful terminal-state consistency

A normal Scenario 1 run independently produced:

```text
state=COMPLETED
outcome=COMPLETED
stop_reason=ACCEPTANCE_SATISFIED
RUN_COMPLETED=1
report.v1.json files=1
report_path matches file=True
REPORT_GENERATED=1
report content matches canonical completion snapshot=True
event count=11
```

The report remains immutable and write-once. Event order is deterministic. A
second `complete_run()` and direct `transition(COMPLETED, ...)` both raise
`InvalidTransitionError`; repeated report publication raises `FileExistsError`.
After all three attempts, canonical state, event count, and report bytes remain
unchanged.

## 18. Commands and exit codes

| Command | Exit code | Result |
|---|---:|---|
| `python3 -m pytest --collect-only -q` | 0 | `43 + 29 + 3 + 63 = 138` collected |
| `python3 -m pytest -q` | 0 | 138 passed |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 63 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 29 passed |
| `python3 -m compileall -q src tests` | 0 | Passed, no output |
| `ruff check .` | 0 | Passed, no output |
| `git diff --check` | 0 | Passed before report creation |
| `python3 -m mypy src tests` | 1 | 83 errors in 4 test files; zero under `src/**` |
| Preparation-failure harness | 0 | Candidate, report-model, render failures remain cleanly `CHECKING` |
| Post-commit failure harness | 0 | Reproduced four distinct partial terminal shapes |
| Actual report-directory publication failure | 0 | Reproduced `COMPLETED + RUN_COMPLETED` without report |
| Artifact-timing harness | 0 | Pre/during gate reject; post-gate pre-commit and post-commit loss succeed |
| SOL-M02 harness | 0 | Four negatives reject at both boundaries; positive passes |
| SOL-M03 harness | 0 | All normal mutation, input, JSON, schema, and journal cases pass |
| F-003 harness | 0 | Four negatives reject; Attempt 2 positive passes |
| Terminal consistency/repetition harness | 0 | One coherent success; all repeats rejected without mutation |

Collection by file:

```text
tests/domain/test_contracts.py:         43
tests/domain/test_evaluation_models.py: 29
tests/domain/test_schema_generation.py: 3
tests/scenarios/test_scenario_1.py:     63
total:                                  138
```

## 19. Findings

### SOL-RH01

ID: `SOL-RH01`
Severity: HIGH
Location: `src/tomtit/runtime/runtime.py:237`, `src/tomtit/runtime/runtime.py:238`, `src/tomtit/runtime/runtime.py:239`, `src/tomtit/runtime/runtime.py:240`, `src/tomtit/runtime/runtime.py:241`, `src/tomtit/runtime/events.py:40`, `src/tomtit/reporting.py:194`
Problem: `complete_run()` commits canonical `COMPLETED` before several ordinary fallible operations. `RUN_COMPLETED` event construction/serialization can fail before the transition is recorded; report publication can fail after that event; path reconstruction and `REPORT_GENERATED` append can fail after publication. The implementation report incorrectly says only the file write remains fallible and treats missing terminal-report publication as an accepted limitation.
Reproduction: Configure the supported event-ID factory to raise on event 10: Runtime leaves `COMPLETED` with no `RUN_COMPLETED`, report, path, or report event. Separately, use an existing regular file as `report_directory`: preflight passes, then `mkdir` raises `FileExistsError` after canonical assignment and `RUN_COMPLETED`, leaving no `report.v1.json`, no `report_path`, and no `REPORT_GENERATED`.
Impact: The first case violates the mandatory one-event-per-transition invariant. The second violates the policy requirement that every terminated run produce a structured artifact. Both yield incomplete terminal traces that cannot be retried because terminal states reject further transitions.
Required fix: Prepare every normally fallible event and publication operation before, or provide bounded recovery around, the canonical commit. Stage complete report bytes before commit and use a policy-compatible publish/recovery order; prepare/serialize terminal events before mutation. Add failure-injection tests for the completion event, directory/open/write publication, path update, and report event, asserting no unrecoverable partial terminal result. No database or general transaction framework is required.

### SOL-RM02

ID: `SOL-RM02`
Severity: MEDIUM
Location: `src/tomtit/runtime/runtime.py:228`, `src/tomtit/runtime/runtime.py:229`, `src/tomtit/runtime/runtime.py:231`, `src/tomtit/runtime/runtime.py:233`, `src/tomtit/runtime/runtime.py:237`
Problem: The filesystem-backed completion gate is not the last Runtime-controlled operation before canonical assignment. Runtime calls its injectable clock for `report.generated_at`, constructs the report, and serializes it after artifact validation. An artifact can therefore become unavailable before the run actually enters `COMPLETED` without another pre-commit proof.
Reproduction: Start from a valid `CHECKING` Runtime and use its injected clock to delete stdout on the second completion clock call, which supplies `report.generated_at` after `validate_completed_record()` returns. `complete_run()` succeeds with canonical `COMPLETED`, both terminal events, and a report whose stdout artifact path no longer exists.
Impact: Required evidence need not exist when canonical completion is assigned, contrary to the mandatory rule that required evidence artifacts exist before completion. This is a bounded internal ordering window, separate from accepted post-completion artifact loss.
Required fix: Make filesystem artifact proof the final Runtime-controlled preparation step immediately before canonical assignment, after timestamp allocation, report-model construction, and serialization, or perform a final in-preparation gate there. Do not reintroduce post-commit validation. Add separate tests for loss before validation, during validation, after validation but before assignment, and after assignment.

### SOL-RM03

ID: `SOL-RM03`
Severity: MEDIUM
Location: `src/tomtit/reporting.py:105`
Problem: `build_terminal_report_from_validated_record()` is a publicly named module function that performs no completion validation, accepts the ordinary `RunRecord` type, and is advertised to callers by the public builder's docstring. Its validation precondition is conventional only.
Reproduction: Take a valid completed record, use unchecked `model_copy(update={"state": RunState.CHECKING})`, and call `build_terminal_report_from_validated_record()` directly. It accepts the invalid state and returns a `COMPLETED` terminal report, which can be persisted with the public writer.
Impact: The reporting module exposes a practical path around the required trust-boundary reconstruction, allowing a terminal report to be generated and published from a record that `build_terminal_report()` rejects.
Required fix: Make the already-validated builder private/internal and stop directing general callers to it. Keep `build_terminal_report()` as the sole public report-construction trust boundary; Runtime may import the explicitly private helper after its own gate. Do not add a wrapper type solely for aesthetics.

### SOL-RL04

ID: `SOL-RL04`
Severity: LOW
Location: `tests/domain/test_evaluation_models.py:500`, `tests/domain/test_evaluation_models.py:532`
Problem: Commit G removes the four originally identified Mypy locations but introduces two new avoidable `attr-defined` errors in SOL-M03 tests. Their narrow comments ignore `union-attr`, which does not cover the actual diagnostic.
Reproduction: Run `python3 -m mypy src tests`; it exits `1` with 83 errors and explicitly reports uncovered `attr-defined` diagnostics at lines 500 and 532. There are zero source diagnostics.
Impact: The patch does not fully meet its bounded typing-cleanup claim, although runtime correctness and the test suite are unaffected.
Required fix: Correct the two narrow ignores or use a local narrowed `FrozenPayload` value. Do not perform broad cleanup of the accepted repository test-typing baseline.

## 20. Definition of Done

| Condition | Status |
|---|---|
| Expected clean Git state and exact graph | PASS |
| Commit G/H path scope exact | PASS |
| No later-phase reachable behavior | PASS |
| `complete_run()` is the only Runtime completion entry | PASS |
| Generic terminal transition rejected | PASS |
| Candidate construction before canonical mutation | PASS |
| Structural and filesystem validation before canonical mutation | PASS |
| Report-model construction and serialization before canonical mutation | PASS |
| Preparation failures leave clean `CHECKING` state | PASS |
| Artifact proof remains true through canonical assignment | FAIL |
| Every completed transition has `RUN_COMPLETED` | FAIL |
| Every completed run has immutable terminal report | FAIL |
| Completed report has canonical `report_path` | FAIL |
| Successful chronology is monotonic and policy-compatible | PASS |
| Runtime remains sole clock authority | PASS |
| No practical unvalidated report-building path | FAIL |
| RequiredCheck ID/argv/cwd authority | PASS |
| FrozenPayload normal deep immutability | PASS |
| FrozenPayload input, JSON, schema, and journal behavior | PASS |
| Original SOL-L04 diagnostics removed | PASS |
| No new avoidable Mypy diagnostic | FAIL |
| F-003 regression matrix | PASS |
| Successful terminal state/events/report coherent | PASS |
| Repeated completion/publication cannot duplicate or overwrite | PASS |
| Required pytest, compile, Ruff, and diff checks | PASS |
| SOL-H01 resolved | FAIL |
| SOL-M02 resolved | PASS |
| SOL-M03 resolved | PASS |
| F-002 resolved | FAIL |
| F-003 resolved | PASS |
| F-004 resolved | PASS |

No required condition is `NOT_PROVEN`; all failures have deterministic
reproductions.

## 21. Final finding statuses

```text
SOL-H01: OPEN
SOL-M02: RESOLVED
SOL-M03: RESOLVED
SOL-L04: OPEN_LOW

SOL-RH01: OPEN
SOL-RM02: OPEN
SOL-RM03: OPEN
SOL-RL04: OPEN

F-002: OPEN
F-003: RESOLVED
F-004: RESOLVED

BASELINE-L01: OPEN
AUDIT-L02: ACCEPTED_LOW
F-005: OPEN
```

## 22. Final gate

```text
COMMIT_3: PENDING_OWNER_AUTHORIZATION
NEXT_TASK: CORRECTIVE_PATCH_FOR_SOL_RH01_SOL_RM02_SOL_RM03
COMMIT_3_NOT_AUTHORIZED_BY_REVIEWER
```
