"""Gate 2 — Minimal Scenario 2: one bounded revision from failure to completion."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest
from pydantic import ValidationError
from tomtit.domain.contracts import (
    AcceptanceCriterion,
    StopDecision,
    CriterionAssignment,
    RequiredCheck,
    ScopeRule,
    TaskContract,
    VerificationPlan,
    WorkerResult,
)
from tomtit.domain.enums import (
    CriterionStatus,
    FindingSeverity,
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
    TASK_CONTRACT_HASH_SCHEMA_VERSION,
    AttemptEvidence,
    ObservedCheckResult,
    RunRecord,
    attempt_evidence_from_observations,
    objective_improvement_error,
    task_contract_hash,
    validate_completed_record,
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
    """Run Attempt 1 to its deterministic failure and record the repairable branch.

    This mirrors what `run_scenario_2` does internally: the canonical repairable
    `StopDecision` and the one canonical `Finding` must exist before a revision
    can be proposed at all.
    """

    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()
    assert runtime.record.state is RunState.CHECKING
    runtime.record_repairable_failure()


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


def drive_to_exhausted_budget(tmp_path: Path) -> Runtime:
    """Reach Attempt 2 CHECKING with both attempts failed and no budget left."""

    runtime = make_runtime(tmp_path, runner=always_failing_runner(tmp_path))
    drive_to_first_failure(runtime)
    runtime.commit_revision_decision(runtime.build_revision_proposal())
    runtime.transition(RunState.IMPLEMENTING, TransitionReason.WORKER_ASSIGNED)
    runtime._execute_attempt()
    assert runtime.record.attempt_count == 2
    return runtime


def forged_revise_draft(runtime: Runtime) -> DecisionEventDraft:
    """A REVISE proposal a caller could forge once canonical policy forbids it."""

    record = runtime.record
    contract = record.task_contract
    evidence_ids = [item.snapshot_id for item in record.observed_checks]
    if record.observed_patch is not None:
        evidence_ids.append(record.observed_patch.snapshot_id)
    return DecisionEventDraft(
        policy_version="mvp-execution-policy-v0.3",
        task_contract_hash=task_contract_hash(contract),
        run_id=record.run_id,
        attempt_number=record.attempt_count,
        state_before=record.state,
        evidence_snapshot_ids=tuple(evidence_ids),
        allowed_actions=(NextAction.REVISE, NextAction.FAIL),
        selected_action=NextAction.REVISE,
        reason_code=DecisionReasonCode.DETERMINISTIC_REPAIRABLE_CHECK_FAILURE,
        binding_constraints=(
            f"max_attempts={contract.max_attempts}",
            "deterministic_evidence_required",
        ),
        budget_snapshot=BudgetSnapshot(
            attempt_number=record.attempt_count,
            max_attempts=contract.max_attempts,
            hard_max_attempts=contract.hard_max_attempts,
            time_budget_seconds=contract.time_budget_seconds,
        ),
        state_after=RunState.REVISING,
    )


def test_second_revision_is_rejected_when_the_budget_is_exhausted(tmp_path: Path) -> None:
    runtime = drive_to_exhausted_budget(tmp_path)

    # Canonical policy no longer permits REVISE at all, so the proposal itself
    # cannot name it as the selected action.
    with pytest.raises(ValidationError, match="selected_action must be one of"):
        runtime.build_revision_proposal()

    # A forged REVISE proposal is still refused at the commit boundary.
    with pytest.raises(RevisionNotAllowed, match="attempt budget exhausted"):
        runtime.commit_revision_decision(forged_revise_draft(runtime))

    assert len(runtime._event_log.decisions) == 1
    assert runtime.record.attempt_count == 2
    assert runtime.record.state is RunState.CHECKING


def test_attempt_three_is_never_created(tmp_path: Path) -> None:
    runtime = drive_to_exhausted_budget(tmp_path)

    with pytest.raises(RevisionNotAllowed):
        runtime.commit_revision_decision(forged_revise_draft(runtime))

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
        run_id=runtime.record.run_id,
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
    """A forged satisfied-criteria claim is refused by derivation, not believed.

    Claiming Attempt 1 already satisfied what Attempt 2 satisfied would defeat
    objective improvement. The archive validator recomputes the tuple from the
    archived evaluations first, so the forgery is caught before the improvement
    predicate is even consulted.
    """

    runtime = make_runtime(tmp_path)
    record = runtime.run_scenario_2()
    attempt_1 = record.attempt_history[0]

    no_improvement = attempt_1.model_copy(
        update={
            "satisfied_criterion_ids": tuple(
                item.criterion_id for item in record.criterion_evaluations
            )
        }
    )
    values = record.model_dump(mode="python")
    values["attempt_history"] = [no_improvement.model_dump(mode="python")]

    with pytest.raises(ValidationError, match="satisfied_criterion_ids"):
        RunRecord.model_validate(values)
    corrupted = record.model_copy(update={"attempt_history": (no_improvement,)})
    with pytest.raises(ValidationError, match="satisfied_criterion_ids"):
        validate_completed_record(corrupted)


def test_completed_record_rejects_reused_attempt_evidence(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record = runtime.run_scenario_2()

    # Archive Attempt 2's own evidence as if it were the earlier attempt's.
    reused = AttemptEvidence(
        run_id=record.run_id,
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


# ---------------------------------------------------------------------------
# G2-S2-01 — the public transition API cannot enter REVISING.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    (
        TransitionReason.REVISION_APPROVED,
        TransitionReason.EVIDENCE_READY,
        TransitionReason.TERMINATION_DECIDED,
    ),
)
def test_public_transition_cannot_enter_revising(
    tmp_path: Path,
    reason: TransitionReason,
) -> None:
    """The exact reviewed bypass, refused for every supplied transition reason."""

    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    record_before = runtime.record
    events_before = runtime.events

    with pytest.raises(InvalidTransitionError, match="commit_revision_decision"):
        runtime.transition(RunState.REVISING, reason)

    assert runtime.record is record_before
    assert runtime.record == record_before
    assert runtime.record.state is RunState.CHECKING
    assert runtime.record.attempt_count == 1
    assert runtime.record.attempt_history == ()
    assert runtime.events == events_before
    assert runtime._event_log.decisions == ()
    assert event_type_count(runtime, EvaluationEventType.REVISION_REQUESTED) == 0


def test_revising_remains_reachable_only_through_the_decision_commit(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)

    with pytest.raises(InvalidTransitionError, match="commit_revision_decision"):
        runtime.transition(RunState.REVISING, TransitionReason.REVISION_APPROVED)

    decision = runtime.commit_revision_decision(runtime.build_revision_proposal())

    assert runtime.record.state is RunState.REVISING
    assert runtime.record.attempt_count == 2
    assert decision.append_sequence == 1
    assert len(runtime._event_log.decisions) == 1
    assert event_type_count(runtime, EvaluationEventType.REVISION_REQUESTED) == 1


def test_completed_remains_protected_from_the_public_transition_api(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)

    with pytest.raises(InvalidTransitionError, match="complete_run"):
        runtime.transition(RunState.COMPLETED, TransitionReason.TERMINATION_DECIDED)

    assert runtime.record.state is RunState.CHECKING


# ---------------------------------------------------------------------------
# G2-S2-02 — every decision metadata field is re-derived from Runtime truth.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("update", "expected_message"),
    (
        ({"policy_version": "attacker-policy-v999"}, "policy_version"),
        (
            {"allowed_actions": (NextAction.REVISE, NextAction.COMPLETE)},
            "allowed_actions",
        ),
        ({"binding_constraints": ("max_attempts=999",)}, "binding_constraints"),
    ),
)
def test_false_decision_metadata_is_rejected(
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


@pytest.mark.parametrize(
    "budget_update",
    (
        {"attempt_number": 2},
        {"max_attempts": 3},
        {"hard_max_attempts": 2},
        {"time_budget_seconds": 999},
    ),
)
def test_false_budget_snapshot_is_rejected(
    tmp_path: Path,
    budget_update: dict[str, int],
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    draft = runtime.build_revision_proposal()
    forged_budget = draft.budget_snapshot.model_copy(update=budget_update)
    tampered = draft.model_copy(
        update={
            "budget_snapshot": forged_budget,
            # Keep the draft internally consistent so only the canonical
            # comparison can reject it.
            "attempt_number": forged_budget.attempt_number,
        }
    )

    with pytest.raises(RevisionNotAllowed):
        runtime.commit_revision_decision(tampered)

    assert_no_revision_committed(runtime)


def test_a_valid_proposal_still_commits_after_metadata_rejections(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    for update in (
        {"policy_version": "attacker-policy-v999"},
        {"binding_constraints": ("max_attempts=999",)},
    ):
        with pytest.raises(RevisionNotAllowed):
            runtime.commit_revision_decision(
                runtime.build_revision_proposal().model_copy(update=update)
            )
    assert_no_revision_committed(runtime)

    decision = runtime.commit_revision_decision(runtime.build_revision_proposal())

    assert decision.append_sequence == 1
    assert len(runtime._event_log.decisions) == 1
    assert decision.policy_version == "mvp-execution-policy-v0.3"
    assert decision.allowed_actions == (NextAction.REVISE, NextAction.FAIL)
    assert decision.binding_constraints == (
        "max_attempts=2",
        "deterministic_evidence_required",
    )
    assert decision.budget_snapshot.max_attempts == 2
    assert decision.budget_snapshot.hard_max_attempts == 3
    assert decision.budget_snapshot.time_budget_seconds == 120


# ---------------------------------------------------------------------------
# G2-S2-03 — archived evidence is bound to the canonical run.
# ---------------------------------------------------------------------------


def foreign_run_archive(record: RunRecord) -> dict[str, object]:
    """An Attempt 1 archive rebuilt under a foreign run ID."""

    archived = record.attempt_history[0]
    return archived.model_copy(
        update={
            "run_id": "foreign-run",
            "observed_checks": tuple(
                item.model_copy(update={"run_id": "foreign-run"})
                for item in archived.observed_checks
            ),
            "observed_patch": (
                archived.observed_patch.model_copy(update={"run_id": "foreign-run"})
                if archived.observed_patch is not None
                else None
            ),
        }
    ).model_dump(mode="python")


def test_attempt_evidence_rejects_foreign_run_checks(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record = runtime.run_scenario_2()
    archived = record.attempt_history[0]

    with pytest.raises(ValidationError, match="belong to the archived run"):
        AttemptEvidence(
            **{
                **archived.model_dump(mode="python"),
                "observed_checks": [
                    item.model_copy(update={"run_id": "foreign-run"}).model_dump(
                        mode="python"
                    )
                    for item in archived.observed_checks
                ],
            }
        )


def test_attempt_evidence_rejects_foreign_run_patch(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record = runtime.run_scenario_2()
    archived = record.attempt_history[0]
    assert archived.observed_patch is not None

    with pytest.raises(ValidationError, match="patch must belong to the archived run"):
        AttemptEvidence(
            **{
                **archived.model_dump(mode="python"),
                "observed_patch": archived.observed_patch.model_copy(
                    update={"run_id": "foreign-run"}
                ).model_dump(mode="python"),
            }
        )


def test_completed_record_rejects_foreign_run_attempt_history(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record = runtime.run_scenario_2()
    values = record.model_dump(mode="python")
    values["attempt_history"] = [foreign_run_archive(record)]

    with pytest.raises(ValidationError, match="must belong to the canonical run"):
        RunRecord.model_validate(values)


def test_validate_completed_record_rejects_foreign_run_attempt_history(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    record = runtime.run_scenario_2()
    archived = record.attempt_history[0]
    foreign = AttemptEvidence.model_validate(foreign_run_archive(record))
    corrupted = record.model_copy(update={"attempt_history": (foreign,)})

    with pytest.raises(ValidationError, match="belong to the canonical run"):
        validate_completed_record(corrupted)

    # The genuine archive is still accepted and unchanged.
    assert validate_completed_record(record).attempt_history[0] == archived


def test_evidence_helper_rejects_foreign_run_observations(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    record = runtime.run_scenario_2()
    archived = record.attempt_history[0]

    with pytest.raises(ValueError, match="belong to the canonical run"):
        attempt_evidence_from_observations(
            run_id="foreign-run",
            attempt_number=1,
            required_checks=record.task_contract.required_checks,
            observed_checks=archived.observed_checks,
            observed_patch=archived.observed_patch,
            criterion_evaluations=archived.criterion_evaluations,
        )


def test_gate_1_empty_history_completion_is_unaffected(tmp_path: Path) -> None:
    runtime = make_runtime(
        tmp_path,
        runner=FakeCheckRunner(artifact_directory=tmp_path / "artifacts"),
    )

    record = runtime.run_scenario_1()

    assert record.attempt_history == ()
    assert validate_completed_record(record).attempt_history == ()


# ---------------------------------------------------------------------------
# G2-S2-04 — repairable StopDecision and exactly one canonical Finding.
# ---------------------------------------------------------------------------


def test_attempt_1_failure_produces_the_required_stop_decision_and_finding(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    record = runtime.record

    assert record.stop_decision is not None
    assert record.stop_decision.next_action is NextAction.REVISE
    assert record.stop_decision.repairable is True
    assert record.stop_decision.owner_input_required is False
    assert record.stop_decision.outcome is None
    assert record.stop_decision.stop_reason is None

    assert len(record.unresolved_findings) == 1
    finding = record.unresolved_findings[0]
    assert finding.severity is FindingSeverity.BLOCKING
    assert finding.finding_id == f"finding:{record.run_id}:1:{FAILING_CHECK_ID}"
    assert finding.acceptance_criterion_id == "tests-pass"
    failed_snapshot = record.observed_checks[0].snapshot_id
    assert failed_snapshot in finding.evidence_refs
    assert FAILING_CHECK_ID in finding.message


def test_successful_revision_archives_the_stop_decision_and_finding(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)

    record = runtime.run_scenario_2()

    archived = record.attempt_history[0]
    assert archived.stop_decision is not None
    assert archived.stop_decision.next_action is NextAction.REVISE
    assert archived.stop_decision.repairable is True
    assert len(archived.findings) == 1
    assert archived.findings[0].finding_id == f"finding:{record.run_id}:1:{FAILING_CHECK_ID}"
    assert archived.failed_check_ids == (FAILING_CHECK_ID,)

    # Attempt 2's own decision/finding fields are the completion ones.
    assert record.unresolved_findings == ()
    assert record.stop_decision is not None
    assert record.stop_decision.next_action is NextAction.COMPLETE
    assert record.stop_decision.owner_input_required is False


def test_revision_is_rejected_without_a_recorded_repairable_branch(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()
    draft = runtime.build_revision_proposal()

    with pytest.raises(RevisionNotAllowed, match="requires a StopDecision"):
        runtime.commit_revision_decision(draft)

    assert_no_revision_committed(runtime)


@pytest.mark.parametrize(
    ("decision_update", "expected_message"),
    (
        ({"next_action": NextAction.FAIL}, "next_action=revise"),
        ({"repairable": False}, "repairable=true"),
        ({"owner_input_required": True}, "owner_input_required=false"),
    ),
)
def test_invalid_stop_decision_rejects_revision(
    tmp_path: Path,
    decision_update: dict[str, object],
    expected_message: str,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    draft = runtime.build_revision_proposal()
    assert runtime.record.stop_decision is not None
    runtime._record = runtime.record.model_copy(
        update={
            "stop_decision": runtime.record.stop_decision.model_copy(
                update=decision_update
            )
        }
    )

    with pytest.raises(RevisionNotAllowed, match=expected_message):
        runtime.commit_revision_decision(draft)

    assert runtime._event_log.decisions == ()
    assert runtime.record.attempt_count == 1
    assert runtime.record.attempt_history == ()


def test_terminal_stop_decision_fields_reject_revision(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    draft = runtime.build_revision_proposal()
    assert runtime.record.stop_decision is not None
    runtime._record = runtime.record.model_copy(
        update={
            "stop_decision": runtime.record.stop_decision.model_copy(
                update={
                    "outcome": Outcome.FAILED,
                    "stop_reason": StopReason.VERIFICATION_FAILED,
                }
            )
        }
    )

    with pytest.raises(RevisionNotAllowed, match="must not set outcome or stop reason"):
        runtime.commit_revision_decision(draft)

    assert runtime._event_log.decisions == ()


@pytest.mark.parametrize("finding_count", (0, 2))
def test_wrong_finding_count_rejects_revision(
    tmp_path: Path,
    finding_count: int,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    draft = runtime.build_revision_proposal()
    canonical = runtime.record.unresolved_findings[0]
    findings = () if finding_count == 0 else (canonical, canonical.model_copy(
        update={"finding_id": "finding:other"}
    ))
    runtime._record = runtime.record.model_copy(update={"unresolved_findings": findings})

    with pytest.raises(RevisionNotAllowed, match="exactly one repairable finding"):
        runtime.commit_revision_decision(draft)

    assert runtime._event_log.decisions == ()
    assert runtime.record.attempt_count == 1


@pytest.mark.parametrize(
    "finding_update",
    (
        {"finding_id": "finding:foreign-run:1:unit-tests"},
        {"finding_id": "finding:run-s2:2:unit-tests"},
        {"evidence_refs": ("check:run-s2:2:unit-tests",)},
        {"severity": FindingSeverity.WARNING},
    ),
)
def test_non_canonical_finding_rejects_revision(
    tmp_path: Path,
    finding_update: dict[str, object],
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    draft = runtime.build_revision_proposal()
    tampered = runtime.record.unresolved_findings[0].model_copy(update=finding_update)
    runtime._record = runtime.record.model_copy(update={"unresolved_findings": (tampered,)})

    with pytest.raises(RevisionNotAllowed, match="canonical repairable finding"):
        runtime.commit_revision_decision(draft)

    assert runtime._event_log.decisions == ()
    assert runtime.record.attempt_count == 1
    assert runtime.record.attempt_history == ()


def test_recording_a_repairable_failure_requires_a_failed_check(tmp_path: Path) -> None:
    runtime = make_runtime(
        tmp_path,
        runner=FakeCheckRunner(artifact_directory=tmp_path / "artifacts"),
    )
    runtime.transition(RunState.CONTRACT_READY, TransitionReason.CONTRACT_RESOLVED)
    runtime.transition(RunState.PREPARING, TransitionReason.WORKSPACE_PREPARED)
    runtime.start_attempt()
    runtime._execute_attempt()

    with pytest.raises(RevisionNotAllowed, match="at least one failed required check"):
        runtime.record_repairable_failure()


def test_repairable_failure_cannot_be_recorded_outside_checking(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.transition(RunState.CONTRACT_READY, TransitionReason.CONTRACT_RESOLVED)

    with pytest.raises(InvalidTransitionError, match="only be recorded from CHECKING"):
        runtime.record_repairable_failure()


def test_archive_decision_and_transition_roll_back_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    stop_decision_before = runtime.record.stop_decision
    findings_before = runtime.record.unresolved_findings

    def failing_append(prepared: object) -> None:
        raise OSError("decision append failed")

    monkeypatch.setattr(runtime._event_log, "_append_prepared_decision", failing_append)
    with pytest.raises(OSError):
        runtime.commit_revision_decision(runtime.build_revision_proposal())

    # No archive without a decision, and no decision without an archive.
    assert runtime.record.attempt_history == ()
    assert runtime._event_log.decisions == ()
    assert runtime.record.attempt_count == 1
    assert runtime.record.stop_decision == stop_decision_before
    assert runtime.record.unresolved_findings == findings_before
    assert event_type_count(runtime, EvaluationEventType.REVISION_REQUESTED) == 0

    monkeypatch.undo()
    decision = runtime.commit_revision_decision(runtime.build_revision_proposal())

    assert decision.append_sequence == 1
    assert len(runtime.record.attempt_history) == 1
    assert runtime.record.attempt_history[0].stop_decision == stop_decision_before
    assert runtime.record.attempt_history[0].findings == findings_before


# ---------------------------------------------------------------------------
# G2-S2-05 — the contract hash names its own schema version.
# ---------------------------------------------------------------------------


def test_contract_hash_includes_an_explicit_schema_version() -> None:
    contract = task_contract()
    expected_envelope = {
        "schema_version": TASK_CONTRACT_HASH_SCHEMA_VERSION,
        "task_contract": json.loads(contract.model_dump_json()),
    }
    expected = hashlib.sha256(
        json.dumps(
            expected_envelope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    assert task_contract_hash(contract) == expected

    unversioned = hashlib.sha256(
        json.dumps(
            json.loads(contract.model_dump_json()),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert task_contract_hash(contract) != unversioned


def test_contract_hash_is_stable_and_semantically_sensitive() -> None:
    assert task_contract_hash(task_contract()) == task_contract_hash(task_contract())
    changed = task_contract().model_copy(update={"goal": "a different goal"})
    assert task_contract_hash(changed) != task_contract_hash(task_contract())


# ---------------------------------------------------------------------------
# G2-S2-04A — the repairable branch writer honours the canonical attempt budget.
# ---------------------------------------------------------------------------


def test_canonical_allowed_actions_drop_revise_at_the_budget_limit(
    tmp_path: Path,
) -> None:
    runtime = drive_to_exhausted_budget(tmp_path)

    metadata = runtime._canonical_revision_metadata()

    assert metadata.allowed_actions == (NextAction.FAIL,)
    assert NextAction.REVISE not in metadata.allowed_actions


def test_record_repairable_failure_is_refused_at_the_exhausted_budget(
    tmp_path: Path,
) -> None:
    """The exact reviewed defect: a REVISE branch written when policy allows FAIL."""

    runtime = drive_to_exhausted_budget(tmp_path)
    record_before = runtime.record
    events_before = runtime.events
    decisions_before = runtime._event_log.decisions

    with pytest.raises(RevisionNotAllowed, match="canonical allowed actions"):
        runtime.record_repairable_failure()

    assert runtime.record is record_before
    assert runtime.record == record_before
    assert runtime.record.stop_decision is None
    assert runtime.record.unresolved_findings == ()
    assert runtime.record.attempt_count == 2
    assert len(runtime.record.attempt_history) == 1
    assert runtime.events == events_before
    assert runtime._event_log.decisions == decisions_before
    assert len(runtime._event_log.decisions) == 1
    assert event_type_count(runtime, EvaluationEventType.REVISION_REQUESTED) == 1


def test_no_revise_stop_decision_can_exist_at_the_exhausted_budget(
    tmp_path: Path,
) -> None:
    runtime = drive_to_exhausted_budget(tmp_path)

    with pytest.raises(RevisionNotAllowed):
        runtime.record_repairable_failure()

    assert runtime.record.stop_decision is None
    assert runtime.record.unresolved_findings == ()
    # A forged REVISE proposal is still refused at the commit boundary too.
    with pytest.raises(RevisionNotAllowed, match="attempt budget exhausted"):
        runtime.commit_revision_decision(forged_revise_draft(runtime))
    assert runtime.record.attempt_count == 2
    assert len(runtime._event_log.decisions) == 1


def test_attempt_1_repairable_recording_still_succeeds(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    record = runtime.record_repairable_failure()

    assert record.stop_decision is not None
    assert record.stop_decision.next_action is NextAction.REVISE
    assert len(record.unresolved_findings) == 1
    assert record.attempt_count == 1
    assert record.state is RunState.CHECKING


def test_repeated_valid_repairable_recording_is_value_idempotent(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    with pytest.raises(CompletionConditionsNotMet):
        runtime.run_scenario_1()

    first = runtime.record_repairable_failure()
    events_after_first = runtime.events
    second = runtime.record_repairable_failure()

    assert second.stop_decision == first.stop_decision
    assert second.unresolved_findings == first.unresolved_findings
    assert len(second.unresolved_findings) == 1
    assert runtime.events == events_after_first
    assert runtime._event_log.decisions == ()
    assert second.attempt_count == 1


def test_happy_path_is_unchanged_by_the_budget_gate(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    record = runtime.run_scenario_2()

    assert record.state is RunState.COMPLETED
    assert record.attempt_count == 2
    assert len(runtime._event_log.decisions) == 1
    assert event_type_count(runtime, EvaluationEventType.REVISION_REQUESTED) == 1
    assert (tmp_path / "report.v1.json").is_file()


# ---------------------------------------------------------------------------
# G2-S2-04B — a completed archive is re-proven, never believed.
# ---------------------------------------------------------------------------


def completed_revised_record(tmp_path: Path) -> RunRecord:
    return make_runtime(tmp_path).run_scenario_2()


def with_archive(record: RunRecord, archive: AttemptEvidence) -> RunRecord:
    """An unsafely copied completed record carrying a tampered archive."""

    return record.model_copy(update={"attempt_history": (archive,)})


def assert_both_boundaries_reject(record: RunRecord, archive: AttemptEvidence) -> None:
    """Both the model boundary and the completion gate must refuse the archive."""

    values = record.model_dump(mode="python")
    values["attempt_history"] = [archive.model_dump(mode="python")]
    with pytest.raises(ValidationError):
        RunRecord.model_validate(values)
    with pytest.raises(ValidationError):
        validate_completed_record(with_archive(record, archive))


def test_genuine_completed_revised_record_is_accepted(tmp_path: Path) -> None:
    record = completed_revised_record(tmp_path)

    revalidated = RunRecord.model_validate(record.model_dump(mode="python"))
    gated = validate_completed_record(record)

    assert revalidated.attempt_history == record.attempt_history
    assert gated.attempt_history == record.attempt_history
    assert gated.state is RunState.COMPLETED


@pytest.mark.parametrize(
    "decision_update",
    (
        None,
        {"next_action": NextAction.FAIL},
        {"repairable": False},
        {"owner_input_required": True},
    ),
)
def test_archived_stop_decision_is_reproven(
    tmp_path: Path,
    decision_update: dict[str, object] | None,
) -> None:
    record = completed_revised_record(tmp_path)
    archive = record.attempt_history[0]
    assert archive.stop_decision is not None
    tampered_decision = (
        None
        if decision_update is None
        else archive.stop_decision.model_copy(update=decision_update)
    )

    assert_both_boundaries_reject(
        record, archive.model_copy(update={"stop_decision": tampered_decision})
    )


@pytest.mark.parametrize(
    "terminal_update",
    (
        {"outcome": Outcome.FAILED},
        {"stop_reason": StopReason.VERIFICATION_FAILED},
    ),
)
def test_archived_stop_decision_rejects_terminal_fields(
    tmp_path: Path,
    terminal_update: dict[str, object],
) -> None:
    record = completed_revised_record(tmp_path)
    archive = record.attempt_history[0]
    assert archive.stop_decision is not None
    # StopDecision itself forbids a bare outcome without a matching action, so
    # build the contradictory pair the archive must refuse.
    forged = StopDecision(
        outcome=terminal_update.get("outcome", Outcome.FAILED),
        stop_reason=terminal_update.get("stop_reason", StopReason.VERIFICATION_FAILED),
        repairable=True,
        owner_input_required=False,
        next_action=NextAction.FAIL,
    )

    assert_both_boundaries_reject(record, archive.model_copy(update={"stop_decision": forged}))


@pytest.mark.parametrize("finding_count", (0, 2))
def test_archived_finding_count_is_reproven(
    tmp_path: Path,
    finding_count: int,
) -> None:
    record = completed_revised_record(tmp_path)
    archive = record.attempt_history[0]
    canonical = archive.findings[0]
    findings = () if finding_count == 0 else (canonical, canonical)

    assert_both_boundaries_reject(record, archive.model_copy(update={"findings": findings}))


@pytest.mark.parametrize(
    "finding_update",
    (
        {"finding_id": "finding:forged"},
        {"finding_id": "finding:foreign-run:1:unit-tests"},
        {"finding_id": "finding:run-s2:2:unit-tests"},
        {"evidence_refs": ("check:run-s2:2:unit-tests",)},
        {"severity": FindingSeverity.WARNING},
        {"acceptance_criterion_id": None},
        {"fingerprint": "deterministic_check_failure:other-check"},
    ),
)
def test_archived_finding_is_reproven_field_by_field(
    tmp_path: Path,
    finding_update: dict[str, object],
) -> None:
    record = completed_revised_record(tmp_path)
    archive = record.attempt_history[0]
    tampered = archive.findings[0].model_copy(update=finding_update)

    assert_both_boundaries_reject(record, archive.model_copy(update={"findings": (tampered,)}))


def test_archived_failed_check_flipped_to_passing_is_rejected(tmp_path: Path) -> None:
    """The reviewed reproduction: exit_code changed to 0 while still named failed."""

    record = completed_revised_record(tmp_path)
    archive = record.attempt_history[0]
    passing = tuple(
        item.model_copy(update={"exit_code": 0}) for item in archive.observed_checks
    )

    assert_both_boundaries_reject(record, archive.model_copy(update={"observed_checks": passing}))


def test_forged_failed_check_ids_are_rejected(tmp_path: Path) -> None:
    record = completed_revised_record(tmp_path)
    archive = record.attempt_history[0]

    assert_both_boundaries_reject(
        record, archive.model_copy(update={"failed_check_ids": ("unit-tests", "unit-tests")})
    )


def test_missing_genuinely_failed_check_id_is_rejected(tmp_path: Path) -> None:
    record = completed_revised_record(tmp_path)
    archive = record.attempt_history[0]

    assert_both_boundaries_reject(record, archive.model_copy(update={"failed_check_ids": ()}))


def test_forged_satisfied_criterion_ids_are_rejected(tmp_path: Path) -> None:
    record = completed_revised_record(tmp_path)
    archive = record.attempt_history[0]

    assert_both_boundaries_reject(
        record, archive.model_copy(update={"satisfied_criterion_ids": ("tests-pass",)})
    )


def test_archive_from_another_run_is_rejected_at_both_boundaries(tmp_path: Path) -> None:
    record = completed_revised_record(tmp_path)
    archive = record.attempt_history[0]
    foreign = archive.model_copy(
        update={
            "run_id": "foreign-run",
            "observed_checks": tuple(
                item.model_copy(update={"run_id": "foreign-run"})
                for item in archive.observed_checks
            ),
            "observed_patch": (
                archive.observed_patch.model_copy(update={"run_id": "foreign-run"})
                if archive.observed_patch is not None
                else None
            ),
        }
    )

    assert_both_boundaries_reject(record, foreign)


def test_archived_attempt_must_precede_the_current_attempt(tmp_path: Path) -> None:
    record = completed_revised_record(tmp_path)
    archive = record.attempt_history[0]
    later = archive.model_copy(
        update={
            "attempt_number": 2,
            "observed_checks": tuple(
                item.model_copy(update={"attempt_number": 2})
                for item in archive.observed_checks
            ),
            "observed_patch": (
                archive.observed_patch.model_copy(update={"attempt_number": 2})
                if archive.observed_patch is not None
                else None
            ),
        }
    )

    assert_both_boundaries_reject(record, later)


def test_revised_completion_requires_the_bounded_gate_2_shape(tmp_path: Path) -> None:
    record = completed_revised_record(tmp_path)
    archive = record.attempt_history[0]
    values = record.model_dump(mode="python")
    values["attempt_history"] = [
        archive.model_dump(mode="python"),
        archive.model_dump(mode="python"),
    ]

    with pytest.raises(ValidationError):
        RunRecord.model_validate(values)


def test_archive_validation_also_applies_before_completion(tmp_path: Path) -> None:
    """The archive is re-proven at REVISING too, not only at COMPLETED."""

    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    runtime.commit_revision_decision(runtime.build_revision_proposal())
    revising = runtime.record
    assert revising.state is RunState.REVISING

    archive = revising.attempt_history[0]
    values = revising.model_dump(mode="python")
    values["attempt_history"] = [
        archive.model_copy(update={"findings": ()}).model_dump(mode="python")
    ]

    with pytest.raises(ValidationError, match="exactly one repairable finding"):
        RunRecord.model_validate(values)


def test_gate_1_unrevised_completion_needs_no_archive(tmp_path: Path) -> None:
    runtime = make_runtime(
        tmp_path,
        runner=FakeCheckRunner(artifact_directory=tmp_path / "artifacts"),
    )

    record = runtime.run_scenario_1()

    assert record.attempt_history == ()
    assert record.attempt_count == 1
    assert RunRecord.model_validate(record.model_dump(mode="python")).attempt_history == ()
    assert validate_completed_record(record).attempt_history == ()


def test_archive_validation_failure_rolls_the_whole_revision_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected archive must leave no decision, no transition and no archive."""

    runtime = make_runtime(tmp_path)
    drive_to_first_failure(runtime)
    stop_decision_before = runtime.record.stop_decision
    findings_before = runtime.record.unresolved_findings
    real_build = runtime._build_record

    def build_with_broken_archive(**updates: object) -> RunRecord:
        history = updates.get("attempt_history")
        if isinstance(history, tuple) and history:
            broken = history[-1].model_copy(update={"findings": ()})
            updates = {**updates, "attempt_history": (*history[:-1], broken)}
        return real_build(**updates)

    monkeypatch.setattr(runtime, "_build_record", build_with_broken_archive)

    with pytest.raises(ValidationError, match="exactly one repairable finding"):
        runtime.commit_revision_decision(runtime.build_revision_proposal())

    assert runtime.record.state is RunState.CHECKING
    assert runtime.record.attempt_count == 1
    assert runtime.record.attempt_history == ()
    assert runtime._event_log.decisions == ()
    assert event_type_count(runtime, EvaluationEventType.REVISION_REQUESTED) == 0
    assert runtime.record.stop_decision == stop_decision_before
    assert runtime.record.unresolved_findings == findings_before

    monkeypatch.undo()
    decision = runtime.commit_revision_decision(runtime.build_revision_proposal())

    assert decision.append_sequence == 1
    assert len(runtime._event_log.decisions) == 1
    assert len(runtime.record.attempt_history) == 1
    assert event_type_count(runtime, EvaluationEventType.REVISION_REQUESTED) == 1
    assert runtime.record.attempt_count == 2
