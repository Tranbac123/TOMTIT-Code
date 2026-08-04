# Codex Sol Final Cleanup-Retry Corrective Rereview — Gate 1

## VERDICT

```text
VERDICT=APPROVE_WITH_NON_BLOCKING_FINDINGS
```

The Owner-authorized corrective source removes both mechanisms that produced
the prior contradiction. Terminal events are appended before publication and
are rolled back if publication fails. The real publisher now performs only the
write-once final-name creation and returns immediately; private staged-file
cleanup occurs separately after the commit point and cannot roll Runtime back.

Independent failure injections produced only two bounded results:

```text
clean retryable CHECKING rollback before final publication
or
coherent COMPLETED state, terminal-event pair and Runtime terminal report
```

The forbidden third result was absent. A private staged file may survive an
injected post-publication cleanup failure, but Runtime remains coherently
completed, repeated completion is rejected, and ordinary success removes the
staged file. This is a bounded nonblocking cleanup limitation.

Reviewer model: Codex GPT-5.6 Sol High.

## TARGET_SOURCE_COMMIT

```text
368e6870c9dfb789273e62b066ab8942452e1241
```

## REVIEWED_DIFF_RANGE

```text
bfe9fc4a662c07a2de286dbb7609547b907758c4..368e6870c9dfb789273e62b066ab8942452e1241
```

The reviewed source diff changes exactly:

```text
M src/tomtit/reporting.py
M src/tomtit/runtime/runtime.py
M tests/scenarios/test_scenario_1.py

3 files changed, 308 insertions(+), 17 deletions(-)
```

`src/tomtit/runtime/events.py`, `src/tomtit/runtime/state.py` and
`src/tomtit/domain/evaluation.py` are unchanged in the reviewed diff.

## REVIEW_REPORT_EXPECTED_PARENT

```text
10aba66d6e867ad355d8d463316845ef86be6af6
```

Pre-review lineage was exact:

```text
branch=main
HEAD=10aba66d6e867ad355d8d463316845ef86be6af6
HEAD^=368e6870c9dfb789273e62b066ab8942452e1241
TARGET_SOURCE_IS_ANCESTOR_OF_IMPLEMENTATION_REPORT=true
```

## GOVERNANCE_DOCUMENT_HASHES_REVIEWED

```text
docs/OFFICIAL_BUILD_AND_AI_CODING_PROCESS_V1.md:
  SHA-256=a3128e2cac4956b642161e4b6583764200a89d831a291c8926e93549aac61a13

docs/EVALUATION_CONTRACT_V1.md:
  SHA-256=0e9c5184f44504add626a81092c1795399d3fcdd3c64d11181c59db541060b4a

docs/VERIFIER_POLICY_V1.md:
  SHA-256=2da9fb1b5f70e2ca75e55cf3ebabf7958a19018b8215d366c3b83b231eaec915
```

These hashes identify the exact uncommitted governance bytes used during the
review. They are not represented as separate Owner-approval evidence.

## FILES_REVIEWED

Governance and product policy:

```text
CLAUDE.md
SYSTEM.md
docs/OFFICIAL_BUILD_AND_AI_CODING_PROCESS_V1.md
docs/EVALUATION_CONTRACT_V1.md
docs/VERIFIER_POLICY_V1.md
docs/MVP_EXECUTION_POLICY_V0.md
docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md
```

Required audit chain:

```text
REPORT_CODEX_SOL_FINAL_REREVIEW_SOL_RH01_RM02_RM03.md
REPORT_CLAUDE_FINAL_SOL_RH01_F002_PATCH.md
REPORT_CLAUDE_CORRECTIVE_SOL_RH01_RM02_RM03.md
```

Implementation and preservation evidence:

```text
src/tomtit/runtime/runtime.py
src/tomtit/reporting.py
src/tomtit/runtime/events.py
src/tomtit/runtime/state.py
src/tomtit/domain/evaluation.py
tests/scenarios/test_scenario_1.py
tests/domain/test_evaluation_models.py
tests/domain/test_contracts.py
tests/domain/test_schema_generation.py
```

