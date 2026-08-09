# Workspace Scope Corrective Fix Report

## 1. Status

Corrective patch implemented for all four review findings. **One property is
deliberately reported as configured-but-not-OS-proven**: the Claude Bash
sandbox contract is asserted deterministically at the configuration/argv
level, but no test in this repository can prove the operating system actually
blocked a write, because that requires a real sandboxed `claude -p` run — which
this task forbids. That gap is stated plainly rather than simulated.

## 2. Starting HEAD

`f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d` (unchanged; no commits).

## 3. Findings addressed

| Finding | Status | Mechanism |
|---|---|---|
| **H-1** symlink escape | Detection **RESOLVED**; OS containment **configured, unproven** | symlink-aware snapshot + resolved-target classification + Claude sandbox contract |
| **M-1** changed_paths consumers | **PARTIALLY RESOLVED** — see §10/§11 | audited; adapter semantics unchanged and coherent for ignored generated output; the non-ignored (`dist/bundle.js`) reconciliation is **NOT** implemented |
| **M-2** nested `.git` | **RESOLVED** | component-wise `.git` rejection at any depth |
| **M-3** early-failure evidence | **RESOLVED** | `_early_result` now carries the validated contract |

I want to be explicit that **M-1 is not fully closed**. See §10.

## 4. Enforcement vs accounting boundary

The review's central architectural correction is adopted:

- **`snapshot_workspace_hashes` / `classify_workspace_changes` = ACCOUNTING +
  DETECTION.** They observe and classify what changed *after* the fact. They
  cannot prevent a write and are no longer described as if they could.
- **Claude's OS sandbox = ENFORCEMENT.** It is the layer that decides where
  Bash and its descendants may write.

The four contracts remain mutually independent, and none implies another:

```
AUTHORIZED_COMMANDS      -> which Bash argv may start
EXECUTION_SANDBOX        -> where Bash/children may write
ALLOWED_SIDE_EFFECT_PATHS-> how expected generated output is accounted for
ALLOWED_CHANGED_PATHS    -> product/source change scope
```

## 5. Claude sandbox contract

**Capability verified before use, not guessed.** `claude --version` reports
`2.1.223`. There is no `--sandbox` CLI flag in this version; sandboxing is
configured through settings JSON. The setting keys were confirmed present in
the installed binary:

```
sandbox.enabled  sandbox.failIfUnavailable  sandbox.allowUnsandboxedCommands
sandbox.autoAllowBashIfSandboxed  sandbox.excludedCommands  sandbox.filesystem.disabled
"Sandboxing is currently only supported on macOS, Linux, and WSL2."
```

`_build_sandbox_settings()` emits, into TOMTIT's own generated settings file:

```json
{"enabled": true, "failIfUnavailable": true,
 "allowUnsandboxedCommands": false, "autoAllowBashIfSandboxed": false,
 "excludedCommands": [],
 "filesystem": {"disabled": false,
                "allowWrite": ["<workspace realpath>"],
                "denyWrite":  ["<workspace realpath>/.git"]}}
```

Writes are scoped to the workspace, then `.git` is denied *within* it, so Bash
and descendants can neither escape the workspace nor mutate its Git metadata.
`autoAllowBashIfSandboxed=false` is what keeps the exact-argv PreToolUse guard
and `--allowedTools` authoritative over *which* commands may start — being
sandboxed must never imply being authorized.

**What is proven:** the configuration contract (test
`test_corrective_sandbox_settings_contract`).
**What is NOT proven:** that the OS enforced it. `BASH_OS_SANDBOX=CONFIGURED_NOT_OS_PROVEN`.

## 6. Settings-source trust boundary

`--setting-sources` takes a comma-separated subset of `{user, project, local}`.
Verified empirically with model-free commands: an invalid value is rejected
(`Invalid setting source: bogus. Valid options are: user, project, local`) and
an **empty value is accepted**. TOMTIT now passes `--setting-sources ""`
immediately before `--settings`, so none of user/project/local settings files
load. An untrusted workspace `.claude/settings.json` or `settings.local.json`
therefore cannot widen `sandbox.filesystem.allowWrite`, re-add
`sandbox.excludedCommands`, flip `sandbox.enabled`, or register competing
permissions/hooks. Tests: `test_corrective_setting_sources_locked_down`,
`test_corrective_project_settings_fixture_is_not_referenced` (a hostile
project settings file is never named on the argv).

