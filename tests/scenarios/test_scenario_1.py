from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest
from pydantic import ValidationError
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
    CriterionStatus,
    EvaluationEventType,
    NextAction,
    Outcome,
    RunState,
    ScopeOperation,
    StopReason,
    VerificationMethod,
    VerificationMode,
    VerificationStatus,
    WorkerStatus,
)
from tomtit.fakes import FakeCheckOutcome, FakeCheckRunner, FakeWorker
from tomtit.reporting import (
    TerminalReport,
    build_terminal_report,
    write_terminal_report,
)
from tomtit.runtime import CompletionBasis, InMemoryEventLog, TransitionReason
from tomtit.runtime.runtime import (
    CompletionConditionsNotMet,
    InvalidTransitionError,
    Runtime,
)
from tomtit.runtime.state import (
    ObservedCheckResult,
    ObservedPatchEvidence,
    RunRecord,
)


class StepClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._value = start or datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self._value
        self._value += timedelta(seconds=1)
        return value


class EvidenceTamperingRunner(FakeCheckRunner):
    """Produces malformed evidence only for negative provenance/artifact tests."""

    def __init__(
        self,
        *,
        artifact_directory: Path,
        foreign_run_id: str | None = None,
        attempt_number_override: int | None = None,
        missing_artifact: str | None = None,
        directory_artifact: str | None = None,
    ) -> None:
        super().__init__(artifact_directory=artifact_directory)
        self._foreign_run_id = foreign_run_id
        self._attempt_number_override = attempt_number_override
        self._missing_artifact = missing_artifact
        self._directory_artifact = directory_artifact

    def run(
        self,
        check: RequiredCheck,
        run_id: str,
        attempt_number: int,
    ) -> ObservedCheckResult:
        result = super().run(check, run_id, attempt_number)
        if self._missing_artifact == "stdout":
            Path(result.stdout_path).unlink()
        elif self._missing_artifact == "stderr":
            Path(result.stderr_path).unlink()
        if self._directory_artifact == "stdout":
            path = Path(result.stdout_path)
            path.unlink()
            path.mkdir()
        updates: dict[str, object] = {}
        if self._foreign_run_id is not None:
            updates["run_id"] = self._foreign_run_id
        if self._attempt_number_override is not None:
            updates["attempt_number"] = self._attempt_number_override
        return result.model_copy(update=updates)

    def collect_patch_evidence(
        self,
        run_id: str,
        attempt_number: int,
    ) -> ObservedPatchEvidence:
        result = super().collect_patch_evidence(run_id, attempt_number)
        if self._missing_artifact == "patch":
            Path(result.artifact_path).unlink()
        updates: dict[str, object] = {}
        if self._foreign_run_id is not None:
            updates["run_id"] = self._foreign_run_id
        if self._attempt_number_override is not None:
            updates["attempt_number"] = self._attempt_number_override
        return result.model_copy(update=updates)


def id_factory() -> Callable[[], str]:
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"event-{counter:03d}"

    return next_id


