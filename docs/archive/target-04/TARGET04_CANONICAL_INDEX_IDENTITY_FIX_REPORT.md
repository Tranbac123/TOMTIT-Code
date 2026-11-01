# TARGET-04 Canonical Index Identity Fix -- Implementation Report

WORK_CLASS=RUNTIME_DESIGN_CORRECTION
CURRENT_GATE=TARGET_04

BASELINE_COMMIT=a29fddb89abda8f3571a674ab1c3cdd05c3cbf60
SOURCE_COMMIT=3f7ac1317ebb23c5ae55e24572582fa1dc9a7a0c

OLD_INDEX_INVARIANT=RAW_PROTECTED_INDEX_DESCRIPTOR_LOAD_BEARING
NEW_INDEX_INVARIANT=CANONICAL_EFFECTIVE_INDEX_IDENTITY_LOAD_BEARING
RAW_INDEX_ROLE=SECONDARY_INTEGRITY_EVIDENCE

## 1. Problem (Run-004 design result)

Run 004 proved deep preflight, Attempt 1, and Review 1 all work, and that
Codex can produce a genuine, unmanufactured `REQUEST_CHANGES` verdict with
real findings. The run still failed closed: the outer pre-Review1/
post-Review1 protected-metadata comparison in `run_loop.py` saw
`.git_dir/index` and `.git_common_dir/index` differ and treated that as
repository drift, even though `SEMANTIC_INDEX_IDENTITY_CHANGED=NO` and
`INDEX_CLEAN_STATE_CHANGED=NO` were independently proven by the (unextended)
`git_index_identity()` check that was already running alongside it. This is
Git's own routine, harmless stat-cache-refresh behavior: the first `git`
command to inspect a tree whose tracked file mtime changed since the index
was last written rewrites the index's physical bytes (dev/ino/size/nlink/
sha256 can all change via Git's lock-file-then-rename write strategy) with
no change to any repository-relevant meaning -- and this is *guaranteed* to
happen shortly after any implementation attempt touches an allowed file.

`RAW_INDEX_REPRESENTATION_CHANGED` was `UNKNOWN` in Run 004 (the complete
BEFORE descriptor was not persisted), so this report does not assume
exactly what changed in that specific run -- only that raw-vs-canonical
divergence is a real, reproducible phenomenon (verified directly against a
real Git repository; see Section 3).

## 2. Canonical effective-index identity

`git_index_identity()` (previously `git ls-files --stage -v -z`, hashed as
text -- classified PARTIAL by the design review: it lacked skip-worktree in
isolation from assume-unchanged, intent-to-add, and resolve-undo) is now
built from a new `canonical_index_projection()`:

`CURRENT_INDEX_IDENTITY_EXTENDED=YES`

- **Per index entry**: exact path, stage, Git mode, full object id, and
  assume-unchanged/skip-worktree/intent-to-add state.
  - `BYTE_SAFE_PATHS=YES` -- built from `-z`-terminated, byte-mode
    subprocess output via a new narrow `_run_git_bytes()` helper (text-mode
    `_run_git()` is untouched and still used everywhere else); paths are
    keyed by their exact byte sequence (`.hex()`), never decoded, so no
    path byte sequence is lost or collapses with another.
  - `SKIP_WORKTREE_COVERED=YES`, `ASSUME_UNCHANGED_COVERED=YES` -- from
    `git ls-files --stage -t -v -z`'s tag-letter + case encoding (`H`/`S`
    identify skip-worktree independent of case; lowercase independently
    marks assume-unchanged -- verified directly that all four
    combinations produce four distinct, correct tags).
  - `INTENT_TO_ADD_COVERED=YES` -- `ls-files -t -v` cannot distinguish an
    intent-to-add entry (empty-blob placeholder) from a genuinely empty
    tracked file, so this is recovered separately from
    `git status --porcelain=v2`'s documented, stable plumbing format (an
    ordinary changed entry with `mI == mH == 000000`), never the
    explicitly unstable `--debug` output.
- **Per resolve-undo record** (`git ls-files --resolve-undo -z`):
  `RESOLVE_UNDO_COVERED=YES`.
- **Split/sparse index**: no extra handling needed -- `git ls-files`
  already resolves ordinary/split/sparse physical layouts into one logical
  listing (no `--sparse` flag is passed, so sparse directories are always
  expanded), so equivalent effective states hash identically by
  construction.
- **Fails closed**: `UNKNOWN_INDEX_STATE_BLOCKS=YES` -- any unparseable
  record, an unrecognized tag letter (only `H`/`S`/`M` are ever valid for a
  plain `--stage` listing), a malformed mode/object-id/stage, or a
  duplicate (path, stage) pair all return `None`, never a guess.
- **Same-observation consistency**: the three underlying git invocations
  run back-to-back with no intervening work, the most coherent boundary a
  CLI-based query can practically offer, documented in
  `canonical_index_projection()`'s docstring.

