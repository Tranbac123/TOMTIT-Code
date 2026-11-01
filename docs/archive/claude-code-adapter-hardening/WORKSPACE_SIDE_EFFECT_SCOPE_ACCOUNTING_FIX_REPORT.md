# Workspace Side-Effect Scope Accounting Fix Report

## 1. Status

Implemented, pending review. TOMTIT now distinguishes three classes of
observed filesystem mutation — source change, explicitly Owner-authorized
generated side effect, and unknown — and only the third causes a scope
violation. This is a **second, independent Owner contract**, not a relaxation
of the existing one, and it is explicitly not derived from `.gitignore`.

## 2. Starting HEAD

```
git rev-parse HEAD -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d
```

Unchanged; no commits made.

## 3. Real dogfood failure

Run `personal-web-content-exactauth-rerun-002`. The Claude CLI and
exact-Bash blockers were successfully crossed and the worker actually
implemented the task, but the run was rejected:

```
changed_count    = 2726
unexpected_count = 2709
  .next        =  192
  node_modules = 2517
2726 - 2709     =   17   <- the real Git-visible product change set
```

All 17 real changes (7 modified tracked files + 10 new non-ignored files)
were inside the Owner-approved source allowlist. The 2709 rejected paths were
entirely the generated output of the Owner-approved `npm install` and
`npm run build`.

## 4. Root cause

`ClaudeCodeCLIAdapter.run()` performed a two-way split:

```python
allowed = set(request.allowed_changed_paths)
unexpected_changed_paths = tuple(p for p in changed_paths if p not in allowed)
```

Any observed mutation not literally in the exact source allowlist was
"unexpected". There was no vocabulary for "this file was generated, and the
Owner expected that". So `node_modules/**` and `.next/**` — the necessary
byproducts of commands TOMTIT itself had authorized — were classified as
unauthorized source changes.

The defect is one of *accounting*, not of enforcement: TOMTIT was observing
the workspace correctly and failing closed correctly; it simply had no way to
express that a mutation was an approved side effect.

## 5. Why blanket gitignore filtering is unsafe

The tempting one-line fix — skip anything `.gitignore` covers — was rejected.
`.gitignore` is a *version-control convenience*, not a security boundary.
Ignored files routinely include `.env`, `.env.local`, credentials, private
configuration, local secrets, and build caches that may embed them. Treating
"git ignores it" as "TOMTIT may write it" would silently widen the trust
boundary to exactly the files most worth protecting, and would do so
invisibly, with no Owner decision recorded anywhere.

It would also make the boundary depend on a file inside the workspace that
the worker itself can edit — a worker could append `*` to `.gitignore` and
launder arbitrary writes.

Tests 6/7 pin this: `.env`, `.env.local`, `secret.txt`, `credentials.json`,
`dist/bundle.js`, and `coverage/lcov.info` all remain **unexpected** even
while `node_modules` and `.next` are authorized.

## 6. Source-change vs side-effect contract

Two independent contracts, neither implying the other:

| Contract | Grants | Does NOT grant |
|---|---|---|
| `allowed_changed_paths` (existing) | exact source paths the worker may change | any generated output |
| `allowed_side_effect_paths` (new) | roots beneath which generated mutations are expected | **any Bash execution** |
| `authorized_commands` (existing) | exact argv the worker may run | **any filesystem scope** |

Approving `npm run build` grants no filesystem scope; approving `.next`
grants no command execution. Both gates must independently pass. Two tests
pin each direction:
`test_side_effect_authorization_does_not_grant_bash_execution` and
`test_side_effect_bash_authorization_does_not_grant_filesystem_scope` (the
latter shows an authorized `npm run build` whose `.next/out.json` output is
still a `SCOPE_VIOLATION` without separate side-effect approval).

CLI (all three dogfood runners): `--allowed-side-effect-path PATH`, repeated.
Internal name: `allowed_side_effect_paths`, an immutable canonical
`tuple[str, ...]` matching existing TaskContract style. Nothing is hardcoded
— `node_modules` and `.next` appear only in tests and this report.

## 7. Path validation / containment

`normalize_allowed_side_effect_path()` is fail-closed and rejects, with the
stable category `INVALID_SIDE_EFFECT_PATH`:

absolute paths (`/abs`), empty values, the workspace root (`.`, `./`, `/`),
`..` traversal at any position (`..`, `../x`, `a/../..`,
`node_modules/../.git`), backslash aliases (`a\b`), NUL bytes, `.git` and
anything beneath it at any depth, and every non-normalized alias (`a//b`,
`a/./b`, `foo/`, `./x`).

Non-normalized aliases are **rejected rather than silently collapsed**: if
TOMTIT quietly rewrote `a/./b` to `a/b`, the string the Owner approved and
the string TOMTIT enforces would differ, which is precisely the kind of gap
containment checks get bypassed through.

