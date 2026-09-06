"""Derived views over the Evaluation assessment stream.

Everything here reads appended events. Nothing appends, and nothing
reconstructs raw events from aggregates. Contract V1 fixes the direction:

* §10 — ``owner_review_status = UNREVIEWED`` is derived from absence.
* §23 — escaped-defect absence is derived, never emitted.
* §16 — per-intervention events are raw history; totals come from them.
* §11 — the effective assessment is the greatest ``append_sequence``, and a
  valid Owner assessment outranks a reviewer proxy.
* §8  — timestamp is "for display and audit, not primary ordering authority".
* §29 — "Missing data is never silently treated as success."

Every lookup is source-qualified: Product Runtime ``run_id=X`` and dogfood
``run_id=X`` are different subjects.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import Field

from tomtit.evaluation.assessments import (
    REVIEWER_PROXY_APPROVAL_OUTCOMES,
    EscapedDefectEventDraft,
    EscapedDefectStatusV1,
    HumanInterventionEventDraft,
    InterventionCategory,
    OwnerAssessmentEventDraft,
    OwnerOutcome,
    OwnerReviewStatus,
    ReviewAssessmentEventDraft,
    SourceAwareAssessment,
    report_target_key,
)
from tomtit.evaluation.provenance import (
    EvaluationModel,
    NonEmptyString,
    OperationalSource,
)
from tomtit.evaluation.store import EvaluationAssessmentStore

#: Contract V1 §23: ``escaped_defect_window_days = 7``.
ESCAPED_DEFECT_WINDOW_DAYS = 7

#: The same fixed duration as a ``timedelta``. Canonical windows are built from
#: this constant alone; no caller supplies a duration.
ESCAPED_DEFECT_WINDOW = timedelta(days=ESCAPED_DEFECT_WINDOW_DAYS)

#: Source-qualified §11 target key.
TargetKey = tuple[str, str, str, str, str, str]


def _report_bound_matching(
    store: EvaluationAssessmentStore, draft_cls: type, target: TargetKey
) -> tuple[SourceAwareAssessment, ...]:
    return tuple(
        record
        for record in store.records_of_type(draft_cls)
        if report_target_key(record.operational_source, record.assessment) == target
    )


class CrossSourceSelectionError(ValueError):
    """Raised when latest-selection is handed records outside its target.

    Contract V1 §8 forbids comparing positions across streams, and the R2.2
    source axis extends the same rule within this one: ``PRODUCT_RUNTIME``
    ``run_id=X`` and ``DOGFOOD_ORCHESTRATOR`` ``run_id=X`` are different
    subjects. Choosing a "latest" across them would let an Evaluation-local
    append order decide which operational source wins, which is not a fact
    Evaluation holds.
    """


def _latest_by_sequence(
    records: tuple[SourceAwareAssessment, ...],
    target: TargetKey,
) -> SourceAwareAssessment | None:
    """Greatest ``append_sequence`` *within one exact target* (§11, §8).

    Private by design (F01). There is no public operation that reduces a bag of
    records to a single "latest", because such an operation cannot tell whether
    its inputs share one source-local assessment domain. Every public caller
    passes an exact source-qualified target, and any record outside that target
    is a programming error rather than something to silently ignore.
    """

    off_target = [
        record
        for record in records
        if report_target_key(record.operational_source, record.assessment) != target
    ]
    if off_target:
        raise CrossSourceSelectionError(
            "latest-assessment selection received records outside the requested "
            f"source-qualified target {target}: "
            f"{[report_target_key(r.operational_source, r.assessment) for r in off_target]}"
        )
    if not records:
        return None
    return max(records, key=lambda record: record.append_sequence)


# ---------------------------------------------------------------------------
# Owner review status and effective assessment (§10, §11).
# ---------------------------------------------------------------------------


def owner_review_status(
    store: EvaluationAssessmentStore, target: TargetKey
) -> OwnerReviewStatus:
    """Derive Contract V1 §10 ``owner_review_status`` for an exact target."""

    latest = _latest_by_sequence(
        _report_bound_matching(store, OwnerAssessmentEventDraft, target), target
    )
    if latest is None:
        return OwnerReviewStatus.UNREVIEWED
    if latest.assessment.owner_outcome is OwnerOutcome.OWNER_ACCEPTED:
        return OwnerReviewStatus.OWNER_ACCEPTED
    return OwnerReviewStatus.OWNER_REJECTED


class EffectiveAssessment(EvaluationModel):
    """Which assessment governs a target, per Contract V1 §11 precedence."""

    operational_source: OperationalSource
    authority: str
    append_sequence: Annotated[int, Field(ge=1)]
    outcome: str
    decided_at: datetime


def effective_assessment(
    store: EvaluationAssessmentStore, target: TargetKey
) -> EffectiveAssessment | None:
    """Resolve the governing assessment for an exact source-qualified target."""

    owner_latest = _latest_by_sequence(
        _report_bound_matching(store, OwnerAssessmentEventDraft, target), target
    )
    if owner_latest is not None:
        return EffectiveAssessment(
            operational_source=owner_latest.operational_source,
            authority="OWNER",
            append_sequence=owner_latest.append_sequence,
            outcome=owner_latest.assessment.owner_outcome.value,
            decided_at=owner_latest.assessment.timestamp,
        )

    review_latest = _latest_by_sequence(
        _report_bound_matching(store, ReviewAssessmentEventDraft, target), target
    )
    if review_latest is not None:
        return EffectiveAssessment(
            operational_source=review_latest.operational_source,
            authority="REVIEWER_PROXY",
            append_sequence=review_latest.append_sequence,
            outcome=review_latest.assessment.review_outcome.value,
            decided_at=review_latest.assessment.timestamp,
        )
    return None


# ---------------------------------------------------------------------------
# Escaped-defect evaluation window (§23).
# ---------------------------------------------------------------------------


class _EvaluationWindow(EvaluationModel):
    """Internal §23 window. Not public, not constructible by a caller (F07).

    §23 states the window "begins at: effective Owner acceptance timestamp; or
    effective reviewer-proxy approval timestamp", and runs
    ``escaped_defect_window_days = 7``.

    Neither end of that interval is an input to the canonical derivation:

    * the duration is fixed at :data:`ESCAPED_DEFECT_WINDOW` and no field,
      property or constructor argument can substitute another value;
    * the start is derived from appended authoritative assessments by
      :func:`_evaluation_window_for_target`, never supplied.

    The class is private because a caller-manufactured window is exactly the
    defect being repaired: a public window type would let a caller assert an
    acceptance time and a run that Evaluation never observed, and have the
    canonical status derivation treat that assertion as fact. The only public
    surface is :func:`escaped_defect_status`, which builds this internally.

    Boundary semantics: Contract V1 fixes the window length and its start but
    does **not** state boundary inclusivity. This implementation uses a
    half-open interval ``[started_at, started_at + 7d)`` — start inclusive, end
    exclusive — pinned by boundary tests. A defect detected before
    ``started_at`` is outside the window, since the window opens at acceptance.
    """

    operational_source: OperationalSource
    run_id: NonEmptyString
    started_at: datetime
    evaluated_at: datetime

    @property
    def window_days(self) -> int:
        """The Contract V1 §23 duration: always seven days, never overridable."""

        return ESCAPED_DEFECT_WINDOW_DAYS

    @property
    def ends_at(self) -> datetime:
        return self.started_at + ESCAPED_DEFECT_WINDOW

    @property
    def is_complete(self) -> bool:
        """Whether the full window has ended as of ``evaluated_at``."""

        return self.evaluated_at >= self.ends_at

    def contains(self, moment: datetime) -> bool:
        """Half-open membership: ``started_at <= moment < ends_at``."""

        return self.started_at <= moment < self.ends_at


class EscapedDefectAssessment(EvaluationModel):
    """Result of a §23 derivation, or an explicit statement that it is unavailable.

    Contract V1 offers exactly three statuses and none of them means "the
    authoritative acceptance facts are missing". Rather than borrow
    ``WINDOW_NOT_COMPLETE`` for that case — which would assert a window exists
    and merely has not ended — an absent window yields ``status=None`` with a
    reason.

    The window fields are *reported* derivation facts, not inputs: they are
    populated from the internally derived window so a caller can audit which
    interval was used, and they are absent whenever the status is unavailable.
    """

    status: EscapedDefectStatusV1 | None = None
    unavailable_reason: str | None = None
    operational_source: OperationalSource | None = None
    run_id: str | None = None
    window_started_at: datetime | None = None
    window_ends_at: datetime | None = None
    window_days: int | None = None

    @property
    def available(self) -> bool:
        return self.status is not None


def _evaluation_window_for_target(
    store: EvaluationAssessmentStore,
    target: TargetKey,
    *,
    evaluated_at: datetime,
) -> _EvaluationWindow | None:
    """Derive the §23 window start from authoritative appended assessments.

    Only the window *start* is derived; its length is the fixed Contract V1
    seven days and is deliberately not a parameter of this function.

    Returns ``None`` when no effective acceptance or proxy *approval* exists:
    a rejected Owner assessment or a REQUEST_CHANGES/BLOCKED review does not
    open an escaped-defect window, and nothing here invents an acceptance time.
    """

    effective = effective_assessment(store, target)
    if effective is None:
        return None
    if effective.authority == "OWNER":
        if effective.outcome != OwnerOutcome.OWNER_ACCEPTED.value:
            return None
    elif effective.outcome not in {
        outcome.value for outcome in REVIEWER_PROXY_APPROVAL_OUTCOMES
    }:
        return None

    return _EvaluationWindow(
        operational_source=effective.operational_source,
        run_id=target[1],
        started_at=effective.decided_at,
        evaluated_at=evaluated_at,
    )


def escaped_defect_status(
    store: EvaluationAssessmentStore,
    target: TargetKey,
    *,
    evaluated_at: datetime,
) -> EscapedDefectAssessment:
    """Derive Contract V1 §23 escaped-defect status for one source-qualified target.

    The canonical entry point, and the only public one. It takes an exact
    source-qualified target and an observation time; it does **not** take a
    window, a duration, an acceptance timestamp or a source/run pair chosen
    independently of the assessments actually appended:

    * the window start comes from the effective acceptance or reviewer-proxy
      approval for exactly this target (§11, §23);
    * the duration is :data:`ESCAPED_DEFECT_WINDOW_DAYS`, an implementation
      constant, so a caller cannot widen or narrow the evaluated interval;
    * the operational source and run are taken from that same authoritative
      fact, so a defect observed from another source, or for another run,
      cannot produce ``FOUND``.

    Only defect events from the same operational source, for the same run,
    whose ``detected_at`` falls inside ``[start, start + 7d)`` produce
    ``FOUND``. When no authoritative acceptance exists the result is
    unavailable — never an assumed ``NONE_OBSERVED`` (§29).
    """

    window = _evaluation_window_for_target(store, target, evaluated_at=evaluated_at)
    if window is None:
        return EscapedDefectAssessment(
            unavailable_reason=(
                "no authoritative acceptance or reviewer-proxy approval exists "
                "for this target, so the Contract V1 §23 evaluation window has "
                "no start; escaped-defect status is unavailable rather than "
                "assumed"
            )
        )

    reported = {
        "operational_source": window.operational_source,
        "run_id": window.run_id,
        "window_started_at": window.started_at,
        "window_ends_at": window.ends_at,
        "window_days": window.window_days,
    }

    in_window = [
        record
        for record in store.records_of_type(
            EscapedDefectEventDraft,
            operational_source=window.operational_source,
        )
        if record.assessment.run_id == window.run_id
        and window.contains(record.assessment.detected_at)
    ]
    if in_window:
        return EscapedDefectAssessment(
            status=EscapedDefectStatusV1.FOUND, **reported
        )
    if not window.is_complete:
        return EscapedDefectAssessment(
            status=EscapedDefectStatusV1.WINDOW_NOT_COMPLETE, **reported
        )
    return EscapedDefectAssessment(
        status=EscapedDefectStatusV1.NONE_OBSERVED, **reported
    )


# ---------------------------------------------------------------------------
# Human intervention aggregates (§16).
# ---------------------------------------------------------------------------


class InterventionTotals(EvaluationModel):
    """Aggregates derived from raw ``HumanInterventionEvent`` history.

    ``active_human_intervention_seconds`` is ``None`` whenever any contributing
    event has an unavailable measurement: an unknown duration is not zero, and
    summing it as zero would understate human effort and could turn missing
    data into apparent success (§29). ``known_active_seconds`` reports the
    partial sum over measured events only, alongside the count that was not
    measurable, so a caller can see exactly what is missing.

    ``owner_wait_elapsed_seconds`` is deliberately absent. §16 requires Owner
    wall-clock wait to be tracked separately from active effort, and Evaluation
    has no authoritative ``WAITING_OWNER`` facts: intervention request/response
    spans cover non-owner categories and may overlap, so summing them would
    manufacture a Runtime fact. See :class:`OwnerWaitMetric`.
    """

    operational_source: OperationalSource
    run_id: str
    intervention_count: Annotated[int, Field(ge=0)]
    active_human_intervention_seconds: float | None
    known_active_seconds: float
    unavailable_measurement_count: Annotated[int, Field(ge=0)]
    counts_by_category: tuple[tuple[InterventionCategory, int], ...]
    unresolved_intervention_count: Annotated[int, Field(ge=0)]

    @property
    def active_seconds_is_complete(self) -> bool:
        return self.unavailable_measurement_count == 0


class OwnerWaitMetric(EvaluationModel):
    """Owner wall-clock wait, or an explicit statement that it is unavailable.

    EVAL-01 has no authoritative owner-wait or ``WAITING_OWNER`` window facts,
    so this is always unavailable today. Producing real values requires Runtime
    owner-wait instrumentation, which is a future source/bridge dependency and
    is not implemented here.
    """

    elapsed_seconds: float | None = None
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.elapsed_seconds is not None


def owner_wait_metric(
    store: EvaluationAssessmentStore,
    operational_source: OperationalSource,
    run_id: str,
) -> OwnerWaitMetric:
    """Return the owner-wait metric, which is unavailable without Runtime facts.

    Intentionally does not read intervention spans: a request/response span is
    not proof that Runtime was in ``WAITING_OWNER``, and several such spans may
    overlap or belong to non-owner categories.
    """

    return OwnerWaitMetric(
        unavailable_reason=(
            "owner wall-clock wait requires authoritative Runtime "
            "WAITING_OWNER window facts, which Evaluation does not have; "
            "intervention request/response spans are not a substitute "
            "(Contract V1 §16). Future dependency: EVAL-RUNTIME-BRIDGE."
        )
    )


def intervention_totals(
    store: EvaluationAssessmentStore,
    operational_source: OperationalSource,
    run_id: str,
) -> InterventionTotals:
    """Derive per-run intervention aggregates from raw events only."""

    events = [
        record.assessment
        for record in store.records_of_type(
            HumanInterventionEventDraft, operational_source=operational_source
        )
        if record.assessment.run_id == run_id
    ]

    counts: dict[InterventionCategory, int] = {}
    for event in events:
        counts[event.category] = counts.get(event.category, 0) + 1

    known = [event for event in events if event.active_seconds_is_known]
    unavailable_count = len(events) - len(known)
    known_total = sum(event.active_seconds or 0.0 for event in known)

    return InterventionTotals(
        operational_source=operational_source,
        run_id=run_id,
        intervention_count=len(events),
        # None whenever any contributing measurement is unknown.
        active_human_intervention_seconds=(
            known_total if unavailable_count == 0 else None
        ),
        known_active_seconds=known_total,
        unavailable_measurement_count=unavailable_count,
        counts_by_category=tuple(
            (category, counts[category])
            for category in InterventionCategory
            if category in counts
        ),
        unresolved_intervention_count=sum(
            1 for event in events if event.responded_at is None
        ),
    )
