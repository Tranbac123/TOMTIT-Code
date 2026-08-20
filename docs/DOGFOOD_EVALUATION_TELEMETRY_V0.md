# Dogfood Evaluation Telemetry V0

Locked contract for `.tomtit/dogfood/evaluate_dogfood_run.py`
(`DOGFOOD_EVALUATION_V0`). This is the minimum evaluation telemetry required
before Dogfood Batch 1 -- not a general analytics platform, not a dashboard,
not a research-completeness framework.

## Purpose / H1

This telemetry exists specifically to evaluate one hypothesis during the
first 3-5 real dogfood tasks:

```text
H1 = TOMTIT reduces active human supervision required to confidently accept
     a software change.
```

Nothing in this contract is designed for future research completeness.
Anything not needed to evaluate H1 for Batch 1 is explicitly deferred
(see below).

## Core invariant: evaluation is never execution authority

```text
Runtime evidence (canonical, read-only)
      ↓
Evaluation extractor  +  Owner-supplied fields
      ↓
DogfoodEvaluationV0 record
```

`.tomtit/dogfood/evaluate_dogfood_run.py`:

- reads `run_loop.py`'s own canonical evidence (`loop-result.json`,
  `loop-receipt.json`, `events.jsonl`, and referenced child receipts) --
  never writes to that tree, never mutates it in memory before persisting
  anything;
- never modifies run state, terminal outcome, `TaskContract`, or any
  evidence Runtime authority itself relies on;
- never authorizes retry or repair;
- writes its own artifacts to a location strictly outside the canonical
  evidence tree (`<evidence-root's-parent>/evaluations/`).

`EVALUATION_CANONICAL_AUTHORITY=NO`. No import or call path exists from
`run_loop.py`/`run_repair.py`/`run_review.py`/`run_task.py` to this module
(enforced by `test_no_runtime_module_imports_the_evaluator`).

## Relationship to existing evaluation architecture

This repository already has a larger, more general evaluation surface
(`docs/EVALUATION_CONTRACT_V1.md`, `src/tomtit/domain/evaluation.py`,
`src/tomtit_decision_eval_v0/`) built around a different, more abstract
Runtime state-machine vocabulary (`RunState`/`StopReason`/`DecisionEvent`
etc.) than what the actual `.tomtit/dogfood/run_loop.py` harness emits.
`DOGFOOD_EVALUATION_V0` is a deliberately small, standalone module scoped
specifically to `run_loop.py`'s own evidence shape -- it does not import
from, extend, or duplicate that larger architecture, and does not attempt
to unify the two. No SQLite store was added: plain JSON/JSONL files are the
existing, already-established convention for every `.tomtit/dogfood/*.py`
tool's evidence, and nothing here requires anything more expansive.

## Schema: `DOGFOOD_EVALUATION_V0`

One flat JSON object per run, versioned by the literal `schema_version`
string `"DOGFOOD_EVALUATION_V0"`.

### Identity fields

| Field | Type | Provenance | Source |
|---|---|---|---|
| `schema_version` | string | tool-generated | constant |
| `evaluation_id` | string (uuid4) | tool-generated | generated fresh per invocation |
| `evaluated_at` | string (ISO-8601 UTC) | tool-generated | wall-clock time of evaluation |
| `run_id` | string | RUNTIME_DERIVED | `loop-result.json.loop_run_id` |
| `task_prompt_sha256` | string \| null | RUNTIME_DERIVED | `task-contract.json.task_prompt_sha256` -- the exact canonical Runtime field, under its own name |
| `task_contract_sha256` | string \| null | EXPLICIT_INPUT only | `--task-contract-sha256 <64-hex>`, or `null` |
| `task_contract_sha256_provenance` | `"EXPLICIT_INPUT"` \| `"UNKNOWN"` | tool-generated | `"EXPLICIT_INPUT"` iff `task_contract_sha256` was supplied and validated; `"UNKNOWN"` otherwise |
| `workspace_baseline` | string \| null | RUNTIME_DERIVED | `task-contract.json.baseline_commit` |
| `runtime_commit` | string \| null | EXPLICIT_INPUT only | `--runtime-commit <git-sha>`, or `null` |
| `runtime_commit_provenance` | `"EXPLICIT_INPUT"` \| `"UNKNOWN"` | tool-generated | `"EXPLICIT_INPUT"` iff `runtime_commit` was supplied and validated; `"UNKNOWN"` otherwise |

