# Exact Bash Guard: Process-Boundary Hardening — Narrow Security Review

**Reviewer:** Claude Code Opus session, Owner-coordinated.
**HEAD at review:** `f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d` (unchanged; no git write action performed).
**Scope:** only (1) Python hook invocation hardening `<sys.executable> -E -s <guard>` and (2) guard-file preflight integrity. M-1/M-2 not in scope.
**Method:** source inspection plus independently authored probes that drive the **actual rendered hook command** and the **actual `_write_exact_bash_guard_artifacts` code path**, not hand-built approximations. The hardening report was read for claims but not treated as evidence.

## 1. Verdict

**APPROVE.**

Both changes do what they claim, at the layer they claim, with no regression to
any previously approved property. The un-hardened baseline was re-run alongside
each hardened case, so every "blocked" result is proven to be the flags' doing
rather than a fixture that never worked. Two non-blocking observations (O-1,
O-2) are recorded; neither is a defect and neither gates the probe.

The report's own scope statements are accurate — including, notably, the parts
that decline to claim a fix. `-S` is correctly not used and correctly labelled
out of scope, and content integrity is correctly **not** claimed as fixed.

## 2. Review area 1 — Python process isolation: PASS

### 2.1 Generated hook command

Obtained by invoking the real `_write_exact_bash_guard_artifacts` and reading
back the `settings.json` it wrote:

```
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -E -s \
  /Users/tranvanbac/Documents/AI/ai-agent/TOMTIT-Coordinator/src/tomtit/adapters/exact_bash_guard.py
```

| Required property | Result |
|---|---|
| Exact absolute `sys.executable` | PASS — `argv[0] == sys.executable`, absolute, contains a separator (never a bare `python3`) |
| Includes `-E` | PASS |
| Includes `-s` | PASS |
| Flags precede the hook path | PASS — argv is exactly the 4-element `[sys.executable, "-E", "-s", <guard path>]` |
| Hook path absolute and resolved | PASS — absolute, and `path.resolve() == path` |
| No shell wrapper | PASS — `argv[0]` is the interpreter itself; no `sh`/`bash`/`zsh`/`env`/`command`/`xargs`/`timeout`/`nice` |
| No `shell=True` | PASS — AST scan finds no `shell=True` keyword anywhere in the adapter; the only textual occurrence is prose in a docstring (`claude_code_cli.py:638`). `subprocess.Popen` (L1417) is called with `list(argv)` and no `shell` argument |
| No PATH-based Python discovery | PASS — `sys.executable` is TOMTIT's own running interpreter |
| No `-c` | PASS — no `-c`, no inline program text |
| No environment-dependent executable lookup | PASS |

`-S` is absent, matching the report's stated scope. Quoting was probed with
adversarial path values (`/a b/`, `/a;rm -rf ~/`, `/a'$(id)/`) and
`shlex.quote` neutralized each correctly.

### 2.2 Attack vectors — hardened vs. un-hardened baseline

Fixtures authored for this review: malicious `tomtit` package (always-allow
validator, constant fingerprint `"a"*64`) with the fingerprints file seeded with
that same constant, so an ambient win yields **exit 0 (PASS)**. Unauthorized
command: `rm -rf /tmp/SYNTHETIC_TARGET`. No real `claude` CLI was invoked.

| Vector | Un-hardened baseline | Hardened `-E -s` | Result |
|---|---|---|---|
| Malicious `tomtit` on `PYTHONPATH` | (already exit 2 — in-process H-1 check) | exit 2 | BLOCKED |
| Same, with ambient + real fingerprint both in the set | — | exit 2 | BLOCKED |
| Malicious `tomtit` in `PYTHONUSERBASE` user site | (already exit 2) | exit 2 | BLOCKED |
| **Spoofed-`__file__` `sitecustomize` via `PYTHONPATH`** (the N-1 / H1-C2 finding) | **exit 0** | **exit 2** | **CLOSED** |
| **Spoofed-`__file__` `usercustomize` via user site** | **exit 0** | **exit 2** | **CLOSED** |
| **Executable `.pth` line via user site** | **exit 0** | **exit 2** | **CLOSED** |

The three bolded rows are the substantive result: each produced a live **exit 0
PASS on `rm -rf /tmp/SYNTHETIC_TARGET`** against the un-hardened invocation, and
exit 2 with `-E -s`. I added the `usercustomize` and `.pth` variants beyond the
task's list because they are the other two unprivileged code-execution-at-startup
delivery mechanisms; both were exploitable before and are closed now.

