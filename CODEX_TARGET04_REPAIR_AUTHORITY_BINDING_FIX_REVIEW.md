# Codex Target-04 repair authority-binding fix review

```text
STATUS=APPROVED
VERDICT=APPROVE
WORK_CLASS=DEFECT_REVIEW
CURRENT_GATE=TARGET_04

SOURCE_COMMIT=64046ae5cfa1fff2822bad907bea26e68b5b0871
TARGET_SOURCE_COMMIT=64046ae5cfa1fff2822bad907bea26e68b5b0871
IMPLEMENTATION_REPORT_COMMIT=a29fddb89abda8f3571a674ab1c3cdd05c3cbf60
REVIEWED_DIFF_RANGE=c942cb32d5db605ecb66f10fc79d72ca32c80b5a..64046ae5cfa1fff2822bad907bea26e68b5b0871
REVIEW_REPORT_EXPECTED_PARENT=a29fddb89abda8f3571a674ab1c3cdd05c3cbf60
FILES_REVIEWED=.tomtit/dogfood/run_loop.py,.tomtit/dogfood/run_repair.py,.tomtit/dogfood/run_review.py,.tomtit/dogfood/test_run_loop.py,.tomtit/dogfood/test_run_repair.py,TARGET04_REPAIR_AUTHORITY_BINDING_FIX_REPORT.md
COMMANDS_AND_EXIT_CODES=RECORDED_BELOW_ALL_ZERO
INDEPENDENT_REPRODUCTIONS=PASS
FINDING_STATUS=NO_FINDINGS
SCOPE_AND_NON_GOALS=TARGET04_REPAIR_AUTHORITY_BINDING_AND_EVIDENCE_CONSISTENCY_ONLY
UNPROVEN_ITEMS=NO_REAL_MODEL_NO_TARGET04_RERUN_NO_LIVE_ARCHIVED_LEGACY_RUN
NEXT_GATE_RECOMMENDATION=OWNER_BINDS_RUNTIME_FOR_TARGET04A_RUN004
```

## Verdict

`APPROVE`. The repair authority is now bound to the exact Review1 execution
selected by the parent run loop, and current-format review evidence containing
the protected-metadata projection cannot downgrade to the historical
compatibility path when other required current fields are absent.

The previously reviewed trusted-projection behavior remains present: Review1
persists its complete exit projection, the projection hashes to the recorded
protected-metadata hash, repair supplies that exact projection as its trusted
baseline, timestamp-only instability can recover, and real semantic drift
still blocks before the fake Attempt2 provider is invoked.

No blocking or non-blocking correctness finding was identified within the
current Target-04 gate scope. This review did not invoke a real product model,
modify or rerun Target-04, start Target-04B, create or move a tag, or push.

## Exact commit lineage and scope

The direct-parent chain was independently resolved from Git objects:

```text
3cda474be1caabfde3bfed03b0c6c6fcbc344d34
-> 5c2eaaf7ef248ae17a80741fef2aeab5e07feb40
-> c942cb32d5db605ecb66f10fc79d72ca32c80b5a
-> 64046ae5cfa1fff2822bad907bea26e68b5b0871
-> a29fddb89abda8f3571a674ab1c3cdd05c3cbf60
```

Therefore:

```text
ACTUAL_SOURCE_PARENT=c942cb32d5db605ecb66f10fc79d72ca32c80b5a
ACTUAL_REPORT_PARENT=64046ae5cfa1fff2822bad907bea26e68b5b0871
PREVIOUS_TRUSTED_PROJECTION_FIX_PRESENT=PASS
LINEAGE=PASS
```

The source commit changes only:

```text
.tomtit/dogfood/run_loop.py
.tomtit/dogfood/run_repair.py
.tomtit/dogfood/test_run_loop.py
.tomtit/dogfood/test_run_repair.py
```

