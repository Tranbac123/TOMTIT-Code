"""TEST-ONLY, NON-CANONICAL report-bound history. Not Runtime authority.

Nothing here is evidence of anything. This module fabricates history for the
derivation tests; it does not observe, verify or attest to a Product Runtime
run, and no record it produces may be treated as a canonical assessment.

Why this exists
---------------
``EVAL-RUNTIME-BRIDGE-01`` ships, so canonical report-bound append is no longer
categorically closed — a Runtime publication receipt can authorize one. But it
cannot authorize one *today*, because
``PRODUCT_RUNTIME_SOURCE_REVISION_AUTHORITY`` is unimplemented: Runtime
publishes ``source_revision = null`` and Contract V1 §4.2 makes such a report
unassessable. Real report-bound history therefore still cannot be produced, and
the §10/§11/§23 derivations over such events still have to be exercised.

So this module *represents* that history test-side rather than asking
production for a way to write it. The production package has exactly one write
callable — :meth:`~tomtit.evaluation.store.EvaluationAssessmentStore.append` —
with the authority gate inside it, and that stays true regardless of what the
tests need.

What it is
----------
:class:`TrustedHistoryStore` re-implements the store's read surface — the only
surface the derivations use — plus an append that is *stricter* than
production: it refuses report-bound drafts outright, where production refuses
them unless a Runtime publication receipt verifies. Being stricter is safe
here; being looser would let a derivation test pass over history the real store
would never have accepted. Report-bound records enter through
:meth:`seed_trusted_report_bound`, which exists nowhere in the shipped package
and cannot: it is an ordinary test object holding ordinary Python lists.

Why it is not production code
-----------------------------
An affordance that publishes otherwise-forbidden events is authority, whatever
it is named and wherever it is hidden. Shipping one — as a seam module, a seed
API, a private ``_publish`` or a capability flag — would put that authority in
every installed copy of ``tomtit.evaluation``. Keeping the whole representation
in ``tests/`` means the package has one write path and it is closed.

This is not hypothetical. The first EVAL-01 commit shipped exactly such an
affordance: a private ``EvaluationAssessmentStore._publish()`` sitting below the
report-bound gate, which the then-committed ``tests/evaluation/_seed.py`` called
directly to place report-bound history the gate forbade. That is the defect this
module exists to avoid repeating, which is why isolation here is asserted
against compiled code rather than trusted to a naming convention.

Fidelity
--------
This is a fake, so it can drift from the real store. ``tests/evaluation/
test_store.py`` exercises the production store directly, and
``test_trusted_history_matches_production_store_semantics`` pins the shared
behaviors (sequence assignment, duplicate identity, exact-type rejection,
read shape, fail-closed report-bound append) against the real implementation.
"""

from __future__ import annotations

from tomtit.evaluation import (
    DecisionAssessmentEvent,
    DecisionAssessmentEventDraft,
    DuplicateAssessmentIdentityError,
    EscapedDefectEvent,
    EscapedDefectEventDraft,
    EvaluationAssessmentStore,
    HumanInterventionEvent,
    HumanInterventionEventDraft,
    OperationalSource,
    OwnerAssessmentEvent,
    OwnerAssessmentEventDraft,
    ReportBoundAppendUnavailableError,
    ReviewAssessmentEvent,
    ReviewAssessmentEventDraft,
    SourceAwareAssessment,
    UnsupportedAssessmentError,
)

#: Exact draft -> exact persisted type, built from public names only.
_DRAFT_TO_PERSISTED: dict[type, type] = {
    ReviewAssessmentEventDraft: ReviewAssessmentEvent,
    OwnerAssessmentEventDraft: OwnerAssessmentEvent,
    DecisionAssessmentEventDraft: DecisionAssessmentEvent,
    HumanInterventionEventDraft: HumanInterventionEvent,
    EscapedDefectEventDraft: EscapedDefectEvent,
}
_REPORT_BOUND_DRAFTS = (ReviewAssessmentEventDraft, OwnerAssessmentEventDraft)
_IDENTITY_FIELD: dict[type, str] = {
    ReviewAssessmentEventDraft: "assessment_id",
    OwnerAssessmentEventDraft: "assessment_id",
    DecisionAssessmentEventDraft: "assessment_id",
    HumanInterventionEventDraft: "intervention_id",
    EscapedDefectEventDraft: "event_id",
}


