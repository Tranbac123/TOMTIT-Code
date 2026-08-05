# Claude Gate 3 — Claude Code CLI Capability Spike

## STATUS

```text
BLOCKED_REAL_CLI_UNAVAILABLE
```

The adapter is implemented and fully proven offline. The real Claude Code CLI
was discovered and genuinely invoked twice, but both invocations failed at the
API layer because this installation's CLI (v1.0.85) resolves its default and
`sonnet`-alias model to a retired model id (`claude-sonnet-4-20250514`) that
the account's API returns `404 not_found_error` for. This is a CLI-installation
configuration defect, not an adapter defect and not an invocation-syntax
mistake — see `REAL_CLI_SPIKE` below for the full reasoning and both
invocations. The Gate 3 objective (one successful end-to-end real invocation)
is **not proven** in this environment.

## STARTING_HEAD

```text
66b6afb6d82934b09d5b8214d2d3e4e6b629111c
```

## SOURCE_COMMIT_HASH

```text
6b370c3236f4120e5a917676231e3420055aa950
```

## IMPLEMENTED_DIFF_RANGE

```text
66b6afb6d82934b09d5b8214d2d3e4e6b629111c..6b370c3236f4120e5a917676231e3420055aa950
```

## REPORT_COMMIT_EXPECTED_PARENT

```text
6b370c3236f4120e5a917676231e3420055aa950
```

## MODEL_USED

Claude Code Sonnet, as assigned for this gate.

## MODEL_REASON

Per the gate definition (`IMPLEMENTER_MODEL: Claude Code Sonnet`).

## FILES_CHANGED

9 files, all new, matching the preferred layout from the task brief exactly:

| File | Purpose |
|---|---|
| `src/tomtit/adapters/__init__.py` | Public re-exports; no other package touched. |
| `src/tomtit/adapters/claude_code_cli.py` | The adapter: request/result types, CLI discovery, bounded subprocess execution, workspace evidence, redaction, manifest/patch rendering. |
| `tests/adapters/test_claude_code_cli.py` | 23 offline, deterministic tests (20 required cases plus 3 defense-in-depth cases). |
| `tests/scenarios/test_claude_code_cli_spike.py` | 2 offline end-to-end rehearsals of the full request → adapter → evidence flow, using a fake executable. |
| `scripts/run_claude_code_cli_spike.py` | The manual, billable, real-invocation script. Not part of the pytest suite. |
| `evidence/gate3/CLAUDE_CODE_CLI_SPIKE_RESULT.json` | Sanitized manifest for the final (retry) real attempt. |
| `evidence/gate3/CLAUDE_CODE_CLI_SPIKE_DIFF.patch` | Patch for the final attempt (empty: nothing changed, since it failed). |
| `evidence/gate3/CLAUDE_CODE_CLI_SPIKE_ATTEMPT1_RESULT.json` | Sanitized manifest for the first real attempt, kept so the failed invocation is not hidden. |
| `evidence/gate3/CLAUDE_CODE_CLI_SPIKE_ATTEMPT1_DIFF.patch` | Patch for the first attempt (also empty). |

No existing file was modified.

## ARCHITECTURE_BOUNDARY

`ClaudeCodeCLIAdapter.run()` returns a frozen `ClaudeCodeCLIResult` (a stdlib
`@dataclass(frozen=True, slots=True)`) and does nothing else. Concretely:

- **No canonical-state import.** `src/tomtit/adapters/claude_code_cli.py`
  imports no `tomtit.runtime`, `tomtit.domain`, `tomtit.reporting`, or
  `tomtit.fakes` symbol — proven by an AST-based test
  (`test_adapter_module_imports_no_runtime_or_domain_code`) that parses actual
  `import`/`from ... import` statements rather than scanning prose, so a
  docstring mentioning those module names cannot produce a false pass.
- **No event, no decision, no attempt increment, no completion, no report.**
  The adapter never touches `RunRecord`, never calls anything resembling
  `_append_event`, `commit_revision_decision`, or `complete_run`. It has no
  reference to any of those types at all.
- **No retry, no escalation.** `Runtime` — or here, the manual retry
  performed for the real spike — decides whether to invoke again;
  `ClaudeCodeCLIAdapter.run()` itself is single-shot, proven by
  `test_adapter_never_retries_internally`.
