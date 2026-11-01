# File-Write Guard Integration Report

## 1. Status

Complete. All test-harness positional-hook assumptions replaced with
matcher-based selection. Full prescribed verification passes. **Zero
production source files were modified this turn** — every failure diagnosed
and fixed was a test-harness defect, not an integration defect.

## 2. Starting HEAD

`f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d` (unchanged; no commits, this turn
or prior).

## 3. Work already completed before this turn

- `src/tomtit/adapters/exact_file_write_guard.py` — the standalone guard.
- File-write guard integration in `src/tomtit/adapters/claude_code_cli.py`
  (always-on file guard/settings; conditional Bash guard/sandbox).
- `tests/adapters/test_exact_file_write_guard.py` — 47 tests, all passing.
- Obsolete `no authorized_commands -> no --settings` assertions intentionally
  updated to the new locked contract.
- Adapter integration tests added.
- `FILE_WRITE_GUARD_INTEGRATION_FULL.log` with the earlier failing sections
  preserved (not erased this turn).

## 4. Remaining test-harness root cause

25 adapter tests failed on the targeted rerun. Diagnosis confirmed the
prior turn's hypothesis exactly: every failure traced to
`settings["hooks"]["PreToolUse"][0]` assuming the Bash hook is first. Since
the integration now emits the four file-write matchers before the Bash
matcher, index `[0]` resolved to the `Write` hook. Concrete symptoms in the
log: a Bash payload fed to the Write guard, the file-write denial string
compared against the Bash denial string, a valid Bash call denied by the
wrong hook, and hook-command assertions finding
`exact_file_write_guard.py` where `exact_bash_guard.py` was expected.

## 5. Why hook-index assumptions were invalid

Hook ordering was never part of the settings contract — nothing in the
integration promises the Bash entry is first, and the locked architecture
(file guard always present, Bash guard conditional and appended after) makes
"first" actively wrong. The tests were relying on an implementation detail
of construction order, not a stated invariant. Hook *identity* (matcher
value) is the actual contract.

## 6. Matcher-based harness correction

Added `_pretool_hook_by_matcher(settings, matcher)`, requiring **exactly
one** match — raises with a clear message if the hook is missing, and
equally if it is duplicated (never silently takes the first of several).
Because `_fake_claude_invoking_real_hook` generates a script that runs in a
**separate subprocess**, it cannot call this in-process helper; a logically
identical standalone snippet (`_PRETOOL_HOOK_BY_MATCHER_SUBPROCESS_SNIPPET`)
is embedded into the generated script text instead, kept next to the
in-process version so they cannot silently drift apart.

Five call sites converted from `[0]` to matcher-based lookup:
`test_exact_bash_guard_artifacts_contain_only_fingerprints`,
`_fake_claude_invoking_real_hook` (the subprocess snippet),
`test_hardening_settings_file_records_the_hardened_hook_command`,
`test_enablement_3_to_7_authorized_run_wires_every_layer_by_default`,
`test_corrective_sandbox_settings_contract`.

`grep -n 'PreToolUse"\]\[0\]' tests/adapters/test_claude_code_cli.py` now
returns nothing.

Also added `test_pretool_use_hook_counts_by_authorization_state`, asserting
the explicit count invariant you specified: exactly one hook per mutating
file tool always; exactly one Bash hook with `authorized_commands`; zero
Bash hooks without it. And strengthened
`test_file_guard_all_four_matchers_present` to route through
`_pretool_hook_by_matcher` (so it too now fails on duplication, not only on
absence) rather than a plain membership check.

**Three additional stale-signature failures found during the same targeted
run**, same root class (test doubles not updated for a parameter added in a
prior turn, not this one): `_capture`/`_raise` monkeypatch stubs for
`_write_exact_bash_guard_artifacts` still declared exactly two parameters,
but the function now takes an optional third
(`allowed_changed_paths=()`). Fixed by adding `*args: object` to each of the
three stubs (`test_exact_bash_guard_artifacts_removed_after_run`,
`test_exact_bash_guard_unavailable_fails_closed`,
`test_corrective_early_guard_failure_preserves_side_effect_contract`) so
they accept and forward the extra argument. This is the same class of
pre-existing-signature-drift issue noted in earlier turns, not a new defect.

## 7. Exact-file-write guard verification

`tests/adapters/test_exact_file_write_guard.py`: 47/47 pass, unchanged this
turn (no modification needed — these tests never touched the
`PreToolUse[0]` assumption).

## 8. Adapter integration verification

