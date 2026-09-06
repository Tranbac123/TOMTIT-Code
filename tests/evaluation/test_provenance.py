"""SourceRevision and operational-source provenance (Contract V1 §5)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tomtit.evaluation import (
    InactiveSourceRevisionError,
    OperationalSource,
    RevisionStatus,
    RevisionType,
    SourceRevision,
    require_active_source_revision,
    revision_type_status,
)

from .conftest import ACTIVE_COMMIT


def test_active_git_commit_revision_is_valid_and_active() -> None:
    revision = SourceRevision(
        revision_type=RevisionType.GIT_COMMIT, revision_value=ACTIVE_COMMIT
    )
    assert revision.status is RevisionStatus.ACTIVE
    assert revision.is_active
    assert require_active_source_revision(revision) is revision


def test_reserved_revision_types_are_constructible_but_not_active() -> None:
    """§5.2 reserves these types; they are not invalid, just not bindable."""

    for revision_type in (
        RevisionType.PATCH_SHA256,
        RevisionType.WORKTREE_SNAPSHOT_SHA256,
    ):
        revision = SourceRevision(
            revision_type=revision_type, revision_value="c" * 64
        )
        assert revision.status is RevisionStatus.RESERVED_NOT_ACTIVE
        assert not revision.is_active


def test_reserved_revision_cannot_bind_an_assessment() -> None:
    """§5.3: reserved types must not bind until a canonicalization contract exists."""

    revision = SourceRevision(
        revision_type=RevisionType.PATCH_SHA256, revision_value="c" * 64
    )
    with pytest.raises(InactiveSourceRevisionError):
        require_active_source_revision(revision)


def test_review_assessment_rejects_reserved_revision(review_draft) -> None:
    reserved = SourceRevision(
        revision_type=RevisionType.WORKTREE_SNAPSHOT_SHA256,
        revision_value="d" * 64,
    )
    with pytest.raises(ValidationError):
        review_draft(revision=reserved)


@pytest.mark.parametrize(
    "value",
    [
        "cdb6d360",  # abbreviated
        ACTIVE_COMMIT.upper(),  # uppercase
        "z" * 40,  # non-hex
        "a" * 64,  # sha256-shaped, not a commit
    ],
)
def test_malformed_git_commit_revision_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        SourceRevision(revision_type=RevisionType.GIT_COMMIT, revision_value=value)


def test_bare_string_is_not_a_source_revision() -> None:
    """A bare commit string cannot stand in for the typed contract concept."""

    with pytest.raises(ValidationError):
        SourceRevision.model_validate(ACTIVE_COMMIT)


def test_revision_requires_explicit_type() -> None:
    with pytest.raises(ValidationError):
        SourceRevision.model_validate({"revision_value": ACTIVE_COMMIT})


def test_revision_type_status_table_matches_contract() -> None:
    assert revision_type_status(RevisionType.GIT_COMMIT) is RevisionStatus.ACTIVE
    assert (
        revision_type_status(RevisionType.PATCH_SHA256)
        is RevisionStatus.RESERVED_NOT_ACTIVE
    )
    assert (
        revision_type_status(RevisionType.WORKTREE_SNAPSHOT_SHA256)
        is RevisionStatus.RESERVED_NOT_ACTIVE
    )


# -- operational source (R2.2 requirement 2) --------------------------------


def test_product_runtime_is_distinguishable_from_dogfood() -> None:
    assert OperationalSource.PRODUCT_RUNTIME is not OperationalSource.DOGFOOD_ORCHESTRATOR
    assert {
        OperationalSource.PRODUCT_RUNTIME,
        OperationalSource.DOGFOOD_ORCHESTRATOR,
    } == set(OperationalSource)


def test_operational_source_is_not_a_contract_v1_event_field(review_draft) -> None:
    """F01: source identity belongs on the envelope, not inside the V1 event."""

    from tomtit.evaluation import (
        DecisionAssessmentEventDraft,
        EscapedDefectEventDraft,
        HumanInterventionEventDraft,
        OwnerAssessmentEventDraft,
        ReviewAssessmentEventDraft,
    )

    for cls in (
        ReviewAssessmentEventDraft,
        OwnerAssessmentEventDraft,
        DecisionAssessmentEventDraft,
        HumanInterventionEventDraft,
        EscapedDefectEventDraft,
    ):
        assert "operational_source" not in cls.model_fields, cls.__name__


def test_operational_source_and_source_revision_are_independent_axes(
    seed_review, active_revision
) -> None:
    """The same revision can be observed from either source, and vice versa."""

    from_runtime = seed_review(
        assessment_id="a-runtime", source=OperationalSource.PRODUCT_RUNTIME
    )
    from_dogfood = seed_review(
        assessment_id="a-dogfood", source=OperationalSource.DOGFOOD_ORCHESTRATOR
    )

    # Same source revision, different operational source: neither is derivable
    # from the other.
    assert (
        from_runtime.assessment.target_source_revision
        == from_dogfood.assessment.target_source_revision
    )
    assert from_runtime.operational_source is not from_dogfood.operational_source
    assert from_runtime.assessment.target_source_revision == active_revision


def test_operational_source_is_required_on_append(store, review_draft) -> None:
    """Source identity is never inferred; the store demands it explicitly."""

    from tomtit.evaluation import UnsupportedAssessmentError

    with pytest.raises(TypeError):
        store.append(review_draft())  # no operational_source keyword
    with pytest.raises(UnsupportedAssessmentError):
        store.append(review_draft(), operational_source="PRODUCT_RUNTIME")
