# Productized CLI V0 — Independent Software Correctness Review

## Verdict

**BLOCKER**

The implementation is additive-only and remains a thin facade over the frozen bounded runtime. Explicit argv construction, `shell=False`, persisted-result outcome authority, verifier-mode refusal, direct bootstrap execution, and ordinary input validation all behaved as claimed.

Approval is nevertheless blocked by three demonstrated defects:

1. TOMTIT repository descendants and same-repository linked-worktree descendants pass the CLI self-target check.
2. A run-specific evidence-directory symlink can redirect evidence into the target workspace.
3. The exported request builder silently converts a truthy non-boolean value such as the string `"false"` into repair authorization.

The full command output, including all first-run failures, is preserved in `CODEX_CLI_V0_INDEPENDENT_REVIEW_FULL.log`.

## Review scope and additive-only result

The peeled baseline tag and `HEAD` both resolve to `cfa4927537b45b40f9badb0f47c7c66e9fca82b9`.

- `git diff --name-status tomtit-m1-guarded-workspace-v1`: empty.
- `git diff --check`: exit 0.
- No baseline-tracked file is modified, deleted, or renamed.
- The complete feature implementation set consists of five untracked additions:
  - `src/tomtit_cli_v0/__init__.py`
  - `src/tomtit_cli_v0/__main__.py`
  - `src/tomtit_cli_v0/cli.py`
  - `tests/cli_v0/test_product_cli_v0.py`
  - `tomtit_cli.py`

The review did not modify any implementation file and created no commit.

## Findings

### CLI-V0-001

- **ID:** CLI-V0-001
- **SEVERITY:** BLOCKER
- **LOCATION:** `src/tomtit_cli_v0/cli.py:159-177`; missing descendant cases in `tests/cli_v0/test_product_cli_v0.py`
- **PROBLEM:** `_reject_self_workspace` rejects the exact coordinator root and compares Git common directories only when the target directory itself has a `.git` entry. It does not discover the repository that owns an arbitrary target descendant. Direct probes produced `B_SELF_SRC=ACCEPTED`, `C_SELF_TESTS=ACCEPTED`, and `E_SAME_REPO_OTHER_WORKTREE_DESCENDANT=ACCEPTED`. The frozen phase-A preflight rejects a descendant of its current checkout lexically, but independently accepted `src/` in the other linked worktree, so that case is not caught by the frozen fallback either.
- **IMPACT:** A user can select a subdirectory in another linked worktree of TOMTIT and proceed beyond both CLI validation and frozen phase-A validation toward worker discovery/invocation against TOMTIT's own repository. This violates the explicit self-target prohibition.
- **REQUIRED_FIX:** Resolve the Git owning worktree/common directory for the target even when the target is a descendant, compare it with TOMTIT's canonical Git common directory, and reject both the owning worktree root and every descendant. Add real or faithful tests for all A–G cases, especially current-checkout `src/`/`tests/` and linked-worktree descendants.

### CLI-V0-002

- **ID:** CLI-V0-002
- **SEVERITY:** BLOCKER
- **LOCATION:** `src/tomtit_cli_v0/cli.py:295-318`
- **PROBLEM:** The evidence root is resolved, but `evidence_root / run_id` is not resolved before workspace-containment validation. An existing symlink named for the run ID, pointing to an empty directory inside the target workspace, passes both the lexical containment check and the non-empty collision check. The direct probe produced `EVIDENCE_RUN_DIRECTORY_SYMLINKED_INSIDE_WORKSPACE=ACCEPTED`. The frozen runner likewise constructs the run directory lexically and then writes its event/evidence files through it.
- **IMPACT:** Runtime evidence can be written inside and mutate the target workspace despite the outside-workspace requirement. This also weakens run-result provenance because the trusted result path can be redirected through a pre-existing filesystem alias.
- **REQUIRED_FIX:** Resolve and validate the final run-specific evidence path (or reject a pre-existing symlink outright) before launch, enforce that its resolved location is outside the resolved workspace, and repeat the invariant at the write boundary. Add a regression test with an empty in-workspace symlink target.

### CLI-V0-003