## COMMANDS_AND_EXIT_CODES

| Command | Exit code | Result |
|---|---:|---|
| `git status --short` | 0 | Only the three permitted governance candidates were untracked. |
| `git status --porcelain=v1 --untracked-files=all` | 0 | Same three permitted files; no unexpected project file. |
| `git status --ignored --short` | 0 | Permitted governance files plus existing ignored caches, bytecode and `.DS_Store` files. |
| `git branch --show-current` | 0 | `main` |
| `git rev-parse HEAD` | 0 | `10aba66d6e867ad355d8d463316845ef86be6af6` |
| `git rev-parse HEAD^` | 0 | `368e6870c9dfb789273e62b066ab8942452e1241` |
| `git merge-base --is-ancestor 368e687... 10aba66...` | 0 | Required ancestry holds. |
| `git log --oneline --decorate -18` | 0 | Required linear audit history present. |
| `git show --stat --oneline 368e687...` | 0 | Three authorized source/test files; 308 insertions, 17 deletions. |
| `git show --stat --oneline 10aba66...` | 0 | Implementation-report-only commit; 321 insertions. |
| `python3 -m pytest --collect-only -q` | 0 | 174 collected: 43 contracts + 29 evaluation + 3 schema + 99 Scenario 1. |
| `python3 -m pytest -q` | 0 | 174 passed. |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed. |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 29 passed. |
| `python3 -m compileall -q src tests` | 0 | Passed; no output. |
| `ruff check .` | 0 | Zero findings. |
| `git diff --check` | 0 | No whitespace errors. |
| `python3 -m mypy src tests` | 1 | 83 diagnostics in four test files; zero diagnostics under `src/**`. |
| `git diff --stat bfe9fc4...368e687` | 0 | Three files; 308 insertions, 17 deletions. |
| `git diff --check bfe9fc4...368e687` | 0 | No whitespace errors. |
| Independent A–G temporary harness | 0 | All required paths executed; forbidden state absent. |
| Negative control 1, isolated copy | 1 | Two expected Scenario 1 tests failed. |
| Negative control 2, isolated copy | 1 | Two expected Scenario 1 tests failed. |
| Negative control 3, isolated copy | 1 | Four expected Scenario 1 tests failed. |

Mypy matches the accepted deferred baseline. The two SOL-RL04 test-only
`attr-defined` diagnostics remain; no production source diagnostic was emitted.

## INDEPENDENT_REPRODUCTIONS

The A–G harness used the real Runtime and filesystem in isolated temporary
directories. Its temporary script was removed after execution. No tracked
source or test was modified.

### A. Event-batch failure before publication

Injected `_append_prepared_batch` failure while wrapping the publisher with a
call recorder:

```text
publisher_calls=0
state=CHECKING
outcome=None
report_path=None
RUN_COMPLETED=0
REPORT_GENERATED=0
event_count=9
final_report=false
staged_report=false
```

After removing the injection, retry produced coherent `COMPLETED`, 11 events,
one terminal-event pair, one final report and no staged file.

### B. Partial terminal-event append

The injected batch appended the first prepared event and raised. Rollback
restored the journal to nine nonterminal events, canonical `CHECKING`, no final
report and no terminal events. After retry, the first nine serialized event
bytes were byte-identical, the journal contained 11 events, and completion
occurred exactly once.

### C. Final-link creation failure

The injected publisher raised before creating the link. Runtime restored
`CHECKING`, zero terminal events and no final or staged report. Retry succeeded
exactly once without manual cleanup.

### D. Staged cleanup failure after final publication

Cleanup was replaced by a no-op after the real publisher created the final
report. Observed state:

```text
state=COMPLETED
outcome=COMPLETED
report_path=<final report>
RUN_COMPLETED=1
REPORT_GENERATED=1
event_count=11
final_report=true
staged_report=true
```

A second `complete_run()` raised `InvalidTransitionError`. The committed record,
event pair and final report remained unchanged. No retry is required after this
coherent committed completion.

