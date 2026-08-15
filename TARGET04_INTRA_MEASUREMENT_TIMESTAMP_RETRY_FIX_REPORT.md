# Target-04 intra-measurement timestamp retry fix report

```text
STATUS=PASS
WORK_CLASS=DEFECT
GATE=TARGET_04

RUN002_EXACT_CAUSE=PARTIAL
REPRODUCED_FAILURE_MECHANISM=CONFIRMED
FAILING_LAYER=INTRA_MEASUREMENT_GUARD
```

## Distinguishing the two claims

**`RUN002_EXACT_CAUSE=PARTIAL`.** Codex's forensic V2 result reproduced the
failing mechanism with disposable temp repositories and OS-level ctime-only
xattr churn, but the exact member path involved in the real
`real-e2e-target04a-002-sonnet-medium` run was never retained. This report
does not claim, and must not be read as claiming, that the historical
run-002 failure is now proven to have this exact cause. That remains
unproven.

**`REPRODUCED_FAILURE_MECHANISM=CONFIRMED`.** Independent of run-002's exact
history, a real defect in the Runtime contract was reproduced
deterministically: `_regular_file_measurement()`'s own before/opened/
closing/final identity checks used raw, full timestamp-aware `_stat_identity()`
comparisons with no tolerance at all. A ctime-only (or mtime-only) mismatch
observed *within one single measurement* of any protected regular file --
the root `.git` entry when it is a linked-worktree indirection file, or any
file inside the `git_dir`/`git_common_dir` trees (`HEAD`, a ref, a loose
object, `index`, `config`, ...) -- raised `EvidenceIntegrityFailure`
immediately, before `protected_git_metadata_snapshot()`'s existing bounded
retry loop could ever apply: that loop only ever caught the root `.git`
entry's before/after *comparison* mismatch, never an exception raised from
*inside* a member measurement. `_snapshot_workspace_for_review()` then
converts that propagated failure into an empty git-metadata component,
producing an unequal cross-phase tuple and the observed
`IMMUTABILITY_VIOLATION`. This is the mechanism, confirmed by direct
reproduction (see regressions below); it is a narrower, additional gap
beyond the earlier cross-phase-projection fix, not a replacement for it.

## Root mechanism (as designed against)

```text
initial pathname lstat -> opened fstat -> read/hash -> closing fstat -> final pathname lstat
```

Any mismatch among these five observation points, for any reason, used to
fail closed unconditionally. The fix narrows *only* the case where every
observed difference is confined exactly to `mtime_ns`/`ctime_ns`, with
device, inode, mode, size, nlink, and the actually-read content (SHA-256)
identical throughout -- and even then, does not accept the measurement as
successful. It reports a structured, internal-only signal and forces a
bounded, whole-snapshot retry.

## Design

### 1. Structured timestamp-only instability

New exception `TimestampOnlyRegularFileInstability` (in
`.tomtit/dogfood/run_review.py`), carrying `path`, `dev`, `ino`, `mode`,
`size`, `nlink`, `sha256` from the read that observed the instability, plus
`matches_nonvolatile_identity(...)` to validate a later reading against it.
It is never returned as a successful descriptor and never silently
swallowed.

### 2. `_regular_file_measurement()` becomes opt-in tolerant

Two new keyword-only parameters, both defaulted so every *other* existing
caller (canonical evidence reading via `read_canonical_evidence`/
`snapshot_evidence_tree`, and anything else using the default) is
byte-for-byte unaffected -- confirmed by the existing
`test_read_canonical_evidence_rejects_same_size_mutation_during_read` and
`test_read_canonical_evidence_rejects_pathname_replacement_during_read`
regressions, which still pass unchanged:

