# Exact-Argv PreToolUse Bash Guard: H-1 / H-2 Independent Security Re-Review

**Reviewer:** fresh Claude Code Opus session, Owner-coordinated.
**HEAD at review:** `f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d` (unchanged; no commits made).
**Scope:** H-1 and H-2 only as blocking findings. M-1/M-2 remain OPEN_MEDIUM and were not re-litigated.
**Method:** source control-flow inspection plus independently authored synthetic
fixtures and subprocess probes, written from the review task's own required
vector list — not by reading or reusing the implementer's test suite. The
implementation report was read for claims but was not treated as evidence.

## 1. Verdict

**APPROVE_WITH_NON_BLOCKING_FINDINGS.**

H-1 and H-2 are independently verified resolved. Every required attack vector
fails closed with exit code 2, a legitimate exact-authorized command still
PASSes silently through the *real* trusted module, and no reachable exit-code-1
path exists inside the guard. Three non-blocking findings (N-1, N-2, N-3) are
recorded below; none of them is an incremental exposure over an
already-total compromise, and none blocks a separately Owner-authorized
isolated real hook probe using only synthetic commands.

## 2. H-1 — trusted import boundary: RESOLVED

### 2.1 Source review

`src/tomtit/adapters/exact_bash_guard.py` satisfies all six required properties:

| # | Requirement | Where | Result |
|---|---|---|---|
| 1 | Trusted src derived solely from `Path(__file__).resolve()` | `_trusted_src_root()` L87-96 (`.parents[2]`) | MET — no `cwd`, no env var, no argv |
| 2 | Trusted path inserted before any `tomtit` import | `_bootstrap_trusted_src_path()` L99-114, called at L204 before `_import_trusted_claude_code_cli` at L205 | MET — the `try/except ImportError` fallback that caused the defect is gone entirely; there is no `import tomtit` anywhere else in the file |
| 3 | Does not trust cwd / `PYTHONPATH` / user site / installed dists / `.pth` / ambient ordering | L112-113 strips every existing occurrence of the resolved path, then `insert(0, ...)` | MET — insertion is unconditional, not a fallback, so no ambient entry can pre-empt it |
| 4 | Verifies imported module's resolved `__file__` is under the trusted tree | `_import_trusted_claude_code_cli` L170-180 (`resolved_module_path.relative_to(resolved_trusted_root)`) | MET, with the caveat in N-1 |
| 5 | Fails closed on missing `__file__`, wrong provenance, failed import, poisoned `sys.modules` | L172-173, L176-179, and the `except BaseException` at L211-222 returning always-raising stand-ins | MET |
| 6 | Ambient validator/fingerprint cannot influence authorization | Stand-ins raise `_TrustedImportUnavailableError`, which *is* what `AuthorizedCommandError` is bound to in the failure case, so the unmodified `except AuthorizedCommandError` at L308 denies | MET |

The design choice at L211 to catch `BaseException` at *module load* is correct
and load-bearing: a standalone script whose top-level code raises produces
Python's default traceback and exit code 1, which under Claude Code's PreToolUse
contract is non-blocking — i.e. the H-1 fix would otherwise have created exactly
the H-2 defect. The two fixes compose correctly (verified empirically in §2.2,
rows H1-E / H2-1).

### 2.2 Independent reproduction

Fixtures authored for this review (malicious ambient package = always-allow
validator + constant fingerprint `"a"*64`; fingerprints file seeded with that
same constant, so an ambient win produces PASS). Unauthorized command:
`rm -rf /tmp/SYNTHETIC_TARGET`. Authorized command: `echo tomtit-synthetic-ok`.
No real `claude` CLI was invoked at any point.

