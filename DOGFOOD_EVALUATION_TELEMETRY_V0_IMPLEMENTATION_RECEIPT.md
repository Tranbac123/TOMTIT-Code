# Dogfood Evaluation Telemetry V0 -- Implementation Receipt

WORK_CLASS=PRODUCT
CURRENT_GATE=DOGFOOD_EVALUATION_TELEMETRY_V0

## What was built

`.tomtit/dogfood/evaluate_dogfood_run.py`: a small, standalone CLI that
reads one `run_loop.py` run's canonical evidence (read-only) plus
Owner-supplied disposition/interaction fields, and writes one immutable
`DOGFOOD_EVALUATION_V0` JSON record, to evaluate

```text
H1 = TOMTIT reduces active human supervision required to confidently
     accept a software change
```

across the first 3-5 real dogfood tasks. The full locked contract is
`docs/DOGFOOD_EVALUATION_TELEMETRY_V0.md`.

## Scope discipline

- No Runtime authority, execution behavior, repair behavior, review
  behavior, or Target-04 logic was touched. `git diff --stat` against
  `run_loop.py`/`run_repair.py`/`run_review.py`/`run_task.py` is empty --
  only three new files were added.
- `EVALUATION_CANONICAL_AUTHORITY=NO`: no import or call path from any of
  those four Runtime modules references `evaluate_dogfood_run` at all
  (`grep` confirms zero matches; also enforced by
  `test_no_runtime_module_imports_the_evaluator`).
- No SQLite, no dashboard, no model routing, no auto-complete/graph
  functionality. Output is plain JSON/JSONL, matching every other
  `.tomtit/dogfood/*.py` tool's existing evidence convention.
- Investigated the repository's existing, larger evaluation architecture
  (`docs/EVALUATION_CONTRACT_V1.md`, `src/tomtit/domain/evaluation.py`,
  `src/tomtit_decision_eval_v0/`) before designing anything, to avoid
  duplicating it. That architecture targets a different, more abstract
  Runtime state-machine vocabulary
  (`RunState`/`StopReason`/`DecisionEvent`) than what `run_loop.py` itself
  actually emits, and is explicitly out of scope for this narrow V0 --
  documented in the spec's "Relationship to existing evaluation
  architecture" section rather than silently ignored.

## Schema highlights

- Identity: `schema_version`, `evaluation_id`, `run_id`,
  `task_contract_sha256` (mapped from `task-contract.json`'s own
  `task_prompt_sha256` field -- the naming mismatch is documented
  explicitly, not silently glossed over), `runtime_commit`
  (auto-detected from the Coordinator repo's own `git rev-parse HEAD`,
  overridable), `workspace_baseline`, `evaluated_at`.
- Owner-supplied: `owner_disposition`
  (`ACCEPTED`/`ACCEPTED_WITH_MANUAL_EDIT`/`NOT_USABLE`),
  `active_human_seconds`, `owner_interruptions`, `clarification_count`,
  `manual_edit_lines`, `owner_note`, `escaped_defect_reported`
  (`TRUE`/`FALSE`/`UNKNOWN`, defaults `UNKNOWN`, never inferred from a
  Review2 APPROVE).
- Derived: `accepted`, `accepted_without_manual_edit`,
  `active_human_minutes_to_confident_accept` (null when not accepted --
  `NOT_USABLE` is never reinterpreted as infinite time).
- Runtime-derived: `runtime_terminal_outcome`, `runtime_stop_reason`,
  `runtime_accepted` (kept strictly distinct from the Owner-derived
  `accepted`), `implementation_attempt_count`, `repair_count`,
  `review_count`, `model_invocation_count`, `elapsed_seconds` -- all read
  directly from `loop-result.json`, never recreated from guesses.
- Tri-state cost/token (`KNOWN`/`UNKNOWN`/`UNAVAILABLE`): reads
  `loop-result.json.usage`; `UNAVAILABLE` when the block/field is absent
  from this evidence shape, `UNKNOWN` when present but not a trustworthy
  number, never zero-filled.
