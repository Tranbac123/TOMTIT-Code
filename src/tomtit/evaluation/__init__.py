"""TOMTIT Evaluation — V0A foundational contract and assessment store.

Scope and authority
-------------------
This package owns the ``evaluation_assessment_stream`` described by
``docs/EVALUATION_CONTRACT_V1.md`` §2.3 and §8. It appends assessments *about*
operational history and never becomes an operational authority:

* it does not write Runtime canonical state (§2.1);
* it does not create or modify a Runtime ``DecisionEvent`` (§2.3);
* it does not mutate dogfood history;
* its ``append_sequence`` is its own and is never comparable to the Runtime or
  dogfood sequences (§8).

Shape
-----
``XxxEventDraft`` (caller-facing, sequence-free) → store assigns the sequence →
``XxxEvent`` (canonical persisted V1 event, containing ``append_sequence``) →
:class:`SourceAwareAssessment` (integration envelope carrying
``operational_source``, which is not a V1 event field).

Trust boundary
--------------
Contract V1 §4.1 makes ``report_id`` and ``source_revision`` Runtime-assigned
and immutable, so Evaluation can neither originate nor verify them. A binding
value a *caller* supplies is therefore the caller attesting to itself, whatever
its shape — a dedicated class, a duck-typed protocol or a chosen ``issued_by``
string are all the same thing. V0A does not pretend otherwise: canonical
publication of the two report-bound events fails closed for every operational
source (:class:`ReportBoundAppendUnavailableError`), and this package exposes
no public way to open it. Producing genuine authority is
``EVAL-RUNTIME-BRIDGE``, which is not implemented.

Public surface and authority
----------------------------
Capability is exported only where its authority semantics hold. In particular
there is no public "latest of these records" reduction (an Evaluation-local
append order must never decide a winner across operational sources) and no
public escaped-defect *window* type (a caller-built window would be a
caller-asserted acceptance fact). The canonical operations —
:func:`owner_review_status`, :func:`effective_assessment`,
:func:`escaped_defect_status` — each require an exact source-qualified target
and derive the rest themselves.

The store is process-local and in-memory: an authority and ordering boundary,
not durable persistence.
"""

from __future__ import annotations

from tomtit.evaluation.assessments import (
    ASSESSMENT_SCHEMA_VERSION,
    INTERVENTION_SCHEMA_VERSION,
    REVIEWER_PROXY_APPROVAL_OUTCOMES,
    DecisionAssessmentEvent,
    DecisionAssessmentEventDraft,
    DecisionAssessmentMethod,
    DecisionReviewLabel,
    EscapedDefectEvent,
    EscapedDefectEventDraft,
    EscapedDefectSeverity,
    EscapedDefectStatusV1,
    HumanInterventionEvent,
    HumanInterventionEventDraft,
    InterventionCategory,
    InterventionMeasurementMethod,
    OwnerAssessmentEvent,
    OwnerAssessmentEventDraft,
    OwnerOutcome,
    OwnerReviewStatus,
    ReviewAssessmentEvent,
    ReviewAssessmentEventDraft,
    ReviewOutcome,
    SourceAwareAssessment,
    report_target_key,
)
from tomtit.evaluation.derive import (
    ESCAPED_DEFECT_WINDOW_DAYS,
    CrossSourceSelectionError,
    EffectiveAssessment,
    EscapedDefectAssessment,
    InterventionTotals,
    OwnerWaitMetric,
    effective_assessment,
    escaped_defect_status,
    intervention_totals,
    owner_review_status,
    owner_wait_metric,
)
from tomtit.evaluation.mapping import (
    DecisionAssessmentAdaptation,
    EvaluationCriterionStatus,
    MeasurementMethodMapping,
    StatusOriginVocabulary,
    TranslatedCriterionStatus,
    adapt_decision_evaluation_result,
    map_human_intervention_source,
    translate_owner_criterion_status,
    translate_requirement_status,
    translate_runtime_criterion_status,
)
from tomtit.evaluation.provenance import (
    EvaluationModel,
    InactiveSourceRevisionError,
    OperationalSource,
    RevisionStatus,
    RevisionType,
    SourceRevision,
    require_active_source_revision,
    revision_type_status,
)
from tomtit.evaluation.store import (
    EVALUATION_ASSESSMENT_STREAM,
    RUNTIME_EXECUTION_STREAM,
    DuplicateAssessmentIdentityError,
    EvaluationAssessmentStore,
    ForeignSequenceError,
    ReportBoundAppendUnavailableError,
    UnsupportedAssessmentError,
)

__all__ = [
    "ASSESSMENT_SCHEMA_VERSION",
    "ESCAPED_DEFECT_WINDOW_DAYS",
    "EVALUATION_ASSESSMENT_STREAM",
    "INTERVENTION_SCHEMA_VERSION",
    "REVIEWER_PROXY_APPROVAL_OUTCOMES",
    "RUNTIME_EXECUTION_STREAM",
    "CrossSourceSelectionError",
    "DecisionAssessmentAdaptation",
    "DecisionAssessmentEvent",
    "DecisionAssessmentEventDraft",
    "DecisionAssessmentMethod",
    "DecisionReviewLabel",
    "DuplicateAssessmentIdentityError",
    "EffectiveAssessment",
    "EscapedDefectAssessment",
    "EscapedDefectEvent",
    "EscapedDefectEventDraft",
    "EscapedDefectSeverity",
    "EscapedDefectStatusV1",
    "EvaluationAssessmentStore",
    "EvaluationCriterionStatus",
    "EvaluationModel",
    "ForeignSequenceError",
    "HumanInterventionEvent",
    "HumanInterventionEventDraft",
    "InactiveSourceRevisionError",
    "InterventionCategory",
    "InterventionMeasurementMethod",
    "InterventionTotals",
    "MeasurementMethodMapping",
    "OperationalSource",
    "OwnerAssessmentEvent",
    "OwnerAssessmentEventDraft",
    "OwnerOutcome",
    "OwnerReviewStatus",
    "OwnerWaitMetric",
    "ReportBoundAppendUnavailableError",
    "ReviewAssessmentEvent",
    "ReviewAssessmentEventDraft",
    "ReviewOutcome",
    "RevisionStatus",
    "RevisionType",
    "SourceAwareAssessment",
    "SourceRevision",
    "StatusOriginVocabulary",
    "TranslatedCriterionStatus",
    "UnsupportedAssessmentError",
    "adapt_decision_evaluation_result",
    "effective_assessment",
    "escaped_defect_status",
    "intervention_totals",
    "map_human_intervention_source",
    "owner_review_status",
    "owner_wait_metric",
    "report_target_key",
    "require_active_source_revision",
    "revision_type_status",
    "translate_owner_criterion_status",
    "translate_requirement_status",
    "translate_runtime_criterion_status",
]