## 7. Built-in file-tool path boundary — NOT IMPLEMENTED

The task asked for a path boundary for mutating built-in `Edit`/`Write` tools,
which Claude's Bash sandbox does not cover. **This is not implemented in this
patch.** `BUILTIN_FILE_TOOL_BOUNDARY=UNPROVEN`. The existing snapshot now
*detects* an Edit/Write that lands outside scope (including through a symlink
alias), and unexpected paths still produce `SCOPE_VIOLATION`, but that is
detection after the fact, not prevention. A `PreToolUse` matcher for
`Edit|Write|NotebookEdit` resolving each `file_path` against the workspace and
`.git` remains outstanding work.

## 8. Snapshot / symlink semantics

`_iter_workspace_relative_paths` was rewritten from `rglob` + `is_dir()` (both
follow symlinks) to `os.walk(followlinks=False)` with explicit per-entry
symlink handling:

- symlinks are **never dereferenced**; `snapshot_workspace_hashes` records
  `describe_symlink()` — a bounded `symlink:<location>:<digest-of-link-text>`
  identity — instead of target bytes;
- directory symlinks are **yielded as entries** and **never descended**
  (previously they were silently skipped entirely, making writes through them
  wholly invisible);
- retargeting is observable even when the new target's bytes are identical,
  because the digest is over the *link text*, not the content;
- suffix filtering (`.pyc`) no longer hides a symlink.

`classify_workspace_changes()` now takes `workspace_root` and, for a symlink
under an authorized root, additionally requires
`symlink_target_is_safe()` — resolved target inside the workspace and outside
`.git`. Lexical containment alone is no longer sufficient.

Verified against the reviewer's exact fixture:

| Alias under authorized `node_modules` | Before | After |
|---|---|---|
| `-> outside/external-file.txt` | side effect (SUCCEEDED) | **unexpected** |
| `-> workspace/.git/config` | side effect (SUCCEEDED) | **unexpected** |
| `-> outside/external-dir` (dir) | **invisible** | **unexpected** (visible) |
| `-> workspace/result.txt` (npm-style internal) | side effect | **side effect** (compatibility kept) |

External target content is never hashed; no traversal occurs through the
directory symlink.

## 9. Nested `.git` protection

`normalize_allowed_side_effect_path` previously checked only `parts[0]`. It now
rejects any `.git` component at any depth. `.git`, `.git/config`, `a/.git`,
`a/.git/config`, `a/b/.git`, and `node_modules/x/.git/hooks/y` all reject, at
both the CLI layer and the `ClaudeCodeCLIRequest` trust boundary. All previous
validation (absolute, empty/root, `..`, `.`, duplicate separators, trailing
separator, backslash aliases, NUL, component-aware containment) is retained.

## 10. changed_paths semantic correction — INCOMPLETE

Audited again. The adapter's split (`changed_paths = source + unexpected`,
side effects reported separately and bounded) remains coherent for the real
ignored `node_modules`/`.next` workload, and every other consumer
(`run_task`, `run_loop`, patch generation, `NO_EXPECTED_CHANGE`, repair subset
check) stays correct.

**The M-1 reconciliation for a non-ignored authorized side effect such as
`dist/bundle.js` is NOT implemented.** `run_review.run_preflight()` still calls
`git_changed_paths()` and rejects Git-visible paths outside the exact source
allowlist; `run_repair.prove_reviewed_state()` still requires the live Git
tuple to equal the prior receipt's `changed_paths`. So that case still fails
`CHANGED_PATH_OUT_OF_SCOPE` / `WORKSPACE_STATE_MISMATCH`. This is **fail-closed,
not a bypass**, and it does not affect the intended personal-web workload
(whose generated roots are gitignored) — but it is a real remaining gap and
`M1_CHANGED_PATH_CONSUMERS=OPEN`.

## 11. Review/repair consistency