**`task_prompt_sha256` vs `task_contract_sha256` (identity correction):**
these are two distinct artifacts and must never be conflated.
`task_prompt_sha256` is `run_loop.py`'s own canonical
`task-contract.json.task_prompt_sha256` field -- the SHA-256 of the exact
task prompt text this run was bound to -- read RUNTIME_DERIVED, exactly as
Runtime recorded it. `task_contract_sha256` is a SEPARATE identity concept:
the hash of an external `TaskContract` document (e.g.
`TARGET_04A_TASK_CONTRACT.md`) that the prompt itself may reference by
hash. Runtime evidence does not record this hash at all, so it is
populated ONLY from an explicit, validated `--task-contract-sha256`
input -- never copied from `task_prompt_sha256`, never inferred from free
text, never scraped as a 64-hex substring anywhere in evidence. It
defaults to `null` (`task_contract_sha256_provenance = "UNKNOWN"`) when not
supplied. `--task-contract-sha256` is validated strictly: exactly 64
hexadecimal characters, case-insensitive on input, stored lowercase;
anything else is a `CONFIG_FAILURE` and no record is written.

**`runtime_commit` note (no automatic historical attribution):**
`runtime_commit` is populated ONLY from an explicit, validated
`--runtime-commit <git-sha>` input -- it is never auto-populated from the
evaluator's own current checkout (`git rev-parse HEAD`). Historical
evidence evaluated without `--runtime-commit` gets `runtime_commit = null`,
`runtime_commit_provenance = "UNKNOWN"`; evaluating evidence long after the
fact must never silently attribute it to whatever commit happens to be
checked out at evaluation time. `--runtime-commit` is validated as 4-64
hexadecimal characters (covering both abbreviated and full git object ids)
and stored lowercase; anything else is a `CONFIG_FAILURE`. No separate
"evaluator's own checkout" field is recorded in V0 -- the smaller
implementation (omission) was preferred over adding a second, easily
confused identity field.

### Owner-supplied fields (Section 4)

| Field | Type | Required |
|---|---|---|
| `owner_disposition` | `"ACCEPTED"` \| `"ACCEPTED_WITH_MANUAL_EDIT"` \| `"NOT_USABLE"` | yes |
| `active_human_seconds` | integer >= 0 | yes |
| `owner_interruptions` | integer >= 0 | yes |
| `clarification_count` | integer >= 0 | yes |
| `manual_edit_lines` | integer >= 0 | yes |
| `owner_note` | string \| null | no |
| `escaped_defect_reported` | `"TRUE"` \| `"FALSE"` \| `"UNKNOWN"` | no (default `"UNKNOWN"`) |

Runtime `terminal_outcome`/`accepted` are never used to infer Owner
acceptance. Runtime completion is not Owner acceptance.

### Derived fields (`DERIVED_FROM_OWNER_AND_RUNTIME`)

```text
accepted =
  owner_disposition in {ACCEPTED, ACCEPTED_WITH_MANUAL_EDIT}

accepted_without_manual_edit =
  owner_disposition == ACCEPTED AND manual_edit_lines == 0

active_human_minutes_to_confident_accept =
  active_human_seconds / 60   if accepted else null
```

`NOT_USABLE` is never reinterpreted as infinite time: the derived minutes
value is `null`, not a sentinel, not `active_human_seconds / 60` anyway.

### Runtime-derived fields (Section 5, `RUNTIME_DERIVED`)

