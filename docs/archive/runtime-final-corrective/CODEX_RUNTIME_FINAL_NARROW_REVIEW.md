# Runtime Final Narrow Software Correctness Review

Verdict: **APPROVE**

Frozen baseline: `a1573bdf3fc8f4adf48f46c96dcf1e3eeba1f7ee`

Review date: 2026-08-11 (Asia/Ho_Chi_Minh)

This was a read-only review of the four Owner-bounded acceptance areas. No source,
test, or documentation file was modified. No real provider or real E2E was invoked.
The only review artifacts created are this report and
`CODEX_RUNTIME_FINAL_NARROW_REVIEW_FULL.log`.

## Blocking findings

None.

## 1. Linked-worktree root `.git` identity — PASS

The implementation now treats the root `.git` entry as an explicit repository
identity component rather than product state:

- `snapshot_workspace_hashes()` excludes a linked-worktree root `.git` regular
  indirection file (`src/tomtit/adapters/claude_code_cli.py:1382-1395`).
- `_workspace_git_entry_descriptor()` measures the root entry without following
  aliases and accepts only a real directory or regular file
  (`.tomtit/dogfood/run_review.py:533-560`). Its regular-file identity binds device,
  inode, type/mode, size, link count, mtime, ctime, and content SHA-256.
- `protected_git_metadata_snapshot()` separately binds the root entry, Git-resolved
  per-worktree git-dir, and Git-resolved common-dir, then revalidates both the root
  entry and resolved paths after measuring the metadata trees
  (`.tomtit/dogfood/run_review.py:581-616`).
- Repair requires all 13 modern identity fields, including
  `reviewed_workspace_git_entry_hash`; partial or malformed modern evidence fails
  closed. It compares the live root-entry hash independently of product and
  authorized-side-effect state (`.tomtit/dogfood/run_repair.py:520-624`). Genuine
  historical evidence with none of those fields retains the strict whole-workspace
  fallback, including the historical linked-worktree `.git` content hash.
- Terminal evidence measures the live repository snapshot and emits separate
  `workspace_git_entry_*` preservation fields without adding `.git` to product
  paths (`.tomtit/dogfood/run_loop.py:173-271`).

Focused mocked/temp-repository coverage passed for ordinary `.git` directories;
unchanged linked gitfiles; content, bound-mode/identity, and type changes; Review,
Repair, and terminal rejection/reporting; non-product classification; complete
modern evidence; incomplete modern fail-closed behavior; and strict legacy fallback.

## 2. Exact validated evidence bytes — PASS

The canonical reader returns one `CanonicalEvidenceSnapshot` containing both the
validated identity and bytes captured from the same safely opened descriptors
(`.tomtit/dogfood/run_review.py:307-312,438-460`). Load-bearing consumers parse
those bytes directly:

- implementation receipt/result/patch:
  `.tomtit/dogfood/run_review.py:891-928`;
- Review result/receipt/checks/prompt:
  `.tomtit/dogfood/run_repair.py:146-189`;
- Loop child JSON: `.tomtit/dogfood/run_loop.py:153-164`.

No validate-then-`Path.read_text()`/`Path.read_bytes()` second pathname reopen
remains in those canonical consumers. Focused tests replaced the pathname after
capture and proved business parsing still used the previously validated bytes.
Normal and malformed JSON semantics and valid legacy evidence remained intact.

## 3. Post-read filesystem identity revalidation — PASS

`_regular_file_measurement()` performs the required single-measurement chain:
initial non-following metadata, `O_NOFOLLOW` open, pre-read `fstat`, exact read,
post-read `fstat`, byte-count check, final pathname `lstat`, and comparison proving
the pathname still names the same regular object (`.tomtit/dogfood/run_review.py:
231-304`). Load-bearing identity includes mtime and ctime as well as device, inode,
mode/type, size, and link count (`.tomtit/dogfood/run_review.py:208-219`).

The tree reader also rejects symlinks and special entries, applies the V2 canonical
regular-file link-count invariant (`st_nlink == 1`), and revalidates every visited
directory after traversal (`.tomtit/dogfood/run_review.py:315-401`). Focused tests
passed for unchanged files, deterministic same-size in-place mutation, pathname
replacement while the old inode remained open/readable, symlink substitution,
directory aliases, hard links, and special entries. An additional parent-directory
alias-swap probe failed closed on the final directory identity comparison.

## 4. Authorized-side-effect Review to Repair regression — PASS

