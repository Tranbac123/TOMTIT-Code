# Codex Sol Review — F-002, F-003, and F-004

## 1. Verdict

```text
VERDICT=REQUEST_CHANGES
F-002=OPEN
F-003=RESOLVED
F-004=OPEN
```

The corrective commit resolves the listed structural completion cases and the
Attempt-1 `NO_PROGRESS` invariant, but it does not fully resolve completion
atomicity or deep event-payload immutability. Two medium-or-higher findings
remain, so the approval gate is not met.

## 2. Model used

Model used: Codex Sol.

Sol was appropriate because this was a source-level correctness review across
canonical Pydantic reconstruction, Runtime transition authority, filesystem
evidence validation, report generation, and custom Pydantic serialization for
deeply immutable values. The review required adversarial behavior probes in
addition to diff and test inspection.

## 3. Reviewed commits

```text
corrective_implementation=c0aa507e90ae4ab7097bcf497a14423c050a4620
implementation_report=6bf4d4b5799c1d4916e8eef04689fad8024bd325
implementation_parent=abca5d066fff10d5d085a96b87ec6275d17aff19
reviewed_target_head=6bf4d4b5799c1d4916e8eef04689fad8024bd325
```

The review-report commit is the commit containing this file and is recorded in
the final reviewer handoff; it cannot be embedded in its own preimage without
rewriting history.

## 4. Initial Git state

All commands in this section ran before this report was created.

| Command | Exit code | Exact output |
|---|---:|---|
| `git status --short` | 0 | `<empty>` |
| `git status --porcelain=v1 --untracked-files=all` | 0 | `<empty>` |
| `git branch --show-current` | 0 | `main` |
| `git rev-parse HEAD` | 0 | `6bf4d4b5799c1d4916e8eef04689fad8024bd325` |
| `git rev-parse HEAD^` | 0 | `c0aa507e90ae4ab7097bcf497a14423c050a4620` |
| `git rev-parse HEAD^^` | 0 | `abca5d066fff10d5d085a96b87ec6275d17aff19` |

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

`git log --oneline --decorate -10` exited `0`:

```text
6bf4d4b (HEAD -> main) docs(audit): record F002 F003 F004 corrective patch
c0aa507 fix(runtime): enforce completion and event invariants
abca5d0 docs(audit): record Terra baseline rereview
0927926 docs(audit): correct recovered baseline test evidence
239f1e9 docs(audit): record Terra baseline review
eb5bb39 docs(audit): record recovered baseline evidence
1e87e99 chore(repo): establish recovered reviewed baseline
```

Conclusion: the initial branch, graph, tracked worktree, index, and untracked
project-file state matched the required preconditions. Only permitted ignored
generated artifacts existed.

## 5. Commit scope verification

`git show --format=fuller --stat` and `--name-status` for both reviewed commits
exited `0`.

Commit `c0aa507e90ae4ab7097bcf497a14423c050a4620` modifies exactly:

```text
M src/tomtit/domain/evaluation.py
M src/tomtit/reporting.py
M src/tomtit/runtime/runtime.py
M src/tomtit/runtime/state.py
M tests/domain/test_evaluation_models.py
M tests/scenarios/test_scenario_1.py
```

Its stat is `6 files changed, 786 insertions(+), 53 deletions(-)`.

Commit `6bf4d4b5799c1d4916e8eef04689fad8024bd325` adds exactly:

```text
A REPORT_CLAUDE_CORRECTIVE_F002_F003_F004.md
```

Its stat is `1 file changed, 320 insertions(+)`. Commit scope matches the task.

## 6. Source architecture review

`RunRecord.validate_runtime_snapshot()` is the structural authority for
completed-state invariants. `validate_completed_record()` reconstructs the
record from a detached Python dump, thereby defeating unchecked
`model_copy(update=...)` changes, and then performs the three filesystem-backed
regular-file checks. Both `Runtime.transition()` and `build_terminal_report()`
call that same explicit gate.