### E. Previous Codex Path A

Event-batch failure was combined with cleanup that removed nothing. Because the
event batch now precedes publication, observed rollback was `CHECKING`, zero
terminal events, no final report and one stale private staged file. After
restoring normal functions, retry overwrote that staged file and completed once
without manual cleanup.

### F. Previous Codex Path B and publication boundary

Direct use of the real publisher confirmed:

```text
final report created=true
staged file retained=true
second publication rejected with FileExistsError=true
```

The publisher contains only `os.link(staged, final)` followed by return. It does
not unlink staged content or run validation, serialization, callbacks or
cleanup. Runtime supplies staged and final paths from the same report directory.

Injecting staged cleanup failure at its new post-publication location left the
same coherent completed result as case D: one event pair, one final report, and
a private stale staged file. Runtime did not roll back.

### G. Forbidden-state search

Every captured result from A through F was checked for:

```text
state=CHECKING
AND RUN_COMPLETED=0
AND REPORT_GENERATED=0
AND report.v1.json exists
```

Result:

```text
FORBIDDEN_STATE=ABSENT
```

Normal completion also preserved terminal event order
`RUN_COMPLETED, REPORT_GENERATED`, monotonic terminal timestamps, one final
write-once report and no staged file. The public builder independently rejected
the copied non-completed record with `ValidationError`.

## NEGATIVE_CONTROLS

All controls ran in isolated local repository copies. The tracked review tree
was never altered.

### Control 1 — publication before terminal-event append

Restored the prior `Runtime._commit_completion` ordering while retaining the
corrected publisher. Scenario 1 exited 1 with exactly these failures:

```text
test_event_batch_append_failure_happens_before_any_report_exists
test_final_report_removal_failure_path_cannot_occur
```

The second test reproduced the literal forbidden contradiction: canonical
`CHECKING`, zero terminal events and an existing `report.v1.json`. This proves
event-before-publication ordering is behaviorally required.

### Control 2 — ambiguous link-and-unlink publisher

Restored the former `publish_staged_report` while retaining the corrected
Runtime ordering. Scenario 1 exited 1 with exactly:

```text
test_staged_cleanup_failure_after_publication_keeps_the_terminal_result
test_publisher_does_not_remove_the_staged_report
```

The control did not need to reproduce a canonical contradiction: it directly
showed that the old publisher still owned a fallible post-link unlink and
violated the new unambiguous publication boundary.

### Control 3 — old rollback after successful publication

Restored both the former Runtime commit body and former ambiguous publisher.
Scenario 1 exited 1 with exactly four failures:

```text
test_event_batch_append_failure_happens_before_any_report_exists
test_staged_cleanup_failure_after_publication_keeps_the_terminal_result
test_final_report_removal_failure_path_cannot_occur
test_publisher_does_not_remove_the_staged_report
```

`test_final_report_removal_failure_path_cannot_occur` again reproduced the
forbidden `CHECKING + zero terminal events + final report` state. The controls
demonstrate that the passing corrected suite depends on both source changes.

## FINDING_STATUS

```text
SOL-RH01=RESOLVED
F-002=RESOLVED

SOL-RM02=RESOLVED
SOL-RM03=RESOLVED

SOL-M02=PRESERVED
SOL-M03=PRESERVED
F-003=PRESERVED
F-004=PRESERVED

SOL-RL04=DEFERRED_LOW
BASELINE-L01=DEFERRED_LOW
AUDIT-L02=DEFERRED_LOW
F-005=DEFERRED
```

### SOL-RH01 — RESOLVED

All reproduced pre-publication failures restore the previous canonical record,
truncate only terminal journal additions, leave no final report and retry
without manual cleanup. Successful final publication is now the unambiguous
commit point. No supported injected path produced contradictory canonical and
filesystem evidence. Post-publication private-stage cleanup failure preserves a
coherent terminal result.

### F-002 — RESOLVED

Gate 1 Scenario 1 now has exactly one coherent completed result or one clean,
retryable pre-publication rollback. No third contradictory result was observed.