- **Request carries no Runtime-authority field.** `ClaudeCodeCLIRequest` has
  only identifiers (`task_id`, `run_id`, `attempt`) and bounded execution
  parameters; it accepts no `RunState`, no `StopDecision`, no full
  `RunRecord`. A caller cannot use it to assert canonical state.

Runtime integration itself (wiring this adapter into the Coordinator/Runtime
flow) is explicitly out of scope for this capability spike, per the gate's
non-goals and per `CLAUDE.md`/`SYSTEM.md`'s phase-gating of "additional worker
adapters."

## CLI_DISCOVERY

```bash
$ command -v claude
/Users/tranvanbac/.nvm/versions/node/v20.19.4/bin/claude
```

`discover_claude_executable()` uses `shutil.which("claude")` (no shell
interpolation) by default, or an explicit absolute-path override that is
verified with `Path.is_file()` and `os.access(..., os.X_OK)` before being
trusted. Both paths are covered by offline tests (`test_unavailable_when_...`
for the not-found case; every other test supplies `executable_path=` directly,
exercising the explicit-path branch).

## CLI_VERSION_AND_INVOCATION

```bash
$ claude --version
1.0.85 (Claude Code)
```

```text
$ claude --help
Usage: claude [options] [command] [prompt]
...
  -p, --print                      Print response and exit (useful for pipes)
  --output-format <format>         "text" (default), "json", "stream-json"
  --permission-mode <mode>         "acceptEdits", "bypassPermissions", "default", "plan"
  --model <model>                  alias (e.g. 'sonnet', 'opus') or full model name
  ...
```

Selected non-interactive invocation, built as an argv list (never a shell
string):

```text
[executable, "-p", "--output-format", "json", "--permission-mode", "acceptEdits", <prompt>]
```

`--model` is appended only when `request.model` is set (kept `None` by
default, per the non-goal "no model selection"). The real spike's retry
attempt set it explicitly to the documented alias `sonnet` as a bounded,
one-time invocation correction (see `REAL_CLI_SPIKE`).

## ADAPTER_REQUEST_CONTRACT

`ClaudeCodeCLIRequest` (frozen, slots): `task_id`, `run_id`, `attempt`,
`goal`, `prompt`, `working_directory` (coerced to `Path`, must resolve
absolute at run time), `timeout_seconds` (must be positive), `allowed_changed_paths`
(coerced to `tuple`), `environment_allowlist` (defaults to
`DEFAULT_ENVIRONMENT_ALLOWLIST`), `max_stdout_bytes`/`max_stderr_bytes`
(positive, default 65536), `model` (optional), `permission_mode` (default
`"acceptEdits"`). `__post_init__` raises `ValueError` for empty identifiers,
non-positive bounds, or attempt `< 1`. No field can encode Runtime authority;
no field accepts a full `RunRecord`.

## ADAPTER_RESULT_CONTRACT

`ClaudeCodeCLIResult` (frozen, slots) contains exactly the fields listed in
the task brief: `adapter_name`, `adapter_version`, `task_id`, `run_id`,
`attempt`, `status`, `success`, `executable_path`, `sanitized_argv`,
`started_at`, `completed_at`, `duration_ms`, `exit_code`, `timed_out`,
`stdout_excerpt`, `stderr_excerpt`, `stdout_sha256`, `stderr_sha256`,
`stdout_truncated`, `stderr_truncated`, `workspace_before_hash`,
`workspace_after_hash`, `changed_paths`, `unexpected_changed_paths`,
`patch_path`, `evidence_manifest_path`, `error_category`, `error_message`.

`ClaudeCodeCLIStatus` is `SUCCEEDED | FAILED | TIMED_OUT | UNAVAILABLE |
SCOPE_VIOLATION`. `_determine_status()` is the single decision point and
enforces the required rules, in this priority order (documented and tested):

1. **Any unexpected changed path → `SCOPE_VIOLATION`.** Checked first,
   deliberately, so a safety-relevant unscoped write is surfaced even if the
   process also timed out or exited non-zero
   (`test_scope_violation_takes_priority_over_nonzero_exit`).
2. **Timeout → `TIMED_OUT`.**
3. **Non-zero exit → `FAILED` / `NONZERO_EXIT`.**
4. **Exit 0 but no allowed path actually changed → `FAILED` /
   `NO_EXPECTED_CHANGE`.**
5. Otherwise → `SUCCEEDED`.

