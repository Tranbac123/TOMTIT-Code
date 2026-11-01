# Workspace Side-Effect Scope Accounting Independent Review

## 1. Verdict

`REQUEST_CHANGES`.

The accounting model correctly handles ordinary lexical paths and reproduces
the intended `17 source / 2709 authorized side effects / 0 unexpected`
result. The command and filesystem contracts are independent, and the
repair-inheritance checks are strong. Approval is nevertheless unsafe because
an authorized side-effect root can currently hide or authorize writes through
symlinks to Git metadata or outside the workspace. Two downstream consumers
also disagree with the new `changed_paths` semantics for Git-visible generated
output, and early-failure evidence loses the approved side-effect contract.

The Owner should not rerun personal-web until these findings are corrected and
independently re-reviewed.

## 2. Root-cause verification

The reported root cause is confirmed. Before this change, the adapter split
the complete observed mutation set only into exact `allowed_changed_paths` and
everything else. The implementation had no Owner-authored classification for
expected generated output, so the 2517 `node_modules/**` and 192 `.next/**`
paths necessarily became unexpected even though the 17 product paths were
exactly allowed.

The new `classify_workspace_changes()` performs the documented three-way split
in the correct order:

1. exact source allowlist membership;
2. lexical component containment beneath an approved side-effect root;
3. unexpected, fail closed.

Source classification therefore wins when an exact source path also lies
beneath a side-effect root. Unexpected paths still reach `_determine_status()`
and produce `SCOPE_VIOLATION` / `UNEXPECTED_CHANGED_PATHS` before timeout or
nonzero-exit handling.

## 3. Three-contract independence

The three contracts are separate in the actual call path:

- `allowed_changed_paths` is used only for exact source classification;
- `allowed_side_effect_paths` is used only for mutation classification and
  bounded side-effect evidence;
- `authorized_commands` alone controls exact-Bash contract resolution, guard
  artifacts, native `--allowedTools`, command fingerprints, and provider-output
  withholding.

The executable regressions prove both required directions. An exact
`npm run build` authorization with no side-effect root still classifies
`.next/out.json` as unexpected and returns a scope violation. Conversely,
authorizing `node_modules` without an authorized command adds neither
`--settings` nor `--allowedTools`, creates no guard artifacts or command
fingerprints, and withholds no provider output. No hidden direct request path
bypasses normalization: `run_task`, `run_loop`, and `run_repair` normalize at
their CLI boundaries, and `ClaudeCodeCLIRequest.__post_init__` validates again.

## 4. Path validation and component containment

The normalizer correctly rejects empty/root values, POSIX absolute paths,
every explicit `.` or `..` component, duplicate separators, trailing slashes,
backslash aliases, NUL, and top-level `.git` plus descendants. Direct request
construction uses the same normalizer. Deduplication is stable and
order-preserving.

Lexical containment is component-aware. Authorization for `node_modules`
accepts `node_modules/x` and `node_modules/a/b`, while denying
`node_modules-evil/x`, `node_modules_evil/x`, `xnode_modules/x`, and
`a/node_modules/x`. No naive `startswith()` check is used.

Finding M-2: the implementation and tests claim `.git` is rejected at any
depth, but the code checks only `parts[0] == ".git"`. The independent proof
showed both `a/.git` and `a/.git/config` are accepted. In addition, the
normalizer has no workspace argument and therefore cannot reject an approved
root that is itself a symlink resolving outside the workspace. Path validation
does not meet the stated contract.

## 5. Symlink / resolved-path analysis

Finding H-1 (high): the side-effect contract is enforced only on lexical path
components, while the observer follows file symlinks for content and skips
directory symlinks entirely.

The observer uses `root.rglob("*")`, calls `path.is_dir()`, yields lexical
relative paths, and later calls `read_bytes()`:

- a symlink to a file is recorded under its lexical workspace alias, but its
  resolved target bytes are hashed;
- a symlink to a directory is skipped by `is_dir()` and is not recursively
  observed;
- symlink identity and resolved target location are never recorded;
- retargeting a file symlink to equal bytes can be invisible;
- creating or mutating through a directory symlink can be wholly invisible.

An isolated fake-worker run constructed only beneath a temporary fixture. Its
workspace contained:

- `node_modules/external-file -> <fixture>/outside/external-file.txt`;
- `node_modules/git-config -> workspace/.git/config`;
- `node_modules/dir-link -> <fixture>/outside/external-dir`.

With only `node_modules` authorized as a side-effect root, the worker changed
all three targets plus the exact allowed source `result.txt`. The adapter
returned `SUCCEEDED`, `success=True`, `unexpected_changed_paths=()`, and a
side-effect count of two. The external file and `.git/config` changes were
classified under their lexical `node_modules/*` aliases; the external
directory write was absent from the snapshot entirely.

