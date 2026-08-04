from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tomtit.domain.evaluation import (
    ContractInferenceAccuracy,
    EvaluationEvent,
    ExperimentRunRecord,
    ExperimentWorker,
    OwnerCriterionEvaluation,
    OwnerEvaluation,
    TaskOutcome,
)
from tomtit.domain.enums import (
    AlignmentScore,
    CarryoverRisk,
    CostSource,
    EvaluationEventType,
    ExperimentArm,
    HumanInterventionSource,
    OverallResult,
    Outcome,
    OwnerCriterionStatus,
    OwnerGoalStatus,
    StopReason,
    TernaryStatus,
    ValidityStatus,
    VerificationStatus,
)


def owner_evaluation() -> OwnerEvaluation:
    return OwnerEvaluation(
        goal_satisfied=OwnerGoalStatus.YES,
        criteria=[],
        scope_respected=TernaryStatus.YES,
        required_checks_passed=TernaryStatus.YES,
        manual_edit_required=False,
        remaining_blocking_issue=False,
        overall_result=OverallResult.ACCEPTED,
    )


def inference_accuracy() -> ContractInferenceAccuracy:
    return ContractInferenceAccuracy(
        goal_alignment=AlignmentScore.FULL,
        criteria_recall=1.0,
        scope_alignment=AlignmentScore.FULL,
        required_check_alignment=AlignmentScore.FULL,
        material_misunderstanding=False,
    )

def test_evaluation_event_attempt_number_is_nullable() -> None:
    event = EvaluationEvent(
        event_id="event-1",
        schema_version="1.0",
        timestamp=datetime.now(UTC),
        event_type=EvaluationEventType.TASK_SUBMITTED,
        task_id="task-1",
        run_id="run-1",
        actor="runtime",
    )
    assert event.attempt_number is None

def test_evaluation_event_rejects_attempt_zero() -> None:
    with pytest.raises(ValidationError):
        EvaluationEvent(
            event_id="event-1",
            schema_version="1.0",
            timestamp=datetime.now(UTC),
            event_type=EvaluationEventType.WORKER_STARTED,
            task_id="task-1",
            run_id="run-1",
            attempt_number=0,
            actor="runtime",
        )

def test_tomtit_experiment_requires_fixed_scale_contract_accuracy() -> None:
    record = ExperimentRunRecord(
        experiment_id="experiment-1",
        comparison_id="comparison-1",
        arm=ExperimentArm.TOMTIT,
        repository_fixture="profile-site-v1",
        base_commit="abc123",
        agent_visible_request_id="request-1",
        hidden_contract_id="contract-1",
        worker=ExperimentWorker(provider="provider", model="model", cli_version="1.0"),
        agent_execution_rounds=1,
        user_reprompts=0,
        user_clarifications=0,
        manual_commands=0,
        human_intervention_seconds=30,
        human_intervention_source=HumanInterventionSource.EXPLICIT_USER_REPORT,
        elapsed_seconds=120.5,
        final_patch_artifact="artifacts/final.patch",
        owner_evaluation=owner_evaluation(),
        carryover_risk=CarryoverRisk.LOW,
        validity_status=ValidityStatus.VALID,
        contract_inference_accuracy=inference_accuracy(),
        total_cost_usd=None,
        cost_source=CostSource.UNAVAILABLE,
    )
    restored = ExperimentRunRecord.model_validate_json(record.model_dump_json())
    assert restored == record

def test_tomtit_experiment_rejects_missing_contract_accuracy() -> None:
    with pytest.raises(ValidationError, match="contract_inference_accuracy"):
        ExperimentRunRecord(
            experiment_id="experiment-1",
            comparison_id="comparison-1",
            arm=ExperimentArm.TOMTIT,
            repository_fixture="profile-site-v1",
            base_commit="abc123",
            agent_visible_request_id="request-1",
            hidden_contract_id="contract-1",
            worker=ExperimentWorker(provider="provider", model="model", cli_version="1.0"),
            agent_execution_rounds=1,
            user_reprompts=0,
            user_clarifications=0,
            manual_commands=0,
            human_intervention_seconds=None,
            human_intervention_source=HumanInterventionSource.UNAVAILABLE,
            elapsed_seconds=120,
            final_patch_artifact=None,
            owner_evaluation=owner_evaluation(),
            carryover_risk=CarryoverRisk.LOW,
            validity_status=ValidityStatus.VALID,
        )