`tests/adapters/test_claude_code_cli.py`: all tests pass after the harness
corrections above. No production assertion was weakened to achieve this —
every fix either selected the correct hook by identity or accepted an
already-correct extra call argument.

## 9. Always-on file guard/settings invariant

Confirmed by `test_file_guard_settings_present_on_every_run`,
`test_no_bash_authorization_leaves_bash_guard_flag_false`, and
`test_pretool_use_hook_counts_by_authorization_state`: `--settings` and an
empty `--setting-sources` are present on every run, and all four mutating
file-tool matchers are present exactly once regardless of
`authorized_commands`.

## 10. Conditional Bash guard/sandbox invariant

Confirmed by `test_file_guard_present_without_bash_authorization` (zero
Bash hooks, no `sandbox` key, `fingerprints_path is None` when
`authorized_commands` is empty) and
`test_authorized_commands_run_has_every_layer` (Bash hook, sandbox, and
`--allowedTools` all present together when it is not).

## 11. Authorization independence

`test_side_effect_paths_do_not_authorize_file_writes`,
`test_source_allowlist_does_not_authorize_bash`, and
`test_authorized_commands_do_not_broaden_source_allowlist` all pass, proving
the three contracts (`allowed_changed_paths`, `allowed_side_effect_paths`,
`authorized_commands`) remain mutually non-granting.

## 12. Legacy test updates

No further legacy-test updates were needed this turn beyond the matcher-fix
and stub-signature fixes above; the settings-invariant renames were already
completed in the prior turn.

## 13. Security regression checks

- Bash guard SHA-256 unchanged: `b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15`.
- `-E -s` hardening on both guards confirmed
  (`test_file_guard_integration_preserves_bash_guard_hardening`,
  `test_file_guard_all_four_matchers_present`).
- `--setting-sources ""` lockdown confirmed present on every run.
- Sandbox contract (`enabled`/`failIfUnavailable`/
  `allowUnsandboxedCommands`/`autoAllowBashIfSandboxed`/no
  `excludedCommands`) unchanged, verified via `_pretool_hook_by_matcher`
  rather than `[0]`.
- Provider-output withholding unchanged
  (`test_file_guard_integration_preserves_provider_output_withholding`).
- Prompt placement (exactly once, immediately after `-p`, before
  `--allowedTools`) unchanged
  (`test_file_guard_integration_preserves_prompt_placement`).
- Native bare-Bash exactness flag still `False`; layered exact authorization
  still `True` (`test_file_guard_integration_preserves_capability_flags`).

## 14. Tests and exact exit codes

```
1. test_claude_code_cli.py -q                          -> EXIT=1 (first, positional-hook failures) -> EXIT=0 (after fix)
2. test_exact_file_write_guard.py -q                    -> EXIT=0
3. test_exact_bash_guard.py -q                           -> EXIT=0
4. tests/adapters -q                                     -> EXIT=0
5. .tomtit/dogfood/test_run_loop.py                      -> EXIT=0
6. .tomtit/dogfood/test_run_repair.py                    -> EXIT=0
7. .tomtit/dogfood/test_run_review.py                    -> EXIT=0
8. tests/scenarios/test_claude_code_cli_spike.py -q      -> EXIT=0
9. pytest -q (full suite)                                -> EXIT=0
10. compileall -q src tests scripts .tomtit/dogfood       -> EXIT=0
11. ruff check src tests scripts (via python3 -m ruff)     -> EXIT=0
12. mypy src                                              -> EXIT=0 ("no issues found in 15 source files")
13. git diff --check                                      -> EXIT=0
14. shasum -a 256 exact_bash_guard.py                      -> matches expected
```

## 15. First-run failures/flakes

**Real (fixed) failures, first run:** 25 adapter test failures, all
positional-hook-index or stale-stub-signature test-harness defects (§4/§6),
zero production defects.

