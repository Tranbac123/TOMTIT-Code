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
| `task_contract_sha256` | string \| null | RUNTIME_DERIVED | `task-contract.json.task_prompt_sha256` (see note below) |
| `workspace_baseline` | string \| null | RUNTIME_DERIVED | `task-contract.json.baseline_commit` |
| `runtime_commit` | string \| null | tool-detected, Owner-overridable | `git rev-parse HEAD` of the Coordinator repo the tool runs from, or `--runtime-commit` |

**`task_contract_sha256` naming note:** `run_loop.py`'s own
`task-contract.json` has no field literally named `task_contract_sha256`.
The closest canonical, deterministic identity hash it records is
`task_prompt_sha256` -- the SHA-256 of the exact task prompt text
(`text.strip().encode("utf-8")`, matching `run_loop.py`'s own
`run_review._sha256` convention) this run was bound to. For a run whose
prompt itself references an external `TaskContract` document by hash (as
Target-04A's prompt references `TARGET_04A_TASK_CONTRACT.md`), these are
two different hashes of two different documents; this field records the
prompt hash, since that is the only sha256-of-a-contract-like-input field
actually present in canonical Runtime evidence.

**`runtime_commit` note:** this is metadata about the evaluator tool's own
checkout, not something read from the run's own Runtime evidence (which
does not record it). `--runtime-commit` overrides the auto-detected value
for evaluating evidence produced by a different Runtime checkout than the
one currently on disk.

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
- `UNKNOWN`: the field is present but is not a trustworthy number (e.g.
  malformed) -- the concept is measurable but this run's evidence did not
  provide enough evidence.
- `KNOWN`: a real numeric value is present.

Never inferred. Never zero-filled in place of `UNKNOWN`/`UNAVAILABLE`.

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

## Immutability / no-overwrite rule (Section 11)

One evaluation artifact per run, written to:

```text
<evidence-root's-parent>/evaluations/<run_id>.evaluation.json
```

plus an append-only line in:

```text
<evidence-root's-parent>/evaluations/dogfood-evaluations-v0.jsonl
```

If `<run_id>.evaluation.json` already exists, the tool fails clearly
(`EVALUATION_ALREADY_EXISTS`) and writes nothing. For Batch 1, evaluation
records are immutable; no replacement mechanism exists.

## Provenance

Every field belongs to exactly one of:

- `RUNTIME_DERIVED` -- read directly from canonical `run_loop.py` evidence.
- `OWNER_SUPPLIED` -- given on the command line, never inferred.
- `DERIVED_FROM_OWNER_AND_RUNTIME` -- a pure function of fields from both
  of the above (`accepted`, `accepted_without_manual_edit`,
  `active_human_minutes_to_confident_accept`).
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
evidence completeness rate, elapsed time, cost per accepted task when
`KNOWN`, and escaped defect count when reported.

`summarize_evaluations()` computes exactly this list as plain arithmetic
over an in-memory list of records (no dashboard, no visualization, no
statistics framework) and is exposed via `--summarize <evaluations-dir>`,
which loads every `*.evaluation.json` in that directory and prints the
summary as JSON to stdout.

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
  [--runtime-commit <sha>]

python3 .tomtit/dogfood/evaluate_dogfood_run.py --summarize <evaluations-dir>
```

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
