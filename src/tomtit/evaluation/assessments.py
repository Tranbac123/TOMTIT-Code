"""Contract V1 Evaluation assessment events.

Layering (F01, F02)
-------------------
Three distinct shapes, deliberately separated::

    XxxEventDraft          caller-facing, no append_sequence, pure V1 fields
        -> store assigns sequence
    XxxEvent               canonical persisted V1 event, *contains* append_sequence
        -> wrapped for source-aware storage
    SourceAwareAssessment  integration envelope carrying operational_source

``operational_source`` is **not** a Contract V1 event field. V1's §9/§10/§14/
§16/§23 field lists do not contain it, so it lives on the envelope rather than
inside the canonical event. The envelope is an integration concern introduced
by the R2.2 amendment; the persisted event stays literal-V1 compatible.

``append_sequence`` *is* a Contract V1 event field (§8 lists it in every
assessment schema), so it lives inside the persisted event. Drafts structurally
cannot carry it, which is how §8's "must not be supplied or overridden by
Coordinator, Evaluation, reviewer, Owner, external caller" is enforced.

Schema versions (F05)
---------------------
Contract V1 requires ``assessment_schema_version`` and
``intervention_schema_version`` fields but **does not specify their literal
string values** — the only literal the document fixes is ``report.v1.json``
(§4.2) for report serialization. These constants are therefore this
implementation's pinned values, enforced as ``Literal`` so an arbitrary version
string is rejected. If a future Owner-approved document fixes different
literals, these change with it.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator

from tomtit.evaluation.provenance import (
    EvaluationModel,
    NonEmptyString,
    OperationalSource,
    SourceRevision,
    StringEnum,
    require_active_source_revision,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

#: Pinned by this implementation; Contract V1 names the field, not the value.
ASSESSMENT_SCHEMA_VERSION = "evaluation-assessment.v1"
INTERVENTION_SCHEMA_VERSION = "evaluation-intervention.v1"

AssessmentSchemaVersion = Literal["evaluation-assessment.v1"]
InterventionSchemaVersion = Literal["evaluation-intervention.v1"]


def _list_to_tuple(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


EvidenceRefs = Annotated[
    tuple[NonEmptyString, ...], BeforeValidator(_list_to_tuple)
]
AppendSequence = Annotated[int, Field(ge=1)]


# ---------------------------------------------------------------------------
# Closed vocabularies, verbatim from Contract V1.
# ---------------------------------------------------------------------------


class ReviewOutcome(StringEnum):
    """Contract V1 §9 allowed review outcomes."""

    REVIEWER_APPROVED = "REVIEWER_APPROVED"
    REVIEWER_APPROVED_WITH_NON_BLOCKING_FINDINGS = (
        "REVIEWER_APPROVED_WITH_NON_BLOCKING_FINDINGS"
    )
    REVIEWER_REQUESTED_CHANGES = "REVIEWER_REQUESTED_CHANGES"
    REVIEWER_BLOCKED = "REVIEWER_BLOCKED"


#: §23's window opens on an *approval*; a changes-requested or blocked review
#: does not start an escaped-defect window.
REVIEWER_PROXY_APPROVAL_OUTCOMES = frozenset(
    {
        ReviewOutcome.REVIEWER_APPROVED,
        ReviewOutcome.REVIEWER_APPROVED_WITH_NON_BLOCKING_FINDINGS,
    }
)


class OwnerOutcome(StringEnum):
    """Contract V1 §10 allowed Owner outcomes.

    ``OWNER_UNREVIEWED`` is deliberately absent: §10 forbids creating it, and
    ``owner_review_status = UNREVIEWED`` is derived from the *absence* of a
    valid assessment.
    """

    OWNER_ACCEPTED = "OWNER_ACCEPTED"
    OWNER_REJECTED = "OWNER_REJECTED"


class OwnerReviewStatus(StringEnum):
    """Derived Owner review status, Contract V1 §10."""

    UNREVIEWED = "UNREVIEWED"
    OWNER_ACCEPTED = "OWNER_ACCEPTED"
    OWNER_REJECTED = "OWNER_REJECTED"


class DecisionReviewLabel(StringEnum):
    """Contract V1 §14 review labels. ``NOT_REVIEWED`` is forbidden by §14."""

    CORRECT = "CORRECT"
    REASONABLE = "REASONABLE"
    INCORRECT = "INCORRECT"
    NOT_ENOUGH_EVIDENCE = "NOT_ENOUGH_EVIDENCE"


class DecisionAssessmentMethod(StringEnum):
    """Contract V1 §14 assessment methods."""

    HUMAN_REVIEW = "HUMAN_REVIEW"
    COUNTERFACTUAL_REPLAY = "COUNTERFACTUAL_REPLAY"
    ALTERNATIVE_ACTION_EXPERIMENT = "ALTERNATIVE_ACTION_EXPERIMENT"


class InterventionCategory(StringEnum):
    """Contract V1 §16 intervention categories."""

    CLARIFICATION = "CLARIFICATION"
    SCOPE_CHANGE = "SCOPE_CHANGE"
    ACCEPTANCE_CHANGE = "ACCEPTANCE_CHANGE"
    OPTION_SELECTION = "OPTION_SELECTION"
    MANUAL_CODE_EDIT = "MANUAL_CODE_EDIT"
    MANUAL_AGENT_RESTART = "MANUAL_AGENT_RESTART"
    ERROR_RECOVERY = "ERROR_RECOVERY"
    RESULT_REJECTION = "RESULT_REJECTION"


class InterventionMeasurementMethod(StringEnum):
    """Contract V1 §16 measurement methods."""

    UI_MEASURED = "UI_MEASURED"
    USER_SELF_REPORTED = "USER_SELF_REPORTED"
    ESTIMATED = "ESTIMATED"
    UNAVAILABLE = "UNAVAILABLE"


class EscapedDefectSeverity(StringEnum):
    """Severity of an observed escaped defect."""

    BLOCKING = "BLOCKING"
    MAJOR = "MAJOR"
    MINOR = "MINOR"


class EscapedDefectStatusV1(StringEnum):
    """Derived escaped-defect status, Contract V1 §23. All three are derived."""

    WINDOW_NOT_COMPLETE = "WINDOW_NOT_COMPLETE"
    FOUND = "FOUND"
    NONE_OBSERVED = "NONE_OBSERVED"


# ---------------------------------------------------------------------------
# Report-bound assessments (Contract V1 §9, §10, §11).
# ---------------------------------------------------------------------------


class _ReportBoundFields(EvaluationModel):
    """Exact-target fields required by Contract V1 §11 (and §9's active rule)."""

    assessment_id: NonEmptyString
    assessment_schema_version: AssessmentSchemaVersion = ASSESSMENT_SCHEMA_VERSION
    run_id: NonEmptyString
    target_terminal_report_id: NonEmptyString
    target_terminal_report_hash: NonEmptyString
    target_source_revision: SourceRevision
    timestamp: datetime
    evidence_refs: EvidenceRefs = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_binding(self) -> _ReportBoundFields:
        if not _SHA256_PATTERN.match(self.target_terminal_report_hash):
            raise ValueError(
                "target_terminal_report_hash must be a 64-character lowercase "
                "hexadecimal SHA-256 of the exact serialized report bytes"
            )
        require_active_source_revision(self.target_source_revision)
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence_refs must be unique")
        return self


class ReviewAssessmentEventDraft(_ReportBoundFields):
    """Contract V1 §9 `ReviewAssessmentEvent`, before append."""

    review_outcome: ReviewOutcome
    reviewer: NonEmptyString


class ReviewAssessmentEvent(ReviewAssessmentEventDraft):
    """Persisted §9 event. Carries the store-assigned ``append_sequence``."""

    append_sequence: AppendSequence


class OwnerAssessmentEventDraft(_ReportBoundFields):
    """Contract V1 §10 `OwnerAssessmentEvent`, before append.

    ``owner_evaluation_ref`` optionally points at existing
    ``tomtit.domain.evaluation.OwnerEvaluation`` content. That content model
    stays where it is; only the append envelope is new.
    """

    owner_outcome: OwnerOutcome
    owner_evaluation_ref: str | None = None


class OwnerAssessmentEvent(OwnerAssessmentEventDraft):
    """Persisted §10 event."""

    append_sequence: AppendSequence


# ---------------------------------------------------------------------------
# Decision assessment (Contract V1 §14).
# ---------------------------------------------------------------------------


class DecisionAssessmentEventDraft(EvaluationModel):
    """Contract V1 §14 `DecisionAssessmentEvent`, before append.

    References a Runtime ``decision_id`` but never modifies the original
    ``DecisionEvent`` (§14).
    """

    assessment_id: NonEmptyString
    assessment_schema_version: AssessmentSchemaVersion = ASSESSMENT_SCHEMA_VERSION
    run_id: NonEmptyString
    decision_id: NonEmptyString
    later_observed_outcome: str | None = None
    review_label: DecisionReviewLabel
    assessment_method: DecisionAssessmentMethod
    reviewer_or_experiment_id: NonEmptyString
    timestamp: datetime
    evidence_refs: EvidenceRefs = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_decision_assessment(self) -> DecisionAssessmentEventDraft:
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence_refs must be unique")
        if (
            self.review_label is DecisionReviewLabel.NOT_ENOUGH_EVIDENCE
            and not self.evidence_refs
        ):
            raise ValueError(
                "NOT_ENOUGH_EVIDENCE must cite the evidence that was examined "
                "and found insufficient"
            )
        return self


class DecisionAssessmentEvent(DecisionAssessmentEventDraft):
    """Persisted §14 event."""

    append_sequence: AppendSequence


# ---------------------------------------------------------------------------
# Human intervention (Contract V1 §16).
# ---------------------------------------------------------------------------


class HumanInterventionEventDraft(EvaluationModel):
    """Contract V1 §16 `HumanInterventionEvent`, before append.

    One event per intervention; aggregates are derived from these, never the
    reverse.

    ``active_seconds`` is ``None`` when measurement is unavailable (F06). An
    unavailable measurement is *unknown*, not a measured zero, and the two must
    not be conflated — so the type itself distinguishes them and the validator
    keeps the duration and the method coherent.

    §16 also requires Owner wall-clock wait to stay separate from active human
    effort. This event deliberately exposes **no** owner-wait figure: a
    request/response span on an intervention is not proof of Runtime
    ``WAITING_OWNER`` state, and inferring one from these timestamps would
    manufacture a fact Evaluation does not have.
    """

    intervention_id: NonEmptyString
    intervention_schema_version: InterventionSchemaVersion = (
        INTERVENTION_SCHEMA_VERSION
    )
    run_id: NonEmptyString
    category: InterventionCategory
    requested_at: datetime
    responded_at: datetime | None = None
    active_seconds: Annotated[float, Field(ge=0)] | None = None
    measurement_method: InterventionMeasurementMethod
    required_by_decision_id: str | None = None
    evidence_refs: EvidenceRefs = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_intervention(self) -> HumanInterventionEventDraft:
        if self.responded_at is not None and self.responded_at < self.requested_at:
            raise ValueError("responded_at cannot precede requested_at")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence_refs must be unique")

        unavailable = (
            self.measurement_method is InterventionMeasurementMethod.UNAVAILABLE
        )
        if unavailable and self.active_seconds is not None:
            raise ValueError(
                "measurement_method=UNAVAILABLE means the active duration is "
                "unknown; it must be None rather than a number (a measured "
                "zero requires a concrete measurement_method)"
            )
        if not unavailable and self.active_seconds is None:
            raise ValueError(
                "a concrete measurement_method must report an active_seconds "
                "value; use UNAVAILABLE when the duration is unknown"
            )
        return self

    @property
    def active_seconds_is_known(self) -> bool:
        """Whether this event carries a measured active duration."""

        return self.active_seconds is not None


class HumanInterventionEvent(HumanInterventionEventDraft):
    """Persisted §16 event."""

    append_sequence: AppendSequence


# ---------------------------------------------------------------------------
# Escaped defect (Contract V1 §23).
# ---------------------------------------------------------------------------


class EscapedDefectEventDraft(EvaluationModel):
    """Contract V1 §23 `EscapedDefectEvent`, before append.

    Emitted **only** when a defect is actually observed. §23 forbids emitting
    events for ``NONE_OBSERVED`` or ``WINDOW_NOT_COMPLETE``.

    This is the one V1 event whose field list carries no schema version and no
    ``timestamp`` — it uses ``event_id`` and ``detected_at``. The shape follows
    the contract rather than being normalized to match its siblings.
    """

    event_id: NonEmptyString
    run_id: NonEmptyString
    severity: EscapedDefectSeverity
    detected_at: datetime
    evidence_refs: EvidenceRefs = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_escaped_defect(self) -> EscapedDefectEventDraft:
        if not self.evidence_refs:
            raise ValueError(
                "an escaped-defect event asserts an observed defect and must "
                "cite at least one evidence reference"
            )
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence_refs must be unique")
        return self


class EscapedDefectEvent(EscapedDefectEventDraft):
    """Persisted §23 event."""

    append_sequence: AppendSequence


# ---------------------------------------------------------------------------
# Source-aware integration envelope (F01).
# ---------------------------------------------------------------------------

PersistedAssessment = (
    ReviewAssessmentEvent
    | OwnerAssessmentEvent
    | DecisionAssessmentEvent
    | HumanInterventionEvent
    | EscapedDefectEvent
)


class SourceAwareAssessment(EvaluationModel):
    """One persisted V1 event plus the operational source that produced it.

    The envelope exists because ``operational_source`` is not a Contract V1
    event field. Keeping it here means the persisted event stays literal-V1
    compatible while source identity remains explicit and joinable.

    Source identity is part of every lookup key: Product Runtime ``run_id=X``
    and dogfood ``run_id=X`` are different subjects, and nothing in this
    package may treat them as one.
    """

    operational_source: OperationalSource
    assessment: PersistedAssessment

    @property
    def append_sequence(self) -> int:
        """Convenience read-through to the persisted event's sequence."""

        return self.assessment.append_sequence

    @property
    def run_scope(self) -> tuple[OperationalSource, str]:
        """Source-qualified run identity, safe to use as a join key."""

        return (self.operational_source, self.assessment.run_id)


def report_target_key(
    operational_source: OperationalSource,
    assessment: ReviewAssessmentEvent
    | OwnerAssessmentEvent
    | ReviewAssessmentEventDraft
    | OwnerAssessmentEventDraft,
) -> tuple[str, str, str, str, str, str]:
    """Source-qualified exact-target key for Contract V1 §11.

    §11 binds on run_id, report id, report SHA-256, revision type and revision
    value. The operational source is prepended so an identical report identity
    observed from two sources cannot collide.
    """

    return (
        operational_source.value,
        assessment.run_id,
        assessment.target_terminal_report_id,
        assessment.target_terminal_report_hash,
        assessment.target_source_revision.revision_type.value,
        assessment.target_source_revision.revision_value,
    )
