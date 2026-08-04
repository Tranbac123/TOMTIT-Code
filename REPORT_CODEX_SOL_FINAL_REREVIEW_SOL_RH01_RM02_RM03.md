# Codex Sol Final Corrective Rereview — Gate 1 Scenario 1

## VERDICT

```text
VERDICT=REQUEST_CHANGES
```

The corrective source closes the prior preparation-order and reporting-boundary
defects, and its normal in-process rollback handles publication and event-batch
failures. It does not establish the Gate 1 retryability evidence when cleanup
itself fails. Two independent injections leave canonical `CHECKING` with no
terminal events but a surviving `report.v1.json`; the next `complete_run()`
fails with `FileExistsError`. This is a contradictory terminal artifact and a
non-retryable result without manual filesystem cleanup, so SOL-RH01 and F-002
remain open.

Reviewer model: Codex GPT-5.6 Sol High.

Model reason: this review covers canonical Runtime completion, bounded atomicity,
rollback, event-journal consistency, filesystem publication, retryability and a
report-construction trust boundary. These are mandatory
`VERIFIER_MODE=INDEPENDENT_CORRECTNESS` triggers under the reviewed verifier
policy.

## TARGET_SOURCE_COMMIT

```text
96ac75b810cdd10530aa72013205e40f456eeb62
```

## REVIEWED_DIFF_RANGE

```text
ca3c9c31583a4a28a335c34dfdada89050acffd4..96ac75b810cdd10530aa72013205e40f456eeb62
```

The diff contains exactly:

```text
M src/tomtit/reporting.py
M src/tomtit/runtime/events.py
M src/tomtit/runtime/runtime.py
M tests/scenarios/test_scenario_1.py

4 files changed, 757 insertions(+), 80 deletions(-)
```

`src/tomtit/domain/evaluation.py` is unchanged in the reviewed source diff.

## REVIEW_REPORT_EXPECTED_PARENT

```text
e85160941e5405773885d81e29a813ff009c9e50
```

Pre-review lineage was exact:

```text
branch=main
HEAD=e85160941e5405773885d81e29a813ff009c9e50
HEAD^=96ac75b810cdd10530aa72013205e40f456eeb62
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

These hashes identify the exact uncommitted governance bytes used for this
review. They are not represented as Owner-approved bundle hashes.

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

Prior audit chain:

```text
REPORT_CLAUDE_CORRECTIVE_SOL_RH01_RM02_RM03.md
REPORT_CODEX_SOL_CORRECTIVE_REREVIEW.md
REPORT_CODEX_SOL_F002_F003_F004_REVIEW.md
REPORT_CLAUDE_CORRECTIVE_SOL_H01_M02_M03.md
```

Reviewed implementation and regression evidence:

```text
src/tomtit/reporting.py
src/tomtit/runtime/events.py
src/tomtit/runtime/runtime.py
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
| `git status --short` | 0 | Only the three permitted governance files were untracked. |
| `git status --porcelain=v1 --untracked-files=all` | 0 | Same three permitted files; no unexpected project file. |
| `git status --ignored --short` | 0 | Three permitted governance files plus ignored caches, bytecode and existing `.DS_Store` files. |
| `git branch --show-current` | 0 | `main` |
| `git rev-parse HEAD` | 0 | `e85160941e5405773885d81e29a813ff009c9e50` |
| `git rev-parse HEAD^` | 0 | `96ac75b810cdd10530aa72013205e40f456eeb62` |
| `git merge-base --is-ancestor 96ac75b... e851609...` | 0 | Required ancestry holds. |
| `git log --oneline --decorate -15` | 0 | Required linear source/report ancestry present. |
| `git show --stat --oneline 96ac75b...` | 0 | Four source/test files; 757 insertions, 80 deletions. |
| `git show --stat --oneline e851609...` | 0 | Implementation report only; 319 insertions. |
| `python3 -m pytest --collect-only -q` | 0 | 164 collected: 43 contracts + 29 evaluation + 3 schema + 89 Scenario 1. |
| `python3 -m pytest -q` | 0 | 164 passed. |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 89 passed. |
| `python3 -m pytest tests/domain/test_evaluation_models.py -q` | 0 | 29 passed. |
| `python3 -m compileall -q src tests` | 0 | No output; passed. |
| `ruff check .` | 0 | No findings. |
| `git diff --check` | 0 | No whitespace errors. |
| `python3 -m mypy src tests` | 1 | 83 diagnostics in four test files; zero diagnostics under `src/**`. |
| `git diff --stat ca3c9c3...96ac75b` | 0 | Four files; 757 insertions, 80 deletions. |
| `git diff --check ca3c9c3...96ac75b` | 0 | No whitespace errors. |
| Independent temporary failure harness | 0 | All injections executed and assertions/observations completed. |