| ID | Vector | Fixture validity confirmed | Observed | Expected | Result |
|---|---|---|---|---|---|
| H1-A | Malicious `tomtit` on `PYTHONPATH` | yes — bare `python3 -c "import tomtit.adapters.claude_code_cli"` resolved to the malicious file and returned the constant fingerprint | exit 2, fixed message | EXIT_2_DENY | PASS |
| H1-A2 | Same, fingerprints file containing *both* the ambient constant and the real fingerprint | yes | exit 2 | EXIT_2_DENY | PASS |
| H1-B | Real `PYTHONUSERBASE`-backed user site (`site.getusersitepackages()`, not `PYTHONPATH`) | yes — plain import resolved to the user-site copy | exit 2 | EXIT_2_DENY | PASS |
| H1-C | `sitecustomize.py` pre-populating `sys.modules` with a fake module (`__file__` outside trusted tree) | yes — `'tomtit.adapters.claude_code_cli' in sys.modules` → `True` at startup | exit 2 | EXIT_2_DENY | PASS |
| H1-C2 | Same, but fake module's `__file__` **spoofed** to the real trusted path | yes | **exit 0** | EXIT_2_DENY | see N-1 (non-blocking) |
| H1-D | Target `cwd` containing its own fake `tomtit/` package | yes | exit 2 | EXIT_2_DENY | PASS |
| H1-E | Broken trusted import (real hook source in a tree whose `claude_code_cli.py` raises at import) | yes | exit 2, no traceback | EXIT_2_DENY | PASS |
| H1-F | Legitimate exact authorized command, clean env, **real** trusted module | — | exit 0, stdout empty, stderr empty | EXIT_0_SILENT_DEFER | PASS |
| H1-G | Legitimate exact authorized command **with the malicious ambient package present** | — | exit 0, silent | EXIT_0_SILENT_DEFER | PASS |

H1-G is the decisive positive control: a PASS is only reachable if the *real*
`compute_authorized_command_fingerprint` ran, since the ambient one returns a
constant that is not in that fingerprints file. Combined with H1-A2 (ambient
always-allow validator cannot produce a PASS even when its constant *is* in the
set), the ambient implementation is proven to have zero influence on the
decision in both directions.

Additional argv-guard spot checks against the same authorized fingerprint:
`timeout 5 echo tomtit-synthetic-ok` → exit 2; `run_in_background: true` → exit 2;
whitespace-only variation `echo  tomtit-synthetic-ok ` → exit 0 (**correct**:
`shlex.split` yields the identical argv, and argv identity — not byte identity
of the command string — is the documented contract).

## 3. H-2 — unexpected failures fail closed: RESOLVED

### 3.1 Source review

`main()` (L359-391) is structurally bounded to `{0, 2}`:

- `_PASS_EXIT_CODE` is returned in exactly one place (L379), reachable only when
  `_run()` returns truthy without raising.
- Every other path — clean deny, any exception in `_run()`'s call graph
  (`BaseException`, L380), and a failure while writing the denial message itself
  (L387) — falls through to `return _DENY_EXIT_CODE` at L391.
- `raise SystemExit(main())` at L395 is the only exit.
- Module load cannot raise: the only fallible module-level work is inside
  `_load_trusted_claude_code_cli()`'s `except BaseException` (L211).
- The sole write to any stream anywhere in the file is
  `print(DENIAL_MESSAGE, file=sys.stderr)` (L386). There is no code path that
  writes a traceback, the raw command, the stdin payload, a fingerprint value,
  or the ambient module path. `_TrustedImportUnavailableError`'s messages are
  fixed static strings and are never printed.

Catching `BaseException` rather than `Exception` is correct here for the stated
reason: uncaught `KeyboardInterrupt` (130) and `SystemExit(1)` are just as
non-blocking under the PreToolUse contract as the exit-1 defect being closed.
Both broad handlers are confined to this one module and explicitly commented.

### 3.2 Independent fault injection

25 conditions injected, each as a real subprocess. Checks applied to every run:
exit code, absence of `Traceback (most recent call last)`, absence of the
command text, absence of a synthetic secret, absence of the fingerprint value,
and exact stderr equality with `DENIAL_MESSAGE` (stdout empty).

