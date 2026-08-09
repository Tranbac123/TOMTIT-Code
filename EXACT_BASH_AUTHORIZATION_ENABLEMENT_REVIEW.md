# Exact-Argv Bash Authorization: Final Enablement Review

**Scope:** final enablement semantics only. H-1, H-2, M-1, M-2 and the real
Claude Code 2.1.223 probe were previously verified and are not re-litigated,
except for the regression confirmation in §9.

**Method:** source inspection plus independently authored runtime probes
against the real adapter with fake `claude` executables. The enablement report
was read for orientation but is not treated as evidence; every finding below
comes from source, tests, or an executed probe. No implementation file was
modified, no real Claude/Codex invoked, no TOMTIT self-run, no git write.

**HEAD:** `f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d` (unchanged; nothing staged).

## 1. Verdict

**APPROVE_WITH_NON_BLOCKING_FINDINGS.**

The layered exact-argv capability is correctly gated, genuinely layered, and
fails closed in every direction probed. One non-blocking finding (N-1) concerns
test-suite flake rate, not the capability.

## 2. Review area 1 — capability semantics: PASS

| Check | Result |
|---|---|
| `_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT` | `False` at line 373 |
| It gates nothing | Confirmed: the only occurrences in source outside its definition are at lines 493 and 544, both inside comments/docstrings. No `if`, `return`, or expression reads it. |
| `_CLAUDE_CLI_LAYERED_..._CONFIRMED_SAFE` is the sole signal | `True` at line 407; `resolve_claude_cli_authorization_contract` (line 539) branches on it alone |
| Single EXACT_SUPPORTED producer | line 556 — the only `return ...EXACT_SUPPORTED` in the module |
| Single resolver call site | line 1822, consumed at 1823 |

**Runtime probe — layered flag forced `False` in memory:**

```
contract                 = UNSUPPORTED
status                   = FAILED
error_category           = UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION
exact_bash_guard_enabled = False
WORKER STARTED           = False
```

**Native-only enable path — probed, not merely reasoned about.** Set
`native=True, layered=False` simultaneously:

```
status                   = FAILED
error_category           = UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION
WORKER STARTED           = False
```

`NATIVE_ONLY_ENABLE_PATH=ABSENT`, confirmed at runtime as well as by reading.

## 3. Review area 2 — layered contract is real, not nominal: PASS

The decisive property is that a `True` capability flag must not by itself
permit execution. Probed three independent breakages, all with `layered=True`:

| Broken layer | error_category | Worker started |
|---|---|---|
| Guard artifact construction raises `OSError` | `EXACT_BASH_GUARD_UNAVAILABLE` | **False** |
| Guard-file preflight: file missing | `EXACT_BASH_GUARD_UNAVAILABLE` | **False** |
| Guard-file preflight: path is a symlink | `EXACT_BASH_GUARD_UNAVAILABLE` | **False** |

In every case the fake `claude` never wrote its start marker, proving the
failure precedes process launch. The capability flag records an Owner
decision; it does not substitute for any layer.

All seven layers verified present on the enabled path (§4).

## 4. Review area 3 — enabled default behavior: PASS

No monkeypatching of any flag. Real `run()` with a fake `claude` that parses
its own argv and executes the configured PreToolUse hook exactly as Claude
would:

```
resolve_claude_cli_authorization_contract() = EXACT_SUPPORTED
status                                      = SUCCEEDED
exact_bash_guard_enabled                    = True
provider_output_withheld                    = True
--settings present in real argv             = True
--allowedTools present in real argv         = True
hook command contains -E                    = True
hook command contains -s                    = True
```

Guard decisions from the real hook binary (0 = silent defer, 2 = block):

| Probe | Hook exit |
|---|---|
| exact authorized argv | **0** |
| `command <exact>` wrapper | 2 |
| `timeout 5 <exact>` wrapper | 2 |
| extra argument | 2 |
| compound (`&&`) | 2 |
| `run_in_background: true` | 2 |

This reproduces the real-probe matrix offline. No real Claude invoked.

## 5. Review area 4 — dormant-direction regression: PASS

Covered by the probe in §2. Additionally, `test_enablement_2b_disabling_the_layered_flag_restores_fail_closed`
and `test_enablement_15_broken_guard_preflight_fails_closed_when_enabled`
encode both directions in the suite. Guard artifacts are never treated as
proof of enablement: with `layered=False`, `exact_bash_guard_enabled` is
`False` and no artifacts are constructed at all.

## 6. Review area 5 — dogfood test changes: SAFE

Three changed functions inspected line by line:

| Test | Verdict |
|---|---|
| `test_run_task_propagates_exact_authorized_commands` | SAFE |
| `test_repair_preserves_original_authorized_command_set` | SAFE |
| `test_loop_persists_and_reuses_identical_authorized_commands` | SAFE |