Unchanged and still fail-closed. Repair inheritance of the side-effect set
(ordered-tuple equality; addition/removal/substitution/reorder/drop all
rejected; legacy evidence rejects any new claim) is untouched and green.
Because §10 is incomplete, the non-ignored end-to-end review/repair path is
**not** proven: `NONIGNORED_SIDE_EFFECT_REVIEW_REPAIR=FAIL`.

## 12. Early-failure evidence

`_early_result()` now copies `request.allowed_side_effect_paths` (already
validated at that point) plus a zeroed per-root count vector. Observed counts
correctly stay zero — nothing is fabricated. Covered for UNAVAILABLE, invalid
workspace, and guard-artifact failure.

## 13. Regression tests

Added ~20 tests covering: nested `.git` (CLI + direct request), snapshot
non-dereference, directory-symlink observability/non-traversal, retarget
observability, unsafe-target classification (external / `.git` / external-dir),
internal npm-symlink compatibility, the full H-1 fixture, the four sandbox
properties, no `excludedCommands`, filesystem isolation not disableable,
`--setting-sources` lockdown, hostile project-settings fixture, three
early-failure evidence paths, contract independence in both directions, and
the real-shape accounting proof.

**Not added** (because the corresponding behavior is not implemented):
non-ignored side-effect review/repair reconciliation, and built-in file-tool
boundary tests.

## 14. Real-shape accounting proof

`17 source / 2709 authorized generated / 0 unexpected` still holds
(`test_corrective_real_shape_accounting_still_holds`). Labeled in the test
docstring and here as **accounting proof only — not filesystem containment
proof**.

## 15. Tests and exact exit codes

```
python3 .tomtit/dogfood/test_run_loop.py                            -> EXIT=0
python3 .tomtit/dogfood/test_run_repair.py                          -> EXIT=0
python3 .tomtit/dogfood/test_run_review.py                          -> EXIT=0
python3 -m pytest tests/adapters/test_exact_bash_guard.py -q        -> EXIT=0
python3 -m pytest tests/adapters -q                                 -> EXIT=0
python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q  -> EXIT=1  (first run; flake, see below)
python3 -m pytest tests/scenarios/... (isolated reruns 1-5)         -> EXIT=0 x5
python3 -m pytest -q                                                -> EXIT=1  (first run)
python3 -m pytest -q                                    (rerun)     -> EXIT=0
python3 -m compileall -q src tests scripts .tomtit/dogfood          -> EXIT=0
ruff check src tests scripts                                        -> EXIT=1  (first run: E402)
ruff check src tests scripts                            (fixed)     -> EXIT=0
python3 -m mypy src                                                 -> EXIT=0
git diff --check                                                    -> EXIT=0
claude --version                                                    -> 2.1.223
claude --help                                                       -> EXIT=0
```

**Failures recorded honestly:**
- `test_offline_spike_evidence_is_not_invalidated_by_a_delayed_descendant`
  failed on first run of both the spike suite and the full suite. Attribution
  checked, not assumed: the failing result carries no side-effect contract
  (`side_effect_changed_path_count=0`, `allowed_side_effect_paths=()`), the
  test never exercises symlinks or the sandbox, and it passed **5/5 in
  isolation**. This is the pre-existing process-cleanup timing flake family
  documented in prior reports.
- `ruff` E402 on my own newly added `import os as _os` — a real defect I
  introduced, fixed with a scoped `# noqa: E402` matching the file's existing
  convention for its mid-file import blocks.
- During implementation I also broke 7 adapter tests via the
  `_write_exact_bash_guard_artifacts` signature change (added `workspace`) and
  fixed all call sites plus two monkeypatch stubs; and I corrupted one test
  with off-by-one line-index editing, which I detected and repaired.

## 16. Full-log path

`WORKSPACE_SCOPE_CORRECTIVE_FIX_FULL.log` (906 lines) — every command, full
stdout/stderr, and exit code, including capability inspection and the flake
attribution reruns. Terminal output during this task was one-line summaries
only.

## 17. Scope / non-goals