The original local/mocked scenario reached the Repair worker boundary when Review's
deterministic pytest-like check changed only an explicitly authorized generated
side-effect path. Repair did not misclassify that drift as product/repository
identity drift. Focused regressions also passed for source drift rejection,
unexpected ignored drift rejection, reviewer mutation rejection, exact source
authorization, and partial/malformed modern identity rejection.

## Evidence, terminal, R-04, and lifecycle regression checks — PASS

Receiptless Repair, actual prior-evidence mutation, live terminal source-path
measurement, authorized-side-effect-only terminal classification, unexpected final
drift, linked gitfile repository-only drift, and child-receipt-independent terminal
measurement all passed their focused regression selection.

R-04 regressions passed: pre-provider failure does not claim invocation; positively
started provider processes remain observed across timeout/nonzero outcomes; missing
or contradictory evidence remains UNKNOWN; capability probes remain excluded; and
assignment-slot `implementation_attempts` remains distinct from
`model_invocation_count`.

The Runtime V0 documentation accurately bounds the guarantee to the process group
Runtime creates and manages and explicitly excludes independently detached sessions
(`docs/MVP_EXECUTION_POLICY_V0.md:758-778`). Review and adapter implementations use
bounded TERM/KILL escalation, positive group-absence checks, and fail closed on
unknown/non-quiescent state (`.tomtit/dogfood/run_review.py:1233-1269` and
`src/tomtit/adapters/claude_code_cli.py:2031-2085`). Intentionally detached-session
containment was not treated as a blocker, per the Owner boundary.

One exact full-suite run outside the sandbox transiently reported the
normal-parent-exit/surviving-descendant test as `FAILED` after the descendant had
already been confirmed absent; the status had conservatively failed closed because
positive quiescence was not established within that run's bounded timing window.
The same case passed in the complete affected suite, then passed 10/10 isolated
repetitions, and the unchanged exact full suite passed all 985 tests on rerun. This
was not reproducible and does not establish a correctness defect in the bounded
acceptance areas.

## Validation results

- Focused linked-worktree selection: 14 passed.
- Focused canonical-read selection: 14 passed.
- Focused original Review-to-Repair selection: 12 passed.
- Focused R-01/R-02/R-03/R-04/lifecycle selection: 19 passed.
- Complete affected suites: 728 collected, 100% passed, exit 0.
- Exact full repository regression: 985 collected, 100% passed on unchanged rerun,
  exit 0.
- Initial in-sandbox exact run preserved the known
  `OMP: Error #179: Function Can't open SHM failed`; the identical command was run
  outside the sandbox using existing Owner approval.
- `/usr/local/bin/python3 -m compileall -q src tests .tomtit/dogfood tomtit_cli.py`:
  PASS (bytecode cache redirected outside the repository).
- `/usr/local/bin/python3 -m ruff check .`: PASS.
- `git diff --check`: PASS.

Full command output is preserved in `CODEX_RUNTIME_FINAL_NARROW_REVIEW_FULL.log`.

## Implementation hash preservation

The final SHA-256 values were byte-identical to those recorded before review:

| File | SHA-256 |
|---|---|
| `.tomtit/dogfood/run_loop.py` | `9e851ec49543af06f91c155bea5fe27e72524d94a7165ad78ceaeab8588b21b3` |
| `.tomtit/dogfood/run_repair.py` | `1fb12f4e0fecc848dbfb329ff6a0ae6c6cb8d26bb0766ecfe199b52a0838813d` |
| `.tomtit/dogfood/run_review.py` | `3a809730e332e8038d55815b7ae3df8582a70dc7200c4a2ac5a2c26087e32831` |
| `.tomtit/dogfood/run_task.py` | `a966a2712dfa226f8a959a4bc3e7ac5c016dfeaa81a29230b78043ce756895c6` |
| `.tomtit/dogfood/test_run_loop.py` | `91f95a01a05db79a355904d85b62d4f5c3d89a2b39c9f98e27b9b36289931e82` |
| `.tomtit/dogfood/test_run_repair.py` | `95ff5a8c6bf175b8dce0f3d36d3413ed7b51feca88058f50f86805782fbfbf08` |
| `.tomtit/dogfood/test_run_review.py` | `b4824626a0d819f2f81b8ecfbaa16e4b13111323a2833485ecb7c353a112d583` |
| `docs/MVP_EXECUTION_POLICY_V0.md` | `f339c329ef24a35c6209ef217246b7dc482b2b369bb934dfcd626ca70f520861` |
| `src/tomtit/adapters/claude_code_cli.py` | `4970beabd05cf1fc34c1a54447e7c90e6813869134b6b445be1ca6e323be457e` |
| `tests/adapters/test_claude_code_cli.py` | `7659adbfb7064936e95ad3360074872262b4b8b508d7f345d9a1b7ca15e40460` |

