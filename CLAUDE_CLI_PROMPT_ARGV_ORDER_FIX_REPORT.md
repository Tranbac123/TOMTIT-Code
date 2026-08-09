# Claude CLI Prompt Argv-Ordering Fix Report

## 1. Status

Fixed. The task prompt now occupies a fixed positional slot immediately after
`-p`, before every variadic option (`--allowedTools`) that could otherwise
consume it. This was the smallest correct change: moving one line, with no
change to prompt transport, redaction, or any authorization/guard layer.

## 2. Starting HEAD

```
git rev-parse HEAD -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d
```

Unchanged; no commits made.

## 3. Root cause

`_build_argv` in `src/tomtit/adapters/claude_code_cli.py` constructed the real
Claude CLI invocation with the task prompt appended as the **last** element:

```python
argv = [executable, "-p", "--output-format", "json", "--permission-mode", request.permission_mode]
if exact_bash_guard_artifacts is not None:
    argv.extend(["--settings", str(exact_bash_guard_artifacts.settings_path)])
if authorized_rules:
    argv.append("--allowedTools")
    argv.extend(authorized_rules)
if request.model:
    argv.extend(["--model", request.model])
argv.append(request.prompt)   # <-- appended last
```

`--allowedTools` accepts an arbitrary number of positional rule values with no
closing delimiter — Claude keeps consuming tokens as rules until it hits the
next `--`-prefixed option. When `authorized_commands` is non-empty and the
prompt is the final argv element, it is the last token Claude sees after
`--allowedTools`, so Claude silently absorbs it as one more allowedTools rule
instead of recognizing it as the `-p` prompt argument. `-p` then receives no
prompt at all, and Claude exits 1 with:

```
Error: Input must be provided either through stdin or as a prompt argument when using --print
```

This matches the real dogfood failure exactly: `ERROR_CATEGORY=NONZERO_EXIT`,
`exit_code=1`, `changed_paths=[]` (Claude never got far enough to touch the
workspace), and a 94-byte stderr identical in length to the message above.

## 4. Why exact authorization was not the defect

The task's own manual probes (Claude CLI itself, `--permission-mode
acceptEdits`, every individual `--allowedTools` rule, the TOMTIT-generated
`--settings`, the PreToolUse hook, and `-E -s` hardening) each worked in
isolation, because a hand-typed command naturally puts the prompt right after
`-p` before any other flag. The failure only appeared when **all** of these
were combined in the exact argv order the adapter itself constructs — which
is precisely the ordering bug, not a defect in any individual layer. This
review independently confirmed the fix does not touch, weaken, or bypass the
exact-argv guard, the canonical validator, the fingerprint algorithm, `-E
-s`, guard-file preflight, `--allowedTools` layering, `run_in_background`
rejection, or provider-output withholding (§8).

## 5. Old argv shape

```
claude -p --output-format json --permission-mode acceptEdits
       --settings <settings> --allowedTools <rule1> <rule2> ... <ruleN>
       <TASK PROMPT>              <- silently absorbed as another allowedTools rule
```

## 6. Corrected argv shape

```
claude -p <TASK PROMPT> --output-format json --permission-mode acceptEdits
       --settings <settings> --allowedTools <rule1> <rule2> ... <ruleN>
```

The prompt is now the value immediately following `-p`; every other flag,
including the variadic `--allowedTools`, is added strictly after it. The
change in `_build_argv`:

```python
argv = [
    executable,
    "-p",
    request.prompt,          # moved here: fixed positional slot before any variadic option
    "--output-format",
    "json",
    "--permission-mode",
    request.permission_mode,
]
if exact_bash_guard_artifacts is not None:
    argv.extend(["--settings", str(exact_bash_guard_artifacts.settings_path)])
if authorized_rules:
    argv.append("--allowedTools")
    argv.extend(authorized_rules)
if request.model:
    argv.extend(["--model", request.model])
