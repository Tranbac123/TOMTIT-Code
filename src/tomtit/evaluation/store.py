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

Report-bound trust boundary
---------------------------
Contract V1 §4.1 makes ``report_id`` and ``source_revision`` Runtime-assigned
and immutable, so Evaluation cannot originate them and cannot verify them on
its own: any "binding" Evaluation accepts *from a caller* is a value the caller
chose. A structural check over caller-chosen values is not a trust boundary —
neither a dedicated class, nor a duck-typed protocol, nor a reserved
``issued_by`` string turns a caller's assertion into Runtime authority. Nor
does an object that carries its own ``verify()``: running a method the caller
supplied to decide whether that caller is authorized is the same defect wearing
a type annotation.

``EVAL-RUNTIME-BRIDGE-01`` closes this, and the authority is deliberately not
anything the caller hands over. Runtime keeps a process-local publication
ledger (:mod:`tomtit.reporting`) of the terminal reports it actually published;
``append`` takes ``publication_receipt_id``, an opaque string, and
:func:`~tomtit.evaluation.bridge.verify_published_report` resolves it against
that ledger. The ledger re-reads the path it recorded and re-hashes those
bytes, then this module compares the draft's §11 target against the facts that
come back. Nothing caller-controlled executes anywhere in that path — the
receipt id must be exactly :class:`str`, so there is no method to override and
no attribute access to intercept.

What that does and does not buy::

    arbitrary report file        -> no ledger entry     -> refused
    copied published bytes       -> no ledger entry     -> refused
    replayed/reused report id    -> allocation consumed -> refused
    tampered published artifact  -> hash mismatch       -> refused
    duck type / subclass / dict  -> not a str           -> refused
    genuine receipt, no revision -> §4.2 unassessable   -> refused

The last line is the current state of every real Product Runtime run.
``PRODUCT_RUNTIME_SOURCE_REVISION_AUTHORITY`` is unimplemented — nothing
establishes the source revision a run executed against — so Runtime publishes
``source_revision = null`` and §4.2 admits no Review or Owner assessment
against such a report. Canonical report-bound publication therefore still fails
closed today, but for a different and narrower reason than before: the bridge
exists, the artifact's provenance does not. A publication that carries both a
ledger receipt and an authoritative source revision is assessable; producing
one is separately authorized future work, and this module must not fabricate
it.

Authenticity here is process-local, and that is the whole claim. The ledger is
in-memory: it says "this process published this artifact" and nothing about
another process or host. No signing, no attestation, no malicious-host
resistance.

The gate holds because the store ships exactly one write callable. Publication
is written inline inside :meth:`EvaluationAssessmentStore.append`, after the
authority check, and no helper — public or private, method or module-level —
reaches that code. The three private module functions here,
:func:`_classify_draft`, :func:`_require_verified_publication` and
:func:`_rehydrate`, are read-only: they classify a type, refuse an unproven
publication, and rebuild a detached record from JSON. None can place anything
into a store, so there is no inner entry point to call instead of ``append``.
Tests that need *non-canonical* report-bound history represent it entirely in
the test tree; the package offers no seam for writing it.

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
    report_target_key,
)
from tomtit.evaluation.bridge import verify_published_report
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

#: Report-bound drafts do have a canonical append path: EVAL-RUNTIME-BRIDGE-01
#: is implemented. It requires a verified Runtime publication receipt, which
#: rebinds the assessment target to the process-local Runtime/reporting ledger's
#: own facts — not to anything the caller supplies — and the published report
#: must also carry an active authoritative source revision.
#:
#: That second requirement is what still fails closed today:
#: PRODUCT_RUNTIME_SOURCE_REVISION_AUTHORITY is not established, so Runtime
#: publishes ``source_revision = null`` and §4.2 leaves current real Product
#: Runtime reports unassessable. The blocker is the artifact's provenance, not a
#: missing bridge. Authenticity here is process-local only: no signing, no
#: cross-process authentication, no hostile-host resistance.
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
    """Raised when a report-bound assessment arrives with no publication evidence.

    Contract V1 §4 admits a valid Review or Owner assessment only against a
    terminal report whose ``report_id``/``source_revision`` were assigned by the
    observed operational source (§4.1). Evaluation neither assigns nor can
    verify those values on its own, so an append without a publication receipt
    id — or with something that merely resembles one — is refused: absence of
    evidence is not authority, and neither is a convincing-looking object.
    """


class PublicationTargetMismatchError(ValueError):
    """Raised when the assessment target disagrees with the published artifact.

    Contract V1 §9/§11 require ``run_id``, ``target_terminal_report_id``,
    ``target_terminal_report_hash`` and ``target_source_revision`` to match the
    report exactly. Every one of those is compared against values the
    publication ledger re-proved from the artifact, never against anything the
    caller stated.
    """


def _classify_draft(draft: object, operational_source: OperationalSource) -> type:
    """Classify a draft as one of the five exact V1 types. Pure, and read-only.

    Returns a type and touches no store: it cannot publish anything, which is
    why it is safe for it to live outside :meth:`EvaluationAssessmentStore.append`
    while the record-appending code does not.
    """

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


