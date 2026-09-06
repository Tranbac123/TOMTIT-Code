"""Explicit bounded mappings at the Evaluation boundary.

Every mapping can say "this does not map" rather than coercing.

Three upstream criterion-status vocabularies exist and are **not** consolidated
(their enums are untouched)::

    tomtit.domain.enums.CriterionStatus              SATISFIED/UNSATISFIED/UNRESOLVED
    tomtit.domain.enums.OwnerCriterionStatus         satisfied/unsatisfied/unresolved
    tomtit_decision_eval_v0.models.RequirementStatus SATISFIED/UNSATISFIED/UNKNOWN

``UNRESOLVED`` and ``UNKNOWN`` are both indeterminate but come from different
authorities and do not mean the same thing, so a translation retains its origin
vocabulary and two translations from different sources are unequal.
"""

from __future__ import annotations

from tomtit.domain.enums import (
    CriterionStatus,
    HumanInterventionSource,
    OwnerCriterionStatus,
)
from tomtit.evaluation.assessments import (
    DecisionAssessmentEventDraft,
    DecisionAssessmentMethod,
    DecisionReviewLabel,
    InterventionMeasurementMethod,
)
from tomtit.evaluation.provenance import EvaluationModel, StringEnum
from tomtit_decision_eval_v0.models import (
    DecisionEvaluationResult,
    DecisionLabel,
    ReasonCode,
    RequirementStatus,
)


class StatusOriginVocabulary(StringEnum):
    """Which upstream vocabulary an Evaluation status was translated from."""

    RUNTIME_CRITERION_STATUS = "RUNTIME_CRITERION_STATUS"
    OWNER_CRITERION_STATUS = "OWNER_CRITERION_STATUS"
    DECISION_EVAL_REQUIREMENT_STATUS = "DECISION_EVAL_REQUIREMENT_STATUS"


class EvaluationCriterionStatus(StringEnum):
    """Adapter-level projection of criterion status — **not** a Contract V1 vocabulary.

    Contract V1 defines no canonical criterion-status enum for Evaluation; this
    is a derived projection used only at this boundary to compare statuses
    coming from three different upstream authorities. ``INDETERMINATE`` covers
    both ``UNRESOLVED`` and ``UNKNOWN`` without asserting they are equivalent,
    because :class:`TranslatedCriterionStatus` carries the origin alongside it.
    """

    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    INDETERMINATE = "INDETERMINATE"


class TranslatedCriterionStatus(EvaluationModel):
    """A derived adapter projection plus the vocabulary it came from.

    Two translations that both produced ``INDETERMINATE`` from different
    upstream vocabularies are not equal, which is what stops ``UNRESOLVED`` and
    ``UNKNOWN`` from silently collapsing into one another.
    """

    status: EvaluationCriterionStatus
    origin_vocabulary: StatusOriginVocabulary
    origin_value: str


def translate_runtime_criterion_status(
    value: CriterionStatus,
) -> TranslatedCriterionStatus:
    """Project Runtime ``CriterionStatus`` without consolidating it."""

    mapping = {
        CriterionStatus.SATISFIED: EvaluationCriterionStatus.SATISFIED,
        CriterionStatus.UNSATISFIED: EvaluationCriterionStatus.UNSATISFIED,
        CriterionStatus.UNRESOLVED: EvaluationCriterionStatus.INDETERMINATE,
    }
    return TranslatedCriterionStatus(
        status=mapping[value],
        origin_vocabulary=StatusOriginVocabulary.RUNTIME_CRITERION_STATUS,
        origin_value=value.value,
    )


def translate_owner_criterion_status(
    value: OwnerCriterionStatus,
) -> TranslatedCriterionStatus:
    """Project ``OwnerCriterionStatus`` without consolidating it."""

    mapping = {
        OwnerCriterionStatus.SATISFIED: EvaluationCriterionStatus.SATISFIED,
        OwnerCriterionStatus.UNSATISFIED: EvaluationCriterionStatus.UNSATISFIED,
        OwnerCriterionStatus.UNRESOLVED: EvaluationCriterionStatus.INDETERMINATE,
    }
    return TranslatedCriterionStatus(
        status=mapping[value],
        origin_vocabulary=StatusOriginVocabulary.OWNER_CRITERION_STATUS,
        origin_value=value.value,
    )


def translate_requirement_status(
    value: RequirementStatus,
) -> TranslatedCriterionStatus:
    """Project decision-eval ``RequirementStatus`` without consolidating it."""

    mapping = {
        RequirementStatus.SATISFIED: EvaluationCriterionStatus.SATISFIED,
        RequirementStatus.UNSATISFIED: EvaluationCriterionStatus.UNSATISFIED,
        RequirementStatus.UNKNOWN: EvaluationCriterionStatus.INDETERMINATE,
    }
    return TranslatedCriterionStatus(
        status=mapping[value],
        origin_vocabulary=StatusOriginVocabulary.DECISION_EVAL_REQUIREMENT_STATUS,
        origin_value=value.value,
    )


# ---------------------------------------------------------------------------
# Decision assessment adapter (Contract V1 §14; F08).
# ---------------------------------------------------------------------------


class DecisionAssessmentAdaptation(EvaluationModel):
    """Outcome of adapting one immutable ``DecisionEvaluationResult``.

    Exactly one of ``draft`` / ``unmappable_reason`` is set.
    """

    draft: DecisionAssessmentEventDraft | None = None
    unmappable_reason: str | None = None

    @property
    def mapped(self) -> bool:
        return self.draft is not None