**Flake, honestly attributed:**
`test_normal_exit_with_surviving_descendant_kills_descendant` failed once in
5 isolated reruns (4/5 pass) during targeted-failure attribution, and did
**not** fail during the subsequent full-suite run (#9), which passed clean
on the first attempt. This is the pre-existing process-cleanup timing flake
documented in prior reports. Confirmed unrelated to this turn's work: `grep`
for `authorized_commands|allowed_side_effect_paths|file_write_guard|exact_bash_guard`
inside its body returns zero matches — it neither authorizes Bash nor
touches guard construction.

## 16. Interpreter/environment note

`python3` on `PATH` in this session resolves to
`/usr/bin/python3`, which lacks `pytest`/`mypy`. All verification in this
turn used `/usr/local/bin/python3` explicitly, per instruction. `ruff` as a
bare command was **not on `PATH`** in this session
(`command not found`, exit 127); it was invoked as
`/usr/local/bin/python3 -m ruff` instead (ruff 0.1.7), which is the same
package and produced a clean result. Both the failure and the workaround
are logged verbatim in the full log rather than silently substituted.

## 17. Full log path

`FILE_WRITE_GUARD_INTEGRATION_FULL.log` (2045 lines) — every command from
this turn and the prior turn, labeled sections, full stdout/stderr, exact
exit codes. Earlier failing sections from the prior turn were not erased.

## 18. Files modified this turn

- `tests/adapters/test_claude_code_cli.py` — matcher-based hook helper +
  five call-site corrections, three monkeypatch stub signature fixes, one
  strengthened test, one new hook-count invariant test.

No other file was modified this turn.

## 19. Production source changed this turn: **NO**

Every diagnosed failure was a test-harness defect. No file under `src/` was
touched.

## 20. Final Git state

```
HEAD f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d (unchanged)
 M src/tomtit/adapters/__init__.py         (from a prior turn, untouched this turn)
 M src/tomtit/adapters/claude_code_cli.py  (from a prior turn, untouched this turn)
 M tests/adapters/test_claude_code_cli.py  (this turn)
git diff --cached -> empty; git diff --check -> EXIT=0
```

No commit/stage/reset/stash/checkout/push/merge/deploy. No real Claude
model or Codex invocation. No TOMTIT self-run. personal-web untouched.

## 21. Remaining assumptions

Unchanged from the prior turn's report: the OS-level sandbox enforcement is
configuration-verified, not OS-proven (requires a real sandboxed run, out of
scope here); the file-write guard's `.git`/symlink/traversal denial is
proven by the guard's own 47 tests, not re-litigated in this turn.

## 22. Deferred M-1 work

`M1_CHANGED_PATH_CONSUMERS` remains deferred to a future turn, as instructed.
No `.tomtit/dogfood/run_review.py`, `run_repair.py`, or `run_loop.py` file
was touched.

## 23. Next gate

Owner review of this integration turn, focused on confirming the
matcher-based hook-selection contract is what should be relied on going
forward (never positional), then scheduling the deferred M-1
changed-path-consumer reconciliation turn.

---

STATUS=READY_FOR_FILE_WRITE_GUARD_INTEGRATION_REVIEW
TEST_HARNESS_POSITIONAL_HOOK_ASSUMPTION=RESOLVED
HOOK_SELECTION_BY_MATCHER=PASS
EXACT_FILE_WRITE_GUARD_TESTS=PASS
WRITE_GUARD_IMPLEMENTED=YES
WRITE_GUARD_INTEGRATED=YES
WRITE_GUARD_PRESENT_EVERY_RUN=YES
SETTINGS_PRESENT_EVERY_RUN=YES
SETTING_SOURCES_LOCKDOWN_EVERY_RUN=YES
BASH_SANDBOX_ONLY_WHEN_AUTHORIZED_COMMANDS=YES
BASH_GUARD_ONLY_WHEN_AUTHORIZED_COMMANDS=YES
WRITE_OUTSIDE_WORKSPACE=DENIED
WRITE_TO_GIT_METADATA=DENIED
WRITE_TO_UNAPPROVED_SOURCE=DENIED
FILE_TOOL_SYMLINK_ESCAPE=DENIED
LEXICAL_ALIAS_WIDENING=DENIED
SIDE_EFFECT_PATH_GRANTS_FILE_WRITE=NO
FILE_GUARD_FAIL_CLOSED=YES
BASH_GUARD=PRESERVED
BASH_GUARD_SHA256=b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15
CLAUDE_SANDBOX_CONFIG=PRESERVED
SETTING_SOURCES_LOCKDOWN=PRESERVED
EXACT_BASH_AUTHORIZATION=PRESERVED
PROVIDER_OUTPUT_EVIDENCE_SAFETY=PRESERVED
PRODUCTION_SOURCE_CHANGED_THIS_TURN=NO
FULL_LOG=FILE_WRITE_GUARD_INTEGRATION_FULL.log
M1_CHANGED_PATH_CONSUMERS=DEFERRED_TO_NEXT_TURN
PERSONAL_WEB_TOUCHED=NO
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_MODEL_INVOCATIONS=0
REAL_CODEX_INVOCATIONS=0
AUTOMATIC_GIT_ACTIONS=0
NEXT_ACTION=OWNER_RUNS_NARROW_FILE_WRITE_GUARD_REVIEW