- **ID:** CLI-V0-003
- **SEVERITY:** HIGH
- **LOCATION:** `src/tomtit_cli_v0/cli.py:242,328`
- **PROBLEM:** The public, exported `build_request` helper does not require `allow_one_repair` to be a real boolean and stores `bool(allow_one_repair)`. The direct probe `allow_one_repair="false"` produced `STRING_FALSE_REPAIR_PERMISSION=True`.
- **IMPACT:** A caller using the exported request-building API can unintentionally grant a repair round even though its supplied value semantically says false. This violates the requirement that validation not silently add repair permission. The argparse command surface happens to supply a real boolean, but the public validation boundary does not enforce that contract.
- **REQUIRED_FIX:** Reject values whose type is not exactly `bool`, preserve the validated boolean without coercion, and add tests for string and integer inputs as well as `False`/`True`.

## Verified behavior

### Thin facade and runtime authority

The new CLI owns input validation/canonicalization, immutable request/result records, argv translation, one subprocess call, and persisted-result reading. It does not implement state transitions, repair decisions, review decisions, changed-path classification, workspace hashing, evidence lifecycle, or terminal-outcome derivation. Those responsibilities remain in `.tomtit/dogfood/run_loop.py` and its frozen children.

No CLI source constructs or invokes `git add`, `git commit`, `git reset`, `git stash`, `git checkout`, `git push`, or `git merge`.

### Subprocess contract

The observed call used a tuple argv and `shell=False`, `check=False`, `stdin=DEVNULL`, and captured stdout/stderr. The argv preserved canonical order and propagated:

- changed paths: `z.py`, `a.py`, `m.py` after first-occurrence deduplication;
- check commands: `check one`, `check two`;
- side-effect paths: `cache/z`, `cache/a` after first-occurrence deduplication;
- implementation timeout `31`;
- review timeout `17`;
- loop run ID;
- evidence root;
- `--allow-one-repair`.

The CLI reused the adapter's public fail-closed path canonicalizer. It did not add changed paths, side-effect roots, check commands, or repair permission on the ordinary argparse path. CLI request/result dataclasses are frozen and slotted, and collection fields are tuples. Finding CLI-V0-003 is the exception at the exported raw-input boundary.

### Runtime outcome handling

Direct fake-runner probes, with no real model invocation, produced:

| Case | Reported terminal status | Result |
|---|---|---|
| Valid same-run result with `COMPLETED` | `COMPLETED` | Pass |
| Exit 0 + stdout `TERMINAL_OUTCOME=COMPLETED`, no result file | `RUNTIME_RESULT_UNAVAILABLE` | Pass |
| Malformed result JSON | `RUNTIME_RESULT_UNAVAILABLE` | Pass |
| Different `loop_run_id` | `RUNTIME_RESULT_UNAVAILABLE` | Pass |
| Unknown terminal outcome | `RUNTIME_RESULT_UNAVAILABLE`, `INVALID_RUNTIME_RESULT` | Pass |
| Non-zero runtime exit, no result | `RUNTIME_RESULT_UNAVAILABLE`, exit preserved as 7 | Pass |
| Runtime launch `OSError` | `RUNTIME_LAUNCH_FAILURE` | Pass |

Stdout/stderr are never parsed into a terminal outcome. A trusted terminal status comes only from `evidence_root/run_id/loop-result.json`, with the expected `loop_run_id` and a known frozen-runtime outcome.

### Input-validation matrix

The following were rejected before runtime launch: missing workspace, non-directory workspace, missing/unreadable/non-UTF8/empty prompt, missing/parent/absolute/`.git`/symlink-escaping changed paths, parent/absolute/`.git`/symlink-escaping side-effect paths, invalid verifier mode, zero/negative timeout, malformed run ID, a non-empty evidence collision, and a lexically in-workspace evidence directory.

The final evidence-path symlink escape is the blocking exception documented in CLI-V0-002.

### Self-target A–G matrix

