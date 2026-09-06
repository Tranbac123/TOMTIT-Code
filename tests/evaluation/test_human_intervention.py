"""Human intervention events, missingness and owner wait (Contract V1 §16; F06)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from tomtit.domain.enums import HumanInterventionSource
from tomtit.evaluation import (
    HumanInterventionEventDraft,
    InterventionCategory,
    InterventionMeasurementMethod,
    InterventionTotals,
    OperationalSource,
    intervention_totals,
    map_human_intervention_source,
    owner_wait_metric,
)

from .conftest import NOW

RUNTIME = OperationalSource.PRODUCT_RUNTIME
DOGFOOD = OperationalSource.DOGFOOD_ORCHESTRATOR


def _draft(**overrides) -> HumanInterventionEventDraft:
    payload = {
        "intervention_id": "int-1",
        "run_id": "run-1",
        "category": InterventionCategory.CLARIFICATION,
        "requested_at": NOW,
        "responded_at": NOW + timedelta(seconds=300),
        "active_seconds": 45.0,
        "measurement_method": InterventionMeasurementMethod.UI_MEASURED,
        "evidence_refs": ("evidence:int-1",),
    }
    payload.update(overrides)
    return HumanInterventionEventDraft(**payload)


# -- raw event semantics ----------------------------------------------------


def test_raw_event_preserves_category_timing_and_method() -> None:
    event = _draft()
    assert event.category is InterventionCategory.CLARIFICATION
    assert event.requested_at == NOW
    assert event.active_seconds == 45.0
    assert event.measurement_method is InterventionMeasurementMethod.UI_MEASURED


def test_all_eight_v1_categories_exist() -> None:
    assert {member.value for member in InterventionCategory} == {
        "CLARIFICATION",
        "SCOPE_CHANGE",
        "ACCEPTANCE_CHANGE",
        "OPTION_SELECTION",
        "MANUAL_CODE_EDIT",
        "MANUAL_AGENT_RESTART",
        "ERROR_RECOVERY",
        "RESULT_REJECTION",
    }


def test_all_four_v1_measurement_methods_exist() -> None:
    assert {member.value for member in InterventionMeasurementMethod} == {
        "UI_MEASURED",
        "USER_SELF_REPORTED",
        "ESTIMATED",
        "UNAVAILABLE",
    }


def test_response_cannot_precede_request() -> None:
    with pytest.raises(ValidationError):
        _draft(responded_at=NOW - timedelta(seconds=1))


def test_optional_decision_linkage() -> None:
    assert _draft().required_by_decision_id is None
    assert _draft(required_by_decision_id="decision-7").required_by_decision_id == (
        "decision-7"
    )


# -- F06 problem A: unavailable is not zero ---------------------------------


def test_unavailable_measurement_must_have_no_duration() -> None:
    """UNAVAILABLE means unknown; it must not be recorded as a number."""

    with pytest.raises(ValidationError):
        _draft(
            active_seconds=0.0,
            measurement_method=InterventionMeasurementMethod.UNAVAILABLE,
        )
    with pytest.raises(ValidationError):
        _draft(
            active_seconds=30.0,
            measurement_method=InterventionMeasurementMethod.UNAVAILABLE,
        )


def test_unavailable_measurement_is_represented_as_none() -> None:
    event = _draft(
        active_seconds=None,
        measurement_method=InterventionMeasurementMethod.UNAVAILABLE,
    )
    assert event.active_seconds is None
    assert not event.active_seconds_is_known


def test_concrete_method_requires_a_duration() -> None:
    with pytest.raises(ValidationError):
        _draft(
            active_seconds=None,
            measurement_method=InterventionMeasurementMethod.UI_MEASURED,
        )


def test_true_measured_zero_is_valid_and_distinct_from_unknown() -> None:
    measured_zero = _draft(
        active_seconds=0.0,
        measurement_method=InterventionMeasurementMethod.UI_MEASURED,
    )
    unknown = _draft(
        intervention_id="int-2",
        active_seconds=None,
        measurement_method=InterventionMeasurementMethod.UNAVAILABLE,
    )
    assert measured_zero.active_seconds == 0.0
    assert measured_zero.active_seconds_is_known
    assert unknown.active_seconds is None
    assert not unknown.active_seconds_is_known
    assert measured_zero.active_seconds != unknown.active_seconds


def test_aggregate_with_unavailable_duration_is_not_summed_as_zero(store) -> None:
    store.append(_draft(intervention_id="i1", active_seconds=45.0),
                 operational_source=RUNTIME)
    store.append(
        _draft(
            intervention_id="i2",
            active_seconds=None,
            measurement_method=InterventionMeasurementMethod.UNAVAILABLE,
        ),
        operational_source=RUNTIME,
    )

    totals = intervention_totals(store, RUNTIME, "run-1")
    assert totals.intervention_count == 2
    # The unknown duration poisons the total rather than counting as zero.
    assert totals.active_human_intervention_seconds is None
    assert totals.known_active_seconds == 45.0
    assert totals.unavailable_measurement_count == 1
    assert not totals.active_seconds_is_complete


def test_aggregate_of_measured_zero_is_zero_not_unknown(store) -> None:
    store.append(
        _draft(
            intervention_id="i1",
            active_seconds=0.0,
            measurement_method=InterventionMeasurementMethod.UI_MEASURED,
        ),
        operational_source=RUNTIME,
    )
    totals = intervention_totals(store, RUNTIME, "run-1")
    assert totals.active_human_intervention_seconds == 0.0
    assert totals.active_seconds_is_complete


def test_fully_measured_aggregate_sums(store) -> None:
    store.append(_draft(intervention_id="i1", active_seconds=45.0),
                 operational_source=RUNTIME)
    store.append(
        _draft(
            intervention_id="i2",
            category=InterventionCategory.MANUAL_CODE_EDIT,
            active_seconds=120.0,
        ),
        operational_source=RUNTIME,
    )
    totals = intervention_totals(store, RUNTIME, "run-1")
    assert totals.active_human_intervention_seconds == 165.0
    assert dict(totals.counts_by_category) == {
        InterventionCategory.CLARIFICATION: 1,
        InterventionCategory.MANUAL_CODE_EDIT: 1,
    }


# -- F06 problem B: owner wait is not inferred ------------------------------


def test_event_exposes_no_owner_wait_figure() -> None:
    """A request/response span is not proof of Runtime WAITING_OWNER."""

    assert not hasattr(_draft(), "owner_wait_elapsed_seconds")


def test_totals_expose_no_owner_wait_figure() -> None:
    assert "owner_wait_elapsed_seconds" not in InterventionTotals.model_fields


def test_owner_wait_metric_is_unavailable(store) -> None:
    store.append(_draft(), operational_source=RUNTIME)
    metric = owner_wait_metric(store, RUNTIME, "run-1")
    assert not metric.available
    assert metric.elapsed_seconds is None
    assert "WAITING_OWNER" in metric.unavailable_reason


def test_non_owner_intervention_creates_no_owner_wait_time(store) -> None:
    """A long MANUAL_CODE_EDIT span must not become owner-wait time."""

    store.append(
        _draft(
            intervention_id="i1",
            category=InterventionCategory.MANUAL_CODE_EDIT,
            requested_at=NOW,
            responded_at=NOW + timedelta(hours=5),
            active_seconds=60.0,
        ),
        operational_source=RUNTIME,
    )
    metric = owner_wait_metric(store, RUNTIME, "run-1")
    assert metric.elapsed_seconds is None

    totals = intervention_totals(store, RUNTIME, "run-1")
    assert totals.active_human_intervention_seconds == 60.0


def test_overlapping_spans_do_not_accumulate_owner_wait(store) -> None:
    for index in range(3):
        store.append(
            _draft(
                intervention_id=f"i{index}",
                requested_at=NOW,
                responded_at=NOW + timedelta(hours=2),
                active_seconds=10.0,
            ),
            operational_source=RUNTIME,
        )
    assert owner_wait_metric(store, RUNTIME, "run-1").elapsed_seconds is None


# -- aggregates are derived, never synthesized ------------------------------


def test_totals_are_empty_without_events(store) -> None:
    totals = intervention_totals(store, RUNTIME, "run-absent")
    assert totals.intervention_count == 0
    assert totals.counts_by_category == ()
    assert totals.active_human_intervention_seconds == 0.0


def test_totals_are_scoped_per_run_and_source(store) -> None:
    store.append(_draft(intervention_id="i1", run_id="run-1"),
                 operational_source=RUNTIME)
    store.append(_draft(intervention_id="i2", run_id="run-2"),
                 operational_source=RUNTIME)
    store.append(_draft(intervention_id="i3", run_id="run-1"),
                 operational_source=DOGFOOD)

    assert intervention_totals(store, RUNTIME, "run-1").intervention_count == 1
    assert intervention_totals(store, RUNTIME, "run-2").intervention_count == 1
    assert intervention_totals(store, DOGFOOD, "run-1").intervention_count == 1


def test_same_run_id_across_sources_does_not_merge_totals(store) -> None:
    """F10 source collision: run-1 in two sources stays separate."""

    store.append(_draft(intervention_id="i1", active_seconds=10.0),
                 operational_source=RUNTIME)
    store.append(_draft(intervention_id="i2", active_seconds=99.0),
                 operational_source=DOGFOOD)

    runtime_totals = intervention_totals(store, RUNTIME, "run-1")
    dogfood_totals = intervention_totals(store, DOGFOOD, "run-1")
    assert runtime_totals.active_human_intervention_seconds == 10.0
    assert dogfood_totals.active_human_intervention_seconds == 99.0
    assert runtime_totals.operational_source is RUNTIME
    assert dogfood_totals.operational_source is DOGFOOD


def test_no_api_synthesizes_events_from_aggregates() -> None:
    import tomtit.evaluation.derive as derive_module

    forbidden = [
        name
        for name in dir(derive_module)
        if any(
            token in name.lower()
            for token in ("from_totals", "from_aggregate", "synthes", "backfill")
        )
    ]
    assert forbidden == []


# -- legacy vocabulary mapping ----------------------------------------------


def test_explicit_user_report_maps_to_self_reported() -> None:
    mapping = map_human_intervention_source(
        HumanInterventionSource.EXPLICIT_USER_REPORT
    )
    assert mapping.measurement_method is InterventionMeasurementMethod.USER_SELF_REPORTED


def test_unavailable_maps_to_unavailable() -> None:
    mapping = map_human_intervention_source(HumanInterventionSource.UNAVAILABLE)
    assert mapping.measurement_method is InterventionMeasurementMethod.UNAVAILABLE


def test_interaction_timestamps_is_not_silently_coerced() -> None:
    mapping = map_human_intervention_source(
        HumanInterventionSource.INTERACTION_TIMESTAMPS
    )
    assert not mapping.mapped
    assert mapping.measurement_method is None