## 3. Reproduction (verified against a real repository, not simulated)

```
canonical_index_identity_before = <hash>
os.utime(tracked_file, future_mtime)   # Attempt-1-equivalent change
git status                              # first inspection -> index rewritten
raw .git/index bytes: before != after   # confirmed
canonical_index_identity_after  = <hash>  # == before
```

`test_canonical_index_identity_tolerates_git_stat_cache_refresh` and
`test_protected_git_metadata_preserved_tolerates_index_only_representation_change`
perform exactly this against a real Git repository -- not synthetic data.

## 4. Where index-tolerant comparison replaces blunt equality

New `protected_git_metadata_preserved(before, after, *,
canonical_index_identity_before, canonical_index_identity_after)`: tolerates
a raw difference confined *exactly* to `git_dir/index`/`git_common_dir/index`
only when the canonical index identity is proven unchanged; any other
protected key changing, or the canonical identity itself changing, still
fails closed. Threaded into:

- `run_review.review_repository_identity_preserved()` (now takes full
  projections, not pre-hashed strings) and its four call sites: capability
  probe, deterministic check, reviewer, overall.
- `run_loop.py` L2/L3 capability-probe boundary (`_deep_preflight`).
- `run_loop._review_preservation_evidence()` -- **the exact Run-004 path**
  (outer pre-Review1 -> Review1 child -> outer post-Review1), also used for
  Review 2.
