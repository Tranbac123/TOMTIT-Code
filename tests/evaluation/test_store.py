"""Store authority, ordering, atomicity, duplicates and trust boundary.

Covers Contract V1 §2.3 and §8, plus repair findings F02, F03, F04, F05.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

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
    OwnerAssessmentEvent,
    OwnerAssessmentEventDraft,
    OwnerOutcome,
    ReportBoundAppendUnavailableError,
    ReviewAssessmentEventDraft,
    ReviewOutcome,
    RevisionType,
    SourceRevision,
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


def test_persisted_event_contains_append_sequence(store) -> None:
    envelope = store.append(_decision(), operational_source=RUNTIME)
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


def test_caller_cannot_forge_sequence_via_persisted_event(store) -> None:
    appended = store.append(_decision(), operational_source=RUNTIME)
    with pytest.raises(UnsupportedAssessmentError):
        store.append(appended.assessment, operational_source=RUNTIME)


def test_sequence_is_strictly_increasing_across_types(store) -> None:
    store.append(_decision(), operational_source=RUNTIME)
    store.append(_intervention(), operational_source=RUNTIME)
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


def test_failed_append_leaves_store_untouched(store) -> None:
    """Length, sequence and history must be unchanged after a failure."""

    store.append(_decision(), operational_source=RUNTIME)
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


def test_append_takes_an_opaque_receipt_and_no_self_attested_parameter() -> None:
    """EVAL-RUNTIME-BRIDGE-01: the only evidence input is an opaque receipt id.

    The rejected EVAL-01 shape — a caller-populated attestation with an issuer
    of its choosing — must stay gone, and so must the binding object that
    replaced it: an object carrying its own ``verify()`` puts caller code in the
    trust path. What is left is a string the store can only look up.
    """

    import inspect

    params = inspect.signature(EvaluationAssessmentStore.append).parameters
    assert set(params) == {
        "self",
        "draft",
        "operational_source",
        "publication_receipt_id",
    }
    for forbidden in (
        "trusted_binding",
        "report_binding",
        "attestation",
        "issued_by",
        "issuer",
    ):
        assert forbidden not in params

    assert str(params["publication_receipt_id"].annotation) == "str | None"
    assert params["publication_receipt_id"].default is None


def test_trusted_binding_type_is_not_exported() -> None:
    """F04: the self-attested binding shape is no longer public surface."""

    import tomtit.evaluation as package
    import tomtit.evaluation.store as store_module

    for name in (
        "TrustedReportBinding",
        "MissingTrustedBindingError",
        "TrustedBindingMismatchError",
        "RuntimeReportBinding",
        "load_terminal_report_binding",
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


def test_production_package_ships_no_publication_bypass() -> None:
    """F04/AUTHORITY: no shipped callable can publish a report-bound event.

    Behavioral, not name-based: every callable reachable on the store class and
    on each package module is invoked with a fabricated Owner assessment and a
    real store, and none of them may leave a record behind. A rename of the old
    ``_publish`` would fail this test exactly as the original did.
    """

    import importlib
    import inspect
    import pkgutil

    import tomtit.evaluation as package

    draft = OwnerAssessmentEventDraft(
        assessment_id="forged-owner",
        run_id="run-1",
        target_terminal_report_id="report-1",
        target_terminal_report_hash="a" * 64,
        target_source_revision=SourceRevision(
            revision_type=RevisionType.GIT_COMMIT, revision_value="c" * 40
        ),
        owner_outcome=OwnerOutcome.OWNER_ACCEPTED,
        timestamp=NOW,
        evidence_refs=("evidence:forged",),
    )
    persisted = OwnerAssessmentEvent(**draft.model_dump(), append_sequence=1)

    def attempt(fn, target_store) -> None:
        """Call `fn` with every plausible publication argument shape."""

        argument_sets = (
            (draft,),
            (draft, OwnerAssessmentEventDraft),
            (draft, OwnerAssessmentEventDraft, RUNTIME),
            (persisted,),
            (target_store, draft),
            (target_store, draft, RUNTIME),
        )
        keyword_sets = ({}, {"operational_source": RUNTIME})
        for args in argument_sets:
            for kwargs in keyword_sets:
                try:
                    fn(*args, **kwargs)
                except Exception:  # noqa: BLE001 - refusal of any kind is the point
                    pass

    modules = [package] + [
        importlib.import_module(f"tomtit.evaluation.{name}")
        for _, name, _ in pkgutil.iter_modules(package.__path__)
    ]

    for module in modules:
        for _, obj in inspect.getmembers(module, callable):
            if getattr(obj, "__module__", "").startswith("tomtit.evaluation") is False:
                continue
            probe_store = EvaluationAssessmentStore()
            attempt(obj, probe_store)
            assert len(probe_store) == 0
            assert probe_store.sequence == 0

    probe_store = EvaluationAssessmentStore()
    for _, method in inspect.getmembers(EvaluationAssessmentStore, callable):
        attempt(method, probe_store)
        try:
            attempt(getattr(probe_store, method.__name__), probe_store)
        except AttributeError:
            pass
    assert len(probe_store) == 0
    assert probe_store.sequence == 0
    assert probe_store.records == ()


def test_store_class_exposes_no_inner_write_helper() -> None:
    """The old ``_publish``/``_validated_draft_type`` write path is gone.

    Structural, and deliberately supplementary to the behavioral probe above:
    the store's whole callable surface is one gated append plus reads.
    """

    import inspect

    callables = {
        name
        for name, value in inspect.getmembers(EvaluationAssessmentStore)
        if not name.startswith("__") and callable(value)
    }
    assert callables == {"append", "records_of_type"}
    assert not hasattr(EvaluationAssessmentStore, "_publish")
    assert not hasattr(EvaluationAssessmentStore, "_validated_draft_type")


#: Names that mention publication but only *read* it. The bridge has to say the
#: word to verify a Runtime publication; what must not exist is anything in
#: Evaluation that performs one, which the next test pins behaviorally.
_READ_ONLY_PUBLICATION_NAMES = frozenset(
    {
        "verify_published_report",
        "PublishedReportVerificationError",
        "PublishedReportChangedError",
    }
)


def test_read_only_publication_names_cannot_write_anything() -> None:
    """The allowlisted names verify; they must not reach a write helper.

    Checked against compiled code rather than source text, so a name that
    appears only in prose does not count and one the code truly reaches does.
    """

    from tomtit.evaluation import bridge

    forbidden = {
        "record_publication",
        "_allocate_report_id",
        "publish_staged_report",
        "stage_terminal_report",
        "write_terminal_report",
        "write_rendered_terminal_report",
        "render_terminal_report",
        "append",
    }
    executed = _reachable_code_names(bridge.verify_published_report)
    assert executed & forbidden == set(), executed & forbidden


#: The attributes that *are* the store's canonical state. Anything that can
#: assign to one, or call a mutator on one, is a write path by definition —
#: whatever it happens to be named.
_CANONICAL_STATE = frozenset({"_serialized", "_sequence", "_identities"})


def _state_mutating_functions(tree, filename: str) -> list[tuple[str, str, int]]:
    """Every function in a module whose body mutates canonical store state."""

    import ast

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            targets = []
            if isinstance(sub, ast.Assign):
                targets = sub.targets
            elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
                targets = [sub.target]
            hit = any(
                isinstance(t, ast.Attribute) and t.attr in _CANONICAL_STATE
                for t in targets
            )
            if not hit and isinstance(sub, ast.Call):
                func = sub.func
                hit = (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr in _CANONICAL_STATE
                )
            if hit:
                found.append((filename, node.name, node.lineno))
                break
    return found


def test_exactly_one_production_callable_can_write_canonical_state() -> None:
    """COMBINED-F01: no second write path, under any name.

    The first EVAL-01 commit shipped ``EvaluationAssessmentStore._publish()``
    below the report-bound gate, and a committed test helper called it directly
    to place history the gate forbade. This asserts the shape that made that
    possible cannot come back: across the whole production package, the only
    callables that touch canonical store state are ``__init__`` (which creates
    it empty) and ``append`` (which is the gate).
    """

    import ast

    package = Path(EvaluationAssessmentStore.__module__.replace(".", "/")).parent
    root = Path(__file__).resolve().parents[2] / "src" / package

    mutators = []
    for path in sorted(root.glob("*.py")):
        mutators += _state_mutating_functions(
            ast.parse(path.read_text(encoding="utf-8")), path.name
        )

    assert [(f, n) for f, n, _ in mutators] == [
        ("store.py", "__init__"),
        ("store.py", "append"),
    ], mutators


def test_authority_gate_runs_before_any_state_mutation() -> None:
    """A gate that runs after the write is not a gate."""

    import ast

    source = Path(__file__).resolve().parents[2] / "src" / "tomtit" / "evaluation" / "store.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    append = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "append"
    )

    gate_line = min(
        sub.lineno
        for sub in ast.walk(append)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Name)
        and sub.func.id == "_require_verified_publication"
    )
    mutation_lines = [
        sub.lineno
        for sub in ast.walk(append)
        for t in (
            sub.targets
            if isinstance(sub, ast.Assign)
            else [sub.target]
            if isinstance(sub, (ast.AugAssign, ast.AnnAssign))
            else []
        )
        if isinstance(t, ast.Attribute) and t.attr in _CANONICAL_STATE
    ]
    assert mutation_lines, "expected append to mutate state at all"
    assert gate_line < min(mutation_lines), (gate_line, mutation_lines)


def test_no_publish_style_bypass_attribute_survives() -> None:
    """The exact committed defect, pinned by name as well as by shape."""

    import tomtit.evaluation as package
    import tomtit.evaluation.store as store_module

    for name in ("_publish", "_validated_draft_type", "seed_report_bound_assessment"):
        assert not hasattr(EvaluationAssessmentStore, name), name
        assert not hasattr(store_module, name), name
        assert not hasattr(package, name), name

    # And the committed test helper that used it is gone from the tree.
    assert not (Path(__file__).parent / "_seed.py").exists()


def test_test_history_never_mutates_production_state(review_draft) -> None:
    """The double builds history in its own lists, never in a real store's."""

    from ._history import TrustedHistoryStore, production_store

    real = production_store()
    baseline = (len(real), real.sequence, real.records)

    double = TrustedHistoryStore()
    for index in range(3):
        double.seed_trusted_report_bound(
            review_draft(assessment_id=f"seeded-{index}"),
            operational_source=RUNTIME,
        )

    assert len(double) == 3 and double.sequence == 3
    assert (len(real), real.sequence, real.records) == baseline
    # The double's state objects are its own, not shared with any real store.
    for attribute in _CANONICAL_STATE:
        assert getattr(double, attribute) is not getattr(real, attribute)