- `scope_violation` (`TRUE`/`FALSE`/`UNKNOWN`): derived from
  `unexpected_changed_paths`; `UNKNOWN` whenever that field is absent or
  evidence is incomplete -- never manufactured certainty.
- `evidence_complete`: a bounded, deterministic, **trajectory-aware**
  check driven by the run's own `implementation_run_ids`/`review_run_ids`
  -- a first-pass APPROVE never requires repair/Review2 artifacts; a
  preflight-blocked run (both lists empty) requires only the three
  loop-level files.

## Output / immutability

One record at `<evidence-root's-parent>/evaluations/<run_id>.evaluation.json`,
plus an append-only line in
`<evidence-root's-parent>/evaluations/dogfood-evaluations-v0.jsonl`. A
second evaluation of the same `run_id` fails clearly
(`EVALUATION_ALREADY_EXISTS`) rather than overwriting.

## Target-04 compatibility (Section 15)

The real, historical Run006 evidence
(`TOMTIT-E2E-Evidence/real-e2e-target04a-006-sonnet-medium`) was used as a
read-only fixture (`test_real_run006_evidence_extraction_read_only`,
skips gracefully if absent on a given machine): the extractor correctly
derives `implementation_attempt_count=2`, `repair_count=1`,
`review_count=2`, `model_invocation_count=4`,
`terminal_outcome="COMPLETED"`, `stop_reason="REVIEW_APPROVED"`,
`evidence_complete=True` -- confirmed by direct manual extraction before
the test was written. The test asserts the evidence root's parent
directory listing is byte-identical before and after (no `write_evaluation_record`
call is ever made against real Run006 evidence). A separate, disposable
synthetic fixture with matching values (`test_run006_shaped_synthetic_fixture`)
exercises the same trajectory without touching any real filesystem state.

## Tests

`.tomtit/dogfood/test_evaluate_dogfood_run.py`: 43 tests -- identity/schema,
the full owner-disposition x manual-edit-lines derivation matrix, the
`active_human_minutes_to_confident_accept` accepted-vs-`NOT_USABLE` case,
Runtime-derived extraction, the `runtime_accepted`-vs-`accepted` distinction,
the full cost/token tri-state matrix (`KNOWN`/`UNKNOWN`/`UNAVAILABLE`), the
`scope_violation` tri-state matrix (including the incomplete-evidence
`UNKNOWN` case), `escaped_defect_reported` default/owner-supplied, the
trajectory-aware completeness matrix (first-pass, repair, a missing
repair-specific file precisely named, preflight-blocked), the Run006-shaped
fixture and the real read-only Run006 fixture, output/append/no-overwrite,
every Section 16 negative case (A-H), the summary helper, CLI end-to-end
(including missing/negative-argument rejection and `--summarize`), and the
no-authority-coupling grep check.

## Validation (Section 19)

Full log: `DOGFOOD_EVALUATION_TELEMETRY_V0_FULL.log` (this task's log; not
committed, gitignored per repository convention).

| Check | Result |
|---|---|
| Focused evaluator test suite | PASS, 43 |
| Full `test_run_review.py` (proves no Runtime change) | PASS, 263 |
| Full `test_run_repair.py` | PASS, 134 |
| Full `test_run_loop.py` | PASS, 93 |
| Full `.tomtit/dogfood/` tree | PASS, 533 (43 + 93 + 134 + 263) |
| `compileall` | PASS |
| `ruff check` | PASS |
| `git diff --check` | PASS |

No transient failures this run -- every check passed on the first serial
pass.

## Out of scope / unchanged

- `run_loop.py`, `run_repair.py`, `run_review.py`, `run_task.py`: byte-unchanged.
- No Target-04 run was started or rerun.
- No real product/Claude/Codex model was invoked.
- No push, no tag.

`EVALUATION_CANONICAL_AUTHORITY=NO`
`RUNTIME_BEHAVIOR_CHANGED=NO`