### 2.3 Mechanism assertions (not merely outcome)

```
hardened -E -s : AMBIENT_ON_PATH=False  USER_SITE=False
unhardened     : AMBIENT_ON_PATH=True   USER_SITE=True
```

`-E` genuinely removes `PYTHONPATH` from the child's `sys.path`, and `-s`
genuinely sets `site.ENABLE_USER_SITE = False`. The denials above are therefore
attributable to the flags, not to a fixture that failed to load.

### 2.4 Remaining assumption — documented, not a blocker

`-E -s` does **not** neutralize a `sitecustomize.py` installed into the
interpreter's **system** site-packages directory. That requires privileged write
access to the Python installation itself, which is strictly outside the
unprivileged threat boundary that a target repository operates within.

Per the review instruction, this is not marked a blocker: **this patch
introduces no new capability for an unprivileged target repository to achieve
it.** The patch strictly reduces the attack surface — every vector it changes
moves from POSSIBLE to BLOCKED, and none moves the other way. The assumption is
recorded in the code itself (`claude_code_cli.py:566-571`), not only in the
report, which is the right place for it.

## 3. Review area 2 — guard-file preflight: PASS

`_verified_exact_bash_guard_module_path()` (`claude_code_cli.py:587-632`),
called as the **first statement** of `_write_exact_bash_guard_artifacts`
(L677), before `tempfile.mkdtemp`, before any file write, and before
`subprocess.Popen`.

| Required property | Result |
|---|---|
| `os.lstat`, not a symlink-following check | PASS — `os.lstat` at L616; no `Path.exists()`/`is_file()` on this path |
| Symlink rejected | PASS — verified with a symlink to the **real** guard file *and* to a malicious always-`exit(0)` guard; both DENIED |
| Directory rejected | PASS |
| FIFO / device / non-regular rejected | PASS — FIFO and character device (`/dev/null`) both DENIED via `stat.S_ISREG` |
| Missing / unreadable fails closed | PASS — missing path and unreadable parent (`/root/...`) both DENIED |
| Expected path derived from TOMTIT-owned adapter location | PASS — `Path(__file__).resolve().parent / "exact_bash_guard.py"` (L536); no env var, no cwd, no caller input |
| Parent-path resolution mismatch rejected | PASS — guard reached via a symlinked parent directory DENIED by the `.resolve()` comparison |
| Runs before artifact creation and before Claude starts | PASS — first statement; verified empirically that a failing preflight leaves **no** `tomtit-exact-bash-guard-*` temp directory behind |
| Enters existing `EXACT_BASH_GUARD_UNAVAILABLE` path | PASS — `_ExactBashGuardIntegrityError` subclasses `OSError`; `run()`'s pre-existing `except OSError` (L1665) routes it unchanged. No already-reviewed control flow was edited |
| Error messages carry nothing environment-derived | PASS — all three messages are static literals; `from None` suppresses chaining; verified no path, errno, or `strerror` text in any raised message |
| Valid regular guard file still passes | PASS — positive control ALLOWED |

### 3.1 SUBSTITUTION_INTEGRITY vs CONTENT_INTEGRITY

The review asked these be explicitly distinguished. They are, correctly, in both
the code docstring and the report:

- **SUBSTITUTION_INTEGRITY — SOLVED by this patch.** The path handed to Claude
  as a PreToolUse hook is proven to still be a regular file at the exact
  TOMTIT-owned location this module was loaded from. Symlink, directory, FIFO,
  device, missing, and symlinked-parent substitutions are all rejected *before
  Claude is started*, rather than being discovered only when the hook runs.
- **CONTENT_INTEGRITY — NOT solved, and correctly not claimed.** Verified
  directly: a syntactically corrupted but still-regular guard file **passes**
  the preflight (ALLOWED), and at runtime Python exits **1** — which is
  non-blocking under the PreToolUse contract, i.e. fail-open. This is exactly
  the H-1/H-2 re-review's N-2, and it **remains open** as an out-of-band
  assumption. The report states this precisely in §3 and does not overclaim.

This distinction is stated accurately throughout the patch. No false "fixed"
claim was found.

## 4. Non-blocking observations