def test_production_package_ships_no_test_seam_or_seed_api() -> None:
    """No seam module, no seed API, no hidden publication helper."""

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
        suspicious = [
            name
            for name in dir(module)
            if callable(getattr(module, name, None))
            and any(word in name.lower() for word in ("seed", "seam", "publish"))
            and name not in _READ_ONLY_PUBLICATION_NAMES
        ]
        assert suspicious == [], (module_name, suspicious)

    namespace: dict[str, object] = {}
    exec("from tomtit.evaluation import *", namespace)  # noqa: S102
    assert "_test_seam" not in namespace


def _reachable_code_names(obj: object) -> set[str]:
    """Globals and attribute names one callable's compiled code can execute."""

    names: set[str] = set()
    for code in _reachable_code(obj):
        names.update(code.co_names)
    return names


def _reachable_code(obj: object) -> list[object]:
    """Every code object a callable can execute, including nested ones."""

    import types

    seen: list[object] = []
    pending = [obj]
    while pending:
        current = pending.pop()
        code = getattr(current, "__code__", None)
        if code is None and isinstance(current, types.CodeType):
            code = current
        if code is None or code in seen:
            continue
        seen.append(code)
        pending.extend(
            const for const in code.co_consts if isinstance(const, types.CodeType)
        )
    return seen


