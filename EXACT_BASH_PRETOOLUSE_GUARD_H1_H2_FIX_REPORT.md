# Exact-Argv PreToolUse Bash Guard: H-1 / H-2 Corrective Fix Report

## 1. Status

**Fixed, pending independent re-review.** Both blocking findings from
`EXACT_BASH_PRETOOLUSE_GUARD_REVIEW.md` (H-1: ambient-package import
hijack; H-2: non-blocking exit code 1 on unexpected failure) are
corrected. Every PASS-reviewed property (hook artifact integrity, exact
argv fingerprint semantics, PASS-never-grants-permission, wrapper-deny via
argv mismatch, the ten anticipated fail-closed paths, fingerprint-only
authorization artifacts) was left untouched. The two MEDIUM
provider-output-redaction findings (M-1, M-2) are intentionally deferred,
as instructed, and remain open.

## 2. Starting HEAD

```
git rev-parse HEAD -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d
```

Unchanged by this task (no commits were made). This matches the HEAD the
review was performed against.

## 3. H-1 correction

**Defect:** the previous design was

```python
try:
    from tomtit.adapters.claude_code_cli import (...)
except ImportError:
    _bootstrap_import_path()
    from tomtit.adapters.claude_code_cli import (...)
```

The trusted-`src/`-path bootstrap ran only on the `except` branch -- i.e.
only when *no* `tomtit` package was importable at all. Any ambient package
(installed distribution, user site-packages, `PYTHONPATH`, a `.pth` file,
`sitecustomize`) made the `try` succeed first, silently skipping the
bootstrap and handing `validate_authorized_command_argv` /
`compute_authorized_command_fingerprint` / `AuthorizedCommandError` to
whatever code answered that import.

**Correction (`src/tomtit/adapters/exact_bash_guard.py`):** the `try`/
`except ImportError` pattern is deleted entirely. The new sequence,
executed unconditionally at module load, before any `tomtit` import is
ever attempted:

1. `_trusted_src_root()` -- `str(Path(__file__).resolve().parents[2])`,
   derived solely from this file's own on-disk location. Never `cwd`,
   never an env var, never anything ambient.
2. `_bootstrap_trusted_src_path()` -- removes every existing occurrence of
   that exact resolved path from `sys.path` first (`sys.path[:] = [e for e
   in sys.path if e != trusted_src_root]`), then `sys.path.insert(0, ...)`.
   Idempotent; the trusted entry always ends up in the winning position.
3. `_import_trusted_claude_code_cli(trusted_src_root)` --
   `importlib.import_module("tomtit.adapters.claude_code_cli")`, then
   verifies `Path(module.__file__).resolve()` is a descendant of
   `Path(trusted_src_root).resolve()` via `.relative_to(...)`. Raises
   `_TrustedImportUnavailableError` if `__file__` is missing or the
   provenance check fails.
4. Only then are the three required names read off the verified module:
   `validate_authorized_command_argv`, `compute_authorized_command_fingerprint`,
   `AuthorizedCommandError`.

**On failure (any exception at any of the above steps, `BaseException`
included):** `_load_trusted_claude_code_cli()` catches it and returns
always-raising stand-ins (`_unavailable_validate_authorized_command_argv`,
`_unavailable_compute_authorized_command_fingerprint`,
`_TrustedImportUnavailableError`) instead. `evaluate()` and
`_command_fingerprint_is_approved()` are **completely unmodified** from
the already-PASS-reviewed implementation -- they still just reference
`validate_authorized_command_argv` / `compute_authorized_command_fingerprint`
/ `AuthorizedCommandError` as module globals. In the failure case, calling
the stand-in validator raises `_TrustedImportUnavailableError`, which *is*
what `AuthorizedCommandError` is bound to in that case, so the existing,
unmodified `except AuthorizedCommandError:` clause in
`_command_fingerprint_is_approved` denies cleanly -- no special-casing was
added anywhere in the reviewed-PASS code paths.