class TrustedHistoryStore:
    """TEST-ONLY store-shaped double carrying NON-CANONICAL report-bound history.

    The name means "history the derivations may treat as given", not "history
    anyone verified". It is a fake, it holds ordinary Python lists, and it
    confers no Runtime authority on anything it contains.

    Reads (``records``, ``records_of_type``, ``sequence``, ``len``) match the
    production store, because that is what the derivations consume. Writes do
    not: ``append`` keeps production's fail-closed rule, and the extra
    :meth:`seed_trusted_report_bound` is the test-only representation of history
    a trusted bridge would have written.
    """

    def __init__(self) -> None:
        self._sequence = 0
        self._serialized: list[tuple[str, str, str]] = []
        self._identities: set[tuple[str, str, str]] = set()

    # -- writes -------------------------------------------------------------

    def append(
        self, draft: object, *, operational_source: OperationalSource
    ) -> SourceAwareAssessment:
        """Refuse report-bound drafts outright — stricter than production.

        Production refuses them without a verified Runtime publication receipt;
        this double has no ledger to consult, so it refuses them always. Test
        history that needs them goes through :meth:`seed_trusted_report_bound`,
        which is explicitly non-canonical.
        """

        draft_type = self._classify(draft, operational_source)
        if draft_type in _REPORT_BOUND_DRAFTS:
            raise ReportBoundAppendUnavailableError(
                f"{draft_type.__name__} has no canonical append path in V0A; "
                "trusted report-bound history is represented test-side only."
            )
        return self._record(draft, draft_type, operational_source)

    def seed_trusted_report_bound(
        self, draft: object, *, operational_source: OperationalSource
    ) -> SourceAwareAssessment:
        """Place report-bound history a trusted bridge is assumed to have written.

        TEST ONLY, and test-resident: no production code path leads here. The
        record gets the same shape and the same monotonic sequence a published
        record would have, so derivations see realistic history.
        """

        draft_type = self._classify(draft, operational_source)
        if draft_type not in _REPORT_BOUND_DRAFTS:
            raise AssertionError(
                f"{draft_type.__name__} is not report-bound; append it instead"
            )
        return self._record(draft, draft_type, operational_source)

    # -- internals (test double) -------------------------------------------

    @staticmethod
    def _classify(draft: object, operational_source: OperationalSource) -> type:
        if not isinstance(operational_source, OperationalSource):
            raise UnsupportedAssessmentError(
                "operational_source must be an OperationalSource member"
            )
        draft_type = type(draft)
        if draft_type not in _DRAFT_TO_PERSISTED:
            raise UnsupportedAssessmentError(
                f"{draft_type.__name__} is not one of the exact Contract V1 "
                "assessment drafts"
            )
        return draft_type

    def _record(
        self,
        draft: object,
        draft_type: type,
        operational_source: OperationalSource,
    ) -> SourceAwareAssessment:
        persisted_cls = _DRAFT_TO_PERSISTED[draft_type]
        identity = (
            operational_source.value,
            draft_type.__name__,
            getattr(draft, _IDENTITY_FIELD[draft_type]),
        )
        if identity in self._identities:
            raise DuplicateAssessmentIdentityError(
                f"{draft_type.__name__} identity {identity[2]!r} already exists "
                f"for {operational_source.value}"
            )

        next_sequence = self._sequence + 1
        persisted = persisted_cls(**draft.model_dump(), append_sequence=next_sequence)
        serialized = persisted.model_dump_json()

        self._sequence = next_sequence
        self._serialized.append(
            (operational_source.value, persisted_cls.__name__, serialized)
        )
        self._identities.add(identity)
        return SourceAwareAssessment(
            operational_source=operational_source, assessment=persisted
        )

    # -- reads (the surface the derivations use) ----------------------------

    @staticmethod
    def _rehydrate(
        source_value: str, persisted_name: str, serialized: str
    ) -> SourceAwareAssessment:
        persisted_cls = next(
            cls for cls in _DRAFT_TO_PERSISTED.values() if cls.__name__ == persisted_name
        )
        return SourceAwareAssessment(
            operational_source=OperationalSource(source_value),
            assessment=persisted_cls.model_validate_json(serialized),
        )

    @property
    def records(self) -> tuple[SourceAwareAssessment, ...]:
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
        persisted_name = _DRAFT_TO_PERSISTED[draft_cls].__name__
        return tuple(
            self._rehydrate(source_value, name, serialized)
            for source_value, name, serialized in self._serialized
            if name == persisted_name
            and (operational_source is None or source_value == operational_source.value)
        )

    @property
    def sequence(self) -> int:
        return self._sequence

    def __len__(self) -> int:
        return len(self._serialized)


def production_store() -> EvaluationAssessmentStore:
    """The real store, for tests that must exercise production behavior."""

    return EvaluationAssessmentStore()