def _names_referenced_by(cls: type) -> set[str]:
    """Globals and attribute names the class's methods actually execute.

    Compiled code, not source text: a name that appears only in a docstring or
    a comment is not here, and a name the code genuinely reaches is, however it
    was spelled. That is the difference between asserting the isolation
    property and grepping for a word.
    """

    names: set[str] = set()
    for attribute in vars(cls).values():
        target = getattr(attribute, "__func__", attribute)
        target = getattr(target, "fget", target)
        names.update(_reachable_code_names(target))
    return names


def test_test_history_cannot_reach_the_production_write_path(review_draft) -> None:
    """F10-D: the fixture is isolated from the production store, behaviorally.

    Three independent properties, none of which a docstring can trip:

    1. it is not a subclass, so it inherits no write path;
    2. its executable code names no private member of the production store or
       its module, so there is no hidden call into one;
    3. seeding it leaves a real store untouched, so no shared state connects
       them.
    """

    from tomtit.evaluation import store as store_module

    from ._history import TrustedHistoryStore

    assert not issubclass(TrustedHistoryStore, EvaluationAssessmentStore)
    assert not TrustedHistoryStore.__module__.startswith("tomtit")
    assert (
        Path(inspect.getfile(TrustedHistoryStore)).resolve().parents[1].name == "tests"
    )

    from . import _history as history_module

    production_privates = {
        name
        for name in list(vars(EvaluationAssessmentStore)) + list(vars(store_module))
        if name.startswith("_") and not name.startswith("__")
    }
    assert production_privates, "expected the store to have private members to guard"
    # A private name the double defines for itself — as a module global or as
    # its own method — is its own. Only a private name it references without
    # defining anywhere could be reaching into production.
    borrowed = (
        production_privates
        - set(vars(history_module))
        - set(vars(TrustedHistoryStore))
    )
    assert borrowed, "expected some production private the double does not define"
    executed = _names_referenced_by(TrustedHistoryStore)
    assert executed & borrowed == set(), executed & borrowed

    # Nor does it import one under any name: anything it holds that the store
    # module defines must be public API, never an internal.
    import tomtit.evaluation as package

    for name, value in vars(history_module).items():
        if name.startswith("__"):
            continue
        if getattr(value, "__module__", "") == store_module.__name__:
            assert getattr(value, "__name__", name) in package.__all__, name

    # The double holds only its own state: nothing it does reaches a real store.
    real = EvaluationAssessmentStore()
    double = TrustedHistoryStore()
    double.seed_trusted_report_bound(review_draft(), operational_source=RUNTIME)
    assert len(double) == 1
    assert len(real) == 0 and real.sequence == 0 and real.records == ()

    # And the production gate is unchanged by the double existing at all.
    with pytest.raises(ReportBoundAppendUnavailableError):
        real.append(review_draft(), operational_source=RUNTIME)
    assert len(real) == 0 and real.sequence == 0


