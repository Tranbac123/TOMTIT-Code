# Codex Terra Baseline Provenance Review

## Scope and verdict

```text
VERDICT=REQUEST_CHANGES
F-001=OPEN
```

This review is limited to Git provenance, recovered-baseline tracking, and
evidence consistency. It does not approve Commit 1/2 behavior and does not
re-open or modify F-002 through F-005; those findings remain `OPEN`.

The recovered commit graph, tracked inventory, ignore rules, and initial
worktree are valid. However, the recovery audit report claims `94 passed`,
while the current clean tracked baseline reproducibly collects and runs 91
tests. Commit B changes only that report, so the historical discrepancy cannot
be explained from the Git evidence available here. This makes the report claim
inconsistent with current evidence and prevents approval of F-001 recovery.

```text
CURRENT_BASELINE_TEST_COUNT_VERIFIED=91
RECOVERY_REPORT_TEST_COUNT_94=NOT_REPRODUCED
HISTORICAL_91_TO_94_CAUSE=NOT_PROVEN
```

## Required governance read

Read before review:

- `CLAUDE.md`
- `SYSTEM.md`
- `docs/MVP_EXECUTION_POLICY_V0.md`
- `docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md`
- `REPORT_REPOSITORY_BASELINE_RECOVERY.md`

## 1. Initial repository state

The following is the raw command output captured before this review created its
allowed report file. Ignored generated artifacts were present; tracked,
untracked, and staged project files were absent.

### `git status --short`

Exit code: `0`

```text

```

### `git status --porcelain=v1 --untracked-files=all`

Exit code: `0`

```text

```

### `git status --ignored --short`

Exit code: `0`

```text
!! .mypy_cache/
!! .pytest_cache/
!! .ruff_cache/
!! src/.DS_Store
!! src/tomtit/.DS_Store
!! src/tomtit/__pycache__/
!! src/tomtit/domain/__pycache__/
!! src/tomtit/runtime/__pycache__/
!! tests/domain/__pycache__/
!! tests/scenarios/__pycache__/
```

### `git branch --show-current`

Exit code: `0`

```text
main
```

### `git rev-parse HEAD`

Exit code: `0`

```text
eb5bb397f812c51f356dae97512b44223a7012a7
```

### `git rev-parse HEAD^`

Exit code: `0`

```text
1e87e99a08860a227408ad99317b8d8586722ab3
```

### `git log --oneline --decorate -10`

Exit code: `0`

```text
eb5bb39 (HEAD -> main) docs(audit): record recovered baseline evidence
1e87e99 chore(repo): establish recovered reviewed baseline
```

### `git rev-list --count eb5bb397f812c51f356dae97512b44223a7012a7`

Exit code: `0`

```text
2
```

### `git rev-list --max-parents=0 eb5bb397f812c51f356dae97512b44223a7012a7`

Exit code: `0`

```text
1e87e99a08860a227408ad99317b8d8586722ab3
```

### Initial-state conclusion

| Check | Result |
|---|---|
| Branch | `main` |
| HEAD | `eb5bb397f812c51f356dae97512b44223a7012a7` |
| HEAD parent | `1e87e99a08860a227408ad99317b8d8586722ab3` |
| Root commit | `1e87e99a08860a227408ad99317b8d8586722ab3` |
| Commit count through Commit B | `2` |
| Initial index/worktree | Clean; no untracked project files |

All expected graph and initial-cleanliness checks pass.

## 2. Commit scope

### Commit A

`git show --format=fuller --stat 1e87e99a08860a227408ad99317b8d8586722ab3` exited `0`:

```text
commit 1e87e99a08860a227408ad99317b8d8586722ab3
Author:     Tranbac123 <tranvanbac061198@gmail.com>
AuthorDate: Tue Aug 4 11:08:43 2026 +0700
Commit:     Tranbac123 <tranvanbac061198@gmail.com>
CommitDate: Tue Aug 4 11:08:43 2026 +0700

    chore(repo): establish recovered reviewed baseline

 .gitignore                             |   12 +
 CLAUDE.md                              |   30 +
 README.md                              |   16 +
 SYSTEM.md                              |  189 +++++
 docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md |  383 +++++++++
 docs/MVP_EXECUTION_POLICY_V0.md        | 1380 ++++++++++++++++++++++++++++++++
 pyproject.toml                         |   33 +
 src/tomtit/__init__.py                 |    1 +
 src/tomtit/domain/__init__.py          |   51 ++
 src/tomtit/domain/contracts.py         |  566 +++++++++++++
 src/tomtit/domain/enums.py             |  286 +++++++
 src/tomtit/domain/evaluation.py        |  196 +++++
 src/tomtit/fakes.py                    |  101 +++
 src/tomtit/reporting.py                |  160 ++++
 src/tomtit/runtime/__init__.py         |   23 +
 src/tomtit/runtime/events.py           |   63 ++
 src/tomtit/runtime/runtime.py          |  413 ++++++++++
 src/tomtit/runtime/state.py            |  192 +++++
 tests/domain/test_contracts.py         |  655 +++++++++++++++
 tests/domain/test_evaluation_models.py |  353 ++++++++
 tests/domain/test_schema_generation.py |   98 +++
 tests/scenarios/test_scenario_1.py     |  682 ++++++++++++++++
 22 files changed, 5883 insertions(+)
```

