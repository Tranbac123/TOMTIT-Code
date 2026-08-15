# TARGET-04 Coherent Initial Baseline Fix -- Implementation Report

WORK_CLASS=RUNTIME_DESIGN_CORRECTION
CURRENT_GATE=TARGET_04
BASELINE_COMMIT=b84ae35c84f196a1af4da0a97239587e9ac66e53
SOURCE_COMMIT=ca06ea4dc517409610e93d351cb588910ca16d04

## 1. Problem

Real dogfood loops (`real-e2e-target04a-001/002/003-sonnet-medium`) blocked
at deep preflight with `IMMUTABILITY_VIOLATION` before Attempt 1 ever ran.
Forensics traced this to the old invariant: *"initial protected measurement
+ timestamp instability + no prior trusted content -> block"*. That
invariant is correct for **post-baseline validation** (an unstable reading
can never prove its own past), but it is too strict for **bootstrap**: at
the very start of a run there is, by construction, no prior trusted
snapshot to validate against, so any transient OS-level metadata churn
(observed consistently as asynchronous ctime-only churn on `.git` files,
hypothesized to be a macOS `com.apple.provenance` xattr write) fails the
very first measurement closed, with no way to ever recover.

## 2. Fix

`OLD_INITIAL_INVARIANT=SUPERSEDED`
`NEW_INITIAL_INVARIANT=ESTABLISH_COHERENT_START_STATE`

Before any untrusted subprocess is authorized, Runtime now establishes a
coherent protected-repository starting state by obtaining, within one
bounded operation, at least two independently rebuilt complete
observations whose stable (timestamp-excluded) projections agree. The
second matching observation becomes the immutable trusted baseline for
the rest of the run.

### 2.1 Explicit bootstrap entrypoint (kept separate from validation)

`BOOTSTRAP_ENTRYPOINT_EXPLICIT=YES`

`protected_git_metadata_snapshot(cwd, *, trusted_baseline=None)` is
**unchanged** and continues to fail closed on any unproven timestamp-only
instability, with or without a baseline -- it never bootstraps. A new,
separate function, `establish_protected_git_metadata_baseline(cwd)`, is
the only place Runtime may trust a protected observation without external
evidence, and only via the bounded two-consecutive-match algorithm below.

### 2.2 Bounded initial establishment algorithm

`MAX_INITIAL_BASELINE_OBSERVATIONS=3`
`TWO_CONSECUTIVE_MATCHES_REQUIRED=YES`

```
S1 == S2                 -> establish S2 (2 observations)
S1 != S2, S2 == S3        -> establish S3 (3 observations)
S1 != S2, S2 != S3        -> block (EvidenceIntegrityFailure)
```

Only *consecutive* pairs are ever compared; S1 is never compared directly
against S3. The loop is flat (one observation per iteration), never
nested. Each observation independently rebuilds the complete protected
tree from scratch via `_build_provisional_protected_git_metadata_observation`
(new function) -- no subtree reuse, no descriptor reuse, no per-file
nested retry.

### 2.3 What one provisional observation may do

A single observation is never authoritative by itself. Within one
observation, a same-window mismatch confined *exactly* to
`mtime_ns`/`ctime_ns` is tolerated unconditionally (new
`bootstrap_unconditional_timestamp_tolerance` mode threaded through
`_regular_file_measurement`, `_measure_type_aware_tree`,
`snapshot_type_aware_tree`, `_workspace_git_entry_descriptor`), recorded
only as a diagnostic (`churn_sink`). Every non-timestamp check --
unreadable/incomplete files, byte-count inconsistency, path replacement,
device/inode/mode/type/symlink mismatches, malformed protected paths,
incomplete tree traversal -- remains unconditional and fails the whole
establishment attempt immediately; nothing in bootstrap mode weakens any
of those.

`TIMESTAMPS_SEMANTIC_IDENTITY=NO`
`TIMESTAMPS_USED_AS_DIAGNOSTIC_OR_RACE_SIGNAL=YES`

The returned `ProtectedGitMetadataBaseline` records
`observations_used`, `timestamp_churn_observed`, and
`required_third_observation` as narrow diagnostics; no new observability
subsystem was added.

### 2.4 Lifecycle threading (run_loop.py `_deep_preflight`)

`L1_MODE=COHERENT_BOOTSTRAP`
`L2_MODE=STRICT_VALIDATION`
`L3_MODE=STRICT_VALIDATION`
`NO_UNTRUSTED_SUBPROCESS_BEFORE_BASELINE=YES`
`POST_BASELINE_VALIDATION_REMAINS_STRICT=YES`
`LATER_VALIDATION_CAN_REBOOTSTRAP=NO`

L1 (`establish_protected_git_metadata_baseline`) now runs inside
`_deep_preflight`, after `claude_executable` discovery and strictly
before `resolve_codex_contract` (the first untrusted subprocess). On
failure it raises `PreflightBlocked("PROTECTED_BASELINE_ESTABLISHMENT_FAILED", ...)`
before any untrusted subprocess starts. L2 (`capability_probe_before`)
now validates in strict mode against L1's established baseline instead
of taking an unbaselined snapshot of its own (the old implicit-bootstrap
shape). L3 (`capability_probe_after`) was already chained from L2 and is
unchanged. `_deep_preflight` now returns `baseline_git_metadata` for
`main()` to carry through to the run-level `baseline_git_metadata` used
by `_derive_final_workspace_evidence()` for the terminal comparison --
the previous standalone pre-`_deep_preflight` bootstrap attempt in
`main()` was removed (it used the old `protected_git_metadata_snapshot(workspace)`
single-snapshot call) rather than duplicating establishment.