def task_contract() -> TaskContract:
    return TaskContract(
        task_id="task-s1",
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


def verification_plan(*, available: bool = True) -> VerificationPlan:
    return VerificationPlan(
        mode=VerificationMode.B,
        criterion_assignments=[
            CriterionAssignment(
                criterion_id="tests-pass",
                verification_method=VerificationMethod.DETERMINISTIC,
                available=available,
            )
        ],
    )


def worker_result(
    *,
    claimed_checks: tuple[str, ...] = (),
    changed_files: tuple[str, ...] = ("src/fixture.py",),
) -> WorkerResult:
    return WorkerResult(
        status=WorkerStatus.COMPLETED,
        summary="Implemented the fixture change",
        changed_files=changed_files,
        commands_executed=[],
        claimed_checks=claimed_checks,
        remaining_risks=[],
        raw_output_path="artifacts/worker-result.json",
    )


def make_runtime(
    tmp_path: Path,
    *,
    plan: VerificationPlan | None = None,
    worker: FakeWorker | None = None,
    runner: FakeCheckRunner | None = None,
) -> Runtime:
    clock = StepClock()
    return Runtime(
        run_id="run-s1",
        task_contract=task_contract(),
        verification_plan=plan or verification_plan(),
        worker=worker or FakeWorker(worker_result()),
        check_runner=runner or FakeCheckRunner(artifact_directory=tmp_path / "artifacts"),
        event_log=InMemoryEventLog(event_id_factory=id_factory()),
        report_directory=tmp_path,
        clock=clock,
    )


def test_scenario_1_completes_from_observed_deterministic_evidence(tmp_path: Path) -> None:
    worker = FakeWorker(
        worker_result(
            claimed_checks=("untrusted-self-claim",),
            changed_files=("worker-claimed-only.py",),
        )
    )
    runner = FakeCheckRunner(artifact_directory=tmp_path / "artifacts")
    runtime = make_runtime(tmp_path, worker=worker, runner=runner)

    record = runtime.run_scenario_1()

    assert record.state is RunState.COMPLETED
    assert record.attempt_count == 1
    assert record.outcome is Outcome.COMPLETED
    assert record.stop_reason is StopReason.ACCEPTANCE_SATISFIED
    assert record.verification_status is VerificationStatus.DETERMINISTICALLY_CHECKED
    assert record.completion_basis is CompletionBasis.DETERMINISTIC_EVIDENCE
    assert record.criterion_evaluations[0].status is CriterionStatus.SATISFIED
    assert worker.calls == [("task-s1", 1)]
    assert runner.calls == [("unit-tests", 1)]

    report_path = tmp_path / "report.v1.json"
    assert record.report_path == str(report_path)
    report = TerminalReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.schema_version == "report.v1"
    assert report.outcome is Outcome.COMPLETED
    assert report.stop_reason is StopReason.ACCEPTANCE_SATISFIED
    assert report.attempts == 1
    assert report.acceptance_criteria[0].status is CriterionStatus.SATISFIED
    assert report.observed_evidence[0].run_id == record.run_id
    assert report.observed_evidence[0].attempt_number == 1
    assert report.observed_evidence[0].exit_code == 0
    assert Path(report.observed_evidence[0].stdout_path).is_file()
    assert Path(report.observed_evidence[0].stderr_path).is_file()
    assert report.observed_patch_evidence.run_id == record.run_id
    assert report.observed_patch_evidence.attempt_number == 1
    assert Path(report.observed_patch_evidence.artifact_path).is_file()
    assert report.changed_files == ("src/fixture.py",)
    assert report.worker_claims.changed_files == ("worker-claimed-only.py",)
    assert report.worker_claims.claimed_checks == ("untrusted-self-claim",)

    event_types = tuple(event.event_type for event in runtime.events)
    assert event_types == (
        EvaluationEventType.TASK_SUBMITTED,
        EvaluationEventType.CONTRACT_INFERRED,
        EvaluationEventType.RUN_STARTED,
        EvaluationEventType.WORKSPACE_PREPARED,
        EvaluationEventType.WORKER_ASSIGNED,
        EvaluationEventType.WORKER_STARTED,
        EvaluationEventType.WORKER_FINISHED,
        EvaluationEventType.EVIDENCE_COLLECTED,
        EvaluationEventType.CHECK_PASSED,
        EvaluationEventType.RUN_COMPLETED,
        EvaluationEventType.REPORT_GENERATED,
    )
    transition_events = [
        event for event in runtime.events if "from_state" in event.payload
    ]
    assert len(transition_events) == 6
    assert transition_events[-1].payload["to_state"] == "COMPLETED"


def test_verification_compatibility_fails_before_preparing_or_worker_call(
    tmp_path: Path,
) -> None:
    worker = FakeWorker(worker_result())
    runtime = make_runtime(
        tmp_path,
        plan=verification_plan(available=False),
        worker=worker,
    )

    with pytest.raises(ValueError, match="unavailable"):
        runtime.run_scenario_1()

    assert runtime.record.state is RunState.CONTRACT_READY
    assert runtime.record.attempt_count == 0
    assert worker.calls == []
    assert not (tmp_path / "report.v1.json").exists()


def test_worker_claim_cannot_satisfy_failed_observed_check(tmp_path: Path) -> None:
    worker = FakeWorker(worker_result(claimed_checks=("unit-tests",)))
    runner = FakeCheckRunner(
        artifact_directory=tmp_path / "artifacts",
        outcomes={"unit-tests": FakeCheckOutcome(exit_code=1)},
    )
    runtime = make_runtime(tmp_path, worker=worker, runner=runner)

    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    assert runtime.record.state is RunState.CHECKING
    assert runtime.record.criterion_evaluations[0].status is CriterionStatus.UNSATISFIED
    assert runtime.record.outcome is None
    assert EvaluationEventType.CHECK_FAILED in {
        event.event_type for event in runtime.events
    }
    assert not (tmp_path / "report.v1.json").exists()


def test_runtime_rejects_illegal_direct_transition(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    with pytest.raises(InvalidTransitionError, match="illegal transition"):
        runtime.transition(RunState.COMPLETED, TransitionReason.TERMINATION_DECIDED)

    assert runtime.record.state is RunState.CREATED
    assert runtime.events == ()


def test_runtime_requires_attempt_one_before_implementing(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.transition(RunState.CONTRACT_READY, TransitionReason.CONTRACT_RESOLVED)
    runtime.transition(RunState.PREPARING, TransitionReason.WORKSPACE_PREPARED)

    with pytest.raises(InvalidTransitionError, match="Attempt 1"):
        runtime.transition(RunState.IMPLEMENTING, TransitionReason.WORKER_ASSIGNED)


def test_runtime_requires_worker_result_before_evidence_collection(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.transition(RunState.CONTRACT_READY, TransitionReason.CONTRACT_RESOLVED)
    runtime.transition(RunState.PREPARING, TransitionReason.WORKSPACE_PREPARED)
    runtime.start_attempt()

    with pytest.raises(InvalidTransitionError, match="WorkerResult"):
        runtime.transition(RunState.COLLECTING_EVIDENCE, TransitionReason.WORKER_FINISHED)


def test_terminal_state_cannot_resume(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.run_scenario_1()

    with pytest.raises(InvalidTransitionError, match="terminal"):
        runtime.transition(RunState.PREPARING, TransitionReason.WORKSPACE_PREPARED)


def test_append_only_log_returns_detached_event_payloads(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.run_scenario_1()
    first_snapshot = runtime.events
    transition_index = next(
        index for index, event in enumerate(first_snapshot) if "to_state" in event.payload
    )
    original_state = first_snapshot[transition_index].payload["to_state"]

    first_snapshot[transition_index].payload["to_state"] = "CORRUPTED"

    assert runtime.events[transition_index].payload["to_state"] == original_state
    assert len(runtime.events) == 11


def test_terminal_report_is_write_once(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.run_scenario_1()
    report = TerminalReport.model_validate_json(
        (tmp_path / "report.v1.json").read_text(encoding="utf-8")
    )

    with pytest.raises(FileExistsError):
        write_terminal_report(tmp_path / "report.v1.json", report)


def test_report_json_is_machine_checkable(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.run_scenario_1()

    raw = json.loads((tmp_path / "report.v1.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == "report.v1"
    assert raw["acceptance_criteria"][0]["status"] == "SATISFIED"
    assert raw["observed_patch_evidence"]["diff_exists"] is True
    assert raw["changed_files"] == ["src/fixture.py"]
    assert raw["observed_evidence"][0]["argv"] == [
        "python",
        "-m",
        "pytest",
        "tests/unit",
    ]


def test_transition_reason_must_match_state_change(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    with pytest.raises(InvalidTransitionError, match="requires reason CONTRACT_RESOLVED"):
        runtime.transition(RunState.CONTRACT_READY, TransitionReason.WORKER_ASSIGNED)

    assert runtime.record.state is RunState.CREATED
    assert runtime.events == ()


def test_existing_terminal_report_blocks_run_before_preparing(tmp_path: Path) -> None:
    (tmp_path / "report.v1.json").write_text("existing", encoding="utf-8")
    worker = FakeWorker(worker_result())
    runtime = make_runtime(tmp_path, worker=worker)

    with pytest.raises(ValueError, match="already exists"):
        runtime.run_scenario_1()

    assert runtime.record.state is RunState.CONTRACT_READY
    assert runtime.record.attempt_count == 0
    assert worker.calls == []


def test_unreferenced_required_check_must_still_pass(tmp_path: Path) -> None:
    base = task_contract()
    lint_check = RequiredCheck(
        id="lint",
        argv=["python", "-m", "ruff", "check", "src"],
        cwd_relative=".",
        timeout_seconds=60,
        expected_exit_codes=[0],
    )
    contract = TaskContract(
        **{
            **base.model_dump(),
            "required_checks": [*base.required_checks, lint_check],
        }
    )
    clock = StepClock()
    runtime = Runtime(
        run_id="run-extra-check",
        task_contract=contract,
        verification_plan=verification_plan(),
        worker=FakeWorker(worker_result(claimed_checks=("unit-tests", "lint"))),
        check_runner=FakeCheckRunner(
            artifact_directory=tmp_path / "artifacts",
            outcomes={"lint": FakeCheckOutcome(exit_code=1)},
        ),
        event_log=InMemoryEventLog(event_id_factory=id_factory()),
        report_directory=tmp_path,
        clock=clock,
    )

    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    assert runtime.record.state is RunState.CHECKING
    assert runtime.record.criterion_evaluations[0].status is CriterionStatus.SATISFIED
    assert not (tmp_path / "report.v1.json").exists()


def test_observed_scope_violation_blocks_completion(tmp_path: Path) -> None:
    runner = FakeCheckRunner(
        artifact_directory=tmp_path / "artifacts",
        patch_scope_violations=("outside.txt: modify is outside allowed scope",),
    )
    runtime = make_runtime(tmp_path, runner=runner)

    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    assert runtime.record.state is RunState.CHECKING
    assert runtime.record.observed_patch is not None
    assert runtime.record.observed_patch.scope_violations
    assert runtime.record.outcome is None
    assert not (tmp_path / "report.v1.json").exists()


def test_canonical_run_record_is_immutable(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record = runtime.run_scenario_1()

    with pytest.raises(ValidationError):
        record.attempt_count = 2
    with pytest.raises(AttributeError):
        record.observed_checks.clear()

    assert runtime.record.attempt_count == 1
    assert runtime.record.state is RunState.COMPLETED


def test_foreign_run_evidence_is_rejected_before_checking(tmp_path: Path) -> None:
    runner = EvidenceTamperingRunner(
        artifact_directory=tmp_path / "artifacts",
        foreign_run_id="foreign-run",
    )
    runtime = make_runtime(tmp_path, runner=runner)

    with pytest.raises(InvalidTransitionError, match="current run"):
        runtime.run_scenario_1()

    assert runtime.record.state is RunState.COLLECTING_EVIDENCE
    assert not (tmp_path / "report.v1.json").exists()


def test_wrong_attempt_evidence_is_rejected_before_checking(tmp_path: Path) -> None:
    runner = EvidenceTamperingRunner(
        artifact_directory=tmp_path / "artifacts",
        attempt_number_override=2,
    )
    runtime = make_runtime(tmp_path, runner=runner)

    with pytest.raises(InvalidTransitionError, match="current attempt"):
        runtime.run_scenario_1()

    assert runtime.record.state is RunState.COLLECTING_EVIDENCE
    assert not (tmp_path / "report.v1.json").exists()


@pytest.mark.parametrize(
    ("missing_artifact", "expected_message"),
    (
        ("stdout", "stdout artifact"),
        ("stderr", "stderr artifact"),
        ("patch", "patch artifact"),
    ),
)
def test_missing_evidence_artifact_is_rejected(
    tmp_path: Path,
    missing_artifact: str,
    expected_message: str,
) -> None:
    runner = EvidenceTamperingRunner(
        artifact_directory=tmp_path / "artifacts",
        missing_artifact=missing_artifact,
    )
    runtime = make_runtime(tmp_path, runner=runner)

    with pytest.raises(InvalidTransitionError, match=expected_message):
        runtime.run_scenario_1()

    assert runtime.record.state is RunState.COLLECTING_EVIDENCE
    assert not (tmp_path / "report.v1.json").exists()


def test_directory_is_not_accepted_as_evidence_artifact(tmp_path: Path) -> None:
    runner = EvidenceTamperingRunner(
        artifact_directory=tmp_path / "artifacts",
        directory_artifact="stdout",
    )
    runtime = make_runtime(tmp_path, runner=runner)

    with pytest.raises(InvalidTransitionError, match="regular file"):
        runtime.run_scenario_1()

    assert runtime.record.state is RunState.COLLECTING_EVIDENCE


def test_run_record_rejects_outcome_mismatch_with_stop_decision(tmp_path: Path) -> None:
    record = make_runtime(tmp_path).run_scenario_1()
    values = record.model_dump()
    values["outcome"] = Outcome.FAILED

    with pytest.raises(ValidationError, match="outcome must match StopDecision"):
        RunRecord.model_validate(values)


def test_run_record_rejects_stop_reason_mismatch_with_stop_decision(
    tmp_path: Path,
) -> None:
    record = make_runtime(tmp_path).run_scenario_1()
    values = record.model_dump()
    values["stop_reason"] = StopReason.NO_PROGRESS

    with pytest.raises(ValidationError, match="stop reason must match StopDecision"):
        RunRecord.model_validate(values)


def test_completed_run_record_requires_complete_next_action(tmp_path: Path) -> None:
    record = make_runtime(tmp_path).run_scenario_1()
    assert record.stop_decision is not None
    invalid_decision = record.stop_decision.model_copy(
        update={"next_action": NextAction.FAIL}
    )
    values = {
        field_name: getattr(record, field_name)
        for field_name in type(record).model_fields
    }
    values["stop_decision"] = invalid_decision

    with pytest.raises(
        ValidationError,
        match="next_action=complete|fail action requires",
    ):
        RunRecord(**values)


@pytest.mark.parametrize(
    ("decision_update", "expected_message"),
    (
        ({"repairable": True}, "repairable=false"),
        ({"owner_input_required": True}, "owner_input_required|wait_owner"),
    ),
)
def test_completed_run_record_rejects_nonterminal_decision_flags(
    tmp_path: Path,
    decision_update: dict[str, bool],
    expected_message: str,
) -> None:
    record = make_runtime(tmp_path).run_scenario_1()
    assert record.stop_decision is not None
    invalid_decision = record.stop_decision.model_copy(update=decision_update)
    values = {
        field_name: getattr(record, field_name)
        for field_name in type(record).model_fields
    }
    values["stop_decision"] = invalid_decision

    with pytest.raises(ValidationError, match=expected_message):
        RunRecord(**values)


def test_report_builder_rejects_non_completed_state(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    with pytest.raises(ValueError, match="COMPLETED state"):
        build_terminal_report(runtime.record, generated_at=StepClock()())


def test_report_builder_revalidates_model_copy_stop_reason(tmp_path: Path) -> None:
    record = make_runtime(tmp_path).run_scenario_1()
    corrupted = record.model_copy(update={"stop_reason": StopReason.NO_PROGRESS})

    with pytest.raises(ValidationError, match="stop reason must match StopDecision"):
        build_terminal_report(corrupted, generated_at=StepClock()())


def test_report_builder_revalidates_model_copy_outcome(tmp_path: Path) -> None:
    record = make_runtime(tmp_path).run_scenario_1()
    corrupted = record.model_copy(update={"outcome": Outcome.FAILED})

    with pytest.raises(ValidationError, match="outcome must match StopDecision"):
        build_terminal_report(corrupted, generated_at=StepClock()())


def test_runtime_clock_is_single_authority_for_terminal_chronology(
    tmp_path: Path,
) -> None:
    clock = StepClock(datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc))
    runtime = Runtime(
        run_id="run-clock",
        task_contract=task_contract(),
        verification_plan=verification_plan(),
        worker=FakeWorker(worker_result()),
        check_runner=FakeCheckRunner(artifact_directory=tmp_path / "artifacts"),
        event_log=InMemoryEventLog(event_id_factory=id_factory()),
        report_directory=tmp_path,
        clock=clock,
    )

    record = runtime.run_scenario_1()
    run_completed = next(
        event
        for event in runtime.events
        if event.event_type is EvaluationEventType.RUN_COMPLETED
    )
    report_generated = next(
        event
        for event in runtime.events
        if event.event_type is EvaluationEventType.REPORT_GENERATED
    )

    report = TerminalReport.model_validate_json(
        (tmp_path / "report.v1.json").read_text(encoding="utf-8")
    )
    assert record.completed_at is not None
    assert (
        record.completed_at
        <= run_completed.timestamp
        <= report.generated_at
        <= report_generated.timestamp
    )
    assert tuple(event.timestamp for event in runtime.events) == tuple(
        sorted(event.timestamp for event in runtime.events)
    )


def test_event_log_rejects_independent_clock_injection() -> None:
    with pytest.raises(TypeError):
        InMemoryEventLog(  # type: ignore[call-arg]
            clock=StepClock(),
            event_id_factory=id_factory(),
        )