- `run_loop._derive_final_workspace_evidence()` and its fallback path (the
  terminal `protected_git_metadata_preserved` result field) -- a new
  `baseline_index_identity`, anchored at the same L1/L2 authority boundary
  as `baseline_git_metadata` (captured once, right after L1 establishes,
  from L2's own canonical-index reading), is threaded through `_finalize`/
  `_finalize_current` alongside it.

`EQUIVALENT_REPRESENTATION_ACCEPTED=YES`, `SEMANTIC_INDEX_CHANGE_REJECTED=YES`
(Section 7: non-index protected metadata -- HEAD, refs, config, gitdir/
common-dir role identity -- keeps its exact prior strict rules; verified by
`test_protected_git_metadata_preserved_rejects_non_index_key_change`).

## 5. Observability improvement (Section 15, narrow)

New `describe_index_representation_diff(before, after)`: when an index-only
raw difference is tolerated, reports exactly which of
dev/ino/mode/size/nlink/sha256 changed as bounded booleans -- never an
unbounded dump. Surfaced as a new `index_representation_diagnostic` field
in `_review_preservation_evidence()`'s output.

## 6. Tests

| Requirement | Test | Result |
|---|---|---|
| Sec. 10 positive (real Git reproduction) | `test_canonical_index_identity_tolerates_git_stat_cache_refresh` | PASS |
| Sec. 10 positive (end-to-end preserved) | `test_protected_git_metadata_preserved_tolerates_index_only_representation_change` | PASS |
| Sec. 11.A path added | `test_canonical_index_identity_changes_when_path_added` | PASS |
| Sec. 11.B path removed | `test_canonical_index_identity_changes_when_path_removed` | PASS |
| Sec. 11.C object id changed | `test_canonical_index_identity_changes_when_staged_object_id_changes` | PASS |
| Sec. 11.D mode changed | `test_canonical_index_identity_changes_when_mode_changes` | PASS |
| Sec. 11.E stage changed (real merge conflict) | `test_canonical_index_identity_changes_with_unmerged_stages` | PASS |
| Sec. 11.F assume-unchanged | `test_canonical_index_identity_changes_with_assume_unchanged_toggle` | PASS |
| Sec. 11.G skip-worktree | `test_canonical_index_identity_changes_with_skip_worktree_toggle` | PASS |
| Sec. 11.H intent-to-add | `test_canonical_index_identity_changes_with_intent_to_add` | PASS |
| Sec. 11.I resolve-undo (real conflict resolution) | `test_canonical_index_identity_changes_with_resolve_undo_state` | PASS |
| Sec. 11.J path bytes (rename) | `test_canonical_index_identity_changes_when_path_bytes_change` | PASS |
| Sec. 11.K unclassified tag blocks | `test_canonical_index_projection_blocks_on_unclassified_tag` | PASS |
| Sec. 11.K duplicate entry blocks | `test_canonical_index_projection_blocks_on_duplicate_entry` | PASS |
| Byte-safety (non-UTF8 path) | `test_canonical_index_projection_is_byte_safe_for_non_utf8_paths` | PASS |
| Sec. 7 non-index key still strict | `test_protected_git_metadata_preserved_rejects_non_index_key_change` | PASS |
| Sec. 6 canonical identity itself must match | `test_protected_git_metadata_preserved_rejects_when_canonical_index_identity_differs` | PASS |
| `protected_git_metadata_preserved` baseline cases | `test_protected_git_metadata_preserved_true_when_nothing_changed`, `test_protected_git_metadata_preserved_false_on_empty_snapshot` | PASS |
| Sec. 15 bounded diagnostic | `test_describe_index_representation_diff_reports_bounded_fields`, `..._none_on_non_matching_descriptor` | PASS |
| Sec. 8 outer Review1 lifecycle, positive | `test_review_preservation_evidence_tolerates_index_only_representation_change` | PASS |
| Sec. 8 outer Review1 lifecycle, semantic drift still blocks | `test_review_preservation_evidence_rejects_semantic_index_drift` | PASS |
| Sec. 8 outer, non-index drift alongside index drift still blocks | `test_review_preservation_evidence_rejects_non_index_drift_alongside_index_drift` | PASS |
| Sec. 16 full lifecycle (real `git status` refresh, real Review1 child) | `test_target04_canonical_index_identity_lifecycle_tolerates_natural_refresh` | PASS -- reaches `terminal_outcome=COMPLETED`, `final_verdict=APPROVE`, with a real observed raw-index difference (`protected_git_metadata_changed_path_count >= 1`) correctly tolerated |
| Sec. 11.L/M (special Git operation state, HEAD/baseline drift) | pre-existing regressions (`test_deterministic_check_repository_drift_stops_before_reviewer`, `test_repair_rejects_reviewed_head_and_baseline_ref_drift`, and others) | PASS (rerun, unmodified) |

## 7. Validation matrix

Full output in `TARGET04_CANONICAL_INDEX_IDENTITY_FIX_FULL.log` (repo root,
no `tee`). All steps exited 0:

1. Focused canonical index identity tests: 21/21 passed.
2. Focused outer Review1 preservation lifecycle tests: 4/4 passed.
3. Focused coherent-bootstrap regressions: 9/9 passed.
4. Focused F01/trusted-content-proof regressions: 26/26 passed.
5. Focused repair trusted-projection + authority-binding regressions: 17/17 passed.
6. Full suite `test_run_review.py`: 175 collected, all passed.
7. Full suite `test_run_repair.py`: 129 collected, all passed.
8. Full suite `test_run_loop.py`: 90 collected, all passed.
9. Full suite `tests/adapters/test_claude_code_cli.py`: 436 collected, all passed.
10. Full repository regression `pytest tests/ -q`: 1006 collected, all passed.
11. `compileall` on all 4 modified files: clean.
12. `ruff check` on all 4 modified files: clean (one round of dead-variable
    cleanup was required after the `review_repository_identity_preserved`
    signature change made three `*_git_metadata_hash` locals unused; fixed
    and rechecked before the final matrix run).
13. `git diff --check`: clean.

## 8. Preserved contracts

`COHERENT_BOOTSTRAP_UNCHANGED=YES` -- `establish_protected_git_metadata_baseline()`,
`MAX_INITIAL_BASELINE_OBSERVATIONS=3`, two-consecutive-match establishment,
L1/L2/L3 lifecycle modes untouched; all 9 focused coherent-bootstrap tests
pass unmodified.

`REPAIR_AUTHORITY_UNCHANGED=YES` -- Review1 exact projection persistence,
repair trusted projection, expected-Review1-id binding, modern/legacy
evidence classification, "repair never bootstraps" all untouched; all 17
focused repair regressions pass unmodified.

`F01_REMAINS_CLOSED=YES` -- `protected_git_metadata_snapshot()`'s own
strict-validation contract (trusted-baseline-gated timestamp tolerance,
fails closed with no trusted evidence) is completely unchanged; only its
*callers'* cross-phase comparison of two already-measured projections gained
the narrow index exemption. All 26 focused F01 regressions pass unmodified.

`RUN004_RETROACTIVELY_RECLASSIFIED=NO` -- Run 004 remains on record exactly
as reported: `TERMINAL_OUTCOME=FAILED`, `TRUSTED_REVIEW1_VERDICT_ACCEPTED=NO`,
`NATURAL_REPAIR_TRIGGER_PROOF=NOT_OBSERVED`. This correction applies
prospectively to the next run only.

## 9. Scope

Modified exactly `.tomtit/dogfood/run_review.py`, `.tomtit/dogfood/run_loop.py`,
and their two directly corresponding test files, per Section 17. No
receipt/evidence schema file exists separately from these two modules.
Target-04 was not touched, not rerun. No real product models were invoked;
all tests use direct unit-level calls against throwaway Git repositories or
fake executables.

`TARGET04_MODIFIED=NO`
`TARGET04_RERUNS=0`
`TARGET04B_STARTED=NO`

## 10. Commits

1. `3f7ac1317ebb23c5ae55e24572582fa1dc9a7a0c` -- source + tests (narrow, single commit).
2. This report (separate commit, immediately following).

No review-report commit, no tag, no push.

`NEXT_ACTION=CODEX_REVIEWS_CANONICAL_INDEX_IDENTITY_FIX`