def test_baseline_record_allows_no_contract_accuracy() -> None:
    record = ExperimentRunRecord(
        experiment_id="experiment-1",
        comparison_id="comparison-1",
        arm=ExperimentArm.BASELINE,
        repository_fixture="profile-site-v1",
        base_commit="abc123",
        agent_visible_request_id="request-1",
        hidden_contract_id="contract-1",
        worker=ExperimentWorker(provider="provider", model="model", cli_version="1.0"),
        agent_execution_rounds=1,
        user_reprompts=1,
        user_clarifications=0,
        manual_commands=1,
        human_intervention_seconds=None,
        human_intervention_source=HumanInterventionSource.UNAVAILABLE,
        elapsed_seconds=200,
        final_patch_artifact=None,
        owner_evaluation=owner_evaluation(),
        carryover_risk=CarryoverRisk.MEDIUM,
        validity_status=ValidityStatus.VALID,
    )
    assert record.contract_inference_accuracy is None

def test_task_outcome_rejects_unclosed_verification_status() -> None:
    with pytest.raises(ValidationError, match="verification_status"):
        TaskOutcome(
            task_id="task-1",
            run_id="run-1",
            execution_outcome=Outcome.FAILED,
            stop_reason=StopReason.NO_PROGRESS,
            verification_status="worker_says_ok",
            scope_valid=True,
            required_checks_passed=False,
            evidence_complete=True,
            agent_execution_rounds=2,
            user_reprompts=0,
            user_clarifications=0,
            manual_commands=0,
            elapsed_seconds=10,
            snapshot_generated_at=datetime.now(UTC),
        )

def test_invalid_tomtit_record_can_preserve_partial_evidence() -> None:
    record = ExperimentRunRecord(
        experiment_id="experiment-invalid",
        comparison_id="comparison-invalid",
        arm=ExperimentArm.TOMTIT,
        repository_fixture="profile-site-v1",
        base_commit="abc123",
        agent_visible_request_id="request-1",
        hidden_contract_id="contract-1",
        worker=ExperimentWorker(provider="provider", model="model", cli_version="1.0"),
        agent_execution_rounds=0,
        user_reprompts=0,
        user_clarifications=0,
        manual_commands=0,
        human_intervention_seconds=None,
        human_intervention_source=HumanInterventionSource.UNAVAILABLE,
        elapsed_seconds=0,
        final_patch_artifact=None,
        owner_evaluation=None,
        carryover_risk=CarryoverRisk.LOW,
        validity_status=ValidityStatus.INVALID,
    )
    assert record.contract_inference_accuracy is None

def test_task_outcome_enforces_stop_reason_mapping() -> None:
    with pytest.raises(ValidationError, match="must map"):
        TaskOutcome(
            task_id="task-1",
            run_id="run-1",
            execution_outcome=Outcome.COMPLETED,
            stop_reason=StopReason.NO_PROGRESS,
            verification_status=VerificationStatus.DETERMINISTICALLY_CHECKED,
            scope_valid=True,
            required_checks_passed=False,
            evidence_complete=True,
            agent_execution_rounds=2,
            user_reprompts=0,
            user_clarifications=0,
            manual_commands=0,
            elapsed_seconds=10,
            snapshot_generated_at=datetime.now(UTC),
        )

def test_baseline_rejects_tomtit_contract_accuracy() -> None:
    with pytest.raises(ValidationError, match="baseline arm"):
        ExperimentRunRecord(
            experiment_id="experiment-1",
            comparison_id="comparison-1",
            arm=ExperimentArm.BASELINE,
            repository_fixture="profile-site-v1",
            base_commit="abc123",
            agent_visible_request_id="request-1",
            hidden_contract_id="contract-1",
            worker=ExperimentWorker(provider="provider", model="model", cli_version="1.0"),
            agent_execution_rounds=1,
            user_reprompts=0,
            user_clarifications=0,
            manual_commands=0,
            human_intervention_seconds=None,
            human_intervention_source=HumanInterventionSource.UNAVAILABLE,
            elapsed_seconds=10,
            final_patch_artifact=None,
            owner_evaluation=owner_evaluation(),
            carryover_risk=CarryoverRisk.LOW,
            validity_status=ValidityStatus.VALID,
            contract_inference_accuracy=inference_accuracy(),
        )

