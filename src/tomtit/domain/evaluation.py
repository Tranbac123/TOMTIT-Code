"""Machine-readable evaluation and dogfood experiment contracts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import (
    BeforeValidator,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, core_schema

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


def _freeze_payload_value(value: Any) -> Any:
    """Recursively convert an event payload value into an immutable canonical value."""

    if isinstance(value, FrozenPayload):
        return value
    if isinstance(value, Mapping):
        return FrozenPayload(value)
    # str and bytes are Sequences but are already immutable scalars here.
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(_freeze_payload_value(item) for item in value)
    return value


def _unfreeze_payload_value(value: Any) -> Any:
    """Convert a canonical payload value back into plain JSON-shaped builtins."""

    if isinstance(value, Mapping):
        return {key: _unfreeze_payload_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_unfreeze_payload_value(item) for item in value]
    return value


class FrozenPayload(Mapping[str, Any]):
    """Deeply immutable mapping used for canonical append-only event payloads.

    Every nested mapping becomes a ``FrozenPayload`` and every nested sequence
    becomes a tuple, so a payload cannot be mutated after validation at any depth.
    Input containers are copied during freezing, so a caller keeping a reference to
    the original object can never reach canonical state.
    """

    __slots__ = ("_data",)

    _data: Mapping[str, Any]

    def __init__(self, data: Mapping[str, Any]) -> None:
        frozen: dict[str, Any] = {}
        for key, value in data.items():
            if not isinstance(key, str):
                raise ValueError("event payload keys must be strings")
            frozen[key] = _freeze_payload_value(value)
        # MappingProxyType keeps the backing dict unreachable and unwritable.
        self._data = MappingProxyType(frozen)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({dict(self._data)!r})"

    def __hash__(self) -> int:
        return hash(frozenset(self._data.items()))

    @classmethod
    def _validate(cls, value: Any) -> FrozenPayload:
        if isinstance(value, FrozenPayload):
            return value
        if isinstance(value, Mapping):
            return cls(value)
        raise ValueError("event payload must be a mapping")

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls._validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                _unfreeze_payload_value,
                return_schema=core_schema.any_schema(),
                when_used="always",
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        schema: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        return {"type": "object", "additionalProperties": True}


# The declared type is the read-only interface callers may rely on; the validated
# value is always a FrozenPayload. Mapping has no __setitem__, so static analysis
# rejects payload mutation as well.
ImmutableEventPayload = Annotated[Mapping[str, Any], FrozenPayload]


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
    payload: ImmutableEventPayload = Field(default_factory=lambda: FrozenPayload({}))


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
