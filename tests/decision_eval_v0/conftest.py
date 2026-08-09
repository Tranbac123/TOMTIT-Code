from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tomtit_decision_eval_v0 import (
    AcceptanceRequirement,
    CheckStatus,
    ClaimStatus,
    CounterfactualEvidence,
    CounterfactualSource,
    CounterfactualSourceType,
    DecisionEvaluationInput,
    DecisionPoint,
    DecisionType,
    DeterministicCheckEvidence,
    EvidenceProvenance,
    EvidenceReference,
    FindingAdjudication,
    FindingImpact,
    PostRetryEvidence,
    RequirementStatus,
    ReviewerFinding,
    TomtitAction,
)


def material_impact() -> FindingImpact:
    return FindingImpact(affects_correctness=ClaimStatus.YES)


def non_material_impact() -> FindingImpact:
    return FindingImpact(
        affects_acceptance=ClaimStatus.NO,
        affects_correctness=ClaimStatus.NO,
        affects_scope=ClaimStatus.NO,
        affects_safety_or_governance=ClaimStatus.NO,
        affects_required_behavior=ClaimStatus.NO,
    )


def canonical_evidence(
    evidence_id: str,
    provenance: EvidenceProvenance,
    *,
    attempt: int,
    sequence: int,
    kind: str,
) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=evidence_id,
        run_id="run-1",
        decision_id="decision-1",
        attempt=attempt,
        evaluates_attempt=1,
        sequence=sequence,
        kind=kind,
        provenance=provenance,
    )


@pytest.fixture
def material_finding() -> ReviewerFinding:
    return ReviewerFinding(
        finding_id="finding-material",
        evidence_id="runtime-review",
        summary="Required behavior returns the wrong value",
        severity="LOW",
        claimed_impact=material_impact(),
        claimed_repairable=ClaimStatus.YES,
        claimed_criterion_ids=("criterion-1",),
    )


@pytest.fixture
def cosmetic_finding() -> ReviewerFinding:
    return ReviewerFinding(
        finding_id="finding-cosmetic",
        evidence_id="runtime-review",
        summary="Optional naming preference",
        severity="BLOCKER",
        claimed_impact=material_impact(),
        claimed_repairable=ClaimStatus.YES,
        claimed_criterion_ids=("criterion-1",),
    )


@pytest.fixture
def material_adjudication() -> FindingAdjudication:
    return FindingAdjudication(
        finding_id="finding-material",
        evidence_refs=("independent-adjudication",),
        impact=material_impact(),
        repairable=ClaimStatus.YES,
        criterion_ids=("criterion-1",),
    )


@pytest.fixture
def cosmetic_adjudication() -> FindingAdjudication:
    return FindingAdjudication(
        finding_id="finding-cosmetic",
        evidence_refs=("independent-adjudication",),
        impact=non_material_impact(),
        repairable=ClaimStatus.YES,
    )


@pytest.fixture
def make_case():
    def factory(
        action: TomtitAction,
        *,
        requirement_status: RequirementStatus = RequirementStatus.SATISFIED,
        requirement_repairable: ClaimStatus = ClaimStatus.UNKNOWN,
        finding: ReviewerFinding | None = None,
        adjudication: FindingAdjudication | None = None,
        check_status: CheckStatus = CheckStatus.PASS,
        check_repairable: ClaimStatus = ClaimStatus.UNKNOWN,
        repair_authorized: bool = True,
        post: PostRetryEvidence | None = None,
        counterfactual: CounterfactualEvidence | None = None,
        extra_evaluation_evidence: tuple[EvidenceReference, ...] = (),
        explicit_counterfactual_sources: tuple[CounterfactualSource, ...] | None = None,
    ) -> DecisionEvaluationInput:
        check_evidence = canonical_evidence(
            "required-check-evidence",
            EvidenceProvenance.DETERMINISTIC_ACCEPTANCE_CHECK,
            attempt=1,
            sequence=2,
            kind="deterministic-check",
        )
        pre_evidence = [check_evidence]
        if finding is not None:
            pre_evidence.append(
                canonical_evidence(
                    "runtime-review",
                    EvidenceProvenance.RUNTIME_REVIEWER,
                    attempt=1,
                    sequence=3,
                    kind="runtime-review",
                )
            )

        evaluation_evidence = list(extra_evaluation_evidence)
        sources: list[CounterfactualSource] = []
        if adjudication is not None:
            independent = canonical_evidence(
                "independent-adjudication",
                EvidenceProvenance.INDEPENDENT_EVALUATOR,
                attempt=1,
                sequence=6,
                kind="independent-finding-adjudication",
            )
            evaluation_evidence.append(independent)
            sources.append(
                CounterfactualSource(
                    source_id="independent-source",
                    source_type=CounterfactualSourceType.INDEPENDENT_EVALUATOR,
                    run_id="run-1",
                    decision_id="decision-1",
                    source_attempt=1,
                    evaluates_attempt=1,
                    evidence_ref=independent.evidence_id,
                )
            )
        if post is not None:
            evaluation_evidence.append(
                canonical_evidence(
                    "attempt-2-evidence",
                    EvidenceProvenance.RUNTIME_OUTCOME,
                    attempt=2,
                    sequence=8,
                    kind="normal-attempt-outcome",
                )
            )

        if explicit_counterfactual_sources is not None:
            sources = list(explicit_counterfactual_sources)
        if counterfactual is None:
            counterfactual = (
                CounterfactualEvidence.VERIFIED
                if adjudication is not None
                else CounterfactualEvidence.NOT_AVAILABLE
            )

        point = DecisionPoint(
            run_id="run-1",
            decision_id="decision-1",
            decision_type=DecisionType(action.value),
            attempt=1,
            decision_timestamp=datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc),
            decision_sequence=5,
            pre_decision_evidence=tuple(pre_evidence),
            tomtit_action=action,
            reviewer_findings=() if finding is None else (finding,),
            deterministic_checks=(
                DeterministicCheckEvidence(
                    check_id="required-check",
                    evidence_id=check_evidence.evidence_id,
                    status=CheckStatus(check_status),
                    required_for_acceptance=True,
                    repairable=check_repairable,
                    criterion_ids=("criterion-1",),
                ),
            ),
            acceptance_requirements=(
                AcceptanceRequirement(
                    criterion_id="criterion-1",
                    description="Required behavior is correct",
                    status=requirement_status,
                    repairable=requirement_repairable,
                    evidence_ids=(check_evidence.evidence_id,),
                ),
            ),
            repair_authorized=repair_authorized,
        )
        return DecisionEvaluationInput(
            decision_point=point,
            evaluation_evidence=tuple(evaluation_evidence),
            finding_adjudications=() if adjudication is None else (adjudication,),
            counterfactual_evidence=counterfactual,
            counterfactual_sources=tuple(sources),
            post_retry_evidence=post,
        )

    return factory


def post_retry(
    *,
    before: int | None = None,
    after: int | None = None,
    acceptance_regression: bool | None = False,
    unrelated_quality_improved: bool | None = False,
    final_outcome: str | None = None,
) -> PostRetryEvidence:
    return PostRetryEvidence(
        run_id="run-1",
        decision_id="decision-1",
        source_attempt=2,
        evidence_refs=("attempt-2-evidence",),
        material_issue_count_before=before,
        material_issue_count_after=after,
        acceptance_regression=acceptance_regression,
        unrelated_quality_improved=unrelated_quality_improved,
        final_outcome=final_outcome,
    )
