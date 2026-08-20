# DOGFOOD_EVALUATION_TELEMETRY_V0 — Final Summary-Semantics Fix Review

Date: 2026-08-20
Work class: `PRODUCT_CORRECTION_REVIEW`
Gate: `DOGFOOD_EVALUATION_TELEMETRY_V0`
Verdict: **APPROVE_WITH_NON_BLOCKING_FINDINGS**

## Executive conclusion

The remaining summary-semantics blocker is closed. Unknown or invalid Runtime count observations are excluded from numerators and known-only denominators, rather than converted to zero or treated as negative observations. Invalid elapsed observations are excluded, while valid numeric zero remains a known observation. The previously approved five blocker areas are unchanged, Run006 extraction remains read-only and compatible, and the complete dogfood tree passes.

This review authorizes freezing `DOGFOOD_EVALUATION_V0` and starting Dogfood Batch 1.

One non-blocking validation observation is preserved honestly: the first full `test_run_review.py` execution had one failure in an unchanged real-Git fixture whose setup expected `git status` to rewrite an index descriptor, but Git left the index byte-identical. The exact test immediately passed alone, and a complete clean rerun passed all 263 tests. The summary correction changes neither Runtime nor that test, so this is an environmental Git-refresh flake, not a correction regression.

No Runtime source or tests were modified, Target-04 was not rerun, no real product model was invoked, and no tag or push occurred.

## 1. Lineage and scope

Verified lineage:

```text
976eb00ab0fcd89521e687bc6215ee42e0a2ff2c
  -> 21068d947d38f7421ccbd60ac7e89248d8d4a3df
  -> d3fc390ef12d027a1e234932c07cc4608f3fd47b
```

- `ACTUAL_SOURCE_PARENT=976eb00ab0fcd89521e687bc6215ee42e0a2ff2c`
- `ACTUAL_REPORT_PARENT=21068d947d38f7421ccbd60ac7e89248d8d4a3df`
- Source correction changes only:
  - `.tomtit/dogfood/evaluate_dogfood_run.py`
  - `.tomtit/dogfood/test_evaluate_dogfood_run.py`
  - `docs/DOGFOOD_EVALUATION_TELEMETRY_V0.md`
- Implementation-report commit adds only:
  - `DOGFOOD_EVALUATION_TELEMETRY_V0_SUMMARY_SEMANTICS_FIX_REPORT.md`
- `run_loop.py`, `run_repair.py`, `run_review.py`, and `run_task.py` are byte-unchanged across the correction.

Result: `LINEAGE=PASS`, `SOURCE_BYTES_UNCHANGED=YES`.

## 2. Locked areas and invariant audit

The latest source diff is confined to summary helpers, summary regression tests, and their documentation. It does not change task identity, Runtime-commit provenance, bounded child path resolution, persistence, or cost/token tri-state extraction. H1 measurability, evaluation non-authority, Runtime independence, Owner-vs-Runtime acceptance separation, and read-only evidence extraction remain intact.

```text
PREVIOUS_FIVE_BLOCKER_AREAS_UNCHANGED=PASS
H1_MEASURABLE_WITH_V0=PASS
EVALUATION_CANONICAL_AUTHORITY=NO
RUNTIME_BEHAVIOR_CHANGED=NO
```

## 3. Known count validation and arithmetic

The new `_valid_known_count()` admits only `int` values that are not `bool` and are greater than or equal to zero. Direct probes confirmed that `None`, `True`, `False`, `-1`, `1.5`, `NaN`, infinity, and `"2"` are excluded; `0` and `2` are included.

Direct aggregate results:

```text
implementation_attempt_count=[2,None,4]
implementation_attempt_count_known_count=2
average_implementation_attempts=3.0

implementation_attempt_count=[None,None]
implementation_attempt_count_known_count=0
average_implementation_attempts=null

review_count=[1,None,3]
review_count_known_count=2
average_review_count=2.0

review_count=[None,None]
review_count_known_count=0
average_review_count=null

repair_count=[0,1,None]
repair_count_known_count=2
first_pass_success_rate=0.5
repair_rate=0.5

repair_count=[None,None]
repair_count_known_count=0
first_pass_success_rate=null
repair_rate=null
```

