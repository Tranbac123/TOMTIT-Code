# Runtime final bounded corrective report

Status: **PASS**

Frozen baseline and current HEAD:
`a1573bdf3fc8f4adf48f46c96dcf1e3eeba1f7ee`. No commit, push, real
provider/model invocation, or real E2E rerun occurred. Full command evidence is
in `CODEX_RUNTIME_FINAL_BOUNDED_CORRECTIVE_FULL.log`.

## A — linked-worktree root `.git` identity

The protected repository snapshot now includes three independent components:

- the worktree-root `.git` entry;
- Git-resolved `git-dir`;
- Git-resolved `git-common-dir`.

The root entry is measured without following aliases and must be either a real
directory or a regular indirection file. Its descriptor binds path, type,
device/inode, full mode, size, link count, `mtime_ns`, `ctime_ns`, and—for a
regular gitfile—the exact content digest. The entry and both resolved Git
locations are rechecked after the metadata-tree measurement.

The aggregate identity therefore covers the root gitfile throughout Review
entry/exit, capability probes, deterministic checks, reviewer execution,
Repair preflight, loop review preservation, and live terminal finalization. A
separate `reviewed_workspace_git_entry_hash` makes the modern Review contract
explicit. Any modern identity missing that field fails closed. Receipts with no
modern identity fields retain strict legacy behavior; a narrow fallback
reconstructs the historical linked-worktree whole-workspace hash that included
the root `.git` file.

The adapter workspace walker now excludes only the root-level `.git` regular
file from product/worktree snapshots. It remains fully represented by protected
repository identity, so metadata drift is not misreported as product drift and
a special root `.git` entry cannot block ordinary product hashing.

## B/C — exact validated evidence bytes and post-read revalidation

Review, Repair, and Loop canonical evidence consumers use one shared
`read_canonical_evidence()` operation. It returns both the tree identity and the
exact required-file bytes read from the validating file descriptors. JSON/text
business parsing uses those captured bytes; it no longer independently reopens
validated artifacts by pathname.

Each accepted canonical regular file is measured in this order:

```text
lstat path before
open O_NOFOLLOW/O_NONBLOCK/O_CLOEXEC where available
fstat descriptor before
read/hash exact bytes and verify byte count
fstat descriptor after
lstat live canonical pathname after
compare type/dev/inode/mode/size/nlink/mtime_ns/ctime_ns
```

A positive result therefore requires the final pathname to name the same
regular object that was opened. Same-size mutation, live-path replacement while
the old inode remains open, symlink substitution, special entries, directory
aliases, and canonical hard links all fail closed. Captured bytes may remain
usable after a later pathname replacement, but the current operation never
silently parses that later pathname's bytes.

The V2 hard-link policy is unchanged: canonical evidence regular files require
`st_nlink == 1`; protected Git metadata permits hard links while binding their
link count and object/content identity.

## Process-lifecycle boundary

`docs/MVP_EXECUTION_POLICY_V0.md` §13.3 now states the exact Runtime V0 contract:
bounded lifecycle control and positive quiescence apply to the process group
Runtime creates and manages. Owner-authorized deterministic checks and
capability commands must not daemonize, re-session, or otherwise detach outside
that group. Full containment of intentionally detached independent sessions is
out of scope and requires a stronger future isolated runtime.

Existing managed-group controls remain unchanged: bounded execution, TERM then
KILL, timeout cleanup, positive absence, disappearance-race handling, and
fail-closed unknown state. No container, cgroup, namespace, daemon scanner, or
global process-tree monitor was added.

## Preserved corrective behavior

The original authorized `.pytest_cache` Review→Repair flow reaches the mocked
repair worker boundary without `WORKSPACE_STATE_MISMATCH`. Source drift,
unexpected ignored drift, reviewer writes, source-authority widening, partial
modern identity, real prior-evidence mutation, receiptless immutability,
terminal recomputation, git-dir/common-dir drift, and all R-04 invocation/count
semantics remain covered and passing.

## Verification

- New linked-worktree/evidence focused tests: **25 passed**.
- Managed-process-group contract regressions: **7 passed** outside the process
  sandbox after one constrained quiescence probe returned indeterminate.
- Original R-01/R-02/R-03/R-04 focused matrix: **23 passed**.
- Complete affected suites: **728 passed** (Review 100, Repair 111, Loop 81,
  Claude adapter 436).
- Exact repository regression: **985 passed**.
- `compileall`: **PASS**.
- Ruff: **PASS**.
- `git diff --check`: **PASS**.
- A collection-only sandbox attempt preserved the known OpenMP SHM error 179;
  unchanged collection outside the sandbox confirmed 985 tests.
- Real provider/model invocations: **0**.

## Scope audit