The protected Decision Evaluation V0 and Product CLI V0 pathsets have no diff from
the frozen baseline. Target-03 and personal-web were not touched.

## Required result fields

```text
VERDICT=APPROVE

LINKED_WORKTREE_GITFILE=PASS
ORDINARY_REPOSITORY=PASS
LINKED_WORKTREE_REVIEW=PASS
LINKED_WORKTREE_REPAIR=PASS
LINKED_WORKTREE_TERMINAL=PASS
GITFILE_NOT_PRODUCT_STATE=PASS
MODERN_LEGACY_BOUNDARY=PASS

VALIDATED_EVIDENCE_BYTES=PASS
REVIEW_EXACT_BYTES=PASS
REPAIR_EXACT_BYTES=PASS
LOOP_EXACT_BYTES=PASS
SECOND_PATH_REOPEN=NONE

POST_READ_REVALIDATION=PASS
SAME_SIZE_MUTATION=PASS
PATH_REPLACEMENT=PASS
FINAL_PATH_OBJECT_MATCH=PASS
SYMLINK_POLICY=PASS
HARDLINK_POLICY=PASS

ORIGINAL_E2E_REPRO=PASS
AUTHORIZED_SIDE_EFFECT_REPAIR=PASS
SOURCE_DRIFT_REJECTED=PASS
UNEXPECTED_DRIFT_REJECTED=PASS

EVIDENCE_IMMUTABILITY_REGRESSION=PASS
FINAL_WORKSPACE_REGRESSION=PASS
R04_MODEL_EVENT_REGRESSION=PASS

PROCESS_LIFECYCLE_CONTRACT=PASS
DETACHED_SESSION_CONTAINMENT=OUT_OF_SCOPE_BY_OWNER

FOCUSED_TESTS=PASS
AFFECTED_TESTS=PASS
FULL_REGRESSION=PASS
FULL_REGRESSION_COUNT=985
COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS

IMPLEMENTATION_HASHES_PRESERVED=YES

DECISION_EVAL_MODIFIED=NO
CLI_V0_MODIFIED=NO
TARGET03_MODIFIED=NO
PERSONAL_WEB_TOUCHED=NO

REAL_MODEL_INVOCATIONS=0
COMMIT_CREATED=NO
PUSHED=NO

NEXT_ACTION=OWNER_FREEZES_RUNTIME_CORRECTIVE_OR_FIXES_ONE_REPRODUCED_BLOCKER
```

## Freeze-binding attestation

The earlier **APPROVE** verdict reviewed exact working-tree bytes.

`TARGET_SOURCE_COMMIT` was created after that review.

This attestation proves that `TARGET_SOURCE_COMMIT` contains those exact approved
bytes and that the committed-state validation contract has now been completed.

This is a provenance-binding attestation, not a new general correctness review.

```text
target_source_commit = 2e0560886b60870267fea25c20ef6f2466338680

reviewed_diff_range = a1573bdf3fc8f4adf48f46c96dcf1e3eeba1f7ee..2e0560886b60870267fea25c20ef6f2466338680

implementation_report_commit = febc6c0b7180b78ff10eb26ae5dbef592007e01f

review_report_expected_parent = febc6c0b7180b78ff10eb26ae5dbef592007e01f

committed_source_byte_equivalence = PASS
committed_state_validation_result = PASS
focused_validation_result = PASS
source_tree_unchanged_after_validation = PASS
lineage_correct = PASS
protected_scope_preserved = PASS
```

### Provenance and committed-state bindings

- Direct-parent lineage was verified as
  `a1573bdf3fc8f4adf48f46c96dcf1e3eeba1f7ee` →
  `771b8bca67779c4e0e6ba18e3d3dfe7e59f2f686` →
  `2e0560886b60870267fea25c20ef6f2466338680` →
  `febc6c0b7180b78ff10eb26ae5dbef592007e01f`.
- The policy commit contains only `docs/MVP_EXECUTION_POLICY_V0.md`; the target
  source commit contains only the nine authorized Runtime source/test files; the
  implementation-report commit contains only
  `RUNTIME_FINAL_BOUNDED_CORRECTIVE_REPORT.md`.