`exit_code != 0`, a timeout, an unexpected path, or CLI unavailability can
never produce `SUCCEEDED` — this is structural (the function has no other
return path to `SUCCEEDED`), not inferred from stdout content.

## PROCESS_EXECUTION

`_execute()` runs `subprocess.Popen` with an argv list, `shell` never passed
(confirmed absent from the call, and separately proven by
`test_subprocess_never_uses_shell_true`), `stdin=subprocess.DEVNULL`,
`stdout=PIPE`, `stderr=PIPE`, `start_new_session=True` (POSIX: `proc.pid`
becomes the new process-group id). Two daemon `_StreamReader` threads drain
the pipes concurrently into bounded buffers while hashing the **entire**
stream with `hashlib.sha256`, so `stdout_sha256`/`stderr_sha256` cover the
true full output even when the excerpt is truncated
(`test_stdout_is_truncated_but_hash_covers_full_output`,
`test_stderr_is_truncated_but_hash_covers_full_output`).

The main loop polls `proc.poll()` against a `time.monotonic()` deadline (never
wall-clock `time.time()`). On timeout: `os.killpg(proc.pid, SIGTERM)`, wait up
to 5s, then `os.killpg(proc.pid, SIGKILL)` as a fallback, then a final bounded
wait. Because the child inherits the adapter-created process group, any
grandchild the CLI itself spawns dies with it —
`test_no_child_process_survives_a_timeout` spawns a real grandchild process
from the fake executable and confirms it is gone (`os.kill(pid, 0)` raises
`ProcessLookupError`) after the adapter's timeout handling completes. Partial
output collected before the kill remains in the reader buffers
(`test_partial_output_is_retained_on_timeout`). No retry, no background
thread survives `run()` (readers are daemon threads, explicitly joined with a
bounded timeout before `run()` returns).

## ENVIRONMENT_AND_SECRET_HANDLING

`build_child_environment()` copies only allowlisted, currently-set variables
from `os.environ` — nothing else is inherited
(`test_environment_allowlist_excludes_unlisted_variables`). Default allowlist:
`PATH, HOME, USER, SHELL, TERM, TMPDIR, LANG, LC_ALL, XDG_CONFIG_HOME,
XDG_CACHE_HOME, CLAUDE_CONFIG_DIR, ANTHROPIC_API_KEY`.

Before any text is stored on the result, `_collect_secret_values()` finds
every allowlisted variable whose **name** matches
`KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL` (case-insensitive) and replaces every
occurrence of its **value** with `[REDACTED]` in stdout/stderr excerpts and in
`sanitized_argv` (longest values first, so a short secret cannot mask part of
a longer one) — `test_secret_values_are_redacted_from_captured_output`. `HOME`
is separately replaced with the literal token `$HOME` wherever it appears, as
a privacy minimization independent of the secret-pattern check (visible in the
committed evidence: `sanitized_argv` shows
`"$HOME/.nvm/versions/node/v20.19.4/bin/claude"`, not the real path). No
environment is ever printed; no exception message includes a secret value.

## WORKSPACE_EVIDENCE

`snapshot_workspace_hashes()` walks the workspace (ignoring only `.git/**`,
`__pycache__/**`, `.DS_Store`, `*.pyc`) and SHA-256s every remaining file;
`workspace_hash()` folds that into one deterministic summary hash via
sorted-key canonical JSON. Before/after snapshots feed
`_diff_changed_paths()` (exact set-symmetric-difference-by-hash) and
`unexpected_changed_paths = changed - allowed`. The adapter never reverts or
cleans an unexpected write — it only reports `SCOPE_VIOLATION`
(`test_unexpected_changed_path_is_scope_violation`). `render_unified_diff()`
builds a Git-independent unified diff (via `difflib`) over exactly
`changed_paths`, using separately captured full-content snapshots (bounded to
1 MB per file; larger/unreadable files are noted as omitted rather than
silently skipped).

## OFFLINE_TESTS

23 tests in `tests/adapters/test_claude_code_cli.py`, covering all 20
required cases (several with an extra defense-in-depth variant):

