"""Gate 2 — Minimal Scenario 2: one bounded revision from failure to completion."""

from __future__ import annotations

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
    WorkerStatus,
)
from tomtit.domain.evaluation import (
    BudgetSnapshot,
    DecisionEventDraft,
    DecisionReasonCode,
    PersistedDecisionEvent,
)
from tomtit.fakes import FakeCheckOutcome, FakeCheckRunner, FakeWorker
from tomtit.runtime import InMemoryEventLog, TransitionReason
from tomtit.runtime.runtime import (
    CompletionConditionsNotMet,
    InvalidTransitionError,
    RevisionNotAllowed,
    Runtime,
)
from tomtit.runtime.state import (
    AttemptEvidence,
    ObservedCheckResult,
    RunRecord,
    attempt_evidence_from_observations,
    objective_improvement_error,
    task_contract_hash,
)

FAILING_CHECK_ID = "unit-tests"


class StepClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._value = start or datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self._value
        self._value += timedelta(seconds=1)
        return value


def id_factory() -> Callable[[], str]:
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"event-{counter:03d}"

    return next_id


def task_contract(*, max_attempts: int = 2) -> TaskContract:
    return TaskContract(
        task_id="task-s2",
        goal="Repair the deterministic fixture within one revision",
        acceptance_criteria=[
            AcceptanceCriterion(
                id="tests-pass",
                description="Required unit tests pass",
                verification_method=VerificationMethod.DETERMINISTIC,
                required_check_ids=[FAILING_CHECK_ID],
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
                id=FAILING_CHECK_ID,
                argv=["python", "-m", "pytest", "tests/unit"],
                cwd_relative=".",
                timeout_seconds=60,
                expected_exit_codes=[0],
            )
        ],
        max_attempts=max_attempts,
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


def worker_result(*, claimed_checks: tuple[str, ...] = ()) -> WorkerResult:
    return WorkerResult(
        status=WorkerStatus.COMPLETED,
        summary="Implemented the fixture change",
        changed_files=["src/fixture.py"],
        commands_executed=[],
        claimed_checks=claimed_checks,
        remaining_risks=[],
        raw_output_path="artifacts/worker-result.json",
    )


def make_runtime(
    tmp_path: Path,
    *,
    runner: FakeCheckRunner | None = None,
    worker: FakeWorker | None = None,
    contract: TaskContract | None = None,
) -> Runtime:
    return Runtime(
        run_id="run-s2",
        task_contract=contract or task_contract(),
        verification_plan=verification_plan(),
        worker=worker or FakeWorker(worker_result()),
        check_runner=runner or failing_first_attempt_runner(tmp_path),
        event_log=InMemoryEventLog(event_id_factory=id_factory()),
        report_directory=tmp_path,
        clock=StepClock(),
    )


def failing_first_attempt_runner(tmp_path: Path) -> FakeCheckRunner:
    """Attempt 1 fails the required check; Attempt 2 passes it."""

    return FakeCheckRunner(
        artifact_directory=tmp_path / "artifacts",
        attempt_outcomes={(1, FAILING_CHECK_ID): FakeCheckOutcome(exit_code=1)},
    )


def always_failing_runner(tmp_path: Path) -> FakeCheckRunner:
    return FakeCheckRunner(
        artifact_directory=tmp_path / "artifacts",
        outcomes={FAILING_CHECK_ID: FakeCheckOutcome(exit_code=1)},
    )


def event_type_count(runtime: Runtime, event_type: EvaluationEventType) -> int:
    return sum(1 for event in runtime.events if event.event_type is event_type)


def drive_to_first_failure(runtime: Runtime) -> None:
    """Run Attempt 1 to its deterministic failure, leaving the run in CHECKING."""

    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()
    assert runtime.record.state is RunState.CHECKING


def assert_no_revision_committed(runtime: Runtime) -> None:
    """The defined failure result for a rejected or failed revision."""

    record = runtime.record
    assert record.state is RunState.CHECKING
    assert record.attempt_count == 1
    assert record.attempt_history == ()
    assert record.observed_checks, "Attempt 1 evidence must remain canonical"
    assert runtime._event_log.decisions == ()
    assert event_type_count(runtime, EvaluationEventType.REVISION_REQUESTED) == 0


# ---------------------------------------------------------------------------
# 14.1 Happy path
# ---------------------------------------------------------------------------


def test_scenario_2_revises_once_and_completes(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    record = runtime.run_scenario_2()

    assert record.state is RunState.COMPLETED
    assert record.outcome is Outcome.COMPLETED
    assert record.stop_reason is StopReason.ACCEPTANCE_SATISFIED
    assert record.attempt_count == 2

    # Exactly one persisted REVISE decision, with Runtime-assigned identity.
    decisions = runtime._event_log.decisions
    assert len(decisions) == 1
    decision = decisions[0]
    assert isinstance(decision, PersistedDecisionEvent)
    assert decision.selected_action is NextAction.REVISE
    assert decision.reason_code is DecisionReasonCode.DETERMINISTIC_REPAIRABLE_CHECK_FAILURE
    assert decision.append_sequence == 1
    assert decision.state_before is RunState.CHECKING
    assert decision.state_after is RunState.REVISING
    assert decision.attempt_number == 1
    assert decision.task_contract_hash == task_contract_hash(record.task_contract)

    # Exactly one revision transition, and the frozen completion path still ran.
    assert event_type_count(runtime, EvaluationEventType.REVISION_REQUESTED) == 1
    assert event_type_count(runtime, EvaluationEventType.RUN_COMPLETED) == 1
    assert event_type_count(runtime, EvaluationEventType.REPORT_GENERATED) == 1
    assert (tmp_path / "report.v1.json").is_file()
    assert record.report_path == str(tmp_path / "report.v1.json")

    # Attempt 1 failed, Attempt 2 passed, both from observed evidence.
    assert len(record.attempt_history) == 1
    attempt_1 = record.attempt_history[0]
    assert attempt_1.attempt_number == 1
    assert attempt_1.failed_check_ids == (FAILING_CHECK_ID,)
    assert record.criterion_evaluations[0].status is CriterionStatus.SATISFIED
    assert runtime.record.observed_checks[0].exit_code == 0


def test_scenario_2_completes_with_zero_owner_intervention(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    record = runtime.run_scenario_2()

    owner_event_types = {
        EvaluationEventType.WAITING_OWNER,
        EvaluationEventType.OWNER_DECISION_RECORDED,
        EvaluationEventType.CLARIFICATION_REQUESTED,
        EvaluationEventType.CLARIFICATION_RECEIVED,
    }
    observed_types = {event.event_type for event in runtime.events}
    assert observed_types & owner_event_types == set()
    assert record.state is not RunState.WAITING_OWNER
    assert record.stop_decision is not None
    assert record.stop_decision.owner_input_required is False
    human_intervention_count = len(observed_types & owner_event_types)
    assert human_intervention_count == 0


def test_scenario_2_event_order_is_deterministic(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    runtime.run_scenario_2()

    assert tuple(event.event_type for event in runtime.events) == (
        EvaluationEventType.TASK_SUBMITTED,
        EvaluationEventType.CONTRACT_INFERRED,
        EvaluationEventType.RUN_STARTED,
        EvaluationEventType.WORKSPACE_PREPARED,
        EvaluationEventType.WORKER_ASSIGNED,
        EvaluationEventType.WORKER_STARTED,
        EvaluationEventType.WORKER_FINISHED,
        EvaluationEventType.EVIDENCE_COLLECTED,
        EvaluationEventType.CHECK_FAILED,
        EvaluationEventType.REVISION_REQUESTED,
        EvaluationEventType.WORKER_ASSIGNED,
        EvaluationEventType.WORKER_STARTED,
        EvaluationEventType.WORKER_FINISHED,
        EvaluationEventType.EVIDENCE_COLLECTED,
        EvaluationEventType.CHECK_PASSED,
        EvaluationEventType.RUN_COMPLETED,
        EvaluationEventType.REPORT_GENERATED,
    )
    timestamps = tuple(event.timestamp for event in runtime.events)
    assert timestamps == tuple(sorted(timestamps))


# ---------------------------------------------------------------------------
# 14.2 Decision authority
# ---------------------------------------------------------------------------


def test_building_a_proposal_mutates_nothing(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    record_before = runtime.record
    events_before = runtime.events

    draft = runtime.build_revision_proposal()

    assert isinstance(draft, DecisionEventDraft)
    assert not isinstance(draft, PersistedDecisionEvent)
    assert runtime.record is record_before
    assert runtime.events == events_before
    assert_no_revision_committed(runtime)


def test_proposal_carries_no_runtime_owned_identity_or_sequence(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)

    draft = runtime.build_revision_proposal()

    for runtime_owned in ("append_sequence", "decision_id", "decision_timestamp"):
        assert runtime_owned not in type(draft).model_fields
        assert not hasattr(draft, runtime_owned)


@pytest.mark.parametrize(
    "runtime_owned_field",
    ("append_sequence", "decision_id", "decision_timestamp"),
)
def test_caller_supplied_runtime_owned_fields_are_rejected(
    tmp_path: Path,
    runtime_owned_field: str,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    values = runtime.build_revision_proposal().model_dump()
    values[runtime_owned_field] = 1 if runtime_owned_field == "append_sequence" else "x"

    with pytest.raises(ValidationError):
        DecisionEventDraft(**values)


def test_only_runtime_assigns_append_sequence(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)

    decision = runtime.commit_revision_decision(runtime.build_revision_proposal())

    assert decision.append_sequence == 1
    assert decision.decision_id
    assert decision.decision_timestamp is not None
    assert runtime.record.state is RunState.REVISING
    assert runtime.record.attempt_count == 2


# ---------------------------------------------------------------------------
# 14.3 / 14.4 Atomicity
# ---------------------------------------------------------------------------


def test_decision_append_failure_commits_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)

    def failing_append(prepared: object) -> None:
        raise OSError("decision append failed")

    monkeypatch.setattr(runtime._event_log, "_append_prepared_decision", failing_append)

    with pytest.raises(OSError, match="decision append failed"):
        runtime.commit_revision_decision(runtime.build_revision_proposal())

    assert_no_revision_committed(runtime)


def test_transition_event_failure_commits_no_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)

    def failing_batch(prepared: object) -> None:
        raise OSError("transition event append failed")

    monkeypatch.setattr(runtime._event_log, "_append_prepared_batch", failing_batch)

    with pytest.raises(OSError, match="transition event append failed"):
        runtime.commit_revision_decision(runtime.build_revision_proposal())

    assert_no_revision_committed(runtime)


def test_transition_preparation_failure_commits_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)

    def failing_build(**updates: object) -> RunRecord:
        raise OSError("transition preparation failed")

    monkeypatch.setattr(runtime, "_build_record", failing_build)

    with pytest.raises(OSError, match="transition preparation failed"):
        runtime.commit_revision_decision(runtime.build_revision_proposal())

    assert_no_revision_committed(runtime)


def test_earlier_history_survives_a_failed_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    history_before = list(runtime._event_log._serialized_events)

    def failing_append(prepared: object) -> None:
        raise OSError("decision append failed")

    monkeypatch.setattr(runtime._event_log, "_append_prepared_decision", failing_append)
    with pytest.raises(OSError):
        runtime.commit_revision_decision(runtime.build_revision_proposal())

    assert runtime._event_log._serialized_events == history_before


# ---------------------------------------------------------------------------
# 14.5 No duplicates after retry
# ---------------------------------------------------------------------------


def test_retry_after_failed_revision_commits_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)

    def failing_append(prepared: object) -> None:
        raise OSError("decision append failed")

    monkeypatch.setattr(runtime._event_log, "_append_prepared_decision", failing_append)
    with pytest.raises(OSError):
        runtime.commit_revision_decision(runtime.build_revision_proposal())
    assert_no_revision_committed(runtime)

    monkeypatch.undo()
    decision = runtime.commit_revision_decision(runtime.build_revision_proposal())

    decisions = runtime._event_log.decisions
    assert len(decisions) == 1
    assert decision.append_sequence == 1
    assert [item.append_sequence for item in decisions] == [1]
    assert event_type_count(runtime, EvaluationEventType.REVISION_REQUESTED) == 1
    assert runtime.record.attempt_count == 2


def test_append_sequence_is_unique_and_increasing(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    runtime.commit_revision_decision(runtime.build_revision_proposal())

    sequences = [item.append_sequence for item in runtime._event_log.decisions]

    assert sequences == sorted(set(sequences))
    assert sequences[0] >= 1


# ---------------------------------------------------------------------------
# 14.6 Attempt constraints
# ---------------------------------------------------------------------------


def test_revision_is_rejected_from_an_invalid_state(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.transition(RunState.CONTRACT_READY, TransitionReason.CONTRACT_RESOLVED)

    draft = DecisionEventDraft(
        policy_version="mvp-execution-policy-v0.3",
        task_contract_hash=task_contract_hash(runtime.record.task_contract),
        run_id=runtime.record.run_id,
        attempt_number=1,
        state_before=RunState.CHECKING,
        evidence_snapshot_ids=["check:run-s2:1:unit-tests"],
        allowed_actions=[NextAction.REVISE],
        selected_action=NextAction.REVISE,
        reason_code=DecisionReasonCode.DETERMINISTIC_REPAIRABLE_CHECK_FAILURE,
        budget_snapshot=BudgetSnapshot(
            attempt_number=1,
            max_attempts=2,
            hard_max_attempts=3,
            time_budget_seconds=120,
        ),
        state_after=RunState.REVISING,
    )

    with pytest.raises(RevisionNotAllowed, match="requires state CHECKING"):
        runtime.commit_revision_decision(draft)

    assert runtime._event_log.decisions == ()
    assert runtime.record.state is RunState.CONTRACT_READY


def test_second_revision_is_rejected_when_the_budget_is_exhausted(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, runner=always_failing_runner(tmp_path))
    drive_to_first_failure(runtime)
    runtime.commit_revision_decision(runtime.build_revision_proposal())
    runtime.transition(RunState.IMPLEMENTING, TransitionReason.WORKER_ASSIGNED)
    runtime._execute_attempt()
    assert runtime.record.attempt_count == 2

    with pytest.raises(RevisionNotAllowed, match="attempt budget exhausted"):
        runtime.commit_revision_decision(runtime.build_revision_proposal())

    assert len(runtime._event_log.decisions) == 1
    assert runtime.record.attempt_count == 2
    assert runtime.record.state is RunState.CHECKING


def test_attempt_three_is_never_created(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, runner=always_failing_runner(tmp_path))
    drive_to_first_failure(runtime)
    runtime.commit_revision_decision(runtime.build_revision_proposal())
    runtime.transition(RunState.IMPLEMENTING, TransitionReason.WORKER_ASSIGNED)
    runtime._execute_attempt()

    with pytest.raises(RevisionNotAllowed):
        runtime.commit_revision_decision(runtime.build_revision_proposal())

    assert runtime.record.attempt_count == 2
    assert len(runtime.record.attempt_history) == 1
    assert event_type_count(runtime, EvaluationEventType.REVISION_REQUESTED) == 1


def test_revision_requires_a_failed_required_check(tmp_path: Path) -> None:
    runtime = make_runtime(
        tmp_path,
        runner=FakeCheckRunner(artifact_directory=tmp_path / "artifacts"),
    )
    runtime.run_scenario_1()

    # A completed run is terminal; a revision cannot be proposed from it at all.
    with pytest.raises(RevisionNotAllowed, match="requires state CHECKING"):
        runtime.commit_revision_decision(runtime.build_revision_proposal())


# ---------------------------------------------------------------------------
# 14.7 Evidence binding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("update", "expected_message"),
    (
        ({"run_id": "foreign-run"}, "must belong to the current run"),
        ({"evidence_snapshot_ids": ("check:run-s2:2:unit-tests",)}, "proposal evidence"),
        ({"task_contract_hash": "0" * 64}, "task_contract_hash"),
        ({"state_after": RunState.COMPLETED}, "must move the run to REVISING"),
        ({"state_before": RunState.IMPLEMENTING}, "state_before must match"),
    ),
)
def test_invalid_evidence_binding_is_rejected(
    tmp_path: Path,
    update: dict[str, object],
    expected_message: str,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    tampered = runtime.build_revision_proposal().model_copy(update=update)

    with pytest.raises(RevisionNotAllowed, match=expected_message):
        runtime.commit_revision_decision(tampered)

    assert_no_revision_committed(runtime)


def test_wrong_attempt_number_is_rejected(tmp_path: Path) -> None:
    """A self-consistent draft that names the wrong attempt is still refused."""

    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    draft = runtime.build_revision_proposal()
    tampered = draft.model_copy(
        update={
            "attempt_number": 2,
            "budget_snapshot": draft.budget_snapshot.model_copy(
                update={"attempt_number": 2}
            ),
        }
    )

    with pytest.raises(RevisionNotAllowed, match="must belong to the current attempt"):
        runtime.commit_revision_decision(tampered)

    assert_no_revision_committed(runtime)


def test_attempt_2_evidence_cannot_justify_the_attempt_1_decision(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    draft = runtime.build_revision_proposal()
    future_evidence = tuple(
        item.replace(":1:", ":2:").replace(":1", ":2")
        for item in draft.evidence_snapshot_ids
    )
    tampered = draft.model_copy(update={"evidence_snapshot_ids": future_evidence})

    with pytest.raises(RevisionNotAllowed, match="proposal evidence"):
        runtime.commit_revision_decision(tampered)

    assert_no_revision_committed(runtime)


def test_missing_evidence_ids_are_rejected(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    patch_only = tuple(
        item
        for item in runtime.build_revision_proposal().evidence_snapshot_ids
        if item.startswith("patch:")
    )
    tampered = runtime.build_revision_proposal().model_copy(
        update={"evidence_snapshot_ids": patch_only}
    )

    with pytest.raises(RevisionNotAllowed, match="missing="):
        runtime.commit_revision_decision(tampered)

    assert_no_revision_committed(runtime)


def test_stale_attempt_1_evidence_cannot_be_presented_as_attempt_2_evidence(
    tmp_path: Path,
) -> None:
    class StaleEvidenceRunner(FakeCheckRunner):
        def run(
            self,
            check: RequiredCheck,
            run_id: str,
            attempt_number: int,
        ) -> ObservedCheckResult:
            result = super().run(check, run_id, attempt_number)
            if attempt_number == 2:
                # Present Attempt 1 provenance while running Attempt 2.
                return result.model_copy(update={"attempt_number": 1})
            return result

    runtime = make_runtime(
        tmp_path,
        runner=StaleEvidenceRunner(
            artifact_directory=tmp_path / "artifacts",
            attempt_outcomes={(1, FAILING_CHECK_ID): FakeCheckOutcome(exit_code=1)},
        ),
    )

    with pytest.raises(InvalidTransitionError, match="current attempt"):
        runtime.run_scenario_2()

    assert runtime.record.state is RunState.COLLECTING_EVIDENCE
    assert runtime.record.attempt_count == 2
    assert not (tmp_path / "report.v1.json").exists()


def test_attempt_1_evidence_stays_immutable_and_auditable(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    record = runtime.run_scenario_2()

    attempt_1 = record.attempt_history[0]
    attempt_2_ids = {item.snapshot_id for item in record.observed_checks}
    assert set(attempt_1.evidence_snapshot_ids) & attempt_2_ids == set()
    assert attempt_1.observed_checks[0].exit_code == 1
    assert attempt_1.observed_checks[0].attempt_number == 1
    with pytest.raises(ValidationError):
        attempt_1.attempt_number = 2
    with pytest.raises(AttributeError):
        attempt_1.observed_checks.clear()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 14.8 Objective improvement
# ---------------------------------------------------------------------------


def test_completion_is_rejected_when_attempt_2_still_fails(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, runner=always_failing_runner(tmp_path))

    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_2()

    assert runtime.record.state is RunState.CHECKING
    assert runtime.record.attempt_count == 2
    assert runtime.record.outcome is None
    assert not (tmp_path / "report.v1.json").exists()


def test_objective_improvement_requires_a_previously_failed_check(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    attempt_1 = attempt_evidence_from_observations(
        attempt_number=1,
        required_checks=runtime.record.task_contract.required_checks,
        observed_checks=runtime.record.observed_checks,
        observed_patch=runtime.record.observed_patch,
        criterion_evaluations=runtime.record.criterion_evaluations,
    )
    clean_attempt_1 = attempt_1.model_copy(update={"failed_check_ids": ()})
    later_attempt = attempt_1.model_copy(
        update={
            "attempt_number": 2,
            "observed_checks": tuple(
                item.model_copy(update={"attempt_number": 2})
                for item in attempt_1.observed_checks
            ),
            "observed_patch": None,
            "failed_check_ids": (),
        }
    )

    error = objective_improvement_error(clean_attempt_1, later_attempt)

    assert error is not None
    assert "previous attempt with a failed check" in error


def test_completed_record_rejects_a_revision_without_improvement(tmp_path: Path) -> None:
    """The canonical validator, not just the driver, enforces improvement."""

    runtime = make_runtime(tmp_path)
    record = runtime.run_scenario_2()
    attempt_1 = record.attempt_history[0]

    # Claim Attempt 1 already satisfied the same criteria Attempt 2 did.
    no_improvement = attempt_1.model_copy(
        update={
            "satisfied_criterion_ids": tuple(
                item.criterion_id for item in record.criterion_evaluations
            )
        }
    )
    values = record.model_dump(mode="python")
    values["attempt_history"] = [no_improvement.model_dump(mode="python")]

    with pytest.raises(ValidationError, match="did not increase satisfied criteria"):
        RunRecord.model_validate(values)


def test_completed_record_rejects_reused_attempt_evidence(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record = runtime.run_scenario_2()

    # Archive Attempt 2's own evidence as if it were the earlier attempt's.
    reused = AttemptEvidence(
        attempt_number=1,
        observed_checks=tuple(
            item.model_copy(update={"attempt_number": 1}) for item in record.observed_checks
        ),
        observed_patch=None,
        criterion_evaluations=record.criterion_evaluations,
        failed_check_ids=(FAILING_CHECK_ID,),
        satisfied_criterion_ids=(),
    )
    values = record.model_dump(mode="python")
    values["observed_checks"] = [
        item.model_copy(update={"attempt_number": 1}).model_dump(mode="python")
        for item in record.observed_checks
    ]
    values["attempt_count"] = 1
    values["attempt_history"] = [reused.model_dump(mode="python")]

    with pytest.raises(ValidationError):
        RunRecord.model_validate(values)


# ---------------------------------------------------------------------------
# 14.9 Gate 1 preservation
# ---------------------------------------------------------------------------


def test_scenario_1_path_is_unchanged_by_scenario_2_support(tmp_path: Path) -> None:
    """A run with no revision still completes with an empty attempt history."""

    runtime = make_runtime(
        tmp_path,
        runner=FakeCheckRunner(artifact_directory=tmp_path / "artifacts"),
    )

    record = runtime.run_scenario_1()

    assert record.state is RunState.COMPLETED
    assert record.attempt_count == 1
    assert record.attempt_history == ()
    assert runtime._event_log.decisions == ()
    assert event_type_count(runtime, EvaluationEventType.REVISION_REQUESTED) == 0
    assert len(runtime.events) == 11
