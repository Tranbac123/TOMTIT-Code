# Target-04 trusted pre-instability content proof fix report

```text
STATUS=PASS
WORK_CLASS=DEFECT
GATE=TARGET_04

RUN002_EXACT_CAUSE=PARTIAL
REPRODUCED_FAILURE_MECHANISM=CONFIRMED

BLOCKING_FINDING_CLOSED=F01_SAME_MEASUREMENT_CONTENT_MUTATION_PROOF_GAP

INITIAL_SNAPSHOT_TIMESTAMP_INSTABILITY=FAIL_CLOSED
TRUSTED_PRIOR_CONTENT_REQUIRED=YES
UNSTABLE_SHA_SELF_BASELINE_ALLOWED=NO
WHOLE_SNAPSHOT_RETRY=YES
GLOBAL_RETRY_LIMIT=3
```

## Root cause of the reproduced gap

The prior fix (source commit `d0ca9b4a6e77ecdaa215d6148cbbd16cacb91bbe`) let a
timestamp-only regular-file instability retry the whole protected snapshot,
validating a *later* attempt's reading against the *first unstable
attempt's own reading* (`observed_instabilities`, populated only from
within the same `protected_git_metadata_snapshot()` call). Codex
reproduced the resulting gap deterministically: if content mutates
same-size, same-inode, same-mode from A to B immediately before the very
first read, the first "unstable" attempt itself only ever observes B --
its own SHA(B) becomes the recorded baseline, and a later coherent re-read
of the (now-permanent) B trivially satisfies `SHA(B) == SHA(B)`. The whole
point of the check -- proving the reading matches what existed *before*
the instability -- was never actually enforced, because the only evidence
compared against was itself produced *during* the current call, after the
mutation had already happened. An unstable attempt can prove what it read;
it cannot prove what existed before it started reading.

## Design: trusted evidence must come from outside the current call

### 1. `TrustedRegularFileEvidence` (new)

