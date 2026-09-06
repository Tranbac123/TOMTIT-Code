"""Test-tree seeding helper. Lives in ``tests/``, never in production source.

Why this exists
---------------
Canonical publication of the two report-bound Contract V1 events fails closed
(:class:`~tomtit.evaluation.store.ReportBoundAppendUnavailableError`) until
``EVAL-RUNTIME-BRIDGE`` supplies genuine Runtime-owned binding authority. The
§10/§11/§23 *derivations* over those events still have to be exercised, so this
helper places them into a store directly.

Why it is not shipped
---------------------
An affordance whose only purpose is to let tests bypass canonical behavior must
not be production source: shipping it would put a second, unauthenticated
publication path inside ``tomtit.evaluation`` where a caller could import it.
Keeping it under ``tests/`` means the installed package has exactly one append
path — :meth:`EvaluationAssessmentStore.append` — and it still refuses
report-bound drafts.

This is not an authority mechanism and must never become one: it takes no
issuer, binding, hash or attestation, because nothing here is checked. It
exists so that tests can construct history, not so that callers can publish it.
When the bridge lands, this file should be deleted, not extended.
"""

from __future__ import annotations

from tomtit.evaluation import EvaluationAssessmentStore, OperationalSource
from tomtit.evaluation.assessments import SourceAwareAssessment


def seed_report_bound_assessment(
    store: EvaluationAssessmentStore,
    draft: object,
    *,
    operational_source: OperationalSource,
) -> SourceAwareAssessment:
    """Place one report-bound draft into `store` without any authority check.

    TEST ONLY. Performs the same exact-type, duplicate and sequence handling as
    a canonical append and deliberately skips nothing else — because there is
    nothing else to skip: V0A has no verifiable report authority to check.
    """

    draft_type = store._validated_draft_type(  # noqa: SLF001 - test-only seeding
        draft, operational_source
    )
    return store._publish(  # noqa: SLF001 - test-only seeding
        draft, draft_type, operational_source
    )
