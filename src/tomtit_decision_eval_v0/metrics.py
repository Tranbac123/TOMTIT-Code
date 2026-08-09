"""Deterministic aggregate task-need and policy-compliance metrics."""

from __future__ import annotations

from collections.abc import Iterable

from .models import (
    AggregateMetrics,
    DecisionEvaluationResult,
    MetricValue,
    ObjectiveTaskNeed,
    PolicyCompliance,
    RepairAuthorization,
    RetryValue,
    TomtitAction,
)


def _ratio(numerator: int, denominator: int) -> MetricValue:
    return MetricValue(
        numerator=numerator,
        denominator=denominator,
        value=None if denominator == 0 else numerator / denominator,
        zero_denominator=denominator == 0,
    )


def aggregate_metrics(
    results: Iterable[DecisionEvaluationResult],
) -> AggregateMetrics:
    """Aggregate objective need separately from authorization/policy compliance."""

    records = tuple(results)
    true_retry = sum(
        result.tomtit_action is TomtitAction.RETRY
        and result.objective_task_need is ObjectiveTaskNeed.RETRY_NEEDED
        for result in records
    )
    false_retry = sum(
        result.tomtit_action is TomtitAction.RETRY
        and result.objective_task_need is ObjectiveTaskNeed.RETRY_NOT_NEEDED
        for result in records
    )
    missed_retry = sum(
        result.tomtit_action is TomtitAction.STOP
        and result.objective_task_need is ObjectiveTaskNeed.RETRY_NEEDED
        for result in records
    )
    correct_stop = sum(
        result.tomtit_action is TomtitAction.STOP
        and result.objective_task_need is ObjectiveTaskNeed.RETRY_NOT_NEEDED
        for result in records
    )
    ambiguous = sum(
        result.objective_task_need is ObjectiveTaskNeed.AMBIGUOUS
        for result in records
    )
    determinate = true_retry + false_retry + missed_retry + correct_stop

    known_retry_values = tuple(
        result
        for result in records
        if result.tomtit_action is TomtitAction.RETRY
        and result.retry_value is not RetryValue.UNKNOWN
    )
    improved_retries = sum(
        result.retry_value is RetryValue.IMPROVED for result in known_retry_values
    )
    policy_compliant = sum(
        result.policy_compliance is PolicyCompliance.COMPLIANT for result in records
    )
    policy_violations = len(records) - policy_compliant
    objective_needed = true_retry + missed_retry
    authorization_blocked_needed = sum(
        result.objective_task_need is ObjectiveTaskNeed.RETRY_NEEDED
        and result.repair_authorization is RepairAuthorization.NOT_AUTHORIZED
        for result in records
    )

    return AggregateMetrics(
        total_decisions=len(records),
        determinate_decisions=determinate,
        ambiguous_decisions=ambiguous,
        true_retry=true_retry,
        false_retry=false_retry,
        missed_retry=missed_retry,
        correct_stop=correct_stop,
        policy_compliant_decisions=policy_compliant,
        policy_violations=policy_violations,
        authorization_blocked_needed_count=authorization_blocked_needed,
        retry_precision=_ratio(true_retry, true_retry + false_retry),
        retry_recall=_ratio(true_retry, objective_needed),
        unnecessary_retry_rate=_ratio(false_retry, true_retry + false_retry),
        missed_retry_rate=_ratio(missed_retry, objective_needed),
        repair_success_rate=_ratio(improved_retries, len(known_retry_values)),
        decision_accuracy=_ratio(true_retry + correct_stop, determinate),
        ambiguous_rate=_ratio(ambiguous, len(records)),
        policy_compliance_accuracy=_ratio(policy_compliant, len(records)),
        authorization_blocked_needed_rate=_ratio(
            authorization_blocked_needed, objective_needed
        ),
    )