def test_trusted_history_matches_production_store_semantics() -> None:
    """The double is only trustworthy where it agrees with the real store."""

    from ._history import TrustedHistoryStore

    real, double = EvaluationAssessmentStore(), TrustedHistoryStore()
    for target in (real, double):
        assert target.append(_decision(), operational_source=RUNTIME).append_sequence == 1
        assert target.append(_defect(), operational_source=RUNTIME).append_sequence == 2
        assert target.sequence == 2
        assert len(target) == 2
        assert len(target.records_of_type(EscapedDefectEventDraft)) == 1
        with pytest.raises(DuplicateAssessmentIdentityError):
            target.append(_defect(), operational_source=RUNTIME)
        with pytest.raises(UnsupportedAssessmentError):
            target.append("not-an-event", operational_source=RUNTIME)
        with pytest.raises(ReportBoundAppendUnavailableError):
            target.append(
                OwnerAssessmentEventDraft(
                    assessment_id="a1",
                    run_id="run-1",
                    target_terminal_report_id="report-1",
                    target_terminal_report_hash="a" * 64,
                    target_source_revision=SourceRevision(
                        revision_type=RevisionType.GIT_COMMIT,
                        revision_value="c" * 40,
                    ),
                    owner_outcome=OwnerOutcome.OWNER_ACCEPTED,
                    timestamp=NOW,
                    evidence_refs=("evidence:a1",),
                ),
                operational_source=RUNTIME,
            )
        assert target.sequence == 2
        assert len(target) == 2

    assert [r.assessment.model_dump_json() for r in real.records] == [
        r.assessment.model_dump_json() for r in double.records
    ]