def _review_label_for(
    result: DecisionEvaluationResult,
) -> tuple[DecisionReviewLabel | None, str | None]:
    """Derive a Contract V1 §14 label from one immutable evaluator result.

    * ``CORRECT`` → ``CORRECT``; ``INCORRECT`` → ``INCORRECT``.
    * ``AMBIGUOUS`` → ``NOT_ENOUGH_EVIDENCE`` **only** when that same result
      recorded ``INSUFFICIENT_PRE_DECISION_EVIDENCE`` — the evaluator's own
      statement that evidence was inadequate.
    * Any other ``AMBIGUOUS`` is unmappable: ambiguity about which action was
      better is not the same as missing evidence.
    * ``REASONABLE`` is never produced. Nothing in the evaluator expresses
      "defensible but not optimal", so synthesizing it from CORRECT, repair
      authorization, policy compliance, or ambiguity would be fabrication.
    """

    if result.decision_label is DecisionLabel.CORRECT:
        return DecisionReviewLabel.CORRECT, None
    if result.decision_label is DecisionLabel.INCORRECT:
        return DecisionReviewLabel.INCORRECT, None
    if ReasonCode.INSUFFICIENT_PRE_DECISION_EVIDENCE in result.reason_codes:
        return DecisionReviewLabel.NOT_ENOUGH_EVIDENCE, None
    return None, (
        "DecisionLabel.AMBIGUOUS without INSUFFICIENT_PRE_DECISION_EVIDENCE "
        "cannot be mapped to a Contract V1 review label: this result recorded "
        "ambiguity about the decision, not that evidence was insufficient, and "
        "V1 has no label for that. REASONABLE must not be synthesized."
    )


def adapt_decision_evaluation_result(
    result: DecisionEvaluationResult,
    *,
    assessment_id: str,
    assessment_method: DecisionAssessmentMethod,
    reviewer_or_experiment_id: str,
    timestamp,
    later_observed_outcome: str | None = None,
) -> DecisionAssessmentAdaptation:
    """Adapt one immutable ``DecisionEvaluationResult`` into a §14 draft.

    The result is the **single authoritative input** for label, reason
    semantics, run and decision identity, evidence refs, and evaluator version.
    There is no parameter through which a caller can substitute a different
    label or a different reason tuple, so fields from two different results can
    never be combined into one assessment.

    The evaluator itself is reused, never reimplemented or modified: this
    function only translates its output into the Contract V1 envelope.
    """

    if not isinstance(result, DecisionEvaluationResult):
        raise TypeError(
            "adapt_decision_evaluation_result requires an immutable "
            "DecisionEvaluationResult produced by "
            "tomtit_decision_eval_v0.evaluator.evaluate_decision"
        )

    review_label, unmappable = _review_label_for(result)
    if review_label is None:
        return DecisionAssessmentAdaptation(unmappable_reason=unmappable)

    # Evidence is carried through from the same result, de-duplicated in the
    # result's own canonical order. The evaluator version travels with it so an
    # assessment can be traced to the exact evaluator contract that produced it.
    evidence: list[str] = []
    for ref in (*result.evidence_refs, *result.retry_value_evidence_refs):
        if ref not in evidence:
            evidence.append(ref)
    evidence.append(f"decision-eval-version:{result.evaluation_version}")

    return DecisionAssessmentAdaptation(
        draft=DecisionAssessmentEventDraft(
            assessment_id=assessment_id,
            run_id=result.run_id,
            decision_id=result.decision_id,
            later_observed_outcome=later_observed_outcome,
            review_label=review_label,
            assessment_method=assessment_method,
            reviewer_or_experiment_id=reviewer_or_experiment_id,
            timestamp=timestamp,
            evidence_refs=tuple(evidence),
        )
    )


# ---------------------------------------------------------------------------
# Intervention measurement method (Contract V1 §16 vs legacy snapshot source).
# ---------------------------------------------------------------------------


class MeasurementMethodMapping(EvaluationModel):
    """Outcome of mapping legacy ``HumanInterventionSource`` onto V1."""

    measurement_method: InterventionMeasurementMethod | None = None
    unmappable_reason: str | None = None

    @property
    def mapped(self) -> bool:
        return self.measurement_method is not None


def map_human_intervention_source(
    source: HumanInterventionSource,
) -> MeasurementMethodMapping:
    """Map the legacy snapshot source vocabulary onto V1 §16 methods.

    ``INTERACTION_TIMESTAMPS`` is deliberately unmapped: timestamps recorded
    around an interaction are neither a measured UI duration nor a self-report
    nor an explicit estimate, and choosing any of them would misstate
    measurement quality.
    """

    if source is HumanInterventionSource.EXPLICIT_USER_REPORT:
        return MeasurementMethodMapping(
            measurement_method=InterventionMeasurementMethod.USER_SELF_REPORTED
        )
    if source is HumanInterventionSource.UNAVAILABLE:
        return MeasurementMethodMapping(
            measurement_method=InterventionMeasurementMethod.UNAVAILABLE
        )
    return MeasurementMethodMapping(
        unmappable_reason=(
            "HumanInterventionSource.INTERACTION_TIMESTAMPS has no Contract V1 "
            "§16 equivalent: it is neither UI_MEASURED nor USER_SELF_REPORTED "
            "nor ESTIMATED. The raw intervention event must state its own "
            "measurement_method."
        )
    )
