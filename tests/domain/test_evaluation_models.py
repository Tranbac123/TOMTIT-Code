from __future__ import annotations

import json
from datetime import UTC, datetime
from types import MappingProxyType

import pytest
from pydantic import ValidationError

from tomtit.domain.evaluation import (
    BudgetSnapshot,
    ContractInferenceAccuracy,
    DecisionEventDraft,
    DecisionReasonCode,
    EvaluationEvent,
    ExperimentRunRecord,
    ExperimentWorker,
    FrozenPayload,
    OwnerCriterionEvaluation,
    PersistedDecisionEvent,
    OwnerEvaluation,
    TaskOutcome,
)
from tomtit.domain.enums import (
    AlignmentScore,
    CarryoverRisk,
    CostSource,
    EvaluationEventType,
    ExperimentArm,
    NextAction,
    HumanInterventionSource,
    OverallResult,
    Outcome,
    OwnerCriterionStatus,
    OwnerGoalStatus,
    RunState,
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


# ---------------------------------------------------------------------------
# F-004 — EvaluationEvent.payload is deeply immutable after validation.
# ---------------------------------------------------------------------------


def event_with_payload(payload: dict[str, object]) -> EvaluationEvent:
    return EvaluationEvent(
        event_id="event-1",
        schema_version="evaluation-event.v1",
        timestamp=datetime.now(UTC),
        event_type=EvaluationEventType.CHECK_PASSED,
        task_id="task-1",
        run_id="run-1",
        attempt_number=1,
        actor="runtime",
        payload=payload,
    )


def nested_payload() -> dict[str, object]:
    return {
        "check_id": "unit-tests",
        "nested": {"items": ["a", "b"], "key": "value"},
    }


def test_event_payload_rejects_top_level_mutation() -> None:
    event = event_with_payload(nested_payload())

    with pytest.raises(TypeError):
        event.payload["new_key"] = "injected"  # type: ignore[index]

    assert "new_key" not in event.payload


def test_event_payload_rejects_nested_mapping_mutation() -> None:
    event = event_with_payload(nested_payload())

    with pytest.raises(TypeError):
        event.payload["nested"]["key"] = "injected"  # type: ignore[index]

    assert event.payload["nested"]["key"] == "value"


def test_event_payload_rejects_nested_list_append() -> None:
    event = event_with_payload(nested_payload())

    with pytest.raises(AttributeError):
        event.payload["nested"]["items"].append("c")  # type: ignore[attr-defined]

    assert event.payload["nested"]["items"] == ("a", "b")


def test_mutating_original_payload_dict_does_not_reach_the_event() -> None:
    original = nested_payload()
    event = event_with_payload(original)

    original["check_id"] = "tampered"
    original["added_after"] = True

    assert event.payload["check_id"] == "unit-tests"
    assert "added_after" not in event.payload


def test_mutating_original_nested_list_does_not_reach_the_event() -> None:
    original = nested_payload()
    event = event_with_payload(original)

    nested = original["nested"]
    assert isinstance(nested, dict)
    items = nested["items"]
    assert isinstance(items, list)
    items.append("tampered")
    nested["key"] = "tampered"

    assert event.payload["nested"]["items"] == ("a", "b")
    assert event.payload["nested"]["key"] == "value"


def test_event_payload_list_values_become_immutable_canonical_values() -> None:
    event = event_with_payload({"argv": ["python", "-m", "pytest"], "nested": {"x": [1]}})

    assert isinstance(event.payload, FrozenPayload)
    assert event.payload["argv"] == ("python", "-m", "pytest")
    assert isinstance(event.payload["argv"], tuple)
    assert isinstance(event.payload["nested"], FrozenPayload)
    assert isinstance(event.payload["nested"]["x"], tuple)


def test_event_payload_json_dump_emits_plain_objects_and_arrays() -> None:
    event = event_with_payload(nested_payload())

    dumped = event.model_dump(mode="json")["payload"]

    assert dumped == {
        "check_id": "unit-tests",
        "nested": {"items": ["a", "b"], "key": "value"},
    }
    assert isinstance(dumped, dict)
    assert isinstance(dumped["nested"], dict)
    assert isinstance(dumped["nested"]["items"], list)


def test_event_payload_json_round_trip_preserves_content_and_immutability() -> None:
    event = event_with_payload(nested_payload())

    serialized = event.model_dump_json()
    restored = EvaluationEvent.model_validate_json(serialized)

    assert json.loads(serialized)["payload"]["nested"]["items"] == ["a", "b"]
    assert restored.payload == event.payload
    assert restored == event
    assert isinstance(restored.payload, FrozenPayload)
    assert isinstance(restored.payload["nested"]["items"], tuple)

    with pytest.raises(TypeError):
        restored.payload["nested"]["key"] = "injected"  # type: ignore[index]


def test_event_payload_json_schema_generation_still_succeeds() -> None:
    schema = EvaluationEvent.model_json_schema()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["payload"]["type"] == "object"


def test_event_payload_rejects_non_mapping_input() -> None:
    with pytest.raises(ValidationError):
        event_with_payload(["not", "a", "mapping"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SOL-M03 — FrozenPayload internal state is frozen after construction.
# ---------------------------------------------------------------------------


def test_payload_rejects_internal_slot_reassignment() -> None:
    event = event_with_payload(nested_payload())

    with pytest.raises(AttributeError):
        event.payload._data = MappingProxyType({"top": "CORRUPTED"})  # type: ignore[union-attr]

    assert event.payload["check_id"] == "unit-tests"
    assert "top" not in event.payload
    assert json.loads(event.model_dump_json())["payload"]["check_id"] == "unit-tests"


def test_nested_payload_rejects_internal_slot_reassignment() -> None:
    event = event_with_payload(nested_payload())
    nested = event.payload["nested"]

    with pytest.raises(AttributeError):
        nested._data = MappingProxyType({"key": "CORRUPTED"})

    assert event.payload["nested"]["key"] == "value"


def test_payload_rejects_arbitrary_attribute_assignment() -> None:
    event = event_with_payload(nested_payload())

    with pytest.raises(AttributeError):
        event.payload.injected = "value"  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        event.payload["nested"].injected = "value"

    assert not hasattr(event.payload, "injected")


def test_payload_rejects_internal_attribute_deletion() -> None:
    event = event_with_payload(nested_payload())

    with pytest.raises(AttributeError):
        del event.payload._data  # type: ignore[union-attr]

    assert event.payload["check_id"] == "unit-tests"


def test_frozen_payload_survives_round_trip_with_frozen_internals() -> None:
    event = event_with_payload(nested_payload())

    restored = EvaluationEvent.model_validate_json(event.model_dump_json())

    assert isinstance(restored.payload, FrozenPayload)
    with pytest.raises(AttributeError):
        restored.payload._data = MappingProxyType({"top": "CORRUPTED"})  # type: ignore[union-attr]
    assert restored.payload == event.payload


# ---------------------------------------------------------------------------
# Gate 2 — DecisionEvent draft/persisted authority split.
# ---------------------------------------------------------------------------


def budget_snapshot(**updates: object) -> BudgetSnapshot:
    values: dict[str, object] = {
        "attempt_number": 1,
        "max_attempts": 2,
        "hard_max_attempts": 3,
        "time_budget_seconds": 120,
    }
    values.update(updates)
    return BudgetSnapshot(**values)  # type: ignore[arg-type]


def decision_draft(**updates: object) -> DecisionEventDraft:
    values: dict[str, object] = {
        "policy_version": "mvp-execution-policy-v0.3",
        "task_contract_hash": "a" * 64,
        "run_id": "run-1",
        "attempt_number": 1,
        "state_before": RunState.CHECKING,
        "evidence_snapshot_ids": ("check:run-1:1:unit-tests",),
        "allowed_actions": (NextAction.REVISE, NextAction.FAIL),
        "selected_action": NextAction.REVISE,
        "reason_code": DecisionReasonCode.DETERMINISTIC_REPAIRABLE_CHECK_FAILURE,
        "budget_snapshot": budget_snapshot(),
        "state_after": RunState.REVISING,
    }
    values.update(updates)
    return DecisionEventDraft(**values)  # type: ignore[arg-type]


def test_decision_draft_accepts_a_valid_revise_proposal() -> None:
    draft = decision_draft()

    assert draft.decision_schema_version == "decision-event.v1"
    assert draft.selected_action is NextAction.REVISE
    assert isinstance(draft.evidence_snapshot_ids, tuple)
    assert isinstance(draft.allowed_actions, tuple)


@pytest.mark.parametrize(
    "runtime_owned_field",
    ("append_sequence", "decision_id", "decision_timestamp"),
)
def test_decision_draft_rejects_runtime_owned_fields(runtime_owned_field: str) -> None:
    with pytest.raises(ValidationError):
        decision_draft(**{runtime_owned_field: 1})


def test_decision_draft_requires_selected_action_within_allowed_actions() -> None:
    with pytest.raises(ValidationError, match="allowed_actions"):
        decision_draft(allowed_actions=(NextAction.FAIL,))


def test_decision_draft_rejects_duplicate_evidence_ids() -> None:
    duplicate = ("check:run-1:1:unit-tests", "check:run-1:1:unit-tests")
    with pytest.raises(ValidationError, match="evidence_snapshot_ids must be unique"):
        decision_draft(evidence_snapshot_ids=duplicate)


def test_decision_draft_requires_a_state_change() -> None:
    with pytest.raises(ValidationError, match="must change the canonical state"):
        decision_draft(state_after=RunState.CHECKING)


def test_decision_draft_requires_matching_budget_attempt() -> None:
    with pytest.raises(ValidationError, match="deciding attempt"):
        decision_draft(budget_snapshot=budget_snapshot(attempt_number=2))


def test_persisted_decision_carries_runtime_owned_identity() -> None:
    persisted = PersistedDecisionEvent(
        **decision_draft().model_dump(),
        decision_id="decision-1",
        decision_timestamp=datetime.now(UTC),
        append_sequence=1,
    )

    assert persisted.append_sequence == 1
    assert persisted.decision_id == "decision-1"
    restored = PersistedDecisionEvent.model_validate_json(persisted.model_dump_json())
    assert restored == persisted
    with pytest.raises(ValidationError):
        persisted.append_sequence = 2


def test_persisted_decision_rejects_a_non_positive_sequence() -> None:
    with pytest.raises(ValidationError):
        PersistedDecisionEvent(
            **decision_draft().model_dump(),
            decision_id="decision-1",
            decision_timestamp=datetime.now(UTC),
            append_sequence=0,
        )


def test_budget_snapshot_rejects_max_above_hard_max() -> None:
    with pytest.raises(ValidationError, match="hard_max_attempts"):
        budget_snapshot(max_attempts=3, hard_max_attempts=2)