`git show --format=fuller --name-status 1e87e99a08860a227408ad99317b8d8586722ab3` exited `0` and marked all 22 tracked files as `A`. Together with the root-commit result, this proves Commit A is the root import commit.

### Commit B

`git show --format=fuller --stat eb5bb397f812c51f356dae97512b44223a7012a7` exited `0`:

```text
commit eb5bb397f812c51f356dae97512b44223a7012a7
Author:     Tranbac123 <tranvanbac061198@gmail.com>
AuthorDate: Tue Aug 4 11:10:28 2026 +0700
Commit:     Tranbac123 <tranvanbac061198@gmail.com>
CommitDate: Tue Aug 4 11:10:28 2026 +0700

    docs(audit): record recovered baseline evidence

 REPORT_REPOSITORY_BASELINE_RECOVERY.md | 173 +++++++++++++++++++++++++++++++++
 1 file changed, 173 insertions(+)
```

`git show --format=fuller --name-status eb5bb397f812c51f356dae97512b44223a7012a7` exited `0`:

```text
commit eb5bb397f812c51f356dae97512b44223a7012a7
Author:     Tranbac123 <tranvanbac061198@gmail.com>
AuthorDate: Tue Aug 4 11:10:28 2026 +0700
Commit:     Tranbac123 <tranvanbac061198@gmail.com>
CommitDate: Tue Aug 4 11:10:28 2026 +0700

    docs(audit): record recovered baseline evidence

A	REPORT_REPOSITORY_BASELINE_RECOVERY.md
```

### A-to-B diff

`git diff --stat 1e87e99a08860a227408ad99317b8d8586722ab3 eb5bb397f812c51f356dae97512b44223a7012a7` exited `0`:

```text
 REPORT_REPOSITORY_BASELINE_RECOVERY.md | 173 +++++++++++++++++++++++++++++++++
 1 file changed, 173 insertions(+)
```

`git diff --name-status 1e87e99a08860a227408ad99317b8d8586722ab3 eb5bb397f812c51f356dae97512b44223a7012a7` exited `0`:

```text
A	REPORT_REPOSITORY_BASELINE_RECOVERY.md
```

Commit B only adds the recovery report. It does not modify source, tests,
governance, policy docs, README, `pyproject.toml`, or `.gitignore`.

This Git graph establishes a new auditable baseline only. It does not, and
cannot, prove that source files did not change before root Commit A.

## 3. Tracked inventory

| Inventory command | Exit code | Result |
|---|---:|---|
| `git ls-tree -r --name-only Commit A \| wc -l` | 0 | `22` |
| `git ls-tree -r --name-only Commit B \| wc -l` | 0 | `23` |
| `git ls-files` | 0 | 23 tracked files |

Commit A inventory contains `.gitignore`, governance, both policy documents,
`pyproject.toml`, all `src/**`, and all `tests/**`. Commit B adds only
`REPORT_REPOSITORY_BASELINE_RECOVERY.md`. The full current tracked inventory is:

```text
.gitignore
CLAUDE.md
README.md
REPORT_REPOSITORY_BASELINE_RECOVERY.md
SYSTEM.md
docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md
docs/MVP_EXECUTION_POLICY_V0.md
pyproject.toml
src/tomtit/__init__.py
src/tomtit/domain/__init__.py
src/tomtit/domain/contracts.py
src/tomtit/domain/enums.py
src/tomtit/domain/evaluation.py
src/tomtit/fakes.py
src/tomtit/reporting.py
src/tomtit/runtime/__init__.py
src/tomtit/runtime/events.py
src/tomtit/runtime/runtime.py
src/tomtit/runtime/state.py
tests/domain/test_contracts.py
tests/domain/test_evaluation_models.py
tests/domain/test_schema_generation.py
tests/scenarios/test_scenario_1.py
```

## 4. Generated-artifact exclusion

`.gitignore` contains all required entries:

```text
.DS_Store
__pycache__/
*.pyc
*.pyo
.pytest_cache/
.coverage
htmlcov/
.mypy_cache/
.ruff_cache/
build/
dist/
*.egg-info/
```

Evidence:

```text
$ git check-ignore -v src/.DS_Store
.gitignore:1:.DS_Store	src/.DS_Store

$ git check-ignore -v src/tomtit/.DS_Store
.gitignore:1:.DS_Store	src/tomtit/.DS_Store
```

Both commands exited `0`. `find` located five `__pycache__` directories and
19 `.pyc` files below `src` and `tests`; they are ignored. The required tracked
artifact check exited `1` with empty output:

