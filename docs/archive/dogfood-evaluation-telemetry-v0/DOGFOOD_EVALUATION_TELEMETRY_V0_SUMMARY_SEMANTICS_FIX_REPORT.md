# Dogfood Evaluation Telemetry V0 -- Summary Semantics Fix Report

WORK_CLASS=PRODUCT_CORRECTION
CURRENT_GATE=DOGFOOD_EVALUATION_TELEMETRY_V0

## Baseline

Independent Codex re-review verdict: `REQUEST_CHANGES`.

- Reviewed source: `f2a0d6b922480fb3fe8cf4f2b4101129d79fa6ec`
- Reviewed implementation report: `976eb00ab0fcd89521e687bc6215ee42e0a2ff2c`

Already `PASS` and locked (not reopened in this task):
`TASK_IDENTITY_SEMANTICS`, `TASK_PROMPT_HASH_CORRECTLY_NAMED`,
`TASK_CONTRACT_HASH_TRUSTWORTHY`, `RUNTIME_COMMIT_PROVENANCE`,
`HISTORICAL_RUNTIME_COMMIT_CAN_BE_MISATTRIBUTED=NO`, `CHILD_PATH_HANDLING`,
`CHILD_PATH_ESCAPE_REJECTED`, `WRITE_FAILURE_CONSISTENCY`,
`PER_RUN_RECORD_SOURCE_OF_TRUTH=YES`, `JSONL_REQUIRED=NO`,
`NO_SILENT_OVERWRITE`, `TOKEN_TRI_STATE`, `COST_TRI_STATE`,
`NONFINITE_VALUES_REJECTED`, `NEGATIVE_VALUES_REJECTED`.

The only remaining blocker: `UNKNOWN_VALUES_NOT_ZERO_FILLED=FAIL`,
`SUMMARY_ELAPSED_TIME_IMPLEMENTED=FAIL`, confined entirely to
`summarize_evaluations()`.

## What changed

`.tomtit/dogfood/evaluate_dogfood_run.py`, `.tomtit/dogfood/test_evaluate_dogfood_run.py`,
`docs/DOGFOOD_EVALUATION_TELEMETRY_V0.md`. No other file was modified. No
Runtime source or test was touched. None of the five already-`PASS`
blocker areas (identity, Runtime commit provenance, child path bounding,
write/persistence consistency, token/cost tri-state) were reopened --
`_valid_known_cost`, `_valid_known_token_count`, `_tri_state_field`,
`resolve_child_evidence_directory`, `validate_task_contract_sha256`,
`validate_runtime_commit`, and `write_evaluation_record` are byte-unchanged.

### Fix: count-like aggregates never zero-filled

Added `_valid_known_count(value)` (int, not bool, `>= 0`) -- a new,
independent helper for `summarize_evaluations()`'s own count-observation
validity, deliberately kept separate from the locked per-field token
validator (`_valid_known_token_count`) even though the underlying rule is
the same, so the already-`PASS` token/cost tri-state code path is never
touched.

- `first_pass_success_rate` / `repair_rate` now divide only by
  `repair_count_known_count` (records where `repair_count` is a valid
  known int `>= 0`), `null` when that denominator is `0`. Previously,
  `repaired = [... (r.get("repair_count") or 0) > 0]` silently treated an
  unknown `repair_count` as "not repaired" while still counting it in the
  `total` denominator -- an unknown repair_count is now excluded from
  both numerators AND the denominator.
- `average_implementation_attempts` / `average_review_count` now divide
  the sum of only the known observations by their own known count
  (`implementation_attempt_count_known_count` /
  `review_count_known_count`), `null` when no known observation exists.
  Previously `sum(r.get(...) or 0 for r in records) / total` turned an
  unknown count into `0` and divided by every record regardless.

```
UNKNOWN_VALUES_NOT_ZERO_FILLED=PASS
FIRST_PASS_KNOWN_DENOMINATOR=PASS
REPAIR_RATE_KNOWN_DENOMINATOR=PASS
AVERAGE_ATTEMPTS_KNOWN_ONLY=PASS
AVERAGE_REVIEWS_KNOWN_ONLY=PASS
```

### Fix: elapsed-value validity and aggregation

Added `_valid_known_elapsed_seconds(value)` (int or float, not bool,
finite via `math.isfinite`, `>= 0`) -- again a new, independent helper,
deliberately not reusing the locked cost validator. Replaced the previous
`isinstance(v, (int, float))` filter (which admitted `bool`, `NaN`,
`+inf`/`-inf`, and negative values, since `bool` is an `int` subclass and
finiteness/sign were never checked) with this validator.
`elapsed_seconds_known_count` exposes the denominator; `mean_elapsed_seconds`
and `total_elapsed_seconds` are both `null` when it is `0`; `elapsed_seconds
== 0` remains a real known observation.

```
ELAPSED_VALIDATION_NON_BOOLEAN=PASS
ELAPSED_VALIDATION_FINITE=PASS
ELAPSED_VALIDATION_NON_NEGATIVE=PASS
SUMMARY_ELAPSED_TIME_IMPLEMENTED=PASS
```

