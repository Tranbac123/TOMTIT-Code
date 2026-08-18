# Dogfood Evaluation Telemetry V0 -- Blocker Fix Report

WORK_CLASS=PRODUCT_CORRECTION
CURRENT_GATE=DOGFOOD_EVALUATION_TELEMETRY_V0

## Baseline

Independent Codex review verdict: `REQUEST_CHANGES`.

- Reviewed source: `624dde7403cd1c0a48a6f3ac612544285dfc3dd5`
- Reviewed implementation report: `a89f3f97000a154ce728b2bc0422039b9e9abe90`
- `H1_MEASURABLE_WITH_V0=PASS` (unchanged; this task is a narrow correctness/
  provenance correction, not a redesign)

This task implements ONLY the six blocking findings from that review.
Nothing else in the schema, architecture, or Runtime relationship was
touched.

## What changed

`.tomtit/dogfood/evaluate_dogfood_run.py`, `.tomtit/dogfood/test_evaluate_dogfood_run.py`,
`docs/DOGFOOD_EVALUATION_TELEMETRY_V0.md`. No other file was modified.
`run_loop.py`/`run_repair.py`/`run_review.py`/`run_task.py`: byte-unchanged.

### Blocker 1 -- Task identity semantics

Split the previously-conflated field into two: `task_prompt_sha256`
(RUNTIME_DERIVED, read exactly from `task-contract.json.task_prompt_sha256`,
under its own correct name) and `task_contract_sha256` (EXPLICIT_INPUT
only, via new `--task-contract-sha256 <64-hex>`, strictly validated,
normalized lowercase, never copied from `task_prompt_sha256`, never
inferred from free text). Added `task_contract_sha256_provenance`
(`EXPLICIT_INPUT` | `UNKNOWN`).

```
TASK_IDENTITY_SEMANTICS=PASS
TASK_PROMPT_HASH_CORRECTLY_NAMED=PASS
TASK_CONTRACT_HASH_TRUSTWORTHY=PASS
```

### Blocker 2 -- Runtime commit provenance