A matrix containing only invalid repair counts (`True`, `False`, `-1`, `1.5`, `NaN`, infinity, and `"2"`) produced `repair_count_known_count=0` and null rates. Unknown repair observations are therefore neither first-pass, repaired, nor negative denominator observations.

Narrow audit of `summarize_evaluations()` found no remaining instance of this defect class. The remaining `or 0` arithmetic is limited to persisted Owner-supplied fields that are CLI-required and validated as non-negative; all-record denominators remain only for deterministically computed non-null booleans. No metric or architecture change is requested.

```text
COUNT_VALIDATION=PASS
UNKNOWN_VALUES_NOT_ZERO_FILLED=PASS
FIRST_PASS_KNOWN_DENOMINATOR=PASS
REPAIR_RATE_KNOWN_DENOMINATOR=PASS
AVERAGE_ATTEMPTS_KNOWN_ONLY=PASS
ATTEMPT_KNOWN_DENOMINATOR=PASS
AVERAGE_REVIEWS_KNOWN_ONLY=PASS
REVIEW_KNOWN_DENOMINATOR=PASS
```

## 4. Elapsed validation and arithmetic

The new `_valid_known_elapsed_seconds()` admits only `int` or `float` values that are not booleans, are finite, and are non-negative.

Direct validation excluded every required invalid observation:

```text
True, False, NaN, +inf, -inf, -1, -0.5, "5"
```

It retained every required valid observation:

```text
0, 0.0, 10, 1.5
```

Direct aggregate results:

```text
elapsed_seconds=[10,None,NaN,20]
elapsed_seconds_known_count=2
mean_elapsed_seconds=15.0
total_elapsed_seconds=30

elapsed_seconds=[all invalid/unknown values]
elapsed_seconds_known_count=0
mean_elapsed_seconds=null
total_elapsed_seconds=null

elapsed_seconds=[0]
elapsed_seconds_known_count=1
mean_elapsed_seconds=0.0
total_elapsed_seconds=0
```

```text
ELAPSED_VALIDATION_NON_BOOLEAN=PASS
ELAPSED_VALIDATION_FINITE=PASS
ELAPSED_VALIDATION_NON_NEGATIVE=PASS
SUMMARY_ELAPSED_TIME_IMPLEMENTED=PASS
ELAPSED_KNOWN_DENOMINATOR=PASS
```

## 5. Other summary semantics

Direct preservation probes confirmed:

- all-unknown scope observations yield `scope_violation_rate=null`;
- known scope denominator `TRUE,FALSE,UNKNOWN` is two and the rate is `0.5`;
- escaped-defect unknown is excluded from the known count, not treated as false;
- only accepted records with `KNOWN` cost contribute to cost arithmetic;
- unknown/unavailable cost is not converted to zero;
- Owner acceptance arithmetic is unchanged.

Result: `OTHER_SUMMARY_SEMANTICS_PRESERVED=PASS`.

## 6. Run006 read-only compatibility

Run006 was available. A full content projection over every evidence file was identical before and after extraction:

```text
fb2b714be12e7869764786b4382b406caef068cec4e6258f5370ba15bd835449
```

Extraction produced:

```text
implementation_attempt_count=2
repair_count=1
review_count=2
model_invocation_count=4
runtime_terminal_outcome=COMPLETED
runtime_stop_reason=REVIEW_APPROVED
evidence_complete=true
```

Result: `RUN006_READ_ONLY_EXTRACTION=PASS`.

## 7. Validation

Required checks were executed serially.

