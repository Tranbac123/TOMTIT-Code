"""Escaped-defect events and the §23 evaluation window (Contract V1 §23; F07).

Window facts under test, taken from Contract V1 §23:

* ``escaped_defect_window_days = 7``
* the window begins at the effective Owner acceptance timestamp, or the
  effective reviewer-proxy approval timestamp
* ``WINDOW_NOT_COMPLETE`` means the window has not ended
* ``FOUND`` means at least one event exists *inside* the window
* ``NONE_OBSERVED`` means the full window ended with no event inside it

V1 does not state boundary inclusivity, so the implementation uses a half-open
interval ``[start, start + 7d)``; these tests pin that documented choice.

F07: the canonical entry point is :func:`escaped_defect_status`, which takes an
exact source-qualified target plus an observation time and derives the window
itself. There is no public window type and no public duration parameter, so the
tests below drive every window fact through appended authoritative assessments
rather than by asserting one.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from tomtit.evaluation import (
    ESCAPED_DEFECT_WINDOW_DAYS,
    EscapedDefectEventDraft,
    EscapedDefectSeverity,
    EscapedDefectStatusV1,
    OperationalSource,
    OwnerOutcome,
    ReviewOutcome,
    escaped_defect_status,
    report_target_key,
)

from .conftest import NOW

RUNTIME = OperationalSource.PRODUCT_RUNTIME
DOGFOOD = OperationalSource.DOGFOOD_ORCHESTRATOR
WINDOW = timedelta(days=ESCAPED_DEFECT_WINDOW_DAYS)
DAY_7_5 = timedelta(days=7, hours=12)


def _defect(**overrides) -> EscapedDefectEventDraft:
    payload = {
        "event_id": "defect-1",
        "run_id": "run-1",
        "severity": EscapedDefectSeverity.MAJOR,
        "detected_at": NOW + timedelta(days=1),
        "evidence_refs": ("evidence:defect-1",),
    }
    payload.update(overrides)
    return EscapedDefectEventDraft(**payload)


def _accepted_target(seed_owner, *, source=RUNTIME, run_id="run-1"):
    """Seed an authoritative Owner acceptance at ``NOW`` and return its target.

    The acceptance is the only thing that opens a window; nothing in these
    tests can state a start time directly.
    """

    envelope = seed_owner(
        source=source, run_id=run_id, outcome=OwnerOutcome.OWNER_ACCEPTED
    )
    assert envelope.assessment.timestamp == NOW
    return report_target_key(source, envelope.assessment)


# -- raw event --------------------------------------------------------------


def test_contract_window_length_is_seven_days() -> None:
    assert ESCAPED_DEFECT_WINDOW_DAYS == 7


def test_event_shape_follows_contract_not_its_siblings() -> None:
    fields = set(EscapedDefectEventDraft.model_fields)
    assert "event_id" in fields and "detected_at" in fields
    assert "timestamp" not in fields
    assert "assessment_schema_version" not in fields


def test_defect_event_must_cite_evidence() -> None:
    with pytest.raises(ValidationError):
        _defect(evidence_refs=())


def test_appended_defect_event_is_immutable(store) -> None:
    envelope = store.append(_defect(), operational_source=RUNTIME)
    with pytest.raises(ValidationError):
        envelope.assessment.severity = EscapedDefectSeverity.MINOR


# -- window derivation from authoritative assessments -----------------------


def test_window_starts_at_owner_acceptance(store, seed_owner) -> None:
    envelope = seed_owner(outcome=OwnerOutcome.OWNER_ACCEPTED, offset_seconds=60)
    target = report_target_key(RUNTIME, envelope.assessment)
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW * 2)
    assert result.available
    assert result.window_started_at == envelope.assessment.timestamp
    assert result.window_ends_at == envelope.assessment.timestamp + WINDOW


def test_window_starts_at_reviewer_proxy_approval(store, seed_review) -> None:
    envelope = seed_review(outcome=ReviewOutcome.REVIEWER_APPROVED)
    target = report_target_key(RUNTIME, envelope.assessment)
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW)
    assert result.available
    assert result.window_started_at == envelope.assessment.timestamp


def test_non_blocking_findings_approval_also_opens_window(store, seed_review) -> None:
    envelope = seed_review(
        outcome=ReviewOutcome.REVIEWER_APPROVED_WITH_NON_BLOCKING_FINDINGS
    )
    target = report_target_key(RUNTIME, envelope.assessment)
    assert escaped_defect_status(store, target, evaluated_at=NOW + WINDOW).available


@pytest.mark.parametrize(
    "outcome",
    [ReviewOutcome.REVIEWER_REQUESTED_CHANGES, ReviewOutcome.REVIEWER_BLOCKED],
)
def test_non_approval_review_does_not_open_window(store, seed_review, outcome) -> None:
    envelope = seed_review(outcome=outcome)
    target = report_target_key(RUNTIME, envelope.assessment)
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW)
    assert not result.available
    assert result.window_started_at is None


def test_owner_rejection_does_not_open_window(store, seed_owner) -> None:
    envelope = seed_owner(outcome=OwnerOutcome.OWNER_REJECTED)
    target = report_target_key(RUNTIME, envelope.assessment)
    assert not escaped_defect_status(store, target, evaluated_at=NOW + WINDOW).available


def test_owner_rejection_after_approval_closes_the_window(
    store, seed_review, seed_owner
) -> None:
    """§11 precedence: the Owner outranks the proxy, so no window remains."""

    seed_review(outcome=ReviewOutcome.REVIEWER_APPROVED)
    envelope = seed_owner(outcome=OwnerOutcome.OWNER_REJECTED)
    target = report_target_key(RUNTIME, envelope.assessment)
    store.append(_defect(detected_at=NOW + timedelta(days=1)), operational_source=RUNTIME)
    assert not escaped_defect_status(store, target, evaluated_at=NOW + WINDOW).available


def test_missing_acceptance_facts_yield_unavailable(store, seed_review) -> None:
    """No acceptance means no window start — not an assumed status."""

    envelope = seed_review(outcome=ReviewOutcome.REVIEWER_BLOCKED)
    store.append(_defect(), operational_source=RUNTIME)
    result = escaped_defect_status(
        store,
        report_target_key(RUNTIME, envelope.assessment),
        evaluated_at=NOW + WINDOW,
    )
    assert not result.available
    assert result.status is None
    assert "no start" in result.unavailable_reason


def test_unknown_target_is_unavailable_not_none_observed(store) -> None:
    unknown = (
        "PRODUCT_RUNTIME",
        "run-x",
        "report-x",
        "a" * 64,
        "GIT_COMMIT",
        "f" * 40,
    )
    result = escaped_defect_status(store, unknown, evaluated_at=NOW + WINDOW)
    assert not result.available
    assert result.status is not EscapedDefectStatusV1.NONE_OBSERVED


# -- temporal boundaries ----------------------------------------------------


def test_defect_just_before_acceptance_is_outside_window(store, seed_owner) -> None:
    target = _accepted_target(seed_owner)
    store.append(
        _defect(detected_at=NOW - timedelta(seconds=1)), operational_source=RUNTIME
    )
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW)
    assert result.status is EscapedDefectStatusV1.NONE_OBSERVED


def test_defect_exactly_at_acceptance_is_inside_window(store, seed_owner) -> None:
    """Start is inclusive."""

    target = _accepted_target(seed_owner)
    store.append(_defect(detected_at=NOW), operational_source=RUNTIME)
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW)
    assert result.status is EscapedDefectStatusV1.FOUND


def test_day_six_defect_is_found(store, seed_owner) -> None:
    target = _accepted_target(seed_owner)
    store.append(
        _defect(detected_at=NOW + timedelta(days=6)), operational_source=RUNTIME
    )
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW)
    assert result.status is EscapedDefectStatusV1.FOUND


def test_defect_exactly_at_seven_day_boundary_is_outside(store, seed_owner) -> None:
    """End is exclusive: [start, start + 7d)."""

    target = _accepted_target(seed_owner)
    store.append(_defect(detected_at=NOW + WINDOW), operational_source=RUNTIME)
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW)
    assert result.status is EscapedDefectStatusV1.NONE_OBSERVED


def test_defect_just_inside_boundary_is_found(store, seed_owner) -> None:
    target = _accepted_target(seed_owner)
    store.append(
        _defect(detected_at=NOW + WINDOW - timedelta(seconds=1)),
        operational_source=RUNTIME,
    )
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW)
    assert result.status is EscapedDefectStatusV1.FOUND


def test_day_ten_defect_is_not_found(store, seed_owner) -> None:
    """The F07 probe: a late defect must never be pulled into the window."""

    target = _accepted_target(seed_owner)
    detected_at = NOW + timedelta(days=10)
    store.append(_defect(detected_at=detected_at), operational_source=RUNTIME)
    result = escaped_defect_status(
        store, target, evaluated_at=detected_at + timedelta(days=1)
    )
    assert result.status is EscapedDefectStatusV1.NONE_OBSERVED
    assert result.window_ends_at == NOW + WINDOW


def test_defect_at_day_seven_and_a_half_is_outside_the_canonical_window(
    store, seed_owner
) -> None:
    target = _accepted_target(seed_owner)
    store.append(_defect(detected_at=NOW + DAY_7_5), operational_source=RUNTIME)
    result = escaped_defect_status(
        store, target, evaluated_at=NOW + DAY_7_5 + timedelta(days=1)
    )
    assert result.status is EscapedDefectStatusV1.NONE_OBSERVED


# -- window completion ------------------------------------------------------


def test_window_not_complete_before_seven_days(store, seed_owner) -> None:
    target = _accepted_target(seed_owner)
    result = escaped_defect_status(store, target, evaluated_at=NOW + timedelta(days=3))
    assert result.status is EscapedDefectStatusV1.WINDOW_NOT_COMPLETE
    assert len(store.records_of_type(EscapedDefectEventDraft)) == 0


def test_window_complete_exactly_at_seven_days(store, seed_owner) -> None:
    target = _accepted_target(seed_owner)
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW)
    assert result.status is EscapedDefectStatusV1.NONE_OBSERVED


def test_found_takes_precedence_over_incomplete_window(store, seed_owner) -> None:
    """An observed in-window defect is an append-only fact."""

    target = _accepted_target(seed_owner)
    store.append(
        _defect(detected_at=NOW + timedelta(days=1)), operational_source=RUNTIME
    )
    result = escaped_defect_status(store, target, evaluated_at=NOW + timedelta(days=2))
    assert result.status is EscapedDefectStatusV1.FOUND


def test_derived_status_vocabulary_has_no_cleared_state() -> None:
    assert {member.value for member in EscapedDefectStatusV1} == {
        "WINDOW_NOT_COMPLETE",
        "FOUND",
        "NONE_OBSERVED",
    }


def test_later_activity_cannot_erase_an_earlier_defect_fact(store, seed_owner) -> None:
    target = _accepted_target(seed_owner)
    store.append(_defect(event_id="d1"), operational_source=RUNTIME)
    store.append(
        _defect(event_id="d2", severity=EscapedDefectSeverity.MINOR),
        operational_source=RUNTIME,
    )
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW)
    assert result.status is EscapedDefectStatusV1.FOUND
    assert len(store.records_of_type(EscapedDefectEventDraft)) == 2


def test_legacy_status_enum_is_untouched() -> None:
    from tomtit.domain.enums import EscapedDefectStatus

    assert {member.value for member in EscapedDefectStatus} == {
        "unknown",
        "none_reported",
        "reported",
        "cleared",
    }


# -- F10: source and run isolation ------------------------------------------


def test_same_run_id_different_source_does_not_produce_found(
    store, seed_owner
) -> None:
    """A dogfood defect must not mark the Product Runtime run as FOUND."""

    runtime_target = _accepted_target(seed_owner, source=RUNTIME, run_id="run-1")
    dogfood_target = _accepted_target(seed_owner, source=DOGFOOD, run_id="run-1")
    assert runtime_target != dogfood_target

    store.append(_defect(run_id="run-1"), operational_source=DOGFOOD)

    runtime_result = escaped_defect_status(
        store, runtime_target, evaluated_at=NOW + WINDOW
    )
    assert runtime_result.status is EscapedDefectStatusV1.NONE_OBSERVED
    assert runtime_result.operational_source is RUNTIME

    dogfood_result = escaped_defect_status(
        store, dogfood_target, evaluated_at=NOW + WINDOW
    )
    assert dogfood_result.status is EscapedDefectStatusV1.FOUND
    assert dogfood_result.operational_source is DOGFOOD


def test_defect_from_another_run_does_not_leak(store, seed_owner) -> None:
    target = _accepted_target(seed_owner, run_id="run-1")
    store.append(_defect(run_id="run-2"), operational_source=RUNTIME)
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW)
    assert result.status is EscapedDefectStatusV1.NONE_OBSERVED
    assert result.run_id == "run-1"


def test_result_carries_its_operational_source(store, seed_owner) -> None:
    target = _accepted_target(seed_owner, source=DOGFOOD)
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW)
    assert result.operational_source is DOGFOOD


# -- F07: the seven-day duration is fixed and never caller-supplied ---------


def test_canonical_window_duration_is_exactly_seven_days(store, seed_owner) -> None:
    """The duration is a property of the canonical derivation, not an input."""

    assert ESCAPED_DEFECT_WINDOW_DAYS == 7
    target = _accepted_target(seed_owner)
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW)
    assert result.window_days == 7
    assert result.window_ends_at - result.window_started_at == timedelta(days=7)


@pytest.mark.parametrize("days", [8, 14, 0, -1])
def test_canonical_api_rejects_a_caller_supplied_duration(
    store, seed_owner, days
) -> None:
    """F07: ``window_days`` is not a parameter of the canonical API at all."""

    target = _accepted_target(seed_owner)
    with pytest.raises(TypeError):
        escaped_defect_status(
            store,
            target,
            evaluated_at=NOW + WINDOW,
            window_days=days,  # type: ignore[call-arg]
        )


def test_canonical_api_signature_exposes_no_window_or_duration() -> None:
    import inspect

    params = inspect.signature(escaped_defect_status).parameters
    assert set(params) == {"store", "target", "evaluated_at"}
    for forbidden in ("window", "window_days", "started_at", "accepted_at", "days"):
        assert forbidden not in params


def test_window_type_is_not_public() -> None:
    """F07 option A: the window is an internal implementation detail."""

    import tomtit.evaluation as package

    for name in ("EvaluationWindow", "evaluation_window_for_target"):
        assert name not in package.__all__, name
        assert not hasattr(package, name), name
    for name in package.__all__:
        assert "Window" not in name or name == "ESCAPED_DEFECT_WINDOW_DAYS", name


def test_caller_manufactured_window_cannot_control_derivation(
    store, seed_owner
) -> None:
    """Even holding the private type, a fabricated window reaches no public API.

    A caller who reaches into ``tomtit.evaluation.derive`` can build a window
    asserting a 14-day-old acceptance, but there is no public function that
    accepts one, so it cannot turn a day-10 defect into ``FOUND``.
    """

    from tomtit.evaluation.derive import _EvaluationWindow

    target = _accepted_target(seed_owner)
    detected_at = NOW + timedelta(days=10)
    store.append(_defect(detected_at=detected_at), operational_source=RUNTIME)

    forged = _EvaluationWindow(
        operational_source=RUNTIME,
        run_id="run-1",
        started_at=NOW - timedelta(days=7),
        evaluated_at=detected_at + timedelta(days=1),
    )
    # Even the private window cannot represent a non-seven-day duration.
    assert forged.window_days == 7
    assert forged.ends_at - forged.started_at == timedelta(days=7)
    with pytest.raises(ValidationError):
        _EvaluationWindow(
            operational_source=RUNTIME,
            run_id="run-1",
            started_at=NOW,
            evaluated_at=NOW + WINDOW,
            window_days=14,  # type: ignore[call-arg]
        )

    # And it cannot be supplied to the canonical derivation.
    with pytest.raises(TypeError):
        escaped_defect_status(store, forged)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        escaped_defect_status(
            store, target, evaluated_at=NOW, window=forged  # type: ignore[call-arg]
        )

    result = escaped_defect_status(
        store, target, evaluated_at=detected_at + timedelta(days=1)
    )
    assert result.status is EscapedDefectStatusV1.NONE_OBSERVED
    assert result.window_started_at == NOW


def test_result_window_facts_are_immutable(store, seed_owner) -> None:
    target = _accepted_target(seed_owner)
    result = escaped_defect_status(store, target, evaluated_at=NOW + WINDOW)
    with pytest.raises(ValidationError):
        result.window_days = 14  # type: ignore[misc]
    with pytest.raises(ValidationError):
        result.window_started_at = NOW - timedelta(days=14)  # type: ignore[misc]
    assert result.window_days == 7


# -- F07: no caller-supplied window behavior reaches canonical status -------


def test_window_subclass_cannot_widen_the_canonical_interval(
    store, seed_owner
) -> None:
    """F07: overriding ``ends_at`` on a subclass reaches no public status path.

    The substitution Codex demonstrated needs two things: a window object whose
    behavior differs from the canonical seven days, *and* a public evaluator
    that accepts one. The first is still constructible by anyone willing to
    import a private name and subclass it — the second no longer exists, so a
    day-7.5 defect cannot be turned into ``FOUND``.
    """

    from datetime import datetime

    from tomtit.evaluation.derive import _EvaluationWindow

    class WidenedWindow(_EvaluationWindow):
        """A hostile window asserting an eight-day escaped-defect interval."""

        @property
        def ends_at(self) -> datetime:
            return self.started_at + timedelta(days=8)

    target = _accepted_target(seed_owner)
    detected_at = NOW + DAY_7_5
    evaluated_at = detected_at + timedelta(days=1)
    store.append(_defect(detected_at=detected_at), operational_source=RUNTIME)

    widened = WidenedWindow(
        operational_source=RUNTIME,
        run_id="run-1",
        started_at=NOW,
        evaluated_at=evaluated_at,
    )
    # The subclass really would classify day 7.5 as inside, so this test would
    # catch any public path that let it decide the interval.
    assert widened.ends_at - widened.started_at == timedelta(days=8)
    assert widened.contains(detected_at)

    # No public canonical entry point accepts it, positionally or by keyword.
    with pytest.raises(TypeError):
        escaped_defect_status(store, widened)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        escaped_defect_status(
            store, target, evaluated_at=evaluated_at, window=widened  # type: ignore[call-arg]
        )
    # Passing it where the source-qualified target belongs matches no appended
    # assessment, so the result is unavailable — never ``FOUND``.
    substituted = escaped_defect_status(
        store, widened, evaluated_at=evaluated_at  # type: ignore[arg-type]
    )
    assert not substituted.available
    assert substituted.status is None

    # The canonical derivation keeps the seven-day interval and the real facts.
    result = escaped_defect_status(store, target, evaluated_at=evaluated_at)
    assert result.status is EscapedDefectStatusV1.NONE_OBSERVED
    assert result.window_days == 7
    assert result.window_ends_at == NOW + WINDOW


def test_no_public_callable_accepts_a_window_object() -> None:
    """F07: the substitution path is closed at the API surface, not by guards."""

    import inspect

    import tomtit.evaluation as package
    from tomtit.evaluation.derive import _EvaluationWindow

    window_types = {_EvaluationWindow, *_EvaluationWindow.__subclasses__()}
    window_names = {cls.__name__ for cls in window_types} | {
        "EvaluationWindow",
        "_EvaluationWindow",
    }
    for name in package.__all__:
        obj = getattr(package, name)
        if not callable(obj) or isinstance(obj, type):
            continue
        for param in inspect.signature(obj).parameters.values():
            assert "window" not in param.name, (name, param.name)
            annotation = param.annotation
            if isinstance(annotation, str):
                assert not any(w in annotation for w in window_names), (name, annotation)
            else:
                assert annotation not in window_types, (name, annotation)


def test_escaped_defect_public_surface_is_coherent() -> None:
    """F10 item 9: exports resolve, and none of them is a window affordance."""

    import tomtit.evaluation as package

    namespace: dict[str, object] = {}
    exec("from tomtit.evaluation import *", namespace)  # noqa: S102
    exported = {name for name in namespace if not name.startswith("__")}
    assert exported == set(package.__all__)

    assert package.escaped_defect_status is not None
    assert package.EscapedDefectAssessment is not None
    assert package.ESCAPED_DEFECT_WINDOW_DAYS == 7
    for leaked in (
        "EvaluationWindow",
        "_EvaluationWindow",
        "evaluation_window_for_target",
        "_evaluation_window_for_target",
        "ESCAPED_DEFECT_WINDOW",
    ):
        assert leaked not in package.__all__, leaked
