"""Deterministic fake dependencies used only by mandatory fake scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from tomtit.domain.contracts import RequiredCheck, TaskContract, WorkerResult
from tomtit.domain.enums import ScopeOperation
from tomtit.runtime.state import (
    ObservedChangedFile,
    ObservedCheckResult,
    ObservedPatchEvidence,
)


@dataclass(slots=True)
class FakeWorker:
    """Returns a configured WorkerResult without owning any runtime state."""

    result: WorkerResult
    calls: list[tuple[str, int]] = field(default_factory=list)

    def execute(self, task_contract: TaskContract, attempt_number: int) -> WorkerResult:
        self.calls.append((task_contract.task_id, attempt_number))
        return self.result


@dataclass(frozen=True, slots=True)
class FakeCheckOutcome:
    exit_code: int = 0
    duration_seconds: float = 0.01
    stdout: str = "fake check passed\n"
    stderr: str = ""


@dataclass(slots=True)
class FakeCheckRunner:
    """Creates deterministic evidence artifacts without invoking a subprocess or shell."""

    artifact_directory: Path
    outcomes: dict[str, FakeCheckOutcome] = field(default_factory=dict)
    # Scenario 2 needs a check that fails on Attempt 1 and passes on Attempt 2.
    attempt_outcomes: dict[tuple[int, str], FakeCheckOutcome] = field(default_factory=dict)
    patch_changed_files: tuple[ObservedChangedFile, ...] = field(
        default_factory=lambda: (
            ObservedChangedFile(path="src/fixture.py", operation=ScopeOperation.MODIFY),
        )
    )
    patch_scope_violations: tuple[str, ...] = ()
    patch_diff_exists: bool = True
    patch_evidence_complete: bool = True
    calls: list[tuple[str, int]] = field(default_factory=list)

    def run(
        self,
        check: RequiredCheck,
        run_id: str,
        attempt_number: int,
    ) -> ObservedCheckResult:
        self.calls.append((check.id, attempt_number))
        outcome = self.attempt_outcomes.get(
            (attempt_number, check.id),
            self.outcomes.get(check.id, FakeCheckOutcome()),
        )
        check_key = sha256(check.id.encode("utf-8")).hexdigest()[:16]
        attempt_directory = self.artifact_directory / f"attempt-{attempt_number}"
        attempt_directory.mkdir(parents=True, exist_ok=True)
        stdout_path = attempt_directory / f"{check_key}.stdout.txt"
        stderr_path = attempt_directory / f"{check_key}.stderr.txt"
        stdout_path.write_text(outcome.stdout, encoding="utf-8")
        stderr_path.write_text(outcome.stderr, encoding="utf-8")
        return ObservedCheckResult(
            run_id=run_id,
            check_id=check.id,
            attempt_number=attempt_number,
            argv=check.argv,
            cwd_relative=check.cwd_relative,
            exit_code=outcome.exit_code,
            duration_seconds=outcome.duration_seconds,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )

    def collect_patch_evidence(
        self,
        run_id: str,
        attempt_number: int,
    ) -> ObservedPatchEvidence:
        """Create one deterministic patch artifact observed by Runtime."""

        attempt_directory = self.artifact_directory / f"attempt-{attempt_number}"
        attempt_directory.mkdir(parents=True, exist_ok=True)
        artifact_path = attempt_directory / "patch-evidence.json"
        evidence = ObservedPatchEvidence(
            run_id=run_id,
            attempt_number=attempt_number,
            changed_files=self.patch_changed_files,
            diff_exists=self.patch_diff_exists,
            scope_violations=self.patch_scope_violations,
            evidence_complete=self.patch_evidence_complete,
            artifact_path=str(artifact_path),
        )
        artifact_path.write_text(evidence.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return evidence