- All ten blobs at the target source commit match the approved SHA-256 manifest
  recorded earlier in this report. The policy blob at the policy and target source
  commits is identical and has SHA-256
  `f339c329ef24a35c6209ef217246b7dc482b2b369bb934dfcd626ca70f520861`.
  The nine source/test paths at the policy commit are unchanged from the baseline.
- `RUNTIME_COMMITTED_STATE_VALIDATION_FULL.log` was independently identified as
  SHA-256 `6c746fe915a39a969dfa66da4988b6a1422c072804636e2afc2675c80660e993`,
  size 3737 bytes. It records 728/728 affected tests and 985/985 full-regression
  tests passing, with compileall, Ruff, and both diff checks passing.
- The implementation report remains historically accurate that its own stage left
  the exact focused selectors unproven. Codex independently reconstructed and ran
  the exact accepted selections afterward, closing that final validation gap.
- The approved source/test/policy hashes were rechecked after focused validation
  and remained unchanged. Decision Evaluation V0 and Product CLI V0 have no diff
  from the frozen baseline; Target-03 and personal-web were not touched.

### Exact focused selections and results

Linked-worktree `.git` selection — **14 passed**:

```text
.tomtit/dogfood/test_run_review.py::test_protected_git_metadata_identity_binds_ordinary_git_directory
.tomtit/dogfood/test_run_review.py::test_linked_worktree_root_gitfile_unchanged_is_bound
.tomtit/dogfood/test_run_review.py::test_linked_worktree_root_gitfile_content_change_is_detected
.tomtit/dogfood/test_run_review.py::test_linked_worktree_root_gitfile_mode_change_is_detected
.tomtit/dogfood/test_run_review.py::test_linked_worktree_root_gitfile_replacement_type_change_is_detected
.tomtit/dogfood/test_run_review.py::test_capability_probe_linked_worktree_gitfile_mutation_is_rejected
.tomtit/dogfood/test_run_review.py::test_reviewer_linked_worktree_gitfile_mutation_is_rejected
.tomtit/dogfood/test_run_review.py::test_linked_worktree_review_receipt_binds_gitfile_identity
.tomtit/dogfood/test_run_repair.py::test_repair_rejects_linked_worktree_gitfile_drift_after_review
.tomtit/dogfood/test_run_repair.py::test_linked_modern_review_missing_gitfile_identity_fails_closed
.tomtit/dogfood/test_run_repair.py::test_linked_legacy_review_without_modern_identity_retains_fallback
.tomtit/dogfood/test_run_loop.py::test_terminal_evidence_reports_linked_worktree_gitfile_drift_separately
.tomtit/dogfood/test_run_loop.py::test_loop_review_preservation_detects_linked_worktree_gitfile_drift
tests/adapters/test_claude_code_cli.py::test_linked_worktree_root_gitfile_is_not_product_state
```

Canonical-evidence selection — **14 passed**:

```text
.tomtit/dogfood/test_run_review.py::test_read_canonical_evidence_accepts_unchanged_regular_artifact
.tomtit/dogfood/test_run_review.py::test_read_canonical_evidence_rejects_same_size_mutation_during_read
.tomtit/dogfood/test_run_review.py::test_read_canonical_evidence_rejects_pathname_replacement_during_read
.tomtit/dogfood/test_run_review.py::test_implementation_loader_parses_captured_bytes_without_path_reopen
.tomtit/dogfood/test_run_repair.py::test_valid_request_changes_with_one_medium_finding
.tomtit/dogfood/test_run_repair.py::test_review_loader_parses_captured_bytes_without_path_reopen
.tomtit/dogfood/test_run_repair.py::test_malformed_review_json_rejected
.tomtit/dogfood/test_run_repair.py::test_same_content_receipt_symlink_substitution_is_rejected
.tomtit/dogfood/test_run_loop.py::test_evidence_tree_regular_file_content_and_missing_member_are_bound
.tomtit/dogfood/test_run_loop.py::test_same_content_external_symlink_substitution_is_rejected
.tomtit/dogfood/test_run_loop.py::test_special_evidence_member_is_rejected
.tomtit/dogfood/test_run_loop.py::test_symlinked_evidence_directory_is_rejected
.tomtit/dogfood/test_run_loop.py::test_hardlinked_evidence_member_is_rejected
.tomtit/dogfood/test_run_loop.py::test_child_json_reader_parses_captured_bytes_without_path_reopen
```

