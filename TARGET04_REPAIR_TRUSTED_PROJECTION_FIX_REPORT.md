# TARGET-04 Repair Trusted Projection Fix -- Implementation Report

WORK_CLASS=DEFECT
CURRENT_GATE=TARGET_04

BASELINE_COMMIT=3cda474be1caabfde3bfed03b0c6c6fcbc344d34
SOURCE_COMMIT=5c2eaaf7ef248ae17a80741fef2aeab5e07feb40

BLOCKING_FINDING=R01_REPAIR_PREFLIGHT_TRUSTED_PROJECTION_MISSING

## 1. Problem (Codex reproduction)

`run_repair.prove_reviewed_state()` called
`run_review.protected_git_metadata_snapshot(workspace)` with no
`trusted_baseline` at all. Any benign, same-window timestamp-only
instability observed during *repair's own* measurement (e.g. an
OS-attached extended attribute) had nothing to validate against, so the
measurement failed closed (`EvidenceIntegrityFailure`, caught, actual
hash falls back to `""`), which then mismatched the reviewed hash and
raised `WORKSPACE_STATE_MISMATCH` -- blocking Attempt 2 before it ever
ran, exactly as Codex's independent reproduction showed
(`ATTEMPT2_MODEL_INVOKED=False`).

## 2. Fix

### 2.1 Persist the exact reviewed projection

`REVIEW1_FULL_PROJECTION_PERSISTED=YES`
`REVIEW1_FULL_PROJECTION_BOUND_TO_RECEIPT=YES`

Chose design **A** (embed directly in the receipt) over a sidecar
artifact: `review-receipt.json` is already one of the exact files
`load_review_evidence()` validates via `read_canonical_evidence()` (the
existing canonical evidence identity mechanism), and it already carries
every provenance identifier (`review_run_id`, `implementation_run_id`,
`implementation_attempt`, `workspace`, `baseline_commit`) a sidecar would
otherwise need its own binding scheme to reproduce. `run_review.py`'s
`main()` now writes `reviewed_protected_git_metadata_projection` --
exactly `review_exit_git_metadata`, the same dict already hashed into
`reviewed_protected_git_metadata_hash` -- as a new receipt field. No new
file, no new binding mechanism invented.

### 2.2 Repair loads and verifies it before use

`REPAIR_USES_EXACT_REVIEW1_PROJECTION=YES`

New `run_repair._load_trusted_review_projection(review_receipt, *,
expected_hash)`:

- missing -> `REVIEWED_PROTECTED_PROJECTION_MISSING`
- not a non-empty string-keyed/string-valued mapping ->
  `REVIEWED_PROTECTED_PROJECTION_MALFORMED`
- `workspace_hash(projection) != expected_hash` (the receipt's own
  already-`_is_sha256`-validated `reviewed_protected_git_metadata_hash`)
  -> `REVIEWED_PROTECTED_PROJECTION_HASH_MISMATCH`

`prove_reviewed_state()` was restructured only enough to compute
`partitioned_identity_available` *before* the git-metadata measurement
(previously computed after, unconditionally, with no baseline). When
available, the verified projection is passed as `trusted_baseline` to
`protected_git_metadata_snapshot()`; legacy (pre-partitioned-identity)
review evidence keeps the exact prior unbaselined call and its separate
strict whole-workspace-hash fallback path, both byte-for-byte unchanged.
Provenance binding (workspace / implementation_run_id /
implementation_attempt / baseline_commit) is inherited for free: it lives
in the same receipt dict `bind_review_to_implementation()` already
verifies *before* `prove_reviewed_state()` is ever called.

### 2.3 No bootstrap in repair

`REPAIR_CAN_BOOTSTRAP=NO`

`run_repair.py` never calls `establish_protected_git_metadata_baseline()`
(verified by a source-scan regression test for the qualified call form
`run_review.establish_protected_git_metadata_baseline`). A missing,
malformed, or hash-mismatched trusted projection blocks preflight
outright -- no rollover to a fresh bootstrap, no fallback to the run-start
baseline, no acceptance of an unstable current reading on its own.

## 3. Tests