| Case | Direct result | Expected | Status |
|---|---|---|---|
| A. TOMTIT worktree root | Rejected | Rejected | Pass |
| B. TOMTIT worktree `src/` | Accepted | Rejected | **Fail** |
| C. TOMTIT worktree `tests/` | Accepted | Rejected | **Fail** |
| D. Other same-repository worktree root | Rejected | Rejected | Pass |
| E. Other same-repository worktree descendant | Accepted | Rejected | **Fail** |
| F. Unrelated repository root | Accepted | Accepted | Pass |
| G. Normal subdirectory in unrelated repository | Accepted | Accepted | Pass |

### Verifier modes

- `NONE`: explicitly rejected with `UNSUPPORTED_VERIFIER_MODE` before subprocess launch.
- `MECHANICAL`: explicitly rejected with `UNSUPPORTED_VERIFIER_MODE` before subprocess launch.
- `INDEPENDENT_CORRECTNESS`: maps to the frozen `run_loop.py` argv.
- Observed subprocess calls for the two unsupported modes: 0.

### Direct execution

`/usr/local/bin/python3 -m tomtit_cli run ...` imported successfully and stopped at harmless workspace validation with exit 2, so the claimed repository-local bootstrap is valid.

`python -m tomtit_cli_v0 ...` is structurally intended by `src/tomtit_cli_v0/__main__.py` and works when the package is installed or `src` is on `PYTHONPATH`. As expected for the current deferred-packaging src layout, it is not importable directly from an uninstalled repository root without `PYTHONPATH=src`. Required `__init__.py`, `__main__.py`, and imports exist.

## Test and tooling results

The sandboxed first runs of Python/pytest suites aborted with OpenMP shared-memory error 179 (exit 134). Those failures are preserved in the full log. Approved out-of-sandbox reruns were then used for correctness results.

| Command | Correctness result |
|---|---|
| `/usr/local/bin/python3 -m pytest tests/cli_v0 -q` | Pass |
| `/usr/local/bin/python3 .tomtit/dogfood/test_run_review.py` | Pass |
| `/usr/local/bin/python3 .tomtit/dogfood/test_run_repair.py` | Pass |
| `/usr/local/bin/python3 .tomtit/dogfood/test_run_loop.py` | Pass |
| `/usr/local/bin/python3 -m pytest tests/adapters -q` | Pass |
| `/usr/local/bin/python3 -m pytest -q` | **Fail: one adapter timing/process-cleanup test** |
| `/usr/local/bin/python3 -m compileall -q src tests .tomtit/dogfood tomtit_cli.py` | Pass |
| `/usr/local/bin/python3 -m ruff check src tests tomtit_cli.py` | Pass |
| `git diff --check` | Pass |

The full suite's only failure was `test_delayed_descendant_write_is_prevented_before_final_snapshot`: it observed adapter status `FAILED` instead of `SUCCEEDED`. The same test passed in the dedicated adapter suite and passed again in an isolated diagnostic. It is not in the additive CLI file set, but the required full-suite first result remains a failure and is reported as such.

VERDICT=BLOCKER
ADDITIVE_ONLY=PASS
PRE_EXISTING_FILES_MODIFIED=0
PRE_EXISTING_FILES_DELETED=0
THIN_FACADE=PASS
FROZEN_RUNTIME_AUTHORITY=PASS
EXPLICIT_ARGV=PASS
SHELL_FALSE=PASS
OUTCOME_AUTHORITY=PASS
INPUT_VALIDATION=FAIL
SELF_REPO_ROOT_REJECTED=YES
SELF_REPO_DESCENDANT_REJECTED=NO
SELF_WORKTREE_ROOT_REJECTED=YES
SELF_WORKTREE_DESCENDANT_REJECTED=NO
UNRELATED_REPOSITORY_ALLOWED=YES
BOOTSTRAP_EXECUTION=PASS
NONE_MODE=EXPLICITLY_UNSUPPORTED
MECHANICAL_MODE=EXPLICITLY_UNSUPPORTED
INDEPENDENT_CORRECTNESS_MODE=PASS
EVIDENCE_COLLISION=PASS
NEW_FEATURE_TESTS=PASS
FULL_REGRESSION=FAIL
REAL_MODEL_INVOCATIONS=0
PERSONAL_WEB_TOUCHED=NO
COMMIT_CREATED=NO
NEXT_ACTION=Fix the three blocking validation defects, add regression tests, and rerun the full matrix.
