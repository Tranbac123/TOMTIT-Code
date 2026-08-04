"""Machine-readable evaluation and dogfood experiment contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BeforeValidator, Field, model_validator

from tomtit.domain.contracts import DomainModel, NonEmptyString, _list_to_tuple
from tomtit.domain.enums import (
    AlignmentScore,
    CarryoverRisk,
    CostSource,
    EscapedDefectStatus,
    EvaluationEventType,
    ExperimentArm,
    FailureCategory,
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

NonNegativeInt = Annotated[int, Field(ge=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]


class ContractInferenceAccuracy(DomainModel):
    goal_alignment: AlignmentScore
    criteria_recall: float = Field(ge=0, le=1)
    scope_alignment: AlignmentScore
    required_check_alignment: AlignmentScore
    material_misunderstanding: bool


class OwnerCriterionEvaluation(DomainModel):
    criterion_id: NonEmptyString
    status: OwnerCriterionStatus
    evidence: Annotated[
        tuple[NonEmptyString, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)


class OwnerEvaluation(DomainModel):
    goal_satisfied: OwnerGoalStatus
    criteria: Annotated[
        tuple[OwnerCriterionEvaluation, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    scope_respected: TernaryStatus
    required_checks_passed: TernaryStatus
    manual_edit_required: bool
    remaining_blocking_issue: bool
    overall_result: OverallResult

    @model_validator(mode="after")
    def validate_unique_criteria(self) -> OwnerEvaluation:
        criterion_ids = [criterion.criterion_id for criterion in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("owner evaluation criterion IDs must be unique")
        return self


class ExperimentWorker(DomainModel):
    provider: NonEmptyString
    model: NonEmptyString
    cli_version: NonEmptyString


class ExperimentRunRecord(DomainModel):
    experiment_id: NonEmptyString
    comparison_id: NonEmptyString
    arm: ExperimentArm
    repository_fixture: NonEmptyString
    base_commit: NonEmptyString
    agent_visible_request_id: NonEmptyString
    hidden_contract_id: NonEmptyString
    worker: ExperimentWorker
    agent_execution_rounds: NonNegativeInt
    user_reprompts: NonNegativeInt
    user_clarifications: NonNegativeInt
    manual_commands: NonNegativeInt
    human_intervention_seconds: NonNegativeInt | None
    human_intervention_source: HumanInterventionSource
    elapsed_seconds: NonNegativeFloat
    final_patch_artifact: str | None
    owner_evaluation: OwnerEvaluation | None
    carryover_risk: CarryoverRisk
    validity_status: ValidityStatus
    contract_inference_accuracy: ContractInferenceAccuracy | None = None
    total_cost_usd: NonNegativeFloat | None = None
    cost_source: CostSource = CostSource.UNAVAILABLE

    @model_validator(mode="after")
    def validate_arm_specific_fields(self) -> ExperimentRunRecord:
        if (
            self.arm is ExperimentArm.TOMTIT
            and self.validity_status is ValidityStatus.VALID
            and self.contract_inference_accuracy is None
        ):
            raise ValueError("valid TOMTIT arm requires contract_inference_accuracy")
        if self.arm is ExperimentArm.BASELINE and self.contract_inference_accuracy is not None:
            raise ValueError("baseline arm cannot record TOMTIT contract inference accuracy")
        if self.validity_status is ValidityStatus.VALID and self.owner_evaluation is None:
            raise ValueError("valid experiment record requires owner_evaluation")
        if self.human_intervention_seconds is None:
            if self.human_intervention_source is not HumanInterventionSource.UNAVAILABLE:
                raise ValueError("missing intervention time requires unavailable source")
        elif self.human_intervention_source is HumanInterventionSource.UNAVAILABLE:
            raise ValueError("recorded intervention time requires a concrete source")
        if self.total_cost_usd is None and self.cost_source is not CostSource.UNAVAILABLE:
            raise ValueError("missing cost requires unavailable source")
        if self.total_cost_usd is not None and self.cost_source is CostSource.UNAVAILABLE:
            raise ValueError("recorded cost requires a concrete source")
        return self


class EvaluationEvent(DomainModel):
    event_id: NonEmptyString
    schema_version: NonEmptyString
    timestamp: datetime
    event_type: EvaluationEventType
    task_id: NonEmptyString
    run_id: NonEmptyString
    attempt_number: Annotated[int | None, Field(ge=1)] = None
    actor: NonEmptyString
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskOutcome(DomainModel):
    task_id: NonEmptyString
    run_id: NonEmptyString
    execution_outcome: Outcome
    stop_reason: StopReason
    verification_status: VerificationStatus
    user_result: OverallResult = OverallResult.UNKNOWN
    accepted_without_manual_edit: bool | None = None
    human_intervention_seconds: NonNegativeInt | None = None
    human_intervention_source: HumanInterventionSource = HumanInterventionSource.UNAVAILABLE
    clarification_count: NonNegativeInt = 0
    user_override_count: NonNegativeInt = 0
    scope_valid: bool
    required_checks_passed: bool
    evidence_complete: bool
    agent_execution_rounds: NonNegativeInt
    user_reprompts: NonNegativeInt
    user_clarifications: NonNegativeInt
    manual_commands: NonNegativeInt
    elapsed_seconds: NonNegativeFloat
    total_cost_usd: NonNegativeFloat | None = None
    cost_source: CostSource = CostSource.UNAVAILABLE
    failure_category: FailureCategory | None = None
    escaped_defect_status: EscapedDefectStatus = EscapedDefectStatus.UNKNOWN
    feedback_reason: str | None = None
    snapshot_generated_at: datetime

    @model_validator(mode="after")
    def validate_sources_and_user_result(self) -> TaskOutcome:
        if self.human_intervention_seconds is None:
            if self.human_intervention_source is not HumanInterventionSource.UNAVAILABLE:
                raise ValueError("missing intervention time requires unavailable source")
        elif self.human_intervention_source is HumanInterventionSource.UNAVAILABLE:
            raise ValueError("recorded intervention time requires a concrete source")

        if self.total_cost_usd is None and self.cost_source is not CostSource.UNAVAILABLE:
            raise ValueError("missing cost requires unavailable source")
        if self.total_cost_usd is not None and self.cost_source is CostSource.UNAVAILABLE:
            raise ValueError("recorded cost requires a concrete source")

        expected_outcomes = {
            StopReason.ACCEPTANCE_SATISFIED: Outcome.COMPLETED,
            StopReason.USER_DECISION_REQUIRED: Outcome.WAITING_OWNER,
            StopReason.OWNER_REJECTED: Outcome.FAILED,
            StopReason.TIME_LIMIT: Outcome.BUDGET_EXHAUSTED,
            StopReason.QUOTA_LIMIT: Outcome.BUDGET_EXHAUSTED,
            StopReason.COST_LIMIT: Outcome.BUDGET_EXHAUSTED,
            StopReason.USER_CANCELLED: Outcome.CANCELLED,
        }
        expected_outcome = expected_outcomes.get(self.stop_reason, Outcome.FAILED)
        if self.execution_outcome is not expected_outcome:
            raise ValueError(
                f"stop reason {self.stop_reason.value} must map to "
                f"{expected_outcome.value}"
            )

        if self.user_result is OverallResult.ACCEPTED:
            if self.accepted_without_manual_edit is None:
                raise ValueError("accepted result requires accepted_without_manual_edit")
        elif self.accepted_without_manual_edit is True:
            raise ValueError("only accepted results can be accepted without manual edit")
        return self
