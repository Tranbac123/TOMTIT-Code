from __future__ import annotations

import pytest
from pydantic import ValidationError

from tomtit_decision_eval_v0 import (
    CheckStatus,
    ClaimStatus,
    CounterfactualEvidence,
    DecisionLabel,
    FindingAdjudication,
    FindingImpact,
    FindingMateriality,
    IndependentJudgment,
    ObjectiveTaskNeed,
    PolicyCompliance,
    ReasonCode,
    RepairAuthorization,
    RequirementStatus,
    RetryValue,
    ReviewerFinding,
    TomtitAction,
    classify_finding_materiality,
    evaluate_decision,
)

from .conftest import post_retry


def needed_case(make_case, action: TomtitAction, *, authorized: bool, post=None):
    return make_case(
        action,
        requirement_status=RequirementStatus.UNSATISFIED,
        requirement_repairable=ClaimStatus.YES,
        check_status=CheckStatus.FAIL,
        check_repairable=ClaimStatus.YES,
        repair_authorized=authorized,
        post=post,
    )


def test_authorized_material_defect_retry_is_objectively_appropriate(make_case) -> None:
    result = evaluate_decision(needed_case(make_case, TomtitAction.RETRY, authorized=True))

    assert result.objective_task_need is ObjectiveTaskNeed.RETRY_NEEDED
    assert result.independent_judgment is IndependentJudgment.RETRY_NEEDED
    assert result.repair_authorization is RepairAuthorization.AUTHORIZED
    assert result.policy_compliance is PolicyCompliance.COMPLIANT
    assert result.decision_label is DecisionLabel.CORRECT


def test_authorized_material_defect_stop_is_missed_retry(make_case) -> None:
    result = evaluate_decision(needed_case(make_case, TomtitAction.STOP, authorized=True))

    assert result.objective_task_need is ObjectiveTaskNeed.RETRY_NEEDED
    assert result.repair_authorization is RepairAuthorization.AUTHORIZED
    assert result.policy_compliance is PolicyCompliance.COMPLIANT
    assert result.decision_label is DecisionLabel.INCORRECT


def test_unauthorized_material_defect_stop_preserves_need_and_is_policy_compliant(
    make_case,
) -> None:
    result = evaluate_decision(needed_case(make_case, TomtitAction.STOP, authorized=False))

    assert result.objective_task_need is ObjectiveTaskNeed.RETRY_NEEDED
    assert result.independent_judgment is IndependentJudgment.RETRY_NEEDED
    assert result.repair_authorization is RepairAuthorization.NOT_AUTHORIZED
    assert result.policy_compliance is PolicyCompliance.COMPLIANT
    assert result.decision_label is DecisionLabel.INCORRECT


def test_unauthorized_material_defect_retry_preserves_need_but_violates_policy(
    make_case,
) -> None:
    result = evaluate_decision(needed_case(make_case, TomtitAction.RETRY, authorized=False))

    assert result.objective_task_need is ObjectiveTaskNeed.RETRY_NEEDED
    assert result.repair_authorization is RepairAuthorization.NOT_AUTHORIZED
    assert result.policy_compliance is PolicyCompliance.NON_COMPLIANT
    assert result.decision_label is DecisionLabel.CORRECT
    assert ReasonCode.RUNTIME_ACTION_POLICY_VIOLATION in result.reason_codes


def test_authorization_never_changes_objective_task_need(make_case) -> None:
    authorized = evaluate_decision(
        needed_case(make_case, TomtitAction.STOP, authorized=True)
    )
    unauthorized = evaluate_decision(
        needed_case(make_case, TomtitAction.STOP, authorized=False)
    )

    assert authorized.objective_task_need is unauthorized.objective_task_need
    assert authorized.repair_authorization is not unauthorized.repair_authorization


def test_no_material_defect_stop_is_correct(make_case) -> None:
    result = evaluate_decision(make_case(TomtitAction.STOP))

    assert result.objective_task_need is ObjectiveTaskNeed.RETRY_NOT_NEEDED
    assert result.decision_label is DecisionLabel.CORRECT


def test_no_material_defect_retry_is_unnecessary(make_case) -> None:
    result = evaluate_decision(make_case(TomtitAction.RETRY))

    assert result.objective_task_need is ObjectiveTaskNeed.RETRY_NOT_NEEDED
    assert result.decision_label is DecisionLabel.INCORRECT


def test_runtime_reviewer_only_cannot_self_establish_retry_need(
    make_case, material_finding
) -> None:
    result = evaluate_decision(
        make_case(
            TomtitAction.RETRY,
            finding=material_finding,
            requirement_status=RequirementStatus.UNKNOWN,
            check_status=CheckStatus.UNKNOWN,
            counterfactual=CounterfactualEvidence.NOT_AVAILABLE,
        )
    )

    assert result.objective_task_need is ObjectiveTaskNeed.AMBIGUOUS
    assert result.decision_label is DecisionLabel.AMBIGUOUS
    assert result.material_finding_present is False
    assert (
        ReasonCode.RUNTIME_REVIEWER_NOT_INDEPENDENTLY_SUBSTANTIATED
        in result.reason_codes
    )


def test_runtime_reviewer_plus_failed_deterministic_check_establishes_need(
    make_case, material_finding
) -> None:
    result = evaluate_decision(
        make_case(
            TomtitAction.RETRY,
            finding=material_finding,
            requirement_status=RequirementStatus.UNSATISFIED,
            requirement_repairable=ClaimStatus.YES,
            check_status=CheckStatus.FAIL,
            check_repairable=ClaimStatus.YES,
        )
    )

    assert result.objective_task_need is ObjectiveTaskNeed.RETRY_NEEDED
    assert result.decision_label is DecisionLabel.CORRECT