HEAD remains the frozen baseline. The additional bounded patch touches only the
existing Runtime Review/Repair/Loop helpers and tests, the already-in-scope
adapter/test, and `docs/MVP_EXECUTION_POLICY_V0.md`. Frozen Decision Evaluation
V0 and Product CLI V0 paths are unchanged. Target-03 and personal-web were not
touched. Pre-existing untracked review/report artifacts were preserved.

## Completion receipt

```text
STATUS=PASS

LINKED_WORKTREE_GITFILE=RESOLVED
ORDINARY_REPO_REGRESSION=PASS
LINKED_WORKTREE_IDENTITY=PASS
GITFILE_CHANGE_DETECTED=PASS
REVIEW_BINDING=PASS
REPAIR_BINDING=PASS
TERMINAL_BINDING=PASS

VALIDATED_EVIDENCE_BYTES=RESOLVED
SECOND_PATH_REOPEN_REMOVED=YES
REVIEW_USES_VALIDATED_BYTES=YES
REPAIR_USES_VALIDATED_BYTES=YES

EVIDENCE_READ_REVALIDATION=RESOLVED
SAME_SIZE_MUTATION_DETECTED=YES
PATH_REPLACEMENT_DETECTED=YES
POST_READ_PATH_IDENTITY_BOUND=YES
SYMLINK_POLICY_PRESERVED=YES
HARDLINK_POLICY_PRESERVED=YES

PROCESS_LIFECYCLE_CONTRACT=DOCUMENTED
MANAGED_PROCESS_GROUP_GUARANTEE=DOCUMENTED
DETACHED_SESSION_FULL_CONTAINMENT=OUT_OF_SCOPE_EXPECTED
PROCESS_GROUP_REGRESSIONS=PASS

ORIGINAL_E2E_FIX_REGRESSION=PASS
AUTHORIZED_SIDE_EFFECT_ONLY=PASS
SOURCE_DRIFT_REJECTED=PASS
UNEXPECTED_IGNORED_DRIFT_REJECTED=PASS
R02_IMMUTABILITY_REGRESSION=PASS
R03_FINAL_WORKSPACE_REGRESSION=PASS
R04_MODEL_EVENT_REGRESSION=PASS

FOCUSED_TESTS=PASS
AFFECTED_TESTS=PASS
FULL_REGRESSION=PASS
FULL_REGRESSION_COUNT=985
COMPILEALL=PASS
RUFF=PASS
DIFF_CHECK=PASS

DECISION_EVAL_MODIFIED=NO
CLI_V0_MODIFIED=NO
TARGET03_MODIFIED=NO
PERSONAL_WEB_TOUCHED=NO

REAL_MODEL_INVOCATIONS=0
COMMIT_CREATED=NO
PUSHED=NO

NEXT_ACTION=OWNER_RUNS_ONE_FINAL_NARROW_CORRECTNESS_REVIEW
```

## Freeze provenance closure (gate FREEZE_PROVENANCE_CLOSURE)

The previous APPROVE review covered the exact working-tree bytes represented by
the approved hash manifest. The committed `TARGET_SOURCE_COMMIT` was created
after that review and has been verified byte-equivalent to those approved
bytes. A separate Codex freeze-binding attestation will bind the commit
identity.

Owner decision: `POLICY_LINEAGE=SEPARATE_POLICY_COMMIT`. The frozen policy
document was committed on its own commit, directly parented on
`BASELINE_COMMIT`, followed by a second commit containing exactly the nine
approved Runtime corrective source/test files, parented on the policy commit.

```text
BASELINE_COMMIT=a1573bdf3fc8f4adf48f46c96dcf1e3eeba1f7ee
POLICY_COMMIT=771b8bca67779c4e0e6ba18e3d3dfe7e59f2f686
TARGET_SOURCE_COMMIT=2e0560886b60870267fea25c20ef6f2466338680

REVIEWED_DIFF_RANGE=a1573bdf3fc8f4adf48f46c96dcf1e3eeba1f7ee..2e0560886b60870267fea25c20ef6f2466338680

REVIEW_REPORT_EXPECTED_PARENT=IMPLEMENTATION_REPORT_COMMIT

OWNER_AUTHORIZATION_REF=OWNER_DECISION_AUTHORIZE_FREEZE_PROVENANCE_CLOSURE_SEPARATE_POLICY_COMMIT_2026-08-13
```

### Commit contents

- `POLICY_COMMIT` (`771b8bc`): exactly `docs/MVP_EXECUTION_POLICY_V0.md`. No
  source, tests, reports, or unrelated files. Direct parent is
  `BASELINE_COMMIT`.
