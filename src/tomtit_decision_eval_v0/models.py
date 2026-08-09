"""Immutable canonical contracts for offline TOMTIT decision evaluation V0."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BeforeValidator, ConfigDict, Field, model_validator
from pydantic.main import BaseModel


def _list_to_tuple(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(value)
    return value


NonEmptyString = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(ge=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
StringTuple = Annotated[tuple[NonEmptyString, ...], BeforeValidator(_list_to_tuple)]


class FrozenModel(BaseModel):
    """Strict, immutable base for every canonical evaluator record."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )


class StringEnum(str, Enum):
    """Stable string enum for JSON evidence and result records."""


class DecisionType(StringEnum):
    RETRY = "RETRY"
    STOP = "STOP"


class TomtitAction(StringEnum):
    RETRY = "RETRY"
    STOP = "STOP"


class ClaimStatus(StringEnum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


class CheckStatus(StringEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"
    UNKNOWN = "UNKNOWN"


class RequirementStatus(StringEnum):
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    UNKNOWN = "UNKNOWN"


class FindingMateriality(StringEnum):
    MATERIAL = "MATERIAL"
    NON_MATERIAL = "NON_MATERIAL"
    AMBIGUOUS = "AMBIGUOUS"


class ObjectiveTaskNeed(StringEnum):
    RETRY_NEEDED = "RETRY_NEEDED"
    RETRY_NOT_NEEDED = "RETRY_NOT_NEEDED"
    AMBIGUOUS = "AMBIGUOUS"


class IndependentJudgment(StringEnum):
    """Compatibility projection of objective_task_need."""

    RETRY_NEEDED = "RETRY_NEEDED"
    RETRY_NOT_NEEDED = "RETRY_NOT_NEEDED"
    AMBIGUOUS = "AMBIGUOUS"


class RepairAuthorization(StringEnum):
    AUTHORIZED = "AUTHORIZED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


class PolicyCompliance(StringEnum):
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"


class DecisionLabel(StringEnum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    AMBIGUOUS = "AMBIGUOUS"


class EvidenceProvenance(StringEnum):
    RUNTIME_REVIEWER = "RUNTIME_REVIEWER"
    DETERMINISTIC_ACCEPTANCE_CHECK = "DETERMINISTIC_ACCEPTANCE_CHECK"
    OWNER_ADJUDICATION = "OWNER_ADJUDICATION"
    INDEPENDENT_EVALUATOR = "INDEPENDENT_EVALUATOR"
    COUNTERFACTUAL_REPLAY = "COUNTERFACTUAL_REPLAY"
    RUNTIME_OUTCOME = "RUNTIME_OUTCOME"


class CounterfactualEvidence(StringEnum):
    VERIFIED = "VERIFIED"
    INFERRED = "INFERRED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class CounterfactualSourceType(StringEnum):
    DETERMINISTIC_ACCEPTANCE_CHECK = "DETERMINISTIC_ACCEPTANCE_CHECK"
    OWNER_ADJUDICATION = "OWNER_ADJUDICATION"
    INDEPENDENT_EVALUATOR = "INDEPENDENT_EVALUATOR"
    EXPLICIT_REPLAY = "EXPLICIT_REPLAY"
    RUNTIME_REVIEWER = "RUNTIME_REVIEWER"
    NORMAL_RUNTIME_OUTCOME = "NORMAL_RUNTIME_OUTCOME"


class RetryValue(StringEnum):
    IMPROVED = "IMPROVED"
    NO_MATERIAL_CHANGE = "NO_MATERIAL_CHANGE"
    HARMFUL = "HARMFUL"
    UNKNOWN = "UNKNOWN"


class ReasonCode(StringEnum):
    MATERIAL_REPAIRABLE_FINDING = "MATERIAL_REPAIRABLE_FINDING"
    REQUIRED_CHECK_FAILED_REPAIRABLE = "REQUIRED_CHECK_FAILED_REPAIRABLE"
    REQUIRED_ACCEPTANCE_UNSATISFIED_REPAIRABLE = (
        "REQUIRED_ACCEPTANCE_UNSATISFIED_REPAIRABLE"
    )
    MATERIAL_PROBLEM_NOT_REPAIRABLE = "MATERIAL_PROBLEM_NOT_REPAIRABLE"
    RUNTIME_REVIEWER_NOT_INDEPENDENTLY_SUBSTANTIATED = (
        "RUNTIME_REVIEWER_NOT_INDEPENDENTLY_SUBSTANTIATED"
    )
    ACCEPTANCE_FULLY_SATISFIED = "ACCEPTANCE_FULLY_SATISFIED"
    ONLY_NON_MATERIAL_FINDINGS = "ONLY_NON_MATERIAL_FINDINGS"
    INSUFFICIENT_PRE_DECISION_EVIDENCE = "INSUFFICIENT_PRE_DECISION_EVIDENCE"
    ACTION_MATCHES_OBJECTIVE_TASK_NEED = "ACTION_MATCHES_OBJECTIVE_TASK_NEED"
    ACTION_CONTRADICTS_OBJECTIVE_TASK_NEED = "ACTION_CONTRADICTS_OBJECTIVE_TASK_NEED"
    REPAIR_AUTHORIZED = "REPAIR_AUTHORIZED"
    REPAIR_NOT_AUTHORIZED = "REPAIR_NOT_AUTHORIZED"
    RUNTIME_ACTION_POLICY_COMPLIANT = "RUNTIME_ACTION_POLICY_COMPLIANT"
    RUNTIME_ACTION_POLICY_VIOLATION = "RUNTIME_ACTION_POLICY_VIOLATION"
    POST_RETRY_EVIDENCE_NOT_AVAILABLE = "POST_RETRY_EVIDENCE_NOT_AVAILABLE"
    RETRY_REDUCED_MATERIAL_ISSUES = "RETRY_REDUCED_MATERIAL_ISSUES"
    RETRY_IMPROVED_UNRELATED_QUALITY = "RETRY_IMPROVED_UNRELATED_QUALITY"
    RETRY_MADE_RESULT_HARMFUL = "RETRY_MADE_RESULT_HARMFUL"
    RETRY_MADE_NO_MATERIAL_CHANGE = "RETRY_MADE_NO_MATERIAL_CHANGE"


TRUSTED_GROUND_TRUTH_PROVENANCE = frozenset(
    {
        EvidenceProvenance.DETERMINISTIC_ACCEPTANCE_CHECK,
        EvidenceProvenance.OWNER_ADJUDICATION,
        EvidenceProvenance.INDEPENDENT_EVALUATOR,
        EvidenceProvenance.COUNTERFACTUAL_REPLAY,
    }
)


class EvidenceReference(FrozenModel):
    evidence_id: NonEmptyString
    run_id: NonEmptyString
    decision_id: NonEmptyString
    attempt: PositiveInt
    evaluates_attempt: PositiveInt
    sequence: PositiveInt
    kind: NonEmptyString
    provenance: EvidenceProvenance
    uri: NonEmptyString | None = None


class FindingImpact(FrozenModel):
    affects_acceptance: ClaimStatus = ClaimStatus.UNKNOWN
    affects_correctness: ClaimStatus = ClaimStatus.UNKNOWN
    affects_scope: ClaimStatus = ClaimStatus.UNKNOWN
    affects_safety_or_governance: ClaimStatus = ClaimStatus.UNKNOWN
    affects_required_behavior: ClaimStatus = ClaimStatus.UNKNOWN

    @property
    def claims(self) -> tuple[ClaimStatus, ...]:
        return (
            self.affects_acceptance,
            self.affects_correctness,
            self.affects_scope,
            self.affects_safety_or_governance,
            self.affects_required_behavior,
        )


class ReviewerFinding(FrozenModel):
    """Raw Runtime reviewer output; claimed impact is never ground truth."""

    finding_id: NonEmptyString
    evidence_id: NonEmptyString
    summary: NonEmptyString
    severity: NonEmptyString | None = None
    claimed_impact: FindingImpact = Field(default_factory=FindingImpact)
    claimed_repairable: ClaimStatus = ClaimStatus.UNKNOWN
    claimed_criterion_ids: StringTuple = Field(default_factory=tuple)

    @model_validator(mode="after")
    def require_unique_claimed_criteria(self) -> ReviewerFinding:
        if len(self.claimed_criterion_ids) != len(set(self.claimed_criterion_ids)):
            raise ValueError("reviewer claimed criterion IDs must be unique")
        return self


class FindingAdjudication(FrozenModel):
    """Independent, provenance-bound classification of one Runtime finding."""

    finding_id: NonEmptyString
    evidence_refs: StringTuple = Field(min_length=1)
    impact: FindingImpact
    repairable: ClaimStatus = ClaimStatus.UNKNOWN
    criterion_ids: StringTuple = Field(default_factory=tuple)

    @model_validator(mode="after")
    def require_unique_references(self) -> FindingAdjudication:
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("finding adjudication evidence references must be unique")
        if len(self.criterion_ids) != len(set(self.criterion_ids)):
            raise ValueError("finding adjudication criterion IDs must be unique")
        return self


class DeterministicCheckEvidence(FrozenModel):
    check_id: NonEmptyString
    evidence_id: NonEmptyString
    status: CheckStatus
    required_for_acceptance: bool
    repairable: ClaimStatus = ClaimStatus.UNKNOWN
    criterion_ids: StringTuple = Field(default_factory=tuple)

    @model_validator(mode="after")
    def require_unique_criteria(self) -> DeterministicCheckEvidence:
        if len(self.criterion_ids) != len(set(self.criterion_ids)):
            raise ValueError("deterministic check criterion IDs must be unique")
        return self


class AcceptanceRequirement(FrozenModel):
    criterion_id: NonEmptyString
    description: NonEmptyString
    required: bool = True
    status: RequirementStatus
    repairable: ClaimStatus = ClaimStatus.UNKNOWN
    evidence_ids: StringTuple = Field(default_factory=tuple)

    @model_validator(mode="after")
    def require_evidence_for_determinate_status(self) -> AcceptanceRequirement:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("acceptance requirement evidence IDs must be unique")
        if self.status is not RequirementStatus.UNKNOWN and not self.evidence_ids:
            raise ValueError("a determinate acceptance status must cite evidence")
        return self


class DecisionPoint(FrozenModel):
    """Runtime evidence and policy state available when TOMTIT decided."""

    run_id: NonEmptyString
    decision_id: NonEmptyString
    decision_type: DecisionType
    attempt: PositiveInt
    decision_timestamp: datetime
    decision_sequence: PositiveInt
    pre_decision_evidence: Annotated[
        tuple[EvidenceReference, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    tomtit_action: TomtitAction
    reviewer_findings: Annotated[
        tuple[ReviewerFinding, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    deterministic_checks: Annotated[
        tuple[DeterministicCheckEvidence, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    acceptance_requirements: Annotated[
        tuple[AcceptanceRequirement, ...], BeforeValidator(_list_to_tuple)
    ] = Field(min_length=1)
    repair_authorized: bool

    @model_validator(mode="after")
    def validate_decision_evidence(self) -> DecisionPoint:
        if self.decision_timestamp.tzinfo is None:
            raise ValueError("decision_timestamp must include a timezone")
        if self.decision_type.value != self.tomtit_action.value:
            raise ValueError("decision_type and tomtit_action must agree")

        evidence_by_id = {item.evidence_id: item for item in self.pre_decision_evidence}
        if len(evidence_by_id) != len(self.pre_decision_evidence):
            raise ValueError("pre-decision evidence IDs must be unique")
        for evidence in self.pre_decision_evidence:
            if (
                evidence.run_id != self.run_id
                or evidence.decision_id != self.decision_id
                or evidence.evaluates_attempt != self.attempt
            ):
                raise ValueError("pre-decision evidence identity does not match the decision")
            if evidence.attempt > self.attempt or evidence.sequence >= self.decision_sequence:
                raise ValueError("pre-decision evidence was not available at the decision point")

        criterion_by_id = {
            requirement.criterion_id: requirement
            for requirement in self.acceptance_requirements
        }
        if len(criterion_by_id) != len(self.acceptance_requirements):
            raise ValueError("acceptance requirement IDs must be unique")

        finding_ids = [finding.finding_id for finding in self.reviewer_findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("reviewer finding IDs must be unique")
        check_ids = [check.check_id for check in self.deterministic_checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("deterministic check IDs must be unique")

        for finding in self.reviewer_findings:
            evidence = self._require_known_evidence(finding.evidence_id, evidence_by_id)
            if evidence.provenance is not EvidenceProvenance.RUNTIME_REVIEWER:
                raise ValueError("Runtime reviewer findings require RUNTIME_REVIEWER provenance")
            self._require_known_criteria(finding.claimed_criterion_ids, criterion_by_id)
        for check in self.deterministic_checks:
            evidence = self._require_known_evidence(check.evidence_id, evidence_by_id)
            if evidence.provenance is not EvidenceProvenance.DETERMINISTIC_ACCEPTANCE_CHECK:
                raise ValueError(
                    "deterministic checks require DETERMINISTIC_ACCEPTANCE_CHECK provenance"
                )
            self._require_known_criteria(check.criterion_ids, criterion_by_id)
            if check.required_for_acceptance and not check.criterion_ids:
                raise ValueError("a required deterministic check must reference a criterion")
            if check.required_for_acceptance and check.status is CheckStatus.FAIL:
                satisfied = [
                    criterion_id
                    for criterion_id in check.criterion_ids
                    if criterion_by_id[criterion_id].status is RequirementStatus.SATISFIED
                ]
                if satisfied:
                    raise ValueError(
                        "a failed required check contradicts a satisfied linked requirement"
                    )
        for requirement in self.acceptance_requirements:
            for evidence_id in requirement.evidence_ids:
                self._require_known_evidence(evidence_id, evidence_by_id)
        return self

    @staticmethod
    def _require_known_evidence(
        evidence_id: str, evidence_by_id: dict[str, EvidenceReference]
    ) -> EvidenceReference:
        try:
            return evidence_by_id[evidence_id]
        except KeyError:
            raise ValueError(f"unknown pre-decision evidence reference: {evidence_id}") from None

    @staticmethod
    def _require_known_criteria(
        criterion_ids: tuple[str, ...],
        criterion_by_id: dict[str, AcceptanceRequirement],
    ) -> None:
        unknown = sorted(set(criterion_ids).difference(criterion_by_id))
        if unknown:
            raise ValueError(f"unknown acceptance requirement references: {unknown}")


class CounterfactualSource(FrozenModel):
    source_id: NonEmptyString
    source_type: CounterfactualSourceType
    run_id: NonEmptyString
    decision_id: NonEmptyString
    source_attempt: PositiveInt
    evaluates_attempt: PositiveInt
    evidence_ref: NonEmptyString


class PostRetryEvidence(FrozenModel):
    """Observations produced after RETRY; never part of objective task need."""

    run_id: NonEmptyString
    decision_id: NonEmptyString
    source_attempt: PositiveInt
    evidence_refs: StringTuple = Field(min_length=1)
    material_issue_count_before: NonNegativeInt | None = None
    material_issue_count_after: NonNegativeInt | None = None
    acceptance_regression: bool | None = None
    unrelated_quality_improved: bool | None = None
    final_outcome: NonEmptyString | None = None

    @model_validator(mode="after")
    def require_complete_issue_counts(self) -> PostRetryEvidence:
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("post-retry evidence references must be unique")
        before_missing = self.material_issue_count_before is None
        after_missing = self.material_issue_count_after is None
        if before_missing != after_missing:
            raise ValueError("material issue counts must provide both before and after")
        return self


_SOURCE_PROVENANCE = {
    CounterfactualSourceType.DETERMINISTIC_ACCEPTANCE_CHECK: (
        EvidenceProvenance.DETERMINISTIC_ACCEPTANCE_CHECK
    ),
    CounterfactualSourceType.OWNER_ADJUDICATION: EvidenceProvenance.OWNER_ADJUDICATION,
    CounterfactualSourceType.INDEPENDENT_EVALUATOR: EvidenceProvenance.INDEPENDENT_EVALUATOR,
    CounterfactualSourceType.EXPLICIT_REPLAY: EvidenceProvenance.COUNTERFACTUAL_REPLAY,
    CounterfactualSourceType.RUNTIME_REVIEWER: EvidenceProvenance.RUNTIME_REVIEWER,
    CounterfactualSourceType.NORMAL_RUNTIME_OUTCOME: EvidenceProvenance.RUNTIME_OUTCOME,
}
_VERIFIED_SOURCE_TYPES = frozenset(
    {
        CounterfactualSourceType.DETERMINISTIC_ACCEPTANCE_CHECK,
        CounterfactualSourceType.OWNER_ADJUDICATION,
        CounterfactualSourceType.INDEPENDENT_EVALUATOR,
        CounterfactualSourceType.EXPLICIT_REPLAY,
    }
)


class DecisionEvaluationInput(FrozenModel):
    schema_version: str = Field(
        default="decision-eval-input.v0.1", pattern=r"^decision-eval-input\.v0\.1$"
    )
    decision_point: DecisionPoint
    evaluation_evidence: Annotated[
        tuple[EvidenceReference, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    finding_adjudications: Annotated[
        tuple[FindingAdjudication, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    counterfactual_evidence: CounterfactualEvidence
    counterfactual_sources: Annotated[
        tuple[CounterfactualSource, ...], BeforeValidator(_list_to_tuple)
    ] = Field(default_factory=tuple)
    post_retry_evidence: PostRetryEvidence | None = None

    @model_validator(mode="after")
    def validate_cross_record_integrity(self) -> DecisionEvaluationInput:
        point = self.decision_point
        all_evidence = (*point.pre_decision_evidence, *self.evaluation_evidence)
        evidence_by_id = {item.evidence_id: item for item in all_evidence}
        if len(evidence_by_id) != len(all_evidence):
            raise ValueError("canonical evidence IDs must be unique across all evidence roles")
        for evidence in self.evaluation_evidence:
            if (
                evidence.run_id != point.run_id
                or evidence.decision_id != point.decision_id
                or evidence.evaluates_attempt != point.attempt
            ):
                raise ValueError("evaluation evidence identity does not match the decision")

        source_ids = [source.source_id for source in self.counterfactual_sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("counterfactual source IDs must be unique")
        if self.counterfactual_evidence is CounterfactualEvidence.NOT_AVAILABLE:
            if self.counterfactual_sources:
                raise ValueError("NOT_AVAILABLE counterfactual evidence cannot cite sources")
        elif not self.counterfactual_sources:
            raise ValueError("VERIFIED or INFERRED counterfactual evidence requires a source")

        source_evidence_ids: set[str] = set()
        for source in self.counterfactual_sources:
            if (
                source.run_id != point.run_id
                or source.decision_id != point.decision_id
                or source.evaluates_attempt != point.attempt
            ):
                raise ValueError("counterfactual source identity does not match the decision")
            evidence = self._require_evidence(source.evidence_ref, evidence_by_id)
            if source.source_attempt != evidence.attempt:
                raise ValueError("counterfactual source attempt does not match its evidence")
            if _SOURCE_PROVENANCE[source.source_type] is not evidence.provenance:
                raise ValueError("counterfactual source provenance does not match its evidence")
            source_evidence_ids.add(evidence.evidence_id)
        if self.counterfactual_evidence is CounterfactualEvidence.VERIFIED:
            invalid = [
                source.source_type
                for source in self.counterfactual_sources
                if source.source_type not in _VERIFIED_SOURCE_TYPES
            ]
            if invalid:
                raise ValueError("VERIFIED counterfactual evidence has invalid provenance")

        finding_by_id = {
            finding.finding_id for finding in point.reviewer_findings
        }
        adjudication_ids = [item.finding_id for item in self.finding_adjudications]
        if len(adjudication_ids) != len(set(adjudication_ids)):
            raise ValueError("finding adjudication IDs must be unique")
        criterion_ids = {
            requirement.criterion_id for requirement in point.acceptance_requirements
        }
        pre_decision_evidence_ids = {
            evidence.evidence_id for evidence in point.pre_decision_evidence
        }
        for adjudication in self.finding_adjudications:
            if adjudication.finding_id not in finding_by_id:
                raise ValueError("finding adjudication references an unknown Runtime finding")
            unknown_criteria = set(adjudication.criterion_ids).difference(criterion_ids)
            if unknown_criteria:
                raise ValueError("finding adjudication references an unknown criterion")
            for evidence_id in adjudication.evidence_refs:
                evidence = self._require_evidence(evidence_id, evidence_by_id)
                if evidence.provenance not in TRUSTED_GROUND_TRUTH_PROVENANCE:
                    raise ValueError("finding adjudication lacks trusted provenance")
                not_in_decision_snapshot = evidence_id not in pre_decision_evidence_ids
                if not_in_decision_snapshot and (
                    self.counterfactual_evidence is not CounterfactualEvidence.VERIFIED
                    or evidence_id not in source_evidence_ids
                ):
                    raise ValueError(
                        "post-decision finding adjudication requires VERIFIED counterfactual evidence"
                    )

        post = self.post_retry_evidence
        if point.tomtit_action is TomtitAction.STOP and post is not None:
            raise ValueError("a STOP decision cannot contain post-retry evidence")
        if post is not None:
            if post.run_id != point.run_id or post.decision_id != point.decision_id:
                raise ValueError("post-retry evidence identity does not match the decision")
            if post.source_attempt <= point.attempt:
                raise ValueError("post-retry evidence must come from a later attempt")
            for evidence_id in post.evidence_refs:
                evidence = self._require_evidence(evidence_id, evidence_by_id)
                if evidence.attempt != post.source_attempt:
                    raise ValueError("post-retry evidence attempt does not match its reference")
        return self

    @staticmethod
    def _require_evidence(
        evidence_id: str, evidence_by_id: dict[str, EvidenceReference]
    ) -> EvidenceReference:
        try:
            return evidence_by_id[evidence_id]
        except KeyError:
            raise ValueError(f"dangling canonical evidence reference: {evidence_id}") from None


class DecisionEvaluationResult(FrozenModel):
    run_id: NonEmptyString
    decision_id: NonEmptyString
    decision_type: DecisionType
    attempt: PositiveInt
    tomtit_action: TomtitAction
    objective_task_need: ObjectiveTaskNeed
    independent_judgment: IndependentJudgment
    repair_authorization: RepairAuthorization
    policy_compliance: PolicyCompliance
    decision_label: DecisionLabel
    material_finding_present: bool
    repairable_finding_present: bool
    counterfactual_evidence: CounterfactualEvidence
    retry_value: RetryValue
    evidence_refs: StringTuple
    retry_value_evidence_refs: StringTuple = Field(default_factory=tuple)
    reason_codes: Annotated[tuple[ReasonCode, ...], BeforeValidator(_list_to_tuple)]
    evaluation_version: str = Field(
        default="decision-eval.v0.2", pattern=r"^decision-eval\.v0\.2$"
    )

    @model_validator(mode="after")
    def require_canonical_result(self) -> DecisionEvaluationResult:
        if self.objective_task_need.value != self.independent_judgment.value:
            raise ValueError("independent_judgment must project objective_task_need")
        for name, values in (
            ("evidence_refs", self.evidence_refs),
            ("retry_value_evidence_refs", self.retry_value_evidence_refs),
            ("reason_codes", self.reason_codes),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
        if not self.reason_codes:
            raise ValueError("at least one reason code is required")
        return self


class MetricValue(FrozenModel):
    numerator: NonNegativeInt
    denominator: NonNegativeInt
    value: float | None = Field(default=None, ge=0, le=1)
    zero_denominator: bool

    @model_validator(mode="after")
    def validate_ratio(self) -> MetricValue:
        if self.zero_denominator != (self.denominator == 0):
            raise ValueError("zero_denominator must match denominator")
        if self.denominator == 0 and self.value is not None:
            raise ValueError("zero-denominator metric value must be null")
        if self.denominator > 0:
            expected = self.numerator / self.denominator
            if self.value is None or abs(self.value - expected) > 1e-12:
                raise ValueError("metric value does not match numerator/denominator")
        return self


class AggregateMetrics(FrozenModel):
    total_decisions: NonNegativeInt
    determinate_decisions: NonNegativeInt
    ambiguous_decisions: NonNegativeInt
    true_retry: NonNegativeInt
    false_retry: NonNegativeInt
    missed_retry: NonNegativeInt
    correct_stop: NonNegativeInt
    policy_compliant_decisions: NonNegativeInt
    policy_violations: NonNegativeInt
    authorization_blocked_needed_count: NonNegativeInt
    retry_precision: MetricValue
    retry_recall: MetricValue
    unnecessary_retry_rate: MetricValue
    missed_retry_rate: MetricValue
    repair_success_rate: MetricValue
    decision_accuracy: MetricValue
    ambiguous_rate: MetricValue
    policy_compliance_accuracy: MetricValue
    authorization_blocked_needed_rate: MetricValue