This placement in `src/tomtit/runtime/state.py` is acceptable for the bounded
MVP. Pydantic model validators do not touch the filesystem; ordinary state
construction has no filesystem dependency; filesystem access occurs only in
the explicit completion gate and the pre-CHECKING Runtime artifact check. The
dependency direction remains understandable and testable.

The small shared predicates for check coverage, patch completeness, and
artifact type avoid duplicating those rules. The preliminary Runtime evidence
screen delegates to the same structural predicates and cannot independently
admit a failed exit code, missing/duplicate/unexpected check ID, incomplete
patch, missing diff, or declared scope violation. However, the shared check
predicate omits contract-to-observation `argv` and `cwd_relative` consistency;
see `SOL-M02`.

## 7. F-002 verification

### Structural completion requirements

An independent in-memory harness began with valid Scenario 1 completion records
and called both `validate_completed_record()` and `build_terminal_report()` for
each mutation. It exited `0`; all 21 structural/check cases were rejected by
both boundaries:

- non-`COMPLETED` state;
- non-`COMPLETED` outcome;
- non-`ACCEPTANCE_SATISFIED` stop reason;
- terminal fields inconsistent with `StopDecision`;
- non-completion `next_action`;
- `repairable=true`;
- `owner_input_required=true`;
- wrong verification status;
- missing completion basis;
- unsatisfied criterion;
- unresolved blocking finding;
- incomplete patch evidence;
- no diff;
- declared scope violation;
- missing required result;
- duplicate required result;
- unexpected check ID;
- unaccepted exit code;
- foreign `run_id`;
- foreign attempt number;
- missing observed result despite a successful Worker claim.

Required checks are keyed by stable check ID, and the required ID set must
equal the observed ID set. Each required ID must have exactly one result and an
accepted exit code. Worker claims do not participate in this decision.

### Artifact validation

The independent artifact matrix exercised missing and directory paths for each
of stdout, stderr, and patch evidence. All six cases were rejected by both
`validate_completed_record()` and `build_terminal_report()` with
`CompletionInvariantError`. The harness exited `0` with:

```text
TOTAL=27 ALL_REJECTED=PASS
```

No finding is raised for the explicitly deferred symlink-confinement, content-
hashing, or real-workspace path-confinement limitations.

### Runtime atomicity

The normal pre-commit failure path passes. An independent clock hook removed
stdout when `completed_at` was requested, before candidate validation. Result:

```text
run_result=CompletionInvariantError: check unit-tests: stdout artifact must exist and be a regular file
state=CHECKING
outcome=None
stop_reason=None
RUN_COMPLETED_count=0
report_exists=False
```

The candidate is therefore validated before it becomes canonical.

End-to-end atomicity nevertheless fails when evidence disappears after that
gate succeeds but before the report boundary revalidates it. The deterministic
reproduction left canonical `COMPLETED`, appended `RUN_COMPLETED`, and produced
no report. See `SOL-H01`.

### Report field authority

All report fields are read from the reconstructed `validated_record`; no field
is read from the original untrusted object after the gate. This is structurally
correct. The accepted `argv`/`cwd_relative` inconsistency in `SOL-M02` is a
missing invariant in the reconstructed record, not a bypass of that read rule.

## 8. F-003 verification

An independent harness exited `0` and produced:

```text
direct_attempt1_terminal: PASS (ValidationError)
copied_attempt2_to_attempt1_revalidate: PASS (ValidationError)
attempt1_stopdecision_no_progress: PASS (ValidationError)
attempt1_report_boundary: PASS (ValidationError)
attempt2_positive_control: PASS (constructed)
```

The canonical validator checks both the terminal `stop_reason` and the nested
`StopDecision.stop_reason`. Attempt 2 `NO_PROGRESS` is not rejected merely for
using that reason. No Scenario 4 execution, progress comparison, Attempt 2
execution, or Attempt 3 eligibility was added.

F-003 is independently verified as resolved.

## 9. F-004 verification

The custom Pydantic schema correctly accepts dictionary/list input and freezes
nested JSON-shaped mappings and sequences. Independent probes confirmed:

- top-level item assignment is rejected;
- nested mapping item assignment is rejected;
- nested sequence append is rejected;
- nested tuple item replacement is rejected;
- assignment through the current mapping proxy is rejected;
- changes to the original top-level dict, nested dict, and nested list do not
  reach the event;
- `model_dump(mode="json")` returns normal dicts/lists;
- `model_dump_json()` and `model_validate_json()` round-trip equivalent content
  and restore frozen containers;
- JSON Schema generation succeeds;
- event-log serialization and detached reads still work.

The same harness exited `1` because ordinary assignment can replace the exposed
slot itself:

```text
internal_attribute_replacement: FAIL (MUTATION_SUCCEEDED)
```

After `event.payload._data = MappingProxyType({"top": "CORRUPTED"})`, the
already validated event reads and serializes the replacement value. This
violates the explicit internal-attribute immutability requirement; see
`SOL-M03`.

The normal Runtime event contract is JSON-shaped. Unsupported custom mutable
leaf values are not used by the Runtime and cannot be serialized into the
event journal, so no separate finding is raised for custom-value hashing.

## 10. Event journal compatibility

Append ordering, versioned event values, JSON serialization, detached reads,
and Runtime clock authority continue to pass. The journal stores serialized
strings, so changing one returned event object does not rewrite stored history;
a subsequent read is reconstructed from the original string. That defense does
not make the individual `EvaluationEvent` deeply immutable, which is the
separate F-004 failure in `SOL-M03`.

## 11. Canonical `unresolved_findings`

The field is necessary because completion cannot otherwise prove that no
blocking finding remains. `Finding` is the correct existing canonical value
type. Only `FindingSeverity.BLOCKING` prevents completion; warning and info
values remain valid and representable. The tuple and frozen `Finding` models
make the collection deeply immutable, and Pydantic dump/reconstruction remains
stable. Having no producer on the current fake Scenario 1 path is acceptable;
the field adds state needed for validation, not verifier execution behavior.

## 12. Runtime exception behavior

`Runtime.transition()` has no documented promise that all completion failures
raise only `InvalidTransitionError`. Repository search found no production
caller catching only that class; the only catches are tests of other transition
preconditions. `CompletionInvariantError` is a `ValueError`, and structural
candidate reconstruction can also raise Pydantic `ValidationError`.

For this bounded internal MVP, translation is not currently required. The
exception distinction usefully separates illegal transition requests from
failure to prove completion. No compatibility finding is raised.

## 13. Complexity and scope assessment

The custom payload type and Pydantic hooks are justified by the required
combination of dictionary/list input, deep freezing, detached input, normal JSON
output, JSON round-trip validation, and schema generation. The implementation
needs one additional immutability guard, but its overall abstraction is not
unnecessary.

The completion helpers and candidate-building split are directly justified by
F-002. `unresolved_findings` is directly justified by the blocking-finding
completion invariant. No unused future-phase helper or generalized execution
framework was added.

The implementation diff adds no reachable behavior for Attempt 2, revision,
`WAITING_OWNER`, Attempt 3, progress comparison, real workspace management,
subprocess execution, additional worker adapters, LLM verification, database,
CLI UI, Memory, model routing, or graph execution. `_ALLOWED_TRANSITIONS`
remains the Scenario 1-only path.

## 14. Commands, exit codes, and counts

| Command | Exit code | Result |
|---|---:|---|
| `python3 -m pytest --collect-only -q` | 0 | `43 + 24 + 3 + 53 = 123` collected |
| `python3 -m pytest -q` | 0 | 123 passed |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 53 passed |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 24 passed |
| `python3 -m compileall -q src tests` | 0 | Passed, no output |
| `ruff check .` | 0 | Passed, no output |
| `git diff --check` | 0 | Passed before report creation |
| F-002 structural/artifact harness | 0 | 27 negative cases rejected by both boundaries |
| F-003 independent harness | 0 | Four negative cases rejected; Attempt 2 positive control accepted |
| F-004 independent harness | 1 | All public/nested/JSON/journal probes pass; exposed-slot replacement fails |
| post-gate completion/report timing probe | 0 | Reproduced invalid terminal state with no report |
| required-check metadata consistency probe | 0 | Reproduced acceptance of inconsistent `argv` and `cwd_relative` |