The 83 Mypy diagnostics match the deferred test-typing baseline. The two
SOL-RL04 `attr-defined` diagnostics remain at
`tests/domain/test_evaluation_models.py:500` and `:532`; no source-level Mypy
diagnostic was emitted.

## INDEPENDENT_REPRODUCTIONS

The independent harness was a temporary uncommitted script, removed immediately
after execution. It used the real Runtime and filesystem in isolated temporary
directories. No tracked source or test was modified.

### 1. Failure after canonical assignment and before terminal-event commit

Injected `OSError` from `publish_staged_report` after preparation. Recovery
produced:

```text
state=CHECKING
outcome=None
report_path=None
RUN_COMPLETED=0
REPORT_GENERATED=0
event_count=9
final_report=false
staged_report=false
```

After removing the injection, retry completed with one final report, exactly one
`RUN_COMPLETED`, exactly one `REPORT_GENERATED`, 11 total events and no staged
file.

### 2. Failure during prepared event-batch append after report publication

The injected batch method appended the first prepared serialized terminal event
and then raised `OSError`. Recovery restored the checkpoint and produced the
same clean nine-event `CHECKING` result. The earlier nine nonterminal events
were unchanged. Retry then completed exactly once with the terminal pair and
one report.

This independently proves that ordinary supported publication and partial-batch
failures roll back record, journal and files when cleanup succeeds.

### 3. Artifact deletion in the SOL-RM02 window

The injected Runtime clock deleted stdout on completion clock call 2, after the
earlier CHECKING evidence opportunity and before the final gate. The final gate
raised `CompletionInvariantError`; Runtime remained `CHECKING` with nine events,
no terminal event and no report. The committed tests also cover all four
completion clock calls, deletion during the final validation walk, absence
before preparation, and accepted loss only after canonical completion.

### 4. Public report-builder rejection

A valid completed record copied with unchecked
`model_copy(update={"state": RunState.CHECKING})` was rejected by
`build_terminal_report()` with `ValidationError`. Repository search found the
former public name absent and the private helper referenced in production only
by `reporting.py` and Runtime's authoritative preparation path.

### 5. Staged-file removal failure during recovery

Injected publication failure plus a transient no-op staged cleanup left:

```text
state=CHECKING
terminal_events=0
final_report=false
staged_report=true
```

After cleanup behavior returned to normal, retry overwrote the stale private
staged file and completed exactly once. A transient stale staged file alone is
therefore retryable in the tested environment.

### 6. Final-report removal failure during recovery — blocking reproduction

Publication succeeded, the event-batch append raised, and final-report cleanup
was injected to fail as `_remove_quietly` explicitly permits. Observed result:

```text
state=CHECKING
outcome=None
report_path=None
RUN_COMPLETED=0
REPORT_GENERATED=0
event_count=9
final_report=true
staged_report=false
```

After restoring normal functions, retry failed with `FileExistsError` from the
write-once `os.link` publication. State and journal stayed nonterminal, while
the final report remained. Completion could not succeed without manual cleanup.

### 7. Cleanup failure after publication but before event commit — blocking reproduction

The injected publisher performed the real `os.link(staged, final)` and then
raised `OSError` to model staged unlink failure. Because
`publish_staged_report()` did not return, `_commit_completion()` never set its
local `published` flag. Recovery restored the record and journal and removed the
staged name, but did not remove the already-created final name. The result was
again `CHECKING`, zero terminal events, and `report.v1.json` present. Retry
failed with `FileExistsError`.

This is a separate path from failure to remove the final report during recovery.
It shows that cleanup failure inside publication itself can create the same
contradiction even when later `_remove_quietly` calls work.

## FINDING_STATUS

```text
SOL-RH01=OPEN
SOL-RM02=RESOLVED
SOL-RM03=RESOLVED
F-002=OPEN

SOL-M02=PRESERVED
SOL-M03=PRESERVED
F-003=PRESERVED
F-004=PRESERVED

SOL-RL04=DEFERRED_LOW
BASELINE-L01=DEFERRED_LOW
AUDIT-L02=DEFERRED_LOW
F-005=DEFERRED
```

### SOL-RH01 — OPEN, HIGH

Affected locations:

```text
src/tomtit/reporting.py:207-208
src/tomtit/runtime/runtime.py:330-342
```

The normal prepare/commit/recovery design is materially improved and succeeds
when cleanup succeeds. However, `_remove_quietly` intentionally swallows
`OSError`, and `published` becomes true only after the publisher returns. The
two reproduced cleanup-failure paths therefore leave a false terminal artifact
and defeat retryability. Deterministic tests did not prove safety because the
committed cleanup helper marks this behavior as an untested documented risk and
the committed failure tests assume cleanup succeeds.

Correctness impact: the canonical Runtime says the run is nonterminal and has no
terminal events, while an immutable `report.v1.json` claims terminal completion.
The same file then blocks bounded retry through write-once collision. This
directly fails the declared Gate 1 product evidence and requires manual
filesystem intervention.

