"""Review and Owner assessment binding and precedence (Contract V1 §9, §10, §11)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tomtit.evaluation import (
    CrossSourceSelectionError,
    OperationalSource,
    OwnerOutcome,
    OwnerReviewStatus,
    ReviewAssessmentEventDraft,
    ReviewOutcome,
    effective_assessment,
    owner_review_status,
    report_target_key,
)

from .conftest import ACTIVE_COMMIT, NOW, OTHER_REPORT_HASH, REPORT_HASH

RUNTIME = OperationalSource.PRODUCT_RUNTIME
DOGFOOD = OperationalSource.DOGFOOD_ORCHESTRATOR


def test_review_assessment_carries_every_v1_binding_field() -> None:
    fields = ReviewAssessmentEventDraft.model_fields
    for field in (
        "assessment_id",
        "assessment_schema_version",
        "run_id",
        "target_terminal_report_id",
        "target_terminal_report_hash",
        "target_source_revision",
        "review_outcome",
        "reviewer",
        "timestamp",
        "evidence_refs",
    ):
        assert field in fields, field


@pytest.mark.parametrize("missing", ["run_id", "target_terminal_report_id", "reviewer"])
def test_missing_required_binding_is_rejected(review_draft, missing) -> None:
    payload = review_draft().model_dump()
    payload.pop(missing)
    with pytest.raises(ValidationError):
        ReviewAssessmentEventDraft.model_validate(payload)


@pytest.mark.parametrize("bad_hash", ["abc", "A" * 64, "a" * 63, "g" * 64, ""])
def test_report_hash_must_be_exact_sha256(review_draft, bad_hash) -> None:
    with pytest.raises(ValidationError):
        review_draft(report_hash=bad_hash)


def test_all_four_review_outcomes_are_accepted(review_draft) -> None:
    for outcome in ReviewOutcome:
        assert review_draft(outcome=outcome).review_outcome is outcome


def test_unknown_review_outcome_is_rejected(review_draft) -> None:
    with pytest.raises(ValidationError):
        review_draft(outcome="REVIEWER_MAYBE")


def test_target_key_is_source_qualified(review_draft) -> None:
    """F01: identical report identity from two sources must not collide."""

    draft = review_draft()
    runtime_key = report_target_key(RUNTIME, draft)
    dogfood_key = report_target_key(DOGFOOD, draft)

    assert runtime_key != dogfood_key
    assert runtime_key == (
        "PRODUCT_RUNTIME",
        "run-1",
        "report-1",
        REPORT_HASH,
        "GIT_COMMIT",
        ACTIVE_COMMIT,
    )


def test_duplicate_evidence_refs_rejected(active_revision) -> None:
    with pytest.raises(ValidationError):
        ReviewAssessmentEventDraft(
            assessment_id="a1",
            run_id="run-1",
            target_terminal_report_id="report-1",
            target_terminal_report_hash=REPORT_HASH,
            target_source_revision=active_revision,
            review_outcome=ReviewOutcome.REVIEWER_APPROVED,
            reviewer="codex",
            timestamp=NOW,
            evidence_refs=("dup", "dup"),
        )


# -- Owner assessment (§10) -------------------------------------------------


def test_owner_outcome_vocabulary_is_exactly_two_values() -> None:
    assert {member.value for member in OwnerOutcome} == {
        "OWNER_ACCEPTED",
        "OWNER_REJECTED",
    }


def test_owner_unreviewed_cannot_be_constructed(owner_draft) -> None:
    with pytest.raises(ValidationError):
        owner_draft(outcome="OWNER_UNREVIEWED")


def test_absence_of_owner_assessment_derives_unreviewed(store, seed_review) -> None:
    envelope = seed_review()
    target = report_target_key(RUNTIME, envelope.assessment)
    assert owner_review_status(store, target) is OwnerReviewStatus.UNREVIEWED
    assert len(store) == 1


def test_owner_accept_and_reject_derive_correctly(store, seed_owner) -> None:
    accepted = seed_owner(outcome=OwnerOutcome.OWNER_ACCEPTED)
    assert (
        owner_review_status(store, report_target_key(RUNTIME, accepted.assessment))
        is OwnerReviewStatus.OWNER_ACCEPTED
    )

    rejected = seed_owner(
        assessment_id="assess-owner-2",
        run_id="run-2",
        report_id="report-2",
        outcome=OwnerOutcome.OWNER_REJECTED,
    )
    assert (
        owner_review_status(store, report_target_key(RUNTIME, rejected.assessment))
        is OwnerReviewStatus.OWNER_REJECTED
    )


# -- Effective assessment (§11) ---------------------------------------------


def test_owner_assessment_overrides_reviewer_proxy(
    store, seed_review, seed_owner
) -> None:
    envelope = seed_review()
    seed_owner(outcome=OwnerOutcome.OWNER_REJECTED)

    effective = effective_assessment(
        store, report_target_key(RUNTIME, envelope.assessment)
    )
    assert effective is not None
    assert effective.authority == "OWNER"
    assert effective.outcome == "OWNER_REJECTED"


def test_reviewer_proxy_used_only_without_owner_assessment(
    store, seed_review
) -> None:
    envelope = seed_review()
    effective = effective_assessment(
        store, report_target_key(RUNTIME, envelope.assessment)
    )
    assert effective is not None
    assert effective.authority == "REVIEWER_PROXY"


def test_greatest_append_sequence_wins_not_latest_timestamp(
    store, seed_review
) -> None:
    """§8: timestamp is display/audit only; sequence is ordering authority."""

    seed_review(assessment_id="a1", offset_seconds=100)
    last = seed_review(
        assessment_id="a2", offset_seconds=0, outcome=ReviewOutcome.REVIEWER_BLOCKED
    )

    effective = effective_assessment(
        store, report_target_key(RUNTIME, last.assessment)
    )
    assert effective is not None
    assert effective.append_sequence == 2
    assert effective.outcome == "REVIEWER_BLOCKED"
    assert len(store) == 2


def test_assessment_of_different_artifact_does_not_apply(
    store, seed_review, review_draft
) -> None:
    seed_review(assessment_id="a-old")
    other = review_draft(assessment_id="a-new", report_hash=OTHER_REPORT_HASH)
    other_target = report_target_key(RUNTIME, other)

    assert effective_assessment(store, other_target) is None
    assert owner_review_status(store, other_target) is OwnerReviewStatus.UNREVIEWED


def test_no_assessment_for_unknown_target(store) -> None:
    unknown = ("PRODUCT_RUNTIME", "run-x", "report-x", REPORT_HASH, "GIT_COMMIT", "f" * 40)
    assert effective_assessment(store, unknown) is None


# -- F10: source collision isolation ----------------------------------------


def test_identical_report_identity_across_sources_stays_distinct(
    store, seed_review
) -> None:
    """Same run_id and report identity, two sources: never merged."""

    runtime_env = seed_review(assessment_id="a-runtime", source=RUNTIME)
    dogfood_env = seed_review(
        assessment_id="a-dogfood",
        source=DOGFOOD,
        outcome=ReviewOutcome.REVIEWER_BLOCKED,
    )

    runtime_target = report_target_key(RUNTIME, runtime_env.assessment)
    dogfood_target = report_target_key(DOGFOOD, dogfood_env.assessment)
    assert runtime_target != dogfood_target

    runtime_effective = effective_assessment(store, runtime_target)
    dogfood_effective = effective_assessment(store, dogfood_target)
    assert runtime_effective is not None and dogfood_effective is not None
    assert runtime_effective.outcome == "REVIEWER_APPROVED"
    assert dogfood_effective.outcome == "REVIEWER_BLOCKED"
    assert runtime_effective.operational_source is RUNTIME
    assert dogfood_effective.operational_source is DOGFOOD


def test_owner_status_does_not_leak_across_sources(
    store, seed_owner, seed_review
) -> None:
    """A dogfood Owner acceptance must not mark the Runtime target reviewed."""

    seed_owner(source=DOGFOOD, outcome=OwnerOutcome.OWNER_ACCEPTED)
    runtime_env = seed_review(source=RUNTIME)

    runtime_target = report_target_key(RUNTIME, runtime_env.assessment)
    assert owner_review_status(store, runtime_target) is OwnerReviewStatus.UNREVIEWED


# -- F01/F10: no public cross-source "latest" -------------------------------


def test_no_public_latest_by_sequence_reduction() -> None:
    """F01: a bag-of-records latest cannot know its inputs share one domain."""

    import tomtit.evaluation as package

    assert "latest_by_sequence" not in package.__all__
    assert not hasattr(package, "latest_by_sequence")
    for name in package.__all__:
        assert "latest" not in name.lower(), name


def test_identical_local_ids_across_sources_never_produce_a_shared_latest(
    store, seed_review
) -> None:
    """Same run_id, same report identity, different Evaluation append order.

    The dogfood record is appended *later*, so a sequence-only comparison would
    hand it the "latest" slot for the Runtime target. Each target must instead
    resolve to its own source's record.
    """

    runtime_env = seed_review(
        assessment_id="a-runtime", run_id="run-X", report_id="report-Y", source=RUNTIME
    )
    dogfood_env = seed_review(
        assessment_id="a-dogfood",
        run_id="run-X",
        report_id="report-Y",
        source=DOGFOOD,
        outcome=ReviewOutcome.REVIEWER_BLOCKED,
    )
    assert runtime_env.append_sequence == 1
    assert dogfood_env.append_sequence == 2

    runtime_target = report_target_key(RUNTIME, runtime_env.assessment)
    dogfood_target = report_target_key(DOGFOOD, dogfood_env.assessment)

    runtime_effective = effective_assessment(store, runtime_target)
    dogfood_effective = effective_assessment(store, dogfood_target)
    assert runtime_effective is not None and dogfood_effective is not None

    # The later dogfood sequence does not win the Runtime target.
    assert runtime_effective.append_sequence == 1
    assert runtime_effective.operational_source is RUNTIME
    assert runtime_effective.outcome == "REVIEWER_APPROVED"

    assert dogfood_effective.append_sequence == 2
    assert dogfood_effective.operational_source is DOGFOOD
    assert dogfood_effective.outcome == "REVIEWER_BLOCKED"


def test_dogfood_owner_acceptance_does_not_govern_the_runtime_target(
    store, seed_review, seed_owner
) -> None:
    """Owner precedence is also source-local, whatever the append order."""

    seed_review(assessment_id="a-runtime", run_id="run-X", report_id="report-Y")
    dogfood_owner = seed_owner(
        assessment_id="o-dogfood",
        run_id="run-X",
        report_id="report-Y",
        source=DOGFOOD,
        outcome=OwnerOutcome.OWNER_ACCEPTED,
    )
    assert dogfood_owner.append_sequence == 2

    runtime_target = ("PRODUCT_RUNTIME",) + report_target_key(
        DOGFOOD, dogfood_owner.assessment
    )[1:]
    effective = effective_assessment(store, runtime_target)
    assert effective is not None
    assert effective.authority == "REVIEWER_PROXY"
    assert effective.operational_source is RUNTIME
    assert owner_review_status(store, runtime_target) is OwnerReviewStatus.UNREVIEWED


def test_mixed_source_records_are_rejected_by_latest_selection(
    store, seed_review
) -> None:
    """The internal reduction refuses anything outside the requested target."""

    from tomtit.evaluation.derive import _latest_by_sequence

    runtime_env = seed_review(assessment_id="a-runtime", source=RUNTIME)
    dogfood_env = seed_review(assessment_id="a-dogfood", source=DOGFOOD)
    mixed = (runtime_env, dogfood_env)
    runtime_target = report_target_key(RUNTIME, runtime_env.assessment)

    with pytest.raises(CrossSourceSelectionError):
        _latest_by_sequence(mixed, runtime_target)

    # Correctly scoped input still resolves.
    assert _latest_by_sequence((runtime_env,), runtime_target) is runtime_env


def test_unfiltered_store_read_is_not_a_latest_domain(store, seed_review) -> None:
    """``records_of_type`` without a source is inspection, not selection."""

    seed_review(assessment_id="a-runtime", source=RUNTIME)
    seed_review(assessment_id="a-dogfood", source=DOGFOOD)

    from tomtit.evaluation.derive import _latest_by_sequence

    both = store.records_of_type(ReviewAssessmentEventDraft)
    assert len(both) == 2
    with pytest.raises(CrossSourceSelectionError):
        _latest_by_sequence(both, report_target_key(RUNTIME, both[0].assessment))