A non-ignored in-workspace source target reached through a file symlink is
safer: the observer sees both the lexical alias and the real source path, so
the real path remains unexpected unless exactly source-authorized. That does
not protect ignored `.git/**`, external targets, or directory-symlink targets.

The process boundary does not prevent this escape. The worker subprocess runs
with the workspace as its cwd but has no filesystem sandbox. `acceptEdits`
tools and exact authorized commands can follow existing symlinks; build/install
commands and lifecycle scripts can also create them. Exact argv authorization
limits which command starts, not what filesystem targets that command or its
children can resolve. This is practically reachable and is a trust-boundary
violation, not merely an evidence-display defect.

## 6. Gitignore independence

Adapter authorization and classification do not consult `.gitignore`.
`.env`, `.env.local`, `secret.txt`, `credentials.json`, `dist/bundle.js`, and
`coverage/lcov.info` remain unexpected unless explicitly covered by the Owner
contract. Editing `.gitignore` cannot alter the in-memory approved roots and
cannot launder an unauthorized mutation past the adapter snapshot.

There is, however, downstream behavioral dependence on Git visibility, as
described in the next section. This does not broaden adapter authorization but
does make the end-to-end workflow work for ignored generated paths and fail
for equivalent non-ignored generated paths.

## 7. changed_paths consumer audit

All consumers were audited, including the adapter evidence writer, spike
script, `run_task`, `run_loop`, `run_review`, and `run_repair`.

The adapter deliberately defines `changed_paths = source + unexpected` and
keeps authorized side effects separate. This keeps generated paths out of the
product patch, terminal path lists, and source-scope subset checks. For the
real ignored `node_modules/.next` workload, this is internally coherent.

Finding M-1 (medium): the semantic split is not coherent for an approved
side-effect path visible to Git. `run_review.run_preflight()` independently
calls `git_changed_paths()` and rejects every Git-visible path outside the
exact source allowlist. `run_repair.prove_reviewed_state()` also requires the
live Git path tuple to exactly equal the prior receipt's `changed_paths`.
Therefore an authorized but non-ignored `dist/bundle.js` is excluded from the
adapter receipt and patch, yet included by both downstream Git consumers. The
review fails `CHANGED_PATH_OUT_OF_SCOPE`, and repair fails
`WORKSPACE_STATE_MISMATCH`. This is fail-closed rather than a repair bypass,
but it is a reviewer-scope mismatch and contradicts the general contract's
claimed `.gitignore` independence.

Other consumers remain coherent: unexpected paths stay in `changed_paths`,
source-only work is not falsely reported as no change, repair's final subset
check remains fail-closed, and the spike runner has no side-effect contract.

## 8. Attempt-2 inheritance

Attempt 2 receives the same in-memory Owner-approved roots from `run_loop`.
`run_repair` normalizes its CLI values and compares the ordered list to Attempt
1 evidence. Addition, removal, substitution, reordering, dropping the set, and
introducing a set where Attempt 1 had none all fail closed. Legacy evidence
lacking the schema rejects any new side-effect authorization; legacy evidence
is usable only when no authorization is claimed.

The reviewer receives no side-effect-path argument and has no data path that
can mutate the in-memory set. Previous implementation/review evidence is
hashed before and after repair. These invariants and their executable tests
pass.

## 9. Evidence boundedness

Successful invocation evidence is bounded as designed: approved roots/count,
one count per root, total observed count, and at most ten sampled paths. The
full generated list is neither persisted nor printed. Patch generation uses
only `changed_paths`, so it does not dump generated files. Existing evidence
immutability checks remain active.

Finding M-3 (medium): `_early_result()` does not copy
`request.allowed_side_effect_paths` into `ClaudeCodeCLIResult`. An isolated
unavailable-executable proof began with `("node_modules",)` but produced
`result.allowed_side_effect_paths == ()` and a manifest count of zero. Thus
early-failure durable evidence does not faithfully record the approved
contract even though the request was already validated. Boundedness succeeds
on the normal path, but the required evidence consistency does not.

## 10. Real-shape regression

Independent classification proofs passed:

- reduced authorized fixture: `2 / 4 / 0`;
- same fixture without side-effect authorization: `2 / 0 / 4`;
- deterministic true scale: `17 / 2709 / 0`;
- true scale without side-effect authorization: `17 / 0 / 2709`.

The adapter end-to-end tests also confirm that unauthorized generated output
still produces `SCOPE_VIOLATION`.

## 11. Security regressions

The pre-existing exact-Bash layers are preserved:

- `exact_bash_guard.py` SHA-256 is
  `b35053af56606a8aca017190bba9c0d31df924e74378cb491007f62aab448e15`;