Removed `_detect_runtime_commit()` (the `git rev-parse HEAD` auto-detection
of the evaluator's own checkout) entirely -- the smaller implementation was
preferred over adding a second, easily-confused identity field.
`runtime_commit` is now populated ONLY from an explicit, validated
`--runtime-commit <git-sha>` (4-64 hex, normalized lowercase); otherwise
`null`. Added `runtime_commit_provenance` (`EXPLICIT_INPUT` | `UNKNOWN`).

```
RUNTIME_COMMIT_PROVENANCE=PASS
HISTORICAL_RUNTIME_COMMIT_CAN_BE_MISATTRIBUTED=NO
```

### Blocker 3 -- Bounded child evidence paths

Added `resolve_child_evidence_directory()`, called for every
`implementation_run_ids`/`review_run_ids` entry before any path under it
is touched. Rejects: empty, `"."`, `".."`, any id containing `/` or `\`,
absolute ids, and any id whose resolved path (following a real symlink)
escapes the canonical `children/` root -- raising `EvaluationError`
(`INVALID_CHILD_ID` / `CHILD_PATH_ESCAPE`) immediately rather than folding
the failure into `missing_evidence_paths` or inspecting the escaped path.

```
CHILD_PATH_HANDLING=PASS
CHILD_PATH_ESCAPE_REJECTED=PASS
```

### Blocker 4 -- Write consistency

Removed the mandatory JSONL append entirely. `<run_id>.evaluation.json`
is now the ONLY authoritative persisted evaluation record; no aggregate
file is written or required by V0. `--summarize` is unchanged (it already
read `*.evaluation.json` files directly). Write path now uses atomic
create-without-overwrite: a temp file in the same directory, `fsync`ed,
then linked into place with `os.link` (which fails atomically with
`FileExistsError` on collision, unlike `os.rename`, which silently
overwrites on POSIX) -- eliminating both the exists-check TOCTOU race and
the two-artifact half-commit window.

```
WRITE_FAILURE_CONSISTENCY=PASS
PER_RUN_RECORD_SOURCE_OF_TRUTH=YES
JSONL_REQUIRED=NO
```

### Blocker 5 -- Summary never zero-fills UNKNOWN

`scope_violation_rate` now divides by `scope_violation_known_count` (only
`TRUE`/`FALSE` records), `null` when that count is `0` -- `UNKNOWN` records
are excluded from the denominator entirely rather than counted as
non-violations. Replaced `escaped_defect_reported_count` with
`escaped_defect_known_count` / `escaped_defect_true_count` (`UNKNOWN` never
counted as `FALSE`). `average_cost_usd_per_accepted_task_when_known`'s
existing null-when-no-known-costs semantics were verified correct and left
unchanged (name unchanged; matching field already existed).

### Blocker 6 -- Summary elapsed-time aggregation

Added `mean_elapsed_seconds` (required) and `total_elapsed_seconds`,
computed over records with a valid numeric `elapsed_seconds` only (`None`
filtered), `null` when none. `median_active_human_minutes_to_confident_accept`
unchanged.

```
SUMMARY_ELAPSED_TIME_IMPLEMENTED=PASS
```

### Blocker 7 -- Token/cost KNOWN-value validation

Replaced the single `_tri_state_numeric` (which accepted any non-bool
`int`/`float`) with field-specific validators: `_valid_known_cost` (finite
via `math.isfinite`, `>= 0`, not `bool`) and `_valid_known_token_count`
(`int` only -- floats rejected even when integral, `>= 0`, not `bool`).
Neither ever coerces strings. Negative matrix (`NaN`/`+inf`/`-inf`/
negative/`bool`/`string`/float-for-tokens) all verified -> `UNKNOWN`.
Positive matrix (`0`, positive values) verified -> `KNOWN`.

```
TOKEN_TRI_STATE=PASS
COST_TRI_STATE=PASS
NONFINITE_VALUES_REJECTED=PASS
NEGATIVE_VALUES_REJECTED=PASS
```

## Spec doc updated

`docs/DOGFOOD_EVALUATION_TELEMETRY_V0.md`: identity field table and note
(A), Runtime commit note -- no automatic attribution (B), immutability
section rewritten as sole-source-of-truth, JSONL removed from the
contract (C), metrics section documents known-value denominators/null
semantics and elapsed aggregation (D), tri-state cost/token section
documents field-specific KNOWN validation (E), CLI section documents the
two new flags. Nothing else in the schema was expanded or changed.

## H1 fields preserved (Section 9)

Unchanged: `owner_disposition`, `active_human_seconds`,
`owner_interruptions`, `clarification_count`, `manual_edit_lines`,
`owner_note`, `escaped_defect_reported` (values/semantics), `accepted`,
`accepted_without_manual_edit`, `active_human_minutes_to_confident_accept`,
all Runtime-derived outcome/count fields and their meanings.

## Run006 compatibility (Section 10)

Re-ran the evaluator extraction test against historical Run006 evidence,
read-only (`test_real_run006_evaluation_record_identity_without_explicit_overrides`,
`test_real_run006_evaluation_record_identity_with_explicit_overrides`, plus
the pre-existing `test_real_run006_evidence_extraction_read_only`). Every
test asserts the evidence root's parent directory listing is byte-identical
before and after; `write_evaluation_record` is never called against real
Run006 evidence in any test.

Trajectory (unchanged): `implementation_attempt_count=2`, `repair_count=1`,
`review_count=2`, `model_invocation_count=4`,
`runtime_terminal_outcome=COMPLETED`, `runtime_stop_reason=REVIEW_APPROVED`,
`evidence_complete=true`.

Without explicit overrides: `task_prompt_sha256` = the actual Runtime
prompt hash, `task_contract_sha256=null`, `runtime_commit=null`,
`runtime_commit_provenance=UNKNOWN`.

With explicit `--task-contract-sha256 9948219bfdd1de053bcdc03239a97029d465a671f9270a58510896c0bbb29e13`
and `--runtime-commit 60a44355c77c4d651cc4a45f7a83c65122fea6d2`: both
stored exactly, both labeled `EXPLICIT_INPUT`.

Run006 evidence was not modified.

## Regression tests (Section 11 -- 33 required cases)

All 33 numbered cases are covered in `test_evaluate_dogfood_run.py`
(TASK IDENTITY 1-5, RUNTIME COMMIT 6-8, CHILD PATH 9-14, WRITE CONSISTENCY
15-18, SUMMARY 19-24, TOKEN/COST 25-33), several via `pytest.mark.parametrize`
covering multiple matrix entries per test function. Total suite grew from
43 to 93 tests (all previously-passing tests were updated in place for the
new `build_evaluation_record`/`write_evaluation_record` signatures; none
were weakened or removed except the two JSONL-specific tests that no
longer apply now that JSONL is not written).

## Validation (Section 12)

Full log: `DOGFOOD_EVALUATION_TELEMETRY_V0_BLOCKER_FIX_FULL.log` (this
task's log; gitignored per repository convention, not committed). Run
serially, no `tee`, first failures preserved honestly.

| Check | Result |
|---|---|
| Focused evaluator test suite (includes all 33 new regressions) | PASS, 93 |
| Full `test_run_review.py` (proves no Runtime change) | PASS, 263 |
| Full `test_run_repair.py` | PASS, 134 |
| Full `test_run_loop.py` | PASS, 93 |
| Full `.tomtit/dogfood/` tree | PASS, 583 (93 + 93 + 134 + 263) |
| `compileall` | PASS |
| `ruff check` | PASS |
| `git diff --check` | PASS |

No failures at any point in this run. Target-04 was not rerun; no real
product/Claude/Codex model was invoked at any point in this task.

## Out of scope / unchanged

- No evaluation architecture redesign, no SQLite, no dashboard, no
  LLM-as-judge, no model routing, no automatic human-attention tracking,
  no Graph/Auto-Complete/RL infrastructure.
- `run_loop.py`, `run_repair.py`, `run_review.py`, `run_task.py`:
  byte-unchanged (confirmed by `git diff --stat`, and by
  `test_no_runtime_module_imports_the_evaluator`).
- No Target-04 run was started or rerun.
- No real product/Claude/Codex model was invoked.
- No push, no tag.

`EVALUATION_CANONICAL_AUTHORITY=NO`
`RUNTIME_BEHAVIOR_CHANGED=NO`
