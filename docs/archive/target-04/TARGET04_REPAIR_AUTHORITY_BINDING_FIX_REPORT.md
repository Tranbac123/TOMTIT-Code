# TARGET-04 Repair Authority Binding Fix -- Implementation Report

WORK_CLASS=DEFECT
CURRENT_GATE=TARGET_04

BLOCKERS_CLOSED=B01_EXPECTED_REVIEW1_ID_UNBOUND, B02_MODERN_EVIDENCE_CAN_DOWNGRADE_TO_LEGACY

## 1. B01 -- bind repair to the exact expected Review1 id

`EXPECTED_REVIEW1_ID_REQUIRED=YES`
`EXPECTED_REVIEW1_ID_MATCH_REQUIRED=YES`
`REPAIR_AUTHORITY_SOURCE=EXACT_EXPECTED_REVIEW1_EXIT_STATE`

`run_loop.py`'s `invoke_repair()` gained a new required parameter
`expected_review_run_id`, threaded from `main()`'s already-selected
`review1_id` (the same value it uses as `child_run_id` for the review that
was actually run) -- never inferred from a directory name, the receipt's
own contents, the implementation identity, or filesystem ordering. It is
passed through to `run_repair.py` as a new required CLI flag
`--expected-review-run-id`.

`run_repair.py` gained `bind_review_to_expected_review_run(review_receipt,
expected_review_run_id)`, called immediately after `load_review_evidence()`
and *before* `require_request_changes_verdict()` -- i.e. before anything in
the receipt (verdict, findings, reviewed projection, reviewed state hashes)
is trusted at all:

- `review_run_id` absent/empty -> `REVIEW_RUN_ID_MISSING`
- `review_run_id` present but `!= expected_review_run_id` -> `REVIEW_RUN_ID_MISMATCH`

Both are `IntegrityFailure` (exit 3), matching the existing evidence-binding
failure pattern (`REVIEW_WORKSPACE_MISMATCH`, `REVIEW_IMPLEMENTATION_MISMATCH`,
...).

## 2. B02 -- modern evidence can never downgrade to legacy

`PROJECTION_IS_MODERN_MARKER=YES`
`MODERN_TO_LEGACY_DOWNGRADE_ALLOWED=NO`
`INCOMPLETE_MODERN_EVIDENCE=BLOCKS (PREVIOUS_EVIDENCE_INCOMPLETE)`
`TRUE_LEGACY_SUPPORT_PRESERVED=YES`

`reviewed_protected_git_metadata_projection` was previously not part of the
13-field `partitioned_identity_fields` tuple that decides modern-vs-legacy
classification, so a receipt could keep the projection while shedding
every other modern field and be silently reclassified as legacy -- exactly
Codex's B02 reproduction. It is now the 14th member of that same tuple:
its presence alone (via the existing `any(...)` / `all(...)` pattern, now
also true of it) is enough to mark a receipt modern, and modern evidence
missing or malformed in *any* required field -- the projection included --
blocks as `PREVIOUS_EVIDENCE_INCOMPLETE` before the legacy
whole-workspace-hash path is ever reached.

A new shared helper, `_is_stable_projection_mapping()`, is used both by
this classification (a shallow shape check: non-empty, string-keyed,
string-valued) and by `_load_trusted_review_projection()`'s deeper
hash-bound verification, so the two can never disagree about what
"well-formed" means. `_load_trusted_review_projection()`'s own missing/
malformed checks are kept as defense in depth even though the
classification layer now makes them unreachable in the normal flow.

Genuine legacy evidence (no partitioned-identity field, no projection, at
all) is completely untouched: `partitioned_identity_present` stays `False`
and the pre-existing strict whole-workspace-hash fallback path runs exactly
as before.

## 3. Authority order (Section 12)

`run_repair.run_preflight()` now performs, in order: load implementation
evidence (bound to expected run/attempt) -> load review evidence -> verify
expected Review1 id (new, B01) -> verify verdict/findings -> verify
workspace/implementation/baseline binding (`bind_review_to_implementation`)
-> classify modern vs legacy (extended, B02) -> for modern evidence, load
and verify the exact trusted projection -> prove current workspace equals
Review1 reviewed state -> remaining allowed-path/side-effect contract
checks -> only then can `main()` invoke Claude for Attempt 2. No provider
boundary precedes any of this.

`ATTEMPT2_GATED_BY_EXACT_REVIEW1_PROOF=YES`

## 4. Tests