The implementation-report commit adds only
`TARGET04_REPAIR_AUTHORITY_BINDING_FIX_REPORT.md`. The current source commit's
parent is the previous trusted-projection report commit, whose direct parent is
the trusted-projection source commit. The prior projection producer in
`run_review.py` and the repair consumer in `run_repair.py` remain in the
reviewed source state.

## Review1 identifier data flow

The exact authority path is:

```text
run_loop.main creates review1_id
-> invoke_review(child_id=review1_id)
-> run_review --review-run-id review1_id
-> Review1 receipt review_run_id

the same run_loop review1_id
-> invoke_repair(expected_review_run_id=review1_id)
-> run_repair --expected-review-run-id review1_id
-> run_preflight
-> bind_review_to_expected_review_run(receipt, expected_review_run_id)
```

The repair value originates from the parent loop's already-selected Review1.
It is not derived from the review directory, file ordering, implementation
identity, or receipt contents. The CLI argument is required; preflight rejects
an empty value; and the receipt must contain the same nonempty string.

```text
EXPECTED_REVIEW1_ID_REQUIRED=PASS
EXPECTED_REVIEW1_ID_ORIGIN_PARENT_LOOP=PASS
EXPECTED_REVIEW1_ID_MATCH_REQUIRED=PASS
```

## Validation order and Attempt2 boundary

The actual preflight order is:

1. Validate generic workspace/input prerequisites.
2. Load implementation evidence and bind it to the expected workspace,
   implementation run, and Attempt1.
3. Load the captured review evidence.
4. Compare receipt `review_run_id` with `expected_review_run_id`.
5. Require the semantic `REQUEST_CHANGES` result and validate findings.
6. Bind the review receipt to implementation, attempt, workspace, baseline,
   original task, allowed paths, and inherited side-effect authority.
7. Classify review evidence as current or historical.
8. For current evidence, validate and hash-bind the exact Review1 projection,
   then compare live repository/product/unexpected state with Review1 state.
9. Complete command, authorization, side-effect, and evidence-directory
   preflight checks.
10. Return preflight state; only then does `main()` construct and invoke the
    Attempt2 adapter request.

The review loader parses captured JSON and performs structural result-schema
validation as part of loading. It does not use receipt authority or make a
repair decision. The first semantic use of verdict/findings and every use of
reviewed receipt state occur only after the expected-ID comparison.

An independent precedence probe replaced the review result with an otherwise
schema-valid `APPROVE` while supplying a foreign expected ID. Preflight raised
`REVIEW_RUN_ID_MISMATCH`, proving the foreign receipt could not influence the
repair decision.

```text
REVIEW_ID_CHECK_PRECEDES_REPAIR_DECISION=PASS
ATTEMPT2_GATED_BY_EXPECTED_REVIEW1=PASS
```

## Review-ID consistency cases

Normal disposable repositories and fake executables established:

| Case | Evidence | Result |
|---|---|---|
| A | expected A, receipt A | normal current-format validation continued |
| B | expected A, receipt B | `REVIEW_RUN_ID_MISMATCH`; fake Attempt2 not invoked |
| C | expected A, receipt ID absent | `REVIEW_RUN_ID_MISSING`; fake Attempt2 not invoked |
| D | separate A and B receipts, each internally consistent for the same implementation/workspace/baseline; repair instructed to use A but given B | `REVIEW_RUN_ID_MISMATCH`; B could not substitute |

```text
MATCHING_REVIEW_ID=PASS
DIFFERENT_REVIEW_ID_BLOCKS=PASS
MISSING_REVIEW_ID_BLOCKS=PASS
DIFFERENT_REVIEW_RESULT_CANNOT_SUBSTITUTE=PASS
```

## Current and historical evidence classification

`prove_reviewed_state()` includes
`reviewed_protected_git_metadata_projection` in the marker tuple. Presence of
that key, even with a missing or malformed value, makes
`partitioned_identity_present` true. Current evidence becomes available only
when every marker field exists and every required value passes its type,
shape, hash, baseline, and allowed-path checks.