**O-1 — the `.resolve()` equality check is a TOCTOU re-check, not a path
normalizer.** Because `_EXACT_BASH_GUARD_MODULE_PATH` is built from
`Path(__file__).resolve()` and `.resolve()` is idempotent (verified),
`expected_path.resolve() == expected_path` holds by construction at import time.
The check can therefore only fire if a parent component *became* a symlink
between module import and preflight — which is precisely its stated purpose
("belt-and-braces"). Worth recording so a future maintainer does not mistake it
for general path normalization and "simplify" it away; there is no availability
risk of it spuriously rejecting a legitimate deployment.

*(During review this initially presented as two failing controls; both were my
own fixture artifact — macOS `/var` is a symlink to `/private/var`, so
monkeypatched temp paths were not self-resolving. Re-run against fully resolved
paths, both controls passed.)*

**O-2 — the hook command is a string executed by Claude Code, so a shell is
involved on the far side.** This is inherent to the PreToolUse `type: "command"`
contract and is not something this patch can change. It is safe here because
every element is individually `shlex.quote`d, and both elements (`sys.executable`
and the preflighted guard path) are TOMTIT-owned rather than target-derived — so
there is no injection surface from TOMTIT's side. The report's "no shell
wrapper" claim is about the command TOMTIT constructs, which is accurate.

## 5. Regression review — no regressions found

`src/tomtit/adapters/exact_bash_guard.py` is byte-identical to the file I
reviewed in the H-1/H-2 re-review (SHA-256 `b35053af5660…b448e15`, 395 lines),
confirming the guard itself was not touched. Re-verified behaviorally **through
the hardened command**, not just by hash:

| Property | Status | Evidence |
|---|---|---|
| `exact_bash_guard.py` behavior | PRESERVED | byte-identical; all 70 of its tests pass unmodified |
| H-1 trusted import boundary | PRESERVED | ambient `PYTHONPATH`/user-site packages still denied; `-E -s` sits in front of the in-process check, never replaces it |
| H-2 exit-0/exit-2 boundary | PRESERVED | malformed JSON through the hardened command → exit 2, no traceback; only 0 and 2 observed |
| Exact argv fingerprint semantics | PRESERVED | wrapper variant `timeout 5 echo …` → exit 2 |
| Canonical validator | PRESERVED | `validate_authorized_command_argv` untouched |
| PASS never grants permission | PRESERVED | legitimate exact command → exit 0 with **empty stdout and empty stderr**, including with the malicious ambient package present |
| Native `--allowedTools` layering | PRESERVED | `_build_argv` untouched; `--settings` added alongside, never instead of |
| Fingerprint-only artifacts | PRESERVED | fingerprints file contains only the SHA-256; raw command text absent from `settings.json` |
| `run_in_background` rejection | PRESERVED | → exit 2 |
| No-command behavior | PRESERVED | preflight is inside `_write_exact_bash_guard_artifacts`, reached only when `request.authorized_commands` is non-empty |
| Evidence schema | PRESERVED | no schema field added or changed; new failure reuses the existing `EXACT_BASH_GUARD_UNAVAILABLE` category |
| Enable constant | **`False`** | `claude_code_cli.py:366`; `resolve_claude_cli_authorization_contract()` returns `UNSUPPORTED` |

**Dormancy note:** with the enable constant `False`, `run()` returns
`UNSUPPORTED_EXACT_COMMAND_AUTHORIZATION` *before* reaching
`_write_exact_bash_guard_artifacts`. Both hardening changes are therefore
themselves dormant in the shipped configuration — they take effect only once the
constant is flipped under separate Owner authorization. My verification drove
them directly rather than through `run()`, so the review covers behavior the
current configuration does not yet exercise.

## 6. Deferred findings — still OPEN

- **M-1 (OPEN_MEDIUM):** redaction still matches only a dict key literally equal
  to `"command"` (`claude_code_cli.py:743`). Unchanged by this patch.
- **M-2 (OPEN_MEDIUM):** `_redact_provider_json_bash_commands` still returns
  non-JSON text unchanged (L763-764), so truncation-before-redaction still
  bypasses it entirely. Unchanged by this patch.

Both still block enablement for real user workloads; neither blocks an isolated
probe using synthetic command text only.

## 7. Verification commands — exact exit codes