- canonical authorized-command validation and fingerprints remain active;
- guard invocation still uses `-E -s` and guard-file preflight;
- native `--allowedTools` remains layered with the TOMTIT guard;
- provider-output withholding remains keyed only to authorized commands;
- prompt remains exactly after `-p` and before `--allowedTools`;
- native bare-Bash exactness remains `False`;
- layered exact-argv authorization remains enabled;
- `run_in_background=True` remains denied;
- no-command/no-side-effect defaults remain inert.

These protections do not mitigate H-1 because they validate command argv and
provider evidence, not resolved filesystem targets.

## 12. Verification receipt with exact exit codes

Full output is preserved in `WORKSPACE_SIDE_EFFECT_SCOPE_REVIEW_FULL.log`.
Canonical results:

| Command | Result |
|---|---:|
| `python3 .tomtit/dogfood/test_run_loop.py` | 57 passed; exit 0 |
| `python3 .tomtit/dogfood/test_run_repair.py` | 61 passed; exit 0 |
| `python3 .tomtit/dogfood/test_run_review.py` | 45 passed; exit 0 |
| `python3 -m pytest tests/adapters/test_exact_bash_guard.py -q` | 70 passed; exit 0 |
| `python3 -m pytest tests/adapters -q` | 458 passed; exit 0 |
| `python3 -m pytest tests/scenarios/test_claude_code_cli_spike.py -q` | 4 passed; exit 0 |
| `python3 -m pytest -q` | 784 passed; exit 0 |
| `python3 -m compileall -q src tests scripts .tomtit/dogfood` | exit 0 |
| `ruff check src tests scripts` | exit 0 |
| `python3 -m mypy src` | exit 0; 14 source files clean |
| `git diff --check` | exit 0 |
| isolated symlink/classification proof | exit 0 |
| isolated early-result evidence proof | exit 0 |

First-run record: no product test flaked and the full suite passed on its first
run. The initial grouped dogfood logging shell exceeded the command tool's
yield and continued asynchronously, interleaving later log sections. Its
output was not erased; all observed exits were zero. Each harness was then run
under a uniquely labeled, explicitly polled canonical section, also exit zero.

No real Claude or Codex child invocation occurred. TOMTIT was not run against
itself, no Git write operation targeted the TOMTIT repository or its history,
and personal-web was not touched. The prescribed dogfood tests manage only
their own isolated temporary Git fixtures.

## 13. Findings by severity

### High

- H-1: lexical side-effect authorization plus symlink-following/skipping
  observation permits successful writes to external targets and `.git` while
  hiding or misclassifying the resolved mutation.

### Medium

- M-1: non-ignored authorized side effects conflict with Git-derived review
  and repair consumers of `changed_paths`.
- M-2: nested `.git` components are accepted despite the stated validation
  contract, and root resolution/symlink containment is not validated.
- M-3: early-result evidence drops approved side-effect roots and records zero.

### Low

- None.

## 14. Personal-web readiness

Not ready. The intended personal-web paths classify correctly in ordinary
fixtures, but `node_modules` commonly contains symlinks and the architecture
does not currently prove resolved writes remain inside the workspace or away
from Git metadata. A green real-shape counter is insufficient while H-1 is
open.

## 15. Next action

Harden resolved-path/symlink scope enforcement, reconcile every
`changed_paths` consumer with authorized non-ignored side effects, preserve the
approved contract in early evidence, add regressions for all findings, and
submit the exact corrective diff for independent re-review before personal-web
is rerun.

VERDICT=REQUEST_CHANGES
ROOT_CAUSE_CONFIRMED=YES
THREE_WAY_CLASSIFICATION=PASS
SOURCE_SCOPE_ENFORCEMENT=REGRESSED
COMMAND_AND_FILESYSTEM_AUTHORIZATION_INDEPENDENT=PASS
PATH_VALIDATION=FAIL
COMPONENT_CONTAINMENT=PASS
GITIGNORE_INDEPENDENCE=PASS
SYMLINK_ESCAPE=FINDING
CHANGED_PATHS_SEMANTICS=FAIL
REPAIR_AUTHORIZATION_IMMUTABILITY=PASS
EVIDENCE_BOUNDED=FAIL
REAL_SHAPE_17_2709_0=PASS
EXACT_BASH_AUTHORIZATION=PRESERVED
PROVIDER_OUTPUT_EVIDENCE_SAFETY=PRESERVED
FULL_LOG=WORKSPACE_SIDE_EFFECT_SCOPE_REVIEW_FULL.log
PERSONAL_WEB_DOGFOOD_READY=NO
REAL_CLAUDE_INVOCATIONS=0
REAL_CODEX_CHILD_INVOCATIONS=0
PERSONAL_WEB_TOUCHED=NO
NEXT_ACTION=FIX_SYMLINK_SCOPE_AND_CHANGED_PATH_CONSUMERS_THEN_REREVIEW
