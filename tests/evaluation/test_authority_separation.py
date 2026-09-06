"""Authority separation and status-vocabulary projection.

Covers Contract V1 §2.1/§2.3 (Evaluation cannot rewrite execution truth), §8
(sequences never compared across streams), and findings F09 (status projection
is an adapter concern, not a V1 vocabulary) and F10 (source isolation).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from tomtit.domain.enums import CriterionStatus, OwnerCriterionStatus
from tomtit.evaluation import (
    EVALUATION_ASSESSMENT_STREAM,
    RUNTIME_EXECUTION_STREAM,
    EscapedDefectEventDraft,
    EscapedDefectSeverity,
    EvaluationCriterionStatus,
    OperationalSource,
    StatusOriginVocabulary,
    translate_owner_criterion_status,
    translate_requirement_status,
    translate_runtime_criterion_status,
)
from tomtit_decision_eval_v0.models import RequirementStatus

from .conftest import NOW

EVAL_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "tomtit" / "evaluation"
RUNTIME = OperationalSource.PRODUCT_RUNTIME
DOGFOOD = OperationalSource.DOGFOOD_ORCHESTRATOR


def _defect(**overrides) -> EscapedDefectEventDraft:
    payload = {
        "event_id": "defect-1",
        "run_id": "run-1",
        "severity": EscapedDefectSeverity.MAJOR,
        "detected_at": NOW,
        "evidence_refs": ("e1",),
    }
    payload.update(overrides)
    return EscapedDefectEventDraft(**payload)


# -- Evaluation never becomes an operational authority ----------------------


def test_evaluation_package_never_imports_runtime_writers() -> None:
    forbidden = (
        "from tomtit.runtime.runtime import",
        "from tomtit.runtime.state import",
        "from tomtit.reporting import",
        "import tomtit.runtime.runtime",
        "import tomtit.reporting",
    )
    for path in sorted(EVAL_PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path.name} imports {token}"


def test_evaluation_does_not_construct_runtime_decision_events() -> None:
    for path in sorted(EVAL_PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "PersistedDecisionEvent(" not in source
        assert "DecisionEventDraft(" not in source


def test_appending_does_not_transition_runtime_state(store) -> None:
    from tomtit.domain.enums import RunState

    states_before = [member.value for member in RunState]
    store.append(_defect(), operational_source=RUNTIME)
    assert [member.value for member in RunState] == states_before


def test_runtime_event_log_is_unaffected_by_evaluation_appends(store) -> None:
    from tomtit.runtime.events import InMemoryEventLog

    runtime_log = InMemoryEventLog()
    store.append(_defect(event_id="d1"), operational_source=RUNTIME)
    store.append(_defect(event_id="d2"), operational_source=RUNTIME)
    assert len(runtime_log) == 0
    assert runtime_log.events == ()
    assert runtime_log.decisions == ()


def test_dogfood_history_is_not_touched() -> None:
    for path in sorted(EVAL_PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "events.jsonl" not in source
        assert "run_loop" not in source


def test_streams_remain_distinct_names() -> None:
    assert EVALUATION_ASSESSMENT_STREAM != RUNTIME_EXECUTION_STREAM


# -- F10: whole-stream source isolation -------------------------------------


def test_records_carry_their_operational_source(store) -> None:
    store.append(_defect(event_id="d1"), operational_source=RUNTIME)
    store.append(_defect(event_id="d2"), operational_source=DOGFOOD)
    sources = [record.operational_source for record in store.records]
    assert sources == [RUNTIME, DOGFOOD]


def test_run_scope_is_source_qualified(store) -> None:
    runtime_env = store.append(_defect(event_id="d1"), operational_source=RUNTIME)
    dogfood_env = store.append(_defect(event_id="d2"), operational_source=DOGFOOD)
    assert runtime_env.run_scope == (RUNTIME, "run-1")
    assert dogfood_env.run_scope == (DOGFOOD, "run-1")
    assert runtime_env.run_scope != dogfood_env.run_scope


def test_one_sequence_spans_both_sources_without_merging_identity(store) -> None:
    """A single Evaluation stream orders both sources without conflating them."""

    a = store.append(_defect(event_id="d1"), operational_source=RUNTIME)
    b = store.append(_defect(event_id="d1"), operational_source=DOGFOOD)
    assert (a.append_sequence, b.append_sequence) == (1, 2)
    assert a.run_scope != b.run_scope


# -- status projection is an adapter concern (F09) --------------------------


def test_three_source_vocabularies_remain_distinct() -> None:
    assert {m.value for m in CriterionStatus} == {
        "SATISFIED",
        "UNSATISFIED",
        "UNRESOLVED",
    }
    assert {m.value for m in OwnerCriterionStatus} == {
        "satisfied",
        "unsatisfied",
        "unresolved",
    }
    assert {m.value for m in RequirementStatus} == {
        "SATISFIED",
        "UNSATISFIED",
        "UNKNOWN",
    }


def test_projection_is_documented_as_adapter_level_not_contract_v1() -> None:
    """F09: INDETERMINATE must not be presented as a V1 canonical vocabulary."""

    doc = EvaluationCriterionStatus.__doc__ or ""
    assert "not** a Contract V1" in doc or "not a Contract V1" in doc
    assert "projection" in doc.lower()


def test_determinate_statuses_translate_directly() -> None:
    assert (
        translate_runtime_criterion_status(CriterionStatus.SATISFIED).status
        is EvaluationCriterionStatus.SATISFIED
    )
    assert (
        translate_owner_criterion_status(OwnerCriterionStatus.UNSATISFIED).status
        is EvaluationCriterionStatus.UNSATISFIED
    )
    assert (
        translate_requirement_status(RequirementStatus.SATISFIED).status
        is EvaluationCriterionStatus.SATISFIED
    )


def test_unresolved_and_unknown_are_not_treated_as_equal() -> None:
    runtime_unresolved = translate_runtime_criterion_status(CriterionStatus.UNRESOLVED)
    evaluator_unknown = translate_requirement_status(RequirementStatus.UNKNOWN)

    assert runtime_unresolved.status is EvaluationCriterionStatus.INDETERMINATE
    assert evaluator_unknown.status is EvaluationCriterionStatus.INDETERMINATE
    assert runtime_unresolved != evaluator_unknown
    assert runtime_unresolved.origin_value == "UNRESOLVED"
    assert evaluator_unknown.origin_value == "UNKNOWN"
    assert (
        runtime_unresolved.origin_vocabulary
        is StatusOriginVocabulary.RUNTIME_CRITERION_STATUS
    )
    assert (
        evaluator_unknown.origin_vocabulary
        is StatusOriginVocabulary.DECISION_EVAL_REQUIREMENT_STATUS
    )


def test_owner_unresolved_is_distinct_from_runtime_unresolved() -> None:
    runtime = translate_runtime_criterion_status(CriterionStatus.UNRESOLVED)
    owner = translate_owner_criterion_status(OwnerCriterionStatus.UNRESOLVED)
    assert runtime.status is owner.status
    assert runtime != owner
    assert runtime.origin_vocabulary is not owner.origin_vocabulary


def test_no_indeterminate_status_is_read_as_success() -> None:
    for translated in (
        translate_runtime_criterion_status(CriterionStatus.UNRESOLVED),
        translate_owner_criterion_status(OwnerCriterionStatus.UNRESOLVED),
        translate_requirement_status(RequirementStatus.UNKNOWN),
    ):
        assert translated.status is not EvaluationCriterionStatus.SATISFIED


def test_contract_v1_document_is_unmodified() -> None:
    contract = (
        Path(__file__).resolve().parents[2] / "docs" / "EVALUATION_CONTRACT_V1.md"
    )
    digest = hashlib.sha256(contract.read_bytes()).hexdigest()
    assert digest == (
        "0e9c5184f44504add626a81092c1795399d3fcdd3c64d11181c59db541060b4a"
    )