return argv                  # (trailing argv.append(request.prompt) removed)
```

No other line in `_build_argv`, `run()`, or anywhere else in the adapter
changed. `sanitized_argv` construction, redaction, and evidence rendering are
element-wise and order-independent, so moving the prompt required no changes
there (confirmed in §8).

## 7. Regression tests

Added to `tests/adapters/test_claude_code_cli.py`:

| # | Test | Proves |
|---|---|---|
| 1 | `test_prompt_argv_order_no_authorized_commands_prompt_present_once` | No `authorized_commands`: prompt present exactly once, immediately after `-p`; no `--allowedTools`/`--settings` present — existing behavior unchanged |
| 2 | `test_prompt_argv_order_authorized_commands_prompt_precedes_allowedtools` | With `authorized_commands`: `-p` present, prompt present exactly once, `prompt_index < allowedtools_index` (the required invariant), every rule present, `--settings` present with the correct path, permission mode unchanged |
| 3 | `test_prompt_argv_order_fake_cli_reproduces_old_ordering_failure_and_new_ordering_success` | A fake Claude parser reproducing the real CLI's specific behavior (`--allowedTools` greedily consumes positional values until the next `--`-prefixed option; `-p` fails with no captured prompt). The **old** ordering, constructed by hand to match the pre-fix argv, is run as a real subprocess and fails with `returncode == 1` and the exact stderr message. The **new** ordering, produced by the adapter's actual `_build_argv`, is run as a real subprocess and succeeds, with the parser correctly recovering both the prompt and all allowedTools rules |
| 4 | `test_prompt_argv_order_full_run_succeeds_with_authorized_commands` | End-to-end through the real `run()`: an authorized-command request against the fake-CLI parser now reaches `SUCCEEDED` with `exit_code == 0` and no `error_category` — the exact failure mode of the real dogfood run |
| 5 | `test_prompt_argv_order_preserves_exact_authorization_security_layers` | Guard enabled, fingerprints-only settings, provider-output withholding, no raw authorized-command text in sanitized argv or evidence manifest, native flag still `False`, prompt present exactly once and still positioned before `--allowedTools` in the *sanitized* argv too |
| 6 | `test_prompt_argv_order_run_in_background_still_rejected` | Non-regression: the exact-argv guard's `run_in_background` rejection (reasoned from the Bash tool call's own JSON payload, unrelated to the CLI invocation argv) is unaffected |

Test 3 is the executable regression proof the task required rather than a
list-index inspection: it runs both orderings as real subprocesses against a
fake CLI that models the actual defect mechanism, and asserts on process exit
codes and stderr/stdout content, not merely on `argv` list contents.

## 8. Security-layer regression check

Verified by inspection and by the tests above:

| Layer | Status |
|---|---|
| Exact Bash guard (`exact_bash_guard.py`) | **untouched** — not modified in this task (see §10); SHA-256 unchanged: `b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15` |
| Canonical argv validator / fingerprint algorithm | untouched — `_build_argv` does not call either |
| `-E -s` hardening | untouched — `_EXACT_BASH_GUARD_PYTHON_ISOLATION_FLAGS` and its use in `_render_exact_bash_guard_hook_command` unchanged |
| Guard-file preflight | untouched — `_verified_exact_bash_guard_module_path` unchanged |
| `--allowedTools` layering | preserved and now correctly reachable — still added alongside, never instead of, `--settings`/permission mode |
| `run_in_background` rejection | preserved — reasoned from the Bash tool payload, independent of adapter argv order (test 6) |
| Provider-output withholding (M-1/M-2) | untouched — `_summarize_provider_output`/placeholder logic unaffected by argv ordering; test 5 re-confirms `provider_output_withheld=True` and the placeholder excerpt on the fixed argv |
| No-command compatibility | preserved — test 1; no `--settings`/`--allowedTools` appear when `authorized_commands` is empty |
| Native bare-Bash flag | `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT = False`, unchanged, not read anywhere (test 5 asserts this explicitly) |
| Layered exact-argv capability | `_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE = True`, unchanged (shipped enabled default); independently probed with no monkeypatching -- prompt index 2 < allowedTools index 9, status `SUCCEEDED` |
| Sanitized argv / evidence rendering | element-wise and order-independent; test 5 confirms no raw authorized-command text reaches `sanitized_argv` or the evidence manifest with the prompt in its new position |

## 9. Tests and exact exit codes

```
python3 .tomtit/dogfood/test_run_loop.py                            -> EXIT=0   (52 tests)
python3 .tomtit/dogfood/test_run_repair.py                          -> EXIT=0   (51 tests)
python3 .tomtit/dogfood/test_run_review.py                          -> EXIT=0   (45 tests)
python3 -m pytest tests/adapters/test_exact_bash_guard.py -q        -> EXIT=0   (70 tests)
python3 -m pytest tests/adapters -q                                 -> EXIT=0   (410 tests; was 404, +6)
python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q  -> EXIT=0   (4 tests)
python3 -m pytest -q            (1st run)                           -> EXIT=0   (736 passed in 87.84s)
python3 -m pytest -q            (2nd run)                           -> EXIT=0   (736 passed in 63.83s)
python3 -m compileall -q src tests scripts .tomtit/dogfood          -> EXIT=0
ruff check src tests scripts                                        -> EXIT=0
python3 -m mypy src                                                 -> EXIT=0   ("Success: no issues found in 14 source files")
git diff --check                                                    -> EXIT=0
```

**Process-cleanup flake:** none observed. Both full-suite runs passed clean on
the first attempt; the timing-sensitive descendant-cleanup family noted in
prior reports did not flake in either run of this task. Recorded honestly per
the task's instruction, whether or not a flake occurred.

**Pre-existing tests updated (2), both because they encoded the old, buggy
argv position as an assertion, not because a safety check was removed:**

- `test_argv_values_are_not_shell_interpreted` asserted `argv_seen[-1] ==
  dangerous_prompt` (prompt is last). Updated to assert the prompt is the
  value immediately after `-p`, which is a *stronger* check — it fails if the
  prompt regresses to any other position, not only if it stops being last.
- `test_exact_supported_command_emits_only_expected_bounded_rule` asserted
  `sanitized_argv[idx+2] != sanitized_argv[idx+1]` to prove no extra rule
  followed the single authorized rule — relying on the old trailing prompt to
  exist at `idx+2`. Updated to `len(sanitized_argv) == idx + 2`, which proves
  the same invariant (nothing extra follows the rule) and is correct now that
  nothing, prompt included, follows `--allowedTools`.

## 10. Scope / non-goals

Modified only the two expected files:
`src/tomtit/adapters/claude_code_cli.py` (the one-line fix plus its
docstring/comment) and `tests/adapters/test_claude_code_cli.py` (2 assertions
corrected, 6 tests added). No other file required modification for this
regression.

Not modified: `exact_bash_guard.py`, Coordinator loop, Runtime, governance,
evaluation, personal-web, historical evidence. Prompt transport was not
redesigned and stdin was not adopted — source inspection confirmed moving the
positional prompt is sufficient (the fake-CLI executable regression test in
§7 proves this directly against the actual defect mechanism), so the "unless
proven insufficient" condition for a larger change was never met.

## 11. Final Git state

```
git rev-parse HEAD -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d  (unchanged)
git status --short (tracked):
  M src/tomtit/adapters/__init__.py         (predates this task, untouched by it)
  M src/tomtit/adapters/claude_code_cli.py  (this task's fix)
  M tests/adapters/test_claude_code_cli.py  (this task's regression tests)
git diff --cached -> (empty; nothing staged)
git diff --check  -> EXIT=0
```

No commit, stage, reset, stash, checkout, push, merge, or deploy. No real
Claude or Codex invocation. No TOMTIT self-run. No exact-authorization
capability flag flipped. No `bypassPermissions`. No personal-web rerun.

## 12. Next gate

Owner-coordinated narrow review of this argv-ordering fix, focused on
confirming the invariant (`PROMPT_POSITION < ALLOWED_TOOLS_POSITION`) and that
no security layer was altered, before authorizing a fresh personal-web
dogfood run to confirm the fix resolves the originally observed failure in
practice.

---

STATUS=READY_FOR_CLAUDE_CLI_PROMPT_ARGV_ORDER_REVIEW
ROOT_CAUSE=VARIADIC_ALLOWEDTOOLS_CONSUMED_TASK_PROMPT
PROMPT_BEFORE_ALLOWEDTOOLS=YES
PROMPT_PRESENT_EXACTLY_ONCE=YES
EXACT_AUTHORIZATION_LAYER=PRESERVED
PROVIDER_OUTPUT_EVIDENCE_SAFETY=PRESERVED
LAYERED_EXACT_ARGV_AUTHORIZATION=ENABLED
NATIVE_BARE_BASH_RAW_EXACTNESS=False
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_INVOCATIONS=0
PERSONAL_WEB_RERUN_AUTHORIZED=NO
AUTOMATIC_GIT_ACTIONS=0
NEXT_ACTION=OWNER_RUNS_NARROW_REVIEW
