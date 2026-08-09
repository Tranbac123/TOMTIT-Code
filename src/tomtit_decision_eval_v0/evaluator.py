"""Deterministic, hindsight-isolated RETRY/STOP decision evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    AcceptanceRequirement,
    CheckStatus,
    ClaimStatus,
    DecisionEvaluationInput,
    DecisionEvaluationResult,
    DecisionLabel,
    EvidenceProvenance,
    FindingAdjudication,
    FindingMateriality,
    IndependentJudgment,
    ObjectiveTaskNeed,
    PolicyCompliance,
    ReasonCode,
    RepairAuthorization,
    RequirementStatus,
    RetryValue,
    TomtitAction,
)


def classify_finding_materiality(
    adjudication: FindingAdjudication,
) -> FindingMateriality:
    """Classify an independently grounded adjudication, never reviewer severity."""

    claims = adjudication.impact.claims
    if any(claim is ClaimStatus.YES for claim in claims) or adjudication.criterion_ids:
        return FindingMateriality.MATERIAL
    if all(claim is ClaimStatus.NO for claim in claims):
        return FindingMateriality.NON_MATERIAL
    return FindingMateriality.AMBIGUOUS


@dataclass(frozen=True, slots=True)
class _Judgment:
    value: ObjectiveTaskNeed
    material_finding_present: bool
    repairable_finding_present: bool
    reasons: tuple[ReasonCode, ...]


def _append_unique(items: list[ReasonCode], value: ReasonCode) -> None:
    if value not in items:
        items.append(value)


def _required_requirements(
    evaluation_input: DecisionEvaluationInput,
) -> tuple[AcceptanceRequirement, ...]:
    return tuple(
        requirement
        for requirement in evaluation_input.decision_point.acceptance_requirements
        if requirement.required
    )


def _grounded_requirement_status(
    requirement: AcceptanceRequirement,
    evaluation_input: DecisionEvaluationInput,
) -> RequirementStatus:
    evidence_by_id = {
        evidence.evidence_id: evidence
        for evidence in evaluation_input.decision_point.pre_decision_evidence
    }
    cited_evidence = tuple(evidence_by_id[evidence_id] for evidence_id in requirement.evidence_ids)
    independently_adjudicated = any(
        evidence.provenance
        in {
            EvidenceProvenance.OWNER_ADJUDICATION,
            EvidenceProvenance.INDEPENDENT_EVALUATOR,
            EvidenceProvenance.COUNTERFACTUAL_REPLAY,
        }
        for evidence in cited_evidence
    )
    if independently_adjudicated:
        return requirement.status

    linked_checks = tuple(
        check
        for check in evaluation_input.decision_point.deterministic_checks
        if check.required_for_acceptance
        and requirement.criterion_id in check.criterion_ids
        and check.evidence_id in requirement.evidence_ids
    )
    if requirement.status is RequirementStatus.UNSATISFIED and any(
        check.status is CheckStatus.FAIL for check in linked_checks
    ):
        return requirement.status
    if (
        requirement.status is RequirementStatus.SATISFIED
        and linked_checks
        and all(check.status is CheckStatus.PASS for check in linked_checks)
    ):
        return requirement.status
    return RequirementStatus.UNKNOWN


def _objective_task_need(evaluation_input: DecisionEvaluationInput) -> _Judgment:
    """Determine objective repair need without consulting authorization or future outcome."""

    point = evaluation_input.decision_point
    reasons: list[ReasonCode] = []
    adjudication_by_finding = {
        adjudication.finding_id: adjudication
        for adjudication in evaluation_input.finding_adjudications
    }

    classified_adjudications = tuple(
        (adjudication, classify_finding_materiality(adjudication))
        for adjudication in evaluation_input.finding_adjudications
    )
    material_adjudications = tuple(
        adjudication
        for adjudication, materiality in classified_adjudications
        if materiality is FindingMateriality.MATERIAL
    )
    ambiguous_adjudications = tuple(
        adjudication
        for adjudication, materiality in classified_adjudications
        if materiality is FindingMateriality.AMBIGUOUS
    )
    non_material_adjudications = tuple(
        adjudication
        for adjudication, materiality in classified_adjudications
        if materiality is FindingMateriality.NON_MATERIAL
    )
    unsubstantiated_findings = tuple(
        finding
        for finding in point.reviewer_findings
        if finding.finding_id not in adjudication_by_finding
    )
    repairable_material_adjudications = tuple(
        adjudication
        for adjudication in material_adjudications
        if adjudication.repairable is ClaimStatus.YES
    )
    uncertain_material_adjudications = tuple(
        adjudication
        for adjudication in material_adjudications
        if adjudication.repairable is ClaimStatus.UNKNOWN
    )

    failed_required_checks = tuple(
        check
        for check in point.deterministic_checks
        if check.required_for_acceptance and check.status is CheckStatus.FAIL
    )
    repairable_failed_checks = tuple(
        check for check in failed_required_checks if check.repairable is ClaimStatus.YES
    )
    uncertain_failed_checks = tuple(
        check for check in failed_required_checks if check.repairable is ClaimStatus.UNKNOWN
    )
    incomplete_required_checks = tuple(
        check
        for check in point.deterministic_checks
        if check.required_for_acceptance
        and check.status in (CheckStatus.NOT_RUN, CheckStatus.UNKNOWN)
    )

    required_requirements = _required_requirements(evaluation_input)
    requirement_statuses = {
        requirement.criterion_id: _grounded_requirement_status(
            requirement, evaluation_input
        )
        for requirement in required_requirements
    }
    unsatisfied_requirements = tuple(
        requirement
        for requirement in required_requirements
        if requirement_statuses[requirement.criterion_id]
        is RequirementStatus.UNSATISFIED
    )
    repairable_unsatisfied_requirements = tuple(
        requirement
        for requirement in unsatisfied_requirements
        if requirement.repairable is ClaimStatus.YES
    )
    uncertain_unsatisfied_requirements = tuple(
        requirement
        for requirement in unsatisfied_requirements
        if requirement.repairable is ClaimStatus.UNKNOWN
    )
    unknown_requirements = tuple(
        requirement
        for requirement in required_requirements
        if requirement_statuses[requirement.criterion_id] is RequirementStatus.UNKNOWN
    )

    retryable_problem = bool(
        repairable_material_adjudications
        or repairable_failed_checks
        or repairable_unsatisfied_requirements
    )
    if repairable_material_adjudications:
        _append_unique(reasons, ReasonCode.MATERIAL_REPAIRABLE_FINDING)
    if repairable_failed_checks:
        _append_unique(reasons, ReasonCode.REQUIRED_CHECK_FAILED_REPAIRABLE)
    if repairable_unsatisfied_requirements:
        _append_unique(
            reasons, ReasonCode.REQUIRED_ACCEPTANCE_UNSATISFIED_REPAIRABLE
        )

    material_finding_present = bool(material_adjudications)
    repairable_finding_present = bool(repairable_material_adjudications)
    if retryable_problem:
        return _Judgment(
            ObjectiveTaskNeed.RETRY_NEEDED,
            material_finding_present,
            repairable_finding_present,
            tuple(reasons),
        )

    if unsubstantiated_findings:
        _append_unique(
            reasons, ReasonCode.RUNTIME_REVIEWER_NOT_INDEPENDENTLY_SUBSTANTIATED
        )
    ambiguity_present = bool(
        unsubstantiated_findings
        or ambiguous_adjudications
        or uncertain_material_adjudications
        or uncertain_failed_checks
        or incomplete_required_checks
        or uncertain_unsatisfied_requirements
        or unknown_requirements
        or not required_requirements
    )
    if ambiguity_present:
        _append_unique(reasons, ReasonCode.INSUFFICIENT_PRE_DECISION_EVIDENCE)
        return _Judgment(
            ObjectiveTaskNeed.AMBIGUOUS,
            material_finding_present,
            repairable_finding_present,
            tuple(reasons),
        )

    nonrepairable_problem = bool(
        material_adjudications or failed_required_checks or unsatisfied_requirements
    )
    if nonrepairable_problem:
        _append_unique(reasons, ReasonCode.MATERIAL_PROBLEM_NOT_REPAIRABLE)
        return _Judgment(
            ObjectiveTaskNeed.RETRY_NOT_NEEDED,
            material_finding_present,
            repairable_finding_present,
            tuple(reasons),
        )

    all_requirements_satisfied = all(
        requirement_statuses[requirement.criterion_id]
        is RequirementStatus.SATISFIED
        for requirement in required_requirements
    )
    all_required_checks_passed = all(
        check.status is CheckStatus.PASS
        for check in point.deterministic_checks
        if check.required_for_acceptance
    )
    if all_requirements_satisfied and all_required_checks_passed:
        _append_unique(reasons, ReasonCode.ACCEPTANCE_FULLY_SATISFIED)
        if non_material_adjudications:
            _append_unique(reasons, ReasonCode.ONLY_NON_MATERIAL_FINDINGS)
        return _Judgment(
            ObjectiveTaskNeed.RETRY_NOT_NEEDED,
            material_finding_present,
            repairable_finding_present,
            tuple(reasons),
        )

    _append_unique(reasons, ReasonCode.INSUFFICIENT_PRE_DECISION_EVIDENCE)
    return _Judgment(
        ObjectiveTaskNeed.AMBIGUOUS,
        material_finding_present,
        repairable_finding_present,
        tuple(reasons),
    )


def _decision_label(
    action: TomtitAction, objective_need: ObjectiveTaskNeed
) -> tuple[DecisionLabel, ReasonCode | None]:
    if objective_need is ObjectiveTaskNeed.AMBIGUOUS:
        return DecisionLabel.AMBIGUOUS, None
    action_matches = (
        action is TomtitAction.RETRY
        and objective_need is ObjectiveTaskNeed.RETRY_NEEDED
    ) or (
        action is TomtitAction.STOP
        and objective_need is ObjectiveTaskNeed.RETRY_NOT_NEEDED
    )
    if action_matches:
        return DecisionLabel.CORRECT, ReasonCode.ACTION_MATCHES_OBJECTIVE_TASK_NEED
    return DecisionLabel.INCORRECT, ReasonCode.ACTION_CONTRADICTS_OBJECTIVE_TASK_NEED


def _authorization_and_policy(
    action: TomtitAction, repair_authorized: bool
) -> tuple[RepairAuthorization, PolicyCompliance, tuple[ReasonCode, ReasonCode]]:
    authorization = (
        RepairAuthorization.AUTHORIZED
        if repair_authorized
        else RepairAuthorization.NOT_AUTHORIZED
    )
    authorization_reason = (
        ReasonCode.REPAIR_AUTHORIZED
        if repair_authorized
        else ReasonCode.REPAIR_NOT_AUTHORIZED
    )
    violation = action is TomtitAction.RETRY and not repair_authorized
    compliance = (
        PolicyCompliance.NON_COMPLIANT if violation else PolicyCompliance.COMPLIANT
    )
    compliance_reason = (
        ReasonCode.RUNTIME_ACTION_POLICY_VIOLATION
        if violation
        else ReasonCode.RUNTIME_ACTION_POLICY_COMPLIANT
    )
    return authorization, compliance, (authorization_reason, compliance_reason)


def _retry_value(
    evaluation_input: DecisionEvaluationInput,
) -> tuple[RetryValue, tuple[str, ...], ReasonCode]:
    post = evaluation_input.post_retry_evidence
    if post is None:
        return RetryValue.UNKNOWN, (), ReasonCode.POST_RETRY_EVIDENCE_NOT_AVAILABLE

    before = post.material_issue_count_before
    after = post.material_issue_count_after
    if post.acceptance_regression is True or (
        before is not None and after is not None and after > before
    ):
        return (
            RetryValue.HARMFUL,
            post.evidence_refs,
            ReasonCode.RETRY_MADE_RESULT_HARMFUL,
        )
    if before is not None and after is not None and after < before:
        return (
            RetryValue.IMPROVED,
            post.evidence_refs,
            ReasonCode.RETRY_REDUCED_MATERIAL_ISSUES,
        )
    if post.unrelated_quality_improved is True:
        return (
            RetryValue.IMPROVED,
            post.evidence_refs,
            ReasonCode.RETRY_IMPROVED_UNRELATED_QUALITY,
        )
    if before is not None and after is not None and after == before:
        return (
            RetryValue.NO_MATERIAL_CHANGE,
            post.evidence_refs,
            ReasonCode.RETRY_MADE_NO_MATERIAL_CHANGE,
        )
    return RetryValue.UNKNOWN, post.evidence_refs, ReasonCode.POST_RETRY_EVIDENCE_NOT_AVAILABLE


def evaluate_decision(evaluation_input: DecisionEvaluationInput) -> DecisionEvaluationResult:
    """Evaluate objective need, policy compliance, and retry value independently."""

    point = evaluation_input.decision_point
    judgment = _objective_task_need(evaluation_input)
    label, label_reason = _decision_label(point.tomtit_action, judgment.value)
    authorization, compliance, policy_reasons = _authorization_and_policy(
        point.tomtit_action, point.repair_authorized
    )
    retry_value, retry_refs, retry_reason = _retry_value(evaluation_input)

    reasons = list(judgment.reasons)
    if label_reason is not None:
        _append_unique(reasons, label_reason)
    for reason in policy_reasons:
        _append_unique(reasons, reason)
    _append_unique(reasons, retry_reason)

    evidence_refs = tuple(
        evidence.evidence_id
        for evidence in (
            *point.pre_decision_evidence,
            *evaluation_input.evaluation_evidence,
        )
    )
    return DecisionEvaluationResult(
        run_id=point.run_id,
        decision_id=point.decision_id,
        decision_type=point.decision_type,
        attempt=point.attempt,
        tomtit_action=point.tomtit_action,
        objective_task_need=judgment.value,
        independent_judgment=IndependentJudgment(judgment.value.value),
        repair_authorization=authorization,
        policy_compliance=compliance,
        decision_label=label,
        material_finding_present=judgment.material_finding_present,
        repairable_finding_present=judgment.repairable_finding_present,
        counterfactual_evidence=evaluation_input.counterfactual_evidence,
        retry_value=retry_value,
        evidence_refs=evidence_refs,
        retry_value_evidence_refs=retry_refs,
        reason_codes=tuple(reasons),
    )