Collection by file:

```text
tests/domain/test_contracts.py:         43
tests/domain/test_evaluation_models.py: 24
tests/domain/test_schema_generation.py: 3
tests/scenarios/test_scenario_1.py:     53
total:                                  123
```

## 15. Mypy informational result

`python3 -m mypy src tests` exited `1`:

```text
Found 136 errors in 4 files (checked 15 source files)
```

All diagnostics are under `tests/**`; there are zero diagnostics under
`src/**`. This reproduces the implementation report's count. Most diagnostics
are the repository's accepted list-input-versus-tuple annotation style. The
new broad `dict[str, object]` expansion used by the F-003 helper and one
deliberate immutable-mapping assignment create avoidable new diagnostics; see
`SOL-L04`.

## 16. Findings

### SOL-H01

ID: `SOL-H01`
Severity: HIGH
Location: `src/tomtit/runtime/runtime.py:170`, `src/tomtit/runtime/runtime.py:215`, `src/tomtit/runtime/runtime.py:232`, `src/tomtit/runtime/runtime.py:172`
Problem: Completion validation is not atomic across canonical completion and terminal-report validation. Runtime validates and stores the completed candidate, appends `RUN_COMPLETED`, and only then calls `build_terminal_report()`, which reruns the filesystem-backed gate. If an artifact disappears after the transition gate but before the report gate, that second validation fails after canonical terminal state and history have already been committed.
Reproduction: Use Runtime's injected clock to delete the observed stdout file when the `RUN_COMPLETED` timestamp is requested, after `validate_completed_record(candidate)` has returned. `run_scenario_1()` raises `CompletionInvariantError`, while `runtime.record.state == COMPLETED`, outcome and stop reason are set, one `RUN_COMPLETED` event exists, and `report.v1.json` does not exist.
Impact: Runtime can retain a canonical successful terminal outcome and success event after the shared validator says the evidence no longer supports completion. It also violates the requirement that a failed completion validation leave state at `CHECKING`, append no `RUN_COMPLETED`, and produce no terminal report.
Required fix: Coordinate candidate validation and in-memory terminal-report construction before committing canonical `COMPLETED` and appending `RUN_COMPLETED`, so every fallible invocation of the shared completion gate occurs before canonical mutation. Add an adversarial test that deletes an artifact after the first gate opportunity but before current report construction and proves `CHECKING`, unset terminal fields, no `RUN_COMPLETED`, and no report.

### SOL-M02

ID: `SOL-M02`
Severity: MEDIUM
Location: `src/tomtit/runtime/state.py:102`
Problem: `required_check_coverage_error()` matches stable IDs and validates multiplicity/exit codes, but does not prove that an observed result's `argv` and `cwd_relative` equal the corresponding `RequiredCheck`. A copied result can retain `check_id="unit-tests"` while claiming an unrelated command and working directory.
Reproduction: Starting from a valid completed Scenario 1 record, replace its sole observed result through `model_copy(update={"argv": ("totally", "different", "command"), "cwd_relative": "different-dir"})`. Both `validate_completed_record()` and `build_terminal_report()` accept it, and the terminal report emits the inconsistent command and directory.
Impact: A terminal report described as reconstructed and validated can contradict the authoritative check contract about what command ran. Stable ID coverage and a passing exit code alone do not validate the report's recorded command evidence.
Required fix: After stable-ID lookup, require each observed result's `argv` and `cwd_relative` to equal the matched `RequiredCheck` before completion. Add direct gate and report-boundary negative tests for both mismatches.

### SOL-M03

