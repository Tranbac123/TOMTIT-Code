# Exact-Argv PreToolUse Bash Guard: Implementation Report

## 1. Summary

An isolated real Claude CLI probe (transcript reproduced in the Owner's task)
established that Claude Code's native `--allowedTools Bash(<command>)`
permission rule is matched *after* the CLI strips a fixed set of process
wrappers (`command`, `builtin`, `timeout`, `time`, `nice`, `nohup`, `stdbuf`,
zsh `noglob`, certain env-assignment prefixes, bare `xargs`). A bare
`Bash(<command>)` rule therefore proves provider-normalized equivalence, not
TOMTIT's stricter Owner-approved exact-argv contract.

This task adds a second, TOMTIT-controlled enforcement layer: a Claude Code
**PreToolUse hook** for the `Bash` tool, installed via an ephemeral,
session-scoped `--settings` file, that independently re-parses every Bash
tool call with the *exact same* canonical validator
(`validate_authorized_command_argv`) and SHA-256 fingerprint contract
(`compute_authorized_command_fingerprint`) already used by
`ClaudeCodeCLIRequest`. A call is allowed to proceed to Claude's own native
permission engine only when it fingerprint-matches an Owner-approved
authorized command exactly; every wrapper, extra/suffix/prefix variant,
compound command, redirect, substitution, or `run_in_background=True`
request is blocked before execution, with a fixed generic denial reason that
never echoes the evaluated command.

```
TOMTIT exact-argv PreToolUse guard  →  Claude native --allowedTools rule  →  existing permission mode
```

This is defense in depth layered *in front of* Claude's permission system,
never a replacement for it. `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT`
remains `False` and is not flipped by this task -- the feature this guard
defends stays disabled at the same top-level gate as before.

## 2. Files changed

- `src/tomtit/adapters/exact_bash_guard.py` (**new**): the standalone
  PreToolUse hook entry point.
- `src/tomtit/adapters/claude_code_cli.py`: ephemeral settings/fingerprints
  construction and cleanup, `--settings` argv wiring, `run_in_background`-
  aware env injection, provider-JSON Bash-command redaction, new
  `exact_bash_guard_enabled` result/evidence field.
- `tests/adapters/test_exact_bash_guard.py` (**new**): hook-module unit and
  standalone-subprocess tests.
- `tests/adapters/test_claude_code_cli.py`: adapter-side plumbing tests
  (settings/argv wiring, cleanup, env propagation, evidence fields,
  provider-JSON redaction, full end-to-end fake-`claude`-invokes-real-hook
  scenarios).

`src/tomtit/adapters/__init__.py` was not modified further in this task (its
pending diff predates this task, from the prior exact-command validator
work). No Runtime, Coordinator, evaluation, governance, memory,
personal-web, or routing files were touched.

## 3. Design

### 3.1 Hook entry point (`exact_bash_guard.py`)

Lives inside TOMTIT's own source tree at
`src/tomtit/adapters/exact_bash_guard.py`, so the target repository being
worked on never needs any TOMTIT source files copied into it -- the hook
command line always references this file by absolute path.

Because it is invoked as a **standalone subprocess by the real `claude` CLI**
(never imported by TOMTIT's own process), it cannot rely on `PYTHONPATH`
being inherited -- the adapter's child-process environment is a strict
allowlist (`DEFAULT_ENVIRONMENT_ALLOWLIST`) that deliberately excludes it.
The module bootstraps its own import path from its own file location
(`Path(__file__).resolve().parents[2]` -> the `src/` root) on an `ImportError`,
so `import tomtit.adapters.claude_code_cli` succeeds regardless of what
environment invoked it.

Core functions:

- `load_approved_fingerprints(path_text) -> frozenset[str] | None` -- reads
  the ephemeral fingerprints file; `None` on any missing/malformed condition,
  which every caller must treat as fail-closed.
- `evaluate(payload, approved_fingerprints) -> bool` -- `True` means PASS
  (defer), `False` means DENY. Requires `tool_name == "Bash"`, a string
  `tool_input.command`, and `tool_input.run_in_background is not True`; then
  parses the command with `shlex.split`, validates it with
  `validate_authorized_command_argv` (the exact same function
  `ClaudeCodeCLIRequest.__post_init__` uses), and checks
  `compute_authorized_command_fingerprint(argv)` for exact membership in the
  approved set. **No separate wrapper-name denylist exists** -- any wrapper
  changes the parsed argv (so it cannot fingerprint-match) or is rejected
  outright by the shared validator (e.g. `env`, `bash`/`sh`/`zsh`), which is
  why this closes every wrapper class in the Owner's probe without needing
  to special-case `command`, `builtin`, `timeout`, `time`, `nice`, `nohup`,
  `stdbuf`, `noglob`, or `xargs` individually.
- `main()` -- reads stdin, calls `evaluate()`, and on PASS exits `0` with
  **no stdout output at all** (deferring entirely to Claude's native
  permission engine); on DENY, prints the fixed `DENIAL_MESSAGE` to stderr
  and exits `2` (Claude Code's documented PreToolUse blocking contract:
  exit code 2 blocks the tool call and surfaces stderr as the reason).

No code path in this module ever emits a `permissionDecision: allow` (or any
other explicit grant) -- verified both by direct testing of `evaluate()`'s
boolean-only return value and by an AST-level source scan
(`test_module_source_never_emits_an_allow_or_permit_decision`) asserting no
string constant in the module's source resembles a permission-decision
payload.

### 3.2 Ephemeral settings/fingerprints construction (`claude_code_cli.py`)

`_write_exact_bash_guard_artifacts(authorized_commands)`:

1. Creates a fresh `tempfile.mkdtemp(prefix="tomtit-exact-bash-guard-")`
   directory, `chmod 0o700`.
2. Writes `authorized_command_fingerprints.json` -- a JSON array of
   **SHA-256 fingerprints only**, `chmod 0o600`. Never the raw argv.
3. Writes `settings.json` containing a `PreToolUse` hook registration
   matched to `"Bash"`, whose `command` is
   `<shlex.quote(sys.executable)> <shlex.quote(path-to-exact_bash_guard.py)>`
   -- `chmod 0o600`. Contains no raw authorized-command text anywhere.
4. Returns an `_ExactBashGuardArtifacts(directory, settings_path,
   fingerprints_path)` frozen dataclass.

Raises `OSError` on any failure; `run()` catches this and fails closed with
`error_category="EXACT_BASH_GUARD_UNAVAILABLE"` before starting any process
(matches "Fail closed if the hook configuration cannot be constructed
safely").

`_cleanup_exact_bash_guard_artifacts(artifacts)` does a best-effort
`shutil.rmtree(ignore_errors=True)` of the whole temp directory; called in a
`finally` block wrapping the `_execute(...)` call in `run()`, so cleanup
happens as soon as the real subprocess (and everything it spawned, including
any hook invocation) is proven quiescent, and never skipped on an
unexpected exception path.

### 3.3 Wiring into `run()` / `_build_argv()`

When `request.authorized_commands` is non-empty **and** the (still-disabled)
contract resolves to `EXACT_SUPPORTED`:

1. `_write_exact_bash_guard_artifacts(...)` is called; failure fails closed.
2. The fingerprints file path is injected into the child subprocess's
   environment as `TOMTIT_EXACT_BASH_GUARD_FINGERPRINTS_FILE` (added
   directly to the already-built `child_env` dict, not via
   `DEFAULT_ENVIRONMENT_ALLOWLIST`, since it is adapter-injected metadata,
   not a passthrough of the operator's own environment).
3. `_build_argv()` adds `["--settings", <settings_path>]` to the real
   subprocess argv, alongside (never instead of) `["--allowedTools", ...]`.
4. `ClaudeCodeCLIResult.exact_bash_guard_enabled` is set to `True`.

When `authorized_commands` is empty, none of the above runs: no `--settings`
argv, no env var, `exact_bash_guard_enabled=False` -- existing behavior is
unchanged (No-command compatibility requirement).

### 3.4 Provider-JSON Bash-command redaction

Claude's `--output-format json` stream can echo a raw Bash command --
including a *rejected* wrapper/bypass attempt this adapter never itself
authorized -- back inside structures like `permission_denials` or
hook-denial payloads, keyed as `tool_input.command`. The existing
`_redact`/`_redact_home` only strip known environment-secret *values* and
the home directory; neither can find an arbitrary command string nested in
provider-controlled JSON.

`_redact_provider_json_bash_commands(text)` is a **structural**, not
value-based, pass: it attempts `json.loads(text)`; non-JSON text (the common
case) is returned unchanged. If it parses, every dict key literally named
`"command"` anywhere in the structure (however deeply nested) is replaced
with a fixed placeholder (`_REDACTED_PROVIDER_COMMAND_PLACEHOLDER`) before
re-serialization. This is applied to both `stdout_text` and `stderr_text` in
`run()`, **only when `request.authorized_commands` is non-empty** (so the
no-authorized-commands path is byte-for-byte unchanged, satisfying No-command
compatibility), and is layered on top of the pre-existing env/HOME redaction,
never a replacement for it.

### 3.5 Evidence field

`ClaudeCodeCLIResult.exact_bash_guard_enabled: bool = False` (also in
`build_evidence_manifest`) records only whether the guard was installed for
this invocation -- never a raw command, a hook decision's command value, or
any per-call transcript. Per-call hook decision counts are not currently
surfaced by the CLI's JSON output in a form this adapter can safely extract
without risking exactly the raw-command leak this task is designed to
prevent, so they are not implemented; noted as a limitation in §8.

## 4. Security semantics preserved

- **A PASS is never a grant.** `evaluate()` returns a plain `bool`; `main()`
  on PASS exits `0` with empty stdout and empty stderr -- there is no code
  path that can construct or emit an "allow" decision. Claude's own
  permission engine (`--allowedTools`, permission mode) must still
  independently approve the call. Verified by
  `test_pass_return_value_carries_no_decision_payload`,
  `test_module_source_never_emits_an_allow_or_permit_decision`,
  `test_subprocess_exact_match_exits_zero_with_no_output`, and the
  end-to-end `test_exact_bash_guard_end_to_end_defers_for_exact_match`
  (asserts empty hook stdout/stderr on the fingerprint-matching path).
- **A mismatch blocks even if `--allowedTools` would otherwise allow it.**
  The guard runs *before* Claude's own matching in the PreToolUse pipeline
  and exits 2, which Claude Code's documented contract treats as an
  unconditional block for that tool call.
- **The native layer is retained, not replaced.** `--settings` and
  `--allowedTools` are both present in the real subprocess argv whenever the
  guard is active (`test_exact_supported_command_wires_settings_argv_alongside_allowedtools`).
- **No credentials reach the hook.** The hook subprocess is spawned by
  `claude` inheriting only what TOMTIT explicitly passed to `claude`'s own
  `child_env` (the existing `environment_allowlist`, plus the one
  fingerprints-file-path env var this task adds) -- no secret-shaped
  variable is added by this change.
- **No raw authorized command ever reaches disk, the hook's own decision
  output, or TOMTIT evidence.** Verified by
  `test_exact_bash_guard_artifacts_contain_only_fingerprints`,
  `test_exact_bash_guard_fingerprints_env_var_reaches_child_process`,
  `test_subprocess_never_writes_any_file`, and the provider-JSON redaction
  tests in §5.

## 5. Tests

`tests/adapters/test_exact_bash_guard.py` (45 tests) -- pure `evaluate()`
tests for all required wrapper/equivalence/malformed-input classes
(different argv, extra argument, suffix variant, compound command, all nine
named wrappers, env-assignment wrapper, `xargs` wrapper, redirect,
`$()`/backtick substitution, different command, invalid shell syntax,
`run_in_background=True`, non-Bash tool name, missing/non-dict `tool_input`,
non-string/empty command, non-dict payload, missing/empty approved-fingerprint
set); `load_approved_fingerprints` fail-closed shape tests; and a standalone-
subprocess group (`python3 exact_bash_guard.py` invoked as a real child
process, proving the self-bootstrapped import path and exit-code/stderr
contract work outside pytest's own import machinery).

`tests/adapters/test_claude_code_cli.py` (38 new tests appended) -- No-command
compatibility (`--settings`/`--allowedTools` both absent, `exact_bash_guard_enabled`
`False`); artifact construction contains only fingerprints (never
`writer.py`/`exact.txt` argv text); cleanup-is-safe-on-`None`; `--settings`
argv wired alongside `--allowedTools` with the settings path removed by the
time `run()` returns; artifact directory removed after `run()` (captured via
monkeypatched wrapper around `_write_exact_bash_guard_artifacts`); the
fingerprints env var reaches a fake child process with only the fingerprint
value visible; fail-closed when artifact construction raises `OSError`
(worker never starts); full end-to-end scenarios where a **fake `claude`
executable itself invokes the real hook script** exactly as Claude Code
would (parses `--settings`, runs the configured hook command, feeds it a
synthetic Bash tool-call payload on stdin) for: exact match (defers, empty
hook stdout/stderr, artifacts already cleaned up), 11 parametrized
wrapper/mismatch variants (`command`, `builtin command`, `timeout`, `time`,
`nice`, `nohup`, `stdbuf`, `noglob`, `env`, different argument, extra
argument -- all denied with the fixed message and no leaked command text),
and `run_in_background=True` (denied); provider-JSON structural redaction
unit tests (non-JSON unchanged, `permission_denials[].tool_input.command`
stripped, nested nested `hookSpecificOutput` shapes stripped) plus an
end-to-end test where a fake `claude` prints a synthetic
`permission_denials` JSON payload containing a synthetic secret and the
resulting `result.stdout_excerpt` / evidence manifest are asserted free of
both the secret and the raw command; no-authorized-commands redaction-pass
skip (existing raw-JSON-stdout behavior unchanged); evidence manifest
includes `exact_bash_guard_enabled`; and a regression assertion that
`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` remains `False`.

### Exit codes

```
python3 .tomtit/dogfood/test_run_loop.py              -> EXIT=0 (52 tests)
python3 .tomtit/dogfood/test_run_repair.py             -> EXIT=0 (51 tests)
python3 .tomtit/dogfood/test_run_review.py             -> EXIT=0 (45 tests)
python3 -m pytest tests/adapters -q                    -> EXIT=0 (all adapter + guard tests)
python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q -> EXIT=0
python3 -m pytest -q                                   -> EXIT=0 (full repo suite)
python3 -m compileall -q src tests scripts .tomtit/dogfood -> EXIT=0
ruff check src tests scripts                           -> EXIT=0
python3 -m mypy src                                    -> EXIT=0 ("Success: no issues found in 14 source files")
git diff --check                                       -> EXIT=0
```

No test invoked a real Claude or Codex process. All Claude-process
interaction is via the pre-existing `write_fake_executable` pattern already
used throughout `test_claude_code_cli.py`, plus direct/standalone-subprocess
invocation of `exact_bash_guard.py` itself.

## 6. No-command compatibility

Confirmed unchanged when `authorized_commands` is empty:

- `_build_argv` emits neither `--settings` nor `--allowedTools`.
- No guard artifacts are written; `exact_bash_guard_enabled` is `False`.
- No env var is injected into the child process.
- The provider-JSON redaction pass is skipped entirely -- `stdout_excerpt`
  is exactly what the pre-existing env/HOME redaction alone produces, byte
  for byte.

## 7. Operational state

- `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` remains `False`, unchanged and
  unflipped by this task. The top-level gate in `run()` (return an early
  `FAILED` result with `error_category="UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION"`
  whenever `authorized_commands` is non-empty and the contract is not
  `EXACT_SUPPORTED`) is untouched, so a real invocation with any
  authorized command still never starts today -- everything built in this
  task is dormant in production until a separate Owner decision both
  confirms the CLI probe's findings are sufficiently addressed and flips
  that constant. All tests that exercise the "enabled" path do so via
  `monkeypatch.setattr(..., "_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT",
  True)` against a fake executable, following the exact pattern already
  established by the pre-existing test suite for this same reason.
- No `bypassPermissions` path exists or was added.
- No real Claude or Codex invocation occurred at any point in this task.
- No personal-web rerun was performed or is authorized by this change.

### Naming debt (reported, not fixed)

The task explicitly asked not to rename or redesign
`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` in this patch. Its name is now
somewhat under-descriptive: it originally meant "the CLI's bare `Bash(...)`
rule is confirmed to match exactly," but the isolated probe that motivated
this task shows that claim was never fully true (the rule is
provider-normalized, not raw-exact) -- what actually now stands between an
authorized command and real execution is the *combination* of this constant,
Claude's native `--allowedTools` rule, and (once wired into the enabled
path) this new PreToolUse guard. A future rename such as
`_CLAUDE_CLI_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE` (or similar) would
better describe "the layered contract as a whole is confirmed safe to rely
on," but that is a naming/documentation change with no behavioral effect and
is deliberately left for a separate, explicitly-scoped task rather than
folded into this one.

## 8. Remaining limitations

- Exact Claude CLI PreToolUse hook *behavior* (ordering relative to
  `--allowedTools` matching, exit-code-2 blocking semantics, hook-input JSON
  shape) is taken from Claude Code's published documentation and the
  Owner-supplied probe transcript, not independently re-confirmed by a real
  invocation in this task -- that confirmation is exactly what the next,
  separately-authorized isolated real-CLI probe is for.
- `run_in_background=True` is unconditionally denied (fail-closed MVP
  posture per the task); no attempt was made to reason about whether
  background Bash execution could ever be made safe to authorize.
- Per-call hook decision counts (PASS/DENY tallies) are not surfaced in
  evidence in this pass -- the CLI's JSON output does not currently expose
  this in a form safely extractable without risking a raw-command leak, so
  only the boolean `exact_bash_guard_enabled` is recorded.
- The provider-JSON redaction pass is structural (keyed on a literal
  `"command"` dict key) rather than an exhaustive scanner of every possible
  provider JSON shape; it targets the specific `permission_denials`/
  hook-denial shapes named in this task, not free-form prose that might
  happen to mention a command.
- The naming debt on `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` (§7) is
  reported, not fixed, per explicit task instruction.

---

STATUS=READY_FOR_EXACT_BASH_GUARD_REVIEW
TOMTIT_PRETOOLUSE_EXACT_ARGV_GUARD=IMPLEMENTED_PENDING_REVIEW
CLAUDE_NATIVE_ALLOWEDTOOLS_RETAINED=YES
MATCHING_HOOK_AUTO_GRANTS_PERMISSION=NO
WRAPPER_VARIANTS_BLOCKED=CLAIMED_PENDING_REVIEW
BACKGROUND_EXECUTION_BLOCKED=CLAIMED_PENDING_REVIEW
RAW_BASH_COMMAND_PERSISTED=NO
PROVIDER_DENIAL_COMMANDS_SANITIZED=CLAIMED_PENDING_REVIEW
EXACT_AUTHORIZATION_OPERATIONALLY_PROVEN=NO
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_INVOCATIONS=0
AUTOMATIC_GIT_ACTIONS=0
NEXT_ACTION=OWNER_RUNS_FRESH_REVIEW_THEN_ISOLATED_REAL_HOOK_PROBE
