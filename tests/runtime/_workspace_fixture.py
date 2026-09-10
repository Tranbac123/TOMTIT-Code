"""Self-contained Git workspaces and a deterministic provider stand-in.

Everything is built under ``tmp_path``: no test reads the developer's own
repository, and no test invokes a paid model. The "provider" is a small Python
script handed to the adapter through its own ``executable_path`` seam, so the
real adapter layer runs for real while what it launches stays deterministic.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tomtit.domain.contracts import (
    AcceptanceCriterion,
    CriterionAssignment,
    RequiredCheck,
    ScopeRule,
    TaskContract,
    VerificationPlan,
)
from tomtit.domain.enums import (
    ScopeOperation,
    VerificationMethod,
    VerificationMode,
)

#: Passes only once the worker has actually edited the in-scope file, so a green
#: check is real evidence rather than a constant.
FIXTURE_CHECK_ARGV = (
    "python3",
    "-c",
    "import pathlib,sys; sys.exit(0 if 'VALUE = 42' in "
    "pathlib.Path('src/fixture.py').read_text() else 1)",
)

_COMMIT_ENV = ("-c", "user.email=fixture@tomtit.invalid", "-c", "user.name=TOMTIT Fixture")


def git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(path), check=True, capture_output=True, text=True
    )


def commit_all(path: Path, message: str) -> str:
    git(path, "add", "-A")
    git(path, *_COMMIT_ENV, "commit", "-qm", message)
    return head_commit(path)


def make_repository(path: Path, *, value: int = 0, marker: str = "tracked") -> str:
    """Create a clean Git repository and return its HEAD.

    ``outside.txt`` is tracked from the start so tests can move or delete a path
    that the usual ``src/*`` scope does not cover.
    """

    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "fixture@tomtit.invalid")
    git(path, "config", "user.name", "TOMTIT Fixture")
    (path / "src").mkdir(exist_ok=True)
    (path / "src" / "fixture.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
    # A second in-scope file, so a test can move or delete something inside
    # scope without also breaking the required check.
    (path / "src" / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
    (path / "outside.txt").write_text(f"{marker} outside\n", encoding="utf-8")
    return commit_all(path, "base")


def head_commit(path: Path) -> str:
    return git(path, "rev-parse", "HEAD").stdout.strip()


def write_provider(
    directory: Path,
    name: str = "fake-claude",
    *,
    body: str | None = None,
    sentinel: Path | None = None,
) -> Path:
    """A stand-in provider executable.

    Default behaviour edits the in-scope file the way a coding worker would.
    ``sentinel`` makes the script touch a path first, so a test can prove
    whether the provider was ever launched at all.
    """

    lines = ["import pathlib, subprocess"]
    if sentinel is not None:
        lines.append(
            f"pathlib.Path({str(sentinel)!r}).write_text('ran', encoding='utf-8')"
        )
    lines.append(
        body
        or "pathlib.Path('src/fixture.py').write_text('VALUE = 42\\n', encoding='utf-8')"
    )
    lines.append("print('ok')")

    script = directory / name
    script.write_text(
        "#!/usr/bin/env python3\n" + "\n".join(lines) + "\n", encoding="utf-8"
    )
    script.chmod(0o755)
    return script


def renaming_provider(directory: Path, source: str, destination: str) -> Path:
    """A provider that commits a rename and then makes the in-scope change.

    This is the exploit shape: with Git rename detection on, the committed move
    is reported only as its destination and the fact that ``source`` was removed
    disappears from evidence.
    """

    return write_provider(
        directory,
        f"rename-{Path(destination).name}",
        body=(
            f"subprocess.run(['git','mv',{source!r},{destination!r}], check=True)\n"
            "subprocess.run(['git','-c','user.email=x@y','-c','user.name=x',"
            f"'commit','-qm','move'], check=True)\n"
            "pathlib.Path('src/fixture.py').write_text('VALUE = 42\\n', encoding='utf-8')"
        ),
    )


def task_contract(
    *,
    task_id: str = "task-real",
    pattern: str = "src/*",
    operations: tuple[ScopeOperation, ...] = (
        ScopeOperation.CREATE,
        ScopeOperation.MODIFY,
    ),
    argv: tuple[str, ...] = FIXTURE_CHECK_ARGV,
) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        goal="Set VALUE to 42",
        acceptance_criteria=[
            AcceptanceCriterion(
                id="tests-pass",
                description="the fixture check passes",
                verification_method=VerificationMethod.DETERMINISTIC,
                required_check_ids=["fixture-check"],
            )
        ],
        allowed_scope=[ScopeRule(pattern=pattern, operations=list(operations))],
        required_checks=[
            RequiredCheck(
                id="fixture-check",
                argv=argv,
                cwd_relative=".",
                timeout_seconds=30,
                expected_exit_codes=[0],
            )
        ],
        time_budget_seconds=120,
    )


def verification_plan() -> VerificationPlan:
    return VerificationPlan(
        mode=VerificationMode.B,
        criterion_assignments=[
            CriterionAssignment(
                criterion_id="tests-pass",
                verification_method=VerificationMethod.DETERMINISTIC,
                available=True,
            )
        ],
    )