If any current marker exists but the complete current schema is unavailable,
preflight raises `PREVIOUS_EVIDENCE_INCOMPLETE` before protected-metadata
measurement or the historical whole-workspace-hash branch. Only receipts with
none of the current markers reach historical compatibility. Valid genuine
legacy evidence continued normally, while legacy state drift remained subject
to its strict whole-workspace-hash comparison, including the linked-worktree
compatibility case.

The exact incomplete-current reproduction retained the nonempty projection,
removed the other required current identity fields, and introduced a
same-length semantic protected-state mutation. It remained current-format,
raised `PREVIOUS_EVIDENCE_INCOMPLETE`, did not select the legacy branch, and
did not invoke the fake Attempt2 executable.

```text
PROJECTION_IS_CURRENT_FORMAT_MARKER=PASS
CURRENT_FORMAT_REQUIRES_COMPLETE_SCHEMA=PASS
CURRENT_FORMAT_DOES_NOT_FALL_BACK_TO_LEGACY=PASS
INCOMPLETE_CURRENT_FORMAT_BLOCKS=PASS
LEGACY_PATH_NOT_SELECTED_FOR_CURRENT_FORMAT=PASS
ATTEMPT2_NOT_REACHED_ON_INCOMPLETE_CURRENT_FORMAT=PASS
TRUE_LEGACY_SUPPORT_PRESERVED=PASS
CURRENT_AND_LEGACY_FORMATS_UNAMBIGUOUS=PASS
```

## Review1 projection consistency and repair state source

`run_review.main()` measures `review_exit_git_metadata` once at Review1 exit,
computes `reviewed_protected_git_metadata_hash` from that mapping, and embeds
the same complete mapping as
`reviewed_protected_git_metadata_projection` in the receipt. The receipt also
contains Review1 ID, implementation run/attempt, workspace, and baseline.

Repair first binds that receipt to the expected Review1 ID and implementation
context. `_load_trusted_review_projection()` then requires a nonempty
string-to-string mapping and independently recomputes its hash. Only that
verified mapping is passed as `trusted_baseline` to
`protected_git_metadata_snapshot()`.

A separate generated-evidence probe recomputed the fixture receipt value:

```text
recorded=b78706cbdeb66b9cd60cb62e47ab24c806dc367cdd892bee5c7936085a384577
recomputed=b78706cbdeb66b9cd60cb62e47ab24c806dc367cdd892bee5c7936085a384577
match=true
exact_projection_observed_as_trusted_baseline=true
```

The hash is fixture-specific; the invariant under review is the exact equality
and exact projection-value handoff. Repair does not call coherent bootstrap, does
not establish a new baseline, does not use the run-start baseline as Review1
state, and does not accept a live reading as its own authority.

```text
REVIEW1_FULL_PROJECTION_PRESENT=PASS
PROJECTION_HASH_MATCH=PASS
REPAIR_USES_EXPECTED_REVIEW1_PROJECTION=PASS
REPAIR_STATE_SOURCE=EXACT_EXPECTED_REVIEW1_EXIT_STATE
RUN_START_BASELINE_NOT_USED_AS_REVIEW_STATE=PASS
REPAIR_DOES_NOT_ESTABLISH_NEW_BASELINE=PASS
```

## Timestamp recovery, semantic drift, and Target-04 lifecycle

The valid lifecycle uses Attempt1 evidence, a Review1 `REQUEST_CHANGES`
receipt with the matching expected ID, a complete current projection, and a
deterministic ctime-only instability during repair's protected metadata
observation. The trusted Review1 content validates the retry, preflight
succeeds, and the fake Attempt2 executable is reached.

The semantic-drift lifecycle mutates a protected regular file in place while
preserving inode, mode, size, and link count. Its SHA-256 differs from Review1,
so preflight raises `WORKSPACE_STATE_MISMATCH` and the fake executable does not
modify `app.txt`.

The four requested lifecycle outcomes are therefore:

```text
BENIGN_TIMESTAMP_RECOVERY=PASS
ATTEMPT2_REACHABLE_WITH_VALID_REVIEW1=PASS
SEMANTIC_STATE_DIFFERENCE_BLOCKS=PASS
ATTEMPT2_NOT_REACHED_ON_STATE_DIFFERENCE=PASS

VALID_TARGET04_REPAIR_LIFECYCLE=PASS
REVIEW_ID_MISMATCH_LIFECYCLE=PASS
INCOMPLETE_CURRENT_FORMAT_LIFECYCLE=PASS
STATE_DIFFERENCE_LIFECYCLE=PASS
```

## Preserved prior contracts

The reviewed source diff does not modify `run_review.py`, including coherent
initial baseline establishment, the three-observation maximum, the requirement
for two consecutive matching observations, L1 coherent bootstrap, L2/L3
strict validation, trusted prior content parsing, descriptor grammar, or the
cross-phase stable projection.

The prior repair projection producer remains in `run_review.py`; its consumer
remains in `run_repair.py`. The current change only adds Review1 ID binding,
extends current-format marker/schema classification to cover the projection,
and centralizes the existing projection mapping shape check. Repair still
passes the hash-bound Review1 projection and never calls baseline
establishment.

Focused regressions passed for coherent bootstrap, canonical descriptor
projection, trusted-content retry and mutation rejection, linked-worktree
identity, exact repair projection use, benign timestamp recovery, and semantic
drift rejection.

```text
COHERENT_BOOTSTRAP_UNCHANGED=PASS
TRUSTED_CONTENT_VALIDATION_UNCHANGED=PASS
REPAIR_TRUSTED_PROJECTION_BEHAVIOR_UNCHANGED=PASS
```

## Commands and exit codes

Every command below exited 0. The common focused-test prefix was:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider
```

It was run against these exact selections:

```text
.tomtit/dogfood/test_run_repair.py::{
  test_repair_accepts_matching_expected_review_run_id,
  test_repair_blocks_when_expected_review_run_id_differs,
  test_repair_blocks_when_receipt_review_run_id_missing,
  test_repair_blocks_foreign_self_consistent_review_with_wrong_id,
  test_target04_repair_lifecycle_wrong_review_id_blocks_attempt2
}

.tomtit/dogfood/test_run_repair.py::{
  test_exact_reviewed_state_is_accepted,
  test_legacy_review_evidence_keeps_strict_whole_workspace_hash,
  test_partial_partitioned_review_identity_fails_closed,
  test_any_lone_current_review_identity_field_fails_closed,
  test_malformed_partitioned_review_hash_fails_closed,
  test_lone_git_metadata_identity_field_cannot_downgrade_to_legacy,
  test_linked_legacy_review_without_modern_identity_retains_fallback,
  test_repair_blocks_modern_marker_with_all_other_modern_fields_removed,
  test_target04_repair_lifecycle_malformed_modern_downgrade_blocks_attempt2
}

.tomtit/dogfood/test_run_repair.py::{
  test_reviewed_protected_git_metadata_hash_survives_timestamp_only_drift,
  test_repair_rejects_git_config_drift_after_review,
  test_repair_recovers_from_benign_intra_measurement_timestamp_race,
  test_repair_rejects_semantic_git_metadata_drift_even_with_matching_stat_fields,
  test_repair_blocks_when_reviewed_projection_missing,
  test_repair_blocks_when_reviewed_projection_malformed,
  test_repair_blocks_when_reviewed_projection_empty,
  test_repair_blocks_when_reviewed_projection_hash_mismatch,
  test_repair_accepts_valid_exact_review1_projection_with_no_drift,
  test_repair_never_calls_coherent_bootstrap_establishment,
  test_target04_repair_lifecycle_benign_churn_reaches_attempt2,
  test_target04_repair_lifecycle_semantic_drift_blocks_attempt2
}