Authorized-side-effect Review→Repair selection — **12 passed** from nine exact
node selectors (the lone-current-identity node expands to four parametrized cases):

```text
.tomtit/dogfood/test_run_loop.py::test_authorized_ignored_check_side_effect_drift_allows_repair
.tomtit/dogfood/test_run_repair.py::test_partitioned_review_identity_allows_authorized_side_effect_drift
.tomtit/dogfood/test_run_repair.py::test_partitioned_review_identity_rejects_source_drift
.tomtit/dogfood/test_run_repair.py::test_partitioned_review_identity_rejects_ignored_unexpected_drift
.tomtit/dogfood/test_run_loop.py::test_source_mutation_during_review_is_rejected
.tomtit/dogfood/test_run_repair.py::test_source_portion_must_equal_reviewed_receipt_source_paths
.tomtit/dogfood/test_run_repair.py::test_partial_partitioned_review_identity_fails_closed
.tomtit/dogfood/test_run_repair.py::test_any_lone_current_review_identity_field_fails_closed
.tomtit/dogfood/test_run_repair.py::test_malformed_partitioned_review_hash_fails_closed
```

R-01/R-02/R-03/R-04/lifecycle selection — **19 passed**:

```text
.tomtit/dogfood/test_run_loop.py::test_receiptless_repair_preflight_failure_keeps_terminal_evidence_truthful
.tomtit/dogfood/test_run_loop.py::test_missing_or_contradictory_provider_evidence_is_unknown
.tomtit/dogfood/test_run_loop.py::test_final_workspace_evidence_separates_authorized_side_effect_only_drift
.tomtit/dogfood/test_run_loop.py::test_final_workspace_evidence_detects_ignored_unexpected_change
.tomtit/dogfood/test_run_loop.py::test_prior_evidence_mutation_is_reported_false
.tomtit/dogfood/test_run_loop.py::test_failed_repair_with_only_authorized_side_effect_has_no_product_paths
.tomtit/dogfood/test_run_loop.py::test_failed_repair_reports_ignored_unexpected_final_change
.tomtit/dogfood/test_run_review.py::test_provider_observer_is_false_only_when_codex_process_does_not_start
.tomtit/dogfood/test_run_review.py::test_provider_observer_stays_true_after_post_start_oserror
.tomtit/dogfood/test_run_review.py::test_capability_probe_delayed_descendant_is_quiesced
.tomtit/dogfood/test_run_review.py::test_deterministic_check_delayed_descendant_is_quiesced
.tomtit/dogfood/test_run_review.py::test_descendant_ignoring_sigterm_is_killed
.tomtit/dogfood/test_run_review.py::test_quiescence_unknown_fails_closed
tests/adapters/test_claude_code_cli.py::test_success_with_expected_file_change
tests/adapters/test_claude_code_cli.py::test_nonzero_exit_never_succeeds
tests/adapters/test_claude_code_cli.py::test_timeout_terminates_the_process
tests/adapters/test_claude_code_cli.py::test_unavailable_when_executable_not_discovered
tests/adapters/test_claude_code_cli.py::test_process_group_exit_during_cleanup_signal_is_quiescent
```

The focused validation output was retained temporarily outside the repository at
`/private/tmp/CODEX_RUNTIME_FREEZE_BINDING_FOCUSED_FULL.log`; it has SHA-256
`18f170872f7dde1a199863ea5b6d2edeb78a12cb7d965ebcb4fde4d80addcc9e`
and size 320 bytes. It is not part of the review-report commit or canonical durable
gate evidence.

```text
FREEZE_BINDING_ATTESTATION=PASS
FOCUSED_LINKED_WORKTREE=14/PASS
FOCUSED_CANONICAL_READ=14/PASS
FOCUSED_REVIEW_REPAIR=12/PASS
FOCUSED_R01_R04_LIFECYCLE=19/PASS
FOCUSED_VALIDATION=PASS
AFFECTED_TESTS=PASS
AFFECTED_TEST_COUNT=728
FULL_REGRESSION=PASS
FULL_REGRESSION_COUNT=985
COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS
SOURCE_TREE_UNCHANGED=YES
LINEAGE_CORRECT=YES
PROTECTED_SCOPE_PRESERVED=YES
NEXT_ACTION=CLAUDE_CODE_SONNET_FINALIZES_GATE_EVIDENCE_AND_CREATES_ANNOTATED_TAG
```
