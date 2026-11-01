# Productized CLI V0 Corrective Patch — Narrow Independent Rereview

## Verdict

**APPROVE**

All three previously blocking findings are resolved under independent direct probes. Previously approved CLI behavior remains intact, the required out-of-sandbox test matrix passes, and the implementation remains additive-only over the frozen baseline.

No new correctness finding was reproduced within the narrow rereview scope.

Full command output, including every preserved sandbox OpenMP failure, is in `CODEX_CLI_V0_NARROW_REREVIEW_FULL.log`.

## Additive-only verification

The three required dimensions pass:

1. `HEAD` and `tomtit-m1-guarded-workspace-v1^{}` both resolve to `cfa4927537b45b40f9badb0f47c7c66e9fca82b9`. Both unstaged and cached name-status diffs are empty, so no baseline tracked file is modified, deleted, or renamed.
2. Baseline tree inspection found none of the current CLI implementation paths. The product implementation consists only of new files under `src/tomtit_cli_v0/`, `tests/cli_v0/`, and `tomtit_cli.py`.
3. Worktree status contains only those expected CLI additions plus the existing independent-review document. No unrelated tracked or untracked implementation path was found overwritten.

The prior review document is review evidence, not product implementation. The rereview created only this report and its ignored `.log` companion; no implementation file was changed.

## CLI-V0-001 — self-target descendants

**RESOLVED.** `_owning_git_common_directory` walks from the candidate workspace through its parents and compares the nearest owning Git common directory with TOMTIT's common directory. This identifies descendants without requiring a local `.git` entry.

Direct tests used the actual current TOMTIT worktree, the actual other linked worktree listed by Git, and a newly initialized unrelated temporary repository:

| Case | Result | Required |
|---|---|---|
| A. Current TOMTIT worktree root | Rejected | Reject |
| B. Current TOMTIT `src/` | Rejected | Reject |
| C. Current TOMTIT `tests/` | Rejected | Reject |
| D. Other same-repository worktree root | Rejected | Reject |
| E. Other same-repository worktree descendant | Rejected | Reject |
| F. Unrelated Git repository root | Accepted | Accept |
| G. Unrelated Git repository descendant | Accepted | Accept |

All rejected self-target cases returned `SELF_REPOSITORY_WORKSPACE`.

## CLI-V0-002 — final evidence path

**RESOLVED.** Validation resolves the final `evidence_root / run_id` path for workspace containment and independently rejects any pre-existing run-specific symlink. The implemented V0 policy consistently rejects symlinks whether they point inside or outside the workspace.

| Case | Result | Required/current policy |
|---|---|---|
| Normal outside evidence root | Accepted | Accept |
| Lexically inside workspace | Rejected | Reject |
| Existing run symlink pointing inside workspace | Rejected | Reject |
| Existing run symlink pointing outside workspace | Rejected | Reject any pre-existing run symlink |
| Existing non-empty run directory | Rejected with `EVIDENCE_COLLISION` | Reject |
| New normal outside run path | Accepted | Accept |

No tested filesystem alias could redirect the trusted run-result location into the target workspace.

## CLI-V0-003 — repair permission boolean

**RESOLVED.** The public `build_request()` boundary requires an actual `bool`, preserves it without coercion, and returns `INVALID_ALLOW_ONE_REPAIR` for every tested non-boolean.

| Value | Result |
|---|---|
| `False` | Accepted and preserved by identity |
| `True` | Accepted and preserved by identity |
| `"false"` | Rejected |
| `"true"` | Rejected |
| `0` | Rejected |
| `1` | Rejected |
| `None` | Rejected |
| `[]` | Rejected |
| `{}` | Rejected |

No generic truthiness coercion remains at the validated request boundary.

## Narrow regression review

Previously approved behavior is preserved:

- The CLI remains limited to product-facing validation/canonicalization, immutable request construction, argv translation, subprocess launch, and persisted-result reading.
- Frozen `.tomtit/dogfood/run_loop.py` remains authoritative for state transitions, implementation/review decisions, repair outcomes, changed-path classification, evidence lifecycle, and terminal outcome.
- Subprocess launch uses a tuple argv and `shell=False`; no shell command string is built.
- Changed paths, side-effect paths, deterministic checks, implementation/review timeouts, run ID, evidence root, and a valid repair flag retain their expected ordering and propagation.
- Exit code 0 plus stdout text claiming completion still reports `RUNTIME_RESULT_UNAVAILABLE` without a persisted same-run result.
- A valid persisted same-run result remains the only source of `COMPLETED`.
- `NONE` and `MECHANICAL` remain explicitly unsupported before subprocess launch.
- `INDEPENDENT_CORRECTNESS` still maps to frozen `run_loop.py`.
- No CLI source generates automatic Git write actions.

## Test results

Each sandboxed Python/pytest first run hit the known OpenMP shared-memory error 179 and exited 134. Those exact first results are preserved in the log. One permitted equivalent out-of-sandbox verification was then performed for each affected command.

| Command | Result outside sandbox |
|---|---|
| `/usr/local/bin/python3 -m pytest tests/cli_v0 -q` | Pass |
| `/usr/local/bin/python3 .tomtit/dogfood/test_run_review.py` | Pass |
| `/usr/local/bin/python3 .tomtit/dogfood/test_run_repair.py` | Pass |
| `/usr/local/bin/python3 .tomtit/dogfood/test_run_loop.py` | Pass |
| `/usr/local/bin/python3 -m pytest tests/adapters -q` | Pass |
| `/usr/local/bin/python3 -m pytest -q` | Pass |
| `/usr/local/bin/python3 -m compileall -q src tests .tomtit/dogfood tomtit_cli.py` | Pass |
| `/usr/local/bin/python3 -m ruff check src tests tomtit_cli.py` | Pass |
| `git diff --check` | Pass |

The prior adapter timing/process flake did not recur in either the dedicated adapter suite or the full suite. The corrective files do not alter the frozen adapter implementation.

VERDICT=APPROVE

CLI_V0_001_SELF_TARGET_DESCENDANTS=RESOLVED
SELF_REPO_ROOT_REJECTED=YES
SELF_REPO_DESCENDANT_REJECTED=YES
SELF_WORKTREE_ROOT_REJECTED=YES
SELF_WORKTREE_DESCENDANT_REJECTED=YES
UNRELATED_REPOSITORY_ALLOWED=YES
UNRELATED_REPOSITORY_DESCENDANT_ALLOWED=YES

CLI_V0_002_EVIDENCE_FINAL_PATH=RESOLVED
EVIDENCE_INSIDE_WORKSPACE_REJECTED=YES
SYMLINKED_RUN_EVIDENCE_INSIDE_WORKSPACE_REJECTED=YES
PREEXISTING_RUN_EVIDENCE_SYMLINK_REJECTED=YES
EVIDENCE_COLLISION_PRESERVED=YES
NORMAL_OUTSIDE_EVIDENCE_ACCEPTED=YES

CLI_V0_003_REPAIR_BOOLEAN=RESOLVED
FALSE_BOOL_ACCEPTED=YES
TRUE_BOOL_ACCEPTED=YES
STRING_FALSE_REJECTED=YES
STRING_TRUE_REJECTED=YES
INTEGER_ZERO_REJECTED=YES
INTEGER_ONE_REJECTED=YES
NONE_REJECTED=YES

PRE_EXISTING_FILES_MODIFIED=0
PRE_EXISTING_FILES_DELETED=0
ADDITIVE_ONLY=PASS

THIN_FACADE=PRESERVED
FROZEN_RUNTIME_AUTHORITY=PRESERVED
SHELL_FALSE=PRESERVED
OUTCOME_AUTHORITY=PRESERVED
VERIFIER_MODE_HANDLING=PRESERVED

NEW_FEATURE_TESTS=PASS
FULL_REGRESSION=PASS_WITH_NOTE
REAL_MODEL_INVOCATIONS=0
PERSONAL_WEB_TOUCHED=NO
COMMIT_CREATED=NO

NEXT_ACTION=Proceed with owner acceptance of the corrected additive CLI V0.