### Audit of the rest of `summarize_evaluations()` (Section 7)

Checked every remaining aggregate for the same zero-fill/all-record-
denominator class of defect:

- `total_owner_interruptions` / `total_clarification_count` /
  `total_manual_edit_lines` (`sum(r.get(...) or 0 ...)`): left unchanged.
  These are OWNER_SUPPLIED, CLI-required fields validated `>= 0` at
  input time -- they can never legitimately be `null`/unknown in a
  persisted record, so `or 0` here is not the described defect class.
- `owner_acceptance_rate`, `accepted_without_manual_edit_rate`,
  `evidence_completeness_rate` (divide by `total`): left unchanged. Their
  numerator fields (`accepted`, `accepted_without_manual_edit`,
  `evidence_complete`) are always deterministically-computed plain
  booleans, never tri-state/nullable, so an all-records denominator is
  correct here, not a defect.
- `scope_violation_rate`, `escaped_defect_*`,
  `average_cost_usd_per_accepted_task_when_known`: already fixed in the
  prior blocker-fix round; verified unchanged and still correct (Section
  12 preservation).

No new metrics were introduced; no existing metric was removed.

## Spec doc updated

`docs/DOGFOOD_EVALUATION_TELEMETRY_V0.md`'s "Metrics this schema enables"
section: documents the count-like known-value denominators
(`repair_count_known_count`, `implementation_attempt_count_known_count`,
`review_count_known_count`) and the tightened elapsed-value validity rule
(`elapsed_seconds_known_count`, non-boolean/finite/non-negative). No
identity, provenance, or persistence section was changed.

## Preserved behavior (Section 12)

Verified unchanged by the full test suite: `scope_violation` UNKNOWN
exclusion and all-UNKNOWN-null rate; `escaped_defect_reported` UNKNOWN not
counted as `FALSE`; cost UNKNOWN/UNAVAILABLE never treated as `$0`, null
when no known accepted cost; `median_active_human_minutes_to_confident_accept`;
Owner acceptance semantics (`accepted`, `accepted_without_manual_edit`).

## Run006 compatibility (Section 14)

Re-ran the existing real-Run006 read-only tests (extraction is untouched
by this change -- `summarize_evaluations()` operates on already-persisted
records, not on Runtime evidence directly). All pass; evidence directory
listing byte-identical before/after; `write_evaluation_record` never
called against real Run006 evidence.

```
implementation_attempt_count=2
repair_count=1
review_count=2
model_invocation_count=4
runtime_terminal_outcome=COMPLETED
runtime_stop_reason=REVIEW_APPROVED
evidence_complete=true
```

## Regression tests (Sections 10-11)

Added to `test_evaluate_dogfood_run.py`, all directly against
`summarize_evaluations()` with hand-built minimal records:

- Count matrix (A-H): unknown-only attempts/reviews -> null; mixed
  known+unknown attempts `[2, None, 4]` -> `3.0`; mixed reviews
  `[1, None, 3]` -> `2.0`; unknown-only repair -> both rates null; mixed
  repair `[0, 1, None]` -> known denominator `2`, both rates `0.5`;
  negative repair_count (`-1`) -> unknown; boolean repair_count (`True`)
  -> unknown.
- Elapsed matrix: each of `True`/`False`/`NaN`/`+inf`/`-inf`/`-1`/`-0.5`/
  `"5"` individually excluded (parametrized); all-invalid collection ->
  both null; mixed `[10, None, NaN, 20]` -> mean `15`, total `30`; valid
  `elapsed_seconds=0` remains a known observation (count `1`, mean/total
  `0`).

Suite grew from 93 to 112 tests; all previously-passing tests unchanged
in assertion intent (the pre-existing basic-arithmetic test's known
denominators happened to equal the old all-record denominators in its
fixture, so its assertions did not need to change).

## Validation (Section 15)

Full log: `DOGFOOD_EVALUATION_TELEMETRY_V0_SUMMARY_SEMANTICS_FIX_FULL.log`
(this task's log; gitignored, not committed). Run serially, no `tee`,
first failures preserved honestly.

| Check | Result |
|---|---|
| Focused evaluator test suite (includes all new regressions) | PASS, 112 |
| Full `test_run_review.py` | PASS, 263 |
| Full `test_run_repair.py` | PASS, 134 |
| Full `test_run_loop.py` | PASS, 93 |
| Full `.tomtit/dogfood/` tree | PASS, 602 (112 + 93 + 134 + 263) |
| `compileall` | PASS |
| `ruff check` | PASS |
| `git diff --check` | PASS |

No failures at any point. Target-04 was not rerun; no real product model
was invoked.

## Previous five blocker areas unchanged

Confirmed by diff inspection and by the full passing suite (which
includes every regression test written for those five areas in the prior
round): `PREVIOUS_FIVE_BLOCKER_AREAS_UNCHANGED=PASS`.

`H1_MEASURABLE_WITH_V0=PASS`
`EVALUATION_CANONICAL_AUTHORITY=NO`
`RUNTIME_BEHAVIOR_CHANGED=NO`
