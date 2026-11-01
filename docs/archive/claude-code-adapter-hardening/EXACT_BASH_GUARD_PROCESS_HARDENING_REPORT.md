# Exact Bash Guard: Process-Boundary Hardening Report

## 1. Status

Both required changes are implemented, verified, and covered by 25 new
deterministic tests. The guard itself was **not** redesigned:
`src/tomtit/adapters/exact_bash_guard.py` is byte-for-byte unmodified by
this task (no `Edit`/`Write` was applied to it), so every property the
H-1/H-2 re-review verified — exact argv fingerprint semantics, canonical
validator, trusted-import logic, exit-0/exit-2 boundary, fingerprint-only
artifacts, PASS-never-grants-permission, native `--allowedTools` layering,
`run_in_background` rejection — is untouched.

Starting HEAD: `f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d` (unchanged; no
commits).

## 2. Required change 1 — isolated Python startup

`_write_exact_bash_guard_artifacts` previously built:

```python
hook_command = f"{shlex.quote(sys.executable)} {shlex.quote(str(_EXACT_BASH_GUARD_MODULE_PATH))}"
```

Now, via a new dedicated renderer:

```python
_EXACT_BASH_GUARD_PYTHON_ISOLATION_FLAGS: tuple[str, ...] = ("-E", "-s")

def _render_exact_bash_guard_hook_command(guard_module_path: Path) -> str:
    parts = (sys.executable, *_EXACT_BASH_GUARD_PYTHON_ISOLATION_FLAGS, str(guard_module_path))
    return " ".join(shlex.quote(part) for part in parts)
```

Rendered result on this machine:

```
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -E -s \
  /Users/tranvanbac/Documents/AI/ai-agent/TOMTIT-Coordinator/src/tomtit/adapters/exact_bash_guard.py
```

Requirements satisfied:

| Requirement | How |
|---|---|
| `-E` (ignore `PYTHON*` env vars) | present, before the script path |
| `-s` (disable user site-packages) | present, before the script path |
| Absolute resolved hook path preserved | `_EXACT_BASH_GUARD_MODULE_PATH` unchanged (`Path(__file__).resolve().parent / "exact_bash_guard.py"`), now additionally preflighted (§3) |
| argv-safe construction | each part `shlex.quote`d individually, joined with single spaces |
| no `shell=True` | none anywhere in the adapter (AST-asserted by `test_hardening_7_no_shell_true_path_exists`) |
| no shell wrapper | argv[0] is the Python interpreter itself; no `sh`/`bash`/`env`/`command`/`xargs`, no `-c`, no inline program text |
| no environment-dependent Python discovery | `sys.executable` — TOMTIT's own already-running interpreter, an absolute path, never a bare `python3` resolved via `PATH` |

**`-S` deliberately not used**, per the stated scope. `-E -s` is what was
approved; `-S` would additionally disable `site` entirely and was excluded
absent deterministic proof it is safe. No such proof was attempted here.

**Why this matters (re-review finding N-1).** The re-review reproduced a
**spoofed-`__file__`** `sitecustomize.py` attack (H1-C2) that produced
**exit 0** on `rm -rf /tmp/SYNTHETIC_TARGET` against the un-hardened
invocation: a fake module pre-placed in `sys.modules` whose `__file__`
attribute is a plain string set to the real trusted path, defeating the
guard's in-process provenance check. No in-process check can survive an
attacker who executes code before the script runs. `-E` prevents that
`sitecustomize.py` from ever loading, which is exactly the reviewer's
recommendation that this defense "moves to the process boundary, where it
actually holds."

Reproduced directly, before writing any test:

```
# spoofed-__file__ sitecustomize delivered via PYTHONPATH
WITHOUT -E -s : EXIT=0     <- the re-review's H1-C2 finding
WITH    -E -s : EXIT=2     <- closed

# -E genuinely neutralizes PYTHONPATH (observed in the child's own sys.path)
python3 -E -s -c "'<ambient>' in sys.path"  -> False
python3       -c "'<ambient>' in sys.path"  -> True

# -s genuinely disables user site
python3 -E -s -c "site.ENABLE_USER_SITE"    -> False
python3       -c "site.ENABLE_USER_SITE"    -> True
```

