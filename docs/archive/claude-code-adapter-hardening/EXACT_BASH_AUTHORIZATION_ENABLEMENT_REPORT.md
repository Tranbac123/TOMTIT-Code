# Exact-Argv Bash Authorization — Enablement Report

## 1. Status

**Enabled, pending review.** The layered exact-argv authorization capability is
active by default. A request carrying valid `authorized_commands` now proceeds
through the already-existing guarded execution path instead of returning
`UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION`.

This was enablement only. No guard, validator, fingerprint, permission, or
persistence logic was redesigned. `exact_bash_guard.py` is byte-for-byte
untouched (SHA-256 `b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15`,
identical to the value recorded in all three prior security reviews).

## 2. Starting HEAD

```
git rev-parse HEAD -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d
```

Unchanged; no commits made.

## 3. Why native bare-Bash exactness remains False

`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` stays `False`, and the flag was
**not** repurposed. Its name asserts a claim about Claude's own permission
engine that the real 2.1.223 probe **disproved**: with only the native rule in
place, `command <exact>` executed. Native bare-rule matching is applied *after* a
fixed set of process-wrapper normalizations (`command`, `builtin`, `timeout`,
`time`, `nice`, `nohup`, `stdbuf`, zsh `noglob`, certain env-assignment prefixes,
bare `xargs`) are stripped — i.e. provider-*normalized* equivalence, never
TOMTIT's Owner-approved exact argv.

Setting that constant `True` would have written a measured falsehood into the
codebase. Its comment block was rewritten to record the disproof rather than the
old "not yet verified" framing, and it is retained purely as that durable record.

**It no longer gates anything.** `resolve_claude_cli_authorization_contract()`
does not read it — asserted by a test that inspects the resolver's own source
(`test_enablement_2_native_bare_bash_exactness_is_not_claimed`). This is what
guarantees there is no code path on which Claude's native `--allowedTools` alone
satisfies `EXACT_SUPPORTED`.

## 4. Layered capability enablement

New single gate in `src/tomtit/adapters/claude_code_cli.py`:

```python
_CLAUDE_CLI_LAYERED_EXACT_ARGV_AUTHORIZATION_CONFIRMED_SAFE = True
```

Named for the layered TOMTIT capability, not for any provider behavior. Its
comment enumerates the full contract it asserts:

| # | Layer | Where enforced |
|---|---|---|
| 1 | Owner-authorized canonical argv validation | `validate_authorized_command_argv`, re-run unconditionally by `ClaudeCodeCLIRequest.__post_init__` |
| 2 | TOMTIT PreToolUse exact-argv fingerprint guard | `exact_bash_guard.py` (shared validator + SHA-256 contract) |
| 3 | Hardened guard-process invocation `-E -s` | `_EXACT_BASH_GUARD_PYTHON_ISOLATION_FLAGS` |
| 4 | Guard-file preflight | `_verified_exact_bash_guard_module_path()` |
| 5 | Claude native `--allowedTools` | `_build_argv` |
| 6 | Request `--permission-mode` (never `bypassPermissions`) | `_build_argv` |
| 7 | Provider-output withholding + typed safe summary | `_summarize_provider_output` + the persistence boundary in `run()` |

Layers 2-4 are not merely *asserted* by the flag — they are enforced at runtime:
`run()` fails closed with `EXACT_BASH_GUARD_UNAVAILABLE`, before starting any
process, if the guard artifacts (including the preflight) cannot be constructed.
Layer 7 is enforced by the persistence boundary. The flag records an Owner
decision that the layered design is safe; it cannot substitute for any layer.

Setting it `False` restores the previous dormant behavior with no other code
change.

## 5. Authorization-contract resolver behavior

`resolve_claude_cli_authorization_contract()` now keys **solely** on the layered
flag. The previous `if _CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT: return
EXACT_SUPPORTED` branch was deleted outright — leaving it would have preserved
exactly the forbidden native-only path.

Both rationales were rewritten to describe the layered contract. The UNSUPPORTED
rationale now states *why* native matching alone is insufficient (wrapper
stripping) rather than the obsolete "not behaviorally confirmed".

Observed:

```
contract  -> EXACT_SUPPORTED
rationale -> "layered TOMTIT exact-argv authorization is enabled: Owner-authorized
              canonical argv validation, the TOMTIT PreToolUse exact-argv fingerprint
              guard (hardened -E -s invocation, guard-file preflight), Claude's native
              --allowedTools rules, the request's permission mode, and provider-output
              persistence withholding all apply together; exactness comes from the
              TOMTIT guard, never from Claude's own wrapper-normalized bare-rule matching"
```

