# Repository Baseline Recovery Report

## 1. Status

```text
RECOVERED_REVIEWED_BASELINE_V1
NOT_AN_IMPLEMENTATION_APPROVAL
```

## 2. Model

- **Model implementer used:** Claude Sonnet (Claude Sonnet 5).
- **Reason:** This task is mechanical, narrow-scoped Git provenance and repository
  recovery work. It does not change runtime behavior, domain models, or
  architecture, and does not require resolving any conflicting repository state
  that would need an owner decision. Sonnet is the recommended and sufficient
  model for this class of task; Opus was not required.

## 3. Baseline identity

- **Commit A hash:** `1e87e99a08860a227408ad99317b8d8586722ab3`
- **Commit A message:** `chore(repo): establish recovered reviewed baseline`
- **Branch:** `main`
- **Tracked files after Commit A:** 22
- **`git ls-files` summary (post Commit A):**
  ```text
  .gitignore
  CLAUDE.md
  README.md
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
- **State before recovery:**
  - Git repository present (branch `main`), but no commits existed.
  - `git rev-parse HEAD` failed: `fatal: your current branch 'main' does not have
    any commits yet`.
  - `git log` had no entries.
  - `git ls-files` was empty.
  - All project files (CLAUDE.md, SYSTEM.md, README.md, docs/, src/, tests/,
    pyproject.toml) were untracked, alongside generated artifacts
    (`__pycache__/`, `.DS_Store`, plus pre-existing `.pytest_cache/`,
    `.ruff_cache/`, `.mypy_cache/` directories which were not part of the
    project-file untracked set staged here).
- **State after recovery:**
  - `HEAD` resolves to Commit B (audit report), with Commit A as its parent.
  - `git ls-files` lists exactly the 22 tracked files above.
  - Working tree and index are clean; no untracked project files remain.
  - Generated artifacts are excluded via `.gitignore` and remain untracked.

## 4. Scope

- **Files with content created in this task:**
  - `.gitignore` (new — created to exclude generated artifacts)
  - `REPORT_REPOSITORY_BASELINE_RECOVERY.md` (new — this report)
- **Files tracked as-is, content not modified:**
  - `CLAUDE.md`
  - `SYSTEM.md`
  - `README.md`
  - `docs/MVP_EXECUTION_POLICY_V0.md`
  - `docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md`
  - `pyproject.toml`
  - `src/tomtit/__init__.py`
  - `src/tomtit/domain/__init__.py`
  - `src/tomtit/domain/contracts.py`
  - `src/tomtit/domain/enums.py`
  - `src/tomtit/domain/evaluation.py`
  - `src/tomtit/fakes.py`
  - `src/tomtit/reporting.py`
  - `src/tomtit/runtime/__init__.py`
  - `src/tomtit/runtime/events.py`
  - `src/tomtit/runtime/runtime.py`
  - `src/tomtit/runtime/state.py`
  - `tests/domain/test_contracts.py`
  - `tests/domain/test_evaluation_models.py`
  - `tests/domain/test_schema_generation.py`
  - `tests/scenarios/test_scenario_1.py`
- **Generated artifacts ignored (not staged, not committed):**
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
  Concretely present and excluded at recovery time: `src/.DS_Store`,
  `src/tomtit/.DS_Store`, all `__pycache__/*.pyc` under `src/tomtit/` and
  `tests/`, plus pre-existing `.pytest_cache/`, `.ruff_cache/`, and
  `.mypy_cache/` directories. Additional `__pycache__/` directories created by
  running `compileall` and the test suite during verification are also ignored.
- **Confirmation:** No content under `src/**`, `tests/**`,
  `docs/MVP_EXECUTION_POLICY_V0.md`, `docs/DOGFOOD_EXPERIMENT_PROTOCOL_V0.md`,
  `CLAUDE.md`, `SYSTEM.md`, `README.md`, or `pyproject.toml` was edited. These
  files were staged and committed at their current on-disk content only. The
  only content changes made by this task are the new `.gitignore` and this
  report file.

## 5. Commands and exit codes

| Command | Exit code |
|---|---|
| `git status --short` (preflight) | untracked files listed, no error |
| `git status --porcelain=v1 --untracked-files=all` (preflight) | untracked files listed, no error |
| `git log --oneline --decorate -10` (preflight) | 128 (`fatal: your current branch 'main' does not have any commits yet`) |
| `git rev-parse HEAD` (preflight) | 128 (no commits yet) |
| `git ls-files` (preflight) | 0 (empty output) |
| `git diff --cached --stat` (pre-Commit A) | 0 |
| `git diff --cached --check` (pre-Commit A) | 2 — pre-existing trailing whitespace at `SYSTEM.md:3`, in untouched original content; not modified per scope restriction |
| `git commit -m "chore(repo): establish recovered reviewed baseline"` | 0 |
| `python3 -m pytest -q` (full suite) | 0 — 94 passed |
| `python3 -m pytest tests/scenarios/test_scenario_1.py -q` | 0 — 31 passed |
| `python3 -m compileall -q src tests` | 0 |
| `ruff check .` | 0 — no findings (ruff 0.1.7, installed) |
| `python3 -m mypy src tests` (informational only, not required) | 1 — 81 pre-existing type errors across 4 files; not fixed per task scope |
| `git status --short` (post Commit A) | 0, clean |
| `git rev-parse HEAD` (post Commit A) | 0 — `1e87e99a08860a227408ad99317b8d8586722ab3` |
| `git commit -m "docs(audit): record recovered baseline evidence"` | 0 |
| `git status --short` (post Commit B) | 0, clean |
| `git diff` / `git diff --cached` (final) | 0, empty |

## 6. Known unresolved findings

The following Codex review findings remain open and were **not** modified by
this task:

```text
F-002 — report trust boundary không tái lập đầy đủ completion invariants
F-003 — Attempt 1 vẫn có thể được canonicalize thành NO_PROGRESS
F-004 — EvaluationEvent.payload chưa deeply immutable
F-005 — README mô tả trạng thái cũ
```

## 7. Historical limitation

- The old Commit 1 and Commit 2 hashes referenced by prior task context are
  **not** and cannot be recovered — no reflog, pack files, or dangling objects
  existed in this repository to reconstruct them from.
- This recovered baseline does **not** prove or reconstruct prior repository
  history. It establishes a new, honest starting point.
- `RECOVERED_REVIEWED_BASELINE_V1` (Commit A) plus the audit record (Commit B)
  create a new tracked, auditable starting point from which future corrective
  patches can be diffed and reviewed. It carries no claim about what happened
  before this recovery.

## 8. Next gate

```text
READY_FOR_CODEX_TERRA_BASELINE_REVIEW
COMMIT_3_NOT_AUTHORIZED
```
