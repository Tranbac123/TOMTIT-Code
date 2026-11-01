# TARGET-04 Canonical Index Authority Correction Report

## Scope

This is a narrow **correction** pass over the canonical Git-index identity
design implemented in `3f7ac1317ebb23c5ae55e24572582fa1dc9a7a0c` and reported
in `b501a4c6054257f555c9c25be94417bb4ce1a9ee`, addressing exactly the five
findings an independent Codex review (verdict `REQUEST_CHANGES`) required
before that design could be accepted: `F01`-`F05`. It does not redesign the
feature, does not touch `run_repair.py`, does not rerun Target-04A, and does
not start Target-04B.

## Baseline / lineage

- `BASELINE_COMMIT` (repo `HEAD` at task start): `b501a4c6054257f555c9c25be94417bb4ce1a9ee`
- `REVIEWED_SOURCE_COMMIT`: `3f7ac1317ebb23c5ae55e24572582fa1dc9a7a0c`
- `REVIEWED_SOURCE_ACTUAL_PARENT`: `808a2785e5c8fbbeec76b523480ca2c1696bb5b0`
  (confirmed via `git show -s --format='%H %P' 3f7ac13...`; this corrects the
  parent-commit label Codex's review flagged as inconsistent -- the lineage
  itself was always valid, only the label was wrong).
- `IMPLEMENTATION_REPORT_COMMIT_REVIEWED`: `b501a4c6054257f555c9c25be94417bb4ce1a9ee`
  (child of `3f7ac1317ebb23c5ae55e24572582fa1dc9a7a0c`).

## F01 -- Multi-command projection not coherent

**Problem:** `canonical_index_projection()` combined three independent Git
commands (`ls-files --stage`, `status --porcelain=v2`, `ls-files
--resolve-undo`) into one projection from a single pass, with no proof the
three outputs corresponded to the same underlying moment.

**Fix:** Split the single-pass query sequence into
`_build_canonical_index_projection_once(cwd) -> dict[str, str] | None`
(exactly one complete, non-retrying attempt). `canonical_index_projection()`
now wraps it in the same bounded 2-of-3-consecutive-match algorithm already
approved for `establish_protected_git_metadata_baseline()`:
`_MAX_CANONICAL_INDEX_OBSERVATIONS = 3`; `P1 == P2` accepts `P2`; `P1 != P2,
P2 == P3` accepts `P3`; `P1 != P2, P2 != P3` -> unavailable. Flat, non-nested:
one whole-projection attempt per loop iteration.

**Status:** CLOSED.

## F02 -- Canonical identity not required on all success paths

**Problem:** `protected_git_metadata_preserved()` had an early `if not
changed: return True` before the canonical index identity was ever checked,
so a raw-byte-identical observation could report "preserved" even with an
unavailable (`None`) or genuinely different canonical index identity.

**Fix:** The canonical-identity check (`before is not None and after is not
None and before == after`) now runs unconditionally, before any other
comparison, including the raw-equality path. No early return can bypass it.

**Status:** CLOSED.

## F03 -- Linked-worktree index role comparison too broad

**Problem:** The representation-tolerance exception applied to any
`*/index` key without regard for which physical file it named, so a linked
worktree's own index and a *different* worktree's index were treated
identically.