.tomtit/dogfood/test_run_review.py -k 'establish_baseline'

.tomtit/dogfood/test_run_review.py -k 'strip_volatile_timestamp_fields or timestamp_like_text or protected_git_metadata_ctime_only_drift_is_cross_phase_stable or protected_git_metadata_same_size_content_mutation_is_detected'

.tomtit/dogfood/test_run_review.py -k '(protected_git_metadata and not strip_volatile and not timestamp_like and not capability_probe and not reviewer and not deterministic_check) or linked_worktree_root_gitfile'
```

The brace notation above compactly records the exact explicitly named pytest
nodes; pytest was invoked with each node as a separate argument, not via shell
brace expansion. The complete-suite and static commands were:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -o addopts='' -q -p no:cacheprovider .tomtit/dogfood/test_run_review.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -o addopts='' -q -p no:cacheprovider .tomtit/dogfood/test_run_repair.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -o addopts='' -q -p no:cacheprovider .tomtit/dogfood/test_run_loop.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -o addopts='' -q -p no:cacheprovider tests/adapters/test_claude_code_cli.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -o addopts='' -q -p no:cacheprovider tests/
PYTHONPYCACHEPREFIX=/tmp/codex-target04-compileall-cache-20260816 python3 -m compileall -q .tomtit/dogfood/run_loop.py .tomtit/dogfood/run_repair.py .tomtit/dogfood/run_review.py .tomtit/dogfood/test_run_loop.py .tomtit/dogfood/test_run_repair.py
ruff check .tomtit/dogfood/run_loop.py .tomtit/dogfood/run_repair.py .tomtit/dogfood/run_review.py .tomtit/dogfood/test_run_loop.py .tomtit/dogfood/test_run_repair.py
git diff --check
git diff --cached --check
```

The independent projection/hash and precedence probes were inline Python
diagnostics importing the normal test fixture builders into disposable
temporary directories. Both exited 0 and produced the results recorded above.

## Independent validation

Commands used Python 3 with plugin autoload and repository cache writes
disabled. All product-boundary executions used fake executables or direct
test-level calls against disposable Git repositories.

| Validation | Result |
|---|---:|
| Expected Review1 ID focused cases | 5/5 PASS |
| Current/legacy format focused cases | 12/12 PASS |
| Prior repair trusted-projection cases | 12/12 PASS |
| Coherent-bootstrap cases | 9/9 PASS |
| Stable descriptor/cross-phase projection cases | 26/26 PASS |
| Trusted-content/protected-metadata cases | 27/27 PASS |
| Full `test_run_review.py` | 154/154 PASS |
| Full `test_run_repair.py` | 129/129 PASS |
| Full `test_run_loop.py` | 86/86 PASS |
| Full Claude CLI adapter suite | 436/436 PASS |
| Full `tests/` regression | 985/985 PASS |
| compileall on reviewed Runtime/test paths | PASS |
| Ruff on reviewed Runtime/test paths | PASS |
| Git diff checks | PASS |

The focused commands executed 91 test cases; two foundational cross-phase
tests intentionally appeared in both the descriptor and protected-metadata
selections, for 89 distinct focused cases. The four complete affected suites
executed 805 cases.

```text
FOCUSED_TESTS=91/PASS
AFFECTED_TESTS=805/PASS
FULL_REGRESSION=985/PASS
COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS
SOURCE_BYTES_UNCHANGED=YES
```

## Findings, scope, and unproven items

No correctness finding was identified. The review is bounded to the repair
authority-binding and evidence-consistency behavior at the exact source
commit above.

No real product invocation, Target-04A rerun, or Target-04B execution was
performed, as explicitly prohibited. Historical compatibility was validated
with ordinary historical-shape fixtures rather than a live archived product
run. This report does not authorize a gate beyond the Owner's stated next
action.

```text
FINDING_STATUS=NO_FINDINGS
TARGET04_MODIFIED=NO
TARGET04_RERUNS=0
TARGET04B_STARTED=NO
TAG_CREATED=NO
PUSHED=NO
```