## 6. Security layers preserved

Verified against the shipped default posture (no monkeypatch):

| Property | State |
|---|---|
| `exact_bash_guard.py` | byte-identical; 70/70 guard tests pass unmodified |
| Canonical validator / fingerprint algorithm | unchanged |
| H-1 trusted import boundary, H-2 exit-0/2 boundary | unchanged (guard file untouched) |
| Hook invocation | `<sys.executable> -E -s <absolute guard path>` — asserted as an exact 4-element argv |
| Guard-file preflight | intact; a substituted path still fails closed **before** the worker starts |
| Native `--allowedTools` | present alongside `--settings`, never instead of |
| Permission mode | request's own (`acceptEdits`); `bypassPermissions` appears nowhere in `src`, `tests`, or `.tomtit/dogfood` except as a prohibition comment |
| Wrapper / extra-arg / compound / `run_in_background` | denied by the guard, exercised against the settings an enabled run actually generated |
| Fingerprint-only artifacts | unchanged |

## 7. No-command compatibility

With `authorized_commands` empty, nothing changed: no `--settings`, no
`--allowedTools`, `exact_bash_guard_enabled=False`,
`provider_output_withheld=False`, `stdout_excerpt` passthrough byte-identical,
and the legacy `run_loop` telemetry path still used. Independently re-confirmed
with a fresh probe outside the test suite (env-secret and HOME redaction also
still applied).

## 8. Evidence-safety preservation

M-1/M-2 remain closed under the enabled default. I re-ran my own independent
evidence-safety probe — which drives the real `run()` and the real
`run_task.persist_evidence()`, then greps the actual on-disk
`result.json`/`receipt.json`/`diff.patch` — with **0 failing checks** across all
40 adversarial cases. Enablement-specific coverage was added for an unknown
future provider key, deep nesting, and malformed JSON on an enabled authorized
run.

`provider_output_withheld=True` and `exact_bash_guard_enabled=True` are now
asserted on enabled authorized runs, including end-to-end through the dogfood
receipt/result pair.

## 9. Tests and exit codes

24 tests added or updated: 14 new enablement tests in
`tests/adapters/test_claude_code_cli.py`, 2 dormancy tests repointed to the
layered flag, 3 dogfood tests updated, and 14 pre-existing enable-monkeypatch
sites repointed from the bare-Bash constant to the layered flag (they had
silently become no-ops otherwise, which would have left them asserting a stale
belief).

All 20 required proofs are covered; the new block deliberately does **not**
monkeypatch, so it pins the shipped posture rather than merely the code path.

```
python3 .tomtit/dogfood/test_run_loop.py                            -> EXIT=0
python3 .tomtit/dogfood/test_run_repair.py                          -> EXIT=0
python3 .tomtit/dogfood/test_run_review.py                          -> EXIT=0
python3 -m pytest tests/adapters/test_exact_bash_guard.py -q        -> EXIT=0   (70 tests, unmodified)
python3 -m pytest tests/adapters -q                                 -> EXIT=0   (404 tests; was 390, +14)
python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q  -> EXIT=0   (4 tests)
python3 -m pytest -q                                                -> EXIT=0   (730 tests; was 716, +14)
python3 -m compileall -q src tests scripts .tomtit/dogfood          -> EXIT=0
ruff check src tests scripts                                        -> EXIT=0
python3 -m mypy src                                                 -> EXIT=0   ("Success: no issues found in 14 source files")
git diff --check                                                    -> EXIT=0
```

**Flakes: none observed.** Every suite above passed on its first attempt,
including the full suite. The known F-1 process-cleanup timing flake
(`test_normal_exit_with_surviving_descendant_kills_descendant` /
`test_delayed_descendant_write_is_prevented_before_final_snapshot`) did not
reproduce in this task. Nothing was rerun and no result discarded.

**Intermediate failures during implementation, recorded honestly.** Five tests
failed mid-task because they encoded the *dormant* posture as expected behavior —
they were found by running the suites, not anticipated:

- `test_unsupported_cli_contract_fails_closed_by_default` and
  `test_unsupported_cli_contract_fails_before_model_invocation`
  (`tests/adapters/`) asserted UNSUPPORTED with no flag set. Both now toggle the
  layered flag **off** and assert the same fail-closed behavior, so the
  fail-closed direction stays proven rather than deleted.