### SOL-RM02 and SOL-RM03 — RESOLVED

The final `validate_completed_record(candidate)` remains the last validation in
preparation; no Runtime clock call, ID allocation, report construction,
serialization or callback follows it before canonical assignment. Artifact
timing regressions pass. `build_terminal_report()` remains the sole public trust
boundary, the former public helper name remains absent, and the private helper
has only its definition and Runtime's validated preparation call site.

### Preserved findings

- SOL-M02: exact `check_id`, `argv` and `cwd_relative` authority remains in the
  unchanged shared predicate; all related tests pass.
- SOL-M03 and F-004: `src/tomtit/domain/evaluation.py` is unchanged; deep frozen,
  detached, JSON/schema and journal behaviors pass.
- F-003: Attempt 1 `NO_PROGRESS` rejection remains unchanged and tested.
- Chronology remains `completed_at <= report.generated_at <=
  RUN_COMPLETED.timestamp <= REPORT_GENERATED.timestamp`.
- Terminal event order remains `RUN_COMPLETED` then `REPORT_GENERATED`.
- Final publication remains write-once through `os.link` refusal to overwrite.

## PRODUCT_EVIDENCE_ASSESSMENT

```text
The Runtime completes or rolls back consistently without leaving
a partial terminal state, and the operation can be retried.

ASSESSMENT=PROVEN
```

The supported bounded in-process model has coherent completion, clean
pre-publication rollback, retry without manual cleanup, and no contradictory
Runtime terminal report.

## RISKS

Nonblocking current limitation:

- A private `.report.v1.json.staged` file may survive post-publication cleanup
  failure. It is not the Runtime terminal report, does not contradict execution
  truth, does not permit repeated completion, and ordinary successful cleanup
  removes it.

Explicitly bounded risks:

- Publication requires hard-link support in the current same-directory layout.
- No fsync or power-loss durability is claimed.
- Process crash recovery, concurrent completion, symlink confinement, artifact
  content hashing and real-workspace path confinement remain outside Gate 1's
  supported bounded model.
- The deferred test-only Mypy baseline remains, including SOL-RL04.

## SCOPE_AND_NON_GOALS

This review covered only the named commit and diff range, the reproduced Gate 1
cleanup/retry defect, publication ordering, rollback, journal preservation,
write-once Runtime terminal-report publication, the forbidden-state search, and
preservation of the already resolved findings.

The reviewer did not modify tracked source, tests, governance, implementation
reports, configuration, prior audit reports or gate evidence. No implementation
prompt, corrective patch, additional reviewer, Scenario 2 feature, crash
recovery, concurrency design, persistence layer or general transaction system
was created or authorized.

## UNPROVEN_ITEMS

- Process-crash and power-loss consistency are not proven.
- Published-report durability under power loss is not proven; no fsync occurs.
- Concurrent or parallel completion is not proven.
- Filesystems without hard-link support were not independently exercised.
- Symlink confinement, content identity and real-workspace path confinement
  remain deferred.
- The supported failure matrix is bounded to the declared in-process injection
  points rather than every theoretical OS or interpreter interruption.
- The governance hashes above identify reviewed bytes; this report does not
  itself establish a separate Owner-approved governance bundle.

None of these unproven items defeats the approved bounded Gate 1 product
evidence.

## REVIEW_BUDGET_CONSEQUENCE

```text
OWNER_AUTHORIZED_ADDITIONAL_CORRECTIVE_REREVIEW=CONSUMED
GATE_STATUS=APPROVED
FREEZE_SCENARIO_1_RECOMMENDED
NO_FURTHER_SCENARIO_1_REVIEW
```

No additional implementation cycle or rereview is authorized by this report.

## NEXT_GATE_RECOMMENDATION

```text
GATE_STATUS=APPROVED
FREEZE_SCENARIO_1
NEXT_GATE=MINIMAL_SCENARIO_2
NO_FURTHER_SCENARIO_1_REVIEW
```