**Why the provenance check (not a `sys.modules` purge) closes the
`sitecustomize`/cache-poisoning vector too:** `importlib.import_module`,
like a plain `import` statement, consults `sys.modules` *before*
`sys.path`. A `sys.path[0]` insertion alone cannot out-rank a
`tomtit.adapters.claude_code_cli` entry something already placed in
`sys.modules` before this script's own code ran (most notably
`sitecustomize.py`, executed by the `site` module at interpreter startup).
Rather than proactively purging `sys.modules` (which would also disturb
the normal, benign case where `pytest` has already legitimately imported
the real trusted module earlier in the same process), the fix instead
verifies the *outcome*: whatever module ends up bound to
`tomtit.adapters.claude_code_cli`, its own resolved `__file__` must be
underneath the trusted root, or it is rejected. This catches a
`sys.modules`-cache-poisoning attack exactly like an ordinary
`sys.path`-ordering attack, and was empirically confirmed to do so (see
§4).

Never logs the ambient module path, the mismatch reason, or any other
dynamic value anywhere -- `_TrustedImportUnavailableError`'s messages are
fixed static strings, and are in any case never printed; only the fixed
`DENIAL_MESSAGE` ever reaches stderr.

## 4. Trusted import provenance proof

Manually reproduced the review's exact H-1 repro against the fixed code,
before writing the automated test suite:

**Ambient `PYTHONPATH` package (the review's exact reproduction):**

```
$ echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /tmp/SYNTHETIC_TARGET"}}' | \
    PYTHONPATH=<ambient-malicious-package> \
    TOMTIT_EXACT_BASH_GUARD_FINGERPRINTS_FILE=<fp-file-containing-ambient-constant> \
    python3 src/tomtit/adapters/exact_bash_guard.py
TOMTIT exact-argv authorization gate: this Bash command does not exactly
match an Owner-approved authorized command and was blocked before execution.
$ echo $?
2
```

Previously `HOOK_EXIT=0` (PASS) against the same inputs; now `EXIT=2`
(DENY).

**Broken trusted import (H-2-adjacent, confirms the two fixes compose):**

```
$ echo '{"tool_name":"Bash","tool_input":{"command":"echo hi"}}' | \
    python3 <fake-trusted-tree>/src/tomtit/adapters/exact_bash_guard.py
TOMTIT exact-argv authorization gate: ... blocked before execution.
$ echo $?
2
```

Previously produced a raw Python traceback and `EXIT=1`.

**`sitecustomize.py` cache-poisoning (the residual vector a `sys.path`
insertion alone cannot out-rank):**

```
$ python3 -c "import sys; print('tomtit.adapters.claude_code_cli' in sys.modules)"
# (with PYTHONPATH pointing at a directory containing a sitecustomize.py
#  that pre-populates sys.modules with a fake claude_code_cli module)
True    # confirms the fixture genuinely poisons sys.modules first
$ echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /tmp/SYNTHETIC_TARGET_2"}}' | \
    PYTHONPATH=<sitecustomize-vector-dir> \
    TOMTIT_EXACT_BASH_GUARD_FINGERPRINTS_FILE=<fp-file> \
    python3 src/tomtit/adapters/exact_bash_guard.py
TOMTIT exact-argv authorization gate: ... blocked before execution.
$ echo $?
2
```

**Real `PYTHONUSERBASE`-backed user-site-packages (not `PYTHONPATH`
again):**

```
$ python3 -c "import site; print(site.getusersitepackages())"  # under PYTHONUSERBASE=<userbase>
<userbase>/lib/python/site-packages
# (malicious tomtit package placed there)
$ echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /tmp/SYNTHETIC_TARGET_3"}}' | \
    PYTHONUSERBASE=<userbase> \
    TOMTIT_EXACT_BASH_GUARD_FINGERPRINTS_FILE=<fp-file> \
    python3 src/tomtit/adapters/exact_bash_guard.py
TOMTIT exact-argv authorization gate: ... blocked before execution.
$ echo $?
2
```

**`cwd` (control -- confirms no influence, as expected for a
directly-executed script):**

```
$ cd <target-repo-containing-its-own-fake-tomtit-package>
$ echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /tmp/SYNTHETIC_TARGET_4"}}' | \
    TOMTIT_EXACT_BASH_GUARD_FINGERPRINTS_FILE=<fp-file> \
    python3 <absolute-path-to>/src/tomtit/adapters/exact_bash_guard.py
TOMTIT exact-argv authorization gate: ... blocked before execution.
$ echo $?
2
```

All five manual reproductions were then encoded as automated,
subprocess-based tests (§5).

## 5. Ambient-package-hijack regression tests

Added to `tests/adapters/test_exact_bash_guard.py`, all subprocess-based
(the defect and its fix both live in module-load-time behavior, which only
a genuinely fresh process can faithfully exercise -- in-process
monkeypatching happens after that sequence has already run once):

| # | Test | Proves |
|---|---|---|
| 1 | `test_h1_1_ambient_package_present_earlier_on_pythonpath` | The ambient-package fixture itself is a real attack surface (plain `python3 -c "import tomtit..."` resolves it) before proving it's closed |
| 2 | `test_h1_2_hook_still_loads_trusted_module_for_legitimate_exact_match` | A real approved command still PASSes (fingerprint match) with the malicious ambient package present -- only possible if the trusted functions, not the ambient always-allow/constant-fingerprint ones, actually ran |
| 3 | `test_h1_3_ambient_always_allow_validator_cannot_influence_decision` | An unauthorized command is denied even though the ambient validator never rejects anything |
| 4 | `test_h1_4_ambient_constant_fingerprint_cannot_influence_decision` | The review's exact H-1 repro, stated from the fingerprint side |
| 5 | `test_h1_5_unauthorized_command_denied_despite_malicious_ambient_package` | The review's exact H-1 repro end to end: `EXIT=2`, fixed message only, no command text in stderr |
| 6 | `test_h1_6_trusted_module_provenance_mismatch_fails_closed` | `_import_trusted_claude_code_cli` raises when given a `trusted_src_root` that doesn't actually contain the real module's `__file__`, even though the import itself succeeds |
| 7 | `test_h1_7_direct_standalone_subprocess_execution_matches` | Both DENY (ambient hijack) and PASS (legitimate exact match, silent, exit 0) hold for the real standalone subprocess Claude actually spawns |
| 8 | `test_h1_8_no_cwd_or_target_repo_package_can_alter_provenance` | `cwd` set to a directory containing its own fake `tomtit/` package has zero effect |
| 9 | `test_h1_9_user_site_like_path_simulation_cannot_alter_provenance` | `PYTHONUSERBASE`-backed *real* `site.getusersitepackages()` mechanism (not `PYTHONPATH`) has zero effect |
| bonus | `test_h1_10_sitecustomize_cache_poisoning_fails_closed_via_provenance_check` | The `sys.modules`-cache-poisoning vector a `sys.path[0]` insertion alone cannot out-rank; fixture-validity is asserted first (`sys.modules` really is poisoned), then denial |

## 6. H-2 correction

**Defect:** `main()` had no top-level exception guard, and the
module-level import was outside any handler that could convert a failure
into a deny. An import failure, or any unexpected exception during
evaluation, produced Python's default uncaught-exception behavior: a
traceback on stderr and exit code **1**. Claude Code's PreToolUse contract
treats only exit code 2 as blocking; any other non-zero exit is
non-blocking and the tool call proceeds regardless.

**Correction (`src/tomtit/adapters/exact_bash_guard.py`):**

1. **Trusted-path bootstrap, TOMTIT import, provenance verification** are
   all inside `_load_trusted_claude_code_cli()`'s own `try: ... except
   BaseException:` at module level (§3) -- module load itself can never
   raise due to this sequence; failure degrades to the always-raising
   stand-ins instead.
2. **Everything else** -- reading the fingerprints-file env var, `stdin`
   read, JSON parsing, `evaluate()`, and by extension the validator and
   fingerprint function calls inside it -- happens inside `_run() -> bool`,
   which is deliberately free to let any exception propagate.
3. **`main()`** is the single boundary that decides the process outcome:

   ```python
   def main() -> int:
       try:
           if _run():
               return _PASS_EXIT_CODE
       except BaseException:  # noqa: BLE001
           pass
       try:
           print(DENIAL_MESSAGE, file=sys.stderr)
       except BaseException:  # noqa: BLE001
           pass
       return _DENY_EXIT_CODE
   ```

   `_PASS_EXIT_CODE` (0) is returned in exactly one case: `_run()` returned
   `True` with no exception. Every other case -- a clean deny, any
   exception anywhere in `_run()`'s call graph, `BaseException` included,
   and even a failure while writing the denial message itself (a broken
   stderr) -- falls through to `_DENY_EXIT_CODE` (2). `main()`'s return
   type is structurally bounded to `{0, 2}`; there is no code path that
   can produce any other value.
4. `BaseException` is caught deliberately, not `Exception`.
   `KeyboardInterrupt`/`SystemExit` are given no special treatment: per
   the task's instruction, since Python's default handling of an uncaught
   `KeyboardInterrupt` (exit code 130) would be exactly as non-blocking
   under Claude Code's PreToolUse contract as the exit-code-1 defect this
   fix closes, both are converted to the same deny path rather than
   preserved.
5. Broad exception handling (`except BaseException`) appears *only* in
   this module -- `_load_trusted_claude_code_cli()` and `main()` -- both
   explicitly commented as the standalone security-hook trust boundary.
   No other TOMTIT product code uses this pattern.

## 7. Exit-code-2 fail-closed proof

Manually reproduced (§4) and then covered by 15 automated tests
(`test_h2_1_import_failure_exits_2` through
`test_h2_15_no_reachable_exit_code_1_across_all_scenarios`), all
subprocess-based except where an in-process mechanism is the more direct
way to inject the specific failure (stdin read, `evaluate()` itself
raising):

| # | Test | Failure injected |
|---|---|---|
| 1 | `test_h2_1_import_failure_exits_2` | Trusted `claude_code_cli.py` raises at import time (fake-trusted-tree copy of the real, unmodified hook) |
| 2 | `test_h2_2_module_provenance_failure_exits_2` | `sitecustomize`-based `sys.modules` cache poisoning |
| 3 | `test_h2_3_stdin_read_failure_exits_2` | `sys.stdin.read()` raises `OSError` (in-process, monkeypatched stdin) |
| 4 | `test_h2_4_malformed_json_exits_2` | Unparseable stdin |
| 5 | `test_h2_5_evaluate_raises_unexpected_runtimeerror_exits_2` | `evaluate()` itself monkeypatched to raise `RuntimeError` (in-process, since `evaluate()` is defined in this module and cannot be overridden via an ambient package) |
| 6 | `test_h2_6_validator_raises_unexpected_exception_exits_2` | Trusted (not ambient) `validate_authorized_command_argv` raises a bare `RuntimeError`, not `AuthorizedCommandError` |
| 7 | `test_h2_7_fingerprint_function_raises_unexpected_exception_exits_2` | Trusted `compute_authorized_command_fingerprint` raises unexpectedly |
| 8 | `test_h2_8_missing_fingerprint_file_exits_2` | Fingerprints-file env var points at a nonexistent file |
| 9 | `test_h2_9_malformed_fingerprint_file_exits_2` | Fingerprints file contains invalid JSON |
| 10 | `test_h2_10_no_traceback_on_any_unexpected_failure` | No `"Traceback (most recent call last)"` in stdout/stderr across all 7 built failure scenarios |
| 11 | `test_h2_11_no_synthetic_command_leak_on_unexpected_failure` | No command text leaks across all 7 scenarios |
| 12 | `test_h2_12_no_synthetic_secret_leak_on_unexpected_failure` | No synthetic secret leaks, including one embedded directly inside a malformed-JSON stdin blob |
| 13 | `test_h2_13_exact_approved_command_still_exits_0_with_empty_output` | Non-regression: PASS unaffected |
| 14 | `test_h2_14_denied_command_still_exits_2_with_only_denial_message` | Non-regression: clean DENY unaffected |
| 15 | `test_h2_15_no_reachable_exit_code_1_across_all_scenarios` | `returncode in (0, 2)` and `!= 1` across all 7 failure scenarios plus clean PASS/DENY |

`_build_h2_failure_scenarios()` constructs seven distinct failure
conditions as real subprocesses (broken trusted import, provenance
mismatch, malformed JSON, missing fingerprints file, malformed
fingerprints file, validator raising unexpectedly, fingerprint function
raising unexpectedly); tests 10/11/15 share one session-scoped build via a
`pytest.fixture(scope="module")` (`_h2_default_failure_scenarios`) since
they assert different properties of the *same* runs, not additional
coverage -- avoiding redundant subprocess spawns.

## 8. Traceback / non-leak proof

Every scenario in §7's table 10-12 was verified to produce **only** the
fixed `DENIAL_MESSAGE` string on stderr (`proc.stderr.strip() ==
DENIAL_MESSAGE` where checked explicitly; `"Traceback"` absence checked
everywhere) and empty stdout on every DENY path. The malformed-JSON
scenario deliberately embeds the synthetic command/secret text *inside the
invalid JSON blob itself* (`f"{{not valid json but mentions {command}"`),
so the leak check covers "the failing input itself contained the secret"
as well as "the failure occurred while processing an otherwise-clean
input."

## 9. Existing guard semantics preserved

Not redesigned, not touched:

- `evaluate()` -- byte-for-byte unchanged. Still a 2-argument pure
  function (`payload`, `approved_fingerprints`) returning a bare `bool`;
  every existing caller (dozens of pre-existing unit tests) works
  unmodified.
- `_command_fingerprint_is_approved()` -- byte-for-byte unchanged.
- `load_approved_fingerprints()` -- byte-for-byte unchanged; its own
  internal `except (OSError, ValueError): return None` fail-soft path is
  untouched.
- `DENIAL_MESSAGE`, `FINGERPRINTS_FILE_ENV`, `_PASS_EXIT_CODE`,
  `_DENY_EXIT_CODE` -- unchanged values.
- No wrapper denylist was introduced anywhere; the wrapper-deny property
  still comes entirely from argv-fingerprint mismatch plus the shared
  validator, exactly as before.
- `run_in_background` rejection -- unchanged, inside the untouched
  `evaluate()`.
- `--allowedTools` defense-in-depth layering, the ephemeral settings
  design, artifact permissions, and the evidence schema all live in
  `src/tomtit/adapters/claude_code_cli.py`, which **this patch does not
  modify at all** (confirmed: `git diff` for that file is byte-identical
  to before this task started; only `exact_bash_guard.py` and
  `tests/adapters/test_exact_bash_guard.py` changed).
- All 45 pre-existing tests in `tests/adapters/test_exact_bash_guard.py`
  pass unmodified against the new code.
- `tests/adapters/test_claude_code_cli.py` required **no changes** --
  grepped for any reference to `exact_bash_guard` internals that changed
  (none; it only imports the still-public `DENIAL_MESSAGE`) and confirmed
  it passes unmodified.

## 10. Deferred M-1/M-2 findings

Not fixed in this task, as instructed. Recorded here as **OPEN_MEDIUM**
for the later enablement gate:

- **M-1 (structural redaction gap):** `_redact_provider_json_bash_commands`
  in `claude_code_cli.py` only redacts a dict key literally named
  `"command"`. Raw command text can still reach persisted evidence via
  `result` prose, `error`/`message`/`reason`/`hookSpecificOutput.
  permissionDecisionReason` keys, nested `content: [{"type":"text",
  "text": "<cmd>"}]` arrays, or alternate `tool_input` field names
  (`cmd`, `Command`).
- **M-2 (truncation-before-redaction):** `_StreamReader` truncates stdout
  at `max_stdout_bytes` (default 65536) *before* redaction runs; a
  truncated `--output-format json` payload is not valid JSON, so
  `_redact_provider_json_bash_commands` returns it unchanged and any
  `"command": "<raw>"` in the surviving prefix persists.

Both live entirely in `claude_code_cli.py`, which this patch does not
touch, and were explicitly out of scope for H-1/H-2. Per the task's
instruction, they do not block an isolated real hook probe that uses only
synthetic command text, but must be closed before the capability is ever
enabled for real user workloads.

## 11. Tests and exact exit codes

```
python3 .tomtit/dogfood/test_run_loop.py                            -> EXIT=0  (52 passed)
python3 .tomtit/dogfood/test_run_repair.py                          -> EXIT=0  (51 passed)
python3 .tomtit/dogfood/test_run_review.py                          -> EXIT=0  (45 passed)
python3 -m pytest tests/adapters/test_exact_bash_guard.py -q        -> EXIT=0  (70 passed: 45 pre-existing + 10 H-1 + 15 H-2)
python3 -m pytest tests/adapters -q                                 -> EXIT=0  (326 passed)
python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q  -> EXIT=0  (4 passed)
python3 -m pytest -q                                                -> EXIT=0  (652 passed, full repo suite)
python3 -m compileall -q src tests scripts .tomtit/dogfood          -> EXIT=0
ruff check src tests scripts                                        -> EXIT=0
python3 -m mypy src                                                 -> EXIT=0  ("Success: no issues found in 14 source files")
git diff --check                                                    -> EXIT=0
```

**Flaky-test note (honestly recorded, not erased):** on one intermediate
`tests/adapters -q` run (not the final run recorded above),
`test_claude_code_cli.py::test_delayed_descendant_write_is_prevented_before_final_snapshot`
failed with `assert result.status is SUCCEEDED` getting `FAILED`. This is
in the same family the original review's F-1 note already documented
(pre-existing timing sensitivity in bounded process-group cleanup under
system load) -- a *different* test in that family than the one F-1 named,
but the same class of flake. Rerun in isolation 5/5 times clean
immediately afterward, and the subsequent full `tests/adapters -q` run
(recorded above) passed clean. Its failing result showed
`authorized_command_count=0, exact_bash_guard_enabled=False`, i.e. it does
not exercise any code this patch touches. Not attributable to H-1/H-2.

**Self-caught issue during test authoring (also recorded honestly):** an
early version of the new `_run_hook()` test helper defaulted its
fingerprints-file directory to `script_path.parent`, which for calls using
the real hook's path resolved to the tracked `src/tomtit/adapters/`
source directory -- writing dozens of stray `fingerprints-*.json` files
into the repository working tree. Caught via `git status` before running
any of the required verification commands in their final form; fixed by
changing the default to a fresh `tempfile.mkdtemp()` directory (never the
script's own directory) and removing the leaked files. `git status`
confirmed clean of any such artifact in every verification run recorded
in this report.

## 12. Real invocation counts

```
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_INVOCATIONS=0
```

No real Claude or Codex process was invoked at any point. All "hook"
executions in this task are the TOMTIT-owned `exact_bash_guard.py` script
itself (or temporary copies of its unmodified source) run directly or via
fake-`claude`-style test harnesses already established by the prior task's
test suite -- never the real `claude` CLI. TOMTIT was not run against
itself. `bypassPermissions` was not used. The enable constant
(`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT`) was not flipped -- confirmed
still `False` in `claude_code_cli.py`, which this patch does not modify.
No personal-web rerun occurred.

## 13. Final Git state

```
git rev-parse HEAD -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d  (unchanged)
git status --short:
  M src/tomtit/adapters/__init__.py            (predates this task, untouched by it)
  M src/tomtit/adapters/claude_code_cli.py     (predates this task, untouched by it)
  M tests/adapters/test_claude_code_cli.py     (predates this task, untouched by it)
  ?? src/tomtit/adapters/exact_bash_guard.py   (rewritten by this task)
  ?? tests/adapters/test_exact_bash_guard.py   (extended by this task)
  ?? (10 report .md files, including this one and the review it responds to)
git diff --cached -> (empty; nothing staged)
```

No commit, stage, reset, stash, checkout, push, merge, or deploy operation
was performed. `git diff --check` passes (no whitespace errors).

## 14. Next gate

Owner-coordinated independent re-review of H-1 and H-2 specifically
(matching this report's §3-§8), followed by an Owner decision on whether
an isolated real hook probe (synthetic commands only, per the deferred-M-1/
M-2 scoping in §10) may proceed. M-1/M-2 remain open and must be addressed
in a separate, later task before the capability is enabled for real user
workloads; they do not block the probe itself.

---

STATUS=READY_FOR_H1_H2_SECURITY_REREVIEW
H1_IMPORT_TRUST_BOUNDARY=CLAIMED_FIXED_PENDING_REVIEW
H2_UNEXPECTED_FAILURE_FAIL_CLOSED=CLAIMED_FIXED_PENDING_REVIEW
HOOK_PASS_EXIT_CODE=0
HOOK_DENY_EXIT_CODE=2
UNEXPECTED_FAILURE_EXIT_CODE=2
TRACEBACK_ON_HOOK_FAILURE=NO
AMBIENT_TOMTIT_IMPORT_INFLUENCE=NO
EXACT_ARGV_GUARD=PRESERVED
PROVIDER_OUTPUT_REDACTION_MEDIUMS=OPEN
EXACT_AUTHORIZATION_OPERATIONALLY_PROVEN=NO
ISOLATED_REAL_HOOK_PROBE_AUTHORIZED=NO
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_INVOCATIONS=0
AUTOMATIC_GIT_ACTIONS=0
NEXT_ACTION=OWNER_RUNS_FRESH_SECURITY_REREVIEW