Containment is **component-aware**, not string-prefix:

```python
path_parts[:len(root_parts)] == root_parts
```

so `node_modules` matches `node_modules`, `node_modules/foo`, and
`node_modules/a/b`, but **not** `node_modules-evil/foo`,
`node_modules_evil/foo`, `xnode_modules/foo`, or `a/node_modules/foo`. A
naive `startswith("node_modules")` would have authorized the first three.

Validation runs at the trust boundary in `ClaudeCodeCLIRequest.__post_init__`,
so a caller constructing a request directly cannot bypass the CLI layer —
mirroring how `authorized_commands` is re-validated.

## 8. Attempt/repair propagation

The Owner-approved set is bound **exactly once**, in `run_loop`'s preflight,
and threaded unchanged to both attempts. It is never re-derived from a
child's evidence, so no reviewer finding can reach it.

`run_repair` enforces inheritance under the same governance rule already used
for `authorized_commands`: Attempt 2 must present the **identical ordered
set** Attempt 1 was approved for. Because the approved roots are Owner-typed
directory prefixes (not sensitive, unlike raw commands), the receipt records
them verbatim and repair compares the ordered tuple directly — which detects
addition, removal, substitution, **and reordering** alike. Evidence predating
this schema fails closed (`SIDE_EFFECT_PATH_EVIDENCE_INVALID`) rather than
inheriting an unknown scope.

Mismatch categories: `SIDE_EFFECT_PATH_CONTRACT_MISMATCH` (exit 3),
`SIDE_EFFECT_PATH_EVIDENCE_INVALID` (exit 3), CLI validation failure (exit 2).

## 9. Workspace classification algorithm

Full filesystem observation is **retained** — nothing was removed from the
snapshot, and no path is skipped. Only classification changed:

```python
if path in SOURCE_ALLOWED_PATHS:        -> SOURCE_CHANGE
elif under an AUTHORIZED_SIDE_EFFECT root: -> AUTHORIZED_SIDE_EFFECT
else:                                    -> UNEXPECTED_CHANGE  (fail closed)
```

Strict ordering means a path can never be laundered out of "unexpected" by
anything except an explicit Owner decision, and a path that is both
source-allowed and under an authorized root classifies as SOURCE.

`changed_paths` remains the **product change set** (source + unexpected).
Authorized generated output is reported separately. This is deliberate and
load-bearing: `run_repair.py:830` and `run_task.py:282` compare
`changed_paths` against the source allowlist, so letting 2709 generated files
into that list would have broken repair-scope enforcement as well as flooding
terminal output. With no side-effect authorization the value is byte-identical
to before.

## 10. Evidence representation

Visibility is preserved without dumping thousands of paths:

| Field | Bound |
|---|---|
| `allowed_side_effect_paths` | verbatim; bounded by the Owner's own list |
| `allowed_side_effect_path_count` | int |
| `observed_authorized_side_effect_count` | int |
| `observed_authorized_side_effect_counts_by_root` | one entry per approved root |
| `observed_authorized_side_effect_sample` | ≤ 10 paths |

Present in the adapter result, `build_evidence_manifest`, the `run_task` and
`run_repair` receipts, and the `run_loop` task contract. Terminal output
prints only the approved roots, the total, and per-root counts — never the
path list. The full generated set is never materialized into evidence.
Existing evidence immutability is untouched.

## 11. Real-shape regression fixture

Test 18 (`..._with_authorization`) uses the required fixture and asserts
`source=2, authorized_side_effect=4, unexpected=0`; test 19
(`..._without_authorization_fails_closed`) asserts the same fixture yields
`unexpected=4` with no authorization.

Independently verified at **true reported scale** during this task:

```
17 source paths + 2517 node_modules + 192 .next
-> source=17  side_effects=2709  unexpected=0   MATCH
```

## 12. Security regression checks

| Property | Status |
|---|---|
| `exact_bash_guard.py` | untouched — SHA-256 still `b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15` |
| Exact-Bash guard / fingerprints / `-E -s` / preflight | unchanged; 70 guard tests green |
| Provider-output withholding (M-1/M-2) | unchanged; still keyed off `authorized_commands` |
| Source-scope enforcement | preserved — test 10 shows `src/unapproved.ts` still violates while side-effect roots exist |
| Gitignored-but-unauthorized files | fail closed (tests 6/7) |
| `.git/**` authorizable | never — rejected by validation at CLI and request layers |
| Repair authorization immutability | ordered-set equality; addition/removal/substitution/reorder/drop all rejected |
| No-command / no-side-effect compatibility | new fields inert at defaults; **every pre-existing test passed unmodified** |

That last point is the strongest compatibility signal: no existing test in
any suite required a change.