| Injected failure | Exit | Traceback | Leak | stderr == DENIAL_MESSAGE |
|---|---|---|---|---|
| Trusted import raises at import time | 2 | no | none | yes |
| Provenance mismatch (`sys.modules` poison) | 2 | no | none | yes |
| Empty stdin (EOF) | 2 | no | none | yes |
| stdin fd closed (`read()` raises) | 2 | no | none | yes |
| Malformed JSON **with the secret and command embedded in the failing input itself** | 2 | no | none | yes |
| `evaluate()` raises `RuntimeError` | 2 | no | none | yes |
| Trusted validator raises bare `RuntimeError` (not `AuthorizedCommandError`) | 2 | no | none | yes |
| Trusted fingerprint function raises `RuntimeError` | 2 | no | none | yes |
| Missing fingerprints file | 2 | no | none | yes |
| Malformed fingerprints file (invalid JSON) | 2 | no | none | yes |
| Fingerprints file wrong shape (`{"a":1}`) | 2 | no | none | yes |
| Fingerprints env var unset/empty | 2 | no | none | yes |
| Fingerprints path is a directory | 2 | no | none | yes |
| `SystemExit(0)` raised inside `_run()` | 2 | no | none | yes |
| `SystemExit(1)` raised inside `_run()` | 2 | no | none | yes |
| `KeyboardInterrupt` inside `_run()` | 2 | no | none | yes |
| Bare `BaseException` inside `_run()` | 2 | no | none | yes |
| `MemoryError` inside `_run()` | 2 | no | none | yes |
| Trusted module missing a required attribute | 2 | no | none | yes |

Non-regression, same harness:

| Case | Exit | stdout | stderr |
|---|---|---|---|
| Exact approved command (real trusted module) | **0** | empty | empty |
| Unauthorized command | 2 | empty | `DENIAL_MESSAGE` only |
| Wrong `tool_name` (`Write`) | 2 | empty | `DENIAL_MESSAGE` only |
| `null` payload | 2 | empty | `DENIAL_MESSAGE` only |
| `[]` payload | 2 | empty | `DENIAL_MESSAGE` only |

```
DISTINCT EXIT CODES OBSERVED ACROSS ALL 25 RUNS: [0, 2]
EXIT CODE 1 OBSERVED: False
TRACEBACK OBSERVED: never
COMMAND / SECRET / FINGERPRINT LEAK: never
```

The malformed-JSON case is the strongest leak check: the synthetic secret and
the command text were embedded *inside the invalid JSON blob being parsed*, so
it covers "the failing input itself was sensitive," not merely "the failure
happened while processing a clean input."

### 3.3 H-1 / H-2 interaction (explicitly required)

Verified directly: trusted-import failure (H1-E) and provenance mismatch (H1-C,
H2-2) both terminate at **exit 2 with the fixed message and no traceback**. The
H-1 fix does not create an import-time path outside the H-2 fail-closed
boundary, because the entire bootstrap/import/provenance sequence lives inside
`_load_trusted_claude_code_cli()`'s own `except BaseException`, and its failure
mode is a *value* (always-raising stand-ins) rather than an exception, which the
already-reviewed `except AuthorizedCommandError` path then handles unchanged.

### 3.4 Test-suite quality (not the basis of approval)

Reviewed to confirm the implementer's tests do not merely monkeypatch the
desired result: of the 25 new H-1/H-2 tests, the H-1 set is entirely
subprocess-based against real fixture trees, and the H-2 set is subprocess-based
except where in-process injection is the only faithful mechanism (`stdin.read()`
raising, `evaluate()` itself raising — both are functions the subprocess
boundary cannot reach). This is sound. **Approval nevertheless rests on §2.2 and
§3.2, which were authored independently of that suite.**

## 4. Non-blocking findings

### N-1 — Provenance check accepts a spoofed `__file__` (LOW, not an incremental exposure)

The provenance check reads `getattr(module, "__file__", None)` off the module
object itself. An attacker who can execute code in the hook interpreter *before*
the script runs can pre-populate `sys.modules` with a fake module whose
`__file__` attribute is a plain string set to the real trusted path. The check
then passes and the ambient always-allow validator wins. Reproduced as H1-C2:
**exit 0** on `rm -rf /tmp/SYNTHETIC_TARGET`.

**Why this is non-blocking:** the capability required is arbitrary
pre-execution code in the interpreter (`sitecustomize.py`, `usercustomize.py`,
or an executable `.pth` line). That capability already defeats the guard
completely and independently of `sys.modules`. Demonstrated:

```
sitecustomize.py containing `import os; os._exit(0)`
→ hook exits 0 on `rm -rf /`, without any sys.modules involvement
   (control run, same input, no sitecustomize → exit 2)
```