def test_valid_experiment_requires_owner_evaluation() -> None:
    with pytest.raises(ValidationError, match="owner_evaluation"):
        ExperimentRunRecord(
            experiment_id="experiment-1",
            comparison_id="comparison-1",
            arm=ExperimentArm.BASELINE,
            repository_fixture="profile-site-v1",
            base_commit="abc123",
            agent_visible_request_id="request-1",
            hidden_contract_id="contract-1",
            worker=ExperimentWorker(provider="provider", model="model", cli_version="1.0"),
            agent_execution_rounds=1,
            user_reprompts=0,
            user_clarifications=0,
            manual_commands=0,
            human_intervention_seconds=None,
            human_intervention_source=HumanInterventionSource.UNAVAILABLE,
            elapsed_seconds=10,
            final_patch_artifact=None,
            owner_evaluation=None,
            carryover_risk=CarryoverRisk.LOW,
            validity_status=ValidityStatus.VALID,
        )

def test_owner_evaluation_rejects_duplicate_criterion_ids() -> None:
    criterion = OwnerCriterionEvaluation(
        criterion_id="criterion-1",
        status=OwnerCriterionStatus.SATISFIED,
        evidence=["check:unit-tests"],
    )
    with pytest.raises(ValidationError, match="criterion IDs"):
        OwnerEvaluation(
            goal_satisfied=OwnerGoalStatus.YES,
            criteria=[criterion, criterion],
            scope_respected=TernaryStatus.YES,
            required_checks_passed=TernaryStatus.YES,
            manual_edit_required=False,
            remaining_blocking_issue=False,
            overall_result=OverallResult.ACCEPTED,
        )

def test_recorded_metrics_require_concrete_sources() -> None:
    with pytest.raises(ValidationError, match="intervention time"):
        ExperimentRunRecord(
            experiment_id="experiment-1",
            comparison_id="comparison-1",
            arm=ExperimentArm.BASELINE,
            repository_fixture="profile-site-v1",
            base_commit="abc123",
            agent_visible_request_id="request-1",
            hidden_contract_id="contract-1",
            worker=ExperimentWorker(provider="provider", model="model", cli_version="1.0"),
            agent_execution_rounds=1,
            user_reprompts=0,
            user_clarifications=0,
            manual_commands=0,
            human_intervention_seconds=5,
            human_intervention_source=HumanInterventionSource.UNAVAILABLE,
            elapsed_seconds=10,
            final_patch_artifact=None,
            owner_evaluation=owner_evaluation(),
            carryover_risk=CarryoverRisk.LOW,
            validity_status=ValidityStatus.VALID,
        )

def test_accepted_task_outcome_requires_manual_edit_flag() -> None:
    with pytest.raises(ValidationError, match="accepted_without_manual_edit"):
        TaskOutcome(
            task_id="task-1",
            run_id="run-1",
            execution_outcome=Outcome.COMPLETED,
            stop_reason=StopReason.ACCEPTANCE_SATISFIED,
            verification_status=VerificationStatus.DETERMINISTICALLY_CHECKED,
            user_result=OverallResult.ACCEPTED,
            scope_valid=True,
            required_checks_passed=True,
            evidence_complete=True,
            agent_execution_rounds=1,
            user_reprompts=0,
            user_clarifications=0,
            manual_commands=0,
            elapsed_seconds=10,
            snapshot_generated_at=datetime.now(UTC),
        )


def test_evaluation_collections_are_immutable_tuples() -> None:
    evaluation = OwnerEvaluation(
        goal_satisfied=OwnerGoalStatus.YES,
        criteria=[
            OwnerCriterionEvaluation(
                criterion_id="criterion-1",
                status=OwnerCriterionStatus.SATISFIED,
                evidence=["check:unit-tests"],
            )
        ],
        scope_respected=TernaryStatus.YES,
        required_checks_passed=TernaryStatus.YES,
        manual_edit_required=False,
        remaining_blocking_issue=False,
        overall_result=OverallResult.ACCEPTED,
    )

    assert isinstance(evaluation.criteria, tuple)
    assert isinstance(evaluation.criteria[0].evidence, tuple)

    with pytest.raises(AttributeError):
        evaluation.criteria.clear()  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        evaluation.criteria[0].evidence.append("other")  # type: ignore[attr-defined]