## 13. Tests and exact exit codes

```
python3 .tomtit/dogfood/test_run_loop.py                            -> EXIT=0   (57 tests; was 52, +5)
python3 .tomtit/dogfood/test_run_repair.py                          -> EXIT=0   (61 tests; was 51, +10)
python3 .tomtit/dogfood/test_run_review.py                          -> EXIT=0   (45 tests)
python3 -m pytest tests/adapters/test_exact_bash_guard.py -q        -> EXIT=0   (70 tests)
python3 -m pytest tests/adapters -q                                 -> EXIT=0   (458 tests; was 410, +48)
python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q  -> EXIT=0   (4 tests)
python3 -m pytest -q                                                -> EXIT=0   (784 passed in 64.72s)
python3 -m compileall -q src tests scripts .tomtit/dogfood          -> EXIT=0
ruff check src tests scripts                                        -> EXIT=0
python3 -m mypy src                                                 -> EXIT=0   ("no issues found in 14 source files")
git diff --check                                                    -> EXIT=0
```

**Flakes:** none. The full suite passed clean on its **first** run; the
process-cleanup timing family recorded in prior reports did not flake. No
result was discarded and no rerun was needed.

**Self-caught issues during implementation, recorded honestly:** (a) the
`run_repair` receipt patch initially failed to apply because the anchor text
differed, caught by a failing inheritance test rather than assumed applied;
(b) a test helper interpolated `\n` as a real newline into a generated fake
script, producing a `SyntaxError` and a misleading `NONZERO_EXIT` — diagnosed
by executing the generated script directly rather than guessing.

## 14. Scope / non-goals

Modified: `src/tomtit/adapters/claude_code_cli.py`,
`tests/adapters/test_claude_code_cli.py`, `.tomtit/dogfood/run_task.py`,
`run_loop.py`, `run_repair.py`, `test_run_loop.py`, `test_run_repair.py` —
each required by the propagation path (CLI → request → classification →
evidence → inheritance).

Not modified: `exact_bash_guard.py`, Runtime core, governance/evaluation
contracts, memory, routing, `run_review.py` (its scope check is Git-based and
already saw only the 17 real paths), and personal-web.

Non-goals: no `.gitignore`-derived authorization; no globally-safe path list;
no change to what the workspace snapshot observes.

## 15. Final Git state

```
git rev-parse HEAD -> f4e6ba6ac66cb3b9f67351a7ae35bc2ceb2cb86d  (unchanged)
git status --short (tracked):
  M src/tomtit/adapters/__init__.py         (predates this task, untouched by it)
  M src/tomtit/adapters/claude_code_cli.py
  M tests/adapters/test_claude_code_cli.py
git diff --cached -> (empty; nothing staged)
git diff --check  -> EXIT=0
```

`.tomtit/dogfood/` changes do not appear in `git status` because that path is
gitignored. **personal-web was not touched**: no write, build, install, git,
or cleanup command was run against it; its pre-existing dirty state (13
entries) is exactly as found.

No commit, stage, reset, stash, checkout, push, merge, or deploy. No real
Claude or Codex invocation. No TOMTIT self-run. No `bypassPermissions`.

## 16. Next gate

Owner-coordinated review of this workspace-scope patch, focused on the
containment rules (§7), the independence of the two contracts (§6), and the
repair-inheritance immutability (§8). If approved, the Owner may authorize a
fresh personal-web dogfood run with `--allowed-side-effect-path node_modules
--allowed-side-effect-path .next` to confirm the real 17/2709/0 outcome in
practice.

---

STATUS=READY_FOR_WORKSPACE_SIDE_EFFECT_SCOPE_REVIEW
ROOT_CAUSE=GENERATED_FILES_MISCLASSIFIED_AS_SOURCE_SCOPE_CHANGES
BLANKET_GITIGNORE_FILTERING=NO
EXPLICIT_SIDE_EFFECT_AUTHORIZATION=IMPLEMENTED_PENDING_REVIEW
SOURCE_SCOPE_ENFORCEMENT=PRESERVED
UNKNOWN_IGNORED_FILES_FAIL_CLOSED=YES
GIT_INTERNALS_AUTHORIZABLE=NO
REPAIR_AUTHORIZATION_IMMUTABLE=YES
EXACT_BASH_AUTHORIZATION=PRESERVED
PROVIDER_OUTPUT_EVIDENCE_SAFETY=PRESERVED
PERSONAL_WEB_MODIFIED_BY_THIS_TASK=NO
PERSONAL_WEB_RERUN_AUTHORIZED=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_INVOCATIONS=0
AUTOMATIC_GIT_ACTIONS=0
NEXT_ACTION=OWNER_RUNS_FRESH_WORKSPACE_SCOPE_REVIEW
