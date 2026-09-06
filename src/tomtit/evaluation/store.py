"""The Evaluation-owned append store (`evaluation_assessment_stream`).

Contract V1 §2.3 gives Evaluation ownership of the five assessment events, and
§8 requires ``append_sequence`` to be assigned only by the owning store, be
strictly increasing and unique within its stream, and be immutable after
append. §8 names two streams and forbids comparing across them::

    runtime_execution_stream
    evaluation_assessment_stream

This store owns the second only. It never touches
``tomtit.runtime.events.InMemoryEventLog`` or the ``append_sequence`` on
``tomtit.domain.evaluation.PersistedDecisionEvent``: those belong to the
Runtime stream, §2.3 forbids Evaluation from creating or modifying a Runtime
``DecisionEvent``, and sharing a counter would make the two streams' positions
comparable.

Report-bound trust boundary (F04)
---------------------------------
Contract V1 §4.1 makes ``report_id`` and ``source_revision`` Runtime-assigned
and immutable, so Evaluation cannot originate them and cannot verify them
either: any "binding" Evaluation accepts from a caller is a value the caller
chose. A structural check over caller-chosen values is not a trust boundary —
neither a dedicated class, nor a duck-typed protocol, nor a reserved
``issued_by`` string turns a caller's assertion into Runtime authority.

Until an authorized ``EVAL-RUNTIME-BRIDGE`` supplies genuinely Runtime-owned
binding authority, the canonical append path for the two report-bound events
therefore **fails closed for every operational source**::

    ReviewAssessmentEventDraft  -> ReportBoundAppendUnavailableError
    OwnerAssessmentEventDraft   -> ReportBoundAppendUnavailableError

The V1 draft and persisted models still exist and are still validated as
data-contract shapes; only canonical *publication* is blocked. There is no
public parameter, keyword, subclass hook or issuer name that opens it.

Durability
----------
Process-local and in-memory. **Not** durable, not shared across processes, not
a database. Sufficient for V0A, whose requirement is an authority and ordering
boundary rather than production persistence.
"""

from __future__ import annotations

from tomtit.evaluation.assessments import (
    DecisionAssessmentEvent,
    DecisionAssessmentEventDraft,
    EscapedDefectEvent,
    EscapedDefectEventDraft,
    HumanInterventionEvent,
    HumanInterventionEventDraft,
    OwnerAssessmentEvent,
    OwnerAssessmentEventDraft,
    ReviewAssessmentEvent,
    ReviewAssessmentEventDraft,
    SourceAwareAssessment,
)
from tomtit.evaluation.provenance import OperationalSource

#: Stream names, per Contract V1 §8.
EVALUATION_ASSESSMENT_STREAM = "evaluation_assessment_stream"
RUNTIME_EXECUTION_STREAM = "runtime_execution_stream"

#: Exact draft type -> exact persisted type. Membership is by *exact* type, not
#: ``isinstance``: a subclass could add fields that silently change the
#: persisted schema, so subclasses are refused (F03).
_DRAFT_TO_PERSISTED: dict[type, type] = {
    ReviewAssessmentEventDraft: ReviewAssessmentEvent,
    OwnerAssessmentEventDraft: OwnerAssessmentEvent,
    DecisionAssessmentEventDraft: DecisionAssessmentEvent,
    HumanInterventionEventDraft: HumanInterventionEvent,
    EscapedDefectEventDraft: EscapedDefectEvent,
}

#: Report-bound drafts have no canonical append path in V0A (F04): only the
#: observed operational source can assign the terminal-report identity they
#: bind to, and no bridge exists to attest it, so both fail closed.
_REPORT_BOUND_DRAFTS = (ReviewAssessmentEventDraft, OwnerAssessmentEventDraft)

#: The canonical identity field for each draft type, used for duplicate
#: rejection. Contract V1 gives each event its own identity field name.
_IDENTITY_FIELD: dict[type, str] = {
    ReviewAssessmentEventDraft: "assessment_id",
    OwnerAssessmentEventDraft: "assessment_id",
    DecisionAssessmentEventDraft: "assessment_id",
    HumanInterventionEventDraft: "intervention_id",
    EscapedDefectEventDraft: "event_id",
}


class ForeignSequenceError(ValueError):
    """Raised when a caller tries to supply or override ``append_sequence``."""


class UnsupportedAssessmentError(TypeError):
    """Raised for anything that is not one of the five exact V1 draft types."""


class DuplicateAssessmentIdentityError(ValueError):
    """Raised when a canonical event identity is appended twice.

    V0A policy: a duplicate identity is rejected whether its content is
    identical or conflicting. Contract V1 requires identity to be unique within
    the stream and gives no merge or last-writer-wins rule, so silently
    overwriting or silently appending a second record under one identity would
    both invent semantics the contract does not grant.
    """


class ReportBoundAppendUnavailableError(ValueError):
    """Raised for every canonical append of a report-bound assessment.

    Contract V1 §4 admits a valid Review or Owner assessment only against a
    terminal report whose ``report_id``/``source_revision`` were assigned by the
    observed operational source (§4.1). Evaluation neither assigns nor can
    verify those values, and EVAL-RUNTIME-BRIDGE — the authorized component
    that would — does not exist yet.

    Accepting caller-supplied "proof" instead would make the caller its own
    authority, so V0A refuses the append outright. This is a deliberate
    unavailability, not a validation failure of the draft.
    """