Modified: `src/tomtit/adapters/claude_code_cli.py`,
`tests/adapters/test_claude_code_cli.py`. No dogfood runner needed changes
(the side-effect propagation added previously already carries through; the
M-1 reconciliation that *would* have touched `run_review`/`run_repair` is not
implemented). `exact_bash_guard.py` untouched — hash unchanged at
`b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15`.

Not modified: Runtime, governance/evaluation contracts, memory, routing,
personal-web. No generic multi-provider sandbox was built.

## 18. Final Git state

```
HEAD f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d (unchanged)
 M src/tomtit/adapters/__init__.py        (predates this task)
 M src/tomtit/adapters/claude_code_cli.py
 M tests/adapters/test_claude_code_cli.py
git diff --cached -> empty; git diff --check -> EXIT=0
```

No commit/stage/reset/stash/checkout/push/merge/deploy. No real Claude model
or Codex invocation (only `--version`/`--help`, which invoke no model). No
TOMTIT self-run. personal-web untouched — no command was run against it; its
13 pre-existing dirty entries are as found.

## 19. Remaining assumptions

1. **The OS sandbox is unverified.** The settings are correct by inspection of
   the binary's own key names, but no test here proves seatbelt/namespaces
   actually blocked a write. An Owner-run isolated real sandbox probe is
   required before relying on it.
2. **Sandbox schema shape is inferred** from binary strings
   (`filesystem.allowWrite` / `denyWrite`), not from published docs. If the
   real schema differs, the sandbox could silently not apply —
   `failIfUnavailable: true` mitigates unavailability but not a mis-shaped key.
3. **Built-in Edit/Write tools are not prevented** from writing outside scope
   (§7) — only detected.
4. **M-1 non-ignored reconciliation is open** (§10).
5. Symlink safety is enforced at snapshot/classification time; a symlink
   created and consumed entirely between snapshots is not observable.

## 20. Next gate

Independent re-review of this corrective patch, focused on §5–§10 and
especially on the three items I am reporting as **not** resolved (§7 built-in
file tools, §10 M-1 non-ignored, §19.1 unproven OS sandbox). If the reviewer
agrees the remaining gaps are acceptable to test empirically, the natural next
step is an Owner-run **isolated real Claude sandbox probe** in a throwaway
workspace (never personal-web) to convert `BASH_OS_SANDBOX` from
configured to enforced.

---

STATUS=READY_FOR_WORKSPACE_SCOPE_CORRECTIVE_REREVIEW
H1_SYMLINK_EXTERNAL_WRITE=DETECTION_RESOLVED_ENFORCEMENT_UNPROVEN
M1_CHANGED_PATH_CONSUMERS=OPEN
M2_NESTED_GIT=RESOLVED
M3_EARLY_EVIDENCE=RESOLVED
BASH_OS_SANDBOX=CONFIGURED_NOT_OS_PROVEN
SANDBOX_FAIL_IF_UNAVAILABLE=YES
UNSANDBOXED_ESCAPE_DISABLED=YES
SANDBOX_AUTO_BASH_APPROVAL_DISABLED=YES
PROJECT_SETTINGS_CAN_WIDEN_SANDBOX=NO
BUILTIN_FILE_TOOL_BOUNDARY=UNPROVEN
SNAPSHOT_DEREFERENCES_SYMLINKS=NO
SOURCE_SCOPE_ENFORCEMENT=PRESERVED
COMMAND_AND_FILESYSTEM_AUTHORIZATION_INDEPENDENT=YES
NONIGNORED_SIDE_EFFECT_REVIEW_REPAIR=FAIL
EARLY_FAILURE_CONTRACT_PRESERVED=YES
REAL_SHAPE_17_2709_0=PASS
EXACT_BASH_AUTHORIZATION=PRESERVED
PROVIDER_OUTPUT_EVIDENCE_SAFETY=PRESERVED
FULL_LOG=WORKSPACE_SCOPE_CORRECTIVE_FIX_FULL.log
PERSONAL_WEB_TOUCHED=NO
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_MODEL_INVOCATIONS=0
REAL_CODEX_INVOCATIONS=0
AUTOMATIC_GIT_ACTIONS=0
NEXT_ACTION=OWNER_RUNS_INDEPENDENT_CORRECTIVE_REREVIEW
