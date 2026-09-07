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
and immutable, so Evaluation can neither originate nor verify them on its own.
A binding value a *caller* supplies is the caller attesting to itself, whatever
its shape — a dedicated class, a duck-typed protocol, a copied report file or a
chosen ``issued_by`` string are all the same thing, and none of them is
authority.

``EVAL-RUNTIME-BRIDGE-01`` supplies the missing half, and it is implemented:
see :mod:`tomtit.evaluation.bridge`. Runtime keeps a **process-local
publication ledger** (:mod:`tomtit.reporting`) recording the terminal reports
this process actually published — allocated report identity, the exact path,
the exact published bytes and their SHA-256, and the run identity — and returns
an opaque publication receipt. A report-bound append therefore takes a receipt
id, and :func:`verify_published_report` rebinds it to the ledger's own facts:
the ledger re-reads the path *it* recorded and re-hashes those bytes. The
caller selects which publication to assess and establishes nothing about it.

Two limits on that guarantee, stated rather than glossed:

**Process-local, not cryptographic.** The ledger lives in memory. It
establishes "this process published this artifact" — not that another process
or host did, and not that a hostile process could not reach its internals.
There is no signing, no attestation and no cross-process or malicious-host
resistance. Those remain open work, not properties of V0A.

**Source revision authority does not exist yet.**
``PRODUCT_RUNTIME_SOURCE_REVISION_AUTHORITY`` is unimplemented: nothing in this
repository establishes the source revision a run executed against, and Runtime
must not manufacture one. Runtime therefore publishes ``source_revision =
null``, and §4.2 makes such a report admit no Review or Owner assessment. So
canonical report-bound publication of a *current* real Product Runtime run
still fails closed — now because the artifact lacks provenance, not because the
bridge is missing. Once a separately authorized source-provenance component
establishes the executed revision, a publication carrying both that revision
and a ledger receipt becomes assessable; until then it does not, and
:class:`UnassessableTerminalReportError` says exactly that.

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
from tomtit.evaluation.bridge import (
    TERMINAL_REPORT_NAME,
    TERMINAL_REPORT_SCHEMA_VERSION,
    PublishedReportVerificationError,
    TerminalReportUnavailableError,
    UnassessableTerminalReportError,
    VerifiedTerminalReport,
    verify_published_report,
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
    PublicationTargetMismatchError,
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
    "TERMINAL_REPORT_NAME",
    "TERMINAL_REPORT_SCHEMA_VERSION",
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
    "PublicationTargetMismatchError",
    "PublishedReportVerificationError",
    "ReportBoundAppendUnavailableError",
    "ReviewAssessmentEvent",
    "ReviewAssessmentEventDraft",
    "ReviewOutcome",
    "RevisionStatus",
    "RevisionType",
    "SourceAwareAssessment",
    "SourceRevision",
    "StatusOriginVocabulary",
    "TerminalReportUnavailableError",
    "TranslatedCriterionStatus",
    "UnassessableTerminalReportError",
    "UnsupportedAssessmentError",
    "VerifiedTerminalReport",
    "adapt_decision_evaluation_result",
    "effective_assessment",
    "escaped_defect_status",
    "intervention_totals",
    "map_human_intervention_source",
    "owner_review_status",
    "owner_wait_metric",
    "verify_published_report",
    "report_target_key",
    "require_active_source_revision",
    "revision_type_status",
    "translate_owner_criterion_status",
    "translate_requirement_status",
    "translate_runtime_criterion_status",
]
