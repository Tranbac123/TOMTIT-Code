"""A real Product Runtime run, used to produce genuine terminal artifacts.

These bridge tests must not fake Runtime authority. Everything here drives the
actual :class:`tomtit.runtime.runtime.Runtime` through a real completion and
lets it publish a real ``report.v1.json``; the tests then read that file. The
fixture is duplicated from the scenario suite rather than imported because
``tests/scenarios`` is not an importable package.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tomtit.domain.contracts import (
    AcceptanceCriterion,
    CriterionAssignment,
    RequiredCheck,
    ScopeRule,
    TaskContract,
    VerificationPlan,
    WorkerResult,
)
from tomtit.domain.enums import (
    ScopeOperation,
    VerificationMethod,
    VerificationMode,
    WorkerStatus,
)
from tomtit.fakes import FakeCheckRunner, FakeWorker
from tomtit.reporting import ReportSourceRevision
from tomtit.runtime.events import InMemoryEventLog
from tomtit.runtime.runtime import Runtime

#: Committed revisions, the only Contract V1 §5.1 ACTIVE revision type. Runtime
#: cannot publish either — no component establishes the revision a run executed
#: against — so they exist here only to build the drafts that must be refused.
RUNTIME_COMMIT = "1810b7e2e70065f097edc35d35e89d6b737e3c27"
OTHER_COMMIT = "cdb6d360d1d340a1f7720707d3a8594937c0a6ad"


class StepClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._value = start or datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self._value
        self._value += timedelta(seconds=1)
        return value


def _id_factory() -> Callable[[], str]:
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"event-{counter:03d}"

    return next_id


def _task_contract() -> TaskContract:
    return TaskContract(
        task_id="task-bridge",
        goal="Make the deterministic fixture pass",
        acceptance_criteria=[
            AcceptanceCriterion(
                id="tests-pass",
                description="Required unit tests pass",
                verification_method=VerificationMethod.DETERMINISTIC,
                required_check_ids=["unit-tests"],
            )
        ],
        allowed_scope=[
            ScopeRule(
                pattern="src/**",
                operations=[ScopeOperation.CREATE, ScopeOperation.MODIFY],
            )
        ],
        required_checks=[
            RequiredCheck(
                id="unit-tests",
                argv=["python", "-m", "pytest", "tests/unit"],
                cwd_relative=".",
                timeout_seconds=60,
                expected_exit_codes=[0],
            )
        ],
        time_budget_seconds=120,
    )


def _worker_result() -> WorkerResult:
    return WorkerResult(
        status=WorkerStatus.COMPLETED,
        summary="Implemented the fixture change",
        changed_files=("src/fixture.py",),
        commands_executed=[],
        claimed_checks=(),
        remaining_risks=[],
        raw_output_path="artifacts/worker-result.json",
    )


def run_product_runtime(directory: Path, *, run_id: str = "run-bridge") -> Runtime:
    """Execute one real Runtime completion and return the Runtime that did it.

    Returns the Runtime rather than a path on purpose: the artifact is not the
    interesting output any more. ``runtime.publication_receipt`` is, because it
    is the only thing that establishes that this report was published by
    Runtime rather than written by whoever is asking.
    """

    runtime = Runtime(
        run_id=run_id,
        task_contract=_task_contract(),
        verification_plan=VerificationPlan(
            mode=VerificationMode.B,
            criterion_assignments=[
                CriterionAssignment(
                    criterion_id="tests-pass",
                    verification_method=VerificationMethod.DETERMINISTIC,
                    available=True,
                )
            ],
        ),
        worker=FakeWorker(_worker_result()),
        check_runner=FakeCheckRunner(artifact_directory=directory / "artifacts"),
        event_log=InMemoryEventLog(event_id_factory=_id_factory()),
        report_directory=directory,
        clock=StepClock(),
    )
    runtime.run_scenario_1()
    return runtime


def published_report(directory: Path) -> Path:
    """The write-once artifact path a completed run publishes."""

    return directory / "report.v1.json"


def git_revision(value: str = RUNTIME_COMMIT) -> ReportSourceRevision:
    """A well-formed revision no production path can attach to a report."""

    return ReportSourceRevision(revision_type="GIT_COMMIT", revision_value=value)