ID: `SOL-M03`
Severity: MEDIUM
Location: `src/tomtit/domain/evaluation.py:76`, `src/tomtit/domain/evaluation.py:80`
Problem: `MappingProxyType` prevents writes through the current `_data` mapping, but `FrozenPayload` does not prevent reassignment of the exposed `_data` slot after construction. The wrapper is therefore not immutable.
Reproduction: Construct an `EvaluationEvent` with a normal nested dict/list payload, then execute `event.payload._data = MappingProxyType({"top": "CORRUPTED"})`. Assignment succeeds; subsequent reads and JSON serialization expose the replacement value.
Impact: Canonical `EvaluationEvent.payload` can change after validation through ordinary attribute assignment. Hash/equality and serialized content can change, violating F-004 even though the event journal's serialized backing history remains detached.
Required fix: Make the payload container conventionally frozen after initialization, permitting `_data` assignment only during construction, and add top-level and nested tests that direct internal-attribute replacement is rejected. Preserve the existing JSON, schema, and round-trip behavior.

### SOL-L04

ID: `SOL-L04`
Severity: LOW
Location: `tests/scenarios/test_scenario_1.py:364`, `tests/scenarios/test_scenario_1.py:1000`, `tests/scenarios/test_scenario_1.py:1004`, `tests/scenarios/test_scenario_1.py:1040`
Problem: Newly introduced deliberate-invalid-input tests add avoidable Mypy diagnostics: an indexed assignment to the declared read-only `Mapping` and repeated `RunRecord(**dict[str, object])` expansions.
Reproduction: Run `python3 -m mypy src tests`; it exits `1` with 136 errors, including the listed new locations. Source remains at zero errors.
Impact: The test suite's static-analysis noise increased and makes future type regressions harder to distinguish, although runtime behavior and required tests are unaffected.
Required fix: Add narrow ignores to the deliberate invalid operations or give the helper a bounded typed construction path. Do not mechanically rewrite the repository's accepted list-input-versus-tuple test style.

## 17. Definition of Done

| Condition | Status |
|---|---|
| Expected initial Git graph and clean project tree | PASS |
| Commit E modifies exactly six authorized files | PASS |
| Commit F adds only the implementation report | PASS |
| No later-phase behavior or significant scope expansion | PASS |
| One shared completion-rule implementation | PASS |
| Listed F-002 structural mutations rejected | PASS |
| Required IDs covered exactly once with accepted exits | PASS |
| Observed check command metadata matches its contract | FAIL |
| Missing/directory stdout, stderr, and patch rejected by gate and report | PASS |
| Candidate rejected before canonical commit on first gate failure | PASS |
| Any completion-gate failure leaves `CHECKING` and no completion event/report | FAIL |
| Report reads only reconstructed/validated record | PASS |
| Pydantic model validation remains free of filesystem I/O | PASS |
| Attempt 1 `NO_PROGRESS` rejected on every required path | PASS |
| Attempt 2 `NO_PROGRESS` positive control accepted structurally | PASS |
| Payload top-level and nested item mutation rejected | PASS |
| Payload input containers detached | PASS |
| Payload internal attributes cannot replace canonical data | FAIL |
| Payload JSON dump, round trip, and schema generation | PASS |
| Event journal ordering, serialization, and detached stored history | PASS |
| Full required suite and quality commands | PASS |
| Zero Mypy diagnostics under `src/**` | PASS |
| F-002 resolved | FAIL |
| F-003 resolved | PASS |
| F-004 resolved | FAIL |

No required item is `NOT_PROVEN`; the failing items have deterministic
reproductions.

## 18. Final status and remaining open findings

```text
F-002: OPEN
F-003: RESOLVED
F-004: OPEN

SOL-H01: OPEN
SOL-M02: OPEN
SOL-M03: OPEN
SOL-L04: OPEN

BASELINE-L01: OPEN
AUDIT-L02: ACCEPTED_LOW
F-005: OPEN
```

## 19. Final gate

```text
COMMIT_3: PENDING_OWNER_AUTHORIZATION
NEXT_TASK: CORRECTIVE_PATCH_FOR_SOL_H01_SOL_M02_SOL_M03
COMMIT_3_NOT_AUTHORIZED_BY_REVIEWER
```