`run_review.py`'s own `main()` (its independent L1/L2/L3 lifecycle) was
already using this exact shape from an earlier fix round and required no
further change; it was verified, not modified, in this task.

### 2.5 Reviewed, left unchanged

The review1/review2 preservation pairs and the terminal-evidence
comparison in `run_loop.py` already correctly chain from their own local
"before" snapshot (or from L1's run-level baseline for the terminal
comparison) and needed no change. `run_repair.py`'s
`prove_reviewed_state()` calls `protected_git_metadata_snapshot(workspace)`
with no `trusted_baseline` during repair preflight; a benign churn there
could in principle also produce a spurious block, but fixing it would
require passing the review's *full* trusted git-metadata projection
through repair evidence (currently only a hash is carried), which is new
plumbing beyond this task's authorized scope (Section 13) and was not
implicated in the reproduced bug (which blocked before Attempt 1, never
reaching repair). Left unchanged; flagged here as a candidate follow-up
for a future, separately authorized task.

## 3. Tests (Section 11, A-N)

| Item | Test | Result |
|---|---|---|
| A | `test_establish_baseline_clean_bootstrap_two_matching_observations` | PASS |
| B | `test_establish_baseline_benign_timestamp_churn_during_first_observation` | PASS |
| C | `test_establish_baseline_content_mutated_before_first_read_adopts_new_content` | PASS |
| D | `test_establish_baseline_disagree_then_agree_establishes_on_third_observation` | PASS |
| E | `test_establish_baseline_three_disagreeing_observations_blocks` | PASS |
| F | `test_establish_baseline_intra_observation_inode_replacement_fails_closed` | PASS |
| G | `test_establish_baseline_unreadable_member_prevents_establishment` | PASS |
| H | `test_no_untrusted_subprocess_before_baseline_establishment` (run_loop) | PASS |
| I | `test_no_rebootstrap_after_baseline_established_on_later_drift` (run_loop) | PASS |
| J | `test_capability_probe_before_validates_against_run_baseline_not_bootstrap` (run_loop) | PASS |
| K | `test_capability_probe_after_remains_strict_validation_chained_from_before` (run_loop) | PASS |
| L | `test_establish_baseline_persistent_disagreement_blocks_after_exactly_bound` | PASS |
| M | `test_establish_baseline_rebuilds_whole_tree_each_observation` | PASS |
| N | All pre-existing F01 / trusted-content-proof / cross-phase regression tests | PASS (unmodified) |

`F01_REOPENED=NO` -- `protected_git_metadata_snapshot`'s strict-validation
contract (e.g. `test_protected_git_metadata_initial_snapshot_timestamp_instability_fails_closed`)
is untouched and still fails closed on a post-baseline unproven
timestamp-only instability.

## 4. Validation matrix

Full output in `TARGET04_COHERENT_INITIAL_BASELINE_FIX_FULL.log` (repo
root, no `tee`). All steps exited 0 / all-pass, no `F`/`E` result
characters in any pytest progress output:

1. Focused coherent-bootstrap tests (`run_review.py`): 9/9 passed.
2. Focused coherent-bootstrap loop-lifecycle tests (`run_loop.py`): 4/4 passed.
3. Focused prior F01 / trusted-content-proof regressions: 26/26 passed.
4. Full suite `test_run_review.py`: 154 collected, all passed.
5. Full suite `test_run_loop.py`: 86 collected, all passed.
6. Full suite `test_run_repair.py`: 112 collected, all passed.
7. Full suite `tests/adapters/test_claude_code_cli.py`: 436 collected, all passed.
8. Full repository regression `pytest tests/ -q`: 985 collected, all passed.
9. `compileall` on all 4 modified files: clean.
10. `ruff check` on all 4 modified files: clean.
11. `git diff --check`: clean (no whitespace errors).

One pre-existing, unrelated `SUBPROCESS_QUIESCENCE_UNVERIFIED`-class flake
was not observed in this run; not applicable here.

## 5. Scope discipline

`TARGET04_MODIFIED=NO` -- the Target-04 fixture was not touched.
`TARGET04_RERUNS=0` -- Target-04A was not rerun, per instruction.
`TARGET04B_STARTED=NO`.
No real product models (Claude/Codex) were invoked; all tests use fake
executables or direct unit-level calls against a throwaway Git repo.
Changes are confined to `.tomtit/dogfood/run_review.py`,
`.tomtit/dogfood/run_loop.py`, and their directly corresponding test
files, per Section 13.

## 6. Commits

1. `ca06ea4dc517409610e93d351cb588910ca16d04` -- source + tests (narrow, single commit).
2. This report (separate commit, immediately following).

No review-report commit, no tag, no push.

`NEXT_ACTION=CODEX_REVIEWS_COHERENT_INITIAL_BASELINE_FIX`