No in-process check can survive an attacker who controls interpreter startup, so
N-1 grants nothing an attacker at that privilege level does not already have.
Every vector that does **not** require pre-execution code — `PYTHONPATH`, real
user site, installed distribution, target-repo `cwd`, plain `sys.modules`
poisoning with an honest `__file__` — is correctly blocked (§2.2).

**However, the claim as written is overstated.** The module docstring (L40-48)
and fix report §3 state that the provenance check "closes that residual gap" and
"catches a `sys.modules`-cache-poisoning attack exactly like an ordinary
`sys.path`-ordering attack." It closes the honest-`__file__` variant only.
Recommended (either, not both):

1. Purge `tomtit`-prefixed keys from `sys.modules` before
   `importlib.import_module` — closes the spoofed-`__file__` variant outright;
   or
2. Narrow the documented claim to what is actually true, and record
   interpreter-startup code execution as an explicit out-of-scope trust
   assumption.

Additionally recommended as the *correct* layer for this class: harden the hook
invocation itself in `_write_exact_bash_guard_artifacts`
(`claude_code_cli.py` L576), which currently builds
`{sys.executable} {module_path}`. Adding `-E -s` (ignore `PYTHONPATH`/
`PYTHONHOME`, disable user site) — and `-S` if the trusted import proves not to
need `site` — moves the defense to the process boundary, where it actually
holds. This is a hardening suggestion for a later task, not a required change.

### N-2 — Guard-file corruption is fail-open (LOW, out-of-band)

If `exact_bash_guard.py` itself is truncated or syntactically corrupted, Python
cannot compile it and exits **1** — non-blocking under the PreToolUse contract.
No construct inside the file can prevent this. (A *missing* file is safe:
Python exits 2.) Guard-file integrity is an out-of-band assumption; worth
recording explicitly before any real probe. Not attributable to this patch.

### N-3 — `evaluate()` / `_command_fingerprint_is_approved()` "byte-for-byte unchanged" could not be diffed

`exact_bash_guard.py` is untracked (`??`), so no pre-patch revision exists in
git to diff against. The report's §9 claim was therefore verified
*behaviorally*, not textually: wrapper-variant deny, `run_in_background` deny,
PASS-never-grants, fingerprint-only artifacts, and exact-argv semantics were all
re-confirmed empirically (§2.2, §3.2) and by reading L282-338. No regression was
found. Recording the limitation for honesty, not as a defect.

## 5. Regression confirmation

| Property | Status | Evidence |
|---|---|---|
| `evaluate()` semantics | unchanged | L313-338 read; behaviorally re-verified (wrong tool_name, non-dict payload, `null`, `[]`, `run_in_background`, empty command all deny) |
| `_command_fingerprint_is_approved()` | unchanged | L282-310 read; shared canonical validator + SHA-256 contract, no second validator |
| Canonical validator semantics | unchanged | `claude_code_cli.py` L254-350 not modified by this patch |
| Command fingerprint algorithm | unchanged | `claude_code_cli.py` L458-468; independently recomputed in my fixtures and matched |
| Artifact/settings creation | unchanged | `_write_exact_bash_guard_artifacts` L554-593: fingerprints only, never raw argv; dir `0o700`, files `0o600` |
| Native `--allowedTools` layering | unchanged | L1717-1720; guard is in front of, never in place of, Claude's engine |
| `run_in_background` rejection | unchanged | `exact_bash_guard.py` L333-334; verified exit 2 |
| PASS never grants permission | holds | exit 0 with **empty stdout** — no `permissionDecision: allow` JSON is ever emitted (only stderr is ever written, and only on deny) |
| Enable constant | **`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT = False`** | `claude_code_cli.py:365` |

## 6. Deferred findings — still OPEN

Re-confirmed in source; **not** resolved, **not** silently closed:

- **M-1 (OPEN_MEDIUM):** `_redact_command_values` (`claude_code_cli.py`
  L627-639) redacts only a dict key literally equal to `"command"`. Raw Bash
  text can still reach persisted evidence via `result`, `error`, `message`,
  `reason`, `hookSpecificOutput.permissionDecisionReason`, nested
  `content: [{"type":"text","text": ...}]`, or alternate key spellings
  (`cmd`, `Command`).
