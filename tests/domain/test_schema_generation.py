from typing import get_args, get_origin

from pydantic import TypeAdapter

from tomtit.domain.contracts import (
    AcceptanceCriterion,
    CriterionAssignment,
    Finding,
    ForbiddenAction,
    ProgressSnapshot,
    RequiredCheck,
    ScopeRule,
    StopDecision,
    TaskContract,
    VerificationPlan,
    VerificationResult,
    VerifierIdentity,
    WorkerCapabilities,
    WorkerResult,
)
from tomtit.domain.enums import OwnerDecision
from tomtit.domain.evaluation import EvaluationEvent, ExperimentRunRecord, TaskOutcome

def test_all_commit_1_contracts_generate_json_schema() -> None:
    models = [
        TaskContract,
        AcceptanceCriterion,
        RequiredCheck,
        ScopeRule,
        ForbiddenAction,
        VerificationPlan,
        CriterionAssignment,
        WorkerCapabilities,
        WorkerResult,
        Finding,
        VerifierIdentity,
        VerificationResult,
        StopDecision,
        ProgressSnapshot,
        EvaluationEvent,
        ExperimentRunRecord,
        TaskOutcome,
    ]
    for model in models:
        schema = model.model_json_schema()
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False

    owner_schema = TypeAdapter(OwnerDecision).json_schema()
    assert set(owner_schema["enum"]) == {"ACCEPT", "REVISE", "REJECT", "CANCEL"}


def test_tuple_backed_collections_remain_json_arrays_in_schema() -> None:
    task_schema = TaskContract.model_json_schema()
    criterion_schema = AcceptanceCriterion.model_json_schema()
    required_check_schema = RequiredCheck.model_json_schema()

    assert task_schema["properties"]["acceptance_criteria"]["type"] == "array"
    assert task_schema["properties"]["required_checks"]["type"] == "array"
    assert criterion_schema["properties"]["required_check_ids"]["type"] == "array"
    assert required_check_schema["properties"]["argv"]["type"] == "array"



def _contains_mutable_list(annotation: object) -> bool:
    if get_origin(annotation) is list:
        return True
    return any(_contains_mutable_list(argument) for argument in get_args(annotation))


def test_canonical_models_do_not_expose_mutable_list_fields() -> None:
    models = [
        TaskContract,
        AcceptanceCriterion,
        RequiredCheck,
        ScopeRule,
        ForbiddenAction,
        VerificationPlan,
        CriterionAssignment,
        WorkerCapabilities,
        WorkerResult,
        Finding,
        VerifierIdentity,
        VerificationResult,
        StopDecision,
        ProgressSnapshot,
        EvaluationEvent,
        ExperimentRunRecord,
        TaskOutcome,
    ]

    mutable_fields = {
        f"{model.__name__}.{field_name}"
        for model in models
        for field_name, field in model.model_fields.items()
        if _contains_mutable_list(field.annotation)
    }
    assert mutable_fields == set()
