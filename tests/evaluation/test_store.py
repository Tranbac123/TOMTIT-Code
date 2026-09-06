"""Store authority, ordering, atomicity, duplicates and trust boundary.

Covers Contract V1 §2.3 and §8, plus repair findings F02, F03, F04, F05.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from tomtit.evaluation import (
    ASSESSMENT_SCHEMA_VERSION,
    EVALUATION_ASSESSMENT_STREAM,
    INTERVENTION_SCHEMA_VERSION,
    RUNTIME_EXECUTION_STREAM,
    DecisionAssessmentEventDraft,
    DecisionAssessmentMethod,
    DecisionReviewLabel,
    DuplicateAssessmentIdentityError,
    EscapedDefectEventDraft,
    EscapedDefectSeverity,
    EvaluationAssessmentStore,
    HumanInterventionEventDraft,
    InterventionCategory,
    InterventionMeasurementMethod,
    OperationalSource,
    OwnerAssessmentEventDraft,
    ReviewAssessmentEventDraft,
    ReportBoundAppendUnavailableError,
    ReviewOutcome,
    UnsupportedAssessmentError,
)

from .conftest import ACTIVE_COMMIT, NOW

RUNTIME = OperationalSource.PRODUCT_RUNTIME
DOGFOOD = OperationalSource.DOGFOOD_ORCHESTRATOR


def _defect(**overrides) -> EscapedDefectEventDraft:
    payload = {
        "event_id": "defect-1",
        "run_id": "run-1",
        "severity": EscapedDefectSeverity.MAJOR,
        "detected_at": NOW,
        "evidence_refs": ("evidence:d1",),
    }
    payload.update(overrides)
    return EscapedDefectEventDraft(**payload)


def _intervention(**overrides) -> HumanInterventionEventDraft:
    payload = {
        "intervention_id": "int-1",
        "run_id": "run-1",
        "category": InterventionCategory.CLARIFICATION,
        "requested_at": NOW,
        "active_seconds": 30.0,
        "measurement_method": InterventionMeasurementMethod.UI_MEASURED,
    }
    payload.update(overrides)
    return HumanInterventionEventDraft(**payload)


def _decision(**overrides) -> DecisionAssessmentEventDraft:
    payload = {
        "assessment_id": "assess-decision-1",
        "run_id": "run-1",
        "decision_id": "decision-1",
        "review_label": DecisionReviewLabel.CORRECT,
        "assessment_method": DecisionAssessmentMethod.HUMAN_REVIEW,
        "reviewer_or_experiment_id": "codex",
        "timestamp": NOW,
        "evidence_refs": ("evidence:d1",),
    }
    payload.update(overrides)
    return DecisionAssessmentEventDraft(**payload)


# -- F02: sequence lives inside the persisted V1 event -----------------------


def test_persisted_event_contains_append_sequence(seed_review) -> None:
    envelope = seed_review()
    assert envelope.assessment.append_sequence == 1
    payload = json.loads(envelope.assessment.model_dump_json())
    assert payload["append_sequence"] == 1


@pytest.mark.parametrize(
    "make,source_kwargs",
    [
        ("review", {}),
        ("owner", {}),
        ("decision", {}),
        ("intervention", {}),
        ("defect", {}),
    ],
)
def test_all_five_persisted_events_serialize_with_sequence(
    store, seed_review, seed_owner, make, source_kwargs
) -> None:
    """Literal serialization check for every Contract V1 event type."""

    if make == "review":
        envelope = seed_review()
    elif make == "owner":
        envelope = seed_owner()
    else:
        draft = {"decision": _decision, "intervention": _intervention, "defect": _defect}[
            make
        ]()
        envelope = store.append(draft, operational_source=RUNTIME)

    payload = json.loads(envelope.assessment.model_dump_json())
    assert "append_sequence" in payload
    assert payload["append_sequence"] >= 1
    # operational_source is an envelope concern, not a V1 event field (F01).
    assert "operational_source" not in payload
    assert envelope.operational_source is RUNTIME


def test_draft_cannot_carry_append_sequence(review_draft) -> None:
    payload = review_draft().model_dump()
    payload["append_sequence"] = 99
    with pytest.raises(ValidationError):
        ReviewAssessmentEventDraft.model_validate(payload)


def test_caller_cannot_forge_sequence_via_persisted_event(store, seed_review) -> None:
    appended = seed_review()
    with pytest.raises(UnsupportedAssessmentError):
        store.append(appended.assessment, operational_source=RUNTIME)


def test_sequence_is_strictly_increasing_across_types(store, seed_review) -> None:
    seed_review()
    store.append(_decision(), operational_source=RUNTIME)
    store.append(_defect(), operational_source=RUNTIME)
    assert [record.append_sequence for record in store.records] == [1, 2, 3]
    assert store.sequence == 3


# -- F05: schema version is an exact literal --------------------------------


def test_schema_version_defaults_to_exact_literal(review_draft) -> None:
    assert review_draft().assessment_schema_version == ASSESSMENT_SCHEMA_VERSION
    assert _intervention().intervention_schema_version == INTERVENTION_SCHEMA_VERSION


@pytest.mark.parametrize(
    "bad", ["", "v1", "evaluation-assessment.v2", "arbitrary", "EVALUATION-ASSESSMENT.V1"]
)
def test_arbitrary_assessment_schema_version_rejected(review_draft, bad) -> None:
    payload = review_draft().model_dump()
    payload["assessment_schema_version"] = bad
    with pytest.raises(ValidationError):
        ReviewAssessmentEventDraft.model_validate(payload)


@pytest.mark.parametrize("bad", ["", "v1", "evaluation-intervention.v2", "nope"])
def test_arbitrary_intervention_schema_version_rejected(bad) -> None:
    payload = _intervention().model_dump()
    payload["intervention_schema_version"] = bad
    with pytest.raises(ValidationError):
        HumanInterventionEventDraft.model_validate(payload)


# -- F03: exact types, atomicity, duplicate identity ------------------------


def test_subclass_injection_is_refused(store) -> None:
    """A subclass could add fields that change the persisted schema."""

    class SneakyDefect(EscapedDefectEventDraft):
        injected: str = "payload"

    sneaky = SneakyDefect(
        event_id="defect-x",
        run_id="run-1",
        severity=EscapedDefectSeverity.MINOR,
        detected_at=NOW,
        evidence_refs=("e1",),
    )
    with pytest.raises(UnsupportedAssessmentError):
        store.append(sneaky, operational_source=RUNTIME)
    assert len(store) == 0
    assert store.sequence == 0


def test_failed_append_leaves_store_untouched(store, seed_review) -> None:
    """Length, sequence and history must be unchanged after a failure."""

    seed_review()
    before_len, before_seq = len(store), store.sequence
    before_history = [r.assessment.model_dump_json() for r in store.records]

    with pytest.raises(UnsupportedAssessmentError):
        store.append("not-an-event", operational_source=RUNTIME)
    with pytest.raises(UnsupportedAssessmentError):
        store.append(_defect(), operational_source="PRODUCT_RUNTIME")

    assert len(store) == before_len
    assert store.sequence == before_seq
    assert [r.assessment.model_dump_json() for r in store.records] == before_history


def test_no_sequence_burned_by_failed_append(store) -> None:
    store.append(_defect(event_id="d1"), operational_source=RUNTIME)
    with pytest.raises(UnsupportedAssessmentError):
        store.append(object(), operational_source=RUNTIME)
    nxt = store.append(_defect(event_id="d2"), operational_source=RUNTIME)
    assert nxt.append_sequence == 2


def test_duplicate_identical_identity_rejected(store) -> None:
    store.append(_defect(event_id="d1"), operational_source=RUNTIME)
    with pytest.raises(DuplicateAssessmentIdentityError):
        store.append(_defect(event_id="d1"), operational_source=RUNTIME)
    assert len(store) == 1
    assert store.sequence == 1


def test_duplicate_conflicting_identity_rejected(store) -> None:
    store.append(_defect(event_id="d1", severity=EscapedDefectSeverity.MAJOR),
                 operational_source=RUNTIME)
    with pytest.raises(DuplicateAssessmentIdentityError):
        store.append(
            _defect(event_id="d1", severity=EscapedDefectSeverity.BLOCKING),
            operational_source=RUNTIME,
        )
    # Original is not overwritten.
    assert len(store) == 1
    assert store.records[0].assessment.severity is EscapedDefectSeverity.MAJOR


def test_same_identity_in_different_sources_is_not_a_duplicate(store) -> None:
    """Identity is source-qualified: two sources may reuse an id (F01)."""

    store.append(_defect(event_id="d1"), operational_source=RUNTIME)
    store.append(_defect(event_id="d1"), operational_source=DOGFOOD)
    assert len(store) == 2


# -- F04: report-bound canonical append fails closed ------------------------


@pytest.mark.parametrize("source", [RUNTIME, DOGFOOD])
def test_review_canonical_append_is_unavailable(store, review_draft, source) -> None:
    """No bridge exists, so §4-bound publication is refused for every source."""

    with pytest.raises(ReportBoundAppendUnavailableError):
        store.append(review_draft(), operational_source=source)
    assert len(store) == 0
    assert store.sequence == 0


@pytest.mark.parametrize("source", [RUNTIME, DOGFOOD])
def test_owner_canonical_append_is_unavailable(store, owner_draft, source) -> None:
    with pytest.raises(ReportBoundAppendUnavailableError):
        store.append(owner_draft(), operational_source=source)
    assert len(store) == 0
    assert store.sequence == 0


def test_fabricated_report_identity_is_refused(store, review_draft) -> None:
    """Syntactically valid but fabricated ids must not append."""

    fabricated = review_draft(
        assessment_id="fabricated",
        report_id="report-that-never-existed",
        report_hash="f" * 64,
    )
    with pytest.raises(ReportBoundAppendUnavailableError):
        store.append(fabricated, operational_source=RUNTIME)
    assert len(store) == 0


class _PlainBinding:
    """An arbitrary caller object claiming to be authority."""

    operational_source = RUNTIME
    run_id = "run-1"
    report_id = "report-1"
    report_hash = "a" * 64
    issued_by = "tomtit-runtime"


class _DuckTypedBinding(_PlainBinding):
    """Structurally complete: it even implements ``target_key``."""

    def target_key(self):
        return (
            "PRODUCT_RUNTIME",
            "run-1",
            "report-1",
            "a" * 64,
            "GIT_COMMIT",
            ACTIVE_COMMIT,
        )


@pytest.mark.parametrize(
    "attested",
    [
        None,
        object(),
        _PlainBinding(),
        _DuckTypedBinding(),
        {
            "operational_source": "PRODUCT_RUNTIME",
            "run_id": "run-1",
            "report_id": "report-1",
            "report_hash": "a" * 64,
            "issued_by": "tomtit-runtime",
        },
        "tomtit-runtime",
    ],
    ids=["none", "bare-object", "plain", "duck-typed", "mapping", "issuer-string"],
)
def test_no_caller_attestation_can_authorize_an_append(
    store, review_draft, attested
) -> None:
    """F04: self-attestation is not authority, in any shape.

    There is no keyword to pass it through, and even if one is invented by a
    caller the append still fails and the store still does not move.
    """

    with pytest.raises((ReportBoundAppendUnavailableError, TypeError)):
        store.append(
            review_draft(),
            operational_source=RUNTIME,
            trusted_binding=attested,  # type: ignore[call-arg]
        )
    assert len(store) == 0
    assert store.sequence == 0


def test_append_signature_accepts_no_binding_parameter() -> None:
    """The old self-attested parameter is gone, not merely ignored."""

    import inspect

    params = inspect.signature(EvaluationAssessmentStore.append).parameters
    assert set(params) == {"self", "draft", "operational_source"}
    for forbidden in ("trusted_binding", "binding", "attestation", "issued_by"):
        assert forbidden not in params


def test_trusted_binding_type_is_not_exported() -> None:
    """F04: the self-attested binding shape is no longer public surface."""

    import tomtit.evaluation as package
    import tomtit.evaluation.store as store_module

    for name in (
        "TrustedReportBinding",
        "MissingTrustedBindingError",
        "TrustedBindingMismatchError",
    ):
        assert name not in package.__all__, name
        assert not hasattr(package, name), name
        assert not hasattr(store_module, name), name


def test_store_is_unchanged_after_every_rejected_report_append(
    store, review_draft, owner_draft
) -> None:
    """F03 preserved under F04: a refused append burns no sequence."""

    store.append(_defect(event_id="d1"), operational_source=RUNTIME)
    before = [r.assessment.model_dump_json() for r in store.records]

    for draft in (review_draft(), owner_draft()):
        with pytest.raises(ReportBoundAppendUnavailableError):
            store.append(draft, operational_source=RUNTIME)

    assert len(store) == 1
    assert store.sequence == 1
    assert [r.assessment.model_dump_json() for r in store.records] == before
    assert store.append(_defect(event_id="d2"), operational_source=RUNTIME).append_sequence == 2


def test_report_bound_models_remain_valid_data_contract_shapes(review_draft) -> None:
    """Publication is blocked; the V1 draft model itself still validates."""

    draft = review_draft()
    assert draft.review_outcome is ReviewOutcome.REVIEWER_APPROVED
    assert draft.assessment_schema_version == ASSESSMENT_SCHEMA_VERSION


def test_production_package_ships_no_test_seam() -> None:
    """Seeding is test scaffolding, so it must not exist in production source.

    A shipped bypass would be a second publication path inside the package —
    importable by any caller — regardless of how it is named or documented.
    The helper lives in ``tests/evaluation/_seed.py`` instead, so the installed
    package exposes exactly one append path.
    """

    import importlib
    import pkgutil

    import tomtit.evaluation as package

    module_names = {name for _, name, _ in pkgutil.iter_modules(package.__path__)}
    assert "_test_seam" not in module_names
    assert not any("seam" in name for name in module_names), module_names
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("tomtit.evaluation._test_seam")

    for module_name in sorted(module_names) + [""]:
        module = (
            package
            if not module_name
            else importlib.import_module(f"tomtit.evaluation.{module_name}")
        )
        seeders = [
            name
            for name in dir(module)
            if "seed" in name.lower() and callable(getattr(module, name, None))
        ]
        assert seeders == [], (module_name, seeders)

    namespace: dict[str, object] = {}
    exec("from tomtit.evaluation import *", namespace)  # noqa: S102
    assert "_test_seam" not in namespace


def test_seeding_helper_is_confined_to_the_test_tree() -> None:
    """The helper the fixtures use is imported from ``tests``, not the package."""

    from ._seed import seed_report_bound_assessment

    assert seed_report_bound_assessment.__module__.startswith("tests.") or (
        "tomtit" not in seed_report_bound_assessment.__module__
    )


def test_seeded_report_bound_history_still_gets_a_store_sequence(seed_review) -> None:
    envelope = seed_review()
    assert envelope.assessment.append_sequence == 1


def test_non_report_bound_events_append_normally(store) -> None:
    """Decision, intervention and defect events are not report-bound."""

    assert store.append(_decision(), operational_source=RUNTIME).append_sequence == 1
    assert store.append(_intervention(), operational_source=RUNTIME).append_sequence == 2
    assert store.append(_defect(), operational_source=RUNTIME).append_sequence == 3


def test_no_public_api_manufactures_report_authority() -> None:
    """No exported callable returns anything that could stand in for a binding."""

    import inspect

    import tomtit.evaluation as package

    for name in package.__all__:
        obj = getattr(package, name)
        if not callable(obj) or inspect.isclass(obj):
            continue
        annotation = str(inspect.signature(obj).return_annotation)
        assert "Binding" not in annotation, name
        assert "Trusted" not in annotation, name


# -- immutability and isolation ---------------------------------------------


def test_appended_record_is_immutable(seed_review) -> None:
    envelope = seed_review()
    with pytest.raises(ValidationError):
        envelope.assessment.append_sequence = 42
    with pytest.raises(ValidationError):
        envelope.assessment.review_outcome = ReviewOutcome.REVIEWER_BLOCKED


def test_records_are_detached_snapshots(seed_review, store) -> None:
    seed_review()
    first, second = store.records[0], store.records[0]
    assert first == second
    assert first is not second


def test_append_does_not_modify_earlier_records(store, seed_review) -> None:
    seed_review(assessment_id="a1")
    snapshot = store.records[0].assessment.model_dump_json()
    seed_review(assessment_id="a2", report_id="report-2")
    assert store.records[0].assessment.model_dump_json() == snapshot


def test_streams_are_named_separately(seed_review) -> None:
    assert EVALUATION_ASSESSMENT_STREAM != RUNTIME_EXECUTION_STREAM
    seed_review()


def test_evaluation_sequence_independent_of_runtime(store, seed_review) -> None:
    from tomtit.runtime.events import InMemoryEventLog

    runtime_log = InMemoryEventLog()
    envelope = seed_review()
    assert envelope.append_sequence == 1
    assert len(runtime_log.decisions) == 0
    assert len(runtime_log.events) == 0


def test_two_stores_have_independent_sequences(review_draft) -> None:
    a, b = EvaluationAssessmentStore(), EvaluationAssessmentStore()
    a.append(_defect(event_id="d1"), operational_source=RUNTIME)
    a.append(_defect(event_id="d2"), operational_source=RUNTIME)
    assert b.append(_defect(event_id="d1"), operational_source=RUNTIME).append_sequence == 1
    assert a.sequence == 2


def test_records_of_type_filters_by_source(store) -> None:
    store.append(_defect(event_id="d1"), operational_source=RUNTIME)
    store.append(_defect(event_id="d2"), operational_source=DOGFOOD)
    assert len(store.records_of_type(EscapedDefectEventDraft)) == 2
    assert (
        len(store.records_of_type(EscapedDefectEventDraft, operational_source=RUNTIME))
        == 1
    )
    assert (
        len(store.records_of_type(EscapedDefectEventDraft, operational_source=DOGFOOD))
        == 1
    )


def test_owner_and_review_use_distinct_type_buckets(
    store, seed_review, seed_owner
) -> None:
    seed_review()
    seed_owner()
    assert len(store.records_of_type(ReviewAssessmentEventDraft)) == 1
    assert len(store.records_of_type(OwnerAssessmentEventDraft)) == 1