**Scope limit recorded honestly:** `-E -s` does **not** neutralize a
`sitecustomize.py` installed into the *system* site-packages directory,
which requires privileged write access to the interpreter's own
installation — a strictly higher bar than the unprivileged `PYTHONPATH`
and user-site vectors these flags do close. This is documented in the
code comment alongside the flags, not just here.

## 3. Required change 2 — guard-file preflight integrity

New `_verified_exact_bash_guard_module_path()`, called as the **first**
statement of `_write_exact_bash_guard_artifacts` — before any temp
directory is created, any file is written, or any process is started:

```python
class _ExactBashGuardIntegrityError(OSError): ...

def _verified_exact_bash_guard_module_path() -> Path:
    expected_path = _EXACT_BASH_GUARD_MODULE_PATH
    try:
        stat_result = os.lstat(expected_path)
    except OSError:
        raise _ExactBashGuardIntegrityError("...missing or unreadable...") from None
    if not stat.S_ISREG(stat_result.st_mode):
        raise _ExactBashGuardIntegrityError("...not a regular file")
    if expected_path.resolve() != expected_path:
        raise _ExactBashGuardIntegrityError("...does not resolve to its expected location")
    return expected_path
```

Design notes:

- **`os.lstat`, not `Path.exists()`/`is_file()`** — so a symlink is
  *detected* rather than silently followed. `stat.S_ISREG` on an `lstat`
  result is `False` for a symlink (`S_IFLNK`), a directory, a FIFO, or a
  device node, so one check covers symlink substitution and every
  non-regular-file substitution together.
- **The `.resolve()` comparison** is belt-and-braces for a *parent*
  directory component having become a symlink after this module was
  imported.
- **`_ExactBashGuardIntegrityError` subclasses `OSError`** so `run()`'s
  already-reviewed `except OSError` → `EXACT_BASH_GUARD_UNAVAILABLE`
  fail-closed path catches it **unchanged**. This adds a new *reason* to
  fail closed, never a new outcome or a new routing path — no
  already-reviewed control flow was edited.
- **Error messages carry no dynamic values** — no errno text, no
  `strerror`, no path — matching the adapter's existing fixed-shape
  convention so nothing environment-derived reaches evidence. Asserted by
  `test_hardening_12d_preflight_error_messages_carry_no_dynamic_values`.
- **Not a signing system, no persistence** — the required MVP scope: a
  deterministic check bound to the one known TOMTIT-owned source location.

**Relation to re-review finding N-2, stated precisely:** N-2 observed that
a *corrupted* (truncated / syntactically invalid) guard file makes Python
exit **1**, which is non-blocking under the PreToolUse contract. This
preflight does **not** close that case — a corrupted-but-still-regular
file passes `S_ISREG`. What it does close is the *substitution* case: a
missing, symlinked, or non-regular guard path is now rejected before
Claude is started at all, rather than being discovered only once the hook
runs. Guard-file *content* integrity remains an out-of-band assumption
that no check inside this process can close, and is recorded as such in
the code docstring.

## 4. Tests

25 new tests, all in `tests/adapters/test_claude_code_cli.py`.
`tests/adapters/test_exact_bash_guard.py` needed **no** changes (all 70 of
its tests pass unmodified).