A small frozen dataclass in `.tomtit/dogfood/run_review.py` binding, for
one regular file: `dev`, `ino`, `mode` (permission bits only, matching the
descriptor grammar's `stat.S_IMODE(...)` convention), `size`, `nlink`,
`sha256`. No `mtime_ns`/`ctime_ns` -- those are exactly what a
timestamp-only instability is allowed to differ on. `.matches(...)`
compares all six fields.

`_parse_trusted_file_descriptor()` builds this safely from a descriptor
*string*, using a new `_STABLE_FILE_DESCRIPTOR_RE` -- the exact grammar
`_strip_volatile_timestamp_fields` actually produces for a `file:`
descriptor (i.e. `_CANONICAL_FILE_DESCRIPTOR_RE` with the
`mtime_ns=...:ctime_ns=...` segment entirely absent, never merely
optional). This distinction mattered in practice: an earlier draft reused
`_CANONICAL_FILE_DESCRIPTOR_RE` directly and every trusted-baseline lookup
silently failed to parse, since a `trusted_baseline` value is always the
*return value* of a prior coherent `protected_git_metadata_snapshot()`
call -- already stable-projected, never the raw canonical form. Caught and
fixed via a direct reproduction script before writing any test.
`fullmatch()`-anchored parsing is used throughout, matching the existing
approved, exact canonical-grammar recognition; nothing about `dir:`/
`path:`/`symlink:` recognition, or the integer/mode/sha256 lexical
grammars themselves, changed.

### 2. `_regular_file_measurement()`: `trusted_evidence` replaces self-baselining

`previous_instability` (a same-call prior unstable reading) is gone
entirely, replaced by `trusted_evidence: TrustedRegularFileEvidence | None`.
When a same-window mismatch confined to `mtime_ns`/`ctime_ns` is observed:

- `trusted_evidence is None` -> fails closed immediately
  (`EvidenceIntegrityFailure`, "no trusted prior coherent evidence"). This
  is what keeps an *initial* snapshot (no prior coherent call exists yet)
  honestly fail-closed, and is the single mechanism behind
  `INITIAL_SNAPSHOT_TIMESTAMP_INSTABILITY=FAIL_CLOSED`.
- `trusted_evidence` given but any of its six fields (decisively including
  SHA-256) doesn't match what was *actually read this attempt* -> fails
  closed immediately (`"...content or identity does not match trusted
  prior coherent evidence"`). This is the exact fix for Codex's
  reproduction: the unstable attempt's own SHA(B) is compared against
  independent trusted SHA(A), not against itself.
- Only when every field matches does `TimestampOnlyRegularFileInstability`
  get raised -- now purely a diagnostic, retry-triggering signal with no
  authority of its own; nothing downstream ever treats it as evidence.

Any non-timestamp-only mismatch, or a byte-count discrepancy, still fails
closed exactly as before regardless of `trusted_evidence` -- nothing here
weakens an existing check, it only narrows what a genuinely external proof
can excuse.

### 3. Threading `trusted_baseline` through the tree walk and root entry

`_measure_type_aware_tree()`/`snapshot_type_aware_tree()` gained
`trusted_regular_file_baseline: Mapping[str, str] | None` (keyed exactly
like the returned identity dict, e.g. `"HEAD"`, `"refs/heads/main"`),
parsed per-file via `_parse_trusted_file_descriptor()` and passed as
`trusted_evidence` to each `_regular_file_measurement()` call. Directory
measurement is completely untouched (see requirement 5; no directory-guard
relaxation was made or needed). `_workspace_git_entry_descriptor()` gained
`trusted_evidence: TrustedRegularFileEvidence | None` for the
linked-worktree regular-indirection-file case -- it goes through the exact
same proof as any other member, never bypassing it (test H).

`protected_git_metadata_snapshot()` gained `trusted_baseline: Mapping[str, str]
| None = None`. It is constant for the whole call: once, before the retry
loop, the root entry's trusted evidence
(`trusted_baseline[_WORKSPACE_GIT_ENTRY_KEY]`, parsed) and a per-role
sub-mapping (keys under `"git_dir/"`/`"git_common_dir/"`, prefix
stripped) are derived. Because validation now happens *inside*
`_regular_file_measurement()` against this constant external evidence, the
retry loop's `except TimestampOnlyRegularFileInstability` clause no longer
needs any bookkeeping of its own (the flawed
`observed_instabilities`/cross-attempt self-comparison is gone entirely)
-- it simply retries, still bounded by the same
`_PROTECTED_GIT_METADATA_MEASUREMENT_ATTEMPTS = 3`, shared across every
source of instability, root or member, however many distinct paths are
involved (test G: two different member paths each validate against their
own trusted entry independently).

### 4. Cross-phase integration (narrowest threading, no lifecycle redesign)

Every existing before/probe/after or before/after pair in the actual
preflight lifecycle now passes the *earlier* half's already-established
git_metadata as `trusted_baseline` for the *later* half, and nothing else:

- `.tomtit/dogfood/run_review.py`'s own `main()` preflight sequence
  (`review_entry` -> `capability_probe` -> `post_check` -> `review_exit`,
  each call trusting the immediately preceding one). `review_entry` itself
  -- the true initial snapshot -- passes no trusted baseline, correctly
  remaining fail-closed on any member instability.
- `.tomtit/dogfood/run_loop.py`'s `_snapshot_workspace_for_review()` gained
  one new keyword-only parameter, `trusted_git_metadata_baseline`, threaded
  straight into `protected_git_metadata_snapshot()`. Every call site that
  is the "after" half of an existing pair now passes the "before" half's
  `[5]` (git_metadata) component: the deep-preflight capability-probe pair
  (the exact pair where the real `real-e2e-target04a-002-sonnet-medium`
  run blocked), both review1 and review2 preservation pairs (pre/post and
  the post-review re-check), and the two places that establish
  `final_git_metadata`/`fallback_final_git_metadata` against the loop's own
  `baseline_git_metadata` (already an existing parameter of
  `_derive_final_workspace_evidence`, simply threaded one call further).
  The one true "establishment" call (`baseline_git_metadata = ...`, no
  earlier coherent snapshot in scope) is unchanged -- no trusted baseline,
  correctly fail-closed.

No `_snapshot_workspace_for_review()` tuple redesign; no new module-global
mutable state; every threaded parameter is explicit and typed.
`run_repair.py` was not modified -- it only ever consumes
`protected_git_metadata_snapshot()`'s return value/hash and had no
concrete correctness need demonstrated for this exact finding.

## Regression coverage

All in `.tomtit/dogfood/test_run_review.py`; disposable `tmp_path` Git
repositories only; every instability is deterministically forced via
`_stat_identity()` perturbation or an `os.open()` hook, never real OS
timing.

- **A** `test_protected_git_metadata_original_counterexample_mutation_before_first_read_fails_closed`
  -- Codex's exact reproduction: same-size A->B mutation immediately
  before the first real read, trusted SHA(A) from an earlier real
  coherent snapshot; fails closed on the very first unstable attempt.
- **B** `test_protected_git_metadata_benign_ctime_only_with_trusted_baseline_retries_and_succeeds`
  -- genuine ctime-only instability, trusted SHA(X) == unstable SHA(X);
  retry-eligible, later coherent reading accepted.
- **C** `test_protected_git_metadata_initial_snapshot_timestamp_instability_fails_closed`
  -- no trusted baseline at all; fails closed, no self-baselining.
- `test_protected_git_metadata_persistent_ctime_instability_fails_closed_despite_matching_trusted_baseline`
  -- a matching trusted baseline does not grant unbounded retries;
  persistent instability across all 3 attempts still exhausts the bound.
- **D** `test_protected_git_metadata_same_size_content_mutation_between_retries_fails_closed`.
- **E** `test_protected_git_metadata_inode_replacement_between_retries_fails_closed`.
- **F** `test_protected_git_metadata_mode_mutation_between_retries_fails_closed` and
  `test_protected_git_metadata_symlink_replacement_between_retries_fails_closed`.
- **G** `test_protected_git_metadata_multiple_unstable_paths_each_validated_against_own_trusted_entry`
  (two distinct member paths, each against its own trusted entry) and
  `test_protected_git_metadata_mixed_root_and_member_instability_shares_one_budget`
  (root-level and member-level instability sharing one flat budget,
  explicit tree-walk-call-count assertion `<= 2 * 3`).
- **H** `test_protected_git_metadata_linked_worktree_gitfile_instability_requires_trusted_evidence_too`
  (the root entry does not bypass the proof when it is a regular file) and
  `test_protected_git_metadata_root_gitfile_transient_retry_shares_global_attempt_limit`
  (the directory-typed root-entry retry, architecturally unaffected,
  rerun unchanged).
- **I** `test_protected_git_metadata_cross_phase_timestamp_stability_still_passes`.
- **J**: the full existing suite -- same-size mutation, index mutation,
  HEAD/ref mutation, linked-worktree mutation, Review→Repair semantic
  drift, capability-probe semantic mutation -- all rerun and still passing
  (see Validation below); none needed source changes.

One pre-existing, unrelated flake was observed and is not a regression:
`test_deterministic_check_delayed_descendant_is_quiesced` failed once with
`SUBPROCESS_QUIESCENCE_UNVERIFIED` (the same class of timing-sensitive
process-quiescence-probe flake noted in the two prior repair rounds, this
time on a different node) during the affected-suite run, then passed
cleanly 3/3 in isolation and cleanly in the final logged rerun below. It
touches process-group lifecycle handling, not this commit's trusted-content
proof mechanism.

## Validation

Full command output in
`TARGET04_TRUSTED_PREINSTABILITY_CONTENT_PROOF_FIX_FULL.log`.

- New F01 content-proof regressions plus all previous intra-measurement
  and Target-04 metadata focused regressions (62 in `test_run_review.py`,
  plus `test_run_loop.py -k capability_probe_semantic` and
  `test_run_repair.py -k timestamp_only_drift`): **62 + 1 + 1 = 64
  passed**, exit code 0 across all three.
- Complete affected suites (`test_run_review.py`, `test_run_repair.py`,
  `test_run_loop.py`, `tests/adapters/test_claude_code_cli.py`): **775
  passed**, exit code 0 (770 previous baseline + 5 net new tests: 10
  added, 5 superseded-and-removed from the prior round's now-incorrect
  self-baseline design).
- Exact full regression (`pytest -q -p no:cacheprovider` from repo root,
  `tests/` tree only): **985 passed**, exit code 0, unchanged.
- `compileall` (`src tests .tomtit/dogfood tomtit_cli.py`): exit code 0,
  PASS.
- `ruff check .`: exit code 0, PASS.
- `git diff --check` (working tree): exit code 0, PASS.

```text
VALIDATION_LOG_SHA256=8516dcc6ed4ad5462f62bc8f17a809e12d271dbdf173cfb0f49abe918f52e86c
VALIDATION_LOG_SIZE=3704
```

## Scope audit

Diff (`git diff --stat`) touches exactly three files:

```text
.tomtit/dogfood/run_loop.py        |  43 ++++-
.tomtit/dogfood/run_review.py      | 346 +++++++++++++++++++++++++------------
.tomtit/dogfood/test_run_review.py | 317 ++++++++++++++++++++++++++-------
```

The `run_loop.py` change is strictly the narrow trusted-baseline threading
permitted by the task (one new keyword-only parameter on
`_snapshot_workspace_for_review`, and passing the appropriate preceding
snapshot's git_metadata at each existing before/after call site) -- no
lifecycle redesign. `run_repair.py` was not modified. Directory
measurement was not relaxed. The retry bound, single-measurement race
detection for non-timestamp fields, and cross-phase stable projection are
all unchanged. Decision Evaluation V0, Product CLI V0, Target-03,
Target-04, and personal-web have no diff and were not opened for writing.

No real product provider/model was invoked. Target-04 was not modified;
Target-04A was not rerun; Target-04B was not started. Nothing was pushed.

## Completion receipt

```text
STATUS=READY_FOR_CODEX_REVIEW

SOURCE_COMMIT=2578901ff6c385fe4b75449e38badfcd487d1516
IMPLEMENTATION_REPORT_COMMIT=<recorded by the next commit; this receipt is written before that commit exists>

BLOCKING_FINDING=F01_SAME_MEASUREMENT_CONTENT_MUTATION_PROOF_GAP

TRUSTED_PRIOR_CONTENT_REQUIRED=YES
INITIAL_SNAPSHOT_TIMESTAMP_INSTABILITY=FAIL_CLOSED
UNSTABLE_SHA_SELF_BASELINE_ALLOWED=NO

ORIGINAL_COUNTEREXAMPLE=PASS
BENIGN_CTIME_WITH_TRUSTED_PRIOR=PASS
INITIAL_UNTRUSTED_CTIME=PASS
SAME_SIZE_CONTENT_MUTATION=PASS
PATH_INODE_MUTATION=PASS
MULTIPLE_PATH_BASELINE_BINDING=PASS

WHOLE_SNAPSHOT_RETRY=PASS
GLOBAL_RETRY_LIMIT=3
STALE_SNAPSHOT_REUSE=NO

CROSS_PHASE_PROJECTION=PASS
SEMANTIC_MUTATION_GUARDS=PASS

FOCUSED_TESTS=64/PASS
AFFECTED_TESTS=775/PASS
FULL_REGRESSION=985/PASS
COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS

TARGET04_MODIFIED=NO
TARGET04_RERUNS=0
TARGET04B_STARTED=NO
PUSHED=NO

NEXT_ACTION=CODEX_REVIEWS_TRUSTED_PREINSTABILITY_CONTENT_PROOF_FIX
```