**Fix:** `_index_metadata_exception_keys(metadata)` reads the projection's own
`@git_dir`/`@git_common_dir` role paths. Ordinary repository (`git_dir ==
git_common_dir`): both `git_dir/index` and `git_common_dir/index` are
eligible. Linked worktree (`git_dir != git_common_dir`): `git_dir/index`
(current worktree) is eligible; the current worktree's own index is *also*
independently rediscovered a second time by the `git_common_dir` tree walk,
under a common-dir-relative key such as
`git_common_dir/worktrees/<name>/index` (Git's own `<common>/worktrees/<name>`
on-disk layout puts `git_dir` physically inside `git_common_dir`) -- that
alias key names the exact same physical file and is exactly as eligible.
`git_common_dir/index` itself (a genuinely different worktree's index, when
one exists at the common directory's own root) remains strict and is never
added. Missing/incomparable role info applies the exception to nothing
(fails closed).

This nested-alias case was found only while writing a *real* linked-worktree
regression test (not synthetic dicts): a genuine `git worktree add` fixture
showed the raw-representation-only refresh test failing because the walk
duplicates the current worktree's own index under a second key that the
first-draft fix did not recognize. The fix above closes that gap; it does not
weaken the finding's core requirement -- a *different* worktree's index
remains strict in every case tested.

**Status:** CLOSED.

## F04 -- Index output validation incomplete

**Problem:** Stage/status/resolve-undo parsing was not proven complete
against every documented record shape; some malformed or unrecognized
records could in principle be silently skipped rather than failing the whole
observation closed.

**Fix:**
- `_split_nul_terminated_records()`: output must be empty or end with the
  terminating NUL; anything else is truncated/incomplete -> `None`.
- `_parse_canonical_index_stage_record()`: exact 4-field prefix, single-letter
  recognized tag (`H`/`S`/`M`, case encodes assume-unchanged), canonical mode
  (`_INDEX_MODE_RE`), canonical full object id (`_INDEX_OBJECT_ID_RE`),
  canonical single-digit stage (`_INDEX_STAGE_RE`, rejects `"00"`), non-empty
  path, duplicate `(path, stage)` rejected.
- `_intent_to_add_paths_from_status_v2()`: every porcelain-v2 record type
  under this exact command is either fully parsed (`"1 "` ordinary, `"2 "`
  renamed/copied consuming its origPath continuation record, `"u "`
  unmerged) or the whole observation fails closed -- nothing is silently
  skipped.
- Intent-to-add cross-check: every status-reported intent-to-add path must
  have a matching effective stage-0 entry whose object id is *exactly* the
  empty-blob placeholder for the object format in use
  (`_EMPTY_BLOB_OBJECT_ID_BY_LENGTH`, selected by the entry's own observed
  id length); any disagreement -> unavailable. This is the exact mechanism
  that rejects the "impossible hybrid" case Codex originally found.
- `_parse_canonical_resolve_undo_record()`: same framing/mode/object-id/stage
  strictness.

**Status:** CLOSED.

## F05 -- Review evidence semantics inconsistent

**Problem:** Persisted evidence fields named `protected_git_metadata_preserved`
(and phase-specific equivalents) were derived from `not raw_changed_paths`,
which could disagree with the semantic authority decision.

**Fix:** Every persisted `protected_git_metadata_preserved`-shaped field (and
its phase-specific siblings: capability-probe, deterministic-check, reviewer,
overall, plus `run_loop.py`'s outer Review1/Review2
`_review_preservation_evidence()`) is now computed by calling
`protected_git_metadata_preserved()` directly -- the same function used for
the authority decision -- never re-derived from `diff_workspace_hashes`
alone. A new, clearly-separate sibling field,
`protected_git_metadata_raw_representation_preserved`, reports pure raw
byte/descriptor equality; `index_representation_diagnostic` remains the
bounded, non-authoritative diagnostic. `review_repository_identity_preserved()`
now takes canonical index identity as a required `actual_index_identity`
parameter (no internal re-measurement), so every caller measures the
canonical index identity exactly once per observation boundary and reuses
that same reading for both the authority decision and the semantic receipt
field (Section 3's "reuse, don't recompute").

**Status:** CLOSED.

## Threading / reuse (Section 3)

`run_loop.py`'s `_deep_preflight` measures `baseline_index_identity` once (at
the L1/L2 boundary, via `capability_probe_before[4]`) and reuses it for the
run's lifetime; it now also blocks PREFLIGHT with
`GIT_INDEX_IDENTITY_UNAVAILABLE` immediately if that reading is `None`,
*before* `resolve_codex_contract()` -- the first untrusted subprocess --
ever runs (Section 6). In `run_review.py`'s `main()`, `review_entry_index_identity`,
`capability_probe_index_identity`, `post_check_index_identity`, and
`review_exit_index_identity` are each measured exactly once per boundary and
threaded into every `review_repository_identity_preserved()` call and every
semantic-preservation computation via `actual_index_identity=`/
`canonical_index_identity_before=`/`canonical_index_identity_after=`. No new
evidence architecture was introduced.

## Caller audit (Section 7)

Every caller of `protected_git_metadata_preserved()` / semantic index
preservation now goes through the shared helper, never an independent local
rule:

| Boundary | File | Status |
|---|---|---|
| Deep-preflight capability boundary | `run_loop.py` `_deep_preflight` | Blocks on `None` baseline index identity before first untrusted subprocess |
| Deterministic-check boundary | `run_review.py` `main()` | `check_repository_identity_preserved` via shared helper, `actual_index_identity=post_check_index_identity` |
| Reviewer boundary | `run_review.py` `main()` | `reviewer_repository_identity_preserved` / `reviewer_metadata_semantically_preserved` via shared helper |
| Overall review preservation | `run_review.py` `main()` | `overall_metadata_semantically_preserved` via shared helper |
| Outer Review1 preservation | `run_loop.py` `_review_preservation_evidence` | `metadata_preserved` via shared helper, both fields persisted |
| Outer Review2 preservation | `run_loop.py` `_review_preservation_evidence` (same function, called per review attempt) | same |
| Final workspace evidence | `run_loop.py` `_derive_final_workspace_evidence` | Requires non-`None` `baseline_index_identity` param (test-verified) |
| Fallback final workspace evidence | `run_loop.py` `_finalize` fallback block | Inherits the same helper, unchanged call shape |

`run_repair.py` was audited and found **out of scope**: it calls
`git_index_identity(workspace) or ""` for its own, independent,
already-`_is_sha256()`-guarded purpose, and never calls
`review_repository_identity_preserved()` or `protected_git_metadata_preserved()`.
Section 7's boundary list does not name repair; left untouched.

## Constants / invariants

- `MAX_CANONICAL_INDEX_OBSERVATIONS`: 3
- `TWO_CONSECUTIVE_CANONICAL_MATCHES_REQUIRED`: YES
- `CANONICAL_IDENTITY_NON_NULL_REQUIRED`: YES
- `RAW_EQUAL_CANONICAL_DIFFERENT_REJECTED`: YES
- `RAW_EQUAL_CANONICAL_UNAVAILABLE_REJECTED`: YES
- `LINKED_WORKTREE_COMMON_INDEX_STRICT`: YES (a genuinely different
  worktree's index; the current worktree's own index remains eligible under
  both of its aliased keys)
- `STATUS_OUTPUT_FULLY_CLASSIFIED`: YES
- `STAGE_OUTPUT_FULLY_VALIDATED`: YES
- `INTENT_TO_ADD_CROSS_CHECKED`: YES
- `RESOLVE_UNDO_FULLY_VALIDATED`: YES
- `SEMANTIC_PRESERVATION_FIELD`: `protected_git_metadata_preserved` (and
  phase-specific siblings) -- reflects the authority decision
- `RAW_REPRESENTATION_FIELD`: `protected_git_metadata_raw_representation_preserved`
- `RAW_INDEX_ROLE`: SECONDARY_INTEGRITY_EVIDENCE (unchanged)
- `RUN004_RETROACTIVELY_RECLASSIFIED`: NO
- `COHERENT_BOOTSTRAP_UNCHANGED`: YES (`establish_protected_git_metadata_baseline`
  untouched; 9 focused tests pass)
- `REPAIR_AUTHORITY_UNCHANGED`: YES (`run_repair.py` untouched; full suite
  passes, 129 tests)
- `F01_PRIOR_CONTENT_GUARDS_PRESERVED`: YES (trusted timestamp/content
  guards from the earlier "F01" timestamp-instability round, e.g.
  `establish_baseline_benign_timestamp_churn_during_first_observation`,
  untouched and passing)

## Tests added

`test_run_review.py` (+26 tests, 175 -> 201):
- F01: `test_canonical_index_projection_two_matching_observations_establish_in_two`,
  `..._disagree_then_agree_establishes_on_third`,
  `..._three_disagreeing_observations_unavailable`,
  `..._incomplete_first_observation_not_authoritative_alone`,
  `..._all_incomplete_observations_unavailable`, `..._bound_is_flat_never_nested`,
  `test_build_canonical_index_projection_once_rejects_intent_to_add_hybrid`.
- F02: `test_protected_git_metadata_preserved_raw_equal_canonical_differs_rejected`,
  `..._raw_equal_canonical_unavailable_rejected`.
- F03: `test_index_metadata_exception_keys_ordinary_repository_both_aliases`,
  `..._linked_worktree_current_only`, `..._missing_role_info_applies_nothing`,
  `test_protected_git_metadata_preserved_linked_worktree_current_index_change_tolerated`,
  `..._common_index_change_rejected` (synthetic), plus two **real** linked
  worktree tests using `make_linked_workspace`:
  `test_canonical_index_identity_real_linked_worktree_tolerates_own_representation_refresh`,
  `test_canonical_index_identity_real_linked_worktree_common_dir_change_rejected`.
- F04: 9 tests covering unexpected status record, incomplete status framing,
  noncanonical stage spelling, invalid mode, invalid object id, empty path,
  incomplete stage framing, intent-to-add/stage mismatch (missing entry and
  incompatible object -- the exact "impossible hybrid"), incomplete
  resolve-undo framing. (Duplicate-record rejection was already covered by
  the prior round's `test_canonical_index_projection_blocks_on_duplicate_entry`.)

`test_run_loop.py` (+2 tests, 90 -> 92):
- F02: `test_no_untrusted_subprocess_before_index_identity_established`
  (mirrors the existing baseline-establishment test; asserts
  `GIT_INDEX_IDENTITY_UNAVAILABLE` and that Codex is never invoked).
- F05: `test_review_preservation_evidence_semantic_false_raw_representation_true`
  (raw metadata identical, canonical identity differs -> semantic field
  false, raw field true, proving the two fields are independent).
- Extended 2 existing tests
  (`test_review_preservation_evidence_tolerates_index_only_representation_change`,
  `test_review_preservation_evidence_rejects_semantic_index_drift`) with
  explicit assertions on the new
  `protected_git_metadata_raw_representation_preserved` /
  `protected_git_metadata_preserved` fields per Section 17's evidence
  consistency test.
- Fixed 2 pre-existing tests broken by the F02/F03 core changes (both were
  asserting the *old*, now-corrected, weaker behavior):
  `test_final_workspace_evidence_no_product_change` (now passes an explicit
  non-`None` `baseline_index_identity`), and
  `test_review_preservation_evidence_tolerates_index_only_representation_change`
  (now includes `@git_dir`/`@git_common_dir` role keys, required by the new
  role-aware exception).

## Full validation matrix (Section 22)

Full log: `TARGET04_CANONICAL_INDEX_AUTHORITY_CORRECTION_FULL.log` (this
task's log; Target-04A's own log was not rerun).

| Check | Result |
|---|---|
| Focused F01-F05 correction tests | PASS |
| Focused F02 preflight + F05 evidence tests | PASS |
| Canonical index identity semantic matrix (incl. Run-004-class representation lifecycle; split/sparse-index layouts are normalized identically by `git ls-files` itself, so no dedicated split/sparse fixture is needed beyond the existing canonicalization tests) | PASS (16) |
| Coherent-bootstrap regressions | PASS (9) |
| Outer Review1/Review2 preservation lifecycle | PASS (15) |
| Repair trusted-projection / expected-Review1 authority-binding regressions | PASS (7) |
| Full suite `test_run_review.py` | PASS, 201 |
| Full suite `test_run_repair.py` | PASS, 129 |
| Full suite `test_run_loop.py` | PASS, 92 |
| Full suite `tests/adapters/test_claude_code_cli.py` | PASS, 436 |
| Full repository regression `pytest tests/ -q` | PASS, 985 (unchanged root; this task did not touch `tests/`) |
| `.tomtit/dogfood/` full tree | PASS, 422 (92 + 129 + 201) |
| `compileall` | PASS |
| `ruff check` | PASS |
| `git diff --check` | PASS |

**Honest note on transient failures:** during the matrix's first pass, two
unrelated, pre-existing, real-subprocess timing tests
(`test_capability_probe_delayed_descendant_is_quiesced` in
`test_run_review.py`, `test_no_child_process_survives_a_timeout` in
`tests/adapters/test_claude_code_cli.py`) failed once each, and a plain `git
status` on the repo itself briefly hung. All three occurred only while
multiple heavy pytest subprocesses were running concurrently against the
same repository -- exactly the "timing noise" class Section 22 itself warns
about ("avoid running heavy independent suites concurrently"). Neither
failing test touches any file this task modified. Both were rerun in
isolation (pass) and both containing full suites were rerun cleanly with
nothing else running concurrently (pass, recorded in the log's section 16).
The final matrix run captured in the log above ran every full suite
sequentially with nothing concurrent, and every check passed cleanly on that
run.

## Out of scope / unchanged

- Target-04A was not rerun; Target-04B was not started.
- No real product/Claude/Codex model was invoked (all tests use fake
  executables).
- `run_repair.py` was not modified.
- No push, no tag.
