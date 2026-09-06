"""Decision assessment adapter (Contract V1 §14; findings F08, F09)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tomtit.evaluation import (
    DecisionAssessmentEventDraft,
    DecisionAssessmentMethod,
    DecisionReviewLabel,
    OperationalSource,
    adapt_decision_evaluation_result,
)
from tomtit_decision_eval_v0.models import (
    DecisionEvaluationResult,
    DecisionLabel,
    DecisionType,
    IndependentJudgment,
    ObjectiveTaskNeed,
    PolicyCompliance,
    ReasonCode,
    RepairAuthorization,
    RetryValue,
    TomtitAction,
    CounterfactualEvidence,
)

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
RUNTIME = OperationalSource.PRODUCT_RUNTIME


def _result(
    *,
    run_id: str = "run-1",
    decision_id: str = "decision-1",
    label: DecisionLabel = DecisionLabel.CORRECT,
    reasons: tuple[ReasonCode, ...] = (ReasonCode.ACCEPTANCE_FULLY_SATISFIED,),
    evidence: tuple[str, ...] = ("evidence:pre-decision",),
) -> DecisionEvaluationResult:
    """Build a real immutable evaluator result (not a stub)."""

    need = (
        ObjectiveTaskNeed.RETRY_NOT_NEEDED
        if label is DecisionLabel.CORRECT
        else ObjectiveTaskNeed.AMBIGUOUS
        if label is DecisionLabel.AMBIGUOUS
        else ObjectiveTaskNeed.RETRY_NEEDED
    )
    return DecisionEvaluationResult(
        run_id=run_id,
        decision_id=decision_id,
        decision_type=DecisionType.STOP,
        attempt=1,
        tomtit_action=TomtitAction.STOP,
        objective_task_need=need,
        independent_judgment=IndependentJudgment(need.value),
        repair_authorization=RepairAuthorization.AUTHORIZED,
        policy_compliance=PolicyCompliance.COMPLIANT,
        decision_label=label,
        material_finding_present=False,
        repairable_finding_present=False,
        counterfactual_evidence=CounterfactualEvidence.NOT_AVAILABLE,
        retry_value=RetryValue.UNKNOWN,
        evidence_refs=evidence,
        reason_codes=reasons,
    )


def _adapt(result, **overrides):
    payload = {
        "assessment_id": "assess-decision-1",
        "assessment_method": DecisionAssessmentMethod.HUMAN_REVIEW,
        "reviewer_or_experiment_id": "codex-gpt-5.6-sol",
        "timestamp": NOW,
    }
    payload.update(overrides)
    return adapt_decision_evaluation_result(result, **payload)


# -- event shape ------------------------------------------------------------


def test_decision_assessment_has_v1_fields() -> None:
    fields = DecisionAssessmentEventDraft.model_fields
    for field in (
        "assessment_id",
        "assessment_schema_version",
        "decision_id",
        "later_observed_outcome",
        "review_label",
        "assessment_method",
        "reviewer_or_experiment_id",
        "evidence_refs",
        "timestamp",
    ):
        assert field in fields, field


def test_not_reviewed_label_does_not_exist() -> None:
    assert "NOT_REVIEWED" not in {member.value for member in DecisionReviewLabel}


# -- F08: adapter consumes one immutable result as a unit --------------------


def test_adapter_requires_a_decision_evaluation_result() -> None:
    with pytest.raises(TypeError):
        _adapt({"decision_label": "CORRECT", "reason_codes": []})
    with pytest.raises(TypeError):
        _adapt("not-a-result")


def test_adapter_carries_through_authoritative_values() -> None:
    result = _result(run_id="run-42", decision_id="decision-99")
    adaptation = _adapt(result)
    assert adaptation.mapped
    draft = adaptation.draft
    assert draft.run_id == "run-42"
    assert draft.decision_id == "decision-99"
    assert "evidence:pre-decision" in draft.evidence_refs
    # Evaluator version travels with the assessment.
    assert any("decision-eval" in ref for ref in draft.evidence_refs)


def test_adapter_has_no_label_or_reason_parameters() -> None:
    """A caller cannot substitute a different label or reason tuple."""

    import inspect

    params = set(inspect.signature(adapt_decision_evaluation_result).parameters)
    assert "review_label" not in params
    assert "decision_label" not in params
    assert "reason_codes" not in params


def test_cannot_mix_data_from_two_different_results() -> None:
    """Label from one result cannot be combined with evidence from another."""

    correct = _result(run_id="run-A", decision_id="decision-A")
    ambiguous = _result(
        run_id="run-B",
        decision_id="decision-B",
        label=DecisionLabel.AMBIGUOUS,
        reasons=(ReasonCode.ONLY_NON_MATERIAL_FINDINGS,),
        evidence=("evidence:B",),
    )

    from_correct = _adapt(correct)
    from_ambiguous = _adapt(ambiguous)

    # Each adaptation reflects exactly one result; the ambiguous one does not
    # borrow the mappable label of the other.
    assert from_correct.mapped
    assert from_correct.draft.run_id == "run-A"
    assert not from_ambiguous.mapped

    # And identity always travels with the result it came from.
    assert from_correct.draft.decision_id == "decision-A"
    assert "evidence:B" not in from_correct.draft.evidence_refs


def test_label_follows_the_supplied_result_only() -> None:
    assert (
        _adapt(_result(label=DecisionLabel.CORRECT)).draft.review_label
        is DecisionReviewLabel.CORRECT
    )
    assert (
        _adapt(
            _result(
                label=DecisionLabel.INCORRECT,
                reasons=(ReasonCode.MATERIAL_PROBLEM_NOT_REPAIRABLE,),
            )
        ).draft.review_label
        is DecisionReviewLabel.INCORRECT
    )


# -- mapping rule (preserved from EVAL-01, accepted by review) ---------------


def test_ambiguous_maps_only_with_insufficient_evidence_reason() -> None:
    adaptation = _adapt(
        _result(
            label=DecisionLabel.AMBIGUOUS,
            reasons=(ReasonCode.INSUFFICIENT_PRE_DECISION_EVIDENCE,),
        )
    )
    assert adaptation.mapped
    assert adaptation.draft.review_label is DecisionReviewLabel.NOT_ENOUGH_EVIDENCE


@pytest.mark.parametrize(
    "reason",
    [
        ReasonCode.ONLY_NON_MATERIAL_FINDINGS,
        ReasonCode.REPAIR_AUTHORIZED,
        ReasonCode.RUNTIME_ACTION_POLICY_COMPLIANT,
        ReasonCode.ACCEPTANCE_FULLY_SATISFIED,
    ],
)
def test_ambiguous_with_unrelated_reason_is_unmappable(reason) -> None:
    adaptation = _adapt(_result(label=DecisionLabel.AMBIGUOUS, reasons=(reason,)))
    assert not adaptation.mapped
    assert adaptation.draft is None
    assert "REASONABLE must not be synthesized" in adaptation.unmappable_reason


def test_reasonable_is_never_produced() -> None:
    for label in DecisionLabel:
        for reasons in (
            (ReasonCode.ACCEPTANCE_FULLY_SATISFIED,),
            (ReasonCode.INSUFFICIENT_PRE_DECISION_EVIDENCE,),
            (ReasonCode.REPAIR_AUTHORIZED, ReasonCode.RUNTIME_ACTION_POLICY_COMPLIANT),
        ):
            adaptation = _adapt(_result(label=label, reasons=reasons))
            if adaptation.mapped:
                assert adaptation.draft.review_label is not DecisionReviewLabel.REASONABLE


def test_adaptation_result_is_exclusive() -> None:
    mapped = _adapt(_result())
    assert mapped.draft is not None and mapped.unmappable_reason is None
    unmapped = _adapt(
        _result(label=DecisionLabel.AMBIGUOUS, reasons=(ReasonCode.REPAIR_AUTHORIZED,))
    )
    assert unmapped.draft is None and unmapped.unmappable_reason is not None


def test_not_enough_evidence_draft_cites_evidence() -> None:
    adaptation = _adapt(
        _result(
            label=DecisionLabel.AMBIGUOUS,
            reasons=(ReasonCode.INSUFFICIENT_PRE_DECISION_EVIDENCE,),
            evidence=("evidence:examined",),
        )
    )
    assert adaptation.draft.evidence_refs


# -- authority separation ---------------------------------------------------


def test_evaluator_is_reused_not_reimplemented() -> None:
    import tomtit.evaluation.mapping as mapping_module
    import tomtit_decision_eval_v0.evaluator as evaluator_module

    assert hasattr(evaluator_module, "evaluate_decision")
    assert not hasattr(mapping_module, "evaluate_decision")


def test_original_decision_record_is_untouched(store) -> None:
    from tomtit.domain.evaluation import DecisionEventDraft

    fields_before = set(DecisionEventDraft.model_fields)
    draft = _adapt(_result(decision_id="decision-under-assessment")).draft
    store.append(draft, operational_source=RUNTIME)
    assert set(DecisionEventDraft.model_fields) == fields_before
    assert store.records[0].assessment.decision_id == "decision-under-assessment"


def test_adapted_draft_appends_and_gains_sequence(store) -> None:
    draft = _adapt(_result()).draft
    envelope = store.append(draft, operational_source=RUNTIME)
    assert envelope.assessment.append_sequence == 1


def test_appended_decision_assessment_is_immutable(store) -> None:
    envelope = store.append(_adapt(_result()).draft, operational_source=RUNTIME)
    with pytest.raises(ValidationError):
        envelope.assessment.review_label = DecisionReviewLabel.INCORRECT