```text
$ git ls-files | grep -E '(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache)(/|$)|\.py[co]$|\.DS_Store$'
<empty>
```

Exit `1` is the expected `grep` result for no matching tracked generated
artifacts.

## 5. Test-count consistency

### Required commands

| Command | Exit code | Raw output / result |
|---|---:|---|
| `python3 -m pytest --collect-only -q` | 0 | `test_contracts: 43`; `test_evaluation_models: 14`; `test_schema_generation: 3`; `test_scenario_1: 31` |
| `python3 -m pytest -q` | 0 | 91-dot progress display; all collected tests pass |
| `python3 -m pytest tests/scenarios/test_scenario_1.py --collect-only -q` | 0 | `tests/scenarios/test_scenario_1.py: 31` |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 | 31-dot progress display; all pass |

The full collection is exactly `43 + 14 + 3 + 31 = 91`. An additional
`python3 -m pytest -rA` confirmation exited `0` with the final line:

```text
91 passed in 0.40s
```

Pytest reports version `8.4.2`. The required command output showed no plugin
list. The tracked `pyproject.toml` supplies `testpaths = ["tests"]` and
`addopts = "-q"`.

Commit B makes no changes under `pyproject.toml`, `tests`, or `src` relative
to Commit A. The recovery report's statement that the full suite passed with
94 tests is therefore not reproduced by the current clean tracked baseline.
The cause of the prior `91` versus report `94` discrepancy is not proven by
the available Git objects or current command output.

## 6. Build and quality evidence

| Command | Exit code | Result |
|---|---:|---|
| `python3 -m compileall -q src tests` | 0 | Passed |
| `ruff check .` | 0 | Passed; no output |
| `git diff --check` | 0 | Passed; no current diff |
| `git show --check --oneline Commit A` | 2 | `SYSTEM.md:3: trailing whitespace.` |
| `git show --check --oneline Commit B` | 0 | Passed |
| `python3 -m mypy src tests` | 1 | Informational only: 81 errors in 4 files |

Raw whitespace evidence for Commit A:

```text
1e87e99 chore(repo): establish recovered reviewed baseline
SYSTEM.md:3: trailing whitespace.
+**Status:** Active  
```

Commit B's name-status and A-to-B diff show that it does not change
`SYSTEM.md`, so this is a pre-existing root-import defect rather than a change
introduced by the audit report.

## 7. F-001 resolution decision

| Required condition | Result |
|---|---|
| Valid HEAD and queryable baseline identity | PASS |
| Commit A is the root baseline | PASS |
| Commit B only adds audit report | PASS |
| Project files tracked | PASS |
| Generated artifacts ignored and untracked | PASS |
| Initial worktree/index clean | PASS |
| No untracked project files initially | PASS |
| Recovery report does not claim historical reconstruction | PASS |
| Recovery report claims match reproducible evidence | FAIL (`94` not reproduced; current result is `91`) |

F-001 is not yet resolved because the last condition is required and fails.
This does not invalidate the honest recovered graph itself; it requires a
separate correction to the audit evidence after this review.

## 8. Findings

### BASELINE-H01

ID: `BASELINE-H01`  
Severity: `HIGH`  
Location: `REPORT_REPOSITORY_BASELINE_RECOVERY.md`, section 5  
Problem: The audit report records `python3 -m pytest -q` as `94 passed`, but
the clean tracked tree at its own Commit B collects and passes 91 tests.  
Evidence: Required collection returns `43 + 14 + 3 + 31 = 91`; an independent
full run reports `91 passed in 0.40s`; Commit B changes only the report and no
test/config/source file.  
Impact: Recovery audit evidence is not internally reproducible, so F-001's
report-consistency condition is not met. This is not evidence that source
changed before Commit A.  
Required fix: Create a separately reviewed audit-correction commit that states
the reproducible current count, records the exact command/environment, and
marks the historical 91-to-94 cause as `NOT_PROVEN` unless new evidence proves
it. Do not alter implementation behavior as part of that correction.

### BASELINE-L01

ID: `BASELINE-L01`  
Severity: `LOW`  
Location: `SYSTEM.md:3` in Commit A  
Problem: `SYSTEM.md` contains pre-existing trailing whitespace.  
Evidence: `git show --check --oneline 1e87e99a08860a227408ad99317b8d8586722ab3`
exits 2 and reports `SYSTEM.md:3: trailing whitespace`; Commit B does not
modify the file.  
Impact: `git show --check` for the root import commit reports a whitespace
error.  
Required fix: Separate governance-cleanup task after baseline approval.

## 9. Open implementation findings outside this review

```text
F-002 OPEN — report trust boundary does not fully re-establish completion invariants
F-003 OPEN — Attempt 1 can still be canonicalized as NO_PROGRESS
F-004 OPEN — EvaluationEvent.payload is not deeply immutable
F-005 OPEN — README describes an obsolete project state
```

No implementation behavior is approved by this report, and Commit 3 remains
unauthorized.