class EvaluationAssessmentStore:
    """Append-only, Evaluation-owned assessment store.

    Records are held as serialized JSON keyed by their exact persisted type and
    re-validated on read, so a caller holding a returned record cannot reach
    back into the store's history.
    """

    def __init__(self) -> None:
        self._sequence = 0
        self._serialized: list[tuple[str, str, str]] = []
        self._identities: set[tuple[str, str, str]] = set()

    # -- append authority ---------------------------------------------------

    def append(
        self,
        draft: object,
        *,
        operational_source: OperationalSource,
    ) -> SourceAwareAssessment:
        """Validate, then atomically publish one persisted V1 event.

        Report-bound drafts are refused unconditionally: see
        :class:`ReportBoundAppendUnavailableError`. There is no keyword,
        binding object or issuer name that changes that — the decision is made
        from the draft's exact type alone, before any other work.

        Nothing mutates store state until every fallible step has succeeded:
        type check, report-bound check, duplicate check, persisted-event
        construction, serialization, and rehydration all happen first. A
        failure therefore leaves length, sequence, and history untouched — no
        burned sequence number and no partially visible record.
        """

        draft_type = self._validated_draft_type(draft, operational_source)
        if draft_type in _REPORT_BOUND_DRAFTS:
            raise ReportBoundAppendUnavailableError(
                f"{draft_type.__name__} binds to a terminal report identity that "
                "only the observed operational source can assign (Contract V1 "
                "§4.1). Evaluation cannot originate or verify report_id and "
                "source_revision, and no caller-supplied value can stand in for "
                "that authority, so canonical publication of report-bound "
                "assessments is unavailable in V0A. Future dependency: "
                "EVAL-RUNTIME-BRIDGE."
            )
        return self._publish(draft, draft_type, operational_source)

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _validated_draft_type(
        draft: object, operational_source: OperationalSource
    ) -> type:
        """Exact-type gate for the canonical append path."""

        if not isinstance(operational_source, OperationalSource):
            raise UnsupportedAssessmentError(
                "operational_source must be an OperationalSource member"
            )

        draft_type = type(draft)
        if draft_type not in _DRAFT_TO_PERSISTED:
            raise UnsupportedAssessmentError(
                f"{draft_type.__name__} is not one of the exact Contract V1 "
                f"assessment drafts "
                f"{[cls.__name__ for cls in _DRAFT_TO_PERSISTED]}; subclasses "
                "are refused because they can alter the persisted schema"
            )
        return draft_type

    def _publish(
        self,
        draft: object,
        draft_type: type,
        operational_source: OperationalSource,
    ) -> SourceAwareAssessment:
        """Duplicate check, construct, serialize, rehydrate, then publish.

        Private. Reached from :meth:`append` only for non-report-bound drafts;
        the report-bound gate above is the sole authority decision and it is
        made before this runs.
        """

        persisted_cls = _DRAFT_TO_PERSISTED[draft_type]
        identity = (
            operational_source.value,
            draft_type.__name__,
            getattr(draft, _IDENTITY_FIELD[draft_type]),
        )
        if identity in self._identities:
            raise DuplicateAssessmentIdentityError(
                f"{draft_type.__name__} identity {identity[2]!r} already exists "
                f"for {operational_source.value}; Contract V1 gives no merge or "
                "overwrite rule, so a duplicate identity is rejected whether "
                "its content is identical or conflicting"
            )

        next_sequence = self._sequence + 1
        # Construct, serialize and rehydrate before publishing anything.
        persisted = persisted_cls(
            **draft.model_dump(), append_sequence=next_sequence
        )
        envelope = SourceAwareAssessment(
            operational_source=operational_source, assessment=persisted
        )
        serialized = persisted.model_dump_json()
        rehydrated = self._rehydrate(
            operational_source.value, persisted_cls.__name__, serialized
        )

        # Atomic publish: every fallible step above already succeeded.
        self._sequence = next_sequence
        self._serialized.append(
            (operational_source.value, persisted_cls.__name__, serialized)
        )
        self._identities.add(identity)
        assert rehydrated.append_sequence == next_sequence
        return envelope

    # -- reads --------------------------------------------------------------

    @staticmethod
    def _rehydrate(
        source_value: str, persisted_name: str, serialized: str
    ) -> SourceAwareAssessment:
        persisted_cls = next(
            cls
            for cls in _DRAFT_TO_PERSISTED.values()
            if cls.__name__ == persisted_name
        )
        return SourceAwareAssessment(
            operational_source=OperationalSource(source_value),
            assessment=persisted_cls.model_validate_json(serialized),
        )

    @property
    def records(self) -> tuple[SourceAwareAssessment, ...]:
        """All appended records in Evaluation-local append order."""

        return tuple(
            self._rehydrate(source_value, persisted_name, serialized)
            for source_value, persisted_name, serialized in self._serialized
        )

    def records_of_type(
        self,
        draft_cls: type,
        *,
        operational_source: OperationalSource | None = None,
    ) -> tuple[SourceAwareAssessment, ...]:
        """Appended records of one exact event type, optionally source-filtered.

        ``operational_source`` must be supplied wherever source-local
        identifiers could collide; leaving it unset returns both sources and is
        only safe for whole-stream inspection.
        """

        persisted_name = _DRAFT_TO_PERSISTED[draft_cls].__name__
        return tuple(
            self._rehydrate(source_value, name, serialized)
            for source_value, name, serialized in self._serialized
            if name == persisted_name
            and (
                operational_source is None
                or source_value == operational_source.value
            )
        )

    @property
    def sequence(self) -> int:
        """Highest sequence assigned so far in this stream."""

        return self._sequence

    def __len__(self) -> int:
        return len(self._serialized)