def _require_verified_publication(
    draft: object,
    draft_type: type,
    operational_source: OperationalSource,
    publication_receipt_id: object,
) -> None:
    """Prove a report-bound draft targets a real Runtime publication. Read-only.

    The whole trust decision lives here, and none of it runs caller code:

    1. a publication receipt id must be present (no evidence, no assessment —
       §4.2), and it must be exactly a string. Anything else — an object with
       a ``verify()``, a mapping, a subclass of a trusted-looking type — is
       refused here rather than consulted, because the store must never invoke
       a method a caller supplied to decide whether that caller is authorized;
    2. :func:`~tomtit.evaluation.bridge.verify_published_report` asks Runtime's
       publication ledger to re-prove the receipt: that it names a real
       publication, that the recorded artifact still hashes to what was
       published, and that the report is assessable at all under §4.2/§5.3;
    3. the draft's §11 target key must equal the key the ledger derived —
       run_id, report id, exact-byte SHA-256, revision type and revision value,
       all five.

    The caller therefore chooses *which* publication to assess and nothing
    else. A caller who wants a different target must get Runtime to publish a
    different report, which is Runtime's job, not Evaluation's.

    Pure and store-free: it can refuse an append but can never perform one.
    """

    if publication_receipt_id is None:
        raise ReportBoundAppendUnavailableError(
            f"{draft_type.__name__} binds to a terminal report identity that only "
            "the observed operational source assigns (Contract V1 §4.1). Supply "
            "the publication receipt id Runtime kept when it published the report "
            "(runtime.publication_receipt.receipt_id); Evaluation cannot originate "
            "report_id or source_revision, and no caller-stated value substitutes "
            "for a recorded publication."
        )
    # noqa rationale: isinstance() is wrong here. A str subclass can override
    # __eq__/__hash__ and so influence its own lookup, which puts caller code
    # back in the trust path. Only the exact built-in type is accepted.
    if type(publication_receipt_id) is not str:  # noqa: E721
        raise ReportBoundAppendUnavailableError(
            "publication_receipt_id must be the opaque receipt id string Runtime "
            f"issued, not {type(publication_receipt_id).__name__}; an object that "
            "looks authoritative proves nothing, because authority is the "
            "publication ledger and not the value presented"
        )

    verified = verify_published_report(
        publication_receipt_id, operational_source=operational_source
    )

    expected = report_target_key(operational_source, draft)  # type: ignore[arg-type]
    if expected != verified.target_key:
        raise PublicationTargetMismatchError(
            "assessment target does not match the published terminal report: "
            f"assessment={expected} report={verified.target_key}. Contract V1 "
            "§9/§11 require run_id, report id, exact-byte hash and source "
            "revision to match exactly; an assessment of an old report or "
            "revision never applies to a newer output."
        )


def _rehydrate(
    source_value: str, persisted_name: str, serialized: str
) -> SourceAwareAssessment:
    """Rebuild one detached record from stored JSON. Read-only."""

    persisted_cls = next(
        cls
        for cls in _DRAFT_TO_PERSISTED.values()
        if cls.__name__ == persisted_name
    )
    return SourceAwareAssessment(
        operational_source=OperationalSource(source_value),
        assessment=persisted_cls.model_validate_json(serialized),
    )


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
        publication_receipt_id: str | None = None,
    ) -> SourceAwareAssessment:
        """Validate, then atomically publish one persisted V1 event.

        This is the whole write surface of the store, and publication is
        written inline rather than delegated: there is no second callable —
        public or private, on this class or beside it — that reaches the
        record-appending code below. A caller cannot therefore skip the
        report-bound gate by calling something further in, because the gate and
        the append are the same function body.

        Report-bound drafts require ``publication_receipt_id``: the opaque id
        Runtime kept when it published the terminal report. It is resolved
        against Runtime's publication ledger, which re-reads the artifact it
        recorded and re-hashes it, and every §11 target field is compared
        against the facts that come back. What authorizes the append is a
        recorded Runtime publication — never the type of the object presented,
        a method on it, or anything the caller asserts.

        Nothing mutates store state until every fallible step has succeeded:
        type check, publication verification, duplicate check, persisted-event
        construction, serialization, and rehydration all happen first. A
        failure therefore leaves length, sequence, and history untouched — no
        burned sequence number and no partially visible record.
        """

        draft_type = _classify_draft(draft, operational_source)
        if draft_type in _REPORT_BOUND_DRAFTS:
            _require_verified_publication(
                draft, draft_type, operational_source, publication_receipt_id
            )
        elif publication_receipt_id is not None:
            raise UnsupportedAssessmentError(
                f"{draft_type.__name__} is not report-bound under Contract V1 and "
                "must not be appended with a publication receipt"
            )

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
        rehydrated = _rehydrate(
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

    @property
    def records(self) -> tuple[SourceAwareAssessment, ...]:
        """All appended records in Evaluation-local append order."""

        return tuple(
            _rehydrate(source_value, persisted_name, serialized)
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
            _rehydrate(source_value, name, serialized)
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