- `tolerate_timestamp_only_instability: bool = False` -- when `True` (only
  `protected_git_metadata_snapshot`'s internal call sites pass this), a
  mismatch confined exactly to `mtime_ns`/`ctime_ns` raises
  `TimestampOnlyRegularFileInstability` instead of `EvidenceIntegrityFailure`.
  Any *non-timestamp* difference (device, inode, mode, size, nlink, type,
  or a byte-count/content discrepancy) still fails closed exactly as
  before, regardless of this flag -- the flag only ever narrows what
  triggers a hard failure, never removes a check.
- `previous_instability: TimestampOnlyRegularFileInstability | None = None`
  -- when given, the *current* reading's nonvolatile identity (dev, ino,
  mode, size, nlink, SHA-256) is validated against it before anything else
  is decided, regardless of whether this attempt is itself internally
  coherent or unstable again. A mismatch fails closed immediately
  ("regular file content or identity changed between coherent-measurement
  retry attempts") -- a real mutation between attempts is never silently
  accepted as a new baseline.

The function's internal structure changed from "raise immediately at each
of the three original checkpoints" to "compute all four stat observations
and the digest unconditionally, then decide once at the end," so the
timestamp-only classification can see the complete picture. The five error
messages that existed (opening/hashing/pathname-replacement) were
consolidated into one ("regular file identity changed during measurement")
for the failing case; no existing test asserted on the removed message
text (verified by search before making the change).

### 3. Threading through the tree walk

`_measure_type_aware_tree()` and `snapshot_type_aware_tree()` gained the
same two parameters (renamed for the tree-wide scope:
`tolerate_regular_file_timestamp_only_instability`,
`previous_regular_file_instabilities: Mapping[str, TimestampOnlyRegularFileInstability]`),
threaded straight into each per-file `_regular_file_measurement()` call.
**Directory measurement is completely untouched** -- the directory-revisit
self-consistency check
(`_stat_identity(directory_after) != _stat_identity(expected_directory)`)
still fails closed unconditionally on any difference, timestamp-only or
not, exactly as before requirement 5 requires. Symlink handling is also
untouched.

`_workspace_git_entry_descriptor()` gained the same two parameters, used
only for the linked-worktree regular-indirection-file case (a directory
root entry has no internal race check of its own to begin with --
`_directory_descriptor()` just formats the passed-in stat once).

### 4. One shared retry budget in `protected_git_metadata_snapshot()`

The existing `for _ in range(_PROTECTED_GIT_METADATA_MEASUREMENT_ATTEMPTS)`
loop (still exactly 3) now wraps the whole per-attempt body -- root entry
before, resolve+walk both trees, root entry after -- in one `try`, and adds
exactly one new `except TimestampOnlyRegularFileInstability`. On catching
it:

- if a prior instability was already recorded for that exact path and the
  new one's nonvolatile identity doesn't match it, fail closed immediately
  (`EvidenceIntegrityFailure`), consuming no further attempts;
- otherwise, record/update the evidence for that path in a
  `dict[str, TimestampOnlyRegularFileInstability]` that lives for the
  whole call (not per-attempt), and `continue` to the next iteration of the
  **same** loop.

There is no nested loop and no second attempt counter anywhere -- root
timestamp-only retries (the pre-existing before/after comparison
mechanism, unchanged) and member timestamp-only retries (the new
exception-based mechanism) both consume from the identical 3-iteration
`for` loop, however many distinct paths or which layer is involved. Every
retry rebuilds the entire composite snapshot (root entry + both full tree
walks) from scratch; no partial tree or previously-successful subtree is
ever reused -- the `identity` dict is a fresh local created at the top of
each attempt and simply discarded when an attempt is abandoned.

The cross-phase stable-projection step
(`_stable_cross_phase_projection`, applied only on the successful return)
is completely unchanged.

## Regression coverage

All in `.tomtit/dogfood/test_run_review.py`, using disposable `tmp_path`
Git repositories only (no OS-level timing dependence -- every test
deterministically perturbs the internal `_stat_identity()` tuple or hooks
`os.open`, never sleeps or races the real filesystem):

- **A** `test_protected_git_metadata_member_ctime_only_transient_recovers`
  -- a targeted `_stat_identity()` perturbation makes HEAD's first
  measurement ctime-only unstable; the whole snapshot retries and a later
  coherent reading of the same unchanged bytes succeeds.
- **B** `test_protected_git_metadata_persistent_member_ctime_instability_fails_closed`
  -- HEAD is ctime-only unstable on every attempt; fails closed after
  exactly the 3-attempt bound (asserted via exact call count).
- **C** `test_protected_git_metadata_content_change_after_member_ctime_fails_closed`
  -- HEAD is ctime-only unstable on attempt 1; its content (different
  size) is rewritten immediately before attempt 2's read; fails closed.
- **D** `test_protected_git_metadata_same_size_content_change_after_member_ctime_fails_closed`
  -- same as C but the rewrite preserves the exact original byte size,
  proving SHA-256 (not size) is what's actually validated; fails closed.
- **E** `test_protected_git_metadata_inode_replacement_after_member_ctime_fails_closed`
  -- HEAD is ctime-only unstable on attempt 1; it is unlinked and
  byte-identically recreated (new inode) before attempt 2's read; fails
  closed.
- **F** `test_protected_git_metadata_root_gitfile_transient_retry_shares_global_attempt_limit`
  -- the pre-existing root-`.git`-entry transient-retry scenario, rerun to
  confirm it still passes unchanged under the extended retry mechanism.
- **G** `test_protected_git_metadata_mixed_root_and_member_instability_shares_one_budget`
  -- root-entry instability resolved within attempt 1, member (HEAD)
  instability during attempt 2; succeeds by attempt 3, and an explicit
  tree-walk call-count assertion (`<= 2 * 3`) proves the two sources of
  instability share one flat budget rather than multiplying nested
  retries.
- **H** `test_protected_git_metadata_cross_phase_timestamp_stability_still_passes`
  -- the original cross-phase-projection regression, rerun to confirm it
  is unaffected by the intra-measurement mechanism added here.
- **I** (rerun, no new tests needed): the full existing suite --
  same-size mutation, index mutation, HEAD/ref mutation, linked-worktree
  mutation, path/type/mode mutation, Review→Repair semantic drift, and
  capability-probe semantic mutation -- all continue to pass unchanged (see
  Validation below).

## Validation

Full command output in `TARGET04_INTRA_MEASUREMENT_TIMESTAMP_RETRY_FIX_FULL.log`.

- New intra-measurement focused tests plus all previous Target-04
  metadata-fix focused regressions (58 in `test_run_review.py`, plus
  `test_run_loop.py -k capability_probe_semantic` and
  `test_run_repair.py -k timestamp_only_drift`): **58 + 1 + 1 = 60 passed**,
  exit code 0 across all three.
- Complete affected suites (`test_run_review.py`, `test_run_repair.py`,
  `test_run_loop.py`, `tests/adapters/test_claude_code_cli.py`): **770
  passed**, exit code 0 (762 previous baseline + 8 new tests).
- Exact full regression (`pytest -q -p no:cacheprovider` from repo root,
  `tests/` tree only): **985 passed**, exit code 0, unchanged (no new test
  lives under `tests/`).
- `compileall` (`src tests .tomtit/dogfood tomtit_cli.py`): exit code 0,
  PASS.
- `ruff check .`: exit code 0, PASS.
- `git diff --check` (working tree): exit code 0, PASS.

```text
VALIDATION_LOG_SHA256=13f5eabac404b9499d1f3cd116f97b3d2434c798136d5376b99bb6d6a2bbbcb9
VALIDATION_LOG_SIZE=3473
```

## Scope audit

Diff (`git diff --stat`) touches exactly two files:

```text
.tomtit/dogfood/run_review.py      | 322 +++++++++++++++++++++++++++++++------
.tomtit/dogfood/test_run_review.py | 299 +++++++++++++++++++++++++++++++++-
```

`run_loop.py` and `run_repair.py` source were not modified -- neither
consumes anything below `protected_git_metadata_snapshot()`'s public
return value, so no concrete need was demonstrated. Target-04's fixture
was not opened for writing; Target-04A was not rerun; Target-04B was not
started. No real product provider/model was invoked. The separate
evidence-reporting limitation (`protected_git_metadata_baseline_hash`
recorded as `null` on a preflight block) was left untouched, per scope.
Decision Evaluation V0, Product CLI V0, Target-03, and personal-web have no
diff and were not opened for writing.

Three pre-existing tests
(`test_protected_git_metadata_bounded_retry_recovers_from_transient_timestamp_only_instability`,
`test_protected_git_metadata_persistent_timestamp_instability_fails_closed`,
`test_protected_git_metadata_content_changing_race_during_retry_fails_closed`)
had their `_workspace_git_entry_descriptor` monkeypatch lambdas widened from
`lambda cwd: next(sequence)` to `lambda cwd, **kwargs: next(sequence)`, the
minimal change needed to keep them compatible with that function's two new
keyword-only parameters. No assertion in those tests changed.

Nothing was pushed.

## Completion receipt

```text
STATUS=READY_FOR_CODEX_REVIEW

BASELINE_COMMIT=f792d5f4b81b2d697d0b9e5917caf5022405fdf7
SOURCE_COMMIT=d0ca9b4a6e77ecdaa215d6148cbbd16cacb91bbe
IMPLEMENTATION_REPORT_COMMIT=<recorded by the next commit; this receipt is written before that commit exists>

RUN002_EXACT_CAUSE=PARTIAL
REPRODUCED_FAILURE_MECHANISM=CONFIRMED
FAILING_LAYER=INTRA_MEASUREMENT_GUARD

MEMBER_CTIME_TRANSIENT_RECOVERS=PASS
PERSISTENT_CTIME_FAILS_CLOSED=PASS
CONTENT_CHANGE_AFTER_CTIME_FAILS_CLOSED=PASS
SAME_SIZE_CONTENT_CHANGE_FAILS_CLOSED=PASS
PATH_INODE_CHANGE_FAILS_CLOSED=PASS

WHOLE_SNAPSHOT_RETRY=PASS
GLOBAL_RETRY_LIMIT=3
STALE_SNAPSHOT_REUSE=NO
DIRECTORY_GUARD_RELAXED=NO

CROSS_PHASE_PROJECTION_PRESERVED=PASS
SEMANTIC_MUTATION_GUARDS_PRESERVED=PASS

FOCUSED_TESTS=60/PASS
AFFECTED_TESTS=770/PASS
FULL_REGRESSION=985/PASS
COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS

TARGET04_MODIFIED=NO
TARGET04_RERUNS=0
TARGET04B_STARTED=NO
PUSHED=NO

NEXT_ACTION=CODEX_REVIEWS_INTRA_MEASUREMENT_TIMESTAMP_RETRY_FIX
```