| Requirement | Test | Result |
|---|---|---|
| 4.A matching id continues | `test_repair_accepts_matching_expected_review_run_id` | PASS |
| 4.B wrong id blocks | `test_repair_blocks_when_expected_review_run_id_differs` | PASS |
| 4.C missing id blocks | `test_repair_blocks_when_receipt_review_run_id_missing` | PASS |
| 4.D foreign self-consistent review blocks | `test_repair_blocks_foreign_self_consistent_review_with_wrong_id` | PASS |
| Lifecycle CASE 2 (wrong id, Attempt2 not invoked) | `test_target04_repair_lifecycle_wrong_review_id_blocks_attempt2` | PASS |
| Sec. 9 exact B02 reproduction | `test_repair_blocks_modern_marker_with_all_other_modern_fields_removed` | PASS |
| Lifecycle CASE 4 (malformed modern, Attempt2 not invoked) | `test_target04_repair_lifecycle_malformed_modern_downgrade_blocks_attempt2` | PASS |
| Valid modern path preserved (prior round) | `test_repair_accepts_valid_exact_review1_projection_with_no_drift`, `test_target04_repair_lifecycle_benign_churn_reaches_attempt2` | PASS (rerun) |
| Benign timestamp recovery preserved | `test_repair_recovers_from_benign_intra_measurement_timestamp_race` | PASS (rerun) |
| Semantic drift still blocks (Lifecycle CASE 5) | `test_repair_rejects_semantic_git_metadata_drift_even_with_matching_stat_fields`, `test_target04_repair_lifecycle_semantic_drift_blocks_attempt2` | PASS (rerun) |
| Prior-round missing/malformed/hash-mismatch projection tests | re-verified against the new (earlier, classification-layer) block point; category updated to `PREVIOUS_EVIDENCE_INCOMPLETE` where the field alone was removed/malformed, `REVIEWED_PROTECTED_PROJECTION_HASH_MISMATCH` unchanged for the hash-binding case | PASS |

`EXPECTED_REVIEW1_BINDING=PASS`
`FOREIGN_REVIEW_BLOCKS=PASS`
`MISSING_REVIEW_ID_BLOCKS=PASS`
`ATTEMPT2_NOT_REACHED_ON_WRONG_REVIEW=PASS`
`VALID_MODERN_REPAIR_PATH=PASS`
`BENIGN_TIMESTAMP_RECOVERY=PASS`
`ATTEMPT2_REACHABLE_WITH_VALID_REVIEW1=PASS`
`MODERN_PROJECTION_MARKS_MODERN_SCHEMA=PASS`
`MODERN_TO_LEGACY_DOWNGRADE_IMPOSSIBLE=PASS`
`ATTEMPT2_NOT_REACHED_ON_INCOMPLETE_MODERN_EVIDENCE=PASS`
`SEMANTIC_DRIFT_BLOCKS=PASS`
`ATTEMPT2_NOT_REACHED_ON_DRIFT=PASS`
`COHERENT_BOOTSTRAP_UNCHANGED=PASS` -- `establish_protected_git_metadata_baseline()` and L1/L2/L3 semantics untouched; all 9 focused coherent-bootstrap tests pass unmodified.
`F01_REMAINS_CLOSED=PASS` -- `protected_git_metadata_snapshot()`'s strict-validation contract untouched; the 26 focused F01/trusted-content-proof regressions pass unmodified.

## 5. Validation matrix

Full output in `TARGET04_REPAIR_AUTHORITY_BINDING_FIX_FULL.log` (repo root,
no `tee`). All steps exited 0:

1. Focused expected-review-ID binding tests: 5/5 passed.
2. Focused modern/legacy classification (B02) tests: 2/2 passed.
3. Focused valid trusted-projection repair tests (prior round): 12/12 passed.
4. Focused coherent-bootstrap regressions: 9/9 passed.
5. Focused F01/trusted-content-proof regressions: 26/26 passed.
6. Full suite `test_run_review.py`: 154 collected, all passed.
7. Full suite `test_run_repair.py`: 129 collected, all passed.
8. Full suite `test_run_loop.py`: 86 collected, all passed.
9. Full suite `tests/adapters/test_claude_code_cli.py`: 436 collected, all passed.
10. Full repository regression `pytest tests/ -q`: 985 collected, all passed.
11. `compileall` on all 5 modified files: clean.
12. `ruff check` on all 5 modified files: clean.
13. `git diff --check`: one trailing-blank-line finding at the tail of
    `test_run_repair.py`, fixed, rerun clean.

## 6. Scope discipline

Modified: `.tomtit/dogfood/run_loop.py` (thread `expected_review_run_id`
through `invoke_repair()` and its one call site),
`.tomtit/dogfood/run_repair.py` (new binding function, new CLI flag, B02
classification fix), `.tomtit/dogfood/test_run_repair.py` (new tests,
fixture updates), `.tomtit/dogfood/test_run_loop.py` (one direct
`invoke_repair()` call site updated for the new required kwarg).
`run_review.py` was not touched -- no receipt-schema change was required
for this task; the projection field it already writes is sufficient.
Coherent-bootstrap (`establish_protected_git_metadata_baseline`, L1/L2/L3)
and F01 (`protected_git_metadata_snapshot`'s trusted-baseline contract)
semantics are unchanged.

`TARGET04_MODIFIED=NO`
`TARGET04_RERUNS=0`
`TARGET04B_STARTED=NO`
No real product models were invoked; all tests use fake executables or
direct unit-level calls against throwaway Git repositories.

## 7. Commits

1. `64046ae5cfa1fff2822bad907bea26e68b5b0871` -- source + tests (narrow, single commit).
2. This report (separate commit, immediately following).

No review-report commit, no tag, no push.

`NEXT_ACTION=CODEX_REVIEWS_REPAIR_AUTHORITY_BINDING_FIX`
