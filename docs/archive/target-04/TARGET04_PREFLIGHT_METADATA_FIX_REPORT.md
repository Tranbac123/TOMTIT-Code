# Target-04 preflight metadata fix report

```text
STATUS=PASS
WORK_CLASS=DEFECT
GATE=TARGET_04

BASELINE_COMMIT=2fde70af07d58a44b32b28ae688271693c5dffed

FIRST_RUN_EXACT_CAUSE=UNPROVEN
REPRODUCED_RUNTIME_CONTRACT_DEFECT=CONFIRMED
```

## Distinguishing the two claims

**`FIRST_RUN_EXACT_CAUSE=UNPROVEN`.** The first real Target-04A run
(`real-e2e-target04a-001-sonnet-medium`) blocked during deep preflight with
`IMMUTABILITY_VIOLATION` before any real Claude or Codex model invocation
(`implementation_attempts=0`, `review_attempts=0`,
`model_invocation_count=0`). A forensic hypothesis (macOS asynchronously
attaching a `com.apple.provenance` extended attribute to `.git/index`) was
consistent with the symptom, but the exact changed field on that specific
run was never captured (the two in-memory snapshots that disagreed were
never persisted), so this remains an unproven hypothesis about that one
run, not a proven root cause.

**`REPRODUCED_RUNTIME_CONTRACT_DEFECT=CONFIRMED`.** Independent of the
first run's exact cause, a real defect in the Runtime contract was
reproduced directly: `protected_git_metadata_snapshot()`'s returned
identity embedded `mtime_ns`/`ctime_ns` for every filesystem object under
`.git`, and every cross-phase comparison in the codebase (Review's own
entry/capability-probe/post-check/exit sequence, `run_loop.py`'s outer
capability probe and pre/post-review preservation checks, and
`run_repair.py`'s reviewed-vs-actual reconciliation) compared that value
directly or via its hash. An xattr-only change to `.git/index` (content
hash unchanged, semantic Git state unchanged, `git fsck` clean) changes
`ctime_ns` without any semantic mutation, and was sufficient to make two
otherwise-identical snapshots compare unequal. This is a genuine false
cross-phase-immutability-violation exposure, reproduced deterministically
in this task's regression tests (see below), independent of whatever
actually happened during the first real run.

## Implementation

Both changes are confined to `.tomtit/dogfood/run_review.py`. No other
Runtime source file changed; `run_loop.py` and `run_repair.py` inherit the
fix automatically because they only ever call
`protected_git_metadata_snapshot()` / `protected_git_metadata_hash()` and
compare/hash the result -- they never construct or interpret the raw
per-entry descriptor strings themselves.

### 1. Cross-phase-stable projection (concept 2)

Two new private helpers:

- `_strip_volatile_timestamp_fields(descriptor: str) -> str` -- removes the
  `:mtime_ns=<int>:ctime_ns=<int>` substring from one identity descriptor
  via a regex anchored to the exact adjacent-pair format both
  `_directory_descriptor` and `_regular_file_measurement` always emit.
  `path:` and `symlink:` descriptors, which never contain that substring,
  are left untouched.
- `_stable_cross_phase_projection(identity: Mapping[str, str]) -> dict[str, str]`
  -- applies the stripper to every entry in an identity snapshot.

`protected_git_metadata_snapshot()` now returns
`_stable_cross_phase_projection(identity)` instead of the raw `identity`
dict. Every other component of identity remains fully load-bearing: path,
entry type (`dir:`/`file:`/`symlink:`/`path:` prefix), mode, device/inode,
size, link count, and content SHA-256. Only the two volatile timestamp
fields are excluded, exactly as required -- nothing else about repository
metadata is ignored.

`protected_git_metadata_hash()` is unchanged (`workspace_hash(protected_git_metadata_snapshot(cwd))`);
it inherits stability automatically. `review_repository_identity_preserved()`,
`diff_workspace_hashes()`, and every `run_loop.py`/`run_repair.py` cross-phase
comparison that consumes these values needed no changes, since they only
ever compare the returned dict/hash, never reconstruct raw descriptors.

### 2. Single-measurement integrity is unchanged (concept 1)

`_stat_identity()`, `_directory_descriptor()`, `_regular_file_measurement()`,
and `_measure_type_aware_tree()` (the per-file open/read/close race checks
and the per-directory revisit check) are **byte-for-byte unchanged**. They
still compare full raw `os.stat_result`-derived identity, including
`mtime_ns`/`ctime_ns`, to detect a genuine same-window mutation while an
object is being measured. `_strip_volatile_timestamp_fields` /
`_stable_cross_phase_projection` are used only at the point where
`protected_git_metadata_snapshot()` returns its final result for cross-phase
consumption -- never inside those lower-level measurement primitives.

### 3. Bounded retry for the composite measurement's own same-window check

`protected_git_metadata_snapshot()` already performed a same-window
self-consistency check: it measures the root `.git` entry and resolves
`git-dir`/`git-common-dir` before walking the metadata trees, then
re-measures both immediately after, requiring them to match. Previously any
mismatch raised `EvidenceIntegrityFailure` immediately. This is now bounded
retry-eligible:

- `git-dir`/`git-common-dir` are pure path strings with no timestamp
  component, so any difference there is always semantic and still fails
  closed immediately (no retry).
- If the root `.git` entry's path is unchanged and its descriptor differs
  *only* in `mtime_ns`/`ctime_ns` (per `_strip_volatile_timestamp_fields`),
  the whole composite measurement (root entry + both full tree walks) is
  retried from scratch, bounded by
  `_PROTECTED_GIT_METADATA_MEASUREMENT_ATTEMPTS = 3` total attempts.
- Any non-timestamp difference in the root entry fails closed immediately,
  on any attempt, without exhausting the retry budget.
- If every attempt sees only timestamp-only instability, the function fails
  closed after the bound is exhausted (never retries unboundedly, never
  sleeps).

```text
BOUNDED_RETRY_ADDED=YES
BOUNDED_RETRY_LIMIT=3 (total attempts, i.e. up to 2 retries)
```

## Focused regressions

All nine required cases, plus retry-limit assertions, in
`.tomtit/dogfood/test_run_review.py` (unless noted), `test_run_loop.py`, and
`test_run_repair.py`:

1. `.git/index` xattr/ctime-only drift between cross-phase snapshots:
   `test_protected_git_metadata_ctime_only_drift_is_cross_phase_stable` --
   `os.utime()` bumps `mtime`/`ctime` only; two separate
   `protected_git_metadata_snapshot()` calls compare equal.
2. Same-size content mutation during a single measurement still rejected:
   `test_protected_git_metadata_same_size_content_mutation_is_detected` --
   a same-size byte flip in `.git/description` (chosen because, unlike
   `.git/config`, Git never parses it, so the mutation cannot break
   subsequent `git` subprocess calls) is still detected.
3. Git index content/stage mutation: `test_protected_git_metadata_index_stage_mutation_is_detected`.
4. HEAD/ref mutation: `test_protected_git_metadata_head_ref_mutation_is_detected`.
5. Root `.git`/gitfile/git-dir/git-common-dir mutations remain detected:
   `test_protected_git_metadata_root_directory_mode_change_is_detected` (new,
   ordinary non-linked-worktree case) plus the four pre-existing
   linked-worktree gitfile tests, all still passing unchanged.
6. Transient timestamp-only instability, bounded retry succeeds:
   `test_protected_git_metadata_bounded_retry_recovers_from_transient_timestamp_only_instability`
   (monkeypatches `_workspace_git_entry_descriptor` with a deterministic
   4-call sequence: attempt 1 mismatched-timestamp-only, attempt 2 coherent;
   asserts success and exactly 4 calls consumed).
7. Persistent timestamp instability fails closed after bounded retry:
   `test_protected_git_metadata_persistent_timestamp_instability_fails_closed`
   (every one of the 3 bounded attempts sees timestamp-only mismatch;
   asserts `EvidenceIntegrityFailure` and exactly 6 calls consumed -- no
   further, unbounded retry).
8. Content-changing race during retry fails closed:
   `test_protected_git_metadata_content_changing_race_during_retry_fails_closed`
   (attempt 1 timestamp-only, attempt 2's own before/after differs by inode
   -- a real change; asserts immediate `EvidenceIntegrityFailure` after
   exactly 4 calls, not the full 6 the bound would allow).
9. Fake capability probe that semantically mutates repository state still
   blocks deep preflight:
   `test_capability_probe_semantic_repository_mutation_still_blocks_preflight`
   in `test_run_loop.py` (a fake Codex's `--help` invocation writes a new
   file under `.git/hooks`; asserts the loop still reaches
   `run_state=BLOCKED` / `stop_reason=PREFLIGHT_FAILED` with
   `implementation_attempts=0`). Required adding a `help_write_path`
   parameter to `test_run_loop.py`'s local `write_fake_codex()` fixture
   (mirroring the one already in `test_run_review.py`), since no existing
   fixture there could simulate a mutation during the `--help` capability
   probe specifically.

One additional targeted regression in `test_run_repair.py`,
`test_reviewed_protected_git_metadata_hash_survives_timestamp_only_drift`,
proves the same fix benefits the Review-to-Repair reconciliation consumer
directly: a timestamp-only `.git/index` touch between building review
evidence and running Repair preflight no longer desynchronizes
`reviewed_protected_git_metadata_hash_actual` from `_expected`.

## Validation

Full command output in `TARGET04_PREFLIGHT_METADATA_FIX_FULL.log`.

- Focused linked-worktree/protected-Git-identity/capability-probe selection
  (`test_run_review.py -k "linked_worktree or capability_probe or
  protected_git_metadata or immutab"`): **22 passed**.
- Complete affected suites (`test_run_review.py`, `test_run_repair.py`,
  `test_run_loop.py`, `tests/adapters/test_claude_code_cli.py`): **738
  passed**, exit code 0 (728 pre-existing + 10 new: 8 in
  `test_run_review.py`, 1 in `test_run_loop.py`, 1 in `test_run_repair.py`).
- Exact full regression (`pytest -q -p no:cacheprovider` from repo root,
  which collects only the `tests/` tree -- `.tomtit/dogfood/` is validated
  by the affected-suite run above, not this one): **985 passed**, exit code
  0, unchanged from before this fix (expected: none of the new tests live
  under `tests/`).
- `compileall` (`src tests .tomtit/dogfood tomtit_cli.py`): exit code 0,
  PASS.
- `ruff check .`: exit code 0, PASS.
- `git diff --check` (working tree): exit code 0, PASS.

```text
VALIDATION_LOG_SHA256=b3b2038a0d80feeb8cbc4791691c30426011bd99b87b9952baf1cf7e96d47770
VALIDATION_LOG_SIZE=2840
```

## Scope audit

Diff (`git diff --stat`) touches exactly the four expected candidate files:

```text
.tomtit/dogfood/run_review.py      | 144 +++++++++++++++++++++++-------
.tomtit/dogfood/test_run_loop.py   |  41 +++++++++
.tomtit/dogfood/test_run_repair.py |  26 ++++++
.tomtit/dogfood/test_run_review.py | 176 +++++++++++++++++++++++++++++++++++++
```

No other Runtime source changed. Decision Evaluation V0, Product CLI V0,
Target-03, Target-04, and personal-web have no diff and were not opened for
writing by this task. `WorktreeManager`/future workspace architecture,
routing, memory, `FindingTriage`, crash/resume, and self-improvement were
not touched. The evidence-quality observation Codex flagged separately
(`protected_git_metadata_baseline_hash=null` /
`protected_git_metadata_preserved=false` on a deep-preflight failure,
because the baseline-measurement exception is discarded before it can be
recorded) was **not** addressed here, per the task's explicit instruction
not to expand scope into that reporting-quality issue.

No real product provider/model was invoked. No Target-04 run occurred
(neither a rerun of Target-04A nor a start of Target-04B); the fixture at
`/Users/tranvanbac/Documents/AI/ai-agent/TOMTIT-E2E-Target-04` was not
opened for writing. Nothing was pushed.

## Completion receipt

```text
STATUS=READY_FOR_CODEX_REVIEW

SOURCE_COMMIT=fbe07396bcc8f1294c7ad897c926320a2f56e4e8
IMPLEMENTATION_REPORT_COMMIT=<recorded by the next commit; this receipt is written before that commit exists>

FIRST_RUN_EXACT_CAUSE=UNPROVEN
REPRODUCED_RUNTIME_CONTRACT_DEFECT=CONFIRMED

CROSS_PHASE_VOLATILE_TIMESTAMPS_EXCLUDED=YES
SINGLE_MEASUREMENT_TIMESTAMP_GUARD_PRESERVED=YES
BOUNDED_RETRY_ADDED=YES
BOUNDED_RETRY_LIMIT=3

XATTR_CTIME_ONLY_REGRESSION=PASS
INDEX_CONTENT_MUTATION_REGRESSION=PASS
HEAD_REF_MUTATION_REGRESSION=PASS
GIT_IDENTITY_MUTATION_REGRESSION=PASS
TRANSIENT_MEASUREMENT_RETRY=PASS
PERSISTENT_INSTABILITY_FAIL_CLOSED=PASS
SEMANTIC_CAPABILITY_MUTATION_BLOCKED=PASS

AFFECTED_TESTS=738/PASS
FULL_REGRESSION=985/PASS
COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS

TARGET04_MODIFIED=NO
TARGET04_RERUNS=0
TARGET04B_STARTED=NO
PROTECTED_SCOPE_PRESERVED=YES
PUSHED=NO

NEXT_ACTION=CODEX_INDEPENDENTLY_REVIEWS_TARGET04_PREFLIGHT_METADATA_FIX
```
