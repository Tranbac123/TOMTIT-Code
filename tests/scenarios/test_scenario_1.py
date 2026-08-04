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
    Finding,
    RequiredCheck,
    ScopeRule,
    StopDecision,
    TaskContract,
    VerificationPlan,
    WorkerResult,
)
from tomtit.domain.enums import (
    CriterionStatus,
    EvaluationEventType,
    FindingSeverity,
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
    CompletionInvariantError,
    ObservedCheckResult,
    ObservedPatchEvidence,
    RunRecord,
    validate_completed_record,
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

    # F-004: the detached snapshot is now deeply immutable, so the journal is
    # protected by rejection rather than only by copying. The declared payload type
    # is a read-only Mapping, so this assignment is also a static-analysis error.
    with pytest.raises(TypeError):
        first_snapshot[transition_index].payload["to_state"] = "CORRUPTED"  # type: ignore[index]

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
    # SOL-H01 ordering: the report object is prepared while completion is still
    # provisional, so its generated_at precedes the RUN_COMPLETED event that
    # records the committed transition. All four stamps still come from the single
    # Runtime clock and remain monotonic.
    assert (
        record.completed_at
        <= report.generated_at
        <= run_completed.timestamp
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


# ---------------------------------------------------------------------------
# F-002 — the terminal-report trust boundary re-proves completion invariants.
# Every case below tampers with an already COMPLETED record through unsafe
# model_copy(update=...) and asserts build_terminal_report() refuses it.
# ---------------------------------------------------------------------------


def completed_record(tmp_path: Path, *, worker: FakeWorker | None = None) -> RunRecord:
    return make_runtime(tmp_path, worker=worker).run_scenario_1()


def with_checks(record: RunRecord, *checks: ObservedCheckResult) -> RunRecord:
    return record.model_copy(update={"observed_checks": checks})


def with_patch(record: RunRecord, **updates: object) -> RunRecord:
    assert record.observed_patch is not None
    return record.model_copy(
        update={"observed_patch": record.observed_patch.model_copy(update=updates)}
    )


def replace_with_directory(raw_path: str) -> None:
    path = Path(raw_path)
    path.unlink()
    path.mkdir()


def test_report_rejects_check_result_downgraded_to_failed(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    tampered = with_checks(
        record,
        record.observed_checks[0].model_copy(update={"exit_code": 1}),
    )

    with pytest.raises(ValidationError, match="not an expected exit code"):
        build_terminal_report(tampered, generated_at=StepClock()())


def test_report_rejects_missing_required_check_result(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    tampered = with_checks(record)

    with pytest.raises(ValidationError, match=r"missing=\['unit-tests'\]"):
        build_terminal_report(tampered, generated_at=StepClock()())


def test_report_rejects_duplicate_results_for_one_required_check(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    tampered = with_checks(record, record.observed_checks[0], record.observed_checks[0])

    with pytest.raises(ValidationError, match="has 2 observed results, expected 1"):
        build_terminal_report(tampered, generated_at=StepClock()())


def test_report_rejects_foreign_run_check_evidence(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    tampered = with_checks(
        record,
        record.observed_checks[0].model_copy(update={"run_id": "foreign-run"}),
    )

    with pytest.raises(ValidationError, match="belong to the canonical run"):
        build_terminal_report(tampered, generated_at=StepClock()())


def test_report_rejects_wrong_attempt_check_evidence(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    tampered = with_checks(
        record,
        record.observed_checks[0].model_copy(update={"attempt_number": 2}),
    )

    with pytest.raises(ValidationError, match="belong to canonical attempt"):
        build_terminal_report(tampered, generated_at=StepClock()())


def test_report_rejects_deleted_stdout_artifact(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    Path(record.observed_checks[0].stdout_path).unlink()

    with pytest.raises(CompletionInvariantError, match="stdout artifact"):
        build_terminal_report(record, generated_at=StepClock()())


def test_report_rejects_deleted_stderr_artifact(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    Path(record.observed_checks[0].stderr_path).unlink()

    with pytest.raises(CompletionInvariantError, match="stderr artifact"):
        build_terminal_report(record, generated_at=StepClock()())


def test_report_rejects_directory_as_stdout_artifact(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    replace_with_directory(record.observed_checks[0].stdout_path)

    with pytest.raises(CompletionInvariantError, match="stdout artifact.*regular file"):
        build_terminal_report(record, generated_at=StepClock()())


def test_report_rejects_deleted_patch_artifact(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    assert record.observed_patch is not None
    Path(record.observed_patch.artifact_path).unlink()

    with pytest.raises(CompletionInvariantError, match="patch artifact"):
        build_terminal_report(record, generated_at=StepClock()())


def test_report_rejects_directory_as_patch_artifact(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    assert record.observed_patch is not None
    replace_with_directory(record.observed_patch.artifact_path)

    with pytest.raises(CompletionInvariantError, match="patch artifact.*regular file"):
        build_terminal_report(record, generated_at=StepClock()())


def test_report_rejects_incomplete_patch_evidence(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    tampered = with_patch(record, evidence_complete=False)

    with pytest.raises(ValidationError, match="evidence_complete"):
        build_terminal_report(tampered, generated_at=StepClock()())


def test_report_rejects_patch_evidence_without_diff(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    tampered = with_patch(record, diff_exists=False)

    with pytest.raises(ValidationError, match="diff exists"):
        build_terminal_report(tampered, generated_at=StepClock()())


def test_report_rejects_patch_evidence_with_scope_violation(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    tampered = with_patch(record, scope_violations=("outside.txt: modify is out of scope",))

    with pytest.raises(ValidationError, match="without scope violations"):
        build_terminal_report(tampered, generated_at=StepClock()())


def test_report_rejects_unresolved_blocking_finding(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    tampered = record.model_copy(
        update={
            "unresolved_findings": (
                Finding(
                    finding_id="finding-1",
                    fingerprint="scope:src/fixture.py:tests-pass",
                    severity=FindingSeverity.BLOCKING,
                    category="correctness",
                    message="unresolved blocking defect",
                ),
            )
        }
    )

    with pytest.raises(ValidationError, match="no unresolved blocking finding"):
        build_terminal_report(tampered, generated_at=StepClock()())


def test_report_rejects_unsatisfied_deterministic_criterion(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    tampered = record.model_copy(
        update={
            "criterion_evaluations": (
                record.criterion_evaluations[0].model_copy(
                    update={"status": CriterionStatus.UNSATISFIED}
                ),
            )
        }
    )

    with pytest.raises(ValidationError, match="all criteria satisfied"):
        build_terminal_report(tampered, generated_at=StepClock()())


def test_report_rejects_absent_required_check_despite_worker_claim(tmp_path: Path) -> None:
    record = completed_record(
        tmp_path,
        worker=FakeWorker(worker_result(claimed_checks=("unit-tests",))),
    )
    assert record.worker_result is not None
    assert record.worker_result.claimed_checks == ("unit-tests",)

    tampered = with_checks(record)

    with pytest.raises(ValidationError, match=r"missing=\['unit-tests'\]"):
        build_terminal_report(tampered, generated_at=StepClock()())


def test_untampered_completed_record_still_builds_immutable_report(tmp_path: Path) -> None:
    record = completed_record(tmp_path)

    report = build_terminal_report(record, generated_at=StepClock()())

    assert report.schema_version == "report.v1"
    assert report.outcome is Outcome.COMPLETED
    assert report.stop_reason is StopReason.ACCEPTANCE_SATISFIED
    assert report.completion_basis is CompletionBasis.DETERMINISTIC_EVIDENCE
    assert report.attempts == 1
    assert report.checks_executed == ("unit-tests",)
    assert report.changed_files == ("src/fixture.py",)
    assert report.acceptance_criteria[0].status is CriterionStatus.SATISFIED
    assert report.blocking_findings == ()
    assert report.scope_violations == ()

    with pytest.raises(ValidationError):
        report.attempts = 2
    with pytest.raises(AttributeError):
        report.checks_executed.append("injected")  # type: ignore[attr-defined]


class ArtifactDeletingClock(StepClock):
    """Deletes an evidence artifact at a chosen point in the completion sequence.

    Runtime owns the only clock, so a clock hook is the narrowest way to make an
    artifact disappear at an exact step without adding production-only callbacks.
    """

    def __init__(
        self,
        runtime_holder: list[Runtime],
        *,
        when: Callable[[RunRecord], bool] | None = None,
    ) -> None:
        super().__init__()
        self._runtime_holder = runtime_holder
        self._when = when or (
            lambda record: record.state is RunState.CHECKING
            and record.stop_decision is not None
        )
        self.deleted: Path | None = None

    def __call__(self) -> datetime:
        if self._runtime_holder and self.deleted is None:
            record = self._runtime_holder[0].record
            if record.observed_checks and self._when(record):
                path = Path(record.observed_checks[0].stdout_path)
                path.unlink()
                self.deleted = path
        return super().__call__()


def runtime_with_clock(directory: Path, clock: Callable[[], datetime]) -> Runtime:
    return Runtime(
        run_id="run-ordering",
        task_contract=task_contract(),
        verification_plan=verification_plan(),
        worker=FakeWorker(worker_result()),
        check_runner=FakeCheckRunner(artifact_directory=directory / "artifacts"),
        event_log=InMemoryEventLog(event_id_factory=id_factory()),
        report_directory=directory,
        clock=clock,
    )


def event_type_count(runtime: Runtime, event_type: EvaluationEventType) -> int:
    return sum(1 for event in runtime.events if event.event_type is event_type)


def test_runtime_and_report_boundary_share_one_completion_gate(tmp_path: Path) -> None:
    runtime_directory = tmp_path / "runtime"
    runtime_directory.mkdir()
    holder: list[Runtime] = []
    clock = ArtifactDeletingClock(holder)
    runtime = Runtime(
        run_id="run-shared-gate",
        task_contract=task_contract(),
        verification_plan=verification_plan(),
        worker=FakeWorker(worker_result()),
        check_runner=FakeCheckRunner(artifact_directory=runtime_directory / "artifacts"),
        event_log=InMemoryEventLog(event_id_factory=id_factory()),
        report_directory=runtime_directory,
        clock=clock,
    )
    holder.append(runtime)

    with pytest.raises(CompletionInvariantError) as runtime_rejection:
        runtime.run_scenario_1()

    assert clock.deleted is not None
    assert runtime.record.state is RunState.CHECKING
    assert runtime.record.outcome is None
    assert not (runtime_directory / "report.v1.json").exists()

    report_directory = tmp_path / "report"
    report_directory.mkdir()
    record = completed_record(report_directory)
    Path(record.observed_checks[0].stdout_path).unlink()

    with pytest.raises(CompletionInvariantError) as report_rejection:
        build_terminal_report(record, generated_at=StepClock()())

    with pytest.raises(CompletionInvariantError) as gate_rejection:
        validate_completed_record(record)

    # Same tampering, same gate, same rejection at every authority boundary.
    assert str(runtime_rejection.value) == str(report_rejection.value)
    assert str(report_rejection.value) == str(gate_rejection.value)


# ---------------------------------------------------------------------------
# F-003 — Attempt 1 can never be canonicalized as NO_PROGRESS.
# ---------------------------------------------------------------------------


def no_progress_record(*, attempt_count: int) -> RunRecord:
    """Build a terminal NO_PROGRESS record with typed keywords, not dict expansion."""

    started_at = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    return RunRecord(
        run_id="run-no-progress",
        task_contract=task_contract(),
        verification_plan=verification_plan(),
        state=RunState.FAILED,
        attempt_count=attempt_count,
        stop_decision=StopDecision(
            outcome=Outcome.FAILED,
            stop_reason=StopReason.NO_PROGRESS,
            repairable=False,
            owner_input_required=False,
            next_action=NextAction.FAIL,
        ),
        outcome=Outcome.FAILED,
        stop_reason=StopReason.NO_PROGRESS,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=5),
    )


def test_direct_construction_rejects_attempt_one_no_progress() -> None:
    with pytest.raises(ValidationError, match="NO_PROGRESS requires attempt_count >= 2"):
        no_progress_record(attempt_count=1)


def test_revalidation_rejects_copied_attempt_one_no_progress() -> None:
    valid = no_progress_record(attempt_count=2)
    corrupted = valid.model_copy(update={"attempt_count": 1})

    with pytest.raises(ValidationError, match="NO_PROGRESS requires attempt_count >= 2"):
        RunRecord.model_validate(corrupted.model_dump(mode="python"))


def test_report_boundary_rejects_copied_attempt_one_no_progress(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    assert record.attempt_count == 1
    corrupted = record.model_copy(
        update={
            "state": RunState.FAILED,
            "outcome": Outcome.FAILED,
            "stop_reason": StopReason.NO_PROGRESS,
            "stop_decision": StopDecision(
                outcome=Outcome.FAILED,
                stop_reason=StopReason.NO_PROGRESS,
                repairable=False,
                owner_input_required=False,
                next_action=NextAction.FAIL,
            ),
        }
    )

    with pytest.raises(ValidationError, match="NO_PROGRESS requires attempt_count >= 2"):
        build_terminal_report(corrupted, generated_at=StepClock()())


# ---------------------------------------------------------------------------
# SOL-H01 — completion preparation must finish before canonical mutation.
# ---------------------------------------------------------------------------


def test_successful_completion_commits_state_events_and_one_report(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    record = runtime.run_scenario_1()

    assert record.state is RunState.COMPLETED
    assert record.outcome is Outcome.COMPLETED
    assert record.stop_reason is StopReason.ACCEPTANCE_SATISFIED
    assert event_type_count(runtime, EvaluationEventType.RUN_COMPLETED) == 1
    assert event_type_count(runtime, EvaluationEventType.REPORT_GENERATED) == 1

    report_path = tmp_path / "report.v1.json"
    assert record.report_path == str(report_path)
    assert report_path.is_file()
    report = TerminalReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.outcome is Outcome.COMPLETED
    with pytest.raises(ValidationError):
        report.attempts = 99


def test_failed_completion_preparation_leaves_no_partial_canonical_state(
    tmp_path: Path,
) -> None:
    holder: list[Runtime] = []
    clock = ArtifactDeletingClock(holder)
    runtime = runtime_with_clock(tmp_path, clock)
    holder.append(runtime)

    with pytest.raises(CompletionInvariantError, match="stdout artifact"):
        runtime.run_scenario_1()

    assert clock.deleted is not None
    assert runtime.record.state is RunState.CHECKING
    assert runtime.record.outcome is None
    assert runtime.record.stop_reason is None
    assert runtime.record.completed_at is None
    assert runtime.record.report_path is None
    assert event_type_count(runtime, EvaluationEventType.RUN_COMPLETED) == 0
    assert event_type_count(runtime, EvaluationEventType.REPORT_GENERATED) == 0
    assert not (tmp_path / "report.v1.json").exists()


def test_completion_does_not_revalidate_artifacts_after_canonical_commit(
    tmp_path: Path,
) -> None:
    """An artifact lost after the commit point must not undo a proven completion.

    Before SOL-H01 this raised from the second, post-commit validation and left
    canonical COMPLETED with a RUN_COMPLETED event and no report.
    """

    holder: list[Runtime] = []
    clock = ArtifactDeletingClock(
        holder,
        when=lambda record: record.state is RunState.COMPLETED,
    )
    runtime = runtime_with_clock(tmp_path, clock)
    holder.append(runtime)

    record = runtime.run_scenario_1()

    assert clock.deleted is not None
    assert not clock.deleted.exists()
    assert record.state is RunState.COMPLETED
    assert event_type_count(runtime, EvaluationEventType.RUN_COMPLETED) == 1
    assert event_type_count(runtime, EvaluationEventType.REPORT_GENERATED) == 1
    assert (tmp_path / "report.v1.json").is_file()


def test_public_report_boundary_still_rejects_untrusted_records(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    Path(record.observed_checks[0].stderr_path).unlink()

    with pytest.raises(CompletionInvariantError, match="stderr artifact"):
        build_terminal_report(record, generated_at=StepClock()())


def test_completion_is_not_reachable_through_the_generic_transition(tmp_path: Path) -> None:
    """From CHECKING the move is otherwise legal, so this exercises the real guard."""

    runner = FakeCheckRunner(
        artifact_directory=tmp_path / "artifacts",
        outcomes={"unit-tests": FakeCheckOutcome(exit_code=1)},
    )
    runtime = make_runtime(tmp_path, runner=runner)
    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()
    assert runtime.record.state is RunState.CHECKING

    with pytest.raises(InvalidTransitionError, match="complete_run"):
        runtime.transition(RunState.COMPLETED, TransitionReason.TERMINATION_DECIDED)

    assert runtime.record.state is RunState.CHECKING
    assert event_type_count(runtime, EvaluationEventType.RUN_COMPLETED) == 0
    assert not (tmp_path / "report.v1.json").exists()


# ---------------------------------------------------------------------------
# SOL-M02 — observed check metadata must match its authoritative RequiredCheck.
# ---------------------------------------------------------------------------


def test_completion_accepts_matching_check_metadata(tmp_path: Path) -> None:
    record = completed_record(tmp_path)
    contract_check = record.task_contract.required_checks[0]

    assert record.observed_checks[0].argv == contract_check.argv
    assert record.observed_checks[0].cwd_relative == contract_check.cwd_relative

    report = build_terminal_report(record, generated_at=StepClock()())

    assert report.observed_evidence[0].argv == contract_check.argv
    assert report.observed_evidence[0].cwd_relative == contract_check.cwd_relative


@pytest.mark.parametrize(
    ("metadata_update", "expected_message"),
    (
        ({"argv": ("totally", "different", "command")}, "does not match the contract argv"),
        ({"cwd_relative": "different-dir"}, "does not match the contract cwd_relative"),
        (
            {"argv": ("other",), "cwd_relative": "elsewhere"},
            "does not match the contract argv",
        ),
    ),
)
def test_completion_rejects_check_metadata_contradicting_the_contract(
    tmp_path: Path,
    metadata_update: dict[str, object],
    expected_message: str,
) -> None:
    record = completed_record(tmp_path)
    tampered = with_checks(
        record,
        record.observed_checks[0].model_copy(update=metadata_update),
    )

    with pytest.raises(ValidationError, match=expected_message):
        validate_completed_record(tampered)
    with pytest.raises(ValidationError, match=expected_message):
        build_terminal_report(tampered, generated_at=StepClock()())


def test_worker_claim_cannot_excuse_contradicting_check_metadata(tmp_path: Path) -> None:
    record = completed_record(
        tmp_path,
        worker=FakeWorker(worker_result(claimed_checks=("unit-tests",))),
    )
    assert record.worker_result is not None
    assert record.worker_result.claimed_checks == ("unit-tests",)

    tampered = with_checks(
        record,
        record.observed_checks[0].model_copy(
            update={"argv": ("python", "-m", "pytest", "tests/other")}
        ),
    )

    with pytest.raises(ValidationError, match="does not match the contract argv"):
        validate_completed_record(tampered)
    with pytest.raises(ValidationError, match="does not match the contract argv"):
        build_terminal_report(tampered, generated_at=StepClock()())


def test_no_progress_is_structurally_allowed_from_attempt_two() -> None:
    """NO_PROGRESS is not rejected merely for existing once a baseline attempt exists.

    This is a structural invariant test only. Scenario 4 no-progress execution and
    ProgressSnapshot comparison remain unimplemented and out of scope.
    """

    record = no_progress_record(attempt_count=2)

    assert record.attempt_count == 2
    assert record.stop_reason is StopReason.NO_PROGRESS
    assert record.state is RunState.FAILED