## Completion receipt

```text
STATUS=APPROVED
VERDICT=APPROVE

ACTUAL_SOURCE_PARENT=c942cb32d5db605ecb66f10fc79d72ca32c80b5a
ACTUAL_REPORT_PARENT=64046ae5cfa1fff2822bad907bea26e68b5b0871
LINEAGE=PASS
PREVIOUS_TRUSTED_PROJECTION_FIX_PRESENT=PASS

EXPECTED_REVIEW1_ID_REQUIRED=PASS
EXPECTED_REVIEW1_ID_ORIGIN_PARENT_LOOP=PASS
EXPECTED_REVIEW1_ID_MATCH_REQUIRED=PASS
REVIEW_ID_CHECK_PRECEDES_REPAIR_DECISION=PASS
ATTEMPT2_GATED_BY_EXPECTED_REVIEW1=PASS

MATCHING_REVIEW_ID=PASS
DIFFERENT_REVIEW_ID_BLOCKS=PASS
MISSING_REVIEW_ID_BLOCKS=PASS
DIFFERENT_REVIEW_RESULT_CANNOT_SUBSTITUTE=PASS

PROJECTION_IS_CURRENT_FORMAT_MARKER=PASS
CURRENT_FORMAT_REQUIRES_COMPLETE_SCHEMA=PASS
CURRENT_FORMAT_DOES_NOT_FALL_BACK_TO_LEGACY=PASS
INCOMPLETE_CURRENT_FORMAT_BLOCKS=PASS
LEGACY_PATH_NOT_SELECTED_FOR_CURRENT_FORMAT=PASS
ATTEMPT2_NOT_REACHED_ON_INCOMPLETE_CURRENT_FORMAT=PASS
TRUE_LEGACY_SUPPORT_PRESERVED=PASS
CURRENT_AND_LEGACY_FORMATS_UNAMBIGUOUS=PASS

REVIEW1_FULL_PROJECTION_PRESENT=PASS
PROJECTION_HASH_MATCH=PASS
REPAIR_USES_EXPECTED_REVIEW1_PROJECTION=PASS
BENIGN_TIMESTAMP_RECOVERY=PASS
ATTEMPT2_REACHABLE_WITH_VALID_REVIEW1=PASS
SEMANTIC_STATE_DIFFERENCE_BLOCKS=PASS
ATTEMPT2_NOT_REACHED_ON_STATE_DIFFERENCE=PASS

REPAIR_STATE_SOURCE=EXACT_EXPECTED_REVIEW1_EXIT_STATE
RUN_START_BASELINE_NOT_USED_AS_REVIEW_STATE=PASS
REPAIR_DOES_NOT_ESTABLISH_NEW_BASELINE=PASS

VALID_TARGET04_REPAIR_LIFECYCLE=PASS
REVIEW_ID_MISMATCH_LIFECYCLE=PASS
INCOMPLETE_CURRENT_FORMAT_LIFECYCLE=PASS
STATE_DIFFERENCE_LIFECYCLE=PASS

COHERENT_BOOTSTRAP_UNCHANGED=PASS
TRUSTED_CONTENT_VALIDATION_UNCHANGED=PASS
REPAIR_TRUSTED_PROJECTION_BEHAVIOR_UNCHANGED=PASS

FOCUSED_TESTS=91/PASS
AFFECTED_TESTS=805/PASS
FULL_REGRESSION=985/PASS
COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS
SOURCE_BYTES_UNCHANGED=YES

TARGET04_MODIFIED=NO
TARGET04_RERUNS=0
TARGET04B_STARTED=NO
PUSHED=NO

NEXT_GATE_RECOMMENDATION=OWNER_BINDS_RUNTIME_FOR_TARGET04A_RUN004
NEXT_ACTION=OWNER_BINDS_RUNTIME_FOR_TARGET04A_RUN004
```