| # | Required property | Test |
|---|---|---|
| 1 | Exact trusted `sys.executable` | `test_hardening_1_hook_command_uses_exact_trusted_sys_executable` (also asserts absolute, never PATH-resolved) |
| 2 | `-E` present | `test_hardening_2_e_flag_present` |
| 3 | `-s` present | `test_hardening_3_s_flag_present` |
| 4 | Both flags before script path | `test_hardening_4_flags_precede_script_path` (asserts the exact 4-element shape) |
| 5 | Absolute resolved guard path | `test_hardening_5_absolute_resolved_guard_path_is_used` |
| 6 | No shell wrapper | `test_hardening_6_no_shell_wrapper_in_hook_command` (argv[0] is python, no `-c`, no shell metacharacters in the rendered line) |
| 7 | No `shell=True` path | `test_hardening_7_no_shell_true_path_exists` (AST scan of the whole adapter) |
| 8 | Malicious `PYTHONPATH` | `test_hardening_8_malicious_pythonpath_cannot_influence_decision` + `_8b_pythonpath_is_not_on_guard_sys_path` |
| 9 | Malicious `PYTHONUSERBASE`/user-site | `test_hardening_9_malicious_user_site_cannot_influence_decision` + `_9b_user_site_is_disabled_in_guard_interpreter` + `_9c_pythonpath_sitecustomize_spoof_is_blocked` (the N-1 H1-C2 vector) |
| 10 | Legitimate exact command still defers silently | `test_hardening_10_legitimate_exact_command_still_defers_silently` + `_10b` (with ambient package present) + `_10c` (`run_in_background` still denied) + `_10d` (wrapper variant still denied) |
| 11 | Missing guard file fails closed before Claude starts | `test_hardening_11_missing_guard_file_fails_closed_before_claude_starts` (asserts the fake Claude's invocation marker was never written) |
| 12 | Non-regular/untrusted guard path fails closed | `test_hardening_12_symlinked_guard_path_fails_closed_before_claude_starts` + `_12b` (directory) + `_12c` (OSError subclass) + `_12d` (no dynamic values in message) + `_12e` (positive control: real file passes) |
| 13 | No authorized command ⇒ unchanged | `test_hardening_13_no_authorized_command_behavior_unchanged` + `_13b` (preflight does not even run when there is no authorized command, so a broken guard path cannot break an existing no-command workflow) |
| — | Settings file records the hardened command, still fingerprint-only | `test_hardening_settings_file_records_the_hardened_hook_command` |

Tests 8–10 execute the **actual rendered hook command** (split back to argv
and run without a shell), not a hand-built approximation — so they exercise
the exact invocation TOMTIT registers with Claude. Tests 8b/9b/9c assert
the *mechanism* (`sys.path` contents, `site.ENABLE_USER_SITE`, fixture
validity confirmed before the deny assertion), not merely the outcome, so
they cannot pass vacuously.

Only fake Claude executables and synthetic values are used. No real Claude
or Codex process was invoked.

### Verification — exact exit codes

```
python3 .tomtit/dogfood/test_run_loop.py                            -> EXIT=0   (52 tests)
python3 .tomtit/dogfood/test_run_repair.py                          -> EXIT=0   (51 tests)
python3 .tomtit/dogfood/test_run_review.py                          -> EXIT=0   (45 tests)
python3 -m pytest tests/adapters/test_exact_bash_guard.py -q        -> EXIT=0   (70 tests, unmodified)
python3 -m pytest tests/adapters -q                                 -> EXIT=0   (351 tests; was 326, +25)
python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q  -> EXIT=0   (4 tests)
python3 -m pytest -q                                                -> EXIT=1   (first run; see flake note)
python3 -m pytest -q   (rerun)                                      -> EXIT=0   (677 tests; was 652, +25)
python3 -m compileall -q src tests scripts .tomtit/dogfood          -> EXIT=0
ruff check src tests scripts                                        -> EXIT=0
python3 -m mypy src                                                 -> EXIT=0   ("Success: no issues found in 14 source files")
git diff --check                                                    -> EXIT=0
```

**Flake note (recorded honestly, not erased).** The first full-suite run
failed on
`tests/adapters/test_claude_code_cli.py::test_normal_exit_with_surviving_descendant_kills_descendant`
(`assert result.status is SUCCEEDED` got `FAILED`) — `1 failed, 676
passed`. This is the pre-existing process-cleanup timing flake the original
guard review recorded as **F-1**. Attribution checked rather than assumed:
the failing result carries `authorized_command_count=0,
exact_bash_guard_enabled=False`, i.e. it exercises no code path this patch
touches (no authorized command ⇒ no preflight, no `-E -s` hook command, no
guard artifacts). Isolated rerun passed **5/5**; the subsequent full-suite
rerun passed clean at 677. `tests/adapters` itself passed clean on its
first attempt both before and after this patch.

## 5. Security properties preserved

| Property | Status | Evidence |
|---|---|---|
| Exact argv fingerprint semantics | unchanged | `exact_bash_guard.py` not modified; `_10d` re-confirms wrapper deny |
| Canonical validator | unchanged | `validate_authorized_command_argv` untouched |
| H-1 trusted import logic | unchanged | `_load_trusted_claude_code_cli` untouched; `-E -s` sits *in front of* it, never replaces it |
| H-2 exit-0/exit-2 boundary | unchanged | `main()` untouched; `_10`/`_10c` re-confirm 0 and 2 |
| Fingerprint-only artifacts | unchanged | settings/fingerprints writing logic untouched; asserted again in `test_hardening_settings_file_records_the_hardened_hook_command` |
| PASS never grants permission | unchanged | `_10` asserts exit 0 with **empty stdout and empty stderr** |
| Native `--allowedTools` layering | unchanged | `_build_argv` untouched; `--settings` still added alongside, never instead of |
| `run_in_background` rejection | unchanged | `_10c` re-confirms exit 2 |
| Enable constant | **`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT = False`** | `claude_code_cli.py:366`, not flipped |

## 6. Deferred findings — still OPEN

**M-1** (structural redaction covers only a dict key literally named
`"command"`) and **M-2** (truncation runs before redaction, so truncated
JSON bypasses it entirely) were **not** touched by this patch and remain
**OPEN_MEDIUM**. They still block enablement for real user workloads. They
do not block a later isolated hook probe that uses synthetic command text
only.

## 7. Constraint compliance

- Files modified: `src/tomtit/adapters/claude_code_cli.py`,
  `tests/adapters/test_claude_code_cli.py` — exactly the expected scope.
  `exact_bash_guard.py` was **not** modified (no regression proved it
  necessary). `tests/adapters/test_exact_bash_guard.py` was **not**
  modified.
- No Runtime, Coordinator, evaluation, governance, memory, personal-web, or
  routing file was touched.
- No real Claude or Codex invocation. No TOMTIT self-run. No constant flip.
  No `bypassPermissions`.
- No commit, stage, reset, stash, checkout, push, merge, or deploy.
  `git rev-parse HEAD` unchanged; `git diff --cached` empty.
- The real hook probe was **not** run. personal-web was **not** rerun.
- No prior report was modified.

## 8. Next gate

Owner-coordinated narrow review of this hardening patch (§2 and §3), then
the Owner decision on authorizing the isolated real hook probe using
synthetic commands only. Note that this patch materially improves the
probe's environmental preconditions: the re-review's §9 recommended
controlling the probe interpreter's `sitecustomize`/`usercustomize`/`.pth`
state manually (N-1), and `-E -s` now enforces the unprivileged subset of
that automatically at the process boundary. The system-site-packages
`sitecustomize` case (§2) and guard-file content corruption (§3, N-2)
remain out-of-band assumptions worth confirming before the probe.

---

STATUS=READY_FOR_PROCESS_HARDENING_REVIEW
PYTHON_E_FLAG=PASS
PYTHON_s_FLAG=PASS
USER_SITE_DISABLED=PASS
PYTHON_ENV_INFLUENCE_REDUCED=PASS
GUARD_FILE_PREFLIGHT=PASS
EXACT_ARGV_GUARD=PRESERVED
H1_H2_RESOLUTION=PRESERVED
M1_M2=OPEN
EXACT_AUTHORIZATION_OPERATIONALLY_PROVEN=NO
ISOLATED_REAL_HOOK_PROBE_AUTHORIZED=NO
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_INVOCATIONS=0
AUTOMATIC_GIT_ACTIONS=0
NEXT_ACTION=OWNER_RUNS_NARROW_REVIEW_THEN_ISOLATED_REAL_HOOK_PROBE
