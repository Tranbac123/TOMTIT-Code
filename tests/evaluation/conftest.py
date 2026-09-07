"""Shared fixtures for TOMTIT Evaluation V0A tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tomtit.evaluation import (
    EvaluationAssessmentStore,
    OperationalSource,
    OwnerAssessmentEventDraft,
    OwnerOutcome,
    ReviewAssessmentEventDraft,
    ReviewOutcome,
    RevisionType,
    SourceRevision,
)
from ._history import TrustedHistoryStore

ACTIVE_COMMIT = "cdb6d360d1d340a1f7720707d3a8594937c0a6ad"
REPORT_HASH = "a" * 64
OTHER_REPORT_HASH = "b" * 64
NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def active_revision() -> SourceRevision:
    return SourceRevision(
        revision_type=RevisionType.GIT_COMMIT, revision_value=ACTIVE_COMMIT
    )


@pytest.fixture
def store() -> EvaluationAssessmentStore:
    """The real production store. Report-bound appends fail closed here."""

    return EvaluationAssessmentStore()


@pytest.fixture
def history() -> TrustedHistoryStore:
    """A store-shaped test double for derivations that need trusted history.

    Distinct from ``store`` on purpose: anything asserted through this fixture
    is a statement about derivation behavior over history, never a statement
    about what the shipped store lets a caller publish.
    """

    return TrustedHistoryStore()


@pytest.fixture
def review_draft(active_revision):
    def _make(
        *,
        assessment_id: str = "assess-review-1",
        run_id: str = "run-1",
        report_id: str = "report-1",
        report_hash: str = REPORT_HASH,
        outcome: ReviewOutcome = ReviewOutcome.REVIEWER_APPROVED,
        revision: SourceRevision | None = None,
        offset_seconds: int = 0,
    ) -> ReviewAssessmentEventDraft:
        return ReviewAssessmentEventDraft(
            assessment_id=assessment_id,
            run_id=run_id,
            target_terminal_report_id=report_id,
            target_terminal_report_hash=report_hash,
            target_source_revision=revision or active_revision,
            review_outcome=outcome,
            reviewer="codex-gpt-5.6-sol",
            timestamp=NOW + timedelta(seconds=offset_seconds),
            evidence_refs=(f"evidence:{assessment_id}",),
        )

    return _make


@pytest.fixture
def owner_draft(active_revision):
    def _make(
        *,
        assessment_id: str = "assess-owner-1",
        run_id: str = "run-1",
        report_id: str = "report-1",
        report_hash: str = REPORT_HASH,
        outcome: OwnerOutcome = OwnerOutcome.OWNER_ACCEPTED,
        offset_seconds: int = 0,
    ) -> OwnerAssessmentEventDraft:
        return OwnerAssessmentEventDraft(
            assessment_id=assessment_id,
            run_id=run_id,
            target_terminal_report_id=report_id,
            target_terminal_report_hash=report_hash,
            target_source_revision=active_revision,
            owner_outcome=outcome,
            timestamp=NOW + timedelta(seconds=offset_seconds),
            evidence_refs=(f"evidence:{assessment_id}",),
        )

    return _make


@pytest.fixture
def seed_review(history, review_draft):
    """Place a review assessment into the test-only trusted history.

    The canonical public ``store.append`` refuses report-bound assessments
    (F04) and stays refused; derivation tests still need such history, so it is
    represented in :mod:`tests.evaluation._history`, which is not part of the
    shipped package and depends on no production write helper. This fixture is
    the *only* way these tests obtain report-bound records, which keeps "can a
    caller publish one?" and "does the derivation behave?" separate questions.
    """

    def _seed(
        *,
        source: OperationalSource = OperationalSource.PRODUCT_RUNTIME,
        **kwargs,
    ):
        return history.seed_trusted_report_bound(
            review_draft(**kwargs), operational_source=source
        )

    return _seed


@pytest.fixture
def seed_owner(history, owner_draft):
    """Place an Owner assessment into the same test-only trusted history."""

    def _seed(
        *,
        source: OperationalSource = OperationalSource.PRODUCT_RUNTIME,
        **kwargs,
    ):
        return history.seed_trusted_report_bound(
            owner_draft(**kwargs), operational_source=source
        )

    return _seed