def test_unknown_deterministic_check_cannot_ground_unsatisfied_assertion(
    make_case,
) -> None:
    result = evaluate_decision(
        make_case(
            TomtitAction.RETRY,
            requirement_status=RequirementStatus.UNSATISFIED,
            requirement_repairable=ClaimStatus.YES,
            check_status=CheckStatus.UNKNOWN,
            check_repairable=ClaimStatus.YES,
        )
    )

    assert result.objective_task_need is ObjectiveTaskNeed.AMBIGUOUS
    assert result.decision_label is DecisionLabel.AMBIGUOUS
    assert ReasonCode.INSUFFICIENT_PRE_DECISION_EVIDENCE in result.reason_codes


def test_runtime_reviewer_plus_independent_adjudication_establishes_need(
    make_case, material_finding, material_adjudication
) -> None:
    result = evaluate_decision(
        make_case(
            TomtitAction.RETRY,
            finding=material_finding,
            adjudication=material_adjudication,
            requirement_status=RequirementStatus.UNKNOWN,
            check_status=CheckStatus.UNKNOWN,
        )
    )

    assert result.objective_task_need is ObjectiveTaskNeed.RETRY_NEEDED
    assert result.material_finding_present is True
    assert result.repairable_finding_present is True


def test_reviewer_severity_and_claimed_impact_are_not_materiality_ground_truth(
    cosmetic_finding, cosmetic_adjudication, material_finding, material_adjudication
) -> None:
    assert cosmetic_finding.severity == "BLOCKER"
    assert cosmetic_finding.claimed_impact.affects_correctness is ClaimStatus.YES
    assert (
        classify_finding_materiality(cosmetic_adjudication)
        is FindingMateriality.NON_MATERIAL
    )
    assert material_finding.severity == "LOW"
    assert (
        classify_finding_materiality(material_adjudication)
        is FindingMateriality.MATERIAL
    )


def test_independently_non_material_finding_does_not_justify_retry(
    make_case, cosmetic_finding, cosmetic_adjudication
) -> None:
    result = evaluate_decision(
        make_case(
            TomtitAction.RETRY,
            finding=cosmetic_finding,
            adjudication=cosmetic_adjudication,
        )
    )

    assert result.objective_task_need is ObjectiveTaskNeed.RETRY_NOT_NEEDED
    assert result.decision_label is DecisionLabel.INCORRECT
    assert ReasonCode.ONLY_NON_MATERIAL_FINDINGS in result.reason_codes


def test_unknown_independent_impact_remains_ambiguous() -> None:
    adjudication = FindingAdjudication(
        finding_id="finding",
        evidence_refs=("independent",),
        impact=FindingImpact(),
    )

    assert classify_finding_materiality(adjudication) is FindingMateriality.AMBIGUOUS


def test_completed_retry_with_unrelated_improvement_does_not_rewrite_need(
    make_case,
) -> None:
    without_future = evaluate_decision(make_case(TomtitAction.RETRY))
    with_completed_future = evaluate_decision(
        make_case(
            TomtitAction.RETRY,
            post=post_retry(
                unrelated_quality_improved=True,
                final_outcome="COMPLETED",
            ),
        )
    )

    assert without_future.objective_task_need is ObjectiveTaskNeed.RETRY_NOT_NEEDED
    assert with_completed_future.objective_task_need is without_future.objective_task_need
    assert with_completed_future.decision_label is DecisionLabel.INCORRECT
    assert with_completed_future.retry_value is RetryValue.IMPROVED


def test_necessary_retry_followed_by_failed_repair_stays_justified(make_case) -> None:
    result = evaluate_decision(
        needed_case(
            make_case,
            TomtitAction.RETRY,
            authorized=True,
            post=post_retry(before=1, after=1, final_outcome="FAILED"),
        )
    )

    assert result.objective_task_need is ObjectiveTaskNeed.RETRY_NEEDED
    assert result.decision_label is DecisionLabel.CORRECT
    assert result.retry_value is RetryValue.NO_MATERIAL_CHANGE


def test_necessary_retry_can_be_harmful_without_rewriting_original_need(make_case) -> None:
    result = evaluate_decision(
        needed_case(
            make_case,
            TomtitAction.RETRY,
            authorized=True,
            post=post_retry(before=1, after=2, acceptance_regression=True),
        )
    )

    assert result.objective_task_need is ObjectiveTaskNeed.RETRY_NEEDED
    assert result.decision_label is DecisionLabel.CORRECT
    assert result.retry_value is RetryValue.HARMFUL


def test_canonical_result_is_immutable(make_case) -> None:
    result = evaluate_decision(make_case(TomtitAction.STOP))

    assert isinstance(result.reason_codes, tuple)
    with pytest.raises(ValidationError):
        result.objective_task_need = ObjectiveTaskNeed.RETRY_NEEDED


def test_result_contains_reason_codes_not_hidden_reasoning(make_case) -> None:
    serialized = evaluate_decision(make_case(TomtitAction.STOP)).model_dump(mode="json")

    assert serialized["reason_codes"]
    assert "reasoning" not in serialized
    assert "chain_of_thought" not in serialized


def test_raw_runtime_finding_shape_is_separate_from_adjudication() -> None:
    finding_fields = set(ReviewerFinding.model_fields)
    adjudication_fields = set(FindingAdjudication.model_fields)

    assert "claimed_impact" in finding_fields
    assert "impact" not in finding_fields
    assert "impact" in adjudication_fields