| Requirement | Test | Result |
|---|---|---|
| Sec. 7 benign race recovery | `test_repair_recovers_from_benign_intra_measurement_timestamp_race` | PASS |
| Sec. 8 semantic drift, matching stat fields | `test_repair_rejects_semantic_git_metadata_drift_even_with_matching_stat_fields` | PASS |
| Sec. 9.A missing projection | `test_repair_blocks_when_reviewed_projection_missing` | PASS |
| Sec. 9.B malformed projection | `test_repair_blocks_when_reviewed_projection_malformed` | PASS |
| Sec. 9.B malformed (empty) | `test_repair_blocks_when_reviewed_projection_empty` | PASS |
| Sec. 9.C hash mismatch | `test_repair_blocks_when_reviewed_projection_hash_mismatch` | PASS |
| Sec. 9.D wrong review run | pre-existing `test_review_bound_to_another_implementation_run`, `test_wrong_implementation_run_id_fails_before_side_effect_authority_trusted` | PASS (rerun, unmodified) |
| Sec. 9.E wrong implementation attempt | pre-existing `test_wrong_implementation_attempt_fails_before_side_effect_authority_trusted`, `*_attempt_fails` family | PASS (rerun, unmodified) |
| Sec. 9.F workspace/path/role mismatch | pre-existing `test_review_bound_to_another_workspace`, `test_review_bound_to_another_baseline` | PASS (rerun, unmodified); role tamper covered by the hash-mismatch test above |
| Sec. 9.G valid exact projection | `test_repair_accepts_valid_exact_review1_projection_with_no_drift` | PASS |
| Sec. 6 no bootstrap | `test_repair_never_calls_coherent_bootstrap_establishment` | PASS |
| Sec. 12 lifecycle: benign churn reaches Attempt 2 | `test_target04_repair_lifecycle_benign_churn_reaches_attempt2` | PASS |
| Sec. 12 lifecycle: semantic drift blocks Attempt 2 | `test_target04_repair_lifecycle_semantic_drift_blocks_attempt2` | PASS |

`REPAIR_TIMESTAMP_ONLY_RECOVERY=PASS`
`ATTEMPT2_REACHABLE_AFTER_BENIGN_CHURN=PASS`
`REPAIR_SEMANTIC_DRIFT_REJECTED=PASS`
`ATTEMPT2_NOT_REACHED_ON_DRIFT=PASS`
`MISSING_PROJECTION_BLOCKS=PASS`
`MALFORMED_PROJECTION_BLOCKS=PASS`
`WRONG_REVIEW_BINDING_BLOCKS=PASS`
`WRONG_IMPLEMENTATION_BINDING_BLOCKS=PASS`
`COHERENT_BOOTSTRAP_UNCHANGED=PASS` -- `establish_protected_git_metadata_baseline()` and its bootstrap contract were not touched; the focused coherent-bootstrap tests (A/B/C/D/E/F/G/L/M) all pass unmodified.
`F01_REMAINS_CLOSED=PASS` -- `protected_git_metadata_snapshot()`'s strict-validation contract is unchanged; only its *caller* in `run_repair.py` now supplies a real trusted baseline instead of `None`.

`test_reviewed_protected_git_metadata_hash_survives_timestamp_only_drift`
(a pre-existing test using a real, non-racy `os.utime()` touch *before*
any measurement starts) was re-verified: it exercises a different, already
-solved case (a clean cross-call timestamp difference, stripped by
`_stable_cross_phase_projection` regardless of `trusted_baseline`), not
Codex's intra-measurement race; it continues to pass unmodified.

## 4. Validation matrix

Full output in `TARGET04_REPAIR_TRUSTED_PROJECTION_FIX_FULL.log` (repo
root, no `tee`). All steps exited 0:

1. Focused new repair trusted-projection tests: 10/10 passed.
2. Focused repair lifecycle/binding regressions: 11/11 passed.
3. Focused coherent-bootstrap tests (unaffected): 9/9 passed.
4. Full suite `test_run_review.py`: 154 collected, all passed.
5. Full suite `test_run_repair.py`: 122 collected, all passed.
6. Full suite `test_run_loop.py`: 86 collected, all passed.
7. Full suite `tests/adapters/test_claude_code_cli.py`: 436 collected, all passed.
8. Full repository regression `pytest tests/ -q`: 985 collected, all passed.
9. `compileall` on all 3 modified files: clean.
10. `ruff check` on all 3 modified files: clean.
11. `git diff --check`: clean.

## 5. Scope discipline

Modified exactly: `.tomtit/dogfood/run_review.py` (one new receipt
field), `.tomtit/dogfood/run_repair.py` (one new helper + the minimal
reordering `prove_reviewed_state()` needed to use it), and
`.tomtit/dogfood/test_run_repair.py` (fixture update + new tests). No
receipt/evidence schema file exists separately from these two modules, so
none was touched. `run_loop.py` was not modified -- it does not call
`prove_reviewed_state()` or `protected_git_metadata_snapshot()` on
repair's behalf. The coherent-bootstrap contract
(`establish_protected_git_metadata_baseline`, L1/L2/L3 modes, F01) is
untouched.

`TARGET04_MODIFIED=NO`
`TARGET04_RERUNS=0`
`TARGET04B_STARTED=NO`
No real product models were invoked; all tests use fake executables or
direct unit-level calls against throwaway Git repositories.

## 6. Commits

1. `5c2eaaf7ef248ae17a80741fef2aeab5e07feb40` -- source + tests (narrow, single commit).
2. This report (separate commit, immediately following).

No review-report commit, no tag, no push.

`NEXT_ACTION=CODEX_REVIEWS_REPAIR_TRUSTED_PROJECTION_FIX`