In all three, **every pre-existing propagation assertion is intact** —
per-command fingerprints, ordered-set fingerprint, count, absence of
`authorized_commands` / `authorized_commands_display` keys, and the
whole-evidence-tree sweep asserting no raw argv or raw `Bash(...)` rule. The
only change is the *outcome* expectation (previously fail-closed
`UNSUPPORTED`, now completes), which is the correct consequence of enablement.

New assertions are meaningful additions, not substitutions:
`error_category != "UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION"`,
`exact_bash_guard_enabled is True`, `provider_output_withheld is True`, and
fresh `receipt_json` checks that the joined argv and `--save-exact` never
appear.

**Negative-direction tests independently confirmed still enforcing failure:**
`cannot_add_a_new_authorized_command` (exit 3), `cannot_replace_an_authorized_command`
(exit 3), `findings_cannot_introduce_bash_authorization` (exit 3 **and**
`not (tmp_path/"runs").exists()`), `rejects_authorized_command_against_legacy_evidence_without_fingerprint`
(exit 3). 37 authorization-propagation assertions remain across the two
dogfood test files.

No security assertion was removed to make the suite green.

## 7. Review area 6 — stale enable monkeypatches: NONE

- **No test monkeypatches the native bare flag.** `grep` for
  `setattr(..._CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT...)` across `tests/`
  and `.tomtit/` returns nothing. Its only three remaining test references are
  assertions that it *is* `False` plus a source-body check that the resolver
  does not consult it.
- **All enabled-path monkeypatches target the layered flag** (21 sites).

**Are the monkeypatches no-ops?** Answered empirically rather than by
inspection. I injected a pytest plugin (via `PYTHONPATH`, touching no
repository file) that forces the layered flag `False` at session start, then
re-ran the adapter suite:

```
baseline (shipped default True)   : all pass
layered forced False at session   : exactly 11 failures
```

The 11 failures are precisely the `test_enablement_*` block, which
deliberately does **not** monkeypatch — proving those tests genuinely pin the
shipped default and are not vacuous. Every test that *does* monkeypatch
`layered=True` still passed under the forced-`False` session, proving those
monkeypatches are load-bearing (they pin the value against a changed default)
rather than silently redundant. Both branches are therefore actually
exercised.

## 8. Review area 7 — evidence safety under enabled default: PASS

Drove real `run()` → fake provider output → **real**
`run_task.persist_evidence()` → on-disk `result.json` / `receipt.json` /
`diff.patch`, using markers confirmed absent from the repository beforehand
(`ZQX7RAVEN9CMD`, `ZQX7RAVEN9SECRET` — 0 pre-existing occurrences). Assertions
are direct file-content greps, not a shared helper.

| Case | withheld | json_parsed | truncated | CMD leak | SECRET leak |
|---|---|---|---|---|---|
| `permission_denials[].tool_input.command` | True | True | False | **No** | **No** |
| unknown provider key | True | True | False | **No** | **No** |
| nested `content[].text` prose | True | True | False | **No** | **No** |
| malformed JSON | True | False | False | **No** | **No** |
| truncated JSON (`max_stdout_bytes=1024`) | True | **False** | **True** | **No** | **No** |
| pure non-JSON prose | True | False | False | **No** | **No** |

The truncated case genuinely truncated (`stdout_truncated=True`,
`json_parsed=False`) and still leaked nothing — the M-2 invariant holds under
the enabled default.

```
RAW_PROVIDER_COMMAND_NEW_EVIDENCE = NO
RAW_PROVIDER_SECRET_NEW_EVIDENCE  = NO
```

## 9. Review area 8 — no-command compatibility: PASS

With `authorized_commands` empty under the shipped enabled default:

```
exact_bash_guard_enabled = False
provider_output_withheld = False
--settings in argv       = False
--allowedTools in argv   = False
legacy stdout preserved  = True
all provider_* fields    = None
legacy telemetry path    = {'cost_usd': 2.5, 'input_tokens': 11, 'output_tokens': 22}
```

Telemetry still parses from `stdout_excerpt` JSON via the unchanged legacy
path. Enablement did not alter behavior for ordinary runs.

## 10. Review area 9 — execution-layer regression: PASS

```
exact_bash_guard.py sha256 = b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15
expected                   = b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15   MATCH
```

- 70 guard tests pass unmodified (H-1/H-2 suites intact).
- `_EXACT_BASH_GUARD_PYTHON_ISOLATION_FLAGS = ("-E", "-s")` at line 624, still
  applied at line 696.
- `_verified_exact_bash_guard_module_path` present and enforced (§3).
- `--allowedTools` still added alongside, never instead of, `--settings`.
- `run_in_background` rejection verified live (§4).
- M-1/M-2 persistence boundary intact; `_ProviderOutputSummary` still has **no
  `str`-typed field** (checked programmatically).