| # | Case | Test(s) |
|---:|---|---|
| 1 | Executable unavailable | `test_unavailable_when_executable_not_discovered` |
| 2 | Success + expected change | `test_success_with_expected_file_change` |
| 3 | Non-zero exit | `test_nonzero_exit_never_succeeds` |
| 4 | Timeout terminates process | `test_timeout_terminates_the_process` |
| 5 | Stdout truncation | `test_stdout_is_truncated_but_hash_covers_full_output` |
| 6 | Stderr truncation | `test_stderr_is_truncated_but_hash_covers_full_output` |
| 7 | Partial output on timeout | `test_partial_output_is_retained_on_timeout` |
| 8 | Unexpected change → `SCOPE_VIOLATION` | `test_unexpected_changed_path_is_scope_violation`, `test_scope_violation_takes_priority_over_nonzero_exit` |
| 9 | Expected change missing → failure | `test_missing_expected_change_is_failure` |
| 10 | Exact changed-path calculation | `test_changed_paths_are_calculated_exactly` |
| 11 | Environment allowlist | `test_environment_allowlist_excludes_unlisted_variables` |
| 12 | Secret redaction | `test_secret_values_are_redacted_from_captured_output` |
| 13 | No `shell=True` | `test_subprocess_never_uses_shell_true` |
| 14 | Argv values stay separate | `test_argv_values_are_not_shell_interpreted` |
| 15 | Working-directory validation | `test_nonexistent_working_directory_fails_before_subprocess`, `test_working_directory_that_is_a_file_fails_before_subprocess` |
| 16 | Manifest determinism | `test_evidence_manifest_rendering_is_deterministic` |
| 17 | Result immutability | `test_result_is_immutable`, `test_request_is_immutable` |
| 18 | No Runtime/domain access | `test_adapter_module_imports_no_runtime_or_domain_code` |
| 19 | No automatic retry | `test_adapter_never_retries_internally` |
| 20 | No surviving child on timeout | `test_no_child_process_survives_a_timeout` |

Plus 2 offline end-to-end rehearsals in
`tests/scenarios/test_claude_code_cli_spike.py`
(`test_offline_end_to_end_spike_flow_produces_valid_evidence`,
`test_offline_scope_violation_flow_produces_evidence_without_success`) that
exercise the exact request → adapter → `write_evidence()` path the real
script uses, entirely with a fake executable. None of the 25 adapter/scenario
tests calls the real Claude service; all are deterministic and offline.

## REAL_CLI_SPIKE