### SOL-RM02 — RESOLVED

The completed candidate, all four timestamps, both event IDs and serialized
events, report model and bytes, and staged report are prepared before the final
`validate_completed_record(candidate)` call. After that gate and before
canonical assignment there is no Runtime clock call, ID-factory call, Pydantic
construction, event serialization, report rendering, staging callback or
user-controlled callback. Independent deletion in the formerly open window was
rejected; deletion after completed commit does not retroactively change the run.

### SOL-RM03 — RESOLVED

`build_terminal_report()` remains the sole public construction boundary and
reconstructs through `validate_completed_record()`. The former public helper is
absent; `_build_terminal_report_from_validated_record` is private, not
re-exported, and its only production use outside its definition is Runtime's
authoritative preparation path. The exact copied-record bypass was rejected.

### F-002 — OPEN

Scenario 1 has one coherent result for success and for supported failures when
cleanup succeeds. The reproduced cleanup failures leave neither valid canonical
completion nor a clean retryable rollback, so the exact Gate 1 evidence is not
established.

### Preserved findings

- SOL-M02: the authoritative `check_id`, exact `argv`, and exact `cwd_relative`
  comparisons remain in the unchanged shared completion predicate, and the full
  regression suite passes.
- SOL-M03 and F-004: `src/tomtit/domain/evaluation.py` is unchanged in the
  reviewed diff; deep freezing, detached input, JSON/schema behavior and journal
  compatibility tests all pass.
- F-003: the Attempt 1 `NO_PROGRESS` structural rejection remains unchanged and
  its regression tests pass; no Attempt 2 execution is required by this gate.

## PRODUCT_EVIDENCE_ASSESSMENT

```text
The Runtime completes or rolls back consistently without leaving
a partial terminal state, and the operation can be retried.

ASSESSMENT=FAILED
```

The assessment is `FAILED`, not merely `NOT_PROVEN`, because two reliable
independent cleanup-failure reproductions leave a contradictory terminal report
and make retry fail until manual cleanup occurs.

## RISKS

Blocking current-gate risk:

- A final report can survive rollback while canonical Runtime state and the
  event journal are restored. Its write-once name prevents retry and contradicts
  the canonical nonterminal record.

Nonblocking or explicitly bounded risks:

- A transient stale staged file is retryable because preparation overwrites the
  private staged name; a persistent filesystem permission failure may continue
  to block I/O generally.
- `os.link` depends on hard-link support. Unsupported filesystems fail closed in
  the tested single-filesystem layout, but portability beyond the current
  environment is unproven.
- Process crash, power loss, fsync durability, concurrent completion, symlink
  confinement, content hashing and real-workspace path confinement remain out
  of the supported bounded in-process model.
- The deferred 83 test-only Mypy diagnostics remain, including SOL-RL04's two
  narrow `attr-defined` diagnostics.

## SCOPE_AND_NON_GOALS

The review covered only the exact source commit and diff range named above,
Gate 1 Scenario 1, bounded single-process failures, rollback, retryability,
event history, terminal-report publication, evidence timing, report trust
boundaries and preservation of the listed findings.

The reviewer did not modify tracked source or tests, governance, configuration,
prior reports or gate evidence. No implementation patch, implementation prompt,
new corrective task, gate-evidence commit, Scenario 2 behavior, Attempt 2
execution, crash-recovery design, database transaction, distributed transaction,
concurrency guarantee or cross-filesystem production design was created.

## UNPROVEN_ITEMS

- Crash/power-loss recovery and durable reconciliation are not proven.
- Concurrent or parallel completion is not proven.
- Behavior on filesystems without hard-link support is not independently run.
- Persistent staged-file permission failures beyond the reproduced transient
  cleanup case are not exhaustively characterized.
- Artifact content identity, symlink confinement and workspace path confinement
  remain explicitly deferred.
- No claim is made that the three governance candidates have externally recorded
  Owner-approved hashes.

The core cleanup-retry defect is reproduced and therefore is not listed as an
unproven item.

## REVIEW_BUDGET_CONSEQUENCE

```text
DEFAULT_REVIEW_BUDGET_EXHAUSTED
GATE_STATUS=NOT_APPROVED
OWNER_DECISION_REQUIRED
NO_AUTOMATIC_CORRECTIVE_CYCLE
```

This report does not authorize another implementation patch or corrective
review cycle.

## NEXT_GATE_RECOMMENDATION

```text
GATE_STATUS=NOT_APPROVED
OWNER_DECISION_REQUIRED
NO_AUTOMATIC_CORRECTIVE_CYCLE
```

Do not freeze Scenario 1 and do not proceed to Minimal Scenario 2 on this
verdict. The Owner must decide whether to accept the reproduced risk, authorize
another bounded corrective cycle, or change/narrow the Gate 1 requirement.