- `TARGET_SOURCE_COMMIT` (`2e05608`): exactly the nine authorized Runtime
  corrective source/test files (`.tomtit/dogfood/run_loop.py`,
  `run_repair.py`, `run_review.py`, `run_task.py`, `test_run_loop.py`,
  `test_run_repair.py`, `test_run_review.py`,
  `src/tomtit/adapters/claude_code_cli.py`,
  `tests/adapters/test_claude_code_cli.py`). No policy file, reports, or
  unrelated files. Direct parent is `POLICY_COMMIT`.

### Approved hash manifest (verified at TARGET_SOURCE_COMMIT)

```text
9e851ec49543af06f91c155bea5fe27e72524d94a7165ad78ceaeab8588b21b3  .tomtit/dogfood/run_loop.py
1fb12f4e0fecc848dbfb329ff6a0ae6c6cb8d26bb0766ecfe199b52a0838813d  .tomtit/dogfood/run_repair.py
3a809730e332e8038d55815b7ae3df8582a70dc7200c4a2ac5a2c26087e32831  .tomtit/dogfood/run_review.py
a966a2712dfa226f8a959a4bc3e7ac5c016dfeaa81a29230b78043ce756895c6  .tomtit/dogfood/run_task.py
91f95a01a05db79a355904d85b62d4f5c3d89a2b39c9f98e27b9b36289931e82  .tomtit/dogfood/test_run_loop.py
95ff5a8c6bf175b8dce0f3d36d3413ed7b51feca88058f50f86805782fbfbf08  .tomtit/dogfood/test_run_repair.py
b4824626a0d819f2f81b8ecfbaa16e4b13111323a2833485ecb7c353a112d583  .tomtit/dogfood/test_run_review.py
f339c329ef24a35c6209ef217246b7dc482b2b369bb934dfcd626ca70f520861  docs/MVP_EXECUTION_POLICY_V0.md
4970beabd05cf1fc34c1a54447e7c90e6813869134b6b445be1ca6e323be457e  src/tomtit/adapters/claude_code_cli.py
7659adbfb7064936e95ad3360074872262b4b8b508d7f345d9a1b7ca15e40460  tests/adapters/test_claude_code_cli.py
```

All ten hashes match exactly at `TARGET_SOURCE_COMMIT`. The policy blob is
identical at `POLICY_COMMIT` and `TARGET_SOURCE_COMMIT`
(`f339c329ef24a35c6209ef217246b7dc482b2b369bb934dfcd626ca70f520861`). The nine
source/test paths at `POLICY_COMMIT` are unchanged from `BASELINE_COMMIT`. No
commit hook exists in this worktree
(`.../worktrees/TOMTIT-Coordinator-cli-v0/hooks` absent), so no hook altered
approved bytes. No staged or tracked source drift exists. Re-verified after
the committed-state validation run below — byte-equivalence held.

### Committed-state validation

Full stdout/stderr recorded in `RUNTIME_COMMITTED_STATE_VALIDATION_FULL.log`.

- Focused groups (linked-worktree `.git` correctness, canonical evidence
  reads, authorized-side-effect Review→Repair, R-01–R-04/lifecycle):
  **UNPROVEN** — the exact selectors for these four previously-accepted
  groups (expected counts 14/14/12/19) could not be reconstructed from
  current repository state (no pytest markers or unambiguous keyword
  matches were found for these group boundaries). Not fabricated; recorded
  honestly as skipped rather than guessed.
- Complete affected suites
  (`test_run_review.py`, `test_run_repair.py`, `test_run_loop.py`,
  `tests/adapters/test_claude_code_cli.py`): **728 passed**, exit code 0.
  Matches expected `AFFECTED_TEST_COUNT=728`.
- Exact full regression (`pytest -q -p no:cacheprovider`): **985 passed**,
  exit code 0. Matches expected `FULL_REGRESSION_COUNT=985`.
- `compileall` (`src tests .tomtit/dogfood tomtit_cli.py`): exit code 0,
  PASS.
- `ruff check .`: exit code 0, PASS.
- `git diff --check` (working tree): exit code 0, PASS.
- `git diff --check a1573bdf..2e05608`: exit code 0, PASS.
- No known OpenMP sandbox failure (#179) was reproduced in this run; this
  environment's pytest invocation ran outside that sandbox condition.

### Validation log

```text
VALIDATION_LOG_SHA256=6c746fe915a39a969dfa66da4988b6a1422c072804636e2afc2675c80660e993
VALIDATION_LOG_SIZE=3737
```

### Scope audit

`BASELINE_COMMIT` remains `a1573bdf3fc8f4adf48f46c96dcf1e3eeba1f7ee`. The two
new commits touch only the ten approved manifest paths. Frozen Decision
Evaluation V0, Product CLI V0, Target-03, and personal-web paths are
unchanged (confirmed via `git diff --name-only` against the excluded
authorized paths — empty result). Pre-existing untracked review/report
artifacts were preserved and not modified by this stage. No real
provider/model invocation occurred for product work. No E2E rerun occurred.
No push occurred.