- `test_run_task_propagates_exact_authorized_commands` and
  `test_loop_persists_and_reuses_identical_authorized_commands`
  (`.tomtit/dogfood/test_run_loop.py`) and
  `test_repair_preserves_original_authorized_command_set`
  (`.tomtit/dogfood/test_run_repair.py`) asserted `exit_code == 1`/`3` plus
  `error_category == "UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION"`. Each test's own
  comment stated its real subject was *propagation*, not the fail-closed
  outcome. They now assert the enabled outcome while keeping every fingerprint
  assertion, and I **added** assertions they previously lacked:
  `exact_bash_guard_enabled is True`, `provider_output_withheld is True`, and
  that no raw command text reaches the receipt.

One self-caught error: my first draft of that last check asserted no argv
*token* appears in the receipt, which failed on the 3-character token `foo`
matching inside a temp directory path. Narrowed to the joined command string and
the distinctive `--save-exact` token — the property actually at issue.

## 10. Scope / non-goals

Modified:

- `src/tomtit/adapters/claude_code_cli.py` — new layered flag, rewritten
  bare-Bash comment, rewritten resolver, one corrected docstring.
- `tests/adapters/test_claude_code_cli.py` — enablement tests and flag
  repointing.
- `.tomtit/dogfood/test_run_loop.py`, `.tomtit/dogfood/test_run_repair.py` —
  **outside the stated primary-file scope.** Flagged explicitly rather than
  passed over: these three tests asserted the dormant behavior the task
  requires be changed, so leaving them would have meant shipping a red suite.
  Only the outcome assertions were updated; no assertion was weakened and three
  were added. If the Owner prefers these reverted and handled separately, the
  change is isolated to those three test functions.

Not modified: `exact_bash_guard.py`, `tests/adapters/test_exact_bash_guard.py`,
`src/tomtit/adapters/__init__.py` (no export needed — the new flag is private and
`resolve_claude_cli_authorization_contract` was already exported), Runtime,
Coordinator, evaluation, governance, memory, routing, personal-web, and all
historical dogfood evidence.

**Observation, not a change:** 8 occurrences of `"command": "npm run lint"` /
`"npm run build"` remain in `.tomtit/dogfood/reviews/*/checks.json` and
`review-receipt.json`. I traced these to `run_review.run_check_command()` — they
are TOMTIT's own locally configured check commands (LOCAL_CHECK_OUTPUT), not
provider output, so they are consistent with the sanitization report's
provider-scoped claim. Recorded so the reviewer is not surprised by them.

## 11. Final Git state

```
git rev-parse HEAD -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d  (unchanged)
git diff --cached  -> (empty; nothing staged)
git diff --check   -> EXIT=0

tracked, modified:
  M src/tomtit/adapters/__init__.py         (predates this task; untouched by it)
  M src/tomtit/adapters/claude_code_cli.py
  M tests/adapters/test_claude_code_cli.py
untracked:
  ?? src/tomtit/adapters/exact_bash_guard.py     (unchanged by this task)
  ?? tests/adapters/test_exact_bash_guard.py     (unchanged by this task)
  ?? (report .md files)
(.tomtit/ is gitignored, so the two dogfood test edits do not appear in git status)
```

No commit, stage, reset, stash, checkout, push, merge, or deploy. No real Claude
or Codex invocation. No TOMTIT self-run. No `bypassPermissions`. No personal-web
rerun. No historical evidence modified.

## 12. Next gate

Owner-coordinated fresh enablement review. Suggested focus, given what this task
touched: that the resolver cannot yield `EXACT_SUPPORTED` from any native-only
signal; that the three dogfood test updates (§9, §10) relaxed no security
assertion; and that the enabled default holds the layered contract end to end.

---

STATUS=READY_FOR_EXACT_AUTHORIZATION_ENABLEMENT_REVIEW
NATIVE_BARE_BASH_RAW_EXACTNESS=False
LAYERED_EXACT_ARGV_AUTHORIZATION=ENABLED_PENDING_REVIEW
PRETOOLUSE_GUARD=ENABLED
PYTHON_PROCESS_HARDENING=PRESERVED
GUARD_FILE_PREFLIGHT=PRESERVED
CLAUDE_NATIVE_ALLOWEDTOOLS=PRESERVED
PROVIDER_OUTPUT_EVIDENCE_SAFETY=PRESERVED
EXACT_ARGV_OPERATIONALLY_PROVEN=YES
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_INVOCATIONS=0
AUTOMATIC_GIT_ACTIONS=0
NEXT_ACTION=OWNER_RUNS_FRESH_ENABLEMENT_REVIEW