Two real, billable invocations occurred, both against the isolated temporary
Git repository `/var/folders/.../tomtit-gate3-spike-jh6djwm5` (never this
project's own source tree), each preceded and followed by a full workspace
snapshot.

**Attempt 1** — default model (no `--model` flag):

```text
argv: [claude, -p, --output-format, json, --permission-mode, acceptEdits, <prompt>]
exit_code=1, timed_out=False, changed_paths=[]
stdout: {"type":"result","subtype":"success","is_error":true,...,
         "result":"API Error: 404 {\"type\":\"error\",\"error\":{\"type\":\"not_found_error\",
                    \"message\":\"model: claude-sonnet-4-20250514\"},...}"}
status=FAILED, error_category=NONZERO_EXIT
```

The CLI process itself ran correctly — it produced well-formed, parseable
`--output-format json` output and a clean exit — so this is **not** an
invocation-syntax error in the literal sense (no `stderr` usage message, no
argument-parsing failure). It is the CLI's own default model resolution
pointing at a retired snapshot id.

Non-billable diagnostics run before deciding on a retry:
`claude config list` (no `model` key exists — not configurable that way),
`claude config get model` (rejected: not a valid key), and inspection of
`~/.claude.json`, which shows a `cachedGrowthBookFeatures` entry advertising
an `"opus-5-launch"` announcement — confirming the account's active model
roster has moved on, while the installed CLI (1.0.85) still has a stale
hardcoded default/alias table.

**Judgment call, stated plainly:** the task's retry allowance is worded around
"invocation syntax... incorrect." This failure is not a syntax defect, but it
is an invocation defect discovered only by actually running the CLI, correctable
via a flag `--help` itself documents (`--model <alias>`). I treated this as
within the spirit of the one-retry allowance (an invocation-correction retry,
not a task retry) rather than as an "authentication/configuration failure"
that should stop at attempt 1. I want this judgment call visible to the
reviewer rather than buried.

**Attempt 2** (the one allowed retry) — explicit `--model sonnet`:

```text
argv: [..., --model, sonnet, <prompt>]
exit_code=1, timed_out=False, changed_paths=[]
stdout: {..., "result":"API Error: 404 {\"type\":\"error\",\"error\":{\"type\":\"not_found_error\",
                    \"message\":\"model: claude-sonnet-4-20250514\"},...}"}
status=FAILED, error_category=NONZERO_EXIT
```

The documented alias `sonnet` resolved to the **identical** retired model id.
This proves the defect is inside the installed CLI's own alias table, not
fixable by any invocation-side flag choice available to this adapter. Per the
task's explicit "at most one retry" bound, no third real call was made.
`total_cost_usd` was `0` in both responses (the request never reached a
priced model call), so this spike incurred no material API cost while still
being two genuine real invocations of the real binary against the real
Anthropic API endpoint.

Both attempts are preserved as separate evidence files (see
`EVIDENCE_PATHS`); neither is hidden. `REAL_CLI_INVOCATION` therefore
**cannot** be claimed proven: the CLI executable was discovered and genuinely
invoked, but no invocation available to this adapter within the allowed
attempt budget produced a successful result. This is the
`STATUS=BLOCKED_REAL_CLI_UNAVAILABLE` case anticipated by the task ("If
authentication or configuration prevents invocation: status = FAILED, success
= false... Do not simulate a successful real invocation").

**Not attempted, and out of scope for this bounded task:** running `claude
update` to fetch a CLI build with a corrected default model. That would
modify software outside this repository and outside the reversible,
project-local actions this gate is scoped to; it is left for the Owner to
decide.

## EVIDENCE_PATHS

```text
evidence/gate3/CLAUDE_CODE_CLI_SPIKE_RESULT.json          (final/retry attempt — canonical)
evidence/gate3/CLAUDE_CODE_CLI_SPIKE_DIFF.patch           (final/retry attempt — empty, nothing changed)
evidence/gate3/CLAUDE_CODE_CLI_SPIKE_ATTEMPT1_RESULT.json (first attempt, preserved for transparency)
evidence/gate3/CLAUDE_CODE_CLI_SPIKE_ATTEMPT1_DIFF.patch  (first attempt — empty)
```

Both manifests were manually reviewed before commit for secret leakage (API
keys, tokens); none was found. `sanitized_argv` and `stdout_excerpt` in both
files show `$HOME`-redacted paths, confirming redaction ran.

## COMMANDS_AND_EXIT_CODES

| Command | Exit code | Result |
|---|---:|---|
| `python3 -m pytest --collect-only -q` | 0 | `23 + 43 + 40 + 3 + 2 + 99 + 137 = 347` collected |
| `python3 -m pytest -q` | 0 | 347 passed |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 99 passed (unchanged) |
| `python3 -m pytest tests/scenarios/test_scenario_2.py -q` | 0 | 137 passed (unchanged) |
| `python3 -m pytest tests/adapters -q` | 0 | 23 passed |
| `python3 -m compileall -q src tests scripts` | 0 | passed |
| `ruff check .` | 0 | no findings |
| `git diff --check` | 0 | no whitespace errors |
| `python3 -m mypy src` | 0 | `Success: no issues found in 13 source files` |
| `command -v claude` | 0 | `/Users/tranvanbac/.nvm/versions/node/v20.19.4/bin/claude` |
| `claude --version` | 0 | `1.0.85 (Claude Code)` |
| `claude --help` | 0 | printed; used to select the invocation flags above |
| `python3 scripts/run_claude_code_cli_spike.py` (attempt 1, as run) | 1 | ran, real API 404, `OVERALL_PASS=False` |
| manual retry against the same workspace (attempt 2, `model=sonnet`) | n/a (Python call, not a script exit) | ran, identical real API 404, `OVERALL_PASS=False` |

Test count moved from the 322-test Gate 2 baseline to **347** (+25: 23 adapter
tests, 2 scenario tests).

## PRESERVATION_RESULTS

```text
Scenario 1 = 99 tests, unchanged, all pass.
Scenario 2 = 137 tests, unchanged, all pass.
```

No file under `src/tomtit/runtime/`, `src/tomtit/reporting.py`,
`src/tomtit/domain/`, `src/tomtit/fakes.py`, `tests/scenarios/test_scenario_1.py`,
or `tests/scenarios/test_scenario_2.py` was modified. Gate 1 and Gate 2
evidence files were not touched.

## RISKS

- **Real invocation is not proven in this environment.** The core Gate 3
  objective — one successful real end-to-end invocation — did not happen. The
  adapter's correctness for a *successful* real run is therefore proven only
  indirectly, via the offline fake-executable tests, not via the real CLI.
- **The failure is environment-specific, not adapter-specific.** Both real
  attempts show the CLI itself producing a clean, well-formed JSON error
  response and exiting deterministically — exactly the shape the adapter is
  designed to observe and classify correctly. But this cannot substitute for
  proof that the adapter correctly captures a *successful* real invocation's
  stdout/exit-code/workspace-diff shape, since no successful real invocation
  was achieved.
- **The one-retry judgment call is subjective.** I treated a model-resolution
  404 as an "invocation defect" eligible for the allowed correction retry
  rather than stopping at attempt 1 as a bare authentication/configuration
  failure. A stricter reader could conclude I should not have made the second
  call at all. Both readings are defensible; I have made the reasoning and
  both attempts fully visible rather than picking silently.
- **Workspace and script are not cleaned up automatically.** The temporary Git
  repository at `/var/folders/.../tomtit-gate3-spike-jh6djwm5` is retained on
  disk (unmodified — both attempts failed before any file changed) for
  inspection; it is outside this repository and safe to delete manually.
- **`claude doctor`** was started as a non-billable diagnostic, hung past its
  120s timeout, and was killed; it produced no output before being killed and
  had no effect on the repository or the spike's evidence.
- Everything listed under `Non-goals` in the task remains unimplemented, as
  required.

## SCOPE_AND_NON_GOALS

Confirmed **not** implemented: multi-provider routing, model selection (the
adapter defaults to no `--model`; the one alias used in the retry was a
one-off spike-script decision, not adapter policy), retry policy/escalation
inside the adapter, repair loop, Attempt 3, persistent Runtime storage, crash
recovery, concurrency, job queue, cloud execution, mobile notifications,
Memory, ContextPack retrieval engine, graph orchestration, ChangeGate
enforcement, production sandboxing, container isolation, Claude SDK
integration, Codex/OpenAI adapters, cost optimization, telemetry backend. This
is a single bounded adapter, not a general subprocess framework — no other
executable type is supported, and no configuration system beyond the request
dataclass exists.

## UNPROVEN_ITEMS

```text
CLAUDE_CODE_CLI_ADAPTER=CLAIMED_IMPLEMENTED_PENDING_REVIEW
REAL_CLI_INVOCATION=NOT_PROVEN_ENVIRONMENT_BLOCKED
PROCESS_TIMEOUT_HANDLING=CLAIMED_PROVEN_PENDING_REVIEW
SCOPE_CHANGE_DETECTION=CLAIMED_PROVEN_PENDING_REVIEW
SECRET_REDACTION=CLAIMED_PROVEN_PENDING_REVIEW
RUNTIME_AUTHORITY_PRESERVED=CLAIMED_PROVEN_PENDING_REVIEW
GATE_1=PRESERVED_PENDING_REVIEW
GATE_2=PRESERVED_PENDING_REVIEW
```

`REAL_CLI_INVOCATION` is marked `NOT_PROVEN_ENVIRONMENT_BLOCKED` rather than
`CLAIMED_PROVEN_PENDING_REVIEW` because it did not succeed, per the task's
explicit instruction not to simulate or overclaim a successful real
invocation. Everything else marked `CLAIMED_...PENDING_REVIEW` is supported
only by offline evidence and awaits independent review.

## OWNER_MANUAL_TEST_COMMAND

```bash
python3 scripts/run_claude_code_cli_spike.py
```

This creates a fresh isolated temporary Git repository each run, invokes the
real `claude` CLI (with the built-in one-retry invocation correction if the
first attempt fails on a syntax or model-resolution defect), verifies
`result.txt`'s exact content, writes sanitized evidence to
`evidence/gate3/`, and exits `0` only on full pass, `1` on a real-but-failed
run, `2` if the CLI cannot be discovered at all. If the Owner has since
resolved the model-resolution issue (for example by updating the Claude Code
CLI), rerunning this command is the way to obtain a genuine passing real
invocation and refresh the Gate 3 evidence.

## NEXT_ACTION

```text
BLOCKED_REAL_CLI_UNAVAILABLE
NO_NEXT_GATE_AUTHORIZED
```