- **M-2 (OPEN_MEDIUM):** `_StreamReader` (L1100-1128) truncates at
  `max_stdout_bytes` (default 65536) *before* redaction; truncated JSON is
  unparseable, so `_redact_provider_json_bash_commands` (L642-653) hits its
  `except ValueError: return text` path and returns the payload unchanged,
  preserving any `"command": "<raw>"` in the surviving prefix.

Neither blocks an isolated probe using synthetic command text only. Both must be
closed before the capability is enabled for any real user workload.

## 7. Verification commands — exact exit codes

```
python3 .tomtit/dogfood/test_run_loop.py                            -> EXIT=0   (52 tests)
python3 .tomtit/dogfood/test_run_repair.py                          -> EXIT=0   (51 tests)
python3 .tomtit/dogfood/test_run_review.py                          -> EXIT=0   (45 tests)
python3 -m pytest tests/adapters/test_exact_bash_guard.py -q        -> EXIT=0   (70 tests)
python3 -m pytest tests/adapters -q                                 -> EXIT=0   (326 tests)
python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q  -> EXIT=0   (4 tests)
python3 -m pytest -q                                                -> EXIT=0   (652 tests)
python3 -m compileall -q src tests scripts .tomtit/dogfood          -> EXIT=0
ruff check src tests scripts                                        -> EXIT=0
python3 -m mypy src                                                 -> EXIT=0   ("Success: no issues found in 14 source files")
git diff --check                                                    -> EXIT=0
```

All test counts match those recorded in the fix report §11 exactly.

**Flake note:** the process-cleanup timing flake the fix report recorded
(`test_delayed_descendant_write_is_prevented_before_final_snapshot`) **did not
reproduce** in this review. `tests/adapters` and the full suite each passed
clean on the first attempt; no rerun was needed and no result was discarded. One
`.tomtit/dogfood` invocation was interrupted by my own 2-minute tool timeout
(not a test failure) and was rerun to completion with `EXIT=0` — the original
interruption is recorded here rather than erased.

## 8. Constraint compliance

- No implementation file was modified. All fixtures were written to a temporary
  directory outside the repository; `git status --short` is byte-identical to
  the session-start snapshot (verified after every probe).
- No commit, stage, reset, stash, checkout, push, merge, or deploy.
- TOMTIT was not run against itself.
- No real Claude or Codex child model was invoked.
- The isolated real hook probe was **not** run.
- The enable constant was not flipped.
- No prior report was modified.

## 9. Recommended next action

Owner decision on whether to authorize an **isolated real hook probe using
synthetic commands only**, with the environment controlled (clean env, no
`sitecustomize`/`usercustomize`/`.pth` in the probe interpreter — see N-1) and
guard-file integrity confirmed beforehand (N-2). N-1's documentation correction
and the `-E -s` invocation hardening should be scheduled as a separate follow-up
task; neither gates the probe. M-1/M-2 remain required before any real workload.

---

VERDICT=APPROVE_WITH_NON_BLOCKING_FINDINGS
H1_IMPORT_TRUST_BOUNDARY=RESOLVED
H2_UNEXPECTED_FAILURE_FAIL_CLOSED=RESOLVED
AMBIENT_PACKAGE_HIJACK=BLOCKED
SYS_MODULES_POISONING=POSSIBLE
SYS_MODULES_POISONING_NOTE=only via spoofed __file__ requiring pre-execution interpreter code, which independently defeats any in-process guard (N-1, non-blocking); the honest-__file__ variant is BLOCKED
HOOK_EXIT_CODES_ONLY_0_OR_2=PASS
TRACEBACK_ON_HOOK_FAILURE=NO
EXACT_ARGV_GUARD=PASS
PASS_AUTO_GRANTS_PERMISSION=NO
M1_PROVIDER_OUTPUT_REDACTION=OPEN
M2_TRUNCATION_BEFORE_REDACTION=OPEN
EXACT_AUTHORIZATION_OPERATIONALLY_PROVEN=NO
ISOLATED_REAL_HOOK_PROBE_READY=YES
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_CHILD_INVOCATIONS=0
NEXT_ACTION=OWNER_DECISION_ON_ISOLATED_REAL_HOOK_PROBE