| Field | Source |
|---|---|
| `runtime_terminal_outcome` | `loop-result.json.terminal_outcome` |
| `runtime_stop_reason` | `loop-result.json.stop_reason` |
| `runtime_accepted` | `loop-result.json.accepted` (Runtime's OWN bool -- distinct from `accepted` above; never conflated) |
| `implementation_attempt_count` | `loop-result.json.implementation_attempts` |
| `repair_count` | `loop-result.json.repair_rounds` |
| `review_count` | `loop-result.json.review_attempts` |
| `model_invocation_count` | `loop-result.json.model_invocation_count` |
| `elapsed_seconds` | `loop-result.json.duration_ms / 1000` |

These are read once, directly, from the canonical loop result -- never
recreated from guesses, never recomputed from child evidence when the loop
result already states them.

### Tri-state cost / token fields (Section 7)

```json
"total_cost_usd": {"status": "KNOWN" | "UNKNOWN" | "UNAVAILABLE", "value": number | null}
"total_input_tokens": {"status": ..., "value": ...}
"total_output_tokens": {"status": ..., "value": ...}
```

Source: `loop-result.json.usage.{total_model_cost_usd,total_input_tokens,total_output_tokens}`.

- `UNAVAILABLE`: the `usage` block, or this specific field, is absent from
  this evidence shape at all -- the concept is not exposed here.
- `UNKNOWN`: the field is present but fails field-specific KNOWN
  validation (see below) -- the concept is measurable but this run's
  evidence did not provide a trustworthy value.
- `KNOWN`: a value that passes field-specific validation is present.

Never inferred. Never zero-filled in place of `UNKNOWN`/`UNAVAILABLE`.

**KNOWN validation (field-specific, never a bare `isinstance(int, float)`):**

- **Cost** (`total_cost_usd`): KNOWN requires a value that is `int` or
  `float`, is NOT `bool`, is finite (`math.isfinite`), and is `>= 0`.
  `NaN`, `+inf`, `-inf`, negative values, `bool`, and strings all reject to
  `UNKNOWN` -- never coerced.
- **Tokens** (`total_input_tokens`, `total_output_tokens`): KNOWN requires
  a value that is `int`, is NOT `bool`, and is `>= 0`. A `float` is
  rejected even when integral (e.g. `1.0`) -- token counts are discrete.
  Negative values, `NaN`/`inf`, `bool`, and strings all reject to
  `UNKNOWN`.
- Absent field or absent `usage` block: `UNAVAILABLE`, never `UNKNOWN`.

### Quality / trust fields (Section 6)

| Field | Type | Semantics |
|---|---|---|
| `scope_violation` | `"TRUE"` \| `"FALSE"` \| `"UNKNOWN"` | `TRUE` iff `loop-result.json.unexpected_changed_paths` is non-empty; `UNKNOWN` if that field is absent OR `evidence_complete` is `false` (never manufactured certainty from incomplete evidence); `FALSE` otherwise. |
| `evidence_complete` | boolean | see below -- always deterministically computable, no tri-state needed. |
| `escaped_defect_reported` | `"TRUE"` \| `"FALSE"` \| `"UNKNOWN"` | Owner-supplied only (see above). Never inferred from a Review2 APPROVE or any other Runtime outcome. |

### Evidence completeness (Section 12)

A bounded, deterministic, **trajectory-aware** check: only the canonical
evidence the run's own recorded trajectory actually requires.

```text
always required (at evidence_root):
    loop-result.json, loop-receipt.json, events.jsonl

for implementation_run_ids[0] (Attempt 1):
    children/<id>/{receipt.json, result.json, diff.patch}

for implementation_run_ids[1:] (each bounded repair round):
    children/<id>/{receipt.json, result.json, diff.patch,
                   repair-context.json, checks.json}

for every id in review_run_ids:
    children/<id>/{review-receipt.json, review-result.json, checks.json}
```

A first-pass APPROVE (`implementation_run_ids` has one entry,
`review_run_ids` has one entry) never requires repair/Review2 artifacts. A
run that never left preflight (`implementation_run_ids`/`review_run_ids`
both empty) requires only the three loop-level files. `missing_evidence_paths`
lists exactly which required paths were absent (bounded to what was
actually checked -- never an unbounded tree dump).

**Bounded child evidence paths:** `implementation_run_ids`/`review_run_ids`
are read from `loop-result.json`, which is Runtime evidence but not a
trusted filesystem path fragment -- each child id is resolved through
`resolve_child_evidence_directory()` before anything under it is touched.
An id must be a simple basename: non-empty, not `"."`/`".."`, containing no
`/` or `\`, not absolute, and the resolved path (following any real
symlink) must remain strictly under the evidence root's `children/`
directory. Any invalid or escaping id raises `EvaluationError`
(`INVALID_CHILD_ID` or `CHILD_PATH_ESCAPE`) immediately -- evaluation
extraction fails clearly and the candidate path is never inspected.

## Immutability / no-overwrite rule, and sole source of truth (Section 11)

Exactly one evaluation artifact per run, written to:

```text
<evidence-root's-parent>/evaluations/<run_id>.evaluation.json
```

This per-run JSON file is the ONLY authoritative persisted evaluation
record for V0 -- there is no mandatory JSONL aggregate and no second
required persistence step. A successful, atomic creation of this file
means the evaluation is fully and completely recorded; no further action
can leave it half-committed. `--summarize` reads every
`*.evaluation.json` file in a directory directly -- it requires no
separate aggregate file.

Writing is atomic and never overwrites: a temporary file is written in the
same directory, flushed and `fsync`ed, then linked into place with
`os.link` (which fails atomically with `FileExistsError` if the target
already exists, unlike `os.rename`, which would silently overwrite on
POSIX). If `<run_id>.evaluation.json` already exists, the tool fails
clearly (`EVALUATION_ALREADY_EXISTS`) and writes nothing; the existing
file's bytes are never touched. For Batch 1, evaluation records are
immutable; no replacement mechanism exists.

## Provenance

Every field belongs to exactly one of:

- `RUNTIME_DERIVED` -- read directly from canonical `run_loop.py` evidence.
- `OWNER_SUPPLIED` -- given on the command line, never inferred.
- `DERIVED_FROM_OWNER_AND_RUNTIME` -- a pure function of fields from both
  of the above (`accepted`, `accepted_without_manual_edit`,
  `active_human_minutes_to_confident_accept`).
- `EXPLICIT_INPUT` only -- populated exclusively from a validated CLI
  input, `null` otherwise, never derived or inferred (`task_contract_sha256`,
  `runtime_commit`); each carries a sibling `*_provenance` field whose
  value is `"EXPLICIT_INPUT"` or `"UNKNOWN"`.
- tool-generated (`schema_version`, `evaluation_id`, `evaluated_at`) --
  bookkeeping, not evidence.

No provenance graph is needed beyond this table.

## Metrics this schema enables (Section 14)

Without any additional Runtime instrumentation, a set of
`DOGFOOD_EVALUATION_V0` records is sufficient to later compute: Owner
acceptance rate, accepted-without-manual-edit rate, active human minutes to
confident accept (and its median), owner interruption count, clarification
count, manual edit lines, first-pass success rate, repair rate, average
implementation attempts, average review count, scope violation rate,
evidence completeness rate, mean/total elapsed time, cost per accepted
task when `KNOWN`, and escaped defect counts when reported.

**Known-value denominators, never zero-filled (tri-state metrics and
count-like Runtime observations):** `UNKNOWN`/`UNAVAILABLE`/`null`/invalid
observations are never treated as numeric zero, and never silently
included in a denominator as if they were the negative case.

- `scope_violation_known_count` = number of records with
  `scope_violation` in `{TRUE, FALSE}`. `scope_violation_rate` =
  `TRUE_count / scope_violation_known_count` when that denominator is
  `> 0`, else `null` -- never computed over all records regardless of
  `UNKNOWN`.
- `escaped_defect_known_count` / `escaped_defect_true_count` = counts of
  records with `escaped_defect_reported` known (`TRUE`/`FALSE`) and `TRUE`
  respectively. `UNKNOWN` is never counted as `FALSE`.
- `cost_known_accepted_task_count` /
  `average_cost_usd_per_accepted_task_when_known` = only accepted records
  whose `total_cost_usd.status == "KNOWN"` contribute; `null` when that
  count is `0` -- `UNKNOWN`/`UNAVAILABLE` costs are never treated as `$0`.
- **Count-like aggregates** (`implementation_attempt_count`,
  `repair_count`, `review_count`): a valid known observation is an `int`,
  not `bool`, `>= 0` -- never coerced from a string, never inferred.
  `repair_count_known_count` / `implementation_attempt_count_known_count` /
  `review_count_known_count` expose each denominator explicitly.
  `first_pass_success_rate` = (records with known `repair_count == 0`) /
  `repair_count_known_count`; `repair_rate` = (records with known
  `repair_count > 0`) / `repair_count_known_count`; both `null` when that
  denominator is `0`. `average_implementation_attempts` /
  `average_review_count` divide the sum of only the known observations by
  their own known count, not by `total_evaluations`; both `null` when no
  known observation exists. An unknown/invalid `repair_count` (e.g.
  `null`, negative, or a `bool`) is excluded from the denominator
  entirely -- it is neither "first pass" nor "repaired."

**Elapsed time aggregation:** a valid known `elapsed_seconds` observation
is an `int` or `float`, not `bool`, finite (`math.isfinite`), and `>= 0` --
`NaN`, `+inf`/`-inf`, negative values, and booleans are all excluded, never
coerced from a string. `elapsed_seconds_known_count` exposes that
denominator. `mean_elapsed_seconds` and `total_elapsed_seconds` are
computed over only the known values; both are `null` when
`elapsed_seconds_known_count` is `0`. A valid `elapsed_seconds == 0` is a
real known observation, not excluded.
`median_active_human_minutes_to_confident_accept` is unchanged.

`summarize_evaluations()` computes exactly this list as plain arithmetic
over an in-memory list of records (no dashboard, no visualization, no
statistics framework) and is exposed via `--summarize <evaluations-dir>`,
which loads every `*.evaluation.json` in that directory (the sole source
of truth -- see Immutability above) and prints the summary as JSON to
stdout.

## CLI

```text
python3 .tomtit/dogfood/evaluate_dogfood_run.py \
  --evidence-root <run-evidence-root> \
  --owner-disposition ACCEPTED \
  --active-human-seconds 120 \
  --owner-interruptions 1 \
  --clarification-count 0 \
  --manual-edit-lines 0 \
  [--escaped-defect-reported TRUE|FALSE|UNKNOWN] \
  [--owner-note "..."] \
  [--task-contract-sha256 <64-hex>] \
  [--runtime-commit <git-sha>]

python3 .tomtit/dogfood/evaluate_dogfood_run.py --summarize <evaluations-dir>
```

`--task-contract-sha256` and `--runtime-commit` are both optional,
EXPLICIT_INPUT only, and strictly validated (`CONFIG_FAILURE`, exit 2, on
a malformed value); omitting either leaves the corresponding field `null`
with `*_provenance = "UNKNOWN"`.

## Explicitly deferred

Not built here, on purpose:

- SQLite (or any other) analytics platform/store.
- Dashboards or any visualization.
- LLM-as-judge evaluation.
- Model routing / model router evaluation.
- A large statistical framework.
- Automatic human-attention tracking (interaction timestamps, activity
  monitors) -- `active_human_seconds` etc. remain Owner-supplied for V0.
- Graph analytics.
- An RL feedback pipeline.
- Auto-complete or graph functionality of any kind.
- A false-success predicate. V0 records only
  `escaped_defect_reported: TRUE | FALSE | UNKNOWN`; a later analysis may
  combine that with Runtime acceptance to classify false-success, but that
  policy is not encoded here.