- `shell=True`: AST scan finds **0** real keyword arguments (the single grep
  hit is a comment). `bypassPermissions`: **0** real uses (single hit is the
  comment "never `bypassPermissions`").

## 11. Verification — exact exit codes

```
python3 .tomtit/dogfood/test_run_loop.py                            -> EXIT=0   (52 tests)
python3 .tomtit/dogfood/test_run_repair.py                          -> EXIT=0   (51 tests)
python3 .tomtit/dogfood/test_run_review.py                          -> EXIT=0   (45 tests)
python3 -m pytest tests/adapters/test_exact_bash_guard.py -q        -> EXIT=0   (70 tests)
python3 -m pytest tests/adapters -q                                 -> EXIT=0   (404 tests)
python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q  -> EXIT=0   (4 tests)
python3 -m pytest -q            (1st run)                           -> EXIT=1   (1 failed, 729 passed)
python3 -m pytest -q            (2nd run)                           -> EXIT=1   (1 failed, 729 passed)
python3 -m pytest -q            (3rd run)                           -> EXIT=0   (730 passed)
python3 -m compileall -q src tests scripts .tomtit/dogfood          -> EXIT=0
ruff check src tests scripts                                        -> EXIT=0
python3 -m mypy src                                                 -> EXIT=0   ("no issues found in 14 source files")
git diff --check                                                    -> EXIT=0
```

## 12. Findings

### N-1 (NON-BLOCKING, INFO) — process-cleanup flake rate is higher than previously characterized

Two of three full-suite runs failed, on **different** tests:

- run 1: `tests/adapters/...::test_normal_exit_with_surviving_descendant_kills_descendant`
- run 2: `tests/scenarios/...::test_offline_spike_evidence_is_not_invalidated_by_a_delayed_descendant`

Both belong to the same family (bounded process-group cleanup / delayed
descendant timing) that prior reports recorded as flaky. **Attribution
checked, not assumed:** neither test references `authorized_commands`,
`exact_bash_guard`, or `provider_output` anywhere in its body (grep count 0),
and each passes 5/5 in isolation. They do not exercise the enablement feature.

Not a security regression, and not caused by the enablement logic. But the
observed rate (2/3) is worse than the "did not reproduce" / "1 in N" recorded
previously, and a plausible contributing factor is this change itself: the
suite grew to 730 tests and the new `test_enablement_*` block spawns many real
hook subprocesses, raising system load precisely where these assertions are
timing-sensitive. Recorded honestly rather than rerun away.

**Consequence for the gate:** "full suite green" is currently an unreliable
signal on this machine. It does not block enablement — every enablement-
specific assertion passed deterministically across all runs — but it should be
addressed before the suite is used as a release gate. Suggested (not
prescriptive): make the cleanup assertions load-tolerant, or mark that family
serial.

### No blocking findings.

## 13. What APPROVE does and does not authorize

Approved: the layered exact-argv Bash authorization capability is correctly
enabled and safe to proceed to a **fresh personal-web dogfood run**.

Not authorized by this review: TOMTIT modifying its own repository; broad
production rollout; `bypassPermissions`; any unrelated feature work.

## 14. Constraint compliance

No implementation file modified (`git status` identical to session start; only
this new review artifact added). No TOMTIT self-run. No real Claude or Codex
child invocation — all provider behavior was simulated with fake executables
and the real TOMTIT guard binary. No personal-web rerun. No git write
operation. Prior reports untouched.

---

VERDICT=APPROVE_WITH_NON_BLOCKING_FINDINGS
NATIVE_BARE_BASH_RAW_EXACTNESS=False
LAYERED_CAPABILITY_GATE=PASS
NATIVE_ONLY_ENABLE_PATH=ABSENT
AUTHORIZED_COMMAND_ENABLED_DEFAULT=PASS
DORMANT_DIRECTION_FAIL_CLOSED=PASS
PRETOOLUSE_GUARD=PASS
PYTHON_PROCESS_HARDENING=PASS
GUARD_FILE_PREFLIGHT=PASS
CLAUDE_NATIVE_ALLOWEDTOOLS=PASS
PROVIDER_OUTPUT_EVIDENCE_SAFETY=PASS
DOGFOOD_TEST_UPDATES=SAFE
STALE_ENABLE_MONKEYPATCHES=NONE
NO_COMMAND_COMPATIBILITY=PASS
EXACT_ARGV_OPERATIONALLY_PROVEN=YES
PERSONAL_WEB_DOGFOOD_READY=YES
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_CHILD_INVOCATIONS=0
NEXT_ACTION=OWNER_AUTHORIZES_FRESH_PERSONAL_WEB_DOGFOOD_RUN
