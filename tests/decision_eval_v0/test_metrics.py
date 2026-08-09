from __future__ import annotations

from tomtit_decision_eval_v0 import (
    CheckStatus,
    ClaimStatus,
    CounterfactualEvidence,
    RequirementStatus,
    TomtitAction,
    aggregate_metrics,
    evaluate_decision,
)

from .conftest import post_retry


def needed_result(
    make_case,
    action: TomtitAction,
    *,
    authorized: bool,
    post=None,
):
    return evaluate_decision(
        make_case(
            action,
            requirement_status=RequirementStatus.UNSATISFIED,
            requirement_repairable=ClaimStatus.YES,
            check_status=CheckStatus.FAIL,
            check_repairable=ClaimStatus.YES,
            repair_authorized=authorized,
            post=post,
        )
    )


def test_metrics_keep_unauthorized_objective_need_in_task_population(
    make_case, material_finding
) -> None:
    results = (
        needed_result(
            make_case,
            TomtitAction.RETRY,
            authorized=True,
            post=post_retry(before=1, after=0),
        ),
        needed_result(make_case, TomtitAction.STOP, authorized=True),
        needed_result(make_case, TomtitAction.STOP, authorized=False),
        needed_result(
            make_case,
            TomtitAction.RETRY,
            authorized=False,
            post=post_retry(before=1, after=1),
        ),
        evaluate_decision(make_case(TomtitAction.STOP)),
        evaluate_decision(
            make_case(
                TomtitAction.RETRY,
                post=post_retry(unrelated_quality_improved=True),
            )
        ),
        evaluate_decision(
            make_case(
                TomtitAction.RETRY,
                finding=material_finding,
                requirement_status=RequirementStatus.UNKNOWN,
                check_status=CheckStatus.UNKNOWN,
                counterfactual=CounterfactualEvidence.NOT_AVAILABLE,
            )
        ),
    )

    metrics = aggregate_metrics(results)

    assert metrics.total_decisions == 7
    assert metrics.determinate_decisions == 6
    assert metrics.ambiguous_decisions == 1
    assert metrics.true_retry == 2
    assert metrics.false_retry == 1
    assert metrics.missed_retry == 2
    assert metrics.correct_stop == 1
    assert metrics.retry_precision.value == 2 / 3
    assert metrics.retry_recall.value == 2 / 4
    assert metrics.unnecessary_retry_rate.value == 1 / 3
    assert metrics.missed_retry_rate.value == 2 / 4
    assert metrics.repair_success_rate.value == 2 / 3
    assert metrics.decision_accuracy.value == 3 / 6
    assert metrics.ambiguous_rate.value == 1 / 7

    assert metrics.policy_compliant_decisions == 6
    assert metrics.policy_violations == 1
    assert metrics.policy_compliance_accuracy.value == 6 / 7
    assert metrics.authorization_blocked_needed_count == 2
    assert metrics.authorization_blocked_needed_rate.value == 2 / 4


def test_reviewer_only_ambiguous_case_does_not_inflate_objective_metrics(
    make_case, material_finding
) -> None:
    ambiguous = evaluate_decision(
        make_case(
            TomtitAction.RETRY,
            finding=material_finding,
            requirement_status=RequirementStatus.UNKNOWN,
            check_status=CheckStatus.UNKNOWN,
            counterfactual=CounterfactualEvidence.NOT_AVAILABLE,
        )
    )
    metrics = aggregate_metrics((ambiguous,))

    assert metrics.determinate_decisions == 0
    assert metrics.retry_precision.value is None
    assert metrics.retry_recall.value is None
    assert metrics.decision_accuracy.value is None
    assert metrics.ambiguous_rate.value == 1.0


def test_zero_denominators_are_explicit() -> None:
    metrics = aggregate_metrics(())

    for metric in (
        metrics.retry_precision,
        metrics.retry_recall,
        metrics.unnecessary_retry_rate,
        metrics.missed_retry_rate,
        metrics.repair_success_rate,
        metrics.decision_accuracy,
        metrics.ambiguous_rate,
        metrics.policy_compliance_accuracy,
        metrics.authorization_blocked_needed_rate,
    ):
        assert metric.denominator == 0
        assert metric.value is None
        assert metric.zero_denominator is True