def test_seeded_report_bound_history_carries_a_store_shaped_sequence(
    seed_review,
) -> None:
    """A statement about the test fixture, not about production authority."""

    envelope = seed_review()
    assert envelope.assessment.append_sequence == 1


def test_non_report_bound_events_append_normally(store) -> None:
    """Decision, intervention and defect events are not report-bound."""

    assert store.append(_decision(), operational_source=RUNTIME).append_sequence == 1
    assert store.append(_intervention(), operational_source=RUNTIME).append_sequence == 2
    assert store.append(_defect(), operational_source=RUNTIME).append_sequence == 3


def test_only_the_ledger_verifier_yields_verified_facts() -> None:
    """Evaluation still mints no provenance; it only re-proves a publication.

    Exactly one exported callable returns verified report facts, and it cannot
    conjure them: it takes an opaque receipt id and resolves it against
    Runtime's publication ledger. Every other public function remains incapable
    of producing them, so there is no Eval-side factory for Runtime identity.
    """

    import inspect

    import tomtit.evaluation as package

    producers = []
    for name in package.__all__:
        obj = getattr(package, name)
        if not callable(obj) or inspect.isclass(obj):
            continue
        annotation = str(inspect.signature(obj).return_annotation)
        for rejected in ("Trusted", "Binding"):
            assert rejected not in annotation, (name, rejected)
        if "VerifiedTerminalReport" in annotation:
            producers.append(name)

    assert producers == ["verify_published_report"]

    parameters = inspect.signature(package.verify_published_report).parameters
    assert "publication_receipt_id" in parameters
    # No path, no bytes, no facts: a caller cannot describe the report at all.
    for absent in ("report_path", "report_bytes", "report_id", "source_revision"):
        assert absent not in parameters, absent


def test_verified_facts_are_not_accepted_from_a_caller() -> None:
    """Authority is ledger-backed, not class-backed.

    :class:`VerifiedTerminalReport` is an ordinary value object, so constructing
    one asserts nothing — and the store will not take one. The only thing it
    accepts is a string it can look up, which is why holding the type confers
    no authority.
    """

    import inspect

    from tomtit.evaluation import VerifiedTerminalReport

    annotation = inspect.signature(EvaluationAssessmentStore.append).parameters[
        "publication_receipt_id"
    ].annotation
    assert "VerifiedTerminalReport" not in str(annotation)
    assert str(annotation) == "str | None"

    # The type exists for reading verified facts, not for presenting them.
    assert not hasattr(VerifiedTerminalReport, "verify")
    assert "target_key" in dir(VerifiedTerminalReport)


# -- immutability and isolation ---------------------------------------------


def test_appended_record_is_immutable(store) -> None:
    envelope = store.append(_defect(), operational_source=RUNTIME)
    with pytest.raises(ValidationError):
        envelope.assessment.append_sequence = 42
    with pytest.raises(ValidationError):
        envelope.assessment.severity = EscapedDefectSeverity.MINOR


def test_records_are_detached_snapshots(store) -> None:
    store.append(_defect(), operational_source=RUNTIME)
    first, second = store.records[0], store.records[0]
    assert first == second
    assert first is not second


def test_append_does_not_modify_earlier_records(store) -> None:
    store.append(_defect(event_id="d1"), operational_source=RUNTIME)
    snapshot = store.records[0].assessment.model_dump_json()
    store.append(_defect(event_id="d2"), operational_source=RUNTIME)
    assert store.records[0].assessment.model_dump_json() == snapshot


def test_streams_are_named_separately() -> None:
    assert EVALUATION_ASSESSMENT_STREAM != RUNTIME_EXECUTION_STREAM


def test_evaluation_sequence_independent_of_runtime(store) -> None:
    from tomtit.runtime.events import InMemoryEventLog

    runtime_log = InMemoryEventLog()
    envelope = store.append(_defect(), operational_source=RUNTIME)
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
    history, seed_review, seed_owner
) -> None:
    """Type bucketing over report-bound history, which only the fixture holds."""

    seed_review()
    seed_owner()
    assert len(history.records_of_type(ReviewAssessmentEventDraft)) == 1
    assert len(history.records_of_type(OwnerAssessmentEventDraft)) == 1