| Check | Final result |
|---|---:|
| Focused evaluator suite | `112/PASS` |
| Full `test_run_review.py` clean rerun | `263/PASS` |
| Full `test_run_repair.py` | `134/PASS` |
| Full `test_run_loop.py` | `93/PASS` |
| Complete `.tomtit/dogfood` tree | `602/PASS` |
| `compileall` | `PASS` |
| Ruff | `PASS` |
| `git diff --check` | `PASS` |

Pytest collection independently confirmed evaluator 112, review 263, repair 134, loop 93, totaling 602.

First failures preserved:

1. The initial temporary direct-probe harness failed before executing product code because its dynamic module import was not registered in `sys.modules`, which Python dataclasses require. The harness alone was corrected; the rerun passed every direct probe. Run006's content projection was identical across both attempts.
2. The first sandboxed focused-suite launch stopped before test collection with OpenMP error 179 (`Can't open SHM`). The identical suite outside that sandbox restriction passed 112/112.
3. The first full review-suite run had one failure in unchanged test `test_canonical_index_identity_real_linked_worktree_common_dir_change_rejected`: the test expected `git status` to alter an index descriptor, but the before/after descriptor was identical. The exact test immediately passed in isolation, and a complete clean rerun passed 263/263. Neither the reviewed source diff nor this report changes Runtime or that test.

The two harness/environment issues and the nondeterministic unchanged Git fixture do not contradict the summary correction's direct proofs or indicate a regression caused by it.

## 8. Verdict matrix

```text
STATUS=APPROVED
VERDICT=APPROVE_WITH_NON_BLOCKING_FINDINGS

SOURCE_COMMIT=21068d947d38f7421ccbd60ac7e89248d8d4a3df
IMPLEMENTATION_REPORT_COMMIT=d3fc390ef12d027a1e234932c07cc4608f3fd47b

ACTUAL_SOURCE_PARENT=976eb00ab0fcd89521e687bc6215ee42e0a2ff2c
ACTUAL_REPORT_PARENT=21068d947d38f7421ccbd60ac7e89248d8d4a3df
LINEAGE=PASS

H1_MEASURABLE_WITH_V0=PASS
EVALUATION_CANONICAL_AUTHORITY=NO
RUNTIME_BEHAVIOR_CHANGED=NO
PREVIOUS_FIVE_BLOCKER_AREAS_UNCHANGED=PASS

COUNT_VALIDATION=PASS
UNKNOWN_VALUES_NOT_ZERO_FILLED=PASS

FIRST_PASS_KNOWN_DENOMINATOR=PASS
REPAIR_RATE_KNOWN_DENOMINATOR=PASS
AVERAGE_ATTEMPTS_KNOWN_ONLY=PASS
ATTEMPT_KNOWN_DENOMINATOR=PASS
AVERAGE_REVIEWS_KNOWN_ONLY=PASS
REVIEW_KNOWN_DENOMINATOR=PASS

ELAPSED_VALIDATION_NON_BOOLEAN=PASS
ELAPSED_VALIDATION_FINITE=PASS
ELAPSED_VALIDATION_NON_NEGATIVE=PASS
SUMMARY_ELAPSED_TIME_IMPLEMENTED=PASS
ELAPSED_KNOWN_DENOMINATOR=PASS

OTHER_SUMMARY_SEMANTICS_PRESERVED=PASS
RUN006_READ_ONLY_EXTRACTION=PASS

EVALUATOR_TESTS=112/PASS
RUN_REVIEW_SUITE=263/PASS
RUN_REPAIR_SUITE=134/PASS
RUN_LOOP_SUITE=93/PASS
DOGFOOD_TREE=602/PASS

COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS
SOURCE_BYTES_UNCHANGED=YES

TARGET04_RERUNS=0
REAL_PRODUCT_MODELS_INVOKED=NO
PUSHED=NO

NEXT_ACTION=FREEZE_DOGFOOD_EVALUATION_V0_AND_START_BATCH1
```

The review-report commit is the single commit that adds this file; its resolved hash and the externally published report's SHA-256 and size are returned by the review process after commit/publication.