```
python3 .tomtit/dogfood/test_run_loop.py                            -> EXIT=0   (52 tests)
python3 .tomtit/dogfood/test_run_repair.py                          -> EXIT=0   (51 tests)
python3 .tomtit/dogfood/test_run_review.py                          -> EXIT=0   (45 tests)
python3 -m pytest tests/adapters/test_exact_bash_guard.py -q        -> EXIT=0   (70 tests, unmodified)
python3 -m pytest tests/adapters -q                                 -> EXIT=0   (351 tests; was 326, +25)
python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q  -> EXIT=0   (4 tests)
python3 -m pytest -q                                                -> EXIT=0   (677 tests; was 652, +25)
python3 -m compileall -q src tests scripts .tomtit/dogfood          -> EXIT=0
ruff check src tests scripts                                        -> EXIT=0
python3 -m mypy src                                                 -> EXIT=0   ("Success: no issues found in 14 source files")
git diff --check                                                    -> EXIT=0
```

All counts match the hardening report exactly, including the +25 delta.

**Flake note:** the report recorded a first-run full-suite failure in
`test_normal_exit_with_surviving_descendant_kills_descendant` (the pre-existing
F-1 process-cleanup timing family). It **did not reproduce here** — the full
suite passed clean on its first attempt at 677, as did `tests/adapters` on its
first attempt. Nothing was rerun and no result was discarded. I concur with the
report's attribution: that test runs with `authorized_command_count=0`, so it
exercises no code path this patch touches.

## 8. Test-suite quality (not the basis of approval)

The 25 new tests were reviewed to confirm they are not vacuous. Tests 8-10 split
and execute the **actual output of `_render_exact_bash_guard_hook_command`**
(`test_claude_code_cli.py:3048`) rather than a hand-built argv, so they exercise
the real registered invocation; 8b/9b/9c assert the underlying mechanism
(`sys.path` contents, `site.ENABLE_USER_SITE`) with fixture validity confirmed
before the deny assertion. `test_hardening_7` uses an AST scan rather than a
substring grep — which is the correct choice, since the literal `shell=True`
does appear in a docstring and a grep-based assertion would have been wrong in
both directions. Approval nevertheless rests on §2 and §3, authored
independently of this suite.

## 9. Constraint compliance

- No implementation file modified; no test file modified. All fixtures written
  to temporary directories outside the repository. `git status --short` is
  identical to the session-start snapshot apart from this new report.
- No git write action of any kind. `HEAD` unchanged.
- TOMTIT was not run against itself. No real Claude or Codex child model
  invoked. The isolated real hook probe was not run. The enable constant was not
  flipped. No prior report was modified.

## 10. Recommended next action

Owner decision on authorizing the **isolated real hook probe with synthetic
commands only**. This patch materially improves the probe's preconditions: the
H-1/H-2 re-review's §9 recommended manually controlling the probe interpreter's
`sitecustomize`/`usercustomize`/`.pth` state, and `-E -s` now enforces that
unprivileged subset automatically. Two out-of-band items remain worth confirming
before the probe, neither blocking: system-site-packages `sitecustomize`
absence (§2.4) and guard-file content integrity (§3.1 / N-2).

---

VERDICT=APPROVE
PYTHON_PROCESS_ISOLATION=PASS
PYTHON_E_FLAG=PASS
PYTHON_s_FLAG=PASS
USER_SITE_DISABLED=PASS
UNPRIVILEGED_SITECUSTOMIZE_VECTOR=BLOCKED
GUARD_FILE_PREFLIGHT=PASS
SYMLINK_SUBSTITUTION=BLOCKED
NON_REGULAR_FILE_SUBSTITUTION=BLOCKED
H1_RESOLUTION=PRESERVED
H2_RESOLUTION=PRESERVED
EXACT_ARGV_GUARD=PASS
M1_PROVIDER_OUTPUT_REDACTION=OPEN
M2_TRUNCATION_BEFORE_REDACTION=OPEN
EXACT_AUTHORIZATION_OPERATIONALLY_PROVEN=NO
ISOLATED_REAL_HOOK_PROBE_READY=YES
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_CHILD_INVOCATIONS=0
NEXT_ACTION=OWNER_DECISION_ON_ISOLATED_REAL_HOOK_PROBE

Note on scope of this APPROVE: it authorizes only an Owner-run isolated real
hook probe using synthetic commands. It does not authorize flipping
`_CLAUDE_CLI_BARE_BASH_RULE_CONFIRMED_EXACT`, closing or ignoring M-1/M-2,
rerunning personal-web, or any real user workload.
